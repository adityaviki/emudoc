"""Drafts an answer offers (`create_draft` in app/agent/tools.py, `write_offered_draft` in
app/api/drafts.py): asked in the chat for something to send, the agent offers a draft of its kind
about the documents it found, and the member's press has it written into the project's drafts,
once, counting no question beside the one asked. Nothing is written before the press, so a
document cannot make drafts, and a member who could not have one written is offered none."""

from datetime import UTC, datetime

from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import func, select

from app.agent.loop import DRAFTING
from app.answers import answer
from app.config import get_settings
from app.flags import DRAFTS, FLAGS
from app.jobs import WRITE_DRAFT
from app.models import ChatMessage, Draft, Job, Organization
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_chats import ask, follow, new_chat, questions_counted, try_again
from tests.test_check_runs import invoices
from tests.test_drafts import shown, write
from tests.test_templates import turn_on

OFFER = {"kind": "dispute_letter", "instructions": "Ask Acme for a credit note of 5 EUR."}


def offering(project_id: int, document_id: int, call: str = "offer-1") -> FakeTurn:
    """A turn that offers the dispute letter of OFFER about the document."""
    arguments = {**OFFER, "document_ids": [document_id]}
    return FakeTurn(ToolCallPart("create_draft", arguments, call))


def asked(client, services, project_id: int, question: str, *turns: FakeTurn) -> tuple[int, int]:
    """A chat asked `question`, answered by the turns as the worker would: its id and the
    answer's."""
    services.llm.agent_turns.extend(turns)
    chat = new_chat(client, project_id)
    message_id = ask(client, chat["id"], question)["answer"]["id"]
    answer(message_id, services)
    return chat["id"], message_id


def the_answer(client, chat_id: int) -> dict:
    response = client.get(f"/api/chats/{chat_id}")
    assert response.status_code == 200, response.text
    return response.json()["messages"][-1]


def press(client, chat_id: int, message_id: int):
    return client.post(f"/api/chats/{chat_id}/messages/{message_id}/draft")


def drafts(session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(Draft))


def draft_jobs(session) -> int:
    return session.scalar(select(func.count()).select_from(Job).where(Job.kind == WRITE_DRAFT))


def test_an_answer_offers_a_draft_the_member_has_written_with_a_press(
    client, session, services, account, monkeypatch
):
    turn_on(monkeypatch, DRAFTS)
    project = create_project(client)
    docs = invoices(session, project["id"])
    chat_id, message_id = asked(
        client,
        services,
        project["id"],
        "Write a dispute letter to Acme about INV-1.",
        offering(project["id"], docs["INV-1"]),
        FakeTurn(TextPart("I offered a dispute letter asking Acme for a credit note.")),
    )

    # The agent was offered the tool and told when to use it.
    assert "create_draft" in services.llm.agent_tools[0]
    assert DRAFTING in services.llm.agent_prompts[0]
    documents = [{"id": docs["INV-1"], "title": "INV-1.md"}]
    events = follow(client, chat_id, message_id)
    assert {"type": "draft", **OFFER, "documents": documents} in events
    offered = the_answer(client, chat_id)
    assert offered["status"] == "done"
    assert offered["draft"] == {**OFFER, "documents": documents, "written": False, "draft_id": None}
    # Offered, nothing is written yet: no draft, no job, and the one question asked.
    assert (drafts(session), draft_jobs(session), questions_counted(session)) == (0, 0, 1)

    pressed = press(client, chat_id, message_id)
    assert pressed.status_code == 201, pressed.text
    draft = pressed.json()
    assert (draft["kind"], draft["instructions"], draft["status"]) == (
        "dispute_letter",
        OFFER["instructions"],
        "queued",
    )
    assert draft["documents"] == [{**documents[0], "gone": False}]
    assert draft["created_by"] == account.user.name
    # Its question was counted when asked: the draft counts none of its own.
    assert questions_counted(session) == 1
    assert the_answer(client, chat_id)["draft"] == {
        **OFFER,
        "documents": documents,
        "written": True,
        "draft_id": draft["id"],
    }
    assert ("draft_created", {"kind": "dispute_letter", "documents": 1, "chat": chat_id}) in (
        actions(session)
    )

    # A second press, a double click, answers the same draft and writes no other.
    again = press(client, chat_id, message_id)
    assert (again.status_code, again.json()["id"]) == (200, draft["id"])
    assert (drafts(session), draft_jobs(session)) == (1, 1)

    services.llm.agent_turns.append(FakeTurn(TextPart("We dispute INV-1. [Your name]")))
    write(services)
    written = shown(client, draft["id"])
    assert written["status"] == "done"
    assert written["latest"]["text"] == "We dispute INV-1. [Your name]"
    # The writing is told what the member asked of it, which the agent put in the instructions.
    assert OFFER["instructions"] in services.llm.agent_requests[-1][0].parts[0].content

    # Deleted, the draft is gone from the answer, and the offer is not written again.
    assert client.delete(f"/api/drafts/{draft['id']}").status_code == 204
    gone = the_answer(client, chat_id)["draft"]
    assert (gone["written"], gone["draft_id"]) == (True, None)
    refused = press(client, chat_id, message_id)
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("The draft written from this answer was deleted.")


