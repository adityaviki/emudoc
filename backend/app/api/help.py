"""Help for members: the help pages and where to write for help (`GET /api/help`, public, since
the sign-in pages link to the help too; the articles are app/help/), what's new (`GET
/api/changelog`, public likewise, and `POST /api/auth/changelog/read`, the account's; the
entries are the changelog's, app/changelog.py), and a member's message to support from the app's
help menu (`POST /api/feedback`), mailed to SUPPORT_EMAIL with what support needs to find what
they met.

The message is the member's words and goes nowhere but the mail: not into a log line, nor an
`AppError`, nor the subject, which the mailer logs when a relay refuses it. What is attached is
the app's own: the account's and the organisation's ids, the page's path with the ids its query
names, the version the API runs, the browser's description of itself, and the reference of the
last request the page saw refused or failed, under which the log keeps what happened. It
changes nothing of the organisation's, so no audit row, and the queue's job gives the message
up once it is sent (app/postbox.py), so the app keeps none of it.
"""

from dataclasses import asdict
from datetime import UTC, date, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import app_url, me_out
from app.api.deps import AccountDep, SessionDep, UsableAccountDep, limited
from app.changelog import news, page, read_through
from app.config import get_settings
from app.help import articles
from app.logs import current_context
from app.mail import compose, support_message_email
from app.postbox import post
from app.ratelimit import FEEDBACK
from app.schemas import (
    ChangelogOut,
    ChangesDayOut,
    FeedbackIn,
    FeedbackOut,
    HelpArticleOut,
    HelpOut,
    MeOut,
)
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
    """The help pages, filled in for this deployment, where members write for help and the
    status page. It reads no database, so that the page that cannot reach its server, a database
    being down, still learns where the status page is."""
    settings = get_settings()
    return HelpOut(
        articles=[HelpArticleOut(**asdict(article)) for article in articles(settings)],
        support_email=settings.support_address or None,
        status_page=settings.status_page_url or None,
    )


@router.get("/changelog", response_model=ChangelogOut)
def changelog(before: date | None = None):
    """What changed for members, a page of days at a time, the newest first: the changelog's
    entries for them, the same for every deployment of a release. Public and without the
    database, like the help pages."""
    days, next_before = page(news(), before)
    return ChangelogOut(
        days=[
            ChangesDayOut(first=day.first, last=day.last, entries=list(day.members)) for day in days
        ],
        next_before=next_before,
    )


@router.post("/auth/changelog/read", response_model=MeOut)
def read_changelog(account: UsableAccountDep, session: SessionDep):
    """Say the user has read what's new as it stands, for every organisation, and answer the
    account with nothing left unread. Not the organisation's business, so not audited; support's
    view of the account cannot say it (`READ_ONLY_VIEW`)."""
    read_through(news(), account.user)
    session.commit()
    return me_out(session, account)


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
