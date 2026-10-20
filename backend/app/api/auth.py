"""Sign-in: accounts with a password, sign-in through Google or Microsoft, the links that
confirm an address or reset a password, authenticator codes, the sessions a user holds, and the
account itself: its user's name, time zone and email address, the terms they accepted
(app/legal/), a copy of what is kept about them, and its deletion (app/retention.py).

An account moves to a new address through a link sent there, which works signed in or not, as
a verification link does; until it is opened the account keeps its address. The answer to the
request is the same whether the new address has an account or not, and an address that has
one is told so in a message instead, so that nobody learns from the app who uses it. Once the
link is opened, every link sent to the old address stops working, and the old address is told,
since it no longer signs in; the sessions stay, as the change says nothing about who holds
them.

The routes that take a password, a code or an address to mail are held to a rate
(app/ratelimit.py): RATE_SIGN_INS a minute per email address, and RATE_SIGN_INS_PER_ADDRESS
per client address, so that neither a password nor an authenticator code can be guessed at
speed and nobody's inbox can be flooded with links.
"""

from contextlib import suppress
from dataclasses import replace
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth
from app.accounts import Account, account_for_user, create_account, time_zones, zone_of
from app.api.deps import (
    AccountDep,
    AnyAccountDep,
    HttpDep,
    LiveAccountDep,
    MailerDep,
    SessionDep,
    limited,
    refuse_past,
)
from app.audit import audit, audit_sign_in
from app.config import get_settings
from app.entitlements import entitlements_of
from app.export import account_export
from app.legal import TERMS_VERSION, terms_due
from app.mail import (
    Mailer,
    MailError,
    account_deletion_email,
    address_taken_email,
    email_change_email,
    email_changed_email,
    password_reset_email,
    verification_email,
)
from app.models import Identity, User, UserSession
from app.oidc import (
    OAUTH_COOKIE,
    OAUTH_WINDOW_SECONDS,
    Claims,
    Handshake,
    OidcError,
    Provider,
    begin,
    finish,
    providers,
)
from app.ratelimit import ADDRESSES, SIGN_INS, by_address, by_email
from app.retention import DELETION_GRACE, LOCAL_ACCOUNT, day, deletion_refusal
from app.schemas import (
    AccountDeletionIn,
    AccountExport,
    AuthOptions,
    CodeIn,
    EmailChangeIn,
    EmailIn,
    LinkTokenIn,
    LoginIn,
    LoginOut,
    MeOut,
    MfaDisableIn,
    MfaSetupOut,
    OAuthProviderOut,
    OrganizationOut,
    PasswordChangeIn,
    PasswordResetIn,
    ProfileIn,
    RecoveryCodesOut,
    SessionOut,
    SignupIn,
    TermsIn,
)
from app.storage import content_disposition

router = APIRouter(prefix="/auth", tags=["auth"])

# Pages of the frontend (frontend/src/account/): the links in emails and the redirects after an
# OAuth sign-in point at them.
LOGIN_PAGE = "/login"
VERIFY_PAGE = "/verify"
RESET_PAGE = "/reset-password"
EMAIL_PAGE = "/confirm-email"

SIGN_IN_FAILED = "The email or password is not right."
LINK_INVALID = "This link has expired or was already used. Request a new one."
CODE_INVALID = (
    "That code is not right. Enter the current code from your authenticator app, "
    "or one of your recovery codes."
)
MFA_FIRST = "Sign in with your password first."
WRONG_PASSWORD = "The password is not right."
TYPE_EMAIL = "Type the email address of your account to confirm."
UNKNOWN_ZONE = "Choose a time zone from the list, such as Europe/Berlin or UTC."
SAME_ADDRESS = "That is your account's address already. Type the new one."
LOCAL_ADDRESS = (
    "The local account's address cannot change: it is what this machine works as without "
    "signing in, and it receives no mail."
)
ADDRESS_TAKEN = (
    "That address became another Emudoc account's after the link was sent, so it cannot be "
    "this account's. Sign in with it, or ask for the change again with another address."
)
# Whom the old address is told to write to when its account moved without its owner asking.
NO_CONTACT = "the people who run this Emudoc service"
ACCEPT_TERMS = "Accept the terms of service and the privacy policy to create an account."
TERMS_CHANGED = (
    "The terms of service and the privacy policy changed while you were reading them. Read "
    "the new version and accept it."
)


