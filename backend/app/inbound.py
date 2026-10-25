"""Mail in: a project's own address, whose mail becomes its documents.

A project's members who may change it give it an address, `<token>@INBOUND_DOMAIN`
(`ProjectMailbox`), and say whom it takes mail from: they themselves always, from their own
confirmed addresses, and the senders they list, each an address (ap@supplier.com) or a domain
(supplier.com, its subdomains with it), never a domain where anyone may have an address
(`SHARED_DOMAINS`). A message from anyone else is turned away, and so is one whose From
address the provider could not prove: the From header is written by whoever sends, so a
message counts as its sender's only when the provider says the sender's domain signed or sent
it for that address (DMARC, or DKIM aligned with the From domain), which `Received.authenticated`
carries from each provider's own verdicts. Nothing goes back to a sender turned away: a reply
to a forged address reaches someone who sent nothing.

A message taken in is an upload (app/notices.py) of its member, or of nobody for a sender
outside the organisation: its text as a `.txt` document named by its subject, its From, To,
Date and Subject above, as an email in the sample corpus reads, and each attachment as an
upload's file (app/uploads.py), into the address's folder. Images an HTML body shows by their
Content-ID are the body's (a signature's logo) and are left out. The project's automations see
it arrive as they see any upload. What became of each message, its sender and subject with the
documents kept and the files rejected, or why it was turned away, is kept for INBOUND_DAYS
(`InboundMessage`), for the members to read beside the address; once per delivery, so that a
provider that delivers a message again keeps it once.

The providers' ways in hand `receive` the message whole, as it was sent (RFC 5322), with the
recipients it was delivered to and their verdict on its sender. A message's text is a
document's text: none of it goes into a log line, an error or the audit log, which names the
documents by their file names as an upload does.
"""

import base64
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO

from sqlalchemy import Connection, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account
from app.audit import audit_event
from app.config import get_settings
from app.entitlements import check_upload, read_only
from app.flags import EMAIL_IN, flag_on
from app.invitations import DOMAIN, SHARED_DOMAINS
from app.jobs import Lease, LeaseLost, renew
from app.messages import MAX_MESSAGE_BYTES, Parsed, body_document, parse
from app.metrics import INBOUND_MESSAGES
from app.models import (
    Document,
    Folder,
    InboundMessage,
    Membership,
    Organization,
    Project,
    ProjectMailbox,
    User,
)
from app.notices import record_upload, settle_upload
from app.retention import held_for_deletion
from app.storage import Storage
from app.uploads import Incoming, take_files

log = logging.getLogger(__name__)

# The senders one address may list.
MAX_SENDERS = 50
# How long what became of a message is kept for the members to read; the privacy policy says so.
INBOUND_DAYS = 30
# The messages the address's page lists, newest first.
SHOWN = 20
# A token is ten random bytes in lower-case base32: sixteen letters and digits, which no one
# guesses and a mail program keeps as typed.
TOKEN_BYTES = 10

NO_DOMAIN = (
    "This deployment does not receive mail yet, so a project cannot have an address. Ask "
    "whoever runs it to set INBOUND_DOMAIN."
)
NO_DOMAIN_MEMBERS = (
    "Email in is not set up on this deployment yet, so a project cannot have an address. Ask "
    "Emudoc's support."
)
SAMPLE_MAILBOX = "The sample project takes no documents, so it has no address. Make a project."
TOO_MANY_SENDERS = f"An address can take mail from {MAX_SENDERS} senders at most."
NO_MAILBOX = "This project has no address. Give it one first."
# Why a message was turned away whole, as its project's members read it.
UNREADABLE_SENDER = "Its sender could not be read, so it was turned away."
UNAUTHENTICATED = (
    "Its sender's domain did not prove that it sent the message (by DMARC, or a DKIM signature "
    "of that domain), so anyone could have written it under that address and it was turned "
    "away. Ask the sender's mail administrator to set up DKIM for their domain."
)
PROJECT_TRASHED = "The project was in the trash, so it took no documents."
TOO_LARGE = (
    f"It was larger than one message may be ({MAX_MESSAGE_BYTES // (1024 * 1024)} MB), so it "
    "was turned away. Send its files in several messages."
)
NOTHING_IN_IT = "It had no text and no attachments to keep."
TOO_MANY_FILES = "One message can bring {limit} files at most; send the rest in another message."


def not_listed(sender: str) -> str:
    return (
        f"{sender} is not among the senders this address takes mail from, so it was turned "
        "away. Add the address or its domain to the list to take its mail."
    )


