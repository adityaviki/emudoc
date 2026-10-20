"""Billing: an owner buys Pro, or Team by the seat, on Stripe's Checkout page and manages it on
Stripe's portal, and buys packs of pages or questions for the month, counted once paid and in
that month alone; the subscription Stripe holds sets the plan, read back from Stripe at each of
its events, which are acted on once and only with Stripe's signature; a failed payment keeps the
plan for the grace period and tells the owners, then the organisation is on Free, keeping all it
holds, until it pays; an owner reads what a lower plan or a cancellation would leave read-only and
which chats and audit rows it would keep for less time before Stripe's page takes it, and the
owners are mailed it once it is done; an organisation whose terms are agreed is left to the
operator. Stripe is `FakeStripe` (tests/conftest.py), behind the app's own client."""

import copy
import itertools
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.billing import (
    AGREED,
    BILLING_OFF,
    ENDING,
    NO_CUSTOMER,
    NO_LIMIT,
    NO_PACK,
    NO_SEATS,
    NO_SUBSCRIPTION,
    NOT_FINISHED,
    NOT_SOLD,
    OTHER_CHECKOUT,
    OWNERS_ONLY,
    PAY_FIRST,
    PAYMENT_ON_ITS_WAY,
    SAME_PLAN,
    SEATS_NEEDED,
    SUBSCRIBED,
    UNLISTED,
    end_grace_periods,
    prune_billing_events,
    subscription_refusal,
)
from app.config import get_settings
from app.db import engine
from app.entitlements import MONTHS, PLANS, check_question, check_upload, month_of
from app.maintenance import housekeeping, plan_report
from app.models import AllowancePack, BillingEvent, Organization
from app.payments import API_VERSION, UNAVAILABLE, BadSignature, form, sign, verified_event
from app.retention import RETENTION_HELD, day
from app.usage import UPLOAD, record
from tests.test_audit import actions

# Built at run time, so that nothing in the repository reads as a key.
STRIPE_KEY = "sk_" + "test_" + "suite"
SECRET = "whsec_" + "suite"
PRICES = "pro=price_pro,team=price_team,pro=price_pro_yearly"
PACKS = "pages:1000=price_pages,questions:500=price_questions"
APP = "https://emudoc.test"
_events = itertools.count(1)


@pytest.fixture
def billing(monkeypatch):
    """Billing on, as a deployment sets it: Pro and Team on sale, with a yearly Pro price the
    portal may switch to, packs of pages and questions, a 14-day trial and a 14-day grace
    period."""
    settings = get_settings()
    for name, value in (
        ("stripe_secret_key", STRIPE_KEY),
        ("stripe_webhook_secret", SECRET),
        ("stripe_prices", PRICES),
        ("stripe_packs", PACKS),
        ("stripe_trial_days", 14),
        ("stripe_tax", False),
        ("billing_grace_days", 14),
        ("app_url", APP),
    ):
        monkeypatch.setattr(settings, name, value)


@pytest.fixture
def ada(client, make_account, act_as, billing):
    """The owner of Acme, on the Free plan, signed in."""
    account = make_account("ada@acme.com")
    act_as(account)
    return account


def buy(client, plan: str = "pro", seats: int | None = None) -> str:
    """The Checkout session's id that buying `plan` sends the owner to."""
    body = {"plan": plan} | ({"seats": seats} if seats is not None else {})
    response = client.post("/api/organization/billing/checkout", json=body)
    assert response.status_code == 200, response.text
    return response.json()["url"].rsplit("/", 1)[1]


