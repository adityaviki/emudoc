import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import auth, chats, documents, folders, projects, query
from app.api.deps import AccountDep, ServicesDep, SessionDep, refuse_cross_site
from app.config import ROOT_DIR, export_env_file, get_settings
from app.db import migrate, reconcile_embeddings
from app.embeddings import Embedder
from app.indexing.pipeline import Services
from app.indexing.worker import Worker
from app.llm import LLM
from app.mail import Mailer, mailer_from_settings
from app.schemas import EmbeddingStatus, HealthOut, LLMStatus, StatusOut

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
    )


def create_app(
    services: Services | None = None,
    start_worker: bool = True,
    mailer: Mailer | None = None,
    http: httpx.Client | None = None,
) -> FastAPI:
    """The application. Tests pass fakes for the services, the mailer and the outbound client."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.files_dir.mkdir(parents=True, exist_ok=True)
        settings.pages_dir.mkdir(parents=True, exist_ok=True)
        migrate()
        reconcile_embeddings()
        if services is None:
            export_env_file()
        app.state.services = services or Services(LLM(settings), Embedder(settings))
        app.state.mailer = mailer or mailer_from_settings(settings)
        app.state.http = http or httpx.Client(timeout=10)
        app.state.worker = None
        if start_worker:
            app.state.worker = Worker(app.state.services, settings.worker_concurrency)
            app.state.worker.start()
        yield
        if app.state.worker:
            app.state.worker.stop()
        app.state.http.close()

    app = FastAPI(title="Emudoc", lifespan=lifespan)
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
