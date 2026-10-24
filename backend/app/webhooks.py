"""Webhooks: the addresses an organisation's programs hear of its work at, posted to as it happens
rather than asked again and again.

An organisation's owners and admins add a webhook on its page (app/api/webhooks.py): an address
and the events it is told of, of WEBHOOK_EVENTS. A document's indexing that ends, ready or not,
is `document.ready` or `document.failed`, once its document is settled, its index job ended and
none queued after it (a retry waiting is not settled); a run of cross-checks worked out, its
report written or failed when one was asked for, is `check_run.completed`. Each is announced
from the end of the job that brought it about (`Handler.after` in app/worker.py), once the job's
row says it ended, and a job's end looked at twice announces nothing twice: a delivery is unique
by its hook and that job. A document indexed again, or a run whose report is asked for after,
settles again and is announced again, under a new event.

An event's body is the versioned API's own (app/api/v1/): `{id, type, created_at, data}`, `data`
what `GET /api/v1/…` answers for the document or the run, so that a program reads one model
however it heard, and the contract lists the events beside the routes (OpenAPI's `webhooks`).
The body is made once, as the event is announced, and every try sends those bytes, so that the
event's `id` names one body however often it is sent.

Each try is signed with the hook's secret, as Stripe signs its own (app/payments.py checks
those): `Emudoc-Signature: t=<seconds>,v1=<hex>`, the HMAC-SHA256 of the try's timestamp, a full
stop and the body, so that a receiver checks the body came from Emudoc and refuses an old one
sent again. The secret must be read to sign with, so it is encrypted for its row with
ENCRYPTION_KEYS (WEBHOOK_SECRETS in app/encrypted.py); it is shown once, as the hook is made or
rolled.

A delivery is a job (`deliver_webhook`, with the indexers), which posts the body and is done:
answered with a 2xx, delivered; anything else, or no answer within TIMEOUT, tried again after a
wait that doubles, MAX_TRIES in about a day and a half, each try a job of its own that carries
the body until it is done with it (`_spent`). A program that was away hears of what it missed.
A try is sent again after a lost lease, and an answer that never arrived is a failure, so a
receiver drops an event whose `id` it has seen.

An address is a member's, so a delivery refuses what a member must not reach through Emudoc:
the deployment's own network, by every address the name resolves to (`reaches_out` in
app/network.py), and the request goes to the address checked rather than to the name, or the
name could resolve elsewhere between the check and the connection; plain HTTP, in production;
a redirect, which is followed nowhere. Development, whose receivers run beside it, takes both.
None of it goes through a proxy the environment names.

The delivery log (`webhook_deliveries`) keeps each event a hook was sent, how its last try was
answered and when the next is due, never what it said; the housekeeper removes an entry
DELIVERIES_KEPT after it was made. What the address receives is the organisation's choice of a
company, not one of Emudoc's subprocessors.
"""

import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel
from sqlalchemy import Connection, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.encrypted import WEBHOOK_SECRETS, decrypt
from app.entitlements import check_api
from app.jobs import DELIVER_WEBHOOK, INDEX_DOCUMENT, LIVE, Lease, LeaseLost, enqueue, renew
from app.keyring import Undecryptable
from app.metrics import WEBHOOK_DELIVERIES
from app.models import (
    WEBHOOK_EVENTS,
    Document,
    Job,
    Organization,
    Project,
    Webhook,
    WebhookDelivery,
    WorkflowRun,
)
from app.network import on_own_network, reaches_out
from app.version import VERSION

if TYPE_CHECKING:
    # For the handler's signature alone.
    from app.indexing.pipeline import Services

log = logging.getLogger(__name__)

