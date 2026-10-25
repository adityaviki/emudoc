"""Help for members (app/api/help.py): the help pages, filled in from what the deployment does,
where to write for help, and a message to support from the app's help menu, mailed with where
the member was and nothing of it kept or logged."""

import re

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.help import NO_SUPPORT
from app.config import ROOT_DIR, Settings, get_settings
from app.help import ARTICLES, articles
from app.main import REFERENCE_PATH
from app.models import AuditEvent, Job
from app.parsing import SUPPORTED_EXTENSIONS
from app.ratelimit import FEEDBACK
from tests.test_apikeys import make_key, program
from tests.test_auth import another_browser, signup
from tests.test_logs import logged  # noqa: F401 (a fixture)
from tests.test_organizations import join, switch
from tests.test_probes import database_down  # noqa: F401 (a fixture)

SUPPORT = "support@emudoc.example.com"
FAILED = "0123456789abcdef0123456789abcdef"
WORDS = "The <b>invoice</b> from Acme shows twice in the library since this morning."


def write(client, message: str = WORDS, kind: str = "problem", **more):
    body = {"kind": kind, "message": message, "page": "/projects/12/documents/34?chat=7", **more}
    return client.post("/api/feedback", json=body)


def test_a_member_writes_to_support_with_where_they_were(client, session, mailer, logged):  # noqa: F811
    ada = signup(client, name="Ada Lovelace")
    audited = session.scalar(select(func.count()).select_from(AuditEvent))

    sent = write(client, failed={"reference": FAILED, "status": 409})
    assert sent.status_code == 202, sent.text
    reference = sent.headers["X-Request-ID"]
    assert sent.json() == {"reference": reference, "to": SUPPORT}

    message = mailer.messages[-1]
    assert message.to == SUPPORT
    assert message.subject == f"A member reports a problem, reference {reference}"
    # Replies go to the member, from wherever support answers.
    assert message.headers == {"Reply-To": "ada@example.com"}
    organization = ada["organization"]
    for line in (
        "Ada Lovelace (ada@example.com) wrote from the app:",
        WORDS,
        f"Account: user {ada['id']}, address confirmed",
        f"Organisation: {organization['name']} ({organization['slug']}), id "
        f"{organization['id']}, on the Free plan, where they are owner",
        "Page: http://testserver/projects/12/documents/34?chat=7",
        f"Last failed request: {FAILED}, answered 409",
        "Version: development",
        "Browser: testclient",
        f"Reference: {reference}",
        f"http://testserver/operator/organizations/{organization['id']}",
    ):
        assert line in message.text, line
    # The member's words are text in the HTML, however they look.
    assert "The &lt;b&gt;invoice&lt;/b&gt; from Acme" in message.html

    # Nothing of it is kept: no audit row, the job gave the message up once sent, and no line
    # of the log holds the words.
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == audited
    job = session.scalars(select(Job).order_by(Job.id.desc())).first()
    assert job.kind == "send_email" and "message" not in job.payload
    assert "invoice" not in str(logged())


def test_a_page_without_a_failure_says_so_and_each_kind_its_subject(client, mailer):
    for kind, subject in (
        ("question", "A member asks a question"),
        ("idea", "A member suggests an idea"),
    ):
        sent = write(client, "  Can a project hold more than one price list?  ", kind)
        assert sent.status_code == 202, sent.text
        message = mailer.messages[-1]
        assert message.subject.startswith(f"{subject}, reference ")
        assert "\n\nCan a project hold more than one price list?\n\n" in message.text
        assert "No request of the page's failed before this message." in message.text


@pytest.mark.parametrize(
    "change",
    [
        {"message": ""},
        {"message": "   \n "},
        {"message": "x" * 10_001},
        {"kind": "complaint"},
        # The page's query carries ids alone, never the words of a search.
        {"page": "/projects/12?q=invoice%20acme"},
        {"page": "projects/12"},
        {"failed": {"reference": "not one", "status": 409}},
        {"failed": {"reference": FAILED, "status": 200}},
    ],
)
def test_a_message_holds_what_support_may_read(client, mailer, change):
    body = {"kind": "problem", "message": WORDS, "page": "/", **change}
    assert client.post("/api/feedback", json=body).status_code == 422
    assert mailer.sent == []