class SignInRefused(Exception):
    """The provider's account cannot be used here; the message says why."""


# Accounts and sessions


@router.get("/options", response_model=AuthOptions)
def options():
    """What the sign-in page can offer: the identity providers that are set up."""
    return AuthOptions(
        providers=[
            OAuthProviderOut(name=p.name, label=p.label) for p in providers(get_settings()).values()
        ]
    )


@router.post("/signup", response_model=MeOut, status_code=201)
def signup(
    body: SignupIn, request: Request, response: Response, session: SessionDep, mailer: MailerDep
):
    """Create an account with its own organisation, sign it in and send the verification link.
    The form carries the edition of the terms its user accepted, which must be the current one.
    """
    refuse_past(session, (ADDRESSES, by_address(request)))
    if body.terms_version is None:
        raise HTTPException(400, ACCEPT_TERMS)
    if body.terms_version != TERMS_VERSION:
        raise HTTPException(409, TERMS_CHANGED)
    email = body.email.strip().lower()
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    account = create_account(
        session,
        email,
        body.name.strip(),
        auth.hash_password(body.password),
        time_zone=body.time_zone,
    )
    user_session, token = auth.start_session(session, account.user, request)
    audit(session, account, request, "sign_up", detail={"method": "password"})
    _accept_terms(session, account, request)
    session.commit()
    _send_verification(session, account.user, request, mailer)
    auth.set_session_cookie(response, request, token)
    return _me(replace(account, session_id=user_session.id))


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response, session: SessionDep):
    """Check the password and start a session; with two-factor on, the code must follow."""
    refuse_past(session, (ADDRESSES, by_address(request)), (SIGN_INS, by_email(body.email)))
    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not auth.verify_password(body.password, user.password_hash):
        # An address with no account is a stranger's and leaves no row (app/audit.py).
        if user is not None:
            audit_sign_in(session, user, request, "sign_in_failed", {"reason": "password"})
            session.commit()
        raise HTTPException(401, SIGN_IN_FAILED)
    if auth.needs_rehash(user.password_hash):
        user.password_hash = auth.hash_password(body.password)
    _, token = auth.start_session(session, user, request, needs_mfa=user.mfa_enabled)
    # With two-factor on, the sign-in is recorded once the code has been entered.
    if not user.mfa_enabled:
        audit_sign_in(session, user, request, "sign_in", {"method": "password"})
    session.commit()
    auth.set_session_cookie(response, request, token, needs_mfa=user.mfa_enabled)
    return LoginOut(mfa_required=user.mfa_enabled)


@router.post("/mfa/verify", response_model=MeOut)
def verify_mfa(body: CodeIn, request: Request, response: Response, session: SessionDep):
    """Finish a sign-in that waits for the authenticator code, or a recovery code."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    user_session = auth.find_session(session, token) if token else None
    if user_session is None or not user_session.needs_mfa:
        raise HTTPException(401, MFA_FIRST)
    user = session.get(User, user_session.user_id)
    # Counted for the account as well as the address: a code has a million values, and the
    # session that waits for it could be tried from anywhere.
    refuse_past(session, (ADDRESSES, by_address(request)), (SIGN_INS, by_email(user.email)))
    if auth.check_totp(user, body.code):
        factor = "authenticator"
    elif auth.use_recovery_code(user, body.code):
        factor = "recovery_code"
    else:
        audit_sign_in(session, user, request, "sign_in_failed", {"reason": "code"})
        session.commit()
        raise HTTPException(401, CODE_INVALID)
    auth.complete_mfa(user_session)
    account = account_for_user(session, user, user_session.id)
    audit_sign_in(session, user, request, "sign_in", {"second_factor": factor})
    session.commit()
    auth.set_session_cookie(response, request, token)
    return _me(account)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: SessionDep):
    """End the session, and have the browser drop what it may still hold of the account's
    pages and files, for whoever uses it next."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    user_session = auth.find_session(session, token) if token else None
    if user_session is not None:
        user_session.revoked_at = auth.now()
        if not user_session.needs_mfa:
            audit_sign_in(session, session.get(User, user_session.user_id), request, "sign_out")
        session.commit()
    auth.clear_session_cookie(response)
    response.headers["Clear-Site-Data"] = '"cache"'