DOCUMENT_READY, DOCUMENT_FAILED, CHECK_RUN_COMPLETED = WEBHOOK_EVENTS
PREFIX = "whsec_"
# The most webhooks one organisation holds, so that one event is not posted a hundred times.
MAX_WEBHOOKS = 10
# How long a try waits for the address to connect, take the body and answer.
TIMEOUT = 10.0
# How many times an event is tried, and the first wait; each wait is twice the one before, so
# the last try is about a day and a half after the first.
MAX_TRIES = 12
FIRST_WAIT = timedelta(minutes=1)
# How long the delivery log keeps an entry.
DELIVERIES_KEPT = timedelta(days=30)
# How far from a receiver's clock a signature's time may be before it refuses the event, as the
# contract and the help advise: Stripe's figure.
TOLERANCE = 300
SIGNATURE = "Emudoc-Signature"
EVENT = "Emudoc-Event"
USER_AGENT = f"Emudoc-Webhooks/1 ({VERSION})"
# A document's statuses its indexing settles at, and the event each is.
SETTLED = {"ready": DOCUMENT_READY, "failed": DOCUMENT_FAILED, "quarantined": DOCUMENT_FAILED}

NO_SCHEME = "Give the webhook an address that starts with https://."
NO_HOST = "Give the webhook a whole address, with the name of the server that takes the events."
NO_CREDENTIALS = (
    "Leave the user name and password out of the webhook's address: the signature on every "
    "event proves it is Emudoc's."
)
PLAIN_HTTP = (
    "The address is plain HTTP, which would send the events unencrypted: give the webhook its "
    "https:// address."
)
OWN_NETWORK = (
    "The address leads into the network Emudoc runs in, which a webhook may not reach: give it "
    "an address on the internet."
)
NO_SUCH_NAME = "The address's name could not be found. It is tried again later."
UNREACHABLE = "The address could not be reached. It is tried again later."
UNVERIFIED = (
    "The address's certificate could not be verified, so nothing was sent. It is tried again later."
)
TIMED_OUT = f"The address did not answer within {TIMEOUT:g} seconds. It is tried again later."
REDIRECTED = (
    "The address answered with a redirect, which Emudoc does not follow: give the webhook the "
    "address it leads to."
)
NOT_TAKEN = "The address answered {status}, not 2xx. It is tried again later."
GAVE_UP = " That was the last try."
PAUSED = "The webhook was paused, so the event was not sent."
NO_API = "The organisation's plan no longer includes the API, so the event was not sent."
UNREADABLE = (
    "Emudoc could not read the webhook's secret to sign the event with, so it was not sent: "
    "roll its secret on the organisation's page."
)

_SPENT = text("UPDATE jobs SET payload = payload - 'body' WHERE id = :id")
_PRUNE = text("DELETE FROM webhook_deliveries WHERE created_at < :before")
# A document's index job still to run, after the one that ended: it is not settled until then.
_PENDING_INDEX = text(
    "SELECT EXISTS (SELECT 1 FROM jobs WHERE kind = :kind AND status IN ('queued', 'running') "
    "AND (payload ->> 'document_id')::bigint = :document AND id <> :job)"
)


def new_secret() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def sign(body: bytes, secret: str, timestamp: int) -> str:
    """The `Emudoc-Signature` header of a try made at `timestamp`, in seconds."""
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={signature.hexdigest()}"


def production() -> bool:
    return get_settings().environment == "production"


def url_problem(url: str) -> str | None:
    """What keeps `url` from being a webhook's address, as the step that fixes it; None when it
    may be one. The name is resolved at each try, where the addresses it leads to are held to
    the same line."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return NO_HOST
    if parsed.scheme not in ("http", "https"):
        return NO_SCHEME
    if not parsed.host:
        return NO_HOST
    if parsed.userinfo:
        return NO_CREDENTIALS
    if production():
        if parsed.scheme != "https":
            return PLAIN_HTTP
        if _own(parsed.raw_host.decode("ascii")):
            return OWN_NETWORK
    return None


def host_of(url: str) -> str:
    """The address's server, which the audit log names in place of the address: its path may
    hold the receiver's own token."""
    try:
        return httpx.URL(url).host
    except httpx.InvalidURL:
        return ""


