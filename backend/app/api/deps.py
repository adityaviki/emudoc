from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_session
from app.indexing.pipeline import Services
from app.indexing.worker import Worker
from app.models import Chat, Document, Project

SessionDep = Annotated[Session, Depends(get_session)]
# The stage a reprocess run starts from; later stages run too.
Stage = Literal["parse", "structure", "enrich", "embed"]


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_worker(request: Request) -> Worker | None:
    return request.app.state.worker


ServicesDep = Annotated[Services, Depends(get_services)]
WorkerDep = Annotated[Worker | None, Depends(get_worker)]


def get_project(project_id: int, session: SessionDep) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def get_document(document_id: int, session: SessionDep) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    return document


def get_chat(chat_id: int, session: SessionDep) -> Chat:
    chat = session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


ProjectDep = Annotated[Project, Depends(get_project)]
DocumentDep = Annotated[Document, Depends(get_document)]
ChatDep = Annotated[Chat, Depends(get_chat)]
