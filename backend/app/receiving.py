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
"""

import logging
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from functools import lru_cache
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.errors import AppError
from app.inbound import MAX_MESSAGE_BYTES, Received, receive, token_in
from app.jobs import RECEIVE_MAIL, Lease, enqueue
from app.mail import RESEND_API
from app.metrics import INBOUND_MESSAGES
from app.models import Job
from app.notices import app_address
from app.storage import Storage

log = logging.getLogger(__name__)

RECEIVED = "email.received"
RESEND = "resend"
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
    if payload.get("source") != RESEND:
        return f"A message of an unknown way in, {payload.get('source')!r}, cannot be fetched."
    try:
        received = fetch_resend(http or http_client(), settings.resend_api_key, payload["email_id"])
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
    return None


def abandon_receive(job: Job) -> None:
    """A message whose every fetch was lost to a worker that died: the provider still holds it,
    and the operator hands it over by hand."""
    log.warning(
        "The message %s was not taken in: its job was lost %s times. Save it from the provider "
        "and hand it over with `python -m app.maintenance receive-mail`.",
        job.payload.get("email_id"),
        job.max_attempts,
    )
