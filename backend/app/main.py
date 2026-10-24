import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path

import httpx
from anyio import CapacityLimiter, to_thread
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.convertors import PathConvertor, register_url_convertor
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    apikeys,
    audit,
    auth,
    automations,
    billing,
    chats,
    check_runs,
    cross_checks,
    documents,
    exports,
    folders,
    help,
    invitations,
    mail,
    notifications,
    onboarding,
    operator,
    organizations,
    projects,
    query,
    reviews,
    support,
    templates,
    trash,
    usage,
)
from app.api.deps import (
    RETRY_AFTER,
    AccountDep,
    ServicesDep,
    SessionDep,
    refuse_cross_site,
    refuse_past,
)
from app.billing import BillingRefused
from app.config import ROOT_DIR, Settings, get_settings
from app.db import BUSY, TOO_SLOW, SessionLocal, canceled, engine
from app.entitlements import PlanRefused
from app.errors import shown, summary
from app.headers import (
    ASSETS_KEPT,
    PAGE_KEPT,
    SecurityHeaders,
    content_security_policy,
    secure,
)
from app.indexing.pipeline import Services
from app.jobs import Work, work_running
from app.legal import TERMS_VERSION, privacy_policy, published, terms_of_service
from app.logs import REQUEST_ID_HEADER, RequestIds, configure_logging, current_context
from app.mail import Mailer, mailer_from_settings
from app.metrics import POOL_TIMEOUTS, HttpMetrics, export_metrics
from app.operators import operator_of
from app.payments import PaymentsError
from app.ratelimit import PAGE_ERRORS, by_address
from app.schemas import (
    EmbeddingStatus,
    HealthOut,
    LegalOut,
    LLMStatus,
    PageErrorIn,
    PageErrorOut,
    PlanRefusalOut,
    StatusOut,
    SubprocessorsOut,
    UploadLimitsOut,
    WorkerStatus,
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
# What a deployment's status line tells members when the models or the embeddings endpoint
# cannot be used: what they cannot do for now, naming no model, address or setting. Why is the
# operator's, from the start's log, the metrics and the worker's watch (app/watch.py), which
# read the same `LLM.problem()` and `Embedder.check()`.
MODELS_UNAVAILABLE = (
    "The AI model is unavailable just now, so questions cannot be asked and documents get no "
    "summaries or facts. Try again later."
)
KEYWORDS_ONLY = (
    "Search matches keywords only just now, so a passage that says the same in other words may "
    "be missed. Try again later."
)
# And when no worker takes the queue's work (app/jobs.py): the operator learns why from the
# processes, `maintenance jobs` and the alerts.
WORK_STOPPED = (
    "Emudoc's background work is not running just now, so uploaded documents wait to be indexed "
    "and questions cannot be asked. Try again later."
)


class PagePath(PathConvertor):
    """A path the page routes itself: any outside /api. A path under /api that no route takes
    is the API's to refuse, 404, or 405 for a method its route does not take, not the page's
    catch-all's, which would answer 404 to a HEAD of a route that serves GET, and 405 to a
    POST to a path that does not exist."""

    regex = "(?!api(?:/|$)).*"


register_url_convertor("page", PagePath())


# The operator's area's page (app/operators.py): a page of its own, `frontend/operator.html`, with
# a bundle of its own, served at /operator and below it to an operator alone. Its own name is
# taken too, so that the file is reached by no other way.
OPERATOR_PAGE = "operator.html"
_OPERATOR_PATH = re.compile(r"operator(?:/.*)?|operator\.html")


class BuiltFiles(StaticFiles):
    """Files of the frontend's build, each sent with how long a browser may keep it (ASSETS_KEPT
    and PAGE_KEPT in app/headers.py). A 304 carries it too; a 404 does not, since a name
    missing now may be served later, as a rollback serves the last release's files again.

    The directory is looked for when a file is asked for, not now: a build empties dist/
    before it writes it, and a process started meanwhile should serve what the build writes
    rather than refuse to start."""

    def __init__(self, directory: Path, cache_control: str) -> None:
        super().__init__(directory=directory, check_dir=False)
        self.cache_control = cache_control

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = self.cache_control
        return response


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
def status(services: ServicesDep, account: AccountDep, session: SessionDep):
    """Which optional services are usable, and whether a worker runs, so the page can say what
    cannot be done.

    In development whoever uses the app runs it, so the status speaks to the operator: it names
    the models and says what stops them and what to change, the embeddings endpoint's address
    included. A deployment's members are told what they cannot do for now and nothing of how
    the deployment is set up, which is the operator's to read elsewhere (`MODELS_UNAVAILABLE`).
    """
    settings = get_settings()
    reason = services.embedder.check()
    problem = services.llm.problem()
    work = work_running(session)
    if settings.environment != "development":
        return StatusOut(
            llm=LLMStatus(available=problem is None, error=MODELS_UNAVAILABLE if problem else None),
            embeddings=EmbeddingStatus(ok=reason is None, error=KEYWORDS_ONLY if reason else None),
            workers=WorkerStatus(
                running=work.running, error=None if work.running else WORK_STOPPED
            ),
            currency=settings.price_currency,
            uploads=upload_limits(settings),
        )
    if problem:
        # The settings are read once, at the start: a key added to .env is used by the backend
        # started after.
        problem += " Then restart the backend to get answers, summaries and facts."
    if reason:
        reason += " Search falls back to keywords."
    return StatusOut(
        llm=LLMStatus(
            available=problem is None,
            agent_model=settings.agent_model,
            index_model=settings.index_model,
            agent_fallback_model=settings.agent_fallback_model or None,
            index_fallback_model=settings.index_fallback_model or None,
            error=problem,
        ),
        embeddings=EmbeddingStatus(ok=reason is None, model=settings.embedding_model, error=reason),
        workers=WorkerStatus(running=work.running, error=None if work.running else no_worker(work)),
        currency=settings.price_currency,
        uploads=upload_limits(settings),
    )


def no_worker(work: Work) -> str:
    """What a developer reads when no worker runs: since when, and how to start one."""
    seen = (
        f"No worker has been seen since {work.last_seen_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
        if work.last_seen_at
        else "No worker has run against this database yet"
    )
    return (
        f"{seen}, so uploads stay queued and questions go unanswered. Run `uv run python -m "
        "app.worker` in emudoc/backend, or leave RUN_WORKER unset for the backend to run one "
        "itself, and restart it."
    )


def upload_limits(settings: Settings) -> UploadLimitsOut:
    megabyte = 1024 * 1024
    return UploadLimitsOut(
        files=settings.max_upload_files,
        request_bytes=settings.max_upload_request_mb * megabyte,
        file_bytes=settings.max_upload_mb * megabyte,
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


def billing_unavailable(request: Request, error: Exception) -> JSONResponse:
    """A request to Stripe that failed mid-request: 503 and the members' sentence, and in the log
    the operator's, which names the call and Stripe's codes. Stripe delivers an event it was
    answered so again."""
    log.warning("%s %s failed: %s", request.method, request.url.path, error)
    development = get_settings().environment == "development"
    return JSONResponse({"detail": shown(error, development=development)}, status_code=503)


def billing_refused(request: Request, error: Exception) -> JSONResponse:
    """What billing turns down (app/billing.py), with its status and sentence."""
    assert isinstance(error, BillingRefused)
    return JSONResponse({"detail": str(error)}, status_code=error.status)


def plan_refused(request: Request, error: Exception) -> JSONResponse:
    """What the organisation's plan turns down (app/entitlements.py): 402, with the sentence and
    the figure it is held to, from which the page chooses what to offer."""
    assert isinstance(error, PlanRefused)
    refusal = error.refusal
    body = PlanRefusalOut(detail=refusal.sentence, limit=refusal.limit)
    return JSONResponse(body.model_dump(), status_code=402)


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


def _from_operator(request: Request) -> bool:
    """Whether the operator's page may be sent: to an operator, from an operator's network. One
    whom the area still refuses, for want of two-factor sign-in, is sent it, and told why by
    the routes."""
    with SessionLocal() as session:
        return operator_of(session, request) is not None


def _request_id() -> str | None:
    request_id = current_context().get("request_id")
    return str(request_id) if request_id is not None else None


# The routers whose routes an API key reaches (app/apikeys.py): what the projects hold, which a
# program reads and changes; every other route, the accounts' and the organisation's own, takes
# a session alone.
KEYED = (
    projects.router,
    exports.router,
    folders.router,
    documents.router,
    query.router,
    chats.router,
    trash.router,
    templates.router,
    cross_checks.router,
    check_runs.router,
    automations.router,
    reviews.router,
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
        # What the worker sends the posted mail and its reports through, when it runs here.
        app.state.services.mailer = (
            mailer or app.state.services.mailer or mailer_from_settings(settings)
        )
        app.state.policy = content_security_policy(app.state.services.storage)
        app.state.http = http or httpx.Client(timeout=10)
        # The threads the page images and thumbnails are drawn in, one per render process
        # (app/rendering.py): a request beyond them waits without holding a thread of its own.
        app.state.drawing = CapacityLimiter(settings.render_processes)
        # The worker is the API's own in development and a process of its own in production
        # (RUN_WORKER); without it, an upload waits for a worker elsewhere.
        app.state.worker = None
        if settings.run_worker:
            app.state.worker = Worker(
                app.state.services,
                settings.worker_concurrency,
                app.state.services.mailer,
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
        # The copies kept for drawing pages, which no exit handler would remove (Storage.close).
        app.state.services.storage.close()
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
    app.add_exception_handler(PaymentsError, billing_unavailable)
    app.add_exception_handler(BillingRefused, billing_refused)
    app.add_exception_handler(PlanRefused, plan_refused)
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
        organizations.router,
        invitations.router,
        apikeys.router,
        *KEYED,
        audit.router,
        usage.router,
        billing.router,
        notifications.router,
        onboarding.router,
        support.router,
        help.router,
    ):
        app.include_router(router, prefix="/api", dependencies=[Depends(refuse_cross_site)])
    # The routes an API key reaches (`_key_account` in app/api/deps.py), by their functions,
    # which the request's scope names once its route is matched.
    app.state.keyed = frozenset(route.endpoint for router in KEYED for route in router.routes)
    # Stripe's and Resend's events carry no header of the app's: their signature proves where
    # they came from (app/api/billing.py, app/api/mail.py); nor does a mail program's one-click
    # unsubscribe, whose token says whose notices stop.
    app.include_router(billing.events, prefix="/api")
    app.include_router(mail.events, prefix="/api")
    # The operator's area asks for the app's header itself, once the operator is known, so that
    # a refusal tells nobody else that a route is there (app/api/operator.py).
    app.include_router(operator.router, prefix="/api")

    if FRONTEND_DIST.is_dir():
        # Serve the built frontend; unknown paths fall back to index.html for client routing.
        app.mount("/assets", BuiltFiles(FRONTEND_DIST / "assets", ASSETS_KEPT), name="assets")
        page = BuiltFiles(FRONTEND_DIST, PAGE_KEPT)

        # HEAD too, which uptime services often probe the page with.
        @app.api_route("/{path:page}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa(path: str, request: Request):
            if _OPERATOR_PATH.fullmatch(path):
                # Anyone but an operator gets the members' page, as for a path nobody serves.
                if await to_thread.run_sync(_from_operator, request):
                    return await page.get_response(OPERATOR_PAGE, request.scope)
                return await page.get_response("index.html", request.scope)
            try:
                return await page.get_response(path, request.scope)
            except StarletteHTTPException as missing:
                if missing.status_code != 404:
                    raise
            return await page.get_response("index.html", request.scope)

    return app


configure_logging(get_settings().log_format)
start_tracking(get_settings())
app = create_app()
