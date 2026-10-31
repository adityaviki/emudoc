"""Runs of a project's cross-checks (app/cross_checks.py, indexing-approach.md, "Cross-checks"):
the rules worked out at a moment over the documents as they read then, and kept with what they
found, and the discrepancy report the agent writes from that when a member asks for one.

A rule's findings are otherwise worked out at each request and kept nowhere, which is right for
the view of a rule but leaves nothing to show for what was checked last week or to hand on. A
run keeps them: `start_run` takes the rules as they are (each one's title and what it compares,
`snapshot`), since rules are changed and deleted after, and queues the worker's `run_workflow`
job, which works each rule out (`work_out`) and keeps every finding as a `WorkflowFinding`, the
values, quotes and places as read, never a fact to join on later: a document read again writes
its facts and passages anew. A rule the project's templates can no longer work out is kept with
why (`problem`), as its view shows it.

The report is the agent's (`write_report`): the run's findings, worded by the server, are the
input of its task, with the passages their values were read from, which it may cite as an answer
does, and its five read-only tools to read around them. What was compared, and what was found,
stays the code's; the model writes it up. It is model spend: its calls are recorded as `report`
usage of the run, held to the day's spending limits as the run starts, and its asking counts as
one of the month's questions (app/api/check_runs.py). It is written whole, not streamed: the page
asks for the run until the report settles.

An automation's run (app/automations.py) is made by the worker (`run_automation`) once the
documents it watches have gone quiet, with no member behind it: it names the automation and what
arrived (`trigger`), and its report, when the automation asks for one, is held to the month's
questions, of which it counts one, and the day's spending as a member's is, a refusal leaving
the run without its report and saying why. Once such a run settles, worked out and its report
written or failed, the documents the automation said are put in review (app/reviews.py), those
its checks found a difference about or every one it watches that arrived, and what it found
about the documents that arrived is told, once (`tell_run`), to whom the automation names.

A run's values are what documents say, like a chat's answers: the members' pages hold them, never
the audit log, a log line or a notice, which counts them, and a run is kept as long as the
organisation keeps a chat (`prune_runs` in app/retention.py).
"""

import logging
from collections import Counter
from collections.abc import Generator, Iterable
from dataclasses import asdict, fields
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, exists, func, insert, or_, select, text, update
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.agent.loop import AGENT_PROMPTS, run_agent
from app.answers import Reply
from app.audit import audit_event
from app.automations import checks_of, due_at, queue_automation, take_arrivals
from app.cross_checks import (
    CURRENCY,
    NO_ITEM,
    NO_KEY,
    NO_PARTNER,
    NO_PARTNER_VALUE,
    NO_VALUE,
    OUTCOMES,
    UNTYPED,
    Finding,
    Rule,
    findings_of,
    rule_of,
    sides_of,
)
from app.db import SessionLocal, unbounded
from app.entitlements import check_question
from app.extraction import Template, templates_for
from app.fact_checks import fact_out
from app.flags import EXTRACTION_TEMPLATES, flag_on
from app.indexing.pipeline import Services
from app.jobs import LIVE, RUN_WORKFLOW, Lease, LeaseLost, enqueue, renew
from app.mail import checks_run_email, in_organization
from app.models import (
    Automation,
    CrossCheck,
    Document,
    Passage,
    Project,
    WorkflowFinding,
    WorkflowRun,
)
from app.notices import (
    CHECKS_RUN,
    MONTH_QUESTIONS,
    PROJECT_PAGE,
    app_address,
    audience_of,
    note_quota,
    tell,
)
from app.prompts import prompt_version
from app.reviews import put_in_review
from app.spending import check_platform_spend, check_spend
from app.usage import ANSWER, REPORT, WORKFLOW_RUN, ModelUsage, record

log = logging.getLogger(__name__)

