"""Support's view of a member's account (app/support.py): given by the member alone, for one
organisation and a while, read only from the operator's own session, and recorded."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.api.deps import ORGANIZATION_HEADER
from app.auth import CREDENTIAL_HISTORY, prune_credentials
from app.db import engine
from app.indexing.pipeline import process_document
from app.legal import TERMS_VERSION
from app.maintenance import main
from app.models import Membership, Notification, SupportGrant, User, UserSession
from app.support import NO_LEAVE, NOT_IN_A_VIEW, READ_ONLY_VIEW, SUPPORT_SESSION, VIEW_OVER
from tests.test_api import create_project, upload
from tests.test_audit import rows
from tests.test_auth import PASSWORD, me
from tests.test_logs import logged  # noqa: F401 (a fixture)
from tests.test_operator import ELSEWHERE, HERE, browser, operator_on
from tests.test_organizations import team  # noqa: F401  (a fixture)

SUBJECT = "Emudoc's support opened your account"


def let_in(client, days=7) -> dict:
    response = client.put("/api/auth/support-access", json={"days": days})
    assert response.status_code == 200, response.text
    return response.json()


def view(operator, org_id: int, user_id: int):
    return operator.post(f"/api/operator/organizations/{org_id}/members/{user_id}/support")


def support_rows(session, org_id: int, *names: str) -> list[tuple[str, int | None, dict]]:
    return [
        (row.action, row.user_id, row.detail)
        for row in rows(session, org_id)
        if row.action in names or row.action.startswith("support_")
    ]


@pytest.fixture
def crew(request, client, session):
    """Ada, owner of an organisation with a project, signed in on `client`; Bob its member on a
    browser of his own; and Olga, an operator on this machine, who belongs to none of it."""
    ada, bob, bob_client = request.getfixturevalue("team")
    project = create_project(client)
    olga = operator_on(client.app, session)
    return ada, bob, bob_client, project, olga


def test_a_member_lets_support_in_for_a_while_and_takes_it_back(client, session, crew, mailer):
    ada, bob, bob_client, _, _ = crew
    org_id = ada["organization"]["id"]
    assert me(bob_client).json()["support_until"] is None

    shown = let_in(bob_client)["support_until"]
    until = datetime.fromisoformat(shown)
    assert timedelta(days=6, hours=23) < until - datetime.now(UTC) <= timedelta(days=7)
    # Asked again, the time is moved, not added to.
    shorter = datetime.fromisoformat(let_in(bob_client, days=1)["support_until"])
    assert shorter - datetime.now(UTC) <= timedelta(days=1)
    for days in (0, 8):
        assert bob_client.put("/api/auth/support-access", json={"days": days}).status_code == 422
    # It is the member's for the organisation they work in: Ada sees none of his.
    assert me(client).json()["support_until"] is None
    exported = bob_client.get("/api/auth/account/export").json()["organizations"]
    assert [m["support_until"] is not None for m in exported] == [True]

    taken = bob_client.delete("/api/auth/support-access")
    assert taken.status_code == 200 and taken.json()["support_until"] is None
    # Taking back a leave that is gone records nothing.
    assert bob_client.delete("/api/auth/support-access").status_code == 200
    assert [
        (action, user, detail.get("days")) for action, user, detail in support_rows(session, org_id)
    ] == [
        ("support_access_granted", bob["id"], 7),
        ("support_access_granted", bob["id"], 1),
        ("support_access_revoked", bob["id"], None),
    ]
    # Nobody but the member was mailed about it.
    assert not [sent for sent in mailer.sent if sent[1] == SUBJECT]


def test_an_operator_views_the_member_read_only_as_the_member_sees_it(
    client, session, services, crew, mailer, sample_pdf
):
    ada, bob, bob_client, project, olga = crew
    org_id = ada["organization"]["id"]
    # Olga's own organisation, from the operator's sign-up, holds a notice of Bob's too.
    own_org = me(olga).json()["organization"]["id"]
    olga_id = session.scalar(select(User.id).where(User.email == "olga@example.com"))
    pdf = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(pdf["id"], services)
    for org in (org_id, own_org):
        session.add(
            Notification(
                user_id=bob["id"], org_id=org, kind="quota", title="A notice", body="", link="/"
            )
        )
    session.commit()

    # Without his leave there is nothing to open, nor for a member who gave none.
    refused = view(olga, org_id, bob["id"])
    assert (refused.status_code, refused.json()) == (409, {"detail": NO_LEAVE})
    let_in(bob_client)
    assert view(olga, org_id, ada["id"]).json() == {"detail": NO_LEAVE}
    members = olga.get(f"/api/operator/organizations/{org_id}").json()["members"]
    assert [m["support_until"] is not None for m in members] == [False, True]

    opened = view(olga, org_id, bob["id"])
    assert opened.status_code == 200, opened.text
    assert opened.json() | {"ends_at": None} == {
        "org_id": org_id,
        "organization": ada["organization"]["name"],
        "user_id": bob["id"],
        "name": "Bob",
        "email": "bob@example.com",
        "ends_at": None,
    }
    assert olga.get("/api/operator/me").json()["viewing"]["user_id"] == bob["id"]
    (mail,) = [sent for sent in mailer.sent if sent[1] == SUBJECT]
    assert mail[0] == "bob@example.com" and ada["organization"]["name"] in mail[2]

    # Her session is Bob's account in Ada's organisation, and that organisation alone.
    seen = me(olga).json()
    assert (seen["id"], seen["organization"]["id"], seen["role"]) == (bob["id"], org_id, "member")
    ends = datetime.fromisoformat(seen["support_view"]["ends_at"])
    assert ends - datetime.now(UTC) <= SUPPORT_SESSION
    assert [p["id"] for p in olga.get("/api/projects").json()] == [project["id"]]
    notices = olga.get("/api/notifications").json()["notifications"]
    assert len(notices) == 1
    assert me(bob_client).json()["support_view"] is None

    # Nothing changes through it, and the member's data is not support's to download.
    for refused in (
        olga.post("/api/projects", json={"name": "Support's"}),
        olga.delete(f"/api/documents/{pdf['id']}"),
        olga.put("/api/auth/support-access", json={"days": 7}),
        olga.patch("/api/auth/profile", json={"name": "Olga was here"}),
    ):
        assert (refused.status_code, refused.json()) == (403, {"detail": READ_ONLY_VIEW})
    exported = olga.get("/api/auth/account/export")
    assert (exported.status_code, exported.json()) == (403, {"detail": NOT_IN_A_VIEW})

    # A download is recorded as the member's with the operator who made it.
    assert olga.get(f"/api/documents/{pdf['id']}/file").status_code == 200
    assert olga.delete("/api/operator/support").status_code == 204
    assert olga.get("/api/operator/me").json()["viewing"] is None
    assert me(olga).json()["email"] == "olga@example.com"
    assert support_rows(session, org_id, "document_downloaded") == [
        ("support_access_granted", bob["id"], {"days": 7, "until": seen_until(session, bob)}),
        (
            "support_session_started",
            None,
            {"operator": olga_id, "ends_at": ends.isoformat()},
        ),
        ("document_downloaded", bob["id"], {"filename": sample_pdf.name, "support": olga_id}),
        ("support_session_ended", None, {"operator": olga_id}),
    ]
    started = [row for row in rows(session, org_id) if row.action == "support_session_started"]
    assert (started[0].target_type, started[0].target_id) == ("user", bob["id"])


def seen_until(session, bob) -> str:
    return session.scalar(
        select(SupportGrant.expires_at).where(SupportGrant.user_id == bob["id"])
    ).isoformat()


# What ends a view, each returning the browser the operator asks from next.


def withdrawn(session, bob_client, bob, olga, org_id):
    assert bob_client.delete("/api/auth/support-access").status_code == 200
    return olga


def run_out(session, bob_client, bob, olga, org_id):
    session.execute(update(SupportGrant).values(expires_at=datetime.now(UTC)))
    session.commit()
    return olga


def an_hour_gone(session, bob_client, bob, olga, org_id):
    session.execute(
        update(UserSession)
        .where(UserSession.support_ends_at.is_not(None))
        .values(support_ends_at=datetime.now(UTC))
    )
    session.commit()
    return olga


def removed(session, bob_client, bob, olga, org_id):
    session.execute(
        delete(Membership).where(Membership.org_id == org_id, Membership.user_id == bob["id"])
    )
    session.commit()
    return olga


def no_longer_an_operator(session, bob_client, bob, olga, org_id):
    main(["operators", "remove", "olga@example.com"])
    return olga


def from_elsewhere(session, bob_client, bob, olga, org_id):
    elsewhere = browser(olga.app, ELSEWHERE)
    elsewhere.cookies.update(dict(olga.cookies))
    return elsewhere


@pytest.mark.parametrize(
    "cause", [withdrawn, run_out, an_hour_gone, removed, no_longer_an_operator, from_elsewhere]
)
def test_a_view_that_may_no_longer_go_on_ends_and_the_page_starts_again(
    client, session, crew, cause
):
    ada, bob, bob_client, _, olga = crew
    org_id = ada["organization"]["id"]
    let_in(bob_client)
    assert view(olga, org_id, bob["id"]).status_code == 200
    assert olga.get("/api/projects").status_code == 200

    asking = cause(session, bob_client, bob, olga, org_id)

    over = asking.get("/api/projects")
    assert (over.status_code, over.json()) == (409, {"detail": VIEW_OVER})
    # The page starts again at the organisation the session works in, the operator's own.
    own = me(olga).json()
    assert (own["email"], own["support_view"]) == ("olga@example.com", None)
    assert over.headers[ORGANIZATION_HEADER] == str(own["organization"]["id"]) != str(org_id)
    session.expire_all()
    assert not session.scalars(
        select(UserSession).where(UserSession.support_ends_at.is_not(None))
    ).all()


def test_signing_out_ends_the_view(client, session, crew):
    ada, bob, bob_client, _, olga = crew
    org_id = ada["organization"]["id"]
    let_in(bob_client)
    assert view(olga, org_id, bob["id"]).status_code == 200
    assert olga.post("/api/auth/logout").status_code == 204
    assert [action for action, _, _ in support_rows(session, org_id)][-1] == "support_session_ended"
    assert me(olga).status_code == 401


def test_a_view_holds_the_member_to_what_holds_them(client, session, crew):
    ada, bob, bob_client, _, olga = crew
    org_id = ada["organization"]["id"]
    let_in(bob_client)
    assert view(olga, org_id, bob["id"]).status_code == 200
    session.execute(update(User).where(User.id == bob["id"]).values(terms_version="2020-01-01"))
    session.commit()
    seen = me(olga).json()
    assert seen["terms_due"] and seen["terms_version"] != TERMS_VERSION
    assert olga.get("/api/projects").status_code == 403
    # A second view replaces the first, its end recorded.
    assert view(olga, org_id, bob["id"]).status_code == 200
    assert [action for action, _, _ in support_rows(session, org_id)][-3:] == [
        "support_session_started",
        "support_session_ended",
        "support_session_started",
    ]


def test_a_member_on_another_browser_is_never_a_view(client, session, crew):
    """The view is the operator's session alone: the member's own browser, and the operator's
    from elsewhere before any view, are as they were."""
    ada, bob, bob_client, _, olga = crew
    let_in(bob_client)
    assert view(olga, ada["organization"]["id"], bob["id"]).status_code == 200
    assert me(bob_client).json()["support_view"] is None
    assert bob_client.post("/api/projects", json={"name": "Bob's"}).status_code == 201
    other = browser(client.app, HERE)
    other.post("/api/auth/login", json={"email": "olga@example.com", "password": PASSWORD})
    assert me(other).json()["email"] == "olga@example.com"


def test_every_line_a_view_logs_names_the_operator(
    client,
    session,
    services,
    crew,
    logged,  # noqa: F811
):
    ada, bob, bob_client, project, olga = crew
    let_in(bob_client)
    assert view(olga, ada["organization"]["id"], bob["id"]).status_code == 200
    olga_id = session.scalar(select(User.id).where(User.email == "olga@example.com"))
    # A search whose vectors cannot be asked for logs a line about the request.
    services.embedder.fail = True
    found = olga.get(f"/api/projects/{project['id']}/search", params={"q": "invoice"})
    assert found.status_code == 200
    (line,) = [line for line in logged() if line["logger"] == "app.search"]
    assert (line["operator_id"], line["user_id"], line["org_id"]) == (
        olga_id,
        bob["id"],
        ada["organization"]["id"],
    )


def test_a_leave_that_ran_out_is_pruned_with_the_credentials(client, session, crew):
    _, _, bob_client, _, _ = crew
    let_in(bob_client)
    with engine.begin() as connection:
        prune_credentials(connection)
    assert session.scalar(select(SupportGrant.id)) is not None
    session.execute(
        update(SupportGrant).values(
            expires_at=datetime.now(UTC) - CREDENTIAL_HISTORY - timedelta(minutes=1)
        )
    )
    session.commit()
    with engine.begin() as connection:
        prune_credentials(connection)
    assert session.scalar(select(SupportGrant.id)) is None
