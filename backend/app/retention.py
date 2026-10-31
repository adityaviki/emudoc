"""How long what people leave behind is kept, and how an account goes.

Chats are kept for the organisation's plan's `retention_days` after their last question
(app/entitlements.py: a month on the free plan, a year on pro, for good on team and
enterprise). The housekeeper (app/maintenance.py) deletes the older ones each round with
`prune_chats`, every organisation in one statement, and leaves a chat whose answer is still
being written. A chat's last question is `chats.updated_at`, which only asking sets, so
renaming or sharing an old chat does not keep it. A chat in the trash (app/trash.py) is still
held to it, and goes at whichever comes first, its retention or the trash's own TRASH_DAYS.

A move to a plan or terms that keep chats or audit rows for less time (a cancelled
subscription that ended, a switch to a smaller plan, a figure agreed lower) takes nothing at
once: what was kept before holds for RETENTION_HELD after the move (`hold_retention`, which
`change_plan` in app/billing.py calls), so that an organisation that comes back to its plan
within it finds its chats, and the owners, who are mailed the day, have time to choose. Both
prune statements read the hold beside the plan, and keep a row while either keeps it. A move
back up that keeps as long ends the hold; another move down within it holds the longest of
the two for RETENTION_HELD from then.

An organisation's owners and admins may keep chats for less time than its plan does
(`organizations.chat_days`, on the organisation page). Their choice wins over the plan and over
a hold alike: the hold keeps what a move the organisation may not have chosen would take, while
this is its own word. So a chat goes once it is older than either the plan, with a standing
hold, or the choice keeps it, and every figure the page and billing's mail give is the plan's
and the hold's with the choice applied (`chosen`).

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

An organisation is deleted the same way, at an owner's request (app/api/organizations.py),
confirmed as the account's is and refused while its subscription renews, since nothing would
end it. It is due DELETION_GRACE later, every member is told by mail, and until then any owner
may keep it. Meanwhile it is held as it stands (`held_for_deletion`): nothing is added to its
projects or changed in them and nobody new joins it or buys it a plan, while what it holds can
still be read, asked about, downloaded, exported (app/export.py) and deleted, so that its members
can take what they need.
Then the housekeeper deletes it with everything in it (`purge_due_organizations`), its audit log
too, which is why the request's mail is its record; a member it was the only organisation of is
given one of their own, as one removed from their last organisation is, since an account in
none can do nothing. Its purge leaves a record under ORGANIZATION_ERASURES, read back after a
restore like the accounts' (`reapply_organization_erasures`).
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from sqlalchemy import Connection, and_, delete, exists, func, select, text
from sqlalchemy.orm import Session, aliased

from app.accounts import LOCAL_ORG_SLUG, create_workspace
from app.audit import audit_event, forget_addresses
from app.auth import forget_sign_ups
from app.db import SessionLocal, engine, unbounded
from app.entitlements import MONTHS, PLANS, Entitlements, entitlements_of
from app.models import ROLES, Membership, Organization, User
from app.storage import Storage, organization_prefix

log = logging.getLogger(__name__)

# How long an account waits between the request to delete it and the purge: long enough to
# change one's mind, or to notice that someone else asked, and short enough that the erasure
# lands within the month the law allows, a housekeeping round's delay included.
DELETION_GRACE = timedelta(days=14)
# How long, after a move to a plan or terms that keep chats or audit rows for less time, they are
# kept as before: time to choose the plan again before the shorter period takes what it would,
# as a lower plan removes no document either.
RETENTION_HELD = timedelta(days=30)

LOCAL_ACCOUNT = (
    "The local account cannot be deleted: it is what this machine works as without signing "
    "in. Reset the database to start over."
)

LOCAL_ORGANISATION = (
    "The local organisation cannot be deleted: it is what this machine works in without "
    "signing in. Reset the database to start over."
)

# Where the purge leaves its record of each account it deleted: `erasure_key` names one; and of
# each organisation, `organization_erasure_key`.
ERASURES = "erasures/users/"
ORGANIZATION_ERASURES = "erasures/organizations/"
_STAMP = "%Y%m%dT%H%M%S.%fZ"
_RECORD = r"(\d+)-(\d{8}T\d{6}\.\d{6}Z)\.json"
# The accounts, or organisations, of the records that are back in the database, and not due
# already, made due now.
_BACK = """
    UPDATE {table} SET deletion_due_at = now()
    FROM unnest(CAST(:ids AS integer[]), CAST(:created AS timestamptz[])) AS erased (id, created_at)
    WHERE {table}.id = erased.id AND {table}.created_at = erased.created_at
      AND ({table}.deletion_due_at IS NULL OR {table}.deletion_due_at > now())
    RETURNING {table}.id
