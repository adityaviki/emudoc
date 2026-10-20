"""How an organisation's plan changes: the one function that puts it on a plan, with the seats it
bought and the figures agreed with it, and records in its audit log what changed; and how a plan
is bought through Stripe, whose subscription then sets it. The operator's `plan` command calls
`change_plan` (app/maintenance.py), and so does everything below.

What an organisation may keep and use is read from those three by `entitlements_of`
(app/entitlements.py). Seats are bought on a plan sold by the seat (`BY_THE_SEAT`) and replace
the plan's members; agreed figures replace any of the plan's, which is how an Enterprise
organisation gets its terms. Both belong to the plan they were set on: a move to another plan
clears them unless they are given again, so that an agreement does not outlive its plan. Fewer
seats or lower figures than the organisation uses leave it over its limits without removing
anything: it keeps its members, projects and documents, and is read-only until it is within
them again (`read_only` in app/entitlements.py).

Billing (deploying.md, "Billing") is on with STRIPE_SECRET_KEY: an owner buys a plan of
STRIPE_PRICES, Pro or Team with its seats, on Stripe's Checkout page, and changes or cancels it,
the payment method and the billing details on Stripe's customer portal; the app makes the
organisation a customer first (`customer_for`) and sends the owner to either page
(app/api/billing.py). Stripe works out the tax where STRIPE_TAX says so and asks a business for
its VAT id, which its invoices then carry. The subscription sets the plan, by what Stripe says of
it now (`apply_subscription`): its price's plan, with its quantity as Team's seats, while it is
in its trial or paid, and while a payment has failed for fewer than BILLING_GRACE_DAYS, during
which Stripe tries again and the owners are written to; the Free plan once it ends, is left
unpaid or the grace period runs out (`end_grace_periods`, the housekeeper's), the organisation
keeping all it holds, read-only where that is more than Free allows, and its chats and audit
rows kept as before for a while (`hold_retention` in app/retention.py, which `change_plan` calls
for every move to a plan that keeps them for less time). Stripe's events
(`handle_event`, from the webhook) only say when to ask: each is acted on once
(`billing_events`), and the subscription it names is read from Stripe as it stands rather than
from the event, which may come late or after a later one. An organisation
on Enterprise, or with figures agreed with it, is the operator's to move: its subscription is
recorded, and its plan left alone. An organisation's first subscription starts with a trial of
STRIPE_TRIAL_DAYS.

Lowering a plan or cancelling is shown before it is done: an owner chooses a lower plan, fewer
seats or Free on the organisation page, which asks `plan_change` what that would leave
read-only and which chats and audit rows the lower plan keeps for less time, until when they
are held, and then opens Stripe's portal on that one step (`portal_for` with a plan: Stripe's
cancellation, or its confirmation of the new price and quantity, with the proration it works
out), which sends the owner back to the page. The page then asks Stripe for the subscription
as it stands (`finish_portal`) rather than wait for its event. A cancellation, and the lower
plan once it comes, are mailed to the owners with the same account of what they mean.
"""

import logging
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo

import httpx
from fastapi import Request
from sqlalchemy import Connection, func, select, text, update
from sqlalchemy.orm import Session

from app.accounts import Account, zone_of
from app.audit import audit, audit_event
from app.config import PACK_UNITS, SOLD_PLANS, Pack, Settings, get_settings
from app.db import SessionLocal
from app.entitlements import (
    BY_THE_SEAT,
    FIELDS,
    MODEL_TIERS,
    PLANS,
    Entitlements,
    Excess,
    agreed_with,
    entitlements_of,
    listed,
    month_of,
    over_limits,
    seats_of,
)
from app.mail import (
    Message,
    cancellation_email,
    compose,
    payment_failed_email,
    plan_lapsed_email,
    plan_lowered_email,
    trial_ending_email,
)
from app.models import PLANS as PLAN_NAMES
from app.models import BillingEvent, Membership, Organization, User
from app.payments import Stripe
from app.postbox import post
from app.retention import (
    RETENTION_HELD,
    Held,
    Kept,
    day,
    hold_retention,
    kept_by,
    kept_for,
    retention_held,
    shortened,
)

log = logging.getLogger(__name__)

assert set(SOLD_PLANS) <= set(PLANS) - {PLAN_NAMES[0], PLAN_NAMES[-1]}, "only paid plans are sold"

# The entitlements that are a yes or a no; `model_tier` is one of MODEL_TIERS, `priority` a
# weight of at least 1, and every other one a limit, a count of at least 0 or None for none.
SWITCHES = ("api_access", "connectors")
# Stands for a figure not agreed, which a figure agreed to be no limit (None) is not.
_NONE = object()

# The page the owners are sent back to from Stripe's, and the links in billing's mail lead to.
ORGANIZATION_PAGE = "/organization"
# What the organisation page is sent back from Stripe's portal with, to ask Stripe about the
# subscription as it stands.
PORTAL_BACK = "billing=back"
FREE = PLAN_NAMES[0]
# The subscription statuses that keep its plan: in its trial, or paid. A past-due one keeps it
# for the grace period; an incomplete one, whose first payment waits for the buyer, changes
# nothing yet; any other has ended, or is left unpaid, and the organisation is on Free.
KEEPS_PLAN = frozenset({"trialing", "active"})
PAST_DUE = "past_due"
INCOMPLETE = "incomplete"
# The statuses of a subscription waiting for a payment, or that ended for want of one: a past-due
# one Stripe cancels after its last try, one left unpaid, one whose trial ended without a payment
# method.
UNPAID = frozenset({PAST_DUE, "unpaid", "paused"})
# The statuses of a subscription that is still billed: an organisation with one buys no second,
# and its only member deletes no account under it until it is set to end.
BILLED = frozenset({*KEEPS_PLAN, PAST_DUE, INCOMPLETE})
# How long an event's id is kept: longer than the three days Stripe goes on delivering one.
EVENT_HISTORY = timedelta(days=30)
# How long a Checkout page stays open: long enough to pay, short enough that one left open does
# not buy a second subscription later.
CHECKOUT_MINUTES = 60
# The events acted on; the webhook endpoint is made to send these (deploying.md, "Billing").
SUBSCRIPTION_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "customer.subscription.trial_will_end",
    }
)
CHECKOUT_COMPLETED = "checkout.session.completed"
# A pack paid by a method that takes days, such as a SEPA debit, is paid once this comes.
PAYMENT_ARRIVED = "checkout.session.async_payment_succeeded"
CHECKOUTS = frozenset({CHECKOUT_COMPLETED, PAYMENT_ARRIVED})
PAYMENT_FAILED = "invoice.payment_failed"
EVENTS = SUBSCRIPTION_EVENTS | CHECKOUTS | {PAYMENT_FAILED}
# How each unit of a pack is said, and which entitlement it adds to.
PACK_LIMITS = {"pages": "pages_per_month", "questions": "questions_per_month"}
assert set(PACK_LIMITS) == set(PACK_UNITS), "every unit a pack may be of adds to a limit"

