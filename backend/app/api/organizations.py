"""The organisations a user belongs to: starting one, switching between them, and the current
one's name, its members and their roles, handing it over to another of them, removing a member,
and leaving it.

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

Owners and admins change the other members' roles and remove them: an admin anyone's but an
owner's, and to any role but owner; an owner anyone's. Nobody changes their own role or removes
themselves here, so the owner who asks is still one after. These two, handing over and leaving
hold the organisation until they commit and read the asker's role again under it, so that two
owners cannot take each other's role at once. A member removed loses what leaving loses; one
removed from their last organisation is given one of their own, as at sign-up.
"""

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.accounts import (
    Account,
    account_for_user,
    create_organization,
    create_workspace,
    free_organizations_owned,
)
from app.api.auth import confirm_account, me_out
from app.api.deps import AccountDep, SessionDep, UsableAccountDep, limited
from app.audit import audit, audit_event
from app.config import get_settings
from app.models import PLANS, ROLES, Chat, Membership, Organization, Project, User, UserSession
from app.ratelimit import SIGN_INS
from app.schemas import MemberOut, MemberRoleIn, MeOut, OrganizationIn, OwnershipIn

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
MANAGERS_ONLY = (
    "Only the owners and admins of the organisation can change a member's role or remove a "
    "member. Ask one of them to."
)
OWNERS_FOR_OWNERS = (
    "Only an owner can make someone an owner, or change an owner's role or remove one."
)
YOUR_OWN_ROLE = (
    "Nobody changes their own role. Ask another owner of the organisation to change yours, or "
    "another admin if you are an admin."
)
REMOVE_YOURSELF = "To stop being a member of the organisation yourself, leave it instead."
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
    members = [_member_out(membership, user) for membership, user in rows]
    return sorted(members, key=lambda m: (ROLES.index(m.role), m.joined_at, m.user_id))


@router.patch("/organization/members/{user_id}", response_model=MemberOut)
def change_role(
    user_id: int, body: MemberRoleIn, account: AccountDep, request: Request, session: SessionDep
):
    """Give another member of the organisation a role, within the asker's own."""
    manager = _manager(session, account)
    if user_id == account.user.id:
        raise HTTPException(400, YOUR_OWN_ROLE)
    membership = _membership(session, account, user_id)
    if not (manager.is_at_least(membership.role) and manager.is_at_least(body.role)):
        raise HTTPException(403, OWNERS_FOR_OWNERS)
    if body.role != membership.role:
        audit(
            session,
            account,
            request,
            "role_changed",
            target=("user", user_id),
            detail={"from": membership.role, "to": body.role},
        )
        membership.role = body.role
        session.commit()
    return _member_out(membership, session.get(User, user_id))


@router.delete("/organization/members/{user_id}", status_code=204)
def remove_member(user_id: int, account: AccountDep, request: Request, session: SessionDep):
    """Remove another member from the organisation, with what leaving it takes; one who was in
    no other organisation is given one of their own."""
    manager = _manager(session, account)
    if user_id == account.user.id:
        raise HTTPException(400, REMOVE_YOURSELF)
    membership = _membership(session, account, user_id)
    if not manager.is_at_least(membership.role):
        raise HTTPException(403, OWNERS_FOR_OWNERS)
    role = membership.role
    chats = _part(session, account.organization.id, user_id)
    audit(
        session,
        account,
        request,
        "member_removed",
        target=("user", user_id),
        detail={"role": role, "private_chats": chats},
    )
    others = session.scalar(
        select(func.count()).select_from(Membership).where(Membership.user_id == user_id)
    )
    if not others:
        own = create_workspace(session, session.get(User, user_id)).organization
        # The app made it, so the row names no member as its maker.
        audit_event(
            session,
            "organization_created",
            org_id=own.id,
            target=("user", user_id),
            detail={"name": own.name},
        )
    session.commit()


@router.post("/organization/owner", response_model=MeOut, dependencies=[Depends(limited(SIGN_INS))])
def hand_over(body: OwnershipIn, account: AccountDep, request: Request, session: SessionDep):
    """Make another member an owner of the organisation, and its owner who asks an admin."""
    if account.role != "owner":
        raise HTTPException(403, OWNERS_ONLY)
    confirm_account(session, account, request, body.password, body.email)
    account = _held(session, account)
    if account.role != "owner":
        raise HTTPException(403, OWNERS_ONLY)
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
    account = _held(session, account)
    if refusal := leaving_refusal(session, account):
        raise HTTPException(409, refusal)
    chats = _part(session, account.organization.id, account.user.id)
    audit(
        session,
        account,
        request,
        "member_left",
        detail={"role": account.role, "private_chats": chats},
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


def locked(session: Session, org_id: int) -> Organization | None:
    """The organisation, held until the commit, so that requests that change who is in it, or
    with what role, take their turns: two cannot both take the plan's last place, nor two
    owners each other's role."""
    return session.scalar(select(Organization).where(Organization.id == org_id).with_for_update())


def _held(session: Session, account: Account) -> Account:
    """The account with its role as it stands once the organisation is held: another request
    may have changed it since this one began. Refused with 404 when the user is a member no
    longer."""
    locked(session, account.organization.id)
    role = session.scalar(
        select(Membership.role).where(
            Membership.org_id == account.organization.id, Membership.user_id == account.user.id
        )
    )
    if role is None:
        raise HTTPException(404, NOT_FOUND)
    return replace(account, role=role)


def _manager(session: Session, account: Account) -> Account:
    """The account as `_held` reads it, refused unless it is an owner's or an admin's."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, MANAGERS_ONLY)
    manager = _held(session, account)
    if not manager.is_at_least("admin"):
        raise HTTPException(403, MANAGERS_ONLY)
    return manager


def _membership(session: Session, account: Account, user_id: int) -> Membership:
    """Another user's membership of the account's organisation, refused with 404 when they are
    not a member of it."""
    membership = session.scalar(
        select(Membership).where(
            Membership.org_id == account.organization.id, Membership.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(404, MEMBER_NOT_FOUND)
    return membership


def _part(session: Session, org_id: int, user_id: int) -> int:
    """End the user's membership of the organisation, as leaving or being removed does, and
    return how many of their chats went: the private ones in its projects, which nobody else
    could read. The caller commits."""
    own_projects = select(Project.id).where(Project.org_id == org_id)
    chats = session.execute(
        delete(Chat).where(
            Chat.user_id == user_id,
            Chat.visibility == "private",
            Chat.project_id.in_(own_projects),
        )
    ).rowcount
    session.execute(
        delete(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    # Every session that worked in it, in other browsers too, goes back to the first joined.
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.org_id == org_id)
        .values(org_id=None)
    )
    return chats


def _member_out(membership: Membership, user: User) -> MemberOut:
    return MemberOut(
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=membership.role,
        joined_at=membership.created_at,
    )


def _name(body: OrganizationIn) -> str:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, NAME_NEEDED)
    return name
