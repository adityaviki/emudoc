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

from app.schemas import (
    ChatVisibility,
    FactType,
    FindingOutcome,
    FindingReason,
    MessageStatus,
    PlanLimit,
    ProjectVisibility,
    ReportStatus,
    ReviewState,
    RunStatus,
)

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Error(BaseModel):
    """A refusal: what was refused and what to do next, in a sentence for the person the program
    works for."""

    detail: str


class PlanError(Error):
    """A refusal by the organisation's plan, with the figure it is held to."""

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
    # The kind of extraction template its facts were read for, such as "invoice".
    template: str | None
    review: ReviewState | None
    assignee_id: int | None
    created_at: dt.datetime


class DocumentList(BaseModel):
    """A page of a project's documents, the newest first."""

    data: list[Document]
    next: str | None


class RejectedFile(BaseModel):
    """A file of an upload that was not kept, by its place among the files sent (from 0) and its
    name, and why: a sentence, the figure of the plan when it was the plan, and the document that
    holds the same bytes when the project holds them already, which is no failure."""

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


class FactCheck(BaseModel):
    """A member's check of a fact: who and when, the model's value when they corrected it, and
    what they said the document states."""

    checked_by: str | None
    checked_at: dt.datetime
    corrected_from: str | None
    value: str


class Fact(BaseModel):
    """A fact read from a document: a field's value as written, typed as a number, a date or an
    amount with its currency where it is one, with the words it was read from and where they are.
    `verified` says the words were found in the text; `check` is a member's word on it."""

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
    check: FactCheck | None


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


class CrossCheck(BaseModel):
    """A rule a project's documents are checked against one another by, and why it cannot be
    worked out with the project's templates as they are now, if it cannot."""

    id: int
    title: str
    problem: str | None


class CrossCheckList(BaseModel):
    """A project's cross-checks, by name."""

    data: list[CrossCheck]
    next: str | None


class NewCheckRun(BaseModel):
    """A run of the project's cross-checks, those named or all of them, and whether the agent
    writes a discrepancy report once they are worked out, which counts as a question."""

    check_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    report: bool = Field(False, examples=[True])


class FindingCounts(BaseModel):
    differs: int
    missing: int
    holds: int


class Rule(BaseModel):
    """A rule of a run as it was when the run was made, by its place in the run (`rule` of its
    findings), the cross-check's id and name, and once the run is worked out why it could not be
    or what it found."""

    rule: int
    check_id: int
    title: str
    problem: str | None
    counts: FindingCounts | None


class CheckRun(BaseModel):
    """A run of a project's cross-checks. `status` is `queued` or `running` until it is worked
    out, then `done` or `failed`; `report_status` is the discrepancy report's, None when none was
    asked for. The report cites the passages of `report_sources` as [P<id>]."""

    id: int
    project_id: int
    status: RunStatus
    report_status: ReportStatus | None
    rules: list[Rule]
    counts: FindingCounts | None
    error: str | None
    report: str
    report_sources: list[Source]
    report_error: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None


class CheckRunList(BaseModel):
    """A page of a project's runs, the newest first."""

    data: list[CheckRun]
    next: str | None


class Partner(BaseModel):
    document_id: int
    title: str


class Finding(BaseModel):
    """What a rule of a run found for a value of a document: whether it differs, is missing or
    holds, and why; the fact checked and those it was compared with, as they were read when the
    run was made; the documents paired with it; and the checked value less the other (or their
    sum), in days for dates."""

    document_id: int
    title: str
    filename: str
    outcome: FindingOutcome
    reason: FindingReason
    left: Fact | None
    right: list[Fact]
    partners: list[Partner]
    difference: float | None


class FindingList(BaseModel):
    """A page of what one rule of a run found, differing first, then missing, then holding, and
    how many of the outcome asked for (or of all) it found."""

    data: list[Finding]
    total: int
    next: str | None


class DocumentEvent(BaseModel):
    """A document's indexing ended, posted to the organisation's webhooks that listen for it:
    `document.ready` once it can be searched and asked about (`data.partly_indexed` says whether
    it went without a part), `document.failed` once it could not be read or was quarantined
    (`data.error` says why). `id` names the event, the same in every try of it."""

    id: str
    type: Literal["document.ready", "document.failed"]
    created_at: dt.datetime
    data: Document


class CheckRunEvent(BaseModel):
    """A run of a project's cross-checks worked out, or failed, and its discrepancy report
    written or failed when one was asked for, posted to the organisation's webhooks that listen
    for it. A report asked for after the run is announced again once it is written. `id` names
    the event, the same in every try of it."""

    id: str
    type: Literal["check_run.completed"]
    created_at: dt.datetime
    data: CheckRun
