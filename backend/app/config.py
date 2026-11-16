import os
import re
from collections.abc import Mapping
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import urlsplit

from dotenv import dotenv_values
from psycopg import pq
from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.keyring import DEVELOPMENT_KEY, NEW_KEY, parse_keys
from app.network import (
    database_problem,
    plain_http_problem,
    scanner_problem,
    trust_store_problem,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
# A developer's settings and keys, which development copies into the environment
# (`export_env_file`); the test suite points it at no file.
ENV_FILE = ROOT_DIR / ".env"

Effort = Literal["", "minimal", "low", "medium", "high", "xhigh"]

# The password of the database docker-compose.yml runs, which only this machine can reach.
# A deployment that carries it has copied the development setup, so production refuses it.
DEVELOPMENT_PASSWORD = "emudoc"
# The threads a worker runs besides its indexing and answering threads that take a connection
# from the pool now and then: the heartbeat, the housekeeper and the alerter (app/worker.py),
# and the metrics' server reading the database (app/metrics.py). The listener holds a
# connection of its own outside the pool. With MAIL_EVENTS_QUEUE, one more reads SES's news of
# mail that bounced (app/bounces.py), and with INBOUND_MAIL_QUEUE one more SES's notices of the
# mail sent to projects' addresses (app/receiving.py).
WORKER_HELPER_THREADS = 4
# The worker's threads that send the mail the app posted (app/postbox.py), a lane of their own
# so that neither a backlog of documents nor one of questions holds a sign-up link up.
MAIL_THREADS = 1
METRICS_ADDRESS_FORM = (
    "METRICS_ADDRESS is a host and port, such as 127.0.0.1:9464, 0.0.0.0:9464 in a container or "
    "[::]:9464, or empty for no metrics"
)
STORAGE_URL_FORMS = (
    "STORAGE_URL names a directory as file:///var/lib/emudoc or an S3-compatible bucket as "
    "s3://bucket/prefix"
)
MODEL_PRICES_FORM = (
    "MODEL_PRICES lists what each model costs per million tokens in and out, as "
    "model=in/out separated by commas, such as google:gemini-3.5-flash-lite=0.30/2.50"
)
SCANNER_URL_FORMS = (
    "SCANNER_URL names the ClamAV daemon that scans uploads as clamd://host:3310 or, for one on "
    "this machine, clamd:///run/clamav/clamd.ctl; `off` runs without scanning"
)
SCANNER_OFF = "off"
CONVERTER_URL_FORM = (
    "CONVERTER_URL names the converter that shows Word, PowerPoint and Excel files as they look "
    "as http://converter:8080, its address in the compose file; empty or `off` converts nothing"
)
# A host name of labels joined by dots, the last of letters or an IDN's punycode, as
# app/invitations.py takes a domain.
INBOUND_DOMAIN = re.compile(
    r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})"
)
INBOUND_DOMAIN_FORM = (
    "INBOUND_DOMAIN is the domain of the projects' addresses, such as in.example.com, or empty "
    "for none"
)
CLAMD_PORT = 3310
# The work a model does: indexing (INDEX_MODEL: sections, summaries, facts, scans) or answering
# (AGENT_MODEL: the Ask tab's agent), each with its fallback.
INDEX = "index"
AGENT = "agent"
# What a page's setting is, said when it is not a web address.
PAGES = {
    "status_page_url": "STATUS_PAGE_URL is the address of the status page, such as "
    "https://status.example.com",
    "source_url": "SOURCE_URL is the address of the code's source, such as "
    "https://git.example.com/team/emudoc",
}
# The model a development checkout runs when .env names none. Production refuses to run on it
# (`Settings.production_problems`): a default moves with the code and the provider retires
# models on a schedule of its own, so a deployment names the model it was tried with.
DEFAULT_MODEL = "google:gemini-3.5-flash-lite"
# Names a provider moves to a newer model when it likes: Google's `gemini-flash-latest`,
# OpenAI's `chatgpt-4o-latest`, Anthropic's former `-latest` aliases, a `:latest` tag, and
# OpenRouter's router, which picks a model per request.
MOVING_ALIAS = re.compile(r"(?:^|[-_.:/])latest$|^openrouter/auto$", re.IGNORECASE)


def parse_metrics_address(value: str) -> tuple[str, int] | None:
    """What METRICS_ADDRESS names: the (host, port) to serve the metrics at, or None for none
    (empty). Raises `ValueError` naming the form for anything else."""
    value = value.strip()
    if not value:
        return None
    host, _, port = value.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    elif ":" in host:
        # An IPv6 address without its brackets, whose port cannot be told from its last group.
        host = ""
    if host and port.isascii() and port.isdigit() and 0 < int(port) < 65536:
        return host, int(port)
    raise ValueError(f"{METRICS_ADDRESS_FORM}; {value!r} is not.")


def parse_scanner_url(value: str) -> tuple[str, int] | str | None:
    """What SCANNER_URL names: a (host, port) pair, a socket path, or None for no scanner
    (empty or `off`). Raises `ValueError` naming the forms for anything else."""
    value = value.strip()
    if not value or value.lower() == SCANNER_OFF:
        return None
    parts = urlsplit(value)
    if parts.scheme == "clamd" and not parts.query and not parts.fragment:
        if parts.netloc and parts.hostname and not parts.path:
            try:
                return parts.hostname, parts.port or CLAMD_PORT
            except ValueError:
                pass
        elif not parts.netloc and parts.path.startswith("/"):
            return parts.path
    raise ValueError(f"{SCANNER_URL_FORMS}; {value!r} is neither.")


def parse_converter_url(value: str) -> str | None:
    """The converter's address CONVERTER_URL names, without a slash at its end, or None for no
    converter (empty or `off`). Raises `ValueError` naming the form for anything else."""
    value = value.strip()
    if not value or value.lower() == SCANNER_OFF:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme in ("http", "https")
        and parts.hostname
        and not parts.query
        and not parts.fragment
        and parts.path in ("", "/")
    ):
        return value.rstrip("/")
    raise ValueError(f"{CONVERTER_URL_FORM}; {value!r} is not one.")


