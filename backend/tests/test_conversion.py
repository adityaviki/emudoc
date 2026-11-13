"""Word, PowerPoint and Excel files shown as they look: the converter (app/converter.py) with a
stand-in for LibreOffice, the app's client against it, and the views a document gets
(app/conversion.py) with a fake converter, as the document pane, the thumbnail and a share link
ask for their pages."""

import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

import httpx
import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.conversion import (
    GAVE_UP,
    MAX_TRIES,
    TOO_MANY_PAGES,
    UNAVAILABLE,
    UNSHOWN,
    Converter,
    ConvertError,
    converter_from_settings,
)
from app.converter import (
    BUSY,
    PROFILE_SETTINGS,
    TYPES,
    UNCONVERTED,
    UNKNOWN_OUTPUT,
    ConverterServer,
    Failed,
    LibreOffice,
    Limits,
)
from app.indexing.pipeline import process_document
from app.jobs import CONVERT_DOCUMENT, LIVE, claim, finish
from app.models import Document, Job
from app.rendering import Renderer, RenderError
from app.storage import is_rendered
from app.worker import HANDLERS
from tests.conftest import APP_HEADERS
from tests.test_api import create_project, upload
from tests.test_share_links import make, opened
from tests.test_versions import give

# A stand-in for LibreOffice's command: it writes each command line it is run with to the log its
# first argument names, and for a conversion copies the PDF its second names, unless the file
# asks for something else by what it holds: `slow` takes longer than any limit, `unreadable`
# makes nothing, as LibreOffice does with a file it cannot read.
FAKE_LIBREOFFICE = """\
import sys, time
from pathlib import Path

log, pdf, args = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
with log.open("a") as out:
    out.write(" ".join(args) + "\\n")
if "--version" in args:
    print("FakeOffice 1.0")
    sys.exit(0)
source = Path(args[-1])
content = source.read_bytes()
if b"slow" in content:
    time.sleep(30)
if b"unreadable" in content:
    sys.exit(0)
# A workbook saved again as one is the workbook itself here.
into = args[args.index("--convert-to") + 1].split(":")[0]
made = pdf.read_bytes() if into == "pdf" else content
(Path(args[args.index("--outdir") + 1]) / (source.stem + "." + into)).write_bytes(made)
"""


@pytest.fixture
def converter_server(tmp_path, sample_pdf):
    """Start a converter on a free port with the stand-in for LibreOffice; returns its address
    and the stand-in's log."""
    servers: list[ConverterServer] = []

    def start(*, concurrency=2, timeout=10.0, max_bytes=1 << 20):
        fake = tmp_path / "soffice.py"
        fake.write_text(FAKE_LIBREOFFICE)
        log = tmp_path / "calls.log"
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        office = LibreOffice(
            work,
            Limits(concurrency, timeout, max_bytes),
            command=[sys.executable, str(fake), str(log), str(sample_pdf)],
        )
        office.start()
        server = ConverterServer(("127.0.0.1", 0), office)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", log

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def a_file(tmp_path, name: str, content: bytes = b"PK\x03\x04 a document") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


# The converter


def test_the_converter_answers_a_pdf_of_the_file(converter_server, tmp_path, sample_pdf):
    address, log = converter_server()
    response = httpx.post(f"{address}/convert", params={"type": "docx"}, content=b"PK a document")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == sample_pdf.read_bytes()
    health = httpx.get(f"{address}/health")
    assert health.json() == {"libreoffice": "FakeOffice 1.0"}
    # The first conversion made the profile at the start, and the file was read by its type's
    # filter alone, in a copy of that profile, as LibreOffice is never left to guess.
    calls = log.read_text().splitlines()
    assert "--infilter=HTML (StarWriter)" in calls[0]
    assert "--infilter=MS Word 2007 XML" in calls[-1]
    assert "--headless" in calls[-1] and "--convert-to pdf" in calls[-1]
    assert "-env:UserInstallation=file://" in calls[-1]
    assert (tmp_path / "work" / "profile" / "user" / "registrymodifications.xcu").read_text() == (
        PROFILE_SETTINGS
    )
    # Nothing of a conversion is left behind but the profile.
    assert [path.name for path in (tmp_path / "work").iterdir()] == ["profile"]


