"""The mail the app sends its members, posted through the queue (app/jobs.py).

`post` queues a `send_email` job in the caller's session, so that the message goes with the
change that calls for it or not at all, and the worker's mail lane sends it (app/worker.py): a
request never waits on a relay, and a relay that is down when a link is asked for costs the
link a wait, not the link. A try the relay refused for now (`MailError.transient`) is queued
again after a wait that doubles, MAX_TRIES in about half an hour, within a reset link's hour; a
message refused for good, or for the last time, ends the job failed, which the operator hears of
with the other dead letters (app/deadletters.py) and from the rules on `emudoc_mail_sent`.

A message with a link that signs in or makes an account carries the link's token, which the
database otherwise never holds (`auth_tokens` keeps its hash), and a job kept for the operator,
or a backup of it, would be read by whoever reads the database. So a job holds its message
encrypted with ENCRYPTION_KEYS (app/keyring.py) for the message's own key, and gives it up once
it is sent or given up (`_spent`); its subject, the app's own sentence, stays for the operator.
A message is not a column `encrypt-secrets` keeps up: it lives minutes, and the keys an older
backup needs stay in ENCRYPTION_KEYS for BACKUP_DAYS anyway (deploying.md, "The encryption
keys"). A job that runs again after a lost lease sends again, once more through SMTP, and once
through Resend, which holds the message's key as its Idempotency-Key for a day.

Notices are the mail a member may do without, as against what the account, an invitation or
billing needs said (deploying.md, "Mail"). `post_notice` posts one to a user who has not turned
its kind's email off (`User.wants`, app/notices.py) and whose address the provider has not said
to spare (app/bounces.py), with a footer that says how to stop them and RFC 8058's one-click
headers, so that a mail program's own "unsubscribe" turns them off without opening anything:
both lead to `/api/mail/unsubscribe` with a token that names the user and the kind, encrypted
with ENCRYPTION_KEYS, so that it cannot be made for another and lasts as long as its key is
held. A token from before the kinds were chosen apart names the user alone, and stops every
kind's email.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.bounces import suppressed
from app.db import SessionLocal
from app.encrypted import keys
from app.jobs import LIVE, SEND_EMAIL, enqueue
from app.keyring import Undecryptable, is_encrypted
from app.mail import Block, Content, Link, LogMailer, Mailer, MailError, Message, compose
from app.metrics import MAIL_SENT
from app.models import NOTICE_CHANNELS, NOTICE_KINDS, Job, User

if TYPE_CHECKING:
    # For the handler's signature alone: the pipeline posts notices through this module.
    from app.indexing.pipeline import Services

log = logging.getLogger(__name__)

# How many times a message is tried while the relay refuses it for now, and the first wait;
# each wait is twice the one before, so the last try is half an hour after the first.
MAX_TRIES = 5
FIRST_WAIT = timedelta(minutes=2)
# Where a notice's link to stop them leads: the page, which says what it did, and the route a
# mail program posts to (RFC 8058), each with the token.
UNSUBSCRIBE_PAGE = "/unsubscribe"
UNSUBSCRIBE_ROUTE = "/api/mail/unsubscribe"
# What an unsubscribe token is encrypted for: a user's id, under no row's place, so that a
# value of another kind decrypts as none.
UNSUBSCRIBE_PLACE = "mail.unsubscribe"
STOP_NOTICES = (
    "You get this email because notices like it are on by email for your Emudoc account. To "
    "stop them, open this link, or choose which notices you get, and how, on your account page:"
)
# The way of telling a notice this module posts.
BY_EMAIL = NOTICE_CHANNELS[1]

_SPENT = text("UPDATE jobs SET payload = payload - 'message' WHERE id = :id")


def post(session: Session, to: str, content: Content | Message, org_id: int | None = None) -> Job:
    """Queue the message to `to` in the caller's session, sent once the caller commits.
    `org_id` names the organisation the mail is about, an invitation's or a subscription's; mail
    about an account or an address is no organisation's."""
    message = content if isinstance(content, Message) else compose(to, content)
    return _queue(session, message.subject, message.key, _sealed(message), org_id, 0, None)


def post_notice(
    session: Session,
    user: User,
    kind: str,
    content: Content,
    app_url: str,
    org_id: int | None = None,
) -> Job | None:
    """Queue a notice of the kind (NOTICE_KINDS) to the user unless they turned its email off,
    or the provider said to spare their address; None when it is not posted."""
    if not user.wants(kind, BY_EMAIL) or suppressed(session, user.email):
        return None
    return post(session, user.email, notice(user, kind, content, app_url), org_id)


def notice(user: User, kind: str, content: Content, app_url: str) -> Message:
    """The notice to the user: `content` with the footer and headers that turn its kind's email
    off."""
    token = quote(unsubscribe_token(user.id, kind))
    page = f"{app_url}{UNSUBSCRIBE_PAGE}#token={token}"
    footer: tuple[Block, ...] = (STOP_NOTICES, Link(page, "Stop these emails"))
    return compose(
        user.email,
        content,
        footer,
        {
            "List-Unsubscribe": f"<{app_url}{UNSUBSCRIBE_ROUTE}?token={token}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


def unsubscribe_token(user_id: int, kind: str) -> str:
    return keys().encrypt(f"{user_id}:{kind}", UNSUBSCRIBE_PLACE)


@dataclass(frozen=True)
class Unsubscriber:
    """Whose notices an unsubscribe token stops by email, and of which kind: None for every
    kind, as a token made before the kinds were chosen apart says."""

    user_id: int
    kind: str | None


def unsubscriber(token: str) -> Unsubscriber | None:
    """Whom an unsubscribe token was made for; None for anything else, or for one whose key
    ENCRYPTION_KEYS no longer holds."""
    if not is_encrypted(token):
        return None
    try:
        user_id, _, kind = keys().decrypt(token, UNSUBSCRIBE_PLACE).partition(":")
        if kind and kind not in NOTICE_KINDS:
            return None
        return Unsubscriber(int(user_id), kind or None)
    except (Undecryptable, ValueError):
        return None


def run_email(job: Job, services: "Services") -> str | None:
    """The worker's handler of `send_email`."""
    return deliver(job, services.mailer or LogMailer())


def deliver(job: Job, mailer: Mailer) -> str | None:
    """Send the job's message, or queue it again for later when the relay refused it for now;
    returns why the message was given up, for the job to end failed with."""
    subject = job.payload.get("subject", "")
    sealed = job.payload.get("message")
    if sealed is None:
        # Sent or given up by a run that lost its lease after it was done with the message.
        return None
    try:
        message = _opened(sealed, job.payload["email"])
    except Undecryptable as error:
        _spent(job.id)
        MAIL_SENT.labels("failed").inc()
        return f"The email {subject!r} could not be read to be sent: {error}"
    try:
        mailer.send(message)
    except MailError as error:
        tries = job.payload.get("try", 1)
        if not error.transient or tries >= MAX_TRIES:
            _spent(job.id)
            MAIL_SENT.labels("failed").inc()
            if error.transient:
                return f"The email {subject!r} was refused {tries} times over {_waited(tries)}"
            return f"The relay refused the email {subject!r} for good"
        wait = FIRST_WAIT * 2 ** (tries - 1)
        with SessionLocal() as session:
            _queue(
                session,
                subject,
                job.payload["email"],
                sealed,
                job.org_id,
                tries,
                datetime.now(UTC) + wait,
            )
            session.execute(_SPENT, {"id": job.id})
            session.commit()
        MAIL_SENT.labels("retried").inc()
        log.info(
            "The email %r was refused for now; it is tried again in %s minutes (try %s of %s)",
            subject,
            round(wait.total_seconds() / 60),
            tries + 1,
            MAX_TRIES,
        )
        return None
    _spent(job.id)
    MAIL_SENT.labels("sent").inc()
    return None


def abandon_email(job: Job, services: "Services") -> None:
    """A message whose every try was lost to a worker that died: nobody waits on the page for
    it, and whoever asked for a link asks again; the job keeps no copy."""
    _spent(job.id)


def _queue(
    session: Session,
    subject: str,
    key: str,
    sealed: str,
    org_id: int | None,
    tries: int,
    run_at: datetime | None,
) -> Job:
    return enqueue(
        session,
        SEND_EMAIL,
        org_id=org_id,
        payload={"email": key, "subject": subject, "message": sealed, "try": tries + 1},
        priority=LIVE,
        run_at=run_at,
    )


def _sealed(message: Message) -> str:
    return keys().encrypt(json.dumps(asdict(message)), _place(message.key))


def _opened(sealed: str, key: str) -> Message:
    return Message(**json.loads(keys().decrypt(sealed, _place(key))))


def _place(key: str) -> str:
    """What a queued message is encrypted for: its own key, which every try of it carries, so
    that a message copied under another key decrypts as none."""
    return f"jobs.payload:email:{key}"


def _spent(job_id: int) -> None:
    """Take the message out of the job, which is done with it."""
    with SessionLocal() as session:
        session.execute(_SPENT, {"id": job_id})
        session.commit()


def _waited(tries: int) -> str:
    minutes = round((FIRST_WAIT * (2 ** (tries - 1) - 1)).total_seconds() / 60)
    return f"{minutes} minutes"
