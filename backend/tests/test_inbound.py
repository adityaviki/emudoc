"""Mail in (app/inbound.py, app/api/mailbox.py): a project's own address, whose mail from the
senders its members allow, proven by the provider, becomes its documents, its text as one and
each attachment as an upload's file, and what became of each message kept for its members."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest
from sqlalchemy import select, text, update

from app.config import get_settings
from app.db import engine
from app.flags import EMAIL_IN, FLAGS
from app.inbound import (
    INBOUND_DAYS,
    MAX_SENDERS,
    NO_DOMAIN,
    NOTHING_IN_IT,
    PROJECT_TRASHED,
    UNAUTHENTICATED,
    Received,
    listed,
    not_listed,
    parse,
    prune_inbound,
    receive,
    sender_entry,
    text_of_html,
    token_in,
)
from app.maintenance import main
from app.models import AuditEvent, Document, InboundMessage, Upload, User
from tests.test_api import create_project
from tests.test_entitlements import plan  # noqa: F401 (a fixture)
from tests.test_folders import make_folder
from tests.test_metrics import Counted
from tests.test_templates import turn_on

DOMAIN = "in.emudoc.test"
APP = "https://emudoc.example.com"
SUPPLIER = "lena.vogt@northwind.example"
DATE = "Fri, 14 Aug 2026 10:02:00 +0200"
INBOUND = "emudoc_inbound_messages_total"


@pytest.fixture(autouse=True)
def email_in(monkeypatch):
    """The feature on for every organisation, and the deployment's inbound domain."""
    turn_on(monkeypatch, EMAIL_IN)
    monkeypatch.setattr(get_settings(), "inbound_domain", DOMAIN)


def mailbox(client, project_id, senders=(), folder_id=None, status=200):
    response = client.put(
        f"/api/projects/{project_id}/mailbox",
        json={"senders": list(senders), "folder_id": folder_id},
    )
    assert response.status_code == status, response.text
    return response.json()


def shown(client, project_id):
    response = client.get(f"/api/projects/{project_id}/mailbox")
    assert response.status_code == 200, response.text
    return response.json()


def message(
    to: str,
    sender: str = SUPPLIER,
    subject: str = "Invoice INV-7 for July",
    body: str | None = "Hi,\n\nThe invoice for July is attached.\n\nLena",
    attachments: tuple[tuple[str, bytes, str], ...] = (),
    html: str | None = None,
) -> bytes:
    """A message as a mail program sends it: a text body, an HTML one beside it, attachments
    as (name, bytes, type)."""
    written = EmailMessage()
    written["From"] = f"Lena Vogt <{sender}>"
    written["To"] = to
    written["Subject"] = subject
    written["Date"] = DATE
    if body is not None:
        written.set_content(body)
    if html is not None:
        if body is None:
            written.set_content(html, subtype="html")
        else:
            written.add_alternative(html, subtype="html")
    for name, data, kind in attachments:
        maintype, subtype = kind.split("/")
        written.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return written.as_bytes()


def counting(outcome: str):
    """How many more messages ended so, from now: `counting("kept")()`."""
    counted = Counted((INBOUND, {"outcome": outcome}))
    return lambda: counted(INBOUND, outcome=outcome)


def confirmed(session, *users: User) -> None:
    session.execute(
        update(User)
        .where(User.id.in_([user.id for user in users]))
        .values(email_verified_at=datetime.now(UTC))
    )
    session.commit()


def deliver(session, services, raw: bytes, to: str, delivery: str = "test:1", proven=True):
    return receive(session, services.storage, Received(delivery, raw, [to], proven), APP)


def documents_of(session, project_id) -> list[Document]:
    session.expire_all()
    return list(
        session.scalars(
            select(Document).where(Document.project_id == project_id).order_by(Document.id)
        )
    )