def _own(host: str) -> bool:
    try:
        return not reaches_out(ipaddress.ip_address(host))
    except ValueError:
        return on_own_network(host)


def announce_document(document_id: int, job_id: int) -> int:
    """Announce the document's settling to the organisation's webhooks that listen for it, once
    the index job `job_id` ended; returns to how many. A document still queued, or with another
    index job to run, in the trash or in a project in the trash, announces nothing."""
    # Here rather than at the top: the API's modules import the worker's (app/legal), which
    # import this one.
    from app.api.v1.library import document_of
    from app.api.v1.schemas import DocumentEvent

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None or document.deleted_at is not None:
            return 0
        event = SETTLED.get(document.status)
        project = session.get(Project, document.project_id)
        if event is None or project.deleted_at is not None:
            return 0
        pending = session.scalar(
            _PENDING_INDEX, {"kind": INDEX_DOCUMENT, "document": document_id, "job": job_id}
        )
        if pending:
            return 0
        told = _announce(
            session,
            project.organization,
            event,
            document_id,
            job_id,
            lambda event_id, created_at: DocumentEvent(
                id=event_id, type=event, created_at=created_at, data=document_of(document)
            ),
        )
        session.commit()
        return told


def announce_run(run_id: int, job_id: int) -> int:
    """Announce a run of cross-checks worked out to the organisation's webhooks that listen for
    it, once the job `job_id` that worked it out or wrote its report ended; returns to how many.
    A run still under way, or whose report is, or in a project in the trash, announces nothing."""
    from app.api.v1.runs import run_of
    from app.api.v1.schemas import CheckRunEvent

    with SessionLocal() as session:
        run = session.get(WorkflowRun, run_id)
        if (
            run is None
            or run.status not in ("done", "failed")
            or run.report_status in ("queued", "writing")
        ):
            return 0
        project = session.get(Project, run.project_id)
        if project.deleted_at is not None:
            return 0
        told = _announce(
            session,
            project.organization,
            CHECK_RUN_COMPLETED,
            run_id,
            job_id,
            lambda event_id, created_at: CheckRunEvent(
                id=event_id, type=CHECK_RUN_COMPLETED, created_at=created_at, data=run_of(run)
            ),
        )
        session.commit()
        return told


def _announce(
    session: Session,
    organization: Organization,
    event: str,
    subject_id: int,
    job_id: int,
    made: Callable[[str, datetime], BaseModel],
) -> int:
    """Add a delivery of the event to each of the organisation's hooks that listen for it, and the
    job of its first try, in the caller's session; the body is made only when one listens."""
    hooks = session.scalars(
        select(Webhook)
        .where(
            Webhook.org_id == organization.id,
            Webhook.enabled.is_(True),
            Webhook.events.contains([event]),
        )
        .order_by(Webhook.id)
    ).all()
    if not hooks or check_api(organization) is not None:
        return 0
    event_id = f"evt_{uuid.uuid4().hex}"
    created_at = datetime.now(UTC)
    body = made(event_id, created_at).model_dump_json()
    told = 0
    for hook in hooks:
        delivery_id = session.scalar(
            insert(WebhookDelivery)
            .values(
                webhook_id=hook.id,
                event_id=event_id,
                event_type=event,
                subject_id=subject_id,
                job_id=job_id,
                created_at=created_at,
            )
            .on_conflict_do_nothing(index_elements=["webhook_id", "job_id"])
            .returning(WebhookDelivery.id)
        )
        if delivery_id is None:
            # Announced already, by an earlier look at the same job's end.
            continue
        _queue(session, organization.id, delivery_id, event, body, 0, None)
        told += 1
    return told


