"""Email the app sends: the links that make an account, confirm an address, reset a password,
move an account to a new address or join an organisation, and the notices that an address has
an account already, that an account's address changed or that it is set to be deleted. And the
notices about a member's work, which they choose how to be told of, kind by kind
(app/notices.py): that an upload which took a while to index is done, with what could not be
indexed and why; a notice's `brief` is what the app's notification centre shows of it. And to
the support address, what a member wrote to it from the app (app/api/help.py).

A template writes `Content`: the subject, and the body as paragraphs and links. Both the plain
text and the HTML of a message are made from it (`compose`), so they say the same: the text has
each link on a line of its own, the HTML a button with the address under it to copy, in the
layout every message shares. Only the links a template names become links in the HTML; a name a
member typed, an organisation's say, stays text however much it looks like an address.

A member's mail is posted through the queue (app/postbox.py) and sent by the worker through a
`Mailer`; the operator's reports (ALERT_EMAIL) are sent at once by the thread that writes them,
since the queue may be what they report on. There are three mailers: the log mailer, where no
relay is set, writes each message to the log, which is enough to follow a link in development,
and in production a member's message by its subject alone, since its link may sign in; the SMTP
mailer sends through any relay (Amazon SES's on AWS, deploying.md "Mail"); and the Resend mailer
through Resend's API, which takes the message's key so that a message sent again after a lost
lease is sent once. When one fails it logs the message's subject and what the
relay said with every address in it hidden, since the person a message was for is no business
of the log's, and says whether trying again later may help.
"""

import html
import logging
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from functools import partial
from string import Template
from typing import Protocol
from urllib.parse import unquote, urlsplit

import httpx

from app.config import Settings
from app.errors import AppError

log = logging.getLogger(__name__)

# Anything shaped like an email address, which a relay's refusal often repeats ("550 5.1.1
# <ada@example.com>: Recipient address rejected"), and smtplib's own errors do too.
_ADDRESS = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")
RESEND_API = "https://api.resend.com"
# Resend's names for its errors, such as "validation_error": said in the log, never its words,
# which may repeat an address.
_RESEND_CODE = re.compile(r"[a-z_]{1,64}")
# How long a relay or the API gets to take one message.
SEND_TIMEOUT = 20.0


class MailError(AppError):
    """The message could not be handed to the mail server; `transient` when trying again later
    may get it through: a relay out of reach or one that answered "later"."""


@dataclass(frozen=True)
class Link:
    """A link the reader is asked to open: a line of its own in the text, a button in the HTML
    labelled `label`."""

    url: str
    label: str


Block = str | Link


@dataclass(frozen=True)
class Content:
    """What a template writes: the subject, which the log may repeat and so is always the app's
    own sentence, and the body's paragraphs and links in order, the first the greeting."""

    subject: str
    blocks: tuple[Block, ...]
    # What the notification centre shows of a notice (app/notices.py): the body without the
    # greeting, the links and what leads to them; empty for mail that is no notice.
    brief: str = ""

    @property
    def text(self) -> str:
        return "\n\n".join(_line(block) for block in self.blocks) + "\n"


@dataclass(frozen=True)
class Message:
    """One message to one address, as a mailer sends it: the plain text, and the HTML when the
    message was composed from a template's content (the operator's reports are text alone)."""

    to: str
    subject: str
    text: str
    html: str | None = None
    # Headers besides From, To and Subject: a notice's List-Unsubscribe (app/postbox.py), a
    # message to support's Reply-To (app/api/help.py).
    headers: dict[str, str] = field(default_factory=dict)
    # The same on every try of one message, for a provider that sends a key once (Resend).
    key: str = field(default_factory=lambda: uuid.uuid4().hex)


def compose(
    to: str, content: Content, footer: tuple[Block, ...] = (), headers: dict[str, str] | None = None
) -> Message:
    """The message of a template's content to `to`, with `footer` after the body, set apart in
    the HTML: what a notice says about turning notices off."""
    body = Content(content.subject, content.blocks + footer)
    return Message(
        to=to,
        subject=content.subject,
        text=body.text,
        html=_LAYOUT.substitute(
            subject=html.escape(content.subject),
            body="".join(_html(block) for block in content.blocks),
            footer="".join(_html(block, small=True) for block in footer),
        ),
        headers=dict(headers or {}),
    )


