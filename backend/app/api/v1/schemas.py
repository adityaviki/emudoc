"""What the versioned API takes and answers with (app/api/v1/): its own models, so that the page's
(app/schemas.py) change with the page and these by addition alone, as the contract holds them
(app/api/v1/contract.py).

Every list is a page, `data` with `next`, the `after` of the page that follows, None on the last;
a list that fits on one page today says so with a `next` of None, so that a program that follows
`next` goes on working when it does not. Times are UTC in ISO 8601; ids are the app's own. The
values a field may take are the app's (the literals of app/schemas.py), so that the API answers
what the app holds and the contract test notices when that grows.
"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas import ChatVisibility, FactType, MessageStatus, ProjectVisibility

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# The figures of a plan a refusal named, as the API was published with them. Nothing is refused
# for a plan since the plans went (S.2, 2026-11-13), and the contract keeps them, since a program
# written against it reads them: taking them out is a /api/v2's.
PlanLimit = Literal[
    "projects",
    "documents",
    "storage_bytes",
    "pages_per_month",
    "questions_per_month",
    "members",
    "daily_spend_micros",
    "api_access",
    "connectors",
]


class Error(BaseModel):
    """A refusal: what was refused and what to do next, in a sentence for the person the program
    works for."""

    detail: str


class PlanError(Error):
    """A refusal by the organisation's plan, with the figure it was held to; never answered since
    the app has no plans."""

    limit: PlanLimit


class UploadLimits(BaseModel):
    """What one upload may carry: how many files, how many bytes together and each, and the file
    types the app reads, by their extensions."""

    files: int
    request_bytes: int
    file_bytes: int
    extensions: list[str]


class Service(BaseModel):
    """Whether a part of the service can be used just now, and if not, what cannot be done."""

    available: bool
    error: str | None


class Status(BaseModel):
    """What the deployment can do just now: answer questions and read summaries and facts
    (`models`), search by meaning as well as by keywords (`search`), and index uploads and write
    answers at all (`workers`); and what one upload may carry."""

    models: Service
    search: Service
    workers: Service
    uploads: UploadLimits


class NewProject(BaseModel):
    """A project to make: one the organisation sees, or one private to the key's member, and the
    organisation's owners and admins, until they choose others in the app."""

    name: str = Field(min_length=1, max_length=200, examples=["Supplier contracts"])
    description: str = Field("", max_length=2000)
    visibility: ProjectVisibility = "organization"


class ProjectChange(BaseModel):
    """A project renamed or described anew; a field left out stays as it is."""

    name: str | None = Field(None, min_length=1, max_length=200, examples=["Supplier contracts"])
    description: str | None = Field(None, max_length=2000)


class Project(BaseModel):
    """A project: its documents are searched and asked about together. The sample project a new
    organisation starts with takes no uploads."""

    id: int
    name: str
    description: str
    visibility: ProjectVisibility
    sample: bool
    document_count: int
    created_at: dt.datetime


class ProjectList(BaseModel):
    """The projects the key's member sees, the newest first."""

    data: list[Project]
    next: str | None


class NewFolder(BaseModel):
    """A folder to make, at the project's root or in another of its folders."""

    name: str = Field(min_length=1, max_length=200, examples=["Invoices"])
    parent_id: int | None = None


class FolderChange(BaseModel):
    """A folder renamed, moved or both; a field left out stays as it is, and a `parent_id` of
    null moves it to the root."""

    name: str | None = Field(None, min_length=1, max_length=200, examples=["Invoices 2026"])
    parent_id: int | None = None


class Folder(BaseModel):
    """A folder of a project's library, with its path from the root and how many documents it
    holds itself, not counting its folders'."""

    id: int
    project_id: int
    parent_id: int | None
    name: str
    path: str
    document_count: int
    created_at: dt.datetime


class FolderList(BaseModel):
    """A project's folders, the oldest first; a tree is built from `parent_id`."""

    data: list[Folder]
    next: str | None


class DocumentChange(BaseModel):
    """A document renamed, moved or both; a field left out stays as it is, and a `folder_id` of
    null moves it to the root. A new name keeps the file's extension."""

    filename: str | None = Field(
        None, min_length=1, max_length=500, examples=["invoice-2026-10.pdf"]
    )
    folder_id: int | None = None


class Document(BaseModel):
    """A document and how far it is indexed. `status` is `queued` or `processing` while it is
    read, `ready` once it can be searched and asked about, `failed` when it could not be read and
    `quarantined` when the malware scan flagged it, `error` saying why. A ready document is
    `partly_indexed` when it went without summaries and facts or without the vectors that search
    by meaning, which the model or the embeddings being out causes; `retry_at` is when a stage
    that failed for a reason that passes runs again by itself."""

    id: int
    project_id: int
    folder_id: int | None
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: Literal["queued", "processing", "ready", "failed", "quarantined"]
    partly_indexed: bool
    retry_at: dt.datetime | None
    error: str | None
    page_count: int | None
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    summary: str | None
    created_at: dt.datetime
    # Which version of the document its file is, from 1: a new version is a file the document
    # reads in the earlier one's place, keeping its id.
    version: int