@dataclass(frozen=True)
class Received:
    """A message as a provider delivered it: its bytes as they were sent (RFC 5322), whom it was
    delivered to, the provider's name for it ("resend:<id>"), and whether the provider proved
    that the From address's domain sent it."""

    delivery: str
    raw: bytes
    recipients: list[str]
    authenticated: bool


def new_token() -> str:
    return base64.b32encode(secrets.token_bytes(TOKEN_BYTES)).decode().lower().rstrip("=")


def address_of(mailbox: ProjectMailbox, domain: str) -> str:
    return f"{mailbox.token}@{domain}"


def token_in(address: str, domain: str) -> str | None:
    """The token of an address at `domain`, its tag (`+…`) and case set aside; None for an
    address elsewhere."""
    local, at, host = address.strip().strip("<>").lower().rpartition("@")
    if not at or not domain or host != domain:
        return None
    return local.split("+", 1)[0] or None


def sender_entry(entry: str) -> tuple[str, str | None]:
    """The entry of the senders' list as it is kept, lower-cased, an address or a bare domain,
    and why it cannot be one (None when it can)."""
    value = entry.strip().lower()
    if "@" in value.lstrip("@"):
        local, _, domain = value.rpartition("@")
        if not local or any(c.isspace() for c in local) or not DOMAIN.fullmatch(domain):
            return value, f"{entry.strip()} is not an email address. Type one such as ap@acme.com."
        return value, None
    domain = value.removeprefix("@")
    if not DOMAIN.fullmatch(domain):
        return domain, (
            f"{entry.strip()} is neither an address nor a domain. Type one such as ap@acme.com "
            "or acme.com."
        )
    if domain in SHARED_DOMAINS:
        return domain, (
            f"Anyone can have an address at {domain}, so its domain would let anyone send to the "
            "project. List the sender's own address instead."
        )
    return domain, None


def listed(senders: list[str], address: str) -> bool:
    """Whether an address is on the list: as it is, or at a domain listed or below one."""
    address = address.lower()
    domain = address.rpartition("@")[2]
    for entry in senders:
        if "@" in entry:
            if entry == address:
                return True
        elif domain == entry or domain.endswith(f".{entry}"):
            return True
    return False


def receive(
    session: Session, storage: Storage, received: Received, app_url: str, lease: Lease | None = None
) -> list[InboundMessage]:
    """Take the message into each project it was delivered to by its address, or turn it away
    there, keeping what became of it; returns those records. An address no project has takes
    nothing, and is only counted."""
    domain = get_settings().inbound_domain
    tokens = {token for address in received.recipients if (token := token_in(address, domain))}
    mailboxes = (
        session.scalars(
            select(ProjectMailbox)
            .where(ProjectMailbox.token.in_(tokens))
            .order_by(ProjectMailbox.id)
        ).all()
        if tokens
        else []
    )
    if len(mailboxes) < len(tokens):
        INBOUND_MESSAGES.labels("unknown_address").inc(len(tokens) - len(mailboxes))
    if not mailboxes:
        log.info("A message (%s) was for no project's address", received.delivery)
        return []
    if len(received.raw) > MAX_MESSAGE_BYTES:
        parsed = Parsed(None, "", "", [])
    else:
        parsed = parse(received.raw)
    kept = []
    for mailbox in mailboxes:
        if record := _deliver(session, storage, mailbox, received, parsed, app_url, lease):
            kept.append(record)
    return kept