def test_the_converter_saves_a_workbook_again_as_one(converter_server):
    address, log = converter_server()
    convert = f"{address}/convert"
    workbook = b"PK\x03\x04 a workbook"
    response = httpx.post(convert, params={"type": "xlsx", "to": "xlsx"}, content=workbook)
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content == workbook
    calls = log.read_text().splitlines()
    assert "--infilter=Calc MS Excel 2007 XML" in calls[-1]
    assert "--convert-to xlsx:Calc MS Excel 2007 XML" in calls[-1]
    # Only a workbook is saved again, and only as a workbook.
    for kind, to in [("docx", "xlsx"), ("xlsx", "docx"), ("xlsx", "odt")]:
        refused = httpx.post(convert, params={"type": kind, "to": to}, content=b"PK")
        assert (refused.status_code, refused.text) == (400, UNKNOWN_OUTPUT)

    # The app's client, for the agent's workbooks (app/sheet_files.py).
    converter = Converter(address)
    assert converter.calculate(workbook) == workbook
    with pytest.raises(ConvertError) as unreadable:
        converter.calculate(b"unreadable")
    assert not unreadable.value.transient
    with pytest.raises(ConvertError) as away:
        Converter("http://127.0.0.1:9").calculate(workbook)
    assert away.value.transient


def test_a_converter_answering_what_is_no_workbook_is_refused():
    def answer(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"type": "xlsx", "to": "xlsx"}
        return httpx.Response(200, content=b"%PDF-1.7")

    converter = Converter("http://converter:8080")
    converter._client = httpx.Client(base_url=converter.url, transport=httpx.MockTransport(answer))
    with pytest.raises(ConvertError, match="answered with what is not a workbook"):
        converter.calculate(b"PK\x03\x04")


def test_every_type_has_its_filter_and_the_profile_blocks_what_a_document_names():
    assert set(TYPES) == {"docx", "pptx", "xlsx", "rtf", "html"}
    for setting in (
        '"MacroSecurityLevel" oor:op="fuse"><value>3<',
        '"DisableMacrosExecution" oor:op="fuse"><value>true<',
        '"BlockUntrustedRefererLinks" oor:op="fuse"><value>true<',
        'Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2<',
        'Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1<',
        '"OOXMLRecalcMode" oor:op="fuse"><value>1<',
    ):
        assert setting in PROFILE_SETTINGS, setting


def test_the_converter_refuses_what_it_cannot_convert(converter_server):
    address, _ = converter_server(timeout=1, max_bytes=100)
    convert = f"{address}/convert"
    assert httpx.post(convert, params={"type": "doc"}, content=b"x").status_code == 400
    assert httpx.post(convert, params={"type": "docx"}, content=b"x" * 101).status_code == 413
    unreadable = httpx.post(convert, params={"type": "xlsx"}, content=b"unreadable")
    assert (unreadable.status_code, unreadable.text) == (422, UNCONVERTED)
    slow = httpx.post(convert, params={"type": "pptx"}, content=b"slow", timeout=10)
    assert slow.status_code == 504
    assert slow.text == "Converting the file took longer than 1 seconds, so it was stopped."
    assert httpx.get(f"{address}/elsewhere").status_code == 404


def test_a_conversion_waits_its_turn_and_no_longer_than_one_may_take(tmp_path, sample_pdf):
    fake = tmp_path / "soffice.py"
    fake.write_text(FAKE_LIBREOFFICE)
    office = LibreOffice(
        tmp_path,
        Limits(1, 0.2, 1 << 20),
        command=[sys.executable, str(fake), str(tmp_path / "log"), str(sample_pdf)],
    )
    office._slots.acquire()
    source = tmp_path / "job" / "document.docx"
    source.parent.mkdir()
    source.write_bytes(b"PK")
    with pytest.raises(Failed) as refused:
        office.convert(source)
    assert (refused.value.status, str(refused.value)) == (HTTPStatus.SERVICE_UNAVAILABLE, BUSY)


# The app's client


