"""Test setup: a separate database, a temporary storage directory, and fake AI services."""

import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import httpx
import psycopg
import pytest
from botocore.exceptions import ClientError
from sqlalchemy.engine import URL, make_url

# The container docker-compose.yml runs; the app itself has no default database.
SERVER = os.environ.get("TEST_DATABASE_SERVER", "emudoc:emudoc@127.0.0.1:5437")
TEST_DATABASE = "emudoc_test"
TEST_DATABASE_URL = f"postgresql+psycopg://{SERVER}/{TEST_DATABASE}"
# Set to run against a server that is not on this machine, such as a CI job's database container
# reached by its service name.
ALLOW_REMOTE = "TEST_DATABASE_ALLOW_REMOTE"


def _on_this_machine(host: str | None) -> bool:
    # No host means a Unix socket.
    if not host or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def refuse_unless_test_database(url: str | URL) -> None:
    """Stop the run before anything connects unless `url` is the local test database.

    The suite truncates tables, drops the schema and rebuilds a column, so it must never reach a
    database that holds anything. Two things could point it at one: the app reading its settings
    before this module set `DATABASE_URL` (they are cached at import, so the engine would be the
    developer's own), and `TEST_DATABASE_SERVER` naming a shared server, where the suite would
    create and wreck an `emudoc_test`. A `UsageError` makes pytest stop with the message and
    collect nothing.
    """
    url = make_url(url)
    shown = url.render_as_string(hide_password=True)
    if url.database != TEST_DATABASE:
        raise pytest.UsageError(
            f"The tests would run against {shown}, which is not the {TEST_DATABASE} database "
            "they truncate and rebuild. The app's settings were read before tests/conftest.py "
            "chose the database: run `uv run pytest` from the backend directory, so that the "
            "app is only imported through tests/conftest.py."
        )
    if not _on_this_machine(url.host) and not os.environ.get(ALLOW_REMOTE):
        raise pytest.UsageError(
            f"The tests would run against {shown}, a server that is not on this machine, and "
            f"they create, truncate and drop tables in its {TEST_DATABASE} database. Point "
            f"TEST_DATABASE_SERVER at a local server, or set {ALLOW_REMOTE}=1 if that server "
            "exists for the tests."
        )


def _ensure_database() -> None:
    with psycopg.connect(f"postgresql://{SERVER}/postgres", autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,)
        ).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{TEST_DATABASE}"')


# The app reads its settings at import time, so the environment is set before importing it.
refuse_unless_test_database(TEST_DATABASE_URL)
_ensure_database()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# The local storage of the whole run; `session` empties it with the tables.
STORAGE_ROOT = Path(tempfile.mkdtemp(prefix="emudoc-test-"))
os.environ["STORAGE_URL"] = STORAGE_ROOT.as_uri()
# Pinned, so the tests do not follow whichever models the developer's .env selects. The
# embedding size is the one `FakeEmbedder` produces and the migrations size the column by.
os.environ["AGENT_MODEL"] = os.environ["INDEX_MODEL"] = "google:gemini-2.5-flash-lite"
os.environ["EMBEDDING_MODEL"] = "nomic-embed-text"
os.environ["EMBEDDING_DIM"] = "768"
# Most tests drive the API as the local account; `signed_in_only` restores the production rule.
os.environ["LOCAL_ACCOUNT"] = "true"
# The messages the tests pin tell a developer to edit `.env`; a production shell says otherwise.
os.environ["ENVIRONMENT"] = "development"
# The app under test starts no worker threads of its own: tests run the pipeline directly, or
# build a `Worker` by hand (tests/test_jobs.py) and stop it.
os.environ["RUN_WORKER"] = "false"

from alembic import command  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    BinaryContent,
    FinishReason,
    ModelResponse,
    ModelResponsePart,
    PartStartEvent,
    ToolCallPart,
)
from pydantic_ai.usage import RequestUsage  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.accounts import Account, local_account  # noqa: E402
from app.api.deps import SAME_APP_HEADER, SessionDep, get_account  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, alembic_config, engine  # noqa: E402
from app.embeddings import EmbeddingError  # noqa: E402
from app.indexing.enrich import (  # noqa: E402
    DocumentAnalysis,
    ExtractedFact,
    InferredSection,
    InferredSections,
    ProfileWithSections,
    SectionSummary,
    WindowAnalysis,
)
from app.indexing.pipeline import Services, process_document  # noqa: E402
from app.jobs import queue_index  # noqa: E402
from app.llm import OcrBlock, OcrPage  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Document, Membership, Organization, Project, User  # noqa: E402
from app.parsing import MIME_TYPES  # noqa: E402
from app.storage import original_key, storage_from_settings  # noqa: E402