BILLING_OFF = (
    "Plans are not sold on this deployment. Write to the people who run it to change the plan."
)
OWNERS_ONLY = "Only an owner of the organisation can buy a plan or manage its billing."
AGREED = (
    "This organisation's terms were agreed with the people who run Emudoc, so they change "
    "its plan. Write to them to change it."
)
SUBSCRIBED = (
    "This organisation has a subscription already. Change its plan or seats, or cancel it, "
    "under Manage billing."
)
NOT_SOLD = "That plan is not sold here. Choose one the organisation page offers."
SEATS_NEEDED = "Say how many seats to buy: the Team plan is sold by the seat."
SEATS_TEAM_ONLY = "Only the Team plan is sold by the seat; buy the {plan} plan without seats."
NO_SEATS = "Only the Team plan has seats; choose the {plan} plan without them."
NO_SUBSCRIPTION = (
    "This organisation has no subscription to change or cancel. Choose a plan to buy one."
)
PAY_FIRST = (
    "A payment of this subscription is outstanding. Pay it under Manage billing first, where "
    "it can also be cancelled."
)
ENDING = (
    "The subscription is cancelled already and ends on {day}. To keep it, renew it under "
    "Manage billing."
)
SAME_PLAN = "The organisation is on that plan already."
UNLISTED = (
    "The subscription is at a price this deployment does not sell, so it cannot be changed "
    "here. Change it under Manage billing."
)
NO_CUSTOMER = (
    "This organisation has bought nothing yet, so there is no billing to manage. Choose a "
    "plan to buy one."
)
NOT_FINISHED = (
    "The payment is not finished yet. Finish it on Stripe's page, or choose the plan again."
)
OTHER_CHECKOUT = "That payment was for another organisation, so nothing changed here."
NO_PACK = "That pack is not sold here. Choose one the organisation page offers."
NO_LIMIT = "The {plan} plan has no limit on {unit}, so it needs no pack of them."
PAYMENT_ON_ITS_WAY = (
    "The payment is on its way; the pack counts once it arrives, which can take a few days for "
    "a bank debit."
)


@dataclass(frozen=True)
class Applied:
    """What a subscription did to an organisation: its status, plan and seats before and after;
    whether the move held its chats and audit rows as before (app/retention.py), and whether
    the subscription was set to end."""

    was_status: str | None
    status: str | None
    was_plan: str
    plan: str
    was_seats: int | None = None
    seats: int | None = None
    held: bool = False
    cancelled: bool = False

    @property
    def lapsed(self) -> bool:
        """Whether a paid plan ended for want of a payment rather than by an owner's choice:
        the owners are told, since nobody chose it."""
        unpaid = self.status in UNPAID or self.was_status in UNPAID
        return unpaid and self.plan == FREE != self.was_plan

    @property
    def lowered(self) -> bool:
        """Whether the organisation moved to a lower plan, or to fewer seats on its own."""
        if self.plan != self.was_plan:
            return PLAN_NAMES.index(self.plan) < PLAN_NAMES.index(self.was_plan)
        return None not in (self.seats, self.was_seats) and self.seats < self.was_seats


@dataclass(frozen=True)
class Change:
    """What moving the organisation to `plan`, with `seats` on a plan sold by them, would do to
    it: what it would allow, what the organisation would hold past that, which would leave it
    read-only, how long it would keep chats and audit rows, and what it keeps now that would be
    held for RETENTION_HELD after the move. `at` is when a cancellation would move it, the end
    of the period paid for; None for a change Stripe makes once it is confirmed."""

    plan: str
    seats: int | None
    entitlements: Entitlements
    over: list[Excess]
    kept: Kept
    held: Held | None
    at: datetime | None


class BillingRefused(Exception):
    """A request billing turns down, with the sentence the member is shown and its status."""

    def __init__(self, status: int, sentence: str):
        super().__init__(sentence)
        self.status = status


