"""The notices about members' work: an upload that took a while is told once its last document
is ready, failed or set apart, in one message naming what could not be indexed and why, to the
member who sent it if they still see its project; an upload indexed while they watched is told
nothing; and an organisation's owners and admins are told once a month when its pages or
questions reach 80 % and all of what it may use, or when its pages left could not take a
document."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text

import app.notices as notices_module
from app.accounts import Account
from app.config import get_settings
from app.entitlements import MONTHS, PLANS, month_of
from app.indexing.pipeline import process_document
from app.jobs import claim, finish, queue_index
from app.mail import LISTED, upload_indexed_email
from app.models import (
    AllowancePack,
    Document,
    Job,
    Membership,
    Notification,
    Project,
    QuotaNotice,
    Upload,
    User,
)
from app.notices import (
    MONTH_PAGES,
    MONTH_QUESTIONS,
    NOTICE_AFTER,
    after_index,
    note_quota,
    prune_quota_notices,
    settle_uploads,
)
from app.postbox import STOP_NOTICES
from app.usage import UPLOAD, record
from app.worker import HANDLERS, Worker
from tests.conftest import pageless_pdf
from tests.test_api import create_project, upload
from tests.test_chats import new_chat
from tests.test_jobs import RATE_LIMITED

APP = "https://emudoc.example.com"
INDEX = ["index_document"]


@pytest.fixture
def ann(make_account, act_as) -> Account:
    """The API acts as Ann, the owner of Globex, a fresh organisation on the free plan."""
    account = make_account("ann@globex.example")
    act_as(account)
    return account


@pytest.fixture
def free(monkeypatch):
    """Put the free plan's limits where a test wants them: `free(pages_per_month=10)`."""

    def set_limits(**limits):
        monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], **limits))

    return set_limits


@pytest.fixture(autouse=True)
def at_app(monkeypatch):
    """Where the links lead, for the request and the worker alike."""
    monkeypatch.setattr(get_settings(), "app_url", APP)


def run_next(services) -> Job:
    """Run the next index job as the worker does, its row ended before what follows it."""
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, INDEX)
    assert job is not None
    worker._run(job)
    return job


def taken_long(session, minutes: int = 10) -> None:
    """Make every upload as old as one that took a while to index."""
    session.execute(
        text("UPDATE uploads SET created_at = created_at - make_interval(mins => :minutes)"),
        {"minutes": minutes},
    )
    session.commit()


def uploads(session) -> list[Upload]:
    session.expire_all()
    return session.scalars(select(Upload).order_by(Upload.id)).all()


def told(mailer, to: str | None = None) -> list:
    """The notices delivered, to `to` alone when given: what has the footer to stop them."""
    return [
        message
        for message in mailer.messages
        if STOP_NOTICES in message.text and (to is None or message.to == to)
    ]


def uploaded_at(session, upload: Upload) -> str:
    moment = upload.created_at
    return f"{moment:%H:%M} UTC on {moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def test_an_upload_that_took_a_while_is_told_once_with_what_could_not_be_indexed(
    client, session, services, mailer, ann, sample_pdf, tmp_path
):
    project = create_project(client)
    empty = pageless_pdf(tmp_path / "empty.pdf")
    result = upload(client, project["id"], sample_pdf, empty)
    contract, broken = (document["id"] for document in result["documents"])
    (made,) = uploads(session)
    assert (made.project_id, made.user_id, made.settled_at) == (
        project["id"],
        ann.user.id,
        None,
    )
    assert set(session.scalars(select(Document.upload_id))) == {made.id}

    # The contract is indexed while the empty file waits: the upload is not settled.
    run_next(services)
    assert session.get(Document, contract).status == "ready"
    assert uploads(session)[0].settled_at is None and told(mailer) == []

    # The empty file fails, the last of the upload, which took long enough to be told.
    taken_long(session)
    run_next(services)
    (made,) = uploads(session)
    assert made.settled_at is not None
    error = session.get(Document, broken).error
    (notice,) = told(mailer)
    assert notice.to == "ann@globex.example"
    assert notice.subject == "Some of your documents could not be indexed"
    assert notice.text.startswith(
        "Hi Ann,\n\n"
        f"Of the 2 documents you uploaded to Contracts in Globex at {uploaded_at(session, made)}, "
        "1 is indexed, ready to search and ask about, and 1 could not be:\n\n"
        f"empty.pdf: {error}\n\n"
        'Once what a document says is put right, "Index again" in the library runs it again:\n\n'
        f"{APP}/projects/{project['id']}?organization={ann.organization.id}\n\n"
    )
    assert notice.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    # Told once: what looks again, the worker or the housekeeper, finds it settled.
    assert settle_uploads() == 0
    queue_index(session, session.get(Document, contract))
    session.commit()
    run_next(services)
    assert len(told(mailer)) == 1


