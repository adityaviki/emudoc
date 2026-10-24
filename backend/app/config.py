import ipaddress
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
# A host name of labels joined by dots, the last of letters or an IDN's punycode, as
# app/invitations.py takes a domain.
INBOUND_DOMAIN = re.compile(
    r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})"
)
INBOUND_DOMAIN_FORM = (
    "INBOUND_DOMAIN is the domain of the projects' addresses, such as in.example.com, or empty "
    "for none"
)
STRIPE_PRICES_FORM = (
    "STRIPE_PRICES lists the Stripe prices sold, as plan=price separated by commas, such as "
    "pro=price_1Pro,team=price_1Team, each plan pro or team"
)
# The plans an organisation may buy for itself (app/billing.py holds them to app/entitlements.py):
# Free is what every organisation has, and Enterprise's terms are agreed, not bought.
SOLD_PLANS = ("pro", "team")
# What a pack adds to an organisation's month (STRIPE_PACKS): pages indexed, or questions asked.
PACK_UNITS = ("pages", "questions")
STRIPE_PACKS_FORM = (
    "STRIPE_PACKS lists the packs sold, as unit:amount=price separated by commas, such as "
    "pages:1000=price_1Pages,questions:500=price_1Questions, each unit pages or questions"
)
CLAMD_PORT = 3310
OPERATOR_NETWORKS_FORM = (
    "OPERATOR_NETWORKS lists the addresses the operator's area answers, as networks or single "
    "addresses separated by commas, such as 203.0.113.0/24,2001:db8::7"
)
# Where the operator's area answers in development while OPERATOR_NETWORKS is unset: this
# machine alone, which the dev server's proxy and a browser on it both come from.
LOOPBACK_NETWORKS = "127.0.0.1/32,::1/128"
# The work a model does: indexing (INDEX_MODEL: sections, summaries, facts, scans) or answering
# (AGENT_MODEL: the Ask tab's agent), each with its fallback.
INDEX = "index"
AGENT = "agent"
# The model a development checkout runs when .env names none. Production refuses to run on it
# (`Settings.production_problems`): a default moves with the code and the provider retires
# models on a schedule of its own, so a deployment names the model it was tried with.
DEFAULT_MODEL = "google:gemini-3.5-flash-lite"
# Names a provider moves to a newer model when it likes: Google's `gemini-flash-latest`,
# OpenAI's `chatgpt-4o-latest`, Anthropic's former `-latest` aliases, a `:latest` tag, and
# OpenRouter's router, which picks a model per request.
MOVING_ALIAS = re.compile(r"(?:^|[-_.:/])latest$|^openrouter/auto$", re.IGNORECASE)
# The regions a deployment can keep its documents in (DATA_REGION), by the code the setting and
# `organizations.region` hold, with the name the app says them by (app/residency.py).
REGIONS = {"eu": "the European Union", "us": "the United States"}


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


type Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_operator_networks(value: str) -> tuple[Network, ...]:
    """The networks OPERATOR_NETWORKS lists, an address alone being a network of one. Raises
    `ValueError` naming the form for anything else, such as a network with host bits set."""
    networks = []
    for entry in value.split(","):
        if not (entry := entry.strip()):
            continue
        try:
            networks.append(ipaddress.ip_network(entry))
        except ValueError:
            raise ValueError(f"{OPERATOR_NETWORKS_FORM}; {entry!r} is not one.") from None
    return tuple(networks)


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


@lru_cache
def parse_stripe_prices(text: str) -> dict[str, list[str]]:
    """The prices STRIPE_PRICES lists, by plan, in the order listed: the first of each is what
    Checkout sells."""
    prices: dict[str, list[str]] = {}
    seen: set[str] = set()
    for entry in filter(None, (part.strip() for part in text.split(","))):
        plan, equals, price = (part.strip() for part in entry.partition("="))
        if not equals or plan not in SOLD_PLANS or not re.fullmatch(r"[A-Za-z0-9_]+", price):
            raise ValueError(f"{STRIPE_PRICES_FORM}; {entry!r} is not of that form.")
        if price in seen:
            raise ValueError(f"{STRIPE_PRICES_FORM}; {price} is listed twice.")
        seen.add(price)
        prices.setdefault(plan, []).append(price)
    return prices