PROJECT_GONE = (
    "The project went into the trash before its checks were run. Restore it to run them again."
)
RUN_FAILED = "The checks failed unexpectedly. Run them again in a moment."
REPORT_FAILED = "The report failed unexpectedly. Ask for it again in a moment."
# The findings a report's input lists one by one, those that differ first; the rest are counted.
REPORT_FINDINGS = 60
# The characters of a value the report's input shows.
VALUE_CHARS = 300
# The findings a statement writes at once.
WRITE_BATCH = 500
# The names of the documents that arrived an automation's run keeps, for its page and its notice.
ARRIVALS_NAMED = 3
# Where a run's notice leads: the run in its project's library.
RUN_PAGE = PROJECT_PAGE + "?run={}"

REPORT_TASK = """\
Your task now is a discrepancy report, not an answer to a question. The message lists what a \
run of the project's cross-checks found: rules the project's members wrote, each comparing a \
field of one kind of document with a field of others, worked out by the server over the facts \
read from the documents. The findings are the server's: report them as they are, never decide \
again whether a rule holds, and add no check of your own.

Write the report in Markdown for the person who must act on it:
- Open with two or three sentences: what was checked, and what needs attention.
- Then, for each rule with discrepancies, a short section whose bullets each name the document, \
the values on both sides and how far apart they are, citing each value's passage straight after \
it.
- Then, briefly, the values that could not be checked, and why.
- End with what to do next: which documents to look at, or whom to ask.
Cite the references given beside the values, or returned by your tools, and no other. Use the \
tools only where a value's passage alone does not settle what its document says."""
# The version of a report's prompts (app/prompts.py), which its usage rows carry.
REPORT_PROMPT_VERSION = prompt_version(*AGENT_PROMPTS, REPORT_TASK)

# Why a value could not be checked, as the report's input says it.
_MISSING = {
    NO_VALUE: "it states no {left_field}",
    NO_KEY: "it states no {left_key}, which pairs it with a {right_kind}",
    NO_PARTNER: "no {right_kind} is paired with it",
    NO_PARTNER_VALUE: "the {right_kind} paired with it states no {right_field}",
    NO_ITEM: "no line of the {right_kind} paired with it names the same item",
    UNTYPED: "a value has no number or date to compare",
    CURRENCY: "the amounts are in two currencies, which are not compared",
}
_RULE_FIELDS = tuple(field.name for field in fields(Rule))


def snapshot(row: CrossCheck, templates: Iterable[Template]) -> dict[str, Any]:
    """A rule as a run keeps it: its cross-check's id, its title, what it compares and the types
    of its fields as the project's templates hold them now, which the page words it by."""
    rule = rule_of(row)
    by_kind = {template.doc_type: template for template in templates}
    return {
        "check_id": row.id,
        "title": row.title,
        **asdict(rule),
        "left_type": _type_of(by_kind, rule.left_kind, rule.left_field),
        "right_type": _type_of(by_kind, rule.right_kind, rule.right_field),
        "problem": None,
        "counts": None,
    }


def rule_from(kept: dict[str, Any]) -> Rule:
    """The rule a run's snapshot names."""
    return Rule(**{name: kept[name] for name in _RULE_FIELDS})


def start_run(
    session: Session,
    project: Project,
    user_id: int | None,
    rows: list[CrossCheck],
    *,
    report: bool,
    automation: Automation | None = None,
    trigger: dict[str, Any] | None = None,
) -> WorkflowRun:
    """A run of the rules over the project, queued for the worker in the caller's session, with
    its report asked for at once when `report`; the member's who ran it, or the automation's
    with what arrived for it. The caller commits."""
    templates = templates_for(session, project.id)
    run = WorkflowRun(
        org_id=project.org_id,
        project_id=project.id,
        user_id=user_id,
        rules=[snapshot(row, templates) for row in rows],
        report_status="queued" if report else None,
        automation_id=automation.id if automation is not None else None,
        trigger=trigger,
    )
    session.add(run)
    session.flush()
    queue_run(session, run)
    return run


