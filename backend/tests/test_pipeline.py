import datetime as dt
import json

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.embeddings import EmbeddingError
from app.indexing import enrich, pipeline
from app.indexing.enrich import ExtractedFact
from app.indexing.pipeline import STAGE_CRASHED, StageFailure, process_document, reset_stages
from app.llm import MODEL_UNAVAILABLE, LLMError
from app.models import Block, Fact, Passage, Section
from app.parsing import images
from app.scanning import UNSCANNED
from tests.conftest import FakeEmbedder


def rows(session, model, document):
    order = model.ordinal if hasattr(model, "ordinal") else model.id
    return session.scalars(
        select(model).where(model.document_id == document.id).order_by(order)
    ).all()


def stage_statuses(document):
    return {name: stage["status"] for name, stage in document.stages.items()}


def test_full_pipeline_builds_all_three_indexes(session, indexed):
    document = indexed
    assert document.status == "ready"
    assert stage_statuses(document) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "done",
        "embed": "done",
    }
    assert document.page_count == 2
    assert document.title == "Master Services Agreement"
    assert document.doc_type == "contract"
    assert document.doc_date == dt.date(2024, 3, 1)
    assert document.summary

    # The map: an outline with a summary on every section.
    sections = rows(session, Section, document)
    titles = [section.title for section in sections]
    assert titles == [
        "Master Services Agreement",
        "1. Definitions",
        "1.1 Interpretation",
        "2. Fees and payment",
        "3. Termination",
    ]
    by_title = {section.title: section for section in sections}
    assert by_title["1.1 Interpretation"].parent_id == by_title["1. Definitions"].id
    assert by_title["3. Termination"].page_start == 2
    assert all(section.summary == f"Summary of section {section.id}." for section in sections)

    # The search index: every passage embedded, and indexed with its path.
    passages = rows(session, Passage, document)
    assert passages and all(passage.embedding is not None for passage in passages)
    termination = next(p for p in passages if "ninety (90) days" in p.text)
    assert termination.context == "Master Services Agreement › 3. Termination"
    assert termination.section_id == by_title["3. Termination"].id
    assert termination.page_start == 2

    # The fact table: typed values, each traced to its source.
    facts = {fact.name: fact for fact in rows(session, Fact, document)}
    notice = facts["notice_period"]
    assert (notice.value, notice.number, notice.value_type) == ("90 days", 90, "number")
    assert notice.verified and notice.page == 2 and notice.passage_id == termination.id

    # The model cited the wrong block; the quote put the fact back where it belongs.
    effective = facts["effective_date"]
    assert effective.verified and effective.date == dt.date(2024, 3, 1) and effective.page == 1
    block = session.scalar(
        select(Block).where(
            Block.document_id == document.id, Block.ordinal == effective.block_ordinal
        )
    )
    assert "1 March 2024" in block.text

    # A quote found nowhere is kept, but flagged.
    assert facts["governing_law"].verified is False


def test_without_a_model_documents_are_still_parsed_and_searchable(
    session, services, make_document, sample_pdf
):
    services.llm.available = False
    document = make_document(sample_pdf)
    process_document(document.id, services)
    session.refresh(document)

    assert document.status == "ready"
    assert stage_statuses(document)["enrich"] == "skipped"
    assert "GOOGLE_API_KEY" in document.stages["enrich"]["detail"]
    assert document.title is None and document.display_title == "msa.pdf"
    passages = rows(session, Passage, document)
    assert passages and all(passage.embedding is not None for passage in passages)
    assert passages[0].context.startswith("msa › ")
    assert rows(session, Fact, document) == []
    assert services.llm.calls == []


def test_embedding_outage_does_not_fail_the_document(session, services, make_document, sample_pdf):
    services.embedder.fail = True
    document = make_document(sample_pdf)
    # The run reports the stage for the job to run again later (tests/test_jobs.py).
    assert process_document(document.id, services) == [
        StageFailure("embed", FakeEmbedder.UNREACHABLE, transient=True)
    ]
    session.refresh(document)

    assert document.status == "ready"
    assert stage_statuses(document)["embed"] == "failed"
    assert "unreachable" in document.stages["embed"]["detail"]
    assert all(passage.embedding is None for passage in rows(session, Passage, document))


