"""Who may make an account: anyone while sign-up is open (SIGNUP_OPEN, the default), and while it
is closed, as for a private beta, only whoever is invited.

Closed, the sign-up form and a first sign-in through Google or Microsoft refuse to make an
account with an organisation of its own (app/api/auth.py), and the sign-up page and the public
site (app/site.py) offer the way to ask for one instead: BOOKING_URL, else the support address.
Two ways in stay. An invitation into an organisation (app/invitations.py) makes an account that
joins it, so that a team let in can bring its colleagues. And the operator sends a sign-up link
(`send_sign_up_link`, from the operator's area or `maintenance invite`), the same link the form
mails, which makes an account with an organisation of its own whether sign-up is open or not.
"""

import datetime as dt
import logging
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.config import Settings
from app.mail import invited_to_sign_up_email
from app.models import User
from app.postbox import post

log = logging.getLogger(__name__)

# The page a sign-up link opens, where whoever holds the address chooses the password.
SIGNUP_PAGE = "/finish-signup"
# How long the operator's sign-up link works: longer than the form's day, since the person it
# is for did not just ask for it.
INVITED_FOR = dt.timedelta(days=14)

SIGNUP_CLOSED = (
    "Emudoc is in a private beta, so accounts are made by invitation for now. Ask us for one "
    "from the home page, or open the link in the invitation you were sent."
)
HAS_AN_ACCOUNT = "{email} has an Emudoc account already, so it needs no sign-up link."


class HasAnAccount(Exception):
    """The address has an account already; the message says so."""


def booking(settings: Settings) -> tuple[str, str]:
    """Where a visitor asks for an account, and the words of the button that leads there: the
    home page's way in (app/site.py), and the sign-in pages' while sign-up is closed. The
    booking page, else a message to the support address; without either, which only development
    lacks, the sign-up page while it is open and the sign-in page while it is not."""
    if settings.booking_url:
        return settings.booking_url, "Book a demo"
    if support := settings.support_address:
        return f"mailto:{support}?subject={quote('Access to Emudoc')}", "Request access"
    if settings.signup_open:
        return "/signup", "Request access"
    return "/login", "Sign in"


def send_sign_up_link(
    session: Session, email: str, app_url: str, operator: int | None = None
) -> dt.datetime:
    """Mail `email` a link that makes an account with an organisation of its own, whether
    sign-up is open or not, and return when it stops working. A link sent before to the address
    stops working. Refused for an address that has an account. `operator` is the operator's
    user from the area, None from the maintenance command. The caller commits."""
    email = email.strip().lower()
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HasAnAccount(HAS_AN_ACCOUNT.format(email=email))
    token = auth.issue_link_token(session, None, "sign_up", email, INVITED_FOR)
    link = f"{app_url.rstrip('/')}{SIGNUP_PAGE}#token={quote(token)}"
    post(session, email, invited_to_sign_up_email(link, INVITED_FOR.days))
    # The operator by id: an address is never logged.
    if operator is None:
        log.info("The maintenance command sent a sign-up link.")
    else:
        log.info("Operator %s sent a sign-up link.", operator)
    return auth.now() + INVITED_FOR
