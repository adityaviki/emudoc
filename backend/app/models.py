import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REGCONFIG, TSVECTOR
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.config import REGIONS, get_settings
from app.db import Base
from app.storage import files_prefix

_settings = get_settings()

# The pipeline's stages in order (app/indexing/pipeline.py): the malware scan comes first,
# since nothing may open the file before it has passed (app/scanning.py).
STAGES = ("scan", "parse", "structure", "enrich", "embed")
# What a fact's value is (`Fact.value_type`), and so what a field of an extraction template may
# be (app/extraction.py).
FACT_TYPES = ("text", "number", "date", "money")
# How a cross-check (app/cross_checks.py) pairs a document with those it is compared with, and how
# it compares their values.
CROSS_CHECK_PAIRINGS = ("same", "key", "every")
CROSS_CHECK_COMPARISONS = ("equal", "at_most", "at_least", "same_words", "same_numbers")
# What a cross-check found for one value (app/cross_checks.py, OUTCOME_OF): the outcome a
# member filters by, and the reason that gives it, as a run keeps them (`WorkflowFinding`).
FINDING_OUTCOMES = ("differs", "missing", "holds")
FINDING_REASONS = (
    "holds",
    "differs",
    "currency",
    "no_value",
    "no_key",
    "no_partner",
    "no_partner_value",
    "no_item",
    "untyped",
)
# How far a run of a project's cross-checks is (app/check_runs.py): waiting for the worker,
# being worked out, kept with what it found, or failed; and the discrepancy report the agent
# writes from it when a member asks for one: waiting, being written, written, or failed.
RUN_STATUSES = ("queued", "running", "done", "failed")
REPORT_STATUSES = ("queued", "writing", "done", "failed")
# Whom an automation (app/automations.py) tells what a run it made found: the member who made it,
# everyone who sees its project, each as they chose for the kind of notice, or nobody, the run
# being listed with the project's either way.
AUTOMATION_AUDIENCES = ("maker", "project", "nobody")
# Where a document stands in its review (app/reviews.py), the accounts payable's way with an
# invoice: put in review for a member to look at, then approved, or disputed with whoever sent
# it. A document in no review has none.
REVIEW_STATES = ("to_review", "approved", "disputed")
# Where a document's view stands, the PDF of its file the document pane shows a Word, PowerPoint
# or Excel file by (app/conversion.py): being made, made, or not made for the reason
# `view_error` gives; null for none (a type that is not converted, or one not asked for yet).
VIEW_STATES = ("queued", "ready", "failed")
# What a file of a member's My files is (app/my_files.py): a Word document or an Excel workbook,
# by the extension its versions carry.
MY_FILE_KINDS = ("docx", "xlsx")
# Where a version of a file in My files stands with the malware scan: waiting for it, passed,
# skipped (no scanner, or the agent wrote it), or flagged, which leaves it unserved for good.
MY_FILE_SCANS = ("pending", "passed", "skipped", "flagged")
# A document's details that a member may set in place of what its reading found (app/details.py):
# its title, its kind, its date and the language its words are searched in, each kept through
# every later reading until the member takes the reading's again.
DOCUMENT_DETAILS = ("title", "doc_type", "doc_date", "language")
# What an automation's run puts in review (app/reviews.py), of the documents that arrived for it
# and are in none: those its cross-checks found a difference about, or every one it watches.
AUTOMATION_REVIEWS = ("differs", "arrived")
# When a project's question set (app/question_sets.py) is asked of its documents on a schedule:
# every day, or one day a week, at an hour of its maker's zone. What made a report of its answers:
# the schedule, documents that arrived, or a member who ran it there and then. How far a report
# and each of its answers is: waiting for the worker, being asked (an answer: its question counted
# and the agent at work), kept, or failed.
QUESTION_SET_SCHEDULES = ("daily", "weekly")
REPORT_TRIGGERS = ("schedule", "arrivals", "member")
QUESTION_REPORT_STATUSES = ("queued", "running", "done", "failed")
# What a draft is (app/drafts.py), each kind with its own instructions to the agent: a reply to
# an email, a letter disputing a document, a memo summing documents up, a table setting them side
# by side, and a report.
DRAFT_KINDS = ("reply_email", "dispute_letter", "summary_memo", "comparison_table", "report")
# How far the agent's writing of a draft is: waiting for the worker, being written, kept as the
# draft's newest version, or failed, the versions before it staying as they were.
DRAFT_STATUSES = ("queued", "writing", "done", "failed")
# From most to least permitted: each role may do everything the roles after it may.
ROLES = ("owner", "admin", "member", "viewer")
# A chat is private to the member who started it until they share it with the project.
CHAT_VISIBILITIES = ("private", "project")
# What a chat is about (app/scopes.py): the project's documents, a folder of it with its
# subfolders, or documents chosen from it.
CHAT_SCOPES = ("project", "folder", "documents")
# A project is seen by everyone in its organisation, or, when private, by the members chosen
# for it and the organisation's owners and admins (app/access.py).
PROJECT_VISIBILITIES = ("organization", "private")
# What an API key may do (app/apikeys.py): read, by the methods that change nothing, or write,
# which reads too; either within the role of the member who made it.
API_KEY_SCOPES = ("read", "write")
# What an organisation's webhooks are told of (app/webhooks.py): a document indexed, one that
# could not be, a run of cross-checks worked out, and a report of a question set's answers kept;
# and how a delivery to one stands: being tried, answered with a 2xx, or given up.
WEBHOOK_EVENTS = ("document.ready", "document.failed", "check_run.completed", "report.completed")
WEBHOOK_DELIVERY_STATUSES = ("pending", "delivered", "failed")
# What a background job can be (app/jobs.py). A worker only claims the kinds it has a handler
# for, so a kind can be queued by a newer API before an older worker knows it.
JOB_KINDS = (
    "index_document",
    "answer_question",
    "render_pages",
    "send_email",
    "apply_language",
    "export_project",
    "sync_connector",
    "run_workflow",
    "run_automation",
    "write_draft",
    "receive_mail",
    "deliver_webhook",
    "run_question_set",
    "write_question_report",
    "send_digests",
    "convert_document",
    "view_my_file",
)
JOB_STATUSES = ("queued", "running", "done", "failed")
# Why an address is sent no notices (app/bounces.py): mail to it bounced for good, or its reader
# marked a message as spam.
MAIL_SUPPRESSIONS = ("bounce", "complaint")
# How far a project's export is (app/export.py): being made by the worker, ready to download
# until it expires, or failed.
EXPORT_STATUSES = ("queued", "ready", "failed")
# How far an answer is: being written by the worker (app/answers.py), finished, stopped by the
# user, or failed. A question is always `done`.
MESSAGE_STATUSES = ("streaming", "done", "stopped", "failed")
# What an organisation is metered for (app/usage.py): the activity a row counts, and what the
# count is in. The stored `usage` of a document, a message, a run of cross-checks or a draft
# (`Document.usage`, `ChatMessage.usage`, `WorkflowRun.usage`, `Draft.usage`) is keyed by the same
# kinds; a report of a question set's answers (`QuestionReport.usage`) is kept under `answer`,
# since each of its answers is the agent's as a chat's is. Nothing records `suggestions` since
# 2026-11-11 (the model wrote a project's suggested questions), whose rows stay.
USAGE_KINDS = (
    "answer",
    "upload",
    "ocr",
    "infer_sections",
    "enrich",
    "embed",
    "render",
    "report",
    "draft",
    "suggestions",
)
USAGE_UNITS = ("calls", "input_tokens", "output_tokens", "pages", "bytes", "inputs", "questions")
# How many days a workspace keeps a row of its audit log until its owners and admins choose
# otherwise (`organizations.audit_days`, app/audit.py): a year, long enough to answer what
# happened last spring.
AUDIT_DAYS = 365
# The kinds of notice the app tells members about their work (app/notices.py), and the ways each
# reaches them: the notification centre in the app, and email. A member chooses each way of
# each kind (`User.notices`). Every kind is told until it is turned off, but those a member turns
# on: the weekly digest of what arrived (app/digests.py), news nobody asked for by their work.
NOTICE_KINDS = (
    "upload_indexed",
    "project_export",
    "checks_run",
    "review_assigned",
    "report_ready",
    "weekly_digest",
    "mentioned",
    "thread_replied",
)
NOTICE_CHANNELS = ("app", "email")
CHOSEN_NOTICES = ("weekly_digest",)
# What became of a message sent to a project's address (app/inbound.py): documents were kept
# from it, it was turned away whole (its sender, the project), or nothing in it could
# be kept.
INBOUND_OUTCOMES = ("kept", "refused", "empty")
# The services a project's folder can be connected from (app/connectors/), and how a connector
# stands: connected, its folder not chosen yet; kept in step with its folder; or paused until its
# member connects it again, the service having stopped its access or the member no longer being
# one who may change the project.
CONNECTOR_PROVIDERS = ("google", "dropbox", "onedrive")
CONNECTOR_STATUSES = ("choosing", "active", "paused")
# The order the library lists a folder's files in, which the database keeps an index in: by
# name, with the numbers in a name compared as numbers ("invoice 2" before "invoice 10") and
# case and accents set aside, as the page sorts the folders. An ICU collation, made by the
# migration that put it on `documents.filename` (deterministic: names equal but for case
# still have an order, so a page boundary between them is exact).
NATURAL_ORDER = "natural_order"
# What the audit log records (app/audit.py): its members' sign-ins and what they did to their
# accounts, what was done to the organisation itself (its name and settings, its owner, its API
# keys, who was invited, joined, left or was removed, the members' roles, and a request to
# delete it, which its owners' mail outlives, since the log goes with it), to its projects,
# its documents and the sharing of its chats, and what the operator and the app did to it on
# their own (a quarantine, the sample project a new account's organisation starts with), and a
# member's leave for Emudoc's support to see their account and support's views of it. A
# project, folder, document or chat `_deleted` went into the trash (app/trash.py), `_restored`
# came out of it, and `_purged` was deleted for good from it by a member. A project's extraction
# templates (app/extraction.py) and cross-checks (app/cross_checks.py) are made, changed and
# deleted for good, with no trash, and its facts tables downloaded (app/fact_export.py), which is
# its documents' content leaving the app; a run of its cross-checks is made, by a member or by an
# automation, a discrepancy report asked of the agent and the run deleted for good
# (app/check_runs.py), and its automations made, changed and deleted (app/automations.py), as
# are its question sets, each run by a member or by the app, and their reports deleted for good
# (app/question_sets.py); its filing rules were made, changed and deleted until they were removed
# on 2026-11-05, and their actions stay so that those entries still read. A document's review
# moves from state to state and is assigned to a member (app/reviews.py). A draft is asked of the
# agent, changed by a member's version or the agent's writing it again, and deleted for good
# (app/drafts.py), and a version of it downloaded as a file (app/draft_export.py),
# which is what documents say leaving the app; a project's own template of a kind of draft is
# made, changed and deleted for good (app/draft_templates.py). A project's address is made, its
# senders, folder or address changed, and taken away (app/inbound.py); a document that arrived
# by it is uploaded by its member, or by nobody for a sender outside the organisation. A webhook
# the organisation's programs hear of its work by is added, changed, given a new secret and
# removed (app/webhooks.py), which sends what documents say to an address of its choosing. A
# link that shows a document to whoever holds it is made and revoked by a member, and opened, and
# its original downloaded, by nobody the app knows (app/share_links.py). An answer, or a whole
# chat, is downloaded as a file (app/chat_export.py), which is what documents say leaving the app.
# Until an install held one workspace (2026-11-12), a member could also leave an organisation, join
# one by their address's domain, change who may, and ask for one's deletion or keep it; and until
# nothing was sold (S.2, 2026-11-13), an organisation's plan, seats and agreed figures changed, it
# bought a plan or a pack and its subscription changed. Those actions stay so that their entries
# still read.
AUDIT_ACTIONS = (
    "sign_up",
    "sign_in",
    "sign_in_failed",
    "sign_in_paused",
    "sign_out",
    "session_revoked",
    "other_sessions_revoked",
    "password_changed",
    "password_reset_requested",
    "password_reset",
    "profile_changed",
    "email_change_requested",
    "email_changed",
    "mfa_enabled",
    "mfa_disabled",
    "account_exported",
    "terms_accepted",
    "account_deletion_requested",
    "account_deletion_cancelled",
    "account_purged",
    "support_access_granted",
    "support_access_revoked",
    "support_session_started",
    "support_session_ended",
    "role_changed",
    "plan_changed",
    "seats_changed",
    "entitlements_changed",
    "checkout_started",
    "subscription_changed",
    "subscription_cancelled",
    "cancellation_withdrawn",
    "payment_failed",
    "pack_bought",
    "organization_created",
    "organization_renamed",
    "organization_deletion_requested",
    "organization_deletion_cancelled",
    "settings_changed",
    "flag_changed",
    "audit_exported",
    "api_key_created",
    "api_key_rolled",
    "api_key_revoked",
    "webhook_created",
    "webhook_changed",
    "webhook_rolled",
    "webhook_deleted",
    "ownership_transferred",
    "invitation_sent",
    "invitation_revoked",
    "invitation_accepted",
    "joining_changed",
    "member_joined",
    "member_left",
    "member_removed",
    "project_access_changed",
    "project_exported",
    "template_created",
    "template_changed",
    "template_deleted",
    "fact_checked",
    "fact_corrected",
    "fact_check_removed",
    "facts_exported",
    "cross_check_created",
    "cross_check_changed",
    "cross_check_deleted",
    "cross_checks_run",
    "check_report_requested",
    "check_run_deleted",
    "automation_created",
    "automation_changed",
    "automation_deleted",
    "question_set_created",
    "question_set_changed",
    "question_set_deleted",
    "question_set_run",
    "question_report_deleted",
    "filing_rule_created",
    "filing_rule_changed",
    "filing_rule_deleted",
    "draft_created",
    "draft_changed",
    "draft_deleted",
    "draft_exported",
    "draft_template_created",
    "draft_template_changed",
    "draft_template_deleted",
    "mailbox_created",
    "mailbox_changed",
    "mailbox_deleted",
    "connector_created",
    "connector_changed",
    "connector_deleted",
    "project_deleted",
    "project_restored",
    "project_purged",
    "sample_added",
    "folder_deleted",
    "folder_restored",
    "folder_purged",
    "document_uploaded",
    "document_version_uploaded",
    "document_downloaded",
    "document_deleted",
    "document_restored",
    "document_purged",
    "document_quarantined",
    "document_requeued",
    "document_review_changed",
    "document_assigned",
    "share_link_created",
    "share_link_revoked",
    "share_link_viewed",
    "share_link_downloaded",
    "chat_shared",
    "chat_unshared",
    "chat_exported",
    "answer_exported",
    "chat_deleted",
    "chat_restored",
    "chat_purged",
    "comment_added",
    "comment_deleted",
    "thread_resolved",
    "thread_reopened",
    "database_restored",
    "my_file_added",
    "my_file_version_added",
    "my_file_renamed",
    "my_file_deleted",
    "my_file_downloaded",
    "my_file_copied",
    "reset_link_made",
)


