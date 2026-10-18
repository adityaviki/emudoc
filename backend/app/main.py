import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.convertors import PathConvertor, register_url_convertor

from app.api import audit, auth, chats, documents, folders, projects, query
from app.api.deps import (
    RETRY_AFTER,
    AccountDep,
    ServicesDep,
    SessionDep,
    refuse_cross_site,
    refuse_past,
)
from app.config import ROOT_DIR, get_settings
from app.db import BUSY, TOO_SLOW, canceled, engine
from app.errors import summary
from app.headers import SecurityHeaders, content_security_policy, secure
from app.indexing.pipeline import Services
from app.legal import TERMS_VERSION, privacy_policy, published, terms_of_service
from app.logs import REQUEST_ID_HEADER, RequestIds, configure_logging, current_context
from app.mail import Mailer, mailer_from_settings
from app.metrics import POOL_TIMEOUTS, HttpMetrics, export_metrics
from app.ratelimit import PAGE_ERRORS, by_address
from app.schemas import (
    EmbeddingStatus,
    HealthOut,
    LegalOut,
    LLMStatus,
    PageErrorIn,
    PageErrorOut,
    StatusOut,
    SubprocessorsOut,
)
from app.shutdown import Stop, heard_stop
from app.startup import prepare
from app.storage import StorageError
from app.subprocessors import subprocessors
from app.tracking import report_page_error, start_tracking
from app.version import VERSION
from app.worker import Worker

log = logging.getLogger(__name__)

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
STORAGE_DOWN = "Emudoc could not reach its file storage just now. Try again in a few minutes."
NOT_READY = "Emudoc cannot serve requests just now. Try again in a few minutes."
FAULT = "Emudoc ran into an error of its own, which it recorded."


class PagePath(PathConvertor):
    """A path the page routes itself: any outside /api. A path under /api that no route takes
    is the API's to refuse, 404, or 405 for a method its route does not take, not the page's
    catch-all's, which would answer 404 to a HEAD of a route that serves GET, and 405 to a
    POST to a path that does not exist."""

    regex = "(?!api(?:/|$)).*"


register_url_convertor("page", PagePath())

system = APIRouter(tags=["system"])


# HEAD as well as GET for both probes, which uptime services often send; a route of its own,
# since one route for both methods gives the schema one operation twice.
@system.get("/livez", response_model=HealthOut)
@system.head("/livez", include_in_schema=False)
async def alive():
    """Whether the process is alive, for an orchestrator that restarts the ones that are not.

    It touches nothing, not even the database: a database outage would otherwise have every
    replica restarted at once, which cures nothing and costs each its start. It is async, as
    is every dependency on the way (`refuse_cross_site`), so it is answered on the event loop
    and not in the thread pool, which a burst of slow requests can fill for a while without
    the process being stuck.
    """
    return HealthOut(status="ok", version=VERSION)


@system.get("/health", response_model=HealthOut)
@system.head("/health", include_in_schema=False)
def health(session: SessionDep):
    """Whether the app can serve requests, for load balancers, uptime checks and an
    orchestrator's readiness probe: a free database connection, and a database that answers.

    Besides, it names the version that answered (app/version.py), which deploy/aws/release.sh
    waits for and the user menu shows: the commit an image was built from, which says which
    code runs and nothing of how it is set up. Nothing else: this route is public, so it must
    not reveal the deployment's settings. A database that cannot be reached is 503 with one
    line in the log, not a traceback per probe; a pool with no connection free is 503 too
    (`pool_exhausted`).
    """
    try:
        session.execute(text("SELECT 1"))
    except DBAPIError as error:
        # The driver's own error, whose first line says what failed; SQLAlchemy's around it adds
        # the driver's hint and a link on lines of their own.
        reason = summary(error.orig or error).splitlines()[0]
        log.warning("Not ready: the database cannot be reached (%s)", reason)
        return JSONResponse({"detail": NOT_READY}, status_code=503)
    return HealthOut(status="ok", version=VERSION)