def back(client, checkout_id: str) -> dict:
    """The account once the owner is back from Checkout."""
    response = client.post(
        "/api/organization/billing/checkout/done", json={"checkout_id": checkout_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def event(kind: str, found: dict, event_id: str | None = None) -> dict:
    return {
        "id": event_id or f"evt_{next(_events)}",
        "object": "event",
        "type": kind,
        "data": {"object": {key: value for key, value in found.items() if key != "form"}},
    }


def deliver(client, sent: dict, secret: str = SECRET, timestamp: int | None = None):
    """Stripe's delivery of the event, signed with `secret`: from a client without the app's
    own header, as Stripe sends it."""
    body = json.dumps(sent).encode()
    return TestClient(client.app).post(
        "/api/billing/events",
        content=body,
        headers={"Stripe-Signature": sign(body, secret, timestamp)},
    )


def organization(session, org_id: int) -> Organization:
    session.expire_all()
    return session.get(Organization, org_id)


def billing_of(client) -> dict:
    return client.get("/api/auth/me").json()["organization"]["billing"]


def subscribed(client, stripe, ada, plan: str = "pro", seats: int | None = None, **state) -> dict:
    """Ada's organisation subscribed to `plan` through Checkout, the subscription Stripe holds."""
    checkout_id = buy(client, plan, seats)
    bought = stripe.complete(checkout_id, **state)
    back(client, checkout_id)
    return bought


def at(moment: datetime) -> int:
    return int(moment.timestamp())


def moment(value: str | None) -> datetime | None:
    """A time as the API gives it."""
    return datetime.fromisoformat(value) if value else None


# Buying


def test_an_owner_buys_a_plan_on_stripe_s_checkout_page(client, session, stripe, ada):
    checkout_id = buy(client, "pro")

    (customer,) = stripe.made("/v1/customers")
    assert customer == {
        "name": "Acme",
        "email": "ada@acme.com",
        "metadata[org_id]": str(ada.organization.id),
    }
    (sent,) = stripe.made("/v1/checkout/sessions")
    org_id = str(ada.organization.id)
    assert sent == {
        "mode": "subscription",
        "customer": stripe.checkouts[checkout_id]["customer"],
        "client_reference_id": org_id,
        "line_items[0][price]": "price_pro",
        "line_items[0][quantity]": "1",
        "success_url": f"{APP}/organization?checkout={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{APP}/organization",
        "metadata[org_id]": org_id,
        "subscription_data[metadata][org_id]": org_id,
        "subscription_data[trial_period_days]": "14",
        "billing_address_collection": "required",
        "tax_id_collection[enabled]": "true",
        "customer_update[name]": "auto",
        "customer_update[address]": "auto",
        "consent_collection[terms_of_service]": "required",
        "expires_at": sent["expires_at"],
    }
    assert 3500 <= int(sent["expires_at"]) - datetime.now(UTC).timestamp() <= 3600
    headers = stripe.requests[-1][3]
    assert headers["Stripe-Version"] == API_VERSION
    assert headers["Authorization"] == f"Bearer {STRIPE_KEY}"
    assert headers["Idempotency-Key"].startswith("checkout-")
    assert actions(session, ada.organization.id) == [
        ("checkout_started", {"plan": "pro", "seats": None})
    ]
    assert organization(session, ada.organization.id).plan == "free"

    # A second try, the first page left, is the same customer's.
    buy(client, "pro")
    assert len(stripe.made("/v1/customers")) == 1
    assert len(stripe.made("/v1/checkout/sessions")) == 2


def test_back_from_checkout_the_organisation_is_on_the_plan_it_bought(client, session, stripe, ada):
    checkout_id = buy(client, "pro")
    trial_end = at(datetime.now(UTC) + timedelta(days=14))
    bought = stripe.complete(checkout_id, status="trialing", trial_end=trial_end)

    me = back(client, checkout_id)

    assert me["organization"]["plan"] == "pro"
    shown = me["organization"]["billing"]
    assert shown["status"] == "trialing"
    assert moment(shown["trial_ends_at"]) == datetime.fromtimestamp(trial_end, UTC)
    assert shown["customer"] is True and shown["trial_days"] is None
    held = organization(session, ada.organization.id)
    assert held.stripe_subscription_id == bought["id"]
    assert actions(session, ada.organization.id)[1:] == [
        ("subscription_changed", {"from": None, "to": "trialing"}),
        ("plan_changed", {"from": "free", "to": "pro", "because": "subscription"}),
    ]
    # Stripe's event says the same a moment later, and changes nothing more.
    response = deliver(client, event("checkout.session.completed", stripe.checkouts[checkout_id]))
    assert response.status_code == 200, response.text
    assert len(actions(session, ada.organization.id)) == 3

    refused = client.post("/api/organization/billing/checkout", json={"plan": "team", "seats": 2})
    assert (refused.status_code, refused.json()["detail"]) == (409, SUBSCRIBED)


def test_the_checkout_event_alone_puts_the_organisation_on_its_plan(client, session, stripe, ada):
    checkout_id = buy(client, "team", 4)
    stripe.complete(checkout_id)

    response = deliver(client, event("checkout.session.completed", stripe.checkouts[checkout_id]))

    assert response.status_code == 200
    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats, held.subscription_status) == ("team", 4, "active")
    assert held.renews_at == datetime.fromtimestamp(1_800_000_000, UTC)


def test_team_is_bought_by_the_seat_and_pro_without(client, stripe, ada):
    for body, sentence in (
        ({"plan": "team"}, SEATS_NEEDED),
        (
            {"plan": "pro", "seats": 3},
            "Only the Team plan is sold by the seat; buy the Pro plan without seats.",
        ),
    ):
        response = client.post("/api/organization/billing/checkout", json=body)
        assert (response.status_code, response.json()["detail"]) == (400, sentence)
    assert (
        client.post("/api/organization/billing/checkout", json={"plan": "enterprise"}).status_code
        == 422
    )

    buy(client, "team", 7)
    assert stripe.made("/v1/checkout/sessions")[-1]["line_items[0][quantity]"] == "7"
    assert stripe.made("/v1/checkout/sessions")[-1]["line_items[0][price]"] == "price_team"


def test_a_plan_not_on_sale_is_not_sold(client, ada, monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_prices", "pro=price_pro")
    response = client.post("/api/organization/billing/checkout", json={"plan": "team", "seats": 2})
    assert (response.status_code, response.json()["detail"]) == (400, NOT_SOLD)
    assert billing_of(client)["sold"] == ["pro"]


def test_only_an_owner_buys_or_manages_billing(client, session, make_account, act_as, billing):
    owner = make_account("ada@acme.com")
    admin = make_account("bob@acme.com", owner.organization, "admin")
    act_as(admin)
    for path, body in (
        ("/api/organization/billing/checkout", {"plan": "pro"}),
        ("/api/organization/billing/portal", None),
    ):
        response = client.post(path, json=body)
        assert (response.status_code, response.json()["detail"]) == (403, OWNERS_ONLY)


def test_without_stripe_nothing_is_sold(client, make_account, act_as):
    act_as(make_account("ada@acme.com"))
    assert billing_of(client)["sold"] == []
    for path, body in (
        ("/api/organization/billing/checkout", {"plan": "pro"}),
        ("/api/organization/billing/portal", None),
        ("/api/organization/billing/checkout/done", {"checkout_id": "cs_1"}),
    ):
        response = client.post(path, json=body)
        assert (response.status_code, response.json()["detail"]) == (404, BILLING_OFF)
    assert (
        deliver(client, event("invoice.payment_failed", {"customer": "cus_1"})).status_code == 404
    )


def test_an_organisation_whose_terms_are_agreed_buys_nothing_here(client, session, stripe, ada):
    held = organization(session, ada.organization.id)
    held.overrides = {"projects": 500}
    session.commit()
    response = client.post("/api/organization/billing/checkout", json={"plan": "pro"})
    assert (response.status_code, response.json()["detail"]) == (409, AGREED)
    assert billing_of(client)["managed"] is False

    # Nor does a subscription it pays for change its plan.
    held.stripe_customer_id = "cus_agreed"
    session.commit()
    subscription = stripe.subscribe("cus_agreed", "price_team", 9)
    deliver(client, event("customer.subscription.created", subscription))
    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats, held.overrides) == ("free", None, {"projects": 500})
    assert held.subscription_status == "active"


def test_a_checkout_of_another_organisation_changes_nothing(client, session, stripe, ada):
    checkout_id = buy(client, "pro")
    stripe.complete(checkout_id)
    stripe.checkouts[checkout_id]["client_reference_id"] = str(ada.organization.id + 1)
    response = client.post(
        "/api/organization/billing/checkout/done", json={"checkout_id": checkout_id}
    )
    assert (response.status_code, response.json()["detail"]) == (404, OTHER_CHECKOUT)
    assert organization(session, ada.organization.id).plan == "free"


def test_a_checkout_not_paid_for_changes_nothing(client, session, ada):
    checkout_id = buy(client, "pro")
    response = client.post(
        "/api/organization/billing/checkout/done", json={"checkout_id": checkout_id}
    )
    assert (response.status_code, response.json()["detail"]) == (409, NOT_FINISHED)
    # What is not a Checkout session's id is not asked about.
    refused = client.post(
        "/api/organization/billing/checkout/done", json={"checkout_id": "cs_1/../x"}
    )
    assert refused.status_code == 422
    assert organization(session, ada.organization.id).plan == "free"


def test_only_the_first_subscription_starts_with_a_trial(client, session, stripe, ada):
    assert billing_of(client)["trial_days"] == 14
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["status"] = "canceled"
    deliver(client, event("customer.subscription.deleted", bought))
    assert organization(session, ada.organization.id).plan == "free"

    assert billing_of(client)["trial_days"] is None
    buy(client, "pro")
    assert "subscription_data[trial_period_days]" not in stripe.made("/v1/checkout/sessions")[-1]


def test_tax_is_worked_out_where_stripe_tax_is_on(client, stripe, ada, monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_tax", True)
    buy(client, "pro")
    assert stripe.made("/v1/checkout/sessions")[-1]["automatic_tax[enabled]"] == "true"


def test_the_portal_is_an_owner_s_once_the_organisation_is_a_customer(client, stripe, ada):
    response = client.post("/api/organization/billing/portal")
    assert (response.status_code, response.json()["detail"]) == (409, NO_CUSTOMER)

    subscribed(client, stripe, ada)
    response = client.post("/api/organization/billing/portal")
    assert response.json() == {"url": "https://billing.stripe.test/session"}
    customer = stripe.subscriptions[next(iter(stripe.subscriptions))]["customer"]
    assert stripe.made("/v1/billing_portal/sessions")[-1] == {
        "customer": customer,
        "return_url": f"{APP}/organization?billing=back",
    }


def test_stripe_out_of_reach_is_a_503_that_quotes_nothing_of_stripe_s(
    client, session, stripe, ada, caplog, monkeypatch
):
    stripe.status = 500
    with caplog.at_level(logging.WARNING, "app.main"):
        response = client.post("/api/organization/billing/checkout", json={"plan": "pro"})
    assert response.status_code == 503
    # Development shows the operator's sentence; a deployment's members are told it is down.
    said = "Stripe refused to create a customer: 500 (api_error)."
    assert response.json()["detail"] == said
    assert said in caplog.text and "Something broke" not in caplog.text
    monkeypatch.setattr(get_settings(), "environment", "production")
    response = client.post("/api/organization/billing/checkout", json={"plan": "pro"})
    assert (response.status_code, response.json()["detail"]) == (503, UNAVAILABLE)
    assert actions(session, ada.organization.id) == []


# Packs


def buy_pack(client, unit: str = "pages", quantity: int = 1000) -> str:
    response = client.post(
        "/api/organization/billing/packs", json={"unit": unit, "quantity": quantity}
    )
    assert response.status_code == 200, response.text
    return response.json()["url"].rsplit("/", 1)[1]


def test_an_owner_buys_a_pack_of_pages_for_the_month(client, session, stripe, ada):
    org_id = ada.organization.id
    assert billing_of(client)["packs"] == [
        {"unit": "pages", "quantity": 1000},
        {"unit": "questions", "quantity": 500},
    ]
    used = {"org_id": org_id, "project_id": None, "user_id": None, "kind": UPLOAD}
    record(session, **used, measures={"pages": 200})
    session.commit()
    month = MONTHS[month_of()[0].month - 1]
    refusal = check_upload(session, organization(session, org_id))
    assert refusal.limit == "pages_per_month"
    assert "The Free plan indexes 200 pages a month, and this" in refusal.sentence

    checkout_id = buy_pack(client)
    sent = stripe.made("/v1/checkout/sessions")[-1]
    assert sent["mode"] == "payment"
    assert (sent["line_items[0][price]"], sent["line_items[0][quantity]"]) == ("price_pages", "1")
    assert (sent["metadata[pack_unit]"], sent["metadata[pack_quantity]"]) == ("pages", "1000")
    assert sent["invoice_creation[enabled]"] == "true"
    assert sent["tax_id_collection[enabled]"] == "true"
    assert not any(key.startswith("subscription_data") for key in sent)
    assert actions(session, org_id) == [("checkout_started", {"pack": "pages", "quantity": 1000})]

    stripe.pay(checkout_id)
    me = back(client, checkout_id)

    assert me["organization"]["allowance"]["pages_bought"] == 1000
    assert me["organization"]["plan"] == "free"
    held = organization(session, org_id)
    assert check_upload(session, held) is None
    # Stripe's event about the same payment counts nothing twice.
    deliver(client, event("checkout.session.completed", stripe.checkouts[checkout_id]))
    assert session.query(AllowancePack).count() == 1
    assert actions(session, org_id)[-1] == (
        "pack_bought",
        {"unit": "pages", "quantity": 1000, "month": f"{month_of()[0]:%Y-%m}"},
    )
    record(session, **used, measures={"pages": 1000})
    session.commit()
    assert check_upload(session, held).sentence.startswith(
        f"The Free plan indexes 200 pages a month and 1,000 more bought for {month}, and this "
        f"organisation has indexed 1,200 in {month}."
    )


def test_a_pack_paid_by_bank_debit_counts_once_the_payment_arrives(client, session, stripe, ada):
    checkout_id = buy_pack(client, "questions", 500)
    stripe.pay(checkout_id, "unpaid")
    response = client.post(
        "/api/organization/billing/checkout/done", json={"checkout_id": checkout_id}
    )
    assert (response.status_code, response.json()["detail"]) == (409, PAYMENT_ON_ITS_WAY)
    deliver(client, event("checkout.session.completed", stripe.checkouts[checkout_id]))
    assert session.query(AllowancePack).count() == 0

    stripe.checkouts[checkout_id]["payment_status"] = "paid"
    sent = event("checkout.session.async_payment_succeeded", stripe.checkouts[checkout_id])
    assert deliver(client, sent).status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["organization"]["allowance"]["questions_bought"] == 500


def test_a_pack_is_sold_only_for_a_limit_the_plan_has(client, session, ada, monkeypatch):
    monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], questions_per_month=None))
    assert billing_of(client)["packs"] == [{"unit": "pages", "quantity": 1000}]
    response = client.post(
        "/api/organization/billing/packs", json={"unit": "questions", "quantity": 500}
    )
    assert (response.status_code, response.json()["detail"]) == (
        409,
        NO_LIMIT.format(plan="Free", unit="questions"),
    )
    response = client.post("/api/organization/billing/packs", json={"unit": "pages", "quantity": 5})
    assert (response.status_code, response.json()["detail"]) == (400, NO_PACK)

    held = organization(session, ada.organization.id)
    held.plan = "enterprise"
    session.commit()
    assert billing_of(client)["packs"] == []
    response = client.post(
        "/api/organization/billing/packs", json={"unit": "pages", "quantity": 1000}
    )
    assert (response.status_code, response.json()["detail"]) == (409, AGREED)