def queue_run(session: Session, run: WorkflowRun) -> None:
    """Queue the job that works the run out, or writes its report, ahead of uploads: a member is
    waiting for it."""
    enqueue(
        session,
        RUN_WORKFLOW,
        org_id=run.org_id,
        project_id=run.project_id,
        payload={"run_id": run.id},
        priority=LIVE,
    )


def run_workflow(run_id: int, services: Services, lease: Lease | None = None) -> str | None:
    """The worker's handler: work the run out if it is not yet, then write its report if one is
    asked for. Run again after a lost lease, it starts the part it was in over. If the run itself
    breaks, the part it was in ends failed with a sentence before the job fails with the
    traceback."""
    try:
        work_out(run_id, lease)
        write_report(run_id, services, lease)
    except LeaseLost:
        raise
    except Exception:
        log.exception("Run %s of cross-checks failed", run_id)
        fail_run(run_id)
        raise
    tell_run(run_id, lease)
    return None


def run_automation(automation_id: int, lease: Lease | None = None) -> str | None:
    """The worker's handler of an automation's job: once the documents that arrived for it have
    gone quiet, make the run of its cross-checks over the project as it reads now, as a member's
    run is made, naming the automation and what arrived; a job that wakes before then queues the
    next for that moment. Committed under the lease, a job run again after a lost one finds the
    arrivals it took gone with the run it made, or waiting still if the commit was lost too."""
    with SessionLocal() as session:
        automation = session.scalar(
            select(Automation).where(Automation.id == automation_id).with_for_update()
        )
        if automation is None:
            return None
        due = due_at(session, automation_id)
        if due is None:
            # An earlier job's run took them.
            return None
        if due > datetime.now(UTC):
            queue_automation(session, automation, due)
            _commit(session, lease)
            return None
        landed, partners = take_arrivals(session, automation_id)
        project = session.get(Project, automation.project_id)
        organization = project.organization
        live = set(
            session.scalars(
                select(Document.id).where(
                    Document.id.in_(landed + partners),
                    Document.project_id == project.id,
                    Document.deleted_at.is_(None),
                )
            )
        )
        landed = [document_id for document_id in landed if document_id in live]
        partners = [document_id for document_id in partners if document_id in live]
        checks = checks_of(session, automation)
        if (
            not (landed or partners)
            or not checks
            or not automation.enabled
            or project.deleted_at is not None
            or not flag_on(organization, EXTRACTION_TEMPLATES)
        ):
            # Nothing to run, or nothing it may run now: what arrived goes with this job, the
            # automation's page saying why when the cause is its own.
            _commit(session, lease)
            return None
        refusal = None
        if automation.report:
            refused = check_question(session, organization)
            refusal = (
                refused.sentence
                if refused is not None
                else check_platform_spend(session) or check_spend(session, organization)
            )
        reported = automation.report and refusal is None
        named = (landed or partners)[:ARRIVALS_NAMED]
        names = dict(
            session.execute(
                select(Document.id, Document.filename).where(Document.id.in_(named))
            ).all()
        )
        trigger = {
            "automation": automation.title,
            "documents": landed,
            "partners": partners,
            "names": [names[document_id] for document_id in named if document_id in names],
            # What it puts in review once the run settles, as it said when it ran.
            "review": automation.review,
            "assignee_id": automation.assignee_id,
        }
        run = start_run(
            session, project, None, checks, report=reported, automation=automation, trigger=trigger
        )
        if refusal is not None:
            log.info("The report of run %s was not asked for: %s", run.id, refusal)
            run.report_status, run.report_error = "failed", refusal
        if reported:
            # The plan's measure of the agent's runs, as a member's report counts.
            record(
                session,
                org_id=organization.id,
                project_id=project.id,
                user_id=None,
                kind=ANSWER,
                measures={"questions": 1},
            )
            note_quota(session, organization, MONTH_QUESTIONS, app_address())
        audit_event(
            session,
            "cross_checks_run",
            org_id=organization.id,
            project_id=project.id,
            target=("workflow_run", run.id),
            detail={
                "names": [check.title for check in checks],
                "report": reported,
                "automation": automation.title,
            },
        )
        _commit(session, lease)
    return None


