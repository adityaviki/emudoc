"""The help pages members read at /help in the app: Markdown templates beside this module, one an
article, filled in as they are served (`GET /api/help/articles`, app/api/help.py). What they say
of a figure is read from the code and the settings rather than written into them: the file types
and sizes an upload takes, how long the trash, an export, an invitation and a deletion's grace
last, the limits of what a project holds, the rates, a webhook's tries; so a figure that changes
changes the help with it, and a sentence about something a deployment does not do (scan uploads,
show Word files as they look, send mail, take mail at a project's address, connect folders, sign
in through Google or Microsoft, cap the models' spending) is left out where it does not. An
article's first line is its title; a link to another is `/help/<slug>`, and tests/test_help.py
holds every link to an article or a page that exists.
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from string import Template

from app.api.connectors import MAX_CONNECTORS
from app.apikeys import MAX_KEYS
from app.auth import HANDED_LINK_TTL, RECOVERY_CODE_COUNT, RESET_LINK_TTL
from app.client import UPLOAD_CLIENT
from app.client.emudoc_upload import EVERY, KEY_VARIABLE, OLDEST_PYTHON, STATE_NAME
from app.config import Settings, parse_converter_url, parse_scanner_url
from app.connectors import providers as folder_services
from app.connectors.sync import SYNC_INTERVAL
from app.export import EXPORT_DAYS
from app.extraction import BUILT_IN, MAX_TEMPLATES
from app.idempotency import KEPT as IDEMPOTENCY_KEPT
from app.inbound import INBOUND_DAYS, MAX_SENDERS
from app.invitations import INVITATION_TTL
from app.mail import relay_set
from app.messages import MAX_MESSAGE_BYTES
from app.models import AUDIT_DAYS
from app.my_files import MAX_FILES as MAX_MY_FILES
from app.notices import NOTICE_AFTER, NOTIFICATION_DAYS
from app.oidc import providers as sign_in_providers
from app.parsing import IMAGE_TYPES, SCAN_TYPES, SUPPORTED_EXTENSIONS
from app.recents import RECENT
from app.retention import DELETION_GRACE
from app.sample import NAME as SAMPLE_NAME
from app.schemas import MAX_MENTIONS, PASSWORD_MIN_LENGTH
from app.selection import MAX_CHOSEN
from app.share_links import DAYS as SHARE_DAYS
from app.share_links import MAX_LINKS
from app.trash import TRASH_DAYS
from app.usage import CHARS_PER_PAGE, money
from app.webhooks import DELIVERIES_KEPT, FIRST_WAIT, MAX_TRIES, MAX_WEBHOOKS, TIMEOUT, TOLERANCE

TEMPLATES = Path(__file__).parent
# The articles in the order the contents list them, each with the line the contents show of it.
ARTICLES = (
    ("getting-started", "Projects, uploads and the first question: the way through Emudoc."),
    (
        "documents",
        "Uploading, the upload client, email in, connected folders, how a file is read, folders, "
        "tabs, versions, the trash and downloads.",
    ),
    ("asking", "Searching a project and every project, asking, citations, and who sees a chat."),
    ("checking", "Extraction templates and the facts table."),
    (
        "working-together",
        "My files, writing templates and share links.",
    ),
    (
        "workspace",
        "Roles, invitations, private projects, settings, usage, the Administration page, API keys "
        "and webhooks.",
    ),
    ("account", "Your account, signing in, passwords, two-factor sign-in and notices."),
    ("data", "Exporting a project or your account, the trash, and deleting your account."),
    ("support", "Whom to write to, and what to give them when something went wrong."),
)
# The file types an upload takes, as a member knows them, in the order the help names them; an
# extension the parsers take that is not here stops the help from being served at all.
DOCUMENT_FORMATS = {
    ".pdf": "PDF",
    ".docx": "Word (.docx)",
    ".xlsx": "Excel (.xlsx)",
    ".pptx": "PowerPoint (.pptx)",
    ".rtf": "RTF",
    ".html": "HTML",
    ".htm": "HTML",
    ".csv": "CSV",
    ".txt": "plain text",
    ".md": "Markdown",
}
IMAGE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WebP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".heic": "HEIC",
    ".heif": "HEIC",
}


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
    mail = relay_set(settings)
    return {
        "sample": _sample(settings.sample_project),
        "formats": formats(),
        "max_upload_mb": f"{settings.max_upload_mb:,}",
        "max_pages": (
            f", and a PDF, a TIFF or a presentation up to {settings.parse_max_pages:,} pages"
            if settings.parse_max_pages
            else ""
        ),
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
        "views": (
            " A Word file, a presentation, a spreadsheet, an RTF or an HTML file opens as it "
            "looks, page by page; Original and Text above it switch to its text, with its outline "
            "beside it, where a passage an answer cites is marked: a citation opens there."
            if parse_converter_url(settings.converter_url)
            else ""
        ),
        "my_views": (
            ", shown as it looks, page by page"
            if parse_converter_url(settings.converter_url)
            else ""
        ),
        "notice_minutes": str(round(NOTICE_AFTER.total_seconds() / 60)),
        "trash_days": str(TRASH_DAYS),
        "recent": str(RECENT),
        "chosen": str(MAX_CHOSEN),
        "within": str(MAX_MENTIONS),
        "question_chars": f"{settings.max_question_chars:,}",
        "spending": _spending(settings),
        "email_in": _email_in(settings),
        "connected": _connected(settings),
        "upload_client": f"/api/help/{UPLOAD_CLIENT.name}",
        "oldest_python": ".".join(map(str, OLDEST_PYTHON)),
        "key_variable": KEY_VARIABLE,
        "app_url": settings.app_url.rstrip("/") or "https://emudoc.example.org",
        "watch_seconds": f"{EVERY:g}",
        "state_file": STATE_NAME,
        # Checking documents.
        "built_in": _count_word(len(BUILT_IN)),
        "max_templates": str(MAX_TEMPLATES),
        # Working together.
        "my_files": str(MAX_MY_FILES),
        "share_days": _either(SHARE_DAYS),
        "share_links": str(MAX_LINKS),
        "password_chars": str(PASSWORD_MIN_LENGTH),
        # The workspace.
        "invitation_days": str(INVITATION_TTL.days),
        "invitation_mail": (
            "an invitation goes to their address, and the page shows its link as well, to send "
            "them yourself"
            if mail
            else "this workspace sends no email, so the page shows the invitation's link, to "
            "copy and send them yourself"
        ),
        "signup": (
            " Anyone may also make an account on the sign-up page, and joins the workspace as a "
            "member."
            if settings.signup_open
            else " Accounts are made by invitation alone: the sign-up page says to ask an admin."
        ),
        "handed_hours": _span(HANDED_LINK_TTL),
        "audit_days": f"{AUDIT_DAYS:,}",
        "session_days": str(settings.session_days),
        "chars_per_page": f"{CHARS_PER_PAGE:,}",
        "max_keys": str(MAX_KEYS),
        "api_rate": (
            f", and makes up to {settings.rate_api_requests:,} requests a minute"
            if settings.rate_api_requests
            else ""
        ),
        "idempotency_hours": str(round(IDEMPOTENCY_KEPT.total_seconds() / 3600)),
        "max_webhooks": str(MAX_WEBHOOKS),
        "tolerance_minutes": str(TOLERANCE // 60),
        "tolerance_seconds": str(TOLERANCE),
        "webhook_timeout": f"{TIMEOUT:g}",
        "webhook_first_wait": _span(FIRST_WAIT),
        "webhook_tries": str(MAX_TRIES),
        "webhook_span": _span(FIRST_WAIT * (2 ** (MAX_TRIES - 1) - 1), "hour"),
        "delivery_days": str(DELIVERIES_KEPT.days),
        # The account.
        "address_change": _address_change(mail),
        "forgotten": _forgotten(mail),
        "outside": _outside(settings),
        "breaches": (
            " A new password that has appeared in a data breach is refused."
            if settings.pwned_passwords_url
            else ""
        ),
        "pausing": _pausing(settings, mail),
        "recovery_codes": _count_word(RECOVERY_CODE_COUNT),
        "notice_channels": (
            "whether each is told in the app, by email, both or neither (the link at the foot "
            "of a notice's email stops that kind's email too)"
            if mail
            else "whether each is told in the app or not at all (this workspace sends no email)"
        ),
        "notification_days": str(NOTIFICATION_DAYS),
        # Your data.
        "export_days": str(EXPORT_DAYS),
        "deletion_days": str(DELETION_GRACE.days),
        # Getting help.
        "write_to": _write_to(settings.support_email, mail),
        "status_page": _status_page(settings.status_page_url),
    }


def formats() -> str:
    """The file types an upload takes, in words: "PDF, Word (.docx), …, and images (PNG, …)"."""
    named = DOCUMENT_FORMATS | IMAGE_FORMATS
    if missing := SUPPORTED_EXTENSIONS - named.keys():
        raise RuntimeError(f"The help names no file type for {', '.join(sorted(missing))}.")
    taken = SUPPORTED_EXTENSIONS
    documents = dict.fromkeys(name for suffix, name in DOCUMENT_FORMATS.items() if suffix in taken)
    images = dict.fromkeys(
        name for suffix, name in IMAGE_FORMATS.items() if suffix in IMAGE_TYPES | SCAN_TYPES
    )
    return f"{', '.join(documents)} files, and images ({', '.join(images)})"


def _span(wait: timedelta, unit: str | None = None) -> str:
    """A wait as a sentence says it, in the largest whole unit unless `unit` names one:
    "2 minutes", "24 hours", "34 hours"."""
    seconds = wait.total_seconds()
    if unit is None:
        unit = next(
            (name for name, size in (("hour", 3600), ("minute", 60)) if seconds % size == 0),
            "second",
        )
    count = round(seconds / {"hour": 3600, "minute": 60, "second": 1}[unit])
    return f"{count} {unit}{'' if count == 1 else 's'}"


def _either(values: tuple[int, ...]) -> str:
    """Choices as a sentence offers them: "1, 7 or 30"."""
    *first, last = map(str, values)
    return f"{', '.join(first)} or {last}" if first else last


def _count_word(count: int) -> str:
    """A small count in words, as prose writes it: "seven"; a larger one in figures."""
    words = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    return words[count] if count < len(words) else f"{count:,}"


def _sample(on: bool) -> str:
    if not on:
        return ""
    return (
        f'\nThe workspace starts with a sample project, "{SAMPLE_NAME}": a supplier\'s contract, '
        "invoices, a price list and a delivery log, read already, to try questions on. It is not "
        "counted in the workspace's usage, and you delete it like any project.\n"
    )


def _spending(settings: Settings) -> str:
    """What the daily spending cap does, where whoever runs Emudoc set one."""
    if not settings.daily_spend_cap_micros:
        return ""
    return (
        "\nThe models' work may cost up to "
        f"{money(settings.daily_spend_cap_micros)} a day, as whoever runs Emudoc set it. Once a "
        "day reaches it, a question or a report is refused with a sentence saying so, "
        "and documents wait to be summarised and read for facts, until the limit starts afresh "
        "at midnight UTC. What has started finishes.\n"
    )


def _email_in(settings: Settings) -> str:
    """The section on a project's address, where the deployment takes mail."""
    if not settings.inbound_domain:
        return ""
    return f"""
## Email in

A project can have an address of its own, so that what is mailed to it becomes its documents:
**Email in**, among the library's buttons, gives it one with "Give it an address", to copy and
hand to a supplier, and changes it ("New address") or takes it away. Each message kept becomes a
document of its text, headed by who sent it to whom and when, and each file attached to it a
document beside it, in the folder "Where it goes" names; a message may be up to
{MAX_MESSAGE_BYTES // 2**20} MB. Those who may change the project may always send to it from
their confirmed address; up to {MAX_SENDERS} other addresses or whole domains are listed under
"Who may send to it", and mail from anyone else is turned away. "Received lately" lists what
arrived over the last {INBOUND_DAYS} days and what became of it. Viewers read it and change
nothing.
"""