def test_a_member_gives_a_project_an_address(client, session):
    project = create_project(client)
    before = shown(client, project["id"])
    assert before["available"] is True
    assert before["address"] is None
    assert before["max_senders"] == MAX_SENDERS
    assert before["days"] == INBOUND_DAYS

    out = mailbox(
        client, project["id"], [" AP@Northwind.example ", "@acme.example", "acme.example"]
    )
    token, _, domain = out["address"].partition("@")
    assert domain == DOMAIN
    assert len(token) == 16 and token.isalnum() and token == token.lower()
    # Lower-cased, a domain bare, each once, in the order given.
    assert out["senders"] == ["ap@northwind.example", "acme.example"]
    assert shown(client, project["id"])["address"] == out["address"]
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "mailbox_created"))
    # Counted, never named.
    assert event.detail == {"senders": 2, "folder": None}
    assert event.project_id == project["id"]


def test_the_list_takes_addresses_and_domains_but_no_mail_service(client):
    project = create_project(client)
    refused = client.put(f"/api/projects/{project['id']}/mailbox", json={"senders": ["gmail.com"]})
    assert refused.status_code == 400
    assert "Anyone can have an address at gmail.com" in refused.json()["detail"]
    for entry in ("not a domain", "a b@acme.example", "ap@nowhere"):
        assert (
            client.put(
                f"/api/projects/{project['id']}/mailbox", json={"senders": [entry]}
            ).status_code
            == 400
        ), entry
    many = [f"ap{n}@acme.example" for n in range(MAX_SENDERS + 1)]
    assert (
        client.put(f"/api/projects/{project['id']}/mailbox", json={"senders": many}).status_code
        == 400
    )
    # A mail service's address is one sender, not everyone there.
    assert mailbox(client, project["id"], ["lena@gmail.com"])["senders"] == ["lena@gmail.com"]

    assert sender_entry("Acme.Example") == ("acme.example", None)
    assert listed(["acme.example"], "ap@billing.acme.example")
    assert not listed(["acme.example"], "ap@notacme.example")
    assert not listed(["ap@acme.example"], "ar@acme.example")
    assert token_in("Abc+invoices@IN.emudoc.test", DOMAIN) == "abc"
    assert token_in("abc@elsewhere.test", DOMAIN) is None


def test_a_listed_sender_s_message_becomes_documents(client, session, services, sample_pdf):
    project = create_project(client)
    address = mailbox(client, project["id"], ["northwind.example"])["address"]
    raw = message(
        address,
        html='<p>The invoice for July is attached.</p><img src="cid:logo">',
        attachments=(("INV-7.pdf", sample_pdf.read_bytes(), "application/pdf"),),
    )
    # The signature's logo, which the HTML shows by its Content-ID, is the body's.
    written = message_from_bytes(raw, policy=policy.default)
    html_part = next(part for part in written.walk() if part.get_content_type() == "text/html")
    html_part.add_related(
        b"\x89PNG logo", "image", "png", cid="<logo>", filename="logo.png", disposition="inline"
    )
    raw = written.as_bytes()

    kept = counting("kept")
    records = deliver(session, services, raw, address)
    assert kept() == 1
    assert [(r.outcome, r.kept, r.rejected, r.user_id) for r in records] == [("kept", 2, [], None)]

    documents = documents_of(session, project["id"])
    assert [d.filename for d in documents] == ["Invoice INV-7 for July.txt", "INV-7.pdf"]
    assert all(d.status == "queued" for d in documents)
    body = services.storage.get(documents[0].storage_key).decode()
    assert body == (
        f"From: Lena Vogt <{SUPPLIER}>\n"
        f"To: {address}\n"
        f"Date: {DATE}\n"
        "Subject: Invoice INV-7 for July\n"
        "Attached: INV-7.pdf\n"
        "\n"
        "Hi,\n\nThe invoice for July is attached.\n\nLena\n"
    )
    upload = session.get(Upload, records[0].upload_id)
    # A sender outside the organisation's is nobody's upload, so its notice goes to no one.
    assert upload.user_id is None
    assert {d.upload_id for d in documents} == {upload.id}
    rows = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "document_uploaded").order_by(AuditEvent.id)
    ).all()
    assert [(row.user_id, row.detail["filename"], row.detail["by_email"]) for row in rows] == [
        (None, "Invoice INV-7 for July.txt", True),
        (None, "INV-7.pdf", True),
    ]

    listed_ = shown(client, project["id"])["messages"]
    assert [
        (m["sender"], m["member"], m["subject"], m["outcome"], m["kept"], m["reason"])
        for m in listed_
    ] == [(SUPPLIER, None, "Invoice INV-7 for July", "kept", 2, "")]


