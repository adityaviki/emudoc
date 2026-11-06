"""The public site: the home page a visitor without a session finds at /, the plans at /pricing,
and what search engines are told (robots.txt, the sitemap, and which pages they may list).

The two pages are plain HTML that the frontend's build writes (`frontend/home.html` and
`frontend/pricing.html`, `vite build --mode site`), so that a search engine reads them whole
without running a script. What they say of the deployment is filled in here as they are served,
as the help's figures are (app/help/): each `$name` from the plans (app/entitlements.py), the
prices Stripe sells them at and the settings, and a part between `<!-- if flag -->` and
`<!-- end flag -->` is kept only once that feature's flag is on for every organisation
(app/flags.py), so that the site offers nothing a new organisation would not find.

While sign-up is closed (SIGNUP_OPEN=false, app/signups.py), as for a private beta, the pages
offer the way to ask for an account (BOOKING_URL's "Book a call") where they offer the sign-up,
by the parts marked `signup_open` and `signup_closed`, and the plans' page is put away: /pricing
sends a visitor to the home page, and no search engine is told of it.

A signed-in browser, or development's local account, gets the members' page at / as before;
the session cookie alone tells them apart, so that the page asks the database nothing. Search
engines are asked to list the public pages only where SEARCH_ENGINES says so: everywhere else
robots.txt turns them away and every page carries `X-Robots-Tag: noindex`, and on a deployment
that is listed, every page but the public ones still does (a member's page, a share link's).
"""

import hashlib
import html as markup
import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from string import Template
from xml.sax.saxutils import escape

import httpx
from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from app.auth import SESSION_COOKIE
from app.billing import sold, stripe_for
from app.config import REGIONS, Settings, parse_scanner_url
from app.entitlements import BY_THE_SEAT, CHARS_PER_PAGE, PLANS, listed, size_label
from app.flags import flag
from app.headers import PAGE_KEPT
from app.help import ARTICLES, formats
from app.payments import PaymentsError
from app.signups import booking

log = logging.getLogger(__name__)

