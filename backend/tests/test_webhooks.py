"""Webhooks (app/webhooks.py, app/api/webhooks.py): the addresses an organisation's programs hear
of its work at, each event signed, posted to the address its name was checked to resolve to, tried
again while it is not taken, and kept in a delivery log that never holds what it said."""

import hashlib
import hmac
import json
import ssl
import time
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

import httpx
import pytest
from sqlalchemy import func, select, text, update

from app import webhooks
from app.api.v1.library import document_of
from app.api.v1.runs import run_of
from app.config import get_settings
from app.encrypted import WEBHOOK_SECRETS, decrypt, encrypt_secrets
from app.jobs import (
    DELIVER_WEBHOOK,
    INDEX_DOCUMENT,
    RUN_WORKFLOW,
    Lease,
    LeaseLost,
    claim,
)
from app.keyring import key_of
from app.models import (
    Document,
    Job,
    Webhook,
    WebhookDelivery,
    WorkflowRun,
)
from app.webhooks import (
    GAVE_UP,
    NO_CREDENTIALS,
    NO_SCHEME,
    NOT_TAKEN,
    OWN_NETWORK,
    PAUSED,
    PLAIN_HTTP,
    REDIRECTED,
    SIGNATURE,
    TIMED_OUT,
    TOLERANCE,
    UNREACHABLE,
    UNREADABLE,
    UNVERIFIED,
    abandon_delivery,
    announce_document,
    prune_deliveries,
)
from app.worker import Worker
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_check_runs import TOTAL, invoices, made, run
from tests.test_encrypted import fingerprint, keyed, new_key  # noqa: F401 (a fixture)

# Names the receivers are reached by, and the addresses they resolve to: really public ones,
# since Python counts the documentation ranges as private (tests/test_network.py).
PUBLIC = "93.184.215.14"
PUBLIC_6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
NAMES = {
    "hooks.example.com": [PUBLIC],
    "six.example.com": [PUBLIC_6],
    "xn--bcher-kva.example": [PUBLIC],
    "inside.example.com": ["10.0.4.7"],
    "split.example.com": [PUBLIC, "127.0.0.1"],
    "localhost": ["127.0.0.1"],
}
HOOK = "https://hooks.example.com/emudoc?token=abc"
READY = ["document.ready"]


class Receiver:
    """The program's end of the webhooks: each request it is sent is kept and answered with the
    next of `answers`, a status or an exception raised in its place, 200 once they run out."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: list[int | Exception] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, text="Thanks!")

    def bodies(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


@pytest.fixture
def receiver(monkeypatch) -> Receiver:
    fake = Receiver()
    monkeypatch.setattr(
        webhooks, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    )

    def addresses_of(host: str, port: int):
        if host in NAMES:
            return [ip_address(address) for address in NAMES[host]]
        try:
            return [ip_address(host)]
        except ValueError:
            raise OSError(f"no such name: {host}") from None

    monkeypatch.setattr(webhooks, "addresses_of", addresses_of)
    return fake


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")


def add(client, url: str = HOOK, events: list[str] = READY) -> dict:
    response = client.post("/api/organization/webhooks", json={"url": url, "events": events})
    assert response.status_code == 201, response.text
    return response.json()


def hooks(client) -> list[dict]:
    response = client.get("/api/organization/webhooks")
    assert response.status_code == 200, response.text
    return response.json()


def log_of(client, hook_id: int, **params) -> dict:
    response = client.get(f"/api/organization/webhooks/{hook_id}/deliveries", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def run_due(services, kind: str) -> int:
    """Run the jobs of the kind that are due as the worker does, what follows each included."""
    worker = Worker(services, concurrency=1)
    ran = 0
    while (job := claim(worker.id, [kind])) is not None:
        worker._run(job)
        ran += 1
    return ran


def due_now(session) -> None:
    """The next tries of the deliveries are due now, rather than after their waits."""
    session.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    session.commit()


def uploaded(client, project_id: int, name: str = "note.txt") -> int:
    response = client.post(
        f"/api/projects/{project_id}/documents",
        files=[("files", (name, f"{name}: a delivery note for 12 pallets.".encode()))],
    )
    assert response.status_code == 201, response.text
    return response.json()["documents"][0]["id"]


def checks(body: bytes, header: str, secret: str) -> bool:
    """What a receiver checks, as the organisation page says it: `v1=` the HMAC-SHA256, in hex,
    of `t=` the time, a full stop and the body as sent, with a time near its own."""
    parts = dict(part.split("=", 1) for part in header.split(","))
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256)
    recent = abs(time.time() - int(parts["t"])) <= TOLERANCE
    return recent and hmac.compare_digest(expected.hexdigest(), parts["v1"])


def verified(request: httpx.Request, secret: str) -> bool:
    """Whether a request the receiver took carries Emudoc's signature of its body."""
    return checks(request.content, request.headers[SIGNATURE], secret)