def test_a_deployments_members_read_what_failed_without_how_it_is_set_up(
    session, services, make_document, sample_pdf, tmp_path, monkeypatch
):
    # The worker writes the stages' details, so its environment decides what they say; the
    # operator reads the reasons in the log and the job's last_error (tests/test_jobs.py).
    monkeypatch.setattr(get_settings(), "environment", "production")
    services.llm.available = False
    services.embedder.fail = True
    services.scanner = None
    document = make_document(sample_pdf)
    members = (
        "This document could not be prepared for semantic search just now, so search finds it "
        "by its words alone."
    )
    assert process_document(document.id, services) == [
        StageFailure("embed", FakeEmbedder.UNREACHABLE, transient=True, detail=members)
    ]
    session.refresh(document)
    assert document.status == "ready"
    assert document.stages["scan"] == {"status": "skipped", "detail": UNSCANNED}
    assert document.stages["enrich"] == {
        "status": "skipped",
        "detail": "Summaries and facts need the AI model, which is unavailable just now. Use "
        '"Index again" later to add them.',
    }
    assert document.stages["embed"] == {"status": "failed", "detail": EmbeddingError.members}

    # A scan cannot be read without the model, which fails it in the members' words.
    path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(path)
    scan = make_document(path)
    process_document(scan.id, services)
    session.refresh(scan)
    assert scan.status == "failed"
    assert scan.error == (
        "This file has no text layer, and reading scans and images needs the AI model, which "
        'is unavailable just now. Use "Index again" later.'
    )
    assert scan.stages["parse"]["detail"] == scan.error

    told = json.dumps([document.stages, scan.stages, scan.error])
    for setup in ("GOOGLE_API_KEY", "gemini", ".env", FakeEmbedder.address, "SCANNER_URL"):
        assert setup not in told


def test_a_deployments_operator_reads_what_its_members_are_not_told(
    session, services, make_document, sample_pdf, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "environment", "production")
    rejected = "The API key for google:gemini-2.5-flash-lite was rejected."
    services.llm.failure = LLMError(rejected, members=MODEL_UNAVAILABLE)
    document = make_document(sample_pdf)
    with caplog.at_level("WARNING", logger="app.indexing.pipeline"):
        assert process_document(document.id, services) == [
            StageFailure("enrich", rejected, transient=False, detail=MODEL_UNAVAILABLE)
        ]
    session.refresh(document)
    assert document.stages["enrich"] == {"status": "failed", "detail": MODEL_UNAVAILABLE}
    # Nothing runs the stage again, so the log is where the operator reads why.
    assert f"Stage enrich of document {document.id} failed: {rejected}" in caplog.text

    # A stage that broke names the exception's kind to the operator alone.
    def crash(session_, document_, services_, meter):
        raise ValueError("cannot split")

    monkeypatch.setitem(pipeline._STAGES, "structure", crash)
    services.llm.failure = None
    broken = make_document(sample_pdf)
    assert process_document(broken.id, services) == [
        StageFailure("structure", "Unexpected error: ValueError", False, detail=STAGE_CRASHED)
    ]
    session.refresh(broken)
    assert broken.status == "failed" and broken.error == STAGE_CRASHED
    assert "ValueError" not in json.dumps(broken.stages)


def test_reprocessing_from_enrich_keeps_the_parse(session, services, make_document, sample_pdf):
    services.llm.available = False
    document = make_document(sample_pdf)
    process_document(document.id, services)
    block_ids = [block.id for block in rows(session, Block, document)]

    # The model becomes available later: only the AI stages and the embeddings run again.
    services.llm.available = True
    session.refresh(document)
    reset_stages(document, "enrich")
    session.commit()
    assert document.status == "queued"
    assert stage_statuses(document) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "pending",
        "embed": "pending",
    }

    process_document(document.id, services)
    session.expire_all()
    session.refresh(document)
    assert document.status == "ready"
    assert stage_statuses(document)["enrich"] == "done"
    assert [block.id for block in rows(session, Block, document)] == block_ids
    assert document.title == "Master Services Agreement"
    # The new title reaches the paths that passages are indexed with.
    assert all(
        passage.context.startswith("Master Services Agreement › ")
        for passage in rows(session, Passage, document)
    )
    assert rows(session, Fact, document)