def test_the_client_keeps_the_pdf_and_tells_what_went_wrong(converter_server, tmp_path, sample_pdf):
    address, _ = converter_server(timeout=1)
    converter = Converter(address)
    assert converter.check() == "FakeOffice 1.0"
    into = tmp_path / "view.pdf"
    converter.convert(a_file(tmp_path, "a.docx"), "docx", into)
    assert into.read_bytes() == sample_pdf.read_bytes()

    with pytest.raises(ConvertError) as unreadable:
        converter.convert(a_file(tmp_path, "b.docx", b"unreadable"), "docx", into)
    assert not unreadable.value.transient
    assert unreadable.value.members == UNSHOWN
    assert str(unreadable.value) == f"The converter at {address} answered 422: {UNCONVERTED}"
    with pytest.raises(ConvertError) as slow:
        converter.convert(a_file(tmp_path, "c.docx", b"slow"), "docx", into)
    assert not slow.value.transient


def test_a_converter_out_of_reach_or_busy_is_a_passing_problem(tmp_path):
    away = Converter("http://127.0.0.1:9")
    with pytest.raises(ConvertError) as unreachable:
        away.convert(a_file(tmp_path, "a.docx"), "docx", tmp_path / "view.pdf")
    assert unreachable.value.transient
    assert unreachable.value.members == UNAVAILABLE
    with pytest.raises(ConvertError):
        away.check()

    def answer(status, body=b""):
        converter = Converter("http://converter:8080")
        converter._client = httpx.Client(
            base_url=converter.url,
            transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)),
        )
        return converter

    with pytest.raises(ConvertError) as busy:
        answer(503, BUSY.encode()).convert(a_file(tmp_path, "a.docx"), "docx", tmp_path / "v.pdf")
    assert busy.value.transient
    with pytest.raises(ConvertError) as nonsense:
        answer(200, b"<html>").convert(a_file(tmp_path, "a.docx"), "docx", tmp_path / "v.pdf")
    assert not nonsense.value.transient
    assert (
        str(nonsense.value)
        == "The converter at http://converter:8080 answered with what is not a PDF."
    )


def test_the_setting_names_a_converter_or_none(monkeypatch):
    settings = get_settings()
    for value in ("", "off", " OFF "):
        monkeypatch.setattr(settings, "converter_url", value)
        assert converter_from_settings(settings) is None
    monkeypatch.setattr(settings, "converter_url", "http://converter:8080/")
    assert converter_from_settings(settings).url == "http://converter:8080"
    for wrong in ("converter:8080", "ftp://converter", "http://converter:8080/convert"):
        with pytest.raises(ValidationError) as refused:
            Settings(_env_file=None, converter_url=wrong)
        assert "CONVERTER_URL names the converter" in str(refused.value)


# A page's size, measured by a render child


def test_a_render_child_measures_a_pdfs_pages(sample_pdf, tmp_path):
    renderer = Renderer(1)
    try:
        sizes = renderer.measure(sample_pdf)
        pdf = pdfium.PdfDocument(str(sample_pdf))
        assert sizes == [[round(side, 2) for side in pdf[i].get_size()] for i in range(len(pdf))]
        assert len(sizes) == 2
        damaged = tmp_path / "damaged.pdf"
        damaged.write_bytes(b"%PDF-1.7\nnothing else")
        with pytest.raises(RenderError) as refused:
            renderer.measure(damaged)
        assert refused.value.outcome == "refused"
        assert str(refused.value) == (
            "The PDF could not be read: it is damaged in a way the viewer cannot read past."
        )
    finally:
        renderer.close()


# A document's view


class FakeConverter:
    """Stands in for the converter: answers the PDF it was given for any file, and a workbook
    saved again as `calculating` would work its formulas out (the workbook itself by default), or
    raises `refusal` while it is set."""

    def __init__(self, pdf: Path):
        self.pdf = pdf
        self.url = "http://converter:8080"
        self.refusal: ConvertError | None = None
        self.converted: list[str] = []
        self.calculating: Callable[[bytes], bytes] = lambda workbook: workbook

    def check(self) -> str:
        return "FakeOffice 1.0"

    def convert(self, source: Path, kind: str, into: Path) -> None:
        self.converted.append(kind)
        if self.refusal is not None:
            raise self.refusal
        into.write_bytes(self.pdf.read_bytes())

    def calculate(self, workbook: bytes) -> bytes:
        self.converted.append("calculated")
        if self.refusal is not None:
            raise self.refusal
        return self.calculating(workbook)


