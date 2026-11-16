"""Keeping a project in step with a connected folder (app/connectors/): the sync, a job of its own
(`sync_connector`), which the worker's heartbeat queues for each connector whose time has come
(`queue_due_syncs`, every SYNC_INTERVAL) and a member's "Sync now" queues at once.

A sync buys an access token with the member's grant, reads the folder as it is now, and asks the
service whether anything changed since the last listing's mark: when nothing did, that is all.
Otherwise it reads every file below the folder and compares them with what the last sync found
(`connector_files`). A file not seen before is downloaded and taken in through the checks an
upload's files go through (app/uploads.py), into the folders of its path below the connector's
target. A file whose version changed is its document's new version (app/versions.py), through
the same checks, so that the document keeps its chats' scope and its members' checks where it
stands in the library; one whose document is being indexed waits for the next sync. The
document of a file that is no longer below the folder goes to the project's trash, which keeps
it TRASH_DAYS, so that a file moved by mistake at the service costs no chat its sources. The
documents a sync keeps or gives new versions are one upload, its member's, so that its notice
and the project's automations follow as any upload's do.

A file is read once per version: one the project keeps, rejects or deleted is not read again
until its version changes, so a document a member deleted stays deleted while its file stays as
it was. A sync takes at most ROUND_FILES files and
queues the next at once while more wait, so that a large folder shares the workers.

Nothing goes to the trash unless the folder was read whole: a folder gone, a listing that
failed or one past MAX_FILES leaves every document as it is, and the connector says why.

What holds a sync back: the project in the trash, and the member who connected it no longer one who
may change the project, which pauses it, since the grant is theirs; so does a service that no longer
accepts the grant, until its member connects it again. A service that does not answer leaves the
rest to the next sync. Nothing of a file's name or the folder's reaches the log, which names the
connector.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import httpx
from sqlalchemy import Connection, exists, select, text
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account
from app.audit import audit_event
from app.config import get_settings
from app.connectors import LABELS, providers
from app.connectors.base import TIMEOUT, ConnectorError, Provider, RemoteFile
from app.db import MIGRATION_LOCK, SessionLocal
from app.encrypted import CONNECTOR_TOKENS, decrypt, encrypt
from app.errors import shown
from app.folders import folder_names
from app.jobs import BULK, SYNC_CONNECTOR, Lease, LeaseLost, enqueue, renew
from app.keyring import Undecryptable
from app.metrics import CONNECTOR_FILES, CONNECTOR_SYNCS
from app.models import (
    Connector,
    ConnectorFile,
    Document,
    Folder,
    Job,
    Membership,
    Organization,
    Project,
    User,
)
from app.notices import app_address, record_upload, settle_upload
from app.storage import Storage
from app.trash import trash_document
from app.uploads import LOST_RACE, Incoming, take_files
from app.versions import VersionRefused, take_version

log = logging.getLogger(__name__)

SYNC_INTERVAL = timedelta(minutes=15)
# How many files one sync downloads before it hands over to the next, queued at once.
ROUND_FILES = 100
# How many files a connected folder may hold below it, its subfolders' with them.
MAX_FILES = 5_000
# How long a connector whose folder was never chosen is kept, for a member who left the page.
CHOOSING_KEPT = timedelta(days=1)
# How many due connectors one sweep queues.
SWEPT = 200
# Held while the sweep queues, so that two workers' sweeps never queue one connector twice.
SYNCS_LOCK = MIGRATION_LOCK + 6

FOLDER_GONE = (
    "The folder is no longer in {label}: it was deleted, moved to its trash, or no longer "
    "shared with the account that connected it. Nothing was moved to the project's trash; "
    "disconnect it, or connect the folder where it is now."
)
TOO_MANY = (
    f"The folder holds more than {MAX_FILES:,} files below it, more than a connected folder "
    "may, so nothing was synced. Connect a folder within it instead."
)
PROJECT_TRASHED = "The project is in the trash, so nothing is synced into it until it is restored."
MEMBER_GONE = (
    "{name}, who connected the folder, no longer may change this project, so it is paused. "
    "A member who may can connect it again."
)
UNREADABLE_GRANT = (
    "Emudoc can no longer read the access {label} gave for this folder. Its member can connect "
    "it again."
)
STOPPED = "The last sync stopped unexpectedly. The next one tries again."

_DUE = text(
    "SELECT c.id, c.org_id, c.project_id FROM connectors c "
    "WHERE c.status = 'active' AND c.next_sync_at <= now() "
    "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = :kind "
    "AND j.status IN ('queued', 'running') AND j.payload ->> 'connector_id' = c.id::text) "
    "ORDER BY c.next_sync_at LIMIT :limit FOR UPDATE OF c SKIP LOCKED"
)
_PRUNE = text("DELETE FROM connectors WHERE status = 'choosing' AND created_at < now() - :age")


@lru_cache
def http_client() -> httpx.Client:
    """The worker's client for the services' APIs, kept for the process's connections."""
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False)


