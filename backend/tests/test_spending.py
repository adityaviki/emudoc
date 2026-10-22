"""Daily spending limits: an organisation's questions are refused with 402 once its plan's
limit for the day is spent, the platform's limit refuses every organisation with 503 until
midnight UTC, an answer queued under a limit ends with its sentence when the worker reaches it,
an indexing stage that needs the model waits for midnight while structure makes do, the
operator is warned once a day as a day nears a limit, and the plan command shows the limit as
money. The kill switch is tested with the model layer (tests/test_llm.py) and the production
start (tests/test_config.py)."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import func, select, text

import app.worker as worker_module
from app.answers import answer
from app.config import get_settings
from app.jobs import INDEX_DOCUMENT, claim
from app.mail import MailError, Message
from app.maintenance import main
from app.models import SpendAlert
from app.spending import alert_spending, day_of, retry_after, warn_without_prices, when
from app.usage import ANSWER, record
from app.worker import HANDLERS, Worker
from tests.conftest import FakeMailer, FakeTurn
from tests.test_api import create_project
from tests.test_chats import answered, ask, follow, new_chat
from tests.test_jobs import all_jobs, wait_until
from tests.test_pipeline import scanned_pdf, stage_statuses

# Ten tokens in cost one unit of currency: a FakeTurn and a FakeLLM.structured call (10 in, 5
# out) each cost 1.50, so a plan's USD 1.00 a day is spent by one of them.
PRICED = f"{get_settings().agent_model}=100000/100000"
OPERATOR = "ops@example.test"


@pytest.fixture(autouse=True)
def one_day(monkeypatch):
    """Hold the limits' clock at noon of this UTC day, as if a test took no time: the day is a
    fixed window, and one that turned while a test spent would start its sums over, which only
    `yesterday` should do. The rows themselves are stamped with the real time, which the sum
    since the held day's midnight counts whichever side of midnight it falls."""
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(
        "app.spending.datetime", SimpleNamespace(now=lambda tz: now, combine=datetime.combine)
    )


@pytest.fixture
def priced(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_prices", PRICED)


@pytest.fixture
def member(make_account, act_as):
    """The API acts as the owner of a fresh organisation, which starts on the free plan."""
    account = make_account("ann@globex.example")
    act_as(account)
    return account


def spend(session, org_id: int, amount: str, at: datetime | None = None) -> None:
    """Charge the organisation `amount` of currency, as an answer that cost it would have."""
    (row,) = record(
        session,
        org_id=org_id,
        project_id=None,
        user_id=None,
        kind=ANSWER,
        measures={"input_tokens": 1},
        model="priced-by-hand",
        occurred_at=at,
    )
    row.cost_micros = int(Decimal(amount) * 1_000_000)
    session.commit()


def yesterday(session) -> None:
    """Move every usage row to the day before: midnight UTC has passed."""
    session.execute(
        text("UPDATE usage_events SET occurred_at = :when"),
        {"when": day_of()[0] - timedelta(hours=1)},
    )
    session.commit()


def plan_refusal(spent: str, cap: str = "1.00", plan_name: str = "Free") -> str:
    return (
        f"The {plan_name} plan allows USD {cap} of model use a day, and this organisation has "
        f"used USD {spent} today (UTC). Upgrade your plan, or try again after midnight UTC."
    )


def platform_refusal(spent: str, cap: str) -> str:
    return (
        f"Emudoc has reached its spending limit of USD {cap} a day on models (USD {spent} spent "
        "today, UTC). The limit resets at midnight UTC; try again then."
    )


def continues() -> str:
    return f"Indexing continues at {when(day_of()[1])}."


def alerts(session) -> list[tuple]:
    return [
        (row.org_id, row.day, row.spent_micros, row.cap_micros)
        for row in session.scalars(select(SpendAlert).order_by(SpendAlert.id))
    ]


def test_the_plan_limits_what_an_organisation_spends_in_a_day(
    member, client, session, services, priced, capsys
):
    project = create_project(client)
    chat = new_chat(client, project["id"])
    services.llm.agent_turns = [FakeTurn(TextPart("Yes."))]
    events = answered(client, services, chat["id"], "Is it?")
    done = next(event for event in events if event["type"] == "done")
    assert done["usage"]["cost_micros"] == 1_500_000

    # The free plan allows USD 1.00 a day, and that answer cost 1.50: the next question is
    # refused before it is saved.
    refused = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "And?"})
    assert refused.status_code == 402
    assert refused.json() == {"detail": plan_refusal("1.50"), "limit": "daily_spend_micros"}
    assert len(client.get(f"/api/chats/{chat['id']}").json()["messages"]) == 2

    # Yesterday's spending was yesterday's.
    yesterday(session)
    services.llm.agent_turns = [FakeTurn(TextPart("Yes."))]
    answered(client, services, chat["id"], "And?")
    assert (
        client.post(f"/api/chats/{chat['id']}/messages", json={"content": "So?"}).status_code == 402
    )

    # The enterprise plan has no limit of its own.
    main(["plan", "globex", "enterprise"])
    capsys.readouterr()
    ask(client, chat["id"], "So?")


