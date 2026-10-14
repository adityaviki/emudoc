"""What every process does before it serves or works.

The API (app/main.py) and the worker (app/worker.py) start the same way: they refuse a database
or a storage this code was not written for, queue what a changed embedding model left stale,
build the services the pipeline and the agent run with, and in production refuse to run without
a usable model, with a model or a bucket reached in the clear across a network the deployment
does not own (app/network.py), or outside the region the deployment promises
(app/residency.py). One function
does all of it, so that the two processes refuse the same misconfigurations and a check added
for one is not forgotten by the other.
"""

import logging
import os
from collections.abc import Mapping

from app.config import Settings, export_env_file, model_provider
from app.db import check_schema
from app.embeddings import Embedder
from app.indexing.pipeline import Services
from app.llm import LLM, LLMUnavailable
from app.maintenance import check_embeddings, requeue_stale_embeddings, warn_about_old_layout
from app.network import MODEL_BASE_URLS, plain_http_problem
from app.residency import check_residency
from app.scanning import check_scanner, scanner_from_settings
from app.search import check_fts_language
from app.spending import warn_without_prices
from app.storage import Storage, report_encryption, storage_from_settings

log = logging.getLogger(__name__)

# What a model's address and the bucket's carry, for the sentences that refuse plain HTTP.
MODEL_READS = "the documents and the questions the model reads"
BUCKET_HOLDS = "every upload, and every page image a browser opens,"


def prepare(settings: Settings, services: Services | None = None) -> Services:
    """Run the start-up checks and return the services to run with.

    Tests pass their fakes as `services`; a process builds the real ones, after copying the
    model keys from `.env` in development, since the model providers and boto3 read their keys
    from the environment as they are created. A check that fails raises `RuntimeError`,
    `StorageError` or `ScanError` with what to do, and the process must not run.
    """
    check_schema()
    check_embeddings()
    check_fts_language()
    requeue_stale_embeddings()
    if services is None:
        export_env_file(settings)
        services = Services(
            LLM(settings),
            Embedder(settings),
            storage_from_settings(settings),
            scanner_from_settings(settings),
        )
    # Before the first request to the bucket, which would already cross the network.
    check_transport(settings, services.storage, os.environ)
    services.storage.check()
    report_encryption(services.storage)
    warn_about_old_layout(services.storage)
    warn_without_prices(settings)
    check_scanner(settings, services.scanner)
    check_model(settings, services.llm)
    # After `export_env_file`: the Vertex location and the base URLs that place a model are
    # read where the providers read them.
    check_residency(settings, services.storage, os.environ)
    return services


def check_model(settings: Settings, llm: LLM) -> None:
    """In production, refuse to start without a usable model, unless it was switched off.

    Development runs without one, with summaries, facts and the Ask tab off (README, "What
    works without what"). A deployment that silently offered that much less is misconfigured,
    not degraded, so it stops at once with the key to set. A model whose provider the operator
    switched off (DISABLED_PROVIDERS, app/llm.py) is the one exception: the switch exists to
    stop the spending while the app keeps serving, so the process starts and says what is off.
    """
    if settings.environment != "production":
        return
    try:
        llm.check()
    except LLMUnavailable as error:
        if not error.transient:
            raise RuntimeError(
                f"The model cannot be used, and production does not start without it. {error}"
            ) from None
        log.warning(
            "%s Until then the app runs without the model: no summaries, facts, scans or answers.",
            error,
        )


def check_transport(settings: Settings, storage: Storage, environ: Mapping[str, str]) -> None:
    """In production, refuse to start while a model's address or the bucket's would carry the
    documents over plain HTTP across a network the deployment does not own.

    The database, the embeddings endpoint and the scanner are refused before this, by
    `Settings.production_problems`, since the settings show them. These two only the
    environment shows: the address a model provider reads (`MODEL_BASE_URLS`), and the one
    boto3 settled on for the bucket, which its signed URLs lead to.
    """
    if settings.environment != "production":
        return
    problems = []
    # One sentence per variable: OPENAI_BASE_URL serves three prefixes.
    variables = {
        MODEL_BASE_URLS.get(model_provider(name)): None
        for name in (settings.index_model, settings.agent_model)
    }
    for variable in filter(None, variables):
        url = environ.get(variable, "")
        if problem := plain_http_problem(variable, url, MODEL_READS):
            problems.append(problem)
    if storage.origin and (
        problem := plain_http_problem("AWS_ENDPOINT_URL", storage.origin, BUCKET_HOLDS)
    ):
        problems.append(problem)
    if problems:
        lines = "\n".join(f"  - {problem}" for problem in problems)
        raise RuntimeError(
            "ENVIRONMENT is production, but these would send the documents unencrypted across "
            f"a network the deployment does not own:\n{lines}"
        )