class ModelSetting(NamedTuple):
    """A model the settings name: the variable that names it, the `provider:model` name, and
    the work it does (INDEX or AGENT)."""

    variable: str
    name: str
    role: str

    @property
    def fallback(self) -> bool:
        """Whether the model is asked only when its role's first model cannot answer."""
        return self.variable.endswith("_FALLBACK_MODEL")


class ModelPrice(NamedTuple):
    """What a model costs per million tokens in and out, in PRICE_CURRENCY."""

    input: Decimal
    output: Decimal


@lru_cache
def parse_model_prices(text: str) -> dict[str, ModelPrice]:
    """The prices MODEL_PRICES lists, by model name as the settings name it."""
    prices: dict[str, ModelPrice] = {}
    for entry in filter(None, (part.strip() for part in text.split(","))):
        model, equals, pair = entry.rpartition("=")
        model = model.strip()
        try:
            # A model name has no whitespace: one with some is two entries missing a comma.
            if not equals or not model or any(char.isspace() for char in model):
                raise ValueError
            input_price, output_price = (Decimal(price.strip()) for price in pair.split("/"))
            if not (input_price >= 0 and output_price >= 0):
                raise ValueError
        except (ValueError, ArithmeticError):
            # A Decimal that cannot be read raises an ArithmeticError, not a ValueError.
            raise ValueError(f"{MODEL_PRICES_FORM}; {entry!r} is not of that form.") from None
        prices[model] = ModelPrice(input_price, output_price)
    return prices


