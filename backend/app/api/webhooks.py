"""The organisation's webhooks (app/webhooks.py), which its owners and admins add, change, roll and
remove on its page beside its API keys, and whose delivery logs they read.

A webhook is made with an address and the events it is told of, by an owner or admin, on a plan
that includes the API; the organisation holds MAX_WEBHOOKS at most. Its secret is in the answer
that makes it and the one that rolls it, and in nothing after. Each change is a row of the audit
log, which names the address's server and never the address, whose path may hold the receiving
program's own token. None of these routes takes an API key: a key that leaks sends the
organisation's work nowhere new (KEYED in app/main.py).
"""

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.deps import AccountDep, SessionDep
from app.api.organizations import locked
from app.audit import audit
from app.auth import now
from app.encrypted import WEBHOOK_SECRETS, encrypt
from app.entitlements import PlanRefused, check_api
from app.models import WEBHOOK_EVENTS, User, Webhook, WebhookDelivery
from app.schemas import (
    WebhookDeliveriesOut,
    WebhookDeliveryOut,
    WebhookIn,
    WebhookMadeOut,
    WebhookOut,
    WebhookPatch,
)
from app.webhooks import MAX_WEBHOOKS, host_of, new_secret, url_problem

router = APIRouter(tags=["webhooks"])

ADMINS_ONLY = (
    "Only the owners and admins of the organisation can see and change its webhooks. Ask one of "
    "them to."
)
WEBHOOK_NOT_FOUND = "Webhook not found"
TOO_MANY = (
    f"The organisation holds {MAX_WEBHOOKS} webhooks, as many as it may. Remove one it no longer "
    "uses to add another."
)
# How many deliveries a page of the log holds, unless asked for fewer.
DELIVERIES_PAGE = 20


@router.get("/organization/webhooks", response_model=list[WebhookOut])
def list_webhooks(account: AccountDep, session: SessionDep):
    """The organisation's webhooks, the oldest first, each with its newest delivery."""
    _refuse_unless_admin(account)
    rows = session.execute(
        select(Webhook, User)
        .outerjoin(User, User.id == Webhook.created_by)
        .where(Webhook.org_id == account.organization.id)
        .order_by(Webhook.id)
    ).all()
    return [_out(session, hook, user) for hook, user in rows]


@router.post("/organization/webhooks", response_model=WebhookMadeOut, status_code=201)
def make_webhook(body: WebhookIn, account: AccountDep, request: Request, session: SessionDep):
    """Add a webhook, and answer it with its secret, shown this once."""
    _refuse_unless_admin(account)
    if problem := url_problem(body.url):
        raise HTTPException(400, problem)
    if refusal := check_api(account.organization):
        raise PlanRefused(refusal)
    # Held, so that two webhooks added at once cannot both take the last place.
    organization = locked(session, account.organization.id)
    held = session.scalar(select(func.count()).where(Webhook.org_id == organization.id))
    if held >= MAX_WEBHOOKS:
        raise HTTPException(409, TOO_MANY)
    hook = Webhook(
        org_id=organization.id,
        created_by=account.user.id,
        url=body.url,
        events=_chosen(body.events),
        # Encrypted once the row has the id it is encrypted for.
        secret="",
    )
    session.add(hook)
    session.flush()
    secret = new_secret()
    hook.secret = encrypt(WEBHOOK_SECRETS, hook.id, secret)
    audit(
        session,
        account,
        request,
        "webhook_created",
        target=("webhook", hook.id),
        detail={"host": host_of(hook.url), "events": hook.events},
    )
    session.commit()
    return WebhookMadeOut(**_out(session, hook, account.user).model_dump(), secret=secret)