def test_an_answer_offers_one_draft_of_documents_in_the_project(
    client, session, services, monkeypatch
):
    turn_on(monkeypatch, DRAFTS)
    project = create_project(client)
    docs = invoices(session, project["id"])
    chat_id, message_id = asked(
        client,
        services,
        project["id"],
        "Write a dispute letter about INV-1.",
        offering(project["id"], 999_999, "unknown"),
        offering(project["id"], docs["INV-1"]),
        FakeTurn(
            ToolCallPart(
                "create_draft",
                {"kind": "report", "document_ids": [docs["INV-2"]]},
                "offer-2",
            )
        ),
        FakeTurn(TextPart("A dispute letter is offered.")),
    )
    steps = the_answer(client, chat_id)["steps"]
    assert [step["name"] for step in steps] == ["create_draft"] * 3
    assert steps[0]["summary"].startswith("No document of this project has the id 999999.")
    assert steps[1]["summary"].startswith("The member is offered a dispute letter about INV-1.md")
    assert steps[2]["summary"].startswith("This answer offers a draft already")
    events = follow(client, chat_id, message_id)
    assert [event["kind"] for event in events if event["type"] == "draft"] == ["dispute_letter"]
    assert the_answer(client, chat_id)["draft"]["kind"] == "dispute_letter"


def test_a_member_who_could_not_have_a_draft_written_is_offered_none(
    client, session, services, account, make_account, act_as, monkeypatch
):
    project = create_project(client)
    docs = invoices(session, project["id"])

    def offers() -> tuple[bool, dict | None]:
        """Whether the turn was offered the tool, and what the answer offers when the model
        calls it all the same."""
        chat_id, _ = asked(
            client,
            services,
            project["id"],
            "Write a dispute letter about INV-1.",
            offering(project["id"], docs["INV-1"]),
            FakeTurn(TextPart("Here it is.")),
        )
        tools, prompt = services.llm.agent_tools[-2], services.llm.agent_prompts[-2]
        assert ("create_draft" in tools) is (DRAFTING in prompt)
        shown = the_answer(client, chat_id)
        if shown["draft"] is None:
            assert shown["steps"][0]["summary"] == "Unknown tool 'create_draft'."
        return "create_draft" in tools, shown["draft"]

    # Drafts off for the organisation.
    assert offers() == (False, None)
    turn_on(monkeypatch, DRAFTS)
    assert offers()[0] is True

    # A viewer, who may not have one written, nor press one an answer offers.
    viewer = make_account("vi@example.com", account.organization, role="viewer")
    act_as(viewer)
    assert offers() == (False, None)
    chat_id = client.get(f"/api/projects/{project['id']}/chats").json()[0]["id"]
    message = session.scalar(
        select(ChatMessage).where(ChatMessage.chat_id == chat_id, ChatMessage.role == "assistant")
    )
    message.draft_offer = {**OFFER, "documents": []}
    session.commit()
    refused = press(client, chat_id, message.id)
    assert refused.status_code == 403, refused.text
    client.app.dependency_overrides.clear()

    # An organisation set to be deleted adds nothing to its projects, drafts among it.
    organization = session.get(Organization, account.organization.id)
    organization.deletion_due_at = datetime.now(UTC)
    session.commit()
    assert offers() == (False, None)


def test_an_offer_waits_for_its_answer_and_goes_with_a_try(client, session, services, monkeypatch):
    turn_on(monkeypatch, DRAFTS)
    project = create_project(client)
    docs = invoices(session, project["id"])
    chat = new_chat(client, project["id"])
    message_id = ask(client, chat["id"], "Write a dispute letter about INV-1.")["answer"]["id"]
    # Being written, the answer has no offer to press yet.
    streaming = press(client, chat["id"], message_id)
    assert streaming.status_code == 409
    assert streaming.json()["detail"].startswith("This chat is still being answered.")

    # An answer that failed after its offer keeps it, to press or to try again.
    services.llm.agent_turns.extend(
        [offering(project["id"], docs["INV-1"]), FakeTurn(finish_reason="content_filter")]
    )
    answer(message_id, services)
    failed = the_answer(client, chat["id"])
    assert (failed["status"], failed["draft"]["kind"]) == ("failed", "dispute_letter")
    # Past the agent's runs in flight, the press waits as a question would.
    monkeypatch.setattr(get_settings(), "max_answers", 0)
    assert press(client, chat["id"], message_id).status_code == 429
    monkeypatch.setattr(get_settings(), "max_answers", 20)
    written = press(client, chat["id"], message_id).json()

    # Tried again, the answer is written anew, its offer and its draft's link with it; the draft
    # stays in the project's drafts.
    assert try_again(client, chat["id"], message_id).status_code == 200
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing to offer this time.")))
    answer(message_id, services, attempt=2)
    tried = the_answer(client, chat["id"])
    assert (tried["status"], tried["draft"]) == ("done", None)
    assert session.get(Draft, written["id"]) is not None
    no_offer = press(client, chat["id"], message_id)
    assert no_offer.status_code == 404
    assert no_offer.json()["detail"].startswith("This answer offers no draft.")


def test_drafts_off_hide_the_route(client, session, services, monkeypatch):
    project = create_project(client)
    chat = new_chat(client, project["id"])
    message_id = ask(client, chat["id"], "What is due?")["answer"]["id"]
    assert FLAGS[DRAFTS].share == 0
    assert press(client, chat["id"], message_id).status_code == 404
