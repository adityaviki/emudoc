"""What every process does before it serves or works.

The API (app/main.py) and the worker (app/worker.py) start the same way: they refuse a database
or a storage this code was not written for, build the services the pipeline and the agent run
with, and in production refuse to run without a usable model or with an embeddings endpoint that
turns its requests down, with a pgvector that loses a project's passages to the search's filter
(app/postgres.py), with a model or a bucket reached in the clear across a network the deployment
does not own (app/network.py), with a bucket that keeps deleted files longer than the privacy
policy says (`check_versions` in app/storage.py), or outside the region the deployment promises
(app/residency.py), and anywhere without a temporary directory to write in; then, refused by
none of it, they queue what a changed embedding model left stale. One function does all of it,
so that the two processes refuse the same misconfigurations and a check added for one is not
forgotten by the other.
"""

import logging
import os
import tempfile
from collections.abc import Mapping

from app.config import BUNDLED_LIBPQ, Settings, export_env_file, model_provider
from app.db import check_schema
from app.embeddings import Embedder, EmbeddingError
from app.indexing.pipeline import Services
from app.llm import LLM, LLMUnavailable
from app.maintenance import check_embeddings, requeue_stale_embeddings, warn_about_old_layout
from app.network import MODEL_BASE_URLS, plain_http_problem, trust_store_problem
from app.postgres import check_database
from app.residency import check_residency
from app.scanning import check_scanner, scanner_from_settings
from app.search import check_fts_language
from app.spending import warn_without_prices
from app.storage import Storage, check_versions, report_encryption, storage_from_settings

log = logging.getLogger(__name__)

# What a model's address and the bucket's carry, for the sentences that refuse plain HTTP.
MODEL_READS = "the documents and the questions the model reads"
BUCKET_HOLDS = "every upload, and every page image a browser opens,"
# What to look at when the embeddings endpoint turns the start's request down.
EMBEDDING_SETTINGS = (
    "Check that EMBEDDING_BASE_URL is the address /embeddings is under, such as "
    "http://ollama:11434/v1, that the endpoint serves EMBEDDING_MODEL (Ollama's after `ollama "
    "pull`) as vectors of EMBEDDING_DIM, and EMBEDDING_API_KEY if it asks for one."
)


def prepare(settings: Settings, services: Services | None = None) -> Services:
    """Run the start-up checks and return the services to run with.

    Tests pass their fakes as `services`; a process builds the real ones, after copying the
    model keys from `.env` in development, since the model providers and boto3 read their keys
    from the environment as they are created. A check that fails raises `RuntimeError`,
    `StorageError` or `ScanError` with what to do, and the process must not run.
    """
    check_temporary_directory()
    warn_about_trust_store(settings, os.environ)
    check_schema()
    check_database(settings)
    check_embeddings()
    check_fts_language()
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
    check_versions(settings, services.storage)
    warn_about_old_layout(services.storage)
    warn_without_prices(settings)
    check_scanner(settings, services.scanner)
    check_model(settings, services.llm)
    check_embedder(settings, services.embedder)
    # After `export_env_file`: the Vertex location and the base URLs that place a model are
    # read where the providers read them.
    check_residency(settings, services.storage, os.environ)
    # Last: a start refused for its settings, a mistyped EMBEDDING_MODEL among them, must not
    # first queue every document to be embedded again.
    requeue_stale_embeddings()
    return services


def check_temporary_directory() -> None:
    """Refuse to start without a temporary directory to write in, and warn when TMPDIR names
    one that Python passed over.

    The API writes every upload there before it stores it, and the API and the worker copy an
    original there from a bucket to scan, parse and render it, so a process without one would
    start and then fail every upload. The image runs with a read-only filesystem and needs a
    tmpfs at /tmp or TMPDIR naming a volume (deploying.md, "The image"). Python takes the
    first of TMPDIR, /tmp, /var/tmp and the working directory that it can write in, so a
    TMPDIR it cannot is passed over in silence, and the documents land in the clear on a disk
    the operator did not choose for them.
    """
    try:
        directory = tempfile.gettempdir()
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError as error:
        raise RuntimeError(
            f"No temporary directory can be written in ({error}), and every upload passes "
            "through one. Mount a tmpfs at /tmp, or set TMPDIR to a directory this process can "
            "write in."
        ) from None
    named = os.environ.get("TMPDIR")
    if named and os.path.realpath(named) != os.path.realpath(directory):
        log.warning(
            "TMPDIR names %s, which this process cannot write in, so the temporary files, "
            "uploads among them, go to %s instead.",
            named,
            directory,
        )


def warn_about_trust_store(settings: Settings, environ: Mapping[str, str]) -> None:
    """In development, say before the first connection why a database URL that trusts the
    system's authorities will fail to verify the server; production refuses it with the other
    settings (`Settings.production_problems`)."""
    if settings.environment == "production":
        return
    for variable, url in (
        ("DATABASE_URL", settings.database_url),
        ("DATABASE_LISTEN_URL", settings.database_listen_url),
    ):
        if url and (problem := trust_store_problem(variable, url, environ, BUNDLED_LIBPQ)):
            log.warning(problem)


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


def check_embedder(settings: Settings, embedder: Embedder) -> None:
    """Ask the embeddings endpoint for one vector at start, and in production refuse one that
    turns the request down.

    Without vectors, search matches keywords only and the agent finds less, while the embed
    stage of every document fails, is tried again and is given up. In production an endpoint
    that refuses the request, with a wrong address, model, size or key, is misconfigured, not
    degraded, and the process stops with the settings to look at. One that is out of reach or
    busy may be starting beside the app, as the compose file's Ollama does, so the process
    starts and says so; development starts either way.
    """
    try:
        embedder.probe()
    except EmbeddingError as error:
        if error.transient:
            log.warning(
                "%s Until it answers, search matches keywords only, and the embed stage of "
                "each document indexed meanwhile is tried again.",
                error,
            )
        elif settings.environment == "production":
            raise RuntimeError(
                "The embeddings endpoint turns requests down, and production does not start "
                f"without it. {error} {EMBEDDING_SETTINGS}"
            ) from None
        else:
            log.warning("%s Search matches keywords only. %s", error, EMBEDDING_SETTINGS)
        return
    log.info("Passages are embedded by %s at %s", embedder.model, embedder.address)


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
