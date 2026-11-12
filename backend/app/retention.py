"""How long what people leave behind is kept, and how an account goes.

Chats are kept until they are deleted, unless the organisation's owners and admins choose a
number of days after their last question (`organizations.chat_days`, on the organisation page).
The housekeeper (app/maintenance.py) deletes the older ones each round with `prune_chats`, every
organisation in one statement, and leaves a chat whose answer is still being written. A chat's
last question is `chats.updated_at`, which only asking sets, so renaming or sharing an old chat
does not keep it. A chat in the trash (app/trash.py) is still held to it, and goes at whichever
comes first, its retention or the trash's own TRASH_DAYS. The runs of cross-checks, the reports
of question sets and the drafts hold what documents said, as a chat's answers do, and are kept
as long.

An account is deleted in two steps. The request (`POST /api/auth/account/deletion`) marks the
user due for purging DELETION_GRACE from now and signs them out everywhere else; until then the
account can do nothing but keep itself, export what is kept about its user (app/export.py) and
sign out, which the account dependency enforces (`get_usable_account` in app/api/deps.py).
Signing in during the grace period is how someone who changed their mind, or whose account
someone else asked to delete, gets it back. When it is due, the housekeeper purges it
(`purge_due_accounts`): every organisation the user is the only member of goes with everything
in it (projects, documents, chats, jobs, usage), then the user with their sessions, sign-in
identities, links and chats, and after the commit the organisations' files. In an organisation
that keeps other members, the user's membership and chats go and its documents stay, since a
document carries no uploader and belongs to the team; so do its audit rows of the user, without
the addresses they were recorded from, and one saying the account was deleted (app/audit.py).
The last owner of such an organisation is refused at the request (`deletion_refusal`); one who
is the last owner by the time of the purge all the same is succeeded by the next member in role
and seniority, so that no organisation is left without an owner and no erasure is held up.

The purge takes each due user under `FOR UPDATE SKIP LOCKED`, one transaction each, so replicas
can run it side by side, and a user who keeps their account at that moment either waits for the
purge to pass or is skipped by it. Only ids are logged on the way, so that the log does not
outlive the erasure; backups do until they expire (deploying.md), which is why the privacy
notice names their window as the outer bound of a deletion.

A backup made before a purge holds the account, and restoring it would bring the account back
with nothing due. So each purge leaves a record in the storage, under ERASURES and outside the
documents' `orgs/`, that names the account by its id and the moment it was created and holds
nothing else about the person; it is written before the purge commits, so no purge goes
unrecorded. Each housekeeping round reads the records first (`reapply_erasures`) and sets any
account that is back to be purged at once. The id alone would not do: a restored database
hands out ids again from where its sequences were, so a new account can have a purged one's
id, never its creation time as well. A database restored on its own heals within a round of
the worker's start; a bucket restored with it must keep ERASURES as it is (deploying.md). The
records are kept for good, since backups may be kept for any time and the records say nothing
of anyone.

The workspace itself is not deleted from the app: it is the install (S.1). Until 2026-11-12 an
owner could ask for an organisation's deletion, and the housekeeper purged it after the grace
period.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta, tzinfo

from sqlalchemy import Connection, and_, delete, func, select, text
from sqlalchemy.orm import Session, aliased

from app.audit import audit_event, forget_addresses
from app.auth import forget_sign_ups
from app.db import SessionLocal, engine, unbounded
from app.models import ROLES, Membership, Organization, User
from app.storage import Storage, organization_prefix
from app.usage import MONTHS

log = logging.getLogger(__name__)

# How long an account waits between the request to delete it and the purge: long enough to
# change one's mind, or to notice that someone else asked, and short enough that the erasure
# lands within the month the law allows, a housekeeping round's delay included.
DELETION_GRACE = timedelta(days=14)

LOCAL_ACCOUNT = (
    "The local account cannot be deleted: it is what this machine works as without signing "
    "in. Reset the database to start over."
)

# Where the purge leaves its record of each account it deleted: `erasure_key` names one.
ERASURES = "erasures/users/"
_STAMP = "%Y%m%dT%H%M%S.%fZ"
_RECORD = r"(\d+)-(\d{8}T\d{6}\.\d{6}Z)\.json"
# The accounts of the records that are back in the database, and not due already, made due now.
_BACK = """
    UPDATE users SET deletion_due_at = now()
    FROM unnest(CAST(:ids AS integer[]), CAST(:created AS timestamptz[])) AS erased (id, created_at)
    WHERE users.id = erased.id AND users.created_at = erased.created_at
      AND (users.deletion_due_at IS NULL OR users.deletion_due_at > now())
    RETURNING users.id
