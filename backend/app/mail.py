"""Email the app sends: the links that make an account, confirm an address, reset a password,
move an account to a new address or join an organisation, and the notices that an address has
an account already, that an account's address changed or that it is set to be deleted.

There is no email provider yet. The log mailer writes each message to the log, which is enough
to follow a link in development and never runs in production, where SMTP_URL is required
(app/config.py); the SMTP mailer sends through any relay, and when it fails it logs the
message's subject and the relay's answer with every address in it hidden, since the person a
message was for is no business of the log's. A provider with templates, bounces and sending
through jobs is a roadmap item (2.4).
"""

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from functools import partial
from typing import Protocol
from urllib.parse import unquote, urlsplit

from app.config import Settings

log = logging.getLogger(__name__)

# Anything shaped like an email address, which a relay's refusal often repeats ("550 5.1.1
# <ada@example.com>: Recipient address rejected"), and smtplib's own errors do too.
_ADDRESS = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")


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
            log.warning(
                "Sending the email %r through %s:%s failed: %s",
                subject,
                self.host,
                self.port,
                hide_addresses(str(error)),
            )
            raise MailError("The email could not be sent.") from error


def hide_addresses(text: str) -> str:
    """The text with every email address in it replaced, for the log."""
    return _ADDRESS.sub("<address>", text)


def mailer_from_settings(settings: Settings) -> Mailer:
    if settings.smtp_url:
        return SmtpMailer(settings.smtp_url, settings.mail_from)
    return LogMailer()


def _greeting(name: str) -> str:
    return f"Hi {name}," if name else "Hi,"


def invitation_email(
    inviter: str, organization: str, role: str, link: str, days: int
) -> tuple[str, str]:
    """To an address a member of an organisation invited: who asked, to what and as what, and
    the link to the page where whoever holds the address accepts, with a new account if it has
    none. The names are in the body alone: a subject is the app's own sentence, which the log
    repeats when a message cannot be sent."""
    article = "an" if role[0] in "aeiou" else "a"
    body = (
        "Hi,\n\n"
        f"{inviter} invited you to join {organization} on Emudoc as {article} {role}, to work "
        "with its documents and ask questions about them. To accept, open this link:\n\n"
        f"{link}\n\n"
        "If you have no Emudoc account with this address yet, you create one there. The link "
        f"works for {days} days. If you were not expecting this, ignore this message and nothing "
        "will happen.\n"
    )
    return "You are invited to join an organisation on Emudoc", body


def sign_up_email(link: str) -> tuple[str, str]:
    """To an address someone asked to make an account with: the link to the page where whoever
    holds the address chooses the password and makes the account."""
    body = (
        "Hi,\n\n"
        "Someone asked to create an Emudoc account with this email address. If it was you, open "
        "this link to choose your password and finish:\n\n"
        f"{link}\n\n"
        "The link works for 24 hours. If you did not ask for this, ignore this message and no "
        "account will be made.\n"
    )
    return "Finish creating your Emudoc account", body


def account_exists_email(name: str, sign_in: str, reset: str) -> tuple[str, str]:
    """To an address someone asked to make an account with, which has one already: said here,
    never in the answer to the request, so that nobody learns from the app whether an address
    has an account."""
    body = (
        f"{_greeting(name)}\n\n"
        "Someone asked to create an Emudoc account with this email address, but it has one "
        "already, so nothing changed. To use it, sign in:\n\n"
        f"{sign_in}\n\n"
        "If you have forgotten your password, or never set one, choose a new one here:\n\n"
        f"{reset}\n\n"
        "If you did not ask for this, ignore this message.\n"
    )
    return "You have an Emudoc account already", body


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


def sign_ins_paused_email(name: str, failures: int, until: str, link: str) -> tuple[str, str]:
    """To an account whose sign-ins from new browsers were paused after many wrong passwords or
    codes (app/ratelimit.py): why a new browser of the owner's is refused, and what to do."""
    body = (
        f"{_greeting(name)}\n\n"
        f"There have been {failures} failed attempts this hour to sign in to your Emudoc "
        "account, with a wrong password or authenticator code, from browsers that have never "
        "signed in to it. To slow down whoever is guessing, signing in from such browsers is "
        f"paused until {until}.\n\n"
        "The browsers you have signed in with before can sign in as usual. To sign in from a "
        "new one before then, reset your password, which ends the pause:\n\n"
        f"{link}\n\n"
        "If that was not you, someone knows your address and is guessing your password: make "
        "sure it is long and used nowhere else, and turn on two-factor sign-in on the account "
        "page.\n"
    )
    return "Sign-ins to your account were paused", body


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


def email_change_email(name: str, link: str) -> tuple[str, str]:
    """To the address an account asked to move to: the link that makes it the account's."""
    body = (
        f"{_greeting(name)}\n\n"
        "Someone asked to make this the email address of their Emudoc account. If it was you, "
        "confirm it by opening this link:\n\n"
        f"{link}\n\n"
        "The link works for 24 hours. Until it is opened, the account keeps its old address. If "
        "you did not ask for this, ignore this message and nothing will happen.\n"
    )
    return "Confirm your new email address", body


def address_taken_email(name: str, link: str) -> tuple[str, str]:
    """To an address another account asked to move to, which has an account already: said
    here, never in the answer to the request, so that nobody learns from the app whether an
    address has an account."""
    body = (
        f"{_greeting(name)}\n\n"
        "Someone asked to make this the email address of another Emudoc account, but it is the "
        "address of your account already, so nothing changed. To use Emudoc with this address, "
        "sign in to your account, where you can also reset a forgotten password:\n\n"
        f"{link}\n\n"
        "If you did not ask for this, ignore this message.\n"
    )
    return "This address has an Emudoc account already", body


def email_changed_email(name: str, contact: str) -> tuple[str, str]:
    """To the address an account just left: what changed, and whom to tell if it was not
    them, since this address no longer signs in or receives a reset link."""
    body = (
        f"{_greeting(name)}\n\n"
        "The email address of your Emudoc account was changed. From now on Emudoc writes to the "
        "new address, and you sign in with it.\n\n"
        f"If you did not ask for this, someone else may be using your account: write to "
        f"{contact} at once.\n"
    )
    return "Your email address was changed", body