def test_a_delivery_is_taken_once(client, session, services):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    raw = message(address)
    assert len(deliver(session, services, raw, address, "resend:abc")) == 1
    assert deliver(session, services, raw, address, "resend:abc") == []
    assert len(documents_of(session, project["id"])) == 1
    assert len(shown(client, project["id"])["messages"]) == 1


def test_a_member_s_own_mail_is_theirs_and_a_viewer_s_is_turned_away(
    client, session, services, account, make_account
):
    project = create_project(client)
    address = mailbox(client, project["id"])["address"]
    organization = account.organization
    member = make_account("bob@acme.example", organization, role="member")
    viewer = make_account("vic@acme.example", organization, role="viewer")
    confirmed(session, member.user, viewer.user)

    kept = deliver(session, services, message(address, sender="Bob@Acme.example"), address, "t:1")
    assert [(r.outcome, r.user_id) for r in kept] == [("kept", member.user.id)]
    assert session.get(Upload, kept[0].upload_id).user_id == member.user.id

    refused = deliver(
        session,
        services,
        message(address, sender="vic@acme.example", subject="Viewer"),
        address,
        "t:2",
    )
    assert [(r.outcome, r.reason) for r in refused] == [("refused", not_listed("vic@acme.example"))]
    messages = shown(client, project["id"])["messages"]
    assert [(m["sender"], m["member"]) for m in messages] == [
        ("vic@acme.example", None),
        ("bob@acme.example", "Bob"),
    ]


def test_a_sender_not_listed_or_not_proven_is_turned_away(client, session, services):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    unproven = counting("unauthenticated")
    records = deliver(session, services, message(address), address, "t:1", proven=False)
    assert unproven() == 1
    assert [(r.outcome, r.reason) for r in records] == [("refused", UNAUTHENTICATED)]

    stranger = "mallory@elsewhere.example"
    refused = counting("refused_sender")
    records = deliver(session, services, message(address, sender=stranger), address, "t:2")
    assert refused() == 1
    assert [(r.outcome, r.reason, r.sender) for r in records] == [
        ("refused", not_listed(stranger), stranger)
    ]
    assert documents_of(session, project["id"]) == []
    # Nothing of the message's in the log or the audit log.
    assert (
        session.scalar(select(AuditEvent).where(AuditEvent.action == "document_uploaded")) is None
    )


def test_mail_for_no_project_or_with_email_in_off_takes_nothing(
    client, session, services, monkeypatch
):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    unknown = counting("unknown_address")
    assert deliver(session, services, message(f"nobody@{DOMAIN}"), f"nobody@{DOMAIN}") == []
    assert unknown() == 1
    # Another domain's address is not looked for at all.
    assert deliver(session, services, message("x@elsewhere.test"), "x@elsewhere.test") == []

    monkeypatch.setitem(FLAGS, EMAIL_IN, replace(FLAGS[EMAIL_IN], share=0))
    off = counting("off")
    assert deliver(session, services, message(address), address) == []
    assert off() == 1
    assert client.get(f"/api/projects/{project['id']}/mailbox").status_code == 404
    assert session.scalar(select(InboundMessage)) is None


def test_what_arrives_goes_into_the_address_s_folder_or_the_root(client, session, services):
    project = create_project(client)
    inbox = make_folder(client, project["id"], "Inbox")
    out = mailbox(client, project["id"], [SUPPLIER], folder_id=inbox["id"])
    assert (out["folder_id"], out["folder"]) == (inbox["id"], "Inbox")
    address = out["address"]
    deliver(session, services, message(address, subject="First"), address, "t:1")
    assert [d.folder_id for d in documents_of(session, project["id"])] == [inbox["id"]]

    assert client.delete(f"/api/folders/{inbox['id']}").status_code == 204
    shown_ = shown(client, project["id"])
    assert (shown_["folder_id"], shown_["folder"]) == (None, None)
    deliver(session, services, message(address, subject="Second"), address, "t:2")
    second = [d for d in documents_of(session, project["id"]) if d.filename == "Second.txt"]
    assert [d.folder_id for d in second] == [None]

    other = create_project(client, "Elsewhere")
    elsewhere = make_folder(client, other["id"], "Theirs")
    assert (
        client.put(
            f"/api/projects/{project['id']}/mailbox",
            json={"senders": [], "folder_id": elsewhere["id"]},
        ).status_code
        == 404
    )