def change_plan(
    session: Session,
    organization: Organization,
    plan: str,
    *,
    seats: int | None = None,
    overrides: Mapping[str, object] | None = None,
    because: str | None = None,
) -> None:
    """Put the organisation on `plan` with `seats` and the `overrides` agreed with it, the three
    as a whole (None and nothing: the plan's own), and write an audit row for each part that
    changed, one per agreed figure, with `because` in each when billing moved it. Chats and
    audit rows the new ones keep for less time are held as before for a while
    (`hold_retention`). Raises RuntimeError with the reason for a plan, seats or a figure that
    cannot be. The caller commits."""
    overrides = dict(overrides or {})
    if problem := plan_problem(plan, seats, overrides):
        raise RuntimeError(problem)
    was_plan, was_seats, was_overrides = (
        organization.plan,
        organization.seats,
        dict(organization.overrides or {}),
    )
    was_kept = kept_by(entitlements_of(organization))
    organization.plan, organization.seats, organization.overrides = plan, seats, overrides
    # Nothing a lower plan keeps for less time goes at once.
    hold_retention(organization, was_kept)

    def changed(action: str, detail: dict[str, object]) -> None:
        if because:
            detail["because"] = because
        audit_event(session, action, org_id=organization.id, detail=detail)

    if plan != was_plan:
        changed("plan_changed", {"from": was_plan, "to": plan})
    if seats != was_seats:
        changed("seats_changed", {"from": was_seats, "to": seats})
    for name in FIELDS:
        if was_overrides.get(name, _NONE) == overrides.get(name, _NONE):
            continue
        # A figure no longer agreed is the plan's again: `agreed` tells that from one agreed
        # to be no limit, both of which are null.
        changed(
            "entitlements_changed",
            {
                "entitlement": name,
                "from": was_overrides.get(name),
                "to": overrides.get(name),
                "agreed": name in overrides,
            },
        )


def plan_problem(plan: str, seats: int | None, overrides: Mapping[str, object]) -> str | None:
    """Why the organisation cannot be put on `plan` with these seats and figures, or None."""
    if plan not in PLANS:
        return f"There is no {plan!r} plan; the plans are {', '.join(PLANS)}."
    label = plan.capitalize()
    if seats is not None:
        if plan not in BY_THE_SEAT:
            sold = ", ".join(name.capitalize() for name in sorted(BY_THE_SEAT))
            return f"The {label} plan is not sold by the seat; only {sold} is."
        if type(seats) is not int or seats < 1:
            return f"Seats are a whole number of one or more, not {seats!r}."
    for name, value in overrides.items():
        if name not in FIELDS:
            return f"{name!r} is no entitlement; they are {', '.join(FIELDS)}."
        if name == "members" and plan in BY_THE_SEAT:
            return f"The {label} plan's members are its seats; set the seats instead."
        if problem := _figure_problem(name, value):
            return problem
    return None


def _figure_problem(name: str, value: object) -> str | None:
    if name in SWITCHES:
        return None if type(value) is bool else f"{name} is yes or no, not {value!r}."
    if name == "model_tier":
        tiers = ", ".join(MODEL_TIERS)
        return None if value in MODEL_TIERS else f"{name} is one of {tiers}, not {value!r}."
    if name == "priority":
        whole = type(value) is int and value >= 1
        return None if whole else f"{name} is a weight of 1 or more, not {value!r}."
    if value is None or type(value) is int and value >= 0:
        return None
    return f"{name} is a whole number of 0 or more, or none for no limit, not {value!r}."


# Buying


def billing_on(settings: Settings) -> bool:
    """Whether the deployment sells its plans (STRIPE_SECRET_KEY)."""
    return bool(settings.stripe_secret_key)


def stripe_for(settings: Settings, http: httpx.Client) -> Stripe | None:
    """Stripe, with the deployment's key, through `http`; None while billing is off."""
    return Stripe(http, settings.stripe_secret_key) if billing_on(settings) else None


def sold(settings: Settings) -> dict[str, str]:
    """The plans on sale, each with the price Checkout sells it at, in the order of the plans."""
    listed = settings.stripe_plans
    return {plan: listed[plan][0] for plan in SOLD_PLANS if plan in listed}


def managed(organization: Organization) -> bool:
    """Whether billing moves the organisation's plan: not when its terms were agreed with the
    operator, an Enterprise organisation's or any with figures of its own."""
    return organization.plan != PLAN_NAMES[-1] and not agreed_with(organization)


def trial_days(settings: Settings, organization: Organization) -> int | None:
    """The trial a subscription bought now starts with: STRIPE_TRIAL_DAYS for an organisation's
    first, none for one that had a subscription before."""
    if organization.stripe_subscription_id is not None or not settings.stripe_trial_days:
        return None
    return settings.stripe_trial_days


def checkout_refusal(
    settings: Settings, account: Account, plan: str, seats: int | None
) -> BillingRefused | None:
    """Why the account cannot buy `plan` with `seats` now, or None."""
    organization = account.organization
    if account.role != "owner":
        return BillingRefused(403, OWNERS_ONLY)
    if not managed(organization):
        return BillingRefused(409, AGREED)
    if organization.subscription_status in BILLED:
        return BillingRefused(409, SUBSCRIBED)
    if plan not in sold(settings):
        return BillingRefused(400, NOT_SOLD)
    if plan in BY_THE_SEAT and seats is None:
        return BillingRefused(400, SEATS_NEEDED)
    if plan not in BY_THE_SEAT and seats is not None:
        return BillingRefused(400, SEATS_TEAM_ONLY.format(plan=plan.capitalize()))
    return None


def customer_for(session: Session, stripe: Stripe, account: Account) -> str:
    """The organisation's customer at Stripe, made the first time with its name and the
    address of the owner who buys. Nothing is held while Stripe is asked: two owners who buy at
    once may both make one, and the first stored is kept (the other is never used)."""
    organization = account.organization
    if organization.stripe_customer_id:
        return organization.stripe_customer_id
    org_id, name, email = organization.id, organization.name, account.user.email
    session.commit()
    customer = stripe.create_customer(org_id, name, email)
    stored = session.execute(
        update(Organization)
        .where(Organization.id == org_id, Organization.stripe_customer_id.is_(None))
        .values(stripe_customer_id=customer)
    ).rowcount
    session.commit()
    session.refresh(organization)
    if not stored:
        log.info(
            "Organisation %s had a customer at Stripe already; %s is not used", org_id, customer
        )
    return organization.stripe_customer_id


