"""A project's extraction templates (app/extraction.py): the fields its documents of each kind are
read for, which the library's Templates page lists, the built-in ones among them, and changes;
the facts table of each kind's documents (app/fact_table.py), a column for each field, and the
same as a file to save (app/fact_export.py); and the members' checks and corrections of its facts
(app/fact_checks.py).

Every route is behind the flag `extraction_templates` (app/flags.py), answering an organisation it
is off for as a path no route takes. Whoever may change the project changes its templates, each
change a row of the organisation's audit log; a template is written whole, and deleted for good,
with no trash, since what it holds is the members' choice of fields and no document. A change
applies to the documents indexed after it, and to those of its kind once they are read again
(`read_again`), each reading keeping what it changed of the last one's facts, which the page
lists (`fact_changes`, app/fact_changes.py).
"""

import datetime as dt
import os
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import fact_checks
from app.api.deps import (
    AccountDep,
    EditableFactCheckDep,
    EditableFactDep,
    EditableProjectDep,
    EditableTemplateDep,
    ProjectDep,
    SessionDep,
    WorkerDep,
    flagged,
    limited,
)
from app.audit import audit
from app.extraction import (
    BUILT_IN_TYPES,
    MAX_TEMPLATES,
    Template,
    kind_of,
    template_of,
    templates_for,
)
from app.fact_changes import (
    ChangesRefusal,
    being_read,
    changed_by_kind,
    of_kind,
    read_changes,
)
from app.fact_checks import FACT_GONE, CheckRefused, check_out, fact_out, written
from app.fact_export import MEDIA_TYPES, ExportFormat, csv_file, read_out, workbook_file
from app.fact_table import Ask, TableRefusal, condition_of, read_table
from app.flags import EXTRACTION_TEMPLATES
from app.indexing.enrich import normalize_fact_name
from app.indexing.pipeline import reset_stages
from app.jobs import BULK, queue_index
from app.models import Document, ExtractionTemplate, Fact, FactCheck, Project
from app.ratelimit import EXPORTS, SEARCHES
from app.schemas import (
    DocumentChangesOut,
    FactChangesOut,
    FactCheckIn,
    FactOut,
    FactRowOut,
    FactTableOut,
    FieldChangeOut,
    ProjectTemplatesOut,
    ReadAgainIn,
    ReadAgainOut,
    TemplateFieldOut,
    TemplateIn,
    TemplateOut,
)
from app.storage import content_disposition

router = APIRouter(tags=["templates"], dependencies=[Depends(flagged(EXTRACTION_TEMPLATES))])

TOO_MANY = (
    f"A project has at most {MAX_TEMPLATES} templates of its own. Delete one it no longer needs "
    "before adding another."
)

# The facts table's rows a page, and the conditions one request may hold.
TABLE_ROWS = 100
MAX_TABLE_ROWS = 200
MAX_CONDITIONS = 10
# The documents a page of the last readings' changes lists.
CHANGES_SHOWN = 50
MAX_CHANGES_SHOWN = 200

NO_KIND = "Name the kind of document whose documents to read again."


@router.get("/projects/{project_id}/templates", response_model=ProjectTemplatesOut)
def list_templates(project: ProjectDep, session: SessionDep):
    """The templates in force for the project, its own first, each with how many of its
    documents were last read for it, are being read and changed at their last reading."""
    kinds = _kinds(session, project.id)
    updated = dict(
        session.execute(
            select(ExtractionTemplate.id, ExtractionTemplate.updated_at).where(
                ExtractionTemplate.project_id == project.id
            )
        ).all()
    )
    return ProjectTemplatesOut(
        templates=[
            _out(template, kinds.get(template.doc_type, _NONE), updated.get(template.id))
            for template in templates_for(session, project.id)
        ],
        max_templates=MAX_TEMPLATES,
    )


@dataclass(frozen=True)
class _Asked:
    """A facts table as a request asks for it: the template in force for its kind, and its
    sort, conditions, words and rows to review, each checked against the template."""

    template: Template
    ask: Ask


def _asked(
    project: ProjectDep,
    session: SessionDep,
    kind: str,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "asc",
    conditions: Annotated[list[str] | None, Query(alias="filter")] = None,
    q: str = "",
    review: bool = False,
) -> _Asked:
    """The table the query asks for: of the documents read for the template of the kind, sorted
    by one field (`sort`; the document's name without one), the rows kept by each `filter`
    (`field:has`, `field:lacks`, `field:unchecked`, `field:contains:words`, `field:min:value`,
    `field:max:value`), the words `q`, in the document's name or its facts, and with `review`
    those with a fact no member has checked."""
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
        held = tuple(condition_of(template, written) for written in conditions or [])
    except TableRefusal as refusal:
        raise HTTPException(422, str(refusal)) from None
    return _Asked(template, Ask(sorted_by, order, held, q, review))


AskedDep = Annotated[_Asked, Depends(_asked)]


