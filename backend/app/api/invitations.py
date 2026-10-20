"""Who may join the organisation: the invitations its owners and admins send, and the domains
whose confirmed addresses may join it by themselves (app/invitations.py).

An owner or admin invites an address with a role no higher than their own, sees the invitations
still open or expired, sends one again, which mails a new link and stops the one before, and
revokes one. Each is held to the plan's members, an open invitation counted (`check_member`),
and an organisation that holds more than its plan allows invites nobody until it is within it
(`read_only`). The mail is posted with the invitation (app/postbox.py), as every link the app
mails is; the route is held to a rate per user and per address mailed, so that no inbox can be
flooded from one organisation. Whoever opens the link accepts it on the invitation's page
(app/api/auth.py).

The domains are chosen by an owner or admin too, and one is added only by someone whose own
confirmed address is at it. A user whose confirmed address is at one is offered the
organisation (`MeOut.joinable`) and joins it here, with the role it chose, if its plan has room.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import Account, add_member
from app.api.auth import INVITATION_PAGE, mailed_link, me_out
from app.api.deps import (
    AccountDep,
    SessionDep,
    UsableAccountDep,
    limited,
    refuse_past,
)
from app.api.organizations import NO_SESSION, NOT_FOUND, locked
from app.audit import audit
from app.auth import now
from app.entitlements import PlanRefused, Refusal, check_member, read_only
from app.invitations import INVITATION_TTL, domain_of, is_open, issue, joining_refusal
from app.mail import invitation_email
from app.models import Invitation, Membership, User, UserSession
from app.postbox import post
from app.ratelimit import SIGN_INS, by_email
from app.schemas import InvitationIn, InvitationOut, JoiningIn, MeOut

router = APIRouter(tags=["invitations"])

ADMINS_ONLY = (
    "Only the owners and admins of the organisation can invite people, see who is invited and "
    "choose who may join. Ask one of them to."
)
OWNERS_INVITE_OWNERS = "Only an owner can invite someone to be an owner, or send that again."
INVITED_ALREADY = (
    "That address is invited already. Send its invitation again from the list, or revoke it to "
    "invite it with another role."
)
INVITATION_NOT_FOUND = "Invitation not found"


@router.get("/organization/invitations", response_model=list[InvitationOut])
def list_invitations(account: AccountDep, session: SessionDep):
    """The invitations still open or expired, the newest first."""
    _refuse_unless_admin(account)
    rows = session.execute(
        select(Invitation, User)
        .outerjoin(User, User.id == Invitation.invited_by_id)
        .where(Invitation.org_id == account.organization.id)
        .order_by(Invitation.created_at.desc(), Invitation.id.desc())
    ).all()
    return [_out(invitation, inviter) for invitation, inviter in rows]


@router.post(
    "/organization/invitations",
    response_model=InvitationOut,
    status_code=201,
    dependencies=[Depends(limited(SIGN_INS))],
)
def invite(body: InvitationIn, account: AccountDep, request: Request, session: SessionDep):
    """Invite an address to the organisation with a role, and mail it the link. An address
    whose invitation expired is invited afresh."""
    _refuse_unless_admin(account)
    if not account.is_at_least(body.role):
        raise HTTPException(403, OWNERS_INVITE_OWNERS)
    email = body.email.strip().lower()
    # Counted for the address as well as the inviter, so that no address is flooded from many.
    refuse_past(session, (SIGN_INS, by_email(email)))
    organization = locked(session, account.organization.id)
    member = session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == organization.id, User.email == email)
    )
    if member is not None:
        raise HTTPException(
            409, f"{member.name or member.email} is a member of {organization.name} already."
        )
    invitation = session.scalar(
        select(Invitation).where(Invitation.org_id == organization.id, Invitation.email == email)
    )
    if invitation is not None and is_open(invitation):
        raise HTTPException(409, INVITED_ALREADY)
    # An organisation past its plan takes nobody new, whatever it is past, until it is within it.
    if refusal := read_only(session, organization) or check_member(session, organization):
        raise PlanRefused(refusal)
    if invitation is None:
        invitation = Invitation(org_id=organization.id, email=email)
        session.add(invitation)
    invitation.role = body.role
    invitation.invited_by_id = account.user.id
    invitation.created_at = now()
    token = issue(invitation)
    try:
        session.flush()
    except IntegrityError:
        # The same address invited twice at once is the database's to see.
        session.rollback()
        raise HTTPException(409, INVITED_ALREADY) from None
    audit(
        session,
        account,
        request,
        "invitation_sent",
        target=("invitation", invitation.id),
        detail={"role": invitation.role},
    )
    _mail(session, request, account, invitation, token)
    session.commit()
    return _out(invitation, account.user)


@router.post(
    "/organization/invitations/{invitation_id}/resend",
    response_model=InvitationOut,
    dependencies=[Depends(limited(SIGN_INS))],
)
def resend_invitation(
    invitation_id: int, account: AccountDep, request: Request, session: SessionDep
):
    """Mail the invitation again, with a new link for INVITATION_TTL from now; the link mailed
    before stops working. One that expired takes a place among the plan's members again."""
    _refuse_unless_admin(account)
    email = _invitation(session, account, invitation_id).email
    refuse_past(session, (SIGN_INS, by_email(email)))
    organization = locked(session, account.organization.id)
    # Held, so that one accepted meanwhile is found gone rather than sent again.
    invitation = _invitation(session, account, invitation_id, lock=True)
    if not account.is_at_least(invitation.role):
        raise HTTPException(403, OWNERS_INVITE_OWNERS)
    if not is_open(invitation) and (
        refusal := read_only(session, organization) or check_member(session, organization)
    ):
        raise PlanRefused(refusal)
    invitation.invited_by_id = account.user.id
    token = issue(invitation)
    audit(
        session,
        account,
        request,
        "invitation_sent",
        target=("invitation", invitation.id),
        detail={"role": invitation.role, "again": True},
    )
    _mail(session, request, account, invitation, token)
    session.commit()
    return _out(invitation, account.user)


