"""Entitlements: an organisation is on a plan that says how many projects, documents, bytes,
pages and questions it gets; the routes refuse past that with 402, a sentence that says to
upgrade and the figure it is held to, the upload rejects the files that would not fit while the
rest go in, the parse stage refuses a document the month has no pages left for, a paying plan's
backlog gets the larger share of the workers, a team has the members it bought as seats, an
organisation's agreed figures take the place of its plan's, one that holds more than its plan
allows is read-only until it is within it, and the operator moves an organisation between
plans, seats and figures with the `plan` command."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import READ_ONLY
from app.billing import change_plan, plan_problem
from app.entitlements import (
    CHARS_PER_PAGE,
    MONTHS,
    PLANS,
    Excess,
    Refusal,
    check_member,
    check_project,
    entitlements_of,
    month_of,
    over_limits,
    read_only,
    size_label,
    text_pages,
)
from app.indexing.pipeline import process_document, reset_stages
from app.jobs import INDEX_DOCUMENT, claim, enqueue
from app.maintenance import main
from app.models import (
    Block,
    ChatMessage,
    Document,
    Invitation,
    Job,
    Organization,
    Project,
    UsageEvent,
)
from app.usage import DOCUMENT, UPLOAD, record
from tests.test_api import create_project, upload
from tests.test_audit import actions
from tests.test_auth import me, signup
from tests.test_authorization import reads, starts, statuses, world, writes  # noqa: F401 (a fixture)
from tests.test_chats import new_chat
from tests.test_pipeline import scanned_pdf
from tests.test_usage import rows_of

ONE_PROJECT = (
    "The Free plan allows 1 project, and this organisation has 1. "
    "Upgrade your plan to create another."
)
ONE_DOCUMENT = (
    "The Free plan allows 1 document, and this organisation has 1. "
    "Delete some, or upgrade your plan to add more."
)


@pytest.fixture
def plan(monkeypatch):
    """Put the free plan's limits where a test wants them: `plan(documents=1)`."""

    def set_limits(**limits):
        monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], **limits))

    return set_limits


@pytest.fixture
def member(make_account, act_as):
    """The API acts as the owner of a fresh organisation, which starts on the free plan."""
    account = make_account("ann@globex.example")
    act_as(account)
    return account


def this_month() -> tuple[str, str]:
    """The current month's name and the next one's, as the refusals name them."""
    start, following = month_of()
    return MONTHS[start.month - 1], MONTHS[following.month - 1]


def last_month() -> datetime:
    return month_of()[0] - timedelta(days=1)