def test_the_platform_limit_refuses_every_organisation_until_midnight_utc(
    client, session, services, account, make_account, act_as, priced, monkeypatch
):
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    # The local organisation is on the enterprise plan, which has no limit of its own; the
    # platform's holds it all the same.
    project = create_project(client)
    chat = new_chat(client, project["id"])
    services.llm.agent_turns = [FakeTurn(TextPart("Yes."))]
    answered(client, services, chat["id"], "Is it?")
    spend(session, account.organization.id, "0.60")

    before = retry_after()
    refused = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "And?"})
    assert refused.status_code == 503
    assert refused.json()["detail"] == platform_refusal("2.10", "2.00")
    # Retry-After is the time to midnight UTC, when the limit resets.
    assert 0 < int(refused.headers["Retry-After"]) <= 24 * 3600
    assert abs(int(refused.headers["Retry-After"]) - before) <= 2
    assert len(client.get(f"/api/chats/{chat['id']}").json()["messages"]) == 2

    # Another organisation is refused too, though nothing of its own ran up the bill.
    other = make_account("ann@globex.example")
    act_as(other)
    theirs = new_chat(client, create_project(client)["id"])
    refused = client.post(f"/api/chats/{theirs['id']}/messages", json={"content": "Hello?"})
    assert refused.status_code == 503

    # Zero is no limit.
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal(0))
    ask(client, theirs["id"], "Hello?")


def test_an_answer_queued_under_the_limit_ends_with_its_sentence_when_the_worker_reaches_it(
    member, client, session, services, priced
):
    project = create_project(client)
    chat = new_chat(client, project["id"])
    # Accepted with nothing spent; another answer runs the day up before the worker gets to it.
    asked = ask(client, chat["id"], "Is it?")
    spend(session, member.organization.id, "1.00")

    services.llm.agent_turns = [FakeTurn(TextPart("Yes."))]
    answer(asked["answer"]["id"], services)
    assert services.llm.agent_requests == []
    assert follow(client, chat["id"], asked["answer"]["id"]) == [
        {"type": "error", "message": plan_refusal("1.00")},
        {"type": "end", "status": "failed"},
    ]
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert reply["status"] == "failed" and reply["error"] == plan_refusal("1.00")
    assert reply["content"] == "" and reply["usage"] == {}


def run_index(services) -> None:
    HANDLERS[INDEX_DOCUMENT].run(claim("w:1", [INDEX_DOCUMENT]), services)


