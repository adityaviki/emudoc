"""The organisations a user belongs to: starting one, switching between them, and the current
one's name, its members, handing it over to another of them, and leaving it.

The organisation a request works in is its session's (app/accounts.py), so starting one and
switching set the session's choice, and every tab of the browser follows. Each answers with
the account as `/auth/me` does, for the page to start again in the organisation it now shows.
The local account has no session to keep a choice in, so it neither starts nor switches.

Renaming is for owners and admins, and keeps the slug, which the operator's commands name the
organisation by (`usage <slug>`, `plan <slug>`). Handing it over makes another member an owner
and its owner an admin, confirmed as deleting the account is, by the password or the address
typed out, since a browser left signed in should not give an organisation away. Leaving takes
the member's private chats in its projects with them, which nobody else could read; their
shared chats stay with the project. Nobody leaves an organisation that would be left without
an owner, or with nobody, nor their last one, since an account in none can do nothing.
"""

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.accounts import (
    Account,
    account_for_user,
    create_organization,
    free_organizations_owned,
)
from app.api.auth import confirm_account, me_out
from app.api.deps import AccountDep, SessionDep, UsableAccountDep, limited
from app.audit import audit
from app.config import get_settings
from app.models import PLANS, ROLES, Chat, Membership, Project, User, UserSession
from app.ratelimit import SIGN_INS
from app.schemas import MemberOut, MeOut, OrganizationIn, OwnershipIn

router = APIRouter(tags=["organizations"])

NO_SESSION = (
    "The local account works in its own organisation alone: without a session there is nothing "
    "to keep another one in. Sign in with an account to start and switch between organisations."
)
NAME_NEEDED = "Give the organisation a name."
NOT_FOUND = "Organisation not found"
ADMINS_ONLY = (
    "Only the owners and admins of the organisation can rename it. Ask one of them to, if it "
    "should change."
)
OWNERS_ONLY = "Only an owner of the organisation can hand it over to another member."
YOURS_ALREADY = "Choose another member: the organisation is yours already."
MEMBER_NOT_FOUND = "Member not found"
LAST_ORGANISATION = (
    "This is the only organisation you are a member of, and an account in none can do "
    "nothing. Join or start another first, or delete your account instead."
)


def too_many_free(allowed: int) -> str:
    """What starting an organisation past MAX_FREE_ORGANIZATIONS is refused with."""
    owned = "one organisation" if allowed == 1 else f"{allowed} organisations"
    return (
        f"You own {owned} on the {PLANS[0].title()} plan, which is as many as one person may. "
        "Move one to a paid plan, or make another of its members its owner, to start another."
    )


@router.post("/organizations", response_model=MeOut, status_code=201)
def start_organization(
    body: OrganizationIn, account: UsableAccountDep, request: Request, session: SessionDep
):
    """Start an organisation the user owns, and work in it from now on."""
    if account.session_id is None:
        raise HTTPException(409, NO_SESSION)
    name = _name(body)
    allowed = get_settings().max_free_organizations
    if allowed and free_organizations_owned(session, account.user) >= allowed:
        raise HTTPException(409, too_many_free(allowed))
    started = replace(
        create_organization(session, account.user, name), session_id=account.session_id
    )
    session.get(UserSession, account.session_id).org_id = started.organization.id
    audit(session, started, request, "organization_created", detail={"name": name})
    session.commit()
    return me_out(session, started)


@router.post("/organizations/{org_id}/switch", response_model=MeOut)
def switch_organization(org_id: int, account: UsableAccountDep, session: SessionDep):
    """Work in another of the user's organisations from now on, in every tab of the browser."""
    member = session.scalar(
        select(Membership.id).where(
            Membership.org_id == org_id, Membership.user_id == account.user.id
        )
    )
    if member is None:
        raise HTTPException(404, NOT_FOUND)
    if account.session_id is None:
        raise HTTPException(409, NO_SESSION)
    user_session = session.get(UserSession, account.session_id)
    user_session.org_id = org_id
    session.commit()
    return me_out(session, account_for_user(session, account.user, user_session))


