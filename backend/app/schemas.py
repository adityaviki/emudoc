import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class _FromORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# PROJECT_VISIBILITIES in app/models.py, which the database's check constraint holds.
ProjectVisibility = Literal["organization", "private"]
# The figures of a plan a refusal or an excess is about (`Entitlements` in app/entitlements.py).
PlanLimit = Literal[
    "projects",
    "documents",
    "storage_bytes",
    "pages_per_month",
    "questions_per_month",
    "members",
    "daily_spend_micros",
    "api_access",
]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


class NewProjectIn(ProjectIn):
    """A project to make: a private one is seen by its maker alone at first, and the owners and
    admins, until its maker chooses others."""

    visibility: ProjectVisibility = "organization"


class ProjectOut(_FromORM):
    id: int
    name: str
    description: str
    created_at: dt.datetime
    visibility: str
    # The sample project a new organisation starts with (app/sample), which takes no uploads,
    # and the questions an empty chat in a project offers, none being the page's own.
    sample: bool = False
    suggestions: list[str] = Field(default_factory=list)
    document_count: int = 0
    # The documents that are partly indexed (`unfinished_stages`, app/indexing/pipeline.py),
    # which the library offers to index the rest of, and those of them without summaries and
    # facts, which wait for the model as well as for the embeddings endpoint.
    partly_indexed_count: int = 0
    unenriched_count: int = 0


class TrashedProjectOut(BaseModel):
    """A project in the organisation's trash (app/trash.py), with what it holds."""

    id: int
    name: str
    sample: bool
    document_count: int
    deleted_at: dt.datetime
    # Who deleted it; null once their account is gone.
    deleted_by: str | None
    # When the housekeeper deletes it for good, unless it is restored first.
    purged_at: dt.datetime


class TrashOut(BaseModel):
    """The organisation's projects in the trash that the account sees, the latest deleted
    first, and how many days the trash keeps what is put in it."""

    projects: list[TrashedProjectOut]
    days: int


class TrashedOut(BaseModel):
    """Something in a project's trash: a folder, with the documents that went with it, a
    document deleted by itself, or one of the asker's own chats."""

    kind: Literal["folder", "document", "chat"]
    id: int
    # The folder's or the file's name, or the chat's title (empty before its first question).
    name: str
    # The path of the folder it was in; null at the project's root, and for a chat.
    place: str | None
    documents: int
    mime_type: str | None
    deleted_at: dt.datetime
    deleted_by: str | None
    purged_at: dt.datetime


class ProjectTrashOut(BaseModel):
    """The newest of what a project's trash holds for the asker, newest first, how much it holds
    in all, and how many days it keeps it."""

    items: list[TrashedOut]
    total: int
    days: int


class ProjectAccessIn(BaseModel):
    """Who is to see a project besides the organisation's owners and admins: everyone in it, or
    the members chosen. An open project keeps no members, so `user_ids` counts only for a
    private one."""

    visibility: ProjectVisibility
    user_ids: list[int] = Field(default_factory=list, max_length=1000)


class ProjectAccessOut(BaseModel):
    """Who sees a project besides the organisation's owners and admins: everyone in it, or the
    members chosen, by user id."""

    visibility: str
    user_ids: list[int]


# FACT_TYPES in app/models.py: what a fact's value is, and so a template's field.
FactType = Literal["text", "number", "date", "money"]

# A template's ceilings (app/extraction.py). Every template in force is listed in the enrich
# stage's prompt, so these bound what reading each of the project's documents costs.
MAX_TEMPLATE_FIELDS = 30
MAX_FIELD_EXAMPLES = 3
MAX_TEMPLATE_WORDS = 300
MAX_EXAMPLE_CHARS = 100

_Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TEMPLATE_WORDS)]