def test_a_new_organisation_starts_on_the_free_plan_and_unknown_plans_are_refused(client, session):
    assert signup(client)["organization"]["plan"] == "free"
    session.add(Organization(name="Gold", slug="gold", plan="gold"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_the_local_account_is_held_to_nothing(client):
    assert me(client).json()["organization"]["plan"] == "enterprise"
    for number in range(3):
        assert client.post("/api/projects", json={"name": f"Project {number}"}).status_code == 201


def test_the_free_plan_allows_one_project(member, client, session, capsys):
    create_project(client, "Contracts")
    refused = client.post("/api/projects", json={"name": "Invoices", "description": ""})
    assert refused.status_code == 402
    # The figure it is held to, from which the page chooses what to offer.
    assert refused.json() == {"detail": ONE_PROJECT, "limit": "projects"}
    assert [p["name"] for p in client.get("/api/projects").json()] == ["Contracts"]

    # The operator moves the organisation up, and the next request sees it.
    main(["plan", "globex", "pro"])
    assert capsys.readouterr().out.startswith(
        "Globex (globex) moved from the Free plan to the Pro plan, which allows:\n"
        "  projects            10\n"
    )
    assert client.post("/api/projects", json={"name": "Invoices"}).status_code == 201


def test_an_upload_fills_the_plan_file_by_file_and_a_full_plan_refuses_the_request(
    member, client, session, plan, sample_pdf, sample_docx
):
    plan(documents=1)
    project = create_project(client)
    result = upload(client, project["id"], sample_pdf, sample_docx)
    assert [d["filename"] for d in result["documents"]] == ["msa.pdf"]
    assert result["rejected"] == [
        {"filename": sample_docx.name, "reason": ONE_DOCUMENT, "limit": "documents"}
    ]
    # The one that went in has its job; the one that did not left nothing behind.
    assert session.scalar(select(func.count()).select_from(Document)) == 1
    assert session.scalar(select(func.count()).select_from(Job)) == 1

    refused = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", (sample_docx.name, sample_docx.read_bytes()))],
    )
    assert refused.status_code == 402
    assert refused.json() == {"detail": ONE_DOCUMENT, "limit": "documents"}
    assert session.scalar(select(func.count()).select_from(Document)) == 1

    # Deleting a document frees its place.
    assert client.delete(f"/api/documents/{result['documents'][0]['id']}").status_code == 204
    assert upload(client, project["id"], sample_docx)["rejected"] == []


def test_a_file_that_would_not_fit_the_plans_storage_is_rejected(
    member, client, plan, sample_pdf, sample_docx
):
    pdf_size, docx_size = len(sample_pdf.read_bytes()), len(sample_docx.read_bytes())
    plan(storage_bytes=pdf_size + 10)
    project = create_project(client)
    result = upload(client, project["id"], sample_pdf, sample_docx)
    assert [d["filename"] for d in result["documents"]] == ["msa.pdf"]
    assert result["rejected"] == [
        {
            "filename": sample_docx.name,
            "reason": (
                f"This file is {size_label(docx_size)}, more than the {size_label(10)} left of "
                f"the Free plan's {size_label(pdf_size + 10)}. Delete some documents, or "
                "upgrade your plan."
            ),
            "limit": "storage_bytes",
        }
    ]

    plan(storage_bytes=pdf_size)
    refused = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", (sample_docx.name, sample_docx.read_bytes()))],
    )
    assert refused.status_code == 402
    assert refused.json()["detail"] == (
        f"The Free plan allows {size_label(pdf_size)} of documents, and this organisation "
        f"holds {size_label(pdf_size)}. Delete some, or upgrade your plan to add more."
    )


def test_a_month_whose_pages_are_used_up_refuses_uploads_until_the_next(
    member, client, session, plan, sample_pdf, sample_docx
):
    plan(pages_per_month=2)
    org = member.organization.id
    project = create_project(client)
    pages = {"org_id": org, "project_id": project["id"], "user_id": None, "kind": UPLOAD}
    # Last month's pages are last month's.
    record(session, **pages, measures={"pages": 2}, occurred_at=last_month())
    session.commit()
    assert upload(client, project["id"], sample_pdf)["rejected"] == []

    record(session, **pages, measures={"pages": 2})
    session.commit()
    month, following = this_month()
    refused = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", (sample_docx.name, sample_docx.read_bytes()))],
    )
    assert refused.status_code == 402
    assert refused.json()["detail"] == (
        f"The Free plan indexes 2 pages a month, and this organisation has indexed 2 in {month}. "
        f"Upgrade your plan, or upload again from 1 {following}."
    )