def checkout_params(
    settings: Settings,
    organization: Organization,
    customer: str,
    plan: str,
    seats: int | None,
    app_url: str,
    now: datetime | None = None,
) -> dict:
    """The Checkout session that sells `plan` (with `seats` on Team) to the organisation."""
    now = now or datetime.now(UTC)
    page = f"{app_url}{ORGANIZATION_PAGE}"
    subscription: dict[str, object] = {"metadata": {"org_id": organization.id}}
    if days := trial_days(settings, organization):
        subscription["trial_period_days"] = days
    params: dict[str, object] = {
        "mode": "subscription",
        "customer": customer,
        "client_reference_id": str(organization.id),
        "line_items": [{"price": sold(settings)[plan], "quantity": seats or 1}],
        # Stripe puts the session's id in place of the braces, which the page then asks about.
        "success_url": page + "?checkout={CHECKOUT_SESSION_ID}",
        "cancel_url": page,
        "metadata": {"org_id": organization.id},
        "subscription_data": subscription,
        # An invoice to a business carries its address and its VAT id, which Checkout asks for
        # and keeps on the customer.
        "billing_address_collection": "required",
        "tax_id_collection": {"enabled": True},
        "customer_update": {"name": "auto", "address": "auto"},
        # Whoever pays accepts the terms of service as they stand, paid plans' included
        # (deploying.md, "Billing": the terms' address in Stripe's settings).
        "consent_collection": {"terms_of_service": "required"},
        "expires_at": int((now + timedelta(minutes=CHECKOUT_MINUTES)).timestamp()),
    }
    if settings.stripe_tax:
        params["automatic_tax"] = {"enabled": True}
    return params


def start_checkout(
    session: Session,
    stripe: Stripe,
    account: Account,
    request: Request | None,
    plan: str,
    seats: int | None,
    app_url: str,
) -> str:
    """The address of a Checkout page that sells `plan` to the account's organisation, which is
    recorded in its audit log as begun; raises BillingRefused when it may not buy it."""
    settings = get_settings()
    if refusal := checkout_refusal(settings, account, plan, seats):
        raise refusal
    customer = customer_for(session, stripe, account)
    params = checkout_params(settings, account.organization, customer, plan, seats, app_url)
    session.commit()
    checkout = stripe.create_checkout(params, idempotency=f"checkout-{uuid.uuid4().hex}")
    audit(session, account, request, "checkout_started", detail={"plan": plan, "seats": seats})
    session.commit()
    return checkout["url"]


def finish_checkout(session: Session, stripe: Stripe, account: Account, checkout_id: str) -> None:
    """Put the organisation on the plan the Checkout session `checkout_id` bought, once the
    owner is back from it, rather than wait for Stripe's event, which says the same."""
    org_id = account.organization.id
    session.commit()
    checkout = stripe.checkout(checkout_id)
    if checkout.get("client_reference_id") != str(org_id):
        raise BillingRefused(404, OTHER_CHECKOUT)
    if checkout.get("status") != "complete":
        raise BillingRefused(409, NOT_FINISHED)
    if checkout.get("mode") == "payment":
        if not paid(checkout):
            raise BillingRefused(409, PAYMENT_ON_ITS_WAY)
        record_pack(session, _held(session, org_id), checkout)
        session.commit()
        return
    if not _id_of(checkout.get("subscription")):
        raise BillingRefused(409, NOT_FINISHED)
    subscription = stripe.subscription(_id_of(checkout["subscription"]))
    organization = _held(session, org_id)
    apply_subscription(session, organization, subscription)
    session.commit()


def packs_for(settings: Settings, organization: Organization) -> list[Pack]:
    """The packs the organisation may buy: those of a unit its plan limits, while billing moves
    its plan."""
    if not managed(organization):
        return []
    entitlements = entitlements_of(organization)
    return [
        pack for pack in settings.packs if getattr(entitlements, PACK_LIMITS[pack.unit]) is not None
    ]


def start_pack_checkout(
    session: Session,
    stripe: Stripe,
    account: Account,
    request: Request | None,
    unit: str,
    quantity: int,
    app_url: str,
    now: datetime | None = None,
) -> str:
    """The address of a Checkout page that sells the pack of `quantity` `unit` to the account's
    organisation, paid once, with an invoice; raises BillingRefused when it may not buy it."""
    settings = get_settings()
    organization = account.organization
    if account.role != "owner":
        raise BillingRefused(403, OWNERS_ONLY)
    if not managed(organization):
        raise BillingRefused(409, AGREED)
    pack = next((p for p in settings.packs if (p.unit, p.quantity) == (unit, quantity)), None)
    if pack is None:
        raise BillingRefused(400, NO_PACK)
    if pack not in packs_for(settings, organization):
        raise BillingRefused(409, NO_LIMIT.format(plan=organization.plan.capitalize(), unit=unit))
    customer = customer_for(session, stripe, account)
    now = now or datetime.now(UTC)
    page = f"{app_url}{ORGANIZATION_PAGE}"
    params: dict[str, object] = {
        "mode": "payment",
        "customer": customer,
        "client_reference_id": str(organization.id),
        "line_items": [{"price": pack.price, "quantity": 1}],
        "success_url": page + "?checkout={CHECKOUT_SESSION_ID}",
        "cancel_url": page,
        "metadata": {"org_id": organization.id, "pack_unit": unit, "pack_quantity": quantity},
        # A payment of its own gets no invoice unless asked for one, and a business needs one.
        "invoice_creation": {"enabled": True},
        "billing_address_collection": "required",
        "tax_id_collection": {"enabled": True},
        "customer_update": {"name": "auto", "address": "auto"},
        "consent_collection": {"terms_of_service": "required"},
        "expires_at": int((now + timedelta(minutes=CHECKOUT_MINUTES)).timestamp()),
    }
    if settings.stripe_tax:
        params["automatic_tax"] = {"enabled": True}
    session.commit()
    checkout = stripe.create_checkout(params, idempotency=f"checkout-{uuid.uuid4().hex}")
    audit(
        session,
        account,
        request,
        "checkout_started",
        detail={"pack": unit, "quantity": quantity},
    )
    session.commit()
    return checkout["url"]


