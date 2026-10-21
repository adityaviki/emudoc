import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    # The documents whose enrich stage was skipped for want of a model: what the library
    # offers to run the model on once there is one.
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
NoticeKind = Literal["upload_indexed", "quota"]


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
