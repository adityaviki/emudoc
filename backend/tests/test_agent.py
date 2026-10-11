import json
import re

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart
from sqlalchemy import select

from app.agent.loop import MAX_RETRIES, run_agent
from app.agent.tools import (
    TOOLS,
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
from app.models import Folder, Passage, Project, Section
from tests.conftest import FakeTurn


def context(session, services, document) -> ToolContext:
    return ToolContext(session, services.embedder, document.project_id)


def section_named(session, document, title) -> Section:
    return session.scalar(
        select(Section).where(Section.document_id == document.id, Section.title == title)
    )


def _has_key(node, key) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_has_key(item, key) for item in node)
    return False


def test_tool_definitions_are_valid_for_the_api():
    assert list(TOOLS) == ["list_documents", "get_outline", "search", "query_facts", "read"]
    for tool in TOOLS.values():
        definition = tool.definition()
        assert definition.name == tool.name and definition.description == tool.description
        schema = definition.parameters_json_schema
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert all("description" in prop for prop in schema["properties"].values())
        assert not _has_key(schema, "title")


def test_list_documents(session, services, indexed):
    ctx = context(session, services, indexed)
    output = list_documents(ctx, ListDocumentsInput())
    assert "Documents 1-1 of 1." in output
    assert "Types in this project: contract (1)" in output
    assert f"doc {indexed.id} · Master Services Agreement" in output
    assert "dated 2024-03-01" in output and "2 pages" in output

    assert "Master Services" in list_documents(ctx, ListDocumentsInput(query="services"))
    assert list_documents(ctx, ListDocumentsInput(query="zebra")) == "No documents match."
    assert list_documents(ctx, ListDocumentsInput(doc_type="invoice")) == "No documents match."


def test_tools_see_folders_and_work_within_one(
    session, services, indexed, make_document, sample_docx
):
    project = session.get(Project, indexed.project_id)
    contracts = Folder(project_id=project.id, name="Contracts")
    invoices = Folder(project_id=project.id, name="Invoices")
    session.add_all([contracts, invoices])
    session.flush()
    signed = Folder(project_id=project.id, parent_id=contracts.id, name="Signed")
    session.add(signed)
    session.flush()
    indexed.folder_id = signed.id
    make_document(sample_docx, project=project)  # at the root
    ctx = context(session, services, indexed)

    output = list_documents(ctx, ListDocumentsInput())
    assert (
        "Folders (documents in each): Contracts (0), Contracts/Signed (1), Invoices (0). "
        "At the root: 1." in output
    )
    assert (
        f"doc {indexed.id} · Master Services Agreement · file msa.pdf · folder Contracts/Signed"
        in output
    )

    # A folder covers its subfolders, and its last part is enough when it is unambiguous.
    scoped = list_documents(ctx, ListDocumentsInput(folder="contracts"))
    assert "Documents 1-1 of 1." in scoped and "policy.docx" not in scoped
    assert "Documents 1-1 of 1." in list_documents(ctx, ListDocumentsInput(folder="Signed/"))
    assert list_documents(ctx, ListDocumentsInput(folder="Receipts")) == (
        "No folder named 'Receipts'. "
        "Folders in this project: Contracts, Contracts/Signed, Invoices."
    )
    session.add(Folder(project_id=project.id, parent_id=invoices.id, name="Signed"))
    session.commit()
    ctx = context(session, services, indexed)
    assert "could be any of Contracts/Signed, Invoices/Signed" in list_documents(
        ctx, ListDocumentsInput(folder="Signed")
    )

    assert "ninety (90) days" in search(ctx, SearchInput(query="termination", folder="Contracts"))
    assert search(ctx, SearchInput(query="termination", folder="Invoices")) == (
        "No documents in folder 'Invoices' to search."
    )
    assert "notice_period" in query_facts(ctx, QueryFactsInput(folder="Contracts/Signed"))
    assert "No facts match" in query_facts(ctx, QueryFactsInput(folder="Invoices"))