class TemplateFieldIn(BaseModel):
    """A field of an extraction template: its name, which its facts are named by, snake_case
    once the server has made it so; its type; what it is; whether every document of the kind
    should state it; and values as documents write them, which show the model what to find."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    type: FactType
    description: _Line = ""
    required: bool = False
    examples: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_EXAMPLE_CHARS),
        ]
    ] = Field(default_factory=list, max_length=MAX_FIELD_EXAMPLES)


class TemplateIn(BaseModel):
    """A project's extraction template as a member writes it, whole: the kind of document it is
    for (lowercase words once the server has made them so), its name, what the kind is, and its
    fields in the order they are asked for."""

    doc_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: _Line = ""
    fields: list[TemplateFieldIn] = Field(min_length=1, max_length=MAX_TEMPLATE_FIELDS)


class TemplateFieldOut(BaseModel):
    name: str
    type: FactType
    description: str
    required: bool
    examples: list[str]


class TemplateOut(BaseModel):
    """An extraction template in force for a project: its own (`id` set) or a built-in one
    (`built_in`, `id` null), which applies while the project has none of its own kind."""

    id: int | None
    doc_type: str
    title: str
    description: str
    fields: list[TemplateFieldOut]
    built_in: bool
    # A project's own template of a kind a built-in one is for, which it replaces.
    replaces_built_in: bool
    # The project's documents whose facts were last read for it.
    documents: int
    # Those of its kind read for no template, such as the ones read before it was made, which
    # reading its documents again takes too.
    matching: int
    # Its documents and those waiting for the enrich stage, read again or retried.
    reading: int
    # Those whose last reading of its kind changed a field or the template they are read for.
    changed: int
    updated_at: dt.datetime | None


_Kind = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ReadAgainIn(BaseModel):
    """Which of a project's documents to read again for its templates as they are now: those of
    the kind a template is for (`kind`: read for it, and of its kind read for none), and those
    read for a kind no template is for any more, as before a template's kind was changed or it
    was deleted (`was`)."""

    kind: _Kind | None = None
    was: _Kind | None = None


class ReadAgainOut(BaseModel):
    queued: int


class FieldChangeOut(BaseModel):
    """A field whose facts a document's last reading changed (app/fact_changes.py): the values
    read before and no longer, and those read now and not before, each list cut at a few of
    the counts; `checked` when a value gone was a member's check, which the new reading
    disagrees with."""

    name: str
    before: list[str]
    after: list[str]
    before_count: int
    after_count: int
    checked: bool


class DocumentChangesOut(BaseModel):
    """What a document's last reading changed: the template it was read for before and now
    (null for none), and its fields."""

    document_id: int
    title: str
    filename: str
    read_at: dt.datetime
    template_before: str | None
    template_after: str | None
    fields: list[FieldChangeOut]


class FactChangesOut(BaseModel):
    """The last readings of a project's documents of one kind, before or now: the template in
    force for the kind (null for none), how many of its documents wait to be read, how many
    were last read and how many of those changed something, how many disagree with a member's
    check, and a page of those that changed, those first, then the newest."""

    kind: str
    template: TemplateOut | None
    reading: int
    read: int
    changed: int
    unheld: int
    documents: list[DocumentChangesOut]
    next_after: str | None


class ProjectTemplatesOut(BaseModel):
    """The templates in force for a project: its own by title, then the built-in ones of the
    kinds it has none of; and how many of its own it may have."""

    templates: list[TemplateOut]
    max_templates: int


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
    # The documents directly in the folder, not in its subfolders: the library lists them a
    # page at a time, so the count is what says whether the folder has anything to open.
    document_count: int = 0


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
    # While a failed stage waits to run again by itself (app/worker.py): when, and whether for
    # the day's spending limit to reset rather than after a backoff. None once it runs or
    # "Index again" overtakes it, and for a wait from before the field.
    retry_at: dt.datetime | None = None
    deferred: bool = False


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
    # The kind of the extraction template its facts were read for (app/extraction.py), if any.
    template: str | None
    # By kind: `upload` (bytes, pages), `ocr`, `infer_sections`, `enrich`, `embed`, `render`.
    usage: dict[str, UsageOut]
    created_at: dt.datetime


class DocumentPage(BaseModel):
    """A page of the documents directly in one folder, in the library's order (by name, the
    `natural_order` collation in app/models.py)."""

    documents: list[DocumentOut]
    # What to pass as `after` for the next page; None on the last one.
    next_after: str | None


class RejectedFile(BaseModel):
    filename: str
    reason: str
    # The figure of the plan that refused the file, when it was the plan (app/entitlements.py).
    limit: PlanLimit | None = None


class UploadResult(BaseModel):
    documents: list[DocumentOut]
    rejected: list[RejectedFile]
    # The upload the documents kept are, which the page follows as they are indexed (GET
    # /api/projects/{id}/uploads); None when the request kept none.
    upload_id: int | None = None


class UploadFailureOut(BaseModel):
    """A document of an upload that could not be indexed, and why, in the words its tab shows."""

    id: int
    filename: str
    reason: str
    # Set apart by the malware scan rather than failed (app/scanning.py).
    quarantined: bool


class UploadStatusOut(BaseModel):
    """How far the documents of some uploads are indexed, those in the trash left out. Each is
    counted once: ready (partly indexed or not), indexing, waiting for a retry of a stage that
    failed for a reason that passes, or failed; so `documents` is the sum of the four."""

    documents: int
    ready: int
    # Of the ready, those without summaries and facts or their vectors, while no retry waits.
    partly_indexed: int
    indexing: int
    retrying: int
    # When the soonest retry is due; None while none waits.
    retry_at: dt.datetime | None
    # Failed or set apart by the malware scan.
    failed: int
    # The first of them by name, at most LISTED; `failed` counts them all.
    failures: list[UploadFailureOut]


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


class FactCheckOut(BaseModel):
    """A member's check of a fact (app/fact_checks.py): who and when, and with a correction what
    the model read in its place."""

    id: int
    # The member's name as it is now; None once their account is gone.
    checked_by: str | None
    checked_at: dt.datetime
    # The model's value, when the member wrote another; None for a check of the model's own.
    corrected_from: str | None
    # What the member said the field holds: their value, or the model's they checked.
    value: str


class FactCheckIn(BaseModel):
    """A fact checked as it reads now (no `value`), or corrected to the value the document states:
    words for a text, digits for a number or an amount (with its currency, else the fact's), a
    date as 2026-03-01."""

    value: str | None = Field(default=None, max_length=4000)
    currency: str | None = Field(default=None, max_length=10)


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
    # Whether the model's quote was found in the text; a person's word is `check`.
    verified: bool
    check: FactCheckOut | None


class FactList(BaseModel):
    total: int
    facts: list[FactOut]


class FactRowOut(BaseModel):
    """A row of the facts table (app/fact_table.py): a document, and the facts of each of the
    template's fields it has, in the document's order; a field it has none of is left out."""

    document_id: int
    title: str
    filename: str
    cells: dict[str, list[FactOut]]
    # The checks of a field no fact reads any more, by the field's name: made before the
    # document was read again, which read it differently.
    earlier: dict[str, list[FactCheckOut]]


class FactTableOut(BaseModel):
    """A page of the facts table of one kind of document: the template whose fields are its
    columns, the rows, how many rows the conditions keep, and where the next page starts."""

    template: TemplateOut
    rows: list[FactRowOut]
    total: int
    # How many of those rows hold a fact of the fields no member has checked.
    to_review: int
    next_after: str | None


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
    # Who started the chat, and their name: a shared chat is read by the project's other
    # members, but asked in, renamed and deleted by its starter alone.
    user_id: int
    started_by: str
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


class RetryOut(BaseModel):
    """An answer tried again, emptied and streaming, and the id of the last event its earlier
    tries left: sent as `Last-Event-ID`, it follows the new try's events alone, the first of
    them `restart`. Null when there are none, and the new try's are followed from the start."""

    answer: ChatMessageOut
    last_event_id: int | None


# Enough to catch a typo; the verification email settles whether the address is real.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# NIST SP 800-63B: a minimum length and no composition rules; the routes refuse a breached
# password (app/passwords.py).
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200

Email = Annotated[str, Field(max_length=320, pattern=EMAIL_PATTERN)]
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
# An IANA zone such as Europe/Berlin; which names are known is the route's to check.
TimeZone = Annotated[str, Field(max_length=64)]


class SignupIn(BaseModel):
    """The address an account is asked for. The rest is chosen on the page its link opens, by
    whoever holds the address."""

    email: Email


class SignupLinkOut(BaseModel):
    """The address a sign-up link was sent to, which the account it makes will have."""

    email: str


class LinkTokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class SignupFinishIn(LinkTokenIn):
    """What the page a sign-up link opens makes the account with."""

    name: str = Field("", max_length=200)
    password: Password
    # The edition of the terms of service and privacy policy the form showed and the person
    # ticked (TERMS_VERSION, app/legal/); an account is made only when it is the current one.
    terms_version: str | None = Field(None, max_length=40)
    # The browser's zone, for the dates in the account's mail; one the app does not know is
    # left out rather than refused.
    time_zone: TimeZone = ""


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


# The kinds of notice (NOTICE_KINDS in app/models.py, app/notices.py).
NoticeKind = Literal["upload_indexed", "quota", "project_export"]


class NoticeChoiceIn(BaseModel):
    """A change to how the user is told one kind of notice; a way left out stays as it is."""

    kind: NoticeKind
    # In the app's notification centre.
    app: bool | None = None
    # By email (app/postbox.py); turning it on writes to an address the mail provider said to
    # spare (app/bounces.py) again.
    email: bool | None = None


class NoticeChoiceOut(BaseModel):
    """How the user is told one kind of notice: in the app's notification centre, by email,
    both or neither."""

    kind: NoticeKind
    app: bool
    email: bool


class ProfileIn(BaseModel):
    """What the account page changes about its user; a field left out stays as it is."""

    name: str | None = Field(None, max_length=200)
    time_zone: TimeZone | None = None
    notices: list[NoticeChoiceIn] | None = Field(None, max_length=20)


class OnboardingIn(BaseModel):
    """Whether the projects page hides its list of first steps, whether the tour of a
    project's workspace was seen, and whether the chat hides its suggestion to invite the team
    (app/onboarding.py); a field left out stays as it is."""

    checklist_hidden: bool | None = None
    tour_seen: bool | None = None
    invite_hidden: bool | None = None


class SupportAccessIn(BaseModel):
    """How many days from now the member lets Emudoc's support see their account in the
    organisation they work in (app/support.py), a week at most, SUPPORT_DAYS."""

    days: int = Field(ge=1, le=7)


class SupportViewOut(BaseModel):
    """The view of a member's account an operator holds as support, which the page says on
    every page while it lasts."""

    ends_at: dt.datetime


class OnboardingStepOut(BaseModel):
    """One of the organisation's first steps, and whether it is taken."""

    key: Literal["upload", "ask", "invite"]
    done: bool


class OnboardingOut(BaseModel):
    """The first steps the member's role is offered (app/onboarding.py), in the order the
    projects page lists them, each with whether the organisation has taken it."""

    steps: list[OnboardingStepOut]


class EmailChangeIn(BaseModel):
    """The address to move the account to, confirmed as a deletion is: by the password, or, for
    an account that has none and signs in through a provider, its current address typed out."""

    email: Email
    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    current_email: str | None = Field(None, max_length=320)


class ExcessOut(BaseModel):
    """A figure the organisation holds past, and how it reads: "3 projects of 1"."""

    limit: PlanLimit
    words: str


class AllowanceOut(BaseModel):
    """What the organisation may keep and use (`entitlements_of` in app/entitlements.py), for
    the organisation page: None is no limit."""

    projects: int | None
    documents: int | None
    storage_bytes: int | None
    pages_per_month: int | None
    questions_per_month: int | None
    members: int | None
    # How many days the plan, or the terms agreed in its place, keeps a chat after its last
    # question.
    retention_days: int | None = None
    # Whether its members may make API keys and call the API with them (app/apikeys.py).
    api_access: bool = False
    # Which of them were agreed with the organisation in place of its plan's.
    agreed: list[str]
    # The pages and questions bought in packs for this month, on top of the plan's.
    pages_bought: int = 0
    questions_bought: int = 0
    # What it holds past them, once they were lowered under it, which makes it read-only until
    # it is within them again (`over_limits`).
    over: list[ExcessOut] = []


class FigureOut(BaseModel):
    """One figure the plan sets, beside what the organisation holds or has used of it, as the
    checks count it (app/entitlements.py): what it holds now (projects, documents, storage,
    members with the open invitations), or the month so far in UTC (pages, questions)."""

    used: int
    # What it may, None for no limit.
    allowed: int | None
    # The pages or questions bought in packs for the month, on top of `allowed`.
    bought: int = 0


class MeteredOut(BaseModel):
    """The pages the organisation's uploads brought in and the questions it asked over one UTC
    day, or one month from its first day (`metered` in app/usage.py)."""

    start: dt.date
    pages: int
    questions: int


class MeteredMonthOut(MeteredOut):
    """A month's pages and questions, with the packs of them bought for it."""

    pages_bought: int = 0
    questions_bought: int = 0


class UsagePageOut(BaseModel):
    """What the organisation holds and has used of what its plan allows, and its pages and
    questions day by day and month by month, for the usage page (app/api/usage.py)."""

    # The first day of the month in UTC the monthly figures count, and of the next, when they
    # start afresh.
    month: dt.date
    next_month: dt.date
    projects: FigureOut
    documents: FigureOut
    storage_bytes: FigureOut
    members: FigureOut
    pages_per_month: FigureOut
    questions_per_month: FigureOut
    # The characters of text a document without pages counts a page for (app/entitlements.py).
    chars_per_page: int
    # The documents of the sample project, which `projects` and `documents` leave out
    # (app/sample); 0 once it is deleted.
    sample_documents: int
    # Every day of the last few up to today, and every month of the last few up to this one,
    # the oldest first.
    days: list[MeteredOut]
    months: list[MeteredMonthOut]


class PlanRefusalOut(BaseModel):
    """The body of a 402, what the organisation's plan turned down: the sentence, and the
    figure it is held to, from which the page chooses what to offer (a pack for the month, a
    larger plan, more seats)."""

    detail: str
    limit: PlanLimit


class PackOut(BaseModel):
    """A pack of pages or questions for the month, as STRIPE_PACKS sells it."""

    unit: Literal["pages", "questions"]
    quantity: int


class PackIn(PackOut):
    """The pack an owner buys for the organisation's month."""


class BillingOut(BaseModel):
    """How the organisation buys its plan (app/billing.py), for the organisation page."""

    # The plans the deployment sells, in the order of the plans; empty while billing is off.
    sold: list[str]
    # Whether billing moves its plan: not when its terms were agreed with the operator.
    managed: bool
    # What Stripe last said of its subscription (trialing, active, past_due, canceled, unpaid,
    # …), None before it had one; when its trial ends, when it renews or ends, since when a
    # payment has failed, and when the plan stops waiting for that payment.
    status: str | None
    trial_ends_at: dt.datetime | None
    renews_at: dt.datetime | None
    ends_at: dt.datetime | None
    payment_failed_at: dt.datetime | None
    grace_ends_at: dt.datetime | None
    # Whether it is a customer at Stripe, whose billing an owner can manage.
    customer: bool
    # The days of trial a subscription bought now starts with, None for none.
    trial_days: int | None
    # The packs it may buy for this month: pages or questions, and how many.
    packs: list[PackOut] = []


class CheckoutIn(BaseModel):
    """The plan an owner buys, with its seats on the Team plan, sold by the seat."""

    plan: Literal["pro", "team"]
    seats: int | None = Field(None, ge=1, le=10_000)


class PlanChoiceIn(BaseModel):
    """The plan an owner would move the organisation's subscription to, with its seats on
    Team; Free is cancelling it."""

    plan: Literal["free", "pro", "team"]
    seats: int | None = Field(None, ge=1, le=10_000)


class PortalIn(BaseModel):
    """What Stripe's portal opens on: its home page, or with `plan` the one step that moves
    the subscription there (app/billing.py, `portal_for`)."""

    plan: Literal["free", "pro", "team"] | None = None
    seats: int | None = Field(None, ge=1, le=10_000)


class RetentionOut(BaseModel):
    """How many days chats are kept after their last question and audit log entries; None for
    good (app/retention.py)."""

    chat_days: int | None
    audit_days: int | None


class RetentionHeldOut(RetentionOut):
    """What an organisation keeps as before after a move to a plan that keeps less, and until
    when; None for a move whose moment is not known yet."""

    until: dt.datetime | None


class PlanChangeOut(BaseModel):
    """What moving the organisation's subscription to a plan would do (`plan_change` in
    app/billing.py), for an owner to read before Stripe's page: what it would allow, what the
    organisation would hold past that (read-only until it is within it), how long it would keep
    chats and audit log entries, and what it keeps now that would be held for `held_days` after
    the move (None when the plan keeps everything as long)."""

    plan: str
    seats: int | None
    # When a cancellation would move it, the end of the period paid for; None for a change of
    # plan, which Stripe makes once it is confirmed there.
    at: dt.datetime | None
    allowance: AllowanceOut
    retention: RetentionOut
    held: RetentionHeldOut | None
    held_days: int


class CheckoutDoneIn(BaseModel):
    """The Checkout session an owner came back from, as Stripe named it in the address."""

    checkout_id: str = Field(max_length=255, pattern=r"^cs_[A-Za-z0-9_]+$")


class RedirectOut(BaseModel):
    """A page of Stripe's to send the owner to."""

    url: str


class OrganizationOut(_FromORM):
    id: int
    name: str
    slug: str
    # Which plan's limits the organisation is held to (app/entitlements.py), for the prompt
    # the app shows when one is reached.
    plan: str
    # The members it bought, on a plan sold by the seat; None otherwise.
    seats: int | None = None
    allowance: AllowanceOut | None = None
    billing: BillingOut | None = None
    # The region its data is kept in, as DATA_REGION names it (eu, us), None when the
    # deployment promised none as it was created (app/residency.py).
    region: str | None
    # How many days a chat is kept after its last question (app/retention.py), None for good,
    # by its plan or by its own choice, whichever is shorter, and an audit log entry.
    chat_retention_days: int | None = None
    audit_retention_days: int | None = None
    # What it keeps as before after a move to a plan that keeps less, and until when, with its
    # own choice applied; None when nothing is held.
    retention_held: RetentionHeldOut | None = None
    # Its settings, as its owners and admins chose them: the language its documents are
    # searched in (a text search configuration, app/languages.py), None for the deployment's;
    # the days it keeps a chat, None for as long as its plan does; and how its members are told
    # each kind of notice until they choose for themselves, in the order of NOTICE_KINDS.
    language: str | None
    chat_days: int | None
    notice_defaults: list[NoticeChoiceOut] = []
    # Its security settings (app/security.py): whether everyone who works in it signs in with
    # two-factor, and the days a session may work in it after signing in, None for as long as
    # the deployment keeps one, which is the most it may choose (`max_session_days`).
    mfa_required: bool
    session_days: int | None
    max_session_days: int | None = None
    # The domains whose confirmed addresses may join it, and the role they join with
    # (app/invitations.py).
    join_domains: list[str]
    join_role: str
    # When it is deleted with everything in it, as an owner asked, until when it is read-only
    # and an owner may keep it (app/retention.py); None when nobody asked.
    deletion_due_at: dt.datetime | None = None
    # The feature flags on for it (app/flags.py), by name: the features being rolled out that
    # the page shows it.
    flags_on: list[str] = []


class MembershipOut(BaseModel):
    """One of the organisations a user is a member of, for the page to offer a switch to."""

    id: int
    name: str
    role: str


class JoinableOut(BaseModel):
    """An organisation the user may join, since it lets everyone at their address's domain."""

    id: int
    name: str
    # The role they would join with.
    role: str


class MeOut(BaseModel):
    """The signed-in user, and the organisation and role they act with."""

    id: int
    email: str
    name: str
    # The zone the account's mail is dated in, empty for UTC until its user chooses one.
    time_zone: str
    # How the user is told each kind of notice, the news they may do without (app/notices.py),
    # in the order of NOTICE_KINDS.
    notices: list[NoticeChoiceOut]
    email_verified: bool
    mfa_enabled: bool
    # Whether the organisation requires two-factor sign-in and the account signs in without
    # it: until it is set up, the app offers nothing there but that, a switch, leaving, and
    # signing out (app/security.py).
    mfa_due: bool
    # Providers the account can sign in through, such as "google".
    identities: list[str]
    organization: OrganizationOut
    role: str
    # Every organisation the user is a member of, the current one among them, by name.
    organizations: list[MembershipOut]
    # The organisations the domain of the user's confirmed address lets them join, by name.
    joinable: list[JoinableOut]
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
    # Whether the user hid the projects page's list of first steps, whether they ended the tour
    # of a project's workspace, and whether they hid the chat's suggestion to invite their team,
    # for every organisation (app/onboarding.py).
    checklist_hidden: bool
    tour_seen: bool
    invite_hidden: bool
    # How many of what's new's entries the user has not read (app/changelog.py), the newest,
    # which the help menu marks.
    changes_unread: int
    # Until when the user lets Emudoc's support see their account in this organisation, None
    # while they do not (app/support.py).
    support_until: dt.datetime | None
    # Set while an operator views the account as support rather than the user signed in: read
    # only, the organisation alone.
    support_view: SupportViewOut | None


class OrganizationIn(BaseModel):
    """The name of a new organisation."""

    name: str = Field(max_length=200)


class OrganizationChangeIn(BaseModel):
    """What the organisation page changes about the current organisation; a field left out
    stays as it is, and a null `language` or `chat_days` goes back to the deployment's or the
    plan's."""

    name: str | None = Field(None, max_length=200)
    # A text search configuration's name, which the database must have.
    language: str | None = Field(None, pattern=r"^[a-z_]{1,63}$")
    # Never more than the plan keeps a chat.
    chat_days: int | None = Field(None, ge=1, le=36500)
    notices: list[NoticeChoiceIn] | None = Field(None, max_length=20)
    # Turned on only by someone who signs in with two-factor themselves.
    mfa_required: bool | None = None
    # Never more than the deployment's SESSION_DAYS.
    session_days: int | None = Field(None, ge=1, le=36500)


class LanguagesOut(BaseModel):
    """The languages an organisation may search its documents in, the text search
    configurations the database has, and the deployment's, which it has until it chooses."""

    languages: list[str]
    default: str


class MemberOut(BaseModel):
    """A member of the organisation, as its other members see them."""

    user_id: int
    name: str
    email: str
    role: str
    joined_at: dt.datetime
    # Whether they sign in with two-factor, which the owners and admins are shown, to know whom
    # requiring it would hold; None for anyone else.
    mfa_enabled: bool | None = None


# ROLES in app/models.py, which the database's check constraints hold.
Role = Literal["owner", "admin", "member", "viewer"]


class MemberRoleIn(BaseModel):
    """The role a member of the organisation is to have from now on."""

    role: Role


class InvitationIn(BaseModel):
    """An address to invite to the organisation, and the role it joins with."""

    email: Email
    role: Role = "member"


class InvitationOut(BaseModel):
    """An invitation still open, or expired and not sent again, as its organisation's owners
    and admins see it."""

    id: int
    email: str
    role: str
    # Who sent it last, by name or address; None once their account is gone.
    invited_by: str | None
    created_at: dt.datetime
    expires_at: dt.datetime
    expired: bool


# API_KEY_SCOPES in app/models.py, which the database's check constraint holds.
ApiKeyScope = Literal["read", "write"]


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: ApiKeyScope = "read"
    # How many days it runs, a year at most; None runs until it is revoked.
    days: int | None = Field(None, ge=1, le=366)


class ApiKeyOut(BaseModel):
    """An API key of the organisation, as its owners and admins see it (app/apikeys.py): never
    the key itself, which is shown once, as it is made or rolled."""

    id: int
    name: str
    scope: ApiKeyScope
    # Its first characters, to tell it from the others.
    hint: str
    # The member it acts as, and their name or address as it is now.
    user_id: int
    made_by: str
    created_at: dt.datetime
    rolled_at: dt.datetime | None
    last_used_at: dt.datetime | None
    expires_at: dt.datetime | None
    expired: bool


class ApiKeyMadeOut(ApiKeyOut):
    """A key just made or rolled, with the key itself, which nothing shows again."""

    key: str


class InvitationLinkOut(BaseModel):
    """What an invitation's link is for, as its page shows it before anything is done."""

    organization: str
    role: str
    # The address it was sent to, which the account that accepts it must have.
    email: str
    # Who sent it, by name or address, "" once their account is gone.
    invited_by: str
    expires_at: dt.datetime
    expired: bool
    # Whether an account has the address, which then signs in to accept rather than signing up.
    has_account: bool


class InvitationSignupIn(LinkTokenIn):
    """The account a new person makes on an invitation's page, as a sign-up link's page makes
    one, without an organisation of its own."""

    name: str = Field("", max_length=200)
    password: Password
    terms_version: str | None = Field(None, max_length=40)
    time_zone: TimeZone = ""


class JoiningIn(BaseModel):
    """The domains whose confirmed addresses may join the organisation, and the role they join
    with; no domains, nobody joins that way. Twenty is plenty for one company's."""

    domains: list[Annotated[str, Field(max_length=253)]] = Field(max_length=20)
    role: Literal["member", "viewer"] = "member"


class OrganizationDeletionIn(BaseModel):
    """What confirms an owner's request to delete the organisation, as a deletion of the account
    is confirmed: by the password, or, for an account that has none, its address typed out."""

    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    email: str | None = Field(None, max_length=320)


class OwnershipIn(BaseModel):
    """Whom the organisation is handed to, confirmed as a deletion is: by the password, or, for
    an account that has none and signs in through a provider, its address typed out."""

    user_id: int
    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    email: str | None = Field(None, max_length=320)


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
    # The project's name as it is now, None once it is gone.
    project_name: str | None = None
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


class NotificationOut(BaseModel):
    """A notice in the user's notification centre (app/notices.py)."""

    id: int
    kind: NoticeKind
    title: str
    body: str
    # The page it leads to, a path in the organisation `org_id`, which may be another than the
    # one the session works in, and its name as it is now.
    link: str
    org_id: int
    organization: str
    created_at: dt.datetime
    read_at: dt.datetime | None


class NotificationPage(BaseModel):
    """A page of the user's notices, the newest first, and how many of all of them are unread."""

    notifications: list[NotificationOut]
    unread: int
    # What to pass as `before` for the next, older page; None on the last one.
    next_before: int | None


class NotificationsReadIn(BaseModel):
    """The notices to mark read, of the user's own; none named marks every one."""

    ids: list[int] | None = Field(None, max_length=200)


class AuthOptions(BaseModel):
    """What the sign-in page can offer besides a password."""

    providers: list[OAuthProviderOut]


class LLMStatus(BaseModel):
    """Whether the models can be used. The names are the operator's, given in development only
    (`status` in main.py); `error` is a sentence for whoever reads the status: what to change in
    development, what members cannot do for now in a deployment."""

    available: bool
    agent_model: str | None = None
    index_model: str | None = None
    # What is asked when the model above cannot answer (AGENT_FALLBACK_MODEL,
    # INDEX_FALLBACK_MODEL); None without one.
    agent_fallback_model: str | None = None
    index_fallback_model: str | None = None
    error: str | None


class EmbeddingStatus(BaseModel):
    """Whether the embeddings endpoint answers; its model is named in development only, like
    the models above."""

    ok: bool
    model: str | None = None
    error: str | None


class WorkerStatus(BaseModel):
    """Whether a worker takes the queue's work (app/jobs.py), without which uploads wait to be
    indexed and questions to be answered; `error` is worded as the models' is."""

    running: bool
    error: str | None


class UploadLimitsOut(BaseModel):
    """What one upload request may carry (MAX_UPLOAD_FILES, MAX_UPLOAD_REQUEST_MB, MAX_UPLOAD_MB),
    so that the page sends a folder of more in several and leaves a file of more unsent."""

    files: int
    request_bytes: int
    file_bytes: int


class StatusOut(BaseModel):
    llm: LLMStatus
    embeddings: EmbeddingStatus
    workers: WorkerStatus
    # The currency the costs on usage are in (PRICE_CURRENCY), an ISO 4217 code.
    currency: str
    uploads: UploadLimitsOut


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


class HelpArticleOut(BaseModel):
    """A help page (app/help/), filled in for the deployment: the whole article as Markdown, its
    title the first heading, and the line the contents show of it."""

    slug: str
    title: str
    summary: str
    text: str


class HelpOut(BaseModel):
    """The help pages and the help menu (app/api/help.py): the articles in the contents' order,
    the address members write to for help, which is none only in development, and the status
    page, where the deployment has one."""

    articles: list[HelpArticleOut]
    support_email: str | None
    # The public status page (STATUS_PAGE_URL), outside the deployment.
    status_page: str | None


class ChangesDayOut(BaseModel):
    """A day of what's new, or days told as one, and what changed for members then, the newest
    first, each entry a line of Markdown (app/changelog.py)."""

    first: dt.date
    last: dt.date
    entries: list[str]


class ChangelogOut(BaseModel):
    """A page of what's new, the newest days first, and the day to ask before for the next page,
    None once the oldest is among them."""

    days: list[ChangesDayOut]
    next_before: dt.date | None


# What a member writes to support about (`SUPPORT_SUBJECTS` in app/mail.py).
FeedbackKind = Literal["problem", "question", "idea"]
# The longest message to support: a page of a letter or two, whatever they pasted into it.
MAX_FEEDBACK_CHARS = 10_000


class FailedRequestIn(BaseModel):
    """The last request of the page's that the API refused or failed, by the id its answer
    carried (X-Request-ID, app/logs.py), under which the log keeps it, and its status."""

    reference: str = Field(max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    status: int = Field(ge=400, le=599)


class FeedbackIn(BaseModel):
    """A member's message to support from the help menu: what about, their words, and what the
    page attaches of where they were. The page is its path with the ids its query names (an
    open chat's, a folder's), never the query's words (`feedbackPage` in
    frontend/src/help/feedback.ts)."""

    kind: FeedbackKind
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_FEEDBACK_CHARS)
    ]
    page: str = Field(
        max_length=400,
        pattern=r"^/[A-Za-z0-9/_.~*-]*(?:\?[a-z]+=[0-9a-z]*(?:&[a-z]+=[0-9a-z]*)*)?$",
    )
    failed: FailedRequestIn | None = None


