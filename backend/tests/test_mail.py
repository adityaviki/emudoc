"""The mailers and the messages they send: a template's content as text and HTML alike, the SMTP
mailer checking the relay's certificate, since the links it sends sign people in, the Resend
mailer sending a message once by its key, and a failure logged without the address it was for
and marked transient when trying again later may help."""

import smtplib
import ssl
from email import message_from_bytes
from email.policy import default

import httpx
import pytest

from app.config import Settings
from app.mail import (
    Content,
    Link,
    LogMailer,
    MailError,
    Message,
    ResendMailer,
    SmtpMailer,
    compose,
    invitation_email,
    mailer_from_settings,
)

SENDER = "Emudoc <no-reply@mail.example.com>"


class FakeSMTP:
    """smtplib's client reduced to what the mailer calls, keeping the TLS context it is given;
    `refuse` stands for a relay whose certificate does not verify, `reject` for one that will
    not take the message."""

    made: list["FakeSMTP"] = []
    refuse: ssl.SSLCertVerificationError | None = None
    reject: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float, context=None):
        self.host, self.port, self.context = host, port, context
        self.sent: list = []
        FakeSMTP.made.append(self)
        if context is not None and FakeSMTP.refuse:
            raise FakeSMTP.refuse

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def starttls(self, context=None) -> None:
        self.context = context
        if FakeSMTP.refuse:
            raise FakeSMTP.refuse

    def login(self, username: str, password: str) -> None:
        pass

    def send_message(self, message) -> None:
        if FakeSMTP.reject:
            raise FakeSMTP.reject
        self.sent.append(message)


@pytest.fixture
def relay(monkeypatch):
    FakeSMTP.made = []
    FakeSMTP.refuse = None
    FakeSMTP.reject = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