def _line(block: Block) -> str:
    return block.url if isinstance(block, Link) else block


# Ink on Paper, the button in Beak (emudoc-logo-kit); tables and inline styles, since mail
# programs drop a style sheet and many ignore anything else.
_INK = "#14161C"
_BEAK = "#3A4BE0"
_PAPER = "#F4F3EF"
_MUTED = "#5A5D66"
_PARAGRAPH = "margin:0 0 16px;"
_SMALL = f"margin:0 0 12px;font-size:13px;color:{_MUTED};"
_LAYOUT = Template(
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>$subject</title>\n</head>\n"
    f'<body style="margin:0;padding:0;background:{_PAPER};color:{_INK};'
    "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">\n"
    f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    f'style="background:{_PAPER};"><tr><td align="center" style="padding:32px 16px;">\n'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="max-width:560px;">\n'
    "<tr><td style=\"padding:0 0 20px;font-family:Kanit,-apple-system,'Segoe UI',Helvetica,"
    f'Arial,sans-serif;font-size:24px;font-weight:600;color:{_INK};">Emudoc</td></tr>\n'
    '<tr><td style="background:#FFFFFF;border-radius:8px;padding:32px;font-size:16px;'
    'line-height:1.5;">$body</td></tr>\n'
    '<tr><td style="padding:20px 8px 0;line-height:1.5;">$footer</td></tr>\n'
    "</table>\n</td></tr></table>\n</body>\n</html>\n"
)


def _html(block: Block, small: bool = False) -> str:
    if isinstance(block, str):
        text = html.escape(block).replace("\n", "<br>")
        return f'<p style="{_SMALL if small else _PARAGRAPH}">{text}</p>'
    url, label = html.escape(block.url), html.escape(block.label)
    if small:
        return f'<p style="{_SMALL}"><a href="{url}" style="color:{_BEAK};">{label}</a></p>'
    return (
        f'<p style="margin:8px 0 12px;"><a href="{url}" style="display:inline-block;'
        f"background:{_BEAK};color:#FFFFFF;text-decoration:none;padding:12px 20px;"
        f'border-radius:6px;font-weight:600;">{label}</a></p>'
        f'<p style="{_SMALL}word-break:break-all;">Or copy this address into your browser: '
        f'<a href="{url}" style="color:{_BEAK};">{url}</a></p>'
    )


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


class LogMailer:
    """Writes messages to the log instead of sending them, where no relay is set.

    With `bodies` (development) the whole message, to follow its link. Without (production) a
    message composed from a template, a member's, by its subject alone: its link may sign in,
    confirm an address or join the workspace, and the log is read by more people than the
    member, while the address is no business of the log's. The operator's reports are text
    alone (`html` None) and carry no link, so they keep their text, the log being then where
    they are read.
    """

    def __init__(self, bodies: bool = True):
        self.bodies = bodies

    def send(self, message: Message) -> None:
        if self.bodies:
            log.info("Email to %s: %s\n%s", message.to, message.subject, message.text)
        elif message.html is None:
            log.warning("Not mailed, since no relay is set: %s\n%s", message.subject, message.text)
        else:
            log.info("Not mailed, since no relay is set: %s", message.subject)


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

    def send(self, message: Message) -> None:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = message.to
        email["Subject"] = message.subject
        email["Message-ID"] = make_msgid(domain=_domain(self.sender))
        for name, value in message.headers.items():
            email[name] = value
        email.set_content(message.text)
        if message.html is not None:
            email.add_alternative(message.html, subtype="html")
        context = ssl.create_default_context()
        try:
            smtp = partial(smtplib.SMTP_SSL, context=context) if self.tls else smtplib.SMTP
            with smtp(self.host, self.port, timeout=SEND_TIMEOUT) as connection:
                if not self.tls:
                    connection.starttls(context=context)
                if self.username:
                    connection.login(self.username, self.password or "")
                connection.send_message(email)
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
            later = _later(error)
            log.warning(
                "Sending the email %r through %s:%s failed%s: %s",
                message.subject,
                self.host,
                self.port,
                ", for now" if later else "",
                hide_addresses(str(error)),
            )
            raise MailError("The email could not be sent.", transient=later) from error


