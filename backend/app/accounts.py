"""Who is acting on a request, and how accounts come to be.

A signed-in request acts as the user named by its session cookie, in the organisation they
joined first (switching organisations is a roadmap item). Signing up makes a user and an
organisation of their own, which they own. For development on one machine, a request without a
session can act as the local account instead: an organisation of one, created on first use.
"""

import datetime as dt
import re
from dataclasses import dataclass
from itertools import count

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ROLES, Membership, Organization, User

LOCAL_ORG_SLUG = "local"
# A reserved domain, so the address can never receive mail or clash with a real account.
LOCAL_OWNER_EMAIL = "owner@emudoc.invalid"


@dataclass(frozen=True)
class Account:
    """The organisation a request works in, the user making it, and their role in it."""

    organization: Organization
    user: User
    role: str
    # The session the request came with; None for the local account.
    session_id: int | None = None

    def is_at_least(self, role: str) -> bool:
        """Whether the account's role grants everything `role` grants."""
        return ROLES.index(self.role) <= ROLES.index(role)


def account_for_user(session: Session, user: User, session_id: int | None = None) -> Account | None:
    """The user's account in the organisation they joined first; None if they are in none."""
    row = session.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at, Membership.id)
        .limit(1)
    ).first()
    if row is None:
        return None
    organization, role = row
    return Account(organization, user, role, session_id)


def create_account(
    session: Session,
    email: str,
    name: str = "",
    password_hash: str | None = None,
    email_verified: bool = False,
) -> Account:
    """A new user who owns a new organisation of their own. The caller commits."""
    user = User(
        email=email,
        name=name,
        password_hash=password_hash,
        email_verified_at=dt.datetime.now(dt.UTC) if email_verified else None,
    )
    stem = name or email.split("@")[0]
    organization = Organization(name=f"{stem}'s workspace", slug=_unique_slug(session, stem))
    session.add_all([user, organization])
    session.flush()
    session.add(Membership(org_id=organization.id, user_id=user.id, role="owner"))
    session.flush()
    return Account(organization, user, "owner")


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
                organization = Organization(name="Local", slug=LOCAL_ORG_SLUG)
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
