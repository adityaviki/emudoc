"""What the mail provider says of the mail it could not deliver, kept so that the app spares the
address: mail to it bounced for good, or its reader marked a message as spam.

Such an address is kept in `mail_suppressions` by its SHA-256 and sent no notices
(app/postbox.py) for SUPPRESSED_FOR, or until its user turns notices on again on the account
page (`spare`). The mail an account cannot do without, a link or what billing must say, is still
posted: the provider keeps a list of its own and drops what goes to an address on it, so that
the app's list is for what the app chooses to send, and a mailbox that is fixed, or a member who
marked one message as spam by mistake, is not cut off from a reset link by the app.

Two providers say so, each in its own way. Resend posts its events to the webhook
(`/api/mail/events`, app/api/mail.py), signed as Svix signs: an HMAC-SHA256 of the event's id,
its time and the body as it came, with the endpoint's secret (`verified_resend_event`). Amazon
SES publishes the events of its configuration set to EventBridge, which the AWS module routes
into an SQS queue, MAIL_EVENTS_QUEUE, that a thread of the worker reads every MAIL_EVENTS_SECONDS
with the app's own credentials (`read_ses_events`): no address of the app's is opened to AWS,
and nothing need be verified that IAM did not. A bounce either calls transient (a full mailbox,
a server that kept answering "later") spares nothing.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import boto3
from sqlalchemy import Connection, delete, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.metrics import MAIL_EVENTS
from app.models import MailSuppression

log = logging.getLogger(__name__)

# How long an address is sent no notices after the provider said to spare it.
SUPPRESSED_FOR = timedelta(days=365)
# How far an event's time may be from this machine's clock, as Stripe's (app/payments.py).
TOLERANCE = 300
# How often the worker reads the queue of SES's events, and how many it takes a request.
MAIL_EVENTS_SECONDS = 60.0
BATCH = 10
SVIX_HEADERS = ("svix-id", "svix-timestamp", "svix-signature")
# SQS's address for a queue: https://sqs.<region>.amazonaws.com/<account>/<name>.
_SQS_HOST = re.compile(r"sqs\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?")

_SPARE = text(
    "INSERT INTO mail_suppressions (address_hash, reason) VALUES (:hash, :reason) "
    "ON CONFLICT (address_hash) DO UPDATE SET reason = excluded.reason, recorded_at = now()"
)
_PRUNE = text("DELETE FROM mail_suppressions WHERE recorded_at < now() - :age")


@dataclass(frozen=True)
class Undeliverable:
    """An address the provider said not to write to, and why: `bounce` or `complaint`."""

    address: str
    reason: str


class Unsigned(Exception):
    """A request to the webhook that Resend did not sign with the endpoint's secret."""


def address_hash(address: str) -> str:
    """What the table keeps of an address, however it was written."""
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def suppressed(session: Session, address: str) -> bool:
    """Whether the provider said to spare `address` within SUPPRESSED_FOR."""
    return (
        session.scalar(
            text(
                "SELECT 1 FROM mail_suppressions WHERE address_hash = :hash "
                "AND recorded_at >= now() - :age"
            ),
            {"hash": address_hash(address), "age": SUPPRESSED_FOR},
        )
        is not None
    )


def spare(session: Session, undeliverable: Iterable[Undeliverable]) -> int:
    """Keep the addresses, in the caller's transaction; returns how many."""
    count = 0
    for found in undeliverable:
        session.execute(_SPARE, {"hash": address_hash(found.address), "reason": found.reason})
        MAIL_EVENTS.labels(found.reason).inc()
        count += 1
    return count


def forget(session: Session, address: str) -> None:
    """Write to `address` again: its user turned notices on, in the caller's transaction."""
    session.execute(
        delete(MailSuppression).where(MailSuppression.address_hash == address_hash(address))
    )


def prune_suppressions(connection: Connection) -> int:
    """Forget the addresses spared longer than SUPPRESSED_FOR ago; returns how many."""
    return connection.execute(_PRUNE, {"age": SUPPRESSED_FOR}).rowcount


def verified_resend_event(
    body: bytes, headers: Mapping[str, str], secret: str, now: float | None = None
) -> dict:
    """The event `body` holds, once its headers show Resend signed it with the endpoint's
    `secret` (whsec_ and the key in base64) within TOLERANCE seconds of `now`. Raises Unsigned
    otherwise, and for a body that is not an event."""
    msg_id, stamp, signatures = (headers.get(name) for name in SVIX_HEADERS)
    if not (msg_id and stamp and signatures and secret):
        raise Unsigned("no signature")
    if not stamp.isdecimal():
        raise Unsigned("a malformed signature")
    if abs((now if now is not None else time.time()) - int(stamp)) > TOLERANCE:
        raise Unsigned("a signature too old")
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError):
        raise Unsigned("a signature this endpoint's secret cannot check") from None
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    given = [part[3:] for part in signatures.split() if part.startswith("v1,")]
    if not any(hmac.compare_digest(expected, signature) for signature in given):
        raise Unsigned("a signature that is not Resend's")
    try:
        event = json.loads(body)
    except ValueError:
        raise Unsigned("a body that is not JSON") from None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise Unsigned("a body that is not an event")
    return event


def sign_resend(body: bytes, secret: str, msg_id: str, stamp: int | None = None) -> dict:
    """The headers Resend would send `body` with, for the tests and for trying the webhook by
    hand."""
    stamp = int(time.time()) if stamp is None else stamp
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signature = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return dict(zip(SVIX_HEADERS, (msg_id, str(stamp), f"v1,{signature}"), strict=True))


def resend_undeliverable(event: Mapping[str, Any]) -> list[Undeliverable]:
    """The addresses a Resend event says to spare: those of a bounce Resend calls permanent
    (`email.bounced`, which a message to an address on Resend's own list is too), and of a
    complaint. Every other event says nothing to keep."""
    data = event.get("data") or {}
    addresses = [address for address in data.get("to") or [] if isinstance(address, str)]
    kind = event.get("type")
    if kind == "email.bounced" and (data.get("bounce") or {}).get("type") == "Permanent":
        return [Undeliverable(address, "bounce") for address in addresses]
    if kind == "email.complained":
        return [Undeliverable(address, "complaint") for address in addresses]
    return []


def ses_undeliverable(event: Mapping[str, Any]) -> list[Undeliverable]:
    """The addresses an event of SES's says to spare, as EventBridge delivers it (the SES event
    in `detail`), as SNS does (in `Message`, a string) or as it is: those of a permanent bounce,
    and of a complaint."""
    if isinstance(event.get("Message"), str):
        event = json.loads(event["Message"])
    detail = event.get("detail", event)
    kind = detail.get("eventType") or detail.get("notificationType")
    if kind == "Bounce" and (detail.get("bounce") or {}).get("bounceType") == "Permanent":
        found = detail["bounce"].get("bouncedRecipients") or []
        reason = "bounce"
    elif kind == "Complaint":
        found = (detail.get("complaint") or {}).get("complainedRecipients") or []
        reason = "complaint"
    else:
        return []
    return [
        Undeliverable(recipient["emailAddress"], reason)
        for recipient in found
        if isinstance(recipient, dict) and isinstance(recipient.get("emailAddress"), str)
    ]


def sqs_client(queue_url: str) -> Any:
    """A client of the queue's own endpoint and region, whatever AWS_ENDPOINT_URL names for the
    bucket; credentials as boto3 finds them, the app role's on AWS. A queue elsewhere, a stand-in
    for SQS, takes the region boto3 is configured with, or any."""
    parts = urlsplit(queue_url)
    match = _SQS_HOST.fullmatch(parts.hostname or "")
    region = match.group(1) if match else boto3.session.Session().region_name or "us-east-1"
    return boto3.client("sqs", region_name=region, endpoint_url=f"{parts.scheme}://{parts.netloc}")


def take_ses_events(client: Any, queue_url: str) -> int:
    """Keep what the queue's events say until it is empty, then return how many addresses they
    named. A message is deleted once what it says is committed; one that is not SES's event
    is deleted with a warning, so that it does not come back for ever."""
    taken = 0
    while True:
        messages = client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=BATCH).get(
            "Messages", []
        )
        if not messages:
            return taken
        with SessionLocal() as session:
            for message in messages:
                try:
                    found = ses_undeliverable(json.loads(message["Body"]))
                except (ValueError, KeyError, TypeError, AttributeError):
                    log.warning(
                        "A message of MAIL_EVENTS_QUEUE is not an event of SES's and is "
                        "dropped: %s",
                        message.get("MessageId"),
                    )
                    continue
                taken += spare(session, found)
            session.commit()
        client.delete_message_batch(
            QueueUrl=queue_url,
            Entries=[
                {"Id": str(index), "ReceiptHandle": message["ReceiptHandle"]}
                for index, message in enumerate(messages)
            ],
        )


def read_ses_events(
    queue_url: str, stopping: threading.Event, client: Callable[[str], Any] = sqs_client
) -> None:
    """The worker's thread: take SES's events every MAIL_EVENTS_SECONDS until it stops. A round
    that fails is logged and the next tries again; the queue keeps what was not taken."""
    reader = None
    while not stopping.wait(MAIL_EVENTS_SECONDS):
        try:
            reader = reader or client(queue_url)
            if taken := take_ses_events(reader, queue_url):
                log.info("SES said not to write to %s more addresses", taken)
        except Exception:
            log.exception(
                "Reading SES's events from MAIL_EVENTS_QUEUE failed; it is read again in %g "
                "seconds",
                MAIL_EVENTS_SECONDS,
            )