def _queue(
    session: Session,
    org_id: int,
    delivery_id: int,
    event: str,
    body: str,
    tries: int,
    run_at: datetime | None,
) -> Job:
    return enqueue(
        session,
        DELIVER_WEBHOOK,
        org_id=org_id,
        payload={"delivery_id": delivery_id, "event": event, "body": body, "try": tries + 1},
        priority=LIVE,
        run_at=run_at,
    )


@dataclass(frozen=True)
class Answer:
    """How a try ended: the address's status when it answered, and else, or for a status that
    is no delivery, the sentence why; `final` when trying again would change nothing."""

    status: int | None = None
    error: str | None = None
    final: bool = False

    @property
    def delivered(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


def run_delivery(job: Job, services: "Services") -> str | None:
    """The worker's handler of `deliver_webhook`."""
    deliver(job, Lease(job.id, job.worker_id))
    return None


def deliver(job: Job, lease: Lease | None = None) -> None:
    """Post the job's event to its hook, and keep how the address answered in the log: delivered,
    tried again later, or given up. Nothing is posted for a hook removed, paused, of a plan
    without the API or whose secret no key decrypts."""
    body = job.payload.get("body")
    if body is None:
        # Done with by a run that lost its lease after it was.
        return
    tries = job.payload.get("try", 1)
    with SessionLocal() as session:
        found = session.execute(
            select(WebhookDelivery, Webhook)
            .join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
            .where(WebhookDelivery.id == job.payload["delivery_id"])
        ).first()
        if found is None or found[0].status != "pending":
            # The hook was removed, or the delivery settled by an earlier run of this try.
            _spent(job.id)
            return
        delivery, hook = found
        delivery_id, url, refusal, secret = delivery.id, hook.url, None, ""
        if not hook.enabled:
            refusal = PAUSED
        elif check_api(session.get(Organization, hook.org_id)) is not None:
            refusal = NO_API
        else:
            try:
                secret = decrypt(WEBHOOK_SECRETS, hook.id, hook.secret)
            except Undecryptable as error:
                log.warning(
                    "The secret of webhook %s could not be read for delivery %s: %s",
                    hook.id,
                    delivery_id,
                    error,
                )
                refusal = UNREADABLE
        # Nothing is held while the address answers.
        session.rollback()
    if refusal is not None:
        answer = Answer(error=refusal, final=True)
        WEBHOOK_DELIVERIES.labels("refused").inc()
    else:
        answer = post(url, body.encode(), secret, job.payload["event"])
    _settle(job, delivery_id, tries, answer, lease)


def post(url: str, body: bytes, secret: str, event: str) -> Answer:
    """Try the event once at `url`, at the address its name resolves to and has been checked."""
    parsed = httpx.URL(url)
    # The name as the resolver and the certificate know it, an international one in its ASCII.
    host = parsed.raw_host.decode("ascii")
    if production() and parsed.scheme != "https":
        WEBHOOK_DELIVERIES.labels("refused").inc()
        return Answer(error=PLAIN_HTTP, final=True)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        found = addresses_of(host, port)
    except (OSError, UnicodeError):
        return Answer(error=NO_SUCH_NAME)
    if not found:
        return Answer(error=NO_SUCH_NAME)
    if production() and not all(reaches_out(address) for address in found):
        WEBHOOK_DELIVERIES.labels("refused").inc()
        return Answer(error=OWN_NETWORK, final=True)
    named = not _literal(host)
    request = http_client().build_request(
        "POST",
        parsed.copy_with(host=str(found[0])),
        content=body,
        headers={
            # The name the address was given by, for the server and its certificate.
            "Host": parsed.netloc.decode("ascii"),
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            EVENT: event,
            SIGNATURE: sign(body, secret, int(time.time())),
        },
        extensions={"sni_hostname": host} if named and parsed.scheme == "https" else {},
    )
    try:
        # The answer's body is never read: its status is the whole answer.
        response = http_client().send(request, stream=True)
        response.close()
    except httpx.TimeoutException:
        return Answer(error=TIMED_OUT)
    except httpx.HTTPError as error:
        return Answer(error=UNVERIFIED if _unverified(error) else UNREACHABLE)
    if 300 <= response.status_code < 400:
        return Answer(response.status_code, REDIRECTED)
    if not 200 <= response.status_code < 300:
        return Answer(response.status_code, NOT_TAKEN.format(status=response.status_code))
    return Answer(response.status_code)


def addresses_of(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """The addresses `host` resolves to, in the resolver's order; a test replaces it, since the
    suite makes no network calls."""
    if _literal(host):
        return [ipaddress.ip_address(host)]
    found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(ipaddress.ip_address(entry[4][0]) for entry in found))


@lru_cache
def http_client() -> httpx.Client:
    """The worker's client for the hooks: no redirect followed, no proxy from the environment,
    and no connection kept, since one made for a name must not carry another's request to the
    same address."""
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
    )


