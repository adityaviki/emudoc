"""Who may make an account on an install, which holds one team's workspace.

An install starts with no account, and every page sends to the setup (`/setup`, `POST
/api/auth/setup` in app/api/auth.py): it makes the first user, the admin, with their address
confirmed, an owner of the workspace named as they typed it, which starts with the sample
project, and signs them in. `python -m app.maintenance create-admin` does the same from a shell,
for a scripted install. Once any user exists the setup is refused, under a lock (SETUP_LOCK),
so that two browsers at once make one admin.

After that people come in by invitation (app/invitations.py), whose link the owner or admin who
made it is shown as well as mailed, since an install may have no mail relay; for the same reason
a member who forgot their password is handed a link that sets it (`handed_reset_link`), made from
the members list or the `reset-password` command. Sign-up is closed by default (SIGNUP_OPEN):
the sign-up form and a first sign-in through Google or Microsoft are refused, and the pages say
to ask the team's admin, with the way to ask (`booking`). Open, a sign-up joins the workspace as
a member instead of making an organisation of its own, within the members its plan allows.

A database that holds several organisations, as development's does, keeps working: the
workspace a sign-up joins is the one made first, the local account's aside.
"""

from datetime import datetime
from urllib.parse import quote

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import LOCAL_ORG_SLUG, Account, add_member, create_organization, create_user
from app.auth import HANDED_LINK_TTL, issue_link_token, now
from app.config import Settings
from app.db import MIGRATION_LOCK
from app.entitlements import PlanRefused, Refusal, check_member, read_only
from app.models import Organization, User, UserSession

# Held while the setup looks for a user and makes the first, so that two at once make one.
SETUP_LOCK = MIGRATION_LOCK + 7
# The frontend's page a reset link opens (frontend/src/account/PasswordPages.tsx).
RESET_PAGE = "/reset-password"

SIGNUP_CLOSED = (
    "Accounts on this Emudoc are made by invitation. Ask your team's admin to invite you, or "
    "open the link in the invitation you were sent."
)
SET_UP_ALREADY = (
    "This Emudoc is set up already: its admin made the first account. Sign in, or ask the admin "
    "for an invitation."
)
NOT_SET_UP = (
    "This Emudoc is not set up yet. Its admin makes the first account on the setup page, which "
    "opening it in a browser shows."
)
WORKSPACE_FULL = (
    "{workspace} has as many members as its plan allows, so nobody can sign up until its admin "
    "makes room. Ask them for an invitation."
)


class SetUpAlready(RuntimeError):
    """The install has a user already, so the setup has nothing to make; the message says so."""


class NotSetUp(RuntimeError):
    """The install has no workspace for a sign-up to join yet; the message says so."""


def booking(settings: Settings) -> tuple[str, str]:
    """Where a visitor asks for an account, and the words of the button that leads there: the
    home page's way in (app/site.py), and the sign-in pages' while sign-up is closed. The
    booking page, else a message to the support address; without either, the sign-up page
    while it is open and the sign-in page while it is not."""
    if settings.booking_url:
        return settings.booking_url, "Book a demo"
    if support := settings.support_address:
        return f"mailto:{support}?subject={quote('Access to Emudoc')}", "Request access"
    if settings.signup_open:
        return "/signup", "Request access"
    return "/login", "Sign in"


def needs_setup(session: Session) -> bool:
    """Whether the install has no user yet, so that its pages send to the setup."""
    return session.scalar(select(User.id).limit(1)) is None


def workspace(session: Session) -> Organization | None:
    """The install's workspace, which a sign-up joins: the organisation made first, the local
    account's aside; None before the setup."""
    return session.scalar(
        select(Organization)
        .where(Organization.slug != LOCAL_ORG_SLUG)
        .order_by(Organization.created_at, Organization.id)
        .limit(1)
    )


def set_up(
    session: Session,
    email: str,
    name: str,
    password_hash: str | None,
    workspace_name: str,
    time_zone: str = "",
) -> Account:
    """Make the install's first user and its workspace, which they own, and return their
    account; the caller then gives the workspace the sample project (`add_sample`), after the
    audit log's rows for what it made. The address starts confirmed: the setup is the install's
    own, typed by whoever runs it. `password_hash` None makes an account that signs in once a
    reset link gives it a password (`create-admin`). Refused with `SetUpAlready` once any user
    exists. The caller commits, which lets the lock go."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK})
    if not needs_setup(session):
        raise SetUpAlready(SET_UP_ALREADY)
    user = create_user(
        session, email, name, password_hash, email_verified=True, time_zone=time_zone
    )
    return create_organization(session, user, workspace_name)


def join_workspace(
    session: Session, user: User, user_session: UserSession | None = None
) -> Account:
    """Make a user who signed up a member of the install's workspace, while sign-up is open,
    within the members its plan allows. Refused with `NotSetUp` before the setup and
    `PlanRefused` when the workspace is full. The caller commits."""
    organization = workspace(session)
    if organization is None:
        raise NotSetUp(NOT_SET_UP)
    # Held, so that two sign-ups cannot both take the plan's last place.
    session.refresh(organization, with_for_update=True)
    if read_only(session, organization) or check_member(session, organization):
        raise PlanRefused(Refusal("members", WORKSPACE_FULL.format(workspace=organization.name)))
    return add_member(session, organization, user, "member", user_session)


def handed_reset_link(session: Session, user: User, app_url: str) -> tuple[str, datetime]:
    """A link to the page that sets the user's password, for an owner or admin, or whoever runs
    `reset-password` or `create-admin`, to hand on where the install mails nothing, and when it
    stops working (HANDED_LINK_TTL). Their earlier reset links stop working. The caller
    commits."""
    token = issue_link_token(session, user, "reset_password", user.email, HANDED_LINK_TTL)
    return f"{app_url.rstrip('/')}{RESET_PAGE}#token={quote(token)}", now() + HANDED_LINK_TTL
