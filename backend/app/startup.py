"""What every process does before it serves or works.

The API (app/main.py) and the worker (app/worker.py) start the same way: they refuse a database
or a storage this code was not written for, queue what a changed embedding model left stale,
build the services the pipeline and the agent run with, and in production refuse to run without
a usable model. One function does all of it, so that the two processes refuse the same
misconfigurations and a check added for one is not forgotten by the other.
"""

from app.config import Settings, export_env_file
from app.db import check_schema
from app.embeddings import Embedder
from app.indexing.pipeline import Services
from app.llm import LLM
from app.maintenance import check_embeddings, requeue_stale_embeddings, warn_about_old_layout
from app.search import check_fts_language
from app.storage import storage_from_settings


def prepare(settings: Settings, services: Services | None = None) -> Services:
    """Run the start-up checks and return the services to run with.

    Tests pass their fakes as `services`; a process builds the real ones, after copying the
    model keys from `.env` in development, since the model providers and boto3 read their keys
    from the environment as they are created. A check that fails raises `RuntimeError` or
    `StorageError` with what to do, and the process must not run.
    """
    check_schema()
    check_embeddings()
    check_fts_language()
    requeue_stale_embeddings()
    if services is None:
        export_env_file(settings)
        services = Services(LLM(settings), Embedder(settings), storage_from_settings(settings))
    services.storage.check()
    warn_about_old_layout(services.storage)
    check_model(settings, services.llm)
    return services


def check_model(settings: Settings, llm: LLM) -> None:
    """In production, refuse to start without a usable model.

    Development runs without one, with summaries, facts and the Ask tab off (README, "What
    works without what"). A deployment that silently offered that much less is misconfigured,
    not degraded, so it stops at once with the key to set.
    """
    if settings.environment == "production" and (problem := llm.problem()):
        raise RuntimeError(
            f"The model cannot be used, and production does not start without it. {problem}"
        )