def _later(error: OSError | smtplib.SMTPException) -> bool:
    """Whether the relay's failure passes by itself: it was out of reach, dropped the
    connection, or answered with a 4xx code, SMTP's "try again later". A 5xx (a sender it will
    not relay for, credentials it refuses, an address it knows to be wrong) does not."""
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in error.recipients.values()]
        return bool(codes) and all(400 <= code < 500 for code in codes)
    if isinstance(error, smtplib.SMTPResponseException):
        return 400 <= error.smtp_code < 500
    return isinstance(error, (OSError, smtplib.SMTPServerDisconnected))


def _domain(sender: str) -> str | None:
    address = parseaddr(sender)[1]
    return address.rpartition("@")[2] or None


class ResendMailer:
    """Sends through Resend's API with the message's key as its Idempotency-Key, which Resend
    holds for a day: a message the worker sends again, after a lease it lost while the request
    was on its way, is sent once."""

    def __init__(self, api_key: str, sender: str, http: httpx.Client):
        self.api_key = api_key
        self.sender = sender
        self.http = http

    def send(self, message: Message) -> None:
        body: dict[str, object] = {
            "from": self.sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html is not None:
            body["html"] = message.html
        headers = dict(message.headers)
        # Resend takes the address replies go to as a field of its own.
        if reply_to := headers.pop("Reply-To", None):
            body["reply_to"] = reply_to
        if headers:
            body["headers"] = headers
        try:
            answer = self.http.post(
                f"{RESEND_API}/emails",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Idempotency-Key": message.key,
                },
                timeout=SEND_TIMEOUT,
            )
        except httpx.HTTPError as error:
            log.warning(
                "Sending the email %r through Resend failed, for now: %s",
                message.subject,
                type(error).__name__,
            )
            raise MailError("The email could not be sent.", transient=True) from error
        if answer.is_success:
            return
        later = answer.status_code == 429 or answer.status_code >= 500
        log.warning(
            "Resend refused the email %r%s: %s%s",
            message.subject,
            ", for now" if later else "",
            answer.status_code,
            f" {code}" if (code := _resend_code(answer)) else "",
        )
        raise MailError("The email could not be sent.", transient=later)


def _resend_code(answer: httpx.Response) -> str | None:
    try:
        name = answer.json().get("name")
    except (ValueError, AttributeError):
        return None
    return name if isinstance(name, str) and _RESEND_CODE.fullmatch(name) else None


def hide_addresses(text: str) -> str:
    """The text with every email address in it replaced, for the log."""
    return _ADDRESS.sub("<address>", text)


def relay_set(settings: Settings) -> bool:
    """Whether the deployment names a way to send mail; without one each message is only
    logged (`LogMailer`), and the links the app mails are handed on by hand instead."""
    return sent_through(settings) is not None


def sent_through(settings: Settings) -> str | None:
    """How the mail goes, as the admins' services page says it: "resend", "smtp", or None where
    nothing is mailed. Resend first, as `mailer_from_settings` takes it."""
    if settings.resend_api_key:
        return "resend"
    return "smtp" if settings.smtp_url else None


def mailer_from_settings(settings: Settings) -> Mailer:
    if settings.resend_api_key:
        return ResendMailer(settings.resend_api_key, settings.mail_from, httpx.Client())
    if settings.smtp_url:
        return SmtpMailer(settings.smtp_url, settings.mail_from)
    return LogMailer(bodies=settings.environment != "production")


def _greeting(name: str) -> str:
    return f"Hi {name}," if name else "Hi,"


def invitation_email(inviter: str, organization: str, role: str, link: str, days: int) -> Content:
    """To an address a member of an organisation invited: who asked, to what and as what, and
    the link to the page where whoever holds the address accepts, with a new account if it has
    none. The names are in the body alone: a subject is the app's own sentence, which the log
    repeats when a message cannot be sent."""
    article = "an" if role[0] in "aeiou" else "a"
    return Content(
        "You are invited to join a workspace on Emudoc",
        (
            "Hi,",
            f"{inviter} invited you to join {organization} on Emudoc as {article} {role}, to "
            "work with its documents and ask questions about them. To accept, open this link:",
            Link(link, "Accept the invitation"),
            "If you have no Emudoc account with this address yet, you create one there. The "
            f"link works for {days} days. If you were not expecting this, ignore this message "
            "and nothing will happen.",
        ),
    )


