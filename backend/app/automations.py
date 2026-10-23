"""A project's automations: rules that run its cross-checks on their own (app/check_runs.py) as
documents arrive, and tell whom they name what the run found about them.

An automation watches the documents of one kind (those read for its template, which
`documents.template` names), or any, arriving in a folder, its subfolders with it, or anywhere
in the project; and with `recheck`, the documents its checks compare those with arriving
anywhere: the delivery note that lets an invoice's quantities be checked, a new price list
(`partner_kinds`). A document arrives as the upload it came in settles (`settle_upload` in
app/notices.py, in its transaction), ready and out of the trash, its kind read by then: each is
kept for the automation as an `AutomationArrival`, and a `run_automation` job is queued for
AUTOMATION_WAIT seconds later unless one is queued already.

The job (`run_automation` in app/check_runs.py) runs the checks once the arrivals have gone
quiet: AUTOMATION_WAIT after the last of them, or MOST_HELD after the first however many keep
arriving (`due_at`), so that forty invoices a member's page sends in four requests make one run,
not four; a job that wakes before then queues the next for the moment and ends. A run an
automation makes is made as a member's is, with no member behind it: it names the automation and
what arrived (`WorkflowRun.trigger`), and once it settles it is told, once, to whom the automation
names (`audience`): its maker, everyone who sees the project, or nobody.

An automation and the arrivals waiting for it lock its row, in id order, so that two uploads
settling at once queue one job and a run takes every arrival before it, none twice.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.cross_checks import MAX_CROSS_CHECKS
from app.extraction import Template
from app.jobs import LIVE, RUN_AUTOMATION, enqueue
from app.models import (
    Automation,
    AutomationArrival,
    AutomationCheck,
    CrossCheck,
    Document,
    Folder,
    Membership,
)

# The automations one project may have.
MAX_AUTOMATIONS = 10
# However many keep arriving, an automation runs within this long of the first that waited.
MOST_HELD = timedelta(minutes=15)

NO_CHECKS = "The project has no cross-checks yet, so it has nothing to run. Add one."
CHECKS_GONE = "The cross-checks it ran were deleted. Choose others, or every one."
KIND_GONE = "The project has no template for its kind of document any more. Choose another kind."
FOLDER_TRASHED = "Its folder is in the trash. Restore the folder, or choose another."
MAKER_GONE = "Whoever made it is no longer a member, so nobody is told what it finds."

# The jobs queued and not yet claimed for an automation, which an arrival does not add to.
_QUEUED = text(
    "SELECT 1 FROM jobs WHERE kind = :kind AND status = 'queued' "
    "AND (payload->>'automation_id')::bigint = :id"
)


def checks_of(session: Session, automation: Automation) -> list[CrossCheck]:
    """The cross-checks the automation runs, by name: every one of its project's, or those it
    chose that are still there."""
    query = select(CrossCheck).where(CrossCheck.project_id == automation.project_id)
    if not automation.every_check:
        query = query.join(
            AutomationCheck,
            (AutomationCheck.cross_check_id == CrossCheck.id)
            & (AutomationCheck.automation_id == automation.id),
        )
    return list(
        session.scalars(query.order_by(CrossCheck.title, CrossCheck.id).limit(MAX_CROSS_CHECKS))
    )


def partner_kinds(kind: str | None, checks: Iterable[CrossCheck]) -> set[str]:
    """The kinds of document the checks compare the automation's kind with, whose arrivals run a
    rechecking automation again: of a rule checking its kind, the other side's, and of one
    checking another kind against its kind, that kind. Without a kind, every kind they compare.
    """
    kinds: set[str] = set()
    for check in checks:
        if kind is None or check.left_kind == kind:
            kinds.add(check.right_kind)
        if kind is None or check.right_kind == kind:
            kinds.add(check.left_kind)
    return kinds - {kind} if kind is not None else kinds


def problem_of(
    session: Session,
    automation: Automation,
    checks: list[CrossCheck],
    templates: list[Template],
    folder: Folder | None,
) -> str | None:
    """Why the automation would run nothing, or tell nobody, as its project stands now; None
    when it would run as it says."""
    if automation.kind is not None and automation.kind not in {t.doc_type for t in templates}:
        return KIND_GONE
    if folder is not None and folder.deleted_at is not None:
        return FOLDER_TRASHED
    if not checks:
        return NO_CHECKS if automation.every_check else CHECKS_GONE
    if automation.audience == "maker" and (
        automation.created_by is None
        or session.scalar(
            select(Membership.user_id).where(
                Membership.org_id == automation.org_id,
                Membership.user_id == automation.created_by,
            )
        )
        is None
    ):
        return MAKER_GONE
    return None


def arrived(session: Session, upload_id: int, project_id: int) -> int:
    """Keep the upload's documents for each of its project's automations that watches them, or
    with `recheck` what they are compared with, and queue the run of each that has none queued,
    in the caller's session, which commits; returns for how many automations they were kept.
    Run as the upload settles: its documents are ready, failed or set apart, and the ready ones
    have their kind."""
    automations = session.scalars(
        select(Automation)
        .where(Automation.project_id == project_id, Automation.enabled.is_(True))
        .order_by(Automation.id)
    ).all()
    if not automations:
        return 0
    documents = session.execute(
        select(Document.id, Document.template, Document.folder_id).where(
            Document.upload_id == upload_id,
            Document.project_id == project_id,
            Document.status == "ready",
            Document.deleted_at.is_(None),
        )
    ).all()
    if not documents:
        return 0
    subtrees = _subtrees(session, project_id, {a.folder_id for a in automations} - {None})
    kept = 0
    for automation in automations:
        watched = [
            document.id
            for document in documents
            if (automation.kind is None or document.template == automation.kind)
            and (
                automation.folder_id is None
                or document.folder_id in subtrees.get(automation.folder_id, ())
            )
        ]
        partners = []
        if automation.recheck:
            kinds = partner_kinds(automation.kind, checks_of(session, automation))
            partners = [
                document.id
                for document in documents
                if document.id not in watched and document.template in kinds
            ]
        if not watched and not partners:
            continue
        # Held until the commit: a run being made for it takes these too, or waits for them.
        session.execute(
            select(Automation.id).where(Automation.id == automation.id).with_for_update()
        )
        rows = [
            {"automation_id": automation.id, "document_id": document_id, "recheck": recheck}
            for ids, recheck in ((watched, False), (partners, True))
            for document_id in ids
        ]
        session.execute(insert(AutomationArrival).values(rows).on_conflict_do_nothing())
        queue_automation(session, automation, _wait_from(datetime.now(UTC)))
        kept += 1
    return kept


def queue_automation(session: Session, automation: Automation, run_at: datetime) -> bool:
    """Queue the job that runs the automation's checks at `run_at`, ahead of uploads, since its
    run is what the members it tells wait for, unless one is queued already: that one, waking
    before the arrivals have gone quiet, queues the next itself. Returns whether it queued one.
    """
    if session.scalar(_QUEUED, {"kind": RUN_AUTOMATION, "id": automation.id}):
        return False
    enqueue(
        session,
        RUN_AUTOMATION,
        org_id=automation.org_id,
        project_id=automation.project_id,
        payload={"automation_id": automation.id},
        priority=LIVE,
        run_at=run_at,
    )
    return True


def due_at(session: Session, automation_id: int) -> datetime | None:
    """When the automation's arrivals have gone quiet enough to run its checks over: the wait
    after the last of them, or MOST_HELD after the first; None while none waits."""
    first, last = session.execute(
        select(
            func.min(AutomationArrival.arrived_at), func.max(AutomationArrival.arrived_at)
        ).where(AutomationArrival.automation_id == automation_id)
    ).one()
    if first is None:
        return None
    return min(_wait_from(last), first + MOST_HELD)


def take_arrivals(session: Session, automation_id: int) -> tuple[list[int], list[int]]:
    """The automation's arrivals waiting, taken for its run in the caller's session: the ids of
    the documents it watches, and of those that run it again, each in the order they came."""
    taken = session.execute(
        delete(AutomationArrival)
        .where(AutomationArrival.automation_id == automation_id)
        .returning(
            AutomationArrival.document_id, AutomationArrival.recheck, AutomationArrival.arrived_at
        )
    ).all()
    ordered = sorted(taken, key=lambda row: (row.arrived_at, row.document_id))
    return (
        [row.document_id for row in ordered if not row.recheck],
        [row.document_id for row in ordered if row.recheck],
    )


def _wait_from(moment: datetime) -> datetime:
    return moment + timedelta(seconds=get_settings().automation_wait)


def _subtrees(session: Session, project_id: int, roots: set[int]) -> dict[int, set[int]]:
    """Each of the folders the automations watch with the live folders below it, read in one
    query; a folder in the trash holds nothing that arrives."""
    if not roots:
        return {}
    children: dict[int | None, list[int]] = {}
    live = set()
    for folder_id, parent_id in session.execute(
        select(Folder.id, Folder.parent_id).where(
            Folder.project_id == project_id, Folder.deleted_at.is_(None)
        )
    ):
        children.setdefault(parent_id, []).append(folder_id)
        live.add(folder_id)
    subtrees: dict[int, set[int]] = {}
    for root in roots & live:
        ids, stack = set(), [root]
        while stack:
            current = stack.pop()
            ids.add(current)
            stack.extend(children.get(current, ()))
        subtrees[root] = ids
    return subtrees