def test_an_admin_adds_a_webhook_whose_secret_is_shown_once(client, session, account):
    made_now = add(client, events=["document.failed", "document.ready", "document.ready"])
    assert made_now["secret"].startswith("whsec_") and len(made_now["secret"]) == 49
    # The events each once, in the app's order.
    assert made_now["events"] == ["document.ready", "document.failed"]
    assert (made_now["enabled"], made_now["made_by"], made_now["last_delivery"]) == (
        True,
        "Owner",
        None,
    )
    listed = hooks(client)
    assert [hook["url"] for hook in listed] == [HOOK] and "secret" not in listed[0]
    # A question set's report was an event until question sets went (S.6): no longer one.
    gone = {"url": HOOK, "events": ["report.completed"]}
    assert client.post("/api/organization/webhooks", json=gone).status_code == 422

    # Kept encrypted for its row, and read back to sign with.
    row = session.get(Webhook, made_now["id"])
    assert row.secret.startswith("v1.") and made_now["secret"] not in row.secret
    assert decrypt(WEBHOOK_SECRETS, row.id, row.secret) == made_now["secret"]
    # The audit log names the server, never the address, whose query holds a token.
    assert (
        "webhook_created",
        {"host": "hooks.example.com", "events": ["document.ready", "document.failed"]},
    ) in actions(session, account.organization.id)


def test_a_document_indexed_is_posted_signed_to_the_address_checked(
    client, session, services, receiver
):
    hook = add(client)
    project = create_project(client)
    document_id = uploaded(client, project["id"])
    assert run_due(services, INDEX_DOCUMENT) == 1
    assert run_due(services, DELIVER_WEBHOOK) == 1

    [request] = receiver.requests
    # Sent to the address the name was checked to resolve to, by the name.
    assert request.url == httpx.URL(f"https://{PUBLIC}/emudoc?token=abc")
    assert request.headers["host"] == "hooks.example.com"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["emudoc-event"] == "document.ready"
    assert verified(request, hook["secret"])
    assert not verified(request, webhooks.new_secret())

    # The body is the versioned API's document, in its envelope.
    event = json.loads(request.content)
    assert event["id"].startswith("evt_") and event["type"] == "document.ready"
    document = session.get(Document, document_id, populate_existing=True)
    assert event["data"] == document_of(document).model_dump(mode="json")
    assert (event["data"]["status"], event["data"]["partly_indexed"]) == ("ready", False)

    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["event_id"], delivery["event_type"], delivery["subject_id"]) == (
        event["id"],
        "document.ready",
        document_id,
    )
    assert (delivery["status"], delivery["attempts"], delivery["response_status"]) == (
        "delivered",
        1,
        200,
    )
    assert delivery["error"] is None and delivery["delivered_at"] is not None
    assert hooks(client)[0]["last_delivery"]["id"] == delivery["id"]
    # The job keeps no body once it is done with it.
    job = session.scalar(select(Job).where(Job.kind == DELIVER_WEBHOOK))
    assert "body" not in job.payload