def sign_up_email(link: str) -> Content:
    """To an address someone asked to make an account with: the link to the page where whoever
    holds the address chooses the password and makes the account."""
    return Content(
        "Finish creating your Emudoc account",
        (
            "Hi,",
            "Someone asked to create an Emudoc account with this email address. If it was you, "
            "open this link to choose your password and finish:",
            Link(link, "Finish creating your account"),
            "The link works for 24 hours. If you did not ask for this, ignore this message and "
            "no account will be made.",
        ),
    )


def account_exists_email(name: str, sign_in: str, reset: str) -> Content:
    """To an address someone asked to make an account with, which has one already: said here,
    never in the answer to the request, so that nobody learns from the app whether an address
    has an account."""
    return Content(
        "You have an Emudoc account already",
        (
            _greeting(name),
            "Someone asked to create an Emudoc account with this email address, but it has one "
            "already, so nothing changed. To use it, sign in:",
            Link(sign_in, "Sign in"),
            "If you have forgotten your password, or never set one, choose a new one here:",
            Link(reset, "Choose a new password"),
            "If you did not ask for this, ignore this message.",
        ),
    )


def verification_email(name: str, link: str) -> Content:
    return Content(
        "Confirm your email address",
        (
            _greeting(name),
            "Confirm your email address for Emudoc by opening this link:",
            Link(link, "Confirm your address"),
            "The link works for 24 hours. If you did not create an Emudoc account, ignore this "
            "message and nothing will happen.",
        ),
    )


def password_reset_email(name: str, link: str) -> Content:
    return Content(
        "Reset your password",
        (
            _greeting(name),
            "Someone asked to reset the password of your Emudoc account. Choose a new one here:",
            Link(link, "Choose a new password"),
            "The link works for one hour. If that was not you, ignore this message; your "
            "password stays as it is.",
        ),
    )


def sign_ins_paused_email(name: str, failures: int, until: str, link: str) -> Content:
    """To an account whose sign-ins from new browsers were paused after many wrong passwords or
    codes (app/ratelimit.py): why a new browser of the owner's is refused, and what to do."""
    return Content(
        "Sign-ins to your account were paused",
        (
            _greeting(name),
            f"There have been {failures} failed attempts this hour to sign in to your Emudoc "
            "account, with a wrong password or authenticator code, from browsers that have "
            "never signed in to it. To slow down whoever is guessing, signing in from such "
            f"browsers is paused until {until}.",
            "The browsers you have signed in with before can sign in as usual. To sign in from "
            "a new one before then, reset your password, which ends the pause:",
            Link(link, "Reset your password"),
            "If that was not you, someone knows your address and is guessing your password: "
            "make sure it is long and used nowhere else, and turn on two-factor sign-in on the "
            "account page.",
        ),
    )


def account_deletion_email(name: str, due: str, link: str) -> Content:
    return Content(
        "Your account is set to be deleted",
        (
            _greeting(name),
            f"Your Emudoc account is set to be deleted on {due}. Then it goes for good, and so "
            "does the workspace if you are its only member, with everything in it: projects, "
            "documents and chats. Every other browser and device was signed out.",
            "To keep the account, sign in before then and choose to keep it:",
            Link(link, "Sign in"),
            "If you did not ask for this, someone else could use your account: sign in, keep "
            "it, and change your password.",
        ),
    )


# What a member's message from the help menu is about, as its subject says it: the app's words,
# since the mailer logs a subject, never the member's.
SUPPORT_SUBJECTS = {
    "problem": "A member reports a problem",
    "question": "A member asks a question",
    "idea": "A member suggests an idea",
}


def support_message_email(
    kind: str, words: str, reference: str, sender: str, details: tuple[str, ...]
) -> Content:
    """To SUPPORT_EMAIL, or each of the workspace's owners and admins: what a member wrote from
    the app's help menu, and what its reader needs to find what they met, one detail a line
    (the account, the workspace, the page, the request that failed). The member's words are in
    the body alone; a reply goes to them (the message's Reply-To, app/api/help.py)."""
    return Content(
        f"{SUPPORT_SUBJECTS[kind]}, reference {reference}",
        (
            f"{sender} wrote from the app:",
            words,
            "\n".join(details),
            "Reply to this message to answer them, and quote the reference if you write to "
            "anyone else about it.",
        ),
    )


