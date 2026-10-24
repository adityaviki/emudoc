"""The providers' ways in for the mail sent to projects' addresses (app/receiving.py): Resend's
`email.received` at the webhook, which queues a job that fetches the message from Resend's API
and takes it in as its projects' documents, asked again later while Resend is out of reach; and
SES's notices on the queue, whose jobs read each message from SES's bucket."""

import io
import json
import threading
from datetime import UTC, datetime

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import receiving
from app.config import get_settings
from app.deadletters import named_jobs
from app.inbound import MAX_MESSAGE_BYTES, TOO_LARGE, UNAUTHENTICATED
from app.jobs import RECEIVE_MAIL
from app.models import Document, InboundMessage, Job
from tests.test_api import create_project
from tests.test_automations import next_job
from tests.test_bounces import FakeSQS, a_secret, post_event
from tests.test_inbound import SUPPLIER, counting, mailbox, message
from tests.test_inbound import email_in as email_in  # noqa: F401 (the module's autouse fixture)

EMAIL_ID = "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"
RAW_URL = "https://inbound.resend.example/raw/4ef9a417?Signature=abc"


@pytest.fixture
def resend(monkeypatch):
    """Resend as the app sees it: the webhook's secret, a key, and its API answering what
    `received` gave it (an email by its id, and the message's download)."""
    secret = a_secret()
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", secret)
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    fake = FakeResend(secret)
    monkeypatch.setattr(receiving, "http_client", lambda: httpx.Client(transport=fake.transport))
    return fake


class FakeResend:
    def __init__(self, secret: str):
        self.secret = secret
        self.emails: dict[str, dict] = {}
        self.raw: dict[str, bytes] = {}
        # What it answers instead, by path, while set: a status code or an exception.
        self.failing: dict[str, int | Exception] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._answer)

    def received(self, raw: bytes, to: list[str], dmarc="pass", dkim="pass", email_id=EMAIL_ID):
        self.emails[email_id] = {
            "object": "email",
            "id": email_id,
            "to": to,
            "from": SUPPLIER,
            "cc": [],
            "bcc": [],
            "received_for": [],
            "subject": "Invoice INV-7 for July",
            "authentication": {"spf": "pass", "dkim": dkim, "dmarc": dmarc},
            "raw": {"download_url": RAW_URL, "expires_at": "2026-10-24T13:00:00Z"},
        }
        self.raw[RAW_URL] = raw

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        failing = self.failing.get(request.url.path)
        if isinstance(failing, Exception):
            raise failing
        if failing:
            return httpx.Response(failing, json={"name": "application_error"})
        if request.url.host == "api.resend.com":
            assert request.headers["authorization"] == "Bearer re_test"
            email_id = request.url.path.rsplit("/", 1)[1]
            if email_id not in self.emails:
                return httpx.Response(404, json={"name": "not_found"})
            return httpx.Response(200, json=self.emails[email_id])
        return httpx.Response(200, content=self.raw[str(request.url)])

    def event(self, to: list[str], email_id=EMAIL_ID) -> dict:
        return {
            "type": "email.received",
            "created_at": "2026-10-24T12:00:00.000Z",
            "data": {
                "email_id": email_id,
                "from": SUPPLIER,
                "to": to,
                "cc": [],
                "bcc": [],
                "received_for": [],
                "subject": "Invoice INV-7 for July",
                "attachments": [],
            },
        }


def jobs(session) -> list[Job]:
    session.expire_all()
    return list(session.scalars(select(Job).where(Job.kind == RECEIVE_MAIL).order_by(Job.id)))


def test_resend_s_message_is_fetched_and_taken_in(client, session, services, resend):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    resend.received(message(address), [f"Projects <{address}>"])
    assert post_event(client, resend.event([address]), resend.secret).status_code == 204
    [job] = jobs(session)
    assert (job.org_id, job.payload["source"], job.payload["email_id"]) == (
        None,
        "resend",
        EMAIL_ID,
    )

    assert next_job(services, RECEIVE_MAIL) is not None
    [record] = session.scalars(select(InboundMessage)).all()
    assert (record.delivery, record.outcome, record.kept) == (f"resend:{EMAIL_ID}", "kept", 1)
    assert [d.filename for d in session.scalars(select(Document))] == ["Invoice INV-7 for July.txt"]
    assert [job.status for job in jobs(session)] == ["done"]

    # Resend delivering the event again takes nothing twice.
    assert (
        post_event(client, resend.event([address]), resend.secret, msg_id="msg_2").status_code
        == 204
    )
    next_job(services, RECEIVE_MAIL)
    assert len(session.scalars(select(InboundMessage)).all()) == 1


