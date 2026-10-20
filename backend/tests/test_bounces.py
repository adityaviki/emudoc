"""What the mail provider says of the mail it could not deliver: Resend's events at the webhook,
signed as Svix signs, and SES's from the queue EventBridge fills, each keeping the addresses
that bounced for good or complained, by their hash, and nothing else."""

import base64
import json
import secrets
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import bounces
from app.bounces import (
    Undeliverable,
    address_hash,
    read_ses_events,
    ses_undeliverable,
    sign_resend,
    sqs_client,
    take_ses_events,
)
from app.config import get_settings
from app.maintenance import housekeeping
from app.models import MailSuppression
from tests.test_metrics import Counted


def a_secret() -> str:
    """A webhook secret as Resend makes one, made afresh, never a literal gitleaks would read."""
    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


@pytest.fixture
def secret(monkeypatch) -> str:
    made = a_secret()
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", made)
    return made


def resend_event(kind: str, to: list[str], bounce: str | None = None) -> dict:
    data: dict = {
        "email_id": "56761188-7520-42d8-8898-ff6fc54ce618",
        "from": "Emudoc <no-reply@mail.example.com>",
        "to": to,
        "subject": "Reset your password",
    }
    if bounce:
        data["bounce"] = {
            "message": "The mailbox does not exist.",
            "subType": "General",
            "type": bounce,
        }
    return {"type": kind, "created_at": "2026-10-20T12:00:00.000Z", "data": data}


def post_event(client, event: dict, secret: str, **signing) -> object:
    """Post `event` as Resend does: from a client without the app's header, signed."""
    body = json.dumps(event).encode()
    headers = sign_resend(
        body, secret, signing.pop("msg_id", "msg_2KWPBgLlAfxdpx2AI54pPJ85f4W"), **signing
    )
    return TestClient(client.app).post("/api/mail/events", content=body, headers=headers)


def spared(session) -> dict[str, str]:
    session.expire_all()
    return {row.address_hash: row.reason for row in session.scalars(select(MailSuppression))}


def test_resend_s_bounces_and_complaints_are_kept_by_their_hash(client, session, secret):
    counted = Counted(
        ("emudoc_mail_events_total", {"kind": "bounce"}),
        ("emudoc_mail_events_total", {"kind": "complaint"}),
    )
    bounced = post_event(
        client, resend_event("email.bounced", ["Ada@Example.com"], "Permanent"), secret
    )
    assert bounced.status_code == 204
    complained = post_event(client, resend_event("email.complained", ["bob@example.com"]), secret)
    assert complained.status_code == 204
    assert spared(session) == {
        address_hash("ada@example.com"): "bounce",
        address_hash("bob@example.com"): "complaint",
    }
    # The table names nobody to whoever reads it.
    assert "example.com" not in "".join(spared(session))
    assert counted("emudoc_mail_events_total", kind="bounce") == 1
    assert counted("emudoc_mail_events_total", kind="complaint") == 1


@pytest.mark.parametrize(
    "event",
    [
        resend_event("email.bounced", ["ada@example.com"], "Transient"),
        resend_event("email.delivered", ["ada@example.com"]),
        resend_event("email.delivery_delayed", ["ada@example.com"]),
        {"type": "domain.updated", "data": {"id": "d91cd9bd"}},
    ],
    ids=["transient bounce", "delivered", "delayed", "another kind"],
)
def test_every_other_event_is_taken_and_keeps_nothing(client, session, secret, event):
    assert post_event(client, event, secret).status_code == 204
    assert spared(session) == {}


def test_only_resend_s_signature_is_believed(client, session, secret, caplog):
    event = resend_event("email.bounced", ["ada@example.com"], "Permanent")
    body = json.dumps(event).encode()
    stranger = TestClient(client.app)

    unsigned = stranger.post("/api/mail/events", content=body)
    assert unsigned.status_code == 400
    assert unsigned.json() == {
        "detail": "That request does not carry Resend's signature for this deployment."
    }
    assert "it carried no signature" in caplog.text
    # Signed with another endpoint's secret, too old, or for another body.
    assert post_event(client, event, a_secret()).status_code == 400
    assert post_event(client, event, secret, stamp=int(time.time()) - 301).status_code == 400
    headers = sign_resend(body, secret, "msg_1")
    tampered = body.replace(b"ada@", b"eve@")
    assert stranger.post("/api/mail/events", content=tampered, headers=headers).status_code == 400
    # One of several signatures may hold, as when the secret is being rotated.
    headers["svix-signature"] = f"v1,bm90IGl0 {headers['svix-signature']}"
    assert stranger.post("/api/mail/events", content=body, headers=headers).status_code == 204
    assert spared(session) == {address_hash("ada@example.com"): "bounce"}


def test_the_webhook_is_off_without_its_secret_and_takes_no_large_body(client, monkeypatch):
    event = resend_event("email.bounced", ["ada@example.com"], "Permanent")
    off = post_event(client, event, a_secret())
    assert off.status_code == 404
    assert off.json() == {"detail": "This deployment takes no events from Resend."}
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", a_secret())
    large = TestClient(client.app).post("/api/mail/events", content=b"x" * 300_000)
    assert large.status_code == 413


def ses_bounce(kind: str, addresses: list[str]) -> dict:
    """An SES bounce as EventBridge delivers it, `Permanent` or `Transient`."""
    return {
        "version": "0",
        "id": "c5a0d2c8-0000-4b0b-9a8f-0123456789ab",
        "detail-type": "Email Bounced",
        "source": "aws.ses",
        "detail": {
            "eventType": "Bounce",
            "bounce": {
                "bounceType": kind,
                "bounceSubType": "General",
                "bouncedRecipients": [{"emailAddress": address} for address in addresses],
            },
            "mail": {"tags": {"ses:configuration-set": ["emudoc-production"]}},
        },
    }