def test_a_pack_counts_in_the_month_it_was_paid_in_alone(client, session, ada, monkeypatch):
    monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], questions_per_month=0))
    org_id = ada.organization.id
    start = month_of()[0]
    last_month = month_of(start - timedelta(days=1))[0]
    session.add(
        AllowancePack(
            org_id=org_id, unit="questions", quantity=5, month=last_month, checkout_id="cs_old"
        )
    )
    session.commit()
    held = organization(session, org_id)
    assert check_question(session, held) is not None
    session.add(
        AllowancePack(
            org_id=org_id, unit="questions", quantity=5, month=start, checkout_id="cs_new"
        )
    )
    session.commit()
    assert check_question(session, held) is None
    assert plan_report(session, "acme")[-1] == (
        f"It bought 5 questions in packs for {MONTHS[start.month - 1]}, on top of the month's "
        "allowance."
    )


# Stripe's events


def test_an_event_without_stripe_s_signature_is_refused(client, session, stripe, ada):
    sent = event("invoice.payment_failed", {"customer": "cus_x"})
    assert deliver(client, sent, secret="whsec_other").status_code == 400
    old = at(datetime.now(UTC) - timedelta(minutes=10))
    assert deliver(client, sent, timestamp=old).status_code == 400
    unsigned = TestClient(client.app).post("/api/billing/events", content=json.dumps(sent))
    assert unsigned.status_code == 400
    large = TestClient(client.app).post("/api/billing/events", content=b"x" * 1_000_001)
    assert large.status_code == 413
    assert session.query(BillingEvent).count() == 0


