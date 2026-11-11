"""Help for members (app/api/help.py): where to write for help, the status page, and a message to
support from the app's help menu, mailed with where the member was and nothing of it kept or
logged."""

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.help import NO_SUPPORT
from app.config import Settings, get_settings
from app.models import AuditEvent, Job
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
    # Anyone may ask, the page that cannot reach its server too.
    assert client.get("/api/help").json() == {"support_email": SUPPORT, "status_page": None}
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


def test_help_names_the_status_page_where_there_is_one(client, monkeypatch):
    assert client.get("/api/help").json()["status_page"] is None
    monkeypatch.setattr(get_settings(), "status_page_url", "https://status.emudoc.example.com")
    assert client.get("/api/help").json()["status_page"] == "https://status.emudoc.example.com"


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
