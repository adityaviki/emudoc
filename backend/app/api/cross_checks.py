"""A project's cross-checks (app/cross_checks.py): the rules its documents are checked against
one another by, which the library's Cross-checks view lists with what each finds, offering the
suggested ones the project's templates allow, and what a rule finds, a finding a value of a
checked document with the values it was compared with, each citing where it was found.

A rule compares the fields of the project's templates. Whoever may change the project makes, changes
and deletes its rules, each change a row of the organisation's audit log naming the kinds and
fields, never a value; a rule is written whole and deleted for good, with no trash, since it holds
the members' choice and no document.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    CrossCheckDep,
    EditableCrossCheckDep,
    EditableProjectDep,
    ProjectDep,
    SessionDep,
    limited,
)
from app.audit import audit
from app.cross_checks import (
    MAX_CROSS_CHECKS,
    FindingsRefusal,
    Rule,
    Sides,
    Suggestion,
    counts_of,
    read_findings,
    rule_of,
    sides_of,
    suggestions_for,
)
from app.extraction import Template, kind_of, templates_for
from app.fact_checks import fact_out
from app.indexing.enrich import normalize_fact_name
from app.models import CrossCheck, Document, Fact, Project
from app.ratelimit import SEARCHES
from app.schemas import (
    CrossCheckIn,
    CrossCheckOut,
    CrossChecksOut,
    CrossCheckSuggestionOut,
    FindingCountsOut,
    FindingOut,
    FindingOutcome,
    FindingsOut,
    PartnerOut,
)

router = APIRouter(tags=["cross-checks"])

TOO_MANY = (
    f"A project has at most {MAX_CROSS_CHECKS} cross-checks. Delete one it no longer needs before "
    "adding another."
)

# A rule's findings a page.
FINDINGS_SHOWN = 50
MAX_FINDINGS_SHOWN = 200

# What of a rule a change may change, as the audit log names it.
PARTS = {
    "title": ("title",),
    "left": ("left_kind", "left_field"),
    "right": ("right_kind", "right_field"),
    "pairing": ("pairing", "left_key", "right_key"),
    "by_item": ("by_item",),
    "comparison": ("comparison", "margin", "summed"),
}


@router.get("/projects/{project_id}/cross-checks", response_model=CrossChecksOut)
def list_cross_checks(project: ProjectDep, session: SessionDep):
    """The project's cross-checks by name, each with how many of its findings differ, miss a
    value and hold, and the suggested rules its templates allow that it has none of."""
    templates = templates_for(session, project.id)
    rows = session.scalars(
        select(CrossCheck)
        .where(CrossCheck.project_id == project.id)
        .order_by(CrossCheck.title, CrossCheck.id)
    ).all()
    checks = []
    for row in rows:
        rule = rule_of(row)
        sides = sides_of(rule, templates)
        counts = counts_of(session, project.id, rule, sides) if isinstance(sides, Sides) else None
        checks.append(_out(row, templates, counts))
    return CrossChecksOut(
        checks=checks,
        suggestions=[
            _suggestion_out(suggestion, templates)
            for suggestion in suggestions_for(templates, [rule_of(row) for row in rows])
        ],
        max_checks=MAX_CROSS_CHECKS,
    )


@router.get(
    "/cross-checks/{check_id}/findings",
    response_model=FindingsOut,
    dependencies=[Depends(limited(SEARCHES))],
)
def findings(
    row: CrossCheckDep,
    session: SessionDep,
    outcome: FindingOutcome | None = None,
    after: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_FINDINGS_SHOWN)] = FINDINGS_SHOWN,
):
    """A page of what the rule finds in its project now, those of one outcome or all, those that
    differ first, then those missing a value, then those that hold, each by its document's name;
    none while the rule cannot be worked out with the project's templates as they are."""
    templates = templates_for(session, row.project_id)
    rule = rule_of(row)
    sides = sides_of(rule, templates)
    if not isinstance(sides, Sides):
        return FindingsOut(
            check=_out(row, templates, None),
            outcome=outcome,
            findings=[],
            total=0,
            next_after=None,
        )
    try:
        page = read_findings(
            session, row.project_id, rule, sides, outcome=outcome, after=after, limit=limit
        )
    except FindingsRefusal as refusal:
        raise HTTPException(422, str(refusal)) from None
    return FindingsOut(
        check=_out(row, templates, page.counts),
        outcome=outcome,
        findings=[
            FindingOut(
                document_id=finding.document.id,
                title=finding.document.display_title,
                filename=finding.document.filename,
                outcome=finding.outcome,
                reason=finding.reason,
                left=fact_out(finding.left, finding.document) if finding.left else None,
                right=[fact_out(fact, _holder(fact, finding.partners)) for fact in finding.right],
                partners=[
                    PartnerOut(document_id=partner.id, title=partner.display_title)
                    for partner in finding.partners
                ],
                difference=finding.difference,
            )
            for finding in page.findings
        ],
        total=page.total,
        next_after=page.next_after,
    )