def test_an_event_of_another_kind_or_customer_is_answered_and_ignored(client, session, stripe, ada):
    assert deliver(client, event("charge.refunded", {"customer": "cus_x"})).status_code == 200
    assert (
        deliver(client, event("invoice.payment_failed", {"customer": "cus_x"})).status_code == 200
    )
    assert session.query(BillingEvent).count() == 0


def test_the_subscription_is_read_as_stripe_holds_it_not_as_the_event_says(
    client, session, stripe, ada
):
    bought = subscribed(client, stripe, ada, "team", 3)
    stale = copy.deepcopy(bought)
    stripe.subscriptions[bought["id"]]["items"]["data"][0]["quantity"] = 8

    # A late event of the subscription as it was says 3 seats; Stripe holds 8.
    deliver(client, event("customer.subscription.updated", stale))

    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats) == ("team", 8)
    assert actions(session, ada.organization.id)[-1] == (
        "seats_changed",
        {"from": 3, "to": 8, "because": "subscription"},
    )


def test_the_portal_s_other_prices_are_their_plans(client, session, stripe, ada):
    bought = subscribed(client, stripe, ada, "team", 3)
    item = stripe.subscriptions[bought["id"]]["items"]["data"][0]
    item["price"]["id"], item["quantity"] = "price_pro_yearly", 1
    deliver(client, event("customer.subscription.updated", bought))
    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats) == ("pro", None)