def test_a_new_address_retires_the_old_and_taking_it_away_ends_the_mail(client, session, services):
    project = create_project(client)
    old = mailbox(client, project["id"], [SUPPLIER])["address"]
    response = client.post(f"/api/projects/{project['id']}/mailbox/address")
    assert response.status_code == 200
    new = response.json()["address"]
    assert new != old
    assert response.json()["senders"] == [SUPPLIER]
    assert deliver(session, services, message(old), old, "t:1") == []
    assert len(deliver(session, services, message(new), new, "t:2")) == 1

    assert client.delete(f"/api/projects/{project['id']}/mailbox").status_code == 204
    assert shown(client, project["id"])["address"] is None
    # What became of the messages stays until it is forgotten as ever.
    assert len(shown(client, project["id"])["messages"]) == 1
    assert deliver(session, services, message(new), new, "t:3") == []
    assert client.delete(f"/api/projects/{project['id']}/mailbox").status_code == 404
    assert client.post(f"/api/projects/{project['id']}/mailbox/address").status_code == 404
    actions = session.scalars(
        select(AuditEvent.action).where(AuditEvent.action.like("mailbox_%")).order_by(AuditEvent.id)
    ).all()
    assert actions == ["mailbox_created", "mailbox_changed", "mailbox_deleted"]
    changed = session.scalar(select(AuditEvent).where(AuditEvent.action == "mailbox_changed"))
    assert changed.detail == {"changed": ["address"], "senders": 1}


def test_a_change_names_what_changed_and_counts_the_senders(client, session):
    project = create_project(client)
    mailbox(client, project["id"], [SUPPLIER, "acme.example"])
    mailbox(client, project["id"], [SUPPLIER, "acme.example"])
    mailbox(client, project["id"], ["acme.example", "globex.example", "initech.example"])
    rows = session.scalars(select(AuditEvent).where(AuditEvent.action == "mailbox_changed")).all()
    # The same list again changes nothing.
    assert [row.detail for row in rows] == [
        {"changed": ["senders"], "senders": 3, "added": 2, "removed": 1, "folder": None}
    ]


def test_an_html_message_s_text_and_an_empty_one(client, session, services):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    html = (
        "<html><head><style>p { color: red }</style></head><body>"
        "<p>Delivery of <b>PO-7708</b> was late.</p><table><tr><td>Pallets</td><td>25</td></tr>"
        "</table><script>alert(1)</script></body></html>"
    )
    assert text_of_html(html) == "Delivery of PO-7708 was late.\n\nPallets 25"
    deliver(session, services, message(address, body=None, html=html), address, "t:1")
    [document] = documents_of(session, project["id"])
    assert (
        services.storage.get(document.storage_key)
        .decode()
        .endswith("\nDelivery of PO-7708 was late.\n\nPallets 25\n")
    )

    [empty] = deliver(
        session, services, message(address, subject="Nothing", body=" \n"), address, "t:2"
    )
    assert (empty.outcome, empty.kept, empty.reason) == ("empty", 0, NOTHING_IN_IT)