@system.get("/status", response_model=StatusOut)
def status(services: ServicesDep, account: AccountDep):
    """Report which optional services are usable, so the UI can say what is missing."""
    settings = get_settings()
    error = services.embedder.check()
    problem = services.llm.problem()
    return StatusOut(
        llm=LLMStatus(
            available=problem is None,
            agent_model=settings.agent_model,
            index_model=settings.index_model,
            agent_fallback_model=settings.agent_fallback_model or None,
            index_fallback_model=settings.index_fallback_model or None,
            error=problem,
        ),
        embeddings=EmbeddingStatus(ok=error is None, model=settings.embedding_model, error=error),
        currency=settings.price_currency,
    )


@system.get("/subprocessors", response_model=SubprocessorsOut)
def list_subprocessors(services: ServicesDep):
    """The companies this deployment hands its users' data to, for the /subprocessors page,
    and the region it keeps their documents in.

    Public on purpose, unlike the status above: a subprocessor list is published to the people
    deciding whether to upload. It names companies, and the host of a service it does not
    know, never a model, a key or an address on the deployment's own network.
    """
    settings = get_settings()
    return SubprocessorsOut(
        region=settings.data_region or None,
        subprocessors=subprocessors(settings, services.storage),
    )


@system.get("/legal", response_model=LegalOut)
def legal(services: ServicesDep):
    """The terms of service and the privacy policy, for the /terms and /privacy pages and the
    edition the sign-up form has its user accept. Public, like the subprocessor list: they are
    read before an account exists."""
    settings = get_settings()
    return LegalOut(
        version=TERMS_VERSION,
        published=published(),
        terms=terms_of_service(settings),
        privacy=privacy_policy(settings, services.storage),
    )


@system.post("/page-errors", response_model=PageErrorOut, status_code=202)
def page_error(report: PageErrorIn, request: Request, session: SessionDep):
    """An error the page rendered into, from its error boundary, for the log and the tracker
    (app/tracking.py). Public, since a sign-in page can fail too, and held to RATE_PAGE_ERRORS
    per client address. The answer is the id the report was logged under, which the page shows
    as the reference to quote."""
    refuse_past(session, (PAGE_ERRORS, by_address(request)))
    report_page_error(report, str(request.base_url).rstrip("/"))
    return PageErrorOut(reference=_request_id())


def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    """A request whose body or parameters do not fit, refused with 422 and where and why each
    part does not, as FastAPI would, but without the values: its own answer repeats them, so a
    password that is too short or a reset link's token would come back in the response, and
    with it into whatever records responses on the way."""
    return JSONResponse(
        {
            "detail": [
                {"type": problem["type"], "loc": problem["loc"], "msg": problem["msg"]}
                for problem in error.errors()
            ]
        },
        status_code=422,
    )


def storage_unavailable(request: Request, error: Exception) -> JSONResponse:
    """A storage that failed mid-request: 503 says it was not the request's fault, and the
    sentence says to try again. What failed is the operator's to read in the log, since it
    names the directory or the bucket and what to change in the settings."""
    log.warning("%s %s failed: %s", request.method, request.url.path, error)
    return JSONResponse({"detail": STORAGE_DOWN}, status_code=503)


def pool_exhausted(request: Request, error: Exception) -> JSONResponse:
    """Every database connection this process may hold was in use for DB_POOL_TIMEOUT seconds.

    The request is refused with 503 and a sentence that says to try again, and the log says how
    the pool stood, so that a deployment that needs a larger pool or another replica shows up
    as a line to act on rather than as a traceback.
    """
    POOL_TIMEOUTS.inc()
    log.warning(
        "%s %s was refused: every database connection is in use (%s)",
        request.method,
        request.url.path,
        engine.pool.status(),
    )
    return JSONResponse(
        {"detail": BUSY}, status_code=503, headers={"Retry-After": str(RETRY_AFTER)}
    )


def statement_canceled(request: Request, error: Exception) -> JSONResponse:
    """A statement the database stopped, at the role's statement_timeout (deploying.md, "The
    database"): 503 and a sentence for the member, and for the operator one line that names the
    route rather than a traceback. Any other operational error goes on as it came, to the 500
    and the traceback of an error nobody planned for."""
    if not (isinstance(error, DBAPIError) and canceled(error)):
        raise error
    log.warning(
        "%s %s was stopped by the database: %s",
        request.method,
        request.url.path,
        summary(error.orig).splitlines()[0],
    )
    return JSONResponse(
        {"detail": TOO_SLOW}, status_code=503, headers={"Retry-After": str(RETRY_AFTER)}
    )


