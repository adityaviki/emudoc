"""Usage metering: an answer's model calls and a document's indexing are recorded as its
organisation's usage whatever ends the run, an upload and a render are the member's, the rows
outlive the project and the user and go with the organisation, the answer and the document
show what they used with its cost by the configured prices, and the daily rollup groups the
rows by day in UTC, which the `usage` command prints; the `costs` command sums every
organisation's rows, costliest first, against its plan's daily limit; and the usage page shows
every member what the organisation holds and used of its plan, as the checks count it, with
its pages and questions by day and by month."""

import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import func, select

import app.answers as answers_module
import app.indexing.pipeline as pipeline
from app.agent.tools import TOOLS
from app.answers import answer
from app.billing import change_plan
from app.config import get_settings
from app.entitlements import CHARS_PER_PAGE, month_of
from app.indexing.pipeline import StageFailure, process_document, reset_stages
from app.jobs import ANSWER_QUESTION, INDEX_DOCUMENT, LeaseLost, claim
from app.llm import LLMError
from app.maintenance import main
from app.models import AllowancePack, Organization, Passage, UsageEvent, User
from app.spending import day_of, spent_since
from app.usage import (
    ANSWER,
    CHAT_MESSAGE,
    DOCUMENT,
    EMBED,
    ENRICH,
    INFER_SECTIONS,
    OCR,
    RENDER,
    UPLOAD,
    DailyUsage,
    Metered,
    ModelCost,
    OrganizationCost,
    add,
    cost_of,
    daily,
    metered,
    model_costs,
    organization_costs,
    record,
    summed,
)
from app.worker import HANDLERS
from tests.conftest import FakeEmbedder, FakeTurn
from tests.test_answers import Held
from tests.test_api import create_project, upload
from tests.test_chats import answered, ask, new_chat
from tests.test_jobs import lose
from tests.test_pipeline import named_pages, page_of, scanned_pdf


def rows_of(session, ref: tuple[str, int]) -> list[UsageEvent]:
    """The usage rows of one answer or document, across every run for it, in the order they
    were written."""
    ref_type, ref_id = ref
    session.expire_all()
    return session.scalars(
        select(UsageEvent)
        .where(UsageEvent.ref_type == ref_type, UsageEvent.ref_id == ref_id)
        .order_by(UsageEvent.id)
    ).all()


def measures_of(rows: list[UsageEvent]) -> list[tuple[str, int]]:
    return [(row.unit, row.quantity) for row in rows]


def spent(rows: list[UsageEvent], *kinds: str) -> list[tuple[str, str, int]]:
    """What the rows count, by kind and unit; only the kinds named, when any are."""
    return [(row.kind, row.unit, row.quantity) for row in rows if not kinds or row.kind in kinds]


# What one indexing request costs in the tests (`FakeLLM.structured`).
ONE_CALL = [("calls", 1), ("input_tokens", 10), ("output_tokens", 5)]


def model_calls(kind: str, calls: int) -> list[tuple[str, str, int]]:
    return [(kind, unit, quantity * calls) for unit, quantity in ONE_CALL]


def embedded(passages: int) -> list[tuple[str, str, int]]:
    """What embedding the passages counts: the tokens the endpoint reported, then the texts."""
    return [
        (EMBED, "input_tokens", passages * FakeEmbedder.TOKENS_PER_TEXT),
        (EMBED, "inputs", passages),
    ]


def shown(**spent) -> dict:
    """What the API shows of one kind's usage: every unit, zero when it was not counted."""
    return {
        "model": None,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "pages": 0,
        "bytes": 0,
        "inputs": 0,
        "cost_micros": None,
        **spent,
    }


def usage_shown(client, path: str) -> dict:
    return client.get(path).json()["usage"]


def passages_of(session, document_id: int, embedded: bool = False) -> int:
    query = select(func.count()).select_from(Passage).where(Passage.document_id == document_id)
    if embedded:
        query = query.where(Passage.embedding.is_not(None))
    return session.scalar(query)


def test_an_answer_records_its_model_calls_and_tokens(client, session, services, account, indexed):
    # Two turns of 10 tokens in and 5 out each (`FakeTurn.response`).
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    answered(client, services, chat["id"], "Notice period?")
    message_id = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["id"]

    rows = rows_of(session, (CHAT_MESSAGE, message_id))
    assert measures_of(rows) == [("calls", 2), ("input_tokens", 20), ("output_tokens", 10)]
    for row in rows:
        assert row.kind == ANSWER and row.model == get_settings().agent_model
        assert row.org_id == account.organization.id and row.user_id == account.user.id
        assert row.project_id == indexed.project_id and row.cost_micros is None
        assert datetime.now(UTC) - row.occurred_at < timedelta(minutes=1)

    # An answer that never reached the model used nothing.
    services.llm.stream_agent = lambda *args: (_ for _ in ()).throw(RuntimeError("down"))
    asked = ask(client, chat["id"], "Again?")
    with pytest.raises(RuntimeError):
        answer(asked["answer"]["id"], services)
    assert rows_of(session, (CHAT_MESSAGE, asked["answer"]["id"])) == []