def test_a_price_not_listed_leaves_the_plan_and_says_so(client, session, stripe, ada, caplog):
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["items"]["data"][0]["price"]["id"] = "price_unknown"
    with caplog.at_level(logging.WARNING, "app.billing"):
        deliver(client, event("customer.subscription.updated", bought))
    assert organization(session, ada.organization.id).plan == "pro"
    assert "STRIPE_PRICES does not list" in caplog.text


def test_an_event_is_acted_on_once(client, session, stripe, mailer, ada):
    bought = subscribed(client, stripe, ada)
    invoice = {"customer": bought["customer"], "attempt_count": 1}
    sent = event("invoice.payment_failed", invoice)
    for _ in range(3):
        assert deliver(client, sent).status_code == 200
    assert len(mailer.sent) == 1
    assert [action for action, _ in actions(session, ada.organization.id)].count(
        "payment_failed"
    ) == 1


def test_stripe_out_of_reach_leaves_the_event_for_its_next_delivery(client, session, stripe, ada):
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["status"] = "canceled"
    sent = event("customer.subscription.deleted", bought)
    stripe.status = 503
    assert deliver(client, sent).status_code == 503
    assert organization(session, ada.organization.id).plan == "pro"
    assert session.query(BillingEvent).count() == 0

    stripe.status = 200
    assert deliver(client, sent).status_code == 200
    assert organization(session, ada.organization.id).plan == "free"


def test_a_late_event_of_an_earlier_subscription_changes_nothing(client, session, stripe, ada):
    first = subscribed(client, stripe, ada)
    stripe.subscriptions[first["id"]]["status"] = "canceled"
    deliver(client, event("customer.subscription.deleted", first))
    second = subscribed(client, stripe, ada, "team", 2)

    deliver(client, event("customer.subscription.updated", first))

    held = organization(session, ada.organization.id)
    assert (held.plan, held.stripe_subscription_id) == ("team", second["id"])


def test_a_cancelled_subscription_runs_to_its_end_then_the_plan_is_free(
    client, session, stripe, mailer, ada
):
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["cancel_at_period_end"] = True
    deliver(client, event("customer.subscription.updated", bought))
    shown = billing_of(client)
    assert moment(shown["ends_at"]) == datetime.fromtimestamp(1_800_000_000, UTC)
    assert shown["renews_at"] is None
    assert organization(session, ada.organization.id).plan == "pro"

    stripe.subscriptions[bought["id"]]["status"] = "canceled"
    deliver(client, event("customer.subscription.deleted", bought))

    held = organization(session, ada.organization.id)
    assert (held.plan, held.subscription_status, held.ends_at) == ("free", "canceled", None)
    assert actions(session, ada.organization.id)[-1] == (
        "plan_changed",
        {"from": "pro", "to": "free", "because": "subscription_ended"},
    )
    # The owners are told of the cancellation, and once it ends, of what the Free plan means.
    assert [subject for _, subject, _ in mailer.sent] == [
        "Your Emudoc subscription was cancelled",
        "Your organisation's plan changed",
    ]
    body = mailer.sent[-1][2]
    assert "The subscription to the Pro plan of Acme on Emudoc has ended" in body
    assert (
        "The Free plan keeps chats 30 days after their last question and audit log entries 90 "
        f"days. Those older are kept as the Pro plan kept them until "
        f"{day(held.retention_held_until)}, and deleted then." in body
    )