def _connected(settings: Settings) -> str:
    """The section on connected folders, naming the services the deployment has set up."""
    services = [service.label for service in folder_services(settings).values()]
    if not services:
        return ""
    *first, last = services
    named = f"{', '.join(first)} or {last}" if first else last
    return f"""
## Connected folders

A project can keep in step with a folder of your {named}: **Connected folders**, among the
library's buttons, connects the service through its own consent page, then you choose the folder
and the project's folder its files go into. Its files are brought in as documents, its folders
with them, and Emudoc looks again every {_span(SYNC_INTERVAL)} and at "Sync now": a file changed
there becomes its document's new version, and one removed there goes into the project's trash.
"Disconnect" stops it, the documents brought in staying. Whoever may change the project
connects folders, up to {_count_word(MAX_CONNECTORS)} a project; viewers read the list.
"""


def _address_change(mail: bool) -> str:
    if mail:
        return "A new email address is confirmed through a link sent to it."
    return (
        "This workspace sends no email, so a new address cannot be confirmed by a link sent to "
        "it: an owner or admin changes theirs at once with their password, and anyone else asks "
        "one of them for a link that moves it."
    )


def _forgotten(mail: bool) -> str:
    if mail:
        return (
            "A forgotten password is reset from the sign-in page, through a link sent by email "
            f"that works for {_span(RESET_LINK_TTL)}, which signs the account out everywhere."
        )
    return (
        "A forgotten password: this workspace sends no email, so ask one of its owners or admins "
        "for a link that sets a new one, which they make beside your name in the members list "
        f"([The workspace, members and roles](/help/workspace)). It works once, for "
        f"{_span(HANDED_LINK_TTL)}, and signs the account out everywhere."
    )