def work_out(run_id: int, lease: Lease | None = None) -> None:
    """Work each of the run's rules out over the project's documents as they read now and keep
    what it finds, committed with the lease renewed. A run worked out already is left as it is;
    one a lost worker was working out is started over."""
    with SessionLocal() as session:
        run = session.get(WorkflowRun, run_id)
        if run is None or run.status not in ("queued", "running"):
            # Deleted, worked out already, or failed.
            return
        if run.status == "queued":
            # Said at once, for the page following the run; a lost run is started over from here.
            run.status = "running"
            _commit(session, lease)
        # The length is the project's: a rule over thousands of lines takes seconds of pairing
        # between its reads, and every rule's findings are written in this one transaction.
        unbounded(session)
        project = session.get(Project, run.project_id)
        if project.deleted_at is not None:
            run.status, run.error, run.finished_at = "failed", PROJECT_GONE, datetime.now(UTC)
            if run.report_status in ("queued", "writing"):
                run.report_status, run.report_error = "failed", PROJECT_GONE
            _commit(session, lease)
            return
        session.execute(delete(WorkflowFinding).where(WorkflowFinding.run_id == run_id))
        templates = templates_for(session, run.project_id)
        rules = []
        for index, kept in enumerate(run.rules):
            rule = rule_from(kept)
            sides = sides_of(rule, templates)
            if isinstance(sides, str):
                rules.append({**kept, "problem": sides, "counts": None})
                continue
            found = findings_of(session, run.project_id, rule, sides)
            _keep(session, run_id, index, found)
            counted = Counter(finding.outcome for finding in found)
            rules.append(
                {
                    **kept,
                    "left_type": sides.left_field.type,
                    "right_type": sides.right_field.type,
                    "problem": None,
                    "counts": {outcome: counted[outcome] for outcome in OUTCOMES},
                }
            )
        run.rules = rules
        run.status, run.finished_at = "done", datetime.now(UTC)
        _commit(session, lease)


def _keep(session: Session, run_id: int, rule: int, found: list[Finding]) -> None:
    """Keep what a rule found as it was found, in the order the rule lists it."""
    rows = [
        {
            "run_id": run_id,
            "rule": rule,
            "seq": seq,
            "outcome": finding.outcome,
            "reason": finding.reason,
            "document_id": finding.document.id,
            "title": finding.document.display_title,
            "filename": finding.document.filename,
            "left": _fact(finding.left, finding.document) if finding.left else None,
            "right": [_fact(fact, _holder(fact, finding.partners)) for fact in finding.right],
            "partners": [
                {"document_id": partner.id, "title": partner.display_title}
                for partner in finding.partners
            ],
            "difference": finding.difference,
        }
        for seq, finding in enumerate(found)
    ]
    for start in range(0, len(rows), WRITE_BATCH):
        session.execute(insert(WorkflowFinding), rows[start : start + WRITE_BATCH])


def _fact(fact: Any, document: Document) -> dict[str, Any]:
    return fact_out(fact, document).model_dump(mode="json")


def _holder(fact: Any, partners: tuple[Document, ...]) -> Document:
    return next(partner for partner in partners if partner.id == fact.document_id)


