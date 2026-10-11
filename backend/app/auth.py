"""Credentials: passwords, sessions, one-time links and authenticator codes.

Everything a browser or an email carries is a random token, and the database holds only its
SHA-256, so a copy of the database signs nobody in. Passwords are hashed with Argon2id. The
routes in `api/auth.py` decide when each of these is used; this module only knows how.
"""

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthToken, User, UserSession

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


def revoke_sessions(session: Session, user_id: int, except_id: int | None = None) -> None:
    """Sign the user out everywhere, except on the session given."""
    statement = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_id is not None:
        statement = statement.where(UserSession.id != except_id)
    session.execute(statement.values(revoked_at=now()))


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
        secure=request.url.scheme == "https",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")


# One-time links


def issue_link_token(
    session: Session, user: User, purpose: str, email: str, lifetime: dt.timedelta
) -> str:
    """A token for a link sent by email. Earlier unused links of the same purpose stop working."""
    session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now())
    )
    token = new_token()
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=token_hash(token),
            email=email,
            expires_at=now() + lifetime,
        )
    )
    return token


def redeem_link_token(session: Session, token: str, purpose: str) -> AuthToken | None:
    """Use a link's token up; None when it is unknown, used, expired or for another purpose."""
    row = session.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash(token), AuthToken.purpose == purpose
        )
    )
    if row is None or row.used_at is not None or row.expires_at <= now():
        return None
    row.used_at = now()
    return row


# Authenticator codes (TOTP) and recovery codes


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


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
    current = totp_step()
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp_code(user.totp_secret, step), code):
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
