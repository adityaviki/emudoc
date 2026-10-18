"""The headers that tell the browser what a page of the app may do, sent on every response.

The app sends them itself rather than leaving them to the reverse proxy, so that every
deployment has them and the tests see them. The policy is strict because the page needs little:
every script and stylesheet is a file the app serves, nothing comes from another origin, and
the one thing that may is an image, when the storage is a bucket and a page image or thumbnail
is a redirect to a URL signed for it. Model output renders through Markdown, so the policy is
also what keeps an answer from loading an image from an address a document asked it to.

Nothing the API answers is kept by the browser (`Cache-Control: no-store`, the files and page
images included, app/storage.py), and signing out has it drop what it holds of the app's
origin (`Clear-Site-Data`, app/api/auth.py), since a computer may be shared.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.storage import Storage

# A year, the least the preload lists accept; neither `includeSubDomains` nor `preload`, which
# bind every subdomain of the deployment's name and are the operator's to add at the proxy.
HSTS = "max-age=31536000"

# What a browser may keep of an answer of the API: nothing. Every one is a member's: their
# chats, a document's text and pages, the organisation's audit log; a browser that kept one
# would show it to the next person at that computer without asking the app, signed in or not.
# A route that may be kept says so itself (`setdefault` leaves its header alone).
API_NOT_KEPT = "no-store"

ALWAYS = {
    # A response is what its Content-Type says, never what its bytes look like.
    "X-Content-Type-Options": "nosniff",
    # `frame-ancestors` below, for the browsers that predate it.
    "X-Frame-Options": "DENY",
    # No request says which page it came from: the library's address holds its search words
    # (`?q=`), which a Referer would carry into the proxy's log with every call to the API,
    # and the signed bucket URLs are another origin. Nothing in the app reads a Referer.
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def content_security_policy(storage: Storage) -> str:
    """What the page may load: its own origin, and images from the bucket when there is one.

    An original downloads, or opens in a new tab when it is an image, and neither is the page's
    to allow; the files the app sends carry a policy of their own (`SANDBOX` in app/storage.py)
    in place of this one. The requests to `/api` and the event stream are `connect-src`, covered
    by `default-src`. React sets its few `style` props through the CSSOM, which `'self'` does
    not block, so no `'unsafe-inline'` is needed for styles either.
    """
    images = ["'self'"]
    if storage.origin:
        images.append(storage.origin)
    return "; ".join(
        (
            "default-src 'self'",
            f"img-src {' '.join(images)}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


class SecurityHeaders:
    """Adds the headers to every HTTP response, after whatever the route set itself."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                secure(MutableHeaders(scope=message), scope)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def secure(headers: MutableHeaders, scope: Scope) -> None:
    """Put the headers on the response to the request `scope`, leaving those it set itself.

    `Strict-Transport-Security` goes out when the request came over HTTPS, as uvicorn learns
    from the proxy, and always in production, where a plain-HTTP request is a misconfiguration
    the cookies already log; over http://localhost a browser would ignore it anyway. The
    policy is built once at start (`app.state.policy`), since the storage does not change.

    `SecurityHeaders` calls it for every response but one: the 500 of an error nobody planned
    for is sent from outside every middleware the app adds (`unexpected_error` in app/main.py),
    which calls it itself.
    """
    for name, value in ALWAYS.items():
        headers.setdefault(name, value)
    headers.setdefault("Content-Security-Policy", scope["app"].state.policy)
    if scope["path"].startswith("/api/"):
        headers.setdefault("Cache-Control", API_NOT_KEPT)
    if scope["scheme"] == "https" or get_settings().environment == "production":
        headers.setdefault("Strict-Transport-Security", HSTS)
