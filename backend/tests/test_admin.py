"""The Administration page (app/api/admin.py): the workspace's owners and admins alone read its
jobs, the services, the model providers and what the models cost, which the maintenance
commands print, and queue failed documents again and switch a provider off.

The suite's `client` acts as the local account, the owner of the local organisation (`account`),
so it is an admin of the workspace the documents are made in; `act_as` gives the other roles."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app.api.admin import ADMINS_ONLY
from app.config import get_settings
from app.indexing.enrich import DocumentAnalysis
from app.jobs import enqueue
from app.maintenance import main
from app.models import Document, Job, Project, ProviderSwitch, WatchAlert
from app.switches import SWITCH_SECONDS, Switches, switched_off
from app.usage import ModelUsage
from tests.test_audit import rows
from tests.test_deadletters import fail, job_of
from tests.test_jobs import hold
from tests.test_llm import ANALYSIS, BACKUP, SCRIPTED, Script, fallback_llm, text_reply
from tests.test_usage import charge

ROUTES = (
    "/api/organization/costs",
    "/api/organization/jobs",
    "/api/organization/services",
)
# What the page changes, each with a body it takes.
ACTIONS = (
    ("POST", "/api/organization/jobs/requeue", {"job_ids": []}),
    ("PUT", "/api/organization/providers/google", {"off": False}),
)
PRICED = "test:priced"


def test_the_administration_is_the_owners_and_admins_alone(client, account, make_account, act_as):
    for path in ROUTES:
        assert client.get(path).status_code == 200, path
    for method, path, body in ACTIONS:
        assert client.request(method, path, json=body).status_code == 200, path

    for role in ("member", "viewer"):
        act_as(make_account(f"{role}@example.com", account.organization, role=role))
        for path in ROUTES:
            refused = client.get(path)
            assert (refused.status_code, refused.json()["detail"]) == (403, ADMINS_ONLY), path
        for method, path, body in ACTIONS:
            refused = client.request(method, path, json=body)
            assert (refused.status_code, refused.json()["detail"]) == (403, ADMINS_ONLY), path

    act_as(make_account("admin@example.com", account.organization, role="admin"))
    for path in ROUTES:
        assert client.get(path).status_code == 200, path


def test_the_operators_area_and_its_command_are_gone(client, capsys):
    # The routes, and the networks and the table that let an operator in, went with S.3.
    for path in ("/api/operator/me", "/api/operator/jobs", "/api/operator/services"):
        assert client.get(path).status_code == 404, path
    with pytest.raises(SystemExit):
        main(["operators"])
    capsys.readouterr()


def test_the_costs_are_the_workspaces(client, session, account, make_account, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_prices", f"{PRICED}=1/2")
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    org_id = account.organization.id
    now = datetime.now(UTC)
    charge(session, org_id, "enrich", {"calls": 4, "input_tokens": 1_000_000}, now, model=PRICED)
    charge(session, org_id, "enrich", {"calls": 1, "input_tokens": 10}, now, model="test:free")
    charge(
        session,
        org_id,
        "answer",
        {"calls": 2, "input_tokens": 3_000_000},
        now - timedelta(days=1),
        model=PRICED,
    )
    # Another workspace's, as development's database may hold, is not this one's.
    globex = make_account("ann@globex.example").organization
    charge(session, globex.id, "answer", {"calls": 9, "input_tokens": 9_000_000}, now, model=PRICED)
    session.commit()

    costs = client.get("/api/organization/costs", params={"days": 7}).json()
    assert costs["since"] == str((now - timedelta(days=6)).date())
    assert costs["until"] == str(now.date()) and costs["currency"] == get_settings().price_currency
    cost = costs["cost"]
    assert (cost["cost_micros"], cost["calls"], cost["limit_micros"]) == (4_000_000, 7, 2_000_000)
    # Yesterday's 3.00 reached the cap; today's 1.00 did not.
    assert (cost["top_day_micros"], cost["days_at_limit"]) == (3_000_000, 1)
    models = {(row["kind"], row["model"]): row for row in costs["models"]}
    assert set(models) == {("answer", PRICED), ("enrich", PRICED), ("enrich", "test:free")}
    assert models["answer", PRICED]["cost_micros"] == 3_000_000
    assert models["enrich", "test:free"]["unpriced_tokens"] == 10

    # Without a cap, nothing is held to one.
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("0"))
    cost = client.get("/api/organization/costs").json()["cost"]
    assert (cost["limit_micros"], cost["days_at_limit"]) == (None, None)
    assert client.get("/api/organization/costs", params={"days": 0}).status_code == 422


def test_the_jobs_show_the_workspaces_queue_the_running_and_the_dead_letters(
    client, session, account, make_account
):
    org_id = account.organization.id
    running, failed, older, due = (
        enqueue(session, "index_document", org_id=org_id, payload={"document_id": 999})
        for _ in range(4)
    )
    # Mail about an account belongs to no organisation, and is the install's to see.
    mail = enqueue(
        session, "send_email", org_id=None, payload={"email": "k", "subject": "Hello", "try": 1}
    )
    # Another workspace's job is not this one's.
    globex = make_account("ann@globex.example").organization
    theirs = enqueue(session, "index_document", org_id=globex.id, payload={"document_id": 998})
    session.commit()
    hold(session, running.id, "running", "2 minutes", lease="1 minute")
    hold(session, failed.id, "failed", "1 hour")
    hold(session, older.id, "failed", "2 hours")
    session.execute(
        update(Job)
        .where(Job.id == older.id)
        .values(finished_at=datetime.now(UTC) - timedelta(hours=1))
    )
    hold(session, due.id, "queued", "10 minutes")
    hold(session, mail.id, "queued", "-1 minute")
    hold(session, theirs.id, "failed", "1 hour")

    shown = client.get("/api/organization/jobs").json()
    assert shown["queue"] == [
        {
            "kind": "index_document",
            "due": 1,
            "waiting": 0,
            "running": 1,
            "failed": 2,
            "oldest_due_seconds": pytest.approx(600, abs=30),
        },
        {
            "kind": "send_email",
            "due": 0,
            "waiting": 1,
            "running": 0,
            "failed": 0,
            "oldest_due_seconds": None,
        },
    ]
    assert [job["id"] for job in shown["running"]] == [running.id]
    assert shown["running"][0]["worker_id"] == "host:41"
    assert [job["id"] for job in shown["failed"]] == [failed.id, older.id]
    assert shown["failed"][0]["last_error"] == "RuntimeError: the parser broke"
    assert shown["failed"][0]["about"].endswith("document 999 (deleted)")
    assert not shown["failed"][0]["reported"]
    assert shown["failed_total"] == 2 and shown["history_days"] == 30


def test_the_services_show_the_models_and_what_the_watch_sees(
    client, session, services, monkeypatch
):
    monkeypatch.setattr(get_settings(), "disabled_providers", "google")
    services.embedder.fail = True
    raised = datetime.now(UTC) - timedelta(hours=2)
    session.add(WatchAlert(key="embeddings", title="old", message="old", raised_at=raised))
    session.commit()

    shown = client.get("/api/organization/services").json()
    models = {model["variable"]: model for model in shown["models"]}
    settings = get_settings()
    assert models["AGENT_MODEL"]["name"] == settings.agent_model
    assert models["AGENT_MODEL"]["disabled"] == settings.agent_model.startswith("google:")
    assert shown["embedding_address"] == services.embedder.address
    assert shown["scanner"] and shown["storage_origin"] is None
    (concern,) = (concern for concern in shown["concerns"] if concern["key"] == "embeddings")
    assert services.embedder.UNREACHABLE in concern["message"]
    assert datetime.fromisoformat(concern["raised_at"]) == raised
    assert shown["unchecked"] == []
    assert shown["database"]["vector"] and shown["database"]["iterative_scans"]


# What the page changes


def test_an_admin_runs_failed_documents_again(
    client, session, account, make_account, make_document, sample_pdf, sample_docx
):
    first = make_document(sample_pdf)
    second = make_document(sample_docx, project=session.get(Project, first.project_id))
    for document in (first, second):
        document.status = "failed"
        document.stages = {
            "scan": {"status": "done", "detail": "clean"},
            "parse": {"status": "failed", "detail": "The parser broke."},
        }
    session.commit()
    first_job, second_job = job_of(session, first).id, job_of(session, second).id
    mail = enqueue(
        session, "send_email", org_id=None, payload={"email": "k", "subject": "Hello", "try": 1}
    )
    globex = make_account("ann@globex.example").organization
    theirs = enqueue(session, "index_document", org_id=globex.id, payload={"document_id": 998})
    session.commit()
    for job_id in (first_job, second_job, mail.id, theirs.id):
        fail(session, job_id, "RuntimeError: boom")
    requeue = "/api/organization/jobs/requeue"

    one = client.post(requeue, json={"job_ids": [first_job]}).json()
    slug = account.organization.slug
    assert one == {
        "queued": 1,
        "lines": [
            f"Queued {slug} / Test project / {sample_pdf.name} again from stage parse "
            f"(job {first_job})."
        ],
    }
    session.expire_all()
    assert session.get(Document, first.id).status == "queued"
    assert session.get(Job, first_job) is None
    assert job_of(session, first).status == "queued"
    failed = [job["id"] for job in client.get("/api/organization/jobs").json()["failed"]]
    assert sorted(failed) == sorted([second_job, mail.id])

    # A job that is not a failed one is nothing to run again, nor another workspace's.
    nothing = {"queued": 0, "lines": []}
    assert client.post(requeue, json={"job_ids": [job_of(session, first).id]}).json() == nothing
    assert client.post(requeue, json={"job_ids": [theirs.id]}).json() == nothing

    every = client.post(requeue, json={"job_ids": None}).json()
    assert every["queued"] == 1
    assert (
        f"Left job {mail.id} (send_email) alone: only index jobs are run again." in (every["lines"])
    )
    assert session.get(Job, theirs.id).status == "failed"
    # Each document queued again is a row of the audit log, the admin's who asked.
    requeued = [row for row in rows(session) if row.action == "document_requeued"]
    assert [(row.user_id, row.detail) for row in requeued] == [
        (account.user.id, {"filename": sample_pdf.name, "stage": "parse"}),
        (account.user.id, {"filename": sample_docx.name, "stage": "parse"}),
    ]
    assert client.post(requeue, json={"job_ids": "all"}).status_code == 422


@pytest.fixture
def scripted(monkeypatch):
    """The deployment's models of two providers of their own, SCRIPTED's and its fallback
    BACKUP's, which the page may switch."""
    settings = get_settings()
    for name, model in (
        ("index_model", SCRIPTED),
        ("agent_model", SCRIPTED),
        ("index_fallback_model", BACKUP),
        ("agent_fallback_model", BACKUP),
    ):
        monkeypatch.setattr(settings, name, model)
    return fallback_llm(
        FunctionModel(Script()), FunctionModel(Script(text_reply(ANALYSIS))), switched_off
    )


