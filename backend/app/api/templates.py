"""A project's extraction templates (app/extraction.py): the fields its documents of each kind are
read for, which the library's Templates page lists, the built-in ones among them, and changes;
and the facts table of each kind's documents (app/fact_table.py), a column for each field.

Every route is behind the flag `extraction_templates` (app/flags.py), answering an organisation it
is off for as a path no route takes. Whoever may change the project changes its templates, each
change a row of the organisation's audit log; a template is written whole, and deleted for good,
with no trash, since what it holds is the members' choice of fields and no document. A change
applies to the documents indexed after it: "Index again" from summaries and facts reads the
others again.
"""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    EditableProjectDep,
    EditableTemplateDep,
    ProjectDep,
    SessionDep,
    flagged,
    limited,
)
from app.api.documents import fact_out
from app.audit import audit
from app.extraction import (
    BUILT_IN_TYPES,
    MAX_TEMPLATES,
    Template,
    kind_of,
    template_of,
    templates_for,
)
from app.fact_table import TableRefusal, condition_of, read_table
from app.flags import EXTRACTION_TEMPLATES
from app.indexing.enrich import normalize_fact_name
from app.models import Document, ExtractionTemplate, Project
from app.ratelimit import SEARCHES
from app.schemas import (
    FactRowOut,
    FactTableOut,
    ProjectTemplatesOut,
    TemplateFieldOut,
    TemplateIn,
    TemplateOut,
)

router = APIRouter(tags=["templates"], dependencies=[Depends(flagged(EXTRACTION_TEMPLATES))])

TOO_MANY = (
    f"A project has at most {MAX_TEMPLATES} templates of its own. Delete one it no longer needs "
    "before adding another."
)

# The facts table's rows a page, and the conditions one request may hold.
TABLE_ROWS = 100
MAX_TABLE_ROWS = 200
MAX_CONDITIONS = 10


@router.get("/projects/{project_id}/templates", response_model=ProjectTemplatesOut)
def list_templates(project: ProjectDep, session: SessionDep):
    """The templates in force for the project, its own first, each with how many of its
    documents were last read for it."""
    read_for = _documents_read_for(session, project.id)
    updated = dict(
        session.execute(
            select(ExtractionTemplate.id, ExtractionTemplate.updated_at).where(
                ExtractionTemplate.project_id == project.id
            )
        ).all()
    )
    return ProjectTemplatesOut(
        templates=[
            _out(template, read_for.get(template.doc_type, 0), updated.get(template.id))
            for template in templates_for(session, project.id)
        ],
        max_templates=MAX_TEMPLATES,
    )


@router.get(
    "/projects/{project_id}/fact-table",
    response_model=FactTableOut,
    dependencies=[Depends(limited(SEARCHES))],
)
def fact_table(
    project: ProjectDep,
    session: SessionDep,
    kind: str,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "asc",
    conditions: Annotated[list[str] | None, Query(alias="filter")] = None,
    q: str = "",
    after: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TABLE_ROWS)] = TABLE_ROWS,
):
    """A page of the facts table of the project's documents of one kind (app/fact_table.py):
    a row for each document read for the kind's template, a column for each of its fields,
    sorted by one field (`sort`; the document's name without one), the rows kept by each
    `filter` (`field:has`, `field:lacks`, `field:contains:words`, `field:min:value`,
    `field:max:value`) and the words `q`, in the document's name or its facts."""
    doc_type = kind_of(kind)
    template = next((t for t in templates_for(session, project.id) if t.doc_type == doc_type), None)
    if template is None:
        raise HTTPException(404, f"This project has no template for {doc_type} documents.")
    if len(conditions or []) > MAX_CONDITIONS:
        raise HTTPException(422, f"A table takes at most {MAX_CONDITIONS} conditions at once.")
    sorted_by = None
    if sort:
        sorted_by = template.field(sort)
        if sorted_by is None:
            raise HTTPException(422, f"The {template.title} template has no field named {sort}.")
    try:
        table = read_table(
            session,
            project.id,
            template,
            sort=sorted_by,
            order=order,
            conditions=[condition_of(template, written) for written in conditions or []],
            words=q,
            after=after,
            limit=limit,
        )
    except TableRefusal as refusal:
        raise HTTPException(422, str(refusal)) from None
    updated_at = (
        session.scalar(
            select(ExtractionTemplate.updated_at).where(ExtractionTemplate.id == template.id)
        )
        if template.id is not None
        else None
    )
    documents = _documents_read_for(session, project.id).get(template.doc_type, 0)
    return FactTableOut(
        template=_out(template, documents, updated_at),
        rows=[
            FactRowOut(
                document_id=row.document.id,
                title=row.document.display_title,
                filename=row.document.filename,
                cells={
                    name: [fact_out(fact, row.document) for fact in facts]
                    for name, facts in row.cells.items()
                },
            )
            for row in table.rows
        ],
        total=table.total,
        next_after=table.next_after,
    )


