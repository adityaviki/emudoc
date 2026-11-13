"""Who may make an account (app/signups.py): the install's admin at its setup, while it has no
account; afterwards whoever is invited, and anyone while SIGNUP_OPEN, who joins the workspace."""

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import engine
from app.legal import TERMS_VERSION
from app.maintenance import main
from app.models import AuditEvent, Organization, User
from app.signups import NOT_SET_UP, SET_UP_ALREADY, SETUP_LOCK, SIGNUP_CLOSED
from tests.test_auth import (
    NEW_PASSWORD,
    PASSWORD,
    another_browser,
    finish_signup,
    login,
    me,
    sign_up_link,
    signup,
)


@pytest.fixture
def opened(monkeypatch, signed_in_only):
    """Sign-up open, as an install that lets anyone who reaches it in."""
    monkeypatch.setattr(get_settings(), "signup_open", True)


def set_up(client, email="ada@example.com", workspace="Acme", **fields):
    """Fill in the setup page as the install's admin does, with the box ticked."""
    body = {
        "email": email,
        "name": "Ada",
        "password": PASSWORD,
        "workspace": workspace,
        "terms_version": TERMS_VERSION,
        **fields,
    }
    return client.post("/api/auth/setup", json=body)


def options(client) -> dict:
    return client.get("/api/auth/options").json()


def reset_with(client, link: str, password: str = NEW_PASSWORD):
    token = parse_qs(urlsplit(link).fragment)["token"][0]
    return client.post("/api/auth/password/reset", json={"token": token, "password": password})


# The setup


def test_a_new_install_is_set_up_by_its_admin(client, session, signed_in_only, monkeypatch):
    monkeypatch.setattr(get_settings(), "sample_project", True)
    # Every page sends to the setup while the install has no account.
    assert options(client)["setup_needed"] is True
    assert me(client).status_code == 401

    made = set_up(client, "Ada@Example.com", "  Acme Legal  ", time_zone="Europe/Berlin")
    assert made.status_code == 201, made.text
    account = made.json()
    # The workspace is named as typed, and its slug follows; the admin owns it, signed in, the
    # address confirmed and the terms accepted.
    assert account["email"] == "ada@example.com" and account["name"] == "Ada"
    assert account["role"] == "owner"
    assert (account["organization"]["name"], account["organization"]["slug"]) == (
        "Acme Legal",
        "acme-legal",
    )
    assert account["email_verified"] is True and account["terms_due"] is False
    assert me(client).json()["id"] == account["id"]
    assert session.scalars(select(User.time_zone)).one() == "Europe/Berlin"
    # With the sample project, whose copy is the app's act in the log.
    projects = client.get("/api/projects").json()
    assert [project["sample"] for project in projects] == [True]
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    assert [row.action for row in rows] == [
        "sign_up",
        "organization_created",
        "terms_accepted",
        "sample_added",
    ]
    assert rows[1].detail == {"name": "Acme Legal"} and rows[1].user_id == account["id"]

    # Nothing sends to the setup now, and the admin signs in with the password.
    assert options(client)["setup_needed"] is False
    login(another_browser(client), "ada@example.com")


def test_the_setup_is_refused_once_any_account_exists(client, session, signed_in_only):
    assert set_up(client).status_code == 201
    for browser in (client, another_browser(client)):
        refused = set_up(browser, "eve@example.com", "Eve's")
        assert (refused.status_code, refused.json()["detail"]) == (409, SET_UP_ALREADY)
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert session.scalar(select(func.count()).select_from(Organization)) == 1


def test_two_setups_at_once_make_one_admin(client, session, signed_in_only):
    """Both pass the look for an account before either makes one; the lock lets one through,
    and the other then finds the account."""
    key = {"key": SETUP_LOCK}
    waiting = text(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
        "AND objid = :key AND database = (SELECT oid FROM pg_database "
        "WHERE datname = current_database())"
    )
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), key)
        with ThreadPoolExecutor(2) as pool:
            tries = [
                pool.submit(set_up, another_browser(client), f"{name}@example.com", name)
                for name in ("ada", "bob")
            ]
            deadline = time.monotonic() + 10
            while holder.execute(waiting, key).scalar() < 2:
                assert time.monotonic() < deadline, "the setups never reached the lock"
                time.sleep(0.05)
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), key)
            answers = [attempt.result() for attempt in tries]
    assert sorted(answer.status_code for answer in answers) == [201, 409]
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_the_setup_needs_a_workspace_name_and_the_terms(client, signed_in_only):
    blank = set_up(client, workspace="   ")
    assert blank.status_code == 400 and "Give the workspace a name" in blank.json()["detail"]
    unticked = set_up(client, terms_version=None)
    assert unticked.status_code == 400
    stale = set_up(client, terms_version="2000-01-01")
    assert stale.status_code == 409
    short = set_up(client, password="short")
    assert short.status_code == 422
    # Nothing was made by any of them.
    assert options(client)["setup_needed"] is True


