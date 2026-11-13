"""What every process does before it serves or works.

The API (app/main.py) and the worker (app/worker.py) start the same way: they refuse a database
or a storage this code was not written for, build the services the pipeline and the agent run
with, and in production refuse to run without a usable model or with an embeddings endpoint that
turns its requests down, with a pgvector that loses a project's passages to the search's filter
(app/postgres.py), with a model or a bucket reached in the clear across a network the deployment
does not own (app/network.py), or with a bucket that keeps deleted files longer than BACKUP_DAYS
says (`check_versions` in app/storage.py), and anywhere without a temporary directory to write in
or with a sample
project whose files its index does not describe (app/sample). What a team may run without, a mail
relay, a scanner, the word that a Gemini key is on the paid tier, is warned about at every start
of a deployment and refused nowhere. Then, refused by none of it, they queue what a changed
embedding model left stale. One function does all of it,
so that the two processes refuse the same misconfigurations and a check added for one is not
forgotten by the other.
"""

import logging
import os
import tempfile
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.config import (
    AGENT,
    BUNDLED_LIBPQ,
    INDEX,
    Settings,
    export_env_file,
    listing,
    model_provider,
)
from app.conversion import converter_from_settings
from app.db import check_schema
from app.embeddings import Embedder, EmbeddingError
from app.encrypted import check_encrypted
from app.indexing.pipeline import Services
from app.llm import LLM, LLMUnavailable
from app.mail import mailer_from_settings, sent_through
from app.maintenance import check_embeddings, requeue_stale_embeddings, warn_about_old_layout
from app.network import MODEL_BASE_URLS, plain_http_problem, trust_store_problem
from app.postgres import check_database
from app.sample import check_sample
from app.scanning import check_scanner, scanner_from_settings
from app.search import check_fts_language
from app.storage import Storage, check_versions, report_encryption, storage_from_settings
from app.switches import switched_off
from app.version import VERSION

log = logging.getLogger(__name__)

# What stops while a role's model cannot answer and nothing takes its place.
UNANSWERED = {
    INDEX: "no document gets its summaries, facts or scanned pages read",
    AGENT: "no question is answered",
}
# What a model's address and the bucket's carry, for the sentences that refuse plain HTTP.
MODEL_READS = "the documents and the questions the model reads"
BUCKET_HOLDS = "every upload, and every page image a browser opens,"
# What to look at when the embeddings endpoint turns the start's request down.
EMBEDDING_SETTINGS = (
    "Check that EMBEDDING_BASE_URL is the address /embeddings is under, such as "
    "http://embeddings:8080/v1 for the compose file's server, that the endpoint serves "
    "EMBEDDING_MODEL (Ollama's after `ollama pull`) as vectors of EMBEDDING_DIM, and "
    "EMBEDDING_API_KEY if it asks for one."
)
# What a deployment without a mail relay goes without, said at every start (`check_mail`).
NO_MAIL = (
    "No mail relay is set (SMTP_URL, or RESEND_API_KEY for Resend), so nothing is mailed: an "
    "owner or admin hands invitations and password-reset links on from the members' page, a "
    "member who forgot their password asks one of them, the notices are told in the app alone, "
    "and nobody can move their account to a new address. Set one to mail them."
)
NO_SIGNUP = (
    "SIGNUP_OPEN is true, but a sign-up is finished from the link mailed to its address, and "
    "nothing is mailed, so nobody can sign up; invite them from the members' page instead, or "
    "set a mail relay."
)


