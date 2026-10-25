"""Files the members' page uploads that bring others with them (app/unpacking.py): a message
saved from a mail program, whose text and attachments are taken in its place, each through the
checks any file meets, and refused to a program, which pairs the documents kept with the files
it sent."""

from email.message import EmailMessage

import pytest

from app import unpacking
from app.models import Document
from app.parsing import SUPPORTED_EXTENSIONS
from app.unpacking import EMPTY_MESSAGE, NESTED
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
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS | {".eml"}))
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


@pytest.mark.parametrize(
    "route", ["/api/v1/projects/{id}/documents", "/api/projects/{id}/documents"]
)
def test_a_program_is_refused_a_message_as_a_type_not_read(client, api, route):  # noqa: F811
    # A program pairs the documents kept with the files it sent, one each.
    project = create_project(client)
    response = api.post(
        route.format(id=project["id"]),
        files=[("files", ("Invoice.eml", message("ap@buyer.example")))],
    )
    assert response.status_code == 201, response.text
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    assert [(r["filename"], r["reason"]) for r in response.json()["rejected"]] == [
        ("Invoice.eml", f"Unsupported type. Use {supported}.")
    ]