class FeedbackOut(BaseModel):
    # The id the message was sent under, which its subject names, for the member to quote.
    reference: str
    # Where it went, and so where an answer comes from.
    to: str


# The account export (app/export.py): what the app keeps about a person, as the file the
# account page downloads. The app itself never reads it back.


class ExportedIdentity(BaseModel):
    provider: str
    email: str
    linked_at: dt.datetime


class ExportedAccount(BaseModel):
    email: str
    name: str
    time_zone: str
    created_at: dt.datetime
    email_verified_at: dt.datetime | None
    two_factor_enabled_at: dt.datetime | None
    terms_version: str | None
    terms_accepted_at: dt.datetime | None
    deletion_due_at: dt.datetime | None
    sign_in_providers: list[ExportedIdentity]
    # How each kind of notice is told (app/notices.py).
    notices: list[NoticeChoiceOut]
    # When the list of first steps was hidden, the tour seen and the suggestion to invite the
    # team hidden (app/onboarding.py).
    checklist_hidden_at: dt.datetime | None
    tour_seen_at: dt.datetime | None
    invite_hidden_at: dt.datetime | None
    # How far what's new was read: its newest day then, and how many entries it held
    # (app/changelog.py).
    changes_seen_on: dt.date | None
    changes_seen_entries: int | None
    # When the mail provider said not to write to the address, and why (app/bounces.py).
    mail_spared: "ExportedSpared | None"