def test_an_indexing_stage_that_needs_the_model_waits_for_midnight_when_the_day_is_spent(
    session, services, account, make_document, sample_pdf, tmp_path, priced, monkeypatch
):
    account.organization.plan = "free"
    session.commit()
    spend(session, account.organization.id, "1.00")
    document = make_document(sample_pdf)
    run_index(services)

    # Parsed and searchable today; the summaries wait for midnight, and the document says so.
    session.refresh(document)
    assert document.status == "ready" and document.error is None
    assert stage_statuses(document) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "failed",
        "embed": "pending",
    }
    assert document.stages["enrich"]["detail"] == f"{plan_refusal('1.00')} {continues()}"
    # The page words the wait as the day's limit and not a retry after a failure.
    assert document.stages["enrich"]["retry_at"] == day_of()[1].isoformat()
    assert document.stages["enrich"]["deferred"] is True
    assert services.llm.calls == []
    # The handler leaves the first job to the worker to finish; the next waits for midnight.
    _, deferred = all_jobs(session)
    assert deferred.status == "queued" and deferred.run_at == day_of()[1]
    # The wait is no retry: the next run is the first try against a day with room.
    assert deferred.payload == {"document_id": document.id}
    assert claim("w:1", [INDEX_DOCUMENT]) is None

    # Midnight: the spending is yesterday's, the job is due, and the stage runs.
    yesterday(session)
    session.execute(text("UPDATE jobs SET run_at = now() WHERE id = :id"), {"id": deferred.id})
    session.commit()
    run_index(services)
    session.refresh(document)
    assert set(stage_statuses(document).values()) == {"done"}
    assert [name for name, _ in services.llm.calls] == ["DocumentAnalysis"]

    # A scan costs a model call per page, so the whole document waits, queued, before a page
    # is read: the parse stage is required.
    scan = make_document(scanned_pdf(tmp_path / "scan.pdf", 2))
    run_index(services)
    session.refresh(scan)
    assert scan.status == "queued" and scan.error is None
    assert scan.stages["parse"] == {
        "status": "failed",
        "detail": f"{plan_refusal('1.50')} {continues()}",
        "retry_at": day_of()[1].isoformat(),
        "deferred": True,
    }
    assert "OcrPage" not in [name for name, _ in services.llm.calls]
    assert all_jobs(session)[-1].run_at == day_of()[1]

    # Structure makes do: a flat text file gets its outline from the pages rather than from
    # the model, and says so, since the document cannot be searched without the stage.
    flat = tmp_path / "notes.txt"
    flat.write_text("\n\n".join(f"Paragraph number {n} of the notes." for n in range(16)))
    notes = make_document(flat)
    run_index(services)
    session.refresh(notes)
    assert notes.status == "ready"
    assert notes.stages["structure"]["status"] == "done"
    assert notes.stages["structure"]["detail"].endswith(
        " passages; sections not inferred (the day's spending limit is reached)"
    )
    assert "InferredSections" not in [name for name, _ in services.llm.calls]

    # The platform's limit holds the stages the same way, with its own sentence.
    account.organization.plan = "enterprise"
    session.commit()
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("1"))
    again = make_document(sample_pdf, "again.pdf")
    run_index(services)
    session.refresh(again)
    assert again.stages["enrich"]["detail"] == f"{platform_refusal('1.50', '1.00')} {continues()}"