def test_scanned_image_is_read_by_ocr(session, services, make_document, tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(path)
    document = make_document(path)
    process_document(document.id, services)
    session.refresh(document)

    assert document.status == "ready"
    blocks = rows(session, Block, document)
    assert [(block.kind, block.text, block.page) for block in blocks] == [
        ("heading", "Scanned Invoice", 1),
        ("paragraph", "Total due: EUR 500", 1),
    ]


def test_scanned_image_without_a_model_fails_with_a_reason(
    session, services, make_document, tmp_path
):
    services.llm.available = False
    path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(path)
    document = make_document(path)
    process_document(document.id, services)
    session.refresh(document)

    assert document.status == "failed"
    assert "GOOGLE_API_KEY" in document.error
    assert stage_statuses(document)["parse"] == "failed"


def test_an_image_too_large_to_decode_fails_with_a_reason(
    session, services, make_document, tmp_path, monkeypatch
):
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 2_000_000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3_000_000)
    large, huge = tmp_path / "large.png", tmp_path / "huge.png"
    Image.new("RGB", (2000, 1500), "white").save(large)
    Image.new("RGB", (2600, 2600), "white").save(huge)

    # The size is read from the header, so the cap refuses the file before a pixel is decoded
    # or the model is asked; the file is what it is, so the job does not queue it again.
    document = make_document(large)
    assert process_document(document.id, services) == [
        StageFailure(
            "parse",
            "This image is 3 megapixels, and images over 2 megapixels cannot be read. Scale it "
            "down and upload it again.",
            transient=False,
        )
    ]
    session.refresh(document)
    assert document.status == "failed"
    assert document.error.startswith("This image is 3 megapixels")
    assert stage_statuses(document)["parse"] == "failed"
    assert services.llm.calls == []

    # Pillow's own, higher ceiling refuses the largest headers before the cap sees the size.
    document = make_document(huge)
    [failure] = process_document(document.id, services)
    assert failure.message == (
        "This image is over 2 megapixels, more than can be read. Scale it down and upload it again."
    )
    session.refresh(document)
    assert document.status == "failed" and document.error == failure.message