class Settings(BaseSettings):
    """How this process runs, read once from the environment and `.env` and shared.

    These are read as the app is imported (`app/db.py` builds the engine from them, `app/models.py`
    sizes the vector column) and cached for the life of the process, so they can only hold what
    is the same for every organisation the process serves. Anything that varies per organisation,
    such as a setting its owners and admins choose, belongs in a table read through the account
    of the request, never here.
    """

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # How the process is run. In development, `.env` is the place for everything, the model
    # keys included (see `export_env_file`). In production the process is configured by its
    # environment, set by the deployment or a secret manager, and secrets never go through a
    # file in the repository; a staging deployment is production too.
    environment: Literal["development", "production"] = "development"
    # How the API, the worker and the migrations write their log (app/logs.py): `json`, one
    # object a line with the ids of the request or job it was logged about, for a collector to
    # search; `text`, lines for a person at a terminal. Unset, json in production and text in
    # development. The maintenance commands write text whatever it says, since a person reads
    # them. There is no level to set: below INFO, the libraries log what they send, documents
    # and questions included.
    log_format: Literal["text", "json"] | None = None
    # Where the API and the worker report the errors nobody planned for (app/tracking.py): a
    # Sentry DSN, https://<key>@<host>/<project>, which GlitchTip and a Sentry of the operator's
    # own take too. Empty reports nothing; the log has every error either way. A report holds
    # what the log would show of the error, never a document's text, a question, an answer, a
    # key or an email address.
    sentry_dsn: str = ""
    # The environment the tracker files the reports under, such as staging; empty takes
    # ENVIRONMENT, which is production on staging too.
    sentry_environment: str = ""
    # Where the API and the worker serve their metrics, in Prometheus's format (app/metrics.py):
    # host:port, on a port of their own that only the deployment's network may reach, never one
    # the proxy forwards, since they say how large the deployment is and which models it pays
    # for. Empty serves none. The image listens on 0.0.0.0:9464 of its container.
    metrics_address: str = ""

    # The database, as a SQLAlchemy URL. There is no default: a process that was not told its
    # database stops (see `load_settings`) rather than reach for a loopback server with a
    # well-known password. `.env.example` names the docker-compose.yml container.
    database_url: str
    # The pooler DATABASE_URL goes through, if any (deploying.md, "The database"). Empty for a
    # direct connection or a pooler in session mode, which gives each client a server
    # connection of its own. `transaction` for a pooler in transaction mode, such as
    # pgbouncer's usual setup, which hands a client a server connection for one transaction at
    # a time: the app then prepares no statement (pgbouncer before 1.21, or without
    # max_prepared_statements, cannot carry one to the next transaction), and the worker
    # listens for queued jobs at DATABASE_LISTEN_URL, since a LISTEN outlives the transaction
    # it was made in.
    database_pooler: Literal["", "transaction"] = ""
    # Where the worker's listener connects (app/jobs.py) when DATABASE_URL goes through a
    # pooler in transaction mode: the database itself, or the pooler's port in session mode.
    # Empty, the listener uses DATABASE_URL; behind a pooler in transaction mode the worker then
    # does not listen, and its threads find queued jobs by polling, every two seconds.
    database_listen_url: str = ""
    # Where uploaded files and rendered pages are kept (see app/storage.py): a directory on
    # this machine as file:///var/lib/emudoc, or an S3-compatible bucket as s3://bucket/prefix,
    # reached with the AWS_* variables in the environment. The default is backend/data inside
    # the checkout, for development.
    storage_url: str = (BACKEND_DIR / "data").as_uri()
    # The ClamAV daemon that scans every upload before it is indexed or served
    # (app/scanning.py): clamd://host:3310, or clamd:///run/clamav/clamd.ctl for one on this
    # machine. Empty or `off`, nothing is scanned, every document says so, and a production
    # start warns.
    scanner_url: str = ""
    # The converter (app/converter.py, the image run as a container of its own) that turns a
    # Word, PowerPoint, Excel, RTF or HTML upload into the PDF the document pane shows it by,
    # page by page, as it looks (app/conversion.py): http://converter:8080 in the compose file.
    # Empty or `off`, nothing is converted, and the pane shows those documents by their text.
    converter_url: str = ""
    # What one upload may bring: the size of a file, how many files one request carries and
    # their bytes together. A file over its size is rejected on its own while the others go
    # in; a request over the other two is refused whole with 413 (app/api/documents.py). The
    # bytes have been read by the time the request is refused, so a proxy's body limit is the
    # guard against the request itself, and this is what the app will store from one.
    max_upload_mb: int = Field(default=100, ge=1)
    max_upload_files: int = Field(default=100, ge=1)
    max_upload_request_mb: int = Field(default=500, ge=1)
    # How long a rendered page or thumbnail is kept after it was rendered, in days. They are a
    # cache of the original (see app/storage.py): one that is asked for again is rendered
    # again. Zero keeps them for good.
    page_image_days: int = Field(default=30, ge=0)
    # The parse stage runs the parsers on an upload in a child process of their own
    # (app/parsing/limits.py), so that a file that takes too long or too much memory stops that
    # process and not the worker: the seconds it may run, the memory it may hold, the pages of a PDF
    # or a TIFF, the slides of a presentation and the rows of one sheet it takes on, and the
    # characters of text one document may hold. The text is what every later stage pays for, the
    # worker holding it, its passages written, indexed and embedded (a passage is some 1,200 to
    # 2,000 characters), so a file over it is refused before the child replies. Zero removes a
    # limit. A PDF's pages take the time one after another, about a
    # tenth of a second each, so a PDF of more pages than PARSE_PART_PAGES is read that many
    # pages a child, each within the limits, and the document made of the parts by one more;
    # zero reads every PDF in one child.
    parse_timeout: int = Field(default=300, ge=0)
    parse_memory_mb: int = Field(default=2048, ge=0)
    parse_max_pages: int = Field(default=5_000, ge=0)
    parse_max_rows: int = Field(default=100_000, ge=0)
    parse_max_chars: int = Field(default=20_000_000, ge=0)
    parse_part_pages: int = Field(default=250, ge=0)
    # A PDF's pages are drawn as images (for the document pane, the thumbnails and OCR) by
    # child processes each process keeps (app/rendering.py), since pdfium draws one page at a
    # time in a process: how many pages a process draws at once, the seconds a page may take
    # and the memory a child may hold (zero removes either limit). And how many of a PDF's first
    # pages are drawn ahead once it is parsed, so that opening it shows them at once; zero
    # draws every page when it is first looked at. From a bucket a page is drawn from a copy of
    # the original in the temporary directory, which a process keeps a few minutes for the
    # next pages, up to this many megabytes of copies (zero keeps none past its page).
    render_processes: int = Field(default=2, ge=1)
    render_timeout: int = Field(default=30, ge=0)
    render_memory_mb: int = Field(default=1024, ge=0)
    render_cache_mb: int = Field(default=256, ge=0)
    prerender_pages: int = Field(default=3, ge=0)
    # The pages of one document the model may read as scans: each costs a model call, and
    # PARSE_MAX_PAGES alone would let a scanned book cost hundreds. A document over it fails
    # with a message that says to split it, before a page is read (app/indexing/pipeline.py).
    # Zero removes the limit.
    ocr_max_pages: int = Field(default=100, ge=0)
    # The pages of one scan read at once: each drawn by one of the worker's render children,
    # then sent to the model, the next started as one is answered. A hosted model takes
    # seconds a page, so one at a time made a long scan the slowest thing a worker did; each
    # indexer reading a scan sends this many requests at once, which the provider's rate
    # limit counts (deploying.md, "Large documents").
    ocr_concurrency: int = Field(default=4, ge=1)
    # The requests to the model one document's summaries and facts may take (app/indexing/
    # enrich.py, a request for each 40,000 characters and one to roll them up): a longer
    # document is summarised from its start as far as they reach, and its enrich stage says
    # how far, rather than refused, since everything is searched without them. The day's
    # spending cap is checked before the stage, so this is the most a document can spend
    # past it. Zero removes the limit.
    enrich_max_requests: int = Field(default=50, ge=0)

    # The model behind structure inference, summaries, facts, OCR and the agent, as
    # provider:model. Each provider reads its own key from the environment (see .env.example).
    # Production refuses the defaults and the aliases a provider moves (MOVING_ALIAS).
    agent_model: str = DEFAULT_MODEL
    index_model: str = DEFAULT_MODEL
    # The model asked instead when the one above cannot answer: its provider is down,
    # overloaded or out of reach, or refuses the key, the account or the model itself, as it
    # does once the model is retired (app/llm.py). Empty, there is none. Best from another
    # provider, which an outage or a revoked key of the first does not reach. A fallback is
    # sent what its primary is, so it is held to the same transport, and needs its price in
    # MODEL_PRICES.
    agent_fallback_model: str = ""
    index_fallback_model: str = ""
    # How hard the model thinks. Empty leaves the model's own default; models that cannot
    # think ignore it.
    agent_effort: Effort = "medium"
    index_effort: Effort = "low"
    # The rounds of tool calls one answer may make before it is stopped with what it has
    # (app/agent/loop.py): each is a model request, so this bounds what a question can cost.
    agent_max_turns: int = Field(default=12, ge=1)
    # What the models cost, for the cost on every usage row (app/usage.py): model=in/out per
    # million tokens, separated by commas, with the models named as AGENT_MODEL,
    # INDEX_MODEL and EMBEDDING_MODEL name them, in PRICE_CURRENCY (an ISO 4217 code). Empty,
    # no cost is recorded. An embedding model is priced for the tokens it is sent as the
    # endpoint reports them, so its price out is 0; an endpoint that reports none costs
    # nothing. The prices are what the install pays its providers.
    model_prices: str = ""
    price_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    # What every organisation together may spend on models in one UTC day, in PRICE_CURRENCY,
    # by the costs the usage rows carry (app/spending.py): past it, every question is refused
    # with 503 until midnight UTC and every indexing stage that needs a model waits for it.
    # Zero is no limit. It is the most the providers' bills show for one day. It counts nothing
    # without MODEL_PRICES, so production refuses the one without the other.
    daily_spend_cap: Decimal = Field(default=Decimal(0), ge=0)
    # The kill switch: the model providers switched off, by the prefix of a provider:model
    # name, separated by commas (DISABLED_PROVIDERS=google,anthropic). A model of one cannot
    # be used until the name is removed and the process restarted (app/llm.py); meanwhile the
    # app runs as it does without a key, and says why. For a bill that is running away or a
    # key that leaked, when midnight is too far off.
    disabled_providers: str = ""
    # Whether billing is on for the Google Cloud project behind GOOGLE_API_KEY, which is what
    # puts the Gemini Developer API (google: models) under Google's paid terms: no use of the
    # prompts to improve its products, and its data processing addendum. On the free tier
    # Google may use the documents and questions it is sent and have people read them, and
    # nothing in a key or a reply tells the two apart, so a production start warns about
    # google: models until this says so (app/startup.py). Vertex AI (google-cloud: models) runs
    # under a Cloud project's own terms and needs no such word.
    google_paid_tier: bool = False
    # Where members' messages to support go: what they write from the app's help menu, with
    # the page and the request it was about (app/api/help.py), and the address the mail about an
    # account's address names for help. Empty sends the messages to the workspace's other
    # owners and admins, and the mail names no address.
    support_email: str = ""
    # The public status page that says whether the service is up, run outside the deployment
    # it reports on (deploying.md, "The status page"): the help menu and the page that cannot
    # reach its server link to it. Empty links to none.
    status_page_url: str = ""
    # Where the source of the code this deployment runs is, which the help menu offers every
    # member as the AGPL asks (app/version.py, `source_url`). Empty is the public repository at
    # the running release; a team that runs code of its own changes points it at its copy.
    source_url: str = ""
    # How many days the deployment keeps the backups of its database and bucket, zero for
    # none, unset for not said. The app makes none itself; at start it holds the bucket's
    # earlier versions to this (app/storage.py, `check_versions`), and `encrypt-secrets` says
    # how long a retired key must stay for a restore, since a purged account stays in a backup
    # until it expires (app/retention.py purges it again after a restore).
    backup_days: int | None = Field(default=None, ge=0)

    # Embeddings: any OpenAI-compatible /embeddings endpoint (app/embeddings.py), sent every
    # passage as it is indexed and every search. The default is an Ollama on this machine,
    # for development; production refuses to start without one named, and asks it once at
    # start (app/startup.py). The model is Google's EmbeddingGemma, which reads a hundred
    # languages and matches a question with a passage in another (deploying.md, "The
    # embeddings endpoint"), and the prefixes are the ones it was trained to search with.
    embedding_base_url: str = "http://127.0.0.1:11434/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "embeddinggemma"
    embedding_dim: int = 768
    embedding_query_prefix: str = "task: search result | query: "
    embedding_document_prefix: str = "title: none | text: "

    # The text search configuration written into each passage as it is indexed; a search parses
    # its query with each passage's own, so changing this only affects later uploads.
    fts_language: str = "english"
    # The threads a worker runs: this many index documents, and ANSWER_CONCURRENCY answer
    # questions (app/worker.py), in lanes of their own so that neither holds the other up.
    # Both spend most of their time waiting on a model, so a thread costs a database
    # connection more than memory or CPU; raise them together with the pool. Of the indexers,
    # PARSE_CONCURRENCY run a parse at once, each in a child that may hold PARSE_MEMORY_MB
    # (app/parsing/limits.py), and the others wait for one to end: the memory a worker needs
    # is its parses', so the indexers that wait on a model can outnumber them (deploying.md,
    # "Load", for the figures these defaults are chosen by).
    worker_concurrency: int = 16
    answer_concurrency: int = Field(default=8, ge=0)
    parse_concurrency: int = Field(default=2, ge=1)
    # The connections this process keeps to the database: DB_POOL_SIZE kept open, and up to
    # DB_MAX_OVERFLOW more opened while all of those are in use and closed as they come back.
    # A session holds one from its first statement to its next commit or rollback, so an API
    # replica needs about one per request in flight, and a worker one per document and per
    # answer it runs at once (an answer hands its back while the model thinks, see
    # app/agent/loop.py) plus one for each helper thread; the sum over every replica stays
    # under the database's max_connections (100 for Postgres). When every connection is in
    # use, a request waits DB_POOL_TIMEOUT seconds for one and is then refused with 503 and a
    # sentence that says to try again (app/main.py), rather than hanging.
    db_pool_size: int = Field(default=10, ge=1)
    # 31 in all at the defaults, the worker's threads with both of SES's queues' readers.
    db_max_overflow: int = Field(default=21, ge=0)
    db_pool_timeout: int = Field(default=5, ge=1)
    # The questions one organisation may have waiting for or receiving an answer at once. The
    # worker answers ANSWER_CONCURRENCY of them at a time, shared between the organisations
    # (app/jobs.py), so the cap bounds how long a question waits without letting one
    # organisation keep the others waiting; the question past it is refused with 429 and asked
    # again in a moment (app/api/chats.py).
    max_answers: int = Field(default=20, ge=1)
    # The longest question, in characters. A question goes to the model whole with every turn
    # of its answer, so one that is pasted rather than asked is refused with 413 and asked to
    # be shortened (app/api/chats.py); the documents are what to upload.
    max_question_chars: int = Field(default=20_000, ge=1)
    # How fast one caller may go, in requests a minute (app/ratelimit.py): upload requests,
    # questions and searches (the search and facts routes) per user; and the sign-in family
    # (sign-in, sign-up, the reset and verification mails, the authenticator code), which has
    # no user yet, per email address named and, with more room since an office shares one,
    # per client address. The request past a rate is refused with 429 and the seconds until
    # the minute ends. Zero removes a limit.
    rate_uploads: int = Field(default=20, ge=0)
    rate_questions: int = Field(default=20, ge=0)
    rate_searches: int = Field(default=120, ge=0)
    rate_sign_ins: int = Field(default=10, ge=0)
    rate_sign_ins_per_address: int = Field(default=60, ge=0)
    # The wrong passwords and authenticator codes an account may be tried with in an hour from
    # the browsers that have not signed in to it, together, and from each browser that has, by
    # itself; past it those sign-ins are paused until the hour ends, the right password
    # included, and the owner is told (app/ratelimit.py). A signed-in request that confirms a
    # change with the password is held to it per user. Zero removes it.
    rate_failed_sign_ins: int = Field(default=20, ge=0)
    # The page's reports of the errors it rendered into (app/tracking.py), per client address:
    # a page that fails again on every load reports each time, and nothing else holds a script
    # back from filling the log and the tracker with reports.
    rate_page_errors: int = Field(default=10, ge=0)
    # Messages an hour per member to support (app/api/help.py): each is a mail to
    # SUPPORT_EMAIL, which nothing else holds a script back from filling.
    rate_feedback: int = Field(default=10, ge=0)
    # Files a minute per member of a project's facts table (app/fact_export.py), each reading
    # every row the table's conditions keep, as many as fifty of its pages, and of its drafts
    # (app/draft_export.py), answers and chats (app/chat_export.py), each laid out whole.
    rate_exports: int = Field(default=10, ge=0)
    # Share links opened a minute per client address (app/share_links.py): opening one needs no
    # account, so nothing else holds a script back from trying tokens. And the wrong passwords
    # an hour a link may be tried with, from every address together and from each by itself,
    # past which it opens with no password, the right one included, until the hour ends.
    rate_shared_links: int = Field(default=30, ge=0)
    rate_shared_passwords: int = Field(default=10, ge=0)
    # Requests a minute per API key (app/apikeys.py), every request it makes, besides the
    # member's own rates above, which a key's uploads, questions and searches count against
    # too: a program that loops goes no faster than this, whatever it asks.
    rate_api_requests: int = Field(default=300, ge=0)
    # Whether this process runs the background worker (app/worker.py) as well. Unset, it does
    # in development, so that one `uvicorn` indexes what it serves, and does not in production,
    # where the deployment runs `python -m app.worker` as its own process and scales it apart
    # from the API. True runs both in one process on a small deployment; false in development
    # is for trying the worker as its own process.
    run_worker: bool | None = None

    # Development on one machine: a request that carries no session acts as the local owner
    # account instead of being refused. Off by default, so a deployment never opens up by
    # accident; the test suite turns it on.
    local_account: bool = False
    # Whether the organisation an account signs up with starts with the sample project
    # (app/sample): Emudoc's own documents of one supplier, indexed already, with three
    # questions to ask about them, so that a new member's first answer waits for no upload or
    # model. Its questions are counted as any are.
    sample_project: bool = True
    # Where the app is reached, for the links in emails and the OAuth redirect URI. Empty takes
    # the origin of the request that needs it, which is right in development.
    app_url: str = ""
    # Whether anyone who reaches the install may make an account, by email or through Google or
    # Microsoft, which joins the workspace as a member (app/signups.py). Off, an account comes
    # from the setup or an invitation alone, and the sign-up page says to ask the team's admin.
    signup_open: bool = False
    session_days: int = 30
    # Where a new password is checked against the passwords exposed in data breaches: a
    # service with Have I Been Pwned's range API (app/passwords.py), which is sent the first five
    # characters of the password's SHA-1 alone. Its own, by default; one run on the deployment's
    # own network, from the downloaded list, sends nothing out. Empty checks nothing, for a
    # server without the internet. A service that does not answer lets the password through,
    # and the log says so.
    pwned_passwords_url: str = "https://api.pwnedpasswords.com"
    # The keys the database's secrets are encrypted with (app/keyring.py): the authenticators'
    # secrets of two-factor sign-in, so that a copy of the database or of its backups cannot
    # make a member's codes. Each key is 32 random bytes in base64, as `python -m app.keyring`
    # prints one, and the keys are separated by commas: the first encrypts, and every one
    # decrypts what it encrypted, so a key is rotated by putting a new one first and running
    # `python -m app.maintenance encrypt-secrets`. Kept apart from the database's backups, or
    # encrypting gains nothing; and a key that stopped being the first is kept for as long as a
    # backup may hold what it encrypted, since a lost key loses those authenticators
    # (deploying.md, "The encryption keys"). Empty is development's own key, which is in the
    # code and which production refuses.
    encryption_keys: str = ""

    # The mail the app sends (deploying.md, "Mail"): through Resend's API with RESEND_API_KEY
    # (re_…), else through the SMTP relay SMTP_URL names, smtp://user:pass@host:587 (STARTTLS)
    # or smtps://user:pass@host:465 (TLS). With neither nothing is mailed: development writes
    # the messages to the log, which is enough to click the links, and production their
    # subjects alone, since a link signs in (app/mail.py).
    smtp_url: str = ""
    resend_api_key: str = ""
    mail_from: str = "Emudoc <no-reply@localhost>"
    # Where the provider says which mail bounced for good or was marked as spam, so that the
    # app sends those addresses no notices: Resend's webhook, {APP_URL}/api/mail/events, signed
    # with RESEND_WEBHOOK_SECRET (whsec_…); or the SQS queue that SES's events reach through
    # EventBridge, which the worker reads (the AWS module makes it and writes its URL here).
    resend_webhook_secret: str = ""
    mail_events_queue: str = ""
    # The domain of the projects' own addresses, <token>@INBOUND_DOMAIN, whose mail becomes
    # their documents (app/inbound.py; deploying.md, "Mail in"): a subdomain of its own, such as
    # in.example.com, whose MX names the provider that receives it. Empty gives no project an
    # address. Resend hands its mail over through the webhook above; SES through the SQS queue
    # its notices reach, INBOUND_MAIL_QUEUE, which the worker waits on (the AWS module makes it).
    inbound_domain: str = ""
    inbound_mail_queue: str = ""
    # Where the jobs the worker gave up on are reported (app/deadletters.py), through the
    # mailer above, as one digest every quarter of an hour. Empty sends nothing: the failures
    # wait, listed by the failed-jobs command, until an address is set.
    alert_email: str = ""

    # Sign-in with Google and Microsoft: an OAuth client per provider, registered with the
    # redirect URI {APP_URL}/api/auth/oauth/{provider}/callback. A provider without an id is
    # not offered.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    # "common" accepts work, school and personal accounts; a tenant id restricts sign-in to
    # that organisation's directory.
    microsoft_oauth_tenant: str = "common"

    # Connected folders (app/connectors/; deploying.md, "Connected folders"): an OAuth client per
    # service, of its own rather than sign-in's, since its consent asks to read the member's
    # files, registered with the redirect URI {APP_URL}/api/connectors/callback. A service
    # without a client id is not offered.
    google_drive_client_id: str = ""
    google_drive_client_secret: str = ""
    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    onedrive_client_id: str = ""
    onedrive_client_secret: str = ""
    # "common" connects work, school and personal accounts; a tenant id one directory's alone.
    onedrive_tenant: str = "common"
    # Where the services are reached in place of their own addresses, each under its name
    # ({CONNECTOR_BASE_URL}/google/…): the browser test's stand-in. Production refuses it.
    connector_base_url: str = ""

    @field_validator("database_url")
    @classmethod
    def _names_a_database(cls, value: str) -> str:
        # A blank `DATABASE_URL=` line reads as not set, not as a database with no name.
        if not value.strip():
            raise ValueError("DATABASE_URL is empty")
        return value

    @field_validator(
        "database_pooler",
        "database_listen_url",
        "sentry_dsn",
        "sentry_environment",
        "agent_model",
        "index_model",
        "agent_fallback_model",
        "index_fallback_model",
        "google_drive_client_id",
        "dropbox_app_key",
        "onedrive_client_id",
        "onedrive_tenant",
        "connector_base_url",
        mode="before",
    )
    @classmethod
    def _trimmed(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("storage_url")
    @classmethod
    def _names_a_directory_or_a_bucket(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme == "file" and parts.path and not parts.netloc:
            return value
        if parts.scheme == "s3" and parts.netloc and not parts.query and not parts.fragment:
            return value
        raise ValueError(f"{STORAGE_URL_FORMS}; {value!r} is neither")

    @field_validator("inbound_domain")
    @classmethod
    def _names_a_domain_or_none(cls, value: str) -> str:
        domain = value.strip().lower().removeprefix("@")
        if domain and not INBOUND_DOMAIN.fullmatch(domain):
            raise ValueError(f"{INBOUND_DOMAIN_FORM}; {value!r} is not one")
        return domain

    @field_validator("encryption_keys")
    @classmethod
    def _lists_keys(cls, value: str) -> str:
        parse_keys(value)
        return value.strip()

    @field_validator("metrics_address")
    @classmethod
    def _names_an_address_or_none(cls, value: str) -> str:
        parse_metrics_address(value)
        return value.strip()

    @field_validator("scanner_url")
    @classmethod
    def _names_a_scanner_or_none(cls, value: str) -> str:
        parse_scanner_url(value)
        return value.strip()

    @field_validator("converter_url")
    @classmethod
    def _names_a_converter_or_none(cls, value: str) -> str:
        parse_converter_url(value)
        return value.strip()

    @field_validator("model_prices")
    @classmethod
    def _lists_prices(cls, value: str) -> str:
        parse_model_prices(value)
        return value

    @field_validator(
        "resend_api_key",
        "resend_webhook_secret",
        "google_drive_client_secret",
        "dropbox_app_secret",
        "onedrive_client_secret",
        mode="before",
    )
    @classmethod
    def _trimmed_secret(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @property
    def receives_through(self) -> str:
        """Which provider hands over the mail sent to projects' addresses (app/receiving.py):
        "ses", through INBOUND_MAIL_QUEUE, where INBOUND_DOMAIN is set and the queue is;
        "resend", through its webhook and API, where Resend's key and webhook secret are set
        instead; "" where nothing does."""
        if not self.inbound_domain:
            return ""
        if self.inbound_mail_queue:
            return "ses"
        if self.resend_api_key and self.resend_webhook_secret:
            return "resend"
        return ""

    @property
    def prices(self) -> dict[str, ModelPrice]:
        """What each model costs per million tokens, by name (MODEL_PRICES)."""
        return parse_model_prices(self.model_prices)

    @property
    def daily_spend_cap_micros(self) -> int:
        """DAILY_SPEND_CAP in millionths of PRICE_CURRENCY, as the usage rows count; zero is
        no limit."""
        return int(self.daily_spend_cap * 1_000_000)

    @field_validator("disabled_providers")
    @classmethod
    def _names_providers(cls, value: str) -> str:
        for name in _listed(value):
            if not re.fullmatch(r"[a-z0-9_-]+", name):
                raise ValueError(
                    "DISABLED_PROVIDERS lists provider names as they prefix a model name, "
                    f"such as google or anthropic, separated by commas; {name!r} is not one."
                )
        return value

    @property
    def disabled(self) -> frozenset[str]:
        """The providers DISABLED_PROVIDERS switches off, by name."""
        return frozenset(_listed(self.disabled_providers))

    @field_validator("status_page_url", "source_url")
    @classmethod
    def _a_page(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"https?://[^\s/?#@]+(?:[/?#]\S*)?", value):
            raise ValueError(PAGES[info.field_name])
        return value

    @field_validator("fts_language")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        # A regconfig name; whether the database has one by that name is checked at start.
        if not re.fullmatch(r"[a-z_]+", value):
            raise ValueError("fts_language must be a Postgres text search configuration name")
        return value

    @field_validator("support_email")
    @classmethod
    def _an_address(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if value and ("@" not in value or any(char.isspace() for char in value)):
            raise ValueError(
                f"{info.field_name.upper()} is an address people write to, such as "
                "support@example.com"
            )
        return value

    @field_validator(
        "run_worker",
        "backup_days",
        "log_format",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        # A blank `RUN_WORKER=` line leaves the choice to the environment, like no line at all.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _run_the_worker_unless_deployed(self) -> "Settings":
        if self.run_worker is None:
            self.run_worker = self.environment == "development"
        return self

    @model_validator(mode="after")
    def _log_json_when_deployed(self) -> "Settings":
        if self.log_format is None:
            self.log_format = "text" if self.environment == "development" else "json"
        return self

    @model_validator(mode="after")
    def _the_pool_holds_the_workers_threads(self) -> "Settings":
        # An indexing or answering thread that found every connection in use would fail its
        # job for a reason the operator set, so a pool too small for the threads this process
        # runs is refused before it runs any.
        needed = (
            self.worker_concurrency
            + self.answer_concurrency
            + MAIL_THREADS
            + WORKER_HELPER_THREADS
            + (1 if self.mail_events_queue else 0)
            + (1 if self.inbound_mail_queue else 0)
        )
        allowed = self.db_pool_size + self.db_max_overflow
        if self.run_worker and allowed < needed:
            raise ValueError(
                f"WORKER_CONCURRENCY is {self.worker_concurrency} and ANSWER_CONCURRENCY is "
                f"{self.answer_concurrency}, so the worker's threads need {needed} database "
                f"connections, but DB_POOL_SIZE + DB_MAX_OVERFLOW allows {allowed}; raise them "
                "or lower the concurrency."
            )
        return self

    @model_validator(mode="after")
    def _a_fallback_is_another_model(self) -> "Settings":
        for role, primary, fallback in (
            ("AGENT", self.agent_model, self.agent_fallback_model),
            ("INDEX", self.index_model, self.index_fallback_model),
        ):
            if fallback and fallback == primary:
                raise ValueError(
                    f"{role}_FALLBACK_MODEL names {role}_MODEL's own model, {fallback}, which "
                    "would only be asked twice; name another model, best from another "
                    "provider, or leave it empty."
                )
        return self

    @property
    def models(self) -> list[ModelSetting]:
        """Every model these settings name, the primaries first, with the variable that names
        each. Whatever reads a model name for anything but a request (the transport, the
        start's checks) reads them all, since a
        fallback is sent what its primary is. A fallback left empty is none; a primary is
        always there, and an empty one says so when it is used."""
        fallbacks = [
            ModelSetting("INDEX_FALLBACK_MODEL", self.index_fallback_model, INDEX),
            ModelSetting("AGENT_FALLBACK_MODEL", self.agent_fallback_model, AGENT),
        ]
        return [
            ModelSetting("INDEX_MODEL", self.index_model, INDEX),
            ModelSetting("AGENT_MODEL", self.agent_model, AGENT),
            *(fallback for fallback in fallbacks if fallback.name),
        ]

    def models_for(self, role: str) -> list[ModelSetting]:
        """The models that do `role`'s work, in the order they are asked."""
        return [model for model in self.models if model.role == role]

    @property
    def listen_url(self) -> str | None:
        """Where the worker listens for queued jobs, or None when it cannot and polls instead:
        a LISTEN made through a pooler in transaction mode ends up on whichever server
        connection the pooler hands out next, and nothing reaches the listener."""
        if self.database_listen_url:
            return self.database_listen_url
        return None if self.database_pooler == "transaction" else self.database_url

    def production_problems(self, environ: Mapping[str, str] = os.environ) -> list[str]:
        """What stops these settings from serving a deployment, each as the step that fixes it.

        Development's conveniences are production's holes: the well-known database password
        and encryption key, requests without a session acting as the owner, files and links
        whose place depends on where the process happens to run, and connections that would
        carry the documents unencrypted across a network the deployment does not own
        (app/network.py), or that could not check the server's certificate at all. `environ` is
        read for the last: OpenSSL takes where to look for the authorities from the process
        environment, not from these settings. What a team may run without, a mail relay, a
        scanner, and the word that a Gemini key is on the paid tier, is its own choice: the
        start warns about each (app/startup.py) and refuses none. Empty in development, and for
        settings that are fit.
        """
        if self.environment != "production":
            return []
        problems = []
        urls = [("DATABASE_URL", self.database_url)]
        if self.database_listen_url:
            urls.append(("DATABASE_LISTEN_URL", self.database_listen_url))
        for variable, url in urls:
            if _password_of(url) == DEVELOPMENT_PASSWORD:
                problems.append(
                    f"{variable} carries the development database's password; give the "
                    "deployment's database a password of its own."
                )
            if problem := database_problem(url, variable):
                problems.append(problem)
            if problem := trust_store_problem(variable, url, environ, BUNDLED_LIBPQ):
                problems.append(problem)
        if self.local_account:
            problems.append(
                "LOCAL_ACCOUNT is true, which lets any request without a session act as the "
                "owner; remove it."
            )
        if "storage_url" not in self.model_fields_set:
            problems.append(
                "STORAGE_URL is not set; name where the deployment keeps uploaded files and "
                "page images, a directory as file:///var/lib/emudoc or a bucket as "
                "s3://bucket/emudoc."
            )
        if not self.app_url:
            problems.append(
                "APP_URL is not set; set it to the address users open the app at, such as "
                "https://emudoc.example.com, so that the links in emails and the OAuth "
                "redirect do not depend on what a proxy says."
            )
        if not self.encryption_keys:
            problems.append(
                "ENCRYPTION_KEYS is not set, so the authenticators' secrets of two-factor "
                "sign-in would be encrypted with development's key, which is in the code; "
                f"make one with {NEW_KEY} and keep it apart from the database's backups."
            )
        elif DEVELOPMENT_KEY in parse_keys(self.encryption_keys):
            problems.append(
                "ENCRYPTION_KEYS holds development's key, which is in the code, so anyone "
                "with a copy of the database could read the authenticators' secrets it "
                f"encrypted; make a key with {NEW_KEY}, put it first, run `python -m "
                "app.maintenance encrypt-secrets`, then remove development's."
            )
        if problem := scanner_problem(parse_scanner_url(self.scanner_url)):
            problems.append(problem)
        # A blank `EMBEDDING_BASE_URL=` line names no endpoint either.
        if "embedding_base_url" not in self.model_fields_set or not self.embedding_base_url.strip():
            problems.append(
                "EMBEDDING_BASE_URL is not set, and its default, Ollama at 127.0.0.1, is this "
                "process's own machine (in the image, its own container), where nothing "
                "answers, so search would match keywords only; name the embeddings endpoint, "
                "such as http://embeddings:8080/v1 for the compose file's server."
            )
        if problem := plain_http_problem(
            "EMBEDDING_BASE_URL",
            self.embedding_base_url,
            "every passage as it is indexed and every search",
        ):
            problems.append(problem)
        if problem := plain_http_problem(
            "CONVERTER_URL",
            self.converter_url,
            "every Word, PowerPoint, Excel, RTF and HTML upload and the PDF made of it",
        ):
            problems.append(problem)
        if problem := plain_http_problem(
            "PWNED_PASSWORDS_URL",
            self.pwned_passwords_url,
            "the start of the hash of every new password",
        ):
            problems.append(problem)
        if problem := plain_http_problem(
            "MAIL_EVENTS_QUEUE",
            self.mail_events_queue,
            "the worker's requests for the addresses whose mail bounced",
        ):
            problems.append(problem)
        if problem := plain_http_problem(
            "INBOUND_MAIL_QUEUE",
            self.inbound_mail_queue,
            "the worker's requests for the mail sent to projects' addresses",
        ):
            problems.append(problem)
        unset = [
            variable
            for variable, name in (("INDEX_MODEL", "index_model"), ("AGENT_MODEL", "agent_model"))
            if name not in self.model_fields_set or not getattr(self, name)
        ]
        if unset:
            problems.append(
                f"{listing(unset)} {'is' if len(unset) == 1 else 'are'} not set, so the "
                f"deployment would run on the default, {DEFAULT_MODEL}, which a release of the "
                "app may change and its provider may stop serving on a schedule of its own; "
                "name the model the deployment was tried with, as provider:model, and try it "
                "with `python -m app.maintenance models`."
            )
        for model in self.models:
            if moving_alias(model.name):
                problems.append(
                    f"{model.variable} is {model.name}, an alias its provider moves to a newer "
                    "model when it likes, so the prices and the answers could change under "
                    "the deployment without a release; name the version the alias points to "
                    "now, from the provider's list of models."
                )
        if self.daily_spend_cap and not self.model_prices:
            problems.append(
                "DAILY_SPEND_CAP is set, but MODEL_PRICES is empty, so no cost is recorded and "
                "the cap would count nothing; list what each model costs, or remove the cap."
            )
        elif self.daily_spend_cap and (unpriced := self.unpriced_models):
            problems.append(
                f"DAILY_SPEND_CAP is set, but MODEL_PRICES has no price for {listing(unpriced)}, "
                "so what it answers would count nothing against the cap; add its price."
            )
        problems += self._mail_problems()
        if self.connector_base_url:
            problems.append(
                "CONNECTOR_BASE_URL is set, so the connected folders would be read from it rather "
                "than from the services themselves: it is the browser test's stand-in; remove it."
            )
        for client, secret, service in (
            ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "Google Drive"),
            ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "Dropbox"),
            ("ONEDRIVE_CLIENT_ID", "ONEDRIVE_CLIENT_SECRET", "OneDrive"),
        ):
            if getattr(self, client.lower()) and not getattr(self, secret.lower()):
                problems.append(
                    f"{client} is set without {secret}, so no member could connect a folder of "
                    f"their {service}; set the client's secret too."
                )
        if self.inbound_domain and not self.receives_through:
            problems.append(
                "INBOUND_DOMAIN is set, but no provider hands its mail to the app: set "
                "INBOUND_MAIL_QUEUE to the queue of SES's notices (the AWS module's inbound), or "
                "RESEND_API_KEY and RESEND_WEBHOOK_SECRET, with the domain's MX at Resend and "
                "the webhook taking email.received, or set INBOUND_DOMAIN empty."
            )
        return problems

    def _mail_problems(self) -> list[str]:
        """What stops the mail settings from meaning one thing: two ways to send, or a key or a
        secret of another kind. Neither is quoted."""
        problems = []
        if self.smtp_url and self.resend_api_key:
            problems.append(
                "SMTP_URL and RESEND_API_KEY are both set, but the mail goes one way; remove "
                "the one not meant."
            )
        if self.resend_api_key and not self.resend_api_key.startswith("re_"):
            problems.append("RESEND_API_KEY is not a Resend API key, which starts re_.")
        if self.resend_webhook_secret and not self.resend_webhook_secret.startswith("whsec_"):
            problems.append(
                "RESEND_WEBHOOK_SECRET is not a Resend webhook's signing secret, which starts "
                "whsec_."
            )
        return problems

    @property
    def unpriced_models(self) -> list[str]:
        """The models MODEL_PRICES has no price for, each as "VARIABLE provider:model": what
        they answer costs nothing the daily spending cap counts (app/spending.py)."""
        return [
            f"{model.variable} {model.name}"
            for model in self.models
            if model.name not in self.prices
        ]


# Whether psycopg runs on the libpq and OpenSSL its binary package brings, rather than the
# system's (app/network.py, `trust_store_problem`).
BUNDLED_LIBPQ = pq.__impl__ == "binary"

MISSING_DATABASE = (
    "DATABASE_URL is not set, and the app has no default database. Put the database's URL, "
    "such as postgresql+psycopg://user:password@host:5432/emudoc, in .env for development "
    "(.env.example names the docker compose database) or in the environment for a deployment."
)


def unfit_for_production(problems: list[str]) -> str:
    """The message a process stops with when ENVIRONMENT is production and the settings are
    not; `problems` are what `Settings.production_problems` found."""
    lines = "\n".join(f"  - {problem}" for problem in problems)
    return f"ENVIRONMENT is production, but these settings are not fit for a deployment:\n{lines}"


def model_provider(name: str) -> str:
    """The provider of a `provider:model` name, as DISABLED_PROVIDERS and Pydantic AI name it."""
    return name.partition(":")[0]


def moving_alias(name: str) -> bool:
    """Whether the `provider:model` name is an alias the provider moves (MOVING_ALIAS)."""
    return MOVING_ALIAS.search(name.partition(":")[2]) is not None


def listing(names: list[str]) -> str:
    """Names as a sentence lists them: "A", "A and B", "A, B and C"."""
    return ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]


def _listed(value: str) -> list[str]:
    """The names a comma-separated setting lists, trimmed and lowercased, blanks left out."""
    return [name.strip().lower() for name in value.split(",") if name.strip()]


def _password_of(url: str) -> str | None:
    try:
        return make_url(url).password
    except ArgumentError:
        # Not a URL at all; the engine says so when it is built.
        return None


def load_settings(**values: Any) -> Settings:
    """Read the settings, and stop the process with what to do when it must not run with them.

    The settings are read as the app is imported, by uvicorn, alembic and the maintenance
    command alike, so the alternative is pydantic's validation traceback from each of them.
    Three mistakes get one message instead: no database named, ENCRYPTION_KEYS holding
    something that is not a key, which the traceback would print, and production asked for
    with settings that only a development machine should run with. Other mistakes are rare
    enough to keep that traceback, which names the setting.
    """
    try:
        settings = Settings(**values)
    except ValidationError as error:
        if any(problem["loc"] == ("database_url",) for problem in error.errors()):
            raise SystemExit(MISSING_DATABASE) from None
        # Pydantic's message would quote the value, and that is the keys.
        for problem in error.errors(include_input=False):
            if problem["loc"] == ("encryption_keys",):
                raise SystemExit(str(problem["ctx"]["error"])) from None
        raise
    if problems := settings.production_problems():
        raise SystemExit(unfit_for_production(problems))
    return settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()


def export_env_file(settings: Settings) -> None:
    """In development, copy `.env` into the process environment, where the model providers look
    for their keys.

    Variables that are already set win, and empty entries are left out so that a blank
    `GOOGLE_API_KEY=` line reads as no key at all. In production nothing is copied: the keys
    reach the environment through the deployment, so a `.env` file that is lying around cannot
    put a key into a process that was not given one.
    """
    if settings.environment != "development":
        return
    for name, value in dotenv_values(ENV_FILE).items():
        if value:
            os.environ.setdefault(name, value)