@pytest.fixture
def converter(services, sample_pdf) -> FakeConverter:
    services.converter = FakeConverter(sample_pdf)
    return services.converter


def conversions(session) -> list[Job]:
    session.expire_all()
    return session.scalars(select(Job).where(Job.kind == CONVERT_DOCUMENT).order_by(Job.id)).all()


def run_conversion(services) -> Job:
    job = claim("test:1", [CONVERT_DOCUMENT])
    assert job is not None
    result = HANDLERS[CONVERT_DOCUMENT].run(job, services)
    assert finish(job, "test:1", "done")
    job.result = result
    return job


def indexed(client, services, path: Path) -> dict:
    project = create_project(client)
    document = upload(client, project["id"], path)["documents"][0]
    process_document(document["id"], services)
    return document


def test_a_word_file_is_shown_by_the_pages_the_converter_made_of_it(
    client, session, services, converter, sample_docx, sample_pdf
):
    document = indexed(client, services, sample_docx)
    assert client.get(f"/api/documents/{document['id']}").json()["view_state"] == "queued"
    (job,) = conversions(session)
    assert job.payload["document_id"] == document["id"]

    run_conversion(services)
    assert converter.converted == ["docx"]
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["view_state"], shown["view_error"]) == ("ready", None)
    sizes = client.get(f"/api/documents/{document['id']}/content").json()["view_page_sizes"]
    pdf = pdfium.PdfDocument(str(sample_pdf))
    assert sizes == [[round(side, 2) for side in pdf[i].get_size()] for i in range(len(pdf))]
    row = session.get(Document, document["id"])
    assert services.storage.exists(row.view_key)
    assert row.view_key.endswith("/view.pdf") and not is_rendered(row.view_key)

    # Its pages and its thumbnail are drawn from the view, as a PDF's are from the PDF.
    page = client.get(f"/api/documents/{document['id']}/pages/2.png")
    assert (page.status_code, page.headers["content-type"]) == (200, "image/png")
    assert client.get(f"/api/documents/{document['id']}/pages/3.png").status_code == 404
    assert client.get(f"/api/documents/{document['id']}/thumbnail.png").status_code == 200
    # A share link shows the same pages.
    made = make(client, document["id"])
    with TestClient(client.app, headers=APP_HEADERS) as visitor:
        shared = opened(visitor, made)
        assert shared.json()["content"]["view_page_sizes"] == sizes
        assert visitor.get(f"/api/shared/{made['id']}/pages/1.png").status_code == 200


def test_what_has_pages_of_its_own_or_no_converter_gets_no_view(
    client, session, services, sample_pdf, sample_docx, sample_csv
):
    document = indexed(client, services, sample_docx)
    assert client.get(f"/api/documents/{document['id']}").json()["view_state"] is None
    assert client.get("/api/status").json()["views"] is False
    refused = client.post(f"/api/documents/{document['id']}/view")
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("This document is shown by its text")
    assert client.get(f"/api/documents/{document['id']}/pages/1.png").status_code == 404
    assert client.get(f"/api/documents/{document['id']}/thumbnail.png").status_code == 404

    services.converter = FakeConverter(sample_pdf)
    assert client.get("/api/status").json()["views"] is True
    for path in (sample_pdf, sample_csv):
        other = indexed(client, services, path)
        assert client.get(f"/api/documents/{other['id']}").json()["view_state"] is None
        assert client.post(f"/api/documents/{other['id']}/view").status_code == 409
    assert conversions(session) == []


def test_opening_a_document_without_a_view_has_one_made_once(
    client, session, services, sample_docx, sample_pdf, account, make_account, act_as
):
    document = indexed(client, services, sample_docx)
    services.converter = FakeConverter(sample_pdf)
    # A viewer opens it: reading is all it takes.
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    opened = client.post(f"/api/documents/{document['id']}/view")
    assert (opened.status_code, opened.json()["view_state"]) == (200, "queued")
    assert client.post(f"/api/documents/{document['id']}/view").json()["view_state"] == "queued"
    (job,) = conversions(session)
    # A member is looking at it, so it goes ahead of the uploads.
    assert job.priority == LIVE
    run_conversion(services)
    assert client.post(f"/api/documents/{document['id']}/view").json()["view_state"] == "ready"
    assert len(conversions(session)) == 1