def test_an_upload_indexed_while_its_member_watched_is_told_nothing(
    client, session, services, mailer, ann, sample_pdf
):
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    run_next(services)
    (made,) = uploads(session)
    assert made.settled_at - made.created_at < NOTICE_AFTER
    assert told(mailer) == []


def test_an_upload_is_settled_once_and_only_after_every_documents_job_ended(
    client, session, services, mailer, ann, sample_pdf, sample_docx
):
    project = create_project(client)
    upload(client, project["id"], sample_pdf, sample_docx)
    taken_long(session)
    index = HANDLERS["index_document"]
    first, second = claim("w:1", INDEX), claim("w:2", INDEX)

    # The first ends while the second is running: not settled, whatever its document says.
    index.run(first, services)
    finish(first, "w:1", "done")
    after_index(first, services)
    assert uploads(session)[0].settled_at is None

    # Both end before either looks: the first look settles it, the second finds it settled.
    index.run(second, services)
    finish(second, "w:2", "done")
    after_index(first, services)
    after_index(second, services)
    (notice,) = told(mailer)
    assert notice.subject == "Your documents are indexed"
    assert "The 2 documents you uploaded to Contracts in Globex at " in notice.text
    assert "are indexed, ready to search and ask about." in notice.text


def test_a_retry_waiting_keeps_the_upload_unsettled(
    client, session, services, mailer, ann, sample_pdf
):
    project = create_project(client)
    (document,) = upload(client, project["id"], sample_pdf)["documents"]
    services.llm.failure = RATE_LIMITED
    run_next(services)
    # Ready without summaries, its enrich stage queued again: not settled yet.
    assert session.get(Document, document["id"]).status == "ready"
    assert uploads(session)[0].settled_at is None

    services.llm.failure = None
    session.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    session.commit()
    taken_long(session)
    run_next(services)
    (notice,) = told(mailer)
    assert notice.subject == "Your document is indexed"
    assert "The document you uploaded to Contracts in Globex at " in notice.text


def test_a_document_deleted_while_it_waited_leaves_the_rest_to_settle(
    client, session, services, mailer, ann, sample_pdf, sample_docx
):
    project = create_project(client)
    first, second = upload(client, project["id"], sample_pdf, sample_docx)["documents"]
    assert client.delete(f"/api/documents/{second['id']}").status_code == 204
    taken_long(session)
    run_next(services)
    (notice,) = told(mailer)
    assert notice.subject == "Your document is indexed"
    # Its job runs for nothing, and finds nothing more to tell.
    run_next(services)
    assert len(told(mailer)) == 1


def test_the_housekeeper_settles_an_upload_whose_last_look_was_lost(
    client, session, services, mailer, ann, sample_pdf
):
    project = create_project(client)
    (document,) = upload(client, project["id"], sample_pdf)["documents"]
    # Indexed by a worker that died before it looked.
    process_document(document["id"], services)
    session.execute(text("UPDATE jobs SET status = 'done'"))
    session.commit()
    taken_long(session)
    assert uploads(session)[0].settled_at is None

    assert settle_uploads() == 1
    assert [notice.subject for notice in told(mailer)] == ["Your document is indexed"]
    assert settle_uploads() == 0