def label_of(connector: Connector) -> str:
    return LABELS.get(connector.provider, connector.provider)


def queue_sync(session: Session, connector: Connector, *, now: bool = False) -> None:
    """Queue a sync of the connector in the caller's session: at a member's asking or for the
    rest of a large folder (`now`), ahead of the syncs that are only due."""
    enqueue(
        session,
        SYNC_CONNECTOR,
        org_id=connector.org_id,
        project_id=connector.project_id,
        payload={"connector_id": connector.id, "now": now},
        priority=0 if now else BULK,
    )


def sync_pending(session: Session, connector_id: int) -> bool:
    """Whether a sync of the connector is queued or running."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    Job.kind == SYNC_CONNECTOR,
                    Job.status.in_(("queued", "running")),
                    Job.payload["connector_id"].astext == str(connector_id),
                )
            )
        )
    )


def queue_due_syncs() -> int:
    """Queue a sync of every connector whose time has come and none is queued or running for;
    returns how many. The worker's heartbeat runs it every round."""
    with SessionLocal() as session:
        taken = session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SYNCS_LOCK})
        if not taken:
            return 0
        due = session.execute(_DUE, {"kind": SYNC_CONNECTOR, "limit": SWEPT}).all()
        for row in due:
            enqueue(
                session,
                SYNC_CONNECTOR,
                org_id=row.org_id,
                project_id=row.project_id,
                payload={"connector_id": row.id, "now": False},
                priority=BULK,
            )
        session.commit()
    return len(due)


def prune_connectors(connection: Connection) -> int:
    """Forget the connectors whose folder was never chosen within CHOOSING_KEPT; returns how
    many."""
    return connection.execute(_PRUNE, {"age": CHOOSING_KEPT}).rowcount


def run_sync(
    job: Job, storage: Storage, lease: Lease, http: httpx.Client | None = None
) -> str | None:
    """The `sync_connector` job: one sync of the connector the job names."""
    connector_id = job.payload["connector_id"]
    with SessionLocal() as session:
        connector = session.get(Connector, connector_id)
        if connector is None or connector.status != "active":
            return None
        project = session.get(Project, connector.project_id)
        if project is None:
            return None
        organization = project.organization
        # Set before anything is asked of the service, so that a sync that fails is not queued
        # again at the next round of the heartbeat.
        connector.next_sync_at = datetime.now(UTC) + SYNC_INTERVAL
        provider = providers(get_settings()).get(connector.provider)
        if held := _held(session, connector, project, organization, provider):
            connector.problem = held
            _commit(session, lease)
            CONNECTOR_SYNCS.labels("held").inc()
            log.info("Connector %s was not synced: it is held back", connector.id)
            return None
        _commit(session, lease)
        assert provider is not None
        sync = Sync(session, storage, http or http_client(), lease, connector, project, provider)
        try:
            outcome = sync.run(forced=bool(job.payload.get("now")))
        except ConnectorError as error:
            outcome = sync.failed(error)
        CONNECTOR_SYNCS.labels(outcome).inc()
        return None


def abandon_sync(job: Job) -> None:
    """A sync lost to a dying worker every time: the connector says so, and the next is due at
    its interval as ever."""
    with SessionLocal() as session:
        connector = session.get(Connector, job.payload["connector_id"])
        if connector is not None:
            connector.problem = STOPPED
            session.commit()
    log.warning("A sync of connector %s was given up", job.payload["connector_id"])