def _one_of(column: str, values: tuple[str, ...], table: str) -> CheckConstraint:
    # A misspelt role or visibility would otherwise grant nothing silently, or show a chat to
    # nobody, so the database refuses it.
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{column}")


def _role_check(table: str) -> CheckConstraint:
    return _one_of("role", ROLES, table)


def _trash_indexes(table: str, scope: str, by: bool = True) -> tuple[Index, ...]:
    # The rows in the trash are few beside the live ones, so they get partial indexes of their
    # own: what one project's (or organisation's) trash holds, and the purge's look for what
    # has been there long enough (app/trash.py). `deleted_by` is indexed for the account purge,
    # whose foreign key would otherwise scan the table for the user's rows.
    trashed = text("deleted_at IS NOT NULL")
    indexes = [Index(f"ix_{table}_trashed", scope, "deleted_at", postgresql_where=trashed)]
    if by:
        indexes.append(
            Index(
                f"ix_{table}_deleted_by",
                "deleted_by",
                postgresql_where=text("deleted_by IS NOT NULL"),
            )
        )
    return tuple(indexes)


def _deleted_by() -> Mapped[int | None]:
    # Who put the row in the trash, which the trash names; null for a live row, and once the
    # member's account is gone.
    return mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class User(Base):
    """A person who signs in. What they may see follows from their organisation memberships."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Lower-cased before it is stored, so sign-in and invitations match however it was typed.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    # The IANA zone the dates in the user's mail are written in (`zone_of` in app/accounts.py),
    # taken from the browser at sign-up and chosen on the account page; empty is UTC. The pages
    # date everything in the browser's own zone.
    time_zone: Mapped[str] = mapped_column(String(64), default="")
    # Null for accounts that only sign in through an identity provider.
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Two-factor sign-in with an authenticator app. The secret is set when setup starts and
    # counts only once `totp_enabled_at` is set by a correct code; the step of the last accepted
    # code is kept so that a code cannot be used twice. The secret is encrypted for this row
    # with ENCRYPTION_KEYS (app/encrypted.py), so a copy of the database makes no codes.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_enabled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    # SHA-256 hashes of the one-time codes that replace the authenticator when it is lost.
    recovery_code_hashes: Mapped[list] = mapped_column(JSONB, default=list)
    # When the account is purged with everything it alone holds (app/retention.py): set when
    # its user asks for it to be deleted, cleared if they keep it before then. While it is
    # set, the account can do nothing but keep itself, export its data and sign out.
    deletion_due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The version of the terms of service and privacy policy the user accepted, and when
    # (app/legal/). Null until they accept; a version other than TERMS_VERSION, after the
    # terms changed, is asked again, and until then the account can do nothing but accept,
    # export its data, delete itself and sign out.
    terms_version: Mapped[str | None] = mapped_column(String(40))
    terms_accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # How the user is told each kind of notice, the news they may do without (app/notices.py):
    # NOTICE_KINDS by NOTICE_CHANNELS as they chose them, such as {"checks_run": {"email": false}}.
    # A kind or a way not named is on, so that a kind added later reaches everyone until they
    # choose. Chosen on the account page, and a kind's email turned off by its notices' own
    # unsubscribe link. The mail about the account, its organisations' invitations and billing
    # is sent whatever it says.
    notices: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # When the user hid the projects page's list of first steps, when they ended the tour of a
    # project's workspace, by finishing or skipping it, and when they hid the chat's suggestion
    # to invite their team (app/onboarding.py). Each is the user's for every organisation they
    # work in: whoever ended one knows their way about.
    checklist_hidden_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    tour_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    invite_hidden_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    identities: Mapped[list["Identity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def mfa_enabled(self) -> bool:
        return self.totp_enabled_at is not None

    def wants(self, kind: str, channel: str, defaults: dict | None = None) -> bool:
        """Whether the user is told notices of the kind that way (NOTICE_CHANNELS): as they
        chose, or where they did not, as `defaults` says, an organisation's `notices`."""
        chosen = (self.notices or {}).get(kind, {}).get(channel)
        if chosen is None:
            chosen = (defaults or {}).get(kind, {}).get(channel)
        return _told(kind, chosen)

    def choose(self, kind: str, channel: str, on: bool) -> None:
        """Tell the user notices of the kind that way from now on, or not."""
        self.notices = _choosing(self.notices, kind, channel, on)


def _told(kind: str, chosen: bool | None) -> bool:
    """Whether a kind of notice is told as chosen, None for not: on until turned off, but a kind
    of CHOSEN_NOTICES, off until turned on."""
    return chosen is True if kind in CHOSEN_NOTICES else chosen is not False


def _choosing(notices: dict | None, kind: str, channel: str, on: bool) -> dict:
    """The choices of `notices` with the kind told that way or not: a new dict, since the
    session saves a JSON column only when it is assigned."""
    chosen = {name: dict(ways) for name, ways in (notices or {}).items()}
    chosen.setdefault(kind, {})[channel] = on
    return chosen


class Identity(Base):
    """A sign-in through an identity provider, tied to the subject the provider issues.

    The subject, not the email, names the person: an email can change hands at the provider,
    a subject never does.
    """

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # google | microsoft
    provider: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(255))
    # The address the provider reported at the last sign-in, for the account's security view.
    email: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="identities")


# What a one-time link does when it is opened.
AUTH_TOKEN_PURPOSES = ("verify_email", "reset_password", "change_email", "sign_up")


