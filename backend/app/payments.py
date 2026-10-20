"""Stripe, the payment provider, reached over its HTTP API with the app's own client: the few
calls billing makes (app/billing.py), and the check of the signature on the events Stripe sends.

There is no SDK on purpose. Billing needs a customer, a Checkout session, a portal session
(opened on its home page, or on one step: cancelling, or confirming a new plan) and a
subscription read back, and an event's signature checked; the library would bring a second
HTTP stack with retries and telemetry of its own, which the app would have to hold to what it
logs and sends as it holds httpx. Every request names API_VERSION, so the answers keep the
shape this module reads whatever the Stripe account's default is, and the webhook endpoint is
made with the same version (deploying.md, "Billing"); the events are read for little but ids,
since the app asks Stripe for the subscription as it stands rather than trust an event's copy,
which may come late or out of order.

A failure is a `PaymentsError` worded from Stripe's status and error codes, never its message,
which can repeat what was sent; transient for a transport failure, a 429 or a 5xx. Its message
names the call and the codes, for the operator's log; members are told that billing cannot be
reached just now. A request that creates something carries an idempotency key, so that one
sent again after a lost answer does not create it twice.
"""

import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

from app.errors import AppError, provider_codes

log = logging.getLogger(__name__)

API = "https://api.stripe.com"
# The version this module was written against: the latest on 2026-10-20.
API_VERSION = "2026-09-30.endive"
TIMEOUT = 20.0
# The header Stripe signs each event in, and how old a signature may be, in seconds: Stripe's
# own libraries allow five minutes, which a replayed event is refused past.
SIGNATURE_HEADER = "Stripe-Signature"
TOLERANCE = 300
UNAVAILABLE = "Billing cannot be reached just now. Try again in a few minutes."
# What the parameter an error names looks like, `line_items[0][price]`: never a value.
_PARAM = re.compile(r"[a-z0-9_\[\]]{1,100}")


class PaymentsError(AppError):
    """A request to Stripe that failed. The message is the operator's: it names the call and
    Stripe's codes."""

    members = UNAVAILABLE


class BadSignature(Exception):
    """An event whose signature is missing, malformed, too old or not Stripe's."""


class Stripe:
    """Stripe's API with the account's secret key, through `http`."""

    def __init__(self, http: httpx.Client, key: str):
        self.http = http
        self.key = key

    def create_customer(self, org_id: int, name: str, email: str) -> str:
        """A new customer for the organisation, named and addressed as its owner gave them;
        the organisation's id in its metadata, for the operator to find it by."""
        # Keyed by the address too, since an owner's second try with another address is another
        # request, which Stripe would refuse under the first one's key.
        asked = hashlib.sha256(email.encode()).hexdigest()[:16]
        customer = self._request(
            "POST",
            "/v1/customers",
            {"name": name, "email": email, "metadata": {"org_id": org_id}},
            call="create a customer",
            idempotency=f"customer-{org_id}-{asked}",
        )
        return customer["id"]

    def create_checkout(self, params: Mapping[str, Any], idempotency: str) -> dict:
        """A Checkout session, whose `url` the buyer is sent to."""
        return self._request(
            "POST",
            "/v1/checkout/sessions",
            params,
            call="create a Checkout session",
            idempotency=idempotency,
        )

    def checkout(self, session_id: str) -> dict:
        return self._request(
            "GET", f"/v1/checkout/sessions/{_id(session_id)}", call="read a Checkout session"
        )

    def create_portal(
        self, customer: str, return_url: str, flow: Mapping[str, Any] | None = None
    ) -> str:
        """The address of a customer portal session, where an owner changes or cancels the
        subscription, the payment method and the billing details, and finds the invoices; with
        `flow` (its `flow_data`), one that opens on that one step, such as cancelling."""
        params: dict[str, Any] = {"customer": _id(customer), "return_url": return_url}
        if flow is not None:
            params["flow_data"] = flow
        portal = self._request(
            "POST", "/v1/billing_portal/sessions", params, call="create a portal session"
        )
        return portal["url"]

    def subscription(self, subscription_id: str) -> dict:
        return self._request(
            "GET", f"/v1/subscriptions/{_id(subscription_id)}", call="read a subscription"
        )

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        call: str,
        idempotency: str | None = None,
    ) -> dict:
        """Stripe's answer to the request, as JSON; `call` says what it was for in an error."""
        headers = {"Authorization": f"Bearer {self.key}", "Stripe-Version": API_VERSION}
        body = None
        if method == "POST":
            body = urlencode(form(params or {}))
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        try:
            response = self.http.request(
                method,
                f"{API}{path}",
                content=body,
                headers=headers,
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as error:
            # The message would name the address, and with it an id.
            raise PaymentsError(
                f"Stripe could not be reached to {call} ({type(error).__name__}).", transient=True
            ) from error
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.is_success and isinstance(body, dict):
            return body
        status = response.status_code
        codes = provider_codes(body)
        error = body.get("error") if isinstance(body, dict) else None
        param = error.get("param") if isinstance(error, dict) else None
        if isinstance(param, str) and _PARAM.fullmatch(param):
            codes.append(f"param {param}")
        said = f" ({', '.join(codes)})" if codes else ""
        # A 409 is a request that met another with its key, or a lock Stripe held: both pass.
        raise PaymentsError(
            f"Stripe refused to {call}: {status}{said}.",
            transient=status in (409, 429) or status >= 500,
        )


def form(params: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Parameters in Stripe's form encoding: a nested key in brackets, `a[b][c]`, a list's items
    by index, `items[0][price]`, and a boolean as true or false."""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, Mapping):
            pairs += form(value, name)
        elif isinstance(value, list | tuple):
            pairs += form({str(index): item for index, item in enumerate(value)}, name)
        elif isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        elif value is not None:
            pairs.append((name, str(value)))
    return pairs


def verified_event(
    payload: bytes, header: str | None, secret: str, now: float | None = None
) -> dict:
    """The event `payload` holds, once `header` shows Stripe signed it with the endpoint's
    `secret` within TOLERANCE seconds of `now`: an HMAC-SHA256 of the timestamp and the body as
    they came, which is why the body is read raw. Raises BadSignature otherwise, and for a body
    that is not an event."""
    if not header or not secret:
        raise BadSignature("no signature")
    timestamp, signatures = None, []
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            timestamp = value
        elif name == "v1":
            signatures.append(value)
    if not timestamp or not timestamp.isdecimal() or not signatures:
        raise BadSignature("a malformed signature")
    if abs((now if now is not None else time.time()) - int(timestamp)) > TOLERANCE:
        raise BadSignature("a signature too old")
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise BadSignature("a signature that is not Stripe's")
    try:
        event = json.loads(payload)
    except ValueError:
        raise BadSignature("a body that is not JSON") from None
    if not isinstance(event, dict) or not isinstance(event.get("id"), str):
        raise BadSignature("a body that is not an event")
    return event


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """The Stripe-Signature header Stripe would send `payload` with, for the tests and for
    trying an endpoint by hand."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _id(value: str) -> str:
    """An id put in a path, as Stripe's ids are made: letters, digits and underscores. Anything
    else is refused before it can move the request elsewhere."""
    if not value or not all(char.isascii() and (char.isalnum() or char == "_") for char in value):
        raise PaymentsError("A Stripe id was not of Stripe's form, so nothing was asked.")
    return value
