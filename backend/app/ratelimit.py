"""Rate limits: how many requests of a kind one user, one address or one email address may
make in a minute, counted in the database so that every API replica counts the same.

The plan's quotas (app/entitlements.py) and the spending limits (app/spending.py) bound what
an organisation uses over a month or a day; these bound how fast one caller may go, so that a
script cannot flood the routes that cost something (uploads, questions, searches) or guess
at the ones that guard an account (sign-in, sign-up, the reset and verification mails, the
authenticator code). The limits are settings, the same for every organisation (`RATE_*` in
app/config.py, each the requests a minute, zero for none); a rate that differs by plan is an
entitlement.

A request is counted under a limit for a key: the user when there is an account (and the API
key, when it came with one, under a limit of its own), and for the sign-in routes, which have
none yet, the email address the request names under one limit and the client's address (what
uvicorn read from the proxy's X-Forwarded-For, see deploying.md) under another, larger one,
since an office shares an address and an account does not; so a password guessed from many
addresses is held as well as many guessed from one. An email address is counted by its
SHA-256, never as it is: the address a sign-in names may be anyone's, an account's or not, and
neither the table nor the line the refusal logs keeps it.
The count is one row per scope, key and minute, raised with one `INSERT … ON CONFLICT
DO UPDATE` and committed at once, before the route runs: a sign-in that fails for the wrong
password counts all the same, which is the point. The minute is a fixed window, so a caller
can make up to twice the limit across its boundary, which is near enough for what the limits
are for.

A request past a limit is refused with 429, a sentence that names the limit, and a Retry-After
of the seconds until the minute ends (`refuse_past` in app/api/deps.py). The housekeeper
removes the rows of windows that started more than an hour ago (`prune_rate_limits`, from
`housekeeping` in app/maintenance.py).

A minute's rate still lets a patient guesser try 14,400 passwords a day at one account, so the
wrong passwords and codes themselves are counted over an hour as well (FAILED_SIGN_INS,
RATE_FAILED_SIGN_INS an hour; `tally` after each failure, `held` before the next try, which
counts nothing): past the number, the sign-ins they came from are paused until the hour ends,
the right password included, and a password reset ends the pause (`forgive`). A pause must not
be a way to keep someone out, and whoever knows an address can fail its sign-ins, so the
browsers that signed in to the account before (`Browser`, told apart by their cookie in
app/auth.py) are counted apart, each by itself (`by_browser`), on the minute's limit as on the
hour's: a stranger guessing pauses only the browsers the account has never signed in with,
which share the address's count, and the owner signs in from their own as before, or from a
new one after a reset. That is the device cookie of OWASP's guidance, rather than a count per
address and client, which a guesser with many addresses would multiply. A signed-in request
that confirms a change with the password (app/api/auth.py) is counted by its user instead,
since only whoever holds the session can make it.
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

from app.config import get_settings
from app.metrics import RATE_LIMITED
from app.models import User

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=1)
HOUR = timedelta(hours=1)
# How long a window's count is kept once the window started: as long as the longest window,
# so that no count is pruned while it counts, and for the operator to see who was held and why.
HISTORY = HOUR

_COUNT = text(
    """
    INSERT INTO rate_limits (scope, key, window_start, count)
    VALUES (:scope, :key, :start, 1)
    ON CONFLICT (scope, key, window_start) DO UPDATE SET count = rate_limits.count + 1
    RETURNING count
    """
)
_COUNTED = text(
    "SELECT count FROM rate_limits WHERE scope = :scope AND key = :key AND window_start = :start"
)
_FORGIVE = text("DELETE FROM rate_limits WHERE scope = :scope AND key = :key")
_PRUNE = text("DELETE FROM rate_limits WHERE window_start < :before")


@dataclass(frozen=True)
class Limit:
    """One kind of request held to a rate: its scope in the table, the setting that gives the
    rate, what the requests are called in the refusal, and the window the rate is for."""

    scope: str
    setting: str
    what: str
    window: timedelta = WINDOW

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
# The sign-in family (app/api/auth.py) is one scope: a password tried at the sign-in route
# and a reset link asked for are the same guess at the same account, counted for the address
# the request names, or for the browser itself when it signed in to that account before. The
# client's address is held to a scope of its own, with more room.
SIGN_INS = Limit("sign_in", "rate_sign_ins", "sign-in requests")
ADDRESSES = Limit("address", "rate_sign_ins_per_address", "sign-in requests")
# The wrong passwords and codes themselves, an hour, counted by `tally` once a guess failed.
FAILED_SIGN_INS = Limit("failed_sign_in", "rate_failed_sign_ins", "failed sign-ins", HOUR)
# The page's error reports (app/tracking.py), which a page on a sign-in form sends too, so per
# client address.
PAGE_ERRORS = Limit("page_error", "rate_page_errors", "error reports")
# A member's requests to billing (app/api/billing.py), each a session made or read at Stripe.
BILLING = Limit("billing", "rate_billing", "billing requests")
# Every request an API key makes (app/apikeys.py), on top of its member's own rates.
API_REQUESTS = Limit("api_key", "rate_api_requests", "requests")
# Each limit's refusals are counted from zero in the metrics (app/metrics.py).
for _limit in (
    UPLOADS,
    QUESTIONS,
    SEARCHES,
    SIGN_INS,
    ADDRESSES,
    FAILED_SIGN_INS,
    PAGE_ERRORS,
    BILLING,
    API_REQUESTS,
):
    RATE_LIMITED.labels(_limit.scope)


class Key(NamedTuple):
    """Whom a request is counted for: the row's key, and how the refusal names them."""

    value: str
    who: str


def by_user(user: User) -> Key:
    return Key(f"user:{user.id}", "from your account")


def by_address(request: Request) -> Key:
    """The client's address: the browser's, when uvicorn was told to believe the proxy."""
    host = request.client.host if request.client else "unknown"
    return Key(f"ip:{host}", "from your address")


def by_email(email: str) -> Key:
    """The address a sign-in names, whether or not an account has it, however it is typed:
    every browser that has not signed in to its account together."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return Key(f"email:{digest}", "for this email address")


def by_key(key_id: int) -> Key:
    """An API key, by itself, whichever program holds it."""
    return Key(f"key:{key_id}", "with this API key")


def by_browser(browser_id: int) -> Key:
    """A browser that signed in to the account the sign-in names (`Browser`), by itself."""
    return Key(f"browser:{browser_id}", "from this browser")


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
    counts = []
    for limit, key, allowed in counted:
        start = window_start(now, limit.window)
        count = session.scalar(_COUNT, {"scope": limit.scope, "key": key.value, "start": start})
        counts.append((limit, key, allowed, start, count))
    session.commit()
    for limit, key, allowed, start, count in counts:
        if count > allowed:
            _refused(limit, key, count, start, "more than")
            return Refusal(limit.refusal(allowed, key.who), seconds_left(now, start))
    return None


def held(
    session: Session, limit: Limit, key: Key, now: datetime | None = None
) -> tuple[int, int] | None:
    """Whether `key` has used up `limit` in the current window, without counting anything:
    the number allowed and the seconds until the window ends, or None while it may go on.

    For a count that `tally` raises after the fact, such as a sign-in that failed: the try
    after the last one allowed is refused before anything is checked."""
    allowed = getattr(get_settings(), limit.setting)
    if not allowed:
        return None
    now = now or datetime.now(UTC)
    start = window_start(now, limit.window)
    count = session.scalar(_COUNTED, {"scope": limit.scope, "key": key.value, "start": start})
    if count is None or count < allowed:
        return None
    _refused(limit, key, count, start, "as many as")
    return allowed, seconds_left(now, start, limit.window)


def tally(session: Session, limit: Limit, key: Key, now: datetime | None = None) -> int | None:
    """Count one under `limit` for `key` in the current window and return the window's count
    so far, or None when the limit is off and nothing is counted. The caller commits, with
    whatever else it records of the failure."""
    if not getattr(get_settings(), limit.setting):
        return None
    start = window_start(now or datetime.now(UTC), limit.window)
    return session.scalar(_COUNT, {"scope": limit.scope, "key": key.value, "start": start})


def window_end(limit: Limit, now: datetime | None = None) -> datetime:
    """When the current window of `limit` ends, and a pause it holds with it."""
    return window_start(now or datetime.now(UTC), limit.window) + limit.window


def forgive(session: Session, limit: Limit, *keys: Key) -> None:
    """Forget the counts of `keys` under `limit`, every window's; the caller commits."""
    for key in keys:
        session.execute(_FORGIVE, {"scope": limit.scope, "key": key.value})


def _refused(limit: Limit, key: Key, count: int, start: datetime, measure: str) -> None:
    window = "minute" if limit.window == WINDOW else "hour"
    log.warning(
        "A request was refused: %s has made %s %s in the %s from %s, %s %s allows",
        key.value,
        count,
        limit.what,
        window,
        start.isoformat(timespec="seconds"),
        measure,
        limit.variable,
    )
    RATE_LIMITED.labels(limit.scope).inc()


def window_start(now: datetime, window: timedelta = WINDOW) -> datetime:
    """The start of the window of the given length that `now` falls in, in UTC: the minute or
    the hour, counted from midnight."""
    moment = now.astimezone(UTC)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + (moment - midnight) // window * window


def seconds_left(now: datetime, start: datetime, window: timedelta = WINDOW) -> int:
    """The seconds until the window that started at `start` ends, at least one."""
    return max(1, math.ceil((start + window - now).total_seconds()))


def prune_rate_limits(connection: Connection, age: timedelta = HISTORY) -> int:
    """Remove the counts of minutes that ended longer ago than `age`; returns how many."""
    before = datetime.now(UTC) - age
    return connection.execute(_PRUNE, {"before": before}).rowcount