def email_change_email(name: str, link: str) -> Content:
    """To the address an account asked to move to: the link that makes it the account's."""
    return Content(
        "Confirm your new email address",
        (
            _greeting(name),
            "Someone asked to make this the email address of their Emudoc account. If it was "
            "you, confirm it by opening this link:",
            Link(link, "Confirm the new address"),
            "The link works for 24 hours. Until it is opened, the account keeps its old "
            "address. If you did not ask for this, ignore this message and nothing will happen.",
        ),
    )


def address_taken_email(name: str, link: str) -> Content:
    """To an address another account asked to move to, which has an account already: said
    here, never in the answer to the request, so that nobody learns from the app whether an
    address has an account."""
    return Content(
        "This address has an Emudoc account already",
        (
            _greeting(name),
            "Someone asked to make this the email address of another Emudoc account, but it is "
            "the address of your account already, so nothing changed. To use Emudoc with this "
            "address, sign in to your account, where you can also reset a forgotten password:",
            Link(link, "Sign in"),
            "If you did not ask for this, ignore this message.",
        ),
    )


def email_changed_email(name: str, contact: str) -> Content:
    """To the address an account just left: what changed, and whom to tell if it was not
    them, since this address no longer signs in or receives a reset link."""
    return Content(
        "Your email address was changed",
        (
            _greeting(name),
            "The email address of your Emudoc account was changed. From now on Emudoc writes to "
            "the new address, and you sign in with it.",
            "If you did not ask for this, someone else may be using your account: write to "
            f"{contact} at once.",
        ),
    )


# How many of an upload's documents that could not be indexed its notice names.
LISTED = 20


def upload_indexed_email(
    name: str,
    organization: str,
    project: str,
    uploaded: str,
    ready: int,
    failed: list[tuple[str, str]],
    link: str,
) -> Content:
    """To the member whose upload took long enough to index that they may have left the page
    (app/notices.py): how many of its documents are ready, and which could not be indexed, each
    with the sentence the library shows for it, its members' words. A long list stops at
    LISTED, the rest counted; the names are the files', in the body alone."""
    total = ready + len(failed)
    where = f"to {project} in {organization} at {uploaded}"
    if not failed:
        subject = "Your document is indexed" if total == 1 else "Your documents are indexed"
        said = (
            f"The document you uploaded {where} is indexed, ready to search and ask about."
            if total == 1
            else f"The {total:,} documents you uploaded {where} are indexed, ready to search "
            "and ask about."
        )
        return Content(subject, (_greeting(name), said, Link(link, "Open the project")), said)
    if total == 1:
        subject, said = (
            "Your document could not be indexed",
            f"The document you uploaded {where} could not be indexed:",
        )
    elif not ready:
        subject, said = (
            "Your documents could not be indexed",
            f"None of the {total:,} documents you uploaded {where} could be indexed:",
        )
    else:
        subject, said = (
            "Some of your documents could not be indexed",
            f"Of the {total:,} documents you uploaded {where}, {ready:,} "
            f"{'is' if ready == 1 else 'are'} indexed, ready to search and ask about, and "
            f"{len(failed):,} could not be:",
        )
    lines = [f"{filename}: {reason}" for filename, reason in failed[:LISTED]]
    if (unlisted := len(failed) - LISTED) > 0:
        lines.append(f"And {unlisted:,} more, which the library lists with theirs.")
    listed = "\n".join(lines)
    return Content(
        subject,
        (
            _greeting(name),
            said,
            listed,
            'Once what a document says is put right, "Index again" in the library runs it again:',
            Link(link, "Open the project"),
        ),
        f"{said}\n{listed}",
    )