def test_an_answer_shows_what_it_used(client, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    events = answered(client, services, chat["id"], "Notice period?")
    question, reply = client.get(f"/api/chats/{chat['id']}").json()["messages"]
    model = get_settings().agent_model
    assert reply["usage"] == {
        ANSWER: shown(model=model, calls=2, input_tokens=20, output_tokens=10)
    }
    assert question["usage"] == {}
    # A follower was told the same by the `done` event, before the row had it.
    done = next(event for event in events if event["type"] == "done")
    assert done["usage"] == {
        "model": model,
        "calls": 2,
        "input_tokens": 20,
        "output_tokens": 10,
        "cost_micros": None,
    }


def test_a_stopped_answer_is_counted_for_the_calls_it_made(
    client, session, services, indexed, monkeypatch
):
    # Text goes out as it comes, so the run notices the stop at the first word of the second
    # call, before that call's usage is known: the first call alone is on the bill.
    monkeypatch.setattr(answers_module, "TEXT_FLUSH_SECONDS", 0)
    held = Held(TextPart("The notice period is 90 days."))
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        held,
    ]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    run = threading.Thread(target=answer, args=(message_id, services))
    run.start()
    try:
        assert held.reached.wait(10)
        stopped = client.post(f"/api/chats/{chat['id']}/messages/{message_id}/stop")
        assert stopped.json()["status"] == "stopped"
        held.release.set()
        run.join(10)
    finally:
        held.release.set()
    assert not run.is_alive()
    assert measures_of(rows_of(session, (CHAT_MESSAGE, message_id))) == [
        ("calls", 1),
        ("input_tokens", 10),
        ("output_tokens", 5),
    ]
    # The stop settled the row without knowing the calls; the run filled them in after.
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert reply["status"] == "stopped" and reply["usage"]["answer"]["calls"] == 1


def test_a_lost_run_and_the_run_that_starts_over_are_both_counted(
    client, session, services, indexed, monkeypatch
):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    job = claim("w:1", [ANSWER_QUESTION])
    search = TOOLS["search"].run

    def search_while_taken_over(ctx, args):
        output = search(ctx, args)
        lose(session, job.id)
        return output

    monkeypatch.setattr(TOOLS["search"], "run", search_while_taken_over)
    with pytest.raises(LeaseLost):
        HANDLERS[ANSWER_QUESTION].run(job, services)
    # The run wrote nothing of the answer past its lease test, but the call it made was made.
    one_call = [("calls", 1), ("input_tokens", 10), ("output_tokens", 5)]
    assert measures_of(rows_of(session, (CHAT_MESSAGE, message_id))) == one_call

    # The next worker starts the answer over and is counted for its own call.
    monkeypatch.setattr(TOOLS["search"], "run", search)
    taken = claim("w:2", [ANSWER_QUESTION])
    assert HANDLERS[ANSWER_QUESTION].run(taken, services) is None
    assert measures_of(rows_of(session, (CHAT_MESSAGE, message_id))) == one_call * 2
    # The answer shows both runs, which is what it cost.
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert reply["usage"] == {
        ANSWER: shown(model=get_settings().agent_model, calls=2, input_tokens=20, output_tokens=10)
    }


def test_usage_stays_with_the_organisation_when_its_project_and_user_go(
    client, session, services, account, indexed
):
    services.llm.agent_turns = [FakeTurn(TextPart("90 days."))]
    chat = new_chat(client, indexed.project_id)
    answered(client, services, chat["id"], "Notice period?")
    message_id = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["id"]
    assert len(rows_of(session, (CHAT_MESSAGE, message_id))) == 3

    # The organisation is what is billed, so its usage outlives the project and the member.
    assert client.delete(f"/api/projects/{indexed.project_id}").status_code == 204
    rows = rows_of(session, (CHAT_MESSAGE, message_id))
    assert [row.project_id for row in rows] == [None] * 3
    assert [row.user_id for row in rows] == [account.user.id] * 3
    session.delete(session.get(User, account.user.id))
    session.commit()
    assert [row.user_id for row in rows_of(session, (CHAT_MESSAGE, message_id))] == [None] * 3
    # It goes with the organisation.
    session.delete(session.get(Organization, account.organization.id))
    session.commit()
    assert rows_of(session, (CHAT_MESSAGE, message_id)) == []


def test_indexing_a_document_records_its_pages_model_calls_and_embeddings(
    client, session, services, account, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    assert process_document(document.id, services) == []
    passages = passages_of(session, document.id)
    index_model = get_settings().index_model

    # Two pages came in with the upload; the contract has headings, so no sections were
    # inferred, and it fits one enrich request; every passage was embedded.
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows) == [
        (UPLOAD, "pages", 2),
        *model_calls(ENRICH, 1),
        *embedded(passages),
    ]
    # The stages ran for the document, not for a member.
    for row in rows:
        assert row.org_id == account.organization.id and row.project_id == document.project_id
        assert row.user_id is None and row.cost_micros is None
    assert {row.kind: row.model for row in rows} == {
        UPLOAD: None,
        ENRICH: index_model,
        EMBED: "fake-embedder",
    }
    # The document shows the same, by kind.
    assert usage_shown(client, f"/api/documents/{document.id}") == {
        UPLOAD: shown(pages=2),
        ENRICH: shown(model=index_model, calls=1, input_tokens=10, output_tokens=5),
        EMBED: shown(
            model="fake-embedder",
            input_tokens=passages * FakeEmbedder.TOKENS_PER_TEXT,
            inputs=passages,
        ),
    }

    # "Index again" from the start calls the model and the embedder again, and is charged
    # for them; the pages were uploaded once.
    session.refresh(document)
    reset_stages(document)
    session.commit()
    assert process_document(document.id, services) == []
    assert spent(rows_of(session, (DOCUMENT, document.id))) == [
        (UPLOAD, "pages", 2),
        *model_calls(ENRICH, 1),
        *embedded(passages),
        *model_calls(ENRICH, 1),
        *embedded(passages),
    ]
    # Both runs add up on the document, and the stages that were reset kept nothing from it.
    assert usage_shown(client, f"/api/documents/{document.id}") == {
        UPLOAD: shown(pages=2),
        ENRICH: shown(model=index_model, calls=2, input_tokens=20, output_tokens=10),
        EMBED: shown(
            model="fake-embedder",
            input_tokens=passages * 2 * FakeEmbedder.TOKENS_PER_TEXT,
            inputs=passages * 2,
        ),
    }