class Pack(NamedTuple):
    """A pack of STRIPE_PACKS: how many pages or questions it adds to the month it is paid in,
    and the Stripe price it is sold at, a one-time one."""

    unit: str
    quantity: int
    price: str


@lru_cache
def parse_stripe_packs(text: str) -> tuple[Pack, ...]:
    """The packs STRIPE_PACKS lists, in the order listed."""
    packs: list[Pack] = []
    for entry in filter(None, (part.strip() for part in text.split(","))):
        size, equals, price = (part.strip() for part in entry.partition("="))
        unit, colon, amount = (part.strip() for part in size.partition(":"))
        if (
            not (equals and colon)
            or unit not in PACK_UNITS
            or not (amount.isascii() and amount.isdecimal() and int(amount) > 0)
            or not re.fullmatch(r"[A-Za-z0-9_]+", price)
        ):
            raise ValueError(f"{STRIPE_PACKS_FORM}; {entry!r} is not of that form.")
        if any(pack.price == price for pack in packs):
            raise ValueError(f"{STRIPE_PACKS_FORM}; {price} is listed twice.")
        if any(pack[:2] == (unit, int(amount)) for pack in packs):
            raise ValueError(f"{STRIPE_PACKS_FORM}; {unit}:{amount} is listed twice.")
        packs.append(Pack(unit, int(amount), price))
    return tuple(packs)


