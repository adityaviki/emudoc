"""Help for members: where to write for help, the status page and the code's source (`GET
/api/help`, public, since the page that cannot reach its server asks it too), the help pages
(`GET /api/help/articles`, a member's, filled in for the deployment: app/help/), the upload
client the help and the workspace's API keys offer (`GET /api/help/emudoc_upload.py`,
app/client/), and a member's message from the app's help menu (`POST /api/feedback`), mailed
with what its reader needs to find what they met.

The help pages come by a route of their own rather than in `HelpOut`: the menu asks `GET
/api/help` on every page, which would carry every article each time, and that route answers
without a session, while the pages are the members', describing what this deployment does.

The message goes to SUPPORT_EMAIL where the server names one, a team's helpdesk; otherwise to the
workspace's owners and admins, who run it, each by a mail of their own, the member who wrote
left out. A sole owner or admin has nobody to write to there, which they are told. An install
that mails nothing (`relay_set`) takes no message, which would reach its log by the subject alone:
`HelpOut.mail` has the menu say whom to write to from the member's own mail instead.

The message is the member's words and goes nowhere but the mail: not into a log line, nor an
`AppError`, nor the subject, which the mailer logs when a relay refuses it. What is attached is
the app's own: the account's and the workspace's ids, the page's path with the ids its query
names, the version the API runs, the browser's description of itself, and the reference of the
last request the page saw refused or failed, under which the log keeps what happened. It
changes nothing of the workspace's, so no audit row, and the queue's job gives the message up
once it is sent (app/postbox.py), so the app keeps none of it.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.auth import app_url
from app.api.deps import AccountDep, SessionDep, limited
from app.client import UPLOAD_CLIENT
from app.config import get_settings
from app.help import articles
from app.logs import current_context
from app.mail import compose, relay_set, support_message_email
from app.models import Membership, User
from app.postbox import post
from app.ratelimit import FEEDBACK
from app.schemas import FeedbackIn, FeedbackOut, HelpArticleOut, HelpOut
from app.version import VERSION, source_url

router = APIRouter(tags=["help"])

# What a sole owner or admin is told, with nobody else to write to and no SUPPORT_EMAIL.
NOBODY_ELSE = (
    "You are this workspace's only owner or admin, so a message from here would come back to "
    "you. To have the help menu write to someone, set SUPPORT_EMAIL on the server to their "
    "address, or make another member an admin."
)
# What a message sent where the install mails nothing is told, which the menu does not offer.
NO_MAIL = (
    "This workspace sends no email, so the help menu cannot send a message. Write from your own "
    "email to the address the help menu names, or to one of the workspace's owners or admins."
)
# The longest browser description a message carries; a real one is a few hundred characters.
BROWSER_CHARS = 300


@router.get("/help", response_model=HelpOut)
def help_offered():
    """Where members write for help, where the server names an address, the status page,
    whether a message can be sent, and where the running code's source is. It reads no
    database, so that the page that cannot reach its server, a database being down, still
    learns where the status page is."""
    settings = get_settings()
    return HelpOut(
        support_email=settings.support_email or None,
        status_page=settings.status_page_url or None,
        mail=relay_set(settings),
        source=source_url(settings.source_url),
    )


@router.get("/help/articles", response_model=list[HelpArticleOut])
def help_articles(account: AccountDep):
    """The help pages in the contents' order, filled in from what this deployment does."""
    return [HelpArticleOut(**asdict(article)) for article in articles(get_settings())]


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
    """Post the member's message to SUPPORT_EMAIL, or to the workspace's other owners and admins,
    replies going to the member, and answer with the reference it was sent under for them to
    quote."""
    settings = get_settings()
    if not relay_set(settings):
        raise HTTPException(409, NO_MAIL)
    support = settings.support_email
    readers = [support] if support else _admins(session, account)
    if not readers:
        raise HTTPException(409, NOBODY_ELSE)
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
        f"Workspace: {organization.name} ({organization.slug}), id {organization.id}, where "
        f"they are {account.role}",
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
    )
    for address in readers:
        post(
            session,
            address,
            compose(address, content, headers={"Reply-To": user.email}),
            organization.id,
        )
    session.commit()
    return FeedbackOut(reference=reference, to=support or None)


def _admins(session: Session, account: Account) -> list[str]:
    """The addresses of the workspace's owners and admins but the member writing, the longest
    in it first."""
    return list(
        session.scalars(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == account.organization.id,
                Membership.role.in_(("owner", "admin")),
                User.id != account.user.id,
            )
            .order_by(Membership.created_at, User.id)
        )
    )
