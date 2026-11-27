"""The first steps an organisation takes, which the projects page lists until they are taken or
its member hides the list: its own documents uploaded, a question asked about them, and its team
invited. Each is read from the rows the step leaves, never kept as a step, so one taken by another
member, or before the list existed, is taken: a member who joins a team at work finds it set up and
is shown nothing. The documents count outside the sample project alone, which every sign-up's
organisation starts with (app/sample), while a question asked in the sample counts, being what it is
for. A question is a member's message in any chat of the organisation's, so the step is untaken
again only once every chat is gone, as when the workspace's retention deleted them all. What is in
the trash (app/trash.py) takes no step, as it held none before there was a trash.

The steps are the organisation's, the member's role picks those it is offered (`offered`): an
owner or admin all three, a member uploading and asking, since only owners and admins invite,
and a viewer none, since it can only ask, which an empty chat offers already.

Whether the list is hidden and whether the tour of a project's workspace was seen are the
user's (`users.checklist_hidden_at`, `users.tour_seen_at`), for every organisation they work in,
chosen through `PATCH /api/auth/onboarding` (app/api/onboarding.py) and carried by `MeOut`.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.accounts import Account

# In the order the list shows them.
STEPS = ("upload", "ask", "invite")

# Whether each step is taken in the organisation, in one statement; every part stops at the first
# row it finds.
_TAKEN = text(
    """
    SELECT
        EXISTS (
            SELECT 1 FROM documents d JOIN projects p ON p.id = d.project_id
            WHERE p.org_id = :org AND NOT p.sample
              AND d.deleted_at IS NULL AND p.deleted_at IS NULL
        ) AS upload,
        EXISTS (
            SELECT 1 FROM chat_messages m
            JOIN chats c ON c.id = m.chat_id
            JOIN projects p ON p.id = c.project_id
            WHERE p.org_id = :org AND m.role = 'user'
              AND c.deleted_at IS NULL AND p.deleted_at IS NULL
        ) AS ask,
        EXISTS (SELECT 1 FROM memberships WHERE org_id = :org AND user_id <> :user)
        OR EXISTS (SELECT 1 FROM invitations WHERE org_id = :org) AS invite
    """
)


@dataclass(frozen=True)
class Step:
    key: str
    done: bool


def offered(account: Account) -> tuple[str, ...]:
    """The steps the account's role may take, in order."""
    if account.is_at_least("admin"):
        return STEPS
    if account.is_at_least("member"):
        return ("upload", "ask")
    return ()


def first_steps(session: Session, account: Account) -> list[Step]:
    """The steps the account is offered, each with whether its organisation has taken it."""
    keys = offered(account)
    if not keys:
        return []
    taken = (
        session.execute(_TAKEN, {"org": account.organization.id, "user": account.user.id})
        .mappings()
        .one()
    )
    return [Step(key, taken[key]) for key in keys]