def test_a_hosted_embedding_model_is_priced_for_the_tokens_it_was_sent(
    client, session, services, make_document, sample_pdf, monkeypatch
):
    # A hosted endpoint bills by the token like a model, so MODEL_PRICES names it as
    # EMBEDDING_MODEL does, and its cost reaches the document and the day's spending.
    monkeypatch.setattr(get_settings(), "model_prices", "fake-embedder=1000/0")
    document = make_document(sample_pdf)
    assert process_document(document.id, services) == []
    tokens = passages_of(session, document.id) * FakeEmbedder.TOKENS_PER_TEXT

    rows = rows_of(session, (DOCUMENT, document.id))
    assert [(row.unit, row.cost_micros) for row in rows if row.kind == EMBED] == [
        ("input_tokens", tokens * 1000),
        ("inputs", None),
    ]
    shown_usage = usage_shown(client, f"/api/documents/{document.id}")
    assert shown_usage[EMBED]["cost_micros"] == tokens * 1000
    assert spent_since(session, day_of()[0]) == tokens * 1000


def test_inferring_sections_is_charged_as_its_own_kind(session, services, make_document, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("\n\n".join(f"Paragraph number {n} of the notes." for n in range(16)))
    document = make_document(path)
    process_document(document.id, services)

    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows, INFER_SECTIONS) == model_calls(INFER_SECTIONS, 1)
    # A text file has no pages of its own, and counts those of its text.
    assert spent(rows, UPLOAD) == [(UPLOAD, "pages", 1)]


def test_a_scan_is_charged_per_page_read_even_when_the_stage_fails_half_way(
    session, services, make_document, tmp_path, monkeypatch
):
    document = make_document(scanned_pdf(tmp_path / "scan.pdf", 2))
    named_pages(monkeypatch)
    read_page = services.llm.ocr_page

    def second_page_fails(image, media_type, usage=None):
        if page_of(image) == 2:
            raise LLMError("The rate limit for the model was reached.", transient=True)
        return read_page(image, media_type, usage)

    monkeypatch.setattr(services.llm, "ocr_page", second_page_fails)
    failures = process_document(document.id, services)
    assert [(failure.stage, failure.transient) for failure in failures] == [("parse", True)]

    # The page the model read while the other failed is on the bill, as the model's call;
    # the upload's pages are not known until a parse succeeds.
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows) == [*model_calls(OCR, 1), (OCR, "pages", 1)]
    assert {row.model for row in rows} == {get_settings().index_model}

    # The retry reads both pages and is charged for both, and the upload is now two pages.
    monkeypatch.setattr(services.llm, "ocr_page", read_page)
    assert process_document(document.id, services) == []
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows, OCR, UPLOAD) == [
        *model_calls(OCR, 1),
        (OCR, "pages", 1),
        *model_calls(OCR, 2),
        (OCR, "pages", 2),
        (UPLOAD, "pages", 2),
    ]
    assert spent(rows, ENRICH) == model_calls(ENRICH, 1)


def test_a_scan_with_more_pages_than_the_model_may_read_fails_before_a_page_is_read(
    session, services, make_document, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ocr_max_pages", 2)
    document = make_document(scanned_pdf(tmp_path / "scan.pdf", 3))
    refusal = (
        "This file has 3 scanned pages to read, more than the 2 one document can have. "
        "Split it and upload the parts."
    )
    assert process_document(document.id, services) == [
        StageFailure("parse", refusal, transient=False)
    ]
    session.refresh(document)
    assert document.status == "failed" and document.error == refusal
    # Refused before the model read a page: no call was made and nothing was charged.
    assert services.llm.calls == []
    assert rows_of(session, (DOCUMENT, document.id)) == []

    # Zero removes the limit, and the scan is then read page by page.
    monkeypatch.setattr(get_settings(), "ocr_max_pages", 0)
    reset_stages(document, "parse")
    session.commit()
    assert process_document(document.id, services) == []
    assert [name for name, _ in services.llm.calls].count("OcrPage") == 3


def test_a_stage_whose_lease_was_lost_is_charged_and_so_is_the_run_that_redoes_it(
    session, services, make_document, sample_pdf, monkeypatch
):
    document = make_document(sample_pdf)
    job = claim("w:1", [INDEX_DOCUMENT])
    embed = services.embedder.embed_documents

    def embed_while_taken_over(texts, usage=None):
        vectors = embed(texts, usage)
        lose(session, job.id)
        return vectors

    monkeypatch.setattr(services.embedder, "embed_documents", embed_while_taken_over)
    with pytest.raises(LeaseLost):
        HANDLERS[INDEX_DOCUMENT].run(job, services)

    # The vectors were discarded with the stage's "done" mark; the texts were embedded.
    session.refresh(document)
    assert document.stages["embed"]["status"] == "running"
    assert passages_of(session, document.id, embedded=True) == 0
    passages = passages_of(session, document.id)
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows, EMBED) == embedded(passages)

    # The next worker runs the stage again and is charged for its own embedding.
    monkeypatch.setattr(services.embedder, "embed_documents", embed)
    taken = claim("w:2", [INDEX_DOCUMENT])
    assert HANDLERS[INDEX_DOCUMENT].run(taken, services) is None
    assert passages_of(session, document.id, embedded=True) == passages
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows, EMBED) == embedded(passages) * 2
    # Parse and enrich ran once: their rows were committed before the takeover.
    assert spent(rows, UPLOAD, ENRICH) == [(UPLOAD, "pages", 2), *model_calls(ENRICH, 1)]


