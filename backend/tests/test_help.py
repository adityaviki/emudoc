"""Help for members (app/api/help.py): where to write for help, the status page, the code's
source, the help pages, filled in from what the deployment does, and a message from the app's
help menu, mailed to SUPPORT_EMAIL or the workspace's owners and admins with where the member
was, and nothing of it kept or logged; none where the install mails nothing.
The install of each test but those about that names a relay."""

import re

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.help import NO_MAIL, NOBODY_ELSE
from app.config import ROOT_DIR, Settings, get_settings
from app.help import ARTICLES, articles
from app.models import AuditEvent, Job
from app.parsing import SUPPORTED_EXTENSIONS
from app.ratelimit import FEEDBACK
from app.version import REPOSITORY, source_url
from tests.test_apikeys import make_key, program
from tests.test_auth import another_browser, signup
from tests.test_logs import logged  # noqa: F401 (a fixture)
from tests.test_probes import database_down  # noqa: F401 (a fixture)

SUPPORT = "support@emudoc.example.com"
FAILED = "0123456789abcdef0123456789abcdef"
WORDS = "The <b>invoice</b> from Acme shows twice in the library since this morning."

pytestmark = pytest.mark.usefixtures("relay")


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

    # SUPPORT_EMAIL, a team's helpdesk, takes the message in place of the admins.
    [message] = mailer.messages
    assert message.to == SUPPORT
    assert message.subject == f"A member reports a problem, reference {reference}"
    # Replies go to the member, from wherever support answers.
    assert message.headers == {"Reply-To": "ada@example.com"}
    organization = ada["organization"]
    for line in (
        "Ada Lovelace (ada@example.com) wrote from the app:",
        WORDS,
        f"Account: user {ada['id']}, address confirmed",
        f"Workspace: {organization['name']} ({organization['slug']}), id "
        f"{organization['id']}, where they are owner",
        "Page: http://testserver/projects/12/documents/34?chat=7",
        f"Last failed request: {FAILED}, answered 409",
        "Version: development",
        "Browser: testclient",
        f"Reference: {reference}",
    ):
        assert line in message.text, line
    assert "operator" not in message.text
    # The member's words are text in the HTML, however they look.
    assert "The &lt;b&gt;invoice&lt;/b&gt; from Acme" in message.html

    # Nothing of it is kept: no audit row, the job gave the message up once sent, and no line
    # of the log holds the words.
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == audited
    job = session.scalars(select(Job).order_by(Job.id.desc())).first()
    assert job.kind == "send_email" and "message" not in job.payload
    assert "invoice" not in str(logged())


def test_without_support_email_the_workspaces_owners_and_admins_read_it(
    client,
    mailer,
    monkeypatch,
    logged,  # noqa: F811
):
    monkeypatch.setattr(get_settings(), "support_email", "")
    ada = signup(client, name="Ada Lovelace")
    org_id = ada["organization"]["id"]
    browsers = {}
    for name, role in (("Bob", "admin"), ("Cy", "member"), ("Dee", "viewer")):
        address = f"{name.lower()}@example.com"
        browsers[address] = another_browser(client)
        signup(browsers[address], address, name=name, joins=org_id, role=role)
    mailer.messages.clear()

    sent = write(browsers["cy@example.com"])
    assert sent.status_code == 202, sent.text
    assert sent.json() == {"reference": sent.headers["X-Request-ID"], "to": None}
    # A mail each, the longest in the workspace first, replies going to the member who wrote.
    assert [message.to for message in mailer.messages] == ["ada@example.com", "bob@example.com"]
    for message in mailer.messages:
        assert message.headers == {"Reply-To": "cy@example.com"}
        assert "Cy (cy@example.com) wrote from the app:" in message.text
        assert "where they are member" in message.text
        assert WORDS in message.text
    assert "invoice" not in str(logged())

    # An admin's message goes to the others alone.
    mailer.messages.clear()
    assert write(browsers["bob@example.com"]).status_code == 202
    assert [message.to for message in mailer.messages] == ["ada@example.com"]


