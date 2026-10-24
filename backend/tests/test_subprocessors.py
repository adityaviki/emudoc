"""The subprocessor list: read from the settings, and published to anyone who asks."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.indexing.pipeline import Services
from app.main import create_app
from app.storage import LocalStorage, S3Storage, Storage
from app.subprocessors import subprocessors
from tests.conftest import APP_HEADERS, FakeEmbedder, FakeLLM, FakeS3Client

# Every setting the list reads, as a development machine has it, so that nothing in the
# environment the suite runs in changes what a test expects; but for the breached-password
# check, on by default, which the tests that are about it turn on.
DEVELOPMENT = {
    "_env_file": None,
    "database_url": "postgresql+psycopg://db.internal/emudoc",
    "index_model": "google:gemini-2.5-flash-lite",
    "agent_model": "google:gemini-2.5-flash-lite",
    "index_fallback_model": "",
    "agent_fallback_model": "",
    "embedding_base_url": "http://127.0.0.1:11434/v1",
    "smtp_url": "",
    "resend_api_key": "",
    "resend_webhook_secret": "",
    "inbound_domain": "",
    "hosting_provider": "",
    "data_region": "",
    "google_oauth_client_id": "",
    "microsoft_oauth_client_id": "",
    "sentry_dsn": "",
    "pwned_passwords_url": "",
    "stripe_secret_key": "",
    "stripe_tax": False,
}
# Files in a directory, which is the deployment's own disk; the list only asks where it is.
DIRECTORY = LocalStorage(Path("unused"))


def configured(**changes: object) -> Settings:
    return Settings(**{**DEVELOPMENT, **changes})


def bucket(origin: str, location: str | None = None) -> S3Storage:
    """A bucket whose signed URLs lead to `origin`, in the region `location` by its answer."""
    return S3Storage(FakeS3Client("emudoc", origin=origin, location=location), "emudoc")


def listed(settings: Settings, storage: Storage = DIRECTORY, **environ: str) -> list[str]:
    return [entry.name for entry in subprocessors(settings, storage, environ)]


def test_development_sends_data_to_the_model_s_company_alone():
    # Ollama on this machine, files in a directory, mail to the log, no OAuth: only the model
    # leaves the machine, and one company serving both roles is one entry naming both.
    (model,) = subprocessors(configured(), DIRECTORY, {})
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
        pwned_passwords_url="https://api.pwnedpasswords.com",
        google_oauth_client_id="client",
        microsoft_oauth_client_id="client",
    )
    s3 = bucket("https://emudoc.s3.eu-central-1.amazonaws.com", "eu-central-1")
    entries = subprocessors(settings, s3, {})
    assert [entry.name for entry in entries] == [
        "Hetzner Online GmbH, Germany",
        "Amazon Web Services",
        "Google Cloud (Vertex AI)",
        "Anthropic",
        "Voyage AI",
        "Postmark",
        "Have I Been Pwned",
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


def test_the_mail_is_listed_by_whichever_sends_it():
    # Resend's API in place of a relay is Resend, whose key is no part of the list.
    settings = configured(resend_api_key="re_123456")
    (mail,) = [entry for entry in subprocessors(settings, DIRECTORY, {}) if entry.name == "Resend"]
    assert mail.purpose.startswith("Sends the emails: the links that make an account")
    assert mail.purpose.endswith("and says which addresses bounce.")
    assert "re_123456" not in str(mail)
    # Receiving the projects' mail too, it receives documents.
    receiving = configured(
        resend_api_key="re_123456", resend_webhook_secret="whsec_abc", inbound_domain="in.x.com"
    )
    (mail,) = [entry for entry in subprocessors(receiving, DIRECTORY, {}) if entry.name == "Resend"]
    assert mail.purpose.endswith(
        "Receives the mail sent to the projects' addresses, which becomes their documents."
    )
    assert "their attachments among them" in mail.data
    # SES's relay is Amazon's; one on the deployment's own network is nobody's.
    ses = configured(smtp_url="smtp://user:secret@email-smtp.eu-central-1.amazonaws.com:587")
    assert "Amazon Web Services" in listed(ses)
    assert "mail" not in " ".join(listed(configured(smtp_url="smtp://mail:25")))


def test_a_fallback_s_company_is_listed_as_standing_in():
    # It is sent what its primary is, whenever the primary cannot answer.
    settings = configured(agent_fallback_model="anthropic:claude-haiku-5-5")
    gemini, claude = subprocessors(settings, DIRECTORY, {})
    assert (gemini.name, claude.name) == ("Google (Gemini API)", "Anthropic")
    assert claude.purpose == (
        "Answers the questions asked in the Ask tab when the model that usually does cannot."
    )
    assert claude.data.startswith("Questions, the project's name and description")
    # A company that does the work anyway does not stand in for itself.
    (gemini,) = subprocessors(
        configured(index_fallback_model="google:gemini-3.5-flash-lite"), DIRECTORY, {}
    )
    assert "usually" not in gemini.purpose
    assert gemini.data.count("The text of documents") == 1


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
        assert listed(settings, bucket("http://127.0.0.1:7070")) == ["Google (Gemini API)"], address


def test_the_breached_password_check_is_named_unless_it_is_the_deployment_s_own():
    # On by default: a development machine with nothing set sends it the start of a hash.
    assert Settings.model_fields["pwned_passwords_url"].default == "https://api.pwnedpasswords.com"
    _, check = subprocessors(
        configured(pwned_passwords_url="https://api.pwnedpasswords.com/"), DIRECTORY, {}
    )
    assert check.name == "Have I Been Pwned"
    assert check.purpose.startswith("Checks each new password against the passwords exposed")
    assert check.data.startswith("The first five characters of the SHA-1 hash")
    assert "no password, account or email address" in check.data
    # A copy of its list served on the deployment's own network, or none, is nobody else.
    for address in ("http://pwned:8080", ""):
        assert listed(configured(pwned_passwords_url=address)) == ["Google (Gemini API)"]


def test_a_host_the_list_does_not_know_is_named_as_it_is():
    # Nothing the app sends data to may be missing from the page, so an unknown company is
    # named by its host rather than left out; a known company is matched by its domain only.
    settings = configured(
        index_model="huggingface:some/model",
        agent_model="huggingface:some/model",
        embedding_base_url="https://embeddings.example.net/v1",
        smtp_url="smtps://relay.notsendgrid.net:465",
    )
    assert listed(settings, bucket("https://files.example.org")) == [
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
    entries = subprocessors(through, DIRECTORY, {})
    assert [entry.name for entry in entries] == ["OpenRouter", "Google (Gemini API)"]
    assert entries[0].purpose.endswith("providers that neither stores it nor trains on it.")

    around = configured(
        index_model="openrouter:google/gemini-2.5-flash",
        agent_model="openai:google/gemini-2.5-flash",
    )
    environ = {"OPENAI_BASE_URL": "https://openrouter.ai/api/v1"}
    (entry,) = subprocessors(around, DIRECTORY, environ)
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
    monkeypatch.setattr(settings, "data_region", "")
    for name in ("google_oauth_client_id", "microsoft_oauth_client_id"):
        monkeypatch.setattr(settings, name, "")
    s3 = FakeS3Client("bucket")
    over_bucket = Services(FakeLLM(), FakeEmbedder(), S3Storage(s3, "bucket", "emudoc"))
    with TestClient(create_app(services=over_bucket, mailer=mailer), headers=APP_HEADERS) as client:
        assert client.get("/api/status").status_code == 401
        response = client.get("/api/subprocessors")

    assert response.status_code == 200
    assert response.json()["region"] is None
    entries = response.json()["subprocessors"]
    assert [entry["name"] for entry in entries] == [
        "Hetzner Online GmbH, Germany",
        "bucket.s3.example",
        "Google (Gemini API)",
        "Anthropic",
        "Twilio SendGrid",
    ]
    assert set(entries[0]) == {"name", "purpose", "data", "location"}
    # A bucket on a service the app cannot place, and a relay, say nothing of where they are.
    assert [entry["location"] for entry in entries] == [
        None,
        None,
        "Anywhere (the Gemini Developer API)",
        "Anywhere (unless the Claude workspace keeps inference in the US)",
        None,
    ]
    # Companies, not configuration: no model, key or password reaches the page.
    assert "claude" not in response.text and "secret" not in response.text


def test_each_company_says_where_it_handles_the_data():
    # Read from the same settings the providers read: the bucket's own answer, the Vertex
    # location, and for who hosts it the region the operator declares.
    settings = configured(
        data_region="eu",
        hosting_provider="Hetzner Online GmbH, Germany",
        index_model="google-cloud:gemini-2.5-flash-lite",
        agent_model="openrouter:anthropic/claude-sonnet-5",
        embedding_base_url="https://europe-west4-aiplatform.googleapis.com/v1/projects/p/openapi",
        smtp_url="smtp://user:secret@smtp.postmarkapp.com:587",
    )
    environ = {"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json", "GOOGLE_CLOUD_LOCATION": "eu"}
    s3 = bucket("https://emudoc.s3.eu-central-1.amazonaws.com", "eu-central-1")
    entries = subprocessors(settings, s3, environ)
    assert [(entry.name, entry.location) for entry in entries] == [
        ("Hetzner Online GmbH, Germany", "The European Union"),
        ("Amazon Web Services", "The European Union (eu-central-1)"),
        ("Google Cloud (Vertex AI)", "The European Union (the eu multi-region)"),
        ("OpenRouter", "The European Union (eu.openrouter.ai)"),
        ("Google Cloud", "The European Union (europe-west4)"),
        # The relay receives addresses, not documents, and nothing says where it runs.
        ("Postmark", None),
    ]


def test_a_company_running_two_models_in_two_places_names_both():
    settings = configured(
        index_model="bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        agent_model="bedrock:global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    )
    (entry,) = subprocessors(settings, DIRECTORY, {"AWS_DEFAULT_REGION": "eu-central-1"})
    assert entry.name == "Amazon Web Services (Bedrock)"
    assert entry.location == (
        "The European Union (the eu inference profile); Anywhere (the global inference profile)"
    )


def test_the_page_says_which_region_the_deployment_keeps_its_documents_in(
    services, mailer, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "data_region", "eu")
    monkeypatch.setattr(settings, "hosting_provider", "Hetzner Online GmbH, Germany")
    with TestClient(create_app(services=services, mailer=mailer), headers=APP_HEADERS) as client:
        page = client.get("/api/subprocessors").json()
    assert page["region"] == "eu"
    assert page["subprocessors"][0] == {
        "name": "Hetzner Online GmbH, Germany",
        "purpose": "Runs the app's servers and its database.",
        "data": "Everything the app keeps: documents, questions and answers, and accounts.",
        "location": "The European Union",
    }


def test_the_error_tracker_is_named_with_where_it_keeps_the_reports():
    def tracker(dsn: str, region: str = ""):
        settings = configured(sentry_dsn=dsn, data_region=region)
        entries = subprocessors(settings, DIRECTORY, {})
        return [entry for entry in entries if "errors" in entry.purpose]

    (eu,) = tracker("https://key@o1.ingest.de.sentry.io/2", "eu")
    assert eu.name == "Sentry"
    assert eu.location == "The European Union (Sentry's EU region, Germany)"
    assert eu.data.endswith("It receives no documents, questions, answers or email addresses.")
    (us,) = tracker("https://key@o1.ingest.us.sentry.io/2")
    assert us.location == "The United States (Sentry's US region)"
    # Under a region the tracker is not held to it, since it receives no documents; the list
    # says where it is.
    assert tracker("https://key@o1.ingest.sentry.io/2", "eu")[0].location == us.location
    (hosted,) = tracker("https://key@app.glitchtip.com/3")
    assert (hosted.name, hosted.location) == ("GlitchTip", None)
    # A tracker of the deployment's own is no subprocessor.
    assert tracker("http://key@glitchtip:8000/1") == []
    assert tracker("https://key@sentry.internal/1") == []


def test_stripe_is_listed_where_the_plans_are_sold():
    assert "Stripe" not in listed(configured())
    (stripe,) = [
        entry
        for entry in subprocessors(configured(stripe_secret_key="sk_test_x"), DIRECTORY, {})
        if entry.name == "Stripe"
    ]
    assert stripe.purpose == (
        "Takes the payments for the paid plans, keeps the payment details and issues the invoices."
    )
    assert "the email address of the owner who buys" in stripe.data
    assert "It receives no documents." in stripe.data
    taxed = subprocessors(configured(stripe_secret_key="sk_test_x", stripe_tax=True), DIRECTORY, {})
    assert taxed[-1].purpose.endswith("issues the invoices and works out the tax due.")