@router.patch("/organization/webhooks/{webhook_id}", response_model=WebhookOut)
def change_webhook(
    webhook_id: int, body: WebhookPatch, account: AccountDep, request: Request, session: SessionDep
):
    """Change a webhook's address or events, or pause it or put it on again; a delivery waiting
    for its next try goes to the address as it is then."""
    _refuse_unless_admin(account)
    hook = _webhook(session, account, webhook_id)
    detail: dict = {"host": host_of(hook.url)}
    if body.url is not None and body.url != hook.url:
        if problem := url_problem(body.url):
            raise HTTPException(400, problem)
        detail["to_host"] = host_of(body.url)
        hook.url = body.url
    if body.events is not None and _chosen(body.events) != hook.events:
        detail |= {"from": hook.events, "to": _chosen(body.events)}
        hook.events = _chosen(body.events)
    if body.enabled is not None and body.enabled != hook.enabled:
        detail["enabled"] = body.enabled
        hook.enabled = body.enabled
    if len(detail) > 1:
        audit(
            session,
            account,
            request,
            "webhook_changed",
            target=("webhook", hook.id),
            detail=detail,
        )
    session.commit()
    return _out(session, hook, session.get(User, hook.created_by) if hook.created_by else None)


@router.post("/organization/webhooks/{webhook_id}/roll", response_model=WebhookMadeOut)
def roll_webhook(webhook_id: int, account: AccountDep, request: Request, session: SessionDep):
    """Give a webhook a new secret, the one before ending at once, every try from now on signed
    with it, and answer it with the new one, shown this once."""
    _refuse_unless_admin(account)
    hook = _webhook(session, account, webhook_id)
    if refusal := check_api(account.organization):
        raise PlanRefused(refusal)
    secret = new_secret()
    hook.secret = encrypt(WEBHOOK_SECRETS, hook.id, secret)
    hook.rolled_at = now()
    audit(
        session,
        account,
        request,
        "webhook_rolled",
        target=("webhook", hook.id),
        detail={"host": host_of(hook.url)},
    )
    session.commit()
    maker = session.get(User, hook.created_by) if hook.created_by else None
    return WebhookMadeOut(**_out(session, hook, maker).model_dump(), secret=secret)


@router.delete("/organization/webhooks/{webhook_id}", status_code=204)
def remove_webhook(webhook_id: int, account: AccountDep, request: Request, session: SessionDep):
    """Remove a webhook with its delivery log: nothing more is sent to it, a try waiting
    included."""
    _refuse_unless_admin(account)
    hook = _webhook(session, account, webhook_id)
    audit(
        session,
        account,
        request,
        "webhook_deleted",
        target=("webhook", hook.id),
        detail={"host": host_of(hook.url), "events": hook.events},
    )
    session.delete(hook)
    session.commit()


@router.get("/organization/webhooks/{webhook_id}/deliveries", response_model=WebhookDeliveriesOut)
def list_deliveries(
    webhook_id: int,
    account: AccountDep,
    session: SessionDep,
    before: int | None = None,
    limit: int = Query(DELIVERIES_PAGE, ge=1, le=100),
):
    """A page of the webhook's delivery log, the newest first, going on from `before`."""
    _refuse_unless_admin(account)
    hook = _webhook(session, account, webhook_id)
    query = select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
    if before is not None:
        query = query.where(WebhookDelivery.id < before)
    rows = session.scalars(query.order_by(WebhookDelivery.id.desc()).limit(limit + 1)).all()
    shown = rows[:limit]
    return WebhookDeliveriesOut(
        deliveries=[WebhookDeliveryOut.model_validate(row, from_attributes=True) for row in shown],
        next_before=shown[-1].id if len(rows) > limit else None,
    )


def _refuse_unless_admin(account: Account) -> None:
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)


def _webhook(session: Session, account: Account, webhook_id: int) -> Webhook:
    """The organisation's webhook with this id; another organisation's is not found."""
    hook = session.scalar(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.org_id == account.organization.id)
    )
    if hook is None:
        raise HTTPException(404, WEBHOOK_NOT_FOUND)
    return hook


def _chosen(events: list[str]) -> list[str]:
    """The events chosen, each once, in the order the app lists them."""
    return [event for event in WEBHOOK_EVENTS if event in events]


def _out(session: Session, hook: Webhook, maker: User | None) -> WebhookOut:
    last = session.scalar(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == hook.id)
        .order_by(WebhookDelivery.id.desc())
        .limit(1)
    )
    return WebhookOut(
        id=hook.id,
        url=hook.url,
        events=hook.events,
        enabled=hook.enabled,
        made_by=(maker.name or maker.email) if maker is not None else None,
        created_at=hook.created_at,
        rolled_at=hook.rolled_at,
        last_delivery=None
        if last is None
        else WebhookDeliveryOut.model_validate(last, from_attributes=True),
    )