class ExportedSpared(BaseModel):
    reason: str
    recorded_at: dt.datetime


class ExportedMembership(BaseModel):
    organization: str
    slug: str
    role: str
    joined_at: dt.datetime
    # The organisation's private projects the user was chosen to see, by name.
    private_projects: list[str]
    # Until when the user lets Emudoc's support see their account there, if they do.
    support_until: dt.datetime | None


class ExportedSession(BaseModel):
    user_agent: str
    ip: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None


class ExportedBrowser(BaseModel):
    """A browser the account signed in with, told apart by its cookie (app/auth.py)."""

    first_signed_in_at: dt.datetime
    last_signed_in_at: dt.datetime


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
    # When the chat, or its project, went into the trash (app/trash.py), where it is still
    # kept; null for one out of it.
    deleted_at: dt.datetime | None = None
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


class ExportedNotification(BaseModel):
    """A notice in the user's notification centre (app/notices.py)."""

    organization: str
    kind: str
    title: str
    body: str
    link: str
    created_at: dt.datetime
    read_at: dt.datetime | None


class AccountExport(BaseModel):
    exported_at: dt.datetime
    # What the file holds and what it leaves out, in words, for whoever reads it.
    about: str
    account: ExportedAccount
    organizations: list[ExportedMembership]
    sessions: list[ExportedSession]
    browsers: list[ExportedBrowser]
    chats: list[ExportedChat]
    notifications: list[ExportedNotification]
    usage: list[ExportedUsage]
    activity: list[ExportedActivity]