def test_a_sole_owner_is_told_the_message_would_come_back_to_them(client, mailer, monkeypatch):
    monkeypatch.setattr(get_settings(), "support_email", "")
    ada = signup(client)
    bob_client = another_browser(client)
    signup(bob_client, "bob@example.com", name="Bob", joins=ada["organization"]["id"])
    mailer.messages.clear()

    refused = write(client)
    assert (refused.status_code, refused.json()["detail"]) == (409, NOBODY_ELSE)
    assert mailer.messages == []
    # A member still reaches the owner.
    assert write(bob_client).status_code == 202
    assert [message.to for message in mailer.messages] == ["ada@example.com"]


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
    # A viewer reads alone, but writing for help changes nothing of the workspace's.
    signup(
        bob_client, "bob@example.com", name="Bob", joins=ada["organization"]["id"], role="viewer"
    )
    assert write(bob_client).status_code == 202
    assert "where they are viewer" in mailer.sent[-1][2]
    assert write(another_browser(client)).status_code == 401


def test_a_program_does_not_write_to_support(client, mailer):
    key = make_key(client)
    assert write(program(client, key["key"])).status_code == 403
    assert mailer.sent == []


def test_an_install_that_mails_nothing_takes_no_message(client, session, mailer, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_url", "")
    assert client.get("/api/help").json()["mail"] is False
    jobs = session.scalar(select(func.count()).select_from(Job))
    # The message would reach the log by its subject alone, so it is refused, with or without
    # SUPPORT_EMAIL, and the menu says whom to write to instead.
    for support in (SUPPORT, ""):
        monkeypatch.setattr(get_settings(), "support_email", support)
        refused = write(client)
        assert (refused.status_code, refused.json()["detail"]) == (409, NO_MAIL)
    assert mailer.sent == []
    assert session.scalar(select(func.count()).select_from(Job)) == jobs


def test_help_names_support_email_alone(client, monkeypatch):
    # Anyone may ask, the page that cannot reach its server too.
    assert client.get("/api/help").json() == {
        "support_email": SUPPORT,
        "status_page": None,
        "mail": True,
        "source": REPOSITORY,
    }
    settings = get_settings()
    monkeypatch.setattr(settings, "support_email", "")
    assert client.get("/api/help").json()["support_email"] is None


def test_help_names_the_status_page_where_there_is_one(client, monkeypatch):
    assert client.get("/api/help").json()["status_page"] is None
    assert "status page" not in article("support")
    monkeypatch.setattr(get_settings(), "status_page_url", "https://status.emudoc.example.com")
    assert client.get("/api/help").json()["status_page"] == "https://status.emudoc.example.com"
    assert "[its status page](https://status.emudoc.example.com)" in article("support")


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
@pytest.mark.parametrize("setting", ["status_page_url", "source_url"])
def test_a_status_page_and_the_source_are_web_addresses(url, setting):
    with pytest.raises(ValidationError, match=setting.upper()):
        Settings(_env_file=None, database_url="postgresql+psycopg://db/emudoc", **{setting: url})


def test_the_source_is_the_running_release_s_unless_the_team_names_its_own(client, monkeypatch):
    # The AGPL's section 13: whoever uses the deployment is offered the source of what it runs.
    assert source_url(version="0.1.0") == f"{REPOSITORY}/tree/v0.1.0"
    commit = "0123456789abcdef0123456789abcdef01234567"
    assert source_url(version=commit) == f"{REPOSITORY}/tree/{commit}"
    for version in ("development", "unknown", "0.1", "abc123"):
        assert source_url(version=version) == REPOSITORY
    own = "https://git.example.com/team/emudoc"
    assert source_url(own, version="0.1.0") == own
    monkeypatch.setattr(
        "app.api.help.source_url", lambda configured: source_url(configured, "0.2.0")
    )
    assert client.get("/api/help").json()["source"] == f"{REPOSITORY}/tree/v0.2.0"
    monkeypatch.setattr(get_settings(), "source_url", own)
    assert client.get("/api/help").json()["source"] == own


# The help pages (app/help): a member's, filled in from the code and the settings, and every link
# of theirs leads to an article or a page of the app.

# The app's pages, as the router names them (frontend/src/App.tsx).
ROUTES = re.compile(r'<Route (?:index |path="([^"]*)")')
# What the help no longer names: the plans and their limits went with S.2, the organisations with
# S.1 (the app says workspace), support's view of an account and the legal pages with S.3.
GONE = re.compile(
    r"\b(plans?|seats?|trials?|billing|subscriptions?|organi[sz]ations?|privacy|terms of)\b",
    re.IGNORECASE,
)


def pages() -> set[str]:
    """The members' page's routes, a route of several pages by its own path, and the articles."""
    paths = ROUTES.findall((ROOT_DIR / "frontend" / "src" / "App.tsx").read_text())
    named = {f"/{path.removesuffix('/*')}" for path in paths if path and ":" not in path}
    return (named - {"/*"}) | {"/"} | {f"/help/{slug}" for slug, _ in ARTICLES}


def said(text: str) -> str:
    return " ".join(text.split())


def article(slug: str) -> str:
    """An article as the suite's deployment, or what a test made of it, fills it in."""
    return said(next(found.text for found in articles(get_settings()) if found.slug == slug))


def test_the_help_pages_are_a_members_filled_in_from_what_the_deployment_does(
    client, signed_in_only
):
    assert client.get("/api/help/articles").status_code == 401
    signup(client)
    help = client.get("/api/help/articles").json()
    assert [found["slug"] for found in help] == [slug for slug, _ in ARTICLES]
    by = {found["slug"]: found for found in help}
    assert by["documents"]["title"] == "Uploading and organising documents"
    assert by["documents"]["text"].startswith("# Uploading and organising documents\n")
    assert by["documents"]["summary"] == dict(ARTICLES)["documents"]
    documents = said(by["documents"]["text"])
    assert (
        "PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), RTF, HTML, CSV, plain text, "
        "Markdown files, and images (PNG, JPEG, WebP, TIFF, HEIC). A file may be up to 100 MB, "
        "and a PDF, a TIFF or a presentation up to 5,000 pages."
    ) in documents
    assert "A larger upload is sent 100 files or 500 MB at a time" in documents
    assert "in the trash for 30 days" in documents
    assert "The invitation lasts 7 days" in said(by["workspace"]["text"])
    assert "Audit log kept**: 365 days" in said(by["workspace"]["text"])
    assert "deletes it 14 days after you ask" in said(by["data"]["text"])
    assert "Emudoc has seven built in" in said(by["checking"]["text"])
    assert "(1, 7 or 30 days)" in said(by["working-together"]["text"])
    assert f"It writes to [{SUPPORT}](mailto:{SUPPORT})" in said(by["support"]["text"])
    for found in help:
        assert "$" not in found["text"], found["slug"]
        # Words a member reads: a link's address keeps the page's path, /organization.
        words = re.sub(r"\]\([^)]*\)", "]", found["text"])
        assert not GONE.search(words), (found["slug"], GONE.search(words))
    # A program reads the API, not the members' help.
    key = make_key(client)
    assert program(client, key["key"]).get("/api/help/articles").status_code == 403


def test_what_a_deployment_does_not_do_the_help_does_not_say(monkeypatch):
    settings = get_settings()
    # A deployment that scans nothing, converts nothing, takes no mail at a project's address,
    # connects no folder, signs in through nobody else, caps no spending and checks no breach
    # list, whatever a developer's .env names of what the suite does not pin.
    for name in (
        "scanner_url",
        "converter_url",
        "google_drive_client_id",
        "dropbox_app_key",
        "onedrive_client_id",
        "google_oauth_client_id",
        "microsoft_oauth_client_id",
    ):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "daily_spend_cap", 0)
    assert "scanned for malware" not in article("documents")
    assert "opens as it looks" not in article("documents")
    assert "## Email in" not in article("documents")
    assert "## Connected folders" not in article("documents")
    assert "Continue with" not in article("account")
    assert "a day, as whoever runs Emudoc set it" not in article("asking")
    assert "data breach" not in article("account")
    assert "The workspace starts with a sample project" not in article("getting-started")
    monkeypatch.setattr(settings, "scanner_url", "clamd://127.0.0.1:3310")
    monkeypatch.setattr(settings, "converter_url", "http://127.0.0.1:2004")
    monkeypatch.setattr(settings, "inbound_domain", "in.emudoc.example.com")
    monkeypatch.setattr(settings, "google_drive_client_id", "drive")
    monkeypatch.setattr(settings, "onedrive_client_id", "onedrive")
    monkeypatch.setattr(settings, "google_oauth_client_id", "google")
    monkeypatch.setattr(settings, "daily_spend_cap", 25)
    monkeypatch.setattr(settings, "pwned_passwords_url", "https://api.pwnedpasswords.com")
    monkeypatch.setattr(settings, "rate_failed_sign_ins", 0)
    monkeypatch.setattr(settings, "sample_project", True)
    monkeypatch.setattr(settings, "ocr_max_pages", 0)
    monkeypatch.setattr(settings, "max_upload_mb", 250)
    monkeypatch.setattr(settings, "parse_max_pages", 0)
    documents = article("documents")
    assert "Every upload is first scanned for malware" in documents
    assert "opens as it looks, page by page" in documents
    assert "a message may be up to 40 MB" in documents
    assert "a folder of your Google Drive or OneDrive" in documents
    assert "Emudoc looks again every 15 minutes" in documents
    assert "as an image." in documents
    assert "A file may be up to 250 MB. Upload files" in documents
    assert ", shown as it looks, page by page" in article("working-together")
    assert "up to USD 25.00 a day, as whoever runs Emudoc set it" in article("asking")
    assert 'You may sign in with "Continue with Google"' in article("account")
    assert "data breach is refused." in article("account") and "pause" not in article("account")
    assert '"Sample: Acme supplier contract"' in article("getting-started")
    # The figures are the code's, never typed in.
    monkeypatch.setattr("app.help.TRASH_DAYS", 45)
    assert "in the trash for 45 days" in article("documents")


