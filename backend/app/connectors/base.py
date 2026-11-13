"""What every service a project's folder is connected from does the same way (app/connectors/):
the member's consent, through OAuth 2's authorization code flow with PKCE, which gives a refresh
token that lasts; the access token it buys for each sync; the services' answers sorted into the
failures that pass by themselves and those that do not; and a file read as it downloads.

A service (`Provider`) adds what is its own: its addresses, the scope it asks for, the account
the consent was given from, where browsing its folders starts and what a folder holds, whether
anything changed since a mark it gave, every file below a folder with a version that changes
with its bytes, and how a file's bytes are read.
"""

import base64
import hashlib
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.errors import AppError

# The cookie that holds the handshake while the browser is at the service for the member's
# consent, and how long it may take there.
CONNECT_COOKIE = "emudoc_connect"
CONNECT_WINDOW_SECONDS = 600
# How long a request to a service may take; a download gets longer, since a file may be the
# largest an upload may be.
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
CHUNK = 1024 * 1024
# What the services answer a request that should be asked again later with.
LATER = (429, 500, 502, 503, 504)
# Why a consent did not connect anything, as the page the browser is sent back to names it
# (`connectError` in frontend/src/connectors.ts): the member cancelled it at the service, it took
# too long or was started elsewhere, the service refused the code, the member did not grant
# leave to read the files or gave no lasting access, the deployment's client is not right, the
# service did not answer, or the member no longer may change the project.
CONSENT_FAILURES = (
    "cancelled",
    "expired",
    "refused",
    "scope",
    "lasting",
    "setup",
    "unreachable",
    "not_allowed",
)
# For the sentences a deployment's members read in place of one that names the deployment's
# own client or its settings.
ASK_THE_OPERATOR = "Ask whoever runs Emudoc to look at its connection with {label}."


class ConnectorError(AppError):
    """A service did not do what was asked. `transient` when asking again later may; `revoked`
    when it no longer accepts the member's grant, which only connecting again mends; `gone`
    when what was asked about is no longer there. `code` names a consent that failed, for the
    page the browser is sent back to, which words it (CONSENT_FAILURES)."""

    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        revoked: bool = False,
        gone: bool = False,
        members: str | None = None,
        code: str = "",
    ):
        super().__init__(message, transient=transient, members=members)
        self.revoked = revoked
        self.gone = gone
        self.code = code or ("unreachable" if transient else "setup" if members else "refused")


@dataclass(frozen=True)
class RemoteFolder:
    """A folder at the service, by its id there. `drive` is the shared drive it is in, where
    the service keeps a change log per drive; `choosable` is False for a place browsing passes
    through that is no folder of its own ("Shared with me")."""

    ref: str
    name: str
    drive: str = ""
    choosable: bool = True


@dataclass(frozen=True)
class RemoteFile:
    """A file below a connected folder: its id at the service; its version, which changes when
    its bytes do; its name and the folders between it and the connected one; its size where the
    service says; and where its bytes are read from, or why they cannot be."""

    ref: str
    version: str
    name: str
    folders: tuple[str, ...]
    size: int | None
    source: str
    unreadable: str = ""

    @property
    def path(self) -> str:
        return "/".join((*self.folders, self.name))


@dataclass(frozen=True)
class Listing:
    """Every file below a folder, and the service's mark of the moment it was read, from which
    `changed` asks; None where the service keeps none."""

    files: list[RemoteFile]
    cursor: str | None


@dataclass(frozen=True)
class Grant:
    """What the service gave for the member's consent: the refresh token that lasts, an access
    token for now, and the account the consent was given from."""

    refresh_token: str
    access_token: str
    account: str


@dataclass(frozen=True)
class Access:
    """An access token for now, and the refresh token the service gave in place of the one it
    was bought with, where it gives a new one each time."""

    token: str
    refresh_token: str | None = None


@dataclass(frozen=True)
class Consent:
    """Where to send the browser for the member's consent, and what to keep until it is back:
    the state that ties the return to this start, and the verifier its code is redeemed with."""

    url: str
    state: str
    verifier: str


def clean(name: str) -> str:
    """A name as the project keeps it: a slash, which a service may allow in a name, would
    otherwise read as a folder."""
    return name.replace("/", "-").replace("\\", "-").strip() or "unnamed"


