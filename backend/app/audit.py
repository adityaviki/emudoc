"""The audit log: who did what to an organisation's documents, chats and accounts, and when.

Each row of `audit_events` (app/models.py) is one action: a member's sign-in or a change to
their account, the organisation started, renamed or handed over, a member leaving it, an upload,
download or deletion, a chat shared with the project or taken private again, and what the
operator's commands (app/maintenance.py) and the app on its own (a
quarantine, an account purge) did to the organisation. The code that acts writes the row with
`audit`, `audit_sign_in` or `audit_event` before its own commit, so an action and its row commit
or roll back together; a route that commits before a storage call writes it before that commit.
A sign-in refused for its password commits the row before the refusal, as the rate limits do
their count. Rendering a page or a thumbnail is a view, not a download, and leaves no row;
searching and asking leave none either, since what they would record is content.

What a row holds is chosen for erasure and secrecy. It names the member by id, so that it says
"a former member" once the account is gone, and the client's address, which the account purge
clears from every row of the user it deletes (`forget_addresses`, from app/retention.py); the
rows of an organisation that goes with the account go with it. Its detail holds names and
settings as they were (a file's name, a role before and after), never what a document, a
question or an answer says: not even a chat's title, which is its first question until it is
renamed. A failed sign-in for an address that has no account is not recorded, since the address
is a stranger's; the rate limits count it (app/ratelimit.py).

Each organisation's log holds what was done in it. A sign-in belongs to the organisation its
session starts in, the one the user joined first (`account_for_user` in app/accounts.py), and
what the account does after, its sign-out and the changes to how it signs in included, to the
one the session works in by then; so a member of several organisations does not show the others
when, or from where, they sign in, and a user in no organisation leaves no row. Switching is
not recorded: it is a view, and what is done after it is.

What a member does with an API key (app/apikeys.py) is theirs, with the key's id and name as
it was in the detail's `api_key`, so the log tells a program's acts from the member's own.
What Emudoc's support does while it views a member's account with their leave (app/support.py),
which changes nothing but may download, is the member's with the operator's id in the
detail's `support`; the leave, its withdrawal and each view's start and end are rows too.

Admins and owners read their organisation's rows page by page through `GET /api/audit`, by
action, member, project and span, and download them as a CSV file (app/api/audit.py), which the
log records as well.
The housekeeper (app/maintenance.py) deletes the rows older than the organisation's
`audit_days`, a year unless its owners and admins choose otherwise, with `prune_audit`, every
organisation in one statement; a null keeps them until the organisation goes.
"""

from typing import Any

from fastapi import Request
from sqlalchemy import Connection, text, update
from sqlalchemy.orm import Session

from app.accounts import Account, account_for_user
from app.models import AUDIT_ACTIONS, AuditEvent, User, UserSession

# What a row was done to: ("document", 7).
type Target = tuple[str, int]

# A null `audit_days` keeps a row for good: the interval and the comparison are null.
_PRUNE = text(
    """
    DELETE FROM audit_events
    USING organizations
    WHERE organizations.id = audit_events.org_id
      AND audit_events.occurred_at < now() - make_interval(days => organizations.audit_days)
    """
)


def audit_event(
    session: Session,
    action: str,
    *,
    org_id: int,
    user_id: int | None = None,
    request: Request | None = None,
    project_id: int | None = None,
    target: Target | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add a row to the organisation's audit log in the session's transaction, to commit with
    the action it records; the caller commits."""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"{action!r} is not one of the audit log's actions (AUDIT_ACTIONS)")
    target_type, target_id = target or (None, None)
    session.add(
        AuditEvent(
            org_id=org_id,
            user_id=user_id,
            action=action,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
            ip=request.client.host[:45] if request is not None and request.client else None,
        )
    )


def audit(
    session: Session,
    account: Account,
    request: Request | None,
    action: str,
    *,
    project_id: int | None = None,
    target: Target | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add a row for what the account's member did through the request, naming the API key it
    came with, if any, as it was then, or the operator who viewed the account as support; the
    caller commits."""
    if account.key is not None:
        detail = {**(detail or {}), "api_key": {"id": account.key.id, "name": account.key.name}}
    if account.operator_id is not None:
        detail = {**(detail or {}), "support": account.operator_id}
    audit_event(
        session,
        action,
        org_id=account.organization.id,
        user_id=account.user.id,
        request=request,
        project_id=project_id,
        target=target,
        detail=detail,
    )


def audit_sign_in(
    session: Session,
    user: User,
    request: Request,
    action: str,
    detail: dict[str, Any] | None = None,
    user_session: UserSession | None = None,
) -> None:
    """Add a row for a sign-in to the user's account, or a change to how it signs in, in the
    organisation `user_session` works in, or without one the one the user joined first; none
    for a user in no organisation. The caller commits."""
    account = account_for_user(session, user, user_session)
    if account is not None:
        audit(session, account, request, action, detail=detail)


def forget_addresses(session: Session, user_id: int) -> None:
    """Clear the client's address from every row recorded for the user, whose account is being
    deleted: the rows stay with the organisations, the address was the user's. The caller
    commits."""
    session.execute(update(AuditEvent).where(AuditEvent.user_id == user_id).values(ip=None))


def prune_audit(connection: Connection) -> int:
    """Delete the rows older than their organisation keeps them; returns how many."""
    return connection.execute(_PRUNE).rowcount
