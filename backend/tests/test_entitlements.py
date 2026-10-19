"""Entitlements: an organisation is on a plan that says how many projects, documents, bytes,
pages and questions it gets; the routes refuse past that with 402 and a sentence that says to
upgrade, the upload rejects the files that would not fit while the rest go in, the parse stage
refuses a document the month has no pages left for, a paying plan's backlog gets the larger
share of the workers, and the operator moves an organisation between plans with the `plan`
command."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.entitlements import MONTHS, PLANS, check_member, month_of, size_label
from app.indexing.pipeline import process_document, reset_stages
from app.jobs import INDEX_DOCUMENT, claim, enqueue
from app.maintenance import main
from app.models import ChatMessage, Document, Invitation, Job, Organization, Project
from app.usage import DOCUMENT, UPLOAD, record
from tests.test_api import create_project, upload
from tests.test_auth import me, signup
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
    assert refused.json()["detail"] == ONE_PROJECT
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
    assert result["rejected"] == [{"filename": sample_docx.name, "reason": ONE_DOCUMENT}]
    # The one that went in has its job; the one that did not left nothing behind.
    assert session.scalar(select(func.count()).select_from(Document)) == 1
    assert session.scalar(select(func.count()).select_from(Job)) == 1

    refused = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", (sample_docx.name, sample_docx.read_bytes()))],
    )
    assert refused.status_code == 402
    assert refused.json()["detail"] == ONE_DOCUMENT
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
    assert refused.json()["detail"] == (
        f"The Free plan answers 1 question a month, and this organisation has asked 1 in {month}. "
        f"Upgrade your plan, or ask again from 1 {following}."
    )
    # Nothing was saved for the refused question.
    assert client.get(f"/api/chats/{second['id']}").json()["messages"] == []

    # Last month's questions are last month's.
    session.execute(text("UPDATE chat_messages SET created_at = :when"), {"when": last_month()})
    session.commit()
    assert (
        client.post(f"/api/chats/{second['id']}/messages", json={"content": "Price?"}).status_code
        == 201
    )
    assert session.scalar(select(func.count()).select_from(ChatMessage)) == 4


def test_an_open_invitation_holds_a_members_place(session, member, plan):
    org = member.organization
    assert check_member(session, org) == (
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

    # An invitation that was accepted became a membership; one that expired holds nothing.
    invite("old@globex.example", expires_at=datetime.now(UTC) - timedelta(days=1))
    invite("done@globex.example", accepted_at=datetime.now(UTC))
    assert check_member(session, org) is None
    invite("new@globex.example")
    assert check_member(session, org).startswith(
        "The Free plan allows 2 members, and this organisation has 2, open invitations counted."
    )


def test_a_paying_plans_backlog_gets_the_larger_share_of_the_workers(session, make_account):
    free = make_account("f@free.test").organization
    paying = make_account("p@paying.test").organization
    paying.plan = "team"
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
    paying.plan = "free"
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
    assert lines[3].split() == ["storage_bytes", "100,000,000"]
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
