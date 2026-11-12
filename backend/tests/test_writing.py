"""The agent writes the asker's My files (app/agent/writing.py, app/word_files.py): asked in a chat,
it writes a Word document into the member's own files, reads and edits one by its paragraphs'
numbers keeping what it does not touch, and copies a document of the project's library to edit as
a private copy. Each write is a version the member alone sees; an answer started over writes its
file again rather than a second one, and a try no longer the answer's writes nothing."""

import io

import pytest
from docx import Document as WordDocument
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from sqlalchemy import func, select

from app.agent.loop import WRITING
from app.agent.tools import ToolContext, Writing
from app.agent.writing import (
    COPIED,
    STOPPED,
    WRITTEN,
    CopyToMyFilesInput,
    CreateDocumentInput,
    EditDocumentInput,
    ReadFileInput,
    copy_to_my_files,
    create_document,
    edit_document,
    read_file,
)
from app.answers import answer, offers_my_files
from app.indexing.pipeline import process_document
from app.models import (
    ChatMessage,
    Membership,
    MyFile,
    MyFileVersion,
    Organization,
    Passage,
    Project,
)
from app.word_files import Change, EditRefused, edited, reading
from tests.conftest import FakeTurn, data_of
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_my_files import kept, shown, view, word, workbook
from tests.test_offered_drafts import asked, the_answer
from tests.test_regenerate import regenerate


@pytest.fixture
def project(client) -> dict:
    return create_project(client)


