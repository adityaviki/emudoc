import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.api import auth, chats, documents, folders, projects, query
from app.api.deps import RETRY_AFTER, AccountDep, ServicesDep, SessionDep, refuse_cross_site
from app.config import ROOT_DIR, get_settings
from app.db import BUSY, engine
from app.headers import SecurityHeaders, content_security_policy
from app.indexing.pipeline import Services
from app.mail import Mailer, mailer_from_settings
from app.schemas import EmbeddingStatus, HealthOut, LLMStatus, StatusOut, SubprocessorsOut
from app.startup import prepare
from app.storage import StorageError
from app.subprocessors import subprocessors
from app.worker import Worker

log = logging.getLogger(__name__)

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

system = APIRouter(tags=["system"])


@system.get("/health", response_model=HealthOut)
def health(session: SessionDep):
    """Whether the app can serve requests, for load balancers and uptime checks.

    It says nothing else: this route is public, so it must not reveal how the app is set up.
    """
    session.execute(text("SELECT 1"))
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


def storage_unavailable(request: Request, error: Exception) -> JSONResponse:
    """A storage that failed mid-request is reported like any other error the user can act on:
    the message says what happened, and 503 says it was not the request's fault."""
    return JSONResponse({"detail": str(error)}, status_code=503)


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
    ):
        app.include_router(router, prefix="/api", dependencies=[Depends(refuse_cross_site)])

    if FRONTEND_DIST.is_dir():
        # Serve the built frontend; unknown paths fall back to index.html for client routing.
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            file = FRONTEND_DIST / path
            if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
