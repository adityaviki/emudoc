"""Drawing PDF pages (app/rendering.py, app/parsing/render.py): in child processes, side by side
and within limits; the routes that serve the pages; the scans the parse stage reads; and a PDF's
first pages drawn ahead once it is parsed."""

import os
import signal
import subprocess
import sys
import threading
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image
from sqlalchemy import select

import app.rendering as rendering
from app.config import get_settings
from app.deadletters import failed_jobs
from app.indexing.pipeline import process_document
from app.jobs import RENDER_PAGES, claim, finish
from app.models import Document, Job, UsageEvent
from app.parsing.limits import BACKEND_DIR
from app.parsing.render import UNDRAWABLE
from app.rendering import (
    CRASHED,
    FOR_PANE,
    MAX_EDGE,
    STOPPING,
    TIMED_OUT,
    Renderer,
    RenderError,
    draw_ahead,
)
from app.usage import RENDER
from app.worker import HANDLERS, Worker
from tests.test_api import create_project, upload
from tests.test_jobs import wait_until
from tests.test_metrics import Counted
from tests.test_pipeline import scanned_pdf


@pytest.fixture(autouse=True)
def drawing_ahead(monkeypatch):
    # The suite draws nothing ahead (tests/conftest.py); these tests draw as a deployment does.
    monkeypatch.setattr(get_settings(), "prerender_pages", 3)


@pytest.fixture
def renderer():
    made = Renderer(2, 1024)
    yield made
    made.close()


@pytest.fixture
def huge_pdf(tmp_path) -> Path:
    """One page as large as the format allows, 200 inches a side."""
    path = tmp_path / "huge.pdf"
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(14400, 14400)
    pdf.save(path)
    pdf.close()
    return path