def test_a_member_writes_to_support_so_often_an_hour(client, mailer, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_feedback", 2)
    assert [write(client).status_code for _ in range(2)] == [202, 202]
    refused = write(client)
    assert refused.status_code == 429
    assert refused.json()["detail"] == FEEDBACK.refusal(2, "from your account")
    assert "in the last hour" in refused.json()["detail"]
    assert 0 < int(refused.headers["Retry-After"]) <= 3600
    assert len(mailer.sent) == 2


def test_a_viewer_writes_too_and_nobody_signed_out(client, session, mailer, signed_in_only):
    ada = signup(client)
    bob_client = another_browser(client)
    bob = signup(bob_client, "bob@example.com", name="Bob")
    # A member past the Free plan's one leaves the organisation read-only, which asks nothing
    # of writing to support.
    join(session, ada["organization"]["id"], bob["id"], "viewer")
    switch(bob_client, ada["organization"]["id"])
    assert write(bob_client).status_code == 202
    assert "where they are viewer" in mailer.sent[-1][2]
    assert write(another_browser(client)).status_code == 401


def test_a_program_does_not_write_to_support(client, mailer):
    key = make_key(client)
    assert write(program(client, key["key"])).status_code == 403
    assert mailer.sent == []


def test_help_names_where_to_write_and_development_may_name_nowhere(
    client, mailer, monkeypatch, signed_in_only
):
    # Anyone may ask, the sign-in pages linking to help too.
    assert client.get("/api/help").json()["support_email"] == SUPPORT
    settings = get_settings()
    monkeypatch.setattr(settings, "support_email", "")
    monkeypatch.setattr(settings, "operator_email", "privacy@emudoc.example.com")
    # One address answers both, unless the deployment names another for support.
    assert client.get("/api/help").json()["support_email"] == "privacy@emudoc.example.com"

    monkeypatch.setattr(settings, "operator_email", "")
    assert client.get("/api/help").json()["support_email"] is None
    signup(client)
    refused = write(client)
    assert refused.status_code == 503 and refused.json()["detail"] == NO_SUPPORT
    assert mailer.sent[1:] == []


# The help pages (app/help): filled in from the code and the settings, and every link of theirs
# leads to an article or a page of the app.

# The app's pages, as the router names them (frontend/src/App.tsx), and the articles'.
ROUTES = re.compile(r'<Route (?:index |path="([^"]*)")')


def pages() -> set[str]:
    """The members' page's routes, the help's articles and the API reference, a page of its own
    (frontend/reference.html), which the help's Markdown loads rather than routes."""
    paths = ROUTES.findall((ROOT_DIR / "frontend" / "src" / "App.tsx").read_text())
    named = {f"/{path}" for path in paths if path and ":" not in path and path != "*"}
    return named | {"/", f"/{REFERENCE_PATH}"} | {f"/help/{slug}" for slug, _ in ARTICLES}


def said(article: dict) -> str:
    return " ".join(article["text"].split())


def test_the_help_is_filled_in_from_what_the_deployment_allows(client, signed_in_only):
    # Anyone may read it, before signing in too.
    help = client.get("/api/help").json()
    assert [article["slug"] for article in help["articles"]] == [slug for slug, _ in ARTICLES]
    by = {article["slug"]: article for article in help["articles"]}
    assert by["documents"]["title"] == "Uploading and organising documents"
    assert by["documents"]["text"].startswith("# Uploading and organising documents\n")
    assert by["documents"]["summary"] == dict(ARTICLES)["documents"]
    documents = said(by["documents"])
    assert (
        "PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), CSV, plain text, Markdown files, "
        "and images (PNG, JPEG, WebP, TIFF, HEIC). A file may be up to 100 MB."
    ) in documents
    assert "A larger upload is sent 100 files or 500 MB at a time" in documents
    assert "in the trash for 30 days" in documents
    assert "| Pages a month | 200 | 5,000 | 25,000 | No limit |" in by["plans"]["text"]
    assert "| Members | 1 | 1 | 3, or the seats bought | No limit |" in by["plans"]["text"]
    assert (
        "| Chats kept after their last question | 30 days | 365 days | Until deleted"
        in (by["plans"]["text"])
    )
    assert f"write to [{SUPPORT}](mailto:{SUPPORT})" in said(by["support"])
    for article in help["articles"]:
        assert "$" not in article["text"], article["slug"]


def test_what_a_deployment_does_not_do_the_help_does_not_say(monkeypatch):
    settings = get_settings()

    def text(slug: str) -> str:
        return " ".join(next(a for a in articles(settings) if a.slug == slug).text.split())

    # The suite scans nothing, sells nothing and checks no breach list.
    assert "scanned for malware" not in text("documents")
    assert "write to support ([Getting help](/help/support))" in text("plans")
    assert "data breach" not in text("account")
    monkeypatch.setattr(settings, "scanner_url", "clamd://127.0.0.1:3310")
    monkeypatch.setattr(settings, "pwned_passwords_url", "https://api.pwnedpasswords.com")
    monkeypatch.setattr(settings, "rate_failed_sign_ins", 0)
    monkeypatch.setattr(settings, "ocr_max_pages", 0)
    monkeypatch.setattr(settings, "max_upload_mb", 250)
    assert "Every upload is first scanned for malware" in text("documents")
    assert "as an image." in text("documents") and "A file may be up to 250 MB" in text("documents")
    assert "data breach is refused." in text("account") and "pause" not in text("account")
    # The figures are the code's, never typed in.
    monkeypatch.setattr("app.help.TRASH_DAYS", 45)
    assert "in the trash for 45 days" in text("documents")


def test_the_help_names_every_file_type_an_upload_takes(monkeypatch):
    monkeypatch.setattr("app.help.SUPPORTED_EXTENSIONS", {*SUPPORTED_EXTENSIONS, ".rtf"})
    with pytest.raises(RuntimeError, match=r"\.rtf"):
        articles(get_settings())


def test_every_link_of_the_help_leads_to_an_article_or_a_page(client):
    known = pages()
    assert {"/account", "/organization/usage", "/privacy", "/help"} <= known
    for article in articles(get_settings()):
        for link in re.findall(r"\]\((/[^)]*)\)", article.text):
            # Or to a file the API serves, which the page downloads: the upload client.
            if link.startswith("/api/"):
                assert client.get(link).status_code == 200, (article.slug, link)
            else:
                assert link in known, (article.slug, link)


def test_the_help_names_the_status_page_where_there_is_one(client, monkeypatch):
    def support() -> str:
        found = client.get("/api/help").json()
        return next(a["text"] for a in found["articles"] if a["slug"] == "support")

    assert client.get("/api/help").json()["status_page"] is None
    assert "status page" not in support()
    monkeypatch.setattr(get_settings(), "status_page_url", "https://status.emudoc.example.com")
    assert client.get("/api/help").json()["status_page"] == "https://status.emudoc.example.com"
    assert "[our status page](https://status.emudoc.example.com)" in support()


def test_the_page_that_cannot_reach_the_database_still_finds_the_status_page(
    client,
    database_down,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(get_settings(), "status_page_url", "https://status.emudoc.example.com")
    assert client.get("/api/health").status_code == 503
    answer = client.get("/api/help")
    assert answer.status_code == 200
    assert answer.json()["status_page"] == "https://status.emudoc.example.com"


@pytest.mark.parametrize(
    "url", ["status.example.com", "ftp://status.example.com", "https://", "https://a b"]
)
def test_a_status_page_is_a_web_address(url):
    with pytest.raises(ValidationError, match="STATUS_PAGE_URL"):
        Settings(_env_file=None, database_url="postgresql+psycopg://db/emudoc", status_page_url=url)


def test_the_help_says_how_to_run_the_upload_client_by_its_own_figures(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_url", "https://docs.acme.example/")
    found = client.get("/api/help").json()["articles"]
    documents = said(next(article for article in found if article["slug"] == "documents"))
    assert "[emudoc_upload.py](/api/help/emudoc_upload.py), which Python 3.9 or later runs" in (
        documents
    )
    assert (
        "python3 emudoc_upload.py watch --server https://docs.acme.example --project "
        '"Invoices" --into Scans ~/Scans'
    ) in documents
    assert "every 10 seconds" in documents and "`.emudoc-upload.json` in the folder" in documents
    assert "`export EMUDOC_API_KEY=emk_…` on a Mac or Linux" in documents