@router.patch("/organization", response_model=MeOut)
def rename_organization(
    body: OrganizationIn, account: AccountDep, request: Request, session: SessionDep
):
    """Rename the organisation; its slug stays as it was."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)
    name = _name(body)
    organization = account.organization
    if name != organization.name:
        audit(
            session,
            account,
            request,
            "organization_renamed",
            detail={"from": organization.name, "to": name},
        )
        organization.name = name
        session.commit()
    return me_out(session, account)


@router.get("/organization/members", response_model=list[MemberOut])
def list_members(account: AccountDep, session: SessionDep):
    """The organisation's members, the owners first, then by role and how long they have been
    members."""
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == account.organization.id)
    ).all()
    members = [
        MemberOut(
            user_id=user.id,
            name=user.name,
            email=user.email,
            role=membership.role,
            joined_at=membership.created_at,
        )
        for membership, user in rows
    ]
    return sorted(members, key=lambda m: (ROLES.index(m.role), m.joined_at, m.user_id))


@router.post("/organization/owner", response_model=MeOut, dependencies=[Depends(limited(SIGN_INS))])
def hand_over(body: OwnershipIn, account: AccountDep, request: Request, session: SessionDep):
    """Make another member an owner of the organisation, and its owner who asks an admin."""
    if account.role != "owner":
        raise HTTPException(403, OWNERS_ONLY)
    confirm_account(session, account, request, body.password, body.email)
    if body.user_id == account.user.id:
        raise HTTPException(400, YOURS_ALREADY)
    org_id = account.organization.id
    successor = session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == body.user_id)
    )
    if successor is None:
        raise HTTPException(404, MEMBER_NOT_FOUND)
    if successor.role == "owner":
        name = session.get(User, body.user_id).name or "That member"
        raise HTTPException(409, f"{name} is an owner of {account.organization.name} already.")
    audit(
        session,
        account,
        request,
        "ownership_transferred",
        target=("user", body.user_id),
        detail={"from": successor.role},
    )
    successor.role = "owner"
    session.execute(
        update(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == account.user.id)
        .values(role="admin")
    )
    session.commit()
    return me_out(session, replace(account, role="admin"))


@router.post("/organization/leave", response_model=MeOut)
def leave_organization(account: AccountDep, request: Request, session: SessionDep):
    """Stop being a member of the organisation, and work in another of the user's from now on."""
    if account.session_id is None:
        raise HTTPException(409, NO_SESSION)
    if refusal := leaving_refusal(session, account):
        raise HTTPException(409, refusal)
    user_id, org_id = account.user.id, account.organization.id
    own_projects = select(Project.id).where(Project.org_id == org_id)
    chats = session.execute(
        delete(Chat).where(
            Chat.user_id == user_id,
            Chat.visibility == "private",
            Chat.project_id.in_(own_projects),
        )
    ).rowcount
    audit(
        session,
        account,
        request,
        "member_left",
        detail={"role": account.role, "private_chats": chats},
    )
    session.execute(
        delete(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    # Every session that worked in it, in other browsers too, goes back to the first joined.
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.org_id == org_id)
        .values(org_id=None)
    )
    session.commit()
    user_session = session.get(UserSession, account.session_id)
    return me_out(session, account_for_user(session, account.user, user_session))


def leaving_refusal(session: Session, account: Account) -> str | None:
    """Why the account's user cannot leave its organisation, or None when they can."""
    name = account.organization.name
    memberships = session.scalar(
        select(func.count()).select_from(Membership).where(Membership.user_id == account.user.id)
    )
    if memberships == 1:
        return LAST_ORGANISATION
    others, owners = session.execute(
        select(func.count(), func.count().filter(Membership.role == "owner")).where(
            Membership.org_id == account.organization.id,
            Membership.user_id != account.user.id,
        )
    ).one()
    if not others:
        return (
            f"You are the only member of {name}, so leaving it would leave it with nobody. "
            "Make another member its owner first, or stay."
        )
    if account.role == "owner" and not owners:
        return (
            f"You are the only owner of {name}, which has other members. Make one of them its "
            "owner first."
        )
    return None


def _name(body: OrganizationIn) -> str:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, NAME_NEEDED)
    return name
