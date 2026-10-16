"""The terms of service and the privacy policy: published to anyone, filled in from the
deployment's settings so that they say what the code does, accepted at sign-up or before
anything else, and asked for again when their edition changes; leaving needs no acceptance."""

import hashlib
from pathlib import Path

from sqlalchemy import select, update

from app.api.auth import ACCEPT_TERMS, TERMS_CHANGED
from app.config import Settings, get_settings
from app.legal import (
    TEMPLATES,
    TERMS_VERSION,
    privacy_policy,
    terms_of_service,
    terms_pending,
)
from app.models import AuditEvent, User
from app.storage import LocalStorage, S3Storage
from tests.conftest import FakeS3Client
from tests.test_auth import PASSWORD, callback, google_claims, me, signup, start

# Every setting the documents read, as a development machine has them, so that nothing in the
# environment the suite runs in changes what a test expects.
DEVELOPMENT = {
    "_env_file": None,
    "database_url": "postgresql+psycopg://db.internal/emudoc",
    "index_model": "google:gemini-2.5-flash-lite",
    "agent_model": "google:gemini-2.5-flash-lite",
    "index_fallback_model": "",
    "agent_fallback_model": "",
    "google_paid_tier": False,
    "scanner_url": "",
    "embedding_base_url": "http://127.0.0.1:11434/v1",
    "data_region": "",
    "operator_name": "",
    "operator_email": "",
    "backup_days": None,
    "session_days": 30,
}
DIRECTORY = LocalStorage(Path("unused"))
# The SHA-256 of terms.md and privacy.md as the edition TERMS_VERSION names was settled.
SETTLED = "8b15d5fa8d6328fea020b1ceebce3b4468843f8fdd191321748adc15d50e4794"
OLDER = "2026-01-01"


def configured(**changes: object) -> Settings:
    return Settings(**{**DEVELOPMENT, **changes})


def policy(storage=DIRECTORY, environ: dict[str, str] | None = None, **changes: object) -> str:
    return privacy_policy(configured(**changes), storage, environ or {})


def accepted(session, email="ada@example.com") -> User:
    session.expire_all()
    return session.scalars(select(User).where(User.email == email)).one()


def an_older_edition(session, user: User) -> None:
    """Make the user one who accepted the edition before this one."""
    session.execute(update(User).where(User.id == user.id).values(terms_version=OLDER))
    session.commit()


def accept(client, version=TERMS_VERSION):
    return client.post("/api/auth/terms", json={"version": version})


def terms_rows(session, user: User) -> list[dict]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditEvent.detail).where(
                AuditEvent.user_id == user.id, AuditEvent.action == "terms_accepted"
            )
        )
    )


def test_the_templates_change_only_with_a_decision_about_the_edition():
    # Changing what the documents say may or may not have to be accepted again: a new section
    # does, a typo does not. Either way it is a decision, made here.
    digest = hashlib.sha256()
    for name in ("terms.md", "privacy.md"):
        digest.update((TEMPLATES / name).read_bytes())
    assert digest.hexdigest() == SETTLED, (
        "terms.md or privacy.md changed. If every user should accept the change, move "
        "TERMS_VERSION in app/legal/__init__.py to today; either way, set SETTLED in this "
        f"test to {digest.hexdigest()}."
    )


def test_the_documents_name_who_runs_the_deployment():
    settings = configured(
        operator_name="Emudoc GmbH, Example Street 1, 10115 Berlin, Germany",
        operator_email="privacy@emudoc.example.com",
    )
    for text in (terms_of_service(settings), privacy_policy(settings, DIRECTORY, {})):
        assert "between you and Emudoc GmbH" in text or "Emudoc GmbH, Example" in text
        assert "[privacy@emudoc.example.com](mailto:privacy@emudoc.example.com)" in text
        # Every placeholder was filled; a template that names one the code does not fill
        # fails to render at all.
        assert "$" not in text
        assert "Version of 15 October 2026." in text
    # Development shows what to set; production refuses to start without it.
    unset = terms_of_service(configured())
    assert "OPERATOR_NAME is not set" in unset and "OPERATOR_EMAIL is not set" in unset


