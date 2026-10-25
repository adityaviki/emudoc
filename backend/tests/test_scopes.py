"""A chat about a folder or chosen documents (app/scopes.py): chosen as it starts and changed
after, shown with the chat, and the agent's tools held to it by the server whatever the model
asks for, a chat whose folder or documents are gone reading nothing."""

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import select

from app.agent.tools import (
    GetOutlineInput,
    ListDocumentsInput,
    QueryFactsInput,
    ReadInput,
    SearchInput,
    ToolContext,
    get_outline,
    list_documents,
    query_facts,
    read,
    search,
)
from app.api.chats import BOTH_SCOPES
from app.indexing.pipeline import process_document
from app.models import Document, Folder, Passage, Project
from app.schemas import SCOPE_DOCUMENTS
from app.scopes import NOTHING_LEFT, Scope
from tests.conftest import FakeTurn
from tests.test_chats import ask, new_chat, run_next


@pytest.fixture
def library(session, services, indexed, make_document, sample_docx):
    """The contract in Contracts/Signed and the travel policy at the root, both indexed."""
    project = session.get(Project, indexed.project_id)
    contracts = Folder(project_id=project.id, name="Contracts")
    session.add(contracts)
    session.flush()
    signed = Folder(project_id=project.id, parent_id=contracts.id, name="Signed")
    session.add(signed)
    session.flush()
    indexed.folder_id = signed.id
    session.commit()
    policy = make_document(sample_docx, project=project)
    process_document(policy.id, services)
    session.refresh(policy)
    return {"contract": indexed, "policy": policy, "contracts": contracts, "signed": signed}


def about(client, chat_id, **scope):
    """Make the chat about what `scope` names; the response, whatever it says."""
    return client.patch(f"/api/chats/{chat_id}", json={"scope": scope})


def test_a_chat_is_about_the_project_a_folder_or_chosen_documents(client, session, library):
    contract, policy, contracts = library["contract"], library["policy"], library["contracts"]
    project_id = contract.project_id
    chat = new_chat(client, project_id)
    assert chat["scope"] == {"kind": "project", "folder": None, "documents": []}

    started = client.post(
        f"/api/projects/{project_id}/chats", json={"scope": {"folder_id": contracts.id}}
    )
    assert started.status_code == 201
    assert started.json()["scope"] == {
        "kind": "folder",
        "folder": {"id": contracts.id, "name": "Contracts"},
        "documents": [],
    }

    changed = about(client, chat["id"], document_ids=[policy.id, contract.id, policy.id])
    assert changed.status_code == 200, changed.text
    assert changed.json()["scope"] == {
        "kind": "documents",
        "folder": None,
        "documents": [
            {"id": contract.id, "filename": "msa.pdf"},
            {"id": policy.id, "filename": "policy.docx"},
        ],
    }
    # The chat list shows each chat with what it is about.
    listed = {
        c["id"]: c["scope"]["kind"] for c in client.get(f"/api/projects/{project_id}/chats").json()
    }
    assert listed == {chat["id"]: "documents", started.json()["id"]: "folder"}
    # Neither is the whole project again.
    assert about(client, chat["id"]).json()["scope"]["kind"] == "project"
    assert client.get(f"/api/chats/{chat['id']}").json()["scope"]["kind"] == "project"

    both = about(client, chat["id"], folder_id=contracts.id, document_ids=[policy.id])
    assert both.status_code == 422 and both.json() == {"detail": BOTH_SCOPES}
    many = about(client, chat["id"], document_ids=list(range(1, SCOPE_DOCUMENTS + 2)))
    assert many.status_code == 422
    # What another project holds, or the trash, is nothing a chat can be about.
    elsewhere = client.post("/api/projects", json={"name": "Elsewhere"}).json()
    other = Folder(project_id=elsewhere["id"], name="Theirs")
    session.add(other)
    session.commit()
    assert about(client, chat["id"], folder_id=other.id).status_code == 404
    assert about(client, chat["id"], document_ids=[policy.id, 999_999]).status_code == 404
    assert client.delete(f"/api/documents/{policy.id}").status_code == 204
    assert about(client, chat["id"], document_ids=[policy.id]).status_code == 404
    # A refused change leaves the chat as it was.
    assert client.get(f"/api/chats/{chat['id']}").json()["scope"]["kind"] == "project"