def paid(checkout: Mapping) -> bool:
    """Whether a Checkout session's payment has arrived."""
    return checkout.get("payment_status") in ("paid", "no_payment_required")


def record_pack(session: Session, organization: Organization, checkout: Mapping) -> bool:
    """Count the pack a paid Checkout session bought for the month it is paid in, once
    whichever of the page and Stripe's events says so first; the caller commits. Returns
    whether this was the first time."""
    metadata = checkout.get("metadata") or {}
    unit, quantity = metadata.get("pack_unit"), str(metadata.get("pack_quantity", ""))
    if unit not in PACK_LIMITS or not (quantity.isascii() and quantity.isdecimal()):
        log.warning("Checkout session %s names no pack, so nothing is counted", checkout.get("id"))
        return False
    month = month_of()[0]
    recorded = session.execute(
        text(
            "INSERT INTO allowance_packs (org_id, unit, quantity, month, checkout_id) "
            "VALUES (:org_id, :unit, :quantity, :month, :checkout_id) "
            "ON CONFLICT (checkout_id) DO NOTHING RETURNING id"
        ),
        {
            "org_id": organization.id,
            "unit": unit,
            "quantity": int(quantity),
            "month": month,
            "checkout_id": checkout["id"],
        },
    ).scalar()
    if recorded is None:
        return False
    audit_event(
        session,
        "pack_bought",
        org_id=organization.id,
        detail={"unit": unit, "quantity": int(quantity), "month": f"{month:%Y-%m}"},
    )
    return True


def portal_for(
    session: Session,
    stripe: Stripe,
    account: Account,
    app_url: str,
    plan: str | None = None,
    seats: int | None = None,
) -> str:
    """The address of the customer portal for the account's organisation: its home page, or
    with `plan` the one step that moves the subscription there, Free being its cancellation;
    raises BillingRefused when the owner may not take it (`change_refusal`). Either sends the
    owner back to the organisation page, which then asks for the subscription as it stands."""
    if account.role != "owner":
        raise BillingRefused(403, OWNERS_ONLY)
    organization = account.organization
    customer = organization.stripe_customer_id
    if not customer:
        raise BillingRefused(409, NO_CUSTOMER)
    back = f"{app_url}{ORGANIZATION_PAGE}?{PORTAL_BACK}"
    if plan is None:
        session.commit()
        return stripe.create_portal(customer, back)
    settings = get_settings()
    if refusal := change_refusal(settings, account, plan, seats):
        raise refusal
    subscription_id = organization.stripe_subscription_id
    session.commit()
    done = {"type": "redirect", "redirect": {"return_url": back}}
    if plan == FREE:
        flow = {
            "type": "subscription_cancel",
            "subscription_cancel": {"subscription": subscription_id},
            "after_completion": done,
        }
    else:
        # Stripe's step changes one item, named by its id, which only the subscription holds.
        item = _sold_item(stripe.subscription(subscription_id), settings)
        if item is None:
            raise BillingRefused(409, UNLISTED)
        flow = {
            "type": "subscription_update_confirm",
            "subscription_update_confirm": {
                "subscription": subscription_id,
                "items": [{"id": item, "price": sold(settings)[plan], "quantity": seats or 1}],
            },
            "after_completion": done,
        }
    return stripe.create_portal(customer, back, flow)


def change_refusal(
    settings: Settings, account: Account, plan: str, seats: int | None
) -> BillingRefused | None:
    """Why the account cannot move its organisation's subscription to `plan` with `seats`, or
    cancel it for Free, here now; None when it can. A subscription in its trial or paid moves;
    one waiting for a payment is paid first, and one cancelled is renewed first, both under
    Manage billing."""
    organization = account.organization
    if account.role != "owner":
        return BillingRefused(403, OWNERS_ONLY)
    if not managed(organization):
        return BillingRefused(409, AGREED)
    status = organization.subscription_status
    if status not in BILLED:
        return BillingRefused(409, NO_SUBSCRIPTION)
    if status not in KEEPS_PLAN:
        return BillingRefused(409, PAY_FIRST)
    if organization.ends_at is not None:
        ends = day(organization.ends_at, zone_of(account.user.time_zone))
        return BillingRefused(409, ENDING.format(day=ends))
    if plan != FREE and plan not in sold(settings):
        return BillingRefused(400, NOT_SOLD)
    if plan in BY_THE_SEAT and seats is None:
        return BillingRefused(400, SEATS_NEEDED)
    if plan not in BY_THE_SEAT and seats is not None:
        return BillingRefused(400, NO_SEATS.format(plan=plan.capitalize()))
    if (plan, seats) == (organization.plan, seats_of(organization)):
        return BillingRefused(400, SAME_PLAN)
    return None


def plan_change(
    session: Session, organization: Organization, plan: str, seats: int | None = None
) -> Change:
    """What moving the organisation to `plan`, with `seats` on Team, would do to it. The
    organisation's terms are a plan's own, since billing moves no other: Free is its
    subscription cancelled, which moves it at the end of the period paid for."""
    entitlements = PLANS[plan]
    if plan in BY_THE_SEAT and seats is not None:
        entitlements = replace(entitlements, members=seats)
    at = (organization.ends_at or organization.renews_at) if plan == FREE else None
    kept, kept_now = kept_by(entitlements), kept_for(organization)
    return Change(
        plan=plan,
        seats=seats,
        entitlements=entitlements,
        over=over_limits(session, organization, entitlements),
        kept=kept,
        held=None if kept.covers(kept_now) else Held(at + RETENTION_HELD if at else None, kept_now),
        at=at,
    )


