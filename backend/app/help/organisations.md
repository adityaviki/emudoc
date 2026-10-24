# Organisations, members and roles

An organisation holds projects, members, a plan and an audit log. You may belong to several:
the organisation's name in the header switches between them, and starts a new one.

## Roles

| Role | May |
| --- | --- |
| Viewer | read every project the organisation lets them see, search, ask, and download |
| Member | as a viewer, and make projects, upload, organise, delete, export and choose who sees a project |
| Admin | as a member, and see every project, invite, change members' roles and remove them, choose the organisation's settings, make API keys and webhooks and read the audit log |
| Owner | as an admin, and buy and change the plan, hand ownership over and delete the organisation |

Owners and admins change a role or remove a member on the [organisation page](/organization),
never one above their own. An organisation always has an owner: the last one hands ownership
over before leaving.

## Inviting

An owner or admin invites by email, with a role, under Invitations; the invitation lasts
$invitation_days days and can be sent again or revoked. They can also let everyone with a
confirmed address at their own email domain join, as a member or a viewer, under Joining by
email domain. Each member takes a place on the plan ([Plans and usage](/help/plans)).

## Private projects

"Who sees it" on a project's card makes it private: only the members chosen for it, and the
owners and admins, see it. Chats inside a project are private to whoever started them until
they share them.

## Settings and security

Owners and admins choose, on the organisation page, the language documents are searched in, to
keep chats for less time than the plan does, how members are told each kind of notice until
they choose themselves, to require two-factor sign-in of everyone, and to sign members in again
sooner. The audit log, under Security, says who did what and when.

## API keys

On a plan with API access, owners and admins make API keys for programs under API keys. A key
acts as the member who made it, in the organisation's projects alone, either reading or reading
and changing, for 30, 90 or 365 days or until revoked$api_rate. The key is shown once. A key
that reads and changes is what the upload client sends a folder or a scanner's files with
([Uploading and organising documents](/help/documents)).

A program builds on the versioned API, under `/api/v1`, which changes only by adding to it, so
that what a program does today it goes on doing. With it a program makes and organises projects,
folders and documents, follows an upload until it is indexed, searches and reads the facts, asks
questions and follows their answers as they are written, and runs the cross-checks and reads
what they found. A request that makes something may carry an `Idempotency-Key` header, a string
of the program's own such as a UUID: sent again with the same key within $idempotency_hours
hours, after an answer that did not arrive, it is given the first one's answer and carried out
once.

The [API reference](/api-reference) describes each of its routes and the events of the
webhooks below: what they take, what they answer, and a request to try with `curl`. Anyone may
read it, before they hold a key. The tools that write a client for a program, and those that
call an API from a flow, read the same as an OpenAPI document,
[openapi.json](/api/v1/openapi.json).

## Webhooks

A webhook tells a program what happens in the organisation as it happens, so that it need not
ask the API again and again. On a plan with API access, owners and admins add one under
Webhooks, up to $max_webhooks: the program's address, starting with `https://`, and the events
it is told of:

| Event | Sent |
| --- | --- |
| `document.ready` | once a document can be searched and asked about, and again each time it is indexed again |
| `document.failed` | once a document could not be read, or the malware scan quarantined it |
| `check_run.completed` | once a run of a project's cross-checks is worked out, and again when its report is written |

Each event is posted as JSON: its `id`, its `type`, `created_at` and `data`, which is what the
versioned API answers for the document or the run, so a program reads one model however it
heard. It is signed with the webhook's secret, shown once as the webhook is added: the
`Emudoc-Signature` header is `t=` the time in seconds and `v1=` the HMAC-SHA256, in hex, of
that time, a full stop and the body as sent. Check it before anything else, as in Python:

```python
import hashlib
import hmac
import time


def verified(body: bytes, header: str, secret: str) -> bool:
    """Whether Emudoc signed the body as it came, within $tolerance_minutes minutes."""
    parts = dict(part.split("=", 1) for part in header.split(","))
    signed = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256)
    recent = abs(time.time() - int(parts["t"])) <= $tolerance_seconds
    return recent and hmac.compare_digest(signed.hexdigest(), parts["v1"])
```

Answer any 2xx, within $webhook_timeout seconds; anything else, a redirect, or no answer is
tried again, the wait doubling from $webhook_first_wait, $webhook_tries times over about $webhook_span, so
that a program that was away hears of what it missed. An event may come twice, so drop one
whose `id` you have seen. Each webhook's Deliveries list what was sent to it, for
$delivery_days days, and how its address answered; Pause holds its events, and Roll the secret
gives it a new one at once. An address must be on the internet: Emudoc sends nothing into the
network it runs in, nor over plain HTTP.
