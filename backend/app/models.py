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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REGCONFIG, TSVECTOR
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.config import PACK_UNITS, REGIONS, get_settings
from app.db import Base

_settings = get_settings()

# The pipeline's stages in order (app/indexing/pipeline.py): the malware scan comes first,
# since nothing may open the file before it has passed (app/scanning.py).
STAGES = ("scan", "parse", "structure", "enrich", "embed")
# From most to least permitted: each role may do everything the roles after it may.
ROLES = ("owner", "admin", "member", "viewer")
# The roles an organisation may let everyone at its domains join with (app/invitations.py): none
# that may manage the organisation itself.
JOIN_ROLES = ("member", "viewer")
# A chat is private to the member who started it until they share it with the project.
CHAT_VISIBILITIES = ("private", "project")
# A project is seen by everyone in its organisation, or, when private, by the members chosen
# for it and the organisation's owners and admins (app/access.py).
PROJECT_VISIBILITIES = ("organization", "private")
# What a background job can be (app/jobs.py). A worker only claims the kinds it has a handler
# for, so a kind can be queued by a newer API before an older worker knows it.
JOB_KINDS = (
    "index_document",
    "answer_question",
    "render_pages",
    "send_email",
    "purge_trash",
    "sync_connector",
    "run_workflow",
)
JOB_STATUSES = ("queued", "running", "done", "failed")
# How far an answer is: being written by the worker (app/answers.py), finished, stopped by the
# user, or failed. A question is always `done`.
MESSAGE_STATUSES = ("streaming", "done", "stopped", "failed")
# What an organisation is metered for (app/usage.py): the activity a row counts, and what the
# count is in. The stored `usage` of a document or a message (`Document.usage`,
# `ChatMessage.usage`) is keyed by the same kinds.
USAGE_KINDS = ("answer", "upload", "ocr", "infer_sections", "enrich", "embed", "render")
USAGE_UNITS = ("calls", "input_tokens", "output_tokens", "pages", "bytes", "inputs", "questions")
# What an organisation is entitled to (app/entitlements.py), from the plan with the least to
# the one with no limits. A new organisation starts on the first.
PLANS = ("free", "pro", "team", "enterprise")
# The order the library lists a folder's files in, which the database keeps an index in: by
# name, with the numbers in a name compared as numbers ("invoice 2" before "invoice 10") and
# case and accents set aside, as the page sorts the folders. An ICU collation, made by the
# migration that put it on `documents.filename` (deterministic: names equal but for case
# still have an order, so a page boundary between them is exact).
NATURAL_ORDER = "natural_order"
# What the audit log records (app/audit.py): its members' sign-ins and what they did to their
# accounts, what was done to the organisation itself (its name, its owner, who was invited,
# joined, left or was removed, and the members' roles), to its projects, its documents and the
# sharing of its chats, and what the operator and the app did to it on their own.
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
    "role_changed",
    "plan_changed",
    "seats_changed",
    "entitlements_changed",
    "checkout_started",
    "subscription_changed",
    "payment_failed",
    "pack_bought",
    "organization_created",
    "organization_renamed",
    "ownership_transferred",
    "invitation_sent",
    "invitation_revoked",
    "invitation_accepted",
    "joining_changed",
    "member_joined",
    "member_left",
    "member_removed",
    "project_access_changed",
    "project_deleted",
    "folder_deleted",
    "document_uploaded",
    "document_downloaded",
    "document_deleted",
    "document_quarantined",
    "document_requeued",
    "chat_shared",
    "chat_unshared",
    "chat_deleted",
    "database_restored",
)


def _one_of(column: str, values: tuple[str, ...], table: str) -> CheckConstraint:
    # A misspelt role or visibility would otherwise grant nothing silently, or show a chat to
    # nobody, so the database refuses it.
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{column}")