def write_report(run_id: int, services: Services, lease: Lease | None = None) -> None:
    """Have the agent write the run's discrepancy report from its findings, and keep it with what
    it used. Nothing is written while the run is not worked out or no report is asked for."""
    with SessionLocal() as session:
        run = session.get(WorkflowRun, run_id)
        if run is None or run.status != "done" or run.report_status not in ("queued", "writing"):
            return
        project = session.get(Project, run.project_id)
        if project.deleted_at is not None:
            _settle_report(session, run_id, Reply(error=PROJECT_GONE), lease)
            return
        # Whose usage the report's model calls are, read before the rollback below expires the
        # rows: reading one afterwards would take a connection back for the whole run.
        payer = {"org_id": run.org_id, "project_id": run.project_id, "user_id": run.user_id}
        project_id = run.project_id
        over = check_platform_spend(session) or check_spend(session, project.organization)
        run.report_status = "writing"
        _commit(session, lease)
        message, sources = report_input(session, session.get(WorkflowRun, run_id))
        # The reads are done, and the agent loop opens sessions of its own per tool call: no
        # connection is held while the model writes.
        session.rollback()

    usage = ModelUsage()
    events: Generator[dict[str, Any]]
    if over is None:
        events = run_agent(
            services,
            project_id,
            [{"role": "user", "content": message}],
            usage,
            task=REPORT_TASK,
            sources=sources,
        )
    else:
        log.info("The report of run %s was not written: %s", run_id, over)
        events = _only({"type": "error", "message": over})
    reply = Reply(sources=dict(sources))
    try:
        for event in events:
            reply.apply(event)
        with SessionLocal() as session:
            _settle_report(session, run_id, reply, lease)
    finally:
        try:
            events.close()
        finally:
            _charge(usage, run_id, payer)


def _only(event: dict[str, Any]) -> Generator[dict[str, Any]]:
    yield event


def _settle_report(session: Session, run_id: int, reply: Reply, lease: Lease | None) -> None:
    """Keep the report as its run ended, written or failed, committed with the lease renewed.
    A run deleted meanwhile, or whose report is no longer being written, takes nothing."""
    session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id, WorkflowRun.report_status.in_(("queued", "writing")))
        .values(
            report=reply.content,
            report_sources=list(reply.sources.values()),
            report_notice=reply.notice,
            report_error=reply.error,
            report_status="failed" if reply.error else "done",
        )
    )
    _commit(session, lease)


def _charge(usage: ModelUsage, run_id: int, payer: dict[str, int | None]) -> None:
    """Record the report's model calls as the usage of whoever ran the run, in a transaction of
    its own, whichever way its run ended."""
    if not usage.calls:
        return
    try:
        with SessionLocal() as session:
            for model, calls in usage.models.items():
                record(
                    session,
                    **payer,
                    kind=REPORT,
                    measures=calls.measures,
                    model=model,
                    prompt_version=REPORT_PROMPT_VERSION,
                    ref=(WORKFLOW_RUN, run_id),
                )
            session.commit()
    except PoolTimeout:
        log.warning(
            "The usage of run %s's report was not recorded: every database connection is in use",
            run_id,
        )


def _commit(session: Session, lease: Lease | None) -> None:
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def fail_run(run_id: int, error: str | None = None) -> None:
    """End whichever part of the run is under way failed: its checks, or its report."""
    with SessionLocal() as session:
        session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.status.in_(("queued", "running")))
            .values(status="failed", error=error or RUN_FAILED, finished_at=func.now())
        )
        session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.report_status.in_(("queued", "writing")))
            .values(report_status="failed", report_error=error or REPORT_FAILED)
        )
        session.commit()


# The findings of a run about the documents given, by outcome: those of the documents
# themselves, and those whose partners they are.
_ABOUT_DOCUMENTS = """
    f.run_id = :run
    AND (f.document_id = ANY(:ids)
         OR EXISTS (SELECT 1 FROM jsonb_array_elements(f.partners) AS p
                    WHERE (p->>'document_id')::bigint = ANY(:ids)))
"""
_ABOUT = text(
    f"""
    SELECT f.outcome, count(*) AS findings
    FROM workflow_findings AS f
    WHERE {_ABOUT_DOCUMENTS}
    GROUP BY f.outcome
    """
)
# The documents a value of which differs, of those findings.
_DIFFERING = text(
    f"""
    SELECT DISTINCT f.document_id
    FROM workflow_findings AS f
    WHERE {_ABOUT_DOCUMENTS} AND f.outcome = 'differs'
    """
)


