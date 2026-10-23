"""An organisation's settings: the language its documents are searched in, which its new
passages are indexed with, its old ones are moved to by a job and its answers fall back to; how
long it keeps a chat when that is less than its plan does, which wins over the plan and a hold
alike; and how its members are told each kind of notice until they choose. Owners and admins
change them, and each change is a row of the audit log."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import select, text, update

from app.api.organizations import ADMINS_ONLY, UNKNOWN_LANGUAGE, longer_than_plan
from app.billing import change_plan, plan_change
from app.entitlements import PLANS
from app.indexing.pipeline import process_document
from app.jobs import APPLY_LANGUAGE, Lease, LeaseLost, claim
from app.languages import apply_language
from app.maintenance import housekeeping
from app.models import Job, Notification, Organization, Passage, Project, User
from app.notices import tell
from app.retention import Kept, hold_in_force, kept_for
from app.worker import Worker
from tests.conftest import FakeTurn
from tests.test_agent import run
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_auth import me, signup
from tests.test_notices import told
from tests.test_notifications import APP, news
from tests.test_organizations import switch, team  # noqa: F401
from tests.test_retention import chat_titles, make_chat

GERMAN = b"Die Lieferungen erfolgen jede Woche und werden quittiert."


def change(client, **body) -> dict:
    response = client.patch("/api/organization", json=body)
    assert response.status_code == 200, response.text
    return response.json()["organization"]


def settings_changed(session, org_id: int) -> list[dict]:
    return [detail for action, detail in actions(session, org_id) if action == "settings_changed"]


def matches(session, word: str) -> int:
    """How many passages a keyword search for the word finds, each by its own language."""
    return session.scalar(
        text("SELECT count(*) FROM passages WHERE tsv @@ plainto_tsquery(language, :word)"),
        {"word": word},
    )


def languages(session) -> set[str]:
    return set(session.scalars(select(text("CAST(language AS text)")).select_from(Passage)))


def text_upload(client, services, project_id: int, name: str, content: bytes) -> int:
    response = client.post(
        f"/api/projects/{project_id}/documents", files=[("files", (name, content))]
    )
    assert response.status_code == 201, response.text
    (document,) = response.json()["documents"]
    process_document(document["id"], services)
    return document["id"]


def run_language(services) -> Job:
    """Run the next language job as the worker does."""
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [APPLY_LANGUAGE])
    assert job is not None
    worker._run(job)
    return job


# Who changes them, and the audit log


def test_owners_and_admins_change_the_settings_and_each_change_is_audited(
    client,
    session,
    team,  # noqa: F811
):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]

    organization = change(
        client,
        language="german",
        chat_days=90,
        notices=[{"kind": "quota", "email": False}],
    )
    assert (organization["language"], organization["chat_days"]) == ("german", 90)
    # Team keeps a chat for good; the organisation keeps one 90 days.
    assert organization["allowance"]["retention_days"] is None
    assert organization["chat_retention_days"] == 90
    assert organization["notice_defaults"] == [
        {"kind": "upload_indexed", "app": True, "email": True},
        {"kind": "quota", "app": True, "email": False},
        {"kind": "project_export", "app": True, "email": True},
        {"kind": "checks_run", "app": True, "email": True},
    ]
    assert settings_changed(session, org_id) == [
        {"setting": "language", "from": None, "to": "german"},
        {"setting": "chat_days", "from": None, "to": 90},
        {"setting": "notices", "kind": "quota", "way": "email", "from": True, "to": False},
    ]
    # The same again changes nothing, and writes nothing.
    change(client, language="german", chat_days=90, notices=[{"kind": "quota", "email": False}])
    assert len(settings_changed(session, org_id)) == 3

    # A member reads them and changes none.
    assert switch(bob_client, org_id)["organization"]["chat_days"] == 90
    refused = bob_client.patch("/api/organization", json={"chat_days": 30})
    assert (refused.status_code, refused.json()["detail"]) == (403, ADMINS_ONLY)
    refused = bob_client.patch("/api/organization", json={"name": "Bob's now"})
    assert refused.status_code == 403

    # A null goes back to the deployment's language and the plan's days.
    organization = change(client, language=None, chat_days=None)
    assert (organization["language"], organization["chat_days"]) == (None, None)
    assert organization["chat_retention_days"] is None
    assert settings_changed(session, org_id)[-2:] == [
        {"setting": "language", "from": "german", "to": None},
        {"setting": "chat_days", "from": 90, "to": None},
    ]
    # Renaming is audited as it was.
    change(client, name="Ada & Co")
    assert actions(session, org_id)[-1] == (
        "organization_renamed",
        {"from": "Ada's workspace", "to": "Ada & Co"},
    )


def test_the_languages_offered_are_the_databases_and_another_is_refused(client):
    offered = client.get("/api/organization/languages").json()
    assert {"english", "german", "simple"} <= set(offered["languages"])
    assert offered["default"] == "english"

    refused = client.patch("/api/organization", json={"language": "klingon"})
    assert (refused.status_code, refused.json()["detail"]) == (400, UNKNOWN_LANGUAGE)
    # Not a configuration's name at all.
    assert client.patch("/api/organization", json={"language": "German"}).status_code == 422


# The language


def test_a_new_language_moves_the_passages_indexed_before_and_new_ones_take_it(
    client, session, services, account
):
    project = create_project(client)
    text_upload(client, services, project["id"], "notes.txt", GERMAN)
    # English stems neither word to the other's stem.
    assert languages(session) == {"english"}
    assert matches(session, "Lieferung") == 0

    change(client, language="german")
    # Twice before the job runs: one job, which reads the language as it runs.
    change(client, language="simple")
    change(client, language="german")
    (job,) = session.scalars(select(Job).where(Job.kind == APPLY_LANGUAGE))
    assert (job.org_id, job.status) == (account.organization.id, "queued")

    run_language(services)
    session.expire_all()
    assert session.get(Job, job.id).status == "done"
    assert languages(session) == {"german"}
    assert matches(session, "Lieferung") == 1

    # What is indexed from now on is indexed in it.
    text_upload(client, services, project["id"], "woche.txt", b"Die Lieferungen der Woche.")
    assert languages(session) == {"german"}
    assert matches(session, "Lieferung") == 2

    # Going back to the deployment's language moves them back; naming it changes nothing.
    change(client, language=None)
    run_language(services)
    assert languages(session) == {"english"}
    change(client, language="english")
    waiting = select(Job).where(Job.kind == APPLY_LANGUAGE, Job.status == "queued")
    assert session.scalar(waiting) is None


def test_the_move_goes_a_batch_at_a_time_and_stops_when_its_lease_is_lost(
    client, session, services, account, make_account, make_document, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.languages.BATCH", 1)
    project = create_project(client)
    text_upload(client, services, project["id"], "a.txt", GERMAN)
    text_upload(client, services, project["id"], "b.txt", b"Jede Woche eine Lieferung.")
    # Another organisation's passages are its own.
    elsewhere = Project(org_id=make_account("ann@globex.example").organization.id, name="Globex")
    session.add(elsewhere)
    session.flush()
    (tmp_path / "theirs.txt").write_bytes(GERMAN)
    process_document(make_document(tmp_path / "theirs.txt", project=elsewhere).id, services)
    change(client, language="german")
    job = session.scalar(select(Job).where(Job.kind == APPLY_LANGUAGE))

    # Another worker took the job over: this run writes nothing.
    held = {"status": "running", "worker_id": "another"}
    session.execute(update(Job).where(Job.id == job.id).values(**held))
    session.commit()
    with pytest.raises(LeaseLost):
        apply_language(account.organization.id, Lease(job.id, "this one"))
    assert languages(session) == {"english"}

    assert apply_language(account.organization.id, Lease(job.id, "another")) == 2
    by_project = session.execute(
        select(Passage.project_id, text("CAST(language AS text)")).distinct()
    ).all()
    assert set(by_project) == {(project["id"], "german"), (elsewhere.id, "english")}


def test_the_agent_is_told_the_language_the_organisation_chose(session, services, indexed):
    organization = session.get(Project, indexed.project_id).organization
    prompts = services.llm.agent_prompts

    for language in (None, "simple", "german"):
        organization.language = language
        session.commit()
        services.llm.agent_turns = [FakeTurn(TextPart("Neunzig Tage."))]
        run(services, indexed)
    unchosen, words_as_written, german = prompts
    line = "The organisation works in German: answer in the language of the question"
    assert line not in unchosen and line not in words_as_written
    assert line in german


# How long a chat is kept


def test_a_choice_longer_than_the_plan_keeps_a_chat_is_refused(client, signed_in_only):
    organization = signup(client, "ada@example.com")["organization"]
    days = PLANS["free"].retention_days
    assert organization["allowance"]["retention_days"] == days

    refused = client.patch("/api/organization", json={"chat_days": days + 1})
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        longer_than_plan("free", days),
    )
    assert change(client, chat_days=days)["chat_retention_days"] == days
    assert change(client, chat_days=7)["chat_retention_days"] == 7
    assert client.patch("/api/organization", json={"chat_days": 0}).status_code == 422


def test_the_organisations_choice_takes_older_chats_whatever_its_plan_or_a_hold_keeps(
    session, services, make_account
):
    ann = make_account("ann@globex.example")
    organization = ann.organization
    change_plan(session, organization, "team", seats=3)
    organization.chat_days = 60
    session.commit()
    project = Project(org_id=organization.id, name="contracts")
    session.add(project)
    session.commit()
    user = session.get(User, ann.user.id)
    for title, days in (("a hundred days", 100), ("forty-five days", 45), ("lately", 5)):
        make_chat(session, project, user, title, days)

    # Free keeps a chat 30 days, and holds what Team kept for a while: the choice still takes
    # what is older than 60 days.
    change_plan(session, organization, "free", because="subscription_ended")
    session.commit()
    housekeeping(services.storage)
    assert chat_titles(session) == {"forty-five days", "lately"}

    # The hold keeps for good what the choice keeps 60 days: the figures say 60.
    assert kept_for(organization) == Kept(60, 365)
    assert hold_in_force(organization).kept == Kept(60, 365)
    # Once the hold is over, Free's 30 days take what is older.
    organization.retention_held_until = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    housekeeping(services.storage)
    assert chat_titles(session) == {"lately"}
    assert kept_for(organization) == Kept(30, 90)


def test_what_a_lower_plan_would_keep_is_said_with_the_choice_applied(session, make_account):
    organization = make_account("ann@globex.example").organization
    change_plan(session, organization, "team", seats=3)
    organization.chat_days = 7
    session.commit()

    # Free's 30 days keep more than the 7 chosen: a move there holds audit log entries alone.
    change = plan_change(session, organization, "free")
    assert change.kept == Kept(7, 90)
    assert change.held.kept == Kept(7, 365)
    # Team keeps chats for good, but the choice keeps them a week: a hold keeps none longer.
    change_plan(session, organization, "free")
    assert hold_in_force(organization).kept == Kept(7, 365)
    organization.chat_days = None
    assert hold_in_force(organization).kept == Kept(None, 365)


# How members are told


def test_members_are_told_as_the_organisation_says_until_they_choose(
    client, session, mailer, make_account, act_as
):
    ann = make_account("ann@globex.example")
    act_as(ann)
    change(client, notices=[{"kind": "quota", "app": False, "email": False}])
    session.expire_all()
    organization = session.get(Organization, ann.organization.id)
    user = session.get(User, ann.user.id)

    assert not tell(session, user, organization.id, "quota", news(), "/organization/usage", APP)
    session.commit()
    assert session.scalar(select(Notification.id)) is None and told(mailer) == []
    # The account page shows what the organisation says, where the member did not choose.
    quota = next(choice for choice in me(client).json()["notices"] if choice["kind"] == "quota")
    assert quota == {"kind": "quota", "app": False, "email": False}

    # The member's own choice wins over it.
    chosen = {"notices": [{"kind": "quota", "email": True}]}
    response = client.patch("/api/auth/profile", json=chosen)
    assert response.status_code == 200, response.text
    session.expire_all()
    assert user.notices == {"quota": {"email": True}}
    assert tell(session, user, organization.id, "quota", news(), "/organization/usage", APP)
    session.commit()
    assert session.scalar(select(Notification.id)) is None and len(told(mailer)) == 1
    # Another kind is told as before.
    assert tell(session, user, organization.id, "upload_indexed", news(), "/projects/1", APP)