HOME_PAGE = "home.html"
PRICING_PAGE = "pricing.html"
# The site's pages by the path they are served at, and the paths search engines may list beside
# them: the help, what's new, the API reference, the terms, the privacy policy and the
# subprocessors, all pages anyone reads without an account, and the ways in.
PAGES = {"": HOME_PAGE, "pricing": PRICING_PAGE}
LISTED = (
    "",
    "pricing",
    "help",
    *(f"help/{slug}" for slug, _ in ARTICLES),
    "whats-new",
    "api-reference",
    "terms",
    "privacy",
    "subprocessors",
)
INDEXED = frozenset([*LISTED, "login", "signup"])
NOT_LISTED = "noindex"
# How long the prices read from Stripe are kept, and how soon a failed read is tried again.
PRICES_KEPT = 3600.0
PRICES_AGAIN = 300.0
# A part of a page shown only while its feature's flag is on for every organisation, or while
# sign-up is open or closed (`SIGNUP`).
_FLAGGED = re.compile(r"[ \t]*<!-- if (\w+) -->\n?(.*?)[ \t]*<!-- end \1 -->\n?", re.S)
SIGNUP = {"signup_open": True, "signup_closed": False}
# The paths put away while sign-up is closed, and where they send a visitor instead.
CLOSED_AWAY = {"pricing": "/"}
# The currencies Stripe counts in whole units, and those in thousandths; the rest in hundredths.
_WHOLE = frozenset(
    {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv"}
    | {"xaf", "xof", "xpf"}
)
_THOUSANDTHS = frozenset({"bhd", "jod", "kwd", "omr", "tnd"})
_SYMBOLS = {"eur": "€", "usd": "$", "gbp": "£"}
_PERIODS = {"day": "a day", "week": "a week", "month": "a month", "year": "a year"}


@dataclass(frozen=True)
class Price:
    """A plan's price as the plans' page shows it: "€29" and "a month, plus tax"."""

    amount: str
    per: str


class Prices:
    """The prices of the plans on sale, read from Stripe at most once an hour by a process, so
    that a visitor's page waits on Stripe once in a while and never on every load; a read that
    failed is tried again after five minutes, the page meanwhile saying the price is shown
    before paying."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._read: tuple[float, tuple, dict[str, Price | None]] | None = None

    def get(self, settings: Settings, http: httpx.Client) -> dict[str, Price | None]:
        """Each plan on sale with its price, None for one that could not be read."""
        on_sale = sold(settings)
        key = tuple(on_sale.items())
        with self._lock:
            if self._read and self._read[1] == key and time.monotonic() < self._read[0]:
                return self._read[2]
            stripe = stripe_for(settings, http)
            found: dict[str, Price | None] = {}
            failed = False
            for plan, price_id in on_sale.items():
                try:
                    found[plan] = price_of(stripe.price(price_id), plan) if stripe else None
                except PaymentsError as error:
                    log.warning("The price of the %s plan could not be read: %s", plan, error)
                    found[plan], failed = None, True
            kept = PRICES_AGAIN if failed else PRICES_KEPT
            self._read = (time.monotonic() + kept, key, found)
            return found


def price_of(price: Mapping, plan: str) -> Price | None:
    """How the plans' page words a Stripe price: None for one it cannot word in a line, a price
    by tiers or chosen by the buyer."""
    amount, currency = price.get("unit_amount"), str(price.get("currency") or "").lower()
    recurring = price.get("recurring")
    if not isinstance(amount, int) or not currency or not isinstance(recurring, Mapping):
        return None
    every = recurring.get("interval_count") or 1
    interval = str(recurring.get("interval") or "")
    if interval not in _PERIODS:
        return None
    period = _PERIODS[interval] if every == 1 else f"every {every} {interval}s"
    per = f"a seat {period}" if plan in BY_THE_SEAT else period
    tax = {"exclusive": ", plus tax", "inclusive": ", tax included"}.get(
        str(price.get("tax_behavior")), ""
    )
    return Price(_money(amount, currency), per + tax)


def _money(amount: int, currency: str) -> str:
    """An amount in the currency's smallest unit as a price is written: "€29", "€29.50",
    "CHF 1,200"."""
    places = 0 if currency in _WHOLE else 3 if currency in _THOUSANDTHS else 2
    whole, part = divmod(amount, 10**places) if places else (amount, 0)
    figure = f"{whole:,}" if not part else f"{whole:,}.{part:0{places}d}"
    symbol = _SYMBOLS.get(currency)
    return f"{symbol}{figure}" if symbol else f"{currency.upper()} {figure}"


def page_at(path: str, request: Request, settings: Settings) -> str | None:
    """The site's page that `path` shows this request, or None for the members' page: / to a
    visitor without a session, /pricing to anyone while sign-up is open."""
    path = path.strip("/")
    if path == "" and (settings.local_account or request.cookies.get(SESSION_COOKIE)):
        return None
    if moved(path, settings):
        return None
    return PAGES.get(path)


def moved(path: str, settings: Settings) -> str | None:
    """Where a page put away while sign-up is closed sends a visitor instead, or None."""
    return None if settings.signup_open else CLOSED_AWAY.get(path.strip("/"))


def fill(
    html: str,
    settings: Settings,
    app_url: str,
    prices: Mapping[str, Price | None] | None = None,
) -> str:
    """The page with its flagged parts kept or left out and every `$name` filled in; the prices
    only for a page that shows them."""
    shown = _FLAGGED.sub(lambda part: part[2] if _shown(part[1], settings) else "", html)
    return Template(shown).substitute(values(settings, app_url, prices))


def _shown(name: str, settings: Settings) -> bool:
    """Whether a page's part marked `name` is kept: one about sign-up by SIGNUP_OPEN, one about
    a feature once its flag is on for every organisation."""
    if name in SIGNUP:
        return settings.signup_open == SIGNUP[name]
    return flag(name).share == 100


def values(
    settings: Settings, app_url: str, prices: Mapping[str, Price | None] | None = None
) -> dict[str, str]:
    """What the site's pages say of this deployment, by placeholder."""
    support = settings.support_address
    on_sale = sold(settings)
    book_url, book_label = booking(settings)
    found = {
        "app_url": app_url,
        "year": str(datetime.now(UTC).year),
        "operator": settings.operator_name or "Emudoc",
        "formats": formats(),
        "api_plans": listed([name.title() for name, plan in PLANS.items() if plan.api_access]),
        "kept": _kept(settings),
        "sample": (
            " A new organisation starts with a sample project already read: a supplier's "
            "contract, its invoices, the price list and the delivery log, with questions to ask "
            "about them."
            if settings.sample_project
            else ""
        ),
        "chars_per_page": f"{CHARS_PER_PAGE:,}",
        "contact": f"mailto:{support}" if support else "/help/support",
        "book": markup.escape(book_url),
        "book_label": book_label,
        "trial": (
            f" {listed([name.title() for name in on_sale])} start"
            f"{'s' if len(on_sale) == 1 else ''} with a {settings.stripe_trial_days}-day trial."
            if on_sale and settings.stripe_trial_days
            else ""
        ),
        "how_to_buy": (
            "Every organisation starts on Free. An owner then chooses a plan, and the seats of "
            "Team, on the organisation's page, and changes or cancels it there."
            if on_sale
            else "Every organisation starts on Free. Write to us for a larger plan, which the "
            "people who run Emudoc set for it."
        ),
        "packs": (
            " An owner can buy a pack of pages or questions for the rest of a busy month."
            if settings.packs
            else ""
        ),
    }
    for name, plan in PLANS.items():
        seats = name in BY_THE_SEAT
        found |= {
            f"{name}_projects": _count(plan.projects),
            f"{name}_projects_words": _counted(plan.projects, "project"),
            f"{name}_documents": _count(plan.documents),
            f"{name}_storage_bytes": (
                "No limit" if plan.storage_bytes is None else size_label(plan.storage_bytes)
            ),
            f"{name}_pages_per_month": _count(plan.pages_per_month),
            f"{name}_questions_per_month": _count(plan.questions_per_month),
            f"{name}_members": "By the seat" if seats else _count(plan.members),
            f"{name}_members_words": (
                "Members by the seat" if seats else _counted(plan.members, "member")
            ),
            f"{name}_api_access": _yes(plan.api_access),
            f"{name}_share_links": _yes(plan.share_links),
            f"{name}_question_sets": _yes(plan.question_sets),
            f"{name}_connectors": _yes(plan.connectors),
            f"{name}_retention_days": _days(plan.retention_days, "Until deleted"),
            f"{name}_audit_days": _days(plan.audit_days, "For good"),
        }
    if prices is not None:
        for name in ("pro", "team"):
            price = prices.get(name)
            if price is not None:
                amount, per = price.amount, price.per
            elif name in on_sale:
                amount, per = "Paid", "the price is shown before you pay"
            else:
                amount, per = "On request", "write to us for it"
            found |= {f"{name}_price": amount, f"{name}_per": per}
    return found


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


def _count(value: int | None) -> str:
    return "No limit" if value is None else f"{value:,}"


def _counted(value: int | None, noun: str) -> str:
    if value is None:
        return f"Unlimited {noun}s"
    return f"{value:,} {noun}{'' if value == 1 else 's'}"


def _yes(allowed: bool) -> str:
    return "Yes" if allowed else "No"


def _days(value: int | None, forever: str) -> str:
    return forever if value is None else f"{value:,} days"


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
    path = path.strip("/")
    return settings.search_engines and path in INDEXED and not moved(path, settings)


def robots(settings: Settings, app_url: str) -> str:
    """robots.txt: the API kept from crawlers and the sitemap named, or everything kept from
    them where search engines are not asked to list the deployment."""
    if not settings.search_engines:
        return "User-agent: *\nDisallow: /\n"
    return f"User-agent: *\nDisallow: /api/\n\nSitemap: {app_url}/sitemap.xml\n"


def sitemap(app_url: str, settings: Settings) -> str:
    """The public pages as a sitemap, for a deployment that search engines are asked to list."""
    urls = "".join(
        f"  <url><loc>{escape(f'{app_url}/{path}')}</loc></url>\n"
        for path in LISTED
        if not moved(path, settings)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )
