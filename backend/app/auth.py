"""Credentials: passwords, sessions, one-time links, authenticator codes, and the browsers an
account signed in with.

Everything a browser or an email carries is a random token, and the database holds only its
SHA-256, so a copy of the database signs nobody in. Passwords are hashed with Argon2id. The
routes in `api/auth.py` decide when each of these is used; this module only knows how.

A browser that completes a sign-in keeps a cookie of its own (BROWSER_COOKIE), which outlives
the session and signing out, and the account a row for it (`Browser`), so that its failed
sign-ins are counted apart from a stranger's (app/ratelimit.py). It grants nothing else: the
password and the code are asked as from any browser.
"""

import base64
import datetime as dt
import hashlib
import hmac
import logging
import re
import secrets
import struct
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request, Response
from sqlalchemy import Connection, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.encrypted import TOTP_SECRETS, decrypt, encrypt
from app.keyring import Undecryptable
from app.models import AuthToken, Browser, User, UserSession

log = logging.getLogger(__name__)

SESSION_COOKIE = "emudoc_session"
# A session that still waits for the authenticator code is short-lived.
MFA_WINDOW = dt.timedelta(minutes=10)
VERIFY_LINK_TTL = dt.timedelta(hours=24)
RESET_LINK_TTL = dt.timedelta(hours=1)
# How often a session's last-seen time is written back; every request would turn each read
# into a write.
LAST_SEEN_RESOLUTION = dt.timedelta(minutes=5)
# Authenticator apps follow RFC 6238 with these defaults; the issuer names the app in them.
TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_ISSUER = "Emudoc"
RECOVERY_CODE_COUNT = 8
# How long a session or a one-time link is kept once it has ended: the address and browser a
# session recorded, and the address a link went to, are personal data with no use after that.
# The month leaves time to answer a question about a recent sign-in from the rows.
CREDENTIAL_HISTORY = dt.timedelta(days=30)
# The cookie that tells a browser that signed in before from one that has not, for the
# sign-in routes alone, and how long after its last sign-in a browser is told apart; its row
# goes then too.
BROWSER_COOKIE = "emudoc_browser"
BROWSER_PATH = "/api/auth"
BROWSER_KNOWN_FOR = dt.timedelta(days=90)
_BROWSER_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
# Logged when a production request arrives as plain HTTP, which is the proxy's doing.
PLAIN_HTTP = (
    "%s %s reached the app as plain HTTP in production, so its cookie was marked Secure "
    "regardless. If TLS ends at a proxy, have it send X-Forwarded-Proto and X-Forwarded-For "
    "and name its address in FORWARDED_ALLOW_IPS so that uvicorn believes them (deploying.md)."
)

_hasher = PasswordHasher()
# Verified against when an account has no password, so that a sign-in attempt takes as long
# for a password-less account as for a wrong password.
_NO_PASSWORD = _hasher.hash(secrets.token_urlsafe(16))


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# Passwords


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Whether the password matches the hash; no hash matches nothing, in the same time."""
    try:
        return _hasher.verify(password_hash or _NO_PASSWORD, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether the hash was made with weaker parameters than the current ones."""
    return _hasher.check_needs_rehash(password_hash)


# Random tokens


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Sessions


def start_session(
    session: Session, user: User, request: Request, needs_mfa: bool = False
) -> tuple[UserSession, str]:
    """A new session for the user, and the token the cookie carries."""
    token = new_token()
    lifetime = MFA_WINDOW if needs_mfa else dt.timedelta(days=get_settings().session_days)
    row = UserSession(
        user_id=user.id,
        token_hash=token_hash(token),
        needs_mfa=needs_mfa,
        user_agent=(request.headers.get("user-agent") or "")[:500],
        ip=request.client.host if request.client else None,
        expires_at=now() + lifetime,
    )
    session.add(row)
    session.flush()
    return row, token


def find_session(session: Session, token: str) -> UserSession | None:
    """The live session behind a cookie token; None when it is unknown, expired or revoked."""
    row = session.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now():
        return None
    return row


def touch_session(session: Session, row: UserSession) -> None:
    """Record that the session was just used, at most once per resolution period."""
    if now() - row.last_seen_at >= LAST_SEEN_RESOLUTION:
        row.last_seen_at = now()
        session.commit()


def complete_mfa(row: UserSession) -> None:
    """Turn a session that waited for the authenticator code into a full one."""
    row.needs_mfa = False
    row.expires_at = now() + dt.timedelta(days=get_settings().session_days)


def revoke_sessions(session: Session, user_id: int, except_id: int | None = None) -> int:
    """Sign the user out everywhere, except on the session given; returns how many sessions
    that ended."""
    statement = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_id is not None:
        statement = statement.where(UserSession.id != except_id)
    return session.execute(statement.values(revoked_at=now())).rowcount


_PRUNE_SESSIONS = text("DELETE FROM sessions WHERE expires_at < :before OR revoked_at < :before")
_PRUNE_LINKS = text("DELETE FROM auth_tokens WHERE expires_at < :before")
# An invitation accepted or revoked is gone already (app/invitations.py); one that expired is
# kept as long as a one-time link, for its inviter to send again.
_PRUNE_INVITATIONS = text("DELETE FROM invitations WHERE expires_at < :before")
_PRUNE_BROWSERS = text("DELETE FROM browsers WHERE signed_in_at < :before")
_PRUNE_KEYS = text("DELETE FROM api_keys WHERE expires_at < :before")


def prune_credentials(connection: Connection, age: dt.timedelta = CREDENTIAL_HISTORY) -> int:
    """Remove the sessions, one-time links, invitations and API keys that ended longer ago than
    `age`, and the browsers that have not signed in for BROWSER_KNOWN_FOR; returns how many."""
    before = now() - age
    pruned = sum(
        connection.execute(statement, {"before": before}).rowcount
        for statement in (_PRUNE_SESSIONS, _PRUNE_LINKS, _PRUNE_INVITATIONS, _PRUNE_KEYS)
    )
    browsers = connection.execute(_PRUNE_BROWSERS, {"before": now() - BROWSER_KNOWN_FOR})
    return pruned + browsers.rowcount


# Browsers


def remember_browser(session: Session, user: User, request: Request) -> str:
    """Record that this browser completed a sign-in to the user's account, now, and return the
    token its cookie keeps: the one it carries, or a new one for a browser without."""
    token = browser_token(request) or new_token()
    session.execute(
        insert(Browser)
        .values(user_id=user.id, token_hash=_browser_hash(user.id, token))
        .on_conflict_do_update(
            index_elements=[Browser.user_id, Browser.token_hash],
            set_={"signed_in_at": func.now()},
        )
    )
    return token


def known_browser(session: Session, user: User, request: Request) -> int | None:
    """The id of this browser's row in the user's account while it is told apart, or None for
    a browser that has not signed in to it, or not for BROWSER_KNOWN_FOR."""
    token = browser_token(request)
    if token is None:
        return None
    return session.scalar(
        select(Browser.id).where(
            Browser.user_id == user.id,
            Browser.token_hash == _browser_hash(user.id, token),
            Browser.signed_in_at > now() - BROWSER_KNOWN_FOR,
        )
    )


def forget_browsers(session: Session, user_id: int) -> None:
    """Tell none of the user's browsers apart any longer, as when a new password replaces the
    one they signed in with."""
    session.execute(delete(Browser).where(Browser.user_id == user_id))


def browser_token(request: Request) -> str | None:
    """The token of the browser's cookie, when it carries one the app could have made."""
    token = request.cookies.get(BROWSER_COOKIE, "")
    return token if _BROWSER_TOKEN.fullmatch(token) else None


def set_browser_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        BROWSER_COOKIE,
        token,
        max_age=int(BROWSER_KNOWN_FOR.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        path=BROWSER_PATH,
    )


def _browser_hash(user_id: int, token: str) -> str:
    # With the user's id, so that two accounts' rows for one browser differ.
    return token_hash(f"{user_id}:{token}")


def cookie_secure(request: Request) -> bool:
    """Whether a cookie set on this response is marked Secure, so the browser sends it over
    HTTPS only.

    In development the request's own scheme decides, so that http://localhost keeps working.
    A deployment is always behind TLS, so production marks it whatever the request says: a
    request that reaches the app as plain HTTP there means the proxy that ends TLS did not say
    so in X-Forwarded-Proto, or uvicorn was not told to believe it (deploying.md). The log
    names that, since a cookie without the flag would be the quieter mistake.
    """
    if request.url.scheme == "https":
        return True
    if get_settings().environment != "production":
        return False
    # Once for a request, however many cookies it sets.
    if not getattr(request.state, "plain_http", False):
        request.state.plain_http = True
        log.warning(PLAIN_HTTP, request.method, request.url.path)
    return True


def set_session_cookie(
    response: Response, request: Request, token: str, needs_mfa: bool = False
) -> None:
    lifetime = MFA_WINDOW if needs_mfa else dt.timedelta(days=get_settings().session_days)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(lifetime.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")


# One-time links


def issue_link_token(
    session: Session, user: User | None, purpose: str, email: str, lifetime: dt.timedelta
) -> str:
    """A token for a link sent by email: about the user's account, or with no user, about an
    address that has none yet (a sign-up). Earlier unused links of the same purpose, the
    user's or the address's, stop working."""
    if user is None:
        session.execute(
            update(AuthToken)
            .where(
                AuthToken.user_id.is_(None),
                AuthToken.email == email,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
            )
            .values(used_at=now())
        )
    else:
        void_link_tokens(session, user.id, purpose)
    token = new_token()
    session.add(
        AuthToken(
            user_id=user.id if user is not None else None,
            purpose=purpose,
            token_hash=token_hash(token),
            email=email,
            expires_at=now() + lifetime,
        )
    )
    return token


def void_link_tokens(session: Session, user_id: int, purpose: str | None = None) -> None:
    """Stop the user's unused links working: those of one purpose, or every one."""
    statement = update(AuthToken).where(AuthToken.user_id == user_id, AuthToken.used_at.is_(None))
    if purpose is not None:
        statement = statement.where(AuthToken.purpose == purpose)
    session.execute(statement.values(used_at=now()))


def forget_sign_ups(session: Session, email: str) -> None:
    """Delete the sign-up links sent to the address, used or not, once it is an account's or
    no longer: they have no user for the account's deletion to take with it."""
    session.execute(
        delete(AuthToken).where(
            AuthToken.user_id.is_(None), AuthToken.purpose == "sign_up", AuthToken.email == email
        )
    )


def find_link_token(session: Session, token: str, purpose: str) -> AuthToken | None:
    """The link behind a token, left unused; None when it is unknown, used, expired or for
    another purpose."""
    row = session.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash(token), AuthToken.purpose == purpose
        )
    )
    if row is None or row.used_at is not None or row.expires_at <= now():
        return None
    return row


def redeem_link_token(session: Session, token: str, purpose: str) -> AuthToken | None:
    """Use a link's token up; None when it is unknown, used, expired or for another purpose."""
    row = find_link_token(session, token, purpose)
    if row is not None:
        row.used_at = now()
    return row


# Authenticator codes (TOTP) and recovery codes


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def set_totp_secret(user: User, secret: str) -> None:
    """Keep the authenticator's secret encrypted for the user's row (app/encrypted.py): the
    codes it makes are a sign-in's second factor, so a copy of the database must not make
    them."""
    user.totp_secret = encrypt(TOTP_SECRETS, user.id, secret)


def totp_secret(user: User) -> str | None:
    """The authenticator's secret; `Undecryptable` when no key this process holds decrypts
    it."""
    if user.totp_secret is None:
        return None
    return decrypt(TOTP_SECRETS, user.id, user.totp_secret)


def totp_readable(user: User) -> bool:
    """Whether the user's authenticator, if any, can be checked: False when the key that
    encrypted its secret is gone, and only a recovery code signs them in."""
    try:
        totp_secret(user)
    except Undecryptable:
        return False
    return True


def totp_uri(secret: str, email: str) -> str:
    """The otpauth link an authenticator app reads, usually from a QR code."""
    label = quote(f"{TOTP_ISSUER}:{email}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={TOTP_ISSUER}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"
    )


def totp_step(at: dt.datetime | None = None) -> int:
    return int((at or now()).timestamp()) // TOTP_STEP_SECONDS


def totp_code(secret: str, step: int) -> str:
    """The code an authenticator shows during the given step (RFC 6238 over RFC 4226)."""
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{number % 10**TOTP_DIGITS:0{TOTP_DIGITS}d}"


def check_totp(user: User, code: str) -> bool:
    """Whether the code is the authenticator's current one or a neighbour, and not used before.

    One step of clock drift either way is allowed. A code that was accepted, or any older one,
    is refused afterwards, so a code seen over someone's shoulder is worthless once used.
    """
    code = code.replace(" ", "")
    if user.totp_secret is None or len(code) != TOTP_DIGITS or not code.isdigit():
        return False
    try:
        secret = totp_secret(user)
    except Undecryptable as error:
        log.warning("User %s's code could not be checked: %s", user.id, error)
        return False
    current = totp_step()
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp_code(secret, step), code):
            if user.totp_last_step is not None and step <= user.totp_last_step:
                return False
            user.totp_last_step = step
            return True
    return False


def new_recovery_codes() -> list[str]:
    """Codes that stand in for a lost authenticator, each usable once; shown to the user once."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        digits = secrets.token_hex(5)
        codes.append(f"{digits[:5]}-{digits[5:]}")
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    """Whether the code is one of the user's unused recovery codes; it is used up if so."""
    digest = token_hash(code.strip().lower())
    hashes = list(user.recovery_code_hashes or [])
    if digest not in hashes:
        return False
    hashes.remove(digest)
    user.recovery_code_hashes = hashes
    return True
