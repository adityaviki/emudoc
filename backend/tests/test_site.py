"""The public site (app/site.py): the home page at / for a visitor without a session, the plans
at /pricing with their figures and prices, and what search engines are told."""

import re
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.auth import SESSION_COOKIE
from app.config import ROOT_DIR, get_settings
from app.entitlements import PLANS
from app.flags import FLAGS
from app.help import ARTICLES
from app.site import PRICES_AGAIN, SIGNUP, Price, fill, price_of
from tests.test_auth import signup

FRONTEND = ROOT_DIR / "frontend"
APP = "https://emudoc.test"
# Built at run time, so that nothing in the repository reads as a key.
STRIPE_KEY = "sk_" + "test_" + "site"


def built(name: str) -> str:
    """The site's page as `vite build --mode site` writes it: its header and footer put in
    (`siteParts` in frontend/vite.config.ts); the rest is the backend's to fill."""
    return re.sub(
        r"<!-- include ([\w-]+\.html) -->",
        lambda part: (FRONTEND / "src" / "site" / part[1]).read_text().strip(),
        (FRONTEND / name).read_text(),
    )


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A build of the members' page, the API reference and the site's two pages."""
    (tmp_path / "index.html").write_text("<!doctype html><title>Emudoc</title><p>members")
    (tmp_path / "reference.html").write_text("<!doctype html><title>Emudoc API reference</title>")
    for name in ("home.html", "pricing.html"):
        (tmp_path / name).write_text(built(name))
    monkeypatch.setattr("app.main.FRONTEND_DIST", tmp_path)
    return tmp_path


@pytest.fixture
def visitor(dist, signed_in_only, client):
    """The app as someone without an account reaches it."""
    return client


@pytest.fixture
def billing(monkeypatch, stripe):
    """Pro and Team on sale, at the prices Stripe holds for them, with a 14-day trial."""
    settings = get_settings()
    for name, value in (
        ("stripe_secret_key", STRIPE_KEY),
        ("stripe_prices", "pro=price_pro,team=price_team"),
        ("stripe_trial_days", 14),
        ("app_url", APP),
    ):
        monkeypatch.setattr(settings, name, value)
    stripe.prices["price_pro"] = {
        "id": "price_pro",
        "unit_amount": 2900,
        "currency": "eur",
        "recurring": {"interval": "month", "interval_count": 1},
        "tax_behavior": "exclusive",
    }
    stripe.prices["price_team"] = {
        "id": "price_team",
        "unit_amount": 1450,
        "currency": "eur",
        "recurring": {"interval": "month", "interval_count": 1},
        "tax_behavior": "unspecified",
    }
    return stripe


def text_of(html: str) -> str:
    """The words a page shows, as one line."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_a_visitor_finds_the_home_page_and_a_member_the_app(visitor, monkeypatch):
    home = visitor.get("/")
    assert home.status_code == 200
    assert "<h1" in home.text and "Ask your documents" in home.text
    assert home.headers["cache-control"] == "no-cache"
    # / is one of two pages by the cookie, which a cache keeps apart.
    assert home.headers["vary"] == "Cookie"
    # The plans' page is the same for everyone.
    assert "Plans and pricing" in visitor.get("/pricing").text

    signup(visitor)
    assert visitor.cookies.get(SESSION_COOKIE)
    member = visitor.get("/")
    assert member.text.endswith("<p>members") and member.headers["vary"] == "Cookie"
    assert "Plans and pricing" in visitor.get("/pricing").text

    # Development's local account has no session, and is a member all the same.
    visitor.cookies.clear()
    monkeypatch.setattr(get_settings(), "local_account", True)
    assert visitor.get("/").text.endswith("<p>members")


def test_the_pages_are_filled_in_and_the_files_are_reached_by_their_paths_alone(visitor):
    for path in ("/", "/pricing"):
        html = visitor.get(path).text
        assert not re.search(r"\$\w", html), path
        assert "<!-- include" not in html and "<!-- if" not in html, path
        assert f"© {datetime.now(UTC).year} Emudoc" in html, path
    # The files as the build wrote them, with their placeholders, are not served.
    for path in ("/home.html", "/pricing.html"):
        assert visitor.get(path).text.endswith("<p>members"), path


def test_without_a_site_build_the_members_page_is_served(dist, signed_in_only, client):
    (dist / "home.html").unlink()
    assert client.get("/").text.endswith("<p>members")