def test_a_file_the_converter_cannot_read_says_so_and_may_be_tried_again(
    client, session, services, converter, sample_docx, monkeypatch
):
    monkeypatch.setattr(get_settings(), "environment", "production")
    converter.refusal = ConvertError("The converter at http://converter:8080 answered 422: …")
    document = indexed(client, services, sample_docx)
    assert run_conversion(services).result is None
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["view_state"], shown["view_error"]) == ("failed", UNSHOWN)
    # The document itself is as indexed as before: the view is no stage of it.
    assert shown["status"] == "ready"

    converter.refusal = None
    assert client.post(f"/api/documents/{document['id']}/view").json()["view_state"] == "queued"
    run_conversion(services)
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["view_state"], shown["view_error"]) == ("ready", None)


def test_a_converter_out_of_reach_is_asked_again_later_then_given_up(
    client, session, services, converter, sample_docx, monkeypatch
):
    monkeypatch.setattr(get_settings(), "environment", "production")
    converter.refusal = ConvertError("The converter could not be reached.", transient=True)
    document = indexed(client, services, sample_docx)
    for tries in range(1, MAX_TRIES):
        assert run_conversion(services).result is None
        waiting = conversions(session)[-1]
        assert waiting.status == "queued" and waiting.payload["try"] == tries + 1
        assert waiting.run_at > datetime.now(UTC)
        assert client.get(f"/api/documents/{document['id']}").json()["view_state"] == "queued"
        session.execute(
            Job.__table__.update().where(Job.id == waiting.id).values(run_at=datetime.now(UTC))
        )
        session.commit()
    # The last try gives up, which the operator hears of; the member is told it may be tried
    # again later.
    gave_up = run_conversion(services)
    assert gave_up.result.startswith(f"The converter stayed out of reach for {MAX_TRIES} tries")
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["view_state"], shown["view_error"]) == ("failed", GAVE_UP)


def test_a_view_of_too_many_pages_is_not_kept(
    client, services, converter, sample_docx, monkeypatch
):
    monkeypatch.setattr(get_settings(), "parse_max_pages", 1)
    monkeypatch.setattr(get_settings(), "environment", "production")
    document = indexed(client, services, sample_docx)
    run_conversion(services)
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["view_state"], shown["view_error"]) == ("failed", TOO_MANY_PAGES.format(limit=1))


def test_a_new_version_drops_the_view_of_the_file_before_and_gets_its_own(
    client, session, services, converter, sample_docx
):
    document = indexed(client, services, sample_docx)
    run_conversion(services)
    row = session.get(Document, document["id"])
    first_view = row.view_key
    assert services.storage.exists(first_view)

    assert (
        give(client, document["id"], "policy.docx", sample_docx.read_bytes() + b" ").status_code
        == 201
    )
    session.expire_all()
    row = session.get(Document, document["id"])
    assert (row.view_state, row.view_page_sizes) == (None, [])
    assert not services.storage.exists(first_view)
    process_document(document["id"], services)
    run_conversion(services)
    session.expire_all()
    row = session.get(Document, document["id"])
    assert row.view_state == "ready" and row.view_key != first_view
    assert services.storage.exists(row.view_key)


def test_a_view_made_of_a_file_replaced_meanwhile_is_not_kept(
    client, session, services, converter, sample_docx
):
    document = indexed(client, services, sample_docx)
    row = session.get(Document, document["id"])
    first_view = row.view_key

    real = converter.convert

    def replaced_meanwhile(source, kind, into):
        real(source, kind, into)
        with type(session)(bind=session.get_bind()) as other:
            moved = other.get(Document, document["id"])
            moved.storage_key = moved.storage_key.replace("original", "versions/2/original")
            other.commit()

    converter.convert = replaced_meanwhile
    run_conversion(services)
    session.expire_all()
    assert session.get(Document, document["id"]).view_state == "queued"
    assert not services.storage.exists(first_view)


def test_a_deleted_document_is_not_converted(client, session, services, converter, sample_docx):
    document = indexed(client, services, sample_docx)
    assert client.delete(f"/api/documents/{document['id']}").status_code == 204
    run_conversion(services)
    assert converter.converted == []