def test_the_tools_read_only_what_the_chat_is_about(session, services, library):
    contract, policy = library["contract"], library["policy"]
    passage = session.scalar(select(Passage).where(Passage.document_id == policy.id))

    def context(scope):
        return ToolContext(session, services.embedder, contract.project_id, scope=scope)

    folder = context(Scope("folder", folder_id=library["contracts"].id))
    listed = list_documents(folder, ListDocumentsInput())
    # The stand-in model titles both alike: the file names tell them apart.
    assert "Documents 1-1 of 1." in listed and "msa.pdf" in listed
    assert "policy.docx" not in listed
    assert "Folders (documents in each): Contracts/Signed (1). At the root: 0." in listed
    assert "ninety (90) days" in search(folder, SearchInput(query="termination"))
    assert "Travel Policy" not in search(folder, SearchInput(query="flights economy receipts"))
    assert search(folder, SearchInput(query="flights", document_ids=[policy.id])) == (
        "None of those documents is one this chat is about, so none was searched."
    )
    assert get_outline(folder, GetOutlineInput(document_id=policy.id)) == (
        f"No document with id {policy.id} among the documents this chat is about."
    )
    assert read(folder, ReadInput(passage_id=passage.id)).startswith("No passage with id")
    assert read(folder, ReadInput(document_id=policy.id)) == "Nothing to read there."
    assert "notice_period" in query_facts(folder, QueryFactsInput())

    chosen = context(Scope("documents", document_ids=(policy.id,)))
    listed = list_documents(chosen, ListDocumentsInput())
    assert "Documents 1-1 of 1." in listed and "msa.pdf" not in listed
    assert "ninety (90) days" not in search(chosen, SearchInput(query="termination notice"))
    assert "Travel Policy" in get_outline(chosen, GetOutlineInput(document_id=policy.id))
    assert "No facts match" in query_facts(chosen, QueryFactsInput(names=["notice_period"]))

    # The whole project, as every chat was before.
    whole = list_documents(context(None), ListDocumentsInput())
    assert "Documents 1-2 of 2." in whole


def test_an_answer_reads_what_its_chat_is_about_as_it_starts(client, session, services, library):
    contract, policy = library["contract"], library["policy"]
    chat = new_chat(client, contract.project_id)
    about(client, chat["id"], document_ids=[policy.id])
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "termination notice days"}, "c1")),
        FakeTurn(TextPart("The policy does not say.")),
    ]
    ask(client, chat["id"], "What is the notice period?")
    run_next(services)
    prompt = services.llm.agent_prompts[0]
    assert (
        f"This chat is about 1 document the member chose, those with the ids {policy.id}. "
        "Your tools read only these documents." in prompt
    )
    # What the tool showed the model came from the policy alone.
    sources = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["sources"]
    assert sources and {source["document_id"] for source in sources} == {policy.id}

    # Made about the folder, the next answer reads the folder.
    about(client, chat["id"], folder_id=library["contracts"].id)
    services.llm.agent_turns = [FakeTurn(TextPart("90 days."))]
    ask(client, chat["id"], "And the contract?")
    run_next(services)
    assert (
        "This chat is about one folder of the project and its subfolders, which hold 1 document"
        in services.llm.agent_prompts[-1]
    )
    about(client, chat["id"])
    services.llm.agent_turns = [FakeTurn(TextPart("Both."))]
    ask(client, chat["id"], "And everything?")
    run_next(services)
    assert "This chat is about" not in services.llm.agent_prompts[-1]


def test_a_chat_whose_folder_or_documents_are_gone_reads_nothing(
    client, session, services, library
):
    contract, policy = library["contract"], library["policy"]
    chat = new_chat(client, contract.project_id)
    about(client, chat["id"], folder_id=library["contracts"].id)
    # In the trash, the folder is none the chat shows, nor one its tools read.
    assert client.delete(f"/api/folders/{library['contracts'].id}").status_code == 204
    assert client.get(f"/api/chats/{chat['id']}").json()["scope"] == {
        "kind": "folder",
        "folder": None,
        "documents": [],
    }
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("list_documents", {}, "c1")),
        FakeTurn(TextPart("Nothing is left.")),
    ]
    ask(client, chat["id"], "What is in it?")
    run_next(services)
    assert "This chat is about a folder no longer in the project" in services.llm.agent_prompts[0]
    (step,) = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["steps"]
    assert step["summary"] == NOTHING_LEFT.split("\n", 1)[0][:200] and not step["is_error"]

    # Deleted for good, the folder leaves the chat about nothing rather than the whole project.
    session.delete(session.get(Folder, library["contracts"].id))
    session.commit()
    session.expire_all()
    assert client.get(f"/api/chats/{chat['id']}").json()["scope"]["kind"] == "folder"

    # A document deleted for good goes from the documents a chat is about.
    about(client, chat["id"], document_ids=[policy.id])
    session.delete(session.get(Document, policy.id))
    session.commit()
    assert client.get(f"/api/chats/{chat['id']}").json()["scope"] == {
        "kind": "documents",
        "folder": None,
        "documents": [],
    }
