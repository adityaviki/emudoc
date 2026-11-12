"""Who is acting on a request, and how accounts and organisations come to be.

A signed-in request acts as the user named by its session cookie, in the organisation the
session works in: the one its user last switched to (`sessions.org_id`), or while they have not,
or have left that one since, the one they joined first. The choice is the session's, so every
tab of a browser works in the same organisation; a tab that still shows the one before is
refused until it catches up (`get_current_account` in app/api/deps.py).

An install's setup makes its first user and the workspace they own (app/signups.py), and a
user may start more organisations (`create_organization`), each with its own plan. So that new
organisations are no way past the free plan's limits, one user may own MAX_FREE_ORGANIZATIONS on
it at most. Everyone else is made alone (`create_user`), a member of the organisation that
invited them (`add_member`, app/invitations.py), or of the workspace when they signed up while
sign-up is open. A user removed from their last organisation is given one of their own
(`create_workspace`). `create_account`, a user with an organisation of their own, is what
signing up made until 2026-11-12, and what the tests make each account they need with.

For development on one machine, a request without a session can act as the local account
instead: an organisation of one, created on first use, which has no session to switch in.
"""

import datetime as dt
import re
from dataclasses import dataclass
from functools import cache
from itertools import count
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PLANS, ROLES, ApiKey, Membership, Organization, User, UserSession

LOCAL_ORG_SLUG = "local"
# A reserved domain, so the address can never receive mail or clash with a real account.
LOCAL_OWNER_EMAIL = "owner@emudoc.invalid"


@dataclass(frozen=True)
class Account:
    """The organisation a request works in, the user making it, and their role in it."""

    organization: Organization
    user: User
    role: str
    # The session the request came with; None for the local account and for an API key's.
    session_id: int | None = None
    # The API key the request came with instead (app/apikeys.py), which acts as its member.
    key: ApiKey | None = None
    # The operator viewing the member's account as support from their own session
    # (app/support.py), read only; None for the member's own requests.
    operator_id: int | None = None

    def is_at_least(self, role: str) -> bool:
        """Whether the account's role grants everything `role` grants."""
        return ROLES.index(self.role) <= ROLES.index(role)

    @property
    def is_local(self) -> bool:
        """Whether this is the local account of development, held to none of what a member's
        account is: a request with neither a session nor a key, or with a key the local owner
        made, which acts as them."""
        if self.session_id is not None:
            return False
        return self.key is None or self.user.email == LOCAL_OWNER_EMAIL


def account_for_user(
    session: Session, user: User, user_session: UserSession | None = None
) -> Account | None:
    """The user's account in the organisation `user_session` works in, if they are still a
    member of it, else in the one they joined first; None if they are in none."""
    chosen = user_session.org_id if user_session is not None else None
    row = session.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(
            case((Membership.org_id == chosen, 0), else_=1),
            Membership.created_at,
            Membership.id,
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    organization, role = row
    return Account(organization, user, role, user_session.id if user_session else None)


def organizations_of(session: Session, user: User) -> list[tuple[Organization, str]]:
    """Every organisation the user is a member of, with their role there, by name."""
    rows = session.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(func.lower(Organization.name), Organization.id)
    ).all()
    return [(organization, role) for organization, role in rows]


def free_organizations_owned(session: Session, user: User) -> int:
    """How many organisations on the plan a new one starts on the user owns."""
    return session.scalar(
        select(func.count())
        .select_from(Membership)
        .join(Organization, Organization.id == Membership.org_id)
        .where(
            Membership.user_id == user.id,
            Membership.role == "owner",
            Organization.plan == PLANS[0],
        )
    )


def create_organization(
    session: Session, user: User, name: str, slug_stem: str | None = None
) -> Account:
    """A new organisation the user owns, on the plan a new one starts on, with its data kept
    where the deployment keeps it (DATA_REGION), and a slug of its own from `slug_stem` or the
    name. The caller commits."""
    organization = Organization(
        name=name,
        slug=_unique_slug(session, slug_stem or name),
        region=get_settings().data_region or None,
    )
    session.add(organization)
    session.flush()
    session.add(Membership(org_id=organization.id, user_id=user.id, role="owner"))
    session.flush()
    return Account(organization, user, "owner")