"""


# A null `chat_days` keeps a row for good: the interval and the comparison are null.
def _past_retention(moment: str) -> str:
    """The condition that a row whose clock is `moment` is older than its organisation keeps a
    chat."""
    return f"{moment} < now() - make_interval(days => organizations.chat_days)"


_PRUNE_CHATS = text(
    f"""
    DELETE FROM chats
    USING projects, organizations
    WHERE projects.id = chats.project_id
      AND organizations.id = projects.org_id
      AND {_past_retention("chats.updated_at")}
      AND NOT EXISTS (
          SELECT 1 FROM chat_messages
          WHERE chat_messages.chat_id = chats.id AND chat_messages.status = 'streaming'
      )
    """
)
# A run of cross-checks holds what documents said, as a chat's answers do, and is kept as long,
# from when it was made; never one the worker is still working out or writing a report for.
_PRUNE_RUNS = text(
    f"""
    DELETE FROM workflow_runs
    USING organizations
    WHERE organizations.id = workflow_runs.org_id
      AND {_past_retention("workflow_runs.created_at")}
      AND workflow_runs.status NOT IN ('queued', 'running')
      AND (workflow_runs.report_status IS NULL
           OR workflow_runs.report_status NOT IN ('queued', 'writing'))
    """
)
# A report of a question set's answers holds what documents said, as a chat's answers do, and is
# kept as long, from when it was made; never one the worker is still answering.
_PRUNE_REPORTS = text(
    f"""
    DELETE FROM question_reports
    USING organizations
    WHERE organizations.id = question_reports.org_id
      AND {_past_retention("question_reports.created_at")}
      AND question_reports.status NOT IN ('queued', 'running')
    """
)
# A draft holds what documents said, as a chat's answers do, and is kept as long, from when it was
# last asked for, written or saved; never one the worker is writing.
_PRUNE_DRAFTS = text(
    f"""
    DELETE FROM drafts
    USING organizations
    WHERE organizations.id = drafts.org_id
      AND {_past_retention("drafts.updated_at")}
      AND drafts.status NOT IN ('queued', 'writing')
    """
)


def day(moment: datetime, zone: tzinfo = UTC) -> str:
    """A date as the messages name it, 29 October 2026, in UTC or in the zone of the person
    they are for (`zone_of` in app/accounts.py)."""
    moment = moment.astimezone(zone)
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def prune_chats(connection: Connection) -> int:
    """Delete the chats whose last question is older than their organisation keeps them;
    returns how many."""
    return connection.execute(_PRUNE_CHATS).rowcount


def prune_runs(connection: Connection) -> int:
    """Delete the runs of cross-checks (app/check_runs.py) made longer ago than their
    organisation keeps a chat; returns how many."""
    return connection.execute(_PRUNE_RUNS).rowcount


def prune_reports(connection: Connection) -> int:
    """Delete the reports of question sets (app/question_reports.py) made longer ago than their
    organisation keeps a chat; returns how many."""
    return connection.execute(_PRUNE_REPORTS).rowcount


def prune_drafts(connection: Connection) -> int:
    """Delete the drafts (app/drafts.py) last asked for or saved longer ago than their
    organisation keeps a chat; returns how many."""
    return connection.execute(_PRUNE_DRAFTS).rowcount


def deletion_pending(due: datetime, zone: tzinfo = UTC) -> str:
    """What a request from an account that is set to be deleted is refused with, the day in
    the user's zone."""
    return (
        f"Your account is set to be deleted on {day(due, zone)}. Choose to keep it to use "
        "Emudoc again."
    )


def deletion_refusal(session: Session, user: User) -> str | None:
    """Why the user's account cannot be set to be deleted, or None when it can: they are the
    only owner of an organisation whose other members would be left without one."""
    for membership, members, owners in _memberships(session, user.id):
        if membership.role == "owner" and members and not owners:
            organization = session.get(Organization, membership.org_id)
            return (
                f"You are the only owner of {organization.name}, which has other members. Make "
                "one of them an owner, or remove them, before deleting your account."
            )
    return None