def test_the_pages_of_a_parse_whose_lease_was_lost_are_not_counted_again(
    session, services, make_document, sample_pdf, monkeypatch
):
    # The pages are counted against the plan's month once: the worker that takes the parse
    # over finds them in the document's usage, recorded with their event.
    document = make_document(sample_pdf)
    job = claim("w:1", [INDEX_DOCUMENT])
    draw_ahead = pipeline._queue_drawing_ahead

    def taken_over(stage_session, parsed):
        draw_ahead(stage_session, parsed)
        lose(session, job.id)

    monkeypatch.setattr(pipeline, "_queue_drawing_ahead", taken_over)
    with pytest.raises(LeaseLost):
        HANDLERS[INDEX_DOCUMENT].run(job, services)
    session.refresh(document)
    assert document.page_count is None and document.usage["upload"] == {"pages": 2}

    monkeypatch.setattr(pipeline, "_queue_drawing_ahead", draw_ahead)
    taken = claim("w:2", [INDEX_DOCUMENT])
    assert HANDLERS[INDEX_DOCUMENT].run(taken, services) is None
    session.refresh(document)
    assert document.page_count == 2 and document.usage["upload"] == {"pages": 2}
    assert spent(rows_of(session, (DOCUMENT, document.id)), UPLOAD) == [(UPLOAD, "pages", 2)]


def test_an_upload_records_its_bytes_for_the_member(client, session, account, sample_pdf, tmp_path):
    project = create_project(client)
    junk = tmp_path / "data.bin"
    junk.write_bytes(b"\x00\x01")
    result = upload(client, project["id"], sample_pdf, junk)
    (document,) = result["documents"]
    assert [rejected["filename"] for rejected in result["rejected"]] == ["data.bin"]

    (row,) = rows_of(session, (DOCUMENT, document["id"]))
    assert (row.kind, row.unit, row.quantity) == (UPLOAD, "bytes", sample_pdf.stat().st_size)
    assert row.org_id == account.organization.id and row.project_id == project["id"]
    assert row.user_id == account.user.id and row.model is None
    # The rejected file was not stored, so nothing was used for it.
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == 1
    # The document shows its bytes from the upload's own response on.
    assert document["usage"] == {UPLOAD: shown(bytes=sample_pdf.stat().st_size)}
    assert usage_shown(client, f"/api/documents/{document['id']}") == document["usage"]