def finish_portal(
    session: Session, stripe: Stripe, account: Account, app_url: str
) -> list[Message]:
    """Put the organisation on what its subscription is as Stripe holds it, once an owner is
    back from the portal, rather than wait for Stripe's event, which says the same; posts the
    mail for what this found first (a cancellation, a lower plan), which the event then does
    not post again, and returns it."""
    org_id = account.organization.id
    subscription_id = account.organization.stripe_subscription_id
    session.commit()
    if not subscription_id:
        return []
    subscription = stripe.subscription(subscription_id)
    organization = _held(session, org_id)
    applied = apply_subscription(session, organization, subscription)
    mails = _news(session, organization, applied, app_url)
    _post(session, organization.id, mails)
    session.commit()
    return mails


# What a subscription does


def apply_subscription(
    session: Session,
    organization: Organization,
    subscription: Mapping,
    now: datetime | None = None,
) -> Applied:
    """Record what Stripe says of the subscription on its organisation, which the caller holds,
    and put the organisation on the plan it pays for, or on Free once it has ended or lapsed;
    the caller commits. A subscription other than the organisation's own, while that is still
    billed, changes nothing: a late event of one it had before, or a second bought at once,
    which the operator is told of to cancel."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    was_status, was_plan = organization.subscription_status, organization.plan
    was_seats, was_ends, was_held = (
        organization.seats,
        organization.ends_at,
        organization.retention_held_until,
    )
    subscription_id, status = subscription["id"], subscription.get("status")
    current = organization.stripe_subscription_id
    if current and current != subscription_id and was_status in BILLED:
        if status in BILLED:
            log.error(
                "Organisation %s has two subscriptions billed at Stripe, %s and %s; the first "
                "sets its plan. Cancel the one it should not have, and refund it.",
                organization.id,
                current,
                subscription_id,
            )
        return Applied(was_status, was_status, was_plan, was_plan)
    organization.stripe_subscription_id = subscription_id
    organization.stripe_customer_id = organization.stripe_customer_id or _id_of(
        subscription.get("customer")
    )
    organization.subscription_status = status
    period_end = _period_end(subscription)
    billed = status in BILLED
    trial_end = subscription.get("trial_end") if status == "trialing" else None
    organization.trial_ends_at = _moment(trial_end)
    ending = subscription.get("cancel_at") or (
        period_end if subscription.get("cancel_at_period_end") else None
    )
    organization.ends_at = _moment(ending) if billed and ending else None
    organization.renews_at = _moment(period_end) if billed and not ending else None
    if status == PAST_DUE:
        organization.payment_failed_at = organization.payment_failed_at or now
    elif status != INCOMPLETE:
        organization.payment_failed_at = None
    if status != was_status:
        audit_event(
            session,
            "subscription_changed",
            org_id=organization.id,
            detail={"from": was_status, "to": status},
        )
    if organization.ends_at is not None and was_ends is None:
        audit_event(
            session,
            "subscription_cancelled",
            org_id=organization.id,
            detail={"ends_at": organization.ends_at.isoformat()},
        )
    elif organization.ends_at is None and was_ends is not None and billed:
        audit_event(session, "cancellation_withdrawn", org_id=organization.id)
    if not managed(organization):
        log.info(
            "Organisation %s's terms are agreed, so its subscription %s (%s) leaves its plan "
            "as it is",
            organization.id,
            subscription_id,
            status,
        )
    elif status == INCOMPLETE:
        pass
    elif _keeps_plan(organization, settings, now):
        bought = _bought(subscription, settings)
        if bought is None:
            log.warning(
                "Subscription %s of organisation %s is at a price STRIPE_PRICES does not list, "
                "so its plan stays %s; add the price to the plan it sells",
                subscription_id,
                organization.id,
                organization.plan,
            )
        else:
            plan, quantity = bought
            seats = quantity if plan in BY_THE_SEAT else None
            if (plan, seats) != (organization.plan, organization.seats):
                change_plan(session, organization, plan, seats=seats, because="subscription")
    elif organization.plan != FREE:
        because = "grace_ended" if status == PAST_DUE else "subscription_ended"
        change_plan(session, organization, FREE, because=because)
    held_until = organization.retention_held_until
    return Applied(
        was_status,
        status,
        was_plan,
        organization.plan,
        was_seats=was_seats,
        seats=organization.seats,
        held=held_until is not None and held_until != was_held,
        cancelled=organization.ends_at is not None and was_ends is None,
    )


def _keeps_plan(organization: Organization, settings: Settings, now: datetime) -> bool:
    status = organization.subscription_status
    if status in KEEPS_PLAN:
        return True
    return status == PAST_DUE and now < grace_end(organization, settings)


def grace_end(organization: Organization, settings: Settings) -> datetime:
    """When a subscription whose payment failed stops keeping its plan."""
    failed = organization.payment_failed_at or datetime.now(UTC)
    return failed + timedelta(days=settings.billing_grace_days)


def _bought(subscription: Mapping, settings: Settings) -> tuple[str, int] | None:
    """The plan the subscription pays for and its quantity, by the first of its items whose
    price STRIPE_PRICES lists; None when none is."""
    plans = {price: plan for plan, prices in settings.stripe_plans.items() for price in prices}
    for item in _items(subscription):
        price = item.get("price") or {}
        if (plan := plans.get(price.get("id"))) is not None:
            return plan, int(item.get("quantity") or 1)
    return None


def _sold_item(subscription: Mapping, settings: Settings) -> str | None:
    """The id of the subscription's item whose price STRIPE_PRICES lists, which a change of
    plan changes; None when none is."""
    plans = {price for prices in settings.stripe_plans.values() for price in prices}
    for item in _items(subscription):
        if (item.get("price") or {}).get("id") in plans and isinstance(item.get("id"), str):
            return item["id"]
    return None


def _items(subscription: Mapping) -> list[Mapping]:
    items = (subscription.get("items") or {}).get("data") or []
    return [item for item in items if isinstance(item, Mapping)]


def _period_end(subscription: Mapping) -> int | None:
    """When the period paid for ends, which is each item's since 2025: the earliest."""
    ends = [item.get("current_period_end") for item in _items(subscription)]
    ends = [end for end in ends if isinstance(end, int)]
    return min(ends) if ends else None


