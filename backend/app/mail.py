"""Email the app sends: the links that make an account, confirm an address, reset a password,
move an account to a new address or join an organisation, the notices that an address has
an account already, that an account's address changed or that it is set to be deleted, and to
an organisation's owners what its subscription needs of them: a payment that failed, a trial
about to end, a plan that lapsed, a cancellation and the lower plan it ends on, with what that
leaves read-only and until when chats and audit rows are kept as before (app/billing.py).

A template writes `Content`: the subject, and the body as paragraphs and links. Both the plain
text and the HTML of a message are made from it (`compose`), so they say the same: the text has
each link on a line of its own, the HTML a button with the address under it to copy, in the
layout every message shares. Only the links a template names become links in the HTML; a name a
member typed, an organisation's say, stays text however much it looks like an address.

A member's mail is posted through the queue (app/postbox.py) and sent by the worker through a
`Mailer`; the operator's reports (ALERT_EMAIL) are sent at once by the thread that writes them,
since the queue may be what they report on. There are three mailers: the log mailer writes each
message to the log, which is enough to follow a link in development and never runs in
production; the SMTP mailer sends through any relay (Amazon SES's on AWS, deploying.md "Mail");
and the Resend mailer through Resend's API, which takes the message's key so that a message sent
again after a lost lease is sent once. When one fails it logs the message's subject and what the
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
    # Headers besides From, To and Subject: a notice's List-Unsubscribe (app/postbox.py).
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
    """Writes messages to the log instead of sending them."""

    def send(self, message: Message) -> None:
        log.info("Email to %s: %s\n%s", message.to, message.subject, message.text)


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
        if message.headers:
            body["headers"] = message.headers
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


def mailer_from_settings(settings: Settings) -> Mailer:
    if settings.resend_api_key:
        return ResendMailer(settings.resend_api_key, settings.mail_from, httpx.Client())
    if settings.smtp_url:
        return SmtpMailer(settings.smtp_url, settings.mail_from)
    return LogMailer()


def _greeting(name: str) -> str:
    return f"Hi {name}," if name else "Hi,"


def invitation_email(inviter: str, organization: str, role: str, link: str, days: int) -> Content:
    """To an address a member of an organisation invited: who asked, to what and as what, and
    the link to the page where whoever holds the address accepts, with a new account if it has
    none. The names are in the body alone: a subject is the app's own sentence, which the log
    repeats when a message cannot be sent."""
    article = "an" if role[0] in "aeiou" else "a"
    return Content(
        "You are invited to join an organisation on Emudoc",
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
            f"Your Emudoc account is set to be deleted on {due}. Then it goes for good, with "
            "every organisation you are the only member of and everything in it: projects, "
            "documents and chats. Every other browser and device was signed out.",
            "To keep the account, sign in before then and choose to keep it:",
            Link(link, "Sign in"),
            "If you did not ask for this, someone else could use your account: sign in, keep "
            "it, and change your password.",
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


def payment_failed_email(
    name: str,
    organization: str,
    plan: str | None,
    until: str | None,
    pay: str | None,
    page: str,
) -> Content:
    """To an owner of an organisation whose subscription's payment failed (app/billing.py): until
    when the plan stays, or that the organisation is on Free meanwhile, and the two ways to pay:
    the invoice's own page at Stripe, and the payment method under Manage billing. `plan` is
    None once the organisation is on Free."""
    subscription = f"the {plan} plan of {organization}" if plan else f"{organization}'s plan"
    if until is None:
        stands = (
            f"{organization} is on the Free plan meanwhile, keeping everything it holds, and has "
            "its plan again once the payment is made."
        )
    else:
        stands = (
            f"{organization} keeps the {plan} plan until {until} while the payment is tried "
            "again. If it has not been made by then, the organisation moves to the Free plan, "
            "keeping everything it holds, until it is."
        )
    change = (
        "the card or other payment method under Manage billing on the organisation page, where "
        "the payment is then tried again:"
    )
    ways: tuple[Block, ...] = (
        ("Pay the invoice here:", Link(pay, "Pay the invoice"), f"or change {change}")
        if pay
        else (f"Change {change}",)
    )
    return Content(
        "A payment for your Emudoc plan failed",
        (
            _greeting(name),
            f"A payment for {subscription} on Emudoc failed. {stands}",
            *ways,
            Link(page, "Open the organisation page"),
        ),
    )


def trial_ending_email(
    name: str, organization: str, plan: str, ends: str, cancelled: bool, page: str
) -> Content:
    """To an owner of an organisation whose trial ends in a few days (Stripe tells three days
    ahead): what happens then, and where to change it."""
    then = (
        f"The subscription was cancelled, so then {organization} moves to the Free plan, keeping "
        "everything it holds."
        if cancelled
        else "Then the subscription's first payment is taken with the payment method given, "
        "and the plan goes on."
    )
    return Content(
        "Your Emudoc trial ends soon",
        (
            _greeting(name),
            f"The trial of the {plan} plan for {organization} on Emudoc ends on {ends}. {then}",
            "To change the plan or the seats, or to cancel, open Manage billing on the "
            "organisation page:",
            Link(page, "Open the organisation page"),
        ),
    )


def plan_lapsed_email(
    name: str,
    organization: str,
    plan: str,
    page: str,
    over: str | None = None,
    kept: str | None = None,
    until: str | None = None,
) -> Content:
    """To an owner of an organisation that moved to the Free plan because its subscription's
    payment kept failing: what it holds past Free's limits (`over`, "4 projects of 1"), and
    what Free keeps for less time (`kept`, worded by `shortened` in app/retention.py), kept as
    before `until` a day."""
    return Content(
        "Your organisation moved to the Free plan",
        (
            _greeting(name),
            f"The payment for the {plan} plan of {organization} on Emudoc could not be made, so "
            "the organisation is on the Free plan now, keeping everything it holds.",
            *_lowered(organization, "Free", plan, over, kept, until),
            "To have the plan again, open the organisation page and pay under Manage billing, "
            "or choose the plan again if the subscription has ended:",
            Link(page, "Open the organisation page"),
        ),
    )


def plan_lowered_email(
    name: str,
    organization: str,
    was_plan: str,
    plan: str,
    seats: int | None,
    ended: bool,
    over: str | None,
    kept: str | None,
    until: str | None,
    page: str,
) -> Content:
    """To an owner of an organisation whose subscription ended, or was changed to a lower plan
    or fewer `seats`, where that leaves it read-only or keeps chats or audit rows for less time:
    as `plan_lapsed_email` says them."""
    with_seats = f" with {seats:,} seat{'' if seats == 1 else 's'}" if seats else ""
    moved = (
        f"The subscription to the {was_plan} plan of {organization} on Emudoc has ended, so the "
        f"organisation is on the {plan} plan now, keeping everything it holds."
        if ended
        else f"The subscription of {organization} on Emudoc was changed to the {plan} plan"
        f"{with_seats}, keeping everything the organisation holds."
    )
    return Content(
        "Your organisation's plan changed",
        (
            _greeting(name),
            moved,
            *_lowered(organization, plan, was_plan, over, kept, until),
            "To choose another plan, open the organisation page:",
            Link(page, "Open the organisation page"),
        ),
    )


def cancellation_email(
    name: str,
    organization: str,
    plan: str,
    ends: str,
    over: str | None,
    kept: str | None,
    until: str | None,
    page: str,
) -> Content:
    """To an owner of an organisation whose subscription was cancelled: the day it ends, when
    the organisation moves to the Free plan, and what that will leave read-only and keep for
    less time, as `plan_lapsed_email` says them."""
    return Content(
        "Your Emudoc subscription was cancelled",
        (
            _greeting(name),
            f"The subscription to the {plan} plan of {organization} on Emudoc was cancelled. It "
            f"runs until {ends}, and then the organisation moves to the Free plan, keeping "
            "everything it holds.",
            *_lowered(organization, "Free", plan, over, kept, until, coming=True),
            "If it should go on, renew the subscription under Manage billing on the "
            "organisation page before then:",
            Link(page, "Open the organisation page"),
        ),
    )


def _lowered(
    organization: str,
    plan: str,
    was_plan: str,
    over: str | None,
    kept: str | None,
    until: str | None,
    coming: bool = False,
) -> tuple[str, ...]:
    """The paragraphs on what a lower plan leaves read-only and keeps for less time; `coming`
    words them for a move still to come."""
    paragraphs = []
    if over:
        holds = "It will then hold" if coming else "It holds"
        paragraphs.append(
            f"{holds} more than the {plan} plan allows ({over}), so until it is within the plan "
            f"nothing can be added to {organization} or changed in it: what it holds can still "
            "be read, asked about and deleted."
        )
    if kept and until:
        kept_as = "will be kept as the" if coming else "are kept as the"
        keeps = "keeps" if coming else "kept"
        paragraphs.append(
            f"The {plan} plan keeps {kept}. Those older {kept_as} {was_plan} plan {keeps} them "
            f"until {until}, and deleted then."
        )
    return tuple(paragraphs)