def _deliver(
    session: Session,
    storage: Storage,
    mailbox: ProjectMailbox,
    received: Received,
    parsed: Parsed,
    app_url: str,
    lease: Lease | None,
) -> InboundMessage | None:
    """Take the message into the mailbox's project, or turn it away, unless this delivery was
    taken already; the record of it, or None."""
    project = session.get(Project, mailbox.project_id)
    if project is None:
        return None
    organization = project.organization
    if not flag_on(organization, EMAIL_IN):
        INBOUND_MESSAGES.labels("off").inc()
        log.info(
            "A message (%s) for project %s came while email in is off",
            received.delivery,
            project.id,
        )
        return None
    if session.scalar(
        select(
            exists().where(
                InboundMessage.project_id == project.id,
                InboundMessage.delivery == received.delivery,
            )
        )
    ):
        log.info("A message (%s) for project %s was taken already", received.delivery, project.id)
        return None
    record = InboundMessage(
        org_id=project.org_id,
        project_id=project.id,
        delivery=received.delivery[:200],
        sender=(parsed.sender or "")[:320],
        subject=parsed.subject[:300],
        outcome="refused",
    )
    user = _member(session, organization, project, parsed.sender) if parsed.sender else None
    record.user_id = user.id if user is not None else None
    if reason := _refusal(session, mailbox, project, organization, received, parsed, user):
        record.reason, label = reason
        return _keep(session, record, label, lease)

    folder = session.get(Folder, mailbox.folder_id) if mailbox.folder_id else None
    target = folder.id if folder is not None and folder.deleted_at is None else None
    limit = get_settings().max_upload_files
    attached = [name for name, _ in parsed.attachments]
    files = [
        *([body] if (body := body_document(parsed, attached)) else []),
        *parsed.attachments,
    ]
    taken = files[:limit]
    rejected = [
        {"filename": name, "reason": TOO_MANY_FILES.format(limit=limit)}
        for name, _ in files[limit:]
    ]
    user_id = record.user_id

    def audited(document: Document, size: int) -> None:
        audit_event(
            session,
            "document_uploaded",
            org_id=project.org_id,
            user_id=user_id,
            project_id=project.id,
            target=("document", document.id),
            detail={"filename": document.filename, "size_bytes": size, "by_email": True},
        )

    documents, refused = take_files(
        session,
        storage,
        project,
        organization,
        target,
        (Incoming(name, [], BytesIO(content)) for name, content in taken),
        user_id=user_id,
        audited=audited,
        lease=lease,
    )
    record.rejected = [{"filename": r.filename, "reason": r.reason} for r in refused] + rejected
    record.kept = len(documents)
    if documents:
        record.outcome = "kept"
        record.upload_id = record_upload(
            session, project.id, user_id, [document.id for document in documents]
        )
    else:
        record.outcome = "empty"
        if not files:
            record.reason = NOTHING_IN_IT
    kept = _keep(session, record, record.outcome, lease)
    if kept is not None and record.upload_id is not None:
        settle_upload(session, record.upload_id, app_url)
    return kept


def _refusal(
    session: Session,
    mailbox: ProjectMailbox,
    project: Project,
    organization: Organization,
    received: Received,
    parsed: Parsed,
    user: User | None,
) -> tuple[str, str] | None:
    """Why the message is turned away whole, with its metric's label; None when it is taken."""
    if len(received.raw) > MAX_MESSAGE_BYTES:
        return TOO_LARGE, "too_large"
    if project.deleted_at is not None:
        return PROJECT_TRASHED, "refused_project"
    if parsed.sender is None:
        return UNREADABLE_SENDER, "unreadable"
    if not received.authenticated:
        return UNAUTHENTICATED, "unauthenticated"
    if user is None and not listed(mailbox.senders, parsed.sender):
        return not_listed(parsed.sender), "refused_sender"
    if held := held_for_deletion(organization):
        return held, "refused_plan"
    if refusal := read_only(session, organization) or check_upload(session, organization):
        return refusal.sentence, "refused_plan"
    return None


def _member(
    session: Session, organization: Organization, project: Project, address: str
) -> User | None:
    """The member whose confirmed address it is, if they may change the project and see it."""
    found = session.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == organization.id,
            User.email == address,
            User.email_verified_at.is_not(None),
        )
    ).first()
    if found is None:
        return None
    account = Account(organization, found[0], found[1])
    if not account.is_at_least("member"):
        return None
    seen = session.scalar(select(exists().where(Project.id == project.id, visible_to(account))))
    return found[0] if seen else None


def _keep(
    session: Session, record: InboundMessage, label: str, lease: Lease | None
) -> InboundMessage | None:
    """Commit the record, unless the same delivery was kept meanwhile by another run of it."""
    values = {
        column.key: getattr(record, column.key)
        for column in InboundMessage.__table__.columns
        if column.key not in ("id", "received_at") and getattr(record, column.key) is not None
    }
    kept_id = session.scalar(
        insert(InboundMessage)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["project_id", "delivery"])
        .returning(InboundMessage.id)
    )
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()
    INBOUND_MESSAGES.labels(label).inc()
    if kept_id is None:
        return None
    if label not in ("kept", "empty"):
        log.info(
            "A message (%s) for project %s was turned away: %s",
            record.delivery,
            record.project_id,
            label,
        )
    return session.get(InboundMessage, kept_id)


_PRUNE = text("DELETE FROM inbound_messages WHERE received_at < now() - :age")


def prune_inbound(connection: Connection) -> int:
    """Forget what became of the messages received longer than INBOUND_DAYS ago; returns how
    many."""
    return connection.execute(_PRUNE, {"age": timedelta(days=INBOUND_DAYS)}).rowcount