def ses_complaint(addresses: list[str]) -> dict:
    return {
        "detail-type": "Email Complaint Received",
        "source": "aws.ses",
        "detail": {
            "eventType": "Complaint",
            "complaint": {"complainedRecipients": [{"emailAddress": a} for a in addresses]},
        },
    }


def test_ses_says_which_addresses_to_spare_however_it_is_delivered():
    assert ses_undeliverable(ses_bounce("Permanent", ["a@example.com", "b@example.com"])) == [
        Undeliverable("a@example.com", "bounce"),
        Undeliverable("b@example.com", "bounce"),
    ]
    assert ses_undeliverable(ses_bounce("Transient", ["a@example.com"])) == []
    assert ses_undeliverable(ses_complaint(["c@example.com"])) == [
        Undeliverable("c@example.com", "complaint")
    ]
    # Through SNS the event is a string in `Message`, and as SES publishes it, the event itself.
    sns = {"Type": "Notification", "Message": json.dumps(ses_bounce("Permanent", ["d@x.com"]))}
    assert ses_undeliverable(sns) == [Undeliverable("d@x.com", "bounce")]
    assert ses_undeliverable(ses_bounce("Permanent", ["e@x.com"])["detail"]) == [
        Undeliverable("e@x.com", "bounce")
    ]
    assert ses_undeliverable({"detail": {"eventType": "Delivery"}}) == []


class FakeSQS:
    """SQS's client reduced to what the reader calls: `messages` are taken BATCH at a time and
    stay until deleted."""

    def __init__(self, bodies: list[object]):
        self.messages = [
            {
                "MessageId": f"m{index}",
                "ReceiptHandle": f"r{index}",
                "Body": body if isinstance(body, str) else json.dumps(body),
            }
            for index, body in enumerate(bodies)
        ]
        self.received = 0

    def receive_message(self, QueueUrl: str, MaxNumberOfMessages: int) -> dict:
        self.received += 1
        return {"Messages": self.messages[:MaxNumberOfMessages]} if self.messages else {}

    def delete_message_batch(self, QueueUrl: str, Entries: list[dict]) -> dict:
        gone = {entry["ReceiptHandle"] for entry in Entries}
        self.messages = [m for m in self.messages if m["ReceiptHandle"] not in gone]
        return {"Successful": [{"Id": entry["Id"]} for entry in Entries]}


QUEUE = "https://sqs.eu-central-1.amazonaws.com/123456789012/emudoc-production-mail-events"


def test_the_worker_takes_ses_events_from_the_queue_until_it_is_empty(session, caplog):
    queue = FakeSQS(
        [ses_bounce("Permanent", [f"user{n}@example.com"]) for n in range(12)]
        + [ses_complaint(["bob@example.com"]), ses_bounce("Transient", ["ann@example.com"])]
        + ["not json", {"Message": "{"}]
    )
    assert take_ses_events(queue, QUEUE) == 13
    assert queue.messages == [] and queue.received == 3
    assert len(spared(session)) == 13
    assert spared(session)[address_hash("bob@example.com")] == "complaint"
    assert "A message of MAIL_EVENTS_QUEUE is not an event of SES's and is dropped: m14" in (
        caplog.text
    )
    assert take_ses_events(queue, QUEUE) == 0


def test_the_reader_keeps_reading_after_a_round_that_failed(session, monkeypatch, caplog):
    monkeypatch.setattr(bounces, "MAIL_EVENTS_SECONDS", 0.01)
    stopping = threading.Event()
    rounds: list[str] = []

    class Flaky(FakeSQS):
        def receive_message(self, QueueUrl: str, MaxNumberOfMessages: int) -> dict:
            rounds.append(QueueUrl)
            if len(rounds) == 1:
                raise ConnectionError("SQS is out of reach")
            if len(rounds) > 3:
                stopping.set()
            return super().receive_message(QueueUrl, MaxNumberOfMessages)

    queue = Flaky([ses_bounce("Permanent", ["ada@example.com"])])
    reader = threading.Thread(target=read_ses_events, args=(QUEUE, stopping, lambda url: queue))
    reader.start()
    reader.join(5)
    assert not reader.is_alive()
    assert "Reading SES's events from MAIL_EVENTS_QUEUE failed" in caplog.text
    assert spared(session) == {address_hash("ada@example.com"): "bounce"}


def test_the_queue_is_reached_at_its_own_address_whatever_the_bucket_s_is(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://fsn1.your-objectstorage.com")
    client = sqs_client(QUEUE)
    assert client.meta.region_name == "eu-central-1"
    assert client.meta.endpoint_url == "https://sqs.eu-central-1.amazonaws.com"
    local = sqs_client("http://127.0.0.1:9324/000000000000/mail-events")
    assert local.meta.endpoint_url == "http://127.0.0.1:9324"


def test_an_address_is_written_to_again_after_a_year(session, services):
    bounces.spare(session, [Undeliverable("ada@example.com", "bounce")])
    bounces.spare(session, [Undeliverable("bob@example.com", "bounce")])
    session.execute(
        text(
            "UPDATE mail_suppressions SET recorded_at = now() - interval '366 days' "
            "WHERE address_hash = :hash"
        ),
        {"hash": address_hash("ada@example.com")},
    )
    session.commit()
    housekeeping(services.storage)
    assert spared(session) == {address_hash("bob@example.com"): "bounce"}