def project_export_email(
    name: str,
    project: str,
    organization: str,
    link: str,
    until: str | None,
    chosen: int | None = None,
) -> Content:
    """To the member who asked for a project's export (app/export.py), or for one of the
    `chosen` documents of it alone: that it is ready to download until the day `until` names,
    or, without one, that it could not be made. The subject names neither, since the log
    repeats it."""
    of = (
        project
        if chosen is None
        else f"{chosen} chosen document{'' if chosen == 1 else 's'} of {project}"
    )
    if until is None:
        said = (
            f"The export of {of} in {organization} you asked for could not be made. Ask for "
            f"another {'on the projects page' if chosen is None else 'in the library'}; if that "
            "one fails too, write to support."
        )
        return Content(
            "Your project export could not be made",
            (_greeting(name), said, Link(link, "Open the projects page")),
            said,
        )
    held = (
        "its documents, what was read from them and its chats"
        if chosen is None
        else "them and what was read from them"
    )
    said = (
        f"The export of {of} in {organization} you asked for is ready: one ZIP file with "
        f"{held}. Download it on the projects page until {until}."
    )
    return Content(
        "Your project export is ready", (_greeting(name), said, Link(link, "Download it")), said
    )


def checks_run_email(
    name: str,
    organization: str,
    project: str,
    automation: str,
    landed: int,
    partners: int,
    names: list[str],
    counts: dict[str, int],
    report_status: str | None,
    report_error: str | None,
    link: str,
) -> Content:
    """To whom an automation names once a run it made settles (app/check_runs.py): what arrived,
    the documents it watches (`landed`) and those its checks compare them with (`partners`),
    the first by its name when it came alone; how many of the values found about them differ,
    could not be checked and hold; and its report's end, when one was asked for. Counts and file
    names, never a value a document states. The subject says only which way it went, since the
    log repeats it."""
    differs, missing, holds = (
        counts.get(outcome, 0) for outcome in ("differs", "missing", "holds")
    )
    if differs:
        subject = "The cross-checks found differences"
    elif missing:
        subject = "The cross-checks could not check everything"
    else:
        subject = "The cross-checks found nothing amiss"
    alone = names[0] if landed + partners == 1 and names else None
    if alone and landed:
        came = f"{alone} arrived"
    elif alone:
        came = f"{alone}, which its cross-checks compare with the documents it watches, arrived"
    elif not partners:
        came = f"{landed:,} documents arrived"
    elif not landed:
        came = (
            f"{partners:,} documents that its cross-checks compare with the documents it watches "
            "arrived"
        )
    else:
        came = (
            f"{landed:,} {'document' if landed == 1 else 'documents'} arrived, and {partners:,} "
            "that its cross-checks compare them with"
        )
    said = (
        f"The automation “{automation}” of {project} in {organization} ran the project's "
        f"cross-checks after {came}. Of what they found about "
        f"{'it' if alone else 'the documents that arrived'}, {differs:,} "
        f"{'value differs' if differs == 1 else 'values differ'}, {missing:,} could not be "
        f"checked and {holds:,} {'holds' if holds == 1 else 'hold'}."
    )
    if report_status == "done":
        said += " The model's discrepancy report is written."
    elif report_status == "failed":
        said += f" The discrepancy report could not be written: {report_error}"
    return Content(subject, (_greeting(name), said, Link(link, "Open the run")), said)


def report_ready_email(
    name: str,
    organization: str,
    project: str,
    title: str,
    trigger: str,
    arrived: int,
    asked: int,
    answered: int,
    link: str,
) -> Content:
    """To whom a question set names once a report of its answers is kept
    (app/question_reports.py): which set, of which project, what ran it (its schedule, documents
    that arrived, by their count, or the member told, who ran it), and how many of its questions
    were answered. Counts and the set's name, never a question or what an answer says, which
    holds what documents say. The subject names neither the set nor the project, since the log
    repeats it."""
    unanswered = asked - answered
    subject = (
        "A question set's report is ready"
        if not unanswered
        else "A question set's report is ready, with questions unanswered"
    )
    if trigger == "member":
        when = "when you ran it"
    elif arrived:
        when = f"after {arrived:,} {'document' if arrived == 1 else 'documents'} arrived"
    else:
        when = "on its schedule"
    said = (
        f"The question set “{title}” of {project} in {organization} was asked {when}: "
        f"{answered:,} of its {asked:,} {'question was' if asked == 1 else 'questions were'} "
        "answered."
    )
    if unanswered:
        said += " The report says why the others were not."
    return Content(subject, (_greeting(name), said, Link(link, "Open the report")), said)


@dataclass(frozen=True)
class Arrivals:
    """What arrived in one project over a week, as the weekly digest names it: the project, how
    many documents, and the names of the first few."""

    project: str
    count: int
    names: list[str]