@router.get("/me", response_model=MeOut)
def me(account: AnyAccountDep):
    return _me(account)


@router.post("/terms", response_model=MeOut)
def accept_terms(body: TermsIn, account: LiveAccountDep, request: Request, session: SessionDep):
    """Accept the current terms of service and privacy policy: what the app asks first of an
    account that signed up through a provider, or that accepted an older edition."""
    if body.version != TERMS_VERSION:
        raise HTTPException(409, TERMS_CHANGED)
    if account.user.terms_version != TERMS_VERSION:
        _accept_terms(session, account, request)
        session.commit()
    return _me(account)


# The profile and the address


@router.patch("/profile", response_model=MeOut)
def update_profile(body: ProfileIn, account: AccountDep, request: Request, session: SessionDep):
    """Change the user's name or time zone. The audit log records which changed, never to
    what, since its rows outlive the account (app/audit.py)."""
    user = account.user
    changed = []
    if body.name is not None and body.name.strip() != user.name:
        user.name = body.name.strip()
        changed.append("name")
    if body.time_zone is not None and body.time_zone != user.time_zone:
        if body.time_zone not in time_zones():
            raise HTTPException(400, UNKNOWN_ZONE)
        user.time_zone = body.time_zone
        changed.append("time_zone")
    if changed:
        audit(session, account, request, "profile_changed", detail={"changed": changed})
        session.commit()
    return _me(account)


@router.post("/email", status_code=202, dependencies=[Depends(limited(SIGN_INS))])
def request_email_change(
    body: EmailChangeIn,
    account: AccountDep,
    request: Request,
    session: SessionDep,
    mailer: MailerDep,
    background: BackgroundTasks,
):
    """Send the link that moves the account to a new address, once the password confirms it.
    The mail goes out after the answer, as `forgot_password`'s does, so that an address with an
    account, which is told so instead of getting the link, answers alike."""
    user = account.user
    if account.session_id is None:
        raise HTTPException(409, LOCAL_ADDRESS)
    _confirm(user, body.password, body.current_email)
    email = body.email.strip().lower()
    if email == user.email:
        raise HTTPException(400, SAME_ADDRESS)
    # Counted for the address as well as the account, so that no address is flooded from many.
    refuse_past(session, (SIGN_INS, by_email(email)))
    holder = session.scalar(select(User).where(User.email == email))
    if holder is None:
        token = auth.issue_link_token(session, user, "change_email", email, auth.VERIFY_LINK_TTL)
        subject, text = email_change_email(user.name, _link(request, EMAIL_PAGE, token))
    else:
        subject, text = address_taken_email(holder.name, f"{_app_url(request)}{LOGIN_PAGE}")
    audit(session, account, request, "email_change_requested")
    session.commit()
    background.add_task(_send_quietly, mailer, email, subject, text)


@router.post("/email/confirm", status_code=204)
def confirm_email_change(
    body: LinkTokenIn,
    request: Request,
    session: SessionDep,
    mailer: MailerDep,
    background: BackgroundTasks,
):
    """Make the address a change-of-address link was sent to the account's, confirmed, since
    opening the link proves it is the requester's. Works without being signed in."""
    link = auth.redeem_link_token(session, body.token, "change_email")
    if link is None:
        raise HTTPException(400, LINK_INVALID)
    user = session.get(User, link.user_id)
    old = user.email
    user.email = link.email
    user.email_verified_at = auth.now()
    try:
        # An account made with the address since the link was sent is the database's to see,
        # however close the two came.
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, ADDRESS_TAKEN) from None
    # A verification or reset link sent to the old address would act for an address that is
    # no longer the account's, and a second change waiting would undo this one.
    auth.void_link_tokens(session, user.id)
    audit_sign_in(session, user, request, "email_changed")
    session.commit()
    subject, text = email_changed_email(user.name, get_settings().operator_email or NO_CONTACT)
    background.add_task(_send_quietly, mailer, old, subject, text)