class DocumentList(BaseModel):
    """A page of a project's documents, the newest first."""

    data: list[Document]
    next: str | None


class Version(BaseModel):
    """A version of a document: its current file, or an earlier one, kept to download, whose text
    nothing searches or answers from any more."""

    number: int
    current: bool
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    uploaded_at: dt.datetime


class VersionList(BaseModel):
    """A document's versions, the current one first, then the earlier ones, the latest first: every
    one of them, on one page."""

    data: list[Version]
    next: str | None


class RejectedFile(BaseModel):
    """A file of an upload that was not kept, by its place among the files sent (from 0) and its
    name, and why: a sentence, `limit` always null since the app has no plans, and the document
    that holds the same bytes when the project holds them already, which is no failure."""

    index: int
    filename: str
    reason: str
    limit: PlanLimit | None
    duplicate_of: int | None


class Upload(BaseModel):
    """The documents an upload kept, in the order their files were sent, and the files it did
    not. Each document is indexed in the background: follow it by its id."""

    documents: list[Document]
    rejected: list[RejectedFile]


class Fact(BaseModel):
    """A fact read from a document: a field's value as written, typed as a number, a date or an
    amount with its currency where it is one, with the words it was read from and where they are.
    `verified` says the words were found in the text."""

    id: int
    document_id: int
    name: str
    value_type: FactType
    value: str
    number: float | None
    date: dt.date | None
    currency: str | None
    quote: str | None
    page: int | None
    passage_id: int | None
    verified: bool


class FactList(BaseModel):
    """A page of facts, the oldest first."""

    data: list[Fact]
    next: str | None


class SearchHit(BaseModel):
    """A passage that matched, with the words around the match, and how well it matched."""

    passage_id: int
    document_id: int
    document_title: str
    doc_type: str | None
    context: str
    snippet: str
    page_start: int | None
    page_end: int | None
    score: float


class SearchResults(BaseModel):
    """The passages that match best, the best first; `semantic` is false while search matches
    keywords alone."""

    data: list[SearchHit]
    semantic: bool


class Passage(BaseModel):
    """A passage of a document, as an answer cites it ([P<id>]): the section it is in, its text
    and its pages."""

    id: int
    document_id: int
    document_title: str
    context: str
    text: str
    page_start: int | None
    page_end: int | None


class ChatChange(BaseModel):
    """A chat renamed, shared with the project or taken private again; a field left out stays as
    it is."""

    title: str | None = Field(None, min_length=1, max_length=200, examples=["Invoice checks"])
    visibility: ChatVisibility | None = None


class Chat(BaseModel):
    """A chat with the agent: private to the member who started it until they share it with the
    project, whose members then read it; only its starter asks in it."""

    id: int
    project_id: int
    user_id: int
    title: str
    visibility: ChatVisibility
    created_at: dt.datetime
    updated_at: dt.datetime


class ChatList(BaseModel):
    """A page of the chats of a project the key's member started or that were shared with it,
    the newest first."""

    data: list[Chat]
    next: str | None


class Question(BaseModel):
    content: Annotated[_Text, Field(examples=["Does the invoice match the contract's prices?"])]


class Source(BaseModel):
    """A passage the agent was shown, which its answer cites as [P<passage_id>]."""

    passage_id: int
    document_id: int
    document_title: str
    page_start: int | None
    page_end: int | None
    excerpt: str


class Message(BaseModel):
    """A question, or the agent's answer. While an answer's `status` is `streaming` it is being
    written and `content` is what was written so far; it ends `done`, `stopped` or `failed`,
    `error` saying why. `notice` is what the answer adds about itself, such as that it stopped."""

    id: int
    chat_id: int
    role: Literal["user", "assistant"]
    content: str
    status: MessageStatus
    sources: list[Source]
    notice: str | None
    error: str | None
    created_at: dt.datetime


class MessageList(BaseModel):
    """A page of a chat's questions and answers, the oldest first."""

    data: list[Message]
    next: str | None


class Asked(BaseModel):
    """A question as saved, and its answer, which the worker writes: follow it by its id."""

    question: Message
    answer: Message


class DocumentEvent(BaseModel):
    """A document's indexing ended, posted to the organisation's webhooks that listen for it:
    `document.ready` once it can be searched and asked about (`data.partly_indexed` says whether
    it went without a part), `document.failed` once it could not be read or was quarantined
    (`data.error` says why). `id` names the event, the same in every try of it."""

    id: str
    type: Literal["document.ready", "document.failed"]
    created_at: dt.datetime
    data: Document
