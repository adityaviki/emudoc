"""The SMTP mailer checks the relay's certificate: the links it sends sign people in."""

import smtplib
import ssl

import pytest

from app.mail import MailError, SmtpMailer


class FakeSMTP:
    """smtplib's client reduced to what the mailer calls, keeping the TLS context it is given;
    `refuse` stands for a relay whose certificate does not verify."""

    made: list["FakeSMTP"] = []
    refuse: ssl.SSLCertVerificationError | None = None

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
        self.sent.append(message)


@pytest.fixture
def relay(monkeypatch):
    FakeSMTP.made = []
    FakeSMTP.refuse = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.mark.parametrize(
    ("url", "port"),
    [
        ("smtp://mailer:secret@smtp.example.com", 587),
        ("smtps://mailer:secret@smtp.example.com", 465),
    ],
)
def test_the_relay_s_certificate_is_checked_either_way(relay, url, port):
    SmtpMailer(url, "Emudoc <no-reply@example.com>").send("ada@example.com", "Hi", "Hello")
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
    with caplog.at_level("WARNING", logger="app.mail"), pytest.raises(MailError):
        mailer.send("ada@example.com", "Reset your password", "https://emudoc.example.com/r/x")
    assert relay.made[0].sent == []
    assert "The mail relay at smtp.example.com:587 presented a certificate" in caplog.text
    assert "(self-signed certificate)" in caplog.text
    assert "secret" not in caplog.text and "/r/x" not in caplog.text