def _outside(settings: Settings) -> str:
    labels = [provider.label for provider in sign_in_providers(settings).values()]
    if not labels:
        return ""
    named = " or ".join(f"Continue with {label}" for label in labels)
    return (
        f' You may sign in with "{named}" on the sign-in page instead of a password, with the '
        "account of the same address."
    )


def _pausing(settings: Settings, mail: bool) -> str:
    if not settings.rate_failed_sign_ins:
        return ""
    told = ", and you are told by email" if mail else ""
    return (
        f" After {settings.rate_failed_sign_ins} wrong passwords or codes in an hour, sign-ins "
        f"from the browsers that never signed in to the account pause until the hour ends{told}; "
        "your own browsers sign in as before."
    )


def _write_to(support: str, mail: bool) -> str:
    """Where the help menu's words go: SUPPORT_EMAIL or the workspace's owners and admins, by
    the menu's message, or from the member's own mail where the install sends none."""
    address = f"[{support}](mailto:{support})" if support else ""
    if mail:
        readers = (
            f"to {address}, the address your team set for help"
            if support
            else "to the workspace's owners and admins, each by a mail of their own; an owner or "
            "admin who is the only one has nobody to write to there"
        )
        also = f" You can also write to {address} from your own mail." if support else ""
        return (
            f"It writes {readers}, from wherever you are: choose whether it is a problem, a "
            "question or an idea, and say it in your own words. The message goes with your name "
            "and address, the page you wrote from and the reference of the last request that "
            "failed there, so that its reader can find what you met, and says where it went; the "
            f"answer comes to your email address.{also}"
        )
    readers = (
        f"{address}, the address your team set for help"
        if support
        else "the workspace's owners and admins"
    )
    return (
        "This workspace sends no email, so the menu sends nothing: it names "
        f"{readers}, to write to from your own email, each a link to a message that carries the "
        "page you were on and the reference of the last request that failed there."
    )


def _status_page(url: str) -> str:
    if not url:
        return ""
    return (
        "\nWhether Emudoc is up, and what is being done about a fault, is on "
        f"[its status page]({url}), which runs apart from Emudoc, so that it answers when Emudoc "
        'does not; the help menu\'s "Service status" leads there too.\n'
    )