# The engine is what every fixture below destroys data through; had the app been imported before
# the environment above was set, it would point at the database in `.env`.
refuse_unless_test_database(engine.url)


class FakeEmbedder:
    """Bag-of-words vectors: texts that share words are close, with no model involved."""

    model = "fake-embedder"
    dim = 768

    def __init__(self):
        # An endpoint that cannot be reached, which is a passing condition to the pipeline.
        self.fail = False

    def _vector(self, text_: str) -> list[float]:
        if self.fail:
            raise EmbeddingError("Embedding endpoint unreachable", transient=True)
        vector = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text_.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text_) for text_ in texts]

    def embed_query(self, text_: str) -> list[float]:
        return self._vector(text_)

    def check(self) -> str | None:
        return "Embedding endpoint unreachable" if self.fail else None


_BLOCK_LINE = re.compile(r"^\[B(\d+)\](?: \(p\.\d+\))? (.*)$", re.MULTILINE)

MISSING_KEY = "Set GOOGLE_API_KEY in .env to use google:gemini-2.5-flash-lite."
# What the frontend sends with every request; a change without it is refused as cross-site.
APP_HEADERS = {SAME_APP_HEADER: "fetch"}


class FakeLLM:
    """Stands in for the model: canned structured replies and scripted agent turns."""

    def __init__(self):
        self.available = True
        # Raised by every indexing request while set: a provider that is down (transient) or
        # one that refuses the request (not), as the pipeline sees them.
        self.failure: Exception | None = None
        self.calls: list[tuple[str, object]] = []
        self.agent_turns: list[FakeTurn] = []
        self.agent_requests: list[list] = []

    def problem(self) -> str | None:
        return None if self.available else MISSING_KEY

    def structured(self, content, schema):
        self.calls.append((schema.__name__, content))
        if self.failure is not None:
            raise self.failure
        if schema is OcrPage:
            return OcrPage(
                blocks=[
                    OcrBlock(kind="heading", level=1, text="Scanned Invoice"),
                    OcrBlock(kind="paragraph", level=None, text="Total due: EUR 500"),
                ]
            )
        if schema is InferredSections:
            return InferredSections(
                sections=[
                    InferredSection(start_block=0, title="Opening remarks", level=1),
                    InferredSection(start_block=8, title="Closing remarks", level=1),
                ]
            )
        sections = [
            SectionSummary(section=int(number), summary=f"Summary of section {number}.")
            for number in dict.fromkeys(re.findall(r"\[S(\d+)\]", content))
        ]
        profile = {
            "title": "Master Services Agreement",
            "doc_type": "Contract",
            "doc_date": "2024-03-01",
            "summary": "A services agreement between Acme Corp and Globex Ltd.",
        }
        if schema is ProfileWithSections:
            return ProfileWithSections(**profile, sections=sections)
        facts = self._facts(content)
        if schema is WindowAnalysis:
            return WindowAnalysis(sections=sections, facts=facts)
        assert schema is DocumentAnalysis
        return DocumentAnalysis(**profile, sections=sections, facts=facts)

    def _facts(self, content: str) -> list[ExtractedFact]:
        facts = []
        for number, line in _BLOCK_LINE.findall(content):
            if "ninety (90) days" in line:
                facts.append(
                    ExtractedFact(
                        name="Notice Period",
                        value="90 days",
                        type="number",
                        number=90,
                        date=None,
                        currency=None,
                        block=int(number),
                        quote="ninety (90) days written notice",
                    )
                )
            if "1 March 2024" in line:
                facts.append(
                    ExtractedFact(
                        name="effective_date",
                        value="1 March 2024",
                        type="date",
                        number=None,
                        date="2024-03-01",
                        currency=None,
                        # Deliberately the wrong block: the quote must relocate it.
                        block=int(number) + 3,
                        quote="on 1 March 2024",
                    )
                )
        if facts:
            facts.append(
                ExtractedFact(
                    name="governing_law",
                    value="England",
                    type="text",
                    number=None,
                    date=None,
                    currency=None,
                    block=0,
                    quote="words that appear nowhere in the document",
                )
            )
        return facts

    def ocr_page(self, image: bytes, media_type: str):
        return self.structured([BinaryContent(image, media_type=media_type)], OcrPage).blocks

    def stream_agent(self, instructions, messages, tools):
        self.agent_requests.append(list(messages))
        return self.agent_turns.pop(0)


