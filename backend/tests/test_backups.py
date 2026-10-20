"""Backups and restores: how long the bucket keeps a deleted file, against what the privacy
policy says (BACKUP_DAYS), and what `after-restore` sets right once the database is restored to
an earlier point: the files deleted since come back from the bucket's versions, the ids move past
the storage's, every sign-in ends and the audit logs record it. An upload refuses a place in the
storage that a lost document's files still hold.

A restore is played by hand: the rows of what was done after the restore point are put back as
they were or removed, and the documents' sequence is set back to where it stood.
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app.auth import issue_link_token, new_token, token_hash
from app.config import get_settings
from app.encrypted import TOTP_SECRETS
from app.indexing.pipeline import Services
from app.keyring import keyring
from app.main import STORAGE_DOWN, create_app
from app.maintenance import main
from app.models import AuditEvent, AuthToken, Document, User, UserSession
from app.restore import after_restore, describe_restore
from app.retention import erasure_key
from app.startup import prepare
from app.storage import LocalStorage, S3Storage, StorageError, check_versions
from tests.conftest import (
    APP_HEADERS,
    STORAGE_ROOT,
    FakeEmbedder,
    FakeLLM,
    FakeS3Client,
    FakeScanner,
)
from tests.test_api import create_project, upload
from tests.test_encrypted import new_key
from tests.test_storage import bucket_error


def rule(days: int, **more) -> dict:
    """A lifecycle rule that removes earlier versions `days` days after they were replaced."""
    return {
        "ID": "earlier-versions",
        "Status": "Enabled",
        "Filter": {},
        "NoncurrentVersionExpiration": {"NoncurrentDays": days},
        **more,
    }


def bucket(versioning: str = "Enabled", lifecycle: list[dict] | None = None) -> S3Storage:
    return S3Storage(FakeS3Client(versioning=versioning, lifecycle=lifecycle), "bucket", "emudoc")


@pytest.fixture
def backup_days(monkeypatch):
    def set_to(days: int | None, environment: str = "development") -> None:
        monkeypatch.setattr(get_settings(), "backup_days", days)
        monkeypatch.setattr(get_settings(), "environment", environment)

    return set_to


# How long the bucket keeps a deleted file


def test_the_lifecycle_rule_that_counts_is_one_for_every_file_of_the_apps():
    assert bucket(lifecycle=[rule(30)]).versions.days == 30
    assert bucket(lifecycle=None).versions.days is None
    assert bucket(lifecycle=[]).versions.days is None
    # The shortest of the rules that cover the documents' files, under the storage's prefix.
    covering = [rule(40, Filter={"Prefix": "emudoc/"}), rule(30, Filter={"Prefix": "emudoc/orgs/"})]
    assert bucket(lifecycle=covering).versions.days == 30
    # Rules from before filters name their prefix beside them.
    assert bucket(lifecycle=[{**rule(20), "Filter": None, "Prefix": "emudoc/"}]).versions.days == 20
    # None of these removes the last version of every deleted file: they leave the documents
    # out, select by a tag or a size the app never sets, keep the newest earlier versions, or
    # are off.
    for leaves_some in (
        rule(10, Filter={"Prefix": "emudoc/erasures/"}),
        rule(10, Filter={"Prefix": "other/"}),
        rule(10, Filter={"Tag": {"Key": "kind", "Value": "page"}}),
        rule(10, Filter={"And": {"Prefix": "emudoc/", "Tags": [{"Key": "a", "Value": "b"}]}}),
        rule(10, Filter={"ObjectSizeGreaterThan": 1024}),
        rule(10, NoncurrentVersionExpiration={"NoncurrentDays": 10, "NewerNoncurrentVersions": 1}),
        {**rule(10), "Status": "Disabled"},
        {"ID": "current", "Status": "Enabled", "Filter": {}, "Expiration": {"Days": 10}},
    ):
        assert bucket(lifecycle=[leaves_some]).versions.days is None, leaves_some
    # A bucket without versioning keeps no earlier versions, whatever its rules say, and is not
    # asked for them.
    plain = bucket(versioning="", lifecycle=[rule(10)])
    assert plain.versions.status == "" and plain.versions.days is None
    assert "get_bucket_lifecycle_configuration" not in plain.client.calls


def test_the_start_says_how_long_the_bucket_keeps_a_deleted_file(backup_days, caplog):
    backup_days(35)
    with caplog.at_level(logging.INFO, logger="app.storage"):
        check_versions(get_settings(), bucket(lifecycle=[rule(30)]))
    assert (
        "The bucket keeps a deleted or replaced file for 30 days (versioning, within "
        "BACKUP_DAYS (35))." in caplog.text
    )

    # Without versions a deleted file is gone at once, which keeps the promise, but the
    # files then have no backup of their own unless the operator keeps one another way.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.storage"):
        check_versions(get_settings(), bucket(versioning=""))
    assert (
        "The bucket keeps no earlier version of a file deleted or replaced from now on "
        "(versioning is off), while BACKUP_DAYS (35) says the deployment keeps backups"
    ) in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.storage"):
        check_versions(get_settings(), bucket(versioning="Suspended", lifecycle=[rule(30)]))
    assert "(versioning is suspended)" in caplog.text
    # With no backups promised, a bucket without versions is what the policy says.
    backup_days(0)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.storage"):
        check_versions(get_settings(), bucket(versioning=""))
    assert caplog.text == ""

    # A bucket that will not say is told what it would take, and not judged.
    silent = bucket()
    silent.client.outage = bucket_error("AccessDenied", 403)
    with caplog.at_level(logging.INFO, logger="app.storage"):
        check_versions(get_settings(), silent)
    assert "did not say whether it keeps deleted files (AccessDenied" in caplog.text
    assert "allow the app s3:GetBucketVersioning on it" in caplog.text
    # A directory keeps no versions; its backups are the disk's.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.storage"):
        check_versions(get_settings(), LocalStorage(Path("unused")))
    assert caplog.text == ""


def test_production_refuses_a_bucket_that_keeps_deleted_files_longer_than_it_says(
    backup_days, caplog
):
    backup_days(35, "production")
    with pytest.raises(RuntimeError) as forever:
        check_versions(get_settings(), bucket(lifecycle=None))
    assert str(forever.value) == (
        "The bucket keeps every deleted or replaced file for good (versioning is on, and no "
        "lifecycle rule removes the earlier versions of every file the app keeps), but the "
        "privacy policy says a deleted account is gone from the backups 35 days after its "
        "deletion (BACKUP_DAYS): add a lifecycle rule whose NoncurrentVersionExpiration is at "
        "most 35 days. Production does not start until they agree."
    )
    with pytest.raises(RuntimeError, match="shorten the rule to at most 35 days, or raise "):
        check_versions(get_settings(), bucket(lifecycle=[rule(60)]))
    # Versions kept after they are no longer current count while versioning is suspended.
    with pytest.raises(RuntimeError, match="for 60 days"):
        check_versions(get_settings(), bucket(versioning="Suspended", lifecycle=[rule(60)]))
    backup_days(0, "production")
    with pytest.raises(RuntimeError, match="BACKUP_DAYS is 0, which the privacy policy words"):
        check_versions(get_settings(), bucket(lifecycle=[rule(1)]))
    check_versions(get_settings(), bucket(versioning=""))

    # Development says the same and starts.
    backup_days(35)
    with caplog.at_level(logging.WARNING, logger="app.storage"):
        check_versions(get_settings(), bucket(lifecycle=[rule(60)]))
    assert "The bucket keeps a deleted or replaced file for 60 days" in caplog.text


def test_every_process_checks_the_bucket_at_start(session, backup_days):
    backup_days(35, "production")
    services = Services(FakeLLM(), FakeEmbedder(), bucket(lifecycle=None), FakeScanner())
    with pytest.raises(RuntimeError, match="keeps every deleted or replaced file for good"):
        prepare(get_settings(), services)


# Bringing back what was deleted since a moment


def test_deletions_since_a_moment_are_found_and_undone_and_none_before():
    storage = bucket(lifecycle=[rule(30)])
    client = storage.client
    for name in ("a", "b", "c", "d", "e"):
        storage.put(f"orgs/1/{name}.txt", name.encode() * 3)
    storage.delete(["orgs/1/a.txt"])
    point = datetime.now(UTC)
    # After the point: b is deleted, c twice over, d deleted then stored again, and e, which
    # was deleted before the point as well, deleted once more.
    storage.delete(["orgs/1/e.txt"])
    for marker in client.versions["emudoc/orgs/1/e.txt"]:
        marker["LastModified"] = point - timedelta(minutes=1)
    storage.delete(["orgs/1/b.txt", "orgs/1/c.txt", "orgs/1/c.txt", "orgs/1/d.txt", "orgs/1/e.txt"])
    storage.put("orgs/1/d.txt", b"again")

    found = storage.deletions("orgs/", point)
    assert [(d.key, d.size, len(d.markers)) for d in found] == [
        ("orgs/1/b.txt", 3, 1),
        ("orgs/1/c.txt", 3, 2),
    ]
    assert all(deletion.deleted_at > point for deletion in found)
    storage.undelete(found)
    assert storage.get("orgs/1/b.txt") == b"bbb" and storage.get("orgs/1/c.txt") == b"ccc"
    assert storage.get("orgs/1/d.txt") == b"again"
    # Deleted before the point, and so not part of the deployment as it was then.
    assert not storage.exists("orgs/1/a.txt") and not storage.exists("orgs/1/e.txt")
    assert storage.deletions("orgs/", point) == []


def test_a_delete_the_bucket_refuses_for_some_keys_is_not_taken_as_done():
    storage = bucket(versioning="")
    storage.put("orgs/1/a.txt", b"a")
    storage.put("orgs/1/b.txt", b"b")
    storage.client.refused.add("emudoc/orgs/1/b.txt")
    with pytest.raises(StorageError, match=r"1 of the objects could not be deleted .*AccessDenied"):
        storage.delete_prefix("orgs/")
    assert storage.exists("orgs/1/b.txt")


# After the database is restored


@pytest.fixture
def versioned(session, mailer):
    """The API over a bucket with versioning, whose rule keeps earlier versions for 30 days."""
    storage = bucket(lifecycle=[rule(30)])
    services = Services(FakeLLM(), FakeEmbedder(), storage, FakeScanner())
    with TestClient(create_app(services=services, mailer=mailer), headers=APP_HEADERS) as client:
        client.storage = storage
        yield client


def documents_continue_after(session, document_id: int) -> None:
    """Set the documents' ids back, as a database restored from before they were handed out
    has them."""
    session.execute(text("SELECT setval('documents_id_seq', :id)"), {"id": document_id})
    session.commit()


@dataclass
class Played:
    """A restore played by hand, and what the database lost to it."""

    point: datetime
    project: dict
    kept: dict
    deleted: dict
    deleted_key: str
    lost: dict
    signed_in: int
    purged: int


def play_a_restore(client, session, account, sample_pdf, sample_docx, sample_xlsx) -> Played:
    """Before the restore point: two documents, a session, a link and an account later purged.
    After it: a third document uploaded, the second deleted, the account purged. Then the
    database is restored to the point, which has the deleted document's row and the purged
    account, not the new document's row."""
    storage = client.storage
    project = create_project(client)
    (kept,) = upload(client, project["id"], sample_pdf)["documents"]
    (deleted,) = upload(client, project["id"], sample_docx)["documents"]
    as_it_was = dict(
        session.execute(select(Document.__table__).where(Document.id == deleted["id"]))
        .one()
        ._mapping
    )
    user = session.get(User, account.user.id)
    signed_in = UserSession(
        user_id=user.id,
        token_hash=token_hash(new_token()),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(signed_in)
    issue_link_token(session, user, "reset_password", user.email, timedelta(hours=1))
    # An account purged since the restore point leaves its record; the restore brings it back.
    purged = User(email="gone@example.com", name="Gone")
    session.add(purged)
    session.commit()
    purged_id = purged.id
    point = datetime.now(UTC)

    # After the point, a document is uploaded and another deleted; then the database is lost
    # and restored to the point, which has the deleted document's row and not the new one's.
    (lost,) = upload(client, project["id"], sample_xlsx)["documents"]
    assert client.delete(f"/api/documents/{deleted['id']}").status_code == 204
    storage.put(erasure_key(purged.id, purged.created_at), b"{}")
    session.execute(Document.__table__.delete().where(Document.id == lost["id"]))
    session.execute(Document.__table__.insert(), [as_it_was])
    for table in ("audit_events", "usage_events"):
        session.execute(text(f"DELETE FROM {table} WHERE occurred_at > :point"), {"point": point})
    session.commit()
    documents_continue_after(session, deleted["id"])
    assert not storage.exists(as_it_was["storage_key"])
    return Played(
        point,
        project,
        kept,
        deleted,
        as_it_was["storage_key"],
        lost,
        signed_in.id,
        purged_id,
    )


def test_after_a_restore_the_files_ids_and_sign_ins_agree_with_the_database(
    versioned, session, account, sample_pdf, sample_docx, sample_xlsx
):
    client, storage = versioned, versioned.storage
    played = play_a_restore(client, session, account, sample_pdf, sample_docx, sample_xlsx)
    point, project, lost, purged_id = played.point, played.project, played.lost, played.purged

    restored = after_restore(storage, point)
    assert [deletion.key for deletion in restored.brought_back] == [played.deleted_key]
    assert storage.get(played.deleted_key) == sample_docx.read_bytes()
    assert restored.advanced == {"documents": lost["id"]}
    assert (restored.sessions, restored.links) == (1, 1)
    assert restored.purged == [purged_id]
    assert restored.missing == [] and restored.unknown == 1
    session.expire_all()
    assert session.get(User, purged_id) is None
    assert session.get(UserSession, played.signed_in).revoked_at is not None
    assert session.scalar(select(func.count()).where(AuthToken.expires_at > func.now())) == 0
    (row,) = session.scalars(select(AuditEvent).where(AuditEvent.action == "database_restored"))
    assert (row.org_id, row.user_id) == (account.organization.id, None)
    assert row.detail == {"restored_to": point.isoformat()}

    moment = f"{point:%Y-%m-%d %H:%M:%S} UTC"
    restored.took = 72.4
    lines = describe_restore(restored, point)
    assert re.fullmatch(
        r"The database reaches 20\d\d-\d\d-\d\d \d\d:\d\d:\d\d UTC \(the newest act of a member it "
        r"records\), \d s before the restore point\.",
        lines[0],
    )
    assert lines[1:] == [
        "It holds 1 organisation, 2 accounts, 1 project, 2 documents and 0 chats.",
        f"Brought back 1 file (0.0 MB) deleted from the bucket since {moment}.",
        f"New ids continue past the storage's: documents after {lost['id']}.",
        "Ended 1 session and 1 unused link: everyone signs in again, and links mailed before now "
        "no longer work.",
        "Recorded the restore in the audit log of 1 organisation.",
        f"Purged again 1 account deleted since the restore point: {purged_id}.",
        "The storage holds the files of 1 document (0.0 MB) that the database does not know, "
        "uploaded after the restore point; the worker's housekeeping removes them an hour after "
        "they were written, and `sweep-orphans` lists them.",
        "Took 1 min 12 s.",
        "Start the app.",
    ]

    # The next upload gets an id of its own, and every document has its original again.
    (new,) = upload(client, project["id"], sample_xlsx)["documents"]
    assert new["id"] == lost["id"] + 1
    for document in (played.kept, played.deleted, new):
        assert storage.exists(session.get(Document, document["id"]).storage_key)

    # Run again, it finds everything set right already.
    again = after_restore(storage, point)
    assert again.brought_back == [] and again.advanced == {} and again.organizations == 0
    assert describe_restore(again, point)[3:6] == [
        "New ids already continued past every id the storage holds.",
        "Ended 0 sessions and 0 unused links: everyone signs in again, and links mailed before "
        "now no longer work.",
        "Every organisation's audit log records this restore already.",
    ]


def test_a_dry_run_finds_what_a_run_does_and_changes_nothing(
    versioned, session, account, sample_pdf, sample_docx, sample_xlsx
):
    client, storage = versioned, versioned.storage
    played = play_a_restore(client, session, account, sample_pdf, sample_docx, sample_xlsx)
    point, lost, purged_id = played.point, played.lost, played.purged
    last_document = session.scalar(text("SELECT pg_sequence_last_value('documents_id_seq')"))
    # An account whose deletion is due: a purge would discard its files and write its record,
    # and the storage a drill reads is the live one.
    due = User(
        email="due@example.com", name="Due", deletion_due_at=datetime.now(UTC) - timedelta(hours=1)
    )
    session.add(due)
    session.commit()
    due_id, due_record = due.id, erasure_key(due.id, due.created_at)

    dry = after_restore(storage, point, dry_run=True)
    assert [deletion.key for deletion in dry.brought_back] == [played.deleted_key]
    # The file it would bring back counts as the storage's, so no document lacks its original.
    assert dry.missing == [] and dry.unknown == 1
    assert dry.advanced == {"documents": lost["id"]}
    assert (dry.sessions, dry.links, dry.organizations, dry.purged) == (1, 1, 1, [purged_id])

    # Neither the storage nor the database changed.
    assert not storage.exists(played.deleted_key)
    session.expire_all()
    assert session.scalar(text("SELECT pg_sequence_last_value('documents_id_seq')")) == (
        last_document
    )
    assert session.get(UserSession, played.signed_in).revoked_at is None
    assert session.scalar(select(func.count()).where(AuthToken.expires_at > func.now())) == 1
    assert session.get(User, purged_id).deletion_due_at is None
    assert session.get(User, due_id) is not None and not storage.exists(due_record)
    assert not session.scalar(select(func.count()).where(AuditEvent.action == "database_restored"))

    moment = f"{point:%Y-%m-%d %H:%M:%S} UTC"
    dry.took = 3
    lines = describe_restore(dry, point)
    assert lines[0] == (
        f"A dry run against the database restored to {moment}: nothing in the database or the "
        "storage was changed, and each line says what after-restore would do."
    )
    assert lines[1].startswith("The database reaches ")
    assert lines[2:] == [
        "It holds 1 organisation, 3 accounts, 1 project, 2 documents and 0 chats.",
        f"Would bring back 1 file (0.0 MB) deleted from the bucket since {moment}.",
        f"New ids would continue past the storage's: documents after {lost['id']}.",
        "Would end 1 session and 1 unused link.",
        "Would record the restore in the audit log of 1 organisation.",
        f"Would purge again 1 account deleted since the restore point: {purged_id}.",
        "The storage holds the files of 1 document (0.0 MB) that the database does not know, "
        "uploaded after the restore point, which a restore to it would leave to the worker's "
        "housekeeping.",
        "Took 3 s.",
        f"The restore is whole: the storage holds every document's original as it was at {moment}.",
    ]

    # The run then does what the dry run said.
    done = after_restore(storage, point)
    for name in ("advanced", "sessions", "links", "organizations", "purged", "missing", "unknown"):
        assert getattr(done, name) == getattr(dry, name), name
    assert done.brought_back == dry.brought_back
    assert storage.exists(played.deleted_key)
    session.expire_all()
    assert session.get(User, due_id) is None and storage.exists(due_record)


def test_a_dry_run_fails_when_the_storage_cannot_come_back_to_the_point(
    client, session, sample_pdf, capsys
):
    project = create_project(client)
    (document,) = upload(client, project["id"], sample_pdf)["documents"]
    point = datetime.now(UTC).isoformat()
    main(["after-restore", "--dry-run", point])
    out = capsys.readouterr().out
    assert "The storage keeps no earlier versions of deleted files, so nothing deleted since" in out
    assert "can be brought back from it" in out
    assert "The restore is whole: the storage holds every document's original as it was" in out

    # The directory lost the original since, and keeps no earlier version of it.
    LocalStorage(STORAGE_ROOT).delete_prefix(session.get(Document, document["id"]).storage_prefix)
    with pytest.raises(SystemExit) as failed:
        main(["after-restore", "--dry-run", point])
    assert failed.value.code == 1
    out = capsys.readouterr().out
    assert (
        f"1 document without an original in the storage, now or among the earlier versions it "
        f"keeps ({document['id']}): a restore to " in out
    )
    assert "would leave it unable to be opened or indexed again." in out
    assert (
        "The restore is not whole: the database and the storage do not come back to the same "
        "point." in out
    )


def test_a_dry_run_fails_when_a_restored_secret_s_key_is_gone(session, account, capsys):
    # The copy holds an authenticator encrypted with a key removed from the list since.
    removed = keyring(new_key(), False)
    user_id = account.user.id
    value = removed.encrypt("JBSWY3DPEHPK3PXP", TOTP_SECRETS.place(user_id))
    session.execute(update(User).where(User.id == user_id).values(totp_secret=value))
    session.commit()
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    with pytest.raises(SystemExit) as failed:
        main(["after-restore", "--dry-run", datetime.now(UTC).isoformat()])
    assert failed.value.code == 1
    out = capsys.readouterr().out
    assert (
        "1 authenticator secret is encrypted with a key ENCRYPTION_KEYS does not hold "
        f"({removed.current}), so its member cannot sign in" in out
    )
    assert "A key stays in ENCRYPTION_KEYS for as long as a backup holds what it encrypted" in out
    assert (
        "The restore is not whole: ENCRYPTION_KEYS no longer holds a key the database's backups "
        "need." in out
    )


def test_a_dry_run_reads_a_directory_it_cannot_write_in(client, sample_pdf, capsys):
    # The drill runs the dry run as Postgres's user, with the live directory mounted read-only.
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    point = datetime.now(UTC).isoformat()
    mode = STORAGE_ROOT.stat().st_mode
    STORAGE_ROOT.chmod(0o500)
    try:
        if os.access(STORAGE_ROOT, os.W_OK):
            pytest.skip("this user writes in any directory")
        main(["after-restore", "--dry-run", point])
        assert "The restore is whole" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="is not writable by this process"):
            main(["after-restore", point])
    finally:
        STORAGE_ROOT.chmod(mode)


