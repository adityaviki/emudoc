"""What a chat is about: the whole project, one of its folders with the subfolders, or documents
chosen from it (`Chat.scope_kind`). Its starter chooses it, from the chat or from the library,
where summarising a folder or comparing two documents starts a chat about them, and changes it
whenever they like; each answer reads what the chat is about as it starts.

The server holds the agent to it as it holds it to the project: every tool reads only the live
documents the scope names (`readable`, through `ToolContext` in app/agent/tools.py), whatever the
model asks for, and the prompt says what the chat is about by ids alone, since a folder's name or
a document's title can carry words the model should not take for the member's. A scope whose
folder or documents were all deleted, or are in the trash, reads nothing, and the tools say so.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.folders import subtree_ids
from app.models import Chat, Document, Folder

# What every tool that reads says once nothing the chat is about is left to read.
NOTHING_LEFT = (
    "Nothing this chat is about is left in the project: its folder or its documents were deleted "
    "or moved to the trash. Tell the member so, and that they can choose again what the chat is "
    "about, above the box they ask in."
)


@dataclass(frozen=True)
class Scope:
    """What a chat that is not about the whole project reads: a folder (None once it was deleted
    for good) with its subfolders, or the documents chosen."""

    kind: str
    folder_id: int | None = None
    document_ids: tuple[int, ...] = ()


def scope_of(chat: Chat) -> Scope | None:
    """The chat's scope, read into plain values; None for a chat about the whole project."""
    if chat.scope_kind == "folder":
        return Scope("folder", folder_id=chat.scope_folder_id)
    if chat.scope_kind == "documents":
        return Scope("documents", document_ids=tuple(d.id for d in chat.scope_documents))
    return None


def readable(session: Session, project_id: int, scope: Scope) -> list[int]:
    """The ids of the project's documents out of the trash that the scope names: those in its
    folder and the folders below it, or those of its documents still there."""
    live = [Document.project_id == project_id, Document.deleted_at.is_(None)]
    if scope.kind == "folder":
        folder = session.scalar(
            select(Folder).where(
                Folder.id == scope.folder_id,
                Folder.project_id == project_id,
                Folder.deleted_at.is_(None),
            )
        )
        if folder is None:
            return []
        within = Document.folder_id.in_(subtree_ids(session, folder))
    else:
        within = Document.id.in_(scope.document_ids)
    return list(session.scalars(select(Document.id).where(*live, within).order_by(Document.id)))


def told(scope: Scope | None, documents: list[int]) -> str:
    """What the prompt says the chat is about, given the documents it may read; nothing for a
    chat about the whole project."""
    if scope is None:
        return ""
    if not documents:
        about = "a folder" if scope.kind == "folder" else "documents"
        return (
            f"\n\nThis chat is about {about} no longer in the project: your tools find nothing. "
            "Tell the member so."
        )
    if scope.kind == "folder":
        about = (
            f"one folder of the project and its subfolders, which hold {_count(documents)}; "
            "list_documents shows them"
        )
    else:
        ids = ", ".join(map(str, documents))
        about = f"{_count(documents)} the member chose, those with the ids {ids}"
    return (
        f"\n\nThis chat is about {about}. Your tools read only these documents. When the member "
        "asks about anything else in the project, say that this chat is limited to them, and "
        "that they can choose what it is about above the box they ask in."
    )


def _count(documents: list[int]) -> str:
    return "1 document" if len(documents) == 1 else f"{len(documents)} documents"
