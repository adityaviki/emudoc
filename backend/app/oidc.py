"""Sign-in with Google and Microsoft through OpenID Connect.

Both are OpenID providers, so one authorization-code flow with PKCE serves both. The browser is
sent to the provider with a random state, a nonce and a code challenge, which the app keeps in a
short-lived cookie; the provider sends the browser back with a code, which the app exchanges for
an ID token directly at the provider's token endpoint. Because the token arrives over that direct
TLS connection, its claims can be trusted once the issuer, audience, expiry and nonce check out,
as OpenID Connect Core 3.1.3.7 allows; no signature check is needed.
"""

import base64
import datetime as dt
import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import httpx

from app.config import Settings

SCOPES = "openid email profile"
# Microsoft's "common" endpoint signs in users of any directory, and the issuer then names the
# tenant the user came from; a tenant id pins it instead.
_MICROSOFT_ANY_TENANT = ("common", "organizations", "consumers")


class OidcError(Exception):
    """The provider's answer could not be used; the message is fit to show the user."""


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: str
    # What the ID token's `iss` must match.
    issuer: re.Pattern[str]


@dataclass(frozen=True)
class Handshake:
    """What the app remembers between sending the browser away and its return."""

    provider: str
    state: str
    nonce: str
    verifier: str
    # Where to send the browser once signed in.
    next: str

    def encode(self) -> str:
        return base64.urlsafe_b64encode(json.dumps(asdict(self)).encode()).decode()

    @classmethod
    def decode(cls, value: str | None) -> "Handshake | None":
        if not value:
            return None
        try:
            return cls(**json.loads(base64.urlsafe_b64decode(value)))
        except (ValueError, TypeError):
            return None


@dataclass(frozen=True)
class Claims:
    """Who the provider says signed in."""

    subject: str
    email: str
    # Whether the provider vouches for the address; an unverified one may belong to anyone.
    email_verified: bool
    name: str


def providers(settings: Settings) -> dict[str, Provider]:
    """The providers an OAuth client is configured for, by name."""
    found: dict[str, Provider] = {}
    if settings.google_oauth_client_id:
        found["google"] = Provider(
            name="google",
            label="Google",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            issuer=re.compile(r"^(https://)?accounts\.google\.com$"),
        )
    if settings.microsoft_oauth_client_id:
        tenant = settings.microsoft_oauth_tenant
        tenant_pattern = r"[0-9a-f-]{36}" if tenant in _MICROSOFT_ANY_TENANT else re.escape(tenant)
        found["microsoft"] = Provider(
            name="microsoft",
            label="Microsoft",
            authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            client_id=settings.microsoft_oauth_client_id,
            client_secret=settings.microsoft_oauth_client_secret,
            issuer=re.compile(rf"^https://login\.microsoftonline\.com/{tenant_pattern}/v2\.0$"),
        )
    return found


def begin(provider: Provider, redirect_uri: str, next_path: str) -> tuple[str, Handshake]:
    """The URL to send the browser to, and what to remember until it comes back."""
    handshake = Handshake(
        provider=provider.name,
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(64),
        next=next_path,
    )
    challenge = _base64url(hashlib.sha256(handshake.verifier.encode()).digest())
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": handshake.state,
        "nonce": handshake.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{provider.authorize_url}?{urlencode(params)}", handshake


def finish(
    provider: Provider, code: str, redirect_uri: str, handshake: Handshake, http: httpx.Client
) -> Claims:
    """Exchange the code the provider sent back for the claims about who signed in."""
    try:
        response = http.post(
            provider.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code_verifier": handshake.verifier,
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as error:
        raise OidcError(f"{provider.label} could not be reached. Try again.") from error
    if response.status_code != 200:
        raise OidcError(f"{provider.label} did not accept the sign-in. Try again.")
    try:
        claims = _payload(response.json()["id_token"])
    except (ValueError, KeyError, TypeError) as error:
        raise OidcError(f"{provider.label} sent an answer the app could not read.") from error
    _validate(provider, claims, handshake)
    return _claims(provider, claims)


def _validate(provider: Provider, claims: dict, handshake: Handshake) -> None:
    audience = claims.get("aud")
    if isinstance(audience, list):
        audience = audience[0] if len(audience) == 1 else None
    expiry = claims.get("exp")
    if (
        not provider.issuer.match(str(claims.get("iss", "")))
        or audience != provider.client_id
        or not isinstance(expiry, int | float)
        or expiry <= dt.datetime.now(dt.UTC).timestamp()
        or claims.get("nonce") != handshake.nonce
        or not claims.get("sub")
    ):
        raise OidcError(f"The sign-in with {provider.label} could not be verified. Try again.")


def _claims(provider: Provider, claims: dict) -> Claims:
    email = str(claims.get("email") or "")
    verified = claims.get("email_verified") is True
    if provider.name == "microsoft":
        # Microsoft only vouches for the address when the app registration asks for the
        # xms_edov claim; otherwise `email` is whatever the directory holds. The user principal
        # name is an address too, but never a verified one.
        verified = claims.get("xms_edov") in (True, "true", "1", 1)
        preferred = str(claims.get("preferred_username") or "")
        if not email and "@" in preferred:
            email = preferred
    return Claims(
        subject=str(claims["sub"]),
        email=email.strip().lower(),
        email_verified=verified and bool(email),
        name=str(claims.get("name") or "").strip(),
    )


def _payload(id_token: str) -> dict:
    """The claims of a JWT, without checking its signature (see the module docstring)."""
    segments = id_token.split(".")
    if len(segments) != 3:
        raise ValueError("not a JWT")
    padded = segments[1] + "=" * (-len(segments[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    if not isinstance(claims, dict):
        raise ValueError("claims are not an object")
    return claims


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
