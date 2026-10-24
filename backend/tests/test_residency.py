"""Data residency: where each piece handles the documents, and what holds a deployment to the
region it promises (app/residency.py)."""

from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts import create_account
from app.config import Settings, get_settings
from app.main import create_app
from app.models import Organization
from app.residency import check_residency, model_place, residency_problems
from app.storage import LocalStorage, S3Storage
from tests.conftest import FakeS3Client
from tests.test_auth import signup

# Everything residency reads, as a deployment that keeps its data in the EU has it, with its
# files in a directory and the embeddings on its own network: the models are what each test
# changes.
FIT = {
    "_env_file": None,
    "database_url": "postgresql+psycopg://db.internal/emudoc",
    "data_region": "eu",
    "index_model": "google-cloud:gemini-2.5-flash-lite",
    "agent_model": "google-cloud:gemini-2.5-flash-lite",
    "embedding_base_url": "http://embeddings:8080/v1",
    "inbound_domain": "",
    "inbound_mail_queue": "",
}
DIRECTORY = LocalStorage(Path("unused"))
# A Vertex AI service account in the EU multi-region.
VERTEX_EU = {"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json", "GOOGLE_CLOUD_LOCATION": "eu"}


def configured(**changes: object) -> Settings:
    return Settings(**{**FIT, **changes})