def test_a_failed_payment_keeps_the_plan_for_the_grace_period_then_the_plan_is_free(
    client, session, stripe, mailer, make_account, ada
):
    make_account("cy@acme.com", ada.organization, "owner")
    make_account("dan@acme.com", ada.organization, "member")
    bought = subscribed(client, stripe, ada, "team", 3)
    invoice = {
        "customer": bought["customer"],
        "attempt_count": 1,
        "hosted_invoice_url": "https://invoice.stripe.test/i/1",
    }

    deliver(client, event("invoice.payment_failed", invoice))
    stripe.subscriptions[bought["id"]]["status"] = "past_due"
    deliver(client, event("customer.subscription.updated", bought))

    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats, held.subscription_status) == ("team", 3, "past_due")
    until = held.payment_failed_at + timedelta(days=14)
    assert moment(billing_of(client)["grace_ends_at"]) == until
    # Both owners are told, and the member is not.
    assert [to for to, _, _ in mailer.sent] == ["ada@acme.com", "cy@acme.com"]
    to, subject, body = mailer.sent[0]
    assert subject == "A payment for your Emudoc plan failed"
    assert f"keeps the Team plan until {day(until)}" in body
    assert "https://invoice.stripe.test/i/1" in body and f"{APP}/organization" in body
    assert ("payment_failed", {"attempt": 1}) in actions(session, ada.organization.id)

    # Within the grace period the housekeeper leaves it; past it, the plan is Free.
    assert end_grace_periods() == 0
    assert end_grace_periods(now=until + timedelta(minutes=1)) == 1
    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats) == ("free", None)
    assert actions(session, ada.organization.id)[-2:] == [
        ("plan_changed", {"from": "team", "to": "free", "because": "grace_ended"}),
        ("seats_changed", {"from": 3, "to": None, "because": "grace_ended"}),
    ]
    assert [subject for _, subject, _ in mailer.sent[-2:]] == [
        "Your organisation moved to the Free plan"
    ] * 2
    # Free keeps chats and audit rows for less time than Team: those older stay for a while.
    assert (
        "Those older are kept as the Team plan kept them until "
        f"{day(held.retention_held_until)}, and deleted then." in mailer.sent[-1][2]
    )
    assert end_grace_periods(now=until + timedelta(days=1)) == 0

    # Paid at last, the subscription is active again, and so is its plan.
    stripe.subscriptions[bought["id"]]["status"] = "active"
    deliver(client, event("customer.subscription.updated", bought))
    held = organization(session, ada.organization.id)
    assert (held.plan, held.seats, held.payment_failed_at) == ("team", 3, None)


def test_a_payment_failing_past_the_grace_period_says_the_plan_is_free(
    client, session, stripe, mailer, ada, monkeypatch
):
    monkeypatch.setattr(get_settings(), "billing_grace_days", 0)
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["status"] = "past_due"
    deliver(client, event("customer.subscription.updated", bought))
    assert organization(session, ada.organization.id).plan == "free"
    # Moved by Stripe's word that a payment failed: the owner is told.
    assert mailer.sent[-1][1] == "Your organisation moved to the Free plan"

    deliver(client, event("invoice.payment_failed", {"customer": bought["customer"]}))
    assert "is on the Free plan meanwhile" in mailer.sent[-1][2]


def test_an_unpaid_subscription_moves_the_organisation_to_free(client, session, stripe, ada):
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["status"] = "past_due"
    deliver(client, event("customer.subscription.updated", bought))
    stripe.subscriptions[bought["id"]]["status"] = "unpaid"
    deliver(client, event("customer.subscription.updated", bought))
    held = organization(session, ada.organization.id)
    assert (held.plan, held.subscription_status) == ("free", "unpaid")


def test_the_owners_are_told_before_a_trial_ends(client, session, stripe, mailer, ada):
    ends = datetime.now(UTC) + timedelta(days=3)
    bought = subscribed(client, stripe, ada, status="trialing", trial_end=at(ends))
    deliver(client, event("customer.subscription.trial_will_end", bought))
    to, subject, body = mailer.sent[-1]
    assert (to, subject) == ("ada@acme.com", "Your Emudoc trial ends soon")
    assert f"ends on {day(ends)}" in body and "first payment is taken" in body


def test_a_renewing_subscription_holds_its_only_member_s_account(client, session, stripe, ada):
    bought = subscribed(client, stripe, ada)
    assert "Cancel it under Manage billing" in subscription_refusal(session, ada.user)
    stripe.subscriptions[bought["id"]]["cancel_at_period_end"] = True
    deliver(client, event("customer.subscription.updated", bought))
    session.expire_all()
    assert subscription_refusal(session, ada.user) is None


def test_the_housekeeper_forgets_events_acted_on_a_month_ago(
    client, session, services, stripe, ada
):
    bought = subscribed(client, stripe, ada)
    deliver(client, event("customer.subscription.updated", bought, "evt_old"))
    deliver(client, event("customer.subscription.updated", bought, "evt_new"))
    session.execute(
        text(
            "UPDATE billing_events SET received_at = now() - interval '31 days' "
            "WHERE id = 'evt_old'"
        )
    )
    session.commit()
    housekeeping(services.storage)
    with engine.begin() as connection:
        assert prune_billing_events(connection) == 0
    session.expire_all()
    assert [row.id for row in session.query(BillingEvent)] == ["evt_new"]


# Lowering and cancelling


def change_of(client, plan: str, seats: int | None = None):
    params = {"plan": plan} | ({"seats": seats} if seats is not None else {})
    return client.get("/api/organization/billing/change", params=params)


