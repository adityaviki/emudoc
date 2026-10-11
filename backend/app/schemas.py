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
    status: Literal["queued", "processing", "ready", "failed"]
    stages: dict[str, StageOut]
    error: str | None
    page_count: int | None
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    summary: str | None
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


class ChatMessageOut(_FromORM):
    id: int
    role: Literal["user", "assistant"]
    content: str
    steps: list[ChatStep]
    sources: list[SourceOut]
    notice: str | None
    error: str | None
    created_at: dt.datetime


class ChatDetail(ChatOut):
    messages: list[ChatMessageOut]


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


class OrganizationOut(_FromORM):
    id: int
    name: str
    slug: str


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


class AuthOptions(BaseModel):
    """What the sign-in page can offer besides a password."""

    providers: list[OAuthProviderOut]


class LLMStatus(BaseModel):
    available: bool
    agent_model: str
    index_model: str
    error: str | None


class EmbeddingStatus(BaseModel):
    ok: bool
    model: str
    error: str | None


class StatusOut(BaseModel):
    llm: LLMStatus
    embeddings: EmbeddingStatus


class HealthOut(BaseModel):
    status: Literal["ok"]
