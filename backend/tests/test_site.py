"""The public site (app/site.py): the home page at / for a visitor without a session, the pages
put away for now, and what search engines are told."""

import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.auth import SESSION_COOKIE
from app.config import ROOT_DIR, get_settings
from app.flags import FLAGS
from app.site import SIGNUP, file_types, fill
from tests.test_auth import signup

FRONTEND = ROOT_DIR / "frontend"
APP = "https://emudoc.test"


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
    """A build of the members' page and the site's home page."""
    (tmp_path / "index.html").write_text("<!doctype html><title>Emudoc</title><p>members")
    (tmp_path / "home.html").write_text(built("home.html"))
    monkeypatch.setattr("app.main.FRONTEND_DIST", tmp_path)
    return tmp_path


@pytest.fixture
def visitor(dist, signed_in_only, client):
    """The app as someone without an account reaches it."""
    return client


def text_of(html: str) -> str:
    """The words a page shows, as one line."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_a_visitor_finds_the_home_page_and_a_member_the_app(visitor, monkeypatch):
    home = visitor.get("/")
    assert home.status_code == 200
    assert "<h1" in home.text and "Ask your documents." in text_of(home.text)
    assert home.headers["cache-control"] == "no-cache"
    # / is one of two pages by the cookie, which a cache keeps apart.
    assert home.headers["vary"] == "Cookie"

    signup(visitor)
    assert visitor.cookies.get(SESSION_COOKIE)
    member = visitor.get("/")
    assert member.text.endswith("<p>members") and member.headers["vary"] == "Cookie"

    # Development's local account has no session, and is a member all the same.
    visitor.cookies.clear()
    monkeypatch.setattr(get_settings(), "local_account", True)
    assert visitor.get("/").text.endswith("<p>members")


def test_the_page_is_filled_in_and_the_file_is_reached_by_its_path_alone(visitor):
    html = visitor.get("/").text
    assert not re.search(r"\$\w", html)
    assert "<!-- include" not in html and "<!-- if" not in html
    assert f"© {datetime.now(UTC).year} Emudoc" in html
    # The file as the build wrote it, with its placeholders, is not served.
    assert visitor.get("/home.html").text.endswith("<p>members")


def test_without_a_site_build_the_members_page_is_served(dist, signed_in_only, client):
    (dist / "home.html").unlink()
    assert client.get("/").text.endswith("<p>members")


def test_a_page_asked_again_unchanged_is_a_304(visitor):
    first = visitor.get("/")
    again = visitor.get("/", headers={"If-None-Match": first.headers["etag"]})
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
    # The file types an upload takes, read from the parsers.
    assert "<li>PDF</li><li>Word</li>" in html
    assert "Scans and photos too: PNG, JPEG," in shown

    monkeypatch.setattr(settings, "data_region", "")
    monkeypatch.setattr(settings, "sample_project", False)
    shown = text_of(visitor.get("/").text)
    assert "kept and processed in" not in shown and "sample project" not in shown


def test_every_file_type_the_parsers_take_is_named(monkeypatch):
    documents, images = file_types()
    assert documents[0] == "PDF" and "Excel" in documents and "HEIC" in images
    # Each named once, though two extensions share it.
    assert images.count("JPEG") == 1
    # A parser that learns a type the page does not name stops the page.
    monkeypatch.setattr("app.site.SUPPORTED_EXTENSIONS", {".pdf", ".odt"})
    with pytest.raises(RuntimeError, match=r"\.odt"):
        file_types()


def test_a_feature_behind_a_flag_is_shown_once_every_organisation_has_it(monkeypatch):
    # The site offers nothing a new organisation would not find: a part about a feature behind
    # a flag is left out until the flag's share is everyone's.
    html = built("home.html")
    marked = re.findall(r"<!-- if (\w+) -->", html)
    assert marked == re.findall(r"<!-- end (\w+) -->", html)
    flagged = [each for each in marked if each not in SIGNUP]
    assert flagged and set(flagged) <= FLAGS.keys()
    settings = get_settings()
    hidden = fill(html, settings, APP)
    for each in set(flagged):
        monkeypatch.setitem(FLAGS, each, replace(FLAGS[each], share=100))
    shown = fill(html, settings, APP)
    for words in ("Cross-checks compare", "Forward mail", "Share by a link", "Connect a folder"):
        assert words in shown and words not in hidden, words


@pytest.mark.parametrize("path", ["/pricing", "/help", "/help/plans", "/api-reference"])
def test_the_pages_put_away_send_everyone_home(visitor, path):
    # The plans, the help and the API reference are put away for now: an old link leads home,
    # a member's to the projects, and no search engine is told of it.
    moved = visitor.get(path, follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == "/"
    assert moved.headers["x-robots-tag"] == "noindex"
    signup(visitor)
    assert visitor.get(path, follow_redirects=False).headers["location"] == "/"


def test_the_home_page_offers_a_demo_and_no_sign_up(visitor, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "booking_url", "https://cal.com/emudoc/intro?a=1&b=2")
    for signup_open in (True, False):
        monkeypatch.setattr(settings, "signup_open", signup_open)
        home = visitor.get("/").text
        shown = text_of(home)
        assert "Book a demo" in shown and "Start free" not in shown
        # The booking page's address is written as an attribute should be.
        assert 'href="https://cal.com/emudoc/intro?a=1&amp;b=2"' in home
        # Nothing leads to the sign-up or to a page put away.
        for path in ("/signup", "/pricing", "/help", "/api-reference"):
            assert f'href="{path}' not in home, path
        assert 'href="/login"' in home
        assert ("Private beta" in shown) is not signup_open
        assert not re.search(r"\$\w", home) and "<!-- if" not in home

    # Without a booking page, the button writes to support.
    monkeypatch.setattr(settings, "booking_url", "")
    monkeypatch.setattr(settings, "support_email", "help@emudoc.test")
    home = visitor.get("/").text
    assert 'href="mailto:help@emudoc.test?subject=Access%20to%20Emudoc"' in home
    assert "Request access" in text_of(home)


def test_search_engines_are_turned_away_unless_asked_to_list_the_site(visitor):
    robots = visitor.get("/robots.txt")
    assert robots.status_code == 200 and robots.text == "User-agent: *\nDisallow: /\n"
    assert robots.headers["content-type"].startswith("text/plain")
    assert visitor.get("/sitemap.xml").status_code == 404
    for path in ("/", "/terms", "/projects/1"):
        assert visitor.get(path).headers["x-robots-tag"] == "noindex", path


def test_a_listed_deployment_names_its_public_pages_to_search_engines(visitor, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_engines", True)
    monkeypatch.setattr(get_settings(), "app_url", APP)
    robots = visitor.get("/robots.txt").text
    assert robots == f"User-agent: *\nDisallow: /api/\n\nSitemap: {APP}/sitemap.xml\n"
    sitemap = visitor.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    listed = re.findall(r"<loc>([^<]+)</loc>", sitemap.text)
    assert listed == [f"{APP}/{path}" for path in ("", "whats-new", "terms", "privacy")] + [
        f"{APP}/subprocessors"
    ]
    # The public pages may be listed; a member's page, a share link's and any other may not.
    for path in ("/", "/whats-new", "/terms", "/login", "/signup"):
        assert "x-robots-tag" not in visitor.get(path).headers, path
    for path in ("/projects/1", "/shared", "/organization", "/nowhere"):
        assert visitor.get(path).headers["x-robots-tag"] == "noindex", path
