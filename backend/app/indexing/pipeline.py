"""The indexing pipeline. Each stage stores its output, so any stage can be re-run alone."""

import io
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.embeddings import Embedder
from app.errors import AppError
from app.indexing.enrich import (
    ExtractedFact,
    analyze_document,
    infer_sections,
    normalize_fact_name,
    parse_iso_date,
)
from app.indexing.structure import SectionHint, build_outline
from app.llm import LLM, LLMError
from app.models import STAGES, Block, Document, Fact, Passage, Section
from app.parsing import IMAGE_TYPES, ParsedBlock, UnsupportedFile
from app.parsing.limits import ParseLimits, parse_within_limits
from app.parsing.pdf import render_page
from app.parsing.types import clean_text
from app.storage import Storage

log = logging.getLogger(__name__)

# Without parse and structure there is nothing to search; the other stages only add to it.
REQUIRED_STAGES = ("parse", "structure")
# Longest image edge sent for OCR; larger scans are scaled down to this.
MAX_IMAGE_EDGE = 2576
MISSING_ORIGINAL = (
    "The uploaded file is missing from the storage. Delete this document and upload it again."
)


@dataclass
class Services:
    llm: LLM
    embedder: Embedder
    storage: Storage


class PipelineError(AppError):
    """A stage failed for a reason the user can act on."""


class StageSkipped(Exception):
    pass


@dataclass(frozen=True)
class StageFailure:
    """A stage that failed in this run: which, why, and whether running it again in a while may
    succeed (the cause was a provider or the storage being out of reach, not the document)."""

    stage: str
    message: str
    transient: bool


Stage = Callable[[Session, Document, Services], str]


def process_document(document_id: int, services: Services) -> list[StageFailure]:
    """Run every stage that is not already done, in order, and report the stages that failed.

    A failed required stage ends the run with the document failed; a failed optional stage
    leaves it ready without that stage's output. The job that runs the pipeline decides from
    the failures whether to queue the document again (app/worker.py).
    """
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None:
            return []
        todo = [name for name in STAGES if document.stages.get(name, {}).get("status") != "done"]
        if not todo:
            # A job queued for a document that another job has since finished, such as a retry
            # that "Index again" overtook.
            document.status = "ready"
            session.commit()
            return []
        document.status = "processing"
        document.error = None
        session.commit()

        failures: list[StageFailure] = []
        for name in todo:
            failure = _run_stage(session, document, name, _STAGES[name], services)
            if failure is None:
                continue
            failures.append(failure)
            if name in REQUIRED_STAGES:
                document.status = "failed"
                document.error = failure.message
                session.commit()
                return failures

        document.status = "ready"
        session.commit()
        return failures


def reset_stages(document: Document, from_stage: str = "parse") -> None:
    """Queue a document to run again from the given stage."""
    rerun = STAGES[STAGES.index(from_stage) :]
    document.stages = {
        name: {"status": "pending", "detail": ""}
        if name in rerun
        else document.stages.get(name, {})
        for name in STAGES
    }
    document.status = "queued"
    document.error = None


def await_retry(document: Document, failure: StageFailure, notice: str) -> None:
    """Set the document up to run again from the failed stage, with the notice in that stage's
    detail for as long as the retry waits.

    The stage keeps saying it failed, and why, with the notice after it; the stages behind it
    run again too, as they do for "Index again". Without a required stage the document is
    queued, as a fresh upload is; without an optional one it stays ready, and what was indexed
    serves until the retry.
    """
    ready = document.status == "ready"
    reset_stages(document, failure.stage)
    document.stages = {
        **document.stages,
        failure.stage: {"status": "failed", "detail": f"{failure.message} {notice}"},
    }
    if ready:
        document.status = "ready"


def give_up_retrying(document: Document, failure: StageFailure, notice: str) -> None:
    """Leave the document as the last run left it, with the notice after the stage's error, and
    in the document's own error when the stage was a required one."""
    detail = f"{failure.message} {notice}"
    document.stages = {**document.stages, failure.stage: {"status": "failed", "detail": detail}}
    if document.status == "failed":
        document.error = detail


def _run_stage(
    session: Session, document: Document, name: str, stage: Stage, services: Services
) -> StageFailure | None:
    """Run one stage and record its outcome. Returns the failure, if it failed."""
    _set_stage(session, document, name, "running")
    try:
        detail = stage(session, document, services)
    except StageSkipped as skipped:
        session.rollback()
        _set_stage(session, document, name, "skipped", str(skipped))
        return None
    except (AppError, UnsupportedFile) as error:
        session.rollback()
        _set_stage(session, document, name, "failed", str(error))
        transient = isinstance(error, AppError) and error.transient
        return StageFailure(name, str(error), transient)
    except Exception as error:
        session.rollback()
        log.exception("Stage %s failed for document %s", name, document.id)
        message = f"Unexpected error: {type(error).__name__}"
        _set_stage(session, document, name, "failed", message)
        return StageFailure(name, message, transient=False)
    _set_stage(session, document, name, "done", detail)
    return None