# A project's export (app/export.py): the export as the projects page lists it, and the JSON its
# ZIP file holds, `project.json` and one `documents/<id>.json` a document.

# queued (being made), ready (to download until `expires_at`), failed (`error` says why).
ExportStatus = Literal["queued", "ready", "failed"]


class ProjectExportOut(_FromORM):
    id: int
    status: ExportStatus
    # How many originals and bytes the file holds; 0 until it is ready.
    documents: int
    size_bytes: int
    # Why it could not be made, in the member's words.
    error: str | None
    created_at: dt.datetime
    # Until when it can be downloaded; None until it is ready.
    expires_at: dt.datetime | None


class ExportedProject(BaseModel):
    name: str
    description: str
    visibility: str
    created_at: dt.datetime


class ExportedBy(BaseModel):
    name: str
    email: str


class ExportedDocument(BaseModel):
    """A document as `project.json` lists it, with where the archive holds its original and
    what was read from it."""

    id: int
    filename: str
    # The folder's path from the project's root, "" for the root.
    folder: str
    # The original's path in the archive; None when the malware scan has not passed it.
    original: str | None
    data: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: str
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    summary: str | None
    # The kind of the extraction template its facts were read for, if any.
    template: str | None
    page_count: int | None
    uploaded_at: dt.datetime


