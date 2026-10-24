"""The providers' ways in for the mail sent to projects' addresses (app/inbound.py): each hands
the app the message whole, as it was sent, with whom it was delivered to and the provider's
verdict on its sender.

Resend posts `email.received` to the webhook its other events reach (`/api/mail/events`,
app/api/mail.py), signed as Svix signs. The event names the message and its recipients but
holds no body, so the route queues a `receive_mail` job for a message to an address at
INBOUND_DOMAIN and answers at once, and the worker asks Resend's API for it (`GET
/emails/receiving/{id}` with RESEND_API_KEY: its recipients, its `authentication`, SPF, DKIM and
DMARC as Resend's receiving server found them, and `raw`, a link to the message as it was sent,
good for an hour), then downloads it, up to MAX_MESSAGE_BYTES. The sender counts as proven when
DMARC passed, or DKIM did, which Resend passes only for a signature of the From domain.

A provider out of reach, or answering "later" (a 429, a 5xx), is asked again after FIRST_WAIT,
doubling, MAX_TRIES times in all, as the mail the app sends is (app/postbox.py); one that refuses
the request, or no longer holds the message, ends the job failed with the reason, for the
operator's dead letters, where `python -m app.maintenance receive-mail` can hand over a copy
saved from the provider's dashboard.

SES writes each message its receipt rule took as it was sent into a bucket and publishes a notice
of it, with the object's key and its SPF, DKIM and DMARC verdicts, which reaches an SQS queue,
INBOUND_MAIL_QUEUE (the AWS module's inbound). A thread of the worker's waits on the queue
(`read_inbound`, SQS's long poll) with the app's own credentials and queues a `receive_mail` job
for each notice, deleting the notice once the job is committed: no address of the app's is
opened to AWS. The job reads the message from the bucket, takes it in, and deletes the bucket's
copy. The sender counts as proven when DMARC passed, or, for a domain that publishes no DMARC
policy, when DKIM passed and every signature the message carries is of the From domain or a
domain it is under or over, so that the signature SES passed is one of them (`aligned_dkim`).
"""

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from functools import lru_cache
from io import BytesIO
from typing import Any

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.bounces import region_of, sqs_client
from app.config import get_settings
from app.db import SessionLocal
from app.errors import AppError
from app.inbound import MAX_MESSAGE_BYTES, Received, receive, token_in
from app.inbound import sender_of as parse_sender
from app.jobs import RECEIVE_MAIL, Lease, enqueue
from app.mail import RESEND_API
from app.metrics import INBOUND_MESSAGES
from app.models import InboundMessage, Job
from app.notices import app_address
from app.storage import Storage

log = logging.getLogger(__name__)

RECEIVED = "email.received"
RESEND = "resend"
SES = "ses"
# How long a request to the queue waits for a notice, SQS's longest: a message is taken within
# seconds of its arrival, the queue asked three times a minute while it is quiet.
INBOUND_WAIT = 20
BATCH = 10
# How long the thread waits after a round that failed before it asks again.
INBOUND_PAUSE = 60.0
MAX_TRIES = 5
FIRST_WAIT = timedelta(minutes=1)
# How long the API and the download get: a message of 40 MB is a few seconds' download.
FETCH_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class FetchError(AppError):
    """The provider did not hand the message over; `transient` when asking again later may."""


@lru_cache
def http_client() -> httpx.Client:
    """The worker's client for the providers' APIs, kept for the process's connections."""
    return httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=False)


def recipients_of(data: dict[str, Any]) -> list[str]:
    """The addresses a message of Resend's was for: its To, Cc and Bcc, and the addresses it was
    forwarded for, each bare."""
    written = [
        value
        for name in ("to", "cc", "bcc", "received_for")
        for value in data.get(name) or []
        if isinstance(value, str)
    ]
    return [address for _, address in getaddresses(written) if address]


def queue_resend(session: Session, event: dict[str, Any]) -> bool:
    """Queue the job that takes the message an `email.received` event names, in the caller's
    session, unless none of its recipients is at INBOUND_DOMAIN; whether it was queued."""
    data = event.get("data") or {}
    email_id = data.get("email_id")
    domain = get_settings().inbound_domain
    if not isinstance(email_id, str) or not email_id or not domain:
        return False
    if not any(token_in(address, domain) for address in recipients_of(data)):
        INBOUND_MESSAGES.labels("unknown_address").inc()
        return False
    enqueue(
        session,
        RECEIVE_MAIL,
        org_id=None,
        payload={"source": RESEND, "email_id": email_id[:100], "try": 1},
    )
    return True


def fetch_resend(http: httpx.Client, api_key: str, email_id: str) -> Received:
    """The message Resend received under `email_id`, as it was sent, with its recipients and
    whether its sender is proven."""
    found = _resend_get(http, api_key, email_id)
    authentication = found.get("authentication") or {}
    proven = authentication.get("dmarc") == "pass" or authentication.get("dkim") == "pass"
    url = (found.get("raw") or {}).get("download_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise FetchError(
            f"Resend gave no link to the message {email_id} as it was sent.", transient=False
        )
    return Received(f"{RESEND}:{email_id}", _download(http, url), recipients_of(found), proven)


