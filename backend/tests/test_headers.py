"""The headers every response carries for the browser, and the page they are written for."""

import re

from fastapi.testclient import TestClient

from app.config import ROOT_DIR, get_settings
from app.headers import HSTS, content_security_policy
from app.indexing.pipeline import Services, process_document
from app.main import create_app
from app.storage import SANDBOX, S3Storage
from tests.conftest import APP_HEADERS, FakeEmbedder, FakeLLM, FakeS3Client
from tests.test_api import create_project, upload

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
        "/api/auth/signup",
        json={"email": "ada@example.com", "name": "Ada", "password": "correct horse battery"},
        headers=other,
    )
    assert signup.status_code == 201, signup.text
    assert "; samesite=lax" in signup.headers["set-cookie"].lower()
    for response in (preflight, signup, client.get("/api/health", headers=other)):
        assert not [name for name in response.headers if name.lower().startswith("access-control-")]
        carries_the_headers(response)
