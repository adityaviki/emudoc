"""The public site: the home page a visitor without a session finds at /, and what search engines
are told (robots.txt, the sitemap, and which pages they may list).

The page is plain HTML that the frontend's build writes (`frontend/home.html`, `vite build
--mode site`), so that a search engine reads it whole without running a script. What it says of
the deployment is filled in here as it is served: each `$name` from the code and the settings,
and a part between `<!-- if name -->` and `<!-- end name -->` kept or left out by the settings:
by SIGNUP_OPEN, or for a feature that needs an outside service (email in, connected folders),
once the deployment has it, so that the site offers nothing a new workspace would not find.

The page offers no sign-up of its own (2026-11-11, the owner's word): its way in is the booking
page, or a message to the support address (`booking` in app/signups.py). While sign-up is closed
(SIGNUP_OPEN=false, as an install starts), it says so, by the parts marked `signup_closed`;
`signup_open` marks a part shown only while it is open.

The plans' page, the help pages and the API reference are put away for now (the same day), and
the page of what's new is gone (the same day): their addresses send a visitor to /, and no search
engine is told of them.

A signed-in browser, or development's local account, gets the members' page at / as before;
the session cookie alone tells them apart, so that the page asks the database nothing. Search
engines are asked to list the public pages only where SEARCH_ENGINES says so: everywhere else
robots.txt turns them away and every page carries `X-Robots-Tag: noindex`, and on a deployment
that is listed, every page but the public ones still does (a member's page, a share link's).
"""

import hashlib
import html as markup
import re
from datetime import UTC, datetime
from string import Template
from xml.sax.saxutils import escape

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from app.auth import SESSION_COOKIE
from app.config import REGIONS, Settings, parse_scanner_url
from app.connectors import providers
from app.headers import PAGE_KEPT
from app.parsing import IMAGE_TYPES, SCAN_TYPES, SUPPORTED_EXTENSIONS
from app.signups import booking