def test_a_document_that_could_not_be_read_or_was_quarantined_is_document_failed(
    client, session, services, receiver
):
    add(client, events=["document.failed"])
    project = create_project(client)
    ids = [uploaded(client, project["id"], f"scan-{number}.txt") for number in (1, 2)]
    # Indexed, they are ready, which this hook does not listen for.
    run_due(services, INDEX_DOCUMENT)
    assert session.scalar(select(func.count()).select_from(WebhookDelivery)) == 0
    for document_id, status in zip(ids, ("failed", "quarantined"), strict=True):
        session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=status, error="The file could not be read.")
        )
        session.commit()
        assert announce_document(document_id, 1000 + document_id) == 1
    run_due(services, DELIVER_WEBHOOK)
    assert [(body["type"], body["data"]["status"]) for body in receiver.bodies()] == [
        ("document.failed", "failed"),
        ("document.failed", "quarantined"),
    ]
    assert receiver.bodies()[0]["data"]["error"] == "The file could not be read."


def test_only_a_settled_document_is_announced_once_to_the_hooks_that_listen(
    client, session, services, receiver, make_account
):
    listening = add(client)
    add(client, "https://hooks.example.com/runs", ["check_run.completed"])
    paused = add(client, "https://hooks.example.com/paused")
    client.patch(f"/api/organization/webhooks/{paused['id']}", json={"enabled": False})
    elsewhere = make_account("eve@globex.com")
    session.add(Webhook(org_id=elsewhere.organization.id, url=HOOK, events=READY, secret="whsec_x"))
    session.commit()
    project = create_project(client)
    document_id = uploaded(client, project["id"])

    # Queued still, and then with its index job queued after another that ended: not settled.
    assert announce_document(document_id, 10_000) == 0
    session.execute(update(Document).where(Document.id == document_id).values(status="ready"))
    session.commit()
    assert announce_document(document_id, 10_000) == 0
    run_due(services, INDEX_DOCUMENT)

    # The end of one job looked at twice announces it once; a new job's end, again.
    job_id = session.scalar(select(Job.id).where(Job.kind == INDEX_DOCUMENT))
    assert announce_document(document_id, job_id) == 0
    assert announce_document(document_id, job_id + 1) == 1
    deliveries = session.scalars(select(WebhookDelivery)).all()
    assert [delivery.webhook_id for delivery in deliveries] == [listening["id"]] * 2
    assert len({delivery.event_id for delivery in deliveries}) == 2

    # Nothing of a document in the trash.
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert announce_document(document_id, job_id + 2) == 0


def test_an_event_not_taken_is_tried_again_with_the_same_body_until_it_is(
    client, session, services, receiver
):
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    receiver.answers = [500, httpx.ConnectTimeout("slow"), 302]
    started = datetime.now(UTC)
    run_due(services, DELIVER_WEBHOOK)

    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["status"], delivery["attempts"], delivery["response_status"]) == (
        "pending",
        1,
        500,
    )
    assert delivery["error"] == NOT_TAKEN.format(status=500)
    retry_at = datetime.fromisoformat(delivery["retry_at"])
    assert started + timedelta(seconds=55) < retry_at < started + timedelta(seconds=65)
    # The next try waits for its moment.
    assert run_due(services, DELIVER_WEBHOOK) == 0

    for error in (TIMED_OUT, REDIRECTED):
        due_now(session)
        run_due(services, DELIVER_WEBHOOK)
        assert log_of(client, hook["id"])["deliveries"][0]["error"] == error
    due_now(session)
    run_due(services, DELIVER_WEBHOOK)
    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["status"], delivery["attempts"], delivery["error"]) == ("delivered", 4, None)

    # Every try sent the same event, each signed at its own moment.
    assert len({request.content for request in receiver.requests}) == 1
    assert all(verified(request, hook["secret"]) for request in receiver.requests)
    tries = session.scalars(select(Job).where(Job.kind == DELIVER_WEBHOOK).order_by(Job.id)).all()
    assert [job.payload["try"] for job in tries] == [1, 2, 3, 4]
    assert all("body" not in job.payload for job in tries)