def test_the_parse_stage_refuses_a_document_the_month_has_no_pages_left_for(
    session, services, account, plan, make_document, tmp_path
):
    account.organization.plan = "free"
    session.commit()
    plan(pages_per_month=3)
    document = make_document(scanned_pdf(tmp_path / "scan.pdf", 2))
    record(
        session,
        org_id=account.organization.id,
        project_id=document.project_id,
        user_id=None,
        kind=UPLOAD,
        measures={"pages": 2},
    )
    session.commit()

    (failure,) = process_document(document.id, services)
    _, following = this_month()
    refusal = (
        "This document has 2 pages, more than the 1 left of the Free plan's 3 a month. "
        f"Upgrade your plan, or index it again from 1 {following}."
    )
    assert (failure.stage, failure.message, failure.transient) == ("parse", refusal, False)
    session.refresh(document)
    assert document.status == "failed" and document.error == refusal
    # Refused before a page was read: no OCR call was made, and no page was counted.
    assert services.llm.calls == []
    assert [(row.kind, row.unit) for row in rows_of(session, (DOCUMENT, document.id))] == []

    # The month has room again (the plan grew): "Index again" parses it and counts its pages.
    plan(pages_per_month=4)
    reset_stages(document, "parse")
    session.commit()
    assert process_document(document.id, services) == []
    session.refresh(document)
    assert document.status == "ready" and document.page_count == 2
    rows = rows_of(session, (DOCUMENT, document.id))
    assert (UPLOAD, "pages", 2) in [(row.kind, row.unit, row.quantity) for row in rows]

    # A document whose pages are counted is not checked again when it is indexed again.
    plan(pages_per_month=1)
    reset_stages(document, "parse")
    session.commit()
    assert process_document(document.id, services) == []


def test_a_document_without_pages_counts_against_the_month_by_its_text(
    session, services, account, plan, make_document, tmp_path
):
    account.organization.plan = "free"
    session.commit()
    plan(pages_per_month=4)
    # Two paragraphs, 7,000 characters of text: three pages begun.
    notes = tmp_path / "notes.txt"
    notes.write_text("a" * 3_500 + "\n\n" + "b" * 3_500)
    document = make_document(notes)
    record(
        session,
        org_id=account.organization.id,
        project_id=document.project_id,
        user_id=None,
        kind=UPLOAD,
        measures={"pages": 2},
    )
    session.commit()

    (failure,) = process_document(document.id, services)
    _, following = this_month()
    refusal = (
        "This document has about 3 pages of text (a page for every 3,000 characters), more "
        "than the 2 left of the Free plan's 4 a month. Upgrade your plan, or index it again "
        f"from 1 {following}."
    )
    assert (failure.stage, failure.message, failure.transient) == ("parse", refusal, False)
    assert rows_of(session, (DOCUMENT, document.id)) == []

    # The month has room again: the parse counts the text's pages, and the document has
    # none of its own still, for the outline and the viewer.
    plan(pages_per_month=5)
    reset_stages(document, "parse")
    session.commit()
    assert process_document(document.id, services) == []
    session.refresh(document)
    assert document.status == "ready" and document.page_count is None
    assert document.usage["upload"]["pages"] == 3
    rows = rows_of(session, (DOCUMENT, document.id))
    assert [row.quantity for row in rows if row.unit == "pages"] == [3]

    # Counted once: "Index again" neither checks the month nor counts the text again.
    plan(pages_per_month=1)
    reset_stages(document, "parse")
    session.commit()
    assert process_document(document.id, services) == []
    rows = rows_of(session, (DOCUMENT, document.id))
    assert [row.quantity for row in rows if row.unit == "pages"] == [3]
    session.refresh(document)
    assert document.usage["upload"]["pages"] == 3


@pytest.mark.parametrize("sample", ["sample_docx", "sample_xlsx", "sample_csv"])
def test_a_word_file_a_sheet_and_a_csv_count_the_pages_of_their_text(
    session, services, account, make_document, sample, request
):
    document = make_document(request.getfixturevalue(sample))
    assert process_document(document.id, services) == []
    characters = session.scalar(
        select(func.sum(func.length(Block.text))).where(Block.document_id == document.id)
    )
    rows = rows_of(session, (DOCUMENT, document.id))
    pages = [row.quantity for row in rows if (row.kind, row.unit) == (UPLOAD, "pages")]
    assert pages == [text_pages(characters)] and pages[0] >= 1