async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """An exception nobody planned for: 500, with a sentence for the member and the request's
    id to quote, in the body and in X-Request-ID, by which the operator finds its traceback in
    the log and its report in the tracker (app/tracking.py).

    Starlette sends this answer from outside every middleware the app adds, so it puts the id
    and the security headers on it itself, and then raises the exception again, for uvicorn to
    log with its traceback. Async, so that a full thread pool does not hold the answer up.
    """
    request_id = _request_id()
    detail = (
        f"{FAULT} Try again, and if it fails again, give the reference {request_id} when you "
        "report it."
        if request_id
        else f"{FAULT} Try again in a moment."
    )
    response = JSONResponse({"detail": detail, "request_id": request_id}, status_code=500)
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    secure(response.headers, request.scope)
    return response


def _request_id() -> str | None:
    request_id = current_context().get("request_id")
    return str(request_id) if request_id is not None else None


def create_app(
    services: Services | None = None,
    mailer: Mailer | None = None,
    http: httpx.Client | None = None,
) -> FastAPI:
    """The application. Tests pass fakes for the services, the mailer and the outbound client."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = prepare(settings, services)
        app.state.policy = content_security_policy(app.state.services.storage)
        app.state.mailer = mailer or mailer_from_settings(settings)
        app.state.http = http or httpx.Client(timeout=10)
        # The worker is the API's own in development and a process of its own in production
        # (RUN_WORKER); without it, an upload waits for a worker elsewhere.
        app.state.worker = None
        if settings.run_worker:
            app.state.worker = Worker(
                app.state.services,
                settings.worker_concurrency,
                app.state.mailer,
                answer_concurrency=settings.answer_concurrency,
            )
        # The metrics' own server (METRICS_ADDRESS), with the worker's figures when the worker
        # runs here; an address it cannot listen on stops the start before the worker's threads.
        exporter = export_metrics(settings, app.state.worker)
        if app.state.worker:
            app.state.worker.start()
        # A stop signal is noted before uvicorn waits for the open responses to end, so that
        # the answer streams end on it rather than hold the stop up (app/shutdown.py).
        app.state.stop = Stop()
        with heard_stop(app.state.stop):
            yield
        if app.state.worker:
            app.state.worker.stop()
        if exporter is not None:
            exporter.close()
        app.state.http.close()

    app = FastAPI(
        title="Emudoc",
        lifespan=lifespan,
        # The schema, and with it the interactive docs at /docs and /redoc, is a development
        # convenience; a deployment does not serve it.
        openapi_url="/openapi.json" if settings.environment == "development" else None,
    )
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(StorageError, storage_unavailable)
    app.add_exception_handler(PoolTimeout, pool_exhausted)
    app.add_exception_handler(OperationalError, statement_canceled)
    app.add_exception_handler(Exception, unexpected_error)
    # No CORSMiddleware, on purpose: the frontend is served from this origin, and the defence
    # against a request forged from another site (refuse_cross_site in api/deps.py) rests on
    # the app answering no cross-origin request. test_no_other_origin_is_answered in
    # tests/test_headers.py says what a frontend on another origin would need instead.
    app.add_middleware(SecurityHeaders)
    # Outside the headers, so that a request is timed to the end of all of its answer.
    app.add_middleware(HttpMetrics)
    # Outside the others, so that what they log is logged under the request's id too.
    app.add_middleware(RequestIds)
    for router in (
        system,
        auth.router,
        projects.router,
        folders.router,
        documents.router,
        query.router,
        chats.router,
        audit.router,
    ):
        app.include_router(router, prefix="/api", dependencies=[Depends(refuse_cross_site)])

    if FRONTEND_DIST.is_dir():
        # Serve the built frontend; unknown paths fall back to index.html for client routing.
        # The assets' directory is looked for when they are asked for, not now: a build empties
        # dist/ before it writes it, and a process started meanwhile should serve what the
        # build writes rather than refuse to start.
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False),
            name="assets",
        )

        # HEAD too, which uptime services often probe the page with.
        @app.api_route("/{path:page}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str):
            file = FRONTEND_DIST / path
            if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


configure_logging(get_settings().log_format)
start_tracking(get_settings())
app = create_app()
