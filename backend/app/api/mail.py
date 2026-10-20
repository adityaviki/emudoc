"""The mail's routes outside the cross-site check: the webhook Resend posts its events to, and
the one-click unsubscribe a notice's headers name (app/postbox.py).

Neither request carries the app's X-Requested-With. Resend's events are proved by their
signature instead (app/bounces.py), over the body as it came, read raw up to MAX_EVENT_BYTES; a
bounce or a complaint is kept, and every other event is taken and does nothing. A mail program
that unsubscribes for its reader (RFC 8058) posts `List-Unsubscribe=One-Click` and nothing of
the app's, so the token in the address, encrypted for its user, is what says whose notices stop
by email, and of which kind; the page a notice's footer leads to (`/unsubscribe`) posts the
same. Turning a user's notices off does them no harm, so the route needs no session, as a mail
program has none.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import SessionDep, body_within
from app.bounces import Unsigned, resend_undeliverable, spare, verified_resend_event
from app.config import get_settings
from app.models import NOTICE_KINDS, User
from app.postbox import BY_EMAIL, unsubscriber

log = logging.getLogger(__name__)

# Included by main.py without the cross-site check.
events = APIRouter(tags=["mail"])

# Larger than any event of one message.
MAX_EVENT_BYTES = 256_000
TOO_LARGE = "That is larger than any event Resend sends."
EVENTS_OFF = "This deployment takes no events from Resend."
NOT_RESEND = "That request does not carry Resend's signature for this deployment."
LINK_INVALID = (
    "This link to stop the notices is not valid. Sign in and turn them off on your account page."
)


async def _event_body(request: Request) -> bytes:
    return await body_within(request, MAX_EVENT_BYTES, TOO_LARGE)


@events.post("/mail/events", status_code=204)
def resend_event(
    body: Annotated[bytes, Depends(_event_body)], request: Request, session: SessionDep
):
    """An event of Resend's, signed with RESEND_WEBHOOK_SECRET. Taking one twice keeps the same
    address twice, which is once."""
    secret = get_settings().resend_webhook_secret
    if not secret:
        raise HTTPException(404, EVENTS_OFF)
    try:
        event = verified_resend_event(body, request.headers, secret)
    except Unsigned as bad:
        log.warning("A request to the mail webhook was refused: it carried %s", bad)
        raise HTTPException(400, NOT_RESEND) from None
    if spare(session, resend_undeliverable(event)):
        session.commit()
        log.info("Resend said not to write to an address: %s", event["type"])


@events.post("/mail/unsubscribe", status_code=204)
def unsubscribe(session: SessionDep, token: Annotated[str, Query(max_length=200)]):
    """Stop the email of the kind of notice the token was made for, to its user; a token from
    before the kinds were chosen apart stops every kind's. One gone since is no one's to stop."""
    found = unsubscriber(token)
    if found is None:
        raise HTTPException(400, LINK_INVALID)
    user = session.get(User, found.user_id)
    if user is None:
        return
    kinds = [kind for kind in [found.kind] if kind] or NOTICE_KINDS
    wanted = [kind for kind in kinds if user.wants(kind, BY_EMAIL)]
    for kind in wanted:
        user.choose(kind, BY_EMAIL, False)
    if wanted:
        session.commit()
