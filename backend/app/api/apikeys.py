"""The organisation's API keys (app/apikeys.py), which its owners and admins see, make, roll and
revoke on its page.

A key is made with a name, a scope and the days it runs, by an owner or admin, and acts as them;
the organisation's plan must include the API, and it holds MAX_KEYS at most. The key itself is
in the answer that makes it and the one that rolls it, and in nothing after. Only the member a
key acts as rolls it, since the new secret acts as them; any owner or admin revokes one, which
takes nothing from anyone but the program that held it. Each is a row of the audit log. None of
these routes takes a key: a key that leaks makes no other (KEYED in app/main.py).
"""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.deps import AccountDep, SessionDep
from app.api.organizations import locked
from app.apikeys import MAX_KEYS, NAME_IT, ROLL_YOUR_OWN, TOO_MANY, expired, issue, runs_out
from app.audit import audit
from app.auth import now
from app.entitlements import PlanRefused, check_api
from app.models import ApiKey, User
from app.schemas import ApiKeyIn, ApiKeyMadeOut, ApiKeyOut

router = APIRouter(tags=["api keys"])

ADMINS_ONLY = (
    "Only the owners and admins of the organisation can see, make and revoke its API keys. Ask "
    "one of them to."
)
KEY_NOT_FOUND = "API key not found"


@router.get("/organization/keys", response_model=list[ApiKeyOut])
def list_keys(account: AccountDep, session: SessionDep):
    """The organisation's keys, the newest first, out of date ones too until they are pruned."""
    _refuse_unless_admin(account)
    rows = session.execute(
        select(ApiKey, User)
        .join(User, User.id == ApiKey.user_id)
        .where(ApiKey.org_id == account.organization.id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    ).all()
    return [_out(key, user) for key, user in rows]


@router.post("/organization/keys", response_model=ApiKeyMadeOut, status_code=201)
def make_key(body: ApiKeyIn, account: AccountDep, request: Request, session: SessionDep):
    """Make a key that acts as the member asking, and answer it with the key, shown this once."""
    _refuse_unless_admin(account)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, NAME_IT)
    if refusal := check_api(account.organization):
        raise PlanRefused(refusal)
    # Held, so that two keys made at once cannot both take the last place.
    organization = locked(session, account.organization.id)
    held = session.scalar(select(func.count()).where(ApiKey.org_id == organization.id))
    if held >= MAX_KEYS:
        raise HTTPException(409, TOO_MANY)
    key = ApiKey(
        org_id=organization.id,
        user_id=account.user.id,
        name=name,
        scope=body.scope,
        expires_at=runs_out(body.days),
    )
    secret = issue(key)
    session.add(key)
    session.flush()
    audit(
        session,
        account,
        request,
        "api_key_created",
        target=("api_key", key.id),
        detail={"name": key.name, "scope": key.scope, "days": body.days},
    )
    session.commit()
    return ApiKeyMadeOut(**_out(key, account.user).model_dump(), key=secret)


@router.post("/organization/keys/{key_id}/roll", response_model=ApiKeyMadeOut)
def roll_key(key_id: int, account: AccountDep, request: Request, session: SessionDep):
    """Give one of the member's own keys a new secret, ending the one before at once, and
    answer it with the new one, shown this once."""
    _refuse_unless_admin(account)
    key = _key(session, account, key_id)
    if key.user_id != account.user.id:
        raise HTTPException(403, ROLL_YOUR_OWN)
    if refusal := check_api(account.organization):
        raise PlanRefused(refusal)
    secret = issue(key)
    key.rolled_at = now()
    key.last_used_at = None
    audit(
        session,
        account,
        request,
        "api_key_rolled",
        target=("api_key", key.id),
        detail={"name": key.name},
    )
    session.commit()
    return ApiKeyMadeOut(**_out(key, account.user).model_dump(), key=secret)


@router.delete("/organization/keys/{key_id}", status_code=204)
def revoke_key(key_id: int, account: AccountDep, request: Request, session: SessionDep):
    """Delete a key, whoever made it: the next request made with it is refused."""
    _refuse_unless_admin(account)
    key = _key(session, account, key_id)
    audit(
        session,
        account,
        request,
        "api_key_revoked",
        target=("api_key", key.id),
        detail={"name": key.name, "scope": key.scope},
    )
    session.delete(key)
    session.commit()


def _refuse_unless_admin(account: Account) -> None:
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)


def _key(session: Session, account: Account, key_id: int) -> ApiKey:
    """The organisation's key with this id; another organisation's is not found."""
    key = session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == account.organization.id)
    )
    if key is None:
        raise HTTPException(404, KEY_NOT_FOUND)
    return key


def _out(key: ApiKey, user: User) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        name=key.name,
        scope=key.scope,
        hint=key.hint,
        user_id=key.user_id,
        made_by=user.name or user.email,
        created_at=key.created_at,
        rolled_at=key.rolled_at,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        expired=expired(key),
    )