def test_the_report_says_how_far_the_restored_database_reaches(session, account, monkeypatch):
    storage = LocalStorage(STORAGE_ROOT)
    point = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
    session.execute(text("DELETE FROM sessions"))
    session.commit()
    assert describe_restore(after_restore(storage, point, dry_run=True), point)[1] == (
        "The database records no act of a member, so how far it reaches cannot be told."
    )

    # A member's newest act stands in; the app's own rows, after-restore's among them, do not.
    org, user = account.organization.id, account.user.id
    for user_id, before in ((user, 19), (user, 400), (None, 1)):
        session.add(
            AuditEvent(
                org_id=org,
                user_id=user_id,
                action="document_uploaded",
                occurred_at=point - timedelta(seconds=before),
            )
        )
    session.commit()
    restored = after_restore(storage, point, dry_run=True)
    assert restored.reached == point - timedelta(seconds=19)
    assert describe_restore(restored, point)[1] == (
        "The database reaches 2026-10-15 09:29:41 UTC (the newest act of a member it records), "
        "19 s before the restore point."
    )

    # The server's own word, while it has it since its recovery, comes first.
    for replayed, said in (
        ("2026-10-15 09:26:00+00", "4 min before the restore point."),
        ("2026-10-15 07:25:00+00", "2 h 5 min before the restore point."),
        ("2026-10-12 09:30:00+00", "3 days before the restore point."),
        (
            "2026-10-15 09:30:41+00",
            "41 s after the restore point given: it was restored to a later moment.",
        ),
    ):
        monkeypatch.setattr(
            "app.restore._REPLAYED", text(f"SELECT CAST('{replayed}' AS timestamptz)")
        )
        line = describe_restore(after_restore(storage, point, dry_run=True), point)[1]
        assert line.endswith(f"(the last transaction its recovery replayed), {said}"), line


