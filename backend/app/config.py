import os
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.network import database_problem, plain_http_problem, scanner_problem

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

Effort = Literal["", "minimal", "low", "medium", "high", "xhigh"]

# The password of the database docker-compose.yml runs, which only this machine can reach.
# A deployment that carries it has copied the development setup, so production refuses it.
DEVELOPMENT_PASSWORD = "emudoc"
# The threads a worker runs besides its indexing and answering threads that take a connection
# from the pool now and then: the heartbeat, the housekeeper and the alerter (app/worker.py).
# The listener holds a connection of its own outside the pool.
WORKER_HELPER_THREADS = 3
STORAGE_URL_FORMS = (
    "STORAGE_URL names a directory as file:///var/lib/emudoc or an S3-compatible bucket as "
    "s3://bucket/prefix"
)
MODEL_PRICES_FORM = (
    "MODEL_PRICES lists what each model costs per million tokens in and out, as "
    "model=in/out separated by commas, such as google:gemini-2.5-flash-lite=0.10/0.40"
)
SCANNER_URL_FORMS = (
    "SCANNER_URL names the ClamAV daemon that scans uploads as clamd://host:3310 or, for one on "
    "this machine, clamd:///run/clamav/clamd.ctl; `off` runs without scanning"
)
SCANNER_OFF = "off"
CLAMD_PORT = 3310
# The regions a deployment can keep its documents in (DATA_REGION), by the code the setting and
# `organizations.region` hold, with the name the app says them by (app/residency.py).
REGIONS = {"eu": "the European Union", "us": "the United States"}


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
    such as a plan, a quota or a bring-your-own model key, belongs in a table read through the
    account of the request, never here.
    """

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # How the process is run. In development, `.env` is the place for everything, the model
    # keys included (see `export_env_file`). In production the process is configured by its
    # environment, set by the deployment or a secret manager, and secrets never go through a
    # file in the repository; a staging deployment is production too.
    environment: Literal["development", "production"] = "development"

    # The database, as a SQLAlchemy URL. There is no default: a process that was not told its
    # database stops (see `load_settings`) rather than reach for a loopback server with a
    # well-known password. `.env.example` names the docker-compose.yml container.
    database_url: str
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
    # process and not the worker: the seconds it may run, the memory it may hold, and the pages
    # of a PDF and the rows of one sheet it takes on. Zero removes a limit. These are the
    # ceiling for every organisation; a cap that differs by plan is an entitlement, read per
    # account.
    parse_timeout: int = Field(default=300, ge=0)
    parse_memory_mb: int = Field(default=2048, ge=0)
    parse_max_pages: int = Field(default=500, ge=0)
    parse_max_rows: int = Field(default=100_000, ge=0)
    # The pages of one document the model may read as scans: each costs a model call, and
    # PARSE_MAX_PAGES alone would let a scanned book cost hundreds. A document over it fails
    # with a message that says to split it, before a page is read (app/indexing/pipeline.py).
    # Zero removes the limit.
    ocr_max_pages: int = Field(default=100, ge=0)

    # The model behind structure inference, summaries, facts, OCR and the agent, as
    # provider:model. Each provider reads its own key from the environment (see .env.example).
    agent_model: str = "google:gemini-2.5-flash-lite"
    index_model: str = "google:gemini-2.5-flash-lite"
    # How hard the model thinks. Empty leaves the model's own default; models that cannot
    # think ignore it.
    agent_effort: Effort = "medium"
    index_effort: Effort = "low"
    # The rounds of tool calls one answer may make before it is stopped with what it has
    # (app/agent/loop.py): each is a model request, so this bounds what a question can cost.
    agent_max_turns: int = Field(default=12, ge=1)
    # What the models cost, for the cost on every usage row (app/usage.py): model=in/out per
    # million tokens, separated by commas, with the models named as AGENT_MODEL and
    # INDEX_MODEL name them, in PRICE_CURRENCY (an ISO 4217 code). Empty, no cost is
    # recorded. Embeddings are counted in texts, not tokens, and carry no cost. The prices
    # are what the platform pays its providers, the same for every organisation; what an
    # organisation is charged is its plan's business.
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
    # The region the deployment keeps its documents in, and everything read from them, as a
    # code of REGIONS: eu or us (app/residency.py). Set, the app holds to the region what it
    # can choose itself (Claude's US-only inference, OpenRouter's regional address), checks
    # at start that the models, the bucket and the embeddings endpoint are in it, and in
    # production refuses to start when one is not or the settings cannot tell; each
    # organisation records it as it is created. Where the servers and the database run is
    # the operator's word: setting it says they are in the region too. Empty promises nothing.
    data_region: str = ""

    # Embeddings: any OpenAI-compatible /embeddings endpoint. The default is a local Ollama.
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
    # An answer's thread is idle while the model thinks, so the answers cost memory and a
    # connection each more than anything else; raise them together with the pool.
    worker_concurrency: int = 2
    answer_concurrency: int = Field(default=8, ge=0)
    # The connections this process keeps to the database: DB_POOL_SIZE kept open, and up to
    # DB_MAX_OVERFLOW more opened while all of those are in use and closed as they come back.
    # A session holds one from its first statement to its next commit or rollback, so an API
    # replica needs about one per request in flight, and a worker one per document and per
    # answer it runs at once (an answer hands its back while the model thinks, see
    # app/agent/loop.py) plus one for each helper thread; the sum over every replica stays
    # under the database's max_connections (100 for Postgres). When every connection is in
    # use, a request waits DB_POOL_TIMEOUT seconds for one and is then refused with 503 and a
    # sentence that says to try again (app/main.py), rather than hanging.
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
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
    # Where the app is reached, for the links in emails and the OAuth redirect URI. Empty takes
    # the origin of the request that needs it, which is right in development.
    app_url: str = ""
    session_days: int = 30

    # Verification and password-reset mail. Empty SMTP_URL writes the messages to the log,
    # which is enough to click the links in development. Otherwise smtp://user:pass@host:587
    # (STARTTLS) or smtps://user:pass@host:465 (TLS).
    smtp_url: str = ""
    mail_from: str = "Emudoc <no-reply@localhost>"
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

    @field_validator("storage_url")
    @classmethod
    def _names_a_directory_or_a_bucket(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme == "file" and parts.path and not parts.netloc:
            return value
        if parts.scheme == "s3" and parts.netloc and not parts.query and not parts.fragment:
            return value
        raise ValueError(f"{STORAGE_URL_FORMS}; {value!r} is neither")

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

    @field_validator("data_region", mode="before")
    @classmethod
    def _names_a_region(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            if value and value not in REGIONS:
                codes = " or ".join(REGIONS)
                raise ValueError(f"DATA_REGION is {codes}, or empty for no region; not {value!r}")
        return value

    @field_validator("fts_language")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        # A regconfig name; whether the database has one by that name is checked at start.
        if not re.fullmatch(r"[a-z_]+", value):
            raise ValueError("fts_language must be a Postgres text search configuration name")
        return value

    @field_validator("run_worker", mode="before")
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
    def _the_pool_holds_the_workers_threads(self) -> "Settings":
        # An indexing or answering thread that found every connection in use would fail its
        # job for a reason the operator set, so a pool too small for the threads this process
        # runs is refused before it runs any.
        needed = self.worker_concurrency + self.answer_concurrency + WORKER_HELPER_THREADS
        allowed = self.db_pool_size + self.db_max_overflow
        if self.run_worker and allowed < needed:
            raise ValueError(
                f"WORKER_CONCURRENCY is {self.worker_concurrency} and ANSWER_CONCURRENCY is "
                f"{self.answer_concurrency}, so the worker's threads need {needed} database "
                f"connections, but DB_POOL_SIZE + DB_MAX_OVERFLOW allows {allowed}; raise them "
                "or lower the concurrency."
            )
        return self

    def production_problems(self) -> list[str]:
        """What stops these settings from serving a deployment, each as the step that fixes it.

        Development's conveniences are production's holes: the well-known database password,
        requests without a session acting as the owner, sign-in links written to the log,
        uploads nobody scans, a model key that may be on a free tier which trains on what it
        is sent, files and links whose place depends on where the process happens to run, and
        connections that would carry the documents unencrypted across a network the deployment
        does not own (app/network.py). Empty in development, and for settings that are fit.
        """
        if self.environment != "production":
            return []
        problems = []
        if _password_of(self.database_url) == DEVELOPMENT_PASSWORD:
            problems.append(
                "DATABASE_URL carries the development database's password; give the "
                "deployment's database a password of its own."
            )
        if problem := database_problem(self.database_url):
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
        if not self.smtp_url:
            problems.append(
                "SMTP_URL is not set, so verification and password-reset links would be "
                "written to the log; name an SMTP relay."
            )
        if not self.scanner_url:
            problems.append(
                "SCANNER_URL is not set, so uploads would reach the other members of a project "
                "unscanned; name the ClamAV daemon, as clamd://clamav:3310, or set it to off "
                "to run without scanning on purpose."
            )
        if problem := scanner_problem(parse_scanner_url(self.scanner_url)):
            problems.append(problem)
        if problem := plain_http_problem(
            "EMBEDDING_BASE_URL",
            self.embedding_base_url,
            "every passage as it is indexed and every search",
        ):
            problems.append(problem)
        models = (("AGENT_MODEL", self.agent_model), ("INDEX_MODEL", self.index_model))
        gemini = [variable for variable, name in models if model_provider(name) == "google"]
        if gemini and not self.google_paid_tier:
            uses = f"{gemini[0]} uses" if len(gemini) == 1 else "AGENT_MODEL and INDEX_MODEL use"
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
        if self.daily_spend_cap and not self.model_prices:
            problems.append(
                "DAILY_SPEND_CAP is set, but MODEL_PRICES is empty, so no cost is recorded and "
                "the cap would count nothing; list what each model costs, or remove the cap."
            )
        return problems


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
    Two mistakes get one message instead: no database named, and production asked for with
    settings that only a development machine should run with. Other mistakes are rare enough
    to keep that traceback, which names the setting.
    """
    try:
        settings = Settings(**values)
    except ValidationError as error:
        if any(problem["loc"] == ("database_url",) for problem in error.errors()):
            raise SystemExit(MISSING_DATABASE) from None
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
