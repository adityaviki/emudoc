"""Files the members' page uploads that bring others with them (app/unpacking.py): a message
saved from a mail program, whose text and attachments are taken in its place, and a ZIP archive,
whose files go into the folders their paths name; each file through the checks any file meets,
the archives of an upload within what one upload may bring, and both refused to a program,
which pairs the documents kept with the files it sent."""

import zipfile
from email.message import EmailMessage
from io import BytesIO

import pytest

from app import unpacking
from app.config import get_settings
from app.models import Document
from app.parsing import SUPPORTED_EXTENSIONS
from app.unpacking import (
    DAMAGED_ARCHIVE,
    DAMAGED_ENTRY,
    EMPTY_ARCHIVE,
    EMPTY_MESSAGE,
    LOCKED_ARCHIVE,
    NESTED,
    PACKED,
    UNKNOWN_PACKING,
)
from app.uploads import UNMAKEABLE_FOLDER
from tests.test_api import create_project
from tests.test_api_v1 import api  # noqa: F401 (a fixture)
from tests.test_folders import make_folder, tree
from tests.test_inbound import message
from tests.test_metrics import Counted
from tests.test_parsing import RENAME

UNOPENED = ("emudoc_upload_rejections_total", {"reason": "unopened"})


def uploaded(client, project_id: int, *files: tuple[str, bytes], **params) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/documents",
        params=params,
        files=[("files", file) for file in files],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_message_brings_its_text_and_its_attachments_into_the_folder_it_went_into(
    client, session, services, sample_pdf
):
    project = create_project(client)
    folder = make_folder(client, project["id"], "Supplier mail")
    raw = message(
        "ap@buyer.example",
        subject="Late delivery of PO-7708",
        body="Hi,\n\nThe pallets arrived two days late.\n\nLena",
        attachments=(("delivery note.pdf", sample_pdf.read_bytes(), "application/pdf"),),
    )
    result = uploaded(
        client,
        project["id"],
        ("Late delivery.eml", raw),
        ("notes.txt", b"Call Lena."),
        folder_id=folder["id"],
    )

    assert result["rejected"] == []
    assert [document["filename"] for document in result["documents"]] == [
        "Late delivery.txt",
        "delivery note.pdf",
        "notes.txt",
    ]
    assert tree(client, project["id"]) == {
        "Supplier mail": ["delivery note.pdf", "Late delivery.txt", "notes.txt"]
    }
    # The text is a document of its own, headed as an email reads, naming what came with it;
    # the message itself is not kept.
    body = session.get(Document, result["documents"][0]["id"])
    assert body.mime_type == "text/plain"
    text = services.storage.get(body.storage_key).decode()
    assert text.startswith("From: Lena Vogt <")
    assert "Subject: Late delivery of PO-7708\n" in text
    assert "Attached: delivery note.pdf\n\nHi,\n\nThe pallets arrived two days late." in text
    assert session.query(Document).filter(Document.filename.like("%.eml")).count() == 0


def test_what_a_message_brings_is_checked_as_any_file_is_under_the_messages_place(
    client, sample_pdf
):
    project = create_project(client)
    uploaded(client, project["id"], ("msa.pdf", sample_pdf.read_bytes()))
    forwarded = message("ap@buyer.example", subject="The first one")
    raw = message(
        "ap@buyer.example",
        attachments=(
            ("msa.pdf", sample_pdf.read_bytes(), "application/pdf"),
            ("setup.exe", b"MZ" + bytes(64), "application/octet-stream"),
            ("first.eml", forwarded, "application/octet-stream"),
        ),
    )
    result = uploaded(
        client, project["id"], ("notes.txt", b"Call Lena."), ("Invoice INV-7.eml", raw)
    )

    assert [document["filename"] for document in result["documents"]] == [
        "notes.txt",
        "Invoice INV-7.txt",
    ]
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS | PACKED))
    assert [(r["filename"], r["reason"], r["index"]) for r in result["rejected"]] == [
        ("msa.pdf", "Already in this project as 'msa.pdf'.", 1),
        ("setup.exe", f"Unsupported type. Use {supported}.", 1),
        ("first.eml", NESTED, 1),
    ]


