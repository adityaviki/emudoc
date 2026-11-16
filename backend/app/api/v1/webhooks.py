"""The events the versioned API posts to an organisation's webhooks (app/webhooks.py), as the
contract lists them (OpenAPI's `webhooks`, app/api/v1/contract.py): what each is, the body it
carries, which is the API's own model, and the headers it is signed and named in. These are
never routes of the app; they are what a program's own route receives.
"""

from typing import Annotated

from fastapi import APIRouter, Header

from app.api.v1.schemas import DocumentEvent
from app.webhooks import FIRST_WAIT, MAX_TRIES, TIMEOUT, TOLERANCE

events = APIRouter(tags=["webhooks"])

# What every event carries in its headers.
Signature = Annotated[
    str,
    Header(
        alias="Emudoc-Signature",
        description=(
            "`t=<seconds>,v1=<hex>`: the HMAC-SHA256, with the webhook's secret, of the try's "
            "Unix time, a full stop and the body as sent. Check it before reading the body, and "
            f"refuse a time more than {TOLERANCE // 60} minutes from yours."
        ),
    ),
]
Event = Annotated[str, Header(alias="Emudoc-Event", description="The event's `type`.")]
TAKEN = (
    "Any 2xx answer is a delivery, its body unread. Anything else, a redirect, or no answer "
    f"within {TIMEOUT:g} seconds is tried again, the wait doubling from "
    f"{FIRST_WAIT.seconds // 60} minute, {MAX_TRIES} times in all."
)


@events.post("document.ready", summary="A document is indexed", response_description=TAKEN)
def document_ready(body: DocumentEvent, signature: Signature, event: Event) -> None:
    """A document of the organisation's projects can be searched and asked about: its indexing
    ended, and no other is to run after it. Sent again each time it is indexed again."""


@events.post(
    "document.failed", summary="A document could not be indexed", response_description=TAKEN
)
def document_failed(body: DocumentEvent, signature: Signature, event: Event) -> None:
    """A document could not be read, or the malware scan quarantined it: its indexing ended, and
    no other is to run after it. `data.error` says why."""
