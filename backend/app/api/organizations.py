"""The workspace a request works in: its name, its settings, its members and their roles,
handing it over to another of them, and removing a member.

An install holds one workspace (S.1, 2026-11-12), the organisation every request works in
(app/accounts.py), so these routes name no organisation: nobody starts another, switches,
leaves, joins one by their address's domain, or deletes it, which would be the install itself.

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
give an organisation away: the way out for a team's admin who leaves the company.

Owners and admins change the other members' roles and remove them: an admin anyone's but an
owner's, and to any role but owner; an owner anyone's. Within the same bounds they make a member
a link that sets the member's password, shown to copy and hand on, for a member who forgot it
where the install mails nothing. Nobody changes their own role or removes themselves here, so
the owner who asks is still one after. These and handing over hold the organisation until they
commit and read the asker's role again under it, so that two owners cannot take each other's
role at once. A member removed loses their private chats in its projects, which nobody else
could read, and their place among its private projects' members; their shared chats stay with
the project. They keep their account, in no workspace, until it is invited again
(app/api/deps.py).
"""

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.access import forget_projects
from app.accounts import Account
from app.api.auth import app_url, confirm_account, me_out
from app.api.deps import AccountDep, SessionDep, limited
from app.audit import audit
from app.config import get_settings
from app.entitlements import entitlements_of
from app.languages import UNKNOWN_LANGUAGE, known_languages, language_of, queue_language
from app.models import (
    ROLES,
    Chat,
    Membership,
    Organization,
    Project,
    ProjectExport,
    User,
    UserSession,
)
from app.my_files import drop_files
from app.notices import IN_APP, forget_notifications
from app.postbox import BY_EMAIL
from app.ratelimit import SIGN_INS
from app.reviews import forget_assignments
from app.schemas import (
    LanguagesOut,
    MemberOut,
    MemberRoleIn,
    MeOut,
    OrganizationChangeIn,
    OwnershipIn,
    ResetLinkOut,
)
from app.security import MFA_YOURS_FIRST, longer_than_sessions
from app.signups import handed_reset_link

router = APIRouter(tags=["organizations"])

NAME_NEEDED = "Give the workspace a name."
NOT_FOUND = "Workspace not found"
ADMINS_ONLY = (
    "Only the owners and admins of the workspace can rename it or change its settings. Ask "
    "one of them to, if something should change."
)
OWNERS_ONLY = "Only an owner of the workspace can hand it over to another member."
YOURS_ALREADY = "Choose another member: the workspace is yours already."
MEMBER_NOT_FOUND = "Member not found"
MANAGERS_ONLY = (
    "Only the owners and admins of the workspace can change a member's role or remove a "
    "member. Ask one of them to."
)
OWNERS_FOR_OWNERS = (
    "Only an owner can make someone an owner, or change an owner's role or remove one."
)
YOUR_OWN_ROLE = (
    "Nobody changes their own role. Ask another owner of the workspace to change yours, or "
    "another admin if you are an admin."
)
REMOVE_YOURSELF = (
    "Nobody removes themselves. Ask another owner or admin of the workspace to remove you."
)
ADMINS_RESET = (
    "Only the owners and admins of the workspace can make a link that resets a member's "
    "password. Ask one of them to, or reset yours from the sign-in page."
)
OWNERS_RESET_OWNERS = "Only an owner can make a link that resets an owner's password."
YOUR_OWN_PASSWORD = "Change your own password on your account page instead."


def longer_than_plan(plan: str, days: int) -> str:
    """What keeping chats longer than the organisation's plan does is refused with."""
    return (
        f"The {plan.title()} plan keeps a chat {days:,} days after its last question, so the "
        f"organisation cannot keep one longer. Choose {days:,} days or fewer, or keep them as "
        "long as the plan does."
    )


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
    if body.share_links is not None and body.share_links != organization.share_links:
        changes.append(("settings_changed", _setting("share_links", not body.share_links, body)))
        organization.share_links = body.share_links
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
    """Remove another member from the organisation, with their private chats in its projects;
    they keep their account, which can do nothing until it is invited again."""
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
    session.commit()


@router.post(
    "/organization/members/{user_id}/reset-link",
    response_model=ResetLinkOut,
    dependencies=[Depends(limited(SIGN_INS))],
)
def make_reset_link(user_id: int, account: AccountDep, request: Request, session: SessionDep):
    """A link to the page that sets another member's password, for the owner or admin who asks
    to copy and hand on, which works for HANDED_LINK_TTL: how a member who forgot theirs signs
    in again where the install mails nothing (app/signups.py). Held to the asker's role as
    changing one is, since whoever opens the link chooses the member's password; their
    two-factor sign-in still holds. Their earlier links stop working, and the audit log keeps
    who made it."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_RESET)
    manager = _manager(session, account)
    if user_id == account.user.id:
        raise HTTPException(400, YOUR_OWN_PASSWORD)
    membership = _membership(session, account, user_id)
    if not manager.is_at_least(membership.role):
        raise HTTPException(403, OWNERS_RESET_OWNERS)
    link, ends = handed_reset_link(session, session.get(User, user_id), app_url(request))
    audit(session, account, request, "reset_link_made", target=("user", user_id))
    session.commit()
    return ResetLinkOut(link=link, expires_at=ends)


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
    """End the user's membership of the organisation, as being removed does, and return how
    many of their chats went: the private ones in its projects, which nobody else
    could read. Their place among its private projects' members goes too, so that joining
    again shows them none, and so do its notices in their notification centre, their exports
    of its projects, the reviews of its documents assigned to them and their My files in its
    projects (app/my_files.py), whose files the storage walk sweeps. The caller commits."""
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
    forget_assignments(session, org_id, user_id)
    drop_files(session, org_id, user_id)
    # Their exports hold their private chats too; the files go by their age.
    session.execute(
        delete(ProjectExport).where(
            ProjectExport.user_id == user_id, ProjectExport.project_id.in_(own_projects)
        )
    )
    session.execute(
        delete(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    # Every session that worked in it, in other browsers too, works in none until the user is
    # invited again.
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