def portal(client, plan: str | None = None, seats: int | None = None):
    body = {"plan": plan, "seats": seats} if plan else None
    return client.post("/api/organization/billing/portal", json=body)


def portal_done(client) -> dict:
    response = client.post("/api/organization/billing/portal/done")
    assert response.status_code == 200, response.text
    return response.json()


def test_an_owner_reads_what_cancelling_would_do_then_cancels_on_stripe_s_page(
    client, session, stripe, make_account, ada
):
    make_account("cy@acme.com", ada.organization, "member")
    bought = subscribed(client, stripe, ada, "team", 3)
    ends = datetime.fromtimestamp(1_800_000_000, UTC)

    response = change_of(client, "free")
    assert response.status_code == 200, response.text
    found = response.json()
    assert moment(found["at"]) == ends
    assert found["allowance"]["members"] == 1 and found["allowance"]["projects"] == 1
    assert found["allowance"]["over"] == [{"limit": "members", "words": "2 members of 1"}]
    assert found["retention"] == {"chat_days": 30, "audit_days": 90}
    # What Team keeps longer stays as it was for a while after the end.
    held = found["held"]
    assert (held["chat_days"], held["audit_days"]) == (None, 365)
    assert moment(held["until"]) == ends + RETENTION_HELD
    assert found["held_days"] == RETENTION_HELD.days

    assert portal(client, "free").json() == {"url": "https://billing.stripe.test/session"}
    back_here = f"{APP}/organization?billing=back"
    assert stripe.made("/v1/billing_portal/sessions")[-1] == {
        "customer": bought["customer"],
        "return_url": back_here,
        "flow_data[type]": "subscription_cancel",
        "flow_data[subscription_cancel][subscription]": bought["id"],
        "flow_data[after_completion][type]": "redirect",
        "flow_data[after_completion][redirect][return_url]": back_here,
    }


def test_back_from_the_portal_the_cancellation_shows_and_is_mailed_once(
    client, session, stripe, mailer, make_account, ada
):
    make_account("cy@acme.com", ada.organization, "owner")
    bought = subscribed(client, stripe, ada)
    stripe.subscriptions[bought["id"]]["cancel_at_period_end"] = True

    me = portal_done(client)
    ends = datetime.fromtimestamp(1_800_000_000, UTC)
    assert moment(me["organization"]["billing"]["ends_at"]) == ends
    assert me["organization"]["plan"] == "pro"
    assert actions(session, ada.organization.id)[-1] == (
        "subscription_cancelled",
        {"ends_at": ends.isoformat()},
    )
    assert [(to, subject) for to, subject, _ in mailer.sent] == [
        ("ada@acme.com", "Your Emudoc subscription was cancelled"),
        ("cy@acme.com", "Your Emudoc subscription was cancelled"),
    ]
    body = mailer.sent[0][2]
    assert f"It runs until {day(ends)}, and then the organisation moves to the Free plan" in body
    assert (
        "The Free plan keeps chats 30 days after their last question and audit log entries 90 "
        "days. Those older will be kept as the Pro plan keeps them until "
        f"{day(ends + RETENTION_HELD)}, and deleted then." in body
    )
    # Stripe's event says the same a moment later, and nobody is told twice.
    deliver(client, event("customer.subscription.updated", bought))
    assert len(mailer.sent) == 2

    # Renewed before it ends, it goes on as before.
    stripe.subscriptions[bought["id"]]["cancel_at_period_end"] = False
    deliver(client, event("customer.subscription.updated", bought))
    held = organization(session, ada.organization.id)
    assert (held.ends_at, held.renews_at) == (None, ends)
    assert actions(session, ada.organization.id)[-1] == ("cancellation_withdrawn", {})
    assert len(mailer.sent) == 2


def test_an_owner_moves_to_a_lower_plan_through_stripe_s_confirmation(
    client, session, stripe, mailer, make_account, ada
):
    make_account("cy@acme.com", ada.organization, "member")
    bought = subscribed(client, stripe, ada, "team", 3)

    # Fewer seats that still hold every member change nothing else.
    fewer = change_of(client, "team", 2).json()
    assert (fewer["allowance"]["members"], fewer["allowance"]["over"], fewer["held"]) == (
        2,
        [],
        None,
    )
    found = change_of(client, "pro").json()
    assert found["at"] is None
    assert found["allowance"]["over"] == [{"limit": "members", "words": "2 members of 1"}]
    assert found["retention"] == {"chat_days": 365, "audit_days": 365}
    held = found["held"]
    assert (held["chat_days"], held["audit_days"], held["until"]) == (None, 365, None)

    assert portal(client, "pro").status_code == 200
    (item,) = bought["items"]["data"]
    sent = stripe.made("/v1/billing_portal/sessions")[-1]
    assert {key: value for key, value in sent.items() if "update_confirm" in key} == {
        "flow_data[subscription_update_confirm][subscription]": bought["id"],
        "flow_data[subscription_update_confirm][items][0][id]": item["id"],
        "flow_data[subscription_update_confirm][items][0][price]": "price_pro",
        "flow_data[subscription_update_confirm][items][0][quantity]": "1",
    }
    assert sent["flow_data[type]"] == "subscription_update_confirm"

    # Confirmed on Stripe's page; back here, the organisation is on Pro.
    item["price"] = {"id": "price_pro", "object": "price"}
    item["quantity"] = 1
    me = portal_done(client)["organization"]
    assert (me["plan"], me["seats"]) == ("pro", None)
    assert me["allowance"]["over"] == [{"limit": "members", "words": "2 members of 1"}]
    assert me["chat_retention_days"] == 365
    kept = me["retention_held"]
    assert (kept["chat_days"], kept["audit_days"]) == (None, 365)
    assert moment(kept["until"]) > datetime.now(UTC) + RETENTION_HELD - timedelta(minutes=1)
    ((to, subject, body),) = mailer.sent
    assert (to, subject) == ("ada@acme.com", "Your organisation's plan changed")
    assert "was changed to the Pro plan, keeping everything the organisation holds" in body
    assert "It holds more than the Pro plan allows (2 members of 1)" in body
    assert "The Pro plan keeps chats 365 days after their last question. Those older" in body
    # The event a moment later finds nothing new.
    deliver(client, event("customer.subscription.updated", bought))
    assert len(mailer.sent) == 1
    # The operator reads the hold too.
    assert any(
        line.startswith("Until ")
        and "it keeps chats for good and audit log entries 365 days, as before its move" in line
        for line in plan_report(session, "acme")
    )


