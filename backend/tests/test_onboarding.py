"""The first steps the projects page lists for an organisation, read from what it has done, and
the account's own choices about the list and the tour (app/onboarding.py,
app/api/onboarding.py)."""

from dataclasses import replace

import pytest

from app.config import get_settings
from app.entitlements import PLANS
from app.models import Chat, ChatMessage, Project, User
from tests.test_api import upload
from tests.test_auth import signup
from tests.test_chats import ask, new_chat
from tests.test_invitations import invite
from tests.test_organizations import join


@pytest.fixture
def ada(client, signed_in_only, monkeypatch) -> dict:
    """Ada, signed in on `client`, whose organisation started with the sample project as a
    deployment's do, with room on its plan for a team."""
    monkeypatch.setattr(get_settings(), "sample_project", True)
    monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], members=10))
    return signup(client, "ada@acme.com", name="Ada")


def steps(client) -> dict[str, bool]:
    response = client.get("/api/organization/onboarding")
    assert response.status_code == 200, response.text
    return {step["key"]: step["done"] for step in response.json()["steps"]}


def projects(client) -> list[dict]:
    return client.get("/api/projects").json()


def onboarding(client, **choices) -> dict:
    response = client.patch("/api/auth/onboarding", json=choices)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_new_organisation_has_taken_none_of_its_steps(client, ada):
    # The sample holds documents, which are Emudoc's, not the organisation's own.
    assert [project["sample"] for project in projects(client)] == [True]
    assert projects(client)[0]["document_count"] > 0
    assert list(steps(client).items()) == [("upload", False), ("ask", False), ("invite", False)]


def test_each_step_is_taken_by_what_it_names(client, ada, sample_pdf):
    mine = client.post("/api/projects", json={"name": "Contracts"}).json()
    assert steps(client)["upload"] is False
    upload(client, mine["id"], sample_pdf)
    assert steps(client) == {"upload": True, "ask": False, "invite": False}

    # A question asked in the sample counts: asking it is what the sample is for.
    sample = next(project for project in projects(client) if project["sample"])
    ask(client, new_chat(client, sample["id"])["id"], "Does the invoice apply the discount?")
    assert steps(client) == {"upload": True, "ask": True, "invite": False}

    invite(client, "bob@example.com")
    assert steps(client) == {"upload": True, "ask": True, "invite": True}


def test_a_member_who_joins_a_team_finds_its_steps_taken(client, session, ada, make_account):
    bob = make_account("bob@example.com")
    join(session, ada["organization"]["id"], bob.user.id)
    # Another member is a team as much as an invitation is.
    assert steps(client)["invite"] is True


def test_each_role_is_offered_the_steps_it_may_take(client, session, make_account, act_as):
    owner = make_account("ann@acme.com")
    admin = make_account("cat@acme.com", owner.organization, "admin")
    member = make_account("bob@acme.com", owner.organization, "member")
    viewer = make_account("vic@acme.com", owner.organization, "viewer")
    offered = {}
    for account in (owner, admin, member, viewer):
        act_as(account)
        offered[account.role] = list(steps(client))
    assert offered == {
        "owner": ["upload", "ask", "invite"],
        "admin": ["upload", "ask", "invite"],
        # Only owners and admins invite, and a viewer can only ask, which an empty chat offers.
        "member": ["upload", "ask"],
        "viewer": [],
    }


def test_what_another_organisation_did_takes_no_step(
    client, session, make_account, act_as, make_document, sample_pdf
):
    acme = make_account("ann@acme.com")
    globex = make_account("gus@globex.com")
    project = Project(org_id=acme.organization.id, name="Contracts")
    session.add(project)
    session.flush()
    make_document(sample_pdf, project=project)
    chat = Chat(project_id=project.id, user_id=acme.user.id, title="Notice")
    session.add(chat)
    session.flush()
    session.add(ChatMessage(chat_id=chat.id, role="user", content="How much notice?"))
    session.commit()

    act_as(globex)
    assert steps(client) == {"upload": False, "ask": False, "invite": False}
    act_as(acme)
    assert steps(client) == {"upload": True, "ask": True, "invite": False}


def test_an_answer_alone_is_no_question(client, session, make_account, act_as):
    acme = make_account("ann@acme.com")
    project = Project(org_id=acme.organization.id, name="Contracts")
    session.add(project)
    session.flush()
    chat = Chat(project_id=project.id, user_id=acme.user.id, title="")
    session.add(chat)
    session.flush()
    session.add(ChatMessage(chat_id=chat.id, role="assistant", content="", status="failed"))
    session.commit()
    act_as(acme)
    assert steps(client)["ask"] is False


def test_hiding_the_list_and_seeing_the_tour_are_the_accounts_own(client, session, ada):
    me = client.get("/api/auth/me").json()
    assert (me["checklist_hidden"], me["tour_seen"]) == (False, False)

    seen = onboarding(client, tour_seen=True)
    assert (seen["checklist_hidden"], seen["tour_seen"]) == (False, True)
    user = session.get(User, ada["id"])
    session.refresh(user)
    first = user.tour_seen_at
    assert first is not None
    # Saying it again keeps the moment it was first said.
    onboarding(client, tour_seen=True)
    session.refresh(user)
    assert user.tour_seen_at == first

    hidden = onboarding(client, checklist_hidden=True)
    assert (hidden["checklist_hidden"], hidden["tour_seen"]) == (True, True)
    # The steps are still the organisation's, for a list shown again.
    assert steps(client) == {"upload": False, "ask": False, "invite": False}

    shown = onboarding(client, checklist_hidden=False)
    assert (shown["checklist_hidden"], shown["tour_seen"]) == (False, True)
    session.refresh(user)
    assert user.checklist_hidden_at is None

    exported = client.get("/api/auth/account/export").json()["account"]
    assert exported["checklist_hidden_at"] is None
    assert exported["tour_seen_at"] is not None


def test_the_steps_and_the_choices_are_a_signed_in_accounts(client, signed_in_only):
    assert client.get("/api/organization/onboarding").status_code == 401
    assert client.patch("/api/auth/onboarding", json={"tour_seen": True}).status_code == 401


def test_hiding_the_suggestion_to_invite_the_team_is_the_accounts_own(client, session, ada):
    assert client.get("/api/auth/me").json()["invite_hidden"] is False

    hidden = onboarding(client, invite_hidden=True)
    assert (hidden["invite_hidden"], hidden["checklist_hidden"], hidden["tour_seen"]) == (
        True,
        False,
        False,
    )
    user = session.get(User, ada["id"])
    session.refresh(user)
    first = user.invite_hidden_at
    assert first is not None
    onboarding(client, invite_hidden=True)
    session.refresh(user)
    assert user.invite_hidden_at == first
    # The step is still the organisation's, which the list goes on offering.
    assert steps(client)["invite"] is False
    assert client.get("/api/auth/account/export").json()["account"]["invite_hidden_at"]

    shown = onboarding(client, invite_hidden=False)
    assert shown["invite_hidden"] is False
    session.refresh(user)
    assert user.invite_hidden_at is None