def test_an_event_is_given_up_after_its_last_try(client, session, services, receiver, monkeypatch):
    monkeypatch.setattr(webhooks, "MAX_TRIES", 2)
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    receiver.answers = [httpx.ConnectError("refused"), 503]
    run_due(services, DELIVER_WEBHOOK)
    assert log_of(client, hook["id"])["deliveries"][0]["error"] == UNREACHABLE
    due_now(session)
    run_due(services, DELIVER_WEBHOOK)
    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["status"], delivery["retry_at"]) == ("failed", None)
    assert delivery["error"] == "The address answered 503, not 2xx." + GAVE_UP
    # No try is queued after the last, and the worker gave nothing up to the operator.
    tries = select(func.count()).where(Job.kind == DELIVER_WEBHOOK, Job.status == "queued")
    assert session.scalar(tries) == 0
    assert session.scalar(select(func.count()).where(Job.status == "failed")) == 0


def test_a_certificate_that_does_not_verify_is_said_so(client, services, receiver):
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    failure = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    failure.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
    receiver.answers = [failure]
    run_due(services, DELIVER_WEBHOOK)
    assert log_of(client, hook["id"])["deliveries"][0]["error"] == UNVERIFIED


def test_names_are_sent_to_as_their_servers_know_them(client, session, services, receiver):
    add(client, "https://six.example.com:8443/in")
    add(client, "https://bücher.example/in")
    add(client, f"https://{PUBLIC}/in")
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    run_due(services, DELIVER_WEBHOOK)
    six, international, literal = receiver.requests
    assert six.url == httpx.URL(f"https://[{PUBLIC_6}]:8443/in")
    assert six.headers["host"] == "six.example.com:8443"
    assert international.headers["host"] == "xn--bcher-kva.example"
    assert international.extensions["sni_hostname"] == "xn--bcher-kva.example"
    # An address given as a number has no name to show the server.
    assert literal.headers["host"] == PUBLIC and "sni_hostname" not in literal.extensions


def test_production_keeps_webhooks_off_the_own_network_and_plain_http(
    client, session, services, receiver, production
):
    for url, problem in [
        ("http://hooks.example.com/in", PLAIN_HTTP),
        ("https://10.0.0.5/in", OWN_NETWORK),
        ("https://[::ffff:127.0.0.1]/in", OWN_NETWORK),
        ("https://worker:8000/in", OWN_NETWORK),
        ("https://metadata.internal/in", OWN_NETWORK),
        ("ftp://hooks.example.com/in", NO_SCHEME),
        ("https://ada:secret@hooks.example.com/in", NO_CREDENTIALS),
    ]:
        refused = client.post("/api/organization/webhooks", json={"url": url, "events": READY})
        assert (refused.status_code, refused.json()["detail"]) == (400, problem), url

    # A name that resolves into the network, wholly or in part, is sent nothing.
    inside = add(client, "https://inside.example.com/in")
    split = add(client, "https://split.example.com/in")
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    run_due(services, DELIVER_WEBHOOK)
    assert receiver.requests == []
    for hook in (inside, split):
        [delivery] = log_of(client, hook["id"])["deliveries"]
        assert (delivery["status"], delivery["attempts"], delivery["error"]) == (
            "failed",
            1,
            OWN_NETWORK,
        )


def test_development_sends_to_its_own_receivers(client, services, receiver):
    hook = add(client, "http://localhost:9000/in")
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    run_due(services, DELIVER_WEBHOOK)
    assert receiver.requests[0].url == httpx.URL("http://127.0.0.1:9000/in")
    assert log_of(client, hook["id"])["deliveries"][0]["status"] == "delivered"