def test_the_create_admin_command_sets_up_the_install_with_a_link_to_its_password(
    client, session, capsys, signed_in_only
):
    main(["create-admin", "Ada@Example.com", "--name", "Ada", "--workspace", "Acme"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(
        "ada@example.com is the admin of Acme, which starts with the sample project. Open this "
        "link to choose the account's password, before "
    )
    # From a shell, the link leads to the frontend's development server, as APP_URL is unset.
    assert out[1].startswith("http://localhost:5173/reset-password#token=")
    # Without a password nothing signs in, until the link sets one.
    refused = client.post(
        "/api/auth/login", json={"email": "ada@example.com", "password": NEW_PASSWORD}
    )
    assert refused.status_code == 401
    assert reset_with(client, out[1]).status_code == 204
    login(client, "ada@example.com", NEW_PASSWORD)
    account = me(client).json()
    assert account["role"] == "owner" and account["organization"]["name"] == "Acme"
    # The command made the workspace, so the row names no member.
    created = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "organization_created")
    ).one()
    assert created.user_id is None and created.target_id == account["id"]

    with pytest.raises(SystemExit, match="This Emudoc is set up already"):
        main(["create-admin", "grace@example.com"])
    with pytest.raises(SystemExit, match="'grace' is not an email address"):
        main(["create-admin", "grace"])


def test_the_reset_password_command_prints_a_link_that_sets_it(
    client, session, capsys, signed_in_only
):
    ada = signup(client)
    main(["reset-password", "ADA@example.com"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Open this link to choose a new password for ada@example.com")
    assert reset_with(another_browser(client), out[1]).status_code == 204
    login(another_browser(client), "ada@example.com", NEW_PASSWORD)
    # The link works once, and the organisation's log keeps that one was made, by nobody there.
    assert reset_with(client, out[1], "another sentence here").status_code == 400
    made = session.scalars(select(AuditEvent).where(AuditEvent.action == "reset_link_made")).one()
    assert (made.org_id, made.user_id, made.target_id) == (
        ada["organization"]["id"],
        None,
        ada["id"],
    )
    with pytest.raises(SystemExit, match="No account has the address eve@example.com"):
        main(["reset-password", "eve@example.com"])


# Sign-up


def test_the_sign_in_pages_are_told_whether_anyone_may_sign_up(client, monkeypatch):
    # Closed as an install starts, when the pages say to ask the team's admin for an invitation;
    # nothing else is offered, no booking page or mail to anyone outside the team.
    assert options(client) == {"providers": [], "signup_open": False, "setup_needed": True}
    monkeypatch.setattr(get_settings(), "signup_open", True)
    assert options(client)["signup_open"] is True


def test_a_closed_sign_up_refuses_every_address_alike(client, mailer, signed_in_only):
    signup(client, "ada@example.com")
    for address in ("grace@example.com", "Ada@Example.com"):
        # The same refusal for an address with an account, so that nobody learns who has one.
        refused = another_browser(client).post("/api/auth/signup", json={"email": address})
        assert (refused.status_code, refused.json()["detail"]) == (403, SIGNUP_CLOSED)
    assert mailer.sent == []
    # The account signs in as before.
    login(another_browser(client), "ada@example.com")


def test_an_open_sign_up_joins_the_workspace_as_a_member(client, session, opened, monkeypatch):
    monkeypatch.setattr(get_settings(), "sample_project", True)
    assert set_up(client).status_code == 201
    workspace = me(client).json()["organization"]
    grace = another_browser(client)
    made = finish_signup(grace, sign_up_link(grace, "grace@example.com"), name="Grace")
    assert made.status_code == 201, made.text
    account = made.json()
    assert account["role"] == "member" and account["organization"]["id"] == workspace["id"]
    # No organisation of her own, and no second sample: she finds the workspace's.
    assert session.scalar(select(func.count()).select_from(Organization)) == 1
    assert [project["sample"] for project in grace.get("/api/projects").json()] == [True]
    events = session.scalars(
        select(AuditEvent.action).where(AuditEvent.user_id == account["id"])
    ).all()
    assert events == ["sign_up", "terms_accepted"]


def test_an_open_sign_up_waits_for_the_setup(client, mailer, opened):
    refused = client.post("/api/auth/signup", json={"email": "grace@example.com"})
    assert (refused.status_code, refused.json()["detail"]) == (409, NOT_SET_UP)
    assert mailer.sent == []


def test_a_sign_up_link_stops_working_once_sign_up_closes(client, monkeypatch, opened):
    assert set_up(client).status_code == 201
    grace = another_browser(client)
    token = sign_up_link(grace, "grace@example.com")
    monkeypatch.setattr(get_settings(), "signup_open", False)
    shown = grace.post("/api/auth/signup/link", json={"token": token})
    assert (shown.status_code, shown.json()["detail"]) == (403, SIGNUP_CLOSED)
    refused = finish_signup(grace, token)
    assert (refused.status_code, refused.json()["detail"]) == (403, SIGNUP_CLOSED)
    # Nothing was used up: opened again, the link works.
    monkeypatch.setattr(get_settings(), "signup_open", True)
    assert finish_signup(grace, token).status_code == 201
