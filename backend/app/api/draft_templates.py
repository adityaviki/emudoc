"""Writing templates (app/draft_templates.py): how the documents of each kind the agent writes into
a member's My files read for the project, its reply emails, dispute letters, summary memos,
comparison tables and reports: the built-in template of each kind, or the project's own in its
place. The agent follows the one in force whenever it writes one in a chat (`get_template`,
app/agent/writing.py).

Anyone who sees the project reads them; whoever may change the project writes and deletes the
project's own, each a row of the organisation's audit log naming the template by its kind and its
parts' names, never what they say.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.deps import AccountDep, EditableProjectDep, ProjectDep, SessionDep
from app.audit import audit
from app.draft_templates import Template, template_of, templates_for
from app.models import DraftTemplate, Project
from app.schemas import (
    DraftKind,
    DraftSectionOut,
    DraftTemplateIn,
    DraftTemplateOut,
    DraftTemplatesOut,
)

router = APIRouter(tags=["writing templates"])

NO_OWN_TEMPLATE = "This project has no template of its own for this kind: the built-in one applies."


@router.get("/projects/{project_id}/draft-templates", response_model=DraftTemplatesOut)
def list_draft_templates(project: ProjectDep, session: SessionDep):
    """The template in force for each kind the agent writes for the project, in the kinds' order:
    its own, or the built-in one."""
    return DraftTemplatesOut(
        templates=[_template_out(template) for template in templates_for(session, project.id)]
    )


@router.put("/projects/{project_id}/draft-templates/{kind}", response_model=DraftTemplateOut)
def save_draft_template(
    project: EditableProjectDep,
    kind: DraftKind,
    body: DraftTemplateIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the project's own template of the kind, whole, in the built-in one's place: its
    tone, its parts in order and what else every document of the kind should do. The next writing
    of the kind follows it."""
    sections = _sections(body)
    # Two first saves of one kind at once would both insert.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    row = session.scalar(
        select(DraftTemplate).where(
            DraftTemplate.project_id == project.id, DraftTemplate.kind == kind
        )
    )
    if row is None:
        row = DraftTemplate(
            org_id=project.org_id,
            project_id=project.id,
            kind=kind,
            tone=body.tone,
            sections=sections,
            guidance=body.guidance,
            created_by=account.user.id,
        )
        session.add(row)
        session.flush()
        _template_audit(
            session,
            account,
            request,
            "draft_template_created",
            row,
            sections=[section["heading"] for section in sections],
        )
    elif (row.tone, row.sections, row.guidance) != (body.tone, sections, body.guidance):
        before = {section["heading"]: section for section in row.sections}
        after = {section["heading"]: section for section in sections}
        _template_audit(
            session,
            account,
            request,
            "draft_template_changed",
            row,
            added=[heading for heading in after if heading not in before],
            removed=[heading for heading in before if heading not in after],
            changed=[
                heading
                for heading in after
                if heading in before and after[heading] != before[heading]
            ],
            tone=row.tone != body.tone,
            guidance=row.guidance != body.guidance,
        )
        row.tone, row.sections, row.guidance = body.tone, sections, body.guidance
    session.commit()
    session.refresh(row)
    return _template_out(template_of(row))


@router.delete("/projects/{project_id}/draft-templates/{kind}", status_code=204)
def delete_draft_template(
    project: EditableProjectDep,
    kind: DraftKind,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Delete the project's own template of the kind for good: the built-in one applies again to
    the next writing, and the files written already stay as they are."""
    row = session.scalar(
        select(DraftTemplate)
        .where(DraftTemplate.project_id == project.id, DraftTemplate.kind == kind)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, NO_OWN_TEMPLATE)
    _template_audit(session, account, request, "draft_template_deleted", row)
    session.delete(row)
    session.commit()


def _sections(body: DraftTemplateIn) -> list[dict[str, Any]]:
    """The template's parts as they are kept, each named once."""
    named: set[str] = set()
    for section in body.sections:
        if section.heading.casefold() in named:
            raise HTTPException(
                400,
                f"Two parts are named “{section.heading}”. Give each part a name of its own.",
            )
        named.add(section.heading.casefold())
    return [section.model_dump() for section in body.sections]


def _template_audit(
    session: Session,
    account: Account,
    request: Request,
    action: str,
    row: DraftTemplate,
    **detail: Any,
) -> None:
    """The audit row of a writing template, named by its kind, its parts by their names and
    whether its tone and the rest it asks changed: what they say is the members' wording."""
    audit(
        session,
        account,
        request,
        action,
        project_id=row.project_id,
        target=("draft_template", row.id),
        detail={"kind": row.kind, **detail},
    )


def _template_out(template: Template) -> DraftTemplateOut:
    return DraftTemplateOut(
        kind=template.kind,
        id=template.id,
        built_in=template.id is None,
        tone=template.tone,
        sections=[
            DraftSectionOut(
                heading=section.heading,
                description=section.description,
                required=section.required,
            )
            for section in template.sections
        ],
        guidance=template.guidance,
        updated_at=template.updated_at,
    )