def test_files_an_upload_rejects_are_listed(client, session, services, sample_pdf, monkeypatch):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    monkeypatch.setattr(get_settings(), "max_upload_files", 3)
    raw = message(
        address,
        attachments=(
            ("archive.zip", b"PK\x03\x04", "application/zip"),
            ("C:\\Scans\\INV-7.pdf", sample_pdf.read_bytes(), "application/pdf"),
            ("fake.pdf", b"not a pdf at all", "application/pdf"),
            ("late.csv", b"a,b\n1,2\n", "text/csv"),
        ),
    )
    [record] = deliver(session, services, raw, address)
    assert record.outcome == "kept"
    assert [d.filename for d in documents_of(session, project["id"])] == [
        "Invoice INV-7 for July.txt",
        "INV-7.pdf",
    ]
    assert [r["filename"] for r in record.rejected] == ["archive.zip", "fake.pdf", "late.csv"]
    assert record.rejected[0]["reason"].startswith("Unsupported type.")
    assert record.rejected[2]["reason"] == (
        "One message can bring 3 files at most; send the rest in another message."
    )
    # The same files again are the project's already.
    [again] = deliver(session, services, raw, address, "test:2")
    assert again.outcome == "empty"
    assert again.rejected[0]["reason"].startswith("Already in this project as ")


def test_a_project_in_the_trash_or_without_room_turns_mail_away(
    client,
    session,
    services,
    make_account,
    act_as,
    plan,  # noqa: F811
):
    owner = make_account("ann@globex.example")
    confirmed(session, owner.user)
    act_as(owner)
    project = create_project(client)
    address = mailbox(client, project["id"])["address"]
    plan(documents=0)
    [refused] = deliver(
        session, services, message(address, sender=owner.user.email), address, "t:1"
    )
    assert refused.outcome == "refused"
    assert "documents" in refused.reason

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    [trashed] = deliver(
        session, services, message(address, sender=owner.user.email), address, "t:2"
    )
    assert (trashed.outcome, trashed.reason) == ("refused", PROJECT_TRASHED)


def test_the_sample_project_and_a_deployment_without_a_domain_have_no_address(
    client, session, monkeypatch
):
    project = create_project(client)
    session.execute(text("UPDATE projects SET sample = true WHERE id = :id"), {"id": project["id"]})
    session.commit()
    assert client.put(f"/api/projects/{project['id']}/mailbox", json={}).status_code == 409

    other = create_project(client, "Other")
    monkeypatch.setattr(get_settings(), "inbound_domain", "")
    assert shown(client, other["id"])["available"] is False
    refused = client.put(f"/api/projects/{other['id']}/mailbox", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"] == NO_DOMAIN


def test_the_command_hands_a_saved_message_over(client, session, tmp_path, capsys):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    path = tmp_path / "saved.eml"
    path.write_bytes(message(address))
    main(["receive-mail", str(path)])
    assert capsys.readouterr().out.splitlines() == ["local / Contracts: kept 1 document."]
    main(["receive-mail", str(path)])
    assert capsys.readouterr().out.splitlines()[0].startswith("No project took the message")
    [record] = session.scalars(select(InboundMessage)).all()
    assert record.delivery.startswith("command:")


def test_what_became_of_a_message_is_forgotten_after_a_month(client, session, services):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    deliver(session, services, message(address, subject="Old"), address, "t:1")
    deliver(session, services, message(address, subject="New"), address, "t:2")
    session.execute(
        update(InboundMessage)
        .where(InboundMessage.subject == "Old")
        .values(received_at=datetime.now(UTC) - timedelta(days=INBOUND_DAYS, hours=1))
    )
    session.commit()
    with engine.begin() as connection:
        assert prune_inbound(connection) == 1
    assert [m["subject"] for m in shown(client, project["id"])["messages"]] == ["New"]
    # The documents stay: they are the project's.
    assert len(documents_of(session, project["id"])) == 2


def test_a_message_s_parts(sample_pdf):
    parsed = parse(
        message(
            f"x@{DOMAIN}",
            attachments=(("a/b/INV-7.pdf", sample_pdf.read_bytes(), "application/pdf"),),
        )
    )
    assert parsed.sender == SUPPLIER
    assert parsed.subject == "Invoice INV-7 for July"
    assert [name for name, _ in parsed.attachments] == ["INV-7.pdf"]
    # Two From addresses are no one sender.
    two = message(f"x@{DOMAIN}").replace(
        f"From: Lena Vogt <{SUPPLIER}>".encode(), b"From: a@one.example, b@two.example"
    )
    assert parse(two).sender is None