class ExportedSection(BaseModel):
    """A node of a document's outline; `parent_id` names another of its sections."""

    id: int
    parent_id: int | None
    level: int
    title: str
    path: str
    summary: str | None
    page_start: int | None
    page_end: int | None


class ExportedPassage(BaseModel):
    """A passage, which answers cite as [P<id>], in the section `section_id` names."""

    id: int
    section_id: int
    text: str
    context: str
    page_start: int | None
    page_end: int | None


class ExportedFact(BaseModel):
    name: str
    value_type: str
    value: str
    number: float | None
    date: dt.date | None
    currency: str | None
    quote: str | None
    page: int | None
    passage_id: int | None
    # Whether the quote was found in the document as the model gave it.
    verified: bool
    # A member's check of it, with their value in `value` when they corrected it.
    check: FactCheckOut | None


class ExportedDocumentData(BaseModel):
    """What was read from one document: `documents/<id>.json` in the archive."""

    id: int
    filename: str
    title: str | None
    sections: list[ExportedSection]
    passages: list[ExportedPassage]
    facts: list[ExportedFact]


class ExportedProjectChat(BaseModel):
    title: str
    visibility: str
    # The name of the member who started it, as it is now.
    started_by: str
    created_at: dt.datetime
    last_question_at: dt.datetime
    messages: list[ExportedMessage]