@router.get(
    "/projects/{project_id}/fact-table",
    response_model=FactTableOut,
    dependencies=[Depends(limited(SEARCHES))],
)
def fact_table(
    project: ProjectDep,
    session: SessionDep,
    asked: AskedDep,
    after: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TABLE_ROWS)] = TABLE_ROWS,
):
    """A page of the facts table of the project's documents of one kind (app/fact_table.py):
    a row for each document read for the kind's template and a column for each of its fields,
    as the query asks (`_asked`), past `after`."""
    template = asked.template
    try:
        table = read_table(session, project.id, template, asked.ask, after=after, limit=limit)
    except TableRefusal as refusal:
        raise HTTPException(422, str(refusal)) from None
    updated_at = (
        session.scalar(
            select(ExtractionTemplate.updated_at).where(ExtractionTemplate.id == template.id)
        )
        if template.id is not None
        else None
    )
    kinds = _kinds(session, project.id)
    return FactTableOut(
        template=_out(template, kinds.get(template.doc_type, _NONE), updated_at),
        rows=[
            FactRowOut(
                document_id=row.document.id,
                title=row.document.display_title,
                filename=row.document.filename,
                cells={
                    name: [fact_out(fact, row.document) for fact in facts]
                    for name, facts in row.cells.items()
                },
                earlier={
                    name: [check_out(held) for held in checks]
                    for name, checks in row.earlier.items()
                },
            )
            for row in table.rows
        ],
        total=table.total,
        to_review=table.to_review,
        next_after=table.next_after,
    )


@router.get(
    "/projects/{project_id}/fact-table/export",
    response_class=Response,
    responses={200: {"content": {media: {} for media in MEDIA_TYPES.values()}}},
    dependencies=[Depends(limited(EXPORTS))],
)
def export_fact_table(
    project: ProjectDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
    asked: AskedDep,
    written_as: Annotated[ExportFormat, Query(alias="format")] = "csv",
):
    """Every row of the facts table the query asks for (`_asked`), as a file to save
    (app/fact_export.py): a CSV of the table as the page shows it, or with `format=xlsx` an Excel
    workbook of the same rows typed and a sheet of each of their facts, with where it was found
    and who checked it. The download is recorded first, with what was asked but none of the
    values or words, which may be what a document says."""
    template = asked.template
    audit(
        session,
        account,
        request,
        "facts_exported",
        project_id=project.id,
        detail={"kind": template.doc_type, "format": written_as, **asked.ask.given()},
    )
    session.commit()
    filename = f"{project.name} {template.doc_type} facts {dt.datetime.now(dt.UTC):%Y-%m-%d}"
    headers = {"Content-Disposition": content_disposition(f"{filename}.{written_as}")}
    if written_as == "csv":
        return StreamingResponse(
            csv_file(project.id, template, asked.ask),
            media_type=MEDIA_TYPES["csv"],
            headers=headers,
        )
    # A workbook is a ZIP file, whose index comes last, so it is sent once it is whole.
    book = workbook_file(project.id, template, asked.ask)
    headers["Content-Length"] = str(os.fstat(book.fileno()).st_size)
    return StreamingResponse(read_out(book), media_type=MEDIA_TYPES["xlsx"], headers=headers)


@router.post(
    "/projects/{project_id}/templates/read-again", response_model=ReadAgainOut, status_code=202
)
def read_again(
    project: EditableProjectDep, body: ReadAgainIn, session: SessionDep, worker: WorkerDep
):
    """Queue the project's documents of a kind to be read again from their summaries and facts,
    for its template as it is now: those read for it and those of its kind read for none
    (`kind`), and those read for a kind its template is no longer for (`was`), as after its kind
    was changed or it was deleted. Each reading keeps what it changed (`fact_changes`); one being
    indexed or quarantined is left alone, as `reprocess_project` leaves it."""
    which = []
    if body.kind is not None and (kind := kind_of(body.kind)):
        which.append(of_kind(kind))
    if body.was is not None and (was := kind_of(body.was)):
        which.append(Document.template == was)
    if not which:
        raise HTTPException(400, NO_KIND)
    documents = session.scalars(
        select(Document).where(
            Document.project_id == project.id,
            Document.deleted_at.is_(None),
            Document.status.not_in(("processing", "quarantined")),
            or_(*which),
        )
    ).all()
    for document in documents:
        reset_stages(document, "enrich")
        # Behind fresh uploads, which someone is waiting for.
        queue_index(session, document, priority=BULK)
    session.commit()
    if worker:
        worker.notify()
    return ReadAgainOut(queued=len(documents))