def test_only_an_owner_of_a_paid_subscription_moves_it_here(
    client, session, stripe, make_account, act_as, billing, monkeypatch
):
    ada = make_account("ada@acme.com")
    act_as(ada)
    for plan in ("free", "pro"):
        response = change_of(client, plan)
        assert (response.status_code, response.json()["detail"]) == (409, NO_SUBSCRIPTION)
    bought = subscribed(client, stripe, ada)

    for plan, seats, status, sentence in (
        ("pro", None, 400, SAME_PLAN),
        ("free", 2, 400, NO_SEATS.format(plan="Free")),
        ("team", None, 400, SEATS_NEEDED),
        ("enterprise", None, 422, None),
    ):
        for response in (change_of(client, plan, seats), portal(client, plan, seats)):
            assert response.status_code == status, (plan, response.text)
            if sentence:
                assert response.json()["detail"] == sentence

    act_as(make_account("bo@acme.com", ada.organization, "admin"))
    for response in (change_of(client, "free"), portal(client, "free")):
        assert (response.status_code, response.json()["detail"]) == (403, OWNERS_ONLY)
    act_as(ada)

    # A payment outstanding is paid first, and a cancelled subscription renewed first.
    stripe.subscriptions[bought["id"]]["status"] = "past_due"
    deliver(client, event("customer.subscription.updated", bought))
    assert change_of(client, "free").json()["detail"] == PAY_FIRST
    stripe.subscriptions[bought["id"]].update(status="active", cancel_at_period_end=True)
    deliver(client, event("customer.subscription.updated", bought))
    ends = day(datetime.fromtimestamp(1_800_000_000, UTC))
    assert portal(client, "team", 2).json()["detail"] == ENDING.format(day=ends)
    stripe.subscriptions[bought["id"]]["cancel_at_period_end"] = False
    deliver(client, event("customer.subscription.updated", bought))

    # A subscription at a price not sold here is changed on the portal's own page.
    stripe.subscriptions[bought["id"]]["items"]["data"][0]["price"]["id"] = "price_old"
    response = portal(client, "team", 2)
    assert (response.status_code, response.json()["detail"]) == (409, UNLISTED)

    # Agreed terms are the operator's; without billing there is nothing to read.
    session.execute(
        text("UPDATE organizations SET overrides = '{\"projects\": 5}' WHERE id = :id"),
        {"id": ada.organization.id},
    )
    session.commit()
    assert change_of(client, "free").json()["detail"] == AGREED
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "")
    assert change_of(client, "free").json()["detail"] == BILLING_OFF


def test_the_plan_command_says_a_subscription_sets_the_plan(client, session, stripe, ada):
    subscribed(client, stripe, ada)
    lines = plan_report(session, "acme")
    assert lines[-1].startswith("It pays for a subscription at Stripe, sub_")
    assert "a change made here lasts until Stripe's next event about it" in lines[-1]


# The pieces


def test_parameters_are_sent_in_stripe_s_form():
    assert form(
        {"a": 1, "b": {"c": True, "d": [{"e": "x"}, {"e": None}]}, "f": None, "g": False}
    ) == [("a", "1"), ("b[c]", "true"), ("b[d][0][e]", "x"), ("g", "false")]


def test_a_signature_is_checked_over_the_body_as_it_came():
    body = b'{"id": "evt_1", "type": "x"}'
    assert verified_event(body, sign(body, SECRET), SECRET)["id"] == "evt_1"
    for header in (
        None,
        "",
        "t=1",
        "v1=abc",
        "t=x,v1=abc",
        sign(body + b" ", SECRET),
        sign(body, SECRET + "x"),
    ):
        with pytest.raises(BadSignature):
            verified_event(body, header, SECRET)
    # A second signature, as Stripe sends while a secret is rolled, is enough when one holds.
    header = sign(body, SECRET) + ",v1=" + "0" * 64
    assert verified_event(body, header, SECRET)["id"] == "evt_1"
    with pytest.raises(BadSignature):
        verified_event(b"[]", sign(b"[]", SECRET), SECRET)