def test_the_privacy_policy_says_what_the_settings_do():
    # The models: a free Gemini key is said to be one, and no promise is made for a provider
    # whose address was moved off the deployment's network.
    assert "free tier of Google's Gemini API" in policy()
    paid = policy(google_paid_tier=True)
    assert "None of the AI model providers may use what it receives" in paid
    assert "free tier" not in paid
    assert "None of the AI model providers" in policy(
        index_model="anthropic:claude-haiku-5-5", agent_model="openrouter:anthropic/claude"
    )
    moved = {"OPENAI_BASE_URL": "https://api.together.xyz/v1"}
    elsewhere = policy(environ=moved, index_model="openai:gpt-5", agent_model="openai:gpt-5")
    assert "set by its own terms; ask us about them" in elsewhere
    local = policy(
        environ={"OPENAI_BASE_URL": "http://10.0.0.5:8000/v1"},
        index_model="openai:llama",
        agent_model="ollama:llama3",
    )
    assert "None of the AI model providers" in local
    assert "ask us about them" in policy(index_model="groq:llama", agent_model="groq:llama")
    # A fallback is sent what its primary is, so the promise holds it too.
    claude = {
        "index_model": "anthropic:claude-haiku-5-5",
        "agent_model": "anthropic:claude-opus-5-5",
    }
    assert "ask us about them" in policy(**claude, agent_fallback_model="groq:llama")
    assert "free tier" in policy(**claude, index_fallback_model="google:gemini-3.5-flash-lite")
    # The embeddings endpoint is sent every passage, so the promise holds it too: one on the
    # deployment's network or under OpenAI's or Google Cloud's terms keeps it, any other not.
    for endpoint in (
        "http://ollama:11434/v1",
        "https://api.openai.com/v1",
        "https://europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/l/endpoints/openapi",
    ):
        assert "None of the AI model providers" in policy(
            google_paid_tier=True, embedding_base_url=endpoint
        )
    hosted = policy(google_paid_tier=True, embedding_base_url="https://api.voyageai.com/v1")
    assert "set by its own terms; ask us about them" in hosted
    assert "None of the AI model providers" not in hosted

    # Where the documents are kept.
    assert "does not keep the data in one region" in policy()
    assert "kept and processed in the European Union" in policy(data_region="eu")

    # How long each thing is kept, by plan, from the plans themselves.
    text = policy()
    assert "(Free: 30 days; Pro: 365 days; Team and Enterprise: until deleted)" in text
    assert "(Free: 90 days; Pro and Team: 365 days; Enterprise: as long as it exists)" in text
    assert "until 30 days after they expired or were ended" in text
    assert "It goes 14 days after you ask" in text
    assert "until they expire (BACKUP_DAYS is not set)" in text
    assert "this deployment keeps none" in policy(backup_days=0)
    assert "kept for 35 days" in policy(backup_days=35)

    # The cookies, by the names the code sets them under.
    assert "`emudoc_session`, which keeps you\nsigned in for up to 7 days" in policy(session_days=7)
    assert "`emudoc_oauth`, which lasts 10 minutes" in text
    assert "the pages load nothing from other sites, so" in text
    in_a_bucket = S3Storage(
        FakeS3Client("emudoc", origin="https://s3.eu-central-1.example"), "emudoc"
    )
    assert "come straight from the file storage" in policy(storage=in_a_bucket)

    # Scanning is promised only where a scanner is set.
    assert "scanned for malware" not in text
    assert "scanned for malware" in policy(scanner_url="clamd://clamav:3310")
    assert "scanned for malware" not in policy(scanner_url="off")