def _resend_get(http: httpx.Client, api_key: str, email_id: str) -> dict[str, Any]:
    try:
        answer = http.get(
            f"{RESEND_API}/emails/receiving/{email_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except httpx.HTTPError as error:
        raise FetchError(
            f"Resend could not be asked for the message {email_id}: {type(error).__name__}.",
            transient=True,
        ) from error
    if answer.status_code == 404:
        raise FetchError(f"Resend holds no message {email_id}.", transient=False)
    if not answer.is_success:
        later = answer.status_code == 429 or answer.status_code >= 500
        raise FetchError(
            f"Resend answered {answer.status_code} when asked for the message {email_id}.",
            transient=later,
        )
    try:
        found = answer.json()
    except ValueError:
        found = None
    if not isinstance(found, dict):
        raise FetchError(f"Resend's answer for the message {email_id} is not one.", transient=True)
    return found


def _download(http: httpx.Client, url: str) -> bytes:
    """The bytes at `url`, up to one past MAX_MESSAGE_BYTES, which the message's projects then
    turn away as too large."""
    chunks: list[bytes] = []
    size = 0
    try:
        with http.stream("GET", url) as response:
            if not response.is_success:
                later = response.status_code == 429 or response.status_code >= 500
                raise FetchError(
                    f"The message's download answered {response.status_code}.", transient=later
                )
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_MESSAGE_BYTES:
                    break
    except httpx.HTTPError as error:
        raise FetchError(
            f"The message could not be downloaded: {type(error).__name__}.", transient=True
        ) from error
    return b"".join(chunks)


def run_receive(
    job: Job, storage: Storage, lease: Lease, http: httpx.Client | None = None
) -> str | None:
    """The `receive_mail` job: fetch the message from its provider and take it into the projects
    it is addressed to, or queue the fetch again for later; returns why it was given up."""
    payload = job.payload
    settings = get_settings()
    source = payload.get("source")
    if source not in (RESEND, SES):
        return f"A message of an unknown way in, {source!r}, cannot be fetched."
    s3 = s3_client(payload["region"]) if source == SES else None
    if s3 is not None and _taken(f"{SES}:{payload['email_id']}"):
        # Taken in by a run that lost its lease before it was done with the copy.
        _discard(s3, payload)
        return None
    try:
        if s3 is not None:
            received = fetch_ses(payload, s3)
        else:
            received = fetch_resend(
                http or http_client(), settings.resend_api_key, payload["email_id"]
            )
    except FetchError as error:
        tries = payload.get("try", 1)
        if not error.transient or tries >= MAX_TRIES:
            return f"{error} It was not taken in{f' after {tries} tries' if tries > 1 else ''}."
        wait = FIRST_WAIT * 2 ** (tries - 1)
        with SessionLocal() as session:
            enqueue(
                session,
                RECEIVE_MAIL,
                org_id=None,
                payload={**payload, "try": tries + 1},
                run_at=datetime.now(UTC) + wait,
            )
            session.commit()
        log.info(
            "%s It is asked for again in %s minutes (try %s of %s)",
            error,
            round(wait.total_seconds() / 60),
            tries + 1,
            MAX_TRIES,
        )
        return None
    with SessionLocal() as session:
        receive(session, storage, received, app_address(), lease)
    if s3 is not None:
        _discard(s3, payload)
    return None


def _taken(delivery: str) -> bool:
    with SessionLocal() as session:
        return bool(session.scalar(select(exists().where(InboundMessage.delivery == delivery))))


def _discard(s3: Any, payload: dict[str, Any]) -> None:
    """Delete SES's copy of a message taken in; one that stays goes with the bucket's rule."""
    try:
        s3.delete_object(Bucket=payload["bucket"], Key=payload["key"])
    except (BotoCoreError, ClientError, OSError) as error:
        log.warning(
            "SES's copy of the message %s stays in its bucket until its rule removes it: %s",
            payload["email_id"],
            type(error).__name__,
        )


def abandon_receive(job: Job) -> None:
    """A message whose every fetch was lost to a worker that died: the provider still holds it,
    and the operator hands it over by hand."""
    log.warning(
        "The message %s was not taken in: its job was lost %s times. Save it from the provider "
        "and hand it over with `python -m app.maintenance receive-mail`.",
        job.payload.get("email_id"),
        job.max_attempts,
    )


def ses_notice(body: dict[str, Any], queue_url: str) -> dict[str, Any] | None:
    """The payload of the job that takes the message an SES notice is about, as the queue holds
    it (SNS's raw delivery) or in SNS's envelope; None for a body that is no notice of a message
    an S3 action stored."""
    if isinstance(body.get("Message"), str):
        body = json.loads(body["Message"])
    receipt = body.get("receipt") or {}
    action = receipt.get("action") or {}
    message_id = (body.get("mail") or {}).get("messageId")
    if (
        body.get("notificationType") != "Received"
        or action.get("type") != "S3"
        or not isinstance(message_id, str)
        or not isinstance(action.get("bucketName"), str)
        or not isinstance(action.get("objectKey"), str)
    ):
        return None
    return {
        "source": SES,
        "email_id": message_id[:100],
        "region": region_of(queue_url),
        "bucket": action["bucketName"],
        "key": action["objectKey"],
        "recipients": [r for r in receipt.get("recipients") or [] if isinstance(r, str)][:100],
        "dmarc": (receipt.get("dmarcVerdict") or {}).get("status"),
        "dkim": (receipt.get("dkimVerdict") or {}).get("status"),
        "try": 1,
    }


def take_inbound(client: Any, queue_url: str, wait: int = INBOUND_WAIT) -> int:
    """Queue the job of each notice the queue holds, waiting up to `wait` seconds for the
    first, until it is empty; returns how many. A notice is deleted once its job is committed;
    one that is no notice is deleted with a warning, so that it does not come back for ever."""
    queued = 0
    while True:
        messages = client.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=BATCH, WaitTimeSeconds=wait
        ).get("Messages", [])
        if not messages:
            return queued
        wait = 0
        with SessionLocal() as session:
            for message in messages:
                try:
                    payload = ses_notice(json.loads(message["Body"]), queue_url)
                except (ValueError, KeyError, TypeError, AttributeError):
                    payload = None
                if payload is None:
                    log.warning(
                        "A message of INBOUND_MAIL_QUEUE is no notice of mail SES received and "
                        "is dropped: %s",
                        message.get("MessageId"),
                    )
                    continue
                enqueue(session, RECEIVE_MAIL, org_id=None, payload=payload)
                queued += 1
            session.commit()
        client.delete_message_batch(
            QueueUrl=queue_url,
            Entries=[
                {"Id": str(index), "ReceiptHandle": message["ReceiptHandle"]}
                for index, message in enumerate(messages)
            ],
        )


def read_inbound(
    queue_url: str, stopping: threading.Event, client: Callable[[str], Any] = sqs_client
) -> None:
    """The worker's thread: wait on the queue for SES's notices and queue their jobs until the
    worker stops. A round that fails is logged and the next tries again after a pause; the
    queue keeps what was not taken."""
    reader = None
    while not stopping.is_set():
        try:
            reader = reader or client(queue_url)
            if queued := take_inbound(reader, queue_url):
                log.info("SES received %s messages for projects' addresses", queued)
        except Exception:
            log.exception(
                "Reading SES's notices from INBOUND_MAIL_QUEUE failed; it is read again in %g "
                "seconds",
                INBOUND_PAUSE,
            )
            stopping.wait(INBOUND_PAUSE)


def fetch_ses(payload: dict[str, Any], s3: Any) -> Received:
    """The message SES stored for the notice the payload holds, with its recipients and whether
    its sender is proven: DMARC passed, or, for a domain with no DMARC policy (GRAY), DKIM did
    and every signature the message carries is of the From domain."""
    try:
        stored = s3.get_object(Bucket=payload["bucket"], Key=payload["key"])
        raw = stored["Body"].read(MAX_MESSAGE_BYTES + 1)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise FetchError(
                f"SES's copy of the message {payload['email_id']} is gone.", transient=False
            ) from None
        raise FetchError(
            f"SES's copy of the message {payload['email_id']} could not be read: {code}.",
            transient=code not in ("AccessDenied", "403"),
        ) from None
    except (BotoCoreError, OSError) as error:
        raise FetchError(
            f"SES's copy of the message {payload['email_id']} could not be read: "
            f"{type(error).__name__}.",
            transient=True,
        ) from None
    proven = payload.get("dmarc") == "PASS" or (
        payload.get("dmarc") == "GRAY" and payload.get("dkim") == "PASS" and aligned_dkim(raw)
    )
    return Received(f"{SES}:{payload['email_id']}", raw, payload["recipients"], proven)


def aligned_dkim(raw: bytes) -> bool:
    """Whether the message carries DKIM signatures and every one is of the From domain or a
    domain it is under or over (relaxed alignment): then a signature SES passed is one."""
    if len(raw) > MAX_MESSAGE_BYTES:
        return False
    headers = BytesParser(policy=policy.default).parse(BytesIO(raw), headersonly=True)
    sender = parse_sender(headers)
    signatures = headers.get_all("DKIM-Signature") or []
    if sender is None or not signatures:
        return False
    domain = sender.rpartition("@")[2]
    for signature in signatures:
        tags = dict(part.strip().split("=", 1) for part in str(signature).split(";") if "=" in part)
        signer = "".join(tags.get("d", "").split()).lower()
        if not signer or not (
            signer == domain or domain.endswith(f".{signer}") or signer.endswith(f".{domain}")
        ):
            return False
    return True


def s3_client(region: str) -> Any:
    """A client of the region's S3 with the credentials boto3 finds, the app role's on AWS."""
    return boto3.client("s3", region_name=region)
