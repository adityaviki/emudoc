import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

Effort = Literal["", "minimal", "low", "medium", "high", "xhigh"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/emudoc"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def export_env_file() -> None:
    """Copy `.env` into the process environment, where the model providers look for their keys.

    Variables that are already set win, and empty entries are left out so that a blank
    `GOOGLE_API_KEY=` line reads as no key at all.
    """
    for name, value in dotenv_values(ROOT_DIR / ".env").items():
        if value:
            os.environ.setdefault(name, value)
