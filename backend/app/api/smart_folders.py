"""A project's smart folders (app/smart_folders.py): saved views of its documents, which the
library lists like folders (`list_documents` in app/api/documents.py, `smart=`), and saved
searches, which it opens as the search of their words within those documents.

Anyone who reads the project reads them; a member who may change the project makes and changes
them, and deletes them by role alone, as a folder is. A smart folder is written whole. Nothing
here is audited: it is a way of reading the library, as a folder is.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    DeletableSmartFolderDep,
    EditableProjectDep,
    EditableSmartFolderDep,
    ProjectDep,
    SessionDep,
)
from app.api.folders import resolve_folder
from app.models import Project, SmartFolder
from app.schemas import DocumentFilter, SmartFolderIn, SmartFolderOut, SmartFoldersOut
from app.smart_folders import (
    BACKWARDS,
    MAX_SMART_FOLDERS,
    TOO_MANY,
    project_smart_folders,
    smart_folder_out,
)
from app.tags import NOT_THE_PROJECTS, the_projects

router = APIRouter(tags=["smart folders"])


@router.get("/projects/{project_id}/smart-folders", response_model=SmartFoldersOut)
def list_smart_folders(project: ProjectDep, session: SessionDep):
    """The project's smart folders by name, each with why it lists nothing as the project stands
    now, if it does not."""
    return SmartFoldersOut(
        smart_folders=[
            smart_folder_out(session, row) for row in project_smart_folders(session, project.id)
        ],
        max_smart_folders=MAX_SMART_FOLDERS,
    )


@router.post("/projects/{project_id}/smart-folders", response_model=SmartFolderOut, status_code=201)
def create_smart_folder(
    project: EditableProjectDep, body: SmartFolderIn, session: SessionDep, account: AccountDep
):
    """Make a smart folder of the project's documents."""
    chosen = _written(session, project, body.filter)
    # The project's row, so that two made at once cannot pass its ceiling.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    held = session.scalar(select(func.count()).where(SmartFolder.project_id == project.id))
    if held >= MAX_SMART_FOLDERS:
        raise HTTPException(409, TOO_MANY)
    row = SmartFolder(
        org_id=project.org_id,
        project_id=project.id,
        name=body.name,
        filter=chosen.model_dump(mode="json"),
        query=body.query or None,
        created_by=account.user.id,
    )
    session.add(row)
    session.commit()
    return smart_folder_out(session, row)


@router.put("/smart-folders/{smart_folder_id}", response_model=SmartFolderOut)
def update_smart_folder(row: EditableSmartFolderDep, body: SmartFolderIn, session: SessionDep):
    """Write the smart folder anew, whole."""
    project = session.get(Project, row.project_id)
    row.name = body.name
    row.filter = _written(session, project, body.filter).model_dump(mode="json")
    row.query = body.query or None
    session.commit()
    return smart_folder_out(session, row)


@router.delete("/smart-folders/{smart_folder_id}", status_code=204)
def delete_smart_folder(row: DeletableSmartFolderDep, session: SessionDep):
    """Delete the smart folder for good; the documents it listed stay where they are."""
    session.delete(row)
    session.commit()


def _written(session: Session, project: Project, chosen: DocumentFilter) -> DocumentFilter:
    """The filter as it is kept, each tag, type and words once; refused for a tag that is not
    the project's, a folder that is not or is in the trash, and dates the wrong way round."""
    tags = the_projects(session, project.id, chosen.tags)
    if tags is None:
        raise HTTPException(404, NOT_THE_PROJECTS)
    if chosen.folder_id is not None:
        resolve_folder(session, project, chosen.folder_id)
    if chosen.after and chosen.before and chosen.after > chosen.before:
        raise HTTPException(400, BACKWARDS)
    return chosen.model_copy(
        update={
            "tags": tags,
            "types": list(dict.fromkeys(chosen.types)),
            "mentions": list(dict.fromkeys(chosen.mentions)),
            "every_tag": bool(tags) and chosen.every_tag,
        }
    )