def test_an_event_for_no_project_s_address_queues_nothing(client, session, resend):
    unknown = counting("unknown_address")
    assert (
        post_event(client, resend.event(["ap@elsewhere.example"]), resend.secret).status_code == 204
    )
    assert unknown() == 1
    assert jobs(session) == []
    # Nor one Resend did not sign.
    body = json.dumps(resend.event(["x@in.emudoc.test"])).encode()
    assert TestClient(client.app).post("/api/mail/events", content=body).status_code == 400
    assert jobs(session) == []


def test_a_sender_resend_could_not_prove_is_turned_away(client, session, services, resend):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    # DKIM gray is a signature of another domain, or none.
    resend.received(message(address), [address], dmarc="fail", dkim="gray")
    post_event(client, resend.event([address]), resend.secret)
    next_job(services, RECEIVE_MAIL)
    [record] = session.scalars(select(InboundMessage)).all()
    assert (record.outcome, record.reason) == ("refused", UNAUTHENTICATED)

    # DKIM of the From domain is enough where DMARC is not set up (gray).
    resend.received(message(address, subject="Second"), [address], dmarc="gray", email_id="b")
    post_event(client, resend.event([address], email_id="b"), resend.secret, msg_id="msg_3")
    next_job(services, RECEIVE_MAIL)
    session.expire_all()
    assert (
        session.scalar(select(InboundMessage.outcome).where(InboundMessage.delivery == "resend:b"))
        == "kept"
    )


def test_resend_out_of_reach_is_asked_again_later_and_a_message_it_lost_is_given_up(
    client, session, services, resend
):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    resend.received(message(address), [address])
    post_event(client, resend.event([address]), resend.secret)
    resend.failing[f"/emails/receiving/{EMAIL_ID}"] = 503
    next_job(services, RECEIVE_MAIL)
    first, again = jobs(session)
    assert first.status == "done"
    assert again.status == "queued" and again.payload["try"] == 2
    assert again.run_at > datetime.now(UTC)

    resend.failing.clear()
    resend.emails.clear()
    session.execute(
        Job.__table__.update().where(Job.id == again.id).values(run_at=datetime.now(UTC))
    )
    session.commit()
    next_job(services, RECEIVE_MAIL)
    session.refresh(again)
    assert again.status == "failed"
    assert again.last_error == (
        f"Resend holds no message {EMAIL_ID}. It was not taken in after 2 tries."
    )
    # The operator's dead letters name it by Resend's id, by which its dashboard finds it.
    [given_up] = named_jobs(session, Job.id == again.id)
    assert given_up.document == f"the message {EMAIL_ID} from resend"


def test_a_message_past_the_largest_is_turned_away_unread(client, session, services, resend):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    resend.received(b"x" * (MAX_MESSAGE_BYTES + 10), [address])
    post_event(client, resend.event([address]), resend.secret)
    next_job(services, RECEIVE_MAIL)
    [record] = session.scalars(select(InboundMessage)).all()
    assert (record.outcome, record.reason) == ("refused", TOO_LARGE)


QUEUE = "https://sqs.eu-central-1.amazonaws.com/123456789012/emudoc-staging-inbound"
BUCKET = "emudoc-staging-inbound-123456789012"


def ses_received(
    key: str, recipients: list[str], dmarc="PASS", dkim="PASS", message_id="o3vrnil0e2ic28tr"
) -> dict:
    """SES's notice of a message its rule's S3 action stored, as SNS's raw delivery leaves it."""
    return {
        "notificationType": "Received",
        "mail": {
            "timestamp": "2026-10-24T12:00:00.000Z",
            "source": SUPPLIER,
            "messageId": message_id,
            "destination": recipients,
            "commonHeaders": {"from": [f"Lena Vogt <{SUPPLIER}>"], "subject": "Invoice INV-7"},
        },
        "receipt": {
            "recipients": recipients,
            "spfVerdict": {"status": "PASS"},
            "dkimVerdict": {"status": dkim},
            "dmarcVerdict": {"status": dmarc},
            "action": {
                "type": "S3",
                "topicArn": "arn:aws:sns:eu-central-1:123456789012:emudoc-staging-inbound",
                "bucketName": BUCKET,
                "objectKeyPrefix": "inbound/",
                "objectKey": key,
            },
        },
    }