class Download:
    """A file's bytes as the service sends them, asked for at the first read, so that a file
    an upload rejects by its name is never downloaded, and hashed as they are read, so that
    bytes the project holds already as the file's last version are told apart from new ones."""

    def __init__(self, opener: Callable[[], AbstractContextManager[Iterator[bytes]]]):
        self._opener = opener
        self._stack = ExitStack()
        self._chunks: Iterator[bytes] | None = None
        self._buffer = b""
        self._digest = hashlib.sha256()
        self.ended = False

    def read(self, size: int = -1) -> bytes:
        if self._chunks is None:
            self._chunks = self._stack.enter_context(self._opener())
        while not self.ended and (size < 0 or len(self._buffer) < size):
            chunk = next(self._chunks, None)
            if chunk is None:
                self.ended = True
            else:
                self._buffer += chunk
        taken = self._buffer if size < 0 else self._buffer[:size]
        self._buffer = self._buffer[len(taken) :]
        self._digest.update(taken)
        return taken

    @property
    def sha256(self) -> str | None:
        """The hash of the whole file, once it was read to its end."""
        return self._digest.hexdigest() if self.ended and not self._buffer else None

    def close(self) -> None:
        self._stack.close()


class Provider:
    """A service folders are connected from. The OAuth flow is the same for all three; the rest
    each service writes."""

    # Its name in the database and the paths, and in the members' words: "Google Drive".
    name = ""
    label = ""
    authorize_url = ""
    token_url = ""
    scope = ""

    def __init__(self, client_id: str, client_secret: str, base: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        # The stand-in's address in place of the service's own (CONNECTOR_BASE_URL).
        self.base = base.rstrip("/")

    # The consent

    def begin(self, redirect_uri: str) -> Consent:
        """Where to send the browser for the member's consent."""
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            # A service whose app names its scopes itself is asked for none.
            **({"scope": self.scope} if self.scope else {}),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **self.consent_params(),
        }
        return Consent(f"{self.authorize_url}?{urlencode(params)}", state, verifier)

    def consent_params(self) -> dict[str, str]:
        """What the service needs besides the flow's own to give a refresh token."""
        return {}

    def finish(self, http: httpx.Client, code: str, redirect_uri: str, verifier: str) -> Grant:
        """Redeem the code the service sent the browser back with for the member's grant."""
        answer = self._token(
            http,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            refusal=f"{self.label} did not accept the connection. Try again.",
        )
        refresh_token = answer.get("refresh_token")
        access_token = answer.get("access_token")
        if not isinstance(refresh_token, str) or not isinstance(access_token, str):
            raise ConnectorError(
                f"{self.label} gave no lasting access to the files. Connect again, and allow "
                "Emudoc to keep reading them.",
                code="lasting",
            )
        if problem := self.not_granted(answer):
            raise ConnectorError(problem, code="scope")
        return Grant(refresh_token, access_token, self.account(http, access_token))

    def not_granted(self, answer: dict[str, Any]) -> str | None:
        """Why the consent given is short of what syncing needs, where the member may grant
        part of what was asked; None when it is enough."""
        return None

    def access(self, http: httpx.Client, refresh_token: str) -> Access:
        """An access token for now, bought with the member's grant; `revoked` when the service
        no longer accepts it."""
        answer = self._token(
            http,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            refusal=(
                f"{self.label} no longer lets Emudoc read this folder: its access was taken "
                "back, or it lapsed. Connect the folder again."
            ),
            revoked=True,
        )
        token = answer.get("access_token")
        if not isinstance(token, str):
            raise ConnectorError(
                f"{self.label} answered without an access token. The next sync tries again.",
                transient=True,
            )
        renewed = answer.get("refresh_token")
        return Access(token, renewed if isinstance(renewed, str) and renewed else None)

    def revoke(self, http: httpx.Client, refresh_token: str) -> None:
        """Give the member's grant back, where the service takes it back; a failure is the
        caller's to log, since the connector goes either way."""

    def _token(
        self, http: httpx.Client, data: dict[str, str], *, refusal: str, revoked: bool = False
    ) -> dict[str, Any]:
        try:
            answer = http.post(
                self.token_url,
                data={**data, "client_id": self.client_id, "client_secret": self.client_secret},
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise self.unreachable() from error
        if answer.status_code in LATER:
            raise self.unreachable()
        body = _json(answer)
        if answer.status_code == 200 and isinstance(body, dict):
            return body
        error = body.get("error") if isinstance(body, dict) else None
        if error in ("invalid_client", "unauthorized_client"):
            # The deployment's client, not the member's grant: its id, its secret, or the
            # redirect URI registered for it.
            raise ConnectorError(
                f"{self.label} refused the deployment's OAuth client ({error}): check its id, "
                "its secret and the redirect URI registered for it.",
                members=ASK_THE_OPERATOR.format(label=self.label),
            )
        raise ConnectorError(refusal, revoked=revoked)

    # Requests to the service's API

    def unreachable(self) -> ConnectorError:
        return ConnectorError(
            f"{self.label} did not answer. The next sync tries again.", transient=True
        )

    def call(
        self,
        http: httpx.Client,
        method: str,
        url: str,
        token: str,
        **options: Any,
    ) -> Any:
        """The service's answer to a request of its API, as JSON; a failure as a
        ConnectorError, sorted by what the service answered."""
        try:
            answer = http.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", **options.pop("headers", {})},
                timeout=TIMEOUT,
                **options,
            )
        except httpx.HTTPError as error:
            raise self.unreachable() from error
        self.check(answer)
        return _json(answer)

    def check(self, answer: httpx.Response) -> None:
        """Raise for an answer that is not a success, sorted: later, gone, or refused."""
        if answer.is_success:
            return
        if answer.status_code in LATER or self.later(answer):
            raise self.unreachable()
        if answer.status_code == 404 or self.missing(answer):
            raise ConnectorError(f"{self.label} no longer holds what was asked for.", gone=True)
        if answer.status_code == 401:
            raise ConnectorError(
                f"{self.label} did not take the access it gave a moment ago. The next sync "
                "tries again.",
                transient=True,
            )
        raise ConnectorError(
            self.refused(answer) or f"{self.label} refused to hand it over ({answer.status_code})."
        )

    def later(self, answer: httpx.Response) -> bool:
        """Whether a refusal is one of the service's ways of saying "later"."""
        return False

    def refused(self, answer: httpx.Response) -> str | None:
        """The service's refusal in a sentence of its own, where it says why."""
        return None

    def missing(self, answer: httpx.Response) -> bool:
        """Whether a refusal is one of the service's ways of saying "not there"."""
        return False

    @contextmanager
    def streamed(
        self, http: httpx.Client, url: str, token: str, method: str = "GET", **options: Any
    ) -> Iterator[Iterator[bytes]]:
        """A download's chunks, the request sent when it is entered."""
        headers = {"Authorization": f"Bearer {token}", **options.pop("headers", {})}
        try:
            with http.stream(
                method, url, headers=headers, timeout=DOWNLOAD_TIMEOUT, **options
            ) as answer:
                if not answer.is_success:
                    answer.read()
                    self.check(answer)
                yield _chunks(self, answer)
        except httpx.HTTPError as error:
            raise self.unreachable() from error

    # What each service writes

    def account(self, http: httpx.Client, token: str) -> str:
        """The account the consent was given from, as the service names it."""
        raise NotImplementedError

    def tops(self, http: httpx.Client, token: str) -> list[RemoteFolder]:
        """Where browsing the account's folders starts."""
        raise NotImplementedError

    def folders(self, http: httpx.Client, token: str, parent: str) -> list[RemoteFolder]:
        """The folders in a folder, by name."""
        raise NotImplementedError

    def folder(self, http: httpx.Client, token: str, ref: str) -> RemoteFolder | None:
        """The folder as it is now, or None once it is gone or in the service's trash."""
        raise NotImplementedError

    def changed(self, http: httpx.Client, token: str, folder: RemoteFolder, cursor: str) -> bool:
        """Whether anything may have changed below the folder since the mark; True where the
        service cannot say."""
        raise NotImplementedError

    def listing(self, http: httpx.Client, token: str, folder: RemoteFolder, limit: int) -> Listing:
        """Every file below the folder, its subfolders' too, up to one past `limit`."""
        raise NotImplementedError

    def download(self, http: httpx.Client, token: str, file: RemoteFile) -> Download:
        """The file's bytes, asked for at their first read."""
        return Download(lambda: self.streamed(http, file.source, token))


def _chunks(provider: Provider, answer: httpx.Response) -> Iterator[bytes]:
    try:
        yield from answer.iter_bytes(CHUNK)
    except httpx.HTTPError as error:
        raise provider.unreachable() from error


def _json(answer: httpx.Response) -> Any:
    try:
        return answer.json()
    except ValueError:
        return None