def _set_stage(
    session: Session, document: Document, name: str, status: str, detail: str = ""
) -> None:
    document.stages = {**document.stages, name: {"status": status, "detail": detail}}
    session.commit()


def _parse(session: Session, document: Document, services: Services) -> str:
    if not services.storage.exists(document.storage_key):
        raise PipelineError(MISSING_ORIGINAL)
    # The parsers and the page renderer open files by path, so a remote original is copied to
    # this machine for the stage; the local storage hands out the file itself.
    with services.storage.localize(document.storage_key) as path:
        parsed = parse_within_limits(path, document.filename, _parse_limits())
        blocks = parsed.blocks
        note = ""

        if parsed.ocr_pages:
            if services.llm.available:
                blocks = _with_ocr(
                    path, document.filename, parsed.blocks, parsed.ocr_pages, services.llm
                )
            elif blocks:
                note = f"; {len(parsed.ocr_pages)} scanned pages skipped (OCR needs the AI model)"
            else:
                raise PipelineError(
                    "This file has no text layer, and reading scans and images needs the AI "
                    f"model. {services.llm.problem()}"
                )
    if not blocks:
        raise PipelineError("No text could be extracted from this file.")

    _delete_derived(session, document.id, Fact, Passage, Section, Block)
    session.add_all(
        Block(
            document_id=document.id,
            ordinal=ordinal,
            kind=block.kind,
            level=block.level,
            text=block.text,
            page=block.page,
            bbox=[round(value, 2) for value in block.bbox] if block.bbox else None,
        )
        for ordinal, block in enumerate(blocks)
    )
    document.page_count = parsed.page_count
    document.page_sizes = [[round(w, 2), round(h, 2)] for w, h in parsed.page_sizes]
    document.title = parsed.title
    return f"{len(blocks)} blocks{note}"


def _parse_limits() -> ParseLimits:
    settings = get_settings()
    return ParseLimits(
        timeout=settings.parse_timeout,
        memory_mb=settings.parse_memory_mb,
        max_pages=settings.parse_max_pages,
        max_rows=settings.parse_max_rows,
    )


def _with_ocr(
    path: Path, filename: str, blocks: list[ParsedBlock], ocr_pages: list[int], llm: LLM
) -> list[ParsedBlock]:
    """Transcribe the pages that have no text layer and merge them in page order."""
    suffix = Path(filename).suffix.lower()
    by_page: dict[int, list[ParsedBlock]] = defaultdict(list)
    for block in blocks:
        by_page[block.page or 1].append(block)
    for page in ocr_pages:
        if suffix == ".pdf":
            image, media_type = render_page(path, page), "image/png"
        else:
            image, media_type = _prepare_image(path, IMAGE_TYPES[suffix])
        for found in llm.ocr_page(image, media_type):
            text = clean_text(found.text)
            if text:
                level = (found.level or 1) if found.kind == "heading" else None
                by_page[page].append(ParsedBlock(found.kind, text, level=level, page=page))
    return [block for page in sorted(by_page) for block in by_page[page]]


def _prepare_image(path: Path, media_type: str) -> tuple[bytes, str]:
    """Return the image as uploaded, or scaled down when it is larger than the model reads."""
    with Image.open(path) as image:
        if max(image.size) <= MAX_IMAGE_EDGE:
            return path.read_bytes(), media_type
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        return buffer.getvalue(), "image/jpeg"


def _structure(session: Session, document: Document, services: Services) -> str:
    blocks = _blocks(session, document.id)
    hints: list[SectionHint] = []
    if services.llm.available and _needs_inferred_sections(blocks):
        try:
            hints = infer_sections(services.llm, blocks)
        except LLMError as error:
            # The outline falls back to one section per page.
            log.warning("Section inference failed for document %s: %s", document.id, error)

    stem = Path(document.filename).stem
    outline = build_outline(blocks, document.title or stem, document.page_count, hints)
    document.title = outline.title if outline.title != stem else None

    _delete_derived(session, document.id, Fact, Passage, Section)
    rows: dict[int, Section] = {}
    for ordinal, draft in enumerate(outline.sections):
        row = Section(
            document_id=document.id,
            project_id=document.project_id,
            parent_id=rows[id(draft.parent)].id if draft.parent else None,
            ordinal=ordinal,
            level=draft.level,
            title=draft.title[:1000],
            path=draft.path(outline.title),
            page_start=draft.page_start,
            page_end=draft.page_end,
            block_start=draft.block_start,
            block_end=draft.block_end,
        )
        session.add(row)
        session.flush()  # children need the parent's id
        rows[id(draft)] = row

    language = get_settings().fts_language
    session.add_all(
        Passage(
            document_id=document.id,
            project_id=document.project_id,
            section_id=rows[id(draft.section)].id,
            ordinal=ordinal,
            text=draft.text,
            context=rows[id(draft.section)].path,
            language=language,
            page_start=draft.page_start,
            page_end=draft.page_end,
            block_start=draft.block_start,
            block_end=draft.block_end,
        )
        for ordinal, draft in enumerate(outline.passages)
    )
    return f"{len(outline.sections)} sections, {len(outline.passages)} passages"