class FakeResend:
    """Stands in for Resend's API: keeps each request's headers and body, and answers with
    `status` and `body`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body: dict = {"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def resend():
    return FakeResend()


def resend_mailer(fake: FakeResend) -> ResendMailer:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return ResendMailer("re_test", SENDER, http)


def reset(link: str = "https://emudoc.example.com/reset-password#token=abc") -> Content:
    return Content("Reset your password", ("Hi Ada,", "Choose a new one here:", Link(link, "Go")))


@pytest.mark.parametrize(
    ("url", "port"),
    [
        ("smtp://mailer:secret@smtp.example.com", 587),
        ("smtps://mailer:secret@smtp.example.com", 465),
    ],
)
def test_the_relay_s_certificate_is_checked_either_way(relay, url, port):
    SmtpMailer(url, SENDER).send(Message("ada@example.com", "Hi", "Hello"))
    (connection,) = relay.made
    assert (connection.host, connection.port) == ("smtp.example.com", port)
    assert len(connection.sent) == 1
    # smtplib's own default is CERT_NONE without a hostname check.
    assert connection.context.verify_mode == ssl.CERT_REQUIRED
    assert connection.context.check_hostname is True


def test_a_relay_the_machine_does_not_trust_gets_nothing(relay, caplog):
    refusal = ssl.SSLCertVerificationError(1, "certificate verify failed")
    refusal.verify_message = "self-signed certificate"
    relay.refuse = refusal
    mailer = SmtpMailer("smtp://mailer:secret@smtp.example.com", "no-reply@example.com")
    message = Message("ada@example.com", "Reset your password", "https://emudoc.example.com/r/x")
    with caplog.at_level("WARNING", logger="app.mail"), pytest.raises(MailError) as failed:
        mailer.send(message)
    # A certificate is the operator's to change: trying again changes nothing.
    assert failed.value.transient is False
    assert relay.made[0].sent == []
    assert "The mail relay at smtp.example.com:587 presented a certificate" in caplog.text
    assert "(self-signed certificate)" in caplog.text
    assert "secret" not in caplog.text and "/r/x" not in caplog.text


def test_a_failure_is_logged_without_the_address_it_was_for(relay, caplog):
    # smtplib's refusal names the recipient, and so does the relay's answer inside it.
    relay.reject = smtplib.SMTPRecipientsRefused(
        {"ada.lovelace@example.co.uk": (550, b"5.1.1 <ada.lovelace@example.co.uk>: no such user")}
    )
    mailer = SmtpMailer("smtp://mailer:secret@smtp.example.com", "no-reply@example.com")
    message = Message("ada.lovelace@example.co.uk", "Reset your password", "https://x.example/r/x")
    with caplog.at_level("WARNING", logger="app.mail"), pytest.raises(MailError) as failed:
        mailer.send(message)
    assert failed.value.transient is False
    assert (
        "Sending the email 'Reset your password' through smtp.example.com:587 failed: "
        "{'<address>': (550, b'5.1.1 <<address>>: no such user')}"
    ) in caplog.text
    assert "lovelace" not in caplog.text and "/r/x" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "later"),
    [
        (ConnectionRefusedError(111, "Connection refused"), True),
        (TimeoutError("timed out"), True),
        (smtplib.SMTPServerDisconnected("Connection unexpectedly closed"), True),
        (smtplib.SMTPRecipientsRefused({"a@example.com": (451, b"4.4.1 try again later")}), True),
        (smtplib.SMTPDataError(421, b"4.3.2 service shutting down"), True),
        (smtplib.SMTPAuthenticationError(535, b"5.7.8 credentials invalid"), False),
        (smtplib.SMTPDataError(554, b"5.7.1 message rejected"), False),
        (smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"5.1.1 no such user")}), False),
    ],
    ids=["refused", "timeout", "dropped", "4xx address", "4xx data", "auth", "5xx", "5xx address"],
)
def test_a_relay_that_answers_later_is_tried_again(relay, caplog, failure, later):
    relay.reject = failure
    mailer = SmtpMailer("smtp://mailer:secret@smtp.example.com", "no-reply@example.com")
    with caplog.at_level("WARNING", logger="app.mail"), pytest.raises(MailError) as failed:
        mailer.send(Message("a@example.com", "Reset your password", "x"))
    assert failed.value.transient is later
    assert ("failed, for now:" in caplog.text) is later


def test_a_message_carries_its_text_its_html_and_its_headers(relay):
    message = compose(
        "ada@example.com",
        reset(),
        footer=("To stop them:", Link("https://emudoc.example.com/unsubscribe#token=t", "Stop")),
        headers={"List-Unsubscribe": "<https://emudoc.example.com/api/mail/unsubscribe?token=t>"},
    )
    SmtpMailer("smtp://mailer:secret@smtp.example.com", SENDER).send(message)
    sent = message_from_bytes(relay.made[0].sent[0].as_bytes(), policy=default)
    assert sent["From"] == SENDER and sent["To"] == "ada@example.com"
    assert sent["Subject"] == "Reset your password"
    assert sent["Message-ID"].endswith("@mail.example.com>")
    assert sent["List-Unsubscribe"] == "<https://emudoc.example.com/api/mail/unsubscribe?token=t>"
    assert sent.get_content_type() == "multipart/alternative"
    text = sent.get_body(("plain",)).get_content()
    html = sent.get_body(("html",)).get_content()
    # The text is the template's, links on lines of their own, the footer after it.
    assert text == (
        "Hi Ada,\n\nChoose a new one here:\n\nhttps://emudoc.example.com/reset-password#token=abc"
        "\n\nTo stop them:\n\nhttps://emudoc.example.com/unsubscribe#token=t\n"
    )
    assert '<a href="https://emudoc.example.com/reset-password#token=abc"' in html
    assert ">Go</a>" in html and ">Stop</a>" in html


def test_only_the_links_a_template_names_are_links_in_the_html():
    # An inviter's name and an organisation's are typed by members: in the HTML they stay text,
    # however much they look like markup or an address.
    content = invitation_email(
        "<b>Eve</b>", "https://evil.example", "admin", "https://emudoc.example.com/i#token=a", 7
    )
    message = compose("ada@example.com", content)
    assert message.text == content.text
    assert message.html is not None
    assert "&lt;b&gt;Eve&lt;/b&gt; invited you to join https://evil.example on Emudoc" in (
        message.html
    )
    assert 'href="https://evil.example' not in message.html
    assert message.html.count('href="https://emudoc.example.com/i#token=a"') == 2
    assert "<title>You are invited to join an organisation on Emudoc</title>" in message.html


def test_resend_sends_a_message_once_by_its_key(resend):
    message = compose("ada@example.com", reset(), headers={"List-Unsubscribe": "<https://x/u>"})
    resend_mailer(resend).send(message)
    (request,) = resend.requests
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == "Bearer re_test"
    # The same on every try of the message, so a retry after a lost lease sends it once.
    assert request.headers["Idempotency-Key"] == message.key
    body = httpx.Response(200, content=request.content).json()
    assert body == {
        "from": SENDER,
        "to": ["ada@example.com"],
        "subject": "Reset your password",
        "text": message.text,
        "html": message.html,
        "headers": {"List-Unsubscribe": "<https://x/u>"},
    }


def test_resend_takes_the_address_replies_go_to_as_its_own_field(resend):
    # A message to support (app/api/help.py), whose replies go to the member who wrote it.
    message = compose("support@example.com", reset(), headers={"Reply-To": "ada@example.com"})
    resend_mailer(resend).send(message)
    body = httpx.Response(200, content=resend.requests[0].content).json()
    assert body["reply_to"] == "ada@example.com" and "headers" not in body
    assert message.headers == {"Reply-To": "ada@example.com"}


@pytest.mark.parametrize(
    ("status", "body", "later", "said"),
    [
        (422, {"statusCode": 422, "name": "validation_error", "message": "a@x.com"}, False, "422 "),
        (403, {"name": "invalid from address", "message": "Verify example.com"}, False, "403"),
        (429, {"name": "rate_limit_exceeded", "message": "Too many"}, True, "429 rate_limit"),
        (500, {"name": "internal_server_error"}, True, "500 internal_server_error"),
    ],
    ids=["invalid", "unverified domain", "rate", "down"],
)
def test_resend_s_refusal_is_worded_from_its_status_and_code(
    resend, caplog, status, body, later, said
):
    resend.status, resend.body = status, body
    with caplog.at_level("WARNING", logger="app.mail"), pytest.raises(MailError) as failed:
        resend_mailer(resend).send(Message("ada@example.com", "Reset your password", "x"))
    assert failed.value.transient is later
    assert "Resend refused the email 'Reset your password'" in caplog.text and said in caplog.text
    # Its words are left out: they may repeat an address or the domain.
    assert "a@x.com" not in caplog.text and "Verify" not in caplog.text
    assert "invalid from address" not in caplog.text


def test_resend_out_of_reach_is_tried_again(caplog):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    mailer = ResendMailer("re_test", SENDER, httpx.Client(transport=httpx.MockTransport(down)))
    with caplog.at_level("WARNING", logger="app.mail"), pytest.raises(MailError) as failed:
        mailer.send(Message("ada@example.com", "Reset your password", "x"))
    assert failed.value.transient is True
    assert "through Resend failed, for now: ConnectError" in caplog.text


def test_the_settings_choose_the_mailer():
    def chosen(**settings: object) -> type:
        return type(mailer_from_settings(Settings(_env_file=None, **settings)))

    assert chosen(resend_api_key="re_123") is ResendMailer
    assert chosen(smtp_url="smtp://mailer:secret@smtp.example.com") is SmtpMailer
    assert chosen() is LogMailer