class FakeS3Client:
    """boto3's S3 client reduced to the calls `S3Storage` makes, over a dict of objects.

    Listings come two keys at a time, so that the storage's paging is exercised, and continue
    after the last key returned, as a bucket's do, so that deleting what was listed does not
    move the page.
    """

    PAGE = 2

    def __init__(self, *buckets: str):
        self.buckets = set(buckets) or {"bucket"}
        self.objects: dict[str, tuple[bytes, str | None]] = {}
        # When each object was written; a test sets one back to age it.
        self.modified: dict[str, datetime] = {}
        self.calls: list[str] = []
        # Raised by every call while set: the bucket throttling or out of reach.
        self.outage: Exception | None = None

    def _check_bucket(self, bucket: str, operation: str) -> None:
        if self.outage is not None:
            raise self.outage
        if bucket not in self.buckets:
            raise ClientError(
                {"Error": {"Code": "NoSuchBucket", "Message": "The bucket does not exist"}},
                operation,
            )

    def head_bucket(self, *, Bucket: str) -> dict:
        self.calls.append("head_bucket")
        self._check_bucket(Bucket, "HeadBucket")
        return {}

    def put_object(self, *, Bucket: str, Key: str, Body, ContentType: str | None = None) -> dict:
        self.calls.append("put_object")
        self._check_bucket(Bucket, "PutObject")
        data = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = (data, ContentType)
        self.modified[Key] = datetime.now(UTC)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        self.calls.append("get_object")
        self._check_bucket(Bucket, "GetObject")
        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The key does not exist"}}, "GetObject"
            )
        data, content_type = self.objects[Key]
        return {"Body": io.BytesIO(data), "ContentType": content_type}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        self.calls.append("head_object")
        self._check_bucket(Bucket, "HeadObject")
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key][0])}

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, ContinuationToken: str | None = None
    ) -> dict:
        self.calls.append("list_objects_v2")
        self._check_bucket(Bucket, "ListObjectsV2")
        after = ContinuationToken or ""
        keys = sorted(key for key in self.objects if key.startswith(Prefix) and key > after)
        page = keys[: self.PAGE]
        truncated = len(keys) > self.PAGE
        listing: dict = {
            "Contents": [
                {"Key": key, "Size": len(self.objects[key][0]), "LastModified": self.modified[key]}
                for key in page
            ],
            "IsTruncated": truncated,
        }
        if truncated:
            listing["NextContinuationToken"] = page[-1]
        return listing

    def delete_objects(self, *, Bucket: str, Delete: dict) -> dict:
        self.calls.append("delete_objects")
        self._check_bucket(Bucket, "DeleteObjects")
        for entry in Delete["Objects"]:
            self.objects.pop(entry["Key"], None)
            self.modified.pop(entry["Key"], None)
        return {}

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int) -> str:
        self.calls.append("generate_presigned_url")
        assert ClientMethod == "get_object"
        query = {
            "response-content-type": Params["ResponseContentType"],
            "response-content-disposition": Params.get("ResponseContentDisposition", ""),
            "X-Amz-Expires": str(ExpiresIn),
        }
        return f"https://{Params['Bucket']}.s3.example/{Params['Key']}?{urlencode(query)}"


class FakeMailer:
    """Keeps the messages the app sends, so a test can follow the link in one."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def link(self, index: int = -1) -> str:
        """The first link in a message's body, the last message by default."""
        return re.search(r"https?://\S+", self.sent[index][2]).group()

    def token(self, index: int = -1) -> str:
        return parse_qs(urlsplit(self.link(index)).query)["token"][0]