def add_member(
    session: Session,
    organization: Organization,
    user: User,
    role: str,
    user_session: UserSession | None = None,
) -> Account:
    """Make the user a member of the organisation with the role, and when they came with a
    session, work in it from now on. The caller commits."""
    session.add(Membership(org_id=organization.id, user_id=user.id, role=role))
    session.flush()
    if user_session is None:
        return Account(organization, user, role)
    user_session.org_id = organization.id
    return Account(organization, user, role, user_session.id)


def create_user(
    session: Session,
    email: str,
    name: str = "",
    password_hash: str | None = None,
    email_verified: bool = False,
    time_zone: str = "",
) -> User:
    """A new user, in the time zone their browser named if it is one the app knows, a member of
    nothing yet: the caller makes them one and commits."""
    user = User(
        email=email,
        name=name,
        time_zone=time_zone if time_zone in time_zones() else "",
        password_hash=password_hash,
        email_verified_at=dt.datetime.now(dt.UTC) if email_verified else None,
    )
    session.add(user)
    session.flush()
    return user


def create_account(
    session: Session,
    email: str,
    name: str = "",
    password_hash: str | None = None,
    email_verified: bool = False,
    time_zone: str = "",
) -> Account:
    """A new user who owns a new organisation of their own. The caller commits."""
    user = create_user(session, email, name, password_hash, email_verified, time_zone)
    return create_workspace(session, user)


def create_workspace(session: Session, user: User) -> Account:
    """An organisation of the user's own, named after them: what a member removed from their
    last organisation is given, since an account in none can do nothing. The caller commits."""
    stem = user.name or user.email.split("@")[0]
    return create_organization(session, user, f"{stem}'s workspace", stem)


@cache
def time_zones() -> frozenset[str]:
    """The zones a user may choose: the IANA database's names, with the old ones a browser may
    still report (Chrome's Asia/Calcutta) from the tzdata package, which the image's own
    database lacks."""
    return frozenset(available_timezones())


def zone_of(name: str) -> dt.tzinfo:
    """The zone a user's time zone names, UTC when they chose none."""
    return ZoneInfo(name) if name in time_zones() else dt.UTC


def _unique_slug(session: Session, stem: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:80] or "workspace"
    taken = set(
        session.scalars(select(Organization.slug).where(Organization.slug.like(f"{base}%")))
    )
    if base not in taken:
        return base
    for number in count(2):
        candidate = f"{base}-{number}"
        if candidate not in taken:
            return candidate
    raise AssertionError("unreachable")


def local_account(session: Session) -> Account:
    """The local organisation and its owner, created on first use."""
    account = _find_local(session)
    if account is None:
        try:
            with session.begin_nested():
                # One machine, one developer: no plan stands between them and their files.
                organization = Organization(
                    name="Local",
                    slug=LOCAL_ORG_SLUG,
                    plan="enterprise",
                    region=get_settings().data_region or None,
                )
                user = User(email=LOCAL_OWNER_EMAIL, name="Owner")
                session.add_all([organization, user])
                session.flush()
                session.add(Membership(org_id=organization.id, user_id=user.id, role="owner"))
        except IntegrityError:
            # Another request created it between the lookup and the insert; use theirs.
            pass
        session.commit()
        account = _find_local(session)
    if account is None:
        raise RuntimeError(
            f"The local organisation '{LOCAL_ORG_SLUG}' has no owner. Reset the database with "
            "`docker compose down -v`, or give it an owner membership."
        )
    return account


def _find_local(session: Session) -> Account | None:
    row = session.execute(
        select(Organization, User, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .join(User, User.id == Membership.user_id)
        .where(Organization.slug == LOCAL_ORG_SLUG, Membership.role == "owner")
        .order_by(User.id)
        .limit(1)
    ).first()
    return None if row is None else Account(*row)