def test_where_the_install_mails_nothing_the_help_promises_no_email(monkeypatch):
    settings = get_settings()
    # The relay the suite names: a link by email, notices by email, the help menu's message.
    assert "through a link sent by email that works for 1 hour" in article("account")
    assert "in the app, by email, both or neither" in article("account")
    assert "an invitation goes to their address" in article("workspace")
    assert "and you are told by email" in article("account")
    assert "under the bell, and by email as each chose" in article("checking")
    monkeypatch.setattr(settings, "smtp_url", "")
    account = article("account")
    assert "by email" not in account.replace("this workspace sends no email", "")
    assert "ask one of its owners or admins for a link that sets a new one" in account
    assert "It works once, for 24 hours" in account
    assert "this workspace sends no email, so the page shows the invitation's link" in (
        article("workspace")
    )
    assert "by email" not in article("working-together")
    assert "by email" not in article("checking")
    support = article("support")
    assert f"it names [{SUPPORT}](mailto:{SUPPORT}), the address your team set for help" in support
    monkeypatch.setattr(settings, "support_email", "")
    assert "it names the workspace's owners and admins, to write to" in article("support")


def test_the_help_names_every_file_type_an_upload_takes(monkeypatch):
    monkeypatch.setattr("app.help.SUPPORTED_EXTENSIONS", {*SUPPORTED_EXTENSIONS, ".odt"})
    with pytest.raises(RuntimeError, match=r"\.odt"):
        articles(get_settings())


