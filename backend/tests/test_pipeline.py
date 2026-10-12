import datetime as dt

from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.indexing import enrich
from app.indexing.pipeline import StageFailure, process_document, reset_stages
from app.models import Block, Fact, Passage, Section


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
        StageFailure("embed", "Embedding endpoint unreachable", transient=True)
    ]
    session.refresh(document)

    assert document.status == "ready"
    assert stage_statuses(document)["embed"] == "failed"
    assert "unreachable" in document.stages["embed"]["detail"]
    assert all(passage.embedding is None for passage in rows(session, Passage, document))


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