def tell_run(run_id: int, lease: Lease | None = None) -> int:
    """Tell what an automation's run found about the documents that arrived for it, once, as the
    run settles: worked out, and its report, when one was asked for, written or failed. To whom
    the automation names, each as they chose for the kind of notice, and to nobody when it found
    nothing about them; returns to how many. First, in the same transaction, the documents it
    puts in review are put there (`_put_in_review`). A member's run, and one told already, tell
    none."""
    with SessionLocal() as session:
        run = session.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())
        if (
            run is None
            or run.trigger is None
            or run.told_at is not None
            or run.status != "done"
            or run.report_status in ("queued", "writing")
        ):
            return 0
        run.told_at = datetime.now(UTC)
        arrived = run.trigger["documents"] + run.trigger["partners"]
        counts = {outcome: 0 for outcome in OUTCOMES}
        for outcome, findings in session.execute(_ABOUT, {"run": run.id, "ids": arrived}):
            counts[outcome] = findings
        _put_in_review(session, run, arrived)
        told = 0
        automation = session.get(Automation, run.automation_id) if run.automation_id else None
        if automation is not None and any(counts.values()):
            told = _tell_run(session, run, automation, counts)
        _commit(session, lease)
        return told


def _put_in_review(session: Session, run: WorkflowRun, arrived: list[int]) -> None:
    """Put in review what the automation said it would when it ran: the documents its findings
    show a value of to differ, of those that arrived or whose partners did (an invoice whose
    order came later among them), or every one it watches that arrived."""
    review = run.trigger.get("review")
    if review is None:
        return
    if review == "differs":
        chosen = list(session.scalars(_DIFFERING, {"run": run.id, "ids": arrived}))
    else:
        chosen = list(run.trigger["documents"])
    if chosen:
        put_in_review(
            session,
            session.get(Project, run.project_id),
            chosen,
            run.trigger.get("assignee_id"),
            run.trigger["automation"],
            app_address(),
        )


def _tell_run(
    session: Session,
    run: WorkflowRun,
    automation: Automation,
    counts: dict[str, int],
) -> int:
    project = session.get(Project, run.project_id)
    organization = project.organization
    page = RUN_PAGE.format(project.id, run.id)
    url = app_address()
    told = 0
    for user in audience_of(session, project, automation.audience, automation.created_by):
        content = checks_run_email(
            user.name,
            organization.name,
            project.name,
            run.trigger["automation"],
            len(run.trigger["documents"]),
            len(run.trigger["partners"]),
            run.trigger["names"],
            counts,
            run.report_status,
            run.report_error,
            in_organization(f"{url}{page}", organization.id),
        )
        told += tell(session, user, organization.id, CHECKS_RUN, content, page, url)
    return told


def abandon_run(run_id: int, attempts: int) -> None:
    """Every run of the job was lost to a dying process: the part under way ends failed, for a
    member to ask again."""
    fail_run(
        run_id,
        f"It stopped unexpectedly {attempts} times and was not tried again. Try again in a moment.",
    )


def report_input(session: Session, run: WorkflowRun) -> tuple[str, dict[int, dict[str, Any]]]:
    """What the report is written from: the run's rules and what each found, in the server's
    words, the values that differ and those that could not be checked listed (REPORT_FINDINGS at
    most, those that differ first) with the references of the passages they were read from;
    and those passages, as the agent's sources, the ones still in the project out of the trash.
    """
    listed = _listed(session, run.id)
    passages = sources_of(session, run.project_id, _cited(listed))
    lines = [
        f"A run of the project's cross-checks, made {run.created_at:%d %B %Y at %H:%M} UTC over "
        "the documents as they were read then.",
    ]
    shown = Counter((finding.rule, finding.outcome) for finding in listed)
    for index, kept in enumerate(run.rules):
        lines += ["", f"Rule {index + 1}: {kept['title']}", f"Checks: {describe_rule(kept)}"]
        if kept.get("problem"):
            lines.append(f"Not worked out: {kept['problem']}")
            continue
        counts = kept.get("counts") or {}
        lines.append(
            f"Found: {counts.get('differs', 0)} differing, {counts.get('missing', 0)} that could "
            f"not be checked, {counts.get('holds', 0)} holding."
        )
        for finding in listed:
            if finding.rule == index:
                lines.append(f"- {_finding_line(finding, kept, passages)}")
        for outcome, words in (("differs", "differing"), ("missing", "that could not be checked")):
            if left := counts.get(outcome, 0) - shown[(index, outcome)]:
                lines.append(f"- And {left} more {words}, not listed.")
    return "\n".join(lines), passages