# Email verification


@router.post("/verify", status_code=204)
def verify_email(body: LinkTokenIn, session: SessionDep):
    """Confirm the address a verification link was sent to. Works without being signed in."""
    link = auth.redeem_link_token(session, body.token, "verify_email")
    if link is None:
        raise HTTPException(400, LINK_INVALID)
    user = session.get(User, link.user_id)
    if user.email == link.email:
        user.email_verified_at = auth.now()
    session.commit()


@router.post("/verify/resend", status_code=202, dependencies=[Depends(limited(SIGN_INS))])
def resend_verification(
    account: AccountDep, request: Request, session: SessionDep, mailer: MailerDep
):
    if account.user.email_verified_at is None:
        _send_verification(session, account.user, request, mailer)


# Passwords


@router.post("/password/forgot", status_code=202)
def forgot_password(
    body: EmailIn,
    request: Request,
    session: SessionDep,
    mailer: MailerDep,
    background: BackgroundTasks,
):
    """Send a reset link. Always accepted, so the reply does not say whether the address exists:
    the mail goes out after the reply, so neither the relay's time nor its failure (which the
    mailer logs) tells an address with an account from one without."""
    refuse_past(session, (ADDRESSES, by_address(request)), (SIGN_INS, by_email(body.email)))
    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        return
    token = auth.issue_link_token(session, user, "reset_password", user.email, auth.RESET_LINK_TTL)
    audit_sign_in(session, user, request, "password_reset_requested")
    session.commit()
    subject, text = password_reset_email(user.name, _link(request, RESET_PAGE, token))
    background.add_task(_send_quietly, mailer, user.email, subject, text)


@router.post("/password/reset", status_code=204)
def reset_password(body: PasswordResetIn, request: Request, session: SessionDep):
    """Set a new password from a reset link and sign the account out everywhere."""
    link = auth.redeem_link_token(session, body.token, "reset_password")
    if link is None:
        raise HTTPException(400, LINK_INVALID)
    user = session.get(User, link.user_id)
    user.password_hash = auth.hash_password(body.password)
    # Opening the link proves control of the address, so it counts as verified.
    if user.email == link.email:
        user.email_verified_at = user.email_verified_at or auth.now()
    auth.revoke_sessions(session, user.id)
    audit_sign_in(session, user, request, "password_reset")
    session.commit()


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChangeIn, account: AccountDep, request: Request, session: SessionDep
):
    """Change the password and sign out every other session."""
    user = account.user
    if user.password_hash is None:
        raise HTTPException(
            400, 'Your account has no password yet. Use "Forgot password" to set one.'
        )
    if not auth.verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "The current password is not right.")
    user.password_hash = auth.hash_password(body.new_password)
    auth.revoke_sessions(session, user.id, except_id=account.session_id)
    audit(session, account, request, "password_changed")
    session.commit()