def test_text_is_counted_a_page_for_every_so_many_characters_begun():
    assert [text_pages(n) for n in (1, CHARS_PER_PAGE, CHARS_PER_PAGE + 1)] == [1, 1, 2]
    assert text_pages(0) == 0


def test_the_free_plan_answers_so_many_questions_a_month(member, client, session, plan):
    plan(questions_per_month=1)
    project = create_project(client)
    first = new_chat(client, project["id"])
    assert (
        client.post(f"/api/chats/{first['id']}/messages", json={"content": "Notice?"}).status_code
        == 201
    )

    month, following = this_month()
    second = new_chat(client, project["id"])
    refused = client.post(f"/api/chats/{second['id']}/messages", json={"content": "Price?"})
    assert refused.status_code == 402
    assert refused.json() == {
        "detail": (
            f"The Free plan answers 1 question a month, and this organisation has asked 1 in "
            f"{month}. Upgrade your plan, or ask again from 1 {following}."
        ),
        "limit": "questions_per_month",
    }
    # Nothing was saved for the refused question.
    assert client.get(f"/api/chats/{second['id']}").json()["messages"] == []
    # The month counts the asking, so deleting the chat, for good too, gives its question back
    # to nobody.
    assert client.delete(f"/api/chats/{first['id']}").status_code == 204
    assert client.delete(f"/api/trash/chats/{first['id']}").status_code == 204
    again = client.post(f"/api/chats/{second['id']}/messages", json={"content": "Price?"})
    assert again.status_code == 402 and again.json() == refused.json()

    # Last month's questions are last month's.
    session.execute(
        text("UPDATE usage_events SET occurred_at = :when WHERE unit = 'questions'"),
        {"when": last_month()},
    )
    session.commit()
    assert (
        client.post(f"/api/chats/{second['id']}/messages", json={"content": "Price?"}).status_code
        == 201
    )
    assert session.scalar(select(func.count()).select_from(ChatMessage)) == 2
    asked = session.scalars(select(UsageEvent).where(UsageEvent.unit == "questions")).all()
    assert [(row.kind, row.quantity, row.user_id) for row in asked] == [
        ("answer", 1, member.user.id)
    ] * 2


def test_an_open_invitation_holds_a_members_place(session, member, plan):
    org = member.organization
    assert check_member(session, org).sentence == (
        "The Free plan allows 1 member, and this organisation has 1, open invitations "
        "counted. Upgrade your plan to invite more."
    )
    plan(members=2)
    assert check_member(session, org) is None

    def invite(email: str, **fields) -> Invitation:
        fields.setdefault("expires_at", datetime.now(UTC) + timedelta(days=7))
        invitation = Invitation(org_id=org.id, email=email, token_hash=email, **fields)
        session.add(invitation)
        session.commit()
        return invitation

    # One that expired holds nothing; an accepted one is gone (app/invitations.py).
    invite("old@globex.example", expires_at=datetime.now(UTC) - timedelta(days=1))
    assert check_member(session, org) is None
    invite("new@globex.example")
    assert check_member(session, org).sentence.startswith(
        "The Free plan allows 2 members, and this organisation has 2, open invitations counted."
    )


# The team plan's weight, or the same weight agreed with an organisation on the free plan.
@pytest.mark.parametrize("plan, overrides", [("team", {}), ("free", {"priority": 3})])
def test_a_paying_plans_backlog_gets_the_larger_share_of_the_workers(
    session, make_account, plan, overrides
):
    free = make_account("f@free.test").organization
    paying = make_account("p@paying.test").organization
    paying.plan, paying.overrides = plan, overrides
    projects = {}
    for organization in (free, paying):
        projects[organization.id] = Project(org_id=organization.id, name="Files")
    session.add_all(projects.values())
    session.flush()

    def queue(organization: Organization) -> int:
        job = enqueue(
            session,
            INDEX_DOCUMENT,
            org_id=organization.id,
            project_id=projects[organization.id].id,
        )
        session.commit()
        return job.id

    # Both have had three jobs lately. The free organisation queued first, but the team plan
    # weighs its share by three, so its job counts as one and goes first.
    served = [queue(organization) for organization in (free, paying) for _ in range(3)]
    session.execute(
        text("UPDATE jobs SET status = 'done', finished_at = now() WHERE id = ANY(:ids)"),
        {"ids": served},
    )
    session.commit()
    free_job, paying_job = queue(free), queue(paying)
    assert claim("w:1", [INDEX_DOCUMENT]).id == paying_job
    assert claim("w:2", [INDEX_DOCUMENT]).id == free_job

    # On the same plan, the older job goes first.
    paying.plan, paying.overrides = "free", {}
    session.commit()
    free_job, paying_job = queue(free), queue(paying)
    assert claim("w:3", [INDEX_DOCUMENT]).id == free_job