def _needs_inferred_sections(blocks: Sequence[Block]) -> bool:
    if any(block.kind == "heading" for block in blocks):
        return False
    return len(blocks) >= 15 or sum(len(block.text) for block in blocks) >= 3000


def _enrich(session: Session, document: Document, services: Services) -> str:
    if not services.llm.available:
        raise StageSkipped(f"Summaries and facts need the AI model. {services.llm.problem()}")

    blocks = _blocks(session, document.id)
    sections = session.scalars(
        select(Section).where(Section.document_id == document.id).order_by(Section.ordinal)
    ).all()
    analysis = analyze_document(services.llm, document, sections, blocks)

    profile = analysis.profile
    old_root = document.title or Path(document.filename).stem
    document.title = clean_text(profile.title)[:500] or document.title
    document.doc_type = clean_text(profile.doc_type).lower()[:100] or None
    document.doc_date = parse_iso_date(profile.doc_date)
    document.summary = profile.summary.strip() or None

    new_root = document.title or old_root
    for section in sections:
        section.summary = analysis.summaries.get(section.id)
        if new_root != old_root:
            # Paths start with the document title, and passages are indexed with their path.
            section.path = new_root + section.path[len(old_root) :]
            session.execute(
                update(Passage).where(Passage.section_id == section.id).values(context=section.path)
            )

    session.execute(delete(Fact).where(Fact.document_id == document.id))
    facts = _fact_rows(session, document, analysis.facts, blocks)
    session.add_all(facts)
    summarized = sum(1 for section in sections if section.summary)
    return f"{summarized} section summaries, {len(facts)} facts"


_WHITESPACE = re.compile(r"\s+")
_QUOTE_MARKS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text.translate(_QUOTE_MARKS)).strip().lower()


def _fact_rows(
    session: Session,
    document: Document,
    extracted: list[ExtractedFact],
    blocks: Sequence[Block],
) -> list[Fact]:
    by_ordinal = {block.ordinal: block for block in blocks}
    normalized = {block.ordinal: _normalize(block.text) for block in blocks}
    passages = session.scalars(
        select(Passage).where(Passage.document_id == document.id).order_by(Passage.ordinal)
    ).all()

    rows: list[Fact] = []
    seen: set[tuple[str, str]] = set()
    for fact in extracted:
        name = normalize_fact_name(fact.name)
        value = clean_text(fact.value)
        if not name or not value or (name, value.lower()) in seen:
            continue
        seen.add((name, value.lower()))

        # Trust the cited block only if the quote is really in it; otherwise look for the quote.
        quote = _normalize(fact.quote)
        block = by_ordinal.get(fact.block)
        verified = bool(quote) and block is not None and quote in normalized[block.ordinal]
        if not verified and quote:
            found = next((o for o, text in normalized.items() if quote in text), None)
            if found is not None:
                block, verified = by_ordinal[found], True

        passage = None
        if block is not None:
            passage = next(
                (p for p in passages if p.block_start <= block.ordinal < p.block_end),
                None,
            )
        rows.append(
            Fact(
                document_id=document.id,
                project_id=document.project_id,
                name=name,
                value_type=fact.type,
                value=value[:2000],
                number=fact.number if fact.type in ("number", "money") else None,
                date=parse_iso_date(fact.date) if fact.type == "date" else None,
                currency=(fact.currency or "").upper()[:10] or None,
                quote=clean_text(fact.quote)[:1000] or None,
                page=block.page if block else None,
                block_ordinal=block.ordinal if block else None,
                passage_id=passage.id if passage else None,
                verified=verified,
            )
        )
    return rows


def _embed(session: Session, document: Document, services: Services) -> str:
    passages = session.scalars(
        select(Passage).where(Passage.document_id == document.id).order_by(Passage.ordinal)
    ).all()
    if not passages:
        return "no passages"
    vectors = services.embedder.embed_documents(
        [f"{passage.context}\n\n{passage.text}" for passage in passages]
    )
    for passage, vector in zip(passages, vectors, strict=True):
        passage.embedding = vector
        passage.embedding_model = services.embedder.model
    return f"{len(passages)} passages"


def _blocks(session: Session, document_id: int) -> Sequence[Block]:
    return session.scalars(
        select(Block).where(Block.document_id == document_id).order_by(Block.ordinal)
    ).all()


def _delete_derived(session: Session, document_id: int, *models: type) -> None:
    for model in models:
        session.execute(delete(model).where(model.document_id == document_id))


_STAGES: dict[str, Stage] = {
    "parse": _parse,
    "structure": _structure,
    "enrich": _enrich,
    "embed": _embed,
}