def describe_rule(kept: dict[str, Any]) -> str:
    """What a rule compares, in the terse words the report's input gives the model."""
    margin = f" (margin {kept['margin']:g})" if kept.get("margin") else ""
    how = {
        "equal": "equals",
        "at_most": "is at most",
        "at_least": "is at least",
        "same_words": "says the same as",
        "same_numbers": "states the same numbers as",
    }[kept["comparison"]]
    left = f"each {kept['left_kind']}'s {kept['left_field']}"
    if kept["pairing"] == "same":
        right = f"its own {kept['right_field']}"
    elif kept["pairing"] == "key":
        right = (
            f"the {kept['right_field']} of the {kept['right_kind']} documents whose "
            f"{kept['right_key']} is its {kept['left_key']}"
        )
    else:
        right = f"the {kept['right_field']} of every {kept['right_kind']}"
    ways = [
        "added up" if kept.get("summed") else "",
        "line by line" if kept.get("by_item") else "",
    ]
    extra = ", ".join(way for way in ways if way)
    return f"{left} {how}{margin} {right}{f', {extra}' if extra else ''}."


def findings_about(
    session: Session, run: WorkflowRun, ids: list[int]
) -> tuple[list[str], dict[int, dict[str, Any]]]:
    """What the run found about the documents, for a draft's input (app/drafts.py): those of its
    findings that differ or could not be checked whose document, or a partner, is one of them,
    in the server's words as a report's input gives them, by rule, at most REPORT_FINDINGS; with
    the passages their values were read from, as the agent's sources. Nothing for none."""
    listed = _listed(session, run.id, about=ids)
    if not listed:
        return [], {}
    passages = sources_of(session, run.project_id, _cited(listed))
    lines = [
        f"What a run of the project's cross-checks, made {run.created_at:%d %B %Y at %H:%M} UTC "
        "over the documents as they were read then, found about them:"
    ]
    for index, kept in enumerate(run.rules):
        mine = [finding for finding in listed if finding.rule == index]
        if mine:
            lines += ["", f"Rule: {kept['title']}", f"Checks: {describe_rule(kept)}"]
            lines += [f"- {_finding_line(finding, kept, passages)}" for finding in mine]
    if len(listed) == REPORT_FINDINGS:
        lines += ["", "It found more about them, not listed."]
    return lines, passages


def latest_run_about(session: Session, project_id: int, ids: list[int]) -> WorkflowRun | None:
    """The project's newest run worked out with a finding about one of the documents, of any
    outcome, as a draft about them reads it; None for none."""
    if not ids:
        return None
    return session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.status == "done",
            exists().where(WorkflowFinding.run_id == WorkflowRun.id, _about(ids)),
        )
        .order_by(WorkflowRun.id.desc())
        .limit(1)
    )


def _about(ids: list[int]) -> Any:
    """That a finding is about one of the documents: its own, or one of its partners'."""
    partner = text(
        "EXISTS (SELECT 1 FROM jsonb_array_elements(workflow_findings.partners) AS p "
        "WHERE (p->>'document_id')::bigint = ANY(:about))"
    ).bindparams(about=ids)
    return or_(WorkflowFinding.document_id.in_(ids), partner)


def _listed(session: Session, run_id: int, about: list[int] | None = None) -> list[WorkflowFinding]:
    """The findings the report's input lists: those that differ, then those missing what they
    need, each rule's in its order, at most REPORT_FINDINGS; those about the documents given
    alone, when given."""
    query = select(WorkflowFinding).where(
        WorkflowFinding.run_id == run_id, WorkflowFinding.outcome != "holds"
    )
    if about is not None:
        query = query.where(_about(about))
    return list(
        session.scalars(
            query.order_by(
                (WorkflowFinding.outcome != "differs"), WorkflowFinding.rule, WorkflowFinding.seq
            ).limit(REPORT_FINDINGS)
        )
    )


