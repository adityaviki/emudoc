"""Email the app sends: the links that confirm an address or reset a password, and the notice
that an account is set to be deleted.

There is no email provider yet. The log mailer writes each message to the log, which is enough
to follow a link in development; the SMTP mailer sends through any relay. A provider with
templates, bounces and sending through jobs is a roadmap item (2.4).
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from functools import partial
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
    """Sends through an SMTP relay named by a URL: smtp:// uses STARTTLS, smtps:// uses TLS.

    Either way the relay's certificate is checked against the authorities this machine trusts
    and must name the relay's host. smtplib checks nothing unless it is given a context, and a
    relay that anyone in the path could stand in for would read every sign-in and
    password-reset link the app sends.
    """

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
        context = ssl.create_default_context()
        try:
            smtp = partial(smtplib.SMTP_SSL, context=context) if self.tls else smtplib.SMTP
            with smtp(self.host, self.port, timeout=20) as connection:
                if not self.tls:
                    connection.starttls(context=context)
                if self.username:
                    connection.login(self.username, self.password or "")
                connection.send_message(message)
        except ssl.SSLCertVerificationError as error:
            log.warning(
                "The mail relay at %s:%s presented a certificate this machine does not trust "
                "(%s), so nothing was sent to it; give the relay a certificate a public "
                "authority signed, or add the authority that signed it to this machine's "
                "trusted certificates.",
                self.host,
                self.port,
                error.verify_message,
            )
            raise MailError("The email could not be sent.") from error
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


def account_deletion_email(name: str, due: str, link: str) -> tuple[str, str]:
    body = (
        f"{_greeting(name)}\n\n"
        f"Your Emudoc account is set to be deleted on {due}. Then it goes for good, with every "
        "organisation you are the only member of and everything in it: projects, documents and "
        "chats. Every other browser and device was signed out.\n\n"
        "To keep the account, sign in before then and choose to keep it:\n\n"
        f"{link}\n\n"
        "If you did not ask for this, someone else could use your account: sign in, keep it, "
        "and change your password.\n"
    )
    return "Your account is set to be deleted", body