def _held(
    session: Session,
    connector: Connector,
    project: Project,
    organization: Organization,
    provider: Provider | None,
) -> str | None:
    """Why the connector is not synced now, in its members' words; a member gone or a grant
    that cannot be read pauses it."""
    if provider is None:
        # The deployment's client for the service was taken away: its connectors wait.
        return (
            f"{label_of(connector)} is not set up on this deployment any more, so the folder "
            "is not synced. Ask whoever runs Emudoc."
        )
    if project.deleted_at is not None:
        return PROJECT_TRASHED
    member = session.get(User, connector.user_id)
    if member is None or not may_change(session, organization, project, member):
        connector.status = "paused"
        return MEMBER_GONE.format(name=(member.name if member else "") or "The member")
    return None


def may_change(session: Session, organization: Organization, project: Project, user: User) -> bool:
    """Whether the user is a member of the organisation who may change the project and sees it."""
    role = session.scalar(
        select(Membership.role).where(
            Membership.org_id == organization.id, Membership.user_id == user.id
        )
    )
    if role is None:
        return False
    account = Account(organization, user, role)
    if not account.is_at_least("member"):
        return False
    return bool(
        session.scalar(select(exists().where(Project.id == project.id, visible_to(account))))
    )


class Sync:
    """One sync of a connector, from the access token to the upload of what it kept."""

    def __init__(
        self,
        session: Session,
        storage: Storage,
        http: httpx.Client,
        lease: Lease,
        connector: Connector,
        project: Project,
        provider: Provider,
    ):
        self.session = session
        self.storage = storage
        self.http = http
        self.lease = lease
        self.connector = connector
        self.project = project
        self.organization = project.organization
        self.provider = provider
        self.token = ""
        self.kept: list[int] = []
        # The rows of the files no longer below the folder, by their ids at the service.
        self.gone: dict[str, ConnectorFile] = {}
        # Whether a file's new version waited for its document's indexing, which the next sync
        # takes: the mark stays where it was, so that the service says the folder changed.
        self.held_back = False

    def run(self, *, forced: bool) -> str:
        """Sync, and say how it went for the metric: synced, unchanged, or failed."""
        connector = self.connector
        try:
            grant = decrypt(CONNECTOR_TOKENS, connector.id, connector.refresh_token)
        except Undecryptable:
            log.warning(
                "Connector %s's grant cannot be decrypted with ENCRYPTION_KEYS", connector.id
            )
            connector.status = "paused"
            connector.problem = UNREADABLE_GRANT.format(label=self.provider.label)
            self.commit()
            return "paused"
        access = self.provider.access(self.http, grant)
        self.token = access.token
        if access.refresh_token and access.refresh_token != grant:
            connector.refresh_token = encrypt(CONNECTOR_TOKENS, connector.id, access.refresh_token)
            self.commit()
        assert connector.folder_ref is not None
        folder = self.provider.folder(self.http, self.token, connector.folder_ref)
        if folder is None:
            connector.problem = FOLDER_GONE.format(label=self.provider.label)
            self.commit()
            return "failed"
        connector.folder_name = folder.name[:300]
        # A sync that finished last time (no problem left) and finds no change is done.
        if (
            not forced
            and connector.cursor
            and not connector.problem
            and not self.provider.changed(self.http, self.token, folder, connector.cursor)
        ):
            connector.synced_at = datetime.now(UTC)
            self.commit()
            return "unchanged"
        listing = self.provider.listing(self.http, self.token, folder, MAX_FILES)
        if len(listing.files) > MAX_FILES:
            connector.problem = TOO_MANY
            self.commit()
            return "failed"

        rows = {
            row.remote_id: row
            for row in self.session.scalars(
                select(ConnectorFile).where(ConnectorFile.connector_id == connector.id)
            )
        }
        listed = {file.ref: file for file in listing.files}
        self.gone = {ref: row for ref, row in rows.items() if ref not in listed}
        waiting = sorted(
            (
                file
                for file in listing.files
                if file.ref not in rows or rows[file.ref].version != file.version
            ),
            key=lambda file: file.path,
        )
        stopped = ""
        taken = 0
        for file in waiting[:ROUND_FILES]:
            try:
                self._take(file, rows.get(file.ref))
            except ConnectorError as error:
                if not (error.transient or error.revoked):
                    raise
                stopped = self._shown(error)
                break
            taken += 1
        left = len(waiting) - taken
        # Only the sync that took the folder whole removes: a file of a gone one's bytes may
        # be among those left, and take its document over.
        if not stopped and left == 0:
            self._remove(list(self.gone.values()))
        brought = len(self.kept)
        self._upload()
        connector = self.connector
        connector.synced_at = datetime.now(UTC)
        connector.problem = stopped
        if not stopped and left == 0:
            # Only a folder taken whole moves the mark on: the next sync asks from it.
            if not self.held_back:
                connector.cursor = listing.cursor
        elif not stopped:
            queue_sync(self.session, connector, now=True)
        self.commit()
        log.info(
            "Connector %s synced: %s documents kept, %s files waiting",
            connector.id,
            brought,
            left,
        )
        return "failed" if stopped else "synced"

    def failed(self, error: ConnectorError) -> str:
        """Keep what the sync kept before the service failed it, and say why it stopped."""
        self.session.rollback()
        self._upload()
        connector = self.session.get(Connector, self.connector.id)
        if connector is None:
            return "failed"
        if error.revoked:
            connector.status = "paused"
        connector.problem = self._shown(error)
        self.commit()
        log.warning("Connector %s was not synced: %s", connector.id, error)
        return "paused" if error.revoked else "failed"

    def commit(self) -> None:
        _commit(self.session, self.lease)

    def _shown(self, error: ConnectorError) -> str:
        return shown(error, development=get_settings().environment == "development")

    def _target(self) -> int | None:
        folder_id = self.connector.target_folder_id
        folder = self.session.get(Folder, folder_id) if folder_id else None
        return folder.id if folder is not None and folder.deleted_at is None else None

    def _remove(self, gone: list[ConnectorFile]) -> None:
        """The documents of the files no longer below the folder go to the project's trash."""
        for row in gone:
            document = self.session.get(Document, row.document_id) if row.document_id else None
            if document is not None and document.deleted_at is None:
                self._trash(document)
            self.session.delete(row)
        if gone:
            self.commit()

    def _trash(self, document: Document) -> None:
        audit_event(
            self.session,
            "document_deleted",
            org_id=self.project.org_id,
            user_id=self.connector.user_id,
            project_id=self.project.id,
            target=("document", document.id),
            detail={"filename": document.filename, "by_connector": self.provider.name},
        )
        trash_document(self.session, document, self.connector.user_id)
        CONNECTOR_FILES.labels("trashed").inc()

    def _take(self, file: RemoteFile, row: ConnectorFile | None) -> None:
        """Download the file and take it in, or note why it is not."""
        if file.unreadable:
            self._note(file, row, file.unreadable)
            return
        earlier = self.session.get(Document, row.document_id) if row and row.document_id else None
        if earlier is not None and earlier.deleted_at is None:
            self._version(file, row, earlier)
            return
        download = self.provider.download(self.http, self.token, file)
        try:
            documents, rejected = take_files(
                self.session,
                self.storage,
                self.project,
                self._target(),
                [Incoming(file.name, folder_names("/".join(file.folders)), download)],
                user_id=self.connector.user_id,
                audited=self._audited(file, row),
                lease=self.lease,
            )
        except ConnectorError as error:
            if error.transient or error.revoked:
                raise
            self.session.rollback()
            self._note(file, self._row(file), self._shown(error))
            return
        finally:
            download.close()
        if documents:
            self.kept.append(documents[0].id)
            CONNECTOR_FILES.labels("kept").inc()
            return
        refused = rejected[0]
        if refused.reason == LOST_RACE:
            # Another upload of the same bytes was being kept: the next sync sees which.
            return
        if (moved := self._moved(download.sha256)) is not None:
            # The bytes of a file no longer below the folder: one deleted and added again, or
            # copied, whose document stays and is this file's from now on.
            gone, document = moved
            row = self._row(file)
            row.version = file.version
            row.path = file.path
            row.document_id = document.id
            row.reason = ""
            row.seen_at = datetime.now(UTC)
            self.session.delete(gone)
            self.commit()
            return
        self._note(file, self._row(file), refused.reason)

    def _version(self, file: RemoteFile, row: ConnectorFile, document: Document) -> None:
        """Download the file and make it its document's new version, or note why it is not."""
        download = self.provider.download(self.http, self.token, file)

        def audited(action: str, detail: dict) -> None:
            audit_event(
                self.session,
                action,
                org_id=self.project.org_id,
                user_id=self.connector.user_id,
                project_id=self.project.id,
                target=("document", document.id),
                detail={**detail, "by_connector": self.provider.name},
            )
            if action == "document_version_uploaded":
                self._seen(row, file)

        try:
            take_version(
                self.session,
                self.storage,
                self.project,
                document,
                file.name,
                download,
                user_id=self.connector.user_id,
                audited=audited,
                lease=self.lease,
            )
        except VersionRefused as refused:
            self.session.rollback()
            if refused.label == "indexing":
                self.held_back = True
            elif refused.duplicate_of == document.id:
                # A new version with the bytes of the last: the document is the file as it is.
                self._seen(row, file)
                self.commit()
            else:
                self._note(file, row, refused.reason)
            return
        except ConnectorError as error:
            if error.transient or error.revoked:
                raise
            self.session.rollback()
            self._note(file, row, self._shown(error))
            return
        finally:
            download.close()
        self.kept.append(document.id)
        CONNECTOR_FILES.labels("replaced").inc()

    def _seen(self, row: ConnectorFile, file: RemoteFile) -> None:
        row.version = file.version
        row.path = file.path
        row.reason = ""
        row.seen_at = datetime.now(UTC)

    def _moved(self, sha256: str | None) -> tuple[ConnectorFile, Document] | None:
        """The row of a file no longer below the folder whose live document has these bytes,
        and the document; None for none."""
        if sha256 is None:
            return None
        for ref, row in self.gone.items():
            document = self.session.get(Document, row.document_id) if row.document_id else None
            if document is not None and document.deleted_at is None and document.sha256 == sha256:
                del self.gone[ref]
                return row, document
        return None

    def _audited(
        self, file: RemoteFile, row: ConnectorFile | None
    ) -> Callable[[Document, int], None]:
        """What is written in the commit of the document a file became: its audit row and the
        file's row."""

        def audited(document: Document, size: int) -> None:
            audit_event(
                self.session,
                "document_uploaded",
                org_id=self.project.org_id,
                user_id=self.connector.user_id,
                project_id=self.project.id,
                target=("document", document.id),
                detail={
                    "filename": document.filename,
                    "size_bytes": size,
                    "by_connector": self.provider.name,
                },
            )
            kept = row if row is not None else self._row(file)
            self._seen(kept, file)
            kept.document_id = document.id

        return audited

    def _row(self, file: RemoteFile) -> ConnectorFile:
        """The file's row, found or added."""
        row = self.session.scalar(
            select(ConnectorFile).where(
                ConnectorFile.connector_id == self.connector.id,
                ConnectorFile.remote_id == file.ref,
            )
        )
        if row is None:
            row = ConnectorFile(
                connector_id=self.connector.id, remote_id=file.ref, version="", path=file.path
            )
            self.session.add(row)
        return row

    def _note(self, file: RemoteFile, row: ConnectorFile | None, reason: str) -> None:
        """Keep why the file's version is not a document, so that it is not read again."""
        row = row if row is not None else self._row(file)
        row.version = file.version
        row.path = file.path
        row.reason = reason
        row.seen_at = datetime.now(UTC)
        self.commit()
        CONNECTOR_FILES.labels("rejected").inc()

    def _upload(self) -> None:
        """The documents this sync kept, as their member's upload, whose notice and the
        project's automations follow."""
        if not self.kept:
            return
        kept, self.kept = self.kept, []
        upload_id = record_upload(self.session, self.project.id, self.connector.user_id, kept)
        self.commit()
        if upload_id is not None:
            settle_upload(self.session, upload_id, app_address())


def _commit(session: Session, lease: Lease) -> None:
    if not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()