def test_a_run_of_cross_checks_worked_out_is_announced(client, session, services, receiver):
    hook = add(client, events=["check_run.completed"])
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    started = run(client, project["id"])
    assert run_due(services, RUN_WORKFLOW) == 1
    run_due(services, DELIVER_WEBHOOK)
    [event] = receiver.bodies()
    assert event["type"] == "check_run.completed"
    worked_out = session.get(WorkflowRun, started["id"], populate_existing=True)
    assert event["data"] == run_of(worked_out).model_dump(mode="json")
    assert event["data"]["counts"] == {"differs": 1, "missing": 1, "holds": 0}

    # A report asked for after is announced again once it is written.
    services.llm.agent_turns.append(_report())
    asked = client.post(f"/api/check-runs/{started['id']}/report")
    assert asked.status_code == 200, asked.text
    run_due(services, RUN_WORKFLOW)
    run_due(services, DELIVER_WEBHOOK)
    later = receiver.bodies()[1]
    assert (later["data"]["report_status"], later["id"] != event["id"]) == ("done", True)
    assert [d["subject_id"] for d in log_of(client, hook["id"])["deliveries"]] == [
        started["id"]
    ] * 2


def _report():
    from pydantic_ai.messages import TextPart

    from tests.conftest import FakeTurn

    return FakeTurn(TextPart("INV-1 is 5 EUR over its order."))


def test_a_paused_or_removed_hook_is_sent_nothing(client, session, services, receiver, account):
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    # Paused after the event was announced: the try waiting sends nothing.
    paused = client.patch(f"/api/organization/webhooks/{hook['id']}", json={"enabled": False})
    assert paused.json()["enabled"] is False
    run_due(services, DELIVER_WEBHOOK)
    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["status"], delivery["error"]) == ("failed", PAUSED)

    # Removed, its log goes with it, and a try left waiting finds nothing to send.
    client.patch(f"/api/organization/webhooks/{hook['id']}", json={"enabled": True})
    uploaded(client, project["id"], "third.txt")
    run_due(services, INDEX_DOCUMENT)
    assert client.delete(f"/api/organization/webhooks/{hook['id']}").status_code == 204
    run_due(services, DELIVER_WEBHOOK)
    assert receiver.requests == []
    assert session.scalar(select(func.count()).select_from(WebhookDelivery)) == 0


def test_a_secret_rolled_signs_every_try_from_then_on(client, session, services, receiver, account):
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    rolled = client.post(f"/api/organization/webhooks/{hook['id']}/roll")
    assert rolled.status_code == 200, rolled.text
    secret = rolled.json()["secret"]
    assert secret != hook["secret"] and rolled.json()["rolled_at"] is not None
    run_due(services, DELIVER_WEBHOOK)
    [request] = receiver.requests
    assert verified(request, secret) and not verified(request, hook["secret"])
    assert ("webhook_rolled", {"host": "hooks.example.com"}) in actions(
        session, account.organization.id
    )


def test_a_secret_no_key_decrypts_sends_nothing(client, session, services, receiver):
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    session.execute(
        update(Webhook).where(Webhook.id == hook["id"]).values(secret="v1.0badc0de.AAAA")
    )
    session.commit()
    run_due(services, DELIVER_WEBHOOK)
    assert receiver.requests == []
    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["status"], delivery["error"]) == ("failed", UNREADABLE)


def test_changes_to_a_webhook_are_audited_by_what_changed(client, session, account):
    hook = add(client)
    path = f"/api/organization/webhooks/{hook['id']}"
    changed = client.patch(
        path,
        json={"url": "https://six.example.com/in", "events": ["check_run.completed"]},
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["url"], changed.json()["events"]) == (
        "https://six.example.com/in",
        ["check_run.completed"],
    )
    # Nothing changed, nothing audited; an address refused changes nothing.
    assert client.patch(path, json={"url": "https://six.example.com/in"}).status_code == 200
    assert client.patch(path, json={"url": "ftp://x.example"}).status_code == 400
    client.delete(path)
    logged = [
        (action, detail)
        for action, detail in actions(session, account.organization.id)
        if action.startswith("webhook_")
    ]
    assert logged[1:] == [
        (
            "webhook_changed",
            {
                "host": "hooks.example.com",
                "to_host": "six.example.com",
                "from": ["document.ready"],
                "to": ["check_run.completed"],
            },
        ),
        ("webhook_deleted", {"host": "six.example.com", "events": ["check_run.completed"]}),
    ]


