"""Billing's routes (app/billing.py): an owner sent to Stripe's Checkout to buy a plan and to
its customer portal to manage it, the way back from Checkout, and the webhook Stripe's events
arrive at.

The two pages are Stripe's, and the app's page goes to them by the address it is given here, so
nothing of Stripe's loads on the app's pages and the content security policy stays as it is. A
request commits what it read before Stripe is asked, so that no transaction waits on another
company's answer, and each is held to RATE_BILLING a minute per member.

The webhook is public, and the one route outside the cross-site check: Stripe sends no
X-Requested-With, and what it sends is proved by its signature instead, an HMAC over the body as
it came (app/payments.py), so the body is read raw, up to MAX_EVENT_BYTES. It counts no rate:
checking a signature costs less than counting a request, and turning Stripe's own deliveries
away in a burst of renewals would only delay them. A signature that does not hold is 400; an
event acted on, or of a kind not acted on, 200; Stripe out of reach while it is acted on, 503,
and Stripe delivers it again, for three days. The mail an event brings goes after the answer.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.auth import app_url, me_out
from app.api.deps import AccountDep, HttpDep, MailerDep, SessionDep, limited
from app.billing import (
    BILLING_OFF,
    finish_checkout,
    handle_event,
    portal_for,
    send_mail,
    start_checkout,
    stripe_for,
)
from app.config import get_settings
from app.payments import SIGNATURE_HEADER, BadSignature, Stripe, verified_event
from app.ratelimit import BILLING
from app.schemas import CheckoutDoneIn, CheckoutIn, MeOut, RedirectOut

log = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])
# Stripe's events, which main.py includes without the cross-site check.
events = APIRouter(tags=["billing"])

# Larger than any event of a subscription or an invoice of a few lines.
MAX_EVENT_BYTES = 1_000_000
TOO_LARGE = "That is larger than any event Stripe sends."
NOT_STRIPE = "That request does not carry Stripe's signature for this deployment."


def _stripe(http) -> Stripe:
    stripe = stripe_for(get_settings(), http)
    if stripe is None:
        raise HTTPException(404, BILLING_OFF)
    return stripe


@router.post(
    "/organization/billing/checkout",
    response_model=RedirectOut,
    dependencies=[Depends(limited(BILLING))],
)
def checkout(
    body: CheckoutIn, account: AccountDep, request: Request, session: SessionDep, http: HttpDep
):
    """The address of Stripe's Checkout page, where an owner buys `plan` for the organisation,
    with its seats on Team; an organisation's first subscription starts with a trial."""
    url = start_checkout(
        session, _stripe(http), account, request, body.plan, body.seats, app_url(request)
    )
    return RedirectOut(url=url)


@router.post(
    "/organization/billing/checkout/done",
    response_model=MeOut,
    dependencies=[Depends(limited(BILLING))],
)
def checkout_done(body: CheckoutDoneIn, account: AccountDep, session: SessionDep, http: HttpDep):
    """The organisation on the plan it just bought, once its owner is back from Checkout:
    Stripe's event says the same, a moment later or sooner."""
    finish_checkout(session, _stripe(http), account, body.checkout_id)
    return me_out(session, account)


@router.post(
    "/organization/billing/portal",
    response_model=RedirectOut,
    dependencies=[Depends(limited(BILLING))],
)
def portal(account: AccountDep, request: Request, session: SessionDep, http: HttpDep):
    """The address of Stripe's customer portal, where an owner changes the plan, the seats or
    the payment method, cancels, gives the billing address and VAT id, and finds the invoices."""
    return RedirectOut(url=portal_for(session, _stripe(http), account, app_url(request)))


async def raw_body(request: Request) -> bytes:
    """The request's body as it came, refused with 413 past MAX_EVENT_BYTES."""
    declared = request.headers.get("content-length", "")
    if declared.isdecimal() and int(declared) > MAX_EVENT_BYTES:
        raise HTTPException(413, TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_EVENT_BYTES:
            raise HTTPException(413, TOO_LARGE)
    return bytes(body)


@events.post("/billing/events")
def stripe_event(
    body: Annotated[bytes, Depends(raw_body)],
    request: Request,
    session: SessionDep,
    http: HttpDep,
    mailer: MailerDep,
    background: BackgroundTasks,
):
    """An event of Stripe's, signed with STRIPE_WEBHOOK_SECRET, acted on once."""
    settings = get_settings()
    stripe = stripe_for(settings, http)
    if stripe is None or not settings.stripe_webhook_secret:
        raise HTTPException(404, BILLING_OFF)
    try:
        event = verified_event(
            body, request.headers.get(SIGNATURE_HEADER), settings.stripe_webhook_secret
        )
    except BadSignature as bad:
        log.warning("A request to the billing webhook was refused: it carried %s", bad)
        raise HTTPException(400, NOT_STRIPE) from None
    mails = handle_event(session, stripe, event, app_url(request))
    background.add_task(send_mail, mailer, mails)
    return {"received": True}