def test_whoever_no_longer_sees_the_project_or_wants_notices_is_told_nothing(
    client, session, services, mailer, ann, make_account, act_as, free, sample_pdf, sample_docx
):
    free(members=5)
    project = create_project(client)
    bob = make_account("bob@globex.example", ann.organization, "member")
    act_as(bob)
    upload(client, project["id"], sample_pdf)
    # The project was made private without him while it was indexed.
    session.get(Project, project["id"]).visibility = "private"
    session.commit()
    taken_long(session)
    run_next(services)
    assert uploads(session)[0].settled_at is not None
    assert told(mailer) == []

    # Ann, who sees every project, turned these notices off both ways.
    act_as(ann)
    upload(client, project["id"], sample_docx)
    for way in ("app", "email"):
        session.get(User, ann.user.id).choose("upload_indexed", way, False)
    session.commit()
    taken_long(session)
    run_next(services)
    assert uploads(session)[1].settled_at is not None
    assert told(mailer) == []
    assert session.scalars(select(Notification)).all() == []


def test_a_member_who_left_is_told_nothing(
    client, session, services, mailer, ann, make_account, act_as, free, sample_pdf
):
    free(members=5)
    project = create_project(client)
    bob = make_account("bob@globex.example", ann.organization, "member")
    act_as(bob)
    upload(client, project["id"], sample_pdf)
    session.execute(delete(Membership).where(Membership.user_id == bob.user.id))
    session.commit()
    taken_long(session)
    run_next(services)
    assert told(mailer) == []


def test_a_long_list_of_failures_names_the_first_and_counts_the_rest():
    failed = [(f"scan {number}.pdf", "No text could be extracted.") for number in range(25)]
    content = upload_indexed_email(
        "Ann", "Globex", "Contracts", "09:00 UTC on 1 October 2026", 0, failed, APP
    )
    assert content.subject == "Your documents could not be indexed"
    listing = content.blocks[2].splitlines()
    assert len(listing) == LISTED + 1
    assert listing[0] == "scan 0.pdf: No text could be extracted."
    assert listing[-1] == "And 5 more, which the library lists with theirs."
    assert content.blocks[1] == (
        "None of the 25 documents you uploaded to Contracts in Globex at 09:00 UTC on 1 October "
        "2026 could be indexed:"
    )

    one = upload_indexed_email("Ann", "Globex", "Contracts", "now", 0, failed[:1], APP)
    assert one.subject == "Your document could not be indexed"
    assert one.blocks[2] == "scan 0.pdf: No text could be extracted."


def test_owners_and_admins_are_told_once_a_month_of_its_questions_at_80_and_100_percent(
    client, session, mailer, ann, make_account, free
):
    free(questions_per_month=5, members=5)
    admin = make_account("ada@globex.example", ann.organization, "admin")
    make_account("max@globex.example", ann.organization, "member")
    quiet = make_account("vic@globex.example", ann.organization, "admin")
    quiet.user.choose("quota", "email", False)
    session.commit()
    project = create_project(client)
    month, following = (MONTHS[moment.month - 1] for moment in month_of())

    def ask() -> None:
        chat = new_chat(client, project["id"])
        response = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Price?"})
        assert response.status_code == 201

    for _ in range(3):
        ask()
    assert told(mailer) == []

    ask()
    near = told(mailer)
    assert [notice.to for notice in near] == ["ann@globex.example", "ada@globex.example"]
    assert near[0].subject == "Your organisation has used most of its questions for the month"
    assert near[0].text.startswith(
        "Hi Ann,\n\n"
        f"Globex has used 4 of the 5 questions it may ask on Emudoc in {month}, 80 %. Once they "
        f"are used, no question is answered until 1 {following}, when the count starts afresh."
        "\n\nThe organisation's usage page shows what it has used, and what lifts the limit for "
        f"this month:\n\n{APP}/organization/usage?organization={ann.organization.id}\n\n"
    )

    ask()
    full = told(mailer, admin.user.email)[-1]
    assert full.subject == "Your organisation has used all its questions for the month"
    assert (
        f"Globex has used all the 5 questions it may ask on Emudoc in {month}. No more questions "
        f"are answered until 1 {following}, when the count starts afresh." in full.text
    )
    assert len(told(mailer)) == 4
    # Vic, who turned these notices off by email, sees both in the app, as the others do.
    centre = session.scalars(
        select(Notification.title)
        .where(Notification.user_id == quiet.user.id)
        .order_by(Notification.id)
    ).all()
    assert centre == [near[0].subject, full.subject]

    # A pack for the month starts the count again, against what the month allows now.
    start = month_of()[0]
    session.add(
        AllowancePack(
            org_id=ann.organization.id,
            unit="questions",
            quantity=5,
            month=start,
            checkout_id="cs_test_more",
        )
    )
    session.commit()
    ask()
    ask()
    assert len(told(mailer)) == 4
    ask()
    assert "has used 8 of the 10 questions" in told(mailer)[-1].text
    notes = session.scalars(select(QuotaNotice).order_by(QuotaNotice.id)).all()
    assert [(note.share, note.allowance, note.used) for note in notes] == [
        (80, 5, 4),
        (100, 5, 5),
        (80, 10, 8),
    ]


