# The workspace, members and roles

The workspace is your team's place in Emudoc: its projects, its members, its settings and its
audit log. The header names it, and a click on its name opens [its page](/organization), as
"Workspace" in the menu behind your initials does.

## Roles

| Role | May |
| --- | --- |
| Viewer | read every project the workspace lets them see, search, ask, download, and keep their own My files |
| Member | as a viewer, and make projects, upload, organise, delete, export, review, check facts, make drafts and share links, and choose who sees a project |
| Admin | as a member, and see every project, invite, change members' roles and remove them, choose the workspace's settings, make API keys and webhooks, read the audit log and open the Administration page |
| Owner | as an admin, and do all of that to the owners too, and hand ownership over |

Owners and admins change a role or remove a member under Members on the workspace's page, never
one above their own, nor their own. "Hand over…", an owner's, makes another member an owner and
themselves an admin. A member removed keeps their account, but no longer reaches the workspace:
only a new invitation brings them back. The workspace always has an owner: the last one hands
ownership over before deleting their account.

## Inviting

An owner or admin invites by email, with a role up to their own, under Invitations:
$invitation_mail. The invitation lasts $invitation_days days, until it is accepted or sent
again, and can be sent again or revoked.$signup

Under Members, an owner or admin also makes, for another member, a link that sets a new password
("Reset password…") or one that moves their account to a new address ("Change address…"), to
send them: each works once, for $handed_hours.

## Private projects

"Who sees it" on a project's card makes it private: only the members chosen for it, and the
owners and admins, see it. Chats inside a project are private to whoever started them until
they share them.

## Settings and security

Everyone reads the workspace's settings on its page; its owners and admins choose them:

- **Language of its documents**: the one documents without one of their own are searched and
  read in ([A document's details](/help/documents)).
- **Chats kept**: until they are deleted, or a number of days after their last question. The
  runs of the cross-checks, the question sets' reports and the drafts, which hold what documents
  said as answers do, are kept as long.
- **Audit log kept**: $audit_days days until they choose fewer or more. The audit log, under
  Security, says who did what and when, and downloads as a CSV file.
- **How members are told** of each kind of notice, until each chooses for themselves.
- **Require two-factor sign-in** of everyone, and **Sign in again** sooner than every
  $session_days days.

Shortening how long chats or the audit log are kept deletes what is older within hours, for good,
so the page asks first.

## Usage and costs

Every member reads what the workspace holds (its projects, documents, storage and members) and
its pages indexed and questions asked, by day and by month, on its
[usage page](/organization/usage), which "See what it holds and has used" on the workspace's page
opens. Days and months are in UTC. A document without pages, such as a Word file or a
spreadsheet, counts a page for every $chars_per_page characters of its text, and the sample
project counts for nothing. Owners and admins see there what the models cost too.

## The Administration page

The workspace's owners and admins have an [Administration page](/admin), from the menu behind
their initials or the workspace's page: under Jobs, the background work, what failed and why, to
run again; under Services, how the AI models and the other services stand, with a switch that
stops a model provider at once; under Costs, what the models cost, by day and by kind of work.
Running failed documents again and switching a provider are written in the audit log, under the
name of whoever did it.

## API keys

Owners and admins make API keys for programs under API keys, up to $max_keys. A key acts as the
member who made it, in the workspace's projects alone, either reading or reading and changing,
for 30 or 90 days, a year or until revoked$api_rate. The key is shown once. A key that reads and
changes is what the upload client sends a folder or a scanner's files with
([Uploading and organising documents](/help/documents)).

A program builds on the versioned API, under `/api/v1`, which changes only by adding to it, so
that what a program does today it goes on doing. With it a program makes and organises projects,
folders and documents, follows an upload until it is indexed, searches and reads the facts, asks
questions and follows their answers as they are written, and runs the cross-checks and reads
what they found. A request that makes something may carry an `Idempotency-Key` header, a string
of the program's own such as a UUID: sent again with the same key within $idempotency_hours
hours, after an answer that did not arrive, it is given the first one's answer and carried out
once. The tools that write a client for a program, and those that call an API from a flow, read
the API as an OpenAPI document, [openapi.json](/api/v1/openapi.json).

## Webhooks

A webhook tells a program what happens in the workspace as it happens, so that it need not ask
the API again and again. Owners and admins add one under Webhooks, up to $max_webhooks: the
program's address, starting with `https://`, and the events it is told of:

| Event | Sent |
| --- | --- |
| `document.ready` | once a document can be searched and asked about, and again each time it is indexed again |
| `document.failed` | once a document could not be read, or the malware scan quarantined it |
| `check_run.completed` | once a run of a project's cross-checks is worked out, and again when its report is written |
| `report.completed` | once a question set's report is answered, or failed; it says which set and how many questions were answered, and a program reads the answers by the report's id |

Each event is posted as JSON: its `id`, its `type`, `created_at` and `data`, which is what the
versioned API answers for the document, the run or the report (without its answers), so a
program reads one model however it heard. It is signed with the webhook's secret, shown once as
the webhook is added: the `Emudoc-Signature` header is `t=` the time in seconds and `v1=` the
HMAC-SHA256, in hex, of that time, a full stop and the body as sent. Check it before anything
else, as in Python:

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
tried again, the wait doubling from $webhook_first_wait, $webhook_tries times over about
$webhook_span, so that a program that was away hears of what it missed. An event may come twice,
so drop one whose `id` you have seen. Each webhook's Deliveries list what was sent to it, for
$delivery_days days, and how its address answered; Pause holds its events, and Roll the secret
gives it a new one at once. An address must be on the internet: Emudoc sends nothing into the
network it runs in, nor over plain HTTP.