def weekly_digest_email(
    name: str, organization: str, projects: list[Arrivals], listed: int, link: str
) -> Content:
    """To a member who turned the weekly digest on (app/digests.py), on Monday morning: how many
    documents arrived over the week in the projects of the organisation they see, and for the
    first `listed` of those projects how many and the first few by name, the others counted.
    Names and counts, never what a document says. The subject names neither the organisation nor
    a project, since the log repeats it."""
    total = sum(arrivals.count for arrivals in projects)
    lines = [
        f"Over the past week, {total:,} {'document' if total == 1 else 'documents'} arrived in "
        f"{len(projects):,} {'project' if len(projects) == 1 else 'projects'} of {organization}."
    ]
    for arrivals in projects[:listed]:
        more = arrivals.count - len(arrivals.names)
        named = ", ".join(arrivals.names) + (f" and {more:,} more" if more else "")
        lines.append(f"{arrivals.project}: {named}.")
    if (unlisted := len(projects) - listed) > 0:
        lines.append(f"And {unlisted:,} {'project' if unlisted == 1 else 'projects'} more.")
    return Content(
        "What arrived in your projects this week",
        (_greeting(name), *lines, Link(link, "Open your projects")),
        "\n".join(lines),
    )


def review_assigned_email(
    name: str,
    assigner: str,
    filename: str,
    state: str,
    project: str,
    organization: str,
    link: str,
) -> Content:
    """To the member another assigned a document's review (app/reviews.py): who did, and which
    document, by the file's name in the body alone, with where its review stands. The subject
    names neither, since the log repeats it."""
    where = f"{filename} in {project} in {organization}"
    said = {
        "to_review": f"{assigner} assigned you {where} to review.",
        "approved": f"{assigner} assigned you {where}, which is approved.",
        "disputed": f"{assigner} assigned you {where}, which is disputed.",
    }[state]
    return Content(
        "A document was assigned to you",
        (_greeting(name), said, Link(link, "Open the document")),
        said,
    )


def mentioned_email(
    name: str, writer: str, filename: str, project: str, organization: str, link: str
) -> Content:
    """To a member another mentioned in a comment (app/comments.py): who did, and on which
    document, by the file's name in the body alone. Never the comment, which is a member's words
    and may quote the document; the subject names nobody, since the log repeats it."""
    said = f"{writer} mentioned you in a comment on {filename} in {project} in {organization}."
    return Content(
        "You were mentioned in a comment",
        (_greeting(name), said, Link(link, "Open the comment")),
        said,
    )


def thread_replied_email(
    name: str, writer: str, filename: str, project: str, organization: str, link: str
) -> Content:
    """To a member who wrote in a thread another replied in (app/comments.py), under the same
    terms as a mention."""
    said = (
        f"{writer} replied in a thread you commented in, on {filename} in {project} in "
        f"{organization}."
    )
    return Content(
        "A reply to a thread you commented in",
        (_greeting(name), said, Link(link, "Open the thread")),
        said,
    )


# The names of the documents an automation put in review that its notice lists; the rest counted.
REVIEWS_NAMED = 3


def automation_review_email(
    name: str,
    automation: str,
    filenames: list[str],
    project: str,
    organization: str,
    link: str,
) -> Content:
    """To the member an automation assigns what its run put in review (app/reviews.py): which
    automation, and which documents, the first REVIEWS_NAMED by their files' names in the body
    alone, the rest counted. The subject names neither, since the log repeats it."""
    by = f"The automation “{automation}” of {project} in {organization}"
    if len(filenames) == 1:
        said = f"{by} put {filenames[0]} in review for you."
        return Content(
            "A document was assigned to you",
            (_greeting(name), said, Link(link, "Open the document")),
            said,
        )
    named = filenames[:REVIEWS_NAMED]
    if (more := len(filenames) - len(named)) > 0:
        listed = f"{', '.join(named)} and {more:,} more"
    else:
        listed = f"{', '.join(named[:-1])} and {named[-1]}"
    said = f"{by} put {len(filenames):,} documents in review for you: {listed}."
    return Content(
        "Documents were assigned to you",
        (_greeting(name), said, Link(link, "Open your reviews")),
        said,
    )