def test_get_outline_shows_the_tree_with_summaries(session, services, indexed):
    ctx = context(session, services, indexed)
    output = get_outline(ctx, GetOutlineInput(document_id=indexed.id))
    interpretation = section_named(session, indexed, "1.1 Interpretation")
    assert f"  [S{interpretation.id}] 1.1 Interpretation (p. 1): Summary of section" in output
    assert "3. Termination (p. 2)" in output
    assert "No document with id 999" in get_outline(ctx, GetOutlineInput(document_id=999))


def test_search_returns_citable_passages(session, services, indexed):
    ctx = context(session, services, indexed)
    output = search(ctx, SearchInput(query="termination notice period"))
    reference = re.search(
        r"\[P(\d+)\] doc \d+ · Master Services Agreement › 3\. Termination", output
    )
    assert reference and "ninety (90) days" in output

    source = ctx.sources[int(reference.group(1))]
    assert source["document_id"] == indexed.id
    assert source["page_start"] == 2
    assert "ninety (90) days" in source["excerpt"]


def test_read_a_section_a_neighbourhood_and_pages(session, services, indexed):
    ctx = context(session, services, indexed)
    definitions = section_named(session, indexed, "1. Definitions")

    # A section is read together with its subsections.
    output = read(ctx, ReadInput(section_id=definitions.id))
    assert "## Master Services Agreement › 1. Definitions" in output
    assert "## Master Services Agreement › 1. Definitions › 1.1 Interpretation" in output
    assert "Fees" not in output

    passage = session.scalar(
        select(Passage).where(Passage.document_id == indexed.id).order_by(Passage.ordinal.desc())
    )
    around = read(ctx, ReadInput(passage_id=passage.id))
    assert f"[P{passage.id}] p. 2" in around and "ninety (90) days" in around

    page_two = read(ctx, ReadInput(document_id=indexed.id, page_from=2))
    assert "ninety (90) days" in page_two and "Definitions" not in page_two
    assert read(ctx, ReadInput()) == "Give a section_id, a passage_id or a document_id."


def test_read_pages_a_long_section(session, services, indexed, monkeypatch):
    monkeypatch.setattr("app.agent.tools.READ_BUDGET", 150)
    ctx = context(session, services, indexed)
    first = read(ctx, ReadInput(document_id=indexed.id))
    more = re.search(r"Call read again with offset=(\d+) to continue", first)
    assert more
    rest = read(ctx, ReadInput(document_id=indexed.id, offset=int(more.group(1))))
    assert rest != first and "[P" in rest


def test_query_facts(session, services, indexed):
    ctx = context(session, services, indexed)
    names = query_facts(ctx, QueryFactsInput())
    assert "notice_period: 1 facts in 1 documents" in names

    rows = query_facts(ctx, QueryFactsInput(names=["notice_period"]))
    assert "1 facts match." in rows
    assert re.search(
        r"notice_period \| 90 days \| doc \d+ · Master Services Agreement \| p. 2 \| \[P\d+\]", rows
    )
    assert "notice_period: count 1, sum 90.00, min 90.00, max 90.00" in rows
    assert ctx.sources  # the fact's passage can be cited

    dated = query_facts(ctx, QueryFactsInput(date_from="2024-01-01", date_to="2024-12-31"))
    assert "effective_date | 1 March 2024" in dated
    assert "unverified" in query_facts(ctx, QueryFactsInput(contains="england"))
    assert "No facts match" in query_facts(ctx, QueryFactsInput(min_number=1000))
    assert "not a date" in query_facts(ctx, QueryFactsInput(date_from="March"))


def test_tools_cannot_reach_another_project(session, services, account, indexed):
    other = Project(org_id=account.organization.id, name="Other")
    session.add(other)
    session.commit()
    ctx = ToolContext(session, services.embedder, other.id)
    passage = session.scalar(select(Passage).where(Passage.document_id == indexed.id))

    assert "No document with id" in get_outline(ctx, GetOutlineInput(document_id=indexed.id))
    assert "No passage with id" in read(ctx, ReadInput(passage_id=passage.id))
    assert read(ctx, ReadInput(document_id=indexed.id)) == "Nothing to read there."
    assert "No passages match" in search(ctx, SearchInput(query="termination"))
    assert "No facts" in query_facts(ctx, QueryFactsInput())


