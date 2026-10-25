"""A project's filing rules (app/filing.py): where its new documents go, and the tags they are
given, once each is read for its type and facts.

Anyone who reads the project reads them; whoever may change it makes, changes, turns off and
deletes them, each change a row of the organisation's audit log naming what the rule looks at and
what it does, by the type, the field, the folder's path and the tag's name, never a value read
from a document. A rule is written whole and deleted for good, with no trash.
"""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    EditableFilingRuleDep,
    EditableProjectDep,
    ProjectDep,
    SessionDep,
)
from app.api.folders import resolve_folder
from app.audit import audit
from app.filing import MAX_FILING_RULES, TOO_MANY, problem_of, project_rules
from app.folders import folder_paths
from app.models import FilingRule, Folder, Project, Tag, User
from app.schemas import FilingRuleIn, FilingRuleOut, FilingRulesOut
from app.tags import NOT_THE_PROJECTS

router = APIRouter(tags=["filing rules"])

NO_TYPE = "Say which type of document the rule files: the type the model gives it, such as invoice."
NO_FIELD = "Say which fact the rule reads, by its field's name, and the words its value holds."
NOTHING_TO_HOLD = "A rule for every document looks at nothing in it. Leave out the field and words."
NO_FOLDER = "Choose the folder the rule moves documents into."
NO_TAG = "Choose the tag the rule puts on documents."
TAG_ONLY = "A rule that tags moves nothing. Leave out the folder."
MOVE_ONLY = "A rule that moves documents puts no tag on them. Leave out the tag."

# What of a rule a change may change, as the audit log names it, by its columns.
PARTS = {
    "condition": ("condition", "field", "value"),
    "action": ("action", "folder_id", "tag_id"),
    "enabled": ("enabled",),
}


@router.get("/projects/{project_id}/filing-rules", response_model=FilingRulesOut)
def list_filing_rules(project: ProjectDep, session: SessionDep):
    """The project's filing rules in the order they apply, each with why it would file nothing
    as the project stands now, if it would not."""
    rules = project_rules(session, project.id)
    return FilingRulesOut(
        rules=_outs(session, project.id, rules), max_filing_rules=MAX_FILING_RULES
    )