def test_a_message_that_cannot_be_opened_is_refused_saying_why(client, monkeypatch):
    project = create_project(client)
    empty = EmailMessage()
    empty["From"] = "lena@supplier.example"
    empty["Subject"] = "Nothing"
    unopened = Counted(UNOPENED)
    result = uploaded(
        client,
        project["id"],
        ("nothing.eml", empty.as_bytes()),
        ("photo.eml", b"\x89PNG\r\n\x1a\n" + bytes(32)),
    )
    assert result["documents"] == []
    assert [(r["filename"], r["reason"]) for r in result["rejected"]] == [
        ("nothing.eml", EMPTY_MESSAGE),
        ("photo.eml", f"Not an email message. {RENAME}"),
    ]
    assert unopened(UNOPENED[0], **UNOPENED[1]) == 2

    # A message is read whole, so one past what mail in takes is not opened.
    monkeypatch.setattr(unpacking, "MAX_MESSAGE_BYTES", 100)
    result = uploaded(client, project["id"], ("long.eml", message("ap@buyer.example")))
    assert [r["reason"] for r in result["rejected"]] == [unpacking.TOO_LARGE_MESSAGE]


def zipped(entries: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    packed = BytesIO()
    with zipfile.ZipFile(packed, "w", method) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return packed.getvalue()


def patched(data: bytes, *, flags: int = 0, method: int | None = None) -> bytes:
    """The archive with these bits set in every entry's flags, or its packing changed, in its
    local headers and its directory alike, as a packer that encrypts or uses Deflate64 writes
    them; `zipfile` writes neither."""
    changed = bytearray(data)
    for signature, flags_at, method_at in ((b"PK\x03\x04", 6, 8), (b"PK\x01\x02", 8, 10)):
        at = changed.find(signature)
        while at != -1:
            changed[at + flags_at] |= flags
            if method is not None:
                changed[at + method_at : at + method_at + 2] = method.to_bytes(2, "little")
            at = changed.find(signature, at + 4)
    return bytes(changed)


def test_an_archive_brings_its_files_into_the_folders_its_paths_name(client):
    project = create_project(client)
    inbox = make_folder(client, project["id"], "Inbox")
    deliveries = zipped(
        {
            "readme.txt": b"Deliveries of the third quarter.",
            "Invoices/notes.md": b"# Notes",
            "Invoices/2024/inv-1.txt": b"Invoice 1",
            "Invoices/empty/": b"",
            # What a computer adds as it packs a folder is no one's document.
            "__MACOSX/Invoices/._notes.md": b"\x00\x05\x16\x07",
            ".DS_Store": b"\x00\x00\x00\x01Bud1",
            "Invoices/Thumbs.db": b"\xd0\xcf\x11\xe0",
        }
    )
    result = uploaded(client, project["id"], ("deliveries.zip", deliveries), folder_id=inbox["id"])

    assert result["rejected"] == []
    # Several things at its top: unpacked into a folder of its name, as a computer unpacks it.
    assert tree(client, project["id"]) == {
        "Inbox": [],
        "Inbox/deliveries": ["readme.txt"],
        "Inbox/deliveries/Invoices": ["notes.md"],
        "Inbox/deliveries/Invoices/2024": ["inv-1.txt"],
    }


def test_an_archive_of_one_folder_or_one_file_adds_no_folder_of_its_own(client):
    project = create_project(client)
    one_folder = zipped({"Receipts/a.txt": b"Receipt A", "Receipts/b.txt": b"Receipt B"})
    one_file = zipped({"contract.txt": b"The contract."})
    result = uploaded(
        client, project["id"], ("Receipts (1).zip", one_folder), ("contract.zip", one_file)
    )
    assert result["rejected"] == []
    assert tree(client, project["id"]) == {"Receipts": ["a.txt", "b.txt"]}
    assert [d["filename"] for d in result["documents"]][-1] == "contract.txt"


def test_what_an_archive_brings_is_checked_file_by_file(client, sample_pdf):
    project = create_project(client)
    stored = zipped(
        {"first.txt": b"First file.", "second.txt": b"Second file."}, zipfile.ZIP_STORED
    )
    # The first file's bytes changed after it was packed: its checksum no longer agrees.
    damaged = stored.replace(b"First file.", b"First fileX", 1)
    archive = zipped(
        {
            "Mail/msa.pdf": sample_pdf.read_bytes(),
            "Mail/inner.zip": zipped({"a.txt": b"A"}),
            # A path out of the folder is hidden, as a dotted name is; one too deep is refused.
            "Mail/../outside.txt": b"Where?",
            f"Mail/{'x' * 201}/far.txt": b"Far",
            "Mail/setup.exe": b"MZ",
        }
    )
    result = uploaded(client, project["id"], ("damaged.zip", damaged), ("mail.zip", archive))

    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS | PACKED))
    assert [d["filename"] for d in result["documents"]] == ["second.txt", "msa.pdf"]
    assert [(r["filename"], r["reason"], r["index"]) for r in result["rejected"]] == [
        ("damaged/first.txt", DAMAGED_ENTRY, 0),
        ("Mail/inner.zip", NESTED, 1),
        ("far.txt", UNMAKEABLE_FOLDER, 1),
        ("Mail/setup.exe", f"Unsupported type. Use {supported}.", 1),
    ]