def prepare(settings: Settings, services: Services | None = None) -> Services:
    """Run the start-up checks and return the services to run with.

    Tests pass their fakes as `services`; a process builds the real ones, after copying the
    model keys from `.env` in development, since the model providers and boto3 read their keys
    from the environment as they are created. A check that fails raises `RuntimeError`,
    `StorageError` or `ScanError` with what to do, and the process must not run.
    """
    # First, so that what a start logs, a refusal included, is read against the release that
    # logged it.
    log.info("Starting Emudoc %s", VERSION)
    check_temporary_directory()
    warn_about_trust_store(settings, os.environ)
    check_schema()
    check_database(settings)
    check_embeddings()
    check_fts_language()
    check_encrypted()
    check_sample(settings)
    if services is None:
        export_env_file(settings)
        services = Services(
            LLM(settings, switched_off),
            Embedder(settings),
            storage_from_settings(settings),
            scanner_from_settings(settings),
            mailer_from_settings(settings),
            converter_from_settings(settings),
        )
    # Before the first request to the bucket, which would already cross the network.
    check_transport(settings, services.storage, os.environ)
    services.storage.check()
    report_encryption(services.storage)
    check_versions(settings, services.storage)
    warn_about_old_layout(services.storage)
    check_scanner(settings, services.scanner)
    check_mail(settings)
    check_model(settings, services.llm)
    check_embedder(settings, services.embedder)
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
    """In production, refuse to start without a usable model, unless it was switched off, and
    say which models do the work and which take it over.

    Development runs without one, with summaries, facts and the Ask tab off (README, "What
    works without what"). A deployment that silently offered that much less is misconfigured,
    not degraded, so it stops at once with the key to set; a fallback that cannot be set up is
    refused as its primary would be, since it would be found out at the first outage. A model
    whose provider the operator switched off (DISABLED_PROVIDERS, app/llm.py) is the one
    exception: the switch exists to stop the spending while the app keeps serving, so the
    process starts and says what is off. The models are built, not asked: an answer at every
    start of every replica would cost tokens, and an outage of the first model would hold a
    release its fallback could serve. `python -m app.maintenance models` asks them
    (app/trial.py).
    """
    for role, work in ((INDEX, "Documents are indexed"), (AGENT, "Questions are answered")):
        primary, *fallbacks = settings.models_for(role)
        if fallbacks:
            log.info(
                "%s by %s, and by %s when it cannot answer", work, primary.name, fallbacks[0].name
            )
        else:
            log.info("%s by %s", work, primary.name)
            if settings.environment == "production":
                log.warning(
                    "%s has no fallback, so while %s is down, refuses the key or has been "
                    "retired, %s; set %s_FALLBACK_MODEL, best to a model of another provider.",
                    primary.variable,
                    primary.name,
                    UNANSWERED[role],
                    primary.variable.removesuffix("_MODEL"),
                )
    if settings.environment != "production":
        return
    gemini = [model.variable for model in settings.models if model_provider(model.name) == "google"]
    if gemini and not settings.google_paid_tier:
        log.warning(
            "%s the Gemini Developer API (google:), whose free tier lets Google use the documents "
            "and questions it is sent to improve its products and have people read some of them, "
            "and the app cannot tell a free key from a paid one. Once billing is on for the "
            "key's Google Cloud project, set GOOGLE_PAID_TIER=true to say so, or use Vertex AI "
            "as google-cloud:<model>.",
            f"{listing(gemini)} {'uses' if len(gemini) == 1 else 'use'}",
        )
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


def check_mail(settings: Settings) -> None:
    """At start, say how the mail goes, and in a deployment that mails nothing, what goes
    without.

    A team may run without a relay: the links the app mails are handed on by an owner or admin
    instead (S.1), and the log keeps them out (`LogMailer`). So the start refuses nothing, but
    says it at every start, as it does a scanner left out (`check_scanner`).
    """
    way = sent_through(settings)
    if way == "resend":
        log.info("Mail is sent through Resend")
    elif way == "smtp":
        log.info("Mail is sent through the relay at %s", urlsplit(settings.smtp_url).hostname)
    elif settings.environment == "production":
        log.warning(NO_MAIL)
        if settings.signup_open:
            log.warning(NO_SIGNUP)


def check_embedder(settings: Settings, embedder: Embedder) -> None:
    """Ask the embeddings endpoint for one vector at start, and in production refuse one that
    turns the request down.

    Without vectors, search matches keywords only and the agent finds less, while the embed
    stage of every document fails, is tried again and is given up. In production an endpoint
    that refuses the request, with a wrong address, model, size or key, is misconfigured, not
    degraded, and the process stops with the settings to look at. One that is out of reach or
    busy may be starting beside the app, as the compose file's server does, so the process
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
    variables = {MODEL_BASE_URLS.get(model_provider(model.name)): None for model in settings.models}
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
