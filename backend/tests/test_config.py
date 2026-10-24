"""What the settings refuse to run with: no database named, or ENVIRONMENT=production with
settings that only a development machine should have. Either stops the process with one
message, from uvicorn, alembic and the maintenance command alike."""

import base64
import os
import re
import secrets
import subprocess
import sys
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import (
    AGENT,
    BACKEND_DIR,
    DEFAULT_MODEL,
    MISSING_DATABASE,
    ROOT_DIR,
    ModelPrice,
    Settings,
    get_settings,
    load_settings,
)
from app.keyring import DEVELOPMENT_KEY
from app.main import MODELS_UNAVAILABLE, create_app
from app.network import MODEL_BASE_URLS

A_DATABASE = "postgresql+psycopg://app:secret@db.internal:5432/emudoc"
# The compose container's password on a server elsewhere: a copied development setup.
WITH_THE_DEVELOPMENT_PASSWORD = "postgresql+psycopg://emudoc:emudoc@db.internal:5432/emudoc"
API_DOCS = {"/openapi.json", "/docs", "/redoc"}
A_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
DEVELOPMENT = base64.b64encode(DEVELOPMENT_KEY).decode()


def paths_of(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_a_process_without_a_database_stops_with_what_to_do(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SystemExit) as stop:
        load_settings(_env_file=None)
    assert stop.value.code == MISSING_DATABASE
    assert "DATABASE_URL is not set" in MISSING_DATABASE
    assert ".env.example" in MISSING_DATABASE


def test_a_blank_database_url_counts_as_unset(monkeypatch):
    # A `DATABASE_URL=` line that was cleared, or an empty variable, is the same mistake.
    monkeypatch.setenv("DATABASE_URL", "   ")
    with pytest.raises(SystemExit) as stop:
        load_settings(_env_file=None)
    assert stop.value.code == MISSING_DATABASE


def test_the_database_comes_from_the_environment_or_the_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", A_DATABASE)
    assert load_settings(_env_file=None).database_url == A_DATABASE

    monkeypatch.delenv("DATABASE_URL")
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={A_DATABASE}\n")
    assert load_settings(_env_file=env_file).database_url == A_DATABASE


def test_the_example_env_file_names_the_development_database(monkeypatch):
    # `cp .env.example .env` must keep working now that the code has no default.
    monkeypatch.delenv("DATABASE_URL")
    settings = load_settings(_env_file=ROOT_DIR / ".env.example")
    assert settings.database_url == "postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/emudoc"


def test_other_mistakes_keep_the_validation_error_that_names_the_setting():
    with pytest.raises(ValueError, match="environment"):
        load_settings(_env_file=None, environment="staging")


def test_model_prices_are_read_per_model_and_a_malformed_entry_is_refused():
    settings = load_settings(
        _env_file=None,
        model_prices=" google:gemini-2.5-flash-lite = 0.10/0.40 , openrouter:a/b=2/0,, c=0/0",
    )
    assert settings.prices == {
        "google:gemini-2.5-flash-lite": ModelPrice(Decimal("0.10"), Decimal("0.40")),
        "openrouter:a/b": ModelPrice(Decimal("2"), Decimal("0")),
        "c": ModelPrice(Decimal("0"), Decimal("0")),
    }
    assert load_settings(_env_file=None).prices == {}
    assert load_settings(_env_file=None, price_currency="EUR").price_currency == "EUR"

    for malformed in ["x", "x=1", "x=1/2/3", "x=a/b", "x=-1/2", "=1/2", "x=1/2 y=3/4"]:
        with pytest.raises(ValueError, match="MODEL_PRICES lists what each model costs"):
            load_settings(_env_file=None, model_prices=malformed)
    with pytest.raises(ValueError, match="price_currency"):
        load_settings(_env_file=None, price_currency="usd")


@pytest.mark.parametrize(
    "url", ["/var/lib/emudoc", "file://data", "s3://", "s3://bucket/prefix?region=x", "gs://b"]
)
def test_a_storage_url_names_a_directory_or_a_bucket(url):
    # A bare path, a relative file URL, a bucket-less s3 URL or another scheme is refused with
    # the two forms that work.
    with pytest.raises(ValueError, match="STORAGE_URL names a directory as file:///") as error:
        load_settings(_env_file=None, storage_url=url)
    assert repr(url) in str(error.value)


def test_the_storage_defaults_to_the_data_directory_in_the_checkout(monkeypatch):
    monkeypatch.delenv("STORAGE_URL")
    settings = load_settings(_env_file=None)
    assert settings.storage_url == (BACKEND_DIR / "data").as_uri()
    assert settings.storage_url.startswith("file:///")


def test_a_command_without_a_database_prints_the_sentence_and_no_traceback():
    # What an operator sees: the one message, exit status 1, no traceback. An empty variable
    # takes precedence over any `.env` beside the app, so the run does not depend on that file.
    env = dict(os.environ, DATABASE_URL="")
    run = subprocess.run(
        [sys.executable, "-m", "app.maintenance", "rebuild-embeddings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 1
    assert run.stderr.strip() == MISSING_DATABASE
    assert "Traceback" not in run.stderr


@pytest.fixture
def deployment(tmp_path, monkeypatch) -> dict[str, object]:
    """A complete production configuration, as arguments for `load_settings`.

    The suite's own environment is a development one, so every variable the checks look at is
    cleared from it first; what a test leaves out is then really missing.
    """
    for name in (
        "ENVIRONMENT",
        "DATABASE_URL",
        "STORAGE_URL",
        "LOCAL_ACCOUNT",
        "APP_URL",
        "SMTP_URL",
        "SCANNER_URL",
        "EMBEDDING_BASE_URL",
        "PWNED_PASSWORDS_URL",
        "RUN_WORKER",
        "LOG_FORMAT",
        "GOOGLE_PAID_TIER",
        "HOSTING_PROVIDER",
        "OPERATOR_NAME",
        "OPERATOR_EMAIL",
        "BACKUP_DAYS",
        "SERVER_LOG_DAYS",
        "DATABASE_POOLER",
        "DATABASE_LISTEN_URL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "PGSSLROOTCERT",
        "AGENT_MODEL",
        "INDEX_MODEL",
        "AGENT_FALLBACK_MODEL",
        "INDEX_FALLBACK_MODEL",
        "ENCRYPTION_KEYS",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICES",
        "STRIPE_PACKS",
        "RESEND_API_KEY",
        "RESEND_WEBHOOK_SECRET",
        "MAIL_EVENTS_QUEUE",
    ):
        monkeypatch.delenv(name, raising=False)
    return {
        "_env_file": None,
        "environment": "production",
        "database_url": A_DATABASE,
        "agent_model": "google:gemini-2.5-flash-lite",
        "index_model": "google:gemini-2.5-flash-lite",
        "storage_url": (tmp_path / "data").as_uri(),
        "app_url": "https://emudoc.example.com",
        "smtp_url": "smtp://mailer:secret@smtp.example.com:587",
        "scanner_url": "clamd://clamav:3310",
        "embedding_base_url": "http://embeddings:8080/v1",
        "google_paid_tier": True,
        "hosting_provider": "Hetzner Online GmbH, Germany",
        "operator_name": "Emudoc GmbH, Example Street 1, 10115 Berlin, Germany",
        "operator_email": "privacy@emudoc.example.com",
        "backup_days": 35,
        "server_log_days": 30,
        "encryption_keys": A_KEY,
    }


def test_a_complete_production_configuration_is_accepted(deployment):
    settings = load_settings(**deployment)
    assert settings.environment == "production"
    assert settings.production_problems() == []


def test_production_refuses_every_development_convenience_in_one_message(deployment):
    # An operator fixes the whole list in one go instead of one setting per restart.
    for name in (
        "storage_url",
        "app_url",
        "smtp_url",
        "scanner_url",
        "embedding_base_url",
        "google_paid_tier",
        "hosting_provider",
        "operator_name",
        "operator_email",
        "backup_days",
        "server_log_days",
        "encryption_keys",
    ):
        del deployment[name]
    deployment["database_url"] = WITH_THE_DEVELOPMENT_PASSWORD
    deployment["local_account"] = True

    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    message = stop.value.code
    assert message.startswith("ENVIRONMENT is production, but")
    for variable in (
        "DATABASE_URL",
        "LOCAL_ACCOUNT",
        "STORAGE_URL",
        "APP_URL",
        "SMTP_URL",
        "SCANNER_URL",
        "EMBEDDING_BASE_URL",
        "GOOGLE_PAID_TIER",
        "HOSTING_PROVIDER",
        "OPERATOR_NAME",
        "OPERATOR_EMAIL",
        "BACKUP_DAYS",
        "SERVER_LOG_DAYS",
        "ENCRYPTION_KEYS",
    ):
        assert variable in message
    # Each line says what to do; none repeats the database's address or password.
    assert "emudoc:emudoc" not in message and "db.internal" not in message


@pytest.mark.parametrize(
    ("change", "variable"),
    [
        ({"database_url": WITH_THE_DEVELOPMENT_PASSWORD}, "DATABASE_URL"),
        ({"local_account": True}, "LOCAL_ACCOUNT"),
        ({"storage_url": None}, "STORAGE_URL"),
        ({"app_url": ""}, "APP_URL"),
        ({"smtp_url": ""}, "SMTP_URL"),
        ({"scanner_url": ""}, "SCANNER_URL"),
        ({"embedding_base_url": None}, "EMBEDDING_BASE_URL"),
        ({"google_paid_tier": False}, "GOOGLE_PAID_TIER"),
        ({"hosting_provider": " "}, "HOSTING_PROVIDER"),
        ({"operator_name": ""}, "OPERATOR_NAME"),
        ({"operator_email": ""}, "OPERATOR_EMAIL"),
        ({"backup_days": None}, "BACKUP_DAYS"),
        ({"server_log_days": None}, "SERVER_LOG_DAYS"),
        ({"encryption_keys": ""}, "ENCRYPTION_KEYS"),
        ({"encryption_keys": f"{A_KEY},{DEVELOPMENT}"}, "ENCRYPTION_KEYS"),
    ],
)
def test_each_development_setting_is_refused_on_its_own(deployment, change, variable):
    for name, value in change.items():
        if value is None:
            del deployment[name]
        else:
            deployment[name] = value

    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()
    assert len(lines) == 2 and variable in lines[1]


@pytest.mark.parametrize(
    ("keys", "said"),
    [
        ("not a key!", "Entry 1 of ENCRYPTION_KEYS is not base64"),
        (f"{A_KEY},{base64.b64encode(b'sixteen bytes ok').decode()}", "Entry 2 of"),
        (f"{A_KEY}, {A_KEY.replace('+', '-').replace('/', '_').rstrip('=')}", "listed before it"),
    ],
    ids=["not base64", "too short", "twice"],
)
def test_keys_that_are_not_keys_stop_the_process_without_printing_them(deployment, keys, said):
    with pytest.raises(SystemExit) as stop:
        load_settings(**{**deployment, "encryption_keys": keys})
    assert said in stop.value.code
    assert A_KEY not in stop.value.code and "not a key" not in stop.value.code


def test_keys_are_read_in_either_base64_alphabet_and_a_trailing_comma_is_none():
    urlsafe = A_KEY.replace("+", "-").replace("/", "_").rstrip("=")
    settings = Settings(_env_file=None, encryption_keys=f" {urlsafe} , {DEVELOPMENT},")
    assert settings.encryption_keys == f"{urlsafe} , {DEVELOPMENT},"


def test_a_spend_cap_without_prices_is_refused_in_production(deployment):
    # A cap measured against costs that are never recorded would hold nothing back.
    deployment["daily_spend_cap"] = "5"
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()
    assert len(lines) == 2 and "DAILY_SPEND_CAP" in lines[1] and "MODEL_PRICES" in lines[1]

    deployment["model_prices"] = "google:gemini-2.5-flash-lite=0.10/0.40"
    assert load_settings(**deployment).daily_spend_cap_micros == 5_000_000
    # A model without a price, a fallback's included, would answer outside the cap.
    deployment["agent_fallback_model"] = "anthropic:claude-haiku-5-5"
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert stop.value.code.splitlines()[1] == (
        "  - DAILY_SPEND_CAP is set, but MODEL_PRICES has no price for AGENT_FALLBACK_MODEL "
        "anthropic:claude-haiku-5-5, so what it answers would count nothing against the cap; "
        "add its price."
    )
    deployment["model_prices"] += ",anthropic:claude-haiku-5-5=1/5"
    assert load_settings(**deployment).production_problems() == []
    # Development is told nothing: it runs with whatever it has.
    del deployment["model_prices"]
    assert load_settings(**{**deployment, "environment": "development"}).production_problems() == []


def test_the_prices_sold_are_read_by_plan_and_a_malformed_entry_is_refused():
    settings = Settings(
        _env_file=None,
        stripe_secret_key="sk_test_x",
        stripe_prices=" pro=price_pro , team=price_team,pro=price_pro_yearly,",
    )
    assert settings.stripe_plans == {
        "pro": ["price_pro", "price_pro_yearly"],
        "team": ["price_team"],
    }
    # Billing off sells nothing, whatever the prices say.
    assert Settings(_env_file=None, stripe_prices="pro=price_pro").stripe_plans == {}
    for entry in ("free=price_free", "enterprise=price_x", "pro", "pro=", "pro=price x"):
        with pytest.raises(ValidationError, match="STRIPE_PRICES lists the Stripe prices"):
            Settings(_env_file=None, stripe_prices=entry)
    with pytest.raises(ValidationError, match="price_a is listed twice"):
        Settings(_env_file=None, stripe_prices="pro=price_a,team=price_a")


def test_the_packs_sold_are_read_in_order_and_a_price_sells_one_thing():
    settings = Settings(
        _env_file=None,
        stripe_secret_key="sk_test_x",
        stripe_prices="pro=price_pro",
        stripe_packs="pages:1000=price_a, questions:500=price_b,pages:5000=price_c",
    )
    assert [tuple(pack) for pack in settings.packs] == [
        ("pages", 1000, "price_a"),
        ("questions", 500, "price_b"),
        ("pages", 5000, "price_c"),
    ]
    for entry in ("seats:5=price_x", "pages=price_x", "pages:0=price_x", "pages:ten=price_x"):
        with pytest.raises(ValidationError, match="STRIPE_PACKS lists the packs sold"):
            Settings(_env_file=None, stripe_packs=entry)
    with pytest.raises(ValidationError, match="pages:10 is listed twice"):
        Settings(_env_file=None, stripe_packs="pages:10=price_a,pages:10=price_b")
    with pytest.raises(ValidationError, match="STRIPE_PRICES and STRIPE_PACKS both list price_a"):
        Settings(_env_file=None, stripe_prices="pro=price_a", stripe_packs="pages:10=price_a")


def test_production_bills_only_with_the_key_the_webhook_and_something_to_sell(deployment):
    billing = {
        "stripe_secret_key": "rk_live_x",
        "stripe_webhook_secret": "whsec_x",
        "stripe_prices": "pro=price_pro,team=price_team",
    }
    assert load_settings(**deployment, **billing).production_problems() == []
    for left_out, said in (
        ("stripe_secret_key", "STRIPE_SECRET_KEY is not, so nothing can be sold"),
        ("stripe_webhook_secret", "what is bought would never reach the app"),
        ("stripe_prices", "STRIPE_PRICES is not set, so there is nothing to sell"),
    ):
        with pytest.raises(SystemExit) as stop:
            load_settings(**deployment, **{**billing, left_out: ""})
        assert said in stop.value.code
    for name, value, said in (
        ("stripe_secret_key", "pk_live_x", "the publishable key, pk_, cannot sell anything"),
        ("stripe_webhook_secret", "we_x", "STRIPE_WEBHOOK_SECRET is not a webhook endpoint's"),
    ):
        with pytest.raises(SystemExit) as stop:
            load_settings(**deployment, **{**billing, name: value})
        assert said in stop.value.code and value not in stop.value.code
    # Development is told nothing: it bills with whatever it has.
    development = {**deployment, "environment": "development", "stripe_secret_key": "sk_test_x"}
    assert load_settings(**development).production_problems() == []


def test_production_mails_one_way_with_keys_of_their_kind(deployment):
    # Resend in place of a relay is a way to send; both is two, and the second would be
    # silently left out.
    del deployment["smtp_url"]
    resend = load_settings(**deployment, resend_api_key="re_" + "x" * 20)
    assert resend.production_problems() == []
    for change, said in (
        (
            {"smtp_url": "smtp://mailer:secret@smtp.example.com:587", "resend_api_key": "re_x"},
            ("SMTP_URL and RESEND_API_KEY are both set, but the mail goes one way"),
        ),
        ({"resend_api_key": "pk_" + "y" * 20}, "RESEND_API_KEY is not a Resend API key"),
        (
            {"resend_api_key": "re_x", "resend_webhook_secret": "sk_" + "z" * 20},
            "RESEND_WEBHOOK_SECRET is not a Resend webhook's signing secret",
        ),
    ):
        with pytest.raises(SystemExit) as stop:
            load_settings(**deployment, **change)
        assert said in stop.value.code
        # Neither is quoted.
        assert "y" * 20 not in stop.value.code and "z" * 20 not in stop.value.code


def test_production_receives_mail_only_through_a_provider_that_hands_it_over(deployment):
    # A domain whose mail nothing hands to the app would give projects addresses that take
    # nothing; Resend's key and its webhook's secret are its way in.
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment, inbound_domain="in.example.com")
    assert "INBOUND_DOMAIN is set, but no provider hands its mail to the app" in stop.value.code
    del deployment["smtp_url"]
    resend = load_settings(
        **deployment,
        inbound_domain="@In.Example.com",
        resend_api_key="re_" + "x" * 20,
        resend_webhook_secret="whsec_" + "y" * 20,
    )
    assert resend.production_problems() == []
    assert (resend.inbound_domain, resend.receives_through) == ("in.example.com", "resend")
    # A mistake is pydantic's, which names the setting, as rare ones are.
    with pytest.raises(ValidationError, match="INBOUND_DOMAIN is the domain of the projects'"):
        load_settings(**deployment, inbound_domain="not a domain")


def test_production_reads_connected_folders_from_the_services_themselves(deployment):
    # The stand-in's address would have members' folders read from it rather than from Google,
    # and a client without its secret could connect nothing.
    drive = load_settings(
        **deployment,
        google_drive_client_id=" drive.apps.googleusercontent.com ",
        google_drive_client_secret="GOCSPX-" + "x" * 20,
    )
    assert drive.production_problems() == []
    assert drive.google_drive_client_id == "drive.apps.googleusercontent.com"
    for change, said in (
        ({"connector_base_url": "http://127.0.0.1:18001"}, "CONNECTOR_BASE_URL is set"),
        (
            {"google_drive_client_id": "drive.apps.googleusercontent.com"},
            "GOOGLE_DRIVE_CLIENT_ID is set without GOOGLE_DRIVE_CLIENT_SECRET",
        ),
        ({"dropbox_app_key": "dropbox-key"}, "DROPBOX_APP_KEY is set without DROPBOX_APP_SECRET"),
    ):
        with pytest.raises(SystemExit) as stop:
            load_settings(**deployment, **change)
        assert said in stop.value.code


def test_production_runs_without_a_scanner_only_when_told_to(deployment):
    # Unscanned uploads reach every member of a project, so leaving the scanner out is a
    # choice to state, not a default to fall into; development scans nothing by default.
    deployment["scanner_url"] = "off"
    assert load_settings(**deployment).production_problems() == []
    deployment["scanner_url"] = ""
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert "set it to off to run without scanning on purpose" in stop.value.code
    assert load_settings(**{**deployment, "environment": "development"}).production_problems() == []


def test_production_names_its_embeddings_endpoint(deployment):
    # The default is Ollama on 127.0.0.1, which in the image is the container itself: a
    # deployment that left it would index and search without vectors and never say why.
    del deployment["embedding_base_url"]
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    (line,) = stop.value.code.splitlines()[1:]
    assert line.startswith("  - EMBEDDING_BASE_URL is not set")
    assert "such as http://embeddings:8080/v1 for the compose file's server" in line
    deployment["embedding_base_url"] = " "
    with pytest.raises(SystemExit, match="EMBEDDING_BASE_URL is not set"):
        load_settings(**deployment)
    # Named, even as the default's own address, it is the operator's choice: an Ollama on the
    # machine that runs the app from a checkout.
    deployment["embedding_base_url"] = "http://127.0.0.1:11434/v1"
    assert load_settings(**deployment).production_problems() == []
    del deployment["embedding_base_url"]
    assert load_settings(**{**deployment, "environment": "development"}).production_problems() == []


def test_production_takes_gemini_s_developer_api_only_on_the_paid_tier(deployment):
    # The free tier lets Google use what it is sent to improve its products, and a free key
    # looks like a paid one, so the operator says which it is; Vertex AI needs no such word.
    deployment["google_paid_tier"] = False
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    (line,) = stop.value.code.splitlines()[1:]
    assert "INDEX_MODEL and AGENT_MODEL use the Gemini Developer API" in line
    assert "set GOOGLE_PAID_TIER=true, or use Vertex AI as google-cloud:<model>" in line
    # A fallback is sent what its primary is.
    deployment["agent_fallback_model"] = "google:gemini-3.5-flash-lite"
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert "INDEX_MODEL, AGENT_MODEL and AGENT_FALLBACK_MODEL use the Gemini" in stop.value.code
    del deployment["agent_fallback_model"]

    deployment["agent_model"] = "anthropic:claude-opus-5-5"
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert "INDEX_MODEL uses the Gemini Developer API" in stop.value.code
    deployment["index_model"] = "google-cloud:gemini-2.5-flash-lite"
    assert load_settings(**deployment).production_problems() == []
    deployment["index_model"] = "openrouter:google/gemini-2.5-flash-lite"
    assert load_settings(**deployment).production_problems() == []
    # A switched-off provider is still the one the deployment would use once it is back.
    deployment.update(index_model="google:gemini-2.5-flash-lite", disabled_providers="google")
    with pytest.raises(SystemExit):
        load_settings(**deployment)


def test_production_runs_only_on_the_models_it_names(deployment):
    # The default moves with the code, and its provider retires it when it likes: Google kept
    # Gemini 2.5 from new accounts in September 2026.
    del deployment["agent_model"]
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    (line,) = stop.value.code.splitlines()[1:]
    assert line == (
        "  - AGENT_MODEL is not set, so the deployment would run on the default, "
        f"{DEFAULT_MODEL}, which a release of the app may change and its provider may stop "
        "serving on a schedule of its own; name the model the deployment was tried with, as "
        "provider:model, and try it with `python -m app.maintenance models`."
    )
    deployment["index_model"] = " "
    with pytest.raises(SystemExit, match="INDEX_MODEL and AGENT_MODEL are not set"):
        load_settings(**deployment)
    # Named, even as the default itself, it is the operator's choice.
    deployment.update(index_model=DEFAULT_MODEL, agent_model=DEFAULT_MODEL)
    assert load_settings(**deployment).production_problems() == []
    del deployment["agent_model"]
    assert load_settings(**{**deployment, "environment": "development"}).production_problems() == []


@pytest.mark.parametrize(
    "alias",
    [
        "google:gemini-flash-latest",
        "openai:chatgpt-4o-latest",
        "anthropic:claude-3-7-sonnet-latest",
        "ollama:qwen3:latest",
        "openrouter:openrouter/auto",
    ],
)
def test_production_refuses_a_model_its_provider_moves(deployment, alias):
    # Behind an alias the provider puts a newer model when it likes, with other prices, other
    # places and other answers, and no release of the deployment.
    deployment["agent_fallback_model"] = alias
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    (line,) = stop.value.code.splitlines()[1:]
    assert line.startswith(f"  - AGENT_FALLBACK_MODEL is {alias}, an alias its provider moves")
    assert line.endswith(
        "name the version the alias points to now, from the provider's list of models."
    )
    assert load_settings(**{**deployment, "environment": "development"}).agent_fallback_model


def test_a_fallback_is_another_model():
    with pytest.raises(ValidationError, match="INDEX_FALLBACK_MODEL names INDEX_MODEL's own model"):
        Settings(
            _env_file=None,
            index_model="anthropic:claude-haiku-5-5",
            index_fallback_model=" anthropic:claude-haiku-5-5 ",
        )
    settings = Settings(_env_file=None, agent_fallback_model=" anthropic:claude-haiku-5-5 ")
    assert [(model.variable, model.name) for model in settings.models_for(AGENT)] == [
        ("AGENT_MODEL", "google:gemini-2.5-flash-lite"),
        ("AGENT_FALLBACK_MODEL", "anthropic:claude-haiku-5-5"),
    ]
    # Without fallbacks the primaries are all there is, blank or not.
    blank = Settings(_env_file=None, index_model="")
    assert [model.variable for model in blank.models] == ["INDEX_MODEL", "AGENT_MODEL"]


def test_production_sends_nothing_off_its_own_network_unencrypted(
    deployment, monkeypatch, tmp_path
):
    # A managed database, a scanner on another host, a hosted embeddings endpoint, a copy of
    # the breached passwords' list and a queue of the mail's bounces elsewhere are each reached
    # across networks nobody in the deployment controls.
    deployment.update(
        database_url="postgresql+psycopg://app:secret@db.example.com:5432/emudoc",
        scanner_url="clamd://clamav.example.com:3310",
        embedding_base_url="http://embeddings.example.com/v1",
        pwned_passwords_url="http://pwned.example.com",
        mail_events_queue="http://sqs.example.com/123456789012/mail-events",
        inbound_domain="in.example.com",
        inbound_mail_queue="http://sqs.example.com/123456789012/inbound",
    )
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()[1:]
    assert len(lines) == 6
    assert "DATABASE_URL connects to db.example.com without sslmode=verify-full" in lines[0]
    assert "SCANNER_URL streams every upload to the ClamAV daemon at clamav.example.com" in lines[1]
    assert (
        "EMBEDDING_BASE_URL sends every passage as it is indexed and every search to " in lines[2]
    )
    assert (
        "PWNED_PASSWORDS_URL sends the start of the hash of every new password to "
        "pwned.example.com over plain HTTP" in lines[3]
    )
    assert (
        "MAIL_EVENTS_QUEUE sends the worker's requests for the addresses whose mail bounced to "
        "sqs.example.com over plain HTTP" in lines[4]
    )
    assert (
        "INBOUND_MAIL_QUEUE sends the worker's requests for the mail sent to projects' addresses "
        "to sqs.example.com over plain HTTP" in lines[5]
    )
    assert "secret" not in stop.value.code

    # Verified, over HTTPS, or inside the network, the same pieces are fit.
    bundle = tmp_path / "ca-certificates.crt"
    bundle.write_text("")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    deployment.update(
        database_url="postgresql+psycopg://app:secret@db.example.com:5432/emudoc"
        "?sslmode=verify-full&sslrootcert=system",
        scanner_url="clamd://clamav:3310",
        embedding_base_url="https://embeddings.example.com/v1",
        pwned_passwords_url="http://pwned:8080",
        mail_events_queue="https://sqs.eu-central-1.amazonaws.com/123456789012/mail-events",
        inbound_mail_queue="https://sqs.eu-central-1.amazonaws.com/123456789012/inbound",
    )
    assert load_settings(**deployment).production_problems() == []


TRUSTING_THE_SYSTEM = (
    "postgresql+psycopg://app:secret@db.example.com:5432/emudoc"
    "?sslmode=verify-full&sslrootcert=system"
)


def test_production_trusts_the_system_s_authorities_only_where_it_can_find_them(
    deployment, monkeypatch, tmp_path
):
    # psycopg's binary package brings an OpenSSL that looks for the authorities in a directory
    # of the machine it was built on: without SSL_CERT_FILE every certificate would fail, and
    # the operator would read "certificate verify failed" from a server whose certificate is
    # fine. Refused before anything connects, with the variable to set.
    monkeypatch.setattr("app.config.BUNDLED_LIBPQ", True)
    deployment["database_url"] = TRUSTING_THE_SYSTEM
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    (line,) = stop.value.code.splitlines()[1:]
    assert line.startswith("  - DATABASE_URL trusts the system's certificate authorities")
    assert "every server's certificate would fail to verify; set SSL_CERT_FILE to" in line

    # A variable that names nothing is no better, and the sentence says which.
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.crt"))
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert f"SSL_CERT_FILE names {tmp_path / 'missing.crt'}, which is not there" in stop.value.code

    # A bundle, or a directory of authorities, is what OpenSSL reads instead.
    bundle = tmp_path / "ca-certificates.crt"
    bundle.write_text("")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert load_settings(**deployment).production_problems() == []
    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    assert load_settings(**deployment).production_problems() == []
    monkeypatch.delenv("SSL_CERT_DIR")

    # The provider's own authority by its path needs neither, and neither does a libpq of the
    # system's, which knows where the system keeps them.
    deployment["database_url"] = TRUSTING_THE_SYSTEM.replace("system", "/etc/emudoc/rds.pem")
    assert load_settings(**deployment).production_problems() == []
    deployment["database_url"] = TRUSTING_THE_SYSTEM
    monkeypatch.setattr("app.config.BUNDLED_LIBPQ", False)
    assert load_settings(**deployment).production_problems() == []

    # libpq's own variable stands for the parameter when the URL leaves it out.
    monkeypatch.setattr("app.config.BUNDLED_LIBPQ", True)
    monkeypatch.setenv("PGSSLROOTCERT", "system")
    deployment["database_url"] = TRUSTING_THE_SYSTEM.replace("&sslrootcert=system", "")
    with pytest.raises(SystemExit, match="DATABASE_URL trusts the system's"):
        load_settings(**deployment)


def test_behind_a_transaction_pooler_the_worker_listens_elsewhere_or_polls(deployment):
    # A LISTEN made through a pooler in transaction mode lands on whichever server connection
    # the pooler hands out next, so the listener needs an address of its own.
    settings = load_settings(**deployment)
    assert settings.listen_url == A_DATABASE
    settings = load_settings(**deployment, database_pooler=" transaction ")
    assert settings.database_pooler == "transaction"
    assert settings.listen_url is None
    direct = "postgresql+psycopg://app:secret@db-direct.internal:5432/emudoc"
    settings = load_settings(
        **deployment, database_pooler="transaction", database_listen_url=direct
    )
    assert settings.listen_url == direct
    with pytest.raises(ValueError, match="database_pooler"):
        load_settings(**deployment, database_pooler="statement")


def test_the_listener_s_address_is_held_to_the_database_s_rules(deployment):
    # It carries no documents, but it signs in as the app's role: a copied development
    # password or a connection off the network in the clear is refused for it as well.
    deployment.update(
        database_pooler="transaction",
        database_listen_url="postgresql+psycopg://emudoc:emudoc@db.example.com:5432/emudoc",
    )
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()[1:]
    assert len(lines) == 2
    assert lines[0].startswith("  - DATABASE_LISTEN_URL carries the development database's")
    assert lines[1].startswith("  - DATABASE_LISTEN_URL connects to db.example.com without")


def test_the_api_runs_the_worker_itself_in_development_only(deployment, monkeypatch):
    # Unset, the choice follows the environment: one process in development, and in production
    # a `python -m app.worker` of its own. Either can be said outright; a blank line is unset.
    assert load_settings(**deployment).run_worker is False
    assert load_settings(**deployment, run_worker=True).run_worker is True
    deployment["environment"] = "development"
    assert load_settings(**deployment).run_worker is True
    assert load_settings(**deployment, run_worker=False).run_worker is False
    monkeypatch.setenv("RUN_WORKER", "")
    assert load_settings(**deployment).run_worker is True


def test_a_deployment_logs_json_and_development_text(deployment, monkeypatch):
    # Unset, the format follows the environment: lines for a collector in production, for a
    # person at a terminal in development. Either can be said outright; a blank line is unset.
    assert load_settings(**deployment).log_format == "json"
    assert load_settings(**deployment, log_format="text").log_format == "text"
    deployment["environment"] = "development"
    assert load_settings(**deployment).log_format == "text"
    assert load_settings(**deployment, log_format="json").log_format == "json"
    monkeypatch.setenv("LOG_FORMAT", "")
    assert load_settings(**deployment).log_format == "text"
    monkeypatch.setenv("LOG_FORMAT", "yaml")
    with pytest.raises(ValidationError, match="log_format"):
        load_settings(**deployment)


QUEUE = "https://sqs.eu-central-1.amazonaws.com/123456789012/emudoc-production-mail-events"


def test_a_process_that_runs_the_worker_needs_a_pool_for_its_threads(deployment):
    # Each indexing, answering and mail thread holds a connection while it works, and the
    # heartbeat, housekeeper, alerter and the metrics' reading take one now and then, and with
    # SES's queue its reader; a pool that cannot hold them all would fail jobs for a reason the
    # operator set, so the settings are refused with the arithmetic.
    deployment.update(run_worker=True, worker_concurrency=19, answer_concurrency=8)
    with pytest.raises(
        ValueError, match="WORKER_CONCURRENCY is 19 and ANSWER_CONCURRENCY is 8"
    ) as error:
        load_settings(**deployment)
    assert "need 32 database connections" in str(error.value)
    assert "DB_POOL_SIZE + DB_MAX_OVERFLOW allows 31" in str(error.value)
    assert load_settings(**deployment, db_max_overflow=22).worker_concurrency == 19
    with pytest.raises(ValueError, match="need 33 database connections"):
        load_settings(**deployment, db_max_overflow=22, mail_events_queue=QUEUE)
    # The defaults hold the default threads, both of SES's queues' readers too.
    del deployment["worker_concurrency"], deployment["answer_concurrency"]
    settings = load_settings(
        **deployment,
        mail_events_queue=QUEUE,
        inbound_domain="in.example.com",
        inbound_mail_queue=QUEUE.replace("mail-events", "inbound"),
    )
    assert (settings.worker_concurrency, settings.answer_concurrency) == (16, 8)
    assert (settings.db_pool_size, settings.db_max_overflow) == (10, 21)
    assert settings.receives_through == "ses"
    # An API replica that runs no worker has no indexing threads to hold connections.
    deployment.update(run_worker=False, worker_concurrency=50)
    assert load_settings(**deployment).run_worker is False


def test_development_runs_with_everything_production_refuses(deployment):
    deployment.update(
        environment="development",
        database_url="postgresql+psycopg://emudoc:emudoc@db.example.com/emudoc",
        scanner_url="clamd://clamav.example.com:3310",
        embedding_base_url="http://embeddings.example.com/v1",
        local_account=True,
        app_url="",
        smtp_url="",
        google_paid_tier=False,
        hosting_provider="",
        operator_name="",
        operator_email="",
        encryption_keys="",
    )
    del deployment["storage_url"]
    del deployment["backup_days"]
    del deployment["server_log_days"]
    assert load_settings(**deployment).production_problems() == []


def test_a_production_command_with_development_settings_prints_the_list_and_no_traceback():
    # The suite's environment is development's: the test database with the compose password,
    # LOCAL_ACCOUNT on, and here no app address or mail relay either.
    env = dict(os.environ, ENVIRONMENT="production", APP_URL="", SMTP_URL="")
    run = subprocess.run(
        [sys.executable, "-m", "app.maintenance", "rebuild-embeddings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 1
    assert run.stderr.startswith("ENVIRONMENT is production, but")
    for variable in ("DATABASE_URL", "LOCAL_ACCOUNT", "APP_URL", "SMTP_URL"):
        assert variable in run.stderr
    assert "Traceback" not in run.stderr


@pytest.fixture
def production(monkeypatch):
    """The running suite's settings, switched to production for the app's own start-up checks.
    The settings object is cached and shared, so the attribute is patched, never the object."""
    monkeypatch.setattr(get_settings(), "environment", "production")


def test_a_production_start_needs_the_model(production, services, monkeypatch):
    # Development degrades to parsing and search without a key; a deployment stops and says so.
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="production does not start without it") as error:
        with TestClient(create_app()):
            pass
    assert "Set GOOGLE_API_KEY in the environment" in str(error.value)

    # With a usable model the same start goes through.
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/health").json()["status"] == "ok"


def test_a_production_start_runs_with_a_switched_off_model(production, monkeypatch, caplog):
    # The kill switch stops the spending, not the app: the process starts and logs what is
    # off, and the status route tells the members that the model cannot be used for now.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(get_settings(), "disabled_providers", "google")
    with caplog.at_level("WARNING", logger="app.startup"):
        with TestClient(create_app()) as client:
            status = client.get("/api/status").json()["llm"]
    assert status["available"] is False and status["error"] == MODELS_UNAVAILABLE
    assert "google:gemini-2.5-flash-lite is switched off: DISABLED_PROVIDERS" in caplog.text
    assert "runs without the model" in caplog.text


def test_a_production_start_needs_the_fallback_it_names(production, monkeypatch):
    # A fallback that cannot be set up would be found out at the first outage, not before.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(get_settings(), "agent_fallback_model", "anthropic:claude-haiku-5-5")
    with pytest.raises(RuntimeError, match="production does not start without it") as error:
        with TestClient(create_app()):
            pass
    assert "Set ANTHROPIC_API_KEY in the environment to use anthropic:claude-haiku-5-5" in str(
        error.value
    )


def test_a_production_start_with_a_switched_off_model_runs_on_its_fallback(
    production, monkeypatch, caplog
):
    # The switch and the fallback together keep the app answering while the bill is stopped.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(get_settings(), "disabled_providers", "google")
    monkeypatch.setattr(get_settings(), "agent_fallback_model", "anthropic:claude-haiku-5-5")
    monkeypatch.setattr(get_settings(), "index_fallback_model", "anthropic:claude-haiku-5-5")
    with caplog.at_level("WARNING", logger="app.startup"):
        with TestClient(create_app()) as client:
            status = client.get("/api/status").json()["llm"]
    assert status["available"] is True and status["error"] is None
    assert "runs without the model" not in caplog.text


def test_the_interactive_api_docs_are_served_in_development_only(client, services, production):
    # `client` was built before the switch to production, so it is the development app.
    assert client.get("/openapi.json").json()["info"]["title"] == "Emudoc"
    assert client.get("/docs").status_code == 200
    assert API_DOCS <= paths_of(client.app)

    app = create_app(services=services)
    assert not API_DOCS & paths_of(app)


# The guide to every setting: deploying.md's "Every setting" and .env.example name each one the
# app reads, so that a deployment's operator finds every variable in one place.

GUIDE = ROOT_DIR / "deploying.md"
# What the app's libraries read rather than its own code: the providers' keys (Pydantic AI's),
# the bucket's credentials (boto3's), the trust store (OpenSSL's) and the proxy's address
# (uvicorn's).
READ_BY_THE_LIBRARIES = {
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_ENDPOINT_URL",
    "AWS_CONFIG_FILE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "FORWARDED_ALLOW_IPS",
}
# How the app's own code reads a variable by its name.
READ_BY_NAME = re.compile(r"""(?:environ\.get\(|environ\[|getenv\()\s*["']([A-Z][A-Z0-9_]*)["']""")


def listed() -> dict[str, tuple[str, str]]:
    """The variables the guide lists, each with its row's default and its production cell."""
    section = GUIDE.read_text().split("\n## Every setting\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        names, default, production, _what = (cell.strip() for cell in line.strip("|").split("|"))
        for name in re.findall(r"`([A-Z][A-Z0-9_]*)`", names):
            rows[name] = (default, production)
    return rows


def shown(default: object) -> str | None:
    """A default as the guide writes it, for the values a person would type."""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, int | Decimal):
        return str(default)
    return default if isinstance(default, str) and default else None


def test_the_example_env_file_names_every_setting():
    named = set(
        re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", (ROOT_DIR / ".env.example").read_text(), re.M)
    )
    assert {name.upper() for name in Settings.model_fields} <= named


def test_the_guide_lists_every_setting_with_its_default():
    rows = listed()
    for name, field in Settings.model_fields.items():
        assert name.upper() in rows, name
        # The default directory is the checkout's own, wherever it is.
        if name != "storage_url" and (value := shown(field.default)) is not None:
            assert f"`{value}`" in rows[name.upper()][0], name


def test_the_guide_lists_what_the_app_reads_besides_its_settings():
    source = "\n".join(path.read_text() for path in (BACKEND_DIR / "app").rglob("*.py"))
    read = set(READ_BY_NAME.findall(source)) | set(MODEL_BASE_URLS.values())
    assert "GOOGLE_APPLICATION_CREDENTIALS" in read and "OLLAMA_BASE_URL" in read
    assert read | READ_BY_THE_LIBRARIES <= listed().keys()


def test_the_guide_says_what_production_requires(deployment):
    complete = {
        **deployment,
        "agent_model": "anthropic:claude-sonnet-5-5",
        "index_model": "anthropic:claude-haiku-5-5",
    }
    del complete["google_paid_tier"]
    assert Settings(**complete).production_problems({}) == []
    required = set()
    for name in complete.keys() & Settings.model_fields.keys():
        rest = {key: value for key, value in complete.items() if key != name}
        try:
            refused = Settings(**rest).production_problems({})
        except ValidationError:
            refused = True
        if refused:
            required.add(name.upper())
    assert "DATABASE_URL" in required and "ENVIRONMENT" not in required
    marked = {
        name for name, (_, production) in listed().items() if production.startswith("required")
    }
    assert marked == required
