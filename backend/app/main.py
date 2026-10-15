import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.api import audit, auth, chats, documents, folders, projects, query
from app.api.deps import RETRY_AFTER, AccountDep, ServicesDep, SessionDep, refuse_cross_site
from app.config import ROOT_DIR, get_settings
from app.db import BUSY, engine
from app.errors import summary
from app.headers import SecurityHeaders, content_security_policy
from app.indexing.pipeline import Services
from app.legal import TERMS_VERSION, privacy_policy, published, terms_of_service
from app.logs import configure_logging
from app.mail import Mailer, mailer_from_settings
from app.schemas import (
    EmbeddingStatus,
    HealthOut,
    LegalOut,
    LLMStatus,
    StatusOut,
    SubprocessorsOut,
)
from app.startup import prepare
from app.storage import StorageError
from app.subprocessors import subprocessors
from app.worker import Worker

log = logging.getLogger(__name__)

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
STORAGE_DOWN = "Emudoc could not reach its file storage just now. Try again in a few minutes."
NOT_READY = "Emudoc cannot serve requests just now. Try again in a few minutes."

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
    return HealthOut(status="ok")


@system.get("/health", response_model=HealthOut)
@system.head("/health", include_in_schema=False)
def health(session: SessionDep):
    """Whether the app can serve requests, for load balancers, uptime checks and an
    orchestrator's readiness probe: a free database connection, and a database that answers.

    It says nothing else: this route is public, so it must not reveal how the app is set up.
    A database that cannot be reached is 503 with one line in the log, not a traceback per
    probe; a pool with no connection free is 503 too (`pool_exhausted`).
    """
    try:
        session.execute(text("SELECT 1"))
    except DBAPIError as error:
        # The driver's own error, whose first line says what failed; SQLAlchemy's around it adds
        # the driver's hint and a link on lines of their own.
        reason = summary(error.orig or error).splitlines()[0]
        log.warning("Not ready: the database cannot be reached (%s)", reason)
        return JSONResponse({"detail": NOT_READY}, status_code=503)
    return HealthOut(status="ok")


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
    log.warning(
        "%s %s was refused: every database connection is in use (%s)",
        request.method,
        request.url.path,
        engine.pool.status(),
    )
    return JSONResponse(
        {"detail": BUSY}, status_code=503, headers={"Retry-After": str(RETRY_AFTER)}
    )


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
            app.state.worker.start()
        yield
        if app.state.worker:
            app.state.worker.stop()
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
    # No CORSMiddleware, on purpose: the frontend is served from this origin, and the defence
    # against a request forged from another site (refuse_cross_site in api/deps.py) rests on
    # the app answering no cross-origin request. test_no_other_origin_is_answered in
    # tests/test_headers.py says what a frontend on another origin would need instead.
    app.add_middleware(SecurityHeaders)
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
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        # HEAD too, which uptime services often probe the page with.
        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            file = FRONTEND_DIST / path
            if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


configure_logging()
app = create_app()