"""


# Each plan joins as a row of `kept`, so one statement prunes every organisation: by the days
# agreed with it when `overrides` names them (a null there keeps a chat for good), else by its
# plan's (a null plan keeps it for good: the interval and the comparison are null), and while a
# hold after a lower plan stands, by its days as well; or by the days the organisation chose.
def _past_retention(moment: str) -> str:
    """The condition that a row whose clock is `moment` is past what its organisation keeps a
    chat for: by its plan or agreed terms (the plans' days `kept` from `unnest`), and by a hold
    after a lower plan while it stands, or by the organisation's own choice."""
    return f"""(({moment} < now() - make_interval(days => CASE
              WHEN organizations.overrides -> 'retention_days' IS NULL THEN kept.days
              ELSE CAST(organizations.overrides ->> 'retention_days' AS integer) END)
            -- Past what a hold after a lower plan keeps, too: a null there keeps it for good.
            AND (organizations.retention_held_until IS NULL
                 OR organizations.retention_held_until <= now()
                 OR {moment} < now() - make_interval(days => organizations.held_chat_days)))
           -- A null choice is the plan's, and the comparison null.
           OR {moment} < now() - make_interval(days => organizations.chat_days))"""


_PRUNE_CHATS = text(
    f"""
    DELETE FROM chats
    USING projects, organizations,
          unnest(CAST(:plans AS text[]), CAST(:days AS integer[])) AS kept (plan, days)
    WHERE projects.id = chats.project_id
      AND organizations.id = projects.org_id
      AND organizations.plan = kept.plan
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
    USING organizations,
          unnest(CAST(:plans AS text[]), CAST(:days AS integer[])) AS kept (plan, days)
    WHERE organizations.id = workflow_runs.org_id
      AND organizations.plan = kept.plan
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
    USING organizations,
          unnest(CAST(:plans AS text[]), CAST(:days AS integer[])) AS kept (plan, days)
    WHERE organizations.id = question_reports.org_id
      AND organizations.plan = kept.plan
      AND {_past_retention("question_reports.created_at")}
      AND question_reports.status NOT IN ('queued', 'running')
    """
)
# A draft holds what documents said, as a chat's answers do, and is kept as long, from when it was
# last asked for, written or saved; never one the worker is writing.
_PRUNE_DRAFTS = text(
    f"""
    DELETE FROM drafts
    USING organizations,
          unnest(CAST(:plans AS text[]), CAST(:days AS integer[])) AS kept (plan, days)
    WHERE organizations.id = drafts.org_id
      AND organizations.plan = kept.plan
      AND {_past_retention("drafts.updated_at")}
      AND drafts.status NOT IN ('queued', 'writing')
    """
)


@dataclass(frozen=True)
class Kept:
    """How many days an organisation keeps a chat after its last question and a row of its
    audit log; None for good."""

    chat_days: int | None
    audit_days: int | None

    def covers(self, other: "Kept") -> bool:
        """Whether this keeps each at least as long as `other` does."""
        return _at_least(self.chat_days, other.chat_days) and _at_least(
            self.audit_days, other.audit_days
        )

    def longest(self, other: "Kept") -> "Kept":
        """Each kept as long as the longer of the two keeps it."""
        return Kept(
            self.chat_days if _at_least(self.chat_days, other.chat_days) else other.chat_days,
            self.audit_days if _at_least(self.audit_days, other.audit_days) else other.audit_days,
        )


def _at_least(days: int | None, other: int | None) -> bool:
    return days is None or (other is not None and days >= other)


@dataclass(frozen=True)
class Held:
    """What an organisation keeps as before after a move to a plan that keeps less, and until
    when; `until` is None for a move whose moment is not known yet."""

    until: datetime | None
    kept: Kept


def kept_by(entitlements: Entitlements) -> Kept:
    """How long a plan, with what was agreed in its place, keeps chats and audit rows."""
    return Kept(entitlements.retention_days, entitlements.audit_days)


def retention_held(organization: Organization, now: datetime | None = None) -> Held | None:
    """The hold that stands on the organisation after a move to a plan that keeps less: None
    once it is over, or when the plan keeps everything as long."""
    until = organization.retention_held_until
    if until is None or until <= (now or datetime.now(UTC)):
        return None
    kept = Kept(organization.held_chat_days, organization.held_audit_days)
    if kept_by(entitlements_of(organization)).covers(kept):
        return None
    return Held(until, kept)


def kept_for(organization: Organization, now: datetime | None = None) -> Kept:
    """How long the organisation keeps chats and audit rows now: as its plan or terms do, or
    longer while a hold after a lower plan stands, and chats no longer than it chose."""
    kept = kept_by(entitlements_of(organization))
    standing = retention_held(organization, now)
    return chosen(kept.longest(standing.kept) if standing else kept, organization)


def chosen(kept: Kept, organization: Organization) -> Kept:
    """What `kept`, a plan's or a hold's, keeps once the organisation's own choice of a
    shorter time for chats takes what is older."""
    days = organization.chat_days
    if days is None or not _at_least(kept.chat_days, days):
        return kept
    return Kept(days, kept.audit_days)


def hold_in_force(organization: Organization, now: datetime | None = None) -> Held | None:
    """The hold after a lower plan as it keeps what the organisation's choice leaves: None
    while none stands, or once the choice takes all the hold would keep that the plan does
    not."""
    standing = retention_held(organization, now)
    if standing is None:
        return None
    kept = chosen(standing.kept, organization)
    if chosen(kept_by(entitlements_of(organization)), organization).covers(kept):
        return None
    return Held(standing.until, kept)


def shortened(before: Kept, after: Kept) -> str | None:
    """What `after` keeps for less time than `before`, as a mail says it: "chats 30 days after
    their last question and audit log entries 90 days"; None when it keeps each as long."""
    parts = []
    if not _at_least(after.chat_days, before.chat_days):
        parts.append(f"chats {_days(after.chat_days)} after their last question")
    if not _at_least(after.audit_days, before.audit_days):
        parts.append(f"audit log entries {_days(after.audit_days)}")
    return " and ".join(parts) or None


def _days(days: int | None) -> str:
    # Never None here: what keeps for good keeps as long as anything.
    return f"{days:,} day{'' if days == 1 else 's'}"


def hold_retention(organization: Organization, was: Kept, now: datetime | None = None) -> None:
    """Hold, after the organisation's plan, seats or terms changed from ones that kept `was`,
    what was kept before for RETENTION_HELD, where the new ones keep less: `was` and a hold that
    stood, whichever keeps longer. New ones that keep as long leave a standing hold as it is,
    and end it once they keep as long as it does. The caller commits."""
    now = now or datetime.now(UTC)
    kept, standing = kept_by(entitlements_of(organization)), retention_held(organization, now)
    if kept.covers(was):
        if standing is None:
            organization.retention_held_until = None
            organization.held_chat_days = organization.held_audit_days = None
        return
    before = was.longest(standing.kept) if standing else was
    organization.retention_held_until = now + RETENTION_HELD
    organization.held_chat_days, organization.held_audit_days = before.chat_days, before.audit_days


def day(moment: datetime, zone: tzinfo = UTC) -> str:
    """A date as the messages name it, 29 October 2026, in UTC or in the zone of the person
    they are for (`zone_of` in app/accounts.py)."""
    moment = moment.astimezone(zone)
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def prune_chats(connection: Connection) -> int:
    """Delete the chats whose last question is older than their organisation keeps them,
    by its plan or its agreed terms, and by a hold after a lower plan while it stands, or by
    its own choice; returns how many."""
    return connection.execute(_PRUNE_CHATS, _plans_kept()).rowcount


def prune_runs(connection: Connection) -> int:
    """Delete the runs of cross-checks (app/check_runs.py) made longer ago than their
    organisation keeps a chat; returns how many."""
    return connection.execute(_PRUNE_RUNS, _plans_kept()).rowcount


def prune_reports(connection: Connection) -> int:
    """Delete the reports of question sets (app/question_reports.py) made longer ago than their
    organisation keeps a chat; returns how many."""
    return connection.execute(_PRUNE_REPORTS, _plans_kept()).rowcount


def prune_drafts(connection: Connection) -> int:
    """Delete the drafts (app/drafts.py) last asked for or saved longer ago than their
    organisation keeps a chat; returns how many."""
    return connection.execute(_PRUNE_DRAFTS, _plans_kept()).rowcount


def _plans_kept() -> dict[str, list]:
    kept = {plan: limit.retention_days for plan, limit in PLANS.items()}
    return {"plans": list(kept), "days": list(kept.values())}


def deletion_pending(due: datetime, zone: tzinfo = UTC) -> str:
    """What a request from an account that is set to be deleted is refused with, the day in
    the user's zone."""
    return (
        f"Your account is set to be deleted on {day(due, zone)}. Choose to keep it to use "
        "Emudoc again."
    )


def deletion_refusal(session: Session, user: User) -> str | None:
    """Why the user's account cannot be set to be deleted, or None when it can: they are the
    only owner of an organisation whose other members would be left without one, unless it is
    set to be deleted itself."""
    for membership, members, owners in _memberships(session, user.id):
        if membership.role == "owner" and members and not owners:
            organization = session.get(Organization, membership.org_id)
            if organization.deletion_due_at is not None:
                continue
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


def organization_erasure_key(org_id: int, created_at: datetime) -> str:
    """The key of the record the purge leaves of the organisation with this id and creation
    time."""
    return f"{ORGANIZATION_ERASURES}{org_id}-{format(created_at.astimezone(UTC), _STAMP)}.json"


def reapply_erasures(storage: Storage, *, dry_run: bool = False) -> list[int]:
    """Set every account that was purged and is back in the database, as a restore from an
    older backup brings it, to be purged at once; returns their ids. A dry run only finds them:
    it rolls the change back."""
    back = _reapply(storage, ERASURES, "users", dry_run)
    if back and not dry_run:
        log.warning(
            "Accounts %s were purged before and are back in the database, as a restore from a "
            "backup made before their purge brings them; they are purged again now",
            back,
        )
    return back


def reapply_organization_erasures(storage: Storage, *, dry_run: bool = False) -> list[int]:
    """`reapply_erasures` for the organisations deleted at their owners' request."""
    back = _reapply(storage, ORGANIZATION_ERASURES, "organizations", dry_run)
    if back and not dry_run:
        log.warning(
            "Organisations %s were deleted before and are back in the database, as a restore "
            "from a backup made before their deletion brings them; they are deleted again now",
            back,
        )
    return back


def _reapply(storage: Storage, prefix: str, table: str, dry_run: bool) -> list[int]:
    """Make due now the rows of `table` the records under `prefix` name, by id and creation
    time; returns their ids."""
    pattern = re.compile(re.escape(prefix) + _RECORD)
    erased = [
        (int(found[1]), datetime.strptime(found[2], _STAMP).replace(tzinfo=UTC))
        for entry in storage.list(prefix)
        if (found := pattern.fullmatch(entry.key))
    ]
    if not erased:
        return []
    with engine.connect() as connection:
        back = sorted(
            connection.scalars(
                text(_BACK.format(table=table)),
                {"ids": [row_id for row_id, _ in erased], "created": [at for _, at in erased]},
            )
        )
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    return back


def held_for_deletion(organization: Organization, zone: tzinfo = UTC) -> str | None:
    """What a change to the organisation is refused with while it is set to be deleted, the day
    in the zone of the user who asks; None when it is not."""
    if (due := organization.deletion_due_at) is None:
        return None
    return (
        f"{organization.name} is set to be deleted on {day(due, zone)}, so until then nothing "
        "can be added to its projects or changed in them and nobody new can join it: what it "
        "holds can still be read, asked about, downloaded, exported and deleted. An owner can "
        "keep it on the organisation page."
    )


def organization_deletion_refusal(organization: Organization) -> str | None:
    """Why the organisation cannot be set to be deleted, or None when it can; its subscription
    is billing's to judge (`renewal_refusal` in app/billing.py)."""
    if organization.slug == LOCAL_ORG_SLUG:
        return LOCAL_ORGANISATION
    return None


def purge_due_organizations(storage: Storage, *, renews: Callable[[Organization], bool]) -> int:
    """Delete every organisation whose deletion is due, one transaction each, and returns how
    many. One whose subscription `renews` again, which an owner could have chosen at Stripe
    after asking, is left due and logged, since deleting it would leave its subscription
    charging nobody's organisation."""
    purged = 0
    skipped: set[int] = set()
    while True:
        with SessionLocal() as session:
            # Deleting an organisation deletes every passage of its documents with it.
            unbounded(session)
            organization = session.scalar(
                select(Organization)
                .where(Organization.deletion_due_at <= func.now(), Organization.id.not_in(skipped))
                .order_by(Organization.deletion_due_at, Organization.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if organization is None:
                return purged
            org_id, created_at = organization.id, organization.created_at
            if renews(organization):
                log.error(
                    "Organisation %s is due to be deleted, but its subscription renews again: "
                    "it is kept until the subscription is cancelled",
                    org_id,
                )
                skipped.add(org_id)
                continue
            housed = purge_organization(session, organization)
            record = {
                "org_id": org_id,
                "created_at": created_at.isoformat(),
                "purged_at": datetime.now(UTC).isoformat(),
            }
            # Before the commit: a purge whose record cannot be kept waits for the next round.
            storage.put(
                organization_erasure_key(org_id, created_at),
                json.dumps(record).encode(),
                "application/json",
            )
            session.commit()
        storage.discard(organization_prefix(org_id))
        purged += 1
        log.info(
            "Deleted organisation %s as an owner asked; %s of its members were given an "
            "organisation of their own",
            org_id,
            housed,
        )


def purge_organization(session: Session, organization: Organization) -> int:
    """Delete the organisation, everything in it cascading from it, and give each member it was
    the only organisation of one of their own; returns how many were given one. The caller
    commits, then discards the organisation's prefix in the storage."""
    other = aliased(Membership)
    alone = session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == organization.id,
            ~exists().where(other.user_id == User.id, other.org_id != organization.id),
        )
        .order_by(User.id)
    ).all()
    session.execute(delete(Organization).where(Organization.id == organization.id))
    for user in alone:
        own = create_workspace(session, user).organization
        # The app made it, so the row names no member as its maker.
        audit_event(
            session,
            "organization_created",
            org_id=own.id,
            target=("user", user.id),
            detail={"name": own.name},
        )
    return len(alone)


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