def problems(storage=DIRECTORY, environ: dict | None = None, **changes: object) -> list[str]:
    """What keeps `configured(**changes)` out of its region; Vertex AI is in the region's
    multi-region unless `environ` says otherwise."""
    settings = configured(**changes)
    if environ is None:
        environ = {**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": settings.data_region or "eu"}
    return residency_problems(settings, storage, environ)


def bucket(origin: str, location: str | None = None) -> tuple[S3Storage, FakeS3Client]:
    client = FakeS3Client("emudoc", origin=origin, location=location)
    return S3Storage(client, "emudoc"), client


def test_mail_received_through_resend_is_kept_in_no_region_the_settings_show():
    resend = {
        "resend_api_key": "re_123456",
        "resend_webhook_secret": "whsec_abc",
        "inbound_domain": "in.example.com",
    }
    assert problems(**resend) == [
        "Resend receives the mail sent to the projects' addresses at INBOUND_DOMAIN, their "
        "attachments among them, and the settings cannot show that it keeps them in the "
        "European Union; receive it through Amazon SES in the European Union, such as "
        "eu-central-1, its queue named in INBOUND_MAIL_QUEUE, or set INBOUND_DOMAIN empty to "
        "receive none."
    ]
    assert problems(**{**resend, "inbound_domain": ""}) == []
    assert problems(**{**resend, "data_region": ""}) == []


def test_mail_received_through_ses_is_kept_in_its_queue_s_region():
    def ses(queue: str, region: str = "eu") -> list[str]:
        return problems(
            data_region=region, inbound_domain="in.example.com", inbound_mail_queue=queue
        )

    assert (
        ses("https://sqs.eu-central-1.amazonaws.com/123456789012/emudoc-production-inbound") == []
    )
    assert ses("https://sqs.us-east-1.amazonaws.com/1/inbound", region="us") == []
    assert ses("https://sqs.eu-west-2.amazonaws.com/1/inbound") == [
        "SES receives the mail sent to the projects' addresses in eu-west-2, which is not in the "
        "European Union; receive it through Amazon SES in the European Union, such as "
        "eu-central-1, its queue named in INBOUND_MAIL_QUEUE, or set INBOUND_DOMAIN empty to "
        "receive none."
    ]
    assert ses("https://queue.elsewhere.example/1/inbound")[0].startswith(
        "INBOUND_MAIL_QUEUE is at queue.elsewhere.example, and the app cannot tell"
    )
    # A stand-in for SQS on the deployment's own network is where the deployment is.
    assert ses("http://sqs:9324/1/inbound") == []


def test_without_a_region_nothing_is_held_to_one():
    assert problems(data_region="", index_model="google:gemini-2.5-flash-lite") == []
    assert problems() == []


@pytest.mark.parametrize("region", ["eu", "us"])
def test_the_gemini_developer_api_is_kept_in_no_region(region):
    # Google's terms let it process the requests in any country where it has facilities.
    (problem,) = problems(data_region=region, index_model="google:gemini-2.5-flash-lite")
    assert problem.startswith("INDEX_MODEL uses the Gemini Developer API (google:)")
    assert f"google-cloud:gemini-2.5-flash-lite, with GOOGLE_CLOUD_LOCATION={region}" in problem


@pytest.mark.parametrize(
    ("environ", "region", "kept"),
    [
        (VERTEX_EU, "eu", True),
        ({**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": "europe-west4"}, "eu", True),
        # London and Zurich are in Europe, not in the European Union.
        ({**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": "europe-west2"}, "eu", False),
        ({**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": "europe-west6"}, "eu", False),
        ({**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": "global"}, "eu", False),
        ({**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": "global"}, "us", False),
        # Unset, the provider sends to us-central1.
        ({"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json"}, "eu", False),
        ({"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json"}, "us", True),
        ({**VERTEX_EU, "GOOGLE_CLOUD_LOCATION": "us"}, "us", True),
    ],
)
def test_vertex_is_kept_where_its_location_is(environ, region, kept):
    found = problems(environ=environ, data_region=region)
    assert found == [] if kept else len(found) == 1
    if not kept:
        assert found[0].startswith("INDEX_MODEL and AGENT_MODEL use Vertex AI at ")
        assert "set GOOGLE_CLOUD_LOCATION to" in found[0]


def test_vertex_in_express_mode_names_no_location():
    # A Gemini key without a service account's file makes the provider use express mode, which
    # ignores GOOGLE_CLOUD_LOCATION.
    environ = {"GOOGLE_API_KEY": "key", "GOOGLE_CLOUD_LOCATION": "eu"}
    (problem,) = problems(environ=environ)
    assert "Vertex AI in express mode" in problem
    assert "sign in with a service account's key file" in problem


def test_claude_is_kept_in_the_us_by_the_app_and_never_in_the_eu():
    claude = "anthropic:claude-opus-5-5"
    # The app sends inference_geo=us on every request (app/llm.py), so the US needs nothing.
    assert problems(data_region="us", agent_model=claude) == []
    assert model_place(claude, "us", {}).region == "us"
    (problem,) = problems(agent_model=claude)
    assert problem.startswith("AGENT_MODEL uses Claude through Anthropic's API")
    assert "Vertex AI (google-cloud:) or Bedrock (bedrock:eu.<model>)" in problem


def test_a_fallback_is_held_to_the_region_as_its_primary_is():
    # It is sent what its primary is, whenever the primary cannot answer.
    (problem,) = problems(agent_fallback_model="anthropic:claude-haiku-5-5")
    assert problem.startswith("AGENT_FALLBACK_MODEL uses Claude")
    (problem,) = problems(
        index_fallback_model="google:gemini-3.5-flash-lite",
        agent_fallback_model="google:gemini-3.5-flash-lite",
    )
    assert problem.startswith(
        "INDEX_FALLBACK_MODEL and AGENT_FALLBACK_MODEL use the Gemini Developer API"
    )
    assert problems(agent_fallback_model="openrouter:anthropic/claude-haiku-5-5") == []


@pytest.mark.parametrize("region", ["eu", "us"])
def test_openrouter_is_sent_to_the_region_s_own_address(region):
    model = "openrouter:anthropic/claude-sonnet-5"
    assert problems(data_region=region, index_model=model, agent_model=model) == []
    assert str(model_place(model, region, {})).endswith(f"({region}.openrouter.ai)")


def test_openai_is_kept_in_the_eu_at_its_european_address_only():
    model = "openai:gpt-5.2"
    (problem,) = problems(agent_model=model)
    assert "OPENAI_BASE_URL=https://eu.api.openai.com/v1" in problem
    european = {**VERTEX_EU, "OPENAI_BASE_URL": "https://eu.api.openai.com/v1"}
    assert problems(environ=european, agent_model=model) == []
    # The app knows no address that keeps OpenAI in the US.
    (problem,) = problems(data_region="us", environ={}, index_model=model, agent_model=model)
    assert "nothing in the settings says which region runs it" in problem


@pytest.mark.parametrize(
    ("model", "aws_region", "kept"),
    [
        ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", None, True),
        ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", "us-east-1", True),
        ("global.anthropic.claude-haiku-4-5-20251001-v1:0", "eu-central-1", False),
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "eu-central-1", False),
        ("anthropic.claude-haiku-4-5-20251001-v1:0", "eu-central-1", True),
        ("anthropic.claude-haiku-4-5-20251001-v1:0", "eu-west-2", False),
        ("anthropic.claude-haiku-4-5-20251001-v1:0", None, False),
    ],
)
def test_bedrock_is_kept_by_its_inference_profile_or_its_region(model, aws_region, kept):
    environ = {**VERTEX_EU, **({"AWS_DEFAULT_REGION": aws_region} if aws_region else {})}
    found = problems(environ=environ, agent_model=f"bedrock:{model}")
    assert found == [] if kept else len(found) == 1
    if not kept:
        assert "use the European Union's inference profile, bedrock:eu.<model>" in found[0]


def test_a_model_on_the_deployment_s_own_network_is_where_the_deployment_is():
    own = {**VERTEX_EU, "OLLAMA_BASE_URL": "http://ollama:11434/v1"}
    assert problems(environ=own, agent_model="ollama:qwen3") == []
    # A provider the app cannot place is refused with the ones it can.
    (problem,) = problems(agent_model="mistral:mistral-large-latest")
    assert problem.startswith("AGENT_MODEL uses mistral:, and nothing in the settings says")
    assert "OpenRouter (openrouter:), which the app sends to its EU address." in problem


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("http://127.0.0.1:11434/v1", True),
        ("https://europe-west4-aiplatform.googleapis.com/v1/projects/p/endpoints/openapi", True),
        ("https://aiplatform.eu.rep.googleapis.com/v1/projects/p/endpoints/openapi", True),
        ("https://us-central1-aiplatform.googleapis.com/v1/projects/p/endpoints/openapi", False),
        ("https://eu.api.openai.com/v1", True),
        ("https://api.voyageai.com/v1", False),
    ],
)
def test_the_embeddings_are_kept_where_their_endpoint_is(url, kept):
    found = problems(embedding_base_url=url)
    assert found == [] if kept else len(found) == 1
    if not kept:
        assert found[0].startswith("EMBEDDING_BASE_URL is at ")
        assert "on the deployment's own network" in found[0]


@pytest.mark.parametrize(
    ("origin", "location", "kept"),
    [
        ("https://emudoc.s3.eu-central-1.amazonaws.com", "eu-central-1", True),
        # AWS answers EU for a bucket made in Ireland long ago, and nothing for us-east-1.
        ("https://emudoc.s3.amazonaws.com", "EU", True),
        ("https://emudoc.s3.amazonaws.com", None, False),
        ("https://emudoc.s3.eu-west-2.amazonaws.com", "eu-west-2", False),
        ("https://emudoc.fsn1.your-objectstorage.com", None, True),
        ("https://account.eu.r2.cloudflarestorage.com", None, True),
        ("https://account.r2.cloudflarestorage.com", None, False),
        ("https://files.example.org", None, False),
    ],
)
def test_the_bucket_is_kept_where_its_service_says(origin, location, kept):
    storage, client = bucket(origin, location)
    found = problems(storage)
    assert found == [] if kept else len(found) == 1
    # Only an AWS bucket is asked where it is; another service's answer means nothing.
    assert ("get_bucket_location" in client.calls) == ("amazonaws.com" in origin)


def test_a_bucket_on_the_deployment_s_own_network_is_where_the_deployment_is():
    # MinIO and its like answer us-east-1 unless told otherwise, so they are not asked.
    storage, client = bucket("http://minio:9000/emudoc", "us-east-1")
    assert problems(storage) == []
    assert "get_bucket_location" not in client.calls


def test_a_bucket_that_does_not_say_where_it_is_is_refused(caplog):
    storage, client = bucket("https://emudoc.s3.eu-central-1.amazonaws.com", "eu-central-1")
    client.outage = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetBucketLocation"
    )
    (problem,) = problems(storage)
    assert problem.startswith("The bucket in STORAGE_URL did not say which region it is in")
    assert "s3:GetBucketLocation" in problem
    assert "AccessDenied: Access Denied" in caplog.text


def production(monkeypatch, **changes: object) -> Settings:
    """The suite's settings as a production deployment in the EU with fit models; tests change
    what they check. The object is shared, so attributes are patched."""
    settings = get_settings()
    values = {
        "environment": "production",
        "data_region": "eu",
        "index_model": "google-cloud:gemini-2.5-flash-lite",
        "agent_model": "google-cloud:gemini-2.5-flash-lite",
        "embedding_base_url": "http://127.0.0.1:11434/v1",
        **changes,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    for name, value in VERTEX_EU.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return settings


def test_production_does_not_start_outside_its_region(session, services, monkeypatch):
    production(
        monkeypatch,
        agent_model="anthropic:claude-opus-5-5",
        embedding_base_url="https://api.voyageai.com/v1",
    )
    with pytest.raises(RuntimeError) as error:
        with TestClient(create_app(services=services)):
            pass
    message = str(error.value)
    assert message.startswith(
        "DATA_REGION is eu, but these would not keep the data in the European Union:\n  - "
    )
    assert "AGENT_MODEL uses Claude through Anthropic's API" in message
    assert "EMBEDDING_BASE_URL is at api.voyageai.com" in message

    # Held to the region, the same start goes through.
    monkeypatch.setattr(get_settings(), "agent_model", "google-cloud:gemini-2.5-flash-lite")
    monkeypatch.setattr(get_settings(), "embedding_base_url", "http://127.0.0.1:11434/v1")
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/health").json()["status"] == "ok"


def test_development_says_what_is_outside_the_region_and_starts(session, monkeypatch, caplog):
    settings = production(monkeypatch, environment="development")
    monkeypatch.setattr(settings, "index_model", "google:gemini-2.5-flash-lite")
    with caplog.at_level("WARNING", logger="app.residency"):
        check_residency(settings, DIRECTORY, {})
    assert "INDEX_MODEL uses the Gemini Developer API" in caplog.text


def test_an_organisation_records_the_region_it_was_created_in(session, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_region", "eu")
    # The member is shown the region their organisation's data is kept in.
    assert signup(client)["organization"]["region"] == "eu"
    organization = session.scalars(select(Organization).where(Organization.slug == "ada")).one()
    assert organization.region == "eu"

    # Without a region there is no promise to record.
    monkeypatch.setattr(get_settings(), "data_region", "")
    account = create_account(session, "grace@example.com", "Grace")
    session.commit()
    assert account.organization.region is None


def test_the_local_organisation_records_the_region_too(session, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_region", "us")
    # Made on the first request that needs it, as the local owner's.
    assert client.get("/api/auth/me").json()["organization"]["region"] == "us"


def test_organisations_from_before_the_region_are_given_it_once_it_is_kept(session):
    create_account(session, "ada@example.com", "Ada")
    session.commit()
    settings = configured(environment="production")

    check_residency(settings, DIRECTORY, VERTEX_EU)
    assert session.scalars(select(Organization.region)).all() == ["eu"]


def test_a_start_that_is_refused_records_no_region(session):
    create_account(session, "ada@example.com", "Ada")
    session.commit()
    unfit = configured(environment="production", index_model="google:gemini-2.5-flash-lite")
    with pytest.raises(RuntimeError, match="Gemini Developer API"):
        check_residency(unfit, DIRECTORY, VERTEX_EU)
    # A start that would be refused records nothing, so the operator may still choose another
    # region; in development the record follows the warning.
    session.expire_all()
    assert session.scalars(select(Organization.region)).all() == [None]


@pytest.mark.parametrize("region", ["us", ""])
def test_organisations_promised_another_region_are_not_served(session, region, caplog):
    promised = create_account(session, "ada@example.com", "Ada").organization
    promised.region = "eu"
    create_account(session, "grace@example.com", "Grace")
    session.commit()
    settings = configured(environment="production", data_region=region)
    environ = {"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json", "GOOGLE_CLOUD_LOCATION": "us"}

    with pytest.raises(RuntimeError) as error:
        check_residency(settings, DIRECTORY, environ)
    message = str(error.value)
    assert "1 in the European Union (such as ada)" in message
    now = f"DATA_REGION is {region}" if region else "DATA_REGION is not set"
    assert f"{now}; set it to the region they were promised" in message
    # The organisation without a record is left without one, not stamped with the wrong region.
    session.expire_all()
    regions = dict(session.execute(select(Organization.slug, Organization.region)).all())
    assert regions == {"ada": "eu", "grace": None}

    # Development says the same and serves, still recording nothing.
    with caplog.at_level("WARNING", logger="app.residency"):
        check_residency(configured(data_region=region), DIRECTORY, environ)
    assert "1 in the European Union (such as ada)" in caplog.text
    session.expire_all()
    assert session.get(Organization, promised.id).region == "eu"
    assert dict(session.execute(select(Organization.slug, Organization.region)).all()) == regions


def test_a_region_is_one_the_app_knows():
    assert configured(data_region=" EU ").data_region == "eu"
    with pytest.raises(ValueError, match="DATA_REGION is eu or us, or empty for no region"):
        configured(data_region="uk")
