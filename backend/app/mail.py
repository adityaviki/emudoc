"""Email the app sends: the links that confirm an address or reset a password.

There is no email provider yet. The log mailer writes each message to the log, which is enough
to follow a link in development; the SMTP mailer sends through any relay. A provider with
templates, bounces and sending through jobs is a roadmap item (2.4).
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import unquote, urlsplit

from app.config import Settings

log = logging.getLogger(__name__)


class MailError(Exception):
    """The message could not be handed to the mail server."""


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class LogMailer:
    """Writes messages to the log instead of sending them."""

    def send(self, to: str, subject: str, body: str) -> None:
        log.info("Email to %s: %s\n%s", to, subject, body)


class SmtpMailer:
    """Sends through an SMTP relay named by a URL: smtp:// uses STARTTLS, smtps:// uses TLS."""

    def __init__(self, url: str, sender: str):
        parts = urlsplit(url)
        if parts.scheme not in ("smtp", "smtps") or not parts.hostname:
            raise ValueError("SMTP_URL must look like smtp://user:pass@host:587")
        self.host = parts.hostname
        self.port = parts.port or (465 if parts.scheme == "smtps" else 587)
        self.tls = parts.scheme == "smtps"
        self.username = unquote(parts.username) if parts.username else None
        self.password = unquote(parts.password) if parts.password else None
        self.sender = sender

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            smtp = smtplib.SMTP_SSL if self.tls else smtplib.SMTP
            with smtp(self.host, self.port, timeout=20) as connection:
                if not self.tls:
                    connection.starttls()
                if self.username:
                    connection.login(self.username, self.password or "")
                connection.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            log.warning("Sending email to %s failed: %s", to, error)
            raise MailError("The email could not be sent.") from error


def mailer_from_settings(settings: Settings) -> Mailer:
    if settings.smtp_url:
        return SmtpMailer(settings.smtp_url, settings.mail_from)
    return LogMailer()


def _greeting(name: str) -> str:
    return f"Hi {name}," if name else "Hi,"


def verification_email(name: str, link: str) -> tuple[str, str]:
    body = (
        f"{_greeting(name)}\n\n"
        "Confirm your email address for Emudoc by opening this link:\n\n"
        f"{link}\n\n"
        "The link works for 24 hours. If you did not create an Emudoc account, ignore this "
        "message and nothing will happen.\n"
    )
    return "Confirm your email address", body


def password_reset_email(name: str, link: str) -> tuple[str, str]:
    body = (
        f"{_greeting(name)}\n\n"
        "Someone asked to reset the password of your Emudoc account. Choose a new one here:\n\n"
        f"{link}\n\n"
        "The link works for one hour. If that was not you, ignore this message; your password "
        "stays as it is.\n"
    )
    return "Reset your password", body
