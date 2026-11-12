"""API keys: how a program calls the API in place of a member's session.

An organisation's owners and admins make its keys on its page (app/api/apikeys.py), each with a
name, a scope and, if they choose, a day it runs out. A key acts as the member who made it, in
the organisation it was made in: it finds what they find there (a private project only if they
see it, app/access.py) and changes what their role lets them change, as their role stands at
each request, so a key does less as soon as its member may. Its scope narrows that further: a
`read` key makes only the requests that change nothing (GET and HEAD), a `write` key any its
member's role allows. The key goes with its member's membership, when they leave or are removed,
and with the organisation or the account, by the database's cascade (app/models.py), and its
member's account holds it as it holds them: while the account is set to be deleted, has yet to
accept the current terms, or is held by the organisation's two-factor requirement, the key is
refused what they would be.

A key reaches the organisation's projects and what they hold, the routes a program has a use for
(KEYED in app/main.py: the projects, folders, documents, search and facts, chats and the trash),
and nothing of the accounts or of the organisation itself: no key signs in, makes another key,
invites anyone, changes a role or reads the audit log, so a key that leaks reads or changes
documents, never who may.

A request names its key in `Authorization: Bearer <key>`. A key is `emk_` and 43 characters of
`secrets.token_urlsafe`; only its SHA-256 is stored, as a session's is (app/auth.py), so a copy
of the database calls nothing, and the page shows a key once, as it is made or rolled, and its
first characters after that (`hint`), to tell keys apart. Rolling a key gives it a new secret and
ends the one before at once, its name, scope and the day it runs out kept; only its own member
rolls it, since whoever holds the new secret acts as them. When a key was last used is kept to
LAST_SEEN_RESOLUTION, as a session's last sight is, so a key in a loop writes its row every five
minutes, not at every request. Every request with a key is counted for it (API_REQUESTS,
app/ratelimit.py), on top of its member's own rates.

A key carries no cookie, so the defence against a page on another site acting with a member's
session (`refuse_cross_site` in app/api/deps.py) does not ask it for the app's header: a browser
adds a Bearer token to no request by itself, as it does Basic credentials, and a page elsewhere
that adds one makes the browser ask first, which the app never answers. A request that carries
one is judged by its key alone, never by a cookie it may carry as well.

Each key made, rolled or revoked is a row of the organisation's audit log, and so is each act of
a key's that the log records, as its member's with the key named (`audit` in app/audit.py). The
housekeeper deletes a key CREDENTIAL_HISTORY after it ran out (`prune_credentials` in
app/auth.py).
"""

import datetime as dt
import secrets

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import zone_of
from app.auth import LAST_SEEN_RESOLUTION, now, token_hash
from app.models import ApiKey, Membership, Organization, User
from app.retention import day

PREFIX = "emk_"
# How much of a key the page goes on showing once it is made: the prefix and four of the 43
# characters after it, which leaves a guesser 39 to find.
HINT = len(PREFIX) + 4
# The most keys one organisation holds, so that a script cannot fill the table.
MAX_KEYS = 50

KEY_UNKNOWN = (
    "This API key is not one Emudoc knows: it was revoked, rolled or mistyped. An owner or admin "
    "of the workspace makes a new one on its page."
)
READ_ONLY_KEY = (
    "This API key may only read: it was made with the read scope. An owner or admin of the "
    "workspace makes one with the write scope on its page to change anything."
)
ELSEWHERE = (
    "An API key reaches the workspace's projects, folders, documents, search, facts, chats and "
    "trash, and nothing else. Sign in to the app for the rest."
)
TOO_MANY = (
    f"The workspace holds {MAX_KEYS} API keys, as many as it may. Revoke one it no longer uses "
    "to make another."
)
NAME_IT = "Name the key, so that you can tell it from the others."
ROLL_YOUR_OWN = (
    "Only the member who made a key can roll it, since it acts as them. Revoke it instead, and "
    "make one of your own."
)
# What a refusal of a key tells the client to send next time, as RFC 6750 says.
CHALLENGE = {"WWW-Authenticate": "Bearer"}


def bearer(request: Request) -> str | None:
    """The token of the request's `Authorization: Bearer` header, or None without one. Another
    scheme is no key's, and no proof the request is a program's: a browser sends Basic
    credentials it was given by itself, with a page elsewhere's requests too."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip()


def issue(key: ApiKey) -> str:
    """Give the key a new secret and return it, to be shown once; the one before stops working.
    The caller commits."""
    secret = PREFIX + secrets.token_urlsafe(32)
    key.token_hash = token_hash(secret)
    key.hint = secret[:HINT]
    return secret


def runs_out(days: int | None) -> dt.datetime | None:
    """When a key made now for `days` runs out, None for one that runs until it is revoked."""
    return None if days is None else now() + dt.timedelta(days=days)


def find_key(session: Session, secret: str) -> tuple[ApiKey, Organization, User, str] | None:
    """The key a request names, with its organisation, its member and their role there; None
    when no key has that secret, out of date or not."""
    if not secret.startswith(PREFIX):
        return None
    row = session.execute(
        select(ApiKey, Organization, User, Membership.role)
        .join(Organization, Organization.id == ApiKey.org_id)
        .join(User, User.id == ApiKey.user_id)
        .join(
            Membership,
            (Membership.org_id == ApiKey.org_id) & (Membership.user_id == ApiKey.user_id),
        )
        .where(ApiKey.token_hash == token_hash(secret))
    ).first()
    return None if row is None else tuple(row)


def expired(key: ApiKey, at: dt.datetime | None = None) -> bool:
    return key.expires_at is not None and key.expires_at <= (at or now())


def key_expired(key: ApiKey, user: User) -> str:
    """What a key past the day it ran out is refused with, the day in its member's zone."""
    return (
        f"This API key ran out on {day(key.expires_at, zone_of(user.time_zone))}. An owner or "
        "admin of the workspace makes a new one on its page."
    )


def touch_key(session: Session, key: ApiKey) -> None:
    """Record that the key was just used, at most once per resolution period."""
    if key.last_used_at is None or now() - key.last_used_at >= LAST_SEEN_RESOLUTION:
        key.last_used_at = now()
        session.commit()