def _moment(timestamp: object) -> datetime | None:
    return datetime.fromtimestamp(timestamp, UTC) if isinstance(timestamp, int) else None


def _id_of(value: object) -> str | None:
    """An id, whether Stripe gave it as one or as the object it names."""
    if isinstance(value, Mapping):
        value = value.get("id")
    return value if isinstance(value, str) else None


def _held(session: Session, org_id: int) -> Organization:
    """The organisation as it is now, held until the commit, so that two events about it take
    their turns."""
    return session.scalar(
        select(Organization)
        .where(Organization.id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


# Stripe's events


def handle_event(session: Session, stripe: Stripe, event: Mapping, app_url: str) -> list[Message]:
    """Act on a Stripe event once, posting the mail it calls for in the same transaction, and return
    that mail. An event of a kind not acted on, one seen before, and one about a customer that
    is no organisation's change nothing. Stripe is asked for the subscription an event names
    before anything is held, and a PaymentsError leaves everything as it was, for Stripe to
    deliver the event again."""
    kind = event.get("type")
    if kind not in EVENTS:
        return []
    found = (event.get("data") or {}).get("object") or {}
    seen = session.get(BillingEvent, event["id"]) is not None
    org_id = None if seen else _organization_of(session, kind, found)
    session.rollback()
    if org_id is None:
        if not seen:
            log.info("Stripe event %s (%s) is about no organisation here", event["id"], kind)
        return []
    subscription = None
    pack = kind in CHECKOUTS and found.get("mode") == "payment"
    if pack and not paid(found):
        return []
    if kind in CHECKOUTS and not pack:
        if kind != CHECKOUT_COMPLETED or not _id_of(found.get("subscription")):
            return []
        subscription = stripe.subscription(_id_of(found["subscription"]))
    elif kind in SUBSCRIPTION_EVENTS:
        subscription = stripe.subscription(found["id"])
    if not _first_time(session, event):
        session.rollback()
        return []
    organization = _held(session, org_id)
    mails: list[Message] = []
    if pack:
        record_pack(session, organization, found)
    elif subscription is not None:
        applied = apply_subscription(session, organization, subscription)
        mails += _news(session, organization, applied, app_url)
        if kind == "customer.subscription.trial_will_end" and organization.trial_ends_at:
            mails += _trial_mail(session, organization, app_url)
    else:
        mails += _payment_failed(session, organization, found, app_url)
    _post(session, organization.id, mails)
    session.commit()
    return mails


def _organization_of(session: Session, kind: str, found: Mapping) -> int | None:
    """The organisation an event is about: a Checkout session's by the id it was opened with,
    which must be the customer's organisation too; anything else's by its customer."""
    customer = _id_of(found.get("customer"))
    by_customer = (
        session.scalar(select(Organization.id).where(Organization.stripe_customer_id == customer))
        if customer
        else None
    )
    if kind not in CHECKOUTS:
        return by_customer
    reference = found.get("client_reference_id")
    if not (isinstance(reference, str) and reference.isdecimal()):
        return None
    org_id = int(reference)
    return org_id if by_customer in (None, org_id) else None


def _first_time(session: Session, event: Mapping) -> bool:
    """Record the event as acted on, in the transaction that acts on it; False when another
    delivery of it was first."""
    recorded = session.execute(
        text(
            "INSERT INTO billing_events (id, type) VALUES (:id, :type) "
            "ON CONFLICT (id) DO NOTHING RETURNING id"
        ),
        {"id": event["id"], "type": str(event.get("type"))[:100]},
    ).scalar()
    return recorded is not None


def _payment_failed(
    session: Session, organization: Organization, invoice: Mapping, app_url: str
) -> list[Message]:
    """A payment of the organisation's subscription failed: the grace period starts, if it has
    not, and the owners are told how to pay before it ends; once it is over, the organisation
    is on Free, and they are told that."""
    if organization.subscription_status not in BILLED or not managed(organization):
        return []
    now = datetime.now(UTC)
    organization.payment_failed_at = organization.payment_failed_at or now
    attempt = invoice.get("attempt_count")
    audit_event(
        session,
        "payment_failed",
        org_id=organization.id,
        detail={"attempt": attempt if isinstance(attempt, int) else None},
    )
    # The plan the payment was for, unless the organisation is on Free already.
    plan = organization.plan.capitalize() if organization.plan != FREE else None
    until = grace_end(organization, get_settings())
    over = now >= until
    if over and plan:
        change_plan(session, organization, FREE, because="grace_ended")
    pay = invoice.get("hosted_invoice_url")
    pay = pay if isinstance(pay, str) and pay.startswith("https://") else None
    return [
        compose(
            email,
            payment_failed_email(
                name,
                organization.name,
                plan,
                None if over else day(until, zone),
                pay,
                f"{app_url}{ORGANIZATION_PAGE}",
            ),
        )
        for email, name, zone in _owners(session, organization.id)
    ]


def _trial_mail(session: Session, organization: Organization, app_url: str) -> list[Message]:
    ends = organization.trial_ends_at
    return [
        compose(
            email,
            trial_ending_email(
                name,
                organization.name,
                organization.plan.capitalize(),
                day(ends, zone),
                organization.ends_at is not None,
                f"{app_url}{ORGANIZATION_PAGE}",
            ),
        )
        for email, name, zone in _owners(session, organization.id)
    ]


def _news(
    session: Session, organization: Organization, applied: Applied, app_url: str
) -> list[Message]:
    """The mail what a subscription did calls for, to the owners: a plan lost for want of a
    payment, a lower plan where it leaves the organisation read-only or keeps chats or audit
    rows for less time, a cancellation. An organisation whose terms are agreed gets none,
    since its subscription moves nothing."""
    if not managed(organization):
        return []
    mails = []
    if applied.lapsed:
        mails += _lapsed_mail(session, organization, applied.was_plan, app_url)
    elif applied.lowered and (applied.held or over_limits(session, organization)):
        mails += _lowered_mail(session, organization, applied, app_url)
    if applied.cancelled:
        mails += _cancelled_mail(session, organization, app_url)
    return mails


def _lapsed_mail(
    session: Session, organization: Organization, was_plan: str, app_url: str
) -> list[Message]:
    over, kept, until = _lowering(session, organization)
    return [
        compose(
            email,
            plan_lapsed_email(
                name,
                organization.name,
                was_plan.capitalize(),
                f"{app_url}{ORGANIZATION_PAGE}",
                over,
                kept,
                day(until, zone) if until else None,
            ),
        )
        for email, name, zone in _owners(session, organization.id)
    ]


def _lowered_mail(
    session: Session, organization: Organization, applied: Applied, app_url: str
) -> list[Message]:
    over, kept, until = _lowering(session, organization)
    return [
        compose(
            email,
            plan_lowered_email(
                name,
                organization.name,
                applied.was_plan.capitalize(),
                organization.plan.capitalize(),
                seats_of(organization),
                applied.status not in BILLED,
                over,
                kept,
                day(until, zone) if until else None,
                f"{app_url}{ORGANIZATION_PAGE}",
            ),
        )
        for email, name, zone in _owners(session, organization.id)
    ]


def _lowering(
    session: Session, organization: Organization
) -> tuple[str | None, str | None, datetime | None]:
    """What the organisation, moved to a lower plan, holds past it, what the plan keeps for
    less time than before, and until when that is held, as billing's mail says them."""
    over = _words(over_limits(session, organization))
    standing = retention_held(organization)
    if standing is None:
        return over, None, None
    return over, shortened(standing.kept, kept_by(entitlements_of(organization))), standing.until


def _words(excesses: list[Excess]) -> str | None:
    """What an organisation holds past a plan, as a sentence lists it: "4 projects of 1 and 30
    documents of 25"."""
    return listed([excess.words for excess in excesses]) if excesses else None


def _cancelled_mail(session: Session, organization: Organization, app_url: str) -> list[Message]:
    change = plan_change(session, organization, FREE)
    over = _words(change.over)
    kept = shortened(change.held.kept, change.kept) if change.held else None
    until = change.held.until if change.held else None
    return [
        compose(
            email,
            cancellation_email(
                name,
                organization.name,
                organization.plan.capitalize(),
                day(organization.ends_at, zone),
                over,
                kept,
                day(until, zone) if until else None,
                f"{app_url}{ORGANIZATION_PAGE}",
            ),
        )
        for email, name, zone in _owners(session, organization.id)
    ]


def _owners(session: Session, org_id: int) -> Iterator[tuple[str, str, tzinfo]]:
    """Each owner of the organisation, as the address, name and zone billing writes to."""
    for user in session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id, Membership.role == "owner")
        .order_by(User.id)
    ):
        yield user.email, user.name, zone_of(user.time_zone)