@router.get("/projects/{project_id}/fact-changes", response_model=FactChangesOut)
def fact_changes(
    project: ProjectDep,
    session: SessionDep,
    kind: str,
    after: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CHANGES_SHOWN)] = CHANGES_SHOWN,
):
    """What the last reading of each of the project's documents of a kind changed, before or now
    (app/fact_changes.py): how many wait to be read and were read, and a page of those that
    changed a field or the template they are read for, the ones whose new reading disagrees
    with a member's check first, then the newest."""
    doc_type = kind_of(kind)
    if not doc_type:
        raise HTTPException(422, "Name the kind of document whose readings to list.")
    template = next((t for t in templates_for(session, project.id) if t.doc_type == doc_type), None)
    # What is still being read is read first: a reading commits its row with its stage's end, so
    # one that ends between these statements leaves `reading` saying so, and the page asks again,
    # where read after the counts it would say none is, beside counts that missed it.
    counts = _kinds(session, project.id).get(doc_type, _NONE)
    try:
        readings = read_changes(session, project.id, doc_type, after, limit)
    except ChangesRefusal as refusal:
        raise HTTPException(422, str(refusal)) from None
    updated_at = (
        session.scalar(
            select(ExtractionTemplate.updated_at).where(ExtractionTemplate.id == template.id)
        )
        if template is not None and template.id is not None
        else None
    )
    return FactChangesOut(
        kind=doc_type,
        template=_out(template, counts, updated_at) if template is not None else None,
        reading=counts.reading,
        read=readings.read,
        changed=readings.changed,
        unheld=readings.unheld,
        documents=[
            DocumentChangesOut(
                document_id=document.id,
                title=document.display_title,
                filename=document.filename,
                read_at=change.read_at,
                template_before=change.template_before,
                template_after=change.template_after,
                fields=[FieldChangeOut.model_validate(field) for field in change.changes],
            )
            for change, document in readings.rows
        ],
        next_after=readings.next_after,
    )


@router.put("/facts/{fact_id}/check", response_model=FactOut)
def check_fact(
    fact: EditableFactDep,
    body: FactCheckIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Check the fact as it reads now, or, with a `value`, correct it to what the document
    states (app/fact_checks.py): the member's value replaces the model's wherever the fact is
    read, and the check outlives the next reading of the document."""
    fact_checks.hold(session, fact.document_id)
    held = session.scalar(
        select(Fact)
        .where(Fact.id == fact.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if held is None:
        raise HTTPException(404, FACT_GONE)
    try:
        value = None if body.value is None else written(held, body.value, body.currency)
    except CheckRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    action = fact_checks.check(session, held, account.user.id, value)
    document = session.get_one(Document, held.document_id)
    if action is not None:
        audit(
            session,
            account,
            request,
            action,
            project_id=document.project_id,
            target=("document", document.id),
            detail={"filename": document.filename, "field": held.name},
        )
    session.commit()
    if held.check is not None:
        # Who checked it is read with the row, as their name is now.
        session.refresh(held.check)
    return fact_out(held, document)


@router.delete("/fact-checks/{check_id}", status_code=204)
def remove_check(
    check: EditableFactCheckDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Take a member's check back: a fact holding it reads as the model read it again, and one
    no fact holds, made before the document was read again, is forgotten."""
    fact_checks.hold(session, check.document_id)
    held = session.scalar(select(FactCheck).where(FactCheck.id == check.id).with_for_update())
    if held is None:
        return
    document = session.get_one(Document, held.document_id)
    audit(
        session,
        account,
        request,
        "fact_check_removed",
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": document.filename, "field": held.name},
    )
    fact_checks.remove(session, held)
    session.commit()


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


@dataclass(frozen=True)
class _KindCounts:
    """The project's documents of a kind out of the trash: read for its template, read for none
    and of the kind, waiting for the enrich stage, and changed at their last reading."""

    documents: int = 0
    matching: int = 0
    reading: int = 0
    changed: int = 0


_NONE = _KindCounts()


def _kinds(session: Session, project_id: int) -> dict[str, _KindCounts]:
    """The project's documents counted by each kind they were read for, or found to be."""
    kind = func.coalesce(Document.template, Document.doc_type)
    rows = session.execute(
        select(
            kind,
            func.count().filter(Document.template.is_not(None)),
            func.count().filter(Document.template.is_(None)),
            func.count().filter(being_read()),
        )
        .where(
            Document.project_id == project_id,
            Document.deleted_at.is_(None),
            kind.is_not(None),
        )
        .group_by(kind)
    ).all()
    changed = changed_by_kind(session, project_id)
    counts = {name: _KindCounts(*figures, changed=changed.get(name, 0)) for name, *figures in rows}
    for name, n in changed.items():
        counts.setdefault(name, _KindCounts(changed=n))
    return counts


def _row_out(session: Session, row: ExtractionTemplate) -> TemplateOut:
    kinds = _kinds(session, row.project_id)
    return _out(template_of(row), kinds.get(row.doc_type, _NONE), row.updated_at)


def _out(template: Template, counts: _KindCounts, updated_at: dt.datetime | None) -> TemplateOut:
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
        documents=counts.documents,
        matching=counts.matching,
        reading=counts.reading,
        changed=counts.changed,
        updated_at=updated_at,
    )
