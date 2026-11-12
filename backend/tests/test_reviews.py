"""Documents' reviews (app/reviews.py): put in review, assigned to a member who may change the
project, approved or disputed, listed by state in the reviews view, told to whoever is assigned,
kept in the audit log by names and states, through the trash, and in the facts table and the
files that hold what was read."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update

from app.mail import review_assigned_email
from app.models import Document, Notification, User
from app.reviews import BAD_CURSOR, NOBODY_OUT_OF_REVIEW, NOT_A_REVIEWER
from tests.test_access import access
from tests.test_api import create_project
from tests.test_audit import actions, read_csv
from tests.test_exports import archive_of, ask, run_export
from tests.test_fact_export import export
from tests.test_fact_table import invoice, table
from tests.test_notices import told
from tests.test_organizations import set_role
from tests.test_templates import turn_on
from tests.test_trash import restore


@pytest.fixture
def desk(client, session, account, make_account, monkeypatch) -> SimpleNamespace:
    """The local owner's organisation on a plan with room for its members, with the feature on:
    an admin, two members and a viewer, a project of three invoices, and a member of another
    organisation."""
    turn_on(monkeypatch)
    org = account.organization
    org.plan = "enterprise"
    session.commit()
    project = create_project(client, "Payables")["id"]
    return SimpleNamespace(
        org_id=org.id,
        owner=account,
        ann=make_account("ann@example.com", org, role="admin"),
        bob=make_account("bob@example.com", org, role="member"),
        cat=make_account("cat@example.com", org, role="member"),
        vic=make_account("vic@example.com", org, role="viewer"),
        eve=make_account("eve@globex.example"),
        project=project,
        first=invoice(session, project, "inv-1.pdf", invoice_number="INV-1"),
        second=invoice(session, project, "inv-2.pdf", invoice_number="INV-2"),
        third=invoice(session, project, "inv-3.pdf", invoice_number="INV-3"),
    )


def review(client, document_id: int, **change):
    return client.put(f"/api/documents/{document_id}/review", json=change)


def reviewed(client, document_id: int, **change) -> dict:
    response = review(client, document_id, **change)
    assert response.status_code == 200, response.text
    return response.json()


def reviews(client, project_id: int, **params) -> dict:
    response = client.get(f"/api/projects/{project_id}/reviews", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def listed(page: dict) -> list[str]:
    return [document["filename"] for document in page["documents"]]


def standing(document: dict) -> tuple:
    return (document["review"], document["assignee"], document["review_changer"])


def review_rows(session, org_id: int) -> list[tuple[str, dict]]:
    return [
        (action, detail)
        for action, detail in actions(session, org_id)
        if action in ("document_review_changed", "document_assigned")
    ]


def test_a_member_puts_a_document_in_review_assigns_it_and_another_approves_it(
    client, session, mailer, desk, act_as
):
    put = reviewed(client, desk.first, state="to_review")
    assert standing(put) == ("to_review", None, "Owner")
    assert put["assignee_id"] is None
    assert datetime.fromisoformat(put["review_changed_at"]) > datetime.now(UTC) - timedelta(
        minutes=1
    )

    assigned = reviewed(client, desk.first, assignee_id=desk.bob.user.id)
    assert standing(assigned) == ("to_review", "Bob", "Owner")
    assert assigned["assignee_id"] == desk.bob.user.id
    # Being assigned by another is news, which leads to the document.
    [notice] = session.scalars(select(Notification)).all()
    assert (notice.user_id, notice.kind, notice.link) == (
        desk.bob.user.id,
        "review_assigned",
        f"/projects/{desk.project}/documents/{desk.first}",
    )
    assert notice.title == "A document was assigned to you"
    assert notice.body == "Owner assigned you inv-1.pdf in Payables in Local to review."
    [mail] = told(mailer, "bob@example.com")
    assert f"/projects/{desk.project}/documents/{desk.first}" in (mail.text)

    act_as(desk.bob)
    approved = reviewed(client, desk.first, state="approved")
    assert standing(approved) == ("approved", "Bob", "Bob")
    # Assigning oneself tells nobody.
    reviewed(client, desk.second, assignee_id=desk.bob.user.id)
    assert len(session.scalars(select(Notification)).all()) == 1

    # By the document's name, the states and the assignee's id, never a value it states.
    assert review_rows(session, desk.org_id) == [
        ("document_review_changed", {"filename": "inv-1.pdf", "from": None, "to": "to_review"}),
        ("document_assigned", {"filename": "inv-1.pdf", "from": None, "to": desk.bob.user.id}),
        (
            "document_review_changed",
            {"filename": "inv-1.pdf", "from": "to_review", "to": "approved"},
        ),
        ("document_review_changed", {"filename": "inv-2.pdf", "from": None, "to": "to_review"}),
        ("document_assigned", {"filename": "inv-2.pdf", "from": None, "to": desk.bob.user.id}),
    ]
    # The document as the library lists it carries its review.
    shown = client.get(f"/api/documents/{desk.first}").json()
    assert standing(shown) == ("approved", "Bob", "Bob")


def test_assigning_puts_a_document_in_review_and_taking_it_out_forgets_its_assignee(
    client, session, desk
):
    assigned = reviewed(client, desk.first, assignee_id=desk.cat.user.id)
    assert standing(assigned) == ("to_review", "Cat", "Owner")
    disputed = reviewed(client, desk.first, state="disputed")
    assert standing(disputed) == ("disputed", "Cat", "Owner")
    # Nothing changed, nothing recorded.
    before = len(review_rows(session, desk.org_id))
    reviewed(client, desk.first, state="disputed", assignee_id=desk.cat.user.id)
    assert len(review_rows(session, desk.org_id)) == before

    refused = review(client, desk.first, state=None, assignee_id=desk.cat.user.id)
    assert (refused.status_code, refused.json()["detail"]) == (400, NOBODY_OUT_OF_REVIEW)
    out = reviewed(client, desk.first, state=None)
    assert standing(out) == (None, None, None)
    assert (out["assignee_id"], out["review_changed_at"]) == (None, None)
    assert review_rows(session, desk.org_id)[-2:] == [
        ("document_review_changed", {"filename": "inv-1.pdf", "from": "disputed", "to": None}),
        ("document_assigned", {"filename": "inv-1.pdf", "from": desk.cat.user.id, "to": None}),
    ]
    # Unassigning keeps the review.
    reviewed(client, desk.second, assignee_id=desk.cat.user.id)
    assert standing(reviewed(client, desk.second, assignee_id=None)) == ("to_review", None, "Owner")


def test_a_review_is_assigned_to_a_member_who_sees_the_project_and_may_change_it(
    client, session, desk, act_as
):
    names = client.get(f"/api/projects/{desk.project}/reviewers").json()
    assert [reviewer["name"] for reviewer in names] == ["Ann", "Bob", "Cat", "Owner"]
    for who in (desk.vic, desk.eve):
        refused = review(client, desk.first, assignee_id=who.user.id)
        assert (refused.status_code, refused.json()["detail"]) == (400, NOT_A_REVIEWER)
    refused = review(client, desk.first, assignee_id=10_000)
    assert refused.status_code == 400

    # A private project's: its chosen members and the owners and admins, who see every project.
    assert access(client, desk.project, "private", desk.bob).status_code == 200
    names = client.get(f"/api/projects/{desk.project}/reviewers").json()
    assert [reviewer["name"] for reviewer in names] == ["Ann", "Bob", "Owner"]
    assert review(client, desk.first, assignee_id=desk.cat.user.id).status_code == 400
    assert review(client, desk.first, assignee_id=desk.ann.user.id).status_code == 200

    # A member since made a viewer keeps the assignment, and the review still moves.
    reviewed(client, desk.second, assignee_id=desk.bob.user.id)
    set_role(session, desk.org_id, desk.bob.user.id, "viewer")
    assert standing(reviewed(client, desk.second, state="approved")) == ("approved", "Bob", "Owner")

    # A viewer reads where each stands, and moves none.
    access(client, desk.project, "organization")
    act_as(desk.vic)
    assert listed(reviews(client, desk.project, state="approved")) == ["inv-2.pdf"]
    assert review(client, desk.third, state="approved").status_code == 403


def test_the_reviews_list_a_state_oldest_first_and_count_each(client, session, desk, act_as):
    for document_id in (desk.first, desk.second, desk.third):
        reviewed(client, document_id, state="to_review")
    reviewed(client, desk.second, assignee_id=desk.bob.user.id)
    # The oldest first: inv-3 has waited longest.
    now = datetime.now(UTC)
    for document_id, hours in ((desk.first, 1), (desk.second, 2), (desk.third, 3)):
        session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(review_changed_at=now - timedelta(hours=hours))
        )
    other = create_project(client, "Other")["id"]
    elsewhere = invoice(session, other, "elsewhere.pdf")
    session.commit()
    reviewed(client, elsewhere, state="to_review")

    page = reviews(client, desk.project)
    assert listed(page) == ["inv-3.pdf", "inv-2.pdf", "inv-1.pdf"]
    assert page["counts"] == {"to_review": 3, "approved": 0, "disputed": 0}
    assert page["next_after"] is None
    assert listed(reviews(client, desk.project, mine="true")) == []

    # A page at a time, by the cursor each gives.
    first = reviews(client, desk.project, limit=2)
    assert listed(first) == ["inv-3.pdf", "inv-2.pdf"]
    rest = reviews(client, desk.project, limit=2, after=first["next_after"])
    assert (listed(rest), rest["next_after"]) == (["inv-1.pdf"], None)
    bad = client.get(f"/api/projects/{desk.project}/reviews", params={"after": "nonsense"})
    assert (bad.status_code, bad.json()["detail"]) == (422, BAD_CURSOR)

    # An approved one is done, and the newest comes first.
    reviewed(client, desk.third, state="approved")
    reviewed(client, desk.first, state="approved")
    approved = reviews(client, desk.project, state="approved")
    assert listed(approved) == ["inv-1.pdf", "inv-3.pdf"]
    assert approved["counts"] == {"to_review": 1, "approved": 2, "disputed": 0}

    act_as(desk.bob)
    mine = reviews(client, desk.project, mine="true")
    assert listed(mine) == ["inv-2.pdf"]
    assert mine["counts"] == {"to_review": 1, "approved": 0, "disputed": 0}


def test_a_document_in_the_trash_keeps_its_review_and_comes_back_with_it(client, desk):
    reviewed(client, desk.first, state="disputed", assignee_id=desk.bob.user.id)
    assert client.delete(f"/api/documents/{desk.first}").status_code == 204
    assert reviews(client, desk.project, state="disputed")["counts"]["disputed"] == 0
    assert review(client, desk.first, state="approved").status_code == 404
    assert restore(client, "documents", desk.first).status_code == 200
    page = reviews(client, desk.project, state="disputed")
    assert listed(page) == ["inv-1.pdf"]
    assert standing(page["documents"][0]) == ("disputed", "Bob", "Owner")


def test_a_member_who_leaves_is_assigned_nothing_and_a_purged_account_leaves_no_name(
    client, session, desk
):
    reviewed(client, desk.first, assignee_id=desk.bob.user.id)
    reviewed(client, desk.second, assignee_id=desk.cat.user.id)
    assert client.delete(f"/api/organization/members/{desk.bob.user.id}").status_code == 204
    assert standing(client.get(f"/api/documents/{desk.first}").json()) == (
        "to_review",
        None,
        "Owner",
    )

    # Who moved it goes with their account, the review staying.
    session.execute(
        update(Document)
        .where(Document.id == desk.second)
        .values(review_changed_by=desk.cat.user.id)
    )
    session.execute(delete(User).where(User.id == desk.cat.user.id))
    session.commit()
    shown = client.get(f"/api/documents/{desk.second}").json()
    assert standing(shown) == ("to_review", None, None)


def test_the_facts_table_and_its_file_show_each_document_s_review(client, desk):
    reviewed(client, desk.first, state="approved", assignee_id=desk.bob.user.id)
    reviewed(client, desk.second, state="disputed")
    rows = {row["filename"]: row for row in table(client, desk.project)["rows"]}
    assert (rows["inv-1.pdf"]["review"], rows["inv-1.pdf"]["assignee"]) == ("approved", "Bob")
    assert (rows["inv-2.pdf"]["review"], rows["inv-2.pdf"]["assignee"]) == ("disputed", None)
    assert rows["inv-3.pdf"]["review"] is None

    written = {row["Document"]: row["Review"] for row in read_csv(export(client, desk.project))}
    assert written == {"inv-1.pdf": "Approved", "inv-2.pdf": "Disputed", "inv-3.pdf": ""}


def test_a_project_s_export_names_each_document_s_review(client, services, desk):
    reviewed(client, desk.first, state="approved", assignee_id=desk.bob.user.id)
    made = ask(client, desk.project).json()
    run_export(services)
    written = json.loads(archive_of(client, desk.project, made["id"]).read("project.json"))
    by_name = {document["filename"]: document for document in written["documents"]}
    assert (by_name["inv-1.pdf"]["review"], by_name["inv-1.pdf"]["assignee"]) == ("approved", "Bob")
    assert by_name["inv-1.pdf"]["review_changed_at"] is not None
    assert by_name["inv-2.pdf"]["review"] is None


def test_reviews_are_hidden_while_the_flag_is_off(client, session):
    project = create_project(client)["id"]
    document = invoice(session, project, "inv.pdf")
    assert client.get(f"/api/projects/{project}/reviews").status_code == 404
    assert client.get(f"/api/projects/{project}/reviewers").status_code == 404
    assert review(client, document, state="approved").status_code == 404


def test_the_notice_names_who_assigned_which_document_and_where_it_stands():
    words = {
        state: review_assigned_email(
            "Bob", "Ada", "inv-1.pdf", state, "Payables", "Acme", "https://emudoc.test/x"
        )
        for state in ("to_review", "approved", "disputed")
    }
    where = "Ada assigned you inv-1.pdf in Payables in Acme"
    assert words["to_review"].brief == f"{where} to review."
    assert words["approved"].brief == f"{where}, which is approved."
    assert words["disputed"].brief == f"{where}, which is disputed."
    # The subject, which the log repeats, names nobody and nothing.
    assert {content.subject for content in words.values()} == {"A document was assigned to you"}