def _post(session: Session, org_id: int, mails: list[Message]) -> None:
    """Queue billing's mail in the transaction that acted, so that it is sent once with what it
    says (app/postbox.py)."""
    for mail in mails:
        post(session, mail.to, mail, org_id)


# The housekeeper's


def end_grace_periods(now: datetime | None = None) -> int:
    """Move to the Free plan every organisation whose payment has failed for longer than
    BILLING_GRACE_DAYS, each in a transaction of its own, and tell its owners; returns how many.
    Stripe goes on trying, and once the payment is made, its event puts the plan back."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    before = now - timedelta(days=settings.billing_grace_days)
    moved = 0
    while True:
        with SessionLocal() as session:
            organization = session.scalar(
                select(Organization)
                .where(
                    Organization.subscription_status == PAST_DUE,
                    Organization.payment_failed_at <= before,
                    Organization.plan.in_(SOLD_PLANS),
                    text("organizations.overrides = '{}'::jsonb"),
                )
                .order_by(Organization.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if organization is None:
                return moved
            was_plan = organization.plan
            change_plan(session, organization, FREE, because="grace_ended")
            mails = _lapsed_mail(session, organization, was_plan, settings.app_url.rstrip("/"))
            _post(session, organization.id, mails)
            session.commit()
        moved += 1
        log.info("Organisation %s moved to the Free plan: its payment failed", organization.id)


def prune_billing_events(connection: Connection) -> int:
    """Forget the events acted on longer ago than Stripe delivers one again; returns how many."""
    return connection.execute(
        text("DELETE FROM billing_events WHERE received_at < now() - make_interval(days => :days)"),
        {"days": EVENT_HISTORY.days},
    ).rowcount


def subscription_refusal(session: Session, user: User) -> str | None:
    """Why the user may not have their account deleted for a subscription's sake, or None: an
    organisation they are the only member of, which goes with the account, still has one that
    renews. Cancelled, it ends with its period, and nothing more is charged."""
    for organization in session.scalars(
        select(Organization)
        .join(Membership, Membership.org_id == Organization.id)
        .where(
            Membership.user_id == user.id,
            Organization.subscription_status.in_(BILLED),
            Organization.ends_at.is_(None),
        )
    ):
        members = session.scalar(
            select(func.count()).select_from(Membership).where(Membership.org_id == organization.id)
        )
        if members == 1:
            return (
                f"{organization.name} goes with your account, and its subscription to the "
                f"{organization.plan.capitalize()} plan still renews. Cancel it under Manage "
                "billing on the organisation page first, then delete your account."
            )
    return None