def test_an_archive_that_cannot_be_opened_is_refused_before_anything_is_unpacked(client):
    project = create_project(client)
    plain = zipped({"a.txt": b"A", "b.txt": b"B"})
    result = uploaded(
        client,
        project["id"],
        ("locked.zip", patched(plain, flags=0x1)),
        ("deflate64.zip", patched(plain, method=9)),
        ("broken.zip", b"PK\x03\x04" + bytes(64)),
        ("empty.zip", zipped({})),
        ("hidden.zip", zipped({".DS_Store": b"Bud1"})),
        ("photo.zip", b"\x89PNG\r\n\x1a\n" + bytes(32)),
    )
    assert result["documents"] == []
    assert [(r["filename"], r["reason"]) for r in result["rejected"]] == [
        ("locked.zip", LOCKED_ARCHIVE),
        ("deflate64.zip", UNKNOWN_PACKING),
        ("broken.zip", DAMAGED_ARCHIVE),
        ("empty.zip", EMPTY_ARCHIVE),
        ("hidden.zip", EMPTY_ARCHIVE),
        ("photo.zip", f"Not a ZIP archive. {RENAME}"),
    ]


def test_the_archives_of_one_upload_bring_what_one_upload_may_between_them(
    client, session, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_upload_files", 3)
    monkeypatch.setattr(get_settings(), "max_upload_request_mb", 1)
    project = create_project(client)
    # A few kilobytes that would unpack to two megabytes: refused from its directory alone.
    bomb = zipped({"zeros.txt": bytes(2 * 1024 * 1024)})
    assert len(bomb) < 10_000
    two = zipped({"a.txt": b"A", "b.txt": b"B"})
    result = uploaded(
        client,
        project["id"],
        ("bomb.zip", bomb),
        ("first.zip", two),
        ("second.zip", zipped({"c.txt": b"C", "d.txt": b"D"})),
    )
    assert [d["filename"] for d in result["documents"]] == ["a.txt", "b.txt"]
    assert [(r["filename"], r["reason"]) for r in result["rejected"]] == [
        (
            "bomb.zip",
            "Unpacked, its files come to 2 MB, and the archives of one upload may bring 1 MB more "
            "at most. Split it, or unpack it and upload its folder.",
        ),
        (
            "second.zip",
            "It holds 2 files, and the archives of one upload may bring 1 more at most. Split "
            "it, or unpack it and upload its folder.",
        ),
    ]
    assert session.query(Document).filter(Document.filename == "zeros.txt").count() == 0


@pytest.mark.parametrize(
    "route", ["/api/v1/projects/{id}/documents", "/api/projects/{id}/documents"]
)
def test_a_program_is_refused_a_message_or_an_archive_as_a_type_not_read(
    client,
    api,  # noqa: F811
    route,
):
    # A program pairs the documents kept with the files it sent, one each.
    project = create_project(client)
    response = api.post(
        route.format(id=project["id"]),
        files=[
            ("files", ("Invoice.eml", message("ap@buyer.example"))),
            ("files", ("deliveries.zip", zipped({"a.txt": b"A"}))),
        ],
    )
    assert response.status_code == 201, response.text
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    assert [(r["filename"], r["reason"]) for r in response.json()["rejected"]] == [
        ("Invoice.eml", f"Unsupported type. Use {supported}."),
        ("deliveries.zip", f"Unsupported type. Use {supported}."),
    ]
