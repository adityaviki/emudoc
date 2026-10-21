"""The operator's area (app/operators.py, app/api/operator.py): found by an operator alone, from
an operator's network, and showing what the maintenance commands print."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.operator import NOT_FOUND
from app.billing import change_plan
from app.config import LOOPBACK_NETWORKS, Settings, get_settings
from app.jobs import enqueue
from app.main import create_app
from app.maintenance import main
from app.models import Job, User, UserSession, WatchAlert
from app.operators import TWO_FACTOR_FIRST, grant
from tests.conftest import APP_HEADERS
from tests.test_auth import signup
from tests.test_jobs import hold
from tests.test_usage import charge

# This machine, which OPERATOR_NETWORKS lets in in development; and an address it does not.
HERE = ("127.0.0.1", 50000)
ELSEWHERE = ("203.0.113.9", 50000)
ROUTES = (
    "/api/operator/me",
    "/api/operator/organizations",
    "/api/operator/organizations/1",
    "/api/operator/users",
    "/api/operator/costs",
    "/api/operator/jobs",
    "/api/operator/services",
)
PRICED = "test:priced"


def browser(app, address=HERE) -> TestClient:
    return TestClient(app, headers=APP_HEADERS, client=address)


def operator_on(app, session, email="olga@example.com", name="Olga") -> TestClient:
    """A browser from this machine signed in as a new account made an operator."""
    signed_in = browser(app)
    signup(signed_in, email, name=name)
    grant(session, session.scalar(select(User).where(User.email == email)))
    session.commit()
    return signed_in


@pytest.fixture
def operator(client, session) -> TestClient:
    return operator_on(client.app, session)


def not_found(response) -> bool:
    # A HEAD is answered without its body.
    body = response.json() if response.content else {"detail": NOT_FOUND}
    return response.status_code == 404 and body == {"detail": NOT_FOUND}


def test_nobody_but_an_operator_finds_the_area(client, session, operator, account):
    for path in ROUTES:
        assert operator.get(path).status_code == 200, path

    member = browser(client.app)
    signup(member, "mo@example.com", name="Mo")
    session_cookie = dict(operator.cookies)
    elsewhere = browser(client.app, ELSEWHERE)
    elsewhere.cookies.update(session_cookie)
    keyed = browser(client.app)
    keyed.cookies.update(session_cookie)
    keyed.headers["Authorization"] = "Bearer emk_" + "x" * 43
    # The suite's local account answers a request without a session elsewhere; never here.
    nobody = browser(client.app)
    for path in ROUTES:
        for who, refused in (
            ("a member", member),
            ("the operator from elsewhere", elsewhere),
            ("the operator with a key", keyed),
            ("the local account", nobody),
        ):
            assert not_found(refused.get(path)), (who, path)

    # A path or a method no route takes is as missing to the operator as to anyone, so that
    # neither a 405 nor a refusal of the app's header says that a route is there.
    for refused in (operator, member, nobody):
        assert not_found(refused.post("/api/operator/organizations"))
        assert not_found(refused.post("/api/operator/costs", headers={"X-Requested-With": ""}))
        assert not_found(refused.head("/api/operator/jobs"))
        assert not_found(refused.get("/api/operator/nothing/here"))

    # Once the operator is no longer one, the area is gone for their session too.
    main(["operators", "remove", "olga@example.com"])
    assert not_found(operator.get("/api/operator/me"))


def test_a_session_waiting_for_its_code_is_no_operator(session, operator):
    session.execute(update(UserSession).values(needs_mfa=True))
    session.commit()
    assert not_found(operator.get("/api/operator/me"))


def test_production_asks_the_operator_for_two_factor_first(session, operator, monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")
    refused = operator.get("/api/operator/me")
    assert refused.status_code == 403
    assert refused.json()["detail"] == TWO_FACTOR_FIRST

    session.execute(
        update(User)
        .where(User.email == "olga@example.com")
        .values(totp_enabled_at=datetime.now(UTC))
    )
    session.commit()
    shown = operator.get("/api/operator/me").json()
    assert (shown["name"], shown["email"], shown["environment"]) == (
        "Olga",
        "olga@example.com",
        "production",
    )


def test_the_networks_are_this_machine_in_development_and_nobody_in_production():
    development = Settings(database_url="postgresql://emudoc@localhost/emudoc")
    assert development.operator_networks == LOOPBACK_NETWORKS
    production = Settings(database_url="postgresql://x@db/x", environment="production")
    assert production.operator_nets == ()
    named = Settings(database_url="postgresql://x@db/x", operator_networks="10.0.0.0/8, ::1")
    assert [str(network) for network in named.operator_nets] == ["10.0.0.0/8", "::1/128"]
    with pytest.raises(ValueError, match="OPERATOR_NETWORKS lists"):
        Settings(database_url="postgresql://x@db/x", operator_networks="10.0.0.1/8")


def test_the_page_is_the_operators_alone(services, mailer, session, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("members")
    (tmp_path / "operator.html").write_text("operators")
    monkeypatch.setattr("app.main.FRONTEND_DIST", tmp_path)
    with TestClient(create_app(services=services, mailer=mailer), headers=APP_HEADERS) as client:
        operator = operator_on(client.app, session)
        member = browser(client.app)
        signup(member, "mo@example.com", name="Mo")
        for path in ("/operator", "/operator/organizations/7", "/operator.html"):
            assert operator.get(path).text == "operators", path
            assert member.get(path).text == "members", path
            assert client.get(path).text == "members", path
        # A path that only begins like it is the members' as any other.
        assert operator.get("/operators").text == "members"


def test_organisations_are_listed_newest_first_and_found(
    operator, session, account, make_account, monkeypatch
):
    monkeypatch.setattr(get_settings(), "model_prices", f"{PRICED}=1/2")
    globex = make_account("ann@globex.example").organization
    initech = make_account("bob@initech.example").organization
    charge(
        session,
        globex.id,
        "answer",
        {"calls": 1, "input_tokens": 3_000_000},
        datetime.now(UTC),
        model=PRICED,
    )
    session.commit()

    listed = operator.get("/api/operator/organizations").json()
    slugs = [row["slug"] for row in listed["organizations"]]
    # Before them, the local organisation and Olga's own, from her sign-up.
    assert slugs[:2] == ["initech", "globex"]
    assert sorted(slugs[2:]) == sorted([account.organization.slug, "olga"])
    globex_row = next(row for row in listed["organizations"] if row["slug"] == "globex")
    assert (globex_row["members"], globex_row["plan"], globex_row["cost_micros"]) == (
        1,
        "free",
        3_000_000,
    )
    assert listed["next_before"] is None and listed["cost_days"] == 30

    def found(q: str) -> list[str]:
        rows = operator.get("/api/operator/organizations", params={"q": q}).json()
        return [row["slug"] for row in rows["organizations"]]

    assert found("glob") == ["globex"]
    assert found("INITECH") == ["initech"]
    assert found("Bob@Initech.example") == ["initech"]
    assert found(str(globex.id)) == ["globex"]
    assert found("_") == []

    first = operator.get("/api/operator/organizations", params={"limit": 3}).json()
    assert [row["id"] for row in first["organizations"]][:2] == [initech.id, globex.id]
    rest = operator.get(
        "/api/operator/organizations", params={"limit": 3, "before": first["next_before"]}
    ).json()
    assert [row["slug"] for row in first["organizations"] + rest["organizations"]] == slugs
    assert rest["next_before"] is None


def test_an_organisation_shows_its_plan_usage_members_and_costs(
    client, operator, session, make_account, monkeypatch
):
    monkeypatch.setattr(get_settings(), "model_prices", f"{PRICED}=1/2")
    ann = make_account("ann@globex.example")
    make_account("bob@globex.example", ann.organization, role="viewer")
    organization = ann.organization
    change_plan(session, organization, "team", seats=5, overrides={"projects": 500})
    organization.stripe_customer_id = "cus_123"
    yesterday = datetime.now(UTC) - timedelta(days=1)
    charge(
        session,
        organization.id,
        "answer",
        {"calls": 2, "input_tokens": 1_500_000},
        yesterday,
        model=PRICED,
    )
    session.commit()

    shown = operator.get(f"/api/operator/organizations/{organization.id}").json()
    assert shown["organization"]["slug"] == "globex"
    assert shown["organization"]["seats"] == 5
    figures = {figure["name"]: figure for figure in shown["entitlements"]}
    assert figures["projects"] == {
        "name": "projects",
        "value": "500",
        "plan_value": figures["projects"]["plan_value"],
        "agreed": True,
    }
    assert figures["members"]["value"] == "5" and figures["members"]["agreed"]
    assert not figures["documents"]["agreed"]
    assert [(member["email"], member["role"]) for member in shown["members"]] == [
        ("ann@globex.example", "owner"),
        ("bob@globex.example", "viewer"),
    ]
    assert shown["usage"]["members"] == {"used": 2, "allowed": 5, "bought": 0}
    assert shown["stripe_customer_id"] == "cus_123"
    assert shown["cost"]["cost_micros"] == 1_500_000 and shown["cost"]["calls"] == 2
    assert shown["cost_days"] == [{"day": str(yesterday.date()), "cost_micros": 1_500_000}]

    gone = operator.get("/api/operator/organizations/999999")
    assert gone.status_code == 404 and "No organisation has this id" in gone.json()["detail"]


def test_users_are_found_by_address_or_name(operator, session, account, make_account):
    ann = make_account("ann@globex.example")
    make_account("ann.b@initech.example", ann.organization, role="admin")

    def found(q: str = "") -> list[dict]:
        return operator.get("/api/operator/users", params={"q": q}).json()["users"]

    assert found()[0]["email"] == "ann.b@initech.example"
    (olga,) = found("olga")
    assert olga["operator"] and olga["email_verified"] and olga["last_seen_at"] is not None
    shown = found("ANN")
    assert [user["email"] for user in shown] == ["ann.b@initech.example", "ann@globex.example"]
    assert shown[0]["memberships"] == [
        {"org_id": ann.organization.id, "slug": "globex", "name": "Globex", "role": "admin"}
    ]
    assert not shown[0]["operator"] and shown[0]["last_seen_at"] is None
    assert found("%") == []


def test_the_costs_are_the_costs_commands(operator, session, account, make_account, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_prices", f"{PRICED}=1/2")
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    globex = make_account("ann@globex.example").organization
    now = datetime.now(UTC)
    charge(session, globex.id, "answer", {"calls": 1, "input_tokens": 3_000_000}, now, model=PRICED)
    charge(
        session,
        account.organization.id,
        "enrich",
        {"calls": 4, "input_tokens": 1_000_000},
        now,
        model=PRICED,
    )
    charge(
        session,
        account.organization.id,
        "enrich",
        {"calls": 1, "input_tokens": 10},
        now,
        model="test:free",
    )
    session.commit()

    costs = operator.get("/api/operator/costs", params={"days": 7}).json()
    assert costs["since"] == str((now - timedelta(days=6)).date())
    rows = [
        (row["slug"], row["cost_micros"], row["limit_micros"]) for row in costs["organizations"]
    ]
    # The free plan's limit is a day's spending of 1.00.
    assert rows == [("globex", 3_000_000, 1_000_000), (account.organization.slug, 1_000_000, None)]
    assert costs["organizations"][0]["days_at_limit"] == 1
    platform = costs["platform"]
    assert (platform["cost_micros"], platform["calls"], platform["limit_micros"]) == (
        4_000_000,
        6,
        2_000_000,
    )
    # Olga's own organisation used nothing.
    assert costs["idle"] == 1
    models = {(row["kind"], row["model"]): row for row in costs["models"]}
    assert models["answer", PRICED]["cost_micros"] == 3_000_000
    assert models["enrich", "test:free"]["unpriced_tokens"] == 10


def test_the_jobs_show_the_queue_the_running_and_the_dead_letters(operator, session, account):
    org_id = account.organization.id
    running, failed, older, due = (
        enqueue(session, "index_document", org_id=org_id, payload={"document_id": 999})
        for _ in range(4)
    )
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

    shown = operator.get("/api/operator/jobs").json()
    assert shown["queue"] == [
        {
            "kind": "index_document",
            "due": 1,
            "waiting": 0,
            "running": 1,
            "failed": 2,
            "oldest_due_seconds": pytest.approx(600, abs=30),
        }
    ]
    assert [job["id"] for job in shown["running"]] == [running.id]
    assert shown["running"][0]["worker_id"] == "host:41"
    assert [job["id"] for job in shown["failed"]] == [failed.id, older.id]
    assert shown["failed"][0]["last_error"] == "RuntimeError: the parser broke"
    assert shown["failed"][0]["about"].endswith("document 999 (deleted)")
    assert not shown["failed"][0]["reported"]
    assert shown["failed_total"] == 2 and shown["history_days"] == 30


def test_the_services_show_the_models_and_what_the_watch_sees(
    operator, session, services, monkeypatch
):
    monkeypatch.setattr(get_settings(), "disabled_providers", "google")
    services.embedder.fail = True
    raised = datetime.now(UTC) - timedelta(hours=2)
    session.add(WatchAlert(key="embeddings", title="old", message="old", raised_at=raised))
    session.commit()

    shown = operator.get("/api/operator/services").json()
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


def test_the_command_grants_and_takes_back_the_area(client, session, capsys):
    signup(client, "olga@example.com", name="Olga")
    main(["operators"])
    assert "Nobody is an operator" in capsys.readouterr().out

    main(["operators", "add", "Olga@Example.com"])
    out = capsys.readouterr().out
    assert "Olga is an operator now." in out
    assert "olga@example.com" in out and "no two-factor" in out
    assert f"from {LOOPBACK_NETWORKS.replace(',', ', ')} (OPERATOR_NETWORKS)" in out
    main(["operators", "add", "olga@example.com"])
    assert "Olga was an operator already." in capsys.readouterr().out

    main(["operators", "remove", "olga@example.com"])
    assert "Olga is no longer an operator." in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No account has the address 'eve@example.com'"):
        main(["operators", "add", "eve@example.com"])
    with pytest.raises(SystemExit):
        main(["operators", "add"])