def test_the_plan_command_shows_what_a_plan_allows(session, account, capsys):
    main(["plan", "local"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Local (local) is on the Enterprise plan, which allows:"
    assert lines[1].split() == ["projects", "no", "limit"]
    assert lines[7].split() == ["api_access", "yes"]

    main(["plan", "local", "free"])
    lines = capsys.readouterr().out.splitlines()
    assert (
        lines[0] == "Local (local) moved from the Enterprise plan to the Free plan, which allows:"
    )
    assert lines[3].split() == ["storage_bytes", "100", "MB"]
    session.refresh(account.organization)
    assert account.organization.plan == "free"

    with pytest.raises(SystemExit, match="No organisation has the slug 'nobody'."):
        main(["plan", "nobody"])
    with pytest.raises(SystemExit):
        main(["plan", "local", "gold"])


def test_sizes_and_months_read_well():
    assert [size_label(n) for n in (999, 12_000, 2_500_000, 100_000_000, 10**10)] == [
        "999 bytes",
        "12 kB",
        "2.5 MB",
        "100 MB",
        "10 GB",
    ]
    start, following = month_of(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert (start, following) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_a_team_has_the_members_it_bought_as_seats(session, member, client):
    org = member.organization
    change_plan(session, org, "team")
    session.commit()
    # Before it buys any, the plan's own.
    assert entitlements_of(org).members == PLANS["team"].members
    change_plan(session, org, "team", seats=2)
    session.commit()
    shown = me(client).json()["organization"]
    assert shown["seats"] == 2
    assert shown["allowance"]["members"] == 2 and shown["allowance"]["agreed"] == []
    assert check_member(session, org) is None

    session.add(
        Invitation(
            org_id=org.id,
            email="bo@globex.example",
            token_hash="bo",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    session.commit()
    assert check_member(session, org) == Refusal(
        "members",
        "This organisation has 2 seats on the Team plan, and 2 members, open invitations "
        "counted. Add seats to invite more.",
    )

    # Fewer seats than it has members leaves it over the limit, and takes nobody away.
    change_plan(session, org, "team", seats=1)
    session.commit()
    assert over_limits(session, org) == [Excess("members", "2 members of 1")]
    assert session.scalar(select(func.count()).select_from(Invitation)) == 1
    assert check_member(session, org).sentence.startswith(
        "This organisation has 1 seat on the Team plan"
    )


def test_figures_agreed_with_an_organisation_take_the_place_of_its_plans(session, member, client):
    org = member.organization
    change_plan(
        session, org, "enterprise", overrides={"storage_bytes": None, "projects": 1, "priority": 9}
    )
    session.commit()
    create_project(client, "Contracts")
    refused = client.post("/api/projects", json={"name": "More"})
    assert refused.status_code == 402
    # On the largest plan there is none to upgrade to.
    assert refused.json()["detail"] == (
        "The Enterprise plan allows 1 project, and this organisation has 1. Ask for a higher "
        "limit to create another."
    )
    assert me(client).json()["organization"]["allowance"] == {
        "projects": 1,
        "documents": None,
        "storage_bytes": None,
        "pages_per_month": None,
        "questions_per_month": None,
        "members": None,
        "retention_days": None,
        "api_access": True,
        "connectors": True,
        "agreed": ["projects", "storage_bytes"],
        "pages_bought": 0,
        "questions_bought": 0,
        "over": [],
    }

    # On another plan an agreed figure takes the place of that plan's, and the rest are its.
    change_plan(session, org, "free", overrides={"projects": 2, "documents": None})
    session.commit()
    assert check_project(session, org) is None
    assert entitlements_of(org).documents is None
    assert entitlements_of(org).storage_bytes == PLANS["free"].storage_bytes


def test_another_plan_clears_the_seats_and_figures_agreed_on_the_one_before(session, member):
    org = member.organization
    change_plan(session, org, "team", seats=5, overrides={"projects": 80})
    session.commit()
    change_plan(session, org, "team", seats=5, overrides={"projects": None})
    session.commit()
    change_plan(session, org, "pro")
    session.commit()
    session.refresh(org)
    assert (org.plan, org.seats, org.overrides) == ("pro", None, {})
    assert actions(session, org.id) == [
        ("plan_changed", {"from": "free", "to": "team"}),
        ("seats_changed", {"from": None, "to": 5}),
        (
            "entitlements_changed",
            {"entitlement": "projects", "from": None, "to": 80, "agreed": True},
        ),
        # Agreed to be no limit, which is not the plan's own.
        (
            "entitlements_changed",
            {"entitlement": "projects", "from": 80, "to": None, "agreed": True},
        ),
        ("plan_changed", {"from": "team", "to": "pro"}),
        ("seats_changed", {"from": 5, "to": None}),
        (
            "entitlements_changed",
            {"entitlement": "projects", "from": None, "to": None, "agreed": False},
        ),
    ]
    # Putting it on the plan it is on, as it is, records nothing.
    change_plan(session, org, "pro")
    session.commit()
    assert len(actions(session, org.id)) == 7


def test_a_plan_seats_or_figure_that_cannot_be_is_refused(session, member):
    assert plan_problem("gold", None, {}) == (
        "There is no 'gold' plan; the plans are free, pro, team, enterprise."
    )
    assert plan_problem("pro", 3, {}) == "The Pro plan is not sold by the seat; only Team is."
    assert plan_problem("team", 0, {}) == "Seats are a whole number of one or more, not 0."
    assert plan_problem("team", None, {"members": 9}) == (
        "The Team plan's members are its seats; set the seats instead."
    )
    assert plan_problem("enterprise", None, {"pages": 9}).startswith(
        "'pages' is no entitlement; they are projects, documents, storage_bytes,"
    )
    for figure, problem in (
        ({"api_access": "maybe"}, "api_access is yes or no, not 'maybe'."),
        ({"model_tier": "gold"}, "model_tier is one of standard, premium, not 'gold'."),
        ({"priority": 0}, "priority is a weight of 1 or more, not 0."),
        (
            {"projects": -1},
            "projects is a whole number of 0 or more, or none for no limit, not -1.",
        ),
        (
            {"projects": True},
            "projects is a whole number of 0 or more, or none for no limit, not True.",
        ),
    ):
        assert plan_problem("enterprise", None, figure) == problem
    agreeable = {"projects": None, "members": 0, "api_access": False, "model_tier": "standard"}
    assert plan_problem("enterprise", None, agreeable) is None

    # Nothing is changed by a refusal.
    with pytest.raises(RuntimeError, match="not sold by the seat"):
        change_plan(session, member.organization, "pro", seats=3)
    assert member.organization.plan == "free" and actions(session) == []


def test_the_plan_command_sells_seats_and_agrees_figures(session, account, client, capsys):
    def plan(*arguments: str) -> list[str]:
        main(["plan", "local", *arguments])
        session.expire_all()
        return capsys.readouterr().out.splitlines()

    lines = plan("team", "--seats", "5")
    assert lines[0] == (
        "Local (local) moved from the Enterprise plan to the Team plan with 5 seats, which allows:"
    )
    assert lines[6] == "  members             5 (its seats; the plan's 3)"

    lines = plan(
        "--set", "projects=500",
        "--set", "storage_bytes=1.5 TB",
        "--set", "daily_spend_micros=25",
        "--set", "api_access=no",
        "--set", "audit_days=none",
    )  # fmt: skip
    assert lines[0] == "Local (local) is on the Team plan with 5 seats, which allows:"
    assert lines[1] == "  projects            500 (agreed; the plan's 50)"
    assert lines[3] == "  storage_bytes       1,500 GB (agreed; the plan's 100 GB)"
    assert lines[7] == "  api_access          no (agreed; the plan's yes)"
    assert lines[10] == "  audit_days          no limit (agreed; the plan's 365)"
    assert lines[13] == "  daily_spend_micros  USD 25.00 a day (agreed; the plan's USD 50.00 a day)"
    organization = session.get(Organization, account.organization.id)
    assert organization.overrides == {
        "projects": 500,
        "storage_bytes": 1_500_000_000_000,
        "daily_spend_micros": 25_000_000,
        "api_access": False,
        "audit_days": None,
    }

    # A figure made the plan's again; the projects past one it now holds are kept.
    create_project(client, "Contracts")
    create_project(client, "Invoices")
    lines = plan("--unset", "storage_bytes", "--set", "projects=1")
    assert lines[3] == "  storage_bytes       100 GB"
    assert lines[-1] == (
        "It holds more than it allows, 2 projects of 1: it keeps it all, and is read-only "
        "until it is within the plan again."
    )

    lines = plan("pro")
    assert lines[-3:-1] == [
        "The 5 seats it bought on the Team plan no longer count.",
        "The figures agreed with it on the Team plan no longer count: projects, api_access, "
        "audit_days, daily_spend_micros.",
    ]
    # Pro keeps chats and audit rows for less time than Team with its agreed figures did.
    assert lines[-1].startswith("Until ") and lines[-1].endswith(
        "it keeps chats for good and audit log entries for good, as before its move to a plan "
        "that keeps them for less time."
    )
    organization = session.get(Organization, account.organization.id)
    assert (organization.seats, organization.overrides) == (None, {})

    for arguments, refusal in (
        (["--seats", "3"], "The Pro plan is not sold by the seat; only Team is."),
        (["team", "--seats", "some"], "--seats takes a whole number or none, not 'some'."),
        (["--set", "projects"], "--set takes NAME=VALUE, such as projects=500, not 'projects'."),
        (
            ["--set", "projects=lots"],
            "projects is a whole number of 0 or more, or none for no limit, not 'lots'.",
        ),
        (["--unset", "pages"], "'pages' is no entitlement; they are projects, documents,"),
    ):
        with pytest.raises(SystemExit, match=re.escape(refusal)):
            main(["plan", "local", *arguments])
    session.expire_all()
    assert session.get(Organization, account.organization.id).plan == "pro"


# Past the plan


PAST_FREE = (
    "This organisation holds more than the Free plan allows ({held}), so until it is within the "
    "plan nothing can be added or changed: what it holds can still be read, asked about and "
    "deleted. {ways}."
)


def test_an_organisation_past_its_plan_is_read_only_until_it_is_within_it(
    client,
    session,
    world,  # noqa: F811
    account,
):
    # Lowered to Free with two members and an open invitation, where the plan allows one.
    change_plan(session, account.organization, "free")
    session.commit()
    assert me(client).json()["organization"]["allowance"]["over"] == [
        {"limit": "members", "words": "3 members of 1"}
    ]
    refusal = {
        "detail": PAST_FREE.format(
            held="3 members of 1",
            ways="Remove members or revoke invitations, or upgrade your plan",
        ),
        "limit": "members",
    }
    changes = [route for route in writes(world) if route[0] != "DELETE"] + [
        ("POST", "/api/projects", {"json": {"name": "More"}}),
        ("POST", "/api/organization/invitations", {"json": {"email": "joe@example.com"}}),
    ]
    for method, path, kwargs in changes:
        refused = client.request(method, path, **kwargs)
        assert (refused.status_code, refused.json()) == (402, refusal), f"{method} {path}"

    # What it holds is read and asked about as a viewer's would be.
    seen = statuses(client, reads(world) + starts(world))
    assert set(seen.values()) <= {200, 201}, seen
    asked = client.post(f"/api/chats/{world.chat_id}/messages", json={"content": "What else?"})
    assert asked.status_code == 201

    # Deleting is how it gets within the plan, and so is parting with members.
    assert client.delete(f"/api/folders/{world.folder_id}").status_code == 204
    assert client.delete(f"/api/documents/{world.document_id}").status_code == 204
    assert client.delete(f"/api/organization/invitations/{world.invitation_id}").status_code == 204
    still = client.patch(f"/api/projects/{world.project_id}", json={"name": "Renamed"})
    assert still.json()["detail"].startswith(
        "This organisation holds more than the Free plan allows (2 members of 1)"
    )
    assert client.delete(f"/api/organization/members/{world.member_id}").status_code == 204
    assert me(client).json()["organization"]["allowance"]["over"] == []
    renamed = client.patch(f"/api/projects/{world.project_id}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert client.delete(f"/api/projects/{world.project_id}").status_code == 204


def test_past_its_plan_a_viewer_hears_of_its_role_and_others_find_nothing(
    client,
    session,
    world,  # noqa: F811
    account,
    make_account,
    act_as,
):
    change_plan(session, account.organization, "free")
    session.commit()
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    viewed = client.patch(f"/api/projects/{world.project_id}", json={"name": "Renamed"})
    assert (viewed.status_code, viewed.json()["detail"]) == (403, READ_ONLY)

    # Another organisation past its own plan finds this one's projects no more than before.
    eve = make_account("eve@globex.com")
    make_account("bo@globex.com", eve.organization, role="member")
    act_as(eve)
    assert read_only(session, eve.organization) is not None
    found = statuses(client, writes(world))
    assert set(found.values()) == {404}, found


def test_the_way_back_within_the_plan_is_what_the_organisation_holds_past_it(
    session, member, client, make_account
):
    org = member.organization
    assert read_only(session, org) is None
    change_plan(session, org, "team", seats=2)
    session.commit()
    create_project(client, "Contracts")
    create_project(client, "Invoices")
    make_account("bo@globex.example", org, role="member")
    assert read_only(session, org) is None

    change_plan(session, org, "free")
    session.commit()
    assert read_only(session, org) == Refusal(
        "projects",
        PAST_FREE.format(
            held="2 projects of 1 and 2 members of 1",
            ways="Delete what it no longer needs, remove members or revoke invitations, or "
            "upgrade your plan",
        ),
    )

    # Seats fewer than its members: more seats are the way, besides parting with someone.
    change_plan(session, org, "team", seats=1)
    session.commit()
    assert read_only(session, org).sentence.endswith(
        "(2 members of 1), so until it is within the plan nothing can be added or changed: what "
        "it holds can still be read, asked about and deleted. Remove members or revoke "
        "invitations, or add seats."
    )

    # Terms agreed with it: a higher figure is asked for, there being no larger plan.
    change_plan(session, org, "enterprise", overrides={"projects": 1})
    session.commit()
    assert read_only(session, org) == Refusal(
        "projects",
        "This organisation holds more than the Enterprise plan allows (2 projects of 1), so "
        "until it is within the plan nothing can be added or changed: what it holds can still "
        "be read, asked about and deleted. Delete what it no longer needs, or ask for a higher "
        "limit.",
    )