HOME_PAGE = "home.html"
# The site's pages by the path they are served at, and the paths search engines may list beside
# them: the terms, the privacy policy and the subprocessors, all pages anyone reads without an
# account, and the ways in.
PAGES = {"": HOME_PAGE}
LISTED = ("", "terms", "privacy", "subprocessors")
INDEXED = frozenset([*LISTED, "login", "signup"])
NOT_LISTED = "noindex"
# The pages put away for now, whose addresses send a visitor home: the plans, the API reference,
# the help with each of its articles, and what's new, which is gone.
AWAY = re.compile(r"pricing|api-reference|help(?:/.*)?|whats-new")
# A part of a page shown only while sign-up is open or closed (`SIGNUP`), or while the
# deployment has the outside service its feature needs (`SERVICES`).
_MARKED = re.compile(r"[ \t]*<!-- if (\w+) -->\n?(.*?)[ \t]*<!-- end \1 -->\n?", re.S)
SIGNUP = {"signup_open": True, "signup_closed": False}
SERVICES = {
    "email_in": lambda settings: bool(settings.inbound_domain),
    "connectors": lambda settings: bool(providers(settings)),
}
# The file types an upload takes, as a member knows them, in the order the page names them; an
# extension the parsers take that is not here stops the page from being served at all.
DOCUMENT_FORMATS = {
    ".pdf": "PDF",
    ".docx": "Word",
    ".xlsx": "Excel",
    ".pptx": "PowerPoint",
    ".rtf": "RTF",
    ".html": "HTML",
    ".htm": "HTML",
    ".csv": "CSV",
    ".txt": "Plain text",
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


def page_at(path: str, request: Request, settings: Settings) -> str | None:
    """The site's page that `path` shows this request, or None for the members' page: / to a
    visitor without a session."""
    path = path.strip("/")
    if path == "" and (settings.local_account or request.cookies.get(SESSION_COOKIE)):
        return None
    return PAGES.get(path)


def moved(path: str) -> str | None:
    """Where a page put away sends a visitor instead, or None."""
    return "/" if AWAY.fullmatch(path.strip("/")) else None


def fill(html: str, settings: Settings, app_url: str) -> str:
    """The page with its marked parts kept or left out and every `$name` filled in."""
    shown = _MARKED.sub(lambda part: part[2] if _shown(part[1], settings) else "", html)
    return Template(shown).substitute(values(settings, app_url))


def _shown(name: str, settings: Settings) -> bool:
    """Whether a page's part marked `name` is kept: one about sign-up by SIGNUP_OPEN, one about a
    feature by whether the deployment has its service. A mark neither names raises KeyError, so
    that a misspelt one stops the page rather than show or hide it."""
    if name in SIGNUP:
        return settings.signup_open == SIGNUP[name]
    return SERVICES[name](settings)


def values(settings: Settings, app_url: str) -> dict[str, str]:
    """What the site's page says of this deployment, by placeholder."""
    book_url, book_label = booking(settings)
    documents, images = file_types()
    return {
        "app_url": app_url,
        "year": str(datetime.now(UTC).year),
        "operator": markup.escape(settings.operator_name or "Emudoc"),
        "document_types": "".join(f"<li>{markup.escape(name)}</li>" for name in documents),
        "image_types": _listed(images),
        "kept": _kept(settings),
        "sample": (
            " Every new team starts with a sample project already read, a supplier's contract "
            "with its invoices, price list and delivery log, and questions to ask about them."
            if settings.sample_project
            else ""
        ),
        "book": markup.escape(book_url),
        "book_label": book_label,
    }


def file_types() -> tuple[list[str], list[str]]:
    """The kinds of document an upload takes and the kinds of image, by the names a member knows
    them by, each once."""
    named = DOCUMENT_FORMATS | IMAGE_FORMATS
    if missing := SUPPORTED_EXTENSIONS - named.keys():
        raise RuntimeError(f"The home page names no file type for {', '.join(sorted(missing))}.")
    documents = dict.fromkeys(
        name for suffix, name in DOCUMENT_FORMATS.items() if suffix in SUPPORTED_EXTENSIONS
    )
    images = dict.fromkeys(
        name for suffix, name in IMAGE_FORMATS.items() if suffix in IMAGE_TYPES | SCAN_TYPES
    )
    return list(documents), list(images)


def _listed(names: list[str]) -> str:
    """Names as a sentence lists them: "PNG, JPEG and TIFF"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _kept(settings: Settings) -> str:
    kept = ""
    if code := settings.data_region:
        kept += (
            " Your documents, and everything read from them, are kept and processed in "
            f"{REGIONS[code]}."
        )
    if parse_scanner_url(settings.scanner_url):
        kept += " Every upload is scanned for malware before anything reads it."
    return kept


def html_response(request: Request, html: str) -> Response:
    """The filled page, with a tag of its own words so that a browser asking again is answered
    304, kept as the build's own pages are (PAGE_KEPT)."""
    tag = f'"{hashlib.sha256(html.encode()).hexdigest()[:24]}"'
    headers = {"Cache-Control": PAGE_KEPT, "ETag": tag}
    if tag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return HTMLResponse(html, headers=headers)


def listed_by_search_engines(path: str, settings: Settings) -> bool:
    """Whether a search engine may list the page at `path`."""
    return settings.search_engines and path.strip("/") in INDEXED


def robots(settings: Settings, app_url: str) -> str:
    """robots.txt: the API kept from crawlers and the sitemap named, or everything kept from
    them where search engines are not asked to list the deployment."""
    if not settings.search_engines:
        return "User-agent: *\nDisallow: /\n"
    return f"User-agent: *\nDisallow: /api/\n\nSitemap: {app_url}/sitemap.xml\n"


def sitemap(app_url: str) -> str:
    """The public pages as a sitemap, for a deployment that search engines are asked to list."""
    urls = "".join(f"  <url><loc>{escape(f'{app_url}/{path}')}</loc></url>\n" for path in LISTED)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )
