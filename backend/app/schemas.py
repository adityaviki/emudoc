import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _FromORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


class ProjectOut(_FromORM):
    id: int
    name: str
    description: str
    created_at: dt.datetime
    document_count: int = 0


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None


class FolderPatch(BaseModel):
    """Rename and/or move a folder. A field that is left out is left unchanged."""

    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: int | None = None


class FolderOut(_FromORM):
    id: int
    project_id: int
    parent_id: int | None
    name: str
    created_at: dt.datetime


class UsageOut(BaseModel):
    """What one kind of work used for a document or an answer, summed over every run for it:
    the counts by unit, the model when one was called, and the cost in millionths of the
    currency the status names, when the model has a price."""

    model: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    pages: int = 0
    bytes: int = 0
    inputs: int = 0
    cost_micros: int | None = None


class StageOut(BaseModel):
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"
    detail: str = ""


class DocumentPatch(BaseModel):
    """Rename and/or move a document. A field that is left out is left unchanged."""

    filename: str | None = Field(None, min_length=1, max_length=500)
    folder_id: int | None = None


class DocumentOut(_FromORM):
    id: int
    project_id: int
    folder_id: int | None
    filename: str
    mime_type: str
    size_bytes: int
    # `quarantined` is a file the malware scan flagged: `error` says so, and the file is
    # neither indexed nor served (app/scanning.py).
    status: Literal["queued", "processing", "ready", "failed", "quarantined"]
    stages: dict[str, StageOut]
    error: str | None
    page_count: int | None
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    summary: str | None
    # By kind: `upload` (bytes, pages), `ocr`, `infer_sections`, `enrich`, `embed`, `render`.
    usage: dict[str, UsageOut]
    created_at: dt.datetime


class RejectedFile(BaseModel):
    filename: str
    reason: str


class UploadResult(BaseModel):
    documents: list[DocumentOut]
    rejected: list[RejectedFile]


class SectionOut(BaseModel):
    id: int
    title: str
    level: int
    summary: str | None
    page_start: int | None
    page_end: int | None
    block_start: int
    block_end: int
    children: list["SectionOut"] = []


class BlockOut(_FromORM):
    ordinal: int
    kind: str
    level: int | None
    text: str
    page: int | None
    bbox: list[float] | None


class DocumentContent(BaseModel):
    blocks: list[BlockOut]
    # [width, height] per page in the same units as block bboxes; empty when there are no pages.
    page_sizes: list[list[float]]


class PassageOut(BaseModel):
    id: int
    document_id: int
    document_title: str
    section_id: int
    context: str
    text: str
    page_start: int | None
    page_end: int | None
    block_start: int
    block_end: int


class FactOut(BaseModel):
    id: int
    document_id: int
    document_title: str
    name: str
    value_type: str
    value: str
    number: float | None
    date: dt.date | None
    currency: str | None
    quote: str | None
    page: int | None
    block_ordinal: int | None
    passage_id: int | None
    verified: bool


class FactList(BaseModel):
    total: int
    facts: list[FactOut]


class FactName(BaseModel):
    name: str
    count: int


class SnippetSegment(BaseModel):
    text: str
    match: bool


class SearchHitOut(BaseModel):
    passage_id: int
    document_id: int
    document_title: str
    doc_type: str | None
    context: str
    snippet: list[SnippetSegment]
    page_start: int | None
    page_end: int | None
    score: float
    keyword_rank: int | None
    vector_rank: int | None


class SearchOut(BaseModel):
    hits: list[SearchHitOut]
    semantic: bool


ChatVisibility = Literal["private", "project"]


class ChatOut(_FromORM):
    id: int
    project_id: int
    title: str
    visibility: ChatVisibility
    created_at: dt.datetime
    updated_at: dt.datetime


class ChatPatch(BaseModel):
    """Rename and/or share a chat. A field that is left out is left unchanged."""

    title: str | None = Field(None, min_length=1, max_length=200)
    visibility: ChatVisibility | None = None


class QuestionIn(BaseModel):
    content: str = Field(min_length=1)


class ChatStep(BaseModel):
    """One tool call the agent made while answering."""

    id: str
    name: str
    input: dict[str, Any]
    summary: str = ""
    is_error: bool = False


class SourceOut(BaseModel):
    """A passage the agent was shown, which its answer may cite as [P<id>]."""

    id: int
    document_id: int
    document_title: str
    context: str
    page_start: int | None
    page_end: int | None
    excerpt: str


# How far an answer is. While it is `streaming` the row is empty and its events, streamed by
# the events route, are the answer so far; a question is always `done`.
MessageStatus = Literal["streaming", "done", "stopped", "failed"]


class ChatMessageOut(_FromORM):
    id: int
    role: Literal["user", "assistant"]
    content: str
    steps: list[ChatStep]
    sources: list[SourceOut]
    notice: str | None
    error: str | None
    status: MessageStatus
    # What the answer used, under `answer`, once its run has recorded it; empty before that
    # and for a question.
    usage: dict[str, UsageOut]
    created_at: dt.datetime


class ChatDetail(ChatOut):
    messages: list[ChatMessageOut]


class AskOut(BaseModel):
    """A question as saved, and the empty answer whose events the client follows."""

    question: ChatMessageOut
    answer: ChatMessageOut


# Enough to catch a typo; the verification email settles whether the address is real.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# NIST SP 800-63B: a minimum length and no composition rules; a breach check is a roadmap item.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200

Email = Annotated[str, Field(max_length=320, pattern=EMAIL_PATTERN)]
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


class SignupIn(BaseModel):
    email: Email
    name: str = Field("", max_length=200)
    password: Password
    # The edition of the terms of service and privacy policy the form showed and the person
    # ticked (TERMS_VERSION, app/legal/); an account is made only when it is the current one.
    terms_version: str | None = Field(None, max_length=40)


class TermsIn(BaseModel):
    """The edition of the terms the user read and accepts, which must be the current one."""

    version: str = Field(max_length=40)


class LoginIn(BaseModel):
    email: Email
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class LoginOut(BaseModel):
    """Whether the password was enough, or an authenticator code must follow."""

    mfa_required: bool


class EmailIn(BaseModel):
    email: Email


class LinkTokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class PasswordResetIn(LinkTokenIn):
    password: Password


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: Password


class CodeIn(BaseModel):
    """An authenticator code, or a recovery code where one is accepted."""

    code: str = Field(min_length=1, max_length=40)


class MfaDisableIn(BaseModel):
    """Either the password or a code confirms the change; one must be given."""

    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    code: str | None = Field(None, max_length=40)


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_url: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class AccountDeletionIn(BaseModel):
    """What confirms a request to delete the account: the password, or, for an account that
    has none and signs in through a provider, its email address typed out."""

    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    email: str | None = Field(None, max_length=320)


class OrganizationOut(_FromORM):
    id: int
    name: str
    slug: str
    # Which plan's limits the organisation is held to (app/entitlements.py), for the prompt
    # the app shows when one is reached.
    plan: str
    # The region its data is kept in, as DATA_REGION names it (eu, us), None when the
    # deployment promised none as it was created (app/residency.py).
    region: str | None
    # How many days a chat is kept after its last question (app/retention.py), None for good.
    chat_retention_days: int | None = None


class MeOut(BaseModel):
    """The signed-in user, and the organisation and role they act with."""

    id: int
    email: str
    name: str
    email_verified: bool
    mfa_enabled: bool
    # Providers the account can sign in through, such as "google".
    identities: list[str]
    organization: OrganizationOut
    role: str
    # Whether the account has a password, which then confirms its deletion.
    has_password: bool
    # When the account will be purged, if its user asked for it to be deleted
    # (app/retention.py); until then the app offers nothing but keeping it.
    deletion_due_at: dt.datetime | None
    # The edition of the terms of service and privacy policy the user accepted and when
    # (app/legal/), and whether the current edition still waits for them: until it is
    # accepted, the app offers nothing but accepting it, a copy of the data, and leaving.
    terms_version: str | None
    terms_accepted_at: dt.datetime | None
    terms_due: bool


class SessionOut(BaseModel):
    id: int
    user_agent: str
    ip: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    # The session the request itself came with.
    current: bool


class OAuthProviderOut(BaseModel):
    name: str
    label: str


class AuditEventOut(BaseModel):
    """One row of the organisation's audit log (app/audit.py)."""

    id: int
    occurred_at: dt.datetime
    # One of AUDIT_ACTIONS in app/models.py, such as "document_deleted".
    action: str
    # The member who acted, or whose account a sign-in was for, as the account is now; all
    # three None for what the operator or the app did, and once the account is deleted.
    user_id: int | None
    user_name: str | None
    user_email: str | None
    ip: str | None
    project_id: int | None
    # What it was done to: "document" and its id. The object may be gone since.
    target_type: str | None
    target_id: int | None
    # Names and settings as they were: {"filename": "invoice.pdf"}.
    detail: dict[str, Any]


class AuditPage(BaseModel):
    """A page of the audit log, the newest row first."""

    events: list[AuditEventOut]
    # What to pass as `before` for the next, older page; None on the last one.
    next_before: int | None


class AuthOptions(BaseModel):
    """What the sign-in page can offer besides a password."""

    providers: list[OAuthProviderOut]


class LLMStatus(BaseModel):
    available: bool
    agent_model: str
    index_model: str
    # What is asked when the model above cannot answer (AGENT_FALLBACK_MODEL,
    # INDEX_FALLBACK_MODEL); None without one.
    agent_fallback_model: str | None
    index_fallback_model: str | None
    error: str | None


class EmbeddingStatus(BaseModel):
    ok: bool
    model: str
    error: str | None


class StatusOut(BaseModel):
    llm: LLMStatus
    embeddings: EmbeddingStatus
    # The currency the costs on usage are in (PRICE_CURRENCY), an ISO 4217 code.
    currency: str


class SubprocessorOut(_FromORM):
    """A company the deployment sends data to (app/subprocessors.py)."""

    name: str
    purpose: str
    data: str
    # Where it handles what it receives, when the settings show it (app/residency.py).
    location: str | None


class SubprocessorsOut(BaseModel):
    """The /subprocessors page: the region the deployment keeps its documents in (DATA_REGION:
    eu, us), None when it promises none, and the companies it sends data to."""

    region: str | None
    subprocessors: list[SubprocessorOut]


class LegalOut(BaseModel):
    """The /terms and /privacy pages (app/legal/): the current edition, its date in words, and
    the two documents as Markdown, filled in from the deployment's settings."""

    version: str
    published: str
    terms: str
    privacy: str


class HealthOut(BaseModel):
    status: Literal["ok"]
    # The commit the image was built from, or "development" (app/version.py).
    version: str


class PageFrame(BaseModel):
    """A frame of the page's own scripts that an error was raised in: the function's name as
    the browser gives it, minified, and where it is in the built file (`frames` in
    frontend/src/errors.ts)."""

    function: str | None = Field(
        None, max_length=200, pattern=r"^(?:new |async )?[A-Za-z0-9_$.<>\[\]/-]+$"
    )
    file: str = Field(max_length=200, pattern=r"^/assets/[A-Za-z0-9._-]+\.js$")
    line: int = Field(ge=0)
    column: int = Field(ge=0)


class PageErrorIn(BaseModel):
    """An error the page rendered into, as its error boundary reports it (app/tracking.py): the
    error's kind, the status the API answered when it was the API's refusal, the frames of the
    page's scripts, the innermost first, and the page's path without its query. Never its
    message, which may quote what the page showed, nor anything else of the page."""

    name: str = Field(max_length=100, pattern=r"^[A-Za-z_$][A-Za-z0-9_$.]*$")
    status: int | None = Field(None, ge=100, le=599)
    frames: list[PageFrame] = Field(default_factory=list, max_length=50)
    path: str = Field(max_length=200, pattern=r"^/[A-Za-z0-9/_.~*-]*$")


class PageErrorOut(BaseModel):
    # The id the report was logged under, for the page to show as the reference to quote.
    reference: str | None


# The account export (app/export.py): what the app keeps about a person, as the file the
# account page downloads. The app itself never reads it back.


class ExportedIdentity(BaseModel):
    provider: str
    email: str
    linked_at: dt.datetime


class ExportedAccount(BaseModel):
    email: str
    name: str
    created_at: dt.datetime
    email_verified_at: dt.datetime | None
    two_factor_enabled_at: dt.datetime | None
    terms_version: str | None
    terms_accepted_at: dt.datetime | None
    deletion_due_at: dt.datetime | None
    sign_in_providers: list[ExportedIdentity]


class ExportedMembership(BaseModel):
    organization: str
    slug: str
    role: str
    joined_at: dt.datetime


class ExportedSession(BaseModel):
    user_agent: str
    ip: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None


class ExportedMessage(BaseModel):
    role: str
    content: str
    status: str
    notice: str | None
    error: str | None
    created_at: dt.datetime


class ExportedChat(BaseModel):
    organization: str
    project: str
    title: str
    visibility: str
    created_at: dt.datetime
    last_question_at: dt.datetime
    messages: list[ExportedMessage]


class ExportedActivity(BaseModel):
    """A row of an organisation's audit log that names the user (app/audit.py)."""

    organization: str
    action: str
    ip: str | None
    detail: dict[str, Any]
    occurred_at: dt.datetime


class ExportedUsage(BaseModel):
    organization: str
    kind: str
    unit: str
    quantity: int
    model: str | None
    occurred_at: dt.datetime


class AccountExport(BaseModel):
    exported_at: dt.datetime
    # What the file holds and what it leaves out, in words, for whoever reads it.
    about: str
    account: ExportedAccount
    organizations: list[ExportedMembership]
    sessions: list[ExportedSession]
    chats: list[ExportedChat]
    usage: list[ExportedUsage]
    activity: list[ExportedActivity]
