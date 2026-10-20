"""The headers every response carries for the browser, and the page they are written for."""

import re

from fastapi.testclient import TestClient

from app.config import ROOT_DIR, get_settings
from app.headers import HSTS, content_security_policy
from app.indexing.pipeline import Services, process_document
from app.legal import TERMS_VERSION
from app.main import create_app
from app.storage import SANDBOX, S3Storage
from tests.conftest import APP_HEADERS, FakeEmbedder, FakeLLM, FakeS3Client
from tests.test_api import create_project, upload
from tests.test_auth import sign_up_link

POLICY = (
    "default-src 'self'; img-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)
EXPECTED = {
    "content-security-policy": POLICY,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def carries_the_headers(response) -> None:
    for name, value in EXPECTED.items():
        assert response.headers[name] == value, (str(response.url), name)


def test_every_api_response_carries_the_headers(client, signed_in_only):
    # A route's own answer, a 404 and a refusal alike.
    for path, status in (("/api/health", 200), ("/api/no-such-route", 404), ("/api/projects", 401)):
        response = client.get(path)
        assert response.status_code == status, path
        carries_the_headers(response)
    # Over plain HTTP in development a browser would ignore HSTS; it is kept out of the noise.
    assert "strict-transport-security" not in client.get("/api/health").headers


def test_the_browser_keeps_nothing_the_api_answers(client, act_as, make_account):
    # Whoever uses the computer next would otherwise read a chat or a document's text from the
    # browser's cache without asking the app.
    act_as(make_account("ada@example.com"))
    for response in (
        client.get("/api/projects"),
        client.get("/api/auth/me"),
        client.get("/api/no-such-route"),
        client.post("/api/projects", json={"name": "Contracts"}),
    ):
        assert response.headers["cache-control"] == "no-store", str(response.url)
    # A route's own word stands.
    export = client.get("/api/auth/account/export")
    assert export.headers["cache-control"] == "no-store"

    # Signing out has the browser drop what it holds of the app's origin.
    left = client.post("/api/auth/logout")
    assert left.status_code == 204 and left.headers["clear-site-data"] == '"cache"'


def test_a_file_is_sandboxed_in_place_of_the_page_policy(client, services, sample_pdf):
    # An original, a page image and a thumbnail are files, never pages of the app: a browser
    # that shows one runs nothing in it, and the other headers stand as on every response.
    project = create_project(client)
    (document,) = upload(client, project["id"], sample_pdf)["documents"]
    process_document(document["id"], services)
    for path in ("file", "pages/1.png", "thumbnail.png"):
        response = client.get(f"/api/documents/{document['id']}/{path}")
        assert response.status_code == 200, path
        assert response.headers["content-security-policy"] == SANDBOX, path
        for name, value in EXPECTED.items():
            if name != "content-security-policy":
                assert response.headers[name] == value, (path, name)


def test_hsts_over_https_and_always_in_production(services, monkeypatch):
    with TestClient(create_app(services=services), base_url="https://testserver") as client:
        assert client.get("/api/health").headers["strict-transport-security"] == HSTS
    monkeypatch.setattr(get_settings(), "environment", "production")
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/health").headers["strict-transport-security"] == HSTS


def test_the_page_and_its_files_carry_the_headers(services, tmp_path, monkeypatch):
    # The frontend is served by the same app, so the policy reaches the page itself, the
    # theme script beside it and the client-routed paths that fall back to it.
    (tmp_path / "index.html").write_text("<!doctype html><title>Emudoc</title>")
    (tmp_path / "theme.js").write_text("")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index.js").write_text("")
    monkeypatch.setattr("app.main.FRONTEND_DIST", tmp_path)
    with TestClient(create_app(services=services), headers=APP_HEADERS) as client:
        for path in ("/", "/projects/1", "/theme.js", "/assets/index.js", "/login"):
            response = client.get(path)
            assert response.status_code == 200, path
            carries_the_headers(response)


def test_the_browser_keeps_the_built_files_and_asks_again_for_the_page(
    services, tmp_path, monkeypatch
):
    # A script or stylesheet of the build is named by its hash, so it is kept for a year
    # without a question; the page names them and keeps its name, so a browser asks each time
    # whether it changed and loads a release's page as soon as it is out.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Emudoc</title>")
    (dist / "theme.js").write_text("")
    (dist / "assets" / "index-Bk8N2tgi.js").write_text("")
    monkeypatch.setattr("app.main.FRONTEND_DIST", dist)
    with TestClient(create_app(services=services), headers=APP_HEADERS) as client:
        script = client.get("/assets/index-Bk8N2tgi.js")
        assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
        for path in ("/", "/index.html", "/projects/1", "/theme.js", "/assets"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["cache-control"] == "no-cache", path

        # Asked again, an unchanged file is a 304 without its body, the page as much as the
        # script, and a client-routed path as its page.
        for path in ("/assets/index-Bk8N2tgi.js", "/projects/1"):
            first = client.get(path)
            again = client.get(path, headers={"If-None-Match": first.headers["etag"]})
            assert again.status_code == 304 and again.content == b"", path
            assert again.headers["cache-control"] == first.headers["cache-control"], path

        # A script a page from before a release asks for is not there; nothing keeps the 404,
        # since a rollback brings it back.
        missing = client.get("/assets/index-0ld0ne.js")
        assert missing.status_code == 404
        assert "cache-control" not in missing.headers
        # The page's files are the build's alone, whatever the path says.
        (tmp_path / "secret.txt").write_text("not the page's")
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            assert "not the page's" not in client.get(path).text, path


def test_the_policy_allows_images_from_the_bucket(services, mailer):
    # Page images and thumbnails are <img> tags whose URL redirects to one signed for the
    # bucket, and the redirect's final origin must be allowed too.
    s3 = FakeS3Client("bucket")
    over_bucket = Services(FakeLLM(), FakeEmbedder(), S3Storage(s3, "bucket", "emudoc"))
    assert content_security_policy(services.storage) == POLICY
    assert content_security_policy(over_bucket.storage) == POLICY.replace(
        "img-src 'self'", "img-src 'self' https://bucket.s3.example"
    )
    with TestClient(create_app(services=over_bucket, mailer=mailer), headers=APP_HEADERS) as client:
        policy = client.get("/api/health").headers["content-security-policy"]
    assert "img-src 'self' https://bucket.s3.example;" in policy
    # The origin is learnt by signing once, not on every request.
    assert s3.calls.count("generate_presigned_url") == 1


def test_the_page_needs_no_inline_code():
    # The policy allows no inline script or style; the frontend's page must keep to that, or
    # the browser refuses the theme script and the page stays light for everyone.
    html = (ROOT_DIR / "frontend" / "index.html").read_text()
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert not body.strip(), "index.html has an inline script; move it to public/ as a file"
    assert not re.search(r"\bstyle=|\bon\w+=", html), "index.html has inline style or handlers"
    assert '<script src="/theme.js"></script>' in html
    assert (ROOT_DIR / "frontend" / "public" / "theme.js").is_file()


def test_no_other_origin_is_answered(client):
    # The frontend is served from the API's own origin, so the app answers no cross-origin
    # request: no response carries an Access-Control header, and a browser on another site,
    # which must ask before it sends the X-Requested-With header, gets no answer to the
    # preflight. With the session cookie's SameSite=Lax that is the defence against a request
    # forged from another site (refuse_cross_site in api/deps.py). A frontend that moves to
    # another origin needs CORSMiddleware naming that one origin, never *, with credentials
    # and the X-Requested-With, Content-Type and Last-Event-ID headers allowed, and the
    # cookie becomes SameSite=None; Secure, which leaves the header check as the whole defence.
    other = {"Origin": "https://other.example"}
    preflight = client.options(
        "/api/projects",
        headers={
            **other,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-requested-with",
        },
    )
    assert preflight.status_code == 405
    signup = client.post(
        "/api/auth/signup/finish",
        json={
            "token": sign_up_link(client),
            "password": "correct horse battery",
            "terms_version": TERMS_VERSION,
        },
        headers=other,
    )
    assert signup.status_code == 201, signup.text
    assert "; samesite=lax" in signup.headers["set-cookie"].lower()
    for response in (preflight, signup, client.get("/api/health", headers=other)):
        assert not [name for name in response.headers if name.lower().startswith("access-control-")]
        carries_the_headers(response)