@router.post("/projects/{project_id}/filing-rules", response_model=FilingRuleOut, status_code=201)
def create_filing_rule(
    project: EditableProjectDep,
    body: FilingRuleIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Make a filing rule of the project's, which files what arrives from now on; it applies
    after the rules made before it."""
    columns = _written(session, project, body)
    # The project's row, so that two made at once cannot pass its ceiling.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    held = session.scalar(select(func.count()).where(FilingRule.project_id == project.id))
    if held >= MAX_FILING_RULES:
        raise HTTPException(409, TOO_MANY)
    row = FilingRule(
        org_id=project.org_id, project_id=project.id, created_by=account.user.id, **columns
    )
    session.add(row)
    session.flush()
    audit(
        session,
        account,
        request,
        "filing_rule_created",
        project_id=project.id,
        target=("filing_rule", row.id),
        detail=_named(session, row),
    )
    session.commit()
    return _outs(session, project.id, [row])[0]


@router.put("/filing-rules/{rule_id}", response_model=FilingRuleOut)
def update_filing_rule(
    row: EditableFilingRuleDep,
    body: FilingRuleIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the rule anew, whole: what arrives from now on is filed as it says."""
    project = session.get(Project, row.project_id)
    columns = _written(session, project, body)
    changed = [
        part
        for part, names in PARTS.items()
        if any(getattr(row, name) != columns[name] for name in names)
    ]
    if changed:
        for name, value in columns.items():
            setattr(row, name, value)
        audit(
            session,
            account,
            request,
            "filing_rule_changed",
            project_id=row.project_id,
            target=("filing_rule", row.id),
            detail={**_named(session, row), "changed": changed},
        )
    session.commit()
    return _outs(session, row.project_id, [row])[0]


@router.delete("/filing-rules/{rule_id}", status_code=204)
def delete_filing_rule(
    row: EditableFilingRuleDep, session: SessionDep, account: AccountDep, request: Request
):
    """Delete the rule for good: what it filed stays where it is."""
    audit(
        session,
        account,
        request,
        "filing_rule_deleted",
        project_id=row.project_id,
        target=("filing_rule", row.id),
        detail=_named(session, row),
    )
    session.delete(row)
    session.commit()


def _written(session: Session, project: Project, body: FilingRuleIn) -> dict:
    """The rule's columns as they are kept; refused for a condition or action missing what it
    needs or given what it has no use for, a folder that is not the project's or is in the
    trash, and a tag that is not the project's."""
    if body.condition == "type" and (body.value is None or body.field is not None):
        raise HTTPException(400, NO_TYPE)
    if body.condition == "fact" and (body.field is None or body.value is None):
        raise HTTPException(400, NO_FIELD)
    if body.condition == "any" and (body.field is not None or body.value is not None):
        raise HTTPException(400, NOTHING_TO_HOLD)
    if body.action == "folder" and body.folder_id is None:
        raise HTTPException(400, NO_FOLDER)
    if body.action == "tag" and body.tag_id is None:
        raise HTTPException(400, NO_TAG)
    if body.action == "tag" and body.folder_id is not None:
        raise HTTPException(400, TAG_ONLY)
    if body.action != "tag" and body.tag_id is not None:
        raise HTTPException(400, MOVE_ONLY)
    resolve_folder(session, project, body.folder_id)
    if body.tag_id is not None and not session.scalar(
        select(Tag.id).where(Tag.id == body.tag_id, Tag.project_id == project.id)
    ):
        raise HTTPException(404, NOT_THE_PROJECTS)
    return {
        "condition": body.condition,
        "field": body.field,
        # The model writes a document's type in lowercase, so the rule looks for it so.
        "value": body.value.lower() if body.condition == "type" else body.value,
        "action": body.action,
        "folder_id": body.folder_id,
        "tag_id": body.tag_id,
        "enabled": body.enabled,
    }


def _named(session: Session, row: FilingRule) -> dict:
    """What the audit log keeps of a rule: what it looks at, by the type or the field, and what
    it does, by the folder's path and the tag's name; never the words a fact's value holds,
    which may be a document's."""
    folder = folder_paths(session, row.project_id, trashed=True).get(row.folder_id)
    tag = session.scalar(select(Tag.name).where(Tag.id == row.tag_id)) if row.tag_id else None
    return {
        "condition": row.condition,
        "type": row.value if row.condition == "type" else None,
        "field": row.field,
        "action": row.action,
        "folder": folder,
        "tag": tag,
        "enabled": row.enabled,
    }


def _outs(session: Session, project_id: int, rows: list[FilingRule]) -> list[FilingRuleOut]:
    """The rules as the page shows them, the project's folders, tags and makers read once."""
    if not rows:
        return []
    paths = folder_paths(session, project_id, trashed=True)
    folders = {
        folder.id: folder
        for folder in session.scalars(
            select(Folder).where(Folder.id.in_({row.folder_id for row in rows} - {None}))
        )
    }
    tags = dict(
        session.execute(
            select(Tag.id, Tag.name).where(Tag.id.in_({row.tag_id for row in rows} - {None}))
        ).all()
    )
    makers = {row.created_by for row in rows} - {None}
    names = dict(session.execute(select(User.id, User.name).where(User.id.in_(makers))).all())
    return [
        FilingRuleOut(
            id=row.id,
            condition=row.condition,
            field=row.field,
            value=row.value,
            action=row.action,
            folder_id=row.folder_id,
            tag_id=row.tag_id,
            enabled=row.enabled,
            folder=paths.get(row.folder_id) if row.folder_id is not None else None,
            tag=tags.get(row.tag_id),
            problem=problem_of(row, folders.get(row.folder_id)),
            made_by=names.get(row.created_by),
            created_at=row.created_at,
        )
        for row in rows
    ]