def _literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _unverified(error: BaseException) -> bool:
    """Whether the connection failed on the address's certificate, somewhere in what httpx
    wrapped."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, ssl.SSLCertVerificationError):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _settle(job: Job, delivery_id: int, tries: int, answer: Answer, lease: Lease | None) -> None:
    """Keep how the try ended, and queue the next one when it is to be tried again, committed
    under the lease with the body taken out of this try's job."""
    now = datetime.now(UTC)
    values = {"attempts": tries, "attempted_at": now, "response_status": answer.status}
    if answer.delivered:
        outcome = "delivered"
        values |= {"status": "delivered", "delivered_at": now, "retry_at": None, "error": None}
    elif answer.final or tries >= MAX_TRIES:
        outcome = "failed"
        error = answer.error if answer.final else _last(answer.error)
        values |= {"status": "failed", "retry_at": None, "error": error}
    else:
        outcome = "retried"
        values |= {"retry_at": now + FIRST_WAIT * 2 ** (tries - 1), "error": answer.error}
    with SessionLocal() as session:
        written = session.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id, WebhookDelivery.status == "pending")
            .values(**values)
        ).rowcount
        if written and outcome == "retried":
            _queue(
                session,
                job.org_id,
                delivery_id,
                job.payload["event"],
                job.payload["body"],
                tries,
                values["retry_at"],
            )
        session.execute(_SPENT, {"id": job.id})
        if lease is not None and not renew(session, lease):
            session.rollback()
            raise LeaseLost(lease)
        session.commit()
    if not answer.final:
        WEBHOOK_DELIVERIES.labels(outcome).inc()
    log.info(
        "Webhook delivery %s, try %s of %s: %s",
        delivery_id,
        tries,
        MAX_TRIES,
        outcome if answer.status is None else f"{outcome}, answered {answer.status}",
    )


def _last(error: str | None) -> str:
    """The last try's sentence: its promise of another taken back."""
    return (error or "").replace(" It is tried again later.", "") + GAVE_UP


def _spent(job_id: int) -> None:
    with SessionLocal() as session:
        session.execute(_SPENT, {"id": job_id})
        session.commit()


def abandon_delivery(job: Job, services: "Services") -> None:
    """A try whose every run was lost to a worker that died: the delivery ends failed, its log
    saying so, and the job keeps no body."""
    with SessionLocal() as session:
        session.execute(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.id == job.payload["delivery_id"],
                WebhookDelivery.status == "pending",
            )
            .values(
                status="failed",
                retry_at=None,
                attempted_at=func.now(),
                error="The try stopped unexpectedly several times and was given up.",
            )
        )
        session.execute(_SPENT, {"id": job.id})
        session.commit()


def prune_deliveries(connection: Connection, age: timedelta = DELIVERIES_KEPT) -> int:
    """Remove the log's entries made longer ago than `age`; returns how many."""
    return connection.execute(_PRUNE, {"before": datetime.now(UTC) - age}).rowcount