def test_an_upload_is_refused_the_place_of_a_document_the_restore_lost(
    client, session, sample_pdf, sample_docx, sample_xlsx, caplog, capsys
):
    project = create_project(client)
    point = datetime.now(UTC)
    first, second = upload(client, project["id"], sample_pdf, sample_docx)["documents"]
    # The database is restored to before both were uploaded; the directory keeps their files.
    session.execute(Document.__table__.delete())
    session.execute(text("SELECT setval('documents_id_seq', 1, false)"))
    session.commit()

    # The next document would be given the first one's ids and served its pages.
    with caplog.at_level(logging.WARNING, logger="app.main"):
        response = client.post(
            f"/api/projects/{project['id']}/documents",
            files=[("files", ("prices.xlsx", sample_xlsx.read_bytes()))],
        )
    assert response.status_code == 503 and response.json()["detail"] == STORAGE_DOWN
    assert (
        f"Document {first['id']} was not stored: orgs/1/projects/{project['id']}/documents/"
        f"{first['id']}/ in the storage holds the files of an earlier document" in caplog.text
    )
    assert "run `uv run python -m app.maintenance after-restore <time>`" in caplog.text
    assert session.scalar(select(func.count()).select_from(Document)) == 0

    main(["after-restore", point.isoformat()])
    out = capsys.readouterr().out
    assert "The storage keeps no earlier versions of deleted files, so nothing deleted since" in out
    assert f"New ids continue past the storage's: documents after {second['id']}." in out
    assert "The storage holds the files of 2 documents (" in out
    (taken,) = upload(client, project["id"], sample_xlsx)["documents"]
    assert taken["id"] == second["id"] + 1


def test_a_document_whose_original_the_storage_lacks_is_named(client, session, sample_pdf, capsys):
    project = create_project(client)
    (document,) = upload(client, project["id"], sample_pdf)["documents"]
    row = session.get(Document, document["id"])
    # The directory was restored to an earlier point than the database.
    LocalStorage(STORAGE_ROOT).delete_prefix(row.storage_prefix)
    main(["after-restore", "2026-10-15 09:30:00+00"])
    out = capsys.readouterr().out
    assert (
        f"1 document without an original in the storage ({document['id']}): the storage is "
        "older than the database's restore point." in out
    )


def test_the_command_needs_the_restore_point_with_its_zone(capsys):
    for wrong, why in (
        ("2026-10-15T09:30:00", "has no zone; add +00 for UTC, as in '2026-10-15 09:30:00+00'"),
        ("yesterday", "is not a time such as '2026-10-15 09:30:00+00'"),
    ):
        with pytest.raises(SystemExit):
            main(["after-restore", wrong])
        assert f"error: argument TIME: '{wrong}' {why}" in capsys.readouterr().err
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(SystemExit):
        main(["after-restore", later])
    assert "TIME is in the future" in capsys.readouterr().err
