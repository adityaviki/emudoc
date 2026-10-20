"""Billing's routes (app/billing.py): an owner sent to Stripe's Checkout to buy a plan or a
pack of pages or questions for the month and to its customer portal to manage them, or to one
step of it that cancels the subscription or moves it to another plan, after reading here what
that would do; the ways back from both, and the webhook Stripe's events arrive at.

The two pages are Stripe's, and the app's page goes to them by the address it is given here, so
nothing of Stripe's loads on the app's pages and the content security policy stays as it is. A
request commits what it read before Stripe is asked, so that no transaction waits on another
company's answer, and each that asks Stripe is held to RATE_BILLING a minute per member.

The webhook is public, and outside the cross-site check: Stripe sends no X-Requested-With, and
what it sends is proved by its signature instead, an HMAC over the body as it came
(app/payments.py), so the body is read raw, up to MAX_EVENT_BYTES. It counts no rate: checking a
signature costs less than counting a request, and turning Stripe's own deliveries away in a
burst of renewals would only delay them. A signature that does not hold is 400; an event acted
on, or of a kind not acted on, 200; Stripe out of reach while it is acted on, 503, and Stripe
delivers it again, for three days. The mail an event brings is posted with what it acted on
(app/postbox.py).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import app_url, held_out, me_out
from app.api.deps import AccountDep, HttpDep, SessionDep, body_within, limited
from app.billing import (
    BILLING_OFF,
    billing_on,
    change_refusal,
    finish_checkout,
    finish_portal,
    handle_event,
    plan_change,
    portal_for,
    start_checkout,
    start_pack_checkout,
    stripe_for,
)
from app.config import get_settings
from app.payments import SIGNATURE_HEADER, BadSignature, Stripe, verified_event
from app.ratelimit import BILLING
from app.retention import RETENTION_HELD
from app.schemas import (
    AllowanceOut,
    CheckoutDoneIn,
    CheckoutIn,
    ExcessOut,
    MeOut,
    PackIn,
    PlanChangeOut,
    PlanChoiceIn,
    PortalIn,
    RedirectOut,
    RetentionOut,
)

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
    "/organization/billing/packs",
    response_model=RedirectOut,
    dependencies=[Depends(limited(BILLING))],
)
def buy_pack(
    body: PackIn, account: AccountDep, request: Request, session: SessionDep, http: HttpDep
):
    """The address of Stripe's Checkout page, where an owner buys a pack of pages or questions
    for the organisation's month, paid once."""
    url = start_pack_checkout(
        session, _stripe(http), account, request, body.unit, body.quantity, app_url(request)
    )
    return RedirectOut(url=url)


@router.post(
    "/organization/billing/checkout/done",
    response_model=MeOut,
    dependencies=[Depends(limited(BILLING))],
)
def checkout_done(body: CheckoutDoneIn, account: AccountDep, session: SessionDep, http: HttpDep):
    """The organisation on the plan, or with the pack, it just bought, once its owner is back
    from Checkout: Stripe's event says the same, a moment later or sooner."""
    finish_checkout(session, _stripe(http), account, body.checkout_id)
    return me_out(session, account)


@router.post(
    "/organization/billing/portal",
    response_model=RedirectOut,
    dependencies=[Depends(limited(BILLING))],
)
def portal(
    account: AccountDep,
    request: Request,
    session: SessionDep,
    http: HttpDep,
    body: PortalIn | None = None,
):
    """The address of Stripe's customer portal, where an owner changes the plan, the seats or
    the payment method, cancels, gives the billing address and VAT id, and finds the invoices;
    with a plan, of its one step that moves the subscription there, Free cancelling it."""
    body = body or PortalIn()
    url = portal_for(
        session, _stripe(http), account, app_url(request), plan=body.plan, seats=body.seats
    )
    return RedirectOut(url=url)


@router.get("/organization/billing/change", response_model=PlanChangeOut)
def change(account: AccountDep, session: SessionDep, choice: Annotated[PlanChoiceIn, Query()]):
    """What moving the subscription to `plan`, with `seats` on Team, or cancelling it for Free,
    would do to the organisation, for its owner to read before Stripe's page: what it would
    leave read-only, and which chats and audit log entries the plan keeps for less time and
    until when they would be kept as before. Asks Stripe nothing."""
    if not billing_on(get_settings()):
        raise HTTPException(404, BILLING_OFF)
    if refusal := change_refusal(get_settings(), account, choice.plan, choice.seats):
        raise refusal
    found = plan_change(session, account.organization, choice.plan, choice.seats)
    allowed = found.entitlements
    return PlanChangeOut(
        plan=found.plan,
        seats=found.seats,
        at=found.at,
        allowance=AllowanceOut(
            projects=allowed.projects,
            documents=allowed.documents,
            storage_bytes=allowed.storage_bytes,
            pages_per_month=allowed.pages_per_month,
            questions_per_month=allowed.questions_per_month,
            members=allowed.members,
            agreed=[],
            over=[ExcessOut(limit=excess.limit, words=excess.words) for excess in found.over],
        ),
        retention=RetentionOut(chat_days=found.kept.chat_days, audit_days=found.kept.audit_days),
        held=held_out(found.held),
        held_days=RETENTION_HELD.days,
    )


@router.post(
    "/organization/billing/portal/done",
    response_model=MeOut,
    dependencies=[Depends(limited(BILLING))],
)
def portal_done(account: AccountDep, request: Request, session: SessionDep, http: HttpDep):
    """The organisation as its subscription stands, once its owner is back from Stripe's
    portal: Stripe's event says the same, a moment later or sooner, and whichever is first
    mails the owners what a cancellation or a lower plan means."""
    finish_portal(session, _stripe(http), account, app_url(request))
    return me_out(session, account)


async def raw_body(request: Request) -> bytes:
    """The request's body as it came, refused with 413 past MAX_EVENT_BYTES."""
    return await body_within(request, MAX_EVENT_BYTES, TOO_LARGE)


@events.post("/billing/events")
def stripe_event(
    body: Annotated[bytes, Depends(raw_body)], request: Request, session: SessionDep, http: HttpDep
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
    handle_event(session, stripe, event, app_url(request))
    return {"received": True}