def test_an_admin_switches_a_provider_off_and_on_again(
    client, session, account, scripted, monkeypatch
):
    llm = scripted
    providers = "/api/organization/providers"
    admin = account.user.name or account.user.email
    assert llm.problem() is None

    off = client.put(f"{providers}/scripted", json={"off": True})
    assert off.status_code == 200, off.text
    listed = {provider["name"]: provider for provider in off.json()}
    assert listed["scripted"]["variables"] == ["INDEX_MODEL", "AGENT_MODEL"]
    assert (listed["scripted"]["off"], listed["scripted"]["switched_by"]) == (True, admin)
    assert not listed["scripted"]["by_setting"] and listed["scripted"]["switched_at"]
    assert not listed["backup"]["off"] and listed["backup"]["switched_at"] is None
    # This process at once, the others within SWITCH_SECONDS; the fallback answers meanwhile.
    assert switched_off() == {"scripted"}
    assert llm.problem() is None
    usage = ModelUsage()
    llm.structured("the document", DocumentAnalysis, usage)
    assert list(usage.models) == [BACKUP]
    # Asked twice, it is switched off once, and written once in the audit log.
    assert client.put(f"{providers}/scripted", json={"off": True}).status_code == 200
    assert session.scalar(select(ProviderSwitch.user_id)) == account.user.id

    client.put(f"{providers}/backup", json={"off": True})
    assert llm.problem() == (
        f"{SCRIPTED} is switched off: an admin switched its provider, scripted, off on the "
        "Administration page. Switch it on there to use the model again."
    )
    shown = client.get("/api/organization/services").json()
    assert {model["variable"]: model["disabled"] for model in shown["models"]} == {
        "INDEX_MODEL": True,
        "AGENT_MODEL": True,
        "INDEX_FALLBACK_MODEL": True,
        "AGENT_FALLBACK_MODEL": True,
    }
    assert [provider["name"] for provider in shown["providers"]] == ["scripted", "backup"]

    client.put(f"{providers}/scripted", json={"off": False})
    assert switched_off() == {"backup"} and llm.problem() is None
    switched = [row for row in rows(session) if row.action == "provider_switched"]
    assert [(row.user_id, row.detail) for row in switched] == [
        (account.user.id, {"provider": "scripted", "off": True}),
        (account.user.id, {"provider": "backup", "off": True}),
        (account.user.id, {"provider": "scripted", "off": False}),
    ]

    # Off only what the settings name a model of; on again only what the page switched off.
    unknown = client.put(f"{providers}/openai", json={"off": True})
    assert (
        unknown.status_code == 400
        and "No model the settings name is of openai" in (unknown.json()["detail"])
    )
    assert client.put(f"{providers}/Not one", json={"off": True}).status_code == 422
    monkeypatch.setattr(get_settings(), "disabled_providers", "backup")
    held = client.put(f"{providers}/backup", json={"off": False})
    assert (
        held.status_code == 409
        and "DISABLED_PROVIDERS switches backup off" in (held.json()["detail"])
    )
    shown = client.get("/api/organization/services").json()["providers"]
    assert [(p["name"], p["off"], p["by_setting"]) for p in shown] == [
        ("scripted", False, False),
        ("backup", True, True),
    ]

    # A provider switched off whose models the settings no longer name is listed to switch on.
    session.add(ProviderSwitch(provider="gone"))
    session.commit()
    shown = client.get("/api/organization/services").json()["providers"]
    assert shown[-1] == {
        "name": "gone",
        "variables": [],
        "off": True,
        "by_setting": False,
        "switched_at": shown[-1]["switched_at"],
        "switched_by": None,
    }
    back = client.put(f"{providers}/gone", json={"off": False}).json()
    assert [provider["name"] for provider in back] == ["scripted", "backup"]


def test_a_process_reads_the_switches_once_in_a_while(session, monkeypatch, caplog):
    clock = [1000.0]
    monkeypatch.setattr("app.switches.time", SimpleNamespace(monotonic=lambda: clock[0]))
    switches = Switches()
    assert switches() == frozenset()
    session.add(ProviderSwitch(provider="google"))
    session.commit()
    assert switches() == frozenset()
    clock[0] += SWITCH_SECONDS
    assert switches() == {"google"}

    # A database it cannot reach leaves what it read before, and says so.
    def unreachable():
        raise OperationalError("SELECT", {}, ConnectionRefusedError("refused"))

    monkeypatch.setattr("app.switches.SessionLocal", unreachable)
    clock[0] += SWITCH_SECONDS
    assert switches() == {"google"}
    assert "could not be read, so those read before still hold" in caplog.text
    # And it asks again only once SWITCH_SECONDS have passed.
    caplog.clear()
    assert switches() == {"google"} and caplog.text == ""
