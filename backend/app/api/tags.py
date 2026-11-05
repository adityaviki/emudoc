"""A project's tags (app/tags.py), and the tags each of its documents carries.

Anyone who reads the project reads its tags, and lists a tag's documents (`list_documents` in
app/api/documents.py, `tag=`); a member who may change the project makes, renames, recolours and
tags with them, one document or the several chosen in the library at once (app/selection.py),
and deletes them by role alone, as a folder is. Nothing here is audited: a tag is a way of
reading the library, as a name or a folder is.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import (
    AccountDep,
    DeletableTagDep,
    EditableDocumentDep,
    EditableProjectDep,
    EditableTagDep,
    ProjectDep,
    SessionDep,
)
from app.models import Project, Tag
from app.schemas import (
    DocumentOut,
    DocumentsTagsIn,
    DocumentTagsIn,
    TagIn,
    TagOut,
    TagPatch,
    TagsOut,
)
from app.selection import NOT_CHOSEN, chosen_documents
from app.tags import (
    BOTH_WAYS,
    MAX_TAGS,
    NAME_TAKEN,
    NOT_THE_PROJECTS,
    TOO_MANY,
    delete_tag,
    name_taken,
    no_room,
    project_tags,
    retagged,
    tag_counts,
    the_projects,
)

router = APIRouter(tags=["tags"])


@router.get("/projects/{project_id}/tags", response_model=TagsOut)
def list_tags(project: ProjectDep, session: SessionDep):
    """The project's tags by name, each with how many of its documents carry it."""
    counts = tag_counts(session, project.id)
    return TagsOut(
        tags=[_out(tag, counts.get(tag.id, 0)) for tag in project_tags(session, project.id)],
        max_tags=MAX_TAGS,
    )


@router.post("/projects/{project_id}/tags", response_model=TagOut, status_code=201)
def create_tag(project: EditableProjectDep, body: TagIn, session: SessionDep, account: AccountDep):
    """Make a tag of the project's, for its documents to carry."""
    # The project's row, so that two made at once cannot pass its ceiling or share a name.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    if name_taken(session, project.id, body.name):
        raise HTTPException(409, NAME_TAKEN)
    if session.scalar(select(func.count()).where(Tag.project_id == project.id)) >= MAX_TAGS:
        raise HTTPException(409, TOO_MANY)
    tag = Tag(
        org_id=project.org_id,
        project_id=project.id,
        name=body.name,
        color=body.color,
        created_by=account.user.id,
    )
    session.add(tag)
    session.commit()
    return _out(tag, 0)


@router.patch("/tags/{tag_id}", response_model=TagOut)
def update_tag(tag: EditableTagDep, body: TagPatch, session: SessionDep):
    """Rename and/or recolour the tag: every document carrying it shows it so at once."""
    session.execute(select(Project.id).where(Project.id == tag.project_id).with_for_update())
    if body.name is not None and name_taken(session, tag.project_id, body.name, tag.id):
        raise HTTPException(409, NAME_TAKEN)
    if body.name is not None:
        tag.name = body.name
    if body.color is not None:
        tag.color = body.color
    session.commit()
    return _out(tag, tag_counts(session, tag.project_id).get(tag.id, 0))


@router.delete("/tags/{tag_id}", status_code=204)
def remove_tag(tag: DeletableTagDep, session: SessionDep):
    """Delete the tag for good, taking it off every document that carries it."""
    delete_tag(session, tag)
    session.commit()


@router.put("/documents/{document_id}/tags", response_model=DocumentOut)
def tag_document(document: EditableDocumentDep, body: DocumentTagsIn, session: SessionDep):
    """Give the document these tags, all of them: one it carries that is left out is taken off."""
    tag_ids = the_projects(session, document.project_id, body.tag_ids)
    if tag_ids is None:
        raise HTTPException(404, NOT_THE_PROJECTS)
    document.tag_ids = tag_ids
    session.commit()
    return document


@router.post("/projects/{project_id}/documents/tags", response_model=list[DocumentOut])
def tag_documents(project: EditableProjectDep, body: DocumentsTagsIn, session: SessionDep):
    """Put tags on every chosen document and take tags off every one, leaving the rest of each
    one's as they are, in one change: a document without room for what it would be given
    refuses the whole change, naming it, rather than the others being tagged and it not."""
    add = the_projects(session, project.id, body.add)
    remove = the_projects(session, project.id, body.remove)
    if add is None or remove is None:
        raise HTTPException(404, NOT_THE_PROJECTS)
    if set(add) & set(remove):
        raise HTTPException(422, BOTH_WAYS)
    documents = chosen_documents(session, project.id, body.document_ids, lock=True)
    if documents is None:
        raise HTTPException(404, NOT_CHOSEN)
    given = {document.id: retagged(document, add, remove) for document in documents}
    refusal = next(filter(None, (no_room(d, given[d.id]) for d in documents)), None)
    if refusal is not None:
        raise HTTPException(409, refusal)
    for document in documents:
        if given[document.id] != document.tag_ids:
            document.tag_ids = given[document.id]
    session.commit()
    return documents


def _out(tag: Tag, count: int) -> TagOut:
    return TagOut(id=tag.id, name=tag.name, color=tag.color, document_count=count)