def test_every_link_of_the_help_leads_to_an_article_or_a_page(client, monkeypatch):
    known = pages()
    assert {"/account", "/organization", "/organization/usage", "/admin", "/help"} <= known
    settings = get_settings()
    # Every conditional sentence written, the status page's among them.
    monkeypatch.setattr(settings, "status_page_url", "https://status.emudoc.example.com")
    monkeypatch.setattr(settings, "inbound_domain", "in.emudoc.example.com")
    monkeypatch.setattr(settings, "dropbox_app_key", "dropbox")
    for mail in ("smtp://relay.example:587", ""):
        monkeypatch.setattr(settings, "smtp_url", mail)
        for found in articles(settings):
            for link in re.findall(r"\]\(([^)]*)\)", found.text):
                if link.startswith(("mailto:", "https://")):
                    continue
                # Or to a file the API serves, which the page downloads: the upload client, the
                # versioned API's contract.
                if link.startswith("/api/"):
                    assert client.get(link).status_code == 200, (found.slug, link)
                else:
                    assert link in known, (found.slug, link)


def test_the_help_says_how_to_run_the_upload_client_by_its_own_figures(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_url", "https://docs.acme.example/")
    documents = article("documents")
    assert "[emudoc_upload.py](/api/help/emudoc_upload.py), which Python 3.9 or later runs" in (
        documents
    )
    assert (
        "python3 emudoc_upload.py watch --server https://docs.acme.example --project "
        '"Invoices" --into Scans ~/Scans'
    ) in documents
    assert "every 10 seconds" in documents and "`.emudoc-upload.json` in the folder" in documents
    assert "`export EMUDOC_API_KEY=emk_…` on a Mac or Linux" in documents
