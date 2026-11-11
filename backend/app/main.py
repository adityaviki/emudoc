import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC
from functools import cache
from pathlib import Path
from typing import Any

import httpx
from anyio import CapacityLimiter, to_thread
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.convertors import PathConvertor, register_url_convertor
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    activity,
    apikeys,
    audit,
    auth,
    automations,
    billing,
    chats,
    check_runs,
    comments,
    connectors,
    cross_checks,
    documents,
    drafts,
    duplicates,
    exports,
    favorites,
    folders,
    help,
    invitations,
    mail,
    mailbox,
    notes,
    notifications,
    onboarding,
    operator,
    organizations,
    projects,
    query,
    question_sets,
    reviews,
    search,
    share_links,
    support,
    templates,
    trash,
    usage,
    v1,
    versions,
    webhooks,
)
from app.api.deps import (
    RETRY_AFTER,
    AccountDep,
    ServicesDep,
    SessionDep,
    refuse_cross_site,
    refuse_past,
)
from app.api.v1.contract import published as published_contract
from app.billing import BillingRefused
from app.config import ROOT_DIR, get_settings
from app.conversion import check_converter
from app.costs import CostsWithheld
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
    WorkerStatus,
)
from app.shutdown import Stop, heard_stop
from app.site import (
    NOT_LISTED,
    PAGES,
    fill,
    html_response,
    listed_by_search_engines,
    moved,
    page_at,
    robots,
    sitemap,
)
from app.startup import prepare
from app.status import KEYWORDS_ONLY, MODELS_UNAVAILABLE, WORK_STOPPED, upload_limits
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


# The operator's area's page (app/operators.py): a page of its own, `frontend/operator.html`, with
# a bundle of its own, served at /operator and below it to an operator alone. Its own name is
# taken too, so that the file is reached by no other way.
OPERATOR_PAGE = "operator.html"
_OPERATOR_PATH = re.compile(r"operator(?:/.*)?|operator\.html")
# The public site's pages (app/site.py), served filled in at their paths alone.
SITE_FILES = frozenset(PAGES.values())


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
            views=services.converter is not None,
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
        views=services.converter is not None,
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


@cache
def _contract() -> dict[str, Any]:
    return published_contract()


@system.get("/v1/openapi.json", include_in_schema=False)
def api_contract(request: Request) -> JSONResponse:
    """The versioned API's published contract (app/api/v1/contract.py), for programs and the
    clients generated from it, with this deployment's address as its server, which the published
    file cannot know. Public, like the subprocessor list: whoever
    writes a program reads it before an owner gives them a key, and it holds no data."""
    return JSONResponse({**_contract(), "servers": [{"url": auth.app_url(request)}]})


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
    notes.router,
    comments.router,
    activity.router,
    duplicates.router,
    versions.router,
    query.router,
    chats.router,
    trash.router,
    templates.router,
    cross_checks.router,
    check_runs.router,
    automations.router,
    question_sets.router,
    reviews.router,
    drafts.router,
    mailbox.router,
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
            # The worker makes the views; the API only queues them.
            check_converter(app.state.services.converter)
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
    # Every request starts withholding what work cost, until its account shows it to an owner or
    # an admin (app/costs.py).
    app.add_middleware(CostsWithheld)
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
        webhooks.router,
        *KEYED,
        connectors.router,
        favorites.router,
        # The search of every project a member sees, which the page alone asks.
        search.router,
        share_links.router,
        # A share link's visitor has no account: the link and its view's cookie are checked
        # instead (app/share_links.py).
        share_links.public,
        audit.router,
        usage.router,
        billing.router,
        notifications.router,
        onboarding.router,
        support.router,
        help.router,
    ):
        app.include_router(router, prefix="/api", dependencies=[Depends(refuse_cross_site)])
    # The versioned API, which a program alone calls, with its key (app/api/v1/): it carries
    # no cookie, so the defence against another site's page needs no header of it.
    app.include_router(v1.router, prefix="/api")
    # The routes an API key reaches (`_key_account` in app/api/deps.py), by their functions,
    # which the request's scope names once its route is matched: the versioned API's, the page's
    # that a program has a use for, and the status, whose upload limits the copies of the upload
    # client downloaded before it moved onto the versioned API send their files within
    # (app/client/).
    app.state.keyed = frozenset(
        [
            *(route.endpoint for router in (*KEYED, *v1.ROUTERS) for route in router.routes),
            status,
        ]
    )
    # Stripe's and Resend's events carry no header of the app's: their signature proves where
    # they came from (app/api/billing.py, app/api/mail.py); nor does a mail program's one-click
    # unsubscribe, whose token says whose notices stop.
    app.include_router(billing.events, prefix="/api")
    app.include_router(mail.events, prefix="/api")
    # The operator's area asks for the app's header itself, once the operator is known, so that
    # a refusal tells nobody else that a route is there (app/api/operator.py).
    app.include_router(operator.router, prefix="/api")

    # What search engines are told of the public pages (app/site.py), before the page's
    # catch-all, which would answer the members' page.
    @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
    async def robots_txt(request: Request):
        return PlainTextResponse(
            robots(settings, auth.app_url(request)), headers={"Cache-Control": PAGE_KEPT}
        )

    @app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
    async def sitemap_xml(request: Request):
        if not settings.search_engines:
            return Response(status_code=404, headers={"X-Robots-Tag": NOT_LISTED})
        return Response(
            sitemap(auth.app_url(request)),
            media_type="application/xml",
            headers={"Cache-Control": PAGE_KEPT},
        )

    if FRONTEND_DIST.is_dir():
        # Serve the built frontend; unknown paths fall back to index.html for client routing.
        app.mount("/assets", BuiltFiles(FRONTEND_DIST / "assets", ASSETS_KEPT), name="assets")
        page = BuiltFiles(FRONTEND_DIST, PAGE_KEPT)

        # HEAD too, which uptime services often probe the page with.
        @app.api_route("/{path:page}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa(path: str, request: Request):
            response = await _page(path, request)
            if not listed_by_search_engines(path, settings):
                response.headers["X-Robots-Tag"] = NOT_LISTED
            if path.strip("/") == "":
                # / is the site's home page or the members' page by the session cookie.
                response.headers["Vary"] = "Cookie"
            return response

        async def _page(path: str, request: Request) -> Response:
            if (elsewhere := moved(path)) is not None:
                # A page put away: the plans, the help, the API reference, what's new (app/site.py).
                return RedirectResponse(elsewhere, status_code=302)
            if (site_page := page_at(path, request, settings)) is not None:
                if served := await to_thread.run_sync(_site_page, site_page, request):
                    return served
            if _OPERATOR_PATH.fullmatch(path):
                # Anyone but an operator gets the members' page, as for a path nobody serves.
                if await to_thread.run_sync(_from_operator, request):
                    return await page.get_response(OPERATOR_PAGE, request.scope)
                return await page.get_response("index.html", request.scope)
            if path in SITE_FILES:
                # The site's pages as written, their placeholders unfilled, by no other way.
                return await page.get_response("index.html", request.scope)
            try:
                return await page.get_response(path, request.scope)
            except StarletteHTTPException as missing:
                if missing.status_code != 404:
                    raise
            return await page.get_response("index.html", request.scope)

        def _site_page(name: str, request: Request) -> Response | None:
            """The site's page filled in, or None for a build without it, which leaves the
            path to the members' page."""
            try:
                html = (FRONTEND_DIST / name).read_text()
            except FileNotFoundError:
                return None
            return html_response(request, fill(html, settings, auth.app_url(request)))

    return app


configure_logging(get_settings().log_format)
start_tracking(get_settings())
app = create_app()
