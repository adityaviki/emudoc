"""A document's versions (app/versions.py): the list on its tab, a new file given to it, and an
earlier version's file and text to open."""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import app_url
from app.api.deps import (
    AccountDep,
    DocumentDep,
    EditableDocumentDep,
    ServicesDep,
    SessionDep,
    WorkerDep,
    limited,
)
from app.api.documents import MISSING_FILE
from app.audit import audit
from app.entitlements import PlanRefused, Refusal
from app.models import Document, DocumentVersion, Project, Upload, User
from app.notices import record_upload, settle_upload
from app.parsing import IMAGE_TYPES
from app.ratelimit import UPLOADS
from app.scanning import scan_passed
from app.schemas import BlockOut, DocumentContent, DocumentOut, DocumentVersionOut
from app.versions import VersionRefused, current_since, take_version

router = APIRouter(tags=["documents"])

NO_VERSION = "This document has no earlier version of that number."
SAMPLE_VERSIONS = (
    "This is the sample project, whose documents are Emudoc's own and take no new versions. "
    "Create a project of your own to upload yours."
)
UNSCANNED_VERSION = (
    "This version was replaced before the malware scan passed it, so it cannot be opened or "
    "downloaded."
)
# What a refused new version is answered with, by why (`VersionRefused.label`); 400 else.
REFUSED = {"too_large": 413, "duplicate": 409, "indexing": 409}


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersionOut])
def list_versions(document: DocumentDep, session: SessionDep):
    """The document's versions: its current file first, then the earlier ones, newest first."""
    earlier = session.execute(
        select(
            DocumentVersion.number,
            DocumentVersion.filename,
            DocumentVersion.mime_type,
            DocumentVersion.size_bytes,
            DocumentVersion.page_count,
            DocumentVersion.uploaded_at,
            DocumentVersion.scan_passed,
            func.jsonb_array_length(DocumentVersion.blocks) > 0,
            User.name,
        )
        .outerjoin(Upload, Upload.id == DocumentVersion.upload_id)
        .outerjoin(User, User.id == Upload.user_id)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.number.desc())
    ).all()
    uploader = session.scalar(
        select(User.name)
        .join(Upload, Upload.user_id == User.id)
        .where(Upload.id == document.upload_id)
    )
    current = DocumentVersionOut(
        number=document.version,
        current=True,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        page_count=document.page_count,
        uploaded_at=current_since(session, document),
        uploaded_by=uploader,
        served=scan_passed(document) and document.status != "quarantined",
        has_text=document.stages.get("parse", {}).get("status") == "done",
    )
    return [
        current,
        *(
            DocumentVersionOut(
                number=number,
                current=False,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                page_count=page_count,
                uploaded_at=uploaded_at,
                uploaded_by=name,
                served=served,
                has_text=has_text,
            )
            for (
                number,
                filename,
                mime_type,
                size_bytes,
                page_count,
                uploaded_at,
                served,
                has_text,
                name,
            ) in earlier
        ),
    ]


@router.post(
    "/documents/{document_id}/versions",
    response_model=DocumentOut,
    status_code=201,
    dependencies=[Depends(limited(UPLOADS))],
)
def upload_version(
    document: EditableDocumentDep,
    file: UploadFile,
    session: SessionDep,
    services: ServicesDep,
    worker: WorkerDep,
    account: AccountDep,
    request: Request,
):
    """Give the document a new file, which it reads in the earlier one's place, keeping the
    earlier one as a version to open and download. Refused, with nothing changed, for a file an
    upload would refuse (400, 413), for the bytes of a file the project holds (409), while the
    document is being indexed (409), and when the plan has no room for its bytes or its pages
    (402); the sample project's documents take none (409)."""
    project = session.get(Project, document.project_id)
    if project.sample:
        raise HTTPException(409, SAMPLE_VERSIONS)

    def audited(action: str, detail: dict) -> None:
        audit(
            session,
            account,
            request,
            action,
            project_id=project.id,
            target=("document", document.id),
            detail=detail,
        )

    try:
        take_version(
            session,
            services.storage,
            project,
            account.organization,
            document,
            file.filename or "unnamed",
            file.file,
            user_id=account.user.id,
            audited=audited,
        )
    except VersionRefused as refused:
        session.rollback()
        if refused.limit is not None:
            raise PlanRefused(Refusal(refused.limit, refused.reason)) from None
        raise HTTPException(REFUSED.get(refused.label, 400), refused.reason) from None
    # The new file is an upload of its own, whose notice and automations follow its reading.
    if upload_id := record_upload(session, project.id, account.user.id, [document.id]):
        session.commit()
        settle_upload(session, upload_id, app_url(request))
    if worker:
        worker.notify()
    session.refresh(document)
    return document


def _earlier(session: Session, document: Document, number: int) -> DocumentVersion:
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id, DocumentVersion.number == number
        )
    )
    if version is None:
        raise HTTPException(404, NO_VERSION)
    return version


@router.get("/documents/{document_id}/versions/{number}/file")
def download_version(
    document: DocumentDep,
    number: int,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
) -> Response:
    """An earlier version's file as it was uploaded, as `download_file` sends the current one,
    and a download in the audit log as that is."""
    version = _earlier(session, document, number)
    if not version.scan_passed:
        raise HTTPException(409, UNSCANNED_VERSION)
    storage = services.storage
    if not storage.exists(version.storage_key):
        raise HTTPException(404, MISSING_FILE)
    response = storage.response(
        version.storage_key,
        media_type=version.mime_type,
        filename=version.filename,
        inline=version.mime_type in IMAGE_TYPES.values(),
    )
    audit(
        session,
        account,
        request,
        "document_downloaded",
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": version.filename, "version": version.number},
    )
    session.commit()
    return response


@router.get("/documents/{document_id}/versions/{number}/content", response_model=DocumentContent)
def version_content(document: DocumentDep, number: int, session: SessionDep):
    """An earlier version's text as its parse read it, block by block, to read beside the
    current one; empty for a version replaced before it was read."""
    version = _earlier(session, document, number)
    return DocumentContent(
        blocks=[
            BlockOut(
                ordinal=ordinal,
                kind=block["kind"],
                level=block["level"],
                text=block["text"],
                page=block["page"],
                bbox=None,
            )
            for ordinal, block in enumerate(version.blocks)
        ],
        page_sizes=[],
    )
