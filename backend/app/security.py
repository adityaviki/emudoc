"""An organisation's security settings: requiring two-factor sign-in of everyone who works in
it, and how long after signing in a session may work in it.

Both are the organisation's own (columns of `organizations`), chosen by its owners and admins on
its page (app/api/organizations.py), and held at the request, in the organisation the session
works in (app/accounts.py).

Two-factor: a member whose account signs in without a second factor is held, as an account that
has not accepted the current terms is (`get_secured_account` in app/api/deps.py). The page
offers them the way to set up an authenticator app, or to sign out, and the API refuses the rest
with 403. An account that signs in through Google or
Microsoft sets one up too, since the provider's own second factor, if it asks one, is not seen
by the app. Nobody turns the requirement on without signing in with two-factor themselves, so
the owner or admin who asks is never held by their own choice; and every session of an account
with two-factor on has had its code, since turning it on ends the others (`enable_mfa` in
app/api/auth.py). An API key is held with its member (app/apikeys.py). The local account of
development has no session and is never held. The notification centre leaves out the notices
of an organisation that holds its user, which name its documents: its routes take the account
that is held (app/api/notifications.py); their mail cannot be held back and still goes.

Sessions: a session that signed in longer ago than the organisation it works in allows ends at
its next request, which is answered with 401 as an expired session's is, and its user signs in
again (`get_account` in app/api/deps.py). The deployment's SESSION_DAYS is the most an
organisation may choose, since no session lasts longer. An API key has no session to end: it
runs out on its own day, or is rolled or revoked.
"""

import datetime as dt

from app.accounts import Account
from app.models import Organization, UserSession

MFA_YOURS_FIRST = (
    "Turn on two-factor sign-in for your own account first, on your account page: once it is "
    "required, nobody works in the organisation without it, you included."
)


def mfa_due(account: Account) -> bool:
    """Whether the account's organisation requires two-factor sign-in and its user signs in
    without it, so that the account may do nothing there but set it up, leave or go elsewhere."""
    return (
        not account.is_local and account.organization.mfa_required and not account.user.mfa_enabled
    )


def mfa_pending(organization: Organization) -> str:
    """What a request from an account `mfa_due` holds is refused with, for whoever calls the API
    without the app, or with a page loaded before the requirement was turned on."""
    return (
        f"{organization.name} asks everyone who works in it to sign in with two-factor. Set up "
        "an authenticator app for your account to go on working in it: reload the page for the "
        "way."
    )


def signed_in_too_long(
    organization: Organization, user_session: UserSession, now: dt.datetime | None = None
) -> bool:
    """Whether the session signed in longer ago than the organisation lets one work in it."""
    if organization.session_days is None:
        return False
    signed_in = user_session.created_at + dt.timedelta(days=organization.session_days)
    return signed_in <= (now or dt.datetime.now(dt.UTC))


def sign_in_again(organization: Organization) -> str:
    """What a session `signed_in_too_long` ends with."""
    return (
        f"{organization.name} asks its members to sign in again {every(organization.session_days)}"
        ". Sign in to continue."
    )


def longer_than_sessions(days: int) -> str:
    """What choosing a session longer than the deployment keeps one is refused with."""
    return (
        f"Emudoc signs everyone in again {every(days)} at the most, so the organisation cannot "
        f"let a session last longer. Choose {days:,} days or fewer."
    )


def every(days: int | None) -> str:
    """How often a member signs in again, in words: "every day", "every 7 days"."""
    return "every day" if days == 1 else f"every {days:,} days"
