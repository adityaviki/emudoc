"""An answer with its question, or a whole chat, as a file to save (app/chat_export.py, `GET
/api/chats/{id}/messages/{id}/export` and `GET /api/chats/{id}/export`): its Markdown, a Word
document or a PDF, each citation a numbered note naming the passage it cites, each download a
row of the audit log."""

import io

import docx
import pdfplumber
import pytest
from pydantic_ai.messages import TextPart, ToolCallPart
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus.doctemplate import LayoutError
from sqlalchemy import update

from app.answers import answer
from app.api.chats import NOTHING_ASKED, STILL_WRITTEN
from app.chat_export import NOT_LAID_OUT, WRITTEN_BY_A_MODEL
from app.chat_export import STILL_WRITTEN as STILL_WRITTEN_IN_THE_FILE
from app.config import get_settings
from app.draft_export import source_label
from app.indexing.pipeline import process_document
from app.models import ChatMessage
from app.storage import content_disposition
from tests.conftest import FakeTurn
from tests.test_api import create_project, upload
from tests.test_audit import actions, rows
from tests.test_chats import ask, new_chat

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ENDED = "How is the agreement ended?"
PAID = "When are the fees due?"


@pytest.fixture
def chat(client, services, sample_pdf) -> dict:
    """The owner's chat about the contract: how it is ended, citing the termination passage, and
    when its fees are due, under a heading of the answer's own, citing the payment passage and
    the termination one again."""
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)

    def hit(query: str) -> int:
        found = client.get(f"/api/projects/{project['id']}/search", params={"q": query})
        return found.json()["hits"][0]["passage_id"]

    ended, paid = hit("terminate notice"), hit("pay fees invoice")
    assert ended != paid
    chat = new_chat(client, project["id"])
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "terminate notice"}, "call_1")),
        FakeTurn(TextPart(f"Either party gives ninety days' written notice [P{ended}].")),
    ]
    first = ask(client, chat["id"], ENDED)
    answer(first["answer"]["id"], services)
    services.llm.agent_turns = [
        FakeTurn(
            ToolCallPart("search", {"query": "pay fees invoice"}, "call_2"),
            ToolCallPart("search", {"query": "terminate notice"}, "call_3"),
        ),
        FakeTurn(
            TextPart(
                f"## Payment\n\nThe Client pays within 30 days [P{paid}], and either party may "
                f"end the agreement on notice [P{ended}]. A passage it never read [P999999] "
                "stays as it is written."
            )
        ),
    ]
    second = ask(client, chat["id"], PAID)
    answer(second["answer"]["id"], services)
    messages = client.get(f"/api/chats/{chat['id']}").json()["messages"]
    sources = {source["id"]: source for m in messages for source in m["sources"]}
    return {
        "id": chat["id"],
        "project_id": project["id"],
        "first": first["answer"]["id"],
        "second": second["answer"]["id"],
        "ended": source_label(sources[ended]),
        "paid": source_label(sources[paid]),
    }


def export(client, chat: dict, message_id: int | None = None, **params):
    if message_id is None:
        return client.get(f"/api/chats/{chat['id']}/export", params=params)
    return client.get(f"/api/chats/{chat['id']}/messages/{message_id}/export", params=params)


def exported(session) -> list[tuple[str, dict]]:
    return [
        (action, detail)
        for action, detail in actions(session)
        if action in ("answer_exported", "chat_exported")
    ]


def test_an_answer_downloads_as_its_markdown_headed_by_its_question(client, session, account, chat):
    response = export(client, chat, chat["first"], format="md")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    # Named by its question, without what a file's name cannot hold.
    assert response.headers["content-disposition"] == content_disposition(
        "How is the agreement ended.md"
    )
    lines = response.text.split("\n\n")
    assert lines[0] == f"# {ENDED}"
    assert lines[1].startswith(f"*Asked by {account.user.name} in Contracts on ")
    assert lines[2:] == [
        "Either party gives ninety days' written notice[^1].",
        f"*{WRITTEN_BY_A_MODEL}*",
        f"[^1]: {chat['ended']}\n",
    ]
    # Recorded by the chat and the answer, never what they say.
    assert exported(session) == [("answer_exported", {"answer": chat["first"], "format": "md"})]
    targets = [(row.target_type, row.target_id) for row in rows(session) if "export" in row.action]
    assert targets == [("chat", chat["id"])]


def test_an_answer_downloads_as_a_word_document_and_a_pdf(client, session, chat):
    response = export(client, chat, chat["second"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == DOCX
    assert response.headers["content-disposition"] == content_disposition(
        "When are the fees due.docx"
    )
    document = docx.Document(io.BytesIO(response.content))
    paragraphs = [p for p in document.paragraphs if p.text]
    assert (paragraphs[0].text, paragraphs[0].style.name) == (PAID, "Heading 1")
    assert all(run.italic for run in paragraphs[1].runs)
    # The answer's own heading below the question's.
    assert (paragraphs[2].text, paragraphs[2].style.name) == ("Payment", "Heading 3")
    claim = paragraphs[3]
    assert claim.text.startswith("The Client pays within 30 days1, and either party may end")
    assert "on notice2. A passage it never read [P999999] stays" in claim.text
    assert [run.text for run in claim.runs if run.font.superscript] == ["1", "2"]
    assert [p.text for p in paragraphs[-4:]] == [
        WRITTEN_BY_A_MODEL,
        "Sources",
        f"1.\t{chat['paid']}",
        f"2.\t{chat['ended']}",
    ]
    assert document.core_properties.title == PAID

    response = export(client, chat, chat["second"], format="pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages)
        assert pdf.metadata["Title"] == PAID
    assert text.startswith(f"{PAID}\nAsked by ")
    assert "\nThe Client pays within 30 days" in text and "[P999999] stays" in text
    assert f"{WRITTEN_BY_A_MODEL}\nSources\n1. {chat['paid']}\n2. {chat['ended']}" in text
    assert [detail["format"] for _, detail in exported(session)] == ["docx", "pdf"]


def test_a_chat_downloads_whole_its_citations_numbered_across_it(client, session, chat):
    response = export(client, chat, format="md")
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == content_disposition(
        "How is the agreement ended.md"
    )
    lines = response.text.split("\n\n")
    assert lines[0] == f"# {ENDED}"
    assert lines[1].startswith("*Asked by ") and " in Contracts on " in lines[1]
    assert lines[2:] == [
        f"## {ENDED}",
        "Either party gives ninety days' written notice[^1].",
        f"## {PAID}",
        # The answer's heading below the question's, the termination passage numbered once.
        "#### Payment",
        "The Client pays within 30 days[^2], and either party may end the agreement on "
        "notice[^1]. A passage it never read [P999999] stays as it is written.",
        f"*{WRITTEN_BY_A_MODEL}*",
        f"[^1]: {chat['ended']}\n[^2]: {chat['paid']}\n",
    ]
    assert exported(session) == [("chat_exported", {"answers": 2, "format": "md"})]

    document = docx.Document(io.BytesIO(export(client, chat).content))
    headings = [(p.text, p.style.name) for p in document.paragraphs if p.style.name != "Normal"]
    assert headings == [
        (ENDED, "Heading 1"),
        (ENDED, "Heading 2"),
        (PAID, "Heading 2"),
        ("Payment", "Heading 4"),
    ]


def test_a_renamed_chat_is_headed_and_named_by_its_title(client, chat):
    client.patch(f"/api/chats/{chat['id']}", json={"title": "Ending: the MSA"})
    response = export(client, chat, format="pdf")
    assert response.headers["content-disposition"] == content_disposition("Ending the MSA.pdf")
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        assert pdf.pages[0].extract_text().startswith("Ending: the MSA\n")


def test_what_a_member_wrote_reads_as_they_wrote_it(client, services, chat):
    question = "Is **this** <b>bold</b> or # not & a [link](x)?\nSecond line\x01"
    services.llm.agent_turns = [FakeTurn(TextPart("It is as written."))]
    asked = ask(client, chat["id"], question)
    answer(asked["answer"]["id"], services)

    text = export(client, chat, asked["answer"]["id"], format="md").text
    assert text.startswith(
        "# Is \\*\\*this\\*\\* \\<b\\>bold\\</b\\> or \\# not & a \\[link\\](x)? Second line\n"
    )
    document = docx.Document(io.BytesIO(export(client, chat, asked["answer"]["id"]).content))
    # A heading of Word keeps the question's line break, and leaves out what XML cannot hold.
    assert document.paragraphs[0].text == question.removesuffix("\x01")
    assert document.core_properties.title.startswith("Is **this** <b>bold</b>")


def test_an_answer_that_did_not_end_says_how_it_ended(client, services, chat):
    asked = ask(client, chat["id"], "Who signed it?")
    # Still being written: the answer waits for its end, and the chat says it was not done.
    refused = export(client, chat, asked["answer"]["id"])
    assert (refused.status_code, refused.json()["detail"]) == (409, STILL_WRITTEN)
    text = export(client, chat, format="md").text
    assert f"## Who signed it?\n\n*{STILL_WRITTEN_IN_THE_FILE}*\n\n" in text

    stopped = client.post(f"/api/chats/{chat['id']}/messages/{asked['answer']['id']}/stop")
    assert stopped.json()["status"] == "stopped"
    text = export(client, chat, asked["answer"]["id"], format="md").text
    assert text.split("\n\n")[2:] == ["*Stopped.*", f"*{WRITTEN_BY_A_MODEL}*\n"]


def test_a_pdf_that_cannot_be_laid_out_says_so(client, session, chat, monkeypatch):
    def too_large(*args, **kwargs):
        raise LayoutError("Flowable too large")

    monkeypatch.setattr(SimpleDocTemplate, "build", too_large)
    refused = export(client, chat, chat["first"], format="pdf")
    assert (refused.status_code, refused.json()["detail"]) == (422, NOT_LAID_OUT.format("answer"))
    refused = export(client, chat, format="pdf")
    assert refused.json()["detail"].startswith("This chat could not be laid out as a PDF")
    assert exported(session) == []


@pytest.mark.usefixtures("one_minute")
def test_a_viewer_reads_a_shared_chat_and_downloads_it_within_the_rate(
    client, account, chat, monkeypatch, make_account, act_as
):
    client.patch(f"/api/chats/{chat['id']}", json={"visibility": "project"})
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert export(client, chat, format="md").status_code == 200
    monkeypatch.setattr(get_settings(), "rate_exports", 1)
    refused = export(client, chat, chat["first"], format="md")
    assert refused.status_code == 429
    assert "downloads of a chat or a file of My files" in refused.json()["detail"]


def test_a_chat_with_nothing_asked_has_nothing_to_download(client, session, chat):
    empty = new_chat(client, chat["project_id"])
    refused = client.get(f"/api/chats/{empty['id']}/export")
    assert (refused.status_code, refused.json()["detail"]) == (409, NOTHING_ASKED)
    assert exported(session) == []


def test_the_days_are_the_downloading_members(client, session, chat):
    # Asked late on the 24th in UTC, which is the 25th in Auckland.
    late = "2026-10-24 23:30+00"
    session.execute(
        update(ChatMessage).where(ChatMessage.chat_id == chat["id"]).values(created_at=late)
    )
    session.execute(
        update(ChatMessage)
        .where(ChatMessage.id == chat["first"])
        .values(created_at="2026-10-20 09:00+00")
    )
    session.commit()
    byline = export(client, chat, chat["second"], format="md").text.split("\n\n")[1]
    assert byline.endswith(" on 24 October 2026.*")
    assert client.patch("/api/auth/profile", json={"time_zone": "Pacific/Auckland"}).is_success
    byline = export(client, chat, chat["second"], format="md").text.split("\n\n")[1]
    assert byline.endswith(" on 25 October 2026.*")
    byline = export(client, chat, format="md").text.split("\n\n")[1]
    assert byline.endswith(" from 20 October 2026 to 25 October 2026.*")