def purge_due_accounts(storage: Storage) -> int:
    """Purge every account whose deletion is due, one transaction each; returns how many."""
    purged = 0
    while True:
        with SessionLocal() as session:
            # Deleting an organisation deletes every passage of its documents with it.
            unbounded(session)
            user = session.scalar(
                select(User)
                .where(User.deletion_due_at <= func.now())
                .order_by(User.deletion_due_at, User.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if user is None:
                return purged
            user_id, created_at = user.id, user.created_at
            prefixes = purge_account(session, user)
            record = {
                "user_id": user_id,
                "created_at": created_at.isoformat(),
                "purged_at": datetime.now(UTC).isoformat(),
            }
            # Before the commit: a purge whose record cannot be kept waits for the next round.
            storage.put(
                erasure_key(user_id, created_at), json.dumps(record).encode(), "application/json"
            )
            session.commit()
        storage.discard(*prefixes)
        purged += 1
        log.info(
            "Deleted account %s as its user asked, with the %s organisation(s) only it belonged to",
            user_id,
            len(prefixes),
        )


def erasure_key(user_id: int, created_at: datetime) -> str:
    """The key of the record the purge leaves of the account with this id and creation time."""
    return f"{ERASURES}{user_id}-{format(created_at.astimezone(UTC), _STAMP)}.json"


def reapply_erasures(storage: Storage, *, dry_run: bool = False) -> list[int]:
    """Set every account that was purged and is back in the database, as a restore from an
    older backup brings it, to be purged at once; returns their ids. A dry run only finds them:
    it rolls the change back."""
    back = _reapply(storage, dry_run)
    if back and not dry_run:
        log.warning(
            "Accounts %s were purged before and are back in the database, as a restore from a "
            "backup made before their purge brings them; they are purged again now",
            back,
        )
    return back


def _reapply(storage: Storage, dry_run: bool) -> list[int]:
    """Make due now the accounts the records name, by id and creation time; returns their
    ids."""
    pattern = re.compile(re.escape(ERASURES) + _RECORD)
    erased = [
        (int(found[1]), datetime.strptime(found[2], _STAMP).replace(tzinfo=UTC))
        for entry in storage.list(ERASURES)
        if (found := pattern.fullmatch(entry.key))
    ]
    if not erased:
        return []
    with engine.connect() as connection:
        back = sorted(
            connection.scalars(
                text(_BACK),
                {"ids": [row_id for row_id, _ in erased], "created": [at for _, at in erased]},
            )
        )
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    return back


def purge_account(session: Session, user: User) -> list[str]:
    """Delete the user and every organisation they are the only member of, the rows cascading
    from them; returns those organisations' prefixes in the storage, for the caller to discard
    once the deletion is committed. The caller commits.

    An organisation that keeps other members keeps its audit rows of the user too, without the
    addresses they were recorded from, and gains one saying the account was deleted."""
    forget_addresses(session, user.id)
    # A sign-up link sent to the address before it became the account's, through a provider
    # or a change of address, names it too and has no user to go with.
    forget_sign_ups(session, user.email)
    alone = []
    for membership, members, owners in _memberships(session, user.id):
        if not members:
            alone.append(membership.org_id)
            continue
        if membership.role == "owner" and not owners:
            _promote_successor(session, membership.org_id, user.id)
        audit_event(session, "account_purged", org_id=membership.org_id, target=("user", user.id))
    if alone:
        session.execute(delete(Organization).where(Organization.id.in_(alone)))
    session.execute(delete(User).where(User.id == user.id))
    return [organization_prefix(org_id) for org_id in alone]


def _memberships(session: Session, user_id: int) -> list[tuple[Membership, int, int]]:
    """Each of the user's memberships, with how many other members its organisation has and
    how many of those are owners."""
    others = aliased(Membership)
    rows = session.execute(
        select(
            Membership,
            func.count(others.id),
            func.count(others.id).filter(others.role == "owner"),
        )
        .outerjoin(
            others, and_(others.org_id == Membership.org_id, others.user_id != Membership.user_id)
        )
        .where(Membership.user_id == user_id)
        .group_by(Membership.id)
        .order_by(Membership.org_id)
    ).all()
    return [tuple(row) for row in rows]


def _promote_successor(session: Session, org_id: int, user_id: int) -> None:
    """Make the organisation's most senior other member its owner: the highest role first,
    then the longest membership."""
    candidates = session.scalars(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id != user_id)
    ).all()
    successor = min(candidates, key=lambda m: (ROLES.index(m.role), m.created_at, m.id))
    log.warning(
        "Organisation %s loses its last owner, user %s, whose account is being deleted; user %s "
        "(%s until now) becomes its owner",
        org_id,
        user_id,
        successor.user_id,
        successor.role,
    )
    audit_event(
        session,
        "role_changed",
        org_id=org_id,
        target=("user", successor.user_id),
        detail={"from": successor.role, "to": "owner"},
    )
    successor.role = "owner"