def run(session, services, document, question="What is the notice period?"):
    project = session.get(Project, document.project_id)
    return list(run_agent(session, services, project, [{"role": "user", "content": question}]))


def test_agent_searches_then_answers_with_a_citation(session, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(
            ThinkingPart("Look for the termination clause."),
            ToolCallPart("search", {"query": "termination notice period"}, "call_1"),
        ),
        FakeTurn(TextPart("The notice period is 90 days.")),
    ]
    events = run(session, services, indexed)

    assert [event["type"] for event in events] == [
        "thinking",
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "done",
    ]
    assert events[1]["name"] == "search"
    assert any("ninety (90) days" in source["excerpt"] for source in events[2]["sources"])
    assert events[3]["is_error"] is False and "passages, best first" in events[3]["summary"]
    assert events[4]["text"] == "The notice period is 90 days."
    assert events[5]["usage"] == {"input_tokens": 20, "output_tokens": 10}

    # The second request carries the model's turn and its tool result, in one message.
    second = services.llm.agent_requests[1]
    assert [type(message) for message in second] == [ModelRequest, ModelResponse, ModelRequest]
    (result,) = second[2].parts
    assert result.tool_call_id == "call_1" and "[P" in result.content


def test_invalid_tool_input_is_returned_to_the_model_not_run(session, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(
            ToolCallPart("search", {"query": "x", "limit": "many"}, "call_a"),
            ToolCallPart("nonexistent", {}, "call_b"),
        ),
        FakeTurn(TextPart("Sorry.")),
    ]
    events = run(session, services, indexed)

    results = [event for event in events if event["type"] == "tool_result"]
    assert [result["is_error"] for result in results] == [True, True]
    sent = services.llm.agent_requests[1][2].parts
    assert sent[0].outcome == "failed" and "INVALID_JSON" in sent[0].content
    assert "limit" in json.loads(sent[0].content)["problems"]
    assert sent[1].outcome == "failed" and sent[1].content == "Unknown tool 'nonexistent'."


def test_a_reply_that_cannot_be_used_reissues_the_turn(session, services, indexed):
    services.llm.agent_turns = [
        # Tool arguments that are not JSON, then the empty reply of a fumbled function call.
        FakeTurn(TextPart("Let me look."), ToolCallPart("search", '{"query": "term', "call_1")),
        FakeTurn(finish_reason="error"),
        FakeTurn(TextPart("Recovered.")),
    ]
    events = run(session, services, indexed)
    assert [event["type"] for event in events] == ["text", "retry", "retry", "text", "done"]
    assert events[-1]["usage"] == {"input_tokens": 30, "output_tokens": 15}
    # Each reissued request is the same conversation, with nothing appended.
    first, second, third = services.llm.agent_requests
    assert first == second == third and len(first) == 1


def test_replies_that_stay_unusable_end_the_turn(session, services, indexed):
    services.llm.agent_turns = [FakeTurn(ThinkingPart("...")) for _ in range(MAX_RETRIES + 1)]
    events = run(session, services, indexed)
    assert [event["type"] for event in events if event["type"] != "thinking"] == [
        *["retry"] * MAX_RETRIES,
        "error",
    ]
    assert "could not be used" in events[-1]["message"]


def test_truncated_replies_and_refusals_stop_without_running_tools(session, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "term"}, "call_1"), finish_reason="length")
    ]
    events = run(session, services, indexed)
    assert [event["type"] for event in events] == ["error"]

    services.llm.agent_turns = [FakeTurn(TextPart("partial"), finish_reason="length")]
    events = run(session, services, indexed)
    assert [event["type"] for event in events] == ["text", "notice", "done"]

    services.llm.agent_turns = [FakeTurn(TextPart("partial"), finish_reason="content_filter")]
    events = run(session, services, indexed)
    assert events[-1] == {"type": "error", "message": "The model declined to answer this request."}
