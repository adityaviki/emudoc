"""The subprocessor list: read from the settings, and published to anyone who asks."""

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.indexing.pipeline import Services
from app.main import create_app
from app.storage import S3Storage
from app.subprocessors import subprocessors
from tests.conftest import APP_HEADERS, FakeEmbedder, FakeLLM, FakeS3Client

# Every setting the list reads, as a development machine has it, so that nothing in the
# environment the suite runs in changes what a test expects.
DEVELOPMENT = {
    "_env_file": None,
    "database_url": "postgresql+psycopg://db.internal/emudoc",
    "index_model": "google:gemini-2.5-flash-lite",
    "agent_model": "google:gemini-2.5-flash-lite",
    "embedding_base_url": "http://127.0.0.1:11434/v1",
    "smtp_url": "",
    "hosting_provider": "",
    "google_oauth_client_id": "",
    "microsoft_oauth_client_id": "",
}


def configured(**changes: object) -> Settings:
    return Settings(**{**DEVELOPMENT, **changes})


def listed(settings: Settings, origin: str | None = None, **environ: str) -> list[str]:
    return [entry.name for entry in subprocessors(settings, origin, environ)]


def test_development_sends_data_to_the_model_s_company_alone():
    # Ollama on this machine, files in a directory, mail to the log, no OAuth: only the model
    # leaves the machine, and one company serving both roles is one entry naming both.
    (model,) = subprocessors(configured(), None, {})
    assert model.name == "Google (Gemini API)"
    assert model.purpose.startswith("Reads documents to index them")
    assert "Answers the questions asked in the Ask tab." in model.purpose
    assert model.data.startswith("The text of documents and images of their scanned pages.")
    assert "Questions, the project's name and description" in model.data


def test_a_deployment_lists_every_company_it_sends_data_to_in_order():
    settings = configured(
        hosting_provider="Hetzner Online GmbH, Germany",
        index_model="google-cloud:gemini-2.5-flash-lite",
        agent_model="anthropic:claude-opus-5-5",
        embedding_base_url="https://api.voyageai.com/v1",
        smtp_url="smtp://user:secret@smtp.postmarkapp.com:587",
        google_oauth_client_id="client",
        microsoft_oauth_client_id="client",
    )
    entries = subprocessors(settings, "https://emudoc.s3.eu-central-1.amazonaws.com", {})
    assert [entry.name for entry in entries] == [
        "Hetzner Online GmbH, Germany",
        "Amazon Web Services",
        "Google Cloud (Vertex AI)",
        "Anthropic",
        "Voyage AI",
        "Postmark",
        "Google",
        "Microsoft",
    ]
    purposes = {entry.name: entry.purpose for entry in entries}
    assert purposes["Hetzner Online GmbH, Germany"] == "Runs the app's servers and its database."
    assert purposes["Amazon Web Services"].startswith("Keeps the uploaded files")
    assert purposes["Google Cloud (Vertex AI)"].startswith("Reads documents to index them")
    assert purposes["Anthropic"] == "Answers the questions asked in the Ask tab."
    assert purposes["Voyage AI"].startswith("Search by meaning")
    assert purposes["Microsoft"] == "Signs in the members who choose to sign in with Microsoft."
    # The relay's password is in its URL; the list names the company, never the URL.
    assert all("secret" not in str(entry) for entry in entries)


def test_services_on_the_deployment_s_own_network_are_not_subprocessors():
    for address in (
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
        "http://ollama:11434/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.20/v1",
        "http://169.254.0.9/v1",
        "http://host.docker.internal:11434/v1",
        "http://embed.emudoc.svc.cluster.local/v1",
    ):
        settings = configured(embedding_base_url=address, smtp_url="smtp://mail:25")
        assert listed(settings, "http://127.0.0.1:7070") == ["Google (Gemini API)"], address


def test_a_host_the_list_does_not_know_is_named_as_it_is():
    # Nothing the app sends data to may be missing from the page, so an unknown company is
    # named by its host rather than left out; a known company is matched by its domain only.
    settings = configured(
        index_model="huggingface:some/model",
        agent_model="huggingface:some/model",
        embedding_base_url="https://embeddings.example.net/v1",
        smtp_url="smtps://relay.notsendgrid.net:465",
    )
    assert listed(settings, "https://files.example.org") == [
        "files.example.org",
        "huggingface",
        "embeddings.example.net",
        "relay.notsendgrid.net",
    ]


def test_a_model_moved_to_another_address_is_listed_where_it_runs():
    # Ollama or an OpenAI-compatible server on the deployment's own machines sends nothing out;
    # the same prefix pointed at a company is that company.
    local = configured(index_model="ollama:qwen3", agent_model="openai:local-model")
    own = {"OLLAMA_BASE_URL": "http://ollama:11434/v1", "OPENAI_BASE_URL": "http://vllm:8000/v1"}
    assert listed(local, **own) == []
    assert listed(local, OLLAMA_BASE_URL="http://ollama:11434/v1") == ["OpenAI"]
    assert listed(local, OPENAI_BASE_URL="https://api.together.xyz/v1") == ["ollama", "Together AI"]


def test_openrouter_is_said_to_forward_only_when_it_is_told_to_keep_nothing():
    # The openrouter: adapter sends the data policy (app/llm.py); an openai: model pointed at
    # OpenRouter's address reaches it without one, and the page must not claim otherwise.
    through = configured(index_model="openrouter:google/gemini-2.5-flash")
    entries = subprocessors(through, None, {})
    assert [entry.name for entry in entries] == ["OpenRouter", "Google (Gemini API)"]
    assert entries[0].purpose.endswith("providers that neither stores it nor trains on it.")

    around = configured(
        index_model="openrouter:google/gemini-2.5-flash",
        agent_model="openai:google/gemini-2.5-flash",
    )
    (entry,) = subprocessors(around, None, {"OPENAI_BASE_URL": "https://openrouter.ai/api/v1"})
    assert entry.name == "OpenRouter" and "forwards" not in entry.purpose


def test_the_list_is_public_and_follows_the_deployment(
    services, mailer, signed_in_only, monkeypatch
):
    # Published to the people deciding whether to upload, so it answers without a session.
    settings = get_settings()
    monkeypatch.setattr(settings, "hosting_provider", "Hetzner Online GmbH, Germany")
    monkeypatch.setattr(settings, "agent_model", "anthropic:claude-opus-5-5")
    monkeypatch.setattr(settings, "smtp_url", "smtp://user:secret@smtp.sendgrid.net:587")
    monkeypatch.setattr(settings, "embedding_base_url", "http://127.0.0.1:11434/v1")
    for name in ("google_oauth_client_id", "microsoft_oauth_client_id"):
        monkeypatch.setattr(settings, name, "")
    s3 = FakeS3Client("bucket")
    over_bucket = Services(FakeLLM(), FakeEmbedder(), S3Storage(s3, "bucket", "emudoc"))
    with TestClient(create_app(services=over_bucket, mailer=mailer), headers=APP_HEADERS) as client:
        assert client.get("/api/status").status_code == 401
        response = client.get("/api/subprocessors")

    assert response.status_code == 200
    entries = response.json()
    assert [entry["name"] for entry in entries] == [
        "Hetzner Online GmbH, Germany",
        "bucket.s3.example",
        "Google (Gemini API)",
        "Anthropic",
        "Twilio SendGrid",
    ]
    assert set(entries[0]) == {"name", "purpose", "data"}
    # Companies, not configuration: no model, key or password reaches the page.
    assert "claude" not in response.text and "secret" not in response.text
