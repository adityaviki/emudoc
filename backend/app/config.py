import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

Effort = Literal["", "minimal", "low", "medium", "high", "xhigh"]


class Settings(BaseSettings):
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
    data_dir: Path = BACKEND_DIR / "data"
    max_upload_mb: int = 100

    # The model behind structure inference, summaries, facts, OCR and the agent, as
    # provider:model. Each provider reads its own key from the environment (see .env.example).
    agent_model: str = "google:gemini-2.5-flash-lite"
    index_model: str = "google:gemini-2.5-flash-lite"
    # How hard the model thinks. Empty leaves the model's own default; models that cannot
    # think ignore it.
    agent_effort: Effort = "medium"
    index_effort: Effort = "low"

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
    worker_concurrency: int = 2

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

    @field_validator("fts_language")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        # A regconfig name; whether the database has one by that name is checked at start.
        if not re.fullmatch(r"[a-z_]+", value):
            raise ValueError("fts_language must be a Postgres text search configuration name")
        return value

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def pages_dir(self) -> Path:
        return self.data_dir / "pages"


MISSING_DATABASE = (
    "DATABASE_URL is not set, and the app has no default database. Put the database's URL, "
    "such as postgresql+psycopg://user:password@host:5432/emudoc, in .env for development "
    "(.env.example names the docker compose database) or in the environment for a deployment."
)


def load_settings(**values: Any) -> Settings:
    """Read the settings, and stop the process with what to do when no database is named.

    The settings are read as the app is imported, by uvicorn, alembic and the maintenance
    command alike, so the alternative is pydantic's validation traceback from each of them.
    Other mistakes are rare enough to keep that traceback, which names the setting.
    """
    try:
        return Settings(**values)
    except ValidationError as error:
        if any(problem["loc"] == ("database_url",) for problem in error.errors()):
            raise SystemExit(MISSING_DATABASE) from None
        raise


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