def test_only_owners_and_admins_manage_webhooks(client, session, account, make_account, act_as):
    hook = add(client)
    act_as(make_account("max@example.com", account.organization, role="member"))
    assert client.get("/api/organization/webhooks").status_code == 403
    assert (
        client.post("/api/organization/webhooks", json={"url": HOOK, "events": READY})
        .json()["detail"]
        .startswith("Only the owners and admins")
    )
    act_as(make_account("ann@example.com", account.organization, role="admin"))
    assert [listed["id"] for listed in hooks(client)] == [hook["id"]]
    # Another organisation's webhook is not found.
    act_as(make_account("eve@globex.com"))
    assert client.get(f"/api/organization/webhooks/{hook['id']}/deliveries").status_code == 404


def test_an_organisation_holds_ten_webhooks(client):
    for number in range(webhooks.MAX_WEBHOOKS):
        add(client, f"https://hooks.example.com/{number}")
    refused = client.post("/api/organization/webhooks", json={"url": HOOK, "events": READY})
    assert refused.status_code == 409 and "as many as it may" in refused.json()["detail"]


def test_the_delivery_log_is_read_a_page_at_a_time_and_pruned(client, session, services, receiver):
    hook = add(client)
    project = create_project(client)
    ids = [uploaded(client, project["id"], f"note-{n}.txt") for n in range(3)]
    run_due(services, INDEX_DOCUMENT)
    first = log_of(client, hook["id"], limit=2)
    assert [d["subject_id"] for d in first["deliveries"]] == ids[:0:-1]
    rest = log_of(client, hook["id"], limit=2, before=first["next_before"])
    assert ([d["subject_id"] for d in rest["deliveries"]], rest["next_before"]) == ([ids[0]], None)

    session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.subject_id == ids[0])
        .values(created_at=datetime.now(UTC) - timedelta(days=31))
    )
    session.commit()
    assert prune_deliveries(session.connection()) == 1
    session.commit()
    assert len(log_of(client, hook["id"])["deliveries"]) == 2


def test_a_try_lost_or_given_up_by_the_worker_is_said_so(client, session, services, receiver):
    hook = add(client)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    job = claim("w:1", [DELIVER_WEBHOOK])
    # A run whose lease was lost writes nothing, and the next sends again.
    with pytest.raises(LeaseLost):
        webhooks.deliver(job, Lease(job.id, "another:1"))
    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert (delivery["status"], delivery["attempts"]) == ("pending", 0)
    assert "body" in session.get(Job, job.id, populate_existing=True).payload

    abandon_delivery(job, services)
    [delivery] = log_of(client, hook["id"])["deliveries"]
    assert delivery["status"] == "failed" and "stopped unexpectedly" in delivery["error"]
    assert "body" not in session.get(Job, job.id, populate_existing=True).payload


def test_a_secret_is_encrypted_afresh_when_the_keys_rotate(
    client,
    session,
    services,
    receiver,
    keyed,  # noqa: F811
):
    old, new = new_key(), new_key()
    keyed(old)
    hook = add(client)
    keyed(new, old)
    *_, done = encrypt_secrets()
    assert (done.column, done.earlier) == (WEBHOOK_SECRETS, 1)
    assert key_of(session.get(Webhook, hook["id"], populate_existing=True).secret) == (
        fingerprint(new)
    )
    # Signed with the same secret, read with the new key alone.
    keyed(new)
    project = create_project(client)
    uploaded(client, project["id"])
    run_due(services, INDEX_DOCUMENT)
    run_due(services, DELIVER_WEBHOOK)
    assert verified(receiver.requests[0], hook["secret"])


def test_a_program_checks_a_signature_as_the_organisation_page_says():
    # A changed body, another secret, or a time too far from the receiver's own fails the check.
    secret, body = webhooks.new_secret(), b'{"id": "evt_1"}'
    now = int(time.time())
    assert checks(body, webhooks.sign(body, secret, now), secret)
    assert not checks(body + b" ", webhooks.sign(body, secret, now), secret)
    assert not checks(body, webhooks.sign(body, secret, now - TOLERANCE - 1), secret)
    assert not checks(body, webhooks.sign(body, webhooks.new_secret(), now), secret)