def test_the_operator_is_warned_once_a_day_as_spending_nears_a_limit(
    session, account, make_account, monkeypatch
):
    mailer = FakeMailer()
    globex = make_account("ann@globex.example").organization
    globex.plan = "pro"
    session.commit()
    # 85 % of the Pro plan's USD 10.00 a day.
    spend(session, globex.id, "8.50")

    # Without an address nothing is sent or noted.
    assert alert_spending(mailer, "") == 0
    assert mailer.sent == [] and alerts(session) == []

    assert alert_spending(mailer, OPERATOR) == 1
    ((to, subject, body),) = mailer.sent
    assert (to, subject) == (OPERATOR, "Emudoc: Globex (globex) has spent 85 % of its daily limit")
    assert "has reached 80 % of a daily limit:\n\n" in body
    assert "  Globex (globex): USD 8.50 of the Pro plan's USD 10.00 (85 %)\n" in body
    assert "set DISABLED_PROVIDERS and restart the app" in body
    assert "`uv run python -m app.maintenance costs --days 1` prints what each organisation" in body

    # Once a day, however much more is spent.
    spend(session, globex.id, "3.00")
    assert alert_spending(mailer, OPERATOR) == 0
    assert len(mailer.sent) == 1

    # The platform's limit has a warning of its own: the local organisation's plan has no
    # limit, but what it spends counts towards DAILY_SPEND_CAP with everyone else's.
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("100"))
    spend(session, account.organization.id, "72")
    assert alert_spending(mailer, OPERATOR) == 1
    _, subject, body = mailer.sent[-1]
    assert subject == "Emudoc: the platform has spent 83 % of its daily limit"
    assert "  The platform: USD 83.50 of DAILY_SPEND_CAP USD 100.00 (83 %)\n" in body
    assert "Globex" not in body
    today = day_of()[0].date()
    assert alerts(session) == [
        (globex.id, today, 8_500_000, 10_000_000),
        (None, today, 83_500_000, 100_000_000),
    ]

    # A relay that is down leaves the warnings for the next round, which sends them as one.
    session.execute(text("DELETE FROM spend_alerts"))
    session.commit()

    class DownMailer:
        def send(self, message: Message) -> None:
            raise MailError("The email could not be sent.")

    with pytest.raises(MailError):
        alert_spending(DownMailer(), OPERATOR)
    assert alerts(session) == []
    assert alert_spending(mailer, OPERATOR) == 2
    _, subject, body = mailer.sent[-1]
    assert subject == "Emudoc: 2 spending warnings today"
    assert body.index("The platform:") < body.index("Globex (globex):")

    # Yesterday's spending warns nobody today.
    session.execute(text("DELETE FROM spend_alerts"))
    session.commit()
    yesterday(session)
    assert alert_spending(mailer, OPERATOR) == 0


def test_the_worker_warns_from_its_alerter_thread(session, services, make_account, monkeypatch):
    globex = make_account("ann@globex.example").organization
    spend(session, globex.id, "0.80")
    monkeypatch.setattr(get_settings(), "alert_email", OPERATOR)
    monkeypatch.setattr(worker_module, "ALERT_INTERVAL", 0.05)
    mailer = FakeMailer()

    worker = Worker(services, concurrency=1, mailer=mailer)
    worker.start()
    try:
        wait_until(lambda: bool(mailer.sent))
    finally:
        worker.stop()
    assert mailer.sent[0][1] == "Emudoc: Globex (globex) has spent 80 % of its daily limit"
    assert session.scalar(select(func.count()).select_from(SpendAlert)) == 1


def test_the_plan_command_shows_the_days_limit_as_money(session, account, capsys):
    main(["plan", "local"])
    assert capsys.readouterr().out.splitlines()[-1].split() == ["daily_spend_micros", "no", "limit"]
    main(["plan", "local", "pro"])
    assert "  daily_spend_micros  USD 10.00 a day" in capsys.readouterr().out.splitlines()


def test_production_is_told_when_the_plans_limits_count_nothing_of_a_model(monkeypatch, caplog):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    with caplog.at_level("WARNING", logger="app.spending"):
        warn_without_prices(settings)
        monkeypatch.setattr(settings, "model_prices", PRICED)
        warn_without_prices(settings)
        monkeypatch.setattr(settings, "index_fallback_model", "anthropic:claude-haiku-5-5")
        warn_without_prices(settings)
    nothing, fallback = caplog.messages
    assert nothing.startswith("MODEL_PRICES is not set: no cost is recorded")
    assert fallback == (
        "MODEL_PRICES has no price for INDEX_FALLBACK_MODEL anthropic:claude-haiku-5-5, so what "
        "it answers counts nothing against the plans' daily spending limits; add its price."
    )


def test_the_day_is_the_utc_day():
    late = datetime(2026, 10, 13, 23, 59, 30, tzinfo=UTC)
    start, following = day_of(late)
    assert (start, following) == (
        datetime(2026, 10, 13, tzinfo=UTC),
        datetime(2026, 10, 14, tzinfo=UTC),
    )
    assert retry_after(late) == 30
    assert when(following) == "00:00 UTC on 14 October"
    # East of Greenwich it is the 14th already; the day stays UTC's.
    east = late.astimezone(timezone(timedelta(hours=2)))
    assert day_of(east) == (start, following)