def letter() -> bytes:
    """A letter with a heading, a paragraph of mixed formatting, a list, a table and a header."""
    document = WordDocument()
    document.sections[0].header.paragraphs[0].text = "Acme Freight Ltd"
    document.add_heading("Invoice INV-1", 1)
    paragraph = document.add_paragraph()
    paragraph.add_run("Total due: ").bold = True
    paragraph.add_run("1,255.00 EUR to Acme Freight")
    document.add_paragraph("Pay within 45 days.")
    document.add_paragraph("Bolts", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    for (row, column), words in {(0, 0): "Item", (0, 1): "Amount", (1, 0): "Bolts"}.items():
        table.cell(row, column).text = words
    table.cell(1, 1).paragraphs[0].add_run("1,255.").bold = True
    table.cell(1, 1).paragraphs[0].add_run("00 Acme Freight")
    document.add_paragraph("Yours sincerely,")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def context(session, services, project_id: int, user_id: int, **writing) -> ToolContext:
    """A tool call's context for an answer that may write the member's My files."""
    return ToolContext(
        session,
        services.embedder,
        project_id,
        writing=Writing(services, user_id, **writing),
    )


def opened(services, session, file_id: int, number: int | None = None):
    """A version of a file of My files as python-docx reads it, the newest by default."""
    session.expire_all()
    query = select(MyFileVersion).where(MyFileVersion.file_id == file_id)
    query = query.where(MyFileVersion.number == number) if number else query
    version = session.scalars(query.order_by(MyFileVersion.number.desc()).limit(1)).one()
    return WordDocument(io.BytesIO(services.storage.get(version.storage_key)))


def results(services) -> list[str]:
    """What each tool call of the answer returned to the model, inside its fence."""
    # Each turn is sent the conversation so far: the last, every result.
    return [
        data_of(part.content)
        for message in services.llm.agent_requests[-1]
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]


def files(session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(MyFile))


def test_an_answer_writes_a_word_document_into_the_askers_own_files(
    client, session, services, account, project, make_document, sample_docx
):
    policy = make_document(sample_docx, project=session.get(Project, project["id"]))
    session.commit()
    process_document(policy.id, services)
    passage = session.scalar(
        select(Passage).where(Passage.document_id == policy.id, Passage.text.contains("14 days"))
    )
    markdown = f"# Booking flights\n\nBook **14 days** ahead [P{passage.id}]."
    chat_id, message_id = asked(
        client,
        services,
        project["id"],
        "Write me a note on booking flights as a Word document.",
        FakeTurn(ToolCallPart("read", {"passage_id": passage.id}, "read-1")),
        FakeTurn(
            ToolCallPart(
                "create_document", {"title": "Booking flights", "markdown": markdown}, "write-1"
            )
        ),
        FakeTurn(TextPart("I wrote a note on booking flights into your files.")),
    )

    # The agent was offered the file tools and told when to use them.
    assert {"list_my_files", "read_file", "create_document", "edit_document"} <= set(
        services.llm.agent_tools[0]
    )
    assert WRITING in services.llm.agent_prompts[0]
    file = session.scalars(select(MyFile)).one()
    assert (file.name, file.user_id, file.answer_id, file.answer_ordinal) == (
        "Booking flights.docx",
        account.user.id,
        message_id,
        0,
    )
    assert "Kept Booking flights.docx in the member's My files" in results(services)[-1]

    # The step names the file and opens it, and keeps none of its text, which is the file's.
    step = the_answer(client, chat_id)["steps"][-1]
    assert step["input"] == {"title": "Booking flights"}
    assert step["file"] == {"id": file.id, "name": "Booking flights.docx", "version": 1}
    detail = shown(client, file.id)
    assert (detail["latest"]["by_model"], detail["latest"]["scan"], detail["latest"]["note"]) == (
        True,
        "skipped",
        WRITTEN,
    )

    # A real Word document, its citation a numbered note naming the passage.
    session.refresh(policy)
    document = opened(services, session, file.id)
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert texts[:2] == ["Booking flights", "Book 14 days ahead1."]
    assert document.paragraphs[1].runs[1].bold
    assert texts[-2] == "Sources"
    assert texts[-1].startswith(f"1.\t{policy.display_title}")
    assert (
        "my_file_added",
        {
            "kind": "docx",
            "by_model": True,
            "size_bytes": detail["latest"]["size_bytes"],
            "chat": chat_id,
        },
    ) in actions(session)


def test_the_agent_reads_a_file_by_numbered_paragraphs_and_its_tables(
    client, session, services, account, project
):
    uploaded = kept(client, project["id"], "Invoice.docx", letter())
    tools = context(session, services, project["id"], account.user.id)

    # A member's upload is read once its scan has passed it.
    unscanned = read_file(tools, ReadFileInput(file_id=uploaded["id"]))
    assert "still being scanned for malware" in unscanned
    view(services)
    read = read_file(tools, ReadFileInput(file_id=uploaded["id"]))
    assert read.splitlines() == [
        f"file {uploaded['id']} · Invoice.docx · version 1 (the newest) · uploaded by the member",
        "5 paragraphs, 1 table.",
        "[1] (Heading 1) Invoice INV-1",
        "[2] (Normal) Total due: 1,255.00 EUR to Acme Freight",
        "[3] (Normal) Pay within 45 days.",
        "[4] (List Bullet) Bolts",
        "Table 1, after paragraph 4, 2 rows:",
        "| Item | Amount |",
        "| Bolts | 1,255.00 Acme Freight |",
        "[5] (Normal) Yours sincerely,",
    ]
    # Another member's file, or one that is none, is not the agent's to read.
    assert "has no file" in read_file(tools, ReadFileInput(file_id=uploaded["id"] + 1))
    others = context(session, services, project["id"], account.user.id + 1000)
    assert "has no file" in read_file(others, ReadFileInput(file_id=uploaded["id"]))
    # A workbook waits for the next tools.
    sheet = kept(client, project["id"], "Prices.xlsx", workbook(("Item", "Price"), ("Bolts", 5)))
    assert "Excel workbook" in read_file(tools, ReadFileInput(file_id=sheet["id"]))


def test_an_edit_keeps_a_version_and_whatever_it_does_not_touch(
    client, session, services, account, project
):
    uploaded = kept(client, project["id"], "Invoice.docx", letter())
    view(services)
    tools = context(session, services, project["id"], account.user.id)
    changes = [
        {"op": "replace", "paragraph": 3, "text": "Pay within **30** days."},
        {"op": "insert", "paragraph": 1, "text": "Our reference: PO-7701"},
        {"op": "insert", "paragraph": 4, "text": "Nuts", "style": "List Bullet"},
        {"op": "find_replace", "find": "Acme Freight", "text": "Acme Ltd"},
    ]
    done = edit_document(
        tools,
        EditDocumentInput.model_validate(
            {"file_id": uploaded["id"], "version": 1, "changes": changes, "note": "30 days."}
        ),
    )
    assert done.startswith(f"Kept version 2 of Invoice.docx (file {uploaded['id']}): replaced")
    assert tools.wrote == {"id": uploaded["id"], "name": "Invoice.docx", "version": 2}

    document = opened(services, session, uploaded["id"])
    paragraphs = document.paragraphs
    assert [(p.style.name, p.text) for p in paragraphs] == [
        ("Heading 1", "Invoice INV-1"),
        # After a heading, a paragraph takes the look of the body text below it.
        ("Normal", "Our reference: PO-7701"),
        ("Normal", "Total due: 1,255.00 EUR to Acme Ltd"),
        ("Normal", "Pay within 30 days."),
        ("List Bullet", "Bolts"),
        ("List Bullet", "Nuts"),
        ("Normal", "Yours sincerely,"),
    ]
    # What the edit did not touch keeps its formatting: the bold label, and the bold figure of
    # the table, whose words were replaced across its two runs.
    assert [(run.text, run.bold) for run in paragraphs[2].runs] == [
        ("Total due: ", True),
        ("1,255.00 EUR to Acme Ltd", None),
    ]
    assert [run.text for run in paragraphs[3].runs] == ["Pay within ", "30", " days."]
    assert paragraphs[3].runs[1].bold
    cell = document.tables[0].cell(1, 1).paragraphs[0]
    assert [(run.text, run.bold) for run in cell.runs] == [("1,255.", True), ("00 Acme Ltd", None)]
    assert document.sections[0].header.paragraphs[0].text == "Acme Ltd Ltd"
    latest = shown(client, uploaded["id"])["latest"]
    assert (latest["number"], latest["by_model"], latest["note"]) == (2, True, "30 days.")
    assert "my_file_version_added" in [action for action, _ in actions(session)]

    # An edit of the version before is refused, as is one that names no paragraph the file has,
    # with nothing written.
    stale = edit_document(
        tools,
        EditDocumentInput.model_validate(
            {"file_id": uploaded["id"], "version": 1, "changes": changes[:1], "note": "Again."}
        ),
    )
    assert "Version 1 is not the newest of Invoice.docx: version 2 is." in stale
    wrong = edit_document(
        tools,
        EditDocumentInput.model_validate(
            {
                "file_id": uploaded["id"],
                "version": 2,
                "changes": [{"op": "delete", "paragraph": 12}],
                "note": "Shorter.",
            }
        ),
    )
    assert wrong.startswith("Nothing was changed: Change 1 (delete) must name a paragraph")
    assert shown(client, uploaded["id"])["latest"]["number"] == 2


def test_an_edit_is_made_whole_or_not_at_all():
    data = letter()
    refused = [
        [Change("replace", 2, text="x"), Change("delete", 1, through=2)],
        [Change("insert", 0, text="x", style="Heading 9 of nothing")],
        [Change("find_replace", find="Nowhere to be found", text="x")],
        [Change("find_replace", find="Acme", text="two\nlines")],
        [Change("replace", 2)],
    ]
    reasons = []
    for changes in refused:
        with pytest.raises(EditRefused) as refusal:
            edited(data, changes)
        reasons.append(str(refusal.value))
    assert reasons[0] == "Paragraph 2 is both replaced and deleted."
    assert reasons[1].startswith(
        'Change 1 (insert): the document has no paragraph style "Heading 9'
    )
    # The styles the document uses come first among those it names.
    assert "Its styles: Heading 1, Normal, List Bullet," in reasons[1]
    assert reasons[2].startswith('"Nowhere to be found" is nowhere in the document')
    assert "one line" in reasons[3]
    assert reasons[4] == "Change 1 (replace) needs the text."
    # Deleting a run of paragraphs, and inserting at the start, by the numbers read.
    changed, done = edited(
        data, [Change("delete", 2, through=3), Change("insert", 0, text="Draft\nSecond line")]
    )
    assert done == "deleted paragraphs 2-3; inserted 2 paragraphs at the start"
    assert reading(changed).splitlines()[1:4] == [
        # Before a heading, a paragraph takes the look of the body text below it.
        "[1] (Normal) Draft",
        "[2] (Normal) Second line",
        "[3] (Heading 1) Invoice INV-1",
    ]


def test_a_long_file_is_read_in_parts():
    document = WordDocument()
    for number in range(1, 401):
        document.add_paragraph(f"Clause {number}: " + "words " * 10)
    out = io.BytesIO()
    document.save(out)
    first = reading(out.getvalue())
    assert first.splitlines()[0] == "400 paragraphs, 0 tables."
    last = first.splitlines()[-1]
    assert last.startswith("... Call read_file again with from_paragraph=")
    start = int(last.rsplit("=", 1)[1].split(" ")[0])
    rest = reading(out.getvalue(), start)
    assert rest.splitlines()[1].startswith(f"[{start}] (Normal) Clause {start}:")


def test_the_agent_copies_a_project_document_to_edit_it_as_a_private_copy(
    client, session, services, account, project, make_document, sample_docx, sample_pdf
):
    held = session.get(Project, project["id"])
    policy = make_document(sample_docx, project=held)
    scanned = make_document(sample_pdf, project=held)
    session.commit()
    tools = context(session, services, project["id"], account.user.id)
    # Not before its scan has passed it.
    assert "still being scanned" in copy_to_my_files(
        tools, CopyToMyFilesInput(document_id=policy.id)
    )
    process_document(policy.id, services)
    process_document(scanned.id, services)
    # Each call has a session of its own, which reads them scanned.
    session.expire_all()

    copied = copy_to_my_files(tools, CopyToMyFilesInput(document_id=policy.id))
    file = session.scalars(select(MyFile)).one()
    # By the title the indexing read, its file's name kept.
    assert copied.startswith(
        f"Copied {policy.display_title} into the member's My files as file {file.id}, policy.docx."
    )
    assert (file.name, file.origin_document_id, file.answer_id) == ("policy.docx", policy.id, None)
    detail = shown(client, file.id)
    assert (detail["origin_name"], detail["latest"]["note"]) == ("policy.docx", COPIED)
    assert opened(services, session, file.id).paragraphs[0].text == "Travel Policy"
    # Only a Word document, of this project and out of its trash.
    assert "not a Word document" in copy_to_my_files(
        tools, CopyToMyFilesInput(document_id=scanned.id)
    )
    assert "No document with id" in copy_to_my_files(
        tools, CopyToMyFilesInput(document_id=scanned.id + 100)
    )

    # Edited, the copy goes back into the library as the document's next version.
    tools = context(session, services, project["id"], account.user.id)
    edit_document(
        tools,
        EditDocumentInput.model_validate(
            {
                "file_id": file.id,
                "version": 1,
                "changes": [{"op": "find_replace", "find": "14 days", "text": "21 days"}],
                "note": "Three weeks ahead.",
            }
        ),
    )
    back = client.post(f"/api/my-files/{file.id}/copy", json={"as_version": True})
    assert back.status_code == 201, back.text
    assert back.json()["version"] == 2


def test_an_answer_written_again_writes_its_file_again_as_a_version(
    client, session, services, project
):
    def writing(title: str) -> FakeTurn:
        return FakeTurn(
            ToolCallPart("create_document", {"title": title, "markdown": f"# {title}"}, "w")
        )

    chat_id, message_id = asked(
        client,
        services,
        project["id"],
        "Write me a memo.",
        writing("Memo"),
        FakeTurn(TextPart("I wrote the memo.")),
    )
    services.llm.agent_turns += [writing("Memo, again"), FakeTurn(TextPart("Written again."))]
    response = regenerate(client, chat_id, message_id)
    assert response.status_code == 200, response.text
    answer(message_id, services, attempt=2)

    # One file, both writings its versions, the name the first gave it.
    assert files(session) == 1
    file = session.scalars(select(MyFile)).one()
    assert shown(client, file.id)["latest"]["number"] == 2
    assert opened(services, session, file.id).paragraphs[0].text == "Memo, again"
    assert the_answer(client, chat_id)["steps"][0]["file"] == {
        "id": file.id,
        "name": "Memo.docx",
        "version": 2,
    }


def test_a_try_that_is_no_longer_the_answers_writes_nothing(client, session, services, project):
    chat_id, message_id = asked(
        client, services, project["id"], "What is this?", FakeTurn(TextPart("A project."))
    )
    message = session.get(ChatMessage, message_id)
    tools = context(
        session,
        services,
        project["id"],
        session.get(ChatMessage, message_id - 1).chat.user_id,
        chat_id=chat_id,
        message_id=message_id,
        attempt=message.attempt,
    )
    written = create_document(tools, CreateDocumentInput(title="Memo", markdown="# Memo"))
    # Settled, the answer takes no more files.
    assert (written, files(session)) == (STOPPED, 0)


def test_the_file_tools_are_offered_to_whoever_may_keep_a_file(session, account):
    organization = session.get(Organization, account.organization.id)
    assert offers_my_files(session, organization, account.user.id)
    # A viewer keeps files of their own too.
    membership = session.scalars(
        select(Membership).where(Membership.user_id == account.user.id)
    ).one()
    membership.role = "viewer"
    session.flush()
    assert offers_my_files(session, organization, account.user.id)
    # Not someone of another organisation.
    assert not offers_my_files(session, organization, account.user.id + 1000)


def test_my_files_refusals_reach_the_agent(
    client, session, services, account, project, monkeypatch
):
    monkeypatch.setattr("app.my_files.MAX_FILES", 1)
    tools = context(session, services, project["id"], account.user.id)
    assert create_document(tools, CreateDocumentInput(title="One", markdown="One")).startswith(
        "Kept One.docx"
    )
    refused = create_document(tools, CreateDocumentInput(title="Two", markdown="Two"))
    assert "as many as it keeps" in refused
    assert files(session) == 1


def test_a_file_the_member_uploaded_is_read_through_the_answer(client, session, services, project):
    uploaded = kept(client, project["id"], "Letter.docx", word("Dear Acme,", "Pay us."))
    view(services)
    asked(
        client,
        services,
        project["id"],
        "Read my letter.",
        FakeTurn(ToolCallPart("list_my_files", {}, "list-1")),
        FakeTurn(ToolCallPart("read_file", {"file_id": uploaded["id"]}, "read-1")),
        FakeTurn(TextPart("It asks Acme to pay.")),
    )
    listed, read = results(services)
    assert listed.splitlines()[1].startswith(
        f"file {uploaded['id']} · Letter.docx · Word document · version 1 · changed "
    )
    assert "[2] (Normal) Pay us." in read