class Settings(BaseSettings):
    """How this process runs, read once from the environment and `.env` and shared.

    These are read as the app is imported (`app/db.py` builds the engine from them, `app/models.py`
    sizes the vector column) and cached for the life of the process, so they can only hold what
    is the same for every organisation the process serves. Anything that varies per organisation,
    such as a plan, a quota or a bring-your-own model key, belongs in a table read through the
    account of the request, never here.
    """

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

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
    # key or an email address, and the subprocessor list names the tracker's company unless it
    # runs on the deployment's own network.
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
    # machine. Empty, nothing is scanned and every document says so; `off` is the same choice
    # made on purpose, the only form production accepts besides a daemon.
    scanner_url: str = ""
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
    # process and not the worker: the seconds it may run, the memory it may hold, the pages of
    # a PDF and the rows of one sheet it takes on, and the characters of text one document may
    # hold. The text is what every later stage pays for, the worker holding it, its passages
    # written, indexed and embedded (a passage is some 1,200 to 2,000 characters), so a file
    # over it is refused before the child replies. Zero removes a limit. These are the ceiling
    # for every organisation; a cap that differs by plan is an entitlement, read per account.
    parse_timeout: int = Field(default=300, ge=0)
    parse_memory_mb: int = Field(default=2048, ge=0)
    parse_max_pages: int = Field(default=500, ge=0)
    parse_max_rows: int = Field(default=100_000, ge=0)
    parse_max_chars: int = Field(default=20_000_000, ge=0)
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
    # spending limits are checked before the stage, so this is the most a document can spend
    # past them. Zero removes the limit.
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
    # sent what its primary is, so it is a subprocessor too, held to the same region, terms
    # and transport, and needs its price in MODEL_PRICES.
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
    # nothing. The prices are what the platform pays its providers, the same for every
    # organisation; what an organisation is charged is its plan's business.
    model_prices: str = ""
    price_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    # What every organisation together may spend on models in one UTC day, in PRICE_CURRENCY,
    # by the costs the usage rows carry (app/spending.py): past it, every question is refused
    # with 503 until midnight UTC and every indexing stage that needs a model waits for it.
    # Zero is no limit. The limit that differs by plan is an entitlement
    # (app/entitlements.py); this is the platform's own ceiling, the most the operator will
    # see on the providers' bills for one day. It counts nothing without MODEL_PRICES, so
    # production refuses the one without the other.
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
    # nothing in a key or a reply tells the two apart, so production refuses google: models
    # until this says so. Vertex AI (google-cloud: models) runs under a Cloud project's own
    # terms and needs no such word.
    google_paid_tier: bool = False
    # Who runs the deployment's servers and its database, as the subprocessor list names it
    # (app/subprocessors.py): the one subprocessor the settings cannot name themselves.
    # Production refuses it empty, since the published list would leave it out.
    hosting_provider: str = ""
    # Who runs the deployment, as its terms of service and privacy policy name them
    # (app/legal/): the company or person with their postal address, and the address people
    # write to about their account, their data and the terms. Production refuses either empty,
    # since the privacy policy must say who answers for the data and how to reach them.
    operator_name: str = ""
    operator_email: str = ""
    # Where members' messages to support go: what they write from the app's help menu, with
    # the page and the request it was about (app/api/help.py), and the address the help pages
    # and the mail about an account's address name for help. Empty is OPERATOR_EMAIL's, for a
    # deployment whose one address answers both.
    support_email: str = ""
    # The public status page that says whether the service is up, run outside the deployment
    # it reports on (deploying.md, "The status page"): the help menu, the help pages and the
    # page that cannot reach its server link to it. Empty links to none.
    status_page_url: str = ""
    # How many days the deployment keeps the backups of its database and bucket, zero for
    # none. The app makes none itself; the privacy policy names this as the outer bound of a
    # deletion, since a purged account stays in a backup until it expires (app/retention.py
    # purges it again after a restore). Production refuses it unset, as the policy could not.
    backup_days: int | None = Field(default=None, ge=0)
    # How many days the deployment's infrastructure keeps its records of connections: the
    # network's flow logs, and the access logs of a bucket that members' browsers fetch page
    # images and downloads from, which hold their IP addresses and browsers and, in a signed
    # address, a download's name. The privacy policy names them and this period when it is
    # set; unset says the deployment keeps none. The AWS module writes it beside the bucket
    # rule that holds to it (deploying.md, "The account's records").
    access_log_days: int | None = Field(default=None, ge=1)
    # How many days the deployment keeps its server logs: the app's own lines (uvicorn's access
    # log among them, with each request's client address), the reverse proxy's access log and
    # the database server's log. The privacy policy names this period beside the logs; the app
    # cannot hold its log to it, since what collects standard error keeps it, so it is the
    # operator's word, and production refuses it unset as for BACKUP_DAYS. The AWS module writes
    # it and the instance's journal deletes by it (deploying.md, "Logs").
    server_log_days: int | None = Field(default=None, ge=1)
    # The region the deployment keeps its documents in, and everything read from them, as a
    # code of REGIONS: eu or us (app/residency.py). Set, the app holds to the region what it
    # can choose itself (Claude's US-only inference, OpenRouter's regional address), checks
    # at start that the models, the bucket and the embeddings endpoint are in it, and in
    # production refuses to start when one is not or the settings cannot tell; each
    # organisation records it as it is created. Where the servers and the database run is
    # the operator's word: setting it says they are in the region too. Empty promises nothing.
    data_region: str = ""

    # Embeddings: any OpenAI-compatible /embeddings endpoint (app/embeddings.py), sent every
    # passage as it is indexed and every search. The default is an Ollama on this machine,
    # for development; production refuses to start without one named, and asks it once at
    # start (app/startup.py).
    embedding_base_url: str = "http://127.0.0.1:11434/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    embedding_query_prefix: str = "search_query: "
    embedding_document_prefix: str = "search_document: "

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
    # How long, in seconds, an automation (app/automations.py) waits after the last document it
    # watches arrived before it runs the project's cross-checks, so that an upload sent in
    # several requests makes one run rather than one a request; however many keep arriving, it
    # runs within a quarter of an hour of the first (MOST_HELD there).
    automation_wait: int = Field(default=120, ge=0)
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
    # again in a moment (app/api/chats.py). It is the same for every organisation; a cap that
    # differs by plan is an entitlement, read per account.
    max_answers: int = Field(default=20, ge=1)
    # The longest question, in characters. A question goes to the model whole with every turn
    # of its answer, so one that is pasted rather than asked is refused with 413 and asked to
    # be shortened (app/api/chats.py); the documents are what to upload.
    max_question_chars: int = Field(default=20_000, ge=1)
    # How many organisations on the free plan one user may own (app/accounts.py): each has the
    # plan's allowance, so without a ceiling one person could start organisations until the
    # free plan held them to nothing. The one past it is refused with 409 and a sentence that
    # says to move one to a paid plan or hand one over (app/api/organizations.py); joining
    # another's organisation counts nothing. Zero removes the ceiling.
    max_free_organizations: int = Field(default=2, ge=0)
    # How fast one caller may go, in requests a minute (app/ratelimit.py): upload requests,
    # questions and searches (the search and facts routes) per user; and the sign-in family
    # (sign-in, sign-up, the reset and verification mails, the authenticator code), which has
    # no user yet, per email address named and, with more room since an office shares one,
    # per client address. The request past a rate is refused with 429 and the seconds until
    # the minute ends. Zero removes a limit. They are the same for every organisation; a
    # rate that differs by plan is an entitlement.
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
    # Requests a minute per member to billing (app/api/billing.py): each starts a Checkout or a
    # customer portal session at Stripe, or reads one back.
    rate_billing: int = Field(default=10, ge=0)
    # Files a minute per member of a project's facts table (app/fact_export.py), each reading
    # every row the table's conditions keep, as many as fifty of its pages, and of its drafts
    # (app/draft_export.py), each laid out whole.
    rate_exports: int = Field(default=10, ge=0)
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
    # model. It counts against nothing the plan holds; its questions count as any do.
    sample_project: bool = True
    # Where the app is reached, for the links in emails and the OAuth redirect URI. Empty takes
    # the origin of the request that needs it, which is right in development.
    app_url: str = ""
    session_days: int = 30
    # The client addresses the operator's area (app/operators.py) answers, as networks or single
    # addresses separated by commas, read from the proxy's X-Forwarded-For as uvicorn believes
    # it (FORWARDED_ALLOW_IPS). From anywhere else it answers 404, whoever is signed in, so a
    # stolen operator's session is of no use off these networks. Unset, it answers this machine
    # alone in development and nobody in production, until the deployment names the operators'
    # own addresses or its VPN's.
    operator_networks: str | None = None
    # Where a new password is checked against the passwords exposed in data breaches: a
    # service with Have I Been Pwned's range API (app/passwords.py), which is sent the first five
    # characters of the password's SHA-1 alone. Its own, by default; one run on the deployment's
    # own network, from the downloaded list, sends nothing out. Empty checks nothing. A service
    # that does not answer lets the password through, and the log says so.
    pwned_passwords_url: str = "https://api.pwnedpasswords.com"
    # The keys the database's secrets are encrypted with (app/keyring.py): the authenticators'
    # secrets of two-factor sign-in, so that a copy of the database or of its backups cannot
    # make a member's codes. Each key is 32 random bytes in base64, as `openssl rand -base64
    # 32` prints one, and the keys are separated by commas: the first encrypts, and every one
    # decrypts what it encrypted, so a key is rotated by putting a new one first and running
    # `python -m app.maintenance encrypt-secrets`. Kept apart from the database's backups, or
    # encrypting gains nothing; and a key that stopped being the first is kept for as long as a
    # backup may hold what it encrypted, since a lost key loses those authenticators
    # (deploying.md, "The encryption keys"). Empty is development's own key, which is in the
    # code and which production refuses.
    encryption_keys: str = ""

    # Billing through Stripe (app/billing.py; deploying.md, "Billing"): the account's secret key,
    # sk_live_… (sk_test_… on staging), or a restricted rk_… one, and the signing secret, whsec_…,
    # of the webhook endpoint that sends Stripe's events to {APP_URL}/api/billing/events. Both
    # are secrets. Empty, nothing is sold: the organisation page offers no plan, and the
    # operator's `plan` command is how an organisation moves.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # The Stripe prices sold, as plan=price separated by commas, a plan pro or team: the first of
    # each plan is what Checkout sells, and every one listed is read back as its plan, so a
    # yearly price or one in another currency that the customer portal may switch to goes here
    # too. Team's is a price per seat, the subscription's quantity its seats.
    stripe_prices: str = ""
    # The packs sold, as unit:amount=price separated by commas, the unit pages or questions and
    # the price a one-time one: each adds its amount to the organisation's allowance for the
    # calendar month in UTC it is paid in, on top of its plan's, for an organisation that wants
    # more this month without a larger plan.
    stripe_packs: str = ""
    # The days of trial an organisation's first subscription starts with, charged only at its
    # end; zero starts none.
    stripe_trial_days: int = Field(default=14, ge=0)
    # Whether Checkout works out the tax due where the buyer is (Stripe Tax), which needs the
    # deployment's tax registrations in Stripe's dashboard first.
    stripe_tax: bool = False
    # The days a subscription whose payment failed keeps its plan while Stripe tries again and
    # the owners are told; past them the organisation moves to the Free plan, keeping all it
    # holds, until the payment is made. Zero moves it at the first failure.
    billing_grace_days: int = Field(default=14, ge=0)

    # The mail the app sends (deploying.md, "Mail"): through Resend's API with RESEND_API_KEY
    # (re_…), else through the SMTP relay SMTP_URL names, smtp://user:pass@host:587 (STARTTLS)
    # or smtps://user:pass@host:465 (TLS). With neither, the messages are written to the log,
    # which is enough to click the links in development.
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

    @field_validator("model_prices")
    @classmethod
    def _lists_prices(cls, value: str) -> str:
        parse_model_prices(value)
        return value

    @field_validator("stripe_prices")
    @classmethod
    def _lists_stripe_prices(cls, value: str) -> str:
        parse_stripe_prices(value)
        return value

    @field_validator("stripe_packs")
    @classmethod
    def _lists_stripe_packs(cls, value: str) -> str:
        parse_stripe_packs(value)
        return value

    @model_validator(mode="after")
    def _a_price_sells_one_thing(self) -> "Settings":
        # A plan's price is a recurring one and a pack's a one-time one: one listed as both
        # would be read back as the plan whatever was bought.
        plans = {
            price for prices in parse_stripe_prices(self.stripe_prices).values() for price in prices
        }
        if shared := sorted(plans & {pack.price for pack in parse_stripe_packs(self.stripe_packs)}):
            raise ValueError(
                f"STRIPE_PRICES and STRIPE_PACKS both list {', '.join(shared)}; a plan's price "
                "recurs and a pack's is paid once, so each is another price."
            )
        return self

    @field_validator(
        "stripe_secret_key",
        "stripe_webhook_secret",
        "resend_api_key",
        "resend_webhook_secret",
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
    def support_address(self) -> str:
        """Where members write to for help: SUPPORT_EMAIL, else OPERATOR_EMAIL; empty for
        none, which only development runs with."""
        return self.support_email or self.operator_email

    @property
    def stripe_plans(self) -> dict[str, list[str]]:
        """The Stripe prices sold, by plan (STRIPE_PRICES); empty while billing is off."""
        return parse_stripe_prices(self.stripe_prices) if self.stripe_secret_key else {}

    @property
    def packs(self) -> tuple[Pack, ...]:
        """The packs sold (STRIPE_PACKS); none while billing is off."""
        return parse_stripe_packs(self.stripe_packs) if self.stripe_secret_key else ()

    @property
    def prices(self) -> dict[str, ModelPrice]:
        """What each model costs per million tokens, by name (MODEL_PRICES)."""
        return parse_model_prices(self.model_prices)

    @property
    def daily_spend_cap_micros(self) -> int:
        """DAILY_SPEND_CAP in millionths of PRICE_CURRENCY, as the usage rows count; zero is
        no limit."""
        return int(self.daily_spend_cap * 1_000_000)

    @field_validator("operator_networks")
    @classmethod
    def _lists_networks(cls, value: str | None) -> str | None:
        if value is not None:
            parse_operator_networks(value)
        return value

    @property
    def operator_nets(self) -> tuple[Network, ...]:
        """The networks the operator's area answers (OPERATOR_NETWORKS), none for nobody."""
        return parse_operator_networks(self.operator_networks or "")

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

    @field_validator("data_region", mode="before")
    @classmethod
    def _names_a_region(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            if value and value not in REGIONS:
                codes = " or ".join(REGIONS)
                raise ValueError(f"DATA_REGION is {codes}, or empty for no region; not {value!r}")
        return value

    @field_validator("status_page_url")
    @classmethod
    def _a_page(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"https?://[^\s/?#@]+(?:[/?#]\S*)?", value):
            raise ValueError(
                "STATUS_PAGE_URL is the address of the status page, such as "
                "https://status.example.com"
            )
        return value

    @field_validator("fts_language")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        # A regconfig name; whether the database has one by that name is checked at start.
        if not re.fullmatch(r"[a-z_]+", value):
            raise ValueError("fts_language must be a Postgres text search configuration name")
        return value

    @field_validator("operator_email", "support_email")
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
        "access_log_days",
        "server_log_days",
        "log_format",
        "operator_networks",
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
    def _operators_on_this_machine_in_development(self) -> "Settings":
        if self.operator_networks is None:
            self.operator_networks = LOOPBACK_NETWORKS if self.environment == "development" else ""
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
        each. Whatever reads a model name for anything but a request (the region, the
        subprocessors, the terms, the transport, the start's checks) reads them all, since a
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
        and encryption key, requests without a session acting as the owner, sign-in links
        written to the log, uploads nobody scans, a model key that may be on a free tier which
        trains on what it is sent, files and links whose place depends on where the process
        happens to run, and connections that would carry the documents unencrypted across a
        network the deployment does not own (app/network.py), or that could not check the
        server's certificate at all. `environ` is read for the last: OpenSSL takes where to look
        for the authorities from the process environment, not from these settings. Empty in
        development, and for settings that are fit.
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
        if not (self.smtp_url or self.resend_api_key):
            problems.append(
                "SMTP_URL is not set, so verification and password-reset links would be "
                "written to the log; name an SMTP relay, or set RESEND_API_KEY to send through "
                "Resend."
            )
        if not self.encryption_keys:
            problems.append(
                "ENCRYPTION_KEYS is not set, so the authenticators' secrets of two-factor "
                "sign-in would be encrypted with development's key, which is in the code; "
                f"make a key with {NEW_KEY} and keep it apart from the database's backups."
            )
        elif DEVELOPMENT_KEY in parse_keys(self.encryption_keys):
            problems.append(
                "ENCRYPTION_KEYS holds development's key, which is in the code, so anyone "
                "with a copy of the database could read the authenticators' secrets it "
                f"encrypted; make a key with {NEW_KEY}, put it first, run `python -m "
                "app.maintenance encrypt-secrets`, then remove development's."
            )
        if not self.scanner_url:
            problems.append(
                "SCANNER_URL is not set, so uploads would reach the other members of a project "
                "unscanned; name the ClamAV daemon, as clamd://clamav:3310, or set it to off "
                "to run without scanning on purpose."
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
                    "model when it likes, so the prices, the region and the answers could "
                    "change under the deployment without a release; name the version the alias "
                    "points to now, from the provider's list of models."
                )
        gemini = [model.variable for model in self.models if model_provider(model.name) == "google"]
        if gemini and not self.google_paid_tier:
            uses = f"{listing(gemini)} {'uses' if len(gemini) == 1 else 'use'}"
            problems.append(
                f"{uses} the Gemini Developer API (google:), whose free tier lets Google use "
                "the documents and questions it is sent to improve its products, and the app "
                "cannot tell a free key from a paid one; once billing is on for the key's "
                "Google Cloud project, set GOOGLE_PAID_TIER=true, or use Vertex AI as "
                "google-cloud:<model>."
            )
        if not self.hosting_provider.strip():
            problems.append(
                "HOSTING_PROVIDER is not set, so the subprocessor list the app publishes at "
                "/subprocessors would leave out who runs its servers and database; name them, "
                'such as "Hetzner Online GmbH, Germany".'
            )
        for variable, value, example in (
            ("OPERATOR_NAME", self.operator_name, '"Emudoc GmbH, Example Street 1, Berlin"'),
            ("OPERATOR_EMAIL", self.operator_email, "privacy@example.com"),
        ):
            if not value.strip():
                problems.append(
                    f"{variable} is not set, so the terms of service and the privacy policy at "
                    "/terms and /privacy would not say who runs the deployment and how to "
                    f"reach them; set it, such as {example}."
                )
        if self.backup_days is None:
            problems.append(
                "BACKUP_DAYS is not set, so the privacy policy at /privacy cannot say how long "
                "a deleted account stays in the backups; set it to the days the deployment "
                "keeps backups of its database and bucket, or 0 for none."
            )
        if self.server_log_days is None:
            problems.append(
                "SERVER_LOG_DAYS is not set, so the privacy policy at /privacy cannot say how "
                "long the server logs, which hold every request's IP address, are kept; set it "
                "to the days the deployment keeps the app's, the proxy's and the database's "
                'logs, and have them deleted after it (deploying.md, "Logs").'
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
        problems += self._billing_problems()
        problems += self._mail_problems()
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

    def _billing_problems(self) -> list[str]:
        """What stops billing from working, once any of its settings is given: Checkout needs
        the key and something to sell, and nothing bought reaches the app without the webhook's
        secret. Neither key is quoted, nor its start beyond its kind."""
        given = (self.stripe_webhook_secret, self.stripe_prices, self.stripe_packs)
        if not (self.stripe_secret_key or any(given)):
            return []
        problems = []
        if not self.stripe_secret_key:
            problems.append(
                "STRIPE_WEBHOOK_SECRET, STRIPE_PRICES or STRIPE_PACKS is set, but "
                "STRIPE_SECRET_KEY is not, so nothing can be sold; set the Stripe account's "
                "secret key, or remove the others."
            )
        elif not self.stripe_secret_key.startswith(("sk_", "rk_")):
            problems.append(
                "STRIPE_SECRET_KEY is not a secret key, which starts sk_ (or rk_ for a "
                "restricted one); the publishable key, pk_, cannot sell anything."
            )
        if not self.stripe_webhook_secret:
            problems.append(
                "STRIPE_WEBHOOK_SECRET is not set, so what is bought would never reach the app: "
                "make the webhook endpoint {APP_URL}/api/billing/events in Stripe's dashboard and "
                'set its signing secret (deploying.md, "Billing").'
            )
        elif not self.stripe_webhook_secret.startswith("whsec_"):
            problems.append(
                "STRIPE_WEBHOOK_SECRET is not a webhook endpoint's signing secret, which starts "
                "whsec_."
            )
        if not self.stripe_prices:
            problems.append(
                "STRIPE_PRICES is not set, so there is nothing to sell; list the prices of the "
                "Pro and Team plans, as pro=price_…,team=price_…."
            )
        return problems

    @property
    def unpriced_models(self) -> list[str]:
        """The models MODEL_PRICES has no price for, each as "VARIABLE provider:model": what
        they answer costs nothing the spending limits count (app/spending.py)."""
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
    for name, value in dotenv_values(ROOT_DIR / ".env").items():
        if value:
            os.environ.setdefault(name, value)
