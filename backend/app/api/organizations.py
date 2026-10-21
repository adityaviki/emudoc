"""The organisations a user belongs to: starting one, switching between them, and the current
one's name, its settings, its members and their roles, handing it over to another of them,
removing a member, and leaving it.

The organisation a request works in is its session's (app/accounts.py), so starting one and
switching set the session's choice, and every tab of the browser follows. Each answers with
the account as `/auth/me` does, for the page to start again in the organisation it now shows.
The local account has no session to keep a choice in, so it neither starts nor switches.

Renaming and the organisation's settings are for owners and admins. Renaming keeps the slug,
which the operator's commands name the organisation by (`usage <slug>`, `plan <slug>`). The
settings are the language its documents are searched in (app/languages.py), which moves the
passages indexed before to it by a job; how long it keeps a chat, never longer than its plan
does (app/retention.py); how its members are told each kind of notice until they choose
for themselves (app/notices.py); and its security settings (app/security.py), whether everyone
who works in it signs in with two-factor, which only someone who does may turn on, and how
many days after signing in a session may work in it. Each change is a row of the audit log of
its own. The owners and admins also see which members sign in with two-factor.

Handing it over makes another member an owner and its owner an admin, confirmed as deleting the
account is, by the password or the address typed out, since a browser left signed in should not
give an organisation away. Leaving takes the member's private chats in its projects with them,
which nobody else could read, and their place among its private projects' members; their shared
chats stay with the project. Nobody leaves an organisation that would be left without an owner,
or with nobody, nor their last one, since an account in none can do nothing.

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

from app.access import forget_projects
from app.accounts import (
    Account,
    account_for_user,
    create_organization,
    create_workspace,
    free_organizations_owned,
)
from app.api.auth import confirm_account, me_out
from app.api.deps import AccountDep, CurrentAccountDep, SessionDep, UsableAccountDep, limited
from app.audit import audit, audit_event
from app.config import get_settings
from app.entitlements import entitlements_of
from app.languages import known_languages, language_of, queue_language
from app.models import PLANS, ROLES, Chat, Membership, Organization, Project, User, UserSession
from app.notices import IN_APP, forget_notifications
from app.postbox import BY_EMAIL
from app.ratelimit import SIGN_INS
from app.schemas import (
    LanguagesOut,
    MemberOut,
    MemberRoleIn,
    MeOut,
    OrganizationChangeIn,
    OrganizationIn,
    OwnershipIn,
)
from app.security import MFA_YOURS_FIRST, longer_than_sessions

router = APIRouter(tags=["organizations"])

NO_SESSION = (
    "The local account works in its own organisation alone: without a session there is nothing "
    "to keep another one in. Sign in with an account to start and switch between organisations."
)
NAME_NEEDED = "Give the organisation a name."
NOT_FOUND = "Organisation not found"
ADMINS_ONLY = (
    "Only the owners and admins of the organisation can rename it or change its settings. Ask "
    "one of them to, if something should change."
)
UNKNOWN_LANGUAGE = "Emudoc cannot search documents in that language. Choose one of those offered."
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


def longer_than_plan(plan: str, days: int) -> str:
    """What keeping chats longer than the organisation's plan does is refused with."""
    return (
        f"The {plan.title()} plan keeps a chat {days:,} days after its last question, so the "
        f"organisation cannot keep one longer. Choose {days:,} days or fewer, or keep them as "
        "long as the plan does."
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
    name = _name(body.name)
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
def change_organization(
    body: OrganizationChangeIn, account: AccountDep, request: Request, session: SessionDep
):
    """Rename the organisation, its slug staying as it was, or change its settings; what the
    body leaves out stays as it is."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)
    organization = account.organization
    given = body.model_fields_set
    changes: list[tuple[str, dict]] = []
    if body.name is not None and (name := _name(body.name)) != organization.name:
        changes.append(("organization_renamed", {"from": organization.name, "to": name}))
        organization.name = name
    if "language" in given and body.language != organization.language:
        if body.language is not None and body.language not in known_languages(session):
            raise HTTPException(400, UNKNOWN_LANGUAGE)
        was = language_of(organization)
        changes.append(("settings_changed", _setting("language", organization.language, body)))
        organization.language = body.language
        if language_of(organization) != was:
            queue_language(session, organization.id)
    if "chat_days" in given and body.chat_days != organization.chat_days:
        kept = entitlements_of(organization).retention_days
        if body.chat_days is not None and kept is not None and body.chat_days > kept:
            raise HTTPException(400, longer_than_plan(organization.plan, kept))
        changes.append(("settings_changed", _setting("chat_days", organization.chat_days, body)))
        organization.chat_days = body.chat_days
    for choice in body.notices or ():
        for way, on in ((IN_APP, choice.app), (BY_EMAIL, choice.email)):
            if on is not None and on != organization.tells(choice.kind, way):
                detail = {"setting": "notices", "kind": choice.kind, "way": way}
                changes.append(("settings_changed", {**detail, "from": not on, "to": on}))
                organization.choose(choice.kind, way, on)
    if body.mfa_required is not None and body.mfa_required != organization.mfa_required:
        if body.mfa_required and not account.user.mfa_enabled:
            raise HTTPException(409, MFA_YOURS_FIRST)
        changes.append(("settings_changed", _setting("mfa_required", not body.mfa_required, body)))
        organization.mfa_required = body.mfa_required
    if "session_days" in given and body.session_days != organization.session_days:
        most = get_settings().session_days
        if body.session_days is not None and body.session_days > most:
            raise HTTPException(400, longer_than_sessions(most))
        was = organization.session_days
        changes.append(("settings_changed", _setting("session_days", was, body)))
        organization.session_days = body.session_days
    for action, detail in changes:
        audit(session, account, request, action, detail=detail)
    if changes:
        session.commit()
    return me_out(session, account)


@router.get("/organization/languages", response_model=LanguagesOut)
def list_languages(account: AccountDep, session: SessionDep):
    """The languages the organisation may search its documents in, and the deployment's."""
    return LanguagesOut(languages=known_languages(session), default=get_settings().fts_language)


@router.get("/organization/members", response_model=list[MemberOut])
def list_members(account: AccountDep, session: SessionDep):
    """The organisation's members, the owners first, then by role and how long they have been
    members."""
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == account.organization.id)
    ).all()
    managing = account.is_at_least("admin")
    members = [_member_out(membership, user, managing) for membership, user in rows]
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
def leave_organization(account: CurrentAccountDep, request: Request, session: SessionDep):
    """Stop being a member of the organisation, and work in another of the user's from now on:
    also the way out of one that holds the account until it signs in with two-factor."""
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
    could read. Their place among its private projects' members goes too, so that joining
    again shows them none, and so do its notices in their notification centre. The caller
    commits."""
    own_projects = select(Project.id).where(Project.org_id == org_id)
    chats = session.execute(
        delete(Chat).where(
            Chat.user_id == user_id,
            Chat.visibility == "private",
            Chat.project_id.in_(own_projects),
        )
    ).rowcount
    forget_projects(session, org_id, user_id)
    forget_notifications(session, org_id, user_id)
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


def _member_out(membership: Membership, user: User, managing: bool = True) -> MemberOut:
    """The member as the page shows them, with whether they sign in with two-factor to an owner
    or admin (`managing`)."""
    return MemberOut(
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=membership.role,
        joined_at=membership.created_at,
        mfa_enabled=user.mfa_enabled if managing else None,
    )


def _setting(name: str, was: object, body: OrganizationChangeIn) -> dict:
    """A setting's audit detail: what it was and what it is now, null for the deployment's or
    the plan's."""
    return {"setting": name, "from": was, "to": getattr(body, name)}


def _name(given: str) -> str:
    name = " ".join(given.split())
    if not name:
        raise HTTPException(400, NAME_NEEDED)
    return name
