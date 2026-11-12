"""Who may join the workspace: the invitations its owners and admins send (app/invitations.py).

An owner or admin invites an address with a role no higher than their own, sees the invitations
still open or expired, sends one again, which mails a new link and stops the one before, and
revokes one. Each is held to the plan's members, an open invitation counted (`check_member`),
and an organisation that holds more than its plan allows invites nobody until it is within it
(`read_only`). The mail is posted with the invitation (app/postbox.py), as every link the app
mails is; the route is held to a rate per user and per address mailed, so that no inbox can be
flooded from one organisation. The link is also the answer to whoever sent it, to copy and hand
on: an install may have no mail relay (app/signups.py), and then that is the only way it
arrives, which the answer says (`mailed`). The address is then the sender's word rather than
the mail's proof, which an owner or admin, who could invite any address, may give. Whoever
opens the link accepts it on the invitation's page (app/api/auth.py), a member removed from the
workspace among them.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.auth import INVITATION_PAGE, mailed_link
from app.api.deps import AccountDep, SessionDep, limited, refuse_past
from app.api.organizations import locked
from app.audit import audit
from app.auth import now
from app.config import get_settings
from app.entitlements import PlanRefused, check_member, read_only
from app.invitations import INVITATION_TTL, is_open, issue
from app.mail import invitation_email, relay_set
from app.models import Invitation, Membership, User
from app.postbox import post
from app.ratelimit import SIGN_INS, by_email
from app.schemas import InvitationIn, InvitationOut, InvitationSentOut

router = APIRouter(tags=["invitations"])

ADMINS_ONLY = (
    "Only the owners and admins of the organisation can invite people and see who is invited. "
    "Ask one of them to."
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
    response_model=InvitationSentOut,
    status_code=201,
    dependencies=[Depends(limited(SIGN_INS))],
)
def invite(body: InvitationIn, account: AccountDep, request: Request, session: SessionDep):
    """Invite an address to the organisation with a role, mail it the link, and give the link
    to the sender too. An address whose invitation expired is invited afresh."""
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
    link = _mail(session, request, account, invitation, token)
    session.commit()
    return _sent(invitation, account.user, link)


@router.post(
    "/organization/invitations/{invitation_id}/resend",
    response_model=InvitationSentOut,
    dependencies=[Depends(limited(SIGN_INS))],
)
def resend_invitation(
    invitation_id: int, account: AccountDep, request: Request, session: SessionDep
):
    """Mail the invitation again, with a new link for INVITATION_TTL from now, given to the
    sender too; the link before stops working. One that expired takes a place among the plan's
    members again."""
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
    link = _mail(session, request, account, invitation, token)
    session.commit()
    return _sent(invitation, account.user, link)


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
) -> str:
    """Post the invitation's link with the change that made it, and return the link."""
    link = mailed_link(request, INVITATION_PAGE, token)
    content = invitation_email(
        account.user.name or account.user.email,
        account.organization.name,
        invitation.role,
        link,
        INVITATION_TTL.days,
    )
    post(session, invitation.email, content, account.organization.id)
    return link


def _sent(invitation: Invitation, inviter: User, link: str) -> InvitationSentOut:
    return InvitationSentOut(
        **_out(invitation, inviter).model_dump(), link=link, mailed=relay_set(get_settings())
    )


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
