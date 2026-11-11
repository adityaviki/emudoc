"""Help for members: where to write for help and the status page (`GET /api/help`, public, since
the page that cannot reach its server asks it too), the upload client the organisation's API keys
offer (`GET /api/help/emudoc_upload.py`, app/client/), and a member's message to support from
the app's help menu (`POST /api/feedback`), mailed to SUPPORT_EMAIL with what support needs to
find what they met.

The message is the member's words and goes nowhere but the mail: not into a log line, nor an
`AppError`, nor the subject, which the mailer logs when a relay refuses it. What is attached is
the app's own: the account's and the organisation's ids, the page's path with the ids its query
names, the version the API runs, the browser's description of itself, and the reference of the
last request the page saw refused or failed, under which the log keeps what happened. It
changes nothing of the organisation's, so no audit row, and the queue's job gives the message
up once it is sent (app/postbox.py), so the app keeps none of it.
"""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.auth import app_url
from app.api.deps import AccountDep, SessionDep, limited
from app.client import UPLOAD_CLIENT
from app.config import get_settings
from app.logs import current_context
from app.mail import compose, support_message_email
from app.postbox import post
from app.ratelimit import FEEDBACK
from app.schemas import FeedbackIn, FeedbackOut, HelpOut
from app.version import VERSION

router = APIRouter(tags=["help"])

# Only development runs without an address: production requires OPERATOR_EMAIL.
NO_SUPPORT = (
    "This deployment names no address for support: set SUPPORT_EMAIL or OPERATOR_EMAIL, then "
    "restart the backend."
)
# The longest browser description a message carries; a real one is a few hundred characters.
BROWSER_CHARS = 300


@router.get("/help", response_model=HelpOut)
def help_offered():
    """Where members write for help and the status page. It reads no database, so that the page
    that cannot reach its server, a database being down, still learns where the status page is."""
    settings = get_settings()
    return HelpOut(
        support_email=settings.support_address or None,
        status_page=settings.status_page_url or None,
    )


@router.get("/help/emudoc_upload.py", response_class=FileResponse)
def upload_client():
    """The upload client, a script a member downloads and runs with an API key to send a
    folder, or what a scanner saves to one, into a project (app/client/). Public, and the same
    for every deployment of a release."""
    return FileResponse(UPLOAD_CLIENT, media_type="text/x-python", filename=UPLOAD_CLIENT.name)


@router.post(
    "/feedback",
    response_model=FeedbackOut,
    status_code=202,
    dependencies=[Depends(limited(FEEDBACK))],
)
def send_feedback(body: FeedbackIn, account: AccountDep, request: Request, session: SessionDep):
    """Post the member's message to the support address, replies going to the member, and
    answer with the reference it was sent under for them to quote."""
    address = get_settings().support_address
    if not address:
        raise HTTPException(503, NO_SUPPORT)
    user, organization = account.user, account.organization
    reference = str(current_context().get("request_id") or uuid4().hex)
    failed = (
        f"Last failed request: {body.failed.reference}, answered {body.failed.status}"
        if body.failed
        else "No request of the page's failed before this message."
    )
    origin = app_url(request)
    details = (
        f"Account: user {user.id}, "
        f"address {'confirmed' if user.email_verified_at else 'not confirmed'}",
        f"Organisation: {organization.name} ({organization.slug}), id {organization.id}, on "
        f"the {organization.plan.title()} plan, where they are {account.role}",
        f"Page: {origin}{body.page}",
        failed,
        f"Version: {VERSION}",
        f"Browser: {request.headers.get('user-agent', 'not said')[:BROWSER_CHARS]}",
        f"Sent: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        f"Reference: {reference}",
    )
    content = support_message_email(
        body.kind,
        body.message,
        reference,
        f"{user.name} ({user.email})" if user.name else user.email,
        details,
        f"{origin}/operator/organizations/{organization.id}",
    )
    post(
        session,
        address,
        compose(address, content, headers={"Reply-To": user.email}),
        organization.id,
    )
    session.commit()
    return FeedbackOut(reference=reference, to=address)