def test_a_page_or_thumbnail_is_charged_when_rendered_not_when_served_again(
    client, session, services, account, sample_pdf
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    for _ in range(2):
        assert client.get(f"/api/documents/{document['id']}/pages/1.png").status_code == 200
        assert client.get(f"/api/documents/{document['id']}/thumbnail.png").status_code == 200
    assert client.get(f"/api/documents/{document['id']}/pages/9.png").status_code == 404

    renders = [row for row in rows_of(session, (DOCUMENT, document["id"])) if row.kind == RENDER]
    assert [(row.unit, row.quantity) for row in renders] == [("pages", 1)] * 2
    for row in renders:
        assert row.user_id == account.user.id and row.org_id == account.organization.id
        assert row.project_id == project["id"] and row.model is None

    assert usage_shown(client, f"/api/documents/{document['id']}")[RENDER] == shown(pages=2)

    # The rollup has the whole document: its upload, its indexing and its renders.
    today = datetime.now(UTC).date()
    kinds = {row.kind for row in daily(session, account.organization.id, today, today)}
    assert kinds == {UPLOAD, ENRICH, EMBED, RENDER}


def test_costs_follow_the_prices_the_settings_list(
    client, session, services, account, indexed, monkeypatch
):
    model = get_settings().agent_model
    # The answer's model at 0.10 in and 0.40 out per million tokens: a FakeTurn's 10 tokens
    # in cost one millionth, its 5 out two. The index model shares the name here, so the
    # tests give it none by pricing a model nothing uses instead.
    monkeypatch.setattr(get_settings(), "model_prices", f" {model} = 0.10 / 0.40 ,other=1/1")
    assert cost_of(model, "input_tokens", 1_000_000) == 100_000
    assert cost_of(model, "output_tokens", 5) == 2
    assert cost_of(model, "pages", 5) is None
    assert cost_of("other", "input_tokens", 10) == 10
    assert cost_of("nobody:prices-this", "input_tokens", 10) is None
    assert cost_of(None, "input_tokens", 10) is None

    services.llm.agent_turns = [FakeTurn(TextPart("90 days."))]
    chat = new_chat(client, indexed.project_id)
    events = answered(client, services, chat["id"], "Notice period?")
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    rows = rows_of(session, (CHAT_MESSAGE, reply["id"]))
    assert [(row.unit, row.cost_micros) for row in rows] == [
        ("calls", None),
        ("input_tokens", 1),
        ("output_tokens", 2),
    ]
    assert reply["usage"][ANSWER]["cost_micros"] == 3
    done = next(event for event in events if event["type"] == "done")
    assert done["usage"]["cost_micros"] == 3
    today = datetime.now(UTC).date()
    costs = {
        (row.unit, row.cost_micros)
        for row in daily(session, account.organization.id, today, today)
        if row.kind == ANSWER
    }
    # The question itself costs nothing: it is what the plan counts, not work a model did.
    assert costs == {
        ("calls", None),
        ("input_tokens", 1),
        ("output_tokens", 2),
        ("questions", None),
    }

    # Without a price for its model, a row has no cost and the document shows none.
    monkeypatch.setattr(get_settings(), "model_prices", "other=1/1")
    usage = usage_shown(client, f"/api/documents/{indexed.id}")
    assert usage[ENRICH]["cost_micros"] is None and usage[EMBED]["cost_micros"] is None


def test_adding_rows_to_a_usage_sums_each_unit_and_the_known_costs():
    rows = [
        UsageEvent(kind=OCR, unit="pages", quantity=2, model="m2"),
        UsageEvent(kind=OCR, unit="calls", quantity=2, model="m2", cost_micros=None),
        UsageEvent(kind=OCR, unit="input_tokens", quantity=400, model="m2", cost_micros=40),
        UsageEvent(kind=RENDER, unit="pages", quantity=1),
    ]
    before = {OCR: {"pages": 1, "calls": 1, "model": "m1", "cost_micros": 5}}
    assert add(before, rows) == {
        # The latest row names the model; a row without a cost adds nothing to it.
        OCR: {"pages": 3, "calls": 3, "input_tokens": 400, "model": "m2", "cost_micros": 45},
        RENDER: {"pages": 1},
    }
    # What was given is left as it was.
    assert before == {OCR: {"pages": 1, "calls": 1, "model": "m1", "cost_micros": 5}}
    assert add({}, []) == {}


def test_the_daily_rollup_sums_per_day_kind_and_unit_in_utc(session, account, make_account):
    org = account.organization.id
    other = make_account("ann@globex.example").organization.id

    def at(day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 10, day, hour, minute, tzinfo=UTC)

    def add(org_id: int, kind: str, measures: dict[str, int], when: datetime) -> None:
        record(
            session,
            org_id=org_id,
            project_id=None,
            user_id=None,
            kind=kind,
            measures=measures,
            occurred_at=when,
        )

    add(org, "answer", {"calls": 1, "input_tokens": 100, "output_tokens": 0}, at(10, 9))
    add(org, "answer", {"calls": 2, "input_tokens": 50}, at(11, 8))
    # Half past eleven at night in UTC is already the 12th east of Greenwich; the rollup
    # keeps the UTC day, like every other date the app stores.
    add(org, "answer", {"calls": 1, "input_tokens": 25}, at(11, 23, 30))
    add(org, "upload", {"bytes": 4096, "pages": 3}, at(11, 8))
    add(other, "answer", {"calls": 7, "input_tokens": 700}, at(11, 8))
    # Outside the span asked for: the day before it, and the first instant after it.
    add(org, "answer", {"calls": 9}, at(9, 23, 59))
    add(org, "answer", {"calls": 9}, at(12, 0))
    # A cost adds up once rows carry one; until then a day's cost is unknown, not zero.
    (priced,) = record(
        session,
        org_id=org,
        project_id=None,
        user_id=None,
        kind="render",
        measures={"pages": 2},
        occurred_at=at(10, 12),
    )
    priced.cost_micros = 1500
    session.commit()

    assert daily(session, org, date(2026, 10, 10), date(2026, 10, 11)) == [
        DailyUsage(date(2026, 10, 10), "answer", "calls", 1, None),
        DailyUsage(date(2026, 10, 10), "answer", "input_tokens", 100, None),
        DailyUsage(date(2026, 10, 10), "render", "pages", 2, 1500),
        DailyUsage(date(2026, 10, 11), "answer", "calls", 3, None),
        DailyUsage(date(2026, 10, 11), "answer", "input_tokens", 75, None),
        DailyUsage(date(2026, 10, 11), "upload", "bytes", 4096, None),
        DailyUsage(date(2026, 10, 11), "upload", "pages", 3, None),
    ]
    assert daily(session, other, date(2026, 10, 1), date(2026, 10, 31)) == [
        DailyUsage(date(2026, 10, 11), "answer", "calls", 7, None),
        DailyUsage(date(2026, 10, 11), "answer", "input_tokens", 700, None),
    ]
    # A measure of zero adds no row at all.
    assert session.scalar(select(UsageEvent).where(UsageEvent.unit == "output_tokens")) is None


def test_the_metered_rollup_has_every_day_and_month_with_the_pages_and_questions_alone(
    session, account, make_account
):
    org = account.organization.id
    other = make_account("ann@globex.example").organization.id

    def add(org_id: int, kind: str, measures: dict[str, int], when: datetime) -> None:
        record(
            session,
            org_id=org_id,
            project_id=None,
            user_id=None,
            kind=kind,
            measures=measures,
            occurred_at=when,
        )

    add(org, "upload", {"bytes": 4096, "pages": 3}, datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    add(org, "upload", {"pages": 2}, datetime(2026, 10, 2, 8, tzinfo=UTC))
    add(org, "answer", {"questions": 1}, datetime(2026, 10, 2, 9, tzinfo=UTC))
    add(org, "answer", {"questions": 1}, datetime(2026, 10, 2, 23, 59, tzinfo=UTC))
    # What the plan does not count against the month: an answer's calls, the pages rendered.
    add(org, "answer", {"calls": 4}, datetime(2026, 10, 2, 9, tzinfo=UTC))
    add(org, "render", {"pages": 9}, datetime(2026, 10, 2, 9, tzinfo=UTC))
    add(other, "answer", {"questions": 5}, datetime(2026, 10, 2, 9, tzinfo=UTC))
    # Past the last day asked for, though in its month.
    add(org, "upload", {"pages": 7}, datetime(2026, 10, 4, tzinfo=UTC))
    add(org, "answer", {"questions": 1}, datetime(2025, 12, 31, 12, tzinfo=UTC))
    session.commit()

    # Every day, those with nothing too, the oldest first.
    assert metered(session, org, date(2026, 9, 30), date(2026, 10, 3), "day") == [
        Metered(date(2026, 9, 30), 3, 0),
        Metered(date(2026, 10, 1), 0, 0),
        Metered(date(2026, 10, 2), 2, 2),
        Metered(date(2026, 10, 3), 0, 0),
    ]
    # Every month from the one the first day is in to the one the last is in, each whole.
    assert metered(session, org, date(2026, 8, 15), date(2026, 10, 3), "month") == [
        Metered(date(2026, 8, 1), 0, 0),
        Metered(date(2026, 9, 1), 3, 0),
        Metered(date(2026, 10, 1), 9, 2),
    ]
    assert metered(session, org, date(2025, 12, 1), date(2026, 1, 1), "month") == [
        Metered(date(2025, 12, 1), 0, 1),
        Metered(date(2026, 1, 1), 0, 0),
    ]
    assert metered(session, other, date(2026, 10, 2), date(2026, 10, 2), "day") == [
        Metered(date(2026, 10, 2), 0, 5)
    ]


def usage_page(client) -> dict:
    response = client.get("/api/organization/usage")
    assert response.status_code == 200, response.text
    return response.json()


FIGURES = (
    "projects",
    "documents",
    "storage_bytes",
    "members",
    "pages_per_month",
    "questions_per_month",
)


def test_the_usage_page_counts_what_the_plan_allows_as_its_checks_do(
    client, session, make_account, act_as, sample_pdf
):
    owner = make_account("ann@globex.example")
    org = owner.organization.id
    act_as(owner)
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    ask(client, new_chat(client, project["id"])["id"], "Notice period?")
    start, following = month_of()
    # The pages the first parse counted, and a pack of questions bought for the month.
    record(session, org_id=org, project_id=None, user_id=None, kind=UPLOAD, measures={"pages": 3})
    session.add(
        AllowancePack(org_id=org, unit="questions", quantity=100, month=start, checkout_id="cs_1")
    )
    # Last month's questions are in the history, not the month.
    last = start - timedelta(hours=1)
    record(
        session,
        org_id=org,
        project_id=None,
        user_id=None,
        kind=ANSWER,
        measures={"questions": 4},
        occurred_at=last,
    )
    # Another organisation's are in neither.
    other = make_account("bob@initech.example").organization.id
    record(
        session, org_id=other, project_id=None, user_id=None, kind=UPLOAD, measures={"pages": 50}
    )
    session.commit()

    page = usage_page(client)
    assert (page["month"], page["next_month"]) == (str(start.date()), str(following.date()))
    # What the page says a document without pages counts by.
    assert page["chars_per_page"] == CHARS_PER_PAGE
    assert {name: page[name] for name in FIGURES} == {
        "projects": {"used": 1, "allowed": 1, "bought": 0},
        "documents": {"used": 1, "allowed": 25, "bought": 0},
        "storage_bytes": {"used": sample_pdf.stat().st_size, "allowed": 100_000_000, "bought": 0},
        "members": {"used": 1, "allowed": 1, "bought": 0},
        "pages_per_month": {"used": 3, "allowed": 200, "bought": 0},
        "questions_per_month": {"used": 1, "allowed": 50, "bought": 100},
    }

    today = datetime.now(UTC).date()
    days = page["days"]
    assert [day["start"] for day in days] == [
        str(today - timedelta(days=back)) for back in range(29, -1, -1)
    ]
    used = {day["start"]: (day["pages"], day["questions"]) for day in days}
    assert used.pop(str(today)) == (3, 1)
    if str(last.date()) in used:
        assert used.pop(str(last.date())) == (0, 4)
    assert set(used.values()) == {(0, 0)}

    months = page["months"]
    assert len(months) == 12 and months[-1] == {
        "start": str(start.date()),
        "pages": 3,
        "questions": 1,
        "pages_bought": 0,
        "questions_bought": 100,
    }
    assert months[-2] == {
        "start": str(last.date().replace(day=1)),
        "pages": 0,
        "questions": 4,
        "pages_bought": 0,
        "questions_bought": 0,
    }
    first = date(start.year - (start.month < 12), start.month % 12 + 1, 1)
    assert months[0]["start"] == str(first)
    assert all(month["pages"] == month["questions"] == 0 for month in months[:-2])

    # A viewer reads it too; the seats bought and the figures agreed are what it is held to.
    viewer = make_account("vic@globex.example", owner.organization, "viewer")
    change_plan(
        session,
        session.get(Organization, org),
        "team",
        seats=4,
        overrides={"questions_per_month": 10},
    )
    session.commit()
    act_as(viewer)
    page = usage_page(client)
    assert page["members"] == {"used": 2, "allowed": 4, "bought": 0}
    assert page["questions_per_month"] == {"used": 1, "allowed": 10, "bought": 100}
    assert page["projects"] == {"used": 1, "allowed": 50, "bought": 0}


def test_the_usage_page_has_no_limits_for_the_local_account(client):
    page = usage_page(client)
    assert {name: page[name]["allowed"] for name in FIGURES} == dict.fromkeys(FIGURES)


def test_the_usage_command_prints_the_rollup(session, account, capsys):
    org = account.organization.id
    now = datetime.now(UTC)
    record(
        session,
        org_id=org,
        project_id=None,
        user_id=None,
        kind="answer",
        measures={"calls": 3, "input_tokens": 12345},
        model="google:gemini-2.5-flash-lite",
    )
    (older,) = record(
        session,
        org_id=org,
        project_id=None,
        user_id=None,
        kind="render",
        measures={"pages": 4},
        occurred_at=now - timedelta(days=2),
    )
    older.cost_micros = 2_500_000
    session.commit()

    main(["usage", "local", "--days", "3"])
    lines = capsys.readouterr().out.splitlines()
    today = now.date()
    assert lines[0] == f"Usage of Local (local) from {today - timedelta(days=2)} to {today} (UTC):"
    assert lines[1].split() == [str(today - timedelta(days=2)), "render", "pages", "4", "2.5000"]
    assert lines[2].split() == [str(today), "answer", "calls", "3"]
    assert lines[3].split() == [str(today), "answer", "input_tokens", "12,345"]
    assert len(lines) == 4

    # Only today: the older row is outside the span.
    main(["usage", "local", "--days", "1"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"Usage of Local (local) from {today} to {today} (UTC):"
    assert len(lines) == 3

    session.execute(UsageEvent.__table__.delete())
    session.commit()
    main(["usage", "local"])
    assert capsys.readouterr().out.startswith("Local (local) used nothing from ")

    with pytest.raises(SystemExit) as stopped:
        main(["usage", "nobody"])
    assert str(stopped.value) == "No organisation has the slug 'nobody'."
    with pytest.raises(SystemExit):
        main(["usage", "local", "--days", "0"])


PRICED = "google:priced"
UNPRICED = "google:unpriced"


def charge(
    session,
    org_id: int,
    kind: str,
    measures: dict[str, int],
    when: datetime,
    *,
    model: str | None = None,
    ref: tuple[str, int] | None = None,
    cost: str | None = None,
) -> None:
    """Record what an organisation used at `when`, costing its input tokens `cost` of currency
    by hand when one is given."""
    rows = record(
        session,
        org_id=org_id,
        project_id=None,
        user_id=None,
        kind=kind,
        measures=measures,
        model=model,
        ref=ref,
        occurred_at=when,
    )
    if cost is not None:
        (tokens,) = (row for row in rows if row.unit == "input_tokens")
        tokens.cost_micros = int(Decimal(cost) * 1_000_000)


def test_the_costs_of_every_organisation_add_up_costliest_first(
    session, account, make_account, monkeypatch
):
    # A token in costs a millionth and a token out two, so a cost reads as its tokens.
    monkeypatch.setattr(get_settings(), "model_prices", f"{PRICED}=1/2")
    local = account.organization.id
    globex = make_account("ann@globex.example").organization.id
    initech = make_account("bob@initech.example").organization.id
    hooli = make_account("cy@hooli.example").organization.id

    def at(day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 10, day, hour, minute, tzinfo=UTC)

    # An answer whose run was lost before midnight and started over after it: one answer,
    # charged on two days.
    tokens = {"calls": 1, "input_tokens": 1000, "output_tokens": 100}
    charge(session, local, ANSWER, tokens, at(10, 23, 30), model=PRICED, ref=(CHAT_MESSAGE, 1))
    tokens = {"calls": 2, "input_tokens": 2000, "output_tokens": 200}
    charge(session, local, ANSWER, tokens, at(11, 0, 10), model=PRICED, ref=(CHAT_MESSAGE, 1))
    # Another, which a model without a price answered.
    tokens = {"calls": 1, "input_tokens": 500}
    charge(session, local, ANSWER, tokens, at(11, 9), model=UNPRICED, ref=(CHAT_MESSAGE, 2))
    # A scanned upload: its pages, the model that read them, and the embeddings endpoint.
    charge(session, globex, UPLOAD, {"bytes": 4096, "pages": 3}, at(11, 8), ref=(DOCUMENT, 7))
    tokens = {"calls": 3, "input_tokens": 9000, "output_tokens": 300}
    charge(session, globex, OCR, tokens, at(11, 8), model=PRICED, ref=(DOCUMENT, 7))
    tokens = {"inputs": 4, "input_tokens": 40}
    charge(session, globex, EMBED, tokens, at(11, 8), model="nomic-embed-text", ref=(DOCUMENT, 7))
    tokens = {"calls": 5, "input_tokens": 50}
    charge(session, initech, ANSWER, tokens, at(10, 9), model=UNPRICED, ref=(CHAT_MESSAGE, 3))
    # The last minute before the span.
    tokens = {"calls": 9, "input_tokens": 9}
    charge(session, hooli, ANSWER, tokens, at(9, 23, 59), model=PRICED, ref=(CHAT_MESSAGE, 4))
    session.commit()

    oct10, oct11 = date(2026, 10, 10), date(2026, 10, 11)
    costs = organization_costs(session, oct10, oct11)
    assert costs == [
        OrganizationCost(
            globex,
            cost_micros=9600,
            calls=3,
            input_tokens=9040,
            output_tokens=300,
            answers=0,
            pages=3,
            days={oct11: 9600},
        ),
        OrganizationCost(
            local,
            cost_micros=3600,
            calls=4,
            input_tokens=3500,
            output_tokens=300,
            answers=2,
            pages=0,
            days={oct10: 1200, oct11: 2400},
        ),
        # Nothing it used has a price, so it comes last, however much it used.
        OrganizationCost(
            initech,
            cost_micros=None,
            calls=5,
            input_tokens=50,
            output_tokens=0,
            answers=1,
            pages=0,
        ),
    ]
    assert all(isinstance(cost.calls, int) for cost in costs)
    assert (costs[1].top_day, costs[1].days_at(2400), costs[1].days_at(1000)) == (2400, 1, 2)
    assert (costs[2].top_day, costs[2].days_at(1)) == (None, 0)
    assert summed(costs) == OrganizationCost(
        None,
        cost_micros=13200,
        calls=12,
        input_tokens=12590,
        output_tokens=600,
        answers=3,
        pages=3,
        days={oct10: 1200, oct11: 12000},
    )

    # The pages and bytes name no model, so they are no model's cost.
    assert model_costs(session, oct10, oct11) == [
        ModelCost(
            OCR, PRICED, 9600, calls=3, input_tokens=9000, output_tokens=300, unpriced_tokens=0
        ),
        ModelCost(
            ANSWER, PRICED, 3600, calls=3, input_tokens=3000, output_tokens=300, unpriced_tokens=0
        ),
        ModelCost(
            ANSWER, UNPRICED, None, calls=6, input_tokens=550, output_tokens=0, unpriced_tokens=550
        ),
        ModelCost(
            EMBED,
            "nomic-embed-text",
            None,
            calls=0,
            input_tokens=40,
            output_tokens=0,
            unpriced_tokens=40,
        ),
    ]
    assert organization_costs(session, date(2026, 10, 12), date(2026, 10, 31)) == []


def test_the_costs_command_holds_each_organisation_to_its_plans_daily_limit(
    session, account, make_account, monkeypatch, capsys
):
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    local = account.organization.id
    globex = make_account("ann@globex.example").organization.id
    initech = make_account("bob@initech.example").organization.id
    # An organisation that used nothing.
    make_account("cy@hooli.example")
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    # The free plan's limit is 1.00 a day: Globex went past it yesterday.
    tokens = {"calls": 3, "input_tokens": 1000}
    charge(session, globex, OCR, tokens, yesterday, model=PRICED, ref=(DOCUMENT, 1), cost="1.20")
    charge(session, globex, UPLOAD, {"bytes": 2048, "pages": 4}, yesterday, ref=(DOCUMENT, 1))
    tokens = {"calls": 1, "input_tokens": 100}
    charge(session, globex, ANSWER, tokens, now, model=PRICED, ref=(CHAT_MESSAGE, 1), cost="0.40")
    # The enterprise plan has no limit.
    tokens = {"calls": 2, "input_tokens": 50}
    charge(session, local, ANSWER, tokens, now, model=PRICED, ref=(CHAT_MESSAGE, 2), cost="0.05")
    charge(session, local, RENDER, {"pages": 2}, now, ref=(DOCUMENT, 2))
    tokens = {"calls": 1, "input_tokens": 30}
    charge(session, initech, ANSWER, tokens, now, model=UNPRICED, ref=(CHAT_MESSAGE, 3))
    session.commit()

    main(["costs", "--days", "2"])
    lines = capsys.readouterr().out.splitlines()
    today = now.date()
    assert lines[0] == (
        f"Model costs per organisation from {today - timedelta(days=1)} to {today} (UTC), in "
        "USD, costliest first:"
    )
    assert lines[1:] == [
        "  organisation  plan            cost   limit         top day  at limit  answers  calls"
        "  pages",
        "  globex        free          1.6000  1.0000  1.2000 (120 %)         1        1      4"
        "      4  Globex",
        "  local         enterprise    0.0500    none          0.0500                  1      2"
        "      0  Local",
        "  initech       free        unpriced  1.0000        unpriced         0        1      1"
        "      0  Initech",
        # The platform's line holds its days to DAILY_SPEND_CAP.
        "  platform                    1.6500  2.0000   1.2000 (60 %)         0        3      7"
        "      4  3 organisations",
        "1 other organisation used nothing.",
        "The top day is the costliest, with its share of the daily limit: the plan's, or the",
        "platform's DAILY_SPEND_CAP. At limit counts the days that reached it.",
        "",
        "By kind of work and model:",
        "  kind    model                cost  calls  tokens in  tokens out",
        "  ocr     google:priced      1.2000      3      1,000           0",
        "  answer  google:priced      0.4500      3        150           0",
        "  answer  google:unpriced  unpriced      1         30           0",
        "",
        "These models' tokens were recorded without a price, so no cost above counts them;",
        "MODEL_PRICES prices a model per million tokens in and out from its next row on:",
        "  google:unpriced  30 tokens  (answer)",
    ]

    # Only today: Globex is within its limit.
    main(["costs", "--days", "1"])
    lines = capsys.readouterr().out.splitlines()
    assert " ".join(lines[2].split()) == "globex free 0.4000 1.0000 0.4000 (40 %) 0 1 1 0 Globex"

    session.execute(UsageEvent.__table__.delete())
    session.commit()
    main(["costs"])
    assert capsys.readouterr().out == (
        f"No organisation used anything from {today - timedelta(days=29)} to {today} (UTC).\n"
    )
    with pytest.raises(SystemExit):
        main(["costs", "--days", "0"])