# Sessions


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(account: AccountDep, session: SessionDep):
    """The user's signed-in browsers and devices, the most recently used first."""
    rows = session.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == account.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > auth.now(),
            UserSession.needs_mfa.is_(False),
        )
        .order_by(UserSession.last_seen_at.desc(), UserSession.id.desc())
    ).all()
    return [
        SessionOut(
            id=row.id,
            user_agent=row.user_agent,
            ip=row.ip,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            current=row.id == account.session_id,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: int,
    account: AccountDep,
    request: Request,
    response: Response,
    session: SessionDep,
):
    row = session.get(UserSession, session_id)
    if row is None or row.user_id != account.user.id:
        raise HTTPException(404, "Session not found")
    row.revoked_at = auth.now()
    audit(session, account, request, "session_revoked", target=("session", row.id))
    session.commit()
    if row.id == account.session_id:
        auth.clear_session_cookie(response)


@router.delete("/sessions", status_code=204)
def revoke_other_sessions(account: AccountDep, request: Request, session: SessionDep):
    """Sign out everywhere else."""
    revoked = auth.revoke_sessions(session, account.user.id, except_id=account.session_id)
    audit(session, account, request, "other_sessions_revoked", detail={"count": revoked})
    session.commit()


# The account: deleting it, keeping it, and a copy of what is kept about its user


@router.post("/account/deletion", response_model=MeOut, dependencies=[Depends(limited(SIGN_INS))])
def delete_account(
    body: AccountDeletionIn,
    account: LiveAccountDep,
    request: Request,
    session: SessionDep,
    mailer: MailerDep,
):
    """Set the account to be deleted once the grace period is over, sign it out everywhere
    else, and tell its address. The password confirms it, or the address typed out for an
    account without one, so that a browser left signed in is not enough by itself. An account
    that has not accepted the current terms may ask too: leaving needs no new terms."""
    user = account.user
    if account.session_id is None:
        raise HTTPException(409, LOCAL_ACCOUNT)
    _confirm(user, body.password, body.email)
    if refusal := deletion_refusal(session, user):
        raise HTTPException(409, refusal)
    user.deletion_due_at = auth.now() + DELETION_GRACE
    auth.revoke_sessions(session, user.id, except_id=account.session_id)
    audit(
        session,
        account,
        request,
        "account_deletion_requested",
        detail={"due_at": user.deletion_due_at.isoformat()},
    )
    session.commit()
    subject, text = account_deletion_email(
        user.name,
        day(user.deletion_due_at, zone_of(user.time_zone)),
        f"{_app_url(request)}{LOGIN_PAGE}",
    )
    # The deletion is set either way, and the next sign-in shows it; a relay that is down
    # is logged by the mailer.
    with suppress(MailError):
        mailer.send(user.email, subject, text)
    return _me(account)


@router.delete("/account/deletion", response_model=MeOut)
def keep_account(account: AnyAccountDep, request: Request, session: SessionDep):
    """Keep an account that was set to be deleted: nothing of it is removed before the day."""
    if account.user.deletion_due_at is not None:
        audit(session, account, request, "account_deletion_cancelled")
    account.user.deletion_due_at = None
    session.commit()
    return _me(account)


@router.get("/account/export", response_model=AccountExport)
def export_account(
    account: AnyAccountDep, request: Request, session: SessionDep, response: Response
):
    """Everything kept about the user as a person, as a file to save (app/export.py)."""
    export = account_export(session, account.user)
    audit(session, account, request, "account_exported")
    session.commit()
    filename = f"emudoc-account-{export.exported_at:%Y-%m-%d}.json"
    response.headers["Content-Disposition"] = content_disposition(filename)
    response.headers["Cache-Control"] = "no-store"
    return export


# Two-factor sign-in


@router.post("/mfa/setup", response_model=MfaSetupOut)
def setup_mfa(account: AccountDep, session: SessionDep):
    """Start adding an authenticator: a new secret, which a correct code then turns on."""
    user = account.user
    if user.mfa_enabled:
        raise HTTPException(
            409, "Two-factor sign-in is already on. Turn it off before setting up a new app."
        )
    secret = user.totp_secret = auth.new_totp_secret()
    user.totp_last_step = None
    session.commit()
    return MfaSetupOut(secret=secret, otpauth_url=auth.totp_uri(secret, user.email))


@router.post("/mfa/enable", response_model=RecoveryCodesOut)
def enable_mfa(body: CodeIn, account: AccountDep, request: Request, session: SessionDep):
    """Turn two-factor sign-in on once the app shows the right code; returns the recovery codes."""
    user = account.user
    if user.mfa_enabled:
        raise HTTPException(409, "Two-factor sign-in is already on.")
    if user.totp_secret is None:
        raise HTTPException(400, "Start the setup first to get a secret for your app.")
    if not auth.check_totp(user, body.code):
        raise HTTPException(400, "That code is not right. Enter the current code from your app.")
    codes = auth.new_recovery_codes()
    user.recovery_code_hashes = [auth.token_hash(code) for code in codes]
    user.totp_enabled_at = auth.now()
    # Other sessions were opened without the second factor; this one just proved it.
    auth.revoke_sessions(session, user.id, except_id=account.session_id)
    audit(session, account, request, "mfa_enabled")
    session.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/mfa/disable", status_code=204)
def disable_mfa(body: MfaDisableIn, account: AccountDep, request: Request, session: SessionDep):
    """Turn two-factor sign-in off, confirmed by the password or by a code."""
    user = account.user
    if not user.mfa_enabled:
        raise HTTPException(409, "Two-factor sign-in is not on.")
    confirmed = (
        body.password is not None and auth.verify_password(body.password, user.password_hash)
    ) or (
        body.code is not None
        and (auth.check_totp(user, body.code) or auth.use_recovery_code(user, body.code))
    )
    if not confirmed:
        raise HTTPException(
            403, "Confirm with your password or with a code from your authenticator app."
        )
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.recovery_code_hashes = []
    audit(session, account, request, "mfa_disabled")
    session.commit()


# Sign-in through Google and Microsoft


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, request: Request, next: str = "/", time_zone: str = ""):
    """Send the browser to the provider, remembering the handshake in a short-lived cookie,
    with the browser's time zone for an account the sign-in creates."""
    found = _provider(provider)
    url, handshake = begin(found, _redirect_uri(request, found), _safe_path(next), time_zone[:64])
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OAUTH_COOKIE,
        handshake.encode(),
        max_age=OAUTH_WINDOW_SECONDS,
        httponly=True,
        samesite="lax",
        secure=auth.cookie_secure(request),
        path="/api/auth/oauth",
    )
    return response


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: str,
    request: Request,
    session: SessionDep,
    http: HttpDep,
    mailer: MailerDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Back from the provider: exchange the code, find or create the account, sign it in."""
    found = _provider(provider)
    handshake = Handshake.decode(request.cookies.get(OAUTH_COOKIE))
    if error:
        return _back_to_login(request, error=f"The sign-in with {found.label} was cancelled.")
    if handshake is None or handshake.provider != provider or not code or state != handshake.state:
        return _back_to_login(
            request, error="The sign-in took too long or was started elsewhere. Try again."
        )
    try:
        claims = finish(found, code, _redirect_uri(request, found), handshake, http)
        user = _user_for_claims(session, found, claims, request, mailer, handshake.time_zone)
    except (OidcError, SignInRefused) as refusal:
        session.rollback()
        return _back_to_login(request, error=str(refusal))
    _, token = auth.start_session(session, user, request, needs_mfa=user.mfa_enabled)
    if not user.mfa_enabled:
        audit_sign_in(session, user, request, "sign_in", {"method": found.name})
    session.commit()
    if user.mfa_enabled:
        response = _back_to_login(request, mfa="1", next=handshake.next)
    else:
        response = RedirectResponse(handshake.next, status_code=302)
        response.delete_cookie(OAUTH_COOKIE, path="/api/auth/oauth")
    auth.set_session_cookie(response, request, token, needs_mfa=user.mfa_enabled)
    return response


def _user_for_claims(
    session: Session,
    provider: Provider,
    claims: Claims,
    request: Request,
    mailer: Mailer,
    time_zone: str = "",
) -> User:
    """The user behind the provider's subject, linking or creating an account the first time.

    An existing account is linked only when both sides vouch for the address: the provider
    says it is verified, and the account verified it too. Otherwise someone could sign up with
    another person's address and wait for them to sign in through a provider.
    """
    identity = session.scalar(
        select(Identity).where(
            Identity.provider == provider.name, Identity.subject == claims.subject
        )
    )
    if identity is not None:
        identity.email = claims.email
        return identity.user
    if not claims.email:
        raise SignInRefused(
            f"{provider.label} did not share an email address for this account, which Emudoc "
            "needs. Sign in another way."
        )
    user = session.scalar(select(User).where(User.email == claims.email))
    if user is None:
        account = create_account(
            session,
            claims.email,
            claims.name,
            email_verified=claims.email_verified,
            time_zone=time_zone,
        )
        audit(session, account, request, "sign_up", detail={"method": provider.name})
        user = account.user
        if not claims.email_verified:
            _send_verification(session, user, request, mailer)
    elif not (claims.email_verified and user.email_verified_at is not None):
        raise SignInRefused(
            "An account with this email already exists. Sign in the way you did before; "
            "if you have forgotten your password, reset it."
        )
    session.add(
        Identity(
            user_id=user.id, provider=provider.name, subject=claims.subject, email=claims.email
        )
    )
    session.flush()
    return user


def _provider(name: str) -> Provider:
    found = providers(get_settings()).get(name)
    if found is None:
        raise HTTPException(404, f"Sign-in with {name} is not set up.")
    return found


def _redirect_uri(request: Request, provider: Provider) -> str:
    return f"{_app_url(request)}/api/auth/oauth/{provider.name}/callback"


def _back_to_login(request: Request, **params: str) -> RedirectResponse:
    response = RedirectResponse(f"{LOGIN_PAGE}?{urlencode(params)}", status_code=302)
    response.delete_cookie(OAUTH_COOKIE, path="/api/auth/oauth")
    return response


def _safe_path(path: str) -> str:
    """A path on this site to return to; anything else goes to the front page."""
    return path if path.startswith("/") and not path.startswith("//") else "/"


# Shared


def _me(account: Account) -> MeOut:
    user = account.user
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        time_zone=user.time_zone,
        email_verified=user.email_verified_at is not None,
        mfa_enabled=user.mfa_enabled,
        identities=sorted(identity.provider for identity in user.identities),
        organization=OrganizationOut.model_validate(account.organization).model_copy(
            update={"chat_retention_days": entitlements_of(account.organization).retention_days}
        ),
        role=account.role,
        has_password=user.password_hash is not None,
        deletion_due_at=user.deletion_due_at,
        terms_version=user.terms_version,
        terms_accepted_at=user.terms_accepted_at,
        terms_due=terms_due(account),
    )


def _confirm(user: User, password: str | None, email: str | None) -> None:
    """Refuse a change to the account that its password does not confirm, or for an account
    without one, its address typed out, so that a browser left signed in is not enough by
    itself."""
    if user.password_hash is not None:
        if not auth.verify_password(password or "", user.password_hash):
            raise HTTPException(403, WRONG_PASSWORD)
    elif (email or "").strip().lower() != user.email:
        raise HTTPException(403, TYPE_EMAIL)


def _accept_terms(session: Session, account: Account, request: Request) -> None:
    """Record that the user accepted the current edition, now, in the audit log as well; the
    caller commits."""
    account.user.terms_version = TERMS_VERSION
    account.user.terms_accepted_at = auth.now()
    audit(session, account, request, "terms_accepted", detail={"version": TERMS_VERSION})


def _app_url(request: Request) -> str:
    return get_settings().app_url.rstrip("/") or str(request.base_url).rstrip("/")


def _link(request: Request, page: str, token: str) -> str:
    """A one-time link to a page of the frontend, with the token in the fragment: the browser
    never sends a fragment, so the token reaches neither the access log nor a proxy's, which a
    query would, and a reset token there signs in within its hour (`useLinkToken` in
    frontend/src/account/linkToken.ts)."""
    return f"{_app_url(request)}{page}#token={quote(token)}"


def _send_verification(session: Session, user: User, request: Request, mailer: Mailer) -> None:
    token = auth.issue_link_token(session, user, "verify_email", user.email, auth.VERIFY_LINK_TTL)
    session.commit()
    subject, text = verification_email(user.name, _link(request, VERIFY_PAGE, token))
    _send(mailer, user.email, subject, text)


def _send_quietly(mailer: Mailer, to: str, subject: str, text: str) -> None:
    with suppress(MailError):
        mailer.send(to, subject, text)


def _send(mailer: Mailer, to: str, subject: str, text: str) -> None:
    try:
        mailer.send(to, subject, text)
    except MailError as error:
        raise HTTPException(503, f"{error} Try again in a few minutes.") from error