def size_of(png: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        return image.size


def pids(renderer: Renderer) -> set[int]:
    return {child.process.pid for child in renderer._children}


def test_a_page_is_drawn_at_twice_its_size_and_never_past_the_edge(renderer, sample_pdf, huge_pdf):
    # An A4 page, 595 by 842 points.
    width, height = size_of(renderer.render(sample_pdf, 1, purpose=FOR_PANE))
    assert abs(width - 1190) <= 1 and abs(height - 1684) <= 1
    # At twice its size the page would be 28,800 pixels a side, a bitmap of over 3 GB.
    assert size_of(renderer.render(huge_pdf, 1, purpose=FOR_PANE)) == (MAX_EDGE, MAX_EDGE)
    # A thumbnail is as long as asked, whatever the page's size.
    width, height = size_of(renderer.render(sample_pdf, 1, purpose=FOR_PANE, scale=None, edge=320))
    assert height == 320 and width < 320


def test_a_page_that_cannot_be_drawn_is_refused_by_a_process_that_goes_on(
    renderer, sample_pdf, tmp_path
):
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.4 and nothing a reader could use")
    renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    drew = pids(renderer)
    for path, page, message in (
        (sample_pdf, 9, "This PDF has no page 9."),
        (damaged, 1, UNDRAWABLE),
    ):
        with pytest.raises(RenderError) as refused:
            renderer.render(path, page, purpose=FOR_PANE)
        assert (str(refused.value), refused.value.outcome) == (message, "refused")
        assert refused.value.transient is False
    renderer.render(sample_pdf, 2, purpose=FOR_PANE)
    assert pids(renderer) == drew


def test_a_page_past_the_time_limit_is_stopped_with_its_process(renderer, sample_pdf, caplog):
    renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    slow = pids(renderer)
    with pytest.raises(RenderError) as stopped:
        renderer.render(sample_pdf, 2, purpose=FOR_PANE, timeout=0.001)
    assert stopped.value.outcome == "timeout" and stopped.value.transient is False
    assert str(stopped.value) == TIMED_OUT.format(limit="0.001 seconds")
    assert "a page took longer than RENDER_TIMEOUT (0.001 seconds)" in caplog.text
    # The process that was drawing it is gone, and a fresh one draws the next page.
    assert not pids(renderer) & slow
    assert size_of(renderer.render(sample_pdf, 2, purpose=FOR_PANE))
    assert pids(renderer) and not pids(renderer) & slow


def test_a_page_past_the_memory_cap_is_stopped_with_its_process(renderer, huge_pdf, sample_pdf):
    # The edge lifted, the bitmap needs more than the 1,024 MB the process may hold.
    with pytest.raises(RenderError) as stopped:
        renderer.render(huge_pdf, 1, purpose=FOR_PANE, edge=40_000)
    assert stopped.value.outcome == "out_of_memory"
    assert "needs more than the 1,024 MB of memory allowed" in str(stopped.value)
    assert size_of(renderer.render(sample_pdf, 1, purpose=FOR_PANE))


def test_a_page_held_up_holds_no_other_page_up(renderer, sample_pdf):
    # The process-wide lock made every page wait for the one being drawn. Now a process that
    # stops in the middle of a page leaves the next page to another.
    renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    (held,) = renderer._children
    os.kill(held.process.pid, signal.SIGSTOP)
    drawn: list[bytes] = []
    first = threading.Thread(
        target=lambda: drawn.append(renderer.render(sample_pdf, 1, purpose=FOR_PANE))
    )
    try:
        first.start()
        # It took the held process, the one given back last.
        wait_until(lambda: renderer._free.qsize() == 1)
        assert size_of(renderer.render(sample_pdf, 2, purpose=FOR_PANE))
        assert first.is_alive() and not drawn
    finally:
        os.kill(held.process.pid, signal.SIGCONT)
        first.join(10)
    assert size_of(drawn[0])


def test_a_process_that_dies_is_replaced(renderer, sample_pdf, caplog):
    renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    (dead,) = renderer._children
    dead.process.kill()
    dead.process.wait()
    with pytest.raises(RenderError) as crashed:
        renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    assert (str(crashed.value), crashed.value.outcome) == (CRASHED, "crashed")
    assert "A render process ended with code -9 while it drew a page" in caplog.text
    assert size_of(renderer.render(sample_pdf, 1, purpose=FOR_PANE))


def test_a_closed_renderer_draws_nothing_and_says_to_try_again(renderer, sample_pdf):
    renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    (child,) = renderer._children
    renderer.close()
    assert child.process.poll() is not None
    with pytest.raises(RenderError) as stopping:
        renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    assert (str(stopping.value), stopping.value.transient) == (STOPPING, True)


def test_the_render_process_loads_the_parsers_and_nothing_else_of_the_app():
    # Like the parse child (app/parsing/limits.py): no settings, no database, no keys.
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, app.parsing.render; "
            "print(' '.join(sorted(m for m in sys.modules if m.split('.')[0] == 'app')))",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert set(loaded) == {
        "app",
        "app.errors",
        "app.parsing",
        "app.parsing.limits",
        "app.parsing.render",
        "app.parsing.types",
    }


def test_the_renders_are_counted_by_what_for_and_how_they_ended(renderer, sample_pdf):
    counted = Counted(
        ("emudoc_renders_total", {"purpose": "pane", "outcome": "drawn"}),
        ("emudoc_renders_total", {"purpose": "pane", "outcome": "refused"}),
        ("emudoc_render_seconds_count", {"purpose": "pane"}),
    )
    renderer.render(sample_pdf, 1, purpose=FOR_PANE)
    with pytest.raises(RenderError):
        renderer.render(sample_pdf, 9, purpose=FOR_PANE)
    assert counted("emudoc_renders_total", purpose="pane", outcome="drawn") == 1
    assert counted("emudoc_renders_total", purpose="pane", outcome="refused") == 1
    assert counted("emudoc_render_seconds_count", purpose="pane") == 2


# The routes


def renders_charged(session, document_id: int) -> list[tuple[int | None, int]]:
    session.expire_all()
    rows = session.scalars(
        select(UsageEvent).where(UsageEvent.kind == RENDER, UsageEvent.ref_id == document_id)
    )
    return [(row.user_id, row.quantity) for row in rows]


def test_a_page_that_cannot_be_drawn_now_is_a_503_to_try_again(
    client, session, services, sample_pdf, monkeypatch
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    closed = Renderer(1)
    closed.close()
    monkeypatch.setattr(rendering, "_renderer", closed)

    for path in ("pages/1.png", "thumbnail.png"):
        response = client.get(f"/api/documents/{document['id']}/{path}")
        assert response.status_code == 503 and response.headers["retry-after"] == "5"
        assert response.json()["detail"] == STOPPING
    assert renders_charged(session, document["id"]) == []


def test_a_page_too_slow_to_draw_is_a_404_that_says_why(
    client, session, services, sample_pdf, monkeypatch
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    monkeypatch.setattr(get_settings(), "render_timeout", 0.001)

    response = client.get(f"/api/documents/{document['id']}/pages/2.png")
    assert response.status_code == 404
    assert response.json()["detail"] == TIMED_OUT.format(limit="0.001 seconds")
    key = session.get(Document, document["id"]).page_image_key(2)
    assert not services.storage.exists(key)
    assert renders_charged(session, document["id"]) == []


# The scans


def test_a_scanned_page_that_cannot_be_drawn_fails_the_parse_saying_which(
    session, services, make_document, tmp_path, monkeypatch
):
    document = make_document(scanned_pdf(tmp_path / "scan.pdf", 2))
    monkeypatch.setattr(get_settings(), "render_timeout", 0.001)
    failures = process_document(document.id, services)
    session.refresh(document)
    assert document.status == "failed"
    assert [(failure.stage, failure.transient) for failure in failures] == [("parse", False)]
    assert document.stages["parse"]["detail"] == (
        "Page 1 could not be drawn to read it as a scan. " + TIMED_OUT.format(limit="0.001 seconds")
    )


# The first pages, drawn ahead


def drawing_jobs(session) -> list[Job]:
    session.expire_all()
    return session.scalars(select(Job).where(Job.kind == RENDER_PAGES).order_by(Job.id)).all()


def run_drawing(services) -> Job:
    job = claim("test:1", [RENDER_PAGES])
    assert job is not None
    assert HANDLERS[RENDER_PAGES].run(job, services) is None
    assert finish(job, "test:1", "done")
    return job


def test_a_parsed_pdf_has_its_first_pages_drawn_ahead(
    client, session, services, account, sample_pdf, monkeypatch
):
    monkeypatch.setattr(get_settings(), "prerender_pages", 1)
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    row = session.get(Document, document["id"])

    (job,) = drawing_jobs(session)
    assert job.payload == {"document_id": row.id, "pages": [1]}
    assert job.org_id == account.organization.id and job.project_id == project["id"]
    # Before the uploads waiting to be indexed: it is a moment's work, worth it only early.
    assert job.priority < 0
    assert not services.storage.exists(row.page_image_key(1))

    run_drawing(services)
    assert services.storage.exists(row.page_image_key(1))
    assert not services.storage.exists(row.page_image_key(2))
    # The organisation's, with no member, as the stages' work is.
    assert renders_charged(session, row.id) == [(None, 1)]

    # The pane is served the page kept, and nobody is charged again; the next page is drawn
    # when it is asked for, and is the member's.
    assert client.get(f"/api/documents/{row.id}/pages/1.png").status_code == 200
    assert client.get(f"/api/documents/{row.id}/pages/2.png").status_code == 200
    assert renders_charged(session, row.id) == [(None, 1), (account.user.id, 1)]


def test_a_parse_run_again_draws_nothing_kept_already(session, services, make_document, sample_pdf):
    document = make_document(sample_pdf)
    process_document(document.id, services)
    run_drawing(services)
    # Both of its pages, PRERENDER_PAGES being more.
    assert renders_charged(session, document.id) == [(None, 2)]

    assert draw_ahead(document.id, [1, 2], services.storage) == 0
    assert renders_charged(session, document.id) == [(None, 2)]


def test_nothing_is_drawn_ahead_but_a_pdf_or_with_the_setting_off(
    session, services, make_document, sample_pdf, sample_docx, monkeypatch
):
    process_document(make_document(sample_docx).id, services)
    assert drawing_jobs(session) == []
    monkeypatch.setattr(get_settings(), "prerender_pages", 0)
    process_document(make_document(sample_pdf).id, services)
    assert drawing_jobs(session) == []


def test_a_document_deleted_or_quarantined_since_is_not_drawn(
    session, services, make_document, sample_pdf
):
    gone = make_document(sample_pdf, "gone.pdf")
    flagged = make_document(sample_pdf, "flagged.pdf")
    for document in (gone, flagged):
        process_document(document.id, services)
    flagged.status = "quarantined"
    session.delete(gone)
    session.commit()

    for document_id in (gone.id, flagged.id):
        assert draw_ahead(document_id, [1, 2], services.storage) == 0
    assert not services.storage.exists(flagged.page_image_key(1))
    session.rollback()
    assert session.scalars(select(UsageEvent).where(UsageEvent.kind == RENDER)).all() == []


def test_a_page_that_cannot_be_drawn_ahead_is_left_for_the_pane(
    session, services, make_document, sample_pdf, monkeypatch, caplog
):
    document = make_document(sample_pdf)
    process_document(document.id, services)
    monkeypatch.setattr(get_settings(), "render_timeout", 0.001)
    job = run_drawing(services)
    assert not services.storage.exists(document.page_image_key(1))
    assert renders_charged(session, document.id) == []
    assert f"Page 1 of document {document.id} was not drawn ahead" in caplog.text
    # The job is done, not failed: nobody needs to hear of it.
    session.expire_all()
    assert session.get(Job, job.id).status == "done"


def test_a_worker_draws_the_first_pages_while_it_indexes(
    session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    worker = Worker(services, concurrency=2)
    worker.start()
    try:
        wait_until(lambda: services.storage.exists(document.page_image_key(2)))
        wait_until(lambda: [job.status for job in drawing_jobs(session)] == ["done"])
    finally:
        worker.stop()
    assert {(row.user_id, row.quantity) for row in session.scalars(select(UsageEvent))} >= {
        (None, 2)
    }


def test_a_drawing_job_given_up_on_is_named_by_its_document(
    session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf, "invoice.pdf")
    process_document(document.id, services)
    job = claim("test:1", [RENDER_PAGES])
    assert finish(job, "test:1", "failed", "The worker lost it 3 times.")
    (failure,) = failed_jobs(session)
    assert failure.about == "local / Test project / invoice.pdf"