def test_flat_document_gets_sections_from_the_model(session, services, make_document, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("\n\n".join(f"Paragraph number {n} of the notes." for n in range(16)))
    document = make_document(path)
    process_document(document.id, services)

    sections = rows(session, Section, document)
    assert [(s.title, s.block_start, s.block_end) for s in sections] == [
        ("Opening remarks", 0, 8),
        ("Closing remarks", 8, 16),
    ]
    assert services.llm.calls[0][0] == "InferredSections"


def test_unsupported_file_fails(session, services, make_document, tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x00\x01")
    document = make_document(path)
    process_document(document.id, services)
    session.refresh(document)
    assert document.status == "failed"
    assert "not supported" in document.error


def test_long_documents_are_enriched_in_parts(
    session, services, make_document, sample_pdf, monkeypatch
):
    monkeypatch.setattr(enrich, "WINDOW_CHARS", 150)
    document = make_document(sample_pdf)
    process_document(document.id, services)
    session.refresh(document)

    kinds = [name for name, _ in services.llm.calls]
    assert kinds.count("WindowAnalysis") >= 2
    assert kinds[-1] == "ProfileWithSections"
    assert "DocumentAnalysis" not in kinds
    assert document.title == "Master Services Agreement"
    assert all(section.summary for section in rows(session, Section, document))
    assert {"notice_period", "effective_date"} <= {
        fact.name for fact in rows(session, Fact, document)
    }


def test_a_long_table_is_shown_to_the_model_in_part_and_gives_no_facts(
    session, services, make_document, tmp_path, monkeypatch
):
    orders = "\n".join(
        f"| ORD-{n:04} | 2026-01-{n % 28 + 1:02} | {n * 10} |" for n in range(1, 301)
    )
    path = tmp_path / "orders.md"
    path.write_text(
        "# Orders\n\nSupplier: Northwind Fasteners Ltd\n\n"
        f"| Order | Date | Amount |\n| --- | --- | --- |\n{orders}\n"
    )

    def facts(content):
        # A fact from the line above the table, and one from a row of it.
        return [
            ExtractedFact(
                name="vendor_name",
                value="Northwind Fasteners Ltd",
                type="text",
                number=None,
                date=None,
                currency=None,
                block=1,
                quote="Northwind Fasteners Ltd",
            ),
            ExtractedFact(
                name="order_number",
                value="ORD-0003",
                type="text",
                number=None,
                date=None,
                currency=None,
                block=2,
                quote="ORD-0003",
            ),
        ]

    monkeypatch.setattr(services.llm, "_facts", facts)
    document = make_document(path)
    process_document(document.id, services)

    ((kind, prompt),) = services.llm.calls
    assert kind == "DocumentAnalysis"
    assert (
        "[B2] A table of 300 rows, shown in part: its header, its first 20 rows and its last 5.\n"
        "| Order | Date | Amount |\n| --- | --- | --- |\n| ORD-0001 |"
    ) in prompt
    assert "| ORD-0020 |" in prompt and "| … | … | … |\n| ORD-0296 |" in prompt
    assert "| ORD-0021 |" not in prompt and "| ORD-0295 |" not in prompt
    assert "| ORD-0300 |" in prompt
    assert [(f.name, f.value) for f in rows(session, Fact, document)] == [
        ("vendor_name", "Northwind Fasteners Ltd")
    ]
    # Every row is searched and read all the same.
    assert any("| ORD-0150 |" in p.text for p in rows(session, Passage, document))


def test_a_sheet_s_rows_are_summarised_from_their_first_and_last(
    session, services, make_document, tmp_path
):
    lines = ["order,amount", *(f"ORD-{n:04},{n * 10}" for n in range(1, 451))]
    path = tmp_path / "orders.csv"
    path.write_text("\n".join(lines) + "\n")
    document = make_document(path)
    process_document(document.id, services)

    # 450 rows are three blocks that repeat the header (app/parsing/sheet.py), one table.
    assert len(rows(session, Block, document)) == 3
    kind, prompt = services.llm.calls[-1]
    assert kind == "DocumentAnalysis"
    assert "A table of 450 rows in blocks B0 to B2, shown in part" in prompt
    assert "| ORD-0446 |" in prompt and "| ORD-0450 |" in prompt
    assert "| ORD-0200 |" not in prompt
    session.refresh(document)
    assert document.stages["enrich"] == {"status": "done", "detail": "1 section summaries, 0 facts"}


def test_a_short_table_is_shown_whole(session, services, make_document, tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("item,price\n" + "\n".join(f"Bolt M{n},{n}.50" for n in range(1, 101)) + "\n")
    document = make_document(path)
    process_document(document.id, services)

    kind, prompt = services.llm.calls[-1]
    assert kind == "DocumentAnalysis"
    assert "A table of" not in prompt
    assert all(f"| Bolt M{n} | {n}.50 |" in prompt for n in range(1, 101))


def test_a_document_longer_than_its_requests_is_summarised_from_its_start(
    session, services, make_document, sample_pdf, monkeypatch
):
    monkeypatch.setattr(enrich, "WINDOW_CHARS", 150)
    monkeypatch.setattr(get_settings(), "enrich_max_requests", 2)
    document = make_document(sample_pdf)
    process_document(document.id, services)
    session.refresh(document)

    kinds = [name for name, _ in services.llm.calls]
    assert kinds == ["WindowAnalysis", "ProfileWithSections"]
    assert document.title == "Master Services Agreement"
    detail = document.stages["enrich"]["detail"]
    assert detail.endswith(
        ", from pages 1 to 1 of 2: the model reads no more of one document, and the rest is "
        "searched without summaries or facts"
    ), detail
    sections = rows(session, Section, document)
    assert sections[0].summary and not sections[-1].summary
    # The roll-up is told which sections were not read, and asked to summarise none of them.
    rollup = services.llm.calls[-1][1]
    assert f"[S{sections[-1].id}] {sections[-1].title}: (not read)" in rollup
    assert f"[S{sections[-1].id}]" not in rollup.split("beneath it:")[1]


def test_a_document_of_one_request_is_read_from_its_opening(
    session, services, make_document, sample_pdf, monkeypatch
):
    monkeypatch.setattr(enrich, "WINDOW_CHARS", 150)
    monkeypatch.setattr(get_settings(), "enrich_max_requests", 1)
    document = make_document(sample_pdf)
    process_document(document.id, services)
    session.refresh(document)

    ((kind, prompt),) = services.llm.calls
    assert kind == "DocumentAnalysis"
    assert "This is the opening part of a longer document, and all of it that is read." in prompt
    assert document.title == "Master Services Agreement"
    assert "from pages 1 to 1 of 2" in document.stages["enrich"]["detail"]


def test_a_block_longer_than_a_request_is_shown_in_pieces(
    session, services, make_document, tmp_path, monkeypatch
):
    monkeypatch.setattr(enrich, "WINDOW_CHARS", 150)
    path = tmp_path / "notes.txt"
    path.write_text(" ".join(f"Sentence number {n} of one long paragraph." for n in range(10)))
    document = make_document(path)
    process_document(document.id, services)

    prompts = [prompt for name, prompt in services.llm.calls if name == "WindowAnalysis"]
    assert len(prompts) == 3
    assert [p.count("[B0] (part ") for p in prompts] == [1, 1, 1]
    pieces = [line for p in prompts for line in p.splitlines() if line.startswith("[B0] (part ")]
    assert [line[: len("[B0] (part 1 of 3)")] for line in pieces] == [
        f"[B0] (part {n} of 3)" for n in (1, 2, 3)
    ]
    assert all(len(line) <= len("[B0] (part 1 of 3) ") + 150 for line in pieces)


def test_a_file_with_more_text_than_a_document_may_hold_fails_and_is_not_retried(
    session, services, make_document, sample_pdf, monkeypatch
):
    monkeypatch.setattr(get_settings(), "parse_max_chars", 100)
    document = make_document(sample_pdf)
    sentence = (
        "This file holds more than 100 characters of text, the most one document can have. "
        "Split it and upload the parts."
    )
    assert process_document(document.id, services) == [
        StageFailure("parse", sentence, transient=False)
    ]
    session.refresh(document)
    assert (document.status, document.error) == ("failed", sentence)
    assert rows(session, Block, document) == []


def test_passages_are_embedded_and_written_a_chunk_at_a_time(
    session, services, make_document, sample_pdf, monkeypatch
):
    monkeypatch.setattr(pipeline, "EMBED_CHUNK", 2)
    monkeypatch.setattr(pipeline, "WRITE_CHUNK", 3)
    asked = []
    embed = services.embedder.embed_documents

    def embed_documents(texts, usage=None):
        asked.append(len(texts))
        return embed(texts, usage)

    monkeypatch.setattr(services.embedder, "embed_documents", embed_documents)
    document = make_document(sample_pdf)
    process_document(document.id, services)

    passages = rows(session, Passage, document)
    assert len(passages) > 3 and max(asked) == 2 and sum(asked) == len(passages)
    for passage in passages:
        expected = services.embedder._vector(f"{passage.context}\n\n{passage.text}")
        assert passage.embedding_model == "fake-embedder"
        assert list(passage.embedding) == pytest.approx(expected, abs=1e-6)


def test_a_file_over_a_parse_limit_fails_and_is_not_retried(
    session, services, make_document, sample_pdf, monkeypatch
):
    monkeypatch.setattr(get_settings(), "parse_max_pages", 1)
    document = make_document(sample_pdf)
    # Not transient: the file is what it is, so the job does not queue it again.
    assert process_document(document.id, services) == [
        StageFailure(
            "parse",
            "This PDF has 2 pages, more than the 1 that can be indexed. Split it and upload "
            "the parts.",
            transient=False,
        )
    ]
    session.refresh(document)
    assert document.status == "failed"
    assert document.error.startswith("This PDF has 2 pages")
    assert stage_statuses(document)["parse"] == "failed"