class AuthToken(Base):
    """A one-time link sent by email: to confirm an address, to reset a password, to make a
    new address the account's, or to make an account for an address that has none.

    The link carries a random token; only its SHA-256 is stored, as for sessions. A sign-up's
    link belongs to no user, since the account is made only once the link is opened, and every
    other link belongs to one.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (
        _one_of("purpose", AUTH_TOKEN_PURPOSES, "auth_tokens"),
        CheckConstraint("(user_id IS NULL) = (purpose = 'sign_up')", name="ck_auth_tokens_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The address the link was sent to, which a verification confirms, a change of address
    # makes the account's and a sign-up makes an account with.
    email: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Organization(Base):
    """The tenant: an install's one workspace (S.1). Projects belong to it, and so do its members,
    its usage and its settings."""

    __tablename__ = "organizations"
    __table_args__ = (
        _one_of("region", tuple(REGIONS), "organizations"),
        CheckConstraint("chat_days > 0", name="ck_organizations_chat_days"),
        CheckConstraint("audit_days > 0", name="ck_organizations_audit_days"),
        CheckConstraint("jsonb_typeof(notices) = 'object'", name="ck_organizations_notices"),
        CheckConstraint("session_days > 0", name="ck_organizations_session_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Appears in URLs and API responses, so it is unique across all tenants.
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    # The region its data is kept in (DATA_REGION, app/residency.py), recorded as it is created
    # so that a deployment in another region refuses to serve it; null while the deployment
    # names none. One deployment is one region; several are 4.3's.
    region: Mapped[str | None] = mapped_column(String(20))
    # Its settings, chosen by its owners and admins (app/api/organizations.py). The language its
    # documents are searched in, a text search configuration of the database, which its new
    # passages are indexed with and its old ones are moved to (app/languages.py), and which
    # answers fall back to; null for the deployment's FTS_LANGUAGE.
    language: Mapped[str | None] = mapped_column(String(63))
    # How many days it keeps a chat after its last question (app/retention.py), and a row of
    # its audit log (app/audit.py); a null keeps it until it is deleted. Chats are kept until
    # deleted unless its owners and admins choose fewer days, and the audit log a year.
    chat_days: Mapped[int | None] = mapped_column(Integer)
    audit_days: Mapped[int | None] = mapped_column(
        Integer, default=AUDIT_DAYS, server_default=str(AUDIT_DAYS)
    )
    # How its members are told each kind of notice until they choose for themselves, as
    # `User.notices` holds a member's choices: a kind or a way not named is on.
    notices: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Its security settings (app/security.py): whether everyone who works in it signs in with
    # two-factor, and how many days after signing in a session may work in it, when that is less
    # than SESSION_DAYS; null for as long as the deployment keeps one.
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    session_days: Mapped[int | None] = mapped_column(Integer)
    # Whether its members may share a document by a link that needs no account
    # (app/share_links.py); turned off, every link it has stops.
    share_links: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )

    def tells(self, kind: str, channel: str) -> bool:
        """Whether its members are told notices of the kind that way until they choose."""
        return _told(kind, (self.notices or {}).get(kind, {}).get(channel))

    def choose(self, kind: str, channel: str, on: bool) -> None:
        """Tell its members notices of the kind that way until they choose, or not."""
        self.notices = _choosing(self.notices, kind, channel, on)


class Membership(Base):
    """A user's role in an organisation; one row per user and organisation."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"), _role_check("memberships"))

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The Monday, in the member's zone, whose weekly digest of what arrived in the organisation's
    # projects they were last told (app/digests.py), so that a week is told once; null before
    # the first.
    digested_on: Mapped[dt.date | None] = mapped_column(Date)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class Operator(Base):
    """A user who may open the operator's area (app/operators.py): someone who runs the
    deployment, whatever organisations they are a member of. Granted and taken back only by the
    `operators` maintenance command, from a shell on the deployment, and by the user's id, so
    an address that changes hands grants nothing; the row goes with the account."""

    __tablename__ = "operators"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProviderSwitch(Base):
    """A model provider an operator switched off from the operator's area (app/switches.py),
    named as DISABLED_PROVIDERS names one, by the prefix of a `provider:model` name: while its
    row is there no process asks a model of it. Switching it on again deletes the row, and a
    provider DISABLED_PROVIDERS names stays off whatever the table says."""

    __tablename__ = "provider_switches"
    __table_args__ = (
        CheckConstraint("provider ~ '^[a-z0-9_-]+$'", name="ck_provider_switches_provider"),
    )

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    # The operator who switched it off; null once their account is gone.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    switched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Invitation(Base):
    """An invitation to join an organisation, open until it is accepted, revoked or expires
    (app/invitations.py). Accepting or revoking it deletes it, so a row is an invitation still
    open or one that expired, which its inviter may send again; one an address at most per
    organisation."""

    __tablename__ = "invitations"
    __table_args__ = (UniqueConstraint("org_id", "email"), _role_check("invitations"))

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    # The link carries a random token; only its SHA-256 is stored, as for sessions and API keys,
    # so a copy of the database cannot be used to accept, sign in or call the API. Sending it
    # again replaces it, so the link mailed before stops working.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApiKey(Base):
    """A key a program calls the API with in place of a member's session (app/apikeys.py). It
    acts as the member who made it, in the organisation it was made in, within their role there
    and its scope; so it goes with their membership, by the database's cascade, when they leave
    or are removed or the organisation or the account goes."""

    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
        _one_of("scope", API_KEY_SCOPES, "api_keys"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(10))
    # Only the key's SHA-256 is stored, as for sessions, so a copy of the database calls nothing.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The key's first characters, which the page shows to tell keys apart.
    hint: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When it was last given a new secret (`roll`), the one before ending then.
    rolled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Kept to LAST_SEEN_RESOLUTION, as a session's last sight is (app/auth.py).
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # None for a key that runs until it is revoked.
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    """An `Idempotency-Key` a program sent with a request that makes something (app/idempotency.py),
    claimed for the API key that sent it with a fingerprint of the request, and once the request
    is carried out the answer it was given, which a request sent again with the key is given in
    its place. It goes with its API key, by the database's cascade, and the housekeeper removes
    it KEPT after it was claimed."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("api_key_id", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(255))
    # The SHA-256 of what the request asked (`fingerprint`), which a request sent again must match.
    fingerprint: Mapped[str] = mapped_column(String(64))
    # The answer, None while the request that claimed the key is being carried out.
    status_code: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Webhook(Base):
    """An address an organisation's programs hear of its work at (app/webhooks.py): each event
    of the kinds it names is posted there, signed with its secret. It is the organisation's,
    made and managed by its owners and admins, and goes with it."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    url: Mapped[str] = mapped_column(String(2000))
    # Of WEBHOOK_EVENTS, in their order.
    events: Mapped[list[str]] = mapped_column(JSONB)
    # Encrypted for its row (WEBHOOK_SECRETS in app/encrypted.py): it must be read to sign with,
    # and a copy of the database that held it could sign as Emudoc.
    secret: Mapped[str] = mapped_column(Text)
    # Paused by an owner or admin: its events are not posted until it is on again.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    rolled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(Base):
    """One event posted to one webhook, as the delivery log shows it: what it was and how the
    address answered its last try, never what it said. The event's id is the same through every
    try, and to every hook it went to; `(webhook_id, job_id)` is unique, so the end of a job
    looked at twice announces its event once. The housekeeper removes it DELIVERIES_KEPT after
    it was made."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("webhook_id", "job_id"),
        # The log a hook at a time, the newest first.
        Index("ix_webhook_deliveries_hook", "webhook_id", "id"),
        _one_of("event_type", WEBHOOK_EVENTS, "webhook_deliveries"),
        _one_of("status", WEBHOOK_DELIVERY_STATUSES, "webhook_deliveries"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(40))
    # The document's or the run's id, which the event's `data` is, and the job whose end
    # announced it, no foreign key, since the jobs are pruned first.
    subject_id: Mapped[int] = mapped_column(BigInteger)
    job_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # How the last try ended: the address's HTTP status, if it answered, and else, or for a status
    # that is no delivery, the app's sentence why.
    response_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    attempted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # When the next try is due, while one waits.
    retry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SupportGrant(Base):
    """A member's leave for Emudoc's support to see their account in one of their organisations
    (app/support.py), until `expires_at`. Granting it again moves the time; withdrawing it
    deletes it, which ends a view resting on it; it goes with the membership, by the database's
    cascade, when they leave or are removed or the organisation or the account goes. One a
    member at most per organisation."""

    __tablename__ = "support_grants"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id"),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    """A signed-in browser or device. The cookie holds a random token; only its hash is stored."""

    __tablename__ = "sessions"
    __table_args__ = (
        # What withdrawing a member's leave for support clears: the few sessions viewing it.
        Index(
            "ix_sessions_support_grant_id",
            "support_grant_id",
            postgresql_where=text("support_grant_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The organisation the session works in, the one an invitation the user accepted last named
    # (`account_for_user` in app/accounts.py); null, or one they are no longer a member of, is
    # the one they joined first. Every tab of the browser shares it. Indexed, since deleting an
    # organisation clears it from every session.
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    # True between a correct password and the authenticator code that must follow it: the
    # session exists, but grants nothing until the code is entered.
    needs_mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    # Shown in the session list so the user can tell which device to revoke.
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # While the session's user, an operator, views a member's account as support from it
    # (app/support.py): the member's leave the view rests on, and when the view ends whatever
    # the leave says. The session stays the operator's: `support_ends_at` set marks the view,
    # and a leave withdrawn, which clears the first, ends it at the next request.
    support_grant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("support_grants.id", ondelete="SET NULL")
    )
    support_ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Browser(Base):
    """A browser that signed in to the account, which its failed sign-ins are counted apart
    for (app/ratelimit.py). Its cookie holds a random token of the browser's own, the same for
    every account it signs in to; the row holds the SHA-256 of the token with the user's id, so
    that two accounts' rows do not show they were used in one browser."""

    __tablename__ = "browsers"
    __table_args__ = (UniqueConstraint("user_id", "token_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    signed_in_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Project(Base):
    """A set of documents and the chats about them. Everything below it is scoped by project."""

    __tablename__ = "projects"
    __table_args__ = (
        _one_of("visibility", PROJECT_VISIBILITIES, "projects"),
        *_trash_indexes("projects", "org_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant: every read of the project's documents, passages, facts and chats checks this.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # organization | private: who sees it besides the owners and admins, who see every project:
    # everyone in the organisation, or the members chosen for it (`ProjectMember`).
    visibility: Mapped[str] = mapped_column(
        String(20), default="organization", server_default="organization"
    )
    # The sample project a new account's organisation starts with (app/sample): the usage page
    # leaves it out, and nothing is uploaded into it.
    sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When a member deleted the project into the trash (app/trash.py), which holds it with
    # everything in it until it is restored or purged; null while it is live.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = _deleted_by()

    organization: Mapped[Organization] = relationship(back_populates="projects")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class ProjectExport(Base):
    """A copy of a project a member asked for: its originals, what was read from them and the
    chats they may read, as one ZIP file the worker makes (app/export.py) and they download until
    it expires. Their private chats are in it, so it is theirs alone and goes with them."""

    __tablename__ = "project_exports"
    __table_args__ = (
        _one_of("status", EXPORT_STATUSES, "project_exports"),
        # A member's exports of a project, which the projects page lists.
        Index("ix_project_exports_project_user", "project_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Indexed for the account purge, whose foreign key would otherwise scan the table.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    # How many originals and bytes the file holds, once it is ready.
    documents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # Why it could not be made, in the member's words.
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Until when it is served, EXPORT_DAYS after it was ready; null until then.
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The documents the member chose in the library, for an export of those alone
    # (app/selection.py); null for the whole project.
    document_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))

    @property
    def chosen(self) -> int | None:
        """How many documents the member chose, which the projects page names; None for the
        whole project."""
        return None if self.document_ids is None else len(self.document_ids)


class ProjectMember(Base):
    """A member of the organisation chosen to see one of its private projects (app/access.py).
    The rows are kept only while the project is private, and go with the membership."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Indexed, since every request of a member who does not see every project asks which
    # private ones they were chosen for.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Folder(Base):
    """A folder in a project's library. Folders nest; a null parent is the project root."""

    __tablename__ = "folders"
    __table_args__ = _trash_indexes("folders", "project_id")

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When the folder went into the trash (app/trash.py): its subfolders and documents that were
    # live then went with it, at the same moment, which is how a restore finds them.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = _deleted_by()


class Upload(Base):
    """The documents one upload request brought in, and who sent them: what the notice that
    their indexing finished is about and for (app/notices.py). The row is made once the request
    has stored its files, so an upload is whole before it can be found settled, and is settled
    once, when none of its documents is waiting or being indexed any more."""

    __tablename__ = "uploads"
    __table_args__ = (
        # What the housekeeper looks through for an upload whose notice nothing posted.
        Index("ix_uploads_unsettled", "id", postgresql_where=text("settled_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Null once the user's account is deleted: the documents stay with the project.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When its last document was ready, failed or set apart, with no retry waiting.
    settled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMailbox(Base):
    """A project's own address, <token>@INBOUND_DOMAIN, whose mail becomes its documents
    (app/inbound.py), and whom it takes mail from: the members who may change the project, from
    their own confirmed addresses, and the senders listed, each an address or a domain. The
    token names the project and nothing more: a message is kept only from a sender the provider
    proved the From address of, so knowing the address lets nobody in who is not let in, and
    the token is kept as it is to be shown. A new address is a new token; the old one then
    reaches nothing."""

    __tablename__ = "project_mailboxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    token: Mapped[str] = mapped_column(String(32), unique=True)
    # Lower-cased, each an address (ap@supplier.com) or a domain (supplier.com, its subdomains
    # with it), in the order they were added.
    senders: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    # Where what arrives goes; the project's root when null, or once the folder is gone or in
    # the trash.
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InboundMessage(Base):
    """A message sent to a project's address and what became of it, for its members to read
    on the address's page (app/inbound.py): its sender and subject, the documents kept, the
    files that were not and why, or why the whole message was turned away. Kept INBOUND_DAYS,
    and once per delivery, so that a provider that delivers a message twice keeps it once."""

    __tablename__ = "inbound_messages"
    __table_args__ = (
        UniqueConstraint("project_id", "delivery"),
        _one_of("outcome", INBOUND_OUTCOMES, "inbound_messages"),
        # A project's messages newest first, which its page lists.
        Index("ix_inbound_messages_project_received", "project_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # The provider's name for the message and its id there ("resend:…", "ses:…").
    delivery: Mapped[str] = mapped_column(String(200))
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # The From address, lower-cased, and the member it is the confirmed address of, if any.
    sender: Mapped[str] = mapped_column(String(320))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    subject: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(20))
    # Why it was turned away, as its members are told; empty otherwise.
    reason: Mapped[str] = mapped_column(Text, default="")
    # How many documents were kept from it.
    kept: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The files of it that were not kept, each {"filename", "reason"}, as an upload rejects them.
    rejected: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # The upload its documents are, which their notice and the automations follow.
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("uploads.id", ondelete="SET NULL"))


class Connector(Base):
    """A folder of a member's Google Drive, Dropbox or OneDrive that a project keeps in step
    with (app/connectors/): its files become the project's documents, a file changed there is
    taken again, and the document of a file that left it goes to the project's trash.

    The member who connected it granted Emudoc access to read their files, which the service
    keeps as a refresh token: encrypted for its row (app/encrypted.py), since a copy of the
    database would otherwise read the member's files. A connector goes with its member's
    membership, as an API key does, so that no grant of someone who left stays behind, and
    pauses while they no longer may change the project."""

    __tablename__ = "connectors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
        _one_of("provider", CONNECTOR_PROVIDERS, "connectors"),
        _one_of("status", CONNECTOR_STATUSES, "connectors"),
        # What the worker's sweep reads each minute: the connectors whose next sync is due.
        Index(
            "ix_connectors_due",
            "next_sync_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The account at the service the member connected, as it names it (an address, else a
    # name), for the members to tell connectors apart.
    account: Mapped[str] = mapped_column(String(320), default="")
    # Encrypted (CONNECTOR_TOKENS in app/encrypted.py); the service's refresh token.
    refresh_token: Mapped[str] = mapped_column(Text)
    # The folder at the service, by its id there, and its name as it was last read; null while
    # it is being chosen.
    folder_ref: Mapped[str | None] = mapped_column(String(300))
    folder_name: Mapped[str] = mapped_column(String(300), default="")
    # Where its files go in the project; the root when null, or once the folder is gone.
    target_folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), default="choosing")
    # The service's mark of how far its changes were read, which the next sync asks from.
    cursor: Mapped[str | None] = mapped_column(Text)
    # Why the last sync did not finish, or why the connector is paused, as its members are
    # told; empty when it went well.
    problem: Mapped[str] = mapped_column(Text, default="")
    synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConnectorFile(Base):
    """A file of a connector's folder as the last sync found it: its id and version at the
    service, its path below the folder, and the document made of it, or why none was. A file
    whose version is unchanged is not read again, so a document deleted in the project stays
    deleted while its file stays as it was."""

    __tablename__ = "connector_files"
    __table_args__ = (UniqueConstraint("connector_id", "remote_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    remote_id: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(Text)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    # Why it is not a document, as an upload rejects a file; empty when it is one.
    reason: Mapped[str] = mapped_column(Text, default="")
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256"),
        # A page of a folder's files in the library's order (`list_documents` in
        # app/api/documents.py), the root's being those whose folder is null; the leading
        # column serves every lookup by project as well.
        Index("ix_documents_listing", "project_id", "folder_id", "filename", "id"),
        # A page of a project's documents, the newest first, as the versioned API lists them
        # (app/api/v1/library.py): the same short scan whatever the project holds.
        Index("ix_documents_project_id_id", "project_id", "id"),
        # A page of a folder in the library's other orders (`SORTS` in app/api/documents.py): as
        # added, by size, and by the document's own date, those it has before those without one.
        Index("ix_documents_listing_added", "project_id", "folder_id", "id"),
        Index("ix_documents_listing_size", "project_id", "folder_id", "size_bytes", "id"),
        Index("ix_documents_listing_date", "project_id", "folder_id", "doc_date", "id"),
        *_trash_indexes("documents", "project_id"),
        # A null review passes, as a check that is not false does; so does a null view.
        _one_of("review", REVIEW_STATES, "documents"),
        _one_of("view_state", VIEW_STATES, "documents"),
        # Whoever is assigned a document is assigned its review, which it is in; and a review
        # has the moment it was moved to its state, which orders the reviews view.
        CheckConstraint("assignee_id IS NULL OR review IS NOT NULL", name="ck_documents_assignee"),
        CheckConstraint(
            "(review IS NULL) = (review_changed_at IS NULL)",
            name="ck_documents_review_changed_at",
        ),
        # A project's reviews of a state in the order the reviews view lists them
        # (app/reviews.py); the documents in none, nearly all of them, are left out.
        Index(
            "ix_documents_review",
            "project_id",
            "review",
            "review_changed_at",
            "id",
            postgresql_where=text("review IS NOT NULL"),
        ),
        # For the account purge and a member leaving, whose assignments go.
        Index(
            "ix_documents_assignee_id",
            "assignee_id",
            postgresql_where=text("assignee_id IS NOT NULL"),
        ),
        Index(
            "ix_documents_review_changed_by",
            "review_changed_by",
            postgresql_where=text("review_changed_by IS NOT NULL"),
        ),
        CheckConstraint(
            "edited_details <@ ARRAY[{}]::varchar[]".format(
                ", ".join(f"'{name}'" for name in DOCUMENT_DETAILS)
            ),
            name="ck_documents_edited_details",
        ),
        # The copies of a file in the organisation's other projects (app/duplicates.py), which
        # the unique constraint above, led by the project, cannot find.
        Index("ix_documents_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Deleting a folder deletes the documents in it, as a file manager would.
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500, collation=NATURAL_ORDER))
    mime_type: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    # The key of the uploaded file in the storage (app/storage.py), its current version's
    # (app/versions.py); the document's other objects sit under the same prefix.
    storage_key: Mapped[str] = mapped_column(Text)
    # Which version of the document the file is, from 1, and the bytes of its earlier versions
    # (`DocumentVersion`), which the usage page's storage counts with the file's own.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    versions_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    # The upload it came in with, and so who brought it in (`Upload`); null for a document from
    # before uploads were kept, and while its request is still storing the others.
    upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), index=True
    )

    # queued | processing | ready | failed | quarantined (the malware scan flagged the file:
    # it is neither indexed nor served, and `error` carries the verdict, app/scanning.py)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # {stage: {"status": pending|running|done|skipped|failed, "detail": str}}
    stages: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    # When the operator was mailed about the quarantine (app/scanning.py); null until then,
    # and for a document that was never quarantined or was cleared by a later scan.
    quarantine_reported_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # What bringing the document in, indexing it and viewing its pages used, by kind and
    # summed over every run for it, as app/usage.py keeps it in step with the usage events:
    # {kind: {unit: quantity, "model": str, "cost_micros": int}}.
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    page_count: Mapped[int | None] = mapped_column(Integer)
    # [[width, height], ...] in PDF points, one per page; empty for formats without pages.
    page_sizes: Mapped[list] = mapped_column(JSONB, default=list)
    # Its view (app/conversion.py): the PDF the converter made of a Word, PowerPoint, Excel, RTF
    # or HTML file, kept at `view_key`, whose pages the document pane shows it by. One of
    # VIEW_STATES, or null for none; the size of each of its pages as `page_sizes` holds a
    # PDF's, empty until it is ready; and while it failed, the sentence a member is told why.
    view_state: Mapped[str | None] = mapped_column(String(20))
    view_page_sizes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    view_error: Mapped[str | None] = mapped_column(Text)
    # Its title, kind and date as everything reads them (the library, its search, the agent): a
    # member's where one set it (`edited_details`, app/details.py), else what
    # its last reading found, which `read_title`, `read_doc_type` and `read_doc_date` keep either
    # way, for the member to take again.
    title: Mapped[str | None] = mapped_column(String(500))
    doc_type: Mapped[str | None] = mapped_column(String(100), index=True)
    doc_date: Mapped[dt.date | None] = mapped_column(Date)
    read_title: Mapped[str | None] = mapped_column(String(500))
    read_doc_type: Mapped[str | None] = mapped_column(String(100))
    read_doc_date: Mapped[dt.date | None] = mapped_column(Date)
    # The text search configuration its passages are indexed with (app/languages.py): the one
    # the parse stage detected in its text, or a member's, as the other details; null for its
    # organisation's, which a change of the organisation's moves it to.
    language: Mapped[str | None] = mapped_column(String(63))
    read_language: Mapped[str | None] = mapped_column(String(63))
    # The ISO 639 code of the language the parse stage detected, which says how its dates and
    # numbers are written (app/locales.py) where `read_language` is `simple` and names none.
    read_language_code: Mapped[str | None] = mapped_column(String(8))
    # The names of DOCUMENT_DETAILS a member set, in no order.
    edited_details: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), default=list, server_default="{}"
    )
    summary: Mapped[str | None] = mapped_column(Text)
    # The kind of document of the extraction template its facts were read for (app/
    # extraction.py), which `doc_type` then names too; null when they were read for none, and
    # until the enrich stage has run.
    template: Mapped[str | None] = mapped_column(String(100))
    # Its review (app/reviews.py): one of REVIEW_STATES, null for none; the member it is
    # assigned to, null for nobody and once they leave the organisation; and who moved it to its
    # state and when, null once their account is gone, or for an automation. Each name is read
    # with the document as it is now, which the library and the reviews view show.
    review: Mapped[str | None] = mapped_column(String(20))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assignee: Mapped[str | None] = column_property(
        select(User.name).where(User.id == assignee_id).correlate_except(User).scalar_subquery()
    )
    review_changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    review_changer: Mapped[str | None] = column_property(
        select(User.name)
        .where(User.id == review_changed_by)
        .correlate_except(User)
        .scalar_subquery()
    )
    review_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # When the document was first ready to be searched and asked about, however many runs its
    # upload's indexing took; null until then. A later run ("Index again", a new embedding
    # model) leaves it, so that the time from the upload (`DOCUMENT_READY`, app/metrics.py) is
    # measured once, as the member waited for it.
    ready_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # When the document went into the trash (app/trash.py), by itself or with its folder; null
    # while it is live. Its bytes still hold its place under the unique constraint above.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = _deleted_by()

    project: Mapped[Project] = relationship(back_populates="documents")

    @property
    def display_title(self) -> str:
        return self.title or self.filename

    @property
    def storage_prefix(self) -> str:
        """The prefix in the storage that everything this document owns sits under, every
        version's files with it."""
        return files_prefix(self.storage_key)

    @property
    def version_prefix(self) -> str:
        """The prefix of the current version's files: its original, and the pages and the
        thumbnail drawn from it."""
        return self.storage_key.rpartition("/")[0] + "/"

    def page_image_key(self, page: int) -> str:
        return f"{self.version_prefix}pages/{page}.png"

    @property
    def thumbnail_key(self) -> str:
        return f"{self.version_prefix}thumbnail.png"

    @property
    def view_key(self) -> str:
        """Where the current version's view is kept, the PDF its pages are drawn from in the
        place of the original's (app/conversion.py)."""
        return f"{self.version_prefix}view.pdf"


class DocumentVersion(Base):
    """An earlier version of a document, kept once a new file took its place (app/versions.py):
    its file, as it was uploaded, and its text as the parse read it, to open and download. Only
    the document's current file is searched, asked about and read for facts; the document row
    is that file, so everything that names the document goes on naming it."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    # The document's name, type and file while this was its version.
    filename: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Whether its file may be served: the malware scan had passed it (or there was no scanner)
    # and had not flagged it when the next version replaced it. One replaced before its scan
    # is kept unserved, as a document waiting for its scan is.
    scan_passed: Mapped[bool] = mapped_column(Boolean)
    # Its text: the blocks the parse read, in order, as [{kind, level, text, page}]; empty for a
    # version replaced before it was read. Loaded only when asked for.
    blocks: Mapped[list] = mapped_column(JSONB, deferred=True)
    # What bringing it in and indexing it used, as `Document.usage` held it (app/usage.py).
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The upload it came in with, and so who brought it in (`Upload`); null as the document's
    # own would be.
    upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), index=True
    )
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    replaced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentFavorite(Base):
    """A document a member starred, which the library lists among their favourites. The member's
    alone, as a chat's pin is; the row goes with either."""

    __tablename__ = "document_favorites"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed for an account deleted, whose favourites go with it.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    starred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentNote(Base):
    """A document's note (app/notes.py): one text its project's members share, which whoever may
    change the project writes and everyone who reads the document reads, with who changed it
    last. A blank note is no row; the row goes with the document."""

    __tablename__ = "document_notes"
    __table_args__ = (
        # For an account deleted, whose notes stay without its name.
        Index(
            "ix_document_notes_changed_by",
            "changed_by",
            postgresql_where=text("changed_by IS NOT NULL"),
        ),
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(Text)
    # Who changed it last and when; null once their account is gone. The name is read as it is
    # now, as a review's changer's is.
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    changer: Mapped[str | None] = column_property(
        select(User.name).where(User.id == changed_by).correlate_except(User).scalar_subquery()
    )
    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CommentThread(Base):
    """A thread of comments on a document (app/comments.py), about the document as a whole or a
    passage of it. Started from a passage, it keeps the words quoted and their page in the
    version it was about: the passage itself goes when the document is read again or given a
    version, and the quote stays. Everyone who sees the project reads and writes in it, and a
    resolved thread stays to read. The row goes with the document."""

    __tablename__ = "comment_threads"
    __table_args__ = (
        CheckConstraint("passage_id IS NULL OR quote IS NOT NULL", name="ck_comment_threads_quote"),
        CheckConstraint(
            "resolved_by IS NULL OR resolved_at IS NOT NULL", name="ck_comment_threads_resolved"
        ),
        Index("ix_comment_threads_document", "document_id", "id"),
        # For a reading that deletes the document's passages, which sets theirs null.
        Index(
            "ix_comment_threads_passage",
            "passage_id",
            postgresql_where=text("passage_id IS NOT NULL"),
        ),
        Index(
            "ix_comment_threads_resolved_by",
            "resolved_by",
            postgresql_where=text("resolved_by IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    # The passage it was started from, while the document's reading still holds it.
    passage_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("passages.id", ondelete="SET NULL")
    )
    # The words it is about, and the page and version of the document they were on.
    quote: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Who resolved it and when; both null while it is open, the name once their account is gone.
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolver: Mapped[str | None] = column_property(
        select(User.name).where(User.id == resolved_by).correlate_except(User).scalar_subquery()
    )


class Comment(Base):
    """A member's comment in a thread (app/comments.py): their words, with the members they
    mention written in them as `<@id>`, when they wrote it and when they last changed it. The
    comment outlives its writer's account without their name, as a note does."""

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_thread", "thread_id", "id"),
        # For the account's export and its purge.
        Index("ix_comments_author", "author_id", postgresql_where=text("author_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("comment_threads.id", ondelete="CASCADE"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author: Mapped[str | None] = column_property(
        select(User.name).where(User.id == author_id).correlate_except(User).scalar_subquery()
    )
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    edited_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVisit(Base):
    """When a member last opened a document, which the library lists among their recent ones:
    the newest `RECENT` of each project's are kept (app/recents.py), the rest let go as newer
    ones come. The member's alone; the row goes with either."""

    __tablename__ = "document_visits"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed for the member's list, and for an account deleted, whose visits go with it.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Block(Base):
    """The parsed document: ordered blocks, each with its page and position."""

    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    # heading | paragraph | list_item | table
    kind: Mapped[str] = mapped_column(String(20))
    level: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    # [x0, top, x1, bottom] in PDF points from the page's top-left corner.
    bbox: Mapped[list | None] = mapped_column(JSONB)


class Section(Base):
    """One node of a document's outline."""

    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    level: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # Block ordinals covered by the section and its subsections; block_end is exclusive.
    block_start: Mapped[int] = mapped_column(Integer)
    block_end: Mapped[int] = mapped_column(Integer)


class Passage(Base):
    """A leaf of the outline: the exact text that search returns and answers cite."""

    __tablename__ = "passages"
    __table_args__ = (
        # A search first lists the languages a project holds, then probes the text index once
        # per language; the leading column serves every lookup by project as well.
        Index("ix_passages_project_language", "project_id", "language"),
        Index("ix_passages_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_passages_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # "Document title › Section › Subsection": indexed with the text so a hit carries its place.
    context: Mapped[str] = mapped_column(Text, default="")
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    block_start: Mapped[int] = mapped_column(Integer)
    block_end: Mapped[int] = mapped_column(Integer)

    # The text search configuration the passage was indexed with: the one `tsv` is built from
    # and the one a search parses its query with for this row. It is `regconfig`, not text,
    # because a generated column's expression must be immutable and casting text to a
    # configuration is not. A passage gets its document's language, else its organisation's,
    # else FTS_LANGUAGE (app/languages.py).
    language: Mapped[str] = mapped_column(REGCONFIG, server_default="english")
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector(language, coalesce(context, '')), 'B') || "
            "setweight(to_tsvector(language, text), 'A')",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_settings.embedding_dim))
    embedding_model: Mapped[str | None] = mapped_column(String(200))


class Fact(Base):
    """A typed field extracted from a document, linked to where it was found."""

    __tablename__ = "facts"
    __table_args__ = (
        # What a check's deletion looks through for the fact it set: the few checked ones.
        Index("ix_facts_check_id", "check_id", postgresql_where=text("check_id IS NOT NULL")),
        # A project's facts, and a page of them in order as the versioned API lists them
        # (app/api/v1/query.py), the same short scan whatever the project holds.
        Index("ix_facts_project_id_id", "project_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100), index=True)
    # One of FACT_TYPES.
    value_type: Mapped[str] = mapped_column(String(20), default="text")
    value: Mapped[str] = mapped_column(Text)
    number: Mapped[float | None] = mapped_column(Float)
    date: Mapped[dt.date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))

    quote: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    block_ordinal: Mapped[int | None] = mapped_column(Integer)
    passage_id: Mapped[int | None] = mapped_column(ForeignKey("passages.id", ondelete="SET NULL"))
    # True when the quote was found in the source block: the model's quote, not a person's word,
    # which is `check`.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # A member's check of the fact, which outlives the row (app/fact_checks.py): with a
    # correction, the columns above hold the member's value and the check what the model read.
    check_id: Mapped[int | None] = mapped_column(ForeignKey("fact_checks.id", ondelete="SET NULL"))
    check: Mapped["FactCheck | None"] = relationship(lazy="selectin")


class FactCheck(Base):
    """A member's word on a fact: that the model read it right, or what the document says in its
    place (app/fact_checks.py). Kept by the document, the field's name and the model's reading,
    since the facts themselves are written anew at every run of the enrich stage, which gives
    the check again to a fact reading the same."""

    __tablename__ = "fact_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    # What the model read, as the fact held it.
    read_value: Mapped[str] = mapped_column(Text)
    read_number: Mapped[float | None] = mapped_column(Float)
    read_date: Mapped[dt.date | None] = mapped_column(Date)
    read_currency: Mapped[str | None] = mapped_column(String(10))
    # The member's value, typed as the fact's, when they wrote another; null when they checked
    # the model's.
    value: Mapped[str | None] = mapped_column(Text)
    number: Mapped[float | None] = mapped_column(Float)
    date: Mapped[dt.date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))
    checked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    checked_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The member's name as it is now, which the table shows; None once their account is gone.
    checker: Mapped[str | None] = column_property(
        select(User.name).where(User.id == checked_by).correlate_except(User).scalar_subquery()
    )

    @property
    def corrected(self) -> bool:
        return self.value is not None


class FactChange(Base):
    """What a document's last reading by the enrich stage changed of the facts the reading before
    left, field by field for the fields of its templates (app/fact_changes.py): one row a
    document, written anew at each reading that had one before it, so that a template changed
    and its documents read again show what the change did. Its values are what the document
    says, as its facts are, and go with the document."""

    __tablename__ = "fact_changes"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_fact_changes_changes"),
        # A project's last readings, the newest first, as the page lists them.
        Index("ix_fact_changes_project_read", "project_id", "read_at"),
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    read_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The kind of the template the document was read for before and now; null for none.
    template_before: Mapped[str | None] = mapped_column(String(100))
    template_after: Mapped[str | None] = mapped_column(String(100))
    # [{"name", "before", "after", "before_count", "after_count", "checked"}]: for each field
    # whose facts changed, the values read before and no longer, and those read now and not
    # before, each list cut at a few, with how many there were; `checked` when a value gone was
    # a member's check, which the new reading disagrees with.
    changes: Mapped[list] = mapped_column(JSONB, default=list)
    # Whether the reading changed a field or the template, and how many of its fields lost a
    # member's check: the page lists the changed readings, those first.
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    checks_unheld: Mapped[int] = mapped_column(Integer, default=0)


class ExtractionTemplate(Base):
    """The fields a project's documents of one kind are read for (app/extraction.py): what the
    enrich stage asks the model to find in an invoice, a contract or a kind of the members' own,
    each field becoming facts under its name. A project's template replaces the built-in one of
    its kind, which applies while the project has none of its own."""

    __tablename__ = "extraction_templates"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_type"),
        CheckConstraint("jsonb_typeof(fields) = 'array'", name="ck_extraction_templates_fields"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # The kind of document it is for, in lowercase words ("invoice", "delivery note"): what the
    # model names when it finds a document is one, and the document's `doc_type` then.
    doc_type: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # [{"name", "type", "description", "required", "examples"}], in the order they are asked.
    fields: Mapped[list] = mapped_column(JSONB, default=list)
    # Who made it; null once their account is gone. Indexed for the account purge.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CrossCheck(Base):
    """A rule a project's documents are checked by (app/cross_checks.py): a field of one kind's
    documents compared with a field of the same document, of the documents of another kind that
    state the same key, or of every document of another kind, value by value or line by line.
    What it finds is worked out from the facts whenever it is asked, so nothing here goes stale
    when a document is read again."""

    __tablename__ = "cross_checks"
    __table_args__ = (
        _one_of("pairing", CROSS_CHECK_PAIRINGS, "cross_checks"),
        _one_of("comparison", CROSS_CHECK_COMPARISONS, "cross_checks"),
        # The keys name the fields a pairing by key goes by, and nothing otherwise.
        CheckConstraint(
            "(pairing = 'key' AND left_key IS NOT NULL AND right_key IS NOT NULL) "
            "OR (pairing <> 'key' AND left_key IS NULL AND right_key IS NULL)",
            name="ck_cross_checks_keys",
        ),
        CheckConstraint("margin >= 0", name="ck_cross_checks_margin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    # The kinds of document and the fields compared, as the project's templates name them: the
    # documents checked are those read for the left kind's template.
    left_kind: Mapped[str] = mapped_column(String(100))
    left_field: Mapped[str] = mapped_column(String(100))
    right_kind: Mapped[str] = mapped_column(String(100))
    right_field: Mapped[str] = mapped_column(String(100))
    # One of CROSS_CHECK_PAIRINGS, and for a pairing by key the field of each side it goes by.
    pairing: Mapped[str] = mapped_column(String(20))
    left_key: Mapped[str | None] = mapped_column(String(100))
    right_key: Mapped[str | None] = mapped_column(String(100))
    # Whether each of the fields' values is compared, line by line, with the value of the other
    # side that names the same item, rather than the first value of each side.
    by_item: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # One of CROSS_CHECK_COMPARISONS.
    comparison: Mapped[str] = mapped_column(String(20))
    # How far apart the two may be and still agree: days for dates, else in the values' units.
    margin: Mapped[float] = mapped_column(Float, default=0, server_default=text("0"))
    # Whether the values of every document paired are added up before they are compared.
    summed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Who made it; null once their account is gone. Indexed for the account purge.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkflowRun(Base):
    """A run of a project's cross-checks (app/check_runs.py): the rules as they were when a
    member ran them, what each found over the documents as they read then, and the discrepancy
    report the agent wrote from that when a member asked for one.

    What a rule finds is otherwise worked out at each request and kept nowhere; a run keeps it as
    it was found, in `workflow_findings`. The facts and passages a finding came from are written
    anew at every reading of their document, so a run names the documents by id and keeps the
    values, quotes and places as read, never a fact to join on later. The worker works a run out
    (`run_workflow` jobs), and the page follows `status` and `report_status` until they settle.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        _one_of("status", RUN_STATUSES, "workflow_runs"),
        _one_of("report_status", REPORT_STATUSES, "workflow_runs"),
        # The project's runs, newest first, as its list pages them.
        Index("ix_workflow_runs_project", "project_id", "id"),
        # What the agent's runs in flight count (app/answers.py): the few reports being written.
        Index(
            "ix_workflow_runs_reporting",
            "org_id",
            postgresql_where=text("report_status IN ('queued', 'writing')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Who ran it; null once their account is gone. Indexed for the account purge.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # One of RUN_STATUSES.
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    # The rules as they were when the run was made, in the order their findings are kept: each a
    # cross-check's id (which may be gone since), its title and what it compares, and once the
    # run is worked out, why it could not be (`problem`) or what it found by outcome (`counts`).
    rules: Mapped[list] = mapped_column(JSONB)
    # Why the run failed, in the sentence its members read.
    error: Mapped[str | None] = mapped_column(Text)
    # One of REPORT_STATUSES; null while no report was asked for.
    report_status: Mapped[str | None] = mapped_column(String(20))
    # The report as the agent wrote it, citing passages as [P<id>], the passages it was shown,
    # which it may cite, and how its run ended when not as it should (cut off, failed).
    report: Mapped[str] = mapped_column(Text, default="", server_default="")
    report_sources: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    report_notice: Mapped[str | None] = mapped_column(Text)
    report_error: Mapped[str | None] = mapped_column(Text)
    # What the run used, by kind (`report`: the report's model calls, tokens and cost), as
    # app/usage.py keeps it; the findings are worked out without a model and cost nothing.
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The automation that made the run, when one did rather than a member (app/automations.py);
    # null for a member's run, and once the automation is deleted, `trigger` still naming it.
    automation_id: Mapped[int | None] = mapped_column(
        ForeignKey("automations.id", ondelete="SET NULL"), index=True
    )
    # What an automation's run was made for: the automation's name as it was, the ids of the
    # documents that arrived (`documents`, those it watches; `partners`, those its checks compare
    # them with), the names of the first few, and the job that made it, which makes it once.
    trigger: Mapped[dict | None] = mapped_column(JSONB)
    # When an automation's run told whom it names what it found: once, as the run settles.
    told_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When its findings were kept, or it failed.
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowFinding(Base):
    """What one rule of a run found for one value of a document it checked, as it was found
    (app/check_runs.py): the document by id and by name as they were, the outcome and its
    reason, the value checked and the values it was compared with as `FactOut`s were then, the
    documents paired with it, and how far apart the two were. Nothing here joins a fact, a
    passage or a document: they change or go, and the finding stays what it was."""

    __tablename__ = "workflow_findings"
    __table_args__ = (
        _one_of("outcome", FINDING_OUTCOMES, "workflow_findings"),
        _one_of("reason", FINDING_REASONS, "workflow_findings"),
    )

    run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), primary_key=True
    )
    # The rule's place in the run's `rules`, and the finding's in what the rule found, in the
    # order the rule lists them: those that differ first, then by the document's name.
    rule: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    outcome: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(20))
    document_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    left: Mapped[dict | None] = mapped_column(JSONB)
    right: Mapped[list] = mapped_column(JSONB, default=list)
    partners: Mapped[list] = mapped_column(JSONB, default=list)
    difference: Mapped[float | None] = mapped_column(Float)


class Automation(Base):
    """A project's rule for running its cross-checks on its own (app/automations.py): when
    documents of a kind, or any, arrive in a folder or anywhere in the project, and when it says
    so, documents its checks compare them with anywhere, the worker runs the checks it names, or
    every one the project has, once the arrivals have gone quiet, with the model's discrepancy
    report when asked, and tells whom it names what they found about what arrived."""

    __tablename__ = "automations"
    __table_args__ = (
        _one_of("audience", AUTOMATION_AUDIENCES, "automations"),
        _one_of("review", AUTOMATION_REVIEWS, "automations"),
        # Whom it assigns what it puts in review, so only with a review.
        CheckConstraint(
            "assignee_id IS NULL OR review IS NOT NULL", name="ck_automations_assignee"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    # The kind of document it watches, as `documents.template` names the kind a document was
    # read for; null for any document.
    kind: Mapped[str | None] = mapped_column(String(100))
    # The folder whose documents it watches, its subfolders' with them; null for the whole
    # project. A folder deleted for good takes its automations with it.
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), index=True
    )
    # Whether it runs every cross-check the project has when it runs, or the ones
    # `automation_checks` names.
    every_check: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Whether the model writes the run's discrepancy report.
    report: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # One of AUTOMATION_AUDIENCES.
    audience: Mapped[str] = mapped_column(String(20), default="maker", server_default="maker")
    # Whether it also runs when documents arrive anywhere in the project that its checks compare
    # the ones it watches with: a delivery note that lets an invoice's quantities be checked.
    recheck: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Off, nothing that arrives runs it.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # One of AUTOMATION_REVIEWS, what its run puts in review, null for nothing; and the member it
    # assigns them to, null for nobody and once they leave the organisation, indexed for the
    # account purge.
    review: Mapped[str | None] = mapped_column(String(20))
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Who made it, whom `maker` tells; null once their account is gone. Indexed for the account
    # purge.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AutomationCheck(Base):
    """A cross-check an automation runs, for one that runs chosen ones rather than every one;
    a cross-check deleted goes from the automations that chose it."""

    __tablename__ = "automation_checks"

    automation_id: Mapped[int] = mapped_column(
        ForeignKey("automations.id", ondelete="CASCADE"), primary_key=True
    )
    cross_check_id: Mapped[int] = mapped_column(
        ForeignKey("cross_checks.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class AutomationArrival(Base):
    """A document that arrived for an automation and waits for its next run
    (app/automations.py): one it watches, or with `recheck`, one its checks compare those with.
    The run takes the automation's arrivals as it is made, and a document deleted meanwhile goes
    with its row."""

    __tablename__ = "automation_arrivals"

    automation_id: Mapped[int] = mapped_column(
        ForeignKey("automations.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    recheck: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    arrived_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QuestionSet(Base):
    """A project's questions saved to be asked of its documents on their own
    (app/question_sets.py): on a schedule, every day or one day a week at an hour of its maker's
    zone, as documents arrive, or both, each run answering every question as a chat would and
    keeping the answers as a report (`QuestionReport`), told to whom it names."""

    __tablename__ = "question_sets"
    __table_args__ = (
        _one_of("schedule", QUESTION_SET_SCHEDULES, "question_sets"),
        _one_of("audience", AUTOMATION_AUDIENCES, "question_sets"),
        # A weekly schedule names its day, and no other set does.
        CheckConstraint(
            "(schedule = 'weekly') = (weekday IS NOT NULL)", name="ck_question_sets_weekday"
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_question_sets_weekday_range"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="ck_question_sets_hour"),
        # Something runs it: a set with neither would wait for good.
        CheckConstraint("schedule IS NOT NULL OR on_arrivals", name="ck_question_sets_runs"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    # The questions in the order they are asked, each as a member would type it in a chat.
    questions: Mapped[list] = mapped_column(JSONB)
    # One of QUESTION_SET_SCHEDULES, null for none; the day of a weekly one, Monday 0; and the
    # hour of the day it runs at, in its maker's zone.
    schedule: Mapped[str | None] = mapped_column(String(20))
    weekday: Mapped[int | None] = mapped_column(Integer)
    hour: Mapped[int] = mapped_column(Integer, default=8, server_default=text("8"))
    # Whether documents arriving in the project run it, once they have gone quiet.
    on_arrivals: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # One of AUTOMATION_AUDIENCES: whom its schedule's and its arrivals' reports are told to.
    audience: Mapped[str] = mapped_column(String(20), default="maker", server_default="maker")
    # Off, nothing runs it, a member's press aside.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # When its schedule runs it next, null without one or while it is off.
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Who made it, whose zone its hour is in and whom `maker` tells; null once their account is
    # gone. Indexed for the account purge.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class QuestionSetArrival(Base):
    """A document that arrived for a question set that runs on arrivals, waiting for its next
    report (app/question_sets.py), which takes the set's arrivals as it is made; a document
    deleted meanwhile goes with its row."""

    __tablename__ = "question_set_arrivals"

    set_id: Mapped[int] = mapped_column(
        ForeignKey("question_sets.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    arrived_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QuestionReport(Base):
    """One run of a question set (app/question_reports.py): its questions as they were, each
    answered by the agent over the project's documents as they read then, the answers kept as a
    chat keeps one, with the passages they cite, and what made it run.

    The worker answers the questions one after another (`write_question_report` jobs), each
    counted as one of the workspace's questions as it is asked, and the page follows `status` until
    it settles. A report holds what documents say, as a chat does, and is kept as long."""

    __tablename__ = "question_reports"
    __table_args__ = (
        _one_of("status", QUESTION_REPORT_STATUSES, "question_reports"),
        _one_of("trigger", REPORT_TRIGGERS, "question_reports"),
        _one_of("audience", AUTOMATION_AUDIENCES, "question_reports"),
        # The project's reports, newest first, as its list pages them.
        Index("ix_question_reports_project", "project_id", "id"),
        # What the agent's runs in flight count (app/answers.py): the few reports being written.
        Index(
            "ix_question_reports_writing",
            "org_id",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # The set it ran, null once the set is deleted, `title` still naming it.
    set_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_sets.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    # One of REPORT_TRIGGERS; the member who ran it, for `member`, null once their account is
    # gone; and whom it is told to, the set's audience as it was, or for a member's run them.
    trigger: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    audience: Mapped[str] = mapped_column(String(20), default="maker", server_default="maker")
    # What arrived for it: the ids of the documents, and the names of the first few.
    arrived: Mapped[dict | None] = mapped_column(JSONB)
    # One of QUESTION_REPORT_STATUSES, and why it failed, in the sentence its members read.
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    # Each question as it was asked, in order, with how far its answer is (one of
    # QUESTION_REPORT_STATUSES, `running` while it is asked), and once settled the answer as the
    # agent wrote it, citing passages as [P<id>], the passages it was shown, and how its run
    # ended when not as it should (cut off, failed, refused by the day's spending cap).
    answers: Mapped[list] = mapped_column(JSONB)
    # What its answers used, by kind (`answer`: the model calls, tokens and cost), as
    # app/usage.py keeps it.
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # When it told whom it names that it was kept: once, as it settles.
    told_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When its last answer was kept, or it failed.
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(Base):
    """A document the agent wrote from a project's documents for its members to edit and send
    (app/drafts.py): a reply email, a dispute letter, a summary memo, a comparison table or a
    report, with what was asked of it, the documents it is about, and its versions.

    The agent writes a version whole as a worker's `write_draft` job, which the page follows by
    `status`; each member's save and each writing again adds another (`DraftVersion`), the older
    ones kept. A draft is the project's: everyone who sees the project reads it, and whoever may
    change the project changes it.
    """

    __tablename__ = "drafts"
    __table_args__ = (
        _one_of("kind", DRAFT_KINDS, "drafts"),
        _one_of("status", DRAFT_STATUSES, "drafts"),
        # The project's drafts, newest first, as its list pages them.
        Index("ix_drafts_project", "project_id", "id"),
        # What the agent's runs in flight count (app/answers.py): the few drafts being written.
        Index(
            "ix_drafts_writing", "org_id", postgresql_where=text("status IN ('queued', 'writing')")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Who asked for it first; null once their account is gone. Indexed for the account purge.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # One of DRAFT_KINDS.
    kind: Mapped[str] = mapped_column(String(30))
    # Its name in the project's list, made from its kind and what it is about when it was asked.
    title: Mapped[str] = mapped_column(String(300))
    # What the member asked of it in their own words, which a writing again may change.
    instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    # The documents it is about, as `{"id", "title"}` when it was asked: their ids, which may be
    # gone since, and their names as they were.
    documents: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The run of the project's cross-checks whose findings about those documents its last
    # writing was given, the newest that had any; null for none, and once the run is deleted.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), index=True
    )
    # One of DRAFT_STATUSES: the agent's writing, the first or the last asked for.
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    # Why the last writing failed, and how it ended when not as it should (cut off), in the
    # sentences its members read.
    error: Mapped[str | None] = mapped_column(Text)
    notice: Mapped[str | None] = mapped_column(Text)
    # What its writings used, by kind (`draft`: the model's calls, tokens and cost), as
    # app/usage.py keeps it.
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When it was last asked for, written or saved, which its retention counts from
    # (app/retention.py).
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DraftVersion(Base):
    """A version of a draft, as the agent wrote it or a member saved it, never changed after.

    Its text cites passages as [P<id>], as an answer does, and keeps the passages it cites as
    they were (`sources`): a document's passages are written anew at every reading, so a version
    reads the same after its documents are read again or deleted.
    """

    __tablename__ = "draft_versions"

    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"), primary_key=True
    )
    # 1 for the first, each next one more.
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Before `text`, whose column would hide SQLAlchemy's `text` in the class body.
    sources: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    text: Mapped[str] = mapped_column(Text)
    # True for the agent's writing, which `user_id` asked for; false for a member's save.
    by_model: Mapped[bool] = mapped_column(Boolean)
    # Who asked for it or saved it; null once their account is gone. Indexed for the purge.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DraftTemplate(Base):
    """How a project's drafts of one kind read (app/draft_templates.py): their tone, their parts
    in order and what else every draft of the kind should do. A project's template replaces the
    built-in one of its kind, which applies while the project has none of its own."""

    __tablename__ = "draft_templates"
    __table_args__ = (
        UniqueConstraint("project_id", "kind"),
        _one_of("kind", DRAFT_KINDS, "draft_templates"),
        CheckConstraint("jsonb_typeof(sections) = 'array'", name="ck_draft_templates_sections"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # One of DRAFT_KINDS.
    kind: Mapped[str] = mapped_column(String(30))
    tone: Mapped[str] = mapped_column(Text, default="", server_default="")
    # [{"heading", "description", "required"}], in the order a draft has them.
    sections: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    guidance: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Who made it; null once their account is gone. Indexed for the account purge.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MyFile(Base):
    """A file of a member's own in a project, among their My files (app/my_files.py): a Word
    document or an Excel workbook they keep there, which the agent writes or edits for them, seen
    by them alone until they copy it into the project's library.

    It is no document: nothing indexes, searches or counts it as one, and none of what reads a
    project's documents reaches it. Its versions (`MyFileVersion`) hold its files, the newest
    being the file; it goes with its member's account, their leaving the organisation and the
    project, and is deleted for good, never put in the trash.
    """

    __tablename__ = "my_files"
    __table_args__ = (
        _one_of("kind", MY_FILE_KINDS, "my_files"),
        # A member's files in a project, newest first, as their list shows them.
        Index("ix_my_files_owner", "project_id", "user_id", "updated_at"),
        # An answer's n-th file, which a run of it that starts over writes again.
        UniqueConstraint("answer_id", "answer_ordinal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tenant, as for every row a member reaches by its id.
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Whose it is, the one member who reaches it. Indexed for an account purged and a member
    # leaving the organisation, whose files go with them.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Its name with its extension, which its kind keeps.
    name: Mapped[str] = mapped_column(String(500))
    # One of MY_FILE_KINDS.
    kind: Mapped[str] = mapped_column(String(10))
    # The document of the project's library it was copied from to be edited, which copying it
    # back may give a new version; null for one of its own, and once that document is gone.
    origin_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    # The answer whose agent wrote it, and which of the answer's files it is, from 0, so that a
    # run of the answer that starts over, or a try asked again, writes it again as a version
    # rather than a second file (app/agent/writing.py); null for an upload, and once the chat is
    # gone.
    answer_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL")
    )
    answer_ordinal: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When its last version was added or it was renamed, which its member's list is ordered by.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MyFileVersion(Base):
    """A version of a file in My files, as its member uploaded it or the agent wrote it, never
    changed after: its file, its malware scan, and its view, the PDF the converter makes of it,
    whose pages the page shows it by as a Word or Excel document's are (app/conversion.py)."""

    __tablename__ = "my_file_versions"
    __table_args__ = (
        UniqueConstraint("file_id", "number"),
        _one_of("scan", MY_FILE_SCANS, "my_file_versions"),
        _one_of("view_state", VIEW_STATES, "my_file_versions"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("my_files.id", ondelete="CASCADE"))
    # 1 for the first, each next one more.
    number: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    # True for the agent's writing, which `user_id` asked for; false for the member's upload.
    by_model: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Who uploaded it or asked for it; null once their account is gone. Indexed for the purge.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # What changed in it, in the agent's words or the page's ("Uploaded"), for its list.
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # One of MY_FILE_SCANS; only a version `passed` or `skipped` is served, drawn or copied.
    scan: Mapped[str] = mapped_column(String(20))
    # Where its view stands, as a document's does (VIEW_STATES, null for none asked for yet),
    # each of its pages' sizes once ready, and why it failed in the sentence its member reads.
    view_state: Mapped[str | None] = mapped_column(String(20))
    view_page_sizes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    view_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def files_prefix(self) -> str:
        """The prefix of this version's files: its original, its view and the pages drawn from
        it."""
        return self.storage_key.rpartition("/")[0] + "/"

    @property
    def view_key(self) -> str:
        return f"{self.files_prefix}view.pdf"

    def page_image_key(self, page: int) -> str:
        return f"{self.files_prefix}pages/{page}.png"


class Chat(Base):
    """A conversation with the agent about one project's documents."""

    __tablename__ = "chats"
    __table_args__ = (
        _one_of("visibility", CHAT_VISIBILITIES, "chats"),
        _one_of("scope_kind", CHAT_SCOPES, "chats"),
        *_trash_indexes("chats", "project_id", by=False),
        # A project's chats, and a page of them, the newest first, as the versioned API lists
        # them (app/api/v1/chats.py).
        Index("ix_chats_project_id_id", "project_id", "id"),
        # What a folder deleted for good looks up to forget: the few chats about it.
        Index(
            "ix_chats_scope_folder_id",
            "scope_folder_id",
            postgresql_where=text("scope_folder_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Who started the chat. A private chat is theirs alone, so it goes when their account does.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The starter's name as it is now, read with the chat, which a shared chat shows the
    # project's other members.
    started_by: Mapped[str] = column_property(
        select(User.name).where(User.id == user_id).correlate_except(User).scalar_subquery()
    )
    # private | project
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    # Taken from the first question, until the user renames the chat.
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When the last question was asked: the chat list shows the most recent conversation first.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When its starter deleted it into the trash (app/trash.py), which only they see it in.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # project | folder | documents: what the chat's answers read (app/scopes.py). The kind is
    # kept apart from the folder and the documents, so that a chat whose folder or documents
    # were deleted for good reads nothing rather than the whole project.
    scope_kind: Mapped[str] = mapped_column(String(20), default="project", server_default="project")
    # The folder a chat about one reads, with its subfolders; null once it is deleted for good.
    scope_folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL")
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.id",
    )
    scope_folder: Mapped[Folder | None] = relationship()
    # The documents a chat about chosen ones reads, of those not deleted for good.
    scope_documents: Mapped[list[Document]] = relationship(
        secondary="chat_scope_documents", order_by=Document.id
    )

    @property
    def scope(self) -> dict:
        """What the chat is about as the members' pages show it: its folder or its documents,
        those of them out of the trash. A chat about the project reads neither."""
        folder = self.scope_folder if self.scope_kind == "folder" else None
        documents = self.scope_documents if self.scope_kind == "documents" else []
        return {
            "kind": self.scope_kind,
            "folder": (
                {"id": folder.id, "name": folder.name}
                if folder is not None and folder.deleted_at is None
                else None
            ),
            "documents": [
                {"id": document.id, "filename": document.filename}
                for document in documents
                if document.deleted_at is None
            ],
        }


class ChatScopeDocument(Base):
    """A document a chat is about (app/scopes.py); the row goes with either."""

    __tablename__ = "chat_scope_documents"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed for a document deleted for good, whose rows go with it.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class ChatPin(Base):
    """A chat a member pinned to the top of their chat list: their own, or one shared with the
    project, which each of its readers pins for themselves. The row goes with either."""

    __tablename__ = "chat_pins"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed for an account deleted, whose pins go with it.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # The pinned chats are listed newest pin first.
    pinned_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatMessage(Base):
    """One turn of a chat: a question, or the agent's answer with the steps that led to it.

    An answer is created empty with its question and written by the worker (app/answers.py):
    while `status` is `streaming` its events in `chat_events` are what a client follows, and
    the row holds the finished answer once the status is anything else.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        _one_of("status", MESSAGE_STATUSES, "chat_messages"),
        # What `ask` counts against MAX_ANSWERS and checks for a chat that is still being
        # answered: the handful of rows in flight, out of every answer ever given.
        Index(
            "ix_chat_messages_streaming",
            "chat_id",
            postgresql_where=text("status = 'streaming'"),
        ),
        # What deleting a draft looks up to forget it: the few answers it was written from.
        Index(
            "ix_chat_messages_draft",
            "draft_id",
            postgresql_where=text("draft_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    # user | assistant
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    # streaming | done | stopped | failed
    status: Mapped[str] = mapped_column(String(20), default="done", server_default="done")
    # The tool calls an answer made: [{id, name, input, summary, is_error}]
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    # The passages the agent was shown while answering, which the answer may cite.
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    # How an answer ended when it did not end normally: a note (cut off, stopped) or an error.
    notice: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    # What the answer used, by kind (`answer`: its model calls, tokens and their cost), as
    # app/usage.py keeps it in step with the usage events the run records when it ends, so
    # empty while the answer is being written and for a question. A retried answer sums
    # every run's.
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Which try at the answer the row holds: 1, and one more each time the member tries a
    # stopped or failed answer again. Its job carries the number, and a run of an earlier try,
    # still waiting on the model or queued, writes nothing into a later one (app/answers.py).
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # The draft the answer offers the member (`create_draft`, app/agent/tools.py): {kind,
    # instructions, documents: [{id, title}]}, and `written` once the member had it written
    # (app/api/drafts.py), which an offer is once; None for an answer that offers none.
    draft_offer: Mapped[dict | None] = mapped_column(JSONB)
    # The draft written from the offer, until it is deleted.
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("drafts.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chat: Mapped[Chat] = relationship(back_populates="messages")

    @property
    def draft(self) -> dict | None:
        """The offer as the members' pages show it: with whether it was written, and the draft
        written from it while that is kept."""
        if self.draft_offer is None:
            return None
        return {
            **self.draft_offer,
            "written": bool(self.draft_offer.get("written")),
            "draft_id": self.draft_id,
        }


class ChatEvent(Base):
    """One event of an answer being written, in order: what the worker appends as the agent
    runs (app/answers.py) and the API relays to whoever is following the answer, so that a
    reload, another tab or a new API process picks the stream up where it was. The events of
    a finished answer are redundant with its row and pruned after a day (app/maintenance.py).
    """

    __tablename__ = "chat_events"

    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The event as the agent loop yields it (app/agent/loop.py), plus `restart` when a run
    # that was lost starts the answer over.
    event: Mapped[dict] = mapped_column(JSONB)


class ShareLink(Base):
    """A link that shows one document, or one answer with the passages it cites, to whoever
    holds it, without an account, until it expires or is revoked (app/share_links.py), which
    deletes it.

    It works as its maker's: only while they are a member of the organisation who may change the
    project and still sees the document or the chat, so it goes with their membership by the
    database's cascade, as an API key does, and stops while they are a viewer or the project is
    private without them; and only while the organisation's owners and admins allow them, and what
    it shows is out of the trash.
    """

    __tablename__ = "share_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
        # What the housekeeper prunes by.
        Index("ix_share_links_expires_at", "expires_at"),
        # A document or an answer, never both.
        CheckConstraint(
            "(document_id IS NULL) <> (message_id IS NULL)", name="ck_share_links_shows"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # An answer of a chat, which the link shows with its question and the passages it cites.
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    # Its maker, as whom it is checked at each opening.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The link carries a random token in its fragment; only its SHA-256 is stored, as for
    # sessions and API keys, so a copy of the database opens nothing.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The password its maker chose for it, hashed as an account's is; null for none.
    password_hash: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # How often it was opened, and when last, for its maker to see.
    views: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_viewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ShareView(Base):
    """One opening of a share link in a browser: the cookie it keeps for the link's pages and
    file holds a random token, of which only the SHA-256 is stored, as a session's is. It lasts
    VIEW_FOR (app/share_links.py), or until the link ends, and the link's checks are made again
    at every request it makes."""

    __tablename__ = "share_views"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    link_id: Mapped[int] = mapped_column(
        ForeignKey("share_links.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)


class Job(Base):
    """A unit of background work, claimed by a worker with `FOR UPDATE SKIP LOCKED` (app/jobs.py).

    The row is the claim and the retry bookkeeping; what the job is about keeps its own
    user-visible state (a document's `status` and `stages`). A running job holds a lease that
    its worker extends while it works, so a job whose worker died is claimed again once the
    lease runs out, and a job that was lost that way `max_attempts` times is given up. A job
    the worker gave up on ends `failed` and is kept for the operator (app/deadletters.py).
    """

    __tablename__ = "jobs"
    __table_args__ = (
        _one_of("kind", JOB_KINDS, "jobs"),
        _one_of("status", JOB_STATUSES, "jobs"),
        # What a claim scans: the queued jobs in the order they run.
        Index("ix_jobs_queue", "status", "priority", "run_at"),
        # What a claim counts: the jobs that finished lately, for each organisation's share of
        # the workers (app/jobs.py), out of a month of finished ones.
        Index("ix_jobs_finished", "finished_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Null for work that is no organisation's: the mail to an address signing up, or about an
    # account rather than what it does in an organisation (app/postbox.py).
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # Null for work that belongs to the organisation rather than to one project, such as mail.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    # What the job is about, by the kind's convention: {"document_id": 7} for index_document,
    # {"document_id": 7, "pages": [1, 2, 3]} for render_pages, with the trace it was queued
    # under (`trace_id`, app/tracing.py).
    payload: Mapped[dict] = mapped_column(JSONB)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    # Lower runs first: an upload a user is waiting for goes before a reprocess of a project.
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # How many times the job was claimed, and how many claims it gets before it is given up.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    # The job is not claimed before this; a retry with backoff moves it into the future.
    run_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # While running: when the claim expires unless the worker extends it.
    lease_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The process that holds or last held the job, as host:pid.
    worker_id: Mapped[str | None] = mapped_column(String(100))
    # Why the last attempt failed, for the operator; the user sees the document's own error.
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # When a failed job was reported to the operator (app/deadletters.py): null until then,
    # and for jobs that did not fail.
    reported_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerSeen(Base):
    """A worker process and when it last said it was running (app/jobs.py): its heartbeat
    writes the row at its start and every round, and its stop marks it stopped. Nothing else
    knows a worker is there, since it serves nothing to ask, so the members' status line and the
    operator read here whether anyone is taking the queue's work. The housekeeper removes a row
    a day after its worker was last seen."""

    __tablename__ = "workers"

    # As the jobs it claims name it (`jobs.worker_id`): host:pid.
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    # The release it runs (app/version.py), so that a worker a release left behind shows.
    version: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    # The database's clock, as the readers compare it, so that no host's clock matters.
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Set by a stop the worker made itself; null while it runs, or once it died without one.
    stopped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    """One measure of one thing an organisation used, for its quotas and its bill (app/usage.py).

    The row names the activity (`kind`), what was measured (`unit`) and how much, who did it,
    the model when one was called, and what it was for (`ref_type` and `ref_id`: a chat
    message, a document), so that a figure on a bill can be traced to its cause. The project
    and the user go null when their row goes and the usage stays, since the organisation is
    what is billed; the organisation's rows go with it. `cost_micros` is in millionths of the
    billing currency and empty until there is a price table to fill it from.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        _one_of("kind", USAGE_KINDS, "usage_events"),
        _one_of("unit", USAGE_UNITS, "usage_events"),
        # A reference is a type and an id together or neither.
        CheckConstraint("(ref_type IS NULL) = (ref_id IS NULL)", name="ck_usage_events_ref"),
        # What a rollup and a quota read: one organisation's rows over a span of time.
        Index("ix_usage_events_org_occurred", "org_id", "occurred_at"),
        # What the platform's daily spending limit reads (app/spending.py): every
        # organisation's rows since midnight.
        Index("ix_usage_events_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(BigInteger)
    # The `provider:model` name the call was configured with, as the settings name it.
    model: Mapped[str | None] = mapped_column(String(200))
    # The version of the prompts the calls were asked with (app/prompts.py); null for a kind
    # that asks no model, and for the rows from before versions were kept.
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SpendAlert(Base):
    """A warning ALERT_EMAIL was sent about one day's spending nearing DAILY_SPEND_CAP
    (app/spending.py). One is sent a day, and the row is what says it was."""

    __tablename__ = "spend_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The UTC day the warning was about.
    day: Mapped[dt.date] = mapped_column(Date)
    # What had been spent when it was sent, and the cap it was measured against, in
    # millionths of PRICE_CURRENCY.
    spent_micros: Mapped[int] = mapped_column(BigInteger)
    cap_micros: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """A notice in its member's notification centre (app/notices.py), made beside its mail and
    in the same transaction, so that the two never disagree: what it says in the app's words,
    the page it leads to, and whether they read it. The organisation it is about may be another
    than the one they work in; a member who leaves or is removed from it loses its notices with
    the membership (`_part` in app/api/organizations.py), and the housekeeper removes those
    older than NOTIFICATION_DAYS."""

    __tablename__ = "notifications"
    __table_args__ = (
        _one_of("kind", NOTICE_KINDS, "notifications"),
        # A member's notices, newest first, a page at a time by id.
        Index("ix_notifications_user_id_id", "user_id", "id"),
        # What the bell counts.
        Index("ix_notifications_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    # The notice's subject, the app's own sentence, and what it says, which may name files, a
    # project and the organisation, and give the library's reasons: never logged.
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # The page of the app it leads to, a path in the organisation it is about.
    link: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WatchAlert(Base):
    """A concern the worker's watch (app/watch.py) raised and has not seen end: the operator was
    written to when it began and is written to again when it ends. The key says what it is
    about (`backlog:index_document`, `fallback:AGENT_FALLBACK_MODEL`); the title and message
    are what the operator was sent."""

    __tablename__ = "watch_alerts"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    raised_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MailSuppression(Base):
    """An address the mail provider said not to write to (app/bounces.py): mail to it bounced for
    good, or its reader marked a message as spam. It is sent no notices, the mail one may do
    without (app/postbox.py), for SUPPRESSED_FOR, or until its user turns notices on again. The
    address is kept as its SHA-256, as the rate limits key one (`by_email`), so that the table
    names nobody to whoever reads it, while a message to the address still finds its row."""

    __tablename__ = "mail_suppressions"
    __table_args__ = (_one_of("reason", MAIL_SUPPRESSIONS, "mail_suppressions"),)

    address_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(20))
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class RateLimit(Base):
    """How many requests of one kind one caller made in one minute (app/ratelimit.py): the
    kind is the scope, the caller the key (a user, an address or an email address), and the
    minute its start. A request raises its row's count by one; the housekeeper removes the
    rows of minutes long past.
    """

    __tablename__ = "rate_limits"

    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    window_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class AuditEvent(Base):
    """One thing done to an organisation's documents, chats or members' accounts, kept for its
    admins (app/audit.py).

    The row is written in the transaction of what it records, so the two commit or roll back
    together. It names the action, the member who acted (or, for a sign-in, the account signed
    into; null for what the operator or the app did, and once the member's account is gone),
    the project and the object it was done to, the client's address, and a few words of
    detail: a file's name, a role before and after. It never holds what a document, a question
    or an answer says. The project and the object are plain ids rather than foreign keys, since
    the row outlives them: "deleted the project" names the project that is gone. The
    organisation's rows go with it.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        _one_of("action", AUDIT_ACTIONS, "audit_events"),
        # An object is a type and an id together or neither.
        CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)", name="ck_audit_events_target"
        ),
        # What the audit route reads (app/api/audit.py): one organisation's rows, the newest
        # first, page by page, of one action, project or member, or a span of time. Each filter
        # has an index that ends in the order of the rows, so that a rare action in a large
        # organisation is a range of an index rather than a walk through all of its rows.
        Index("ix_audit_events_org_time", "org_id", "occurred_at", "id"),
        Index("ix_audit_events_org_action", "org_id", "action", "occurred_at", "id"),
        Index(
            "ix_audit_events_org_project",
            "org_id",
            "project_id",
            "occurred_at",
            "id",
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        # A member's rows, which the account purge also clears the addresses of.
        Index("ix_audit_events_user", "user_id", "occurred_at", "id"),
        # What the housekeeper prunes by: every organisation's rows older than it keeps them.
        Index("ix_audit_events_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    project_id: Mapped[int | None] = mapped_column(Integer)
    # project | folder | document | chat | session | user
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    # {"filename": "invoice.pdf"}, {"from": "member", "to": "owner"}: names and settings as
    # they were, never content.
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The address the request came from, as uvicorn believes it; cleared when the account it
    # was recorded for is deleted (app/retention.py).
    ip: Mapped[str | None] = mapped_column(String(45))
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
