"""Usage metering: an answer's model calls and a document's indexing are recorded as its
organisation's usage whatever ends the run, an upload and a render are the member's, the rows
outlive the project and the user and go with the organisation, the answer and the document
show what they used with its cost by the configured prices, and the daily rollup groups the
rows by day in UTC, which the `usage` command prints."""

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from PIL import Image
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import func, select

import app.answers as answers_module
from app.agent.tools import TOOLS
from app.answers import answer
from app.config import get_settings
from app.indexing.pipeline import StageFailure, process_document, reset_stages
from app.jobs import ANSWER_QUESTION, INDEX_DOCUMENT, LeaseLost, claim
from app.llm import LLMError
from app.maintenance import main
from app.models import Organization, Passage, UsageEvent, User
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
    add,
    cost_of,
    daily,
    record,
)
from app.worker import HANDLERS
from tests.conftest import FakeTurn
from tests.test_answers import Held
from tests.test_api import create_project, upload
from tests.test_chats import answered, ask, new_chat
from tests.test_jobs import lose


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


def scanned_pdf(path, pages: int):
    """A PDF of `pages` pages that are pictures and nothing else, as a scanner makes them."""
    images = [Image.new("RGB", (400, 300), "white") for _ in range(pages)]
    images[0].save(path, save_all=True, append_images=images[1:])
    return path


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
        (EMBED, "inputs", passages),
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
        EMBED: shown(model="fake-embedder", inputs=passages),
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
        (EMBED, "inputs", passages),
        *model_calls(ENRICH, 1),
        (EMBED, "inputs", passages),
    ]
    # Both runs add up on the document, and the stages that were reset kept nothing from it.
    assert usage_shown(client, f"/api/documents/{document.id}") == {
        UPLOAD: shown(pages=2),
        ENRICH: shown(model=index_model, calls=2, input_tokens=20, output_tokens=10),
        EMBED: shown(model="fake-embedder", inputs=passages * 2),
    }


def test_inferring_sections_is_charged_as_its_own_kind(session, services, make_document, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("\n\n".join(f"Paragraph number {n} of the notes." for n in range(16)))
    document = make_document(path)
    process_document(document.id, services)

    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows, INFER_SECTIONS) == model_calls(INFER_SECTIONS, 1)
    assert spent(rows, UPLOAD) == []  # a text file has no pages


def test_a_scan_is_charged_per_page_read_even_when_the_stage_fails_half_way(
    session, services, make_document, tmp_path, monkeypatch
):
    document = make_document(scanned_pdf(tmp_path / "scan.pdf", 2))
    answer_page = services.llm.structured

    def second_page_fails(content, schema, usage=None):
        if len(services.llm.calls) == 1:
            services.llm.calls.append((schema.__name__, content))
            raise LLMError("The rate limit for the model was reached.", transient=True)
        return answer_page(content, schema, usage)

    monkeypatch.setattr(services.llm, "structured", second_page_fails)
    failures = process_document(document.id, services)
    assert [(failure.stage, failure.transient) for failure in failures] == [("parse", True)]

    # The page the model read before the stage failed is on the bill, as the model's call;
    # the upload's pages are not known until a parse succeeds.
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows) == [*model_calls(OCR, 1), (OCR, "pages", 1)]
    assert {row.model for row in rows} == {get_settings().index_model}

    # The retry reads both pages and is charged for both, and the upload is now two pages.
    monkeypatch.setattr(services.llm, "structured", answer_page)
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

    def embed_while_taken_over(texts):
        vectors = embed(texts)
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
    assert spent(rows, EMBED) == [(EMBED, "inputs", passages)]

    # The next worker runs the stage again and is charged for its own embedding.
    monkeypatch.setattr(services.embedder, "embed_documents", embed)
    taken = claim("w:2", [INDEX_DOCUMENT])
    assert HANDLERS[INDEX_DOCUMENT].run(taken, services) is None
    assert passages_of(session, document.id, embedded=True) == passages
    rows = rows_of(session, (DOCUMENT, document.id))
    assert spent(rows, EMBED) == [(EMBED, "inputs", passages)] * 2
    # Parse and enrich ran once: their rows were committed before the takeover.
    assert spent(rows, UPLOAD, ENRICH) == [(UPLOAD, "pages", 2), *model_calls(ENRICH, 1)]


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
    assert costs == {("calls", None), ("input_tokens", 1), ("output_tokens", 2)}

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