def _role_check(table: str) -> CheckConstraint:
    return _one_of("role", ROLES, table)


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
    """The tenant. Projects belong to an organisation, and so do its plan, usage and billing."""

    __tablename__ = "organizations"
    __table_args__ = (
        _one_of("plan", PLANS, "organizations"),
        _one_of("region", tuple(REGIONS), "organizations"),
        _one_of("join_role", JOIN_ROLES, "organizations"),
        CheckConstraint("seats > 0", name="ck_organizations_seats"),
        CheckConstraint("jsonb_typeof(overrides) = 'object'", name="ck_organizations_overrides"),
        # Which organisations a confirmed address's domain may join is asked at every `/me`.
        Index("ix_organizations_join_domains", "join_domains", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Appears in URLs and API responses, so it is unique across all tenants.
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    # Which plan's entitlements the organisation is held to (app/entitlements.py). Set by the
    # operator's `plan` command until billing sets it; the local account has no limits.
    plan: Mapped[str] = mapped_column(String(20), default="free", server_default="free")
    # The members it bought on a plan sold by the seat (`BY_THE_SEAT`, app/entitlements.py),
    # in place of the plan's own number; null on any other plan, or before any were bought.
    seats: Mapped[int | None] = mapped_column(Integer)
    # The entitlements agreed with the organisation in place of its plan's, by the name of an
    # `Entitlements` field, a null value being no limit: an Enterprise organisation's terms.
    # Set with the plan (app/billing.py), and cleared when the plan changes.
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The region its data is kept in (DATA_REGION, app/residency.py), recorded as it is created
    # so that a deployment in another region refuses to serve it; null while the deployment
    # names none. One deployment is one region; several are 4.3's.
    region: Mapped[str | None] = mapped_column(String(20))
    # The domains whose confirmed addresses the app offers to join the organisation, with the
    # role they join with (app/invitations.py); none by default.
    join_domains: Mapped[list[str]] = mapped_column(
        ARRAY(String(253)), default=list, server_default="{}"
    )
    join_role: Mapped[str] = mapped_column(String(20), default="member", server_default="member")
    # Its customer at Stripe (app/billing.py), made at its first checkout and kept, so that its
    # invoices, payment details and tax ids stay together; null until then.
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Its last subscription, and what Stripe last said of it: its status (trialing, active,
    # past_due, canceled, unpaid, …), when its trial ends, when it renews and when it ends, and
    # since when a payment has failed, which the grace period counts from. Kept once it ends,
    # since an organisation that had one starts its next without a trial.
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    subscription_status: Mapped[str | None] = mapped_column(String(30))
    trial_ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    renews_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    payment_failed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
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

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


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


class UserSession(Base):
    """A signed-in browser or device. The cookie holds a random token; only its hash is stored."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The organisation the session works in, which the user chose by switching to it
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


class ApiKey(Base):
    """A key for programmatic access to an organisation's projects.

    Only the hash is stored; the prefix is shown in the UI so one key can be told from another.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Scope names the key is limited to; empty means the whole API its organisation may use.
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Project(Base):
    """A set of documents and the chats about them. Everything below it is scoped by project."""

    __tablename__ = "projects"
    __table_args__ = (_one_of("visibility", PROJECT_VISIBILITIES, "projects"),)

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
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped[Organization] = relationship(back_populates="projects")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


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


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256"),
        # A page of a folder's files in the library's order (`list_documents` in
        # app/api/documents.py), the root's being those whose folder is null; the leading
        # column serves every lookup by project as well.
        Index("ix_documents_listing", "project_id", "folder_id", "filename", "id"),
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
    # The key of the uploaded file in the storage (app/storage.py); the document's other
    # objects sit under the same prefix.
    storage_key: Mapped[str] = mapped_column(Text)

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
    title: Mapped[str | None] = mapped_column(String(500))
    doc_type: Mapped[str | None] = mapped_column(String(100), index=True)
    doc_date: Mapped[dt.date | None] = mapped_column(Date)
    summary: Mapped[str | None] = mapped_column(Text)

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

    project: Mapped[Project] = relationship(back_populates="documents")

    @property
    def display_title(self) -> str:
        return self.title or self.filename

    @property
    def storage_prefix(self) -> str:
        """The prefix in the storage that everything this document owns sits under."""
        return self.storage_key.rpartition("/")[0] + "/"

    def page_image_key(self, page: int) -> str:
        return f"{self.storage_prefix}pages/{page}.png"

    @property
    def thumbnail_key(self) -> str:
        return f"{self.storage_prefix}thumbnail.png"


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
    # configuration is not. Today every passage gets FTS_LANGUAGE; per-document detection
    # will write what it finds here.
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    # text | number | date | money
    value_type: Mapped[str] = mapped_column(String(20), default="text")
    value: Mapped[str] = mapped_column(Text)
    number: Mapped[float | None] = mapped_column(Float)
    date: Mapped[dt.date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))

    quote: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    block_ordinal: Mapped[int | None] = mapped_column(Integer)
    passage_id: Mapped[int | None] = mapped_column(ForeignKey("passages.id", ondelete="SET NULL"))
    # True when the quote was found in the source block.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Chat(Base):
    """A conversation with the agent about one project's documents."""

    __tablename__ = "chats"
    __table_args__ = (_one_of("visibility", CHAT_VISIBILITIES, "chats"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
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

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.id",
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
    # empty while the answer is being written and for a question.
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chat: Mapped[Chat] = relationship(back_populates="messages")


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
    org_id: Mapped[int] = mapped_column(
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
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SpendAlert(Base):
    """A warning the operator was sent about one day's spending (app/spending.py): an
    organisation's when `org_id` names one, the platform's as a whole when it is null. One is
    sent a day for each, and the row is what says it was; the organisation's rows go with it.
    """

    __tablename__ = "spend_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # The UTC day the warning was about.
    day: Mapped[dt.date] = mapped_column(Date)
    # What had been spent when it was sent, and the limit it was measured against, in
    # millionths of the billing currency.
    spent_micros: Mapped[int] = mapped_column(BigInteger)
    cap_micros: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class AllowancePack(Base):
    """Pages or questions an organisation bought for one calendar month on top of its plan's
    (app/billing.py; STRIPE_PACKS), by the Checkout session that paid for them, which is
    recorded once however often Stripe or the page says it was paid. The checks of the month
    add them (app/entitlements.py); a month's packs end with it, used or not."""

    __tablename__ = "allowance_packs"
    __table_args__ = (
        _one_of("unit", PACK_UNITS, "allowance_packs"),
        CheckConstraint("quantity > 0", name="ck_allowance_packs_quantity"),
        # A check sums an organisation's packs of one month.
        Index("ix_allowance_packs_org_id_month", "org_id", "month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    unit: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    # The start of the month, in UTC, it counts in: the month it was paid in.
    month: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    checkout_id: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BillingEvent(Base):
    """A Stripe event the app has acted on, by Stripe's id (app/billing.py): Stripe delivers an
    event again when an answer is late, so one acted on twice would mail an owner twice. Kept a
    month, longer than Stripe goes on delivering one, then pruned by the housekeeper."""

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


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
        # What the audit route reads: one organisation's rows, the newest first, page by page.
        Index("ix_audit_events_org_newest", "org_id", "id"),
        # What the housekeeper prunes by: every organisation's rows older than its plan keeps.
        Index("ix_audit_events_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
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