def test_the_documents_are_published_to_anyone(client, signed_in_only):
    assert me(client).status_code == 401
    response = client.get("/api/legal")
    assert response.status_code == 200
    legal = response.json()
    assert legal["version"] == TERMS_VERSION and legal["published"] == "15 October 2026"
    assert legal["terms"].startswith("# Terms of service")
    assert legal["privacy"].startswith("# Privacy policy")
    # The deployment's own settings fill them, as the subprocessor list's do.
    assert f"up to {get_settings().session_days} days" in legal["privacy"]


def test_sign_up_records_the_terms_its_form_showed(client, session, signed_in_only):
    body = {"email": "ada@example.com", "name": "Ada", "password": PASSWORD}
    refused = client.post("/api/auth/signup", json=body)
    assert refused.status_code == 400 and refused.json()["detail"] == ACCEPT_TERMS
    stale = client.post("/api/auth/signup", json={**body, "terms_version": OLDER})
    assert stale.status_code == 409 and stale.json()["detail"] == TERMS_CHANGED
    assert session.scalars(select(User)).all() == []

    account = signup(client)
    assert account["terms_version"] == TERMS_VERSION and account["terms_due"] is False
    assert account["terms_accepted_at"] is not None
    user = accepted(session)
    assert user.terms_version == TERMS_VERSION and user.terms_accepted_at is not None
    assert terms_rows(session, user) == [{"version": TERMS_VERSION}]
    assert client.get("/api/projects").status_code == 200


def test_a_new_edition_is_accepted_before_anything_else(client, session, signed_in_only):
    signup(client)
    user = accepted(session)
    an_older_edition(session, user)

    shown = me(client).json()
    assert shown["terms_due"] is True and shown["terms_version"] == OLDER
    for refused in (client.get("/api/projects"), client.post("/api/projects", json={"name": "X"})):
        assert refused.status_code == 403 and refused.json()["detail"] == terms_pending()
    # A copy of the data needs no acceptance.
    assert client.get("/api/auth/account/export").status_code == 200

    # Only the edition on show can be accepted.
    behind = accept(client, OLDER)
    assert behind.status_code == 409 and behind.json()["detail"] == TERMS_CHANGED
    taken = accept(client)
    assert taken.status_code == 200
    assert taken.json()["terms_due"] is False and taken.json()["terms_version"] == TERMS_VERSION
    assert client.get("/api/projects").status_code == 200
    # Accepting the edition already accepted records nothing more.
    assert accept(client).status_code == 200
    assert terms_rows(session, user) == [{"version": TERMS_VERSION}] * 2


def test_an_account_made_through_a_provider_accepts_before_it_starts(
    client, session, identity_provider, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "google_oauth_client_id", "google-client")
    monkeypatch.setattr(get_settings(), "google_oauth_client_secret", "google-secret")
    _, handshake = start(client)
    identity_provider.claims = google_claims(handshake.nonce)
    assert callback(client, code="the-code", state=handshake.state).status_code == 302

    shown = me(client).json()
    assert shown["terms_due"] is True and shown["terms_version"] is None
    assert client.get("/api/projects").status_code == 403
    assert accept(client).json()["terms_due"] is False
    assert client.get("/api/projects").status_code == 200
    assert terms_rows(session, accepted(session)) == [{"version": TERMS_VERSION}]


def test_an_account_that_will_not_accept_can_still_leave(client, session, signed_in_only):
    signup(client)
    an_older_edition(session, accepted(session))
    deletion = client.post("/api/auth/account/deletion", json={"password": PASSWORD})
    assert deletion.status_code == 200, deletion.text
    assert deletion.json()["deletion_due_at"] is not None
    # Kept again, the account is back where it was: the terms still wait.
    kept = client.delete("/api/auth/account/deletion")
    assert kept.status_code == 200 and kept.json()["terms_due"] is True


def test_the_local_account_accepts_nothing(client):
    # Development without signing in: no session, nobody to ask.
    shown = me(client).json()
    assert shown["terms_due"] is False and shown["terms_version"] is None
    assert client.get("/api/projects").status_code == 200