def test_a_page_asked_again_unchanged_is_a_304(visitor):
    first = visitor.get("/pricing")
    again = visitor.get("/pricing", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_the_home_page_names_what_the_deployment_does(visitor, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_url", APP)
    monkeypatch.setattr(settings, "operator_name", "Emudoc Ltd, 1 Example Street")
    monkeypatch.setattr(settings, "data_region", "eu")
    monkeypatch.setattr(settings, "sample_project", True)
    html = visitor.get("/").text
    shown = text_of(html)
    assert f'<link rel="canonical" href="{APP}/" />' in html
    assert f'content="{APP}/brand/og-image.png"' in html
    assert "Emudoc Ltd, 1 Example Street" in shown
    assert "kept and processed in the European Union" in shown
    assert "starts with a sample project" in shown
    assert "PDF, Word (.docx)" in shown
    assert "On Pro, Team and Enterprise, a versioned API" in shown

    monkeypatch.setattr(settings, "data_region", "")
    monkeypatch.setattr(settings, "sample_project", False)
    shown = text_of(visitor.get("/").text)
    assert "kept and processed in" not in shown and "sample project" not in shown


@pytest.mark.parametrize("name", ["home.html", "pricing.html"])
def test_a_feature_behind_a_flag_is_shown_once_every_organisation_has_it(name, monkeypatch):
    # The site offers nothing a new organisation would not find: a part about a feature behind
    # a flag is left out until the flag's share is everyone's.
    html = built(name)
    marked = re.findall(r"<!-- if (\w+) -->", html)
    assert marked == re.findall(r"<!-- end (\w+) -->", html)
    flagged = [each for each in marked if each not in SIGNUP]
    assert flagged and set(flagged) <= FLAGS.keys()
    settings = get_settings()
    prices = {"pro": None, "team": None} if name == "pricing.html" else None
    hidden = fill(html, settings, APP, prices)
    for each in set(flagged):
        monkeypatch.setitem(FLAGS, each, replace(FLAGS[each], share=100))
    shown = fill(html, settings, APP, prices)
    for words in ("Cross-checks compare", "Forward mail", "Share links", "Connected folders"):
        if words in html:
            assert words in shown and words not in hidden, words


def test_the_plans_page_shows_each_plans_figures(visitor):
    shown = text_of(visitor.get("/pricing").text)
    pro, team = PLANS["pro"], PLANS["team"]
    assert f"{pro.pages_per_month:,} pages a month" in shown
    assert f"{team.questions_per_month:,} questions a month" in shown
    assert f"Pages read a month {PLANS['free'].pages_per_month:,} {pro.pages_per_month:,}" in shown
    assert "Members 1 1 By the seat No limit" in shown
    assert "Chats kept after their last question 30 days 365 days Until deleted" in shown
    # Nothing is sold here, so the paid plans are the operator's to set.
    assert "Pro For one person's work, with the API. On request write to us for it" in shown
    assert "Write to us for a larger plan" in shown


def test_the_plans_page_shows_the_prices_stripe_sells_them_at(visitor, billing):
    shown = text_of(visitor.get("/pricing").text)
    assert "€29 a month, plus tax" in shown
    assert "€14.50 a seat a month" in shown
    assert "Pro and Team start with a 14-day trial." in shown
    assert "An owner then chooses a plan" in shown
    # Read once an hour by a process, not on every load.
    visitor.get("/pricing")
    assert [path for _, path, _, _ in billing.requests] == [
        "/v1/prices/price_pro",
        "/v1/prices/price_team",
    ]


def test_a_price_stripe_does_not_give_is_said_to_be_shown_before_paying(
    visitor, billing, monkeypatch, caplog
):
    billing.status = 503
    shown = text_of(visitor.get("/pricing").text)
    assert (
        "Pro For one person's work, with the API. Paid the price is shown before you pay" in shown
    )
    assert "The price of the pro plan could not be read" in caplog.text
    # Tried again after a while, not on every load.
    asked = len(billing.requests)
    visitor.get("/pricing")
    assert len(billing.requests) == asked
    billing.status = 200
    later = time.monotonic() + PRICES_AGAIN + 1
    monkeypatch.setattr("app.site.time.monotonic", lambda: later)
    assert "€29 a month, plus tax" in text_of(visitor.get("/pricing").text)


@pytest.mark.parametrize(
    ("price", "plan", "shown"),
    [
        (
            {"unit_amount": 2900, "currency": "eur", "recurring": {"interval": "month"}},
            "pro",
            Price("€29", "a month"),
        ),
        (
            {
                "unit_amount": 123456,
                "currency": "usd",
                "recurring": {"interval": "year"},
                "tax_behavior": "inclusive",
            },
            "pro",
            Price("$1,234.56", "a year, tax included"),
        ),
        (
            {
                "unit_amount": 3000,
                "currency": "jpy",
                "recurring": {"interval": "month", "interval_count": 3},
            },
            "team",
            Price("JPY 3,000", "a seat every 3 months"),
        ),
        (
            {"unit_amount": 1500, "currency": "chf", "recurring": {"interval": "month"}},
            "pro",
            Price("CHF 15", "a month"),
        ),
        # Tiers, or a price the buyer chooses, cannot be said in a line.
        ({"unit_amount": None, "currency": "eur", "recurring": {"interval": "month"}}, "pro", None),
        ({"unit_amount": 900, "currency": "eur", "recurring": None}, "pro", None),
    ],
)
def test_a_stripe_price_in_words(price, plan, shown):
    assert price_of(price, plan) == shown


def test_search_engines_are_turned_away_unless_asked_to_list_the_site(visitor):
    robots = visitor.get("/robots.txt")
    assert robots.status_code == 200 and robots.text == "User-agent: *\nDisallow: /\n"
    assert robots.headers["content-type"].startswith("text/plain")
    assert visitor.get("/sitemap.xml").status_code == 404
    for path in ("/", "/pricing", "/help/plans", "/projects/1"):
        assert visitor.get(path).headers["x-robots-tag"] == "noindex", path


def test_a_listed_deployment_names_its_public_pages_to_search_engines(visitor, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_engines", True)
    monkeypatch.setattr(get_settings(), "app_url", APP)
    robots = visitor.get("/robots.txt").text
    assert robots == f"User-agent: *\nDisallow: /api/\n\nSitemap: {APP}/sitemap.xml\n"
    sitemap = visitor.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    listed = re.findall(r"<loc>([^<]+)</loc>", sitemap.text)
    assert listed[:2] == [f"{APP}/", f"{APP}/pricing"]
    assert {f"{APP}/help/{slug}" for slug, _ in ARTICLES} <= set(listed)
    assert f"{APP}/api-reference" in listed and f"{APP}/login" not in listed
    # The public pages may be listed; a member's page, a share link's and any other may not.
    for path in ("/", "/pricing", "/help", "/help/plans", "/terms", "/login", "/api-reference"):
        assert "x-robots-tag" not in visitor.get(path).headers, path
    for path in ("/projects/1", "/shared", "/organization", "/nowhere"):
        assert visitor.get(path).headers["x-robots-tag"] == "noindex", path


def test_a_closed_sign_up_offers_a_call_and_puts_the_plans_away(visitor, monkeypatch):
    settings = get_settings()
    open_home = visitor.get("/").text
    assert 'href="/signup"' in open_home and "Private beta" not in text_of(open_home)
    assert 'href="/pricing"' in open_home

    monkeypatch.setattr(settings, "signup_open", False)
    monkeypatch.setattr(settings, "booking_url", "https://cal.com/emudoc/intro?a=1&b=2")
    home = visitor.get("/").text
    shown = text_of(home)
    assert "Private beta" in shown and "Book a call" in shown
    # The booking page's address is written as an attribute should be.
    assert 'href="https://cal.com/emudoc/intro?a=1&amp;b=2"' in home
    # Nothing leads to the sign-up or to the plans, which send a visitor home.
    assert 'href="/signup"' not in home and 'href="/pricing"' not in home
    assert not re.search(r"\$\w", home) and "<!-- if" not in home
    moved = visitor.get("/pricing", follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == "/"
    # A member's page links no plans either: the sign-in pages are told (app/signups.py).
    assert visitor.get("/api/auth/options").json()["signup_open"] is False

    # Without a booking page, the button writes to support.
    monkeypatch.setattr(settings, "booking_url", "")
    monkeypatch.setattr(settings, "support_email", "help@emudoc.test")
    home = visitor.get("/").text
    assert 'href="mailto:help@emudoc.test"' in home and "Write to us" in text_of(home)


def test_a_closed_sign_up_names_no_plans_to_search_engines(visitor, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_engines", True)
    monkeypatch.setattr(get_settings(), "app_url", APP)
    monkeypatch.setattr(get_settings(), "signup_open", False)
    listed = re.findall(r"<loc>([^<]+)</loc>", visitor.get("/sitemap.xml").text)
    assert f"{APP}/" in listed and f"{APP}/pricing" not in listed
    assert "x-robots-tag" not in visitor.get("/").headers
    assert visitor.get("/pricing", follow_redirects=False).headers["x-robots-tag"] == "noindex"