def _cited(listed: list[WorkflowFinding]) -> set[int]:
    """The passages the listed findings' values were read from."""
    return {
        fact["passage_id"]
        for finding in listed
        for fact in ([finding.left] if finding.left else []) + finding.right
        if fact.get("passage_id")
    }


def sources_of(session: Session, project_id: int, wanted: set[int]) -> dict[int, dict[str, Any]]:
    """The passages wanted, as the agent's sources, that are still in the project out of the
    trash: a document read since may have others."""
    if not wanted:
        return {}
    # The columns alone: a passage's search vector and embedding are no part of a source.
    found = session.execute(
        select(
            Passage.id,
            Passage.document_id,
            func.coalesce(func.nullif(Document.title, ""), Document.filename),
            Passage.context,
            Passage.page_start,
            Passage.page_end,
            func.left(Passage.text, 280),
        )
        .join(Document, Document.id == Passage.document_id)
        .where(
            Passage.id.in_(wanted),
            Document.project_id == project_id,
            Document.deleted_at.is_(None),
        )
    ).all()
    keys = ("id", "document_id", "document_title", "context", "page_start", "page_end", "excerpt")
    return {row[0]: dict(zip(keys, row, strict=True)) for row in found}


def _finding_line(
    finding: WorkflowFinding, kept: dict[str, Any], passages: dict[int, dict[str, Any]]
) -> str:
    """One finding for the report's input: its document, its outcome, the values on both sides
    with their references, and how far apart they are or why it could not be checked."""
    parts = [f'"{finding.title}"']
    if finding.left:
        parts.append(f"{kept['left_field']} {_value(finding.left, passages)}")
    if finding.right:
        theirs = "; ".join(
            f'"{fact["document_title"]}" {kept["right_field"]} {_value(fact, passages)}'
            for fact in finding.right
        )
        parts.append(f"compared with {theirs}")
    if finding.outcome == "differs" and finding.reason != CURRENCY:
        unit = " days" if kept.get("left_type") == "date" else ""
        if finding.difference is not None:
            parts.append(f"difference {finding.difference:g}{unit} (checked less the other)")
        return "Differs: " + ", ".join(parts) + "."
    reason = _MISSING.get(finding.reason, "")
    if reason:
        parts.append(reason.format(**{**kept, "left_key": kept.get("left_key") or ""}))
    label = "Differs" if finding.outcome == "differs" else "Could not be checked"
    return f"{label}: " + ", ".join(parts) + "."


def _value(fact: dict[str, Any], passages: dict[int, dict[str, Any]]) -> str:
    value = fact["value"]
    if len(value) > VALUE_CHARS:
        value = value[: VALUE_CHARS - 1] + "…"
    corrected = (
        " (as a member corrected it)" if (fact.get("check") or {}).get("corrected_from") else ""
    )
    where = (
        f" [P{fact['passage_id']}]"
        if fact.get("passage_id") in passages
        else (f" (page {fact['page']})" if fact.get("page") else "")
    )
    return f'"{value}"{corrected}{where}'


def _type_of(by_kind: dict[str, Template], kind: str, name: str) -> str | None:
    template = by_kind.get(kind)
    field = template.field(name) if template is not None else None
    return field.type if field is not None else None


def gone_documents(session: Session, project_id: int, ids: Iterable[int]) -> list[int]:
    """Of the documents a run's findings name, those deleted since or in the trash now."""
    wanted = set(ids)
    if not wanted:
        return []
    live = set(
        session.scalars(
            select(Document.id).where(
                Document.id.in_(wanted),
                Document.project_id == project_id,
                Document.deleted_at.is_(None),
            )
        )
    )
    return sorted(wanted - live)
