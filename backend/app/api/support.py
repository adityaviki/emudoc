"""A member's leave for Emudoc's support to see their account in the organisation they work in
(app/support.py): given for some days from the account page, and withdrawn there, each a row of
the organisation's audit log. A view by support changes nothing, so neither can be done from
one."""

from fastapi import APIRouter, Request

from app.api.auth import me_out
from app.api.deps import AccountDep, SessionDep
from app.schemas import MeOut, SupportAccessIn
from app.support import grant, withdraw

router = APIRouter(tags=["support"])


@router.put("/auth/support-access", response_model=MeOut)
def let_support_in(
    body: SupportAccessIn, account: AccountDep, request: Request, session: SessionDep
):
    """Let support see the account in this organisation for the days asked from now, in place
    of a leave given before."""
    grant(session, account, request, body.days)
    session.commit()
    return me_out(session, account)


@router.delete("/auth/support-access", response_model=MeOut)
def keep_support_out(account: AccountDep, request: Request, session: SessionDep):
    """Withdraw the leave, which ends a view resting on it at its next request."""
    withdraw(session, account, request)
    session.commit()
    return me_out(session, account)
