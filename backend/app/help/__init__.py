"""The help pages members read at /help: Markdown templates beside this module, one an article,
filled in as they are served (`GET /api/help`, app/api/help.py), as the terms of service are
(app/legal/). What they say of a figure is read from the code and the settings rather than
written into them: the plans' figures, the file types and sizes an upload takes, how long the
trash, an export and a deletion's grace last, the rates; so a figure that changes changes the
help with it, and a sentence about something a deployment does not do (scan uploads, sell
plans) is left out where it does not. An article's first line is its title; a link to another is
`/help/<slug>`, and tests/test_help.py holds every link to an article or a page that exists.
"""

from dataclasses import dataclass
from pathlib import Path
from string import Template

from app.config import Settings, parse_scanner_url
from app.entitlements import BY_THE_SEAT, CHARS_PER_PAGE, GB, MB, PLANS
from app.export import EXPORT_DAYS
from app.invitations import INVITATION_TTL
from app.legal import by_plan
from app.notices import NOTICE_AFTER, NOTIFICATION_DAYS
from app.parsing import IMAGE_TYPES, SUPPORTED_EXTENSIONS
from app.retention import DELETION_GRACE, RETENTION_HELD
from app.sample import NAME as SAMPLE_NAME
from app.support import SUPPORT_SESSION
from app.trash import TRASH_DAYS

TEMPLATES = Path(__file__).parent
# The articles in the order the contents list them, each with the line the contents show of it.
ARTICLES = (
    ("getting-started", "Projects, uploads and the first question: the way through Emudoc."),
    ("documents", "What can be uploaded, how it is read, folders, the trash and downloads."),
    ("asking", "Searching a project, asking about it, citations, and who sees a chat."),
    ("organisations", "Roles, invitations, private projects, settings and API keys."),
    ("plans", "What each plan allows, how usage is counted, and what happens at a limit."),
    ("account", "Your account, signing in, two-factor, notices and letting support in."),
    ("data", "Exporting a project or your account, the trash, and deleting."),
    ("support", "How to reach us, and what to give us when something went wrong."),
)
# The file types an upload takes, as a member knows them, in the order the help names them; an
# extension the parsers take that is not here stops the help from being served at all.
DOCUMENT_FORMATS = {
    ".pdf": "PDF",
    ".docx": "Word (.docx)",
    ".xlsx": "Excel (.xlsx)",
    ".csv": "CSV",
    ".txt": "plain text",
    ".md": "Markdown",
}
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WebP"}


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    summary: str
    # The whole article, its title the first heading.
    text: str


def articles(settings: Settings) -> list[Article]:
    """Every article, filled in for the deployment `settings` configure."""
    values = _values(settings)
    found = []
    for slug, summary in ARTICLES:
        text = Template((TEMPLATES / f"{slug}.md").read_text()).substitute(values)
        found.append(Article(slug, text.partition("\n")[0].removeprefix("# "), summary, text))
    return found


def _values(settings: Settings) -> dict[str, str]:
    support = settings.support_address
    return {
        "sample": _sample(settings.sample_project),
        "formats": _formats(),
        "max_upload_mb": f"{settings.max_upload_mb:,}",
        "max_upload_files": f"{settings.max_upload_files:,}",
        "max_request_mb": f"{settings.max_upload_request_mb:,}",
        "ocr_pages": (
            f", up to {settings.ocr_max_pages:,} pages of one document"
            if settings.ocr_max_pages
            else ""
        ),
        "scanning": (
            "Every upload is first scanned for malware; a file that is flagged is kept apart, "
            "and is never read or served."
            if parse_scanner_url(settings.scanner_url)
            else ""
        ),
        "notice_minutes": str(round(NOTICE_AFTER.total_seconds() / 60)),
        "trash_days": str(TRASH_DAYS),
        "question_chars": f"{settings.max_question_chars:,}",
        "chat_retention": by_plan(lambda plan: plan.retention_days, "until deleted"),
        "invitation_days": str(INVITATION_TTL.days),
        "api_rate": (
            f", and makes up to {settings.rate_api_requests:,} requests a minute"
            if settings.rate_api_requests
            else ""
        ),
        "plans": _plans(),
        "chars_per_page": f"{CHARS_PER_PAGE:,}",
        "lifts": _lifts(settings),
        "held_days": str(RETENTION_HELD.days),
        "session_days": str(settings.session_days),
        "breaches": (
            " A new password that has appeared in a data breach is refused."
            if settings.pwned_passwords_url
            else ""
        ),
        "pausing": (
            f" After {settings.rate_failed_sign_ins} wrong passwords or codes in an hour, "
            "sign-ins from the browsers that never signed in to the account pause until the "
            "hour ends, and you are told by email; your own browsers sign in as before."
            if settings.rate_failed_sign_ins
            else ""
        ),
        "notification_days": str(NOTIFICATION_DAYS),
        "support_hours": _hours(round(SUPPORT_SESSION.total_seconds() / 3600)),
        "export_days": str(EXPORT_DAYS),
        "deletion_days": str(DELETION_GRACE.days),
        "support": f"[{support}](mailto:{support})" if support else "[SUPPORT_EMAIL is not set]",
    }