@router.post("/projects/{project_id}/cross-checks", response_model=CrossCheckOut, status_code=201)
def create_cross_check(
    project: EditableProjectDep,
    body: CrossCheckIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Make a rule the project's documents are checked by."""
    templates = templates_for(session, project.id)
    rule = _rule(body, templates)
    _hold(session, project.id)
    held = session.scalar(select(func.count()).where(CrossCheck.project_id == project.id))
    if held >= MAX_CROSS_CHECKS:
        raise HTTPException(409, TOO_MANY)
    row = CrossCheck(
        org_id=project.org_id,
        project_id=project.id,
        title=body.title,
        **_written(rule),
        created_by=account.user.id,
    )
    session.add(row)
    session.flush()
    audit(
        session,
        account,
        request,
        "cross_check_created",
        project_id=project.id,
        target=("cross_check", row.id),
        detail={"name": row.title, **_named(rule)},
    )
    session.commit()
    return _counted_out(session, row, templates)


@router.put("/cross-checks/{check_id}", response_model=CrossCheckOut)
def update_cross_check(
    row: EditableCrossCheckDep,
    body: CrossCheckIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the rule anew, whole."""
    templates = templates_for(session, row.project_id)
    rule = _rule(body, templates)
    after = {"title": body.title, **_written(rule)}
    changed = [
        part
        for part, columns in PARTS.items()
        if any(getattr(row, column) != after[column] for column in columns)
    ]
    if changed:
        for column, value in after.items():
            setattr(row, column, value)
        audit(
            session,
            account,
            request,
            "cross_check_changed",
            project_id=row.project_id,
            target=("cross_check", row.id),
            detail={"name": body.title, **_named(rule), "changed": changed},
        )
    session.commit()
    return _counted_out(session, row, templates)


@router.delete("/cross-checks/{check_id}", status_code=204)
def delete_cross_check(
    row: EditableCrossCheckDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Delete the rule for good: what it found goes with it, being worked out when asked."""
    audit(
        session,
        account,
        request,
        "cross_check_deleted",
        project_id=row.project_id,
        target=("cross_check", row.id),
        detail={"name": row.title, **_named(rule_of(row))},
    )
    session.delete(row)
    session.commit()


def _rule(body: CrossCheckIn, templates: list[Template]) -> Rule:
    """The rule as it is kept: its kinds in lowercase words and its fields named as their facts
    are, keys only for a pairing by key; refused when the project's templates cannot work it
    out."""
    keyed = body.pairing == "key"
    rule = Rule(
        left_kind=kind_of(body.left_kind),
        left_field=normalize_fact_name(body.left_field),
        right_kind=kind_of(body.right_kind),
        right_field=normalize_fact_name(body.right_field),
        pairing=body.pairing,
        left_key=normalize_fact_name(body.left_key or "") if keyed else None,
        right_key=normalize_fact_name(body.right_key or "") if keyed else None,
        by_item=body.by_item,
        comparison=body.comparison,
        margin=body.margin,
        summed=body.summed,
    )
    sides = sides_of(rule, templates)
    if not isinstance(sides, Sides):
        raise HTTPException(400, sides)
    return rule


def _written(rule: Rule) -> dict:
    return {
        "left_kind": rule.left_kind,
        "left_field": rule.left_field,
        "right_kind": rule.right_kind,
        "right_field": rule.right_field,
        "pairing": rule.pairing,
        "left_key": rule.left_key,
        "right_key": rule.right_key,
        "by_item": rule.by_item,
        "comparison": rule.comparison,
        "margin": rule.margin,
        "summed": rule.summed,
    }


def _named(rule: Rule) -> dict:
    """What the audit log keeps of a rule: the kinds and fields it compares and how, never a
    value of a document."""
    named: dict[str, object] = {
        "left": f"{rule.left_kind}.{rule.left_field}",
        "right": f"{rule.right_kind}.{rule.right_field}",
        "comparison": rule.comparison,
    }
    if rule.pairing == "key":
        named["key"] = [rule.left_key, rule.right_key]
    return named


def _hold(session: Session, project_id: int) -> None:
    """Hold the project's row, so that two rules made at once cannot pass its ceiling."""
    session.execute(select(Project.id).where(Project.id == project_id).with_for_update())


def _type_of(templates: list[Template], kind: str, name: str) -> str | None:
    template = next((t for t in templates if t.doc_type == kind), None)
    field = template.field(name) if template is not None else None
    return field.type if field is not None else None


def _holder(fact: Fact, partners: tuple[Document, ...]) -> Document:
    return next(partner for partner in partners if partner.id == fact.document_id)


def _suggestion_out(suggestion: Suggestion, templates: list[Template]) -> CrossCheckSuggestionOut:
    sides = sides_of(suggestion.rule, templates)
    assert isinstance(sides, Sides)
    return CrossCheckSuggestionOut(
        title=suggestion.title,
        **_written(suggestion.rule),
        left_type=sides.left_field.type,
        right_type=sides.right_field.type,
    )


def _counted_out(session: Session, row: CrossCheck, templates: list[Template]) -> CrossCheckOut:
    rule = rule_of(row)
    sides = sides_of(rule, templates)
    counts = counts_of(session, row.project_id, rule, sides) if isinstance(sides, Sides) else None
    return _out(row, templates, counts)


def _out(
    row: CrossCheck, templates: list[Template], counts: dict[str, int] | None
) -> CrossCheckOut:
    rule = rule_of(row)
    sides = sides_of(rule, templates)
    return CrossCheckOut(
        id=row.id,
        title=row.title,
        **_written(rule),
        left_type=_type_of(templates, row.left_kind, row.left_field),
        right_type=_type_of(templates, row.right_kind, row.right_field),
        problem=sides if isinstance(sides, str) else None,
        counts=FindingCountsOut(**counts) if counts is not None else None,
        updated_at=row.updated_at,
    )