@router.post("/projects/{project_id}/templates", response_model=TemplateOut, status_code=201)
def create_template(
    project: EditableProjectDep,
    body: TemplateIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Make a template of the project's own: for a kind the built-in ones are for, it replaces
    the built-in one."""
    doc_type, fields = _written(body)
    _hold(session, project.id)
    held = session.scalar(select(func.count()).where(ExtractionTemplate.project_id == project.id))
    if held >= MAX_TEMPLATES:
        raise HTTPException(409, TOO_MANY)
    _refuse_taken(session, project.id, doc_type)
    row = ExtractionTemplate(
        org_id=project.org_id,
        project_id=project.id,
        doc_type=doc_type,
        title=body.title,
        description=body.description,
        fields=fields,
        created_by=account.user.id,
    )
    session.add(row)
    session.flush()
    audit(
        session,
        account,
        request,
        "template_created",
        project_id=project.id,
        target=("template", row.id),
        detail={"name": row.title, "doc_type": doc_type, "fields": _names(fields)},
    )
    session.commit()
    return _row_out(session, row)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    row: EditableTemplateDep,
    body: TemplateIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the template anew, whole: its kind, name, description and fields."""
    doc_type, fields = _written(body)
    _hold(session, row.project_id)
    if doc_type != row.doc_type:
        _refuse_taken(session, row.project_id, doc_type)
    before = {field["name"]: field for field in row.fields}
    after = {field["name"]: field for field in fields}
    detail: dict[str, object] = {
        "name": body.title,
        "doc_type": doc_type,
        "added": [name for name in after if name not in before],
        "removed": [name for name in before if name not in after],
        "changed": [name for name in after if name in before and after[name] != before[name]],
    }
    if doc_type != row.doc_type:
        detail |= {"from": row.doc_type, "to": doc_type}
    same = (
        doc_type == row.doc_type
        and body.title == row.title
        and body.description == row.description
        and fields == row.fields
    )
    if not same:
        row.doc_type = doc_type
        row.title = body.title
        row.description = body.description
        row.fields = fields
        audit(
            session,
            account,
            request,
            "template_changed",
            project_id=row.project_id,
            target=("template", row.id),
            detail=detail,
        )
    session.commit()
    return _row_out(session, row)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    row: EditableTemplateDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Delete the template for good; for a kind the built-in ones are for, the built-in one
    applies again."""
    audit(
        session,
        account,
        request,
        "template_deleted",
        project_id=row.project_id,
        target=("template", row.id),
        detail={"name": row.title, "doc_type": row.doc_type},
    )
    session.delete(row)
    session.commit()


def _written(body: TemplateIn) -> tuple[str, list[dict]]:
    """The template's kind and fields as they are kept: the kind in lowercase words, each field's
    name as its facts are named (`normalize_fact_name`), its examples once each."""
    doc_type = kind_of(body.doc_type)
    if not doc_type:
        raise HTTPException(400, "Name the kind of document the template is for.")
    fields: list[dict] = []
    for field in body.fields:
        name = normalize_fact_name(field.name)
        if not name:
            raise HTTPException(
                400, f"The field “{field.name}” needs a letter or a digit in its name."
            )
        if any(kept["name"] == name for kept in fields):
            raise HTTPException(
                400, f"Two fields are named {name}. Give each field a name of its own."
            )
        fields.append(
            {
                "name": name,
                "type": field.type,
                "description": field.description,
                "required": field.required,
                "examples": list(dict.fromkeys(field.examples)),
            }
        )
    return doc_type, fields


def _hold(session: Session, project_id: int) -> None:
    """Hold the project's row, so that two templates of one kind cannot be made at once."""
    session.execute(select(Project.id).where(Project.id == project_id).with_for_update())


def _refuse_taken(session: Session, project_id: int, doc_type: str) -> None:
    taken = session.scalar(
        select(ExtractionTemplate.title).where(
            ExtractionTemplate.project_id == project_id, ExtractionTemplate.doc_type == doc_type
        )
    )
    if taken is not None:
        raise HTTPException(
            409,
            f"This project's template “{taken}” is for {doc_type} documents already. Change "
            "that one, or name another kind.",
        )


def _names(fields: list[dict]) -> list[str]:
    return [field["name"] for field in fields]


def _documents_read_for(session: Session, project_id: int) -> dict[str, int]:
    """How many of the project's documents out of the trash were last read for each template."""
    return dict(
        session.execute(
            select(Document.template, func.count())
            .where(
                Document.project_id == project_id,
                Document.deleted_at.is_(None),
                Document.template.is_not(None),
            )
            .group_by(Document.template)
        ).all()
    )


def _row_out(session: Session, row: ExtractionTemplate) -> TemplateOut:
    read_for = _documents_read_for(session, row.project_id)
    return _out(template_of(row), read_for.get(row.doc_type, 0), row.updated_at)


def _out(template: Template, documents: int, updated_at: dt.datetime | None) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        doc_type=template.doc_type,
        title=template.title,
        description=template.description,
        fields=[
            TemplateFieldOut(
                name=field.name,
                type=field.type,
                description=field.description,
                required=field.required,
                examples=list(field.examples),
            )
            for field in template.fields
        ],
        built_in=template.id is None,
        replaces_built_in=template.id is not None and template.doc_type in BUILT_IN_TYPES,
        documents=documents,
        updated_at=updated_at,
    )