def _sample(on: bool) -> str:
    if not on:
        return ""
    return (
        f'\nA new account\'s organisation starts with a sample project, "{SAMPLE_NAME}": a '
        "supplier's contract, invoices, a price list and a delivery log, read already, with "
        "questions to try in its chat. It counts against nothing on the plan, and you delete it "
        "like any project.\n"
    )


def _formats() -> str:
    """The file types an upload takes, in words: "PDF, Word (.docx), …, and images (PNG, …)"."""
    named = DOCUMENT_FORMATS | IMAGE_FORMATS
    if missing := SUPPORTED_EXTENSIONS - named.keys():
        raise RuntimeError(f"The help names no file type for {', '.join(sorted(missing))}.")
    taken = SUPPORTED_EXTENSIONS
    documents = [name for suffix, name in DOCUMENT_FORMATS.items() if suffix in taken]
    images = dict.fromkeys(name for suffix, name in IMAGE_FORMATS.items() if suffix in IMAGE_TYPES)
    return f"{', '.join(documents)} files, and images ({', '.join(images)})"


def _plans() -> str:
    """What each plan allows, as a table with a column a plan."""
    names = [name.title() for name in PLANS]
    rows = [
        ("Projects", lambda plan, _: _count(plan.projects)),
        ("Documents", lambda plan, _: _count(plan.documents)),
        ("Storage", lambda plan, _: _bytes(plan.storage_bytes)),
        ("Pages a month", lambda plan, _: _count(plan.pages_per_month)),
        ("Questions a month", lambda plan, _: _count(plan.questions_per_month)),
        (
            "Members",
            lambda plan, name: (
                f"{_count(plan.members)}, or the seats bought"
                if name in BY_THE_SEAT
                else _count(plan.members)
            ),
        ),
        ("API keys", lambda plan, _: "Yes" if plan.api_access else "No"),
        (
            "Chats kept after their last question",
            lambda plan, _: _days(plan.retention_days, "Until deleted"),
        ),
        ("Audit log kept", lambda plan, _: _days(plan.audit_days, "For good")),
    ]
    lines = [f"| | {' | '.join(names)} |", f"| --- |{' --- |' * len(names)}"]
    for label, cell in rows:
        cells = (cell(plan, name) for name, plan in PLANS.items())
        lines.append(f"| {label} | {' | '.join(cells)} |")
    return "\n".join(lines)


def _count(value: int | None) -> str:
    return "No limit" if value is None else f"{value:,}"


def _bytes(value: int | None) -> str:
    if value is None:
        return "No limit"
    return f"{value // GB:,} GB" if value >= GB else f"{value // MB:,} MB"


def _days(value: int | None, forever: str) -> str:
    return forever if value is None else f"{value:,} days"


def _hours(hours: int) -> str:
    return "an hour" if hours == 1 else f"{hours} hours"


def _lifts(settings: Settings) -> str:
    """What lifts a limit on this deployment: what an owner buys where plans are sold, else
    whom to write to."""
    if not settings.stripe_secret_key:
        return (
            "the people who run Emudoc set an organisation's plan, so write to support "
            "([Getting help](/help/support))."
        )
    packs = "a pack of pages or questions for the rest of the month, " if settings.packs else ""
    return (
        f"{packs}a larger plan, or more seats on Team, which an owner buys on the organisation "
        "page, under Plan, and changes or cancels there; anyone else is told to ask an owner."
    )
