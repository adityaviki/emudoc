"""Support's view of a member's account: Emudoc's support sees what a member sees in one of
their organisations, when the member lets it, and changes nothing.

The consent is the member's own and is for one organisation: on the account page they let
Emudoc's support see their account in the organisation they work in, for a day or a week
(SUPPORT_DAYS at most), and withdraw it whenever they like (`grant`, `withdraw`), each a row of
that organisation's audit log, which its owners and admins read. It is for that organisation
alone, since another one's projects are another organisation's to show, and it goes with the
membership.

An operator (app/operators.py) opens the view from the operator's area while the leave stands:
their own session, never the member's, then acts as the member in that organisation (`start`),
for SUPPORT_SESSION at most. The view is read only: a request that would change something is
refused (`get_account` in app/api/deps.py), since what support changed as the member would be
the member's act in every record. Opening it is a row of the organisation's audit log naming
the operator and the member, and the member is mailed at once; what the member's own requests
would record, a download, records the operator too (`detail.support`, app/audit.py), and every
log line of the view names the operator (`operator_id`, app/logs.py).

The view ends when the operator ends it or signs out (a row of the log), and when the request
that finds it can no longer go on: an hour gone, the leave withdrawn or run out, the member no
longer in the organisation, or the request no longer let into the operator's area (`viewed`).
That request is refused with 409 and the organisation the session works in once more, so that
the page starts again as the operator's own.

The view holds the member's account to everything it is held to (the terms to accept, the
organisation's two-factor sign-in, a deletion asked for) and shows that organisation alone: the
notification centre shows none of another's notices, and the account's export, which holds
every organisation of theirs, is refused.
"""

import datetime as dt
import logging

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.accounts import Account
from app.audit import audit, audit_event
from app.auth import now
from app.models import Membership, Organization, SupportGrant, User, UserSession
from app.operators import from_operator_network, is_operator, two_factor_due

log = logging.getLogger(__name__)

# The longest a member lets support see their account at once; the page offers a day or this.
SUPPORT_DAYS = 7
# The longest one view lasts; the operator opens another while the leave stands.
SUPPORT_SESSION = dt.timedelta(hours=1)

# What a view refuses, to the operator who opened it.
READ_ONLY_VIEW = (
    "You are viewing this account as Emudoc's support, which changes nothing: ask the member "
    "to make the change, or end the view to act as yourself."
)
# What the request that finds a view over is told, though the page starts again at once.
VIEW_OVER = (
    "Emudoc's support no longer views this account: the view ended, or the member withdrew "
    "their leave. Reload the page to continue as yourself."
)
# What an operator is told who would open a view without the member's leave.
NO_LEAVE = (
    "This member has not let Emudoc's support see their account in this organisation, or the "
    "time they gave has run out. Ask them to allow it on their account page."
)
# The account's export holds every organisation of the member's, which a view does not show.
NOT_IN_A_VIEW = (
    "Emudoc's support does not download a member's data, which holds every organisation of "
    "theirs: ask the member for it."
)


def leave_of(session: Session, org_id: int, user_id: int) -> SupportGrant | None:
    """The member's leave for support in the organisation, while it stands."""
    grant = session.scalar(
        select(SupportGrant).where(SupportGrant.org_id == org_id, SupportGrant.user_id == user_id)
    )
    return grant if grant is not None and grant.expires_at > now() else None


def grant(session: Session, account: Account, request: Request, days: int) -> dt.datetime:
    """Let support see the member's account in the organisation for `days` from now, in place
    of any leave before; returns until when. The caller commits."""
    until = now() + dt.timedelta(days=days)
    session.execute(
        insert(SupportGrant)
        .values(org_id=account.organization.id, user_id=account.user.id, expires_at=until)
        .on_conflict_do_update(
            index_elements=[SupportGrant.org_id, SupportGrant.user_id],
            set_={"expires_at": until},
        )
    )
    audit(
        session,
        account,
        request,
        "support_access_granted",
        detail={"days": days, "until": until.isoformat()},
    )
    return until


def withdraw(session: Session, account: Account, request: Request) -> bool:
    """Take the member's leave for support back, which ends a view resting on it; False when
    none stood. The caller commits."""
    withdrawn = session.execute(
        delete(SupportGrant).where(
            SupportGrant.org_id == account.organization.id,
            SupportGrant.user_id == account.user.id,
            SupportGrant.expires_at > now(),
        )
    ).rowcount
    if withdrawn:
        audit(session, account, request, "support_access_revoked")
    return bool(withdrawn)


def start(
    session: Session,
    user_session: UserSession,
    leave: SupportGrant,
    operator: User,
    request: Request,
) -> dt.datetime:
    """Turn the operator's session into a view of the member's account the leave is for, in
    place of any view it held, and record it in the organisation's log; returns when it ends.
    The caller mails the member and commits."""
    if user_session.support_ends_at is not None:
        end(session, user_session, operator, request)
    user_session.support_grant_id = leave.id
    user_session.support_ends_at = now() + SUPPORT_SESSION
    audit_event(
        session,
        "support_session_started",
        org_id=leave.org_id,
        request=request,
        target=("user", leave.user_id),
        detail={"operator": operator.id, "ends_at": user_session.support_ends_at.isoformat()},
    )
    log.info(
        "Operator %s views user %s in organisation %s", operator.id, leave.user_id, leave.org_id
    )
    return user_session.support_ends_at


def end(session: Session, user_session: UserSession, operator: User, request: Request) -> None:
    """End the view the operator's session holds, by their act: a row of the organisation's
    log while the leave it rested on is there to say which. The caller commits."""
    leave = (
        session.get(SupportGrant, user_session.support_grant_id)
        if user_session.support_grant_id is not None
        else None
    )
    if leave is not None:
        audit_event(
            session,
            "support_session_ended",
            org_id=leave.org_id,
            request=request,
            target=("user", leave.user_id),
            detail={"operator": operator.id},
        )
    stop(user_session)


def stop(user_session: UserSession) -> None:
    """Make the session the operator's own again. The caller commits."""
    user_session.support_grant_id = None
    user_session.support_ends_at = None


def viewed(
    session: Session, request: Request, user_session: UserSession, operator: User
) -> Account | None:
    """The member's account the operator's session views, as the member holds it in that
    organisation, while the view may go on; None once it may not."""
    leave = (
        session.get(SupportGrant, user_session.support_grant_id)
        if user_session.support_grant_id is not None
        else None
    )
    if (
        leave is None
        or leave.expires_at <= now()
        or user_session.support_ends_at is None
        or user_session.support_ends_at <= now()
        or not from_operator_network(request)
        or not is_operator(session, operator.id)
        or two_factor_due(operator)
    ):
        return None
    # The leave goes with the membership, so the member is still one.
    role = session.scalar(
        select(Membership.role).where(
            Membership.org_id == leave.org_id, Membership.user_id == leave.user_id
        )
    )
    return Account(
        session.get(Organization, leave.org_id),
        session.get(User, leave.user_id),
        role,
        user_session.id,
        operator_id=operator.id,
    )