def test_owners_are_told_when_the_months_pages_run_short(
    session, services, mailer, ann, make_document, free, sample_pdf, tmp_path
):
    free(pages_per_month=10)
    org = ann.organization
    project = Project(org_id=org.id, name="Contracts")
    session.add(project)
    session.commit()
    record(
        session,
        org_id=org.id,
        project_id=project.id,
        user_id=None,
        kind=UPLOAD,
        measures={"pages": 7},
    )
    session.commit()
    month, following = (MONTHS[moment.month - 1] for moment in month_of())

    # Two pages more make 9 of 10.
    contract = make_document(sample_pdf, project=project)
    assert process_document(contract.id, services) == []
    (near,) = told(mailer)
    assert near.subject == "Your organisation has used most of its pages for the month"
    assert (
        f"Globex has used 9 of the 10 pages it may index on Emudoc in {month}, 90 %. 1 is left, "
        "and a document with more pages than that is not indexed until "
        f"1 {following}, when the count starts afresh." in near.text
    )

    # The next two-page document does not fit what is left.
    more = Project(org_id=org.id, name="More")
    session.add(more)
    session.commit()
    other = make_document(sample_pdf, "copy.pdf", project=more)
    (failure,) = process_document(other.id, services)
    assert failure.stage == "parse"
    short = told(mailer)[-1]
    assert short.subject == "Your organisation's pages for the month ran short"
    assert (
        f"Globex may index 10 pages on Emudoc in {month} and has used 9, so a document with "
        "more pages than the 1 left was not indexed. One that fits still is, and from 1 "
        f'{following}, when the count starts afresh, "Index again" indexes the other.' in short.text
    )
    # All of it is not told again once it ran short, nor most of it.
    assert note_quota(session, org, MONTH_PAGES, APP, adding=1) == 0
    assert len(told(mailer)) == 2


def test_a_plan_without_the_limit_is_told_nothing(session, account):
    assert account.organization.plan == "enterprise"
    assert note_quota(session, account.organization, MONTH_QUESTIONS, APP, adding=10**6) == 0
    assert session.scalars(select(QuotaNotice)).all() == []


def test_the_housekeeper_forgets_the_notices_of_months_gone_by(session, ann):
    start = month_of()[0]
    for month in (start - timedelta(days=40), start):
        session.add(
            QuotaNotice(
                org_id=ann.organization.id,
                month=month,
                figure=MONTH_PAGES,
                share=80,
                allowance=10,
                used=8,
            )
        )
    session.commit()
    assert prune_quota_notices(session.connection()) == 1
    session.commit()
    assert [note.month for note in session.scalars(select(QuotaNotice))] == [start]


def test_what_follows_a_job_that_fails_changes_nothing_of_the_job(
    client, session, services, ann, sample_pdf, monkeypatch, caplog
):
    project = create_project(client)
    upload(client, project["id"], sample_pdf)

    def broken(*_):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(notices_module, "settle_upload", broken)
    with caplog.at_level("ERROR", logger="app.worker"):
        job = run_next(services)
    session.expire_all()
    assert session.get(Job, job.id).status == "done"
    assert f"What follows job {job.id} (index_document) failed" in caplog.text