class FakeInboundBucket:
    """S3 reduced to what the job asks of SES's bucket: an object read and deleted."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.read: list[str] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        assert Bucket == BUCKET
        self.read.append(Key)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def ses(monkeypatch) -> FakeInboundBucket:
    """SES as the app sees it: INBOUND_MAIL_QUEUE set and its bucket a fake."""
    monkeypatch.setattr(get_settings(), "inbound_mail_queue", QUEUE)
    bucket = FakeInboundBucket()
    monkeypatch.setattr(receiving, "s3_client", lambda region: bucket)
    return bucket


def signed(raw: bytes, *domains: str) -> bytes:
    """The message with a DKIM-Signature of each domain above it, as a signing server adds one."""
    head = b"".join(
        f"DKIM-Signature: v=1; a=rsa-sha256; d={domain}; s=s1; h=from:to; bh=x; b=y\r\n".encode()
        for domain in domains
    )
    return head + raw


def test_ses_s_notices_queue_jobs_that_take_the_messages_in(client, session, services, ses):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    ses.objects["inbound/one"] = message(address)
    queue = FakeSQS([ses_received("inbound/one", [address]), {"Type": "Notification"}])
    assert receiving.take_inbound(queue, QUEUE, wait=0) == 1
    # Each notice deleted once its job is committed; the one that is none dropped too.
    assert queue.messages == []
    [job] = jobs(session)
    assert job.payload["source"] == "ses"
    assert (job.payload["region"], job.payload["bucket"], job.payload["key"]) == (
        "eu-central-1",
        BUCKET,
        "inbound/one",
    )

    next_job(services, RECEIVE_MAIL)
    [record] = session.scalars(select(InboundMessage)).all()
    assert (record.delivery, record.outcome, record.kept) == ("ses:o3vrnil0e2ic28tr", "kept", 1)
    # Taken in, the bucket's copy goes.
    assert ses.objects == {}

    # SQS delivering the notice again takes nothing twice, and reads nothing.
    ses.read.clear()
    receiving.take_inbound(FakeSQS([ses_received("inbound/one", [address])]), QUEUE, wait=0)
    next_job(services, RECEIVE_MAIL)
    assert len(session.scalars(select(InboundMessage)).all()) == 1
    assert ses.read == []


@pytest.mark.parametrize(
    ("dmarc", "dkim", "signers", "proven"),
    [
        ("PASS", "FAIL", (), True),
        ("FAIL", "PASS", ("northwind.example",), False),
        # A domain with no DMARC policy is proven by DKIM of its own, or of a domain it is under.
        ("GRAY", "PASS", ("northwind.example",), True),
        ("GRAY", "PASS", ("mail.northwind.example", "northwind.example"), True),
        # One signature of another domain could be the one SES passed.
        ("GRAY", "PASS", ("northwind.example", "bulkmailer.example"), False),
        ("GRAY", "PASS", (), False),
        ("GRAY", "FAIL", ("northwind.example",), False),
    ],
)
def test_ses_s_verdicts_prove_a_sender(
    client, session, services, ses, dmarc, dkim, signers, proven
):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    ses.objects["inbound/one"] = signed(message(address), *signers)
    receiving.take_inbound(
        FakeSQS([ses_received("inbound/one", [address], dmarc=dmarc, dkim=dkim)]), QUEUE, wait=0
    )
    next_job(services, RECEIVE_MAIL)
    [record] = session.scalars(select(InboundMessage)).all()
    assert (record.outcome == "kept") is proven
    if not proven:
        assert record.reason == UNAUTHENTICATED


def test_a_copy_gone_from_the_bucket_is_given_up(client, session, services, ses):
    project = create_project(client)
    address = mailbox(client, project["id"], [SUPPLIER])["address"]
    receiving.take_inbound(FakeSQS([ses_received("inbound/gone", [address])]), QUEUE, wait=0)
    next_job(services, RECEIVE_MAIL)
    [job] = jobs(session)
    assert (job.status, job.last_error) == (
        "failed",
        "SES's copy of the message o3vrnil0e2ic28tr is gone. It was not taken in.",
    )


def test_the_worker_waits_on_the_queue_until_it_stops(ses):
    stopping = threading.Event()
    queue = FakeSQS([])
    waited = []

    def receive_message(QueueUrl: str, MaxNumberOfMessages: int, WaitTimeSeconds: int) -> dict:
        waited.append(WaitTimeSeconds)
        stopping.set()
        return {}

    queue.receive_message = receive_message
    receiving.read_inbound(QUEUE, stopping, client=lambda url: queue)
    assert waited == [receiving.INBOUND_WAIT]