@router.delete("/organization/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    invitation_id: int, account: AccountDep, request: Request, session: SessionDep
):
    """Revoke an invitation: its link stops working and its place is free again."""
    _refuse_unless_admin(account)
    invitation = _invitation(session, account, invitation_id, lock=True)
    audit(
        session,
        account,
        request,
        "invitation_revoked",
        target=("invitation", invitation.id),
        detail={"role": invitation.role},
    )
    session.delete(invitation)
    session.commit()


@router.put("/organization/joining", response_model=MeOut)
def set_joining(body: JoiningIn, account: AccountDep, request: Request, session: SessionDep):
    """Choose the domains whose confirmed addresses may join the organisation, and the role
    they join with. A domain is added only by someone whose own confirmed address is at it, and
    any may be taken off."""
    _refuse_unless_admin(account)
    organization = account.organization
    domains = sorted({domain.strip().lower().removeprefix("@") for domain in body.domains} - {""})
    for domain in sorted(set(domains) - set(organization.join_domains)):
        if refusal := joining_refusal(domain, account.user):
            raise HTTPException(400, refusal)
    if domains != sorted(organization.join_domains) or body.role != organization.join_role:
        audit(
            session,
            account,
            request,
            "joining_changed",
            detail={"domains": ", ".join(domains), "role": body.role},
        )
        organization.join_domains = domains
        organization.join_role = body.role
        session.commit()
    return me_out(session, account)


@router.post("/organizations/{org_id}/join", response_model=MeOut)
def join_organization(
    org_id: int, account: UsableAccountDep, request: Request, session: SessionDep
):
    """Join an organisation that lets everyone at the domain of the user's confirmed address
    join, with the role it chose, and work in it from now on, in every tab of the browser."""
    user = account.user
    domain = domain_of(user.email)
    organization = locked(session, org_id)
    if (
        organization is None
        or user.email_verified_at is None
        or domain not in organization.join_domains
    ):
        raise HTTPException(404, NOT_FOUND)
    if account.session_id is None:
        raise HTTPException(409, NO_SESSION)
    member = session.scalar(
        select(Membership.id).where(Membership.org_id == org_id, Membership.user_id == user.id)
    )
    if member is not None:
        raise HTTPException(
            409, f"You are a member of {organization.name} already: switch to it instead."
        )
    if read_only(session, organization) is not None:
        raise PlanRefused(
            Refusal(
                "members",
                f"{organization.name} holds more than its plan allows, so nobody can join it "
                "until it is within the plan. Ask one of its owners to make room, or to move it "
                "to a larger plan.",
            )
        )
    if check_member(session, organization) is not None:
        raise PlanRefused(
            Refusal(
                "members",
                f"{organization.name} has as many members as its plan allows. Ask one of its "
                "owners to make room, or to move it to a larger plan.",
            )
        )
    user_session = session.get(UserSession, account.session_id)
    joined = add_member(session, organization, user, organization.join_role, user_session)
    audit(
        session,
        joined,
        request,
        "member_joined",
        detail={"role": joined.role, "domain": domain},
    )
    session.commit()
    return me_out(session, joined)


def _refuse_unless_admin(account: Account) -> None:
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)


def _invitation(
    session: Session, account: Account, invitation_id: int, lock: bool = False
) -> Invitation:
    """One of the organisation's invitations, refused with 404 when it has none by that id;
    `lock` holds it until the commit."""
    statement = select(Invitation).where(
        Invitation.id == invitation_id, Invitation.org_id == account.organization.id
    )
    invitation = session.scalar(statement.with_for_update() if lock else statement)
    if invitation is None:
        raise HTTPException(404, INVITATION_NOT_FOUND)
    return invitation


def _mail(
    session: Session, request: Request, account: Account, invitation: Invitation, token: str
) -> None:
    """Post the invitation's link with the change that made it."""
    content = invitation_email(
        account.user.name or account.user.email,
        account.organization.name,
        invitation.role,
        mailed_link(request, INVITATION_PAGE, token),
        INVITATION_TTL.days,
    )
    post(session, invitation.email, content, account.organization.id)


def _out(invitation: Invitation, inviter: User | None) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        invited_by=(inviter.name or inviter.email) if inviter is not None else None,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        expired=not is_open(invitation),
    )
