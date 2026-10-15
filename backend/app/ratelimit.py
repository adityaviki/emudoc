"""Rate limits: how many requests of a kind one user, one address or one email address may
make in a minute, counted in the database so that every API replica counts the same.

The plan's quotas (app/entitlements.py) and the spending limits (app/spending.py) bound what
an organisation uses over a month or a day; these bound how fast one caller may go, so that a
script cannot flood the routes that cost something (uploads, questions, searches) or guess
at the ones that guard an account (sign-in, sign-up, the reset and verification mails, the
authenticator code). The limits are settings, the same for every organisation (`RATE_*` in
app/config.py, each the requests a minute, zero for none); a rate that differs by plan is an
entitlement.

A request is counted under a limit for a key: the user when there is an account, and for
the sign-in routes, which have none yet, the email address the request names under one limit
and the client's address (what uvicorn read from the proxy's X-Forwarded-For, see
deploying.md) under another, larger one, since an office shares an address and an account
does not; so a password guessed from many addresses is held as well as many guessed from
one. An email address is counted by its SHA-256, never as it is: the address a sign-in names may
be anyone's, an account's or not, and neither the table nor the line the refusal logs keeps it.
The count is one row per scope, key and minute, raised with one `INSERT … ON CONFLICT
DO UPDATE` and committed at once, before the route runs: a sign-in that fails for the wrong
password counts all the same, which is the point. The minute is a fixed window, so a caller
can make up to twice the limit across its boundary, which is near enough for what the limits
are for.

A request past a limit is refused with 429, a sentence that names the limit, and a Retry-After
of the seconds until the minute ends (`refuse_past` in app/api/deps.py). The housekeeper
removes the rows older than an hour (`prune_rate_limits`, from `housekeeping` in
app/maintenance.py).
"""

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from fastapi import Request
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.accounts import Account
from app.config import get_settings

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=1)
# How long the counted minutes are kept, for the operator to see who was held and why.
HISTORY = timedelta(hours=1)

_COUNT = text(
    """
    INSERT INTO rate_limits (scope, key, window_start, count)
    VALUES (:scope, :key, :start, 1)
    ON CONFLICT (scope, key, window_start) DO UPDATE SET count = rate_limits.count + 1
    RETURNING count
    """
)
_PRUNE = text("DELETE FROM rate_limits WHERE window_start < :before")


@dataclass(frozen=True)
class Limit:
    """One kind of request held to a rate: its scope in the table, the setting that gives the
    rate, and what the requests are called in the refusal."""

    scope: str
    setting: str
    what: str

    @property
    def variable(self) -> str:
        """The setting as the environment names it, for the log."""
        return self.setting.upper()

    def refusal(self, allowed: int, who: str) -> str:
        return (
            f"There have been {allowed} {self.what} {who} in the last minute, as many as the "
            "app allows. Wait a minute and try again."
        )


UPLOADS = Limit("upload", "rate_uploads", "uploads")
QUESTIONS = Limit("question", "rate_questions", "questions")
SEARCHES = Limit("search", "rate_searches", "searches")
# The sign-in family (app/api/auth.py) is one scope per account: a password tried at the
# sign-in route and a reset link asked for are the same guess at the same account. The
# address it comes from is held to a scope of its own, with more room.
SIGN_INS = Limit("sign_in", "rate_sign_ins", "sign-in requests")
ADDRESSES = Limit("address", "rate_sign_ins_per_address", "sign-in requests")


class Key(NamedTuple):
    """Whom a request is counted for: the row's key, and how the refusal names them."""

    value: str
    who: str


def by_user(account: Account) -> Key:
    return Key(f"user:{account.user.id}", "from your account")


def by_address(request: Request) -> Key:
    """The client's address: the browser's, when uvicorn was told to believe the proxy."""
    host = request.client.host if request.client else "unknown"
    return Key(f"ip:{host}", "from your address")


def by_email(email: str) -> Key:
    """The address a sign-in names, whether or not an account has it, however it is typed."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return Key(f"email:{digest}", "for this email address")


@dataclass(frozen=True)
class Refusal:
    """Why a request is refused and the seconds until the same one would be allowed."""

    sentence: str
    retry_after: int


def hit(session: Session, *hits: tuple[Limit, Key], now: datetime | None = None) -> Refusal | None:
    """Count one request under each limit for its key and commit the counts, then say whether
    one of them is past its rate: the refusal, or None when the request may go on.

    Every key is counted before any is judged, so that an address trying many email addresses
    is counted for each one it tries. The commit is the request session's: nothing of the
    route has run yet, and the count must stand however the route ends.
    """
    settings = get_settings()
    counted = [(limit, key, getattr(settings, limit.setting)) for limit, key in hits]
    counted = [(limit, key, allowed) for limit, key, allowed in counted if allowed]
    if not counted:
        return None
    now = now or datetime.now(UTC)
    start = window_start(now)
    counts = [
        (
            limit,
            key,
            allowed,
            session.scalar(_COUNT, {"scope": limit.scope, "key": key.value, "start": start}),
        )
        for limit, key, allowed in counted
    ]
    session.commit()
    for limit, key, allowed, count in counts:
        if count > allowed:
            log.warning(
                "A request was refused: %s has made %s %s in the minute from %s, more than "
                "%s allows",
                key.value,
                count,
                limit.what,
                start.isoformat(timespec="seconds"),
                limit.variable,
            )
            return Refusal(limit.refusal(allowed, key.who), seconds_left(now, start))
    return None


def window_start(now: datetime) -> datetime:
    """The start of the minute `now` falls in, in UTC."""
    return now.astimezone(UTC).replace(second=0, microsecond=0)


def seconds_left(now: datetime, start: datetime) -> int:
    """The seconds until the minute that started at `start` ends, at least one."""
    return max(1, math.ceil((start + WINDOW - now).total_seconds()))


def prune_rate_limits(connection: Connection, age: timedelta = HISTORY) -> int:
    """Remove the counts of minutes that ended longer ago than `age`; returns how many."""
    before = datetime.now(UTC) - age
    return connection.execute(_PRUNE, {"before": before}).rowcount