class ExportedTemplate(BaseModel):
    """An extraction template of the project's own, as its members wrote it."""

    doc_type: str
    title: str
    description: str
    fields: list[TemplateFieldOut]


class ProjectExportFile(BaseModel):
    """`project.json` in the archive."""

    exported_at: dt.datetime
    # What the archive holds and what it leaves out, in words, for whoever reads it.
    about: str
    organization: str
    project: ExportedProject
    exported_by: ExportedBy
    # Every folder's path from the project's root, an empty one too.
    folders: list[str]
    documents: list[ExportedDocument]
    chats: list[ExportedProjectChat]
    # The project's own extraction templates; the built-in ones are Emudoc's, and not in it.
    templates: list[ExportedTemplate]


# The operator's area (app/operators.py, app/api/operator.py): what the maintenance commands
# print, as the page shows it. Amounts are millionths of PRICE_CURRENCY, as the usage rows hold
# them; a figure of a plan is worded as the `plan` command prints it, which is how `--set` takes it.


class OperatorViewOut(BaseModel):
    """The member's account the operator's session views as support (app/support.py)."""

    org_id: int
    organization: str
    user_id: int
    name: str
    email: str
    ends_at: dt.datetime


class OperatorOut(BaseModel):
    """Who opened the area, the deployment it is about, and the member's account their session
    views as support, if one."""

    user_id: int
    name: str
    email: str
    environment: str
    version: str
    currency: str
    viewing: OperatorViewOut | None


class OperatorOrganizationRow(BaseModel):
    id: int
    name: str
    slug: str
    plan: str
    seats: int | None
    members: int
    created_at: dt.datetime
    subscription_status: str | None
    deletion_due_at: dt.datetime | None
    # Its known model cost over the list's `cost_days`, None while none is known.
    cost_micros: int | None


class OperatorOrganizationsOut(BaseModel):
    organizations: list[OperatorOrganizationRow]
    # The id to ask `before` for the next page, None on the last.
    next_before: int | None
    cost_days: int


class EntitlementOut(BaseModel):
    """One figure the organisation is held to, its plan's own beside it, and whether it was
    agreed with the organisation in place of the plan's (or its seats bought, for members)."""

    name: str
    value: str
    plan_value: str
    agreed: bool


class OperatorMemberOut(BaseModel):
    user_id: int
    name: str
    email: str
    role: str
    mfa_enabled: bool
    joined_at: dt.datetime
    # Until when the member lets support see their account here, None while they do not.
    support_until: dt.datetime | None


class CostOut(BaseModel):
    """What one organisation's work, or every organisation's, cost over a span of days, against
    the daily limit it is held to (None for none): its costliest day, and the days that reached
    the limit."""

    cost_micros: int | None
    calls: int
    input_tokens: int
    output_tokens: int
    answers: int
    pages: int
    top_day_micros: int | None
    limit_micros: int | None
    days_at_limit: int | None


class DayCostOut(BaseModel):
    day: dt.date
    cost_micros: int


class OperatorPlanOut(BaseModel):
    """A plan the organisation may be put on, with its own figures as `entitlements` words them,
    for the form that changes the organisation's."""

    name: str
    # Whether it is sold by the seat, whose members are its seats rather than a figure.
    by_the_seat: bool
    figures: dict[str, str]


class OperatorFlagOut(BaseModel):
    """A feature flag as it stands for one organisation (app/flags.py): what it turns on, the
    percentage of organisations it is rolled out to, what an operator chose for this one (None
    for the rollout's), and whether it is on for it now."""

    name: str
    title: str
    share: int
    chosen: bool | None
    on: bool


class OperatorFlagIn(BaseModel):
    """A feature flag turned on or off for an organisation, or None back to its rollout."""

    on: bool | None


class OperatorFlagChoiceOut(BaseModel):
    """An organisation an operator turned a flag on or off for."""

    org_id: int
    slug: str
    name: str
    on: bool