class FakeIdentityProvider:
    """Stands in for Google's and Microsoft's token endpoints: hands out the claims it is told."""

    def __init__(self):
        self.claims: dict = {}
        self.requests: list[dict[str, str]] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(dict(parse_qsl(request.content.decode())))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "id_token": unsigned_jwt(self.claims),
                "access_token": "x",
                "token_type": "Bearer",
            },
        )


def unsigned_jwt(claims: dict) -> str:
    """A JWT with an empty signature: the app trusts the token endpoint, not the signature."""

    def segment(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{segment({'alg': 'none'})}.{segment(claims)}."


class FakeTurn:
    """One scripted model turn, shaped like `app.llm.ModelStream`."""

    def __init__(self, *parts: ModelResponsePart, finish_reason: FinishReason | None = None):
        self.parts = list(parts)
        has_call = any(isinstance(part, ToolCallPart) for part in parts)
        self.finish_reason = finish_reason or ("tool_call" if has_call else "stop")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for index, part in enumerate(self.parts):
            if not isinstance(part, ToolCallPart):
                yield PartStartEvent(index=index, part=part)

    def response(self) -> ModelResponse:
        return ModelResponse(
            parts=self.parts,
            usage=RequestUsage(input_tokens=10, output_tokens=5),
            finish_reason=self.finish_reason,
        )


def drop_schema() -> None:
    """Empty the test database of tables, so the migrations build the schema from nothing."""
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


def build_schema() -> None:
    """Apply the migrations, as `alembic upgrade head` does before a deployment starts."""
    command.upgrade(alembic_config(), "head")


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # The tests run against the schema the migrations make, as a deployment does, not against
    # `create_all`: a model change without its migration fails here.
    drop_schema()
    build_schema()
    yield
    engine.dispose()


@pytest.fixture
def services() -> Services:
    return Services(
        llm=FakeLLM(), embedder=FakeEmbedder(), storage=storage_from_settings(get_settings())
    )


@pytest.fixture
def session():
    with SessionLocal() as session_:
        session_.execute(text("TRUNCATE projects, organizations, users RESTART IDENTITY CASCADE"))
        session_.commit()
        # Ids start over, so the files and rendered pages of an earlier test's documents would
        # be served for a new test's; the storage is emptied with the tables.
        shutil.rmtree(STORAGE_ROOT, ignore_errors=True)
        yield session_


@pytest.fixture
def account(session) -> Account:
    """The local organisation and owner that the API acts as while there is no sign-in."""
    return local_account(session)


@pytest.fixture
def make_account(session):
    """A user with a role in an organisation: a new one, or the one given."""

    def make(email: str, organization: Organization | None = None, role: str = "owner") -> Account:
        user = User(email=email, name=email.split("@")[0].title())
        if organization is None:
            slug = email.split("@")[1].split(".")[0]
            organization = Organization(name=slug.title(), slug=slug)
        session.add_all([user, organization])
        session.flush()
        session.add(Membership(org_id=organization.id, user_id=user.id, role=role))
        session.commit()
        return Account(organization, user, role)

    return make


@pytest.fixture
def act_as(client):
    """Make the API act as the given account instead of the local one, as sign-in will."""

    def act_as(account: Account) -> None:
        org_id, user_id, role = account.organization.id, account.user.id, account.role

        def signed_in(session: SessionDep) -> Account:
            return Account(session.get(Organization, org_id), session.get(User, user_id), role)

        client.app.dependency_overrides[get_account] = signed_in

    yield act_as
    client.app.dependency_overrides.pop(get_account, None)


@pytest.fixture
def make_document(session, account, services):
    """Store a file as a queued document with its index job, creating a project unless one is
    given: what an upload does, without the request."""

    def make(path: Path, filename: str | None = None, project: Project | None = None) -> Document:
        if project is None:
            project = Project(org_id=account.organization.id, name="Test project")
            session.add(project)
            session.flush()
        data = path.read_bytes()
        suffix = Path(filename or path.name).suffix.lower()
        document = Document(
            project_id=project.id,
            filename=filename or path.name,
            mime_type=MIME_TYPES.get(suffix, "application/octet-stream"),
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_key="",
            status="queued",
            stages={},
        )
        session.add(document)
        session.flush()
        document.storage_key = original_key(project.org_id, project.id, document.id, suffix)
        services.storage.put(document.storage_key, path, document.mime_type)
        queue_index(session, document)
        session.commit()
        return document

    return make


@pytest.fixture
def indexed(session, services, make_document, sample_pdf) -> Document:
    """The sample contract, fully indexed."""
    document = make_document(sample_pdf)
    process_document(document.id, services)
    session.refresh(document)
    return document


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def identity_provider() -> FakeIdentityProvider:
    return FakeIdentityProvider()


@pytest.fixture
def client(services, session, mailer, identity_provider):
    http = httpx.Client(transport=httpx.MockTransport(identity_provider.handler))
    app = create_app(services=services, mailer=mailer, http=http)
    with TestClient(app, headers=APP_HEADERS) as test_client:
        yield test_client


@pytest.fixture
def signed_in_only(monkeypatch):
    """Require a session on every request, as production does; development falls back to the
    local account instead."""
    monkeypatch.setattr(get_settings(), "local_account", False)


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory) -> Path:
    """A two-page contract: title, numbered headings, a table, a running header and footer."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    path = tmp_path_factory.mktemp("files") / "msa.pdf"
    styles = getSampleStyleSheet()

    def furniture(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(72, 820, "Acme Corp - Confidential")
        canvas.drawRightString(520, 30, f"Page {document.page}")
        canvas.restoreState()

    story = [
        Paragraph("Master Services Agreement", styles["Title"]),
        Paragraph(
            "This agreement is made between Acme Corp and Globex Ltd on 1 March 2024. "
            "It sets out the terms under which consulting services are provided.",
            styles["BodyText"],
        ),
        Paragraph("1. Definitions", styles["Heading1"]),
        Paragraph(
            "In this agreement, Services means the consulting services described in Schedule 1.",
            styles["BodyText"],
        ),
        Paragraph("1.1 Interpretation", styles["Heading2"]),
        Paragraph(
            "Headings are for convenience only and do not affect interpretation.",
            styles["BodyText"],
        ),
        Paragraph("2. Fees and payment", styles["Heading1"]),
        Paragraph(
            "The Client shall pay the fees set out below within 30 days of each invoice.",
            styles["BodyText"],
        ),
        Table(
            [
                ["Service", "Rate", "Unit"],
                ["Consulting", "EUR 1,200", "per day"],
                ["Support", "EUR 300", "per month"],
            ],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]),
        ),
        PageBreak(),
        Paragraph("3. Termination", styles["Heading1"]),
        Paragraph(
            "Either party may terminate this agreement by giving ninety (90) days written "
            "notice to the other party.",
            styles["BodyText"],
        ),
    ]
    SimpleDocTemplate(str(path), pagesize=A4).build(
        story, onFirstPage=furniture, onLaterPages=furniture
    )
    return path


@pytest.fixture(scope="session")
def sample_xlsx(tmp_path_factory) -> Path:
    """Three sheets: a budget table, an empty sheet, and a one-row totals sheet."""
    import datetime as dt

    from openpyxl import Workbook

    path = tmp_path_factory.mktemp("files") / "budget.xlsx"
    workbook = Workbook()
    budget = workbook.active
    budget.title = "Budget"
    budget.append(["Item", "Amount", "Date", None, None])
    budget.append(["Consulting", 1200.0, dt.datetime(2024, 3, 1)])
    budget.append([None, None, None])
    budget.append(["Support", 300.5, None])
    workbook.create_sheet("Notes")
    totals = workbook.create_sheet("Totals")
    totals.append(["Total", 1500.5])
    workbook.save(str(path))
    return path


@pytest.fixture(scope="session")
def sample_csv(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("files") / "people.csv"
    path.write_text("name;city\nAda;London\nBob;Paris\n", encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def sample_docx(tmp_path_factory) -> Path:
    from docx import Document

    path = tmp_path_factory.mktemp("files") / "policy.docx"
    document = Document()
    document.add_heading("Travel Policy", level=0)
    document.add_paragraph("This policy applies to all employees.")
    document.add_heading("Booking", level=1)
    document.add_paragraph("Book flights at least 14 days ahead.")
    document.add_paragraph("Economy class for flights under six hours.", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "Daily allowance"
    table.cell(1, 0).text = "Europe"
    table.cell(1, 1).text = "EUR 60"
    document.add_heading("Expenses", level=1)
    document.add_paragraph("Submit receipts within 30 days.")
    document.save(str(path))
    return path