class OperatorFlagSummaryOut(BaseModel):
    """A feature flag across the deployment: its rollout, how many organisations it is on for
    of all of them, and those an operator chose for, by name."""

    name: str
    title: str
    share: int
    on: int
    organizations: int
    chosen: list[OperatorFlagChoiceOut]


class OperatorOrganizationOut(BaseModel):
    organization: OrganizationOut
    usage: UsagePageOut
    entitlements: list[EntitlementOut]
    # The figures agreed with it in place of its plan's, worded as `entitlements` words them,
    # which is how a change takes them back; its seats are `organization.seats`.
    overrides: dict[str, str]
    plans: list[OperatorPlanOut]
    members: list[OperatorMemberOut]
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    since: dt.date
    until: dt.date
    cost: CostOut
    # The known cost of each day of the span that has one, the oldest first.
    cost_days: list[DayCostOut]
    # Every feature flag, as it stands for the organisation, in the order of FLAGS.
    flags: list[OperatorFlagOut]


class OperatorPlanIn(BaseModel):
    """What the organisation is to be held to, as a whole: the plan, the seats bought on a plan
    sold by the seat (None for the plan's own members) and every figure agreed with it, each in
    the words the area shows (`figure_of` reads them), any left out being the plan's own."""

    plan: str = Field(max_length=40)
    seats: int | None = Field(None, le=1_000_000)
    agreed: dict[str, Annotated[str, StringConstraints(max_length=100)]] = Field(
        default_factory=dict, max_length=40
    )


class OperatorUserMembershipOut(BaseModel):
    org_id: int
    slug: str
    name: str
    role: str


class OperatorUserOut(BaseModel):
    id: int
    name: str
    email: str
    created_at: dt.datetime
    email_verified: bool
    mfa_enabled: bool
    operator: bool
    deletion_due_at: dt.datetime | None
    # When one of its sessions was last seen, to LAST_SEEN_RESOLUTION; None without one.
    last_seen_at: dt.datetime | None
    memberships: list[OperatorUserMembershipOut]


class OperatorUsersOut(BaseModel):
    users: list[OperatorUserOut]
    # Whether more users matched than are shown.
    more: bool


class OrganizationCostOut(CostOut):
    org_id: int
    slug: str
    name: str
    plan: str


class ModelCostOut(BaseModel):
    kind: str
    model: str
    cost_micros: int | None
    calls: int
    input_tokens: int
    output_tokens: int
    # The tokens recorded without a price, which no cost counts.
    unpriced_tokens: int


class OperatorCostsOut(BaseModel):
    since: dt.date
    until: dt.date
    currency: str
    # Every organisation that used anything, the costliest first.
    organizations: list[OrganizationCostOut]
    # Theirs together, against DAILY_SPEND_CAP.
    platform: CostOut
    # The organisations that used nothing.
    idle: int
    models: list[ModelCostOut]


class QueueOut(BaseModel):
    """One kind of job by state (JOB_STATES in app/jobs.py), and how long the oldest due one
    has waited for a worker."""

    kind: str
    due: int
    waiting: int
    running: int
    failed: int
    oldest_due_seconds: float | None


class OperatorJobOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    max_attempts: int
    run_at: dt.datetime
    lease_until: dt.datetime | None
    finished_at: dt.datetime | None
    worker_id: str | None
    # What it is for, as "acme / Contracts / invoice.pdf" (`NamedJob.about`).
    about: str
    # Why it failed, for the operator; None for one that did not.
    last_error: str | None
    # Whether the alerter mailed its failure to ALERT_EMAIL.
    reported: bool


class OperatorWorkerOut(BaseModel):
    """A worker seen within WORKERS_KEPT (app/jobs.py): running, stopped by itself, or neither,
    which is a process that died or lost the database."""

    id: str
    version: str
    started_at: dt.datetime
    last_seen_at: dt.datetime
    stopped_at: dt.datetime | None
    running: bool


class OperatorJobsOut(BaseModel):
    taken_at: dt.datetime
    queue: list[QueueOut]
    running: list[OperatorJobOut]
    # The dead letters of the last `history_days`, the newest first, `failed_shown` at most.
    failed: list[OperatorJobOut]
    failed_total: int
    history_days: int
    # The workers seen lately, the latest first; none running leaves the queue's work waiting.
    workers: list[OperatorWorkerOut]


class RequeueIn(BaseModel):
    """The failed jobs to run again, by id; None for every one the queue keeps, as the
    `failed-jobs --requeue` command runs them."""

    job_ids: list[int] | None = Field(None, max_length=1000)


class RequeueOut(BaseModel):
    # How many documents were queued again, and what was done with each job, one line each.
    queued: int
    lines: list[str]


class OperatorModelOut(BaseModel):
    variable: str
    name: str
    role: str
    fallback: bool
    # Whether its provider is switched off, by DISABLED_PROVIDERS or from the area.
    disabled: bool


class OperatorProviderOut(BaseModel):
    """A model provider the settings name a model of, or that the area holds switched off,
    and whether it is: by DISABLED_PROVIDERS, which the area cannot switch back on, or from the
    area, by whom and since when."""

    name: str
    # The settings that name a model of it (AGENT_MODEL, …), none for one no longer named.
    variables: list[str]
    off: bool
    by_setting: bool
    switched_at: dt.datetime | None
    # The operator who switched it off, by name or address; None for none, or one since gone.
    switched_by: str | None


class ProviderSwitchIn(BaseModel):
    off: bool


class ConcernOut(BaseModel):
    """What the worker's watch sees wrong now (app/watch.py), with since when it was raised and
    mailed, None for one the watch has not looked at yet."""

    key: str
    title: str
    message: str
    raised_at: dt.datetime | None


class DatabaseOut(BaseModel):
    version: str
    role: str
    vector: str | None
    iterative_scans: bool
    # The role's statement_timeout and idle_in_transaction_session_timeout, "0" for none.
    timeouts: dict[str, str]


class OperatorServicesOut(BaseModel):
    models: list[OperatorModelOut]
    providers: list[OperatorProviderOut]
    # What stops the models, in the operator's words, None while they can be used.
    model_problem: str | None
    embedding_model: str
    embedding_address: str
    scanner: bool
    # Where the files are kept: the bucket's origin, or None for a directory.
    storage_origin: str | None
    concerns: list[ConcernOut]
    # The checks that could not look, whose concerns are unknown (the watch's key prefixes).
    unchecked: list[str]
    database: DatabaseOut
