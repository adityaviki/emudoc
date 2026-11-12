"""Connected folders (app/connectors/): a member connects Google Drive to a project through its
consent, chooses one of their folders, and the worker keeps the project in step with it: new
files become documents, a changed one is taken again with its earlier document in the trash, and
the document of a file no longer there goes to the trash too, but only when the folder was read
whole. Google's API is a fake behind httpx's MockTransport, reached at CONNECTOR_BASE_URL."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, update

from app.api.connectors import Handshake
from app.config import get_settings
from app.connectors import dropbox, google, onedrive, sync
from app.connectors.base import CONNECT_COOKIE
from app.connectors.sync import (
    FOLDER_GONE,
    MEMBER_GONE,
    TOO_MANY,
    prune_connectors,
    queue_due_syncs,
)
from app.db import engine
from app.encrypted import CONNECTOR_TOKENS, decrypt
from app.flags import CONNECTORS
from app.jobs import SYNC_CONNECTOR
from app.models import AuditEvent, Connector, ConnectorFile, Document, Job, Membership, Upload
from tests.test_api import create_project
from tests.test_automations import next_job
from tests.test_folders import make_folder
from tests.test_metrics import Counted
from tests.test_templates import turn_on
from tests.test_versions import versions

BASE = "https://drive.test"
FOLDER = google.FOLDER
DOC = "application/vnd.google-apps.document"
FORM = "application/vnd.google-apps.form"
SYNCS = "emudoc_connector_syncs_total"
GRAPH = f"{BASE}/onedrive/graph/v1.0"


class FakeDrive:
    """Google's consent, token endpoint and Drive API, over a tree of items it keeps: each has
    an id, a name, a type, its parents, its bytes, and whether it is in the trash. Each change
    moves the change log on; what it answers instead is set by path in `failing`."""

    def __init__(self):
        self.items: dict[str, dict] = {"root": self._item("root", "My Drive", FOLDER, [])}
        self.content: dict[str, bytes] = {}
        self.changes = 0
        self.next_id = 0
        # The refresh tokens it gave, and whether it still takes each.
        self.grants: dict[str, bool] = {}
        self.scope = google.SCOPE
        self.give_refresh_token = True
        self.revoked: list[str] = []
        self.failing: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._answer)

    def _item(self, ref, name, kind, parents, trashed=False):
        return {"id": ref, "name": name, "mimeType": kind, "parents": parents, "trashed": trashed}

    def folder(self, name: str, parent: str = "root") -> str:
        return self._add(name, FOLDER, parent, None)

    def file(self, name: str, content: bytes, parent: str, kind: str = "application/pdf") -> str:
        return self._add(name, kind, parent, content)

    def _add(self, name, kind, parent, content) -> str:
        self.next_id += 1
        ref = f"id{self.next_id}"
        self.items[ref] = self._item(ref, name, kind, [parent])
        if content is not None:
            self.content[ref] = content
        self.changes += 1
        return ref

    def change(self, ref: str, content: bytes) -> None:
        self.content[ref] = content
        self.changes += 1

    def trash(self, ref: str) -> None:
        self.items[ref]["trashed"] = True
        self.changes += 1

    def remove(self, ref: str) -> None:
        del self.items[ref]
        self.content.pop(ref, None)
        self.changes += 1

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if status := self.failing.get(path):
            return httpx.Response(status, json={"error": {"errors": [{"reason": "backendError"}]}})
        if path == "/google/token":
            return self._token(parse_qs(request.content.decode()))
        if path == "/google/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        assert request.headers["authorization"] == "Bearer access-now"
        api = path.removeprefix("/google/drive/v3")
        params = request.url.params
        if api == "/about":
            return httpx.Response(200, json={"user": {"emailAddress": "ann@gmail.example"}})
        if api == "/drives":
            return httpx.Response(200, json={"drives": [{"id": "drive1", "name": "Finance"}]})
        if api == "/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": str(self.changes)})
        if api == "/changes":
            since = int(params["pageToken"])
            changed = [{"fileId": "x"}] if self.changes > since else []
            return httpx.Response(
                200, json={"changes": changed, "newStartPageToken": str(self.changes)}
            )
        if api == "/files":
            return self._list(params)
        found = re.fullmatch(r"/files/([^/]+)(/export)?", api)
        assert found, path
        ref = found.group(1)
        item = self.items.get(ref)
        if item is None:
            return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
        if found.group(2):
            assert params["mimeType"] == google.EXPORTS[item["mimeType"]][1]
            return httpx.Response(200, content=self.content[ref])
        if params.get("alt") == "media":
            return httpx.Response(200, content=self.content[ref])
        return httpx.Response(200, json=item)

    def _token(self, form: dict[str, list[str]]) -> httpx.Response:
        assert form["client_id"] == ["drive-client"] and form["client_secret"] == ["drive-secret"]
        if form["grant_type"] == ["authorization_code"]:
            if form["code"] != ["the-code"] or not form.get("code_verifier"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = f"refresh-{len(self.grants) + 1}"
            self.grants[token] = True
            answer = {"access_token": "access-now", "scope": self.scope, "expires_in": 3599}
            if self.give_refresh_token:
                answer["refresh_token"] = token
            return httpx.Response(200, json=answer)
        if not self.grants.get(form["refresh_token"][0]):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "access-now", "expires_in": 3599})

    def _list(self, params) -> httpx.Response:
        query = params["q"]
        folders_only = f"mimeType = '{FOLDER}'" in query
        if "sharedWithMe" in query:
            found = []
        else:
            parent = re.search(r"'([^']+)' in parents", query).group(1)
            found = [
                item
                for item in self.items.values()
                if parent in item["parents"] and not item["trashed"]
            ]
        if folders_only:
            found = [item for item in found if item["mimeType"] == FOLDER]
        files = []
        for item in sorted(found, key=lambda item: item["name"]):
            shown = {"id": item["id"], "name": item["name"], "mimeType": item["mimeType"]}
            if item["id"] in self.content and not item["mimeType"].startswith(google.NATIVE):
                content = self.content[item["id"]]
                shown["md5Checksum"] = hashlib.md5(content).hexdigest()
                shown["size"] = str(len(content))
            shown["modifiedTime"] = f"2026-10-24T10:00:{self.changes:02d}Z"
            files.append(shown)
        return httpx.Response(200, json={"files": files})


class FakeDropbox:
    """Dropbox's consent, token endpoint and API over a tree of paths it keeps: each file and
    folder has an id, a display path and, for a file, its bytes; each change moves its cursor on.
    What it answers instead is set by route in `failing`."""

    def __init__(self):
        self.entries: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self.changes = 0
        self.next_id = 0
        self.grants: dict[str, bool] = {}
        self.revoked: list[str] = []
        self.failing: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._answer)

    def folder(self, path: str) -> str:
        return self._add(path, "folder", None)

    def file(self, path: str, content: bytes, downloadable: bool = True) -> str:
        ref = self._add(path, "file", content)
        self.entries[ref]["is_downloadable"] = downloadable
        if not downloadable:
            self.entries[ref]["export_info"] = {
                "export_as": "html",
                "export_options": ["html", "markdown"],
            }
        return ref

    def _add(self, path, tag, content) -> str:
        self.next_id += 1
        ref = f"id:{self.next_id}"
        self.entries[ref] = {
            ".tag": tag,
            "id": ref,
            "name": path.rsplit("/", 1)[-1],
            "path_display": path,
            "path_lower": path.lower(),
        }
        if content is not None:
            self.content[ref] = content
            self.entries[ref]["content_hash"] = hashlib.sha256(content).hexdigest()
            self.entries[ref]["size"] = len(content)
        self.changes += 1
        return ref

    def change(self, ref, content):
        self.content[ref] = content
        self.entries[ref]["content_hash"] = hashlib.sha256(content).hexdigest()
        self.changes += 1

    def remove(self, ref):
        del self.entries[ref]
        self.changes += 1

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if status := self.failing.get(path):
            return httpx.Response(status, json={"error_summary": "too_many_requests/"})
        if path == "/dropbox/api/oauth2/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["the-code"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                token = f"refresh-{len(self.grants) + 1}"
                self.grants[token] = True
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-now",
                        "refresh_token": token,
                        "scope": " ".join(dropbox.SCOPES),
                    },
                )
            if not self.grants.get(form["refresh_token"][0]):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "access-now", "expires_in": 14400})
        assert request.headers["authorization"] == "Bearer access-now"
        if path == "/dropbox/content/2/files/download":
            argument = json.loads(request.headers["dropbox-api-arg"])
            return httpx.Response(200, content=self.content[argument["path"]])
        if path == "/dropbox/content/2/files/export":
            argument = json.loads(request.headers["dropbox-api-arg"])
            assert argument["export_format"] == "markdown"
            return httpx.Response(200, content=self.content[argument["path"]])
        body = json.loads(request.content or b"null")
        route = path.removeprefix("/dropbox/api/2/")
        if route == "auth/token/revoke":
            self.revoked.append("access-now")
            return httpx.Response(200, json=None)
        if route == "users/get_current_account":
            return httpx.Response(
                200, json={"email": "ann@dropbox.example", "name": {"display_name": "Ann"}}
            )
        if route == "files/get_metadata":
            entry = self.entries.get(body["path"])
            if entry is None:
                return httpx.Response(409, json={"error_summary": "path/not_found/.."})
            return httpx.Response(200, json=entry)
        if route == "files/list_folder":
            under = "" if body["path"] == "" else self.entries[body["path"]]["path_lower"]
            found = [
                entry
                for entry in self.entries.values()
                if entry["path_lower"].startswith(f"{under}/")
                and (body["recursive"] or "/" not in entry["path_lower"][len(under) + 1 :])
            ]
            return httpx.Response(
                200, json={"entries": found, "cursor": str(self.changes), "has_more": False}
            )
        if route == "files/list_folder/continue":
            changed = self.changes > int(body["cursor"])
            return httpx.Response(
                200,
                json={
                    "entries": [{}] if changed else [],
                    "cursor": str(self.changes),
                    "has_more": False,
                },
            )
        raise AssertionError(path)


class FakeGraph:
    """Microsoft's identity platform and Graph over two drives it keeps, the member's OneDrive
    and a followed site's library: each item has an id, a name, its parent and, for a file, its
    bytes and its cTag. A file's content redirects to another host, as Graph's does; a refresh
    gives a new refresh token each time, as Microsoft's does."""

    DOWNLOADS = "https://files.graph.test"

    def __init__(self):
        self.items: dict[str, dict] = {
            "me-root": {
                "id": "me-root",
                "name": "root",
                "drive": "me",
                "parent": None,
                "folder": {},
                "root": {},
            },
            "site-root": {
                "id": "site-root",
                "name": "root",
                "drive": "site",
                "parent": None,
                "folder": {},
                "root": {},
            },
        }
        self.content: dict[str, bytes] = {}
        self.changes = 0
        self.next_id = 0
        self.grants: dict[str, bool] = {}
        self.failing: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._answer)

    def folder(self, name, parent="me-root") -> str:
        return self._add(name, parent, None)

    def file(self, name, content, parent) -> str:
        return self._add(name, parent, content)

    def _add(self, name, parent, content) -> str:
        self.next_id += 1
        ref = f"item{self.next_id}"
        item = {"id": ref, "name": name, "drive": self.items[parent]["drive"], "parent": parent}
        if content is None:
            item["folder"] = {"childCount": 0}
        else:
            self.content[ref] = content
            item["file"] = {}
            item["size"] = len(content)
            item["cTag"] = hashlib.md5(content).hexdigest()
        self.items[ref] = item
        self.changes += 1
        return ref

    def change(self, ref, content):
        self.content[ref] = content
        self.items[ref]["cTag"] = hashlib.md5(content).hexdigest()
        self.changes += 1

    def remove(self, ref):
        del self.items[ref]
        self.changes += 1

    def _shown(self, item):
        shown = {k: v for k, v in item.items() if k not in ("drive", "parent")}
        shown["parentReference"] = {"driveId": item["drive"]}
        return shown

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if status := self.failing.get(path):
            return httpx.Response(status, json={"error": {"code": "serviceNotAvailable"}})
        if request.url.host == "files.graph.test":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=self.content[path.strip("/")])
        if path == "/onedrive/login/common/oauth2/v2.0/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["the-code"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                token = f"refresh-{len(self.grants) + 1}"
                self.grants[token] = True
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-now",
                        "refresh_token": token,
                        "scope": onedrive.SCOPE,
                    },
                )
            given = form["refresh_token"][0]
            if not self.grants.get(given):
                return httpx.Response(400, json={"error": "invalid_grant"})
            renewed = f"refresh-{len(self.grants) + 1}"
            self.grants[renewed] = True
            return httpx.Response(
                200, json={"access_token": "access-now", "refresh_token": renewed}
            )
        assert request.headers["authorization"] == "Bearer access-now"
        api = path.removeprefix("/onedrive/graph/v1.0")
        if api == "/me":
            return httpx.Response(200, json={"mail": "ann@contoso.example"})
        if api == "/me/drive/root":
            return httpx.Response(200, json=self._shown(self.items["me-root"]))
        if api == "/me/followedSites":
            return httpx.Response(200, json={"value": [{"id": "site1", "displayName": "Finance"}]})
        if api == "/sites/site1/drive/root":
            return httpx.Response(200, json=self._shown(self.items["site-root"]))
        found = re.fullmatch(
            r"/drives/([^/]+)(?:/items/([^/]+)(/children|/content)?|/root/delta)?", api
        )
        assert found, path
        drive, ref, rest = found.groups()
        if api.endswith("/root/delta"):
            if request.url.params.get("token") == "latest":
                return httpx.Response(
                    200,
                    json={
                        "value": [],
                        "@odata.deltaLink": f"{GRAPH}/drives/{drive}/root/delta"
                        f"?since={self.changes}",
                    },
                )
            since = int(request.url.params["since"])
            return httpx.Response(
                200, json={"value": [{"id": "x"}] if self.changes > since else []}
            )
        if ref is None:
            return httpx.Response(200, json={"name": "OneDrive" if drive == "me" else "Documents"})
        item = self.items.get(ref)
        if item is None:
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        if rest == "/children":
            children = [
                self._shown(child) for child in self.items.values() if child["parent"] == ref
            ]
            return httpx.Response(200, json={"value": children})
        if rest == "/content":
            return httpx.Response(302, headers={"Location": f"{self.DOWNLOADS}/{ref}"})
        return httpx.Response(200, json=self._shown(item))


def serving(monkeypatch, client, fake, **settings) -> None:
    """The service set up by `settings`, the flag on, and the fake answering both the API's
    client and the worker's."""
    turn_on(monkeypatch, CONNECTORS)
    for name, value in {**settings, "connector_base_url": BASE}.items():
        monkeypatch.setattr(get_settings(), name, value)
    client.app.state.http = httpx.Client(transport=fake.transport)
    monkeypatch.setattr(sync, "http_client", lambda: httpx.Client(transport=fake.transport))


@pytest.fixture
def drive(monkeypatch, client):
    fake = FakeDrive()
    serving(
        monkeypatch,
        client,
        fake,
        google_drive_client_id="drive-client",
        google_drive_client_secret="drive-secret",
    )
    return fake


@pytest.fixture
def graph(monkeypatch, client):
    fake = FakeGraph()
    serving(
        monkeypatch,
        client,
        fake,
        onedrive_client_id="onedrive-client",
        onedrive_client_secret="onedrive-secret",
    )
    return fake


@pytest.fixture
def box(monkeypatch, client):
    fake = FakeDropbox()
    serving(
        monkeypatch,
        client,
        fake,
        dropbox_app_key="dropbox-key",
        dropbox_app_secret="dropbox-secret",
    )
    return fake


def connected(client, fake, project_id: int, provider: str = "google") -> dict:
    """Connect the service to the project as the page does, through the consent; the
    connector made, its folder not chosen yet."""
    started = client.post(f"/api/projects/{project_id}/connectors", json={"provider": provider})
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    back = client.get(
        "/api/connectors/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )
    assert back.status_code == 302
    assert back.headers["location"] == f"/projects/{project_id}?connectors="
    return listed(client, project_id)["connectors"][-1]


def listed(client, project_id: int) -> dict:
    found = client.get(f"/api/projects/{project_id}/connectors")
    assert found.status_code == 200, found.text
    return found.json()


def chosen(client, fake, project_id, ref, target=None, provider="google") -> dict:
    connector = connected(client, fake, project_id, provider)
    answer = client.put(
        f"/api/projects/{project_id}/connectors/{connector['id']}",
        json={"folder_ref": ref, "target_folder_id": target},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


def synced(services) -> Job | None:
    return next_job(services, SYNC_CONNECTOR)


def library(session, project_id: int, trashed: bool = False) -> list[str]:
    session.expire_all()
    found = session.scalars(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.deleted_at.is_not(None) if trashed else Document.deleted_at.is_(None),
        )
        .order_by(Document.filename)
    )
    return [document.filename for document in found]


def the_connector(session) -> Connector:
    session.expire_all()
    return session.scalars(select(Connector)).one()


def due(session) -> None:
    """Make every connector's next sync due now."""
    session.execute(update(Connector).values(next_sync_at=datetime.now(UTC) - timedelta(seconds=1)))
    session.commit()


def test_a_member_connects_drive_and_its_folder_becomes_the_project_s_documents(
    client, session, services, drive, sample_pdf, sample_docx
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    july = drive.folder("July", invoices)
    drive.file("inv-7.pdf", sample_pdf.read_bytes(), july)
    drive.file("Notes", sample_docx.read_bytes(), invoices, DOC)
    drive.file("Survey", b"", invoices, FORM)
    pages = drive.file("notes.pages", b"not read", invoices, "application/x-iwork-pages")
    target = make_folder(client, project["id"], "From Drive")

    started = client.post(f"/api/projects/{project['id']}/connectors", json={"provider": "google"})
    url = urlsplit(started.json()["url"])
    params = parse_qs(url.query)
    assert (url.netloc, url.path) == ("drive.test", "/google/auth")
    assert params["scope"] == [google.SCOPE]
    assert params["redirect_uri"] == ["http://testserver/api/connectors/callback"]
    assert (params["access_type"], params["prompt"]) == (["offline"], ["consent"])
    assert params["code_challenge_method"] == ["S256"]
    assert CONNECT_COOKIE in started.cookies

    connector = connected(client, drive, project["id"])
    assert (connector["status"], connector["account"], connector["mine"]) == (
        "choosing",
        "ann@gmail.example",
        True,
    )
    stored = the_connector(session)
    assert stored.refresh_token.startswith("v1.")
    assert decrypt(CONNECTOR_TOKENS, stored.id, stored.refresh_token) == "refresh-1"

    # Browsing starts at My Drive, the shared drives and what was shared, then goes down.
    base = f"/api/projects/{project['id']}/connectors/{connector['id']}/folders"
    tops = client.get(base).json()["folders"]
    assert [(f["name"], f["choosable"]) for f in tops] == [
        ("My Drive", True),
        ("Finance", True),
        ("Shared with me", False),
    ]
    assert [f["name"] for f in client.get(base, params={"parent": "root"}).json()["folders"]] == [
        "Invoices"
    ]

    chose = client.put(
        f"/api/projects/{project['id']}/connectors/{connector['id']}",
        json={"folder_ref": invoices, "target_folder_id": target["id"]},
    )
    assert chose.status_code == 200, chose.text
    assert (chose.json()["status"], chose.json()["folder"], chose.json()["target"]) == (
        "active",
        "Invoices",
        "From Drive",
    )
    assert chose.json()["syncing"] is True

    assert synced(services) is not None
    assert library(session, project["id"]) == ["inv-7.pdf", "Notes.docx"]
    folders = {f["id"]: f for f in client.get(f"/api/projects/{project['id']}/folders").json()}
    documents = session.scalars(select(Document).where(Document.project_id == project["id"])).all()
    placed = {d.filename: folders[d.folder_id]["name"] for d in documents}
    assert placed == {"inv-7.pdf": "July", "Notes.docx": "From Drive"}
    after = listed(client, project["id"])["connectors"][0]
    assert (after["kept"], after["problem"], after["syncing"]) == (2, "", False)
    rejected = {f["path"]: f["reason"] for f in after["rejected"]}
    assert rejected["Survey"] == google.NOT_EXPORTED
    assert rejected["notes.pages"].startswith("Unsupported type.")
    assert after["rejected_count"] == 2
    # A file whose name an upload rejects is never downloaded.
    assert f"/google/drive/v3/files/{pages}" not in drive.paths()

    # The documents are the member's upload, audited as from Google Drive.
    [upload] = session.scalars(select(Upload)).all()
    assert upload.user_id == stored.user_id
    rows = session.scalars(select(AuditEvent).where(AuditEvent.action == "document_uploaded")).all()
    assert {row.detail["by_connector"] for row in rows} == {"google"}
    actions = [
        row.action
        for row in session.scalars(
            select(AuditEvent).where(AuditEvent.action.like("connector_%")).order_by(AuditEvent.id)
        )
    ]
    assert actions == ["connector_created", "connector_changed"]


def test_a_sync_with_no_change_asks_the_change_log_alone(
    client, session, services, drive, sample_pdf
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    chosen(client, drive, project["id"], invoices)
    synced(services)
    unchanged = Counted((SYNCS, {"outcome": "unchanged"}))

    due(session)
    assert queue_due_syncs() == 1
    # Queued once: the sweep leaves a connector whose sync is queued alone.
    assert queue_due_syncs() == 0
    drive.requests.clear()
    synced(services)
    assert unchanged(SYNCS, outcome="unchanged") == 1
    assert "/google/drive/v3/files" not in drive.paths()
    assert the_connector(session).next_sync_at > datetime.now(UTC) + timedelta(minutes=14)


def test_a_changed_file_is_its_documents_new_version_and_a_removed_one_goes_to_the_trash(
    client, session, services, drive, sample_pdf, sample_docx, tmp_path
):
    from docx import Document as Word

    project = create_project(client)
    invoices = drive.folder("Invoices")
    first = drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    second = drive.file("policy.docx", sample_docx.read_bytes(), invoices)
    chosen(client, drive, project["id"], invoices)
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf", "policy.docx"]
    policy = session.scalar(select(Document).where(Document.filename == "policy.docx"))

    revised = Word()
    revised.add_paragraph("Book flights at least 21 days ahead.")
    revised.save(str(tmp_path / "revised.docx"))
    drive.change(second, (tmp_path / "revised.docx").read_bytes())
    drive.remove(first)
    due(session)
    queue_due_syncs()
    synced(services)
    # The new version is the document's, which keeps its id and its earlier file; the removed
    # file's document is in the trash.
    assert library(session, project["id"]) == ["policy.docx"]
    assert library(session, project["id"], trashed=True) == ["inv-7.pdf"]
    session.refresh(policy)
    assert policy.version == 2
    assert policy.sha256 == hashlib.sha256((tmp_path / "revised.docx").read_bytes()).hexdigest()
    assert [version["number"] for version in versions(client, policy.id)] == [2, 1]
    logged = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action.in_(("document_deleted", "document_version_uploaded")))
        .order_by(AuditEvent.id)
    )
    assert [(row.action, row.detail["filename"], row.detail["by_connector"]) for row in logged] == [
        ("document_version_uploaded", "policy.docx", "google"),
        ("document_deleted", "inv-7.pdf", "google"),
    ]


def test_a_changed_file_whose_document_is_being_indexed_waits_for_the_next_sync(
    client, session, services, drive
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    notes = drive.file("notes.txt", b"Call the supplier.", invoices, "text/plain")
    chosen(client, drive, project["id"], invoices)
    synced(services)
    document = session.scalar(select(Document))
    document.status = "processing"
    session.commit()

    drive.change(notes, b"Call the supplier on Monday.")
    due(session)
    queue_due_syncs()
    synced(services)
    session.refresh(document)
    assert document.version == 1
    # The mark stayed, so the service says the folder changed until the version is taken.
    document.status = "ready"
    session.commit()
    due(session)
    queue_due_syncs()
    synced(services)
    session.refresh(document)
    assert document.version == 2


def test_a_folder_of_a_shared_drive_is_read_from_that_drive(
    client, session, services, drive, sample_pdf
):
    project = create_project(client)
    drive.items["drive1"] = drive._item("drive1", "Finance", FOLDER, [])
    drive.items["drive1"]["driveId"] = "drive1"
    budgets = drive.folder("Budgets", "drive1")
    drive.items[budgets]["driveId"] = "drive1"
    drive.file("plan.pdf", sample_pdf.read_bytes(), budgets)
    chosen(client, drive, project["id"], budgets)
    drive.requests.clear()
    synced(services)
    assert library(session, project["id"]) == ["plan.pdf"]
    listed_in = {
        (request.url.params.get("corpora"), request.url.params.get("driveId"))
        for request in drive.requests
        if request.url.path in ("/google/drive/v3/files", "/google/drive/v3/changes/startPageToken")
    }
    assert listed_in == {("drive", "drive1"), (None, "drive1")}


def test_a_document_deleted_in_the_project_stays_deleted_while_its_file_is_as_it_was(
    client, session, services, drive, sample_pdf
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    chosen(client, drive, project["id"], invoices)
    synced(services)
    [document] = session.scalars(select(Document)).all()
    assert client.delete(f"/api/documents/{document.id}").status_code == 204

    drive.folder("Elsewhere")
    due(session)
    queue_due_syncs()
    synced(services)
    assert library(session, project["id"]) == []
    assert library(session, project["id"], trashed=True) == ["inv-7.pdf"]


def test_a_file_added_again_with_the_same_bytes_keeps_its_document(
    client, session, services, drive, sample_pdf
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    first = drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    chosen(client, drive, project["id"], invoices)
    synced(services)
    [document] = session.scalars(select(Document)).all()

    drive.remove(first)
    again = drive.file("inv-7 (copy).pdf", sample_pdf.read_bytes(), invoices)
    due(session)
    queue_due_syncs()
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf"]
    assert library(session, project["id"], trashed=True) == []
    [row] = session.scalars(select(ConnectorFile)).all()
    assert (row.remote_id, row.document_id, row.reason) == (again, document.id, "")


def test_nothing_goes_to_the_trash_unless_the_folder_was_read_whole(
    client, session, services, drive, sample_pdf
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    first = drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    connector = chosen(client, drive, project["id"], invoices)
    synced(services)

    # Google failing while the folder is listed leaves the document as it is.
    drive.remove(first)
    drive.failing["/google/drive/v3/files"] = 503
    due(session)
    queue_due_syncs()
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf"]
    shown = listed(client, project["id"])["connectors"][0]
    assert shown["problem"] == "Google Drive did not answer. The next sync tries again."

    # So does the folder itself gone, which the connector says.
    drive.failing.clear()
    drive.trash(invoices)
    due(session)
    queue_due_syncs()
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf"]
    shown = listed(client, project["id"])["connectors"][0]
    assert shown["problem"] == FOLDER_GONE.format(label="Google Drive")
    assert shown["id"] == connector["id"]


def test_a_grant_google_took_back_pauses_the_connector_until_it_is_connected_again(
    client, session, services, drive, sample_pdf
):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    connector = chosen(client, drive, project["id"], invoices)
    drive.grants = dict.fromkeys(drive.grants, False)
    synced(services)
    shown = listed(client, project["id"])["connectors"][0]
    assert shown["status"] == "paused"
    assert shown["problem"].startswith("Google Drive no longer lets Emudoc read this folder")
    assert library(session, project["id"]) == []
    refused = client.post(f"/api/projects/{project['id']}/connectors/{connector['id']}/sync")
    assert refused.status_code == 409

    started = client.post(
        f"/api/projects/{project['id']}/connectors",
        json={"provider": "google", "connector_id": connector["id"]},
    )
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    client.get(
        "/api/connectors/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )
    again = listed(client, project["id"])["connectors"]
    assert [(c["id"], c["status"], c["problem"], c["syncing"]) for c in again] == [
        (connector["id"], "active", "", True)
    ]
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf"]


def test_a_consent_that_cannot_be_used_connects_nothing(client, session, drive):
    project = create_project(client)

    def back(**params):
        started = client.post(
            f"/api/projects/{project['id']}/connectors", json={"provider": "google"}
        )
        state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
        answer = client.get(
            "/api/connectors/callback",
            params={"state": state, **params},
            follow_redirects=False,
        )
        return parse_qs(urlsplit(answer.headers["location"]).query).get("connect_error")

    assert back(error="access_denied") == ["cancelled"]
    assert back(code="another-code") == ["refused"]
    drive.scope = "openid"
    assert back(code="the-code") == ["scope"]
    drive.scope = google.SCOPE
    drive.give_refresh_token = False
    assert back(code="the-code") == ["lasting"]
    # A state that is not the handshake's, or a handshake started for someone else.
    stray = client.get(
        "/api/connectors/callback",
        params={"code": "the-code", "state": "forged"},
        follow_redirects=False,
    )
    assert stray.headers["location"] == "/?connect_error=expired"
    assert listed(client, project["id"])["connectors"] == []


def test_another_member_cannot_browse_or_choose_the_member_s_folders(
    client, session, drive, account, make_account, act_as
):
    project = create_project(client)
    connector = connected(client, drive, project["id"])
    act_as(make_account("max@example.com", account.organization, role="member"))
    base = f"/api/projects/{project['id']}/connectors/{connector['id']}"
    assert client.get(f"{base}/folders").status_code == 403
    chose = client.put(base, json={"folder_ref": "root", "target_folder_id": None})
    assert chose.status_code == 403
    [shown] = listed(client, project["id"])["connectors"]
    assert shown["mine"] is False


def test_a_folder_is_chosen_once(client, drive):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    connector = chosen(client, drive, project["id"], invoices)
    other = drive.folder("Other")
    again = client.put(
        f"/api/projects/{project['id']}/connectors/{connector['id']}",
        json={"folder_ref": other, "target_folder_id": None},
    )
    assert again.status_code == 409


def test_the_member_no_longer_one_who_may_change_the_project_pauses_it(
    client, session, services, drive, sample_pdf, account, make_account, act_as
):
    project = create_project(client)
    max_ = make_account("max@example.com", account.organization, role="member")
    act_as(max_)
    invoices = drive.folder("Invoices")
    drive.file("inv-7.pdf", sample_pdf.read_bytes(), invoices)
    chosen(client, drive, project["id"], invoices)
    session.execute(
        update(Membership).where(Membership.user_id == max_.user.id).values(role="viewer")
    )
    session.commit()
    synced(services)
    connector = the_connector(session)
    assert connector.status == "paused"
    assert connector.problem == MEMBER_GONE.format(name="Max")
    assert library(session, project["id"]) == []


def test_a_large_folder_is_taken_a_round_at_a_time(
    client, session, services, drive, sample_pdf, sample_docx, sample_csv, monkeypatch
):
    monkeypatch.setattr(sync, "ROUND_FILES", 2)
    project = create_project(client)
    invoices = drive.folder("Invoices")
    for name, path in (("a.pdf", sample_pdf), ("b.docx", sample_docx), ("c.csv", sample_csv)):
        drive.file(name, path.read_bytes(), invoices)
    chosen(client, drive, project["id"], invoices)
    synced(services)
    assert library(session, project["id"]) == ["a.pdf", "b.docx"]
    # The rest follows at once, in a sync of its own.
    assert the_connector(session).cursor is None
    assert synced(services) is not None
    assert library(session, project["id"]) == ["a.pdf", "b.docx", "c.csv"]
    assert the_connector(session).cursor == str(drive.changes)


def test_a_folder_past_the_files_a_connected_folder_may_hold_syncs_nothing(
    client, session, services, drive, sample_pdf, monkeypatch
):
    monkeypatch.setattr(sync, "MAX_FILES", 1)
    project = create_project(client)
    invoices = drive.folder("Invoices")
    drive.file("a.pdf", sample_pdf.read_bytes(), invoices)
    drive.file("b.pdf", b"%PDF-1.4 other", invoices)
    chosen(client, drive, project["id"], invoices)
    synced(services)
    assert library(session, project["id"]) == []
    assert the_connector(session).problem == TOO_MANY


def test_sync_now_and_disconnecting(client, session, services, drive, sample_pdf):
    project = create_project(client)
    invoices = drive.folder("Invoices")
    drive.file("a.pdf", sample_pdf.read_bytes(), invoices)
    connector = chosen(client, drive, project["id"], invoices)
    synced(services)
    base = f"/api/projects/{project['id']}/connectors/{connector['id']}"
    now = client.post(f"{base}/sync")
    assert (now.status_code, now.json()["syncing"]) == (202, True)
    client.post(f"{base}/sync")
    queued = select(Job).where(Job.kind == SYNC_CONNECTOR, Job.status == "queued")
    assert len(session.scalars(queued).all()) == 1

    assert client.delete(base).status_code == 204
    assert drive.revoked == ["refresh-1"]
    assert listed(client, project["id"])["connectors"] == []
    # Its documents stay in the library.
    assert library(session, project["id"]) == ["a.pdf"]


def test_a_connector_whose_folder_was_never_chosen_is_forgotten_after_a_day(client, session, drive):
    project = create_project(client)
    connected(client, drive, project["id"])
    with engine.begin() as connection:
        assert prune_connectors(connection) == 0
    session.execute(update(Connector).values(created_at=datetime.now(UTC) - timedelta(days=2)))
    session.commit()
    with engine.begin() as connection:
        assert prune_connectors(connection) == 1


def test_the_handshake_cookie_round_trips():
    handshake = Handshake("google", "s", "v", 1, 2, 3)
    assert Handshake.decode(handshake.encode()) == handshake
    assert Handshake.decode("not base64!") is None


def test_a_dropbox_folder_is_read_in_one_listing_and_asked_after_from_its_cursor(
    client, session, services, box, sample_pdf, tmp_path
):
    project = create_project(client)
    invoices = box.folder("/Invoices")
    july = box.folder("/Invoices/July")
    box.file("/Invoices/July/inv-7.pdf", sample_pdf.read_bytes())
    notes = box.file("/Invoices/Notes.md", b"# Notes\n\nCall the supplier.")
    box.file("/Invoices/Plan.paper", b"# Plan\n\nOrder in May.", downloadable=False)
    box.file("/Elsewhere.md", b"# Not in the folder")

    started = client.post(f"/api/projects/{project['id']}/connectors", json={"provider": "dropbox"})
    params = parse_qs(urlsplit(started.json()["url"]).query)
    assert (urlsplit(started.json()["url"]).path, params["token_access_type"]) == (
        "/dropbox/oauth2/authorize",
        ["offline"],
    )
    # Dropbox asks for the scopes its app names.
    assert "scope" not in params
    connector = connected(client, box, project["id"], "dropbox")
    assert (connector["label"], connector["account"]) == ("Dropbox", "ann@dropbox.example")
    base = f"/api/projects/{project['id']}/connectors/{connector['id']}/folders"
    assert [f["name"] for f in client.get(base).json()["folders"]] == ["Dropbox"]
    assert [f["ref"] for f in client.get(base, params={"parent": "root"}).json()["folders"]] == [
        invoices
    ]
    assert [f["ref"] for f in client.get(base, params={"parent": invoices}).json()["folders"]] == [
        july
    ]
    chose = client.put(
        f"/api/projects/{project['id']}/connectors/{connector['id']}",
        json={"folder_ref": invoices, "target_folder_id": None},
    )
    assert chose.status_code == 200, chose.text
    assert chose.json()["folder"] == "Invoices"
    synced(services)
    # A Paper document is exported as Markdown; the July folder is made for its file.
    assert library(session, project["id"]) == ["inv-7.pdf", "Notes.md", "Plan.md"]
    paths = {row.path for row in session.scalars(select(ConnectorFile))}
    assert paths == {"July/inv-7.pdf", "Notes.md", "Plan.md"}

    box.requests.clear()
    due(session)
    queue_due_syncs()
    synced(services)
    assert [r.url.path for r in box.requests][-1] == "/dropbox/api/2/files/list_folder/continue"

    box.change(notes, b"# Notes\n\nCall the supplier on Monday.")
    due(session)
    queue_due_syncs()
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf", "Notes.md", "Plan.md"]
    assert library(session, project["id"], trashed=True) == []
    assert session.scalar(select(Document.version).where(Document.filename == "Notes.md")) == 2


def test_a_dropbox_folder_gone_and_a_grant_taken_back_say_so(client, session, services, box):
    project = create_project(client)
    invoices = box.folder("/Invoices")
    box.file("/Invoices/Notes.md", b"# Notes")
    connector = chosen(client, box, project["id"], invoices, provider="dropbox")
    synced(services)
    box.remove(invoices)
    due(session)
    queue_due_syncs()
    synced(services)
    assert the_connector(session).problem == FOLDER_GONE.format(label="Dropbox")
    assert library(session, project["id"]) == ["Notes.md"]

    # Disconnecting gives the grant back through an access token of it.
    base = f"/api/projects/{project['id']}/connectors/{connector['id']}"
    assert client.delete(base).status_code == 204
    assert box.revoked == ["access-now"]


def test_a_onedrive_folder_is_read_by_its_children_and_asked_after_from_the_drive_s_delta(
    client, session, services, graph, sample_pdf
):
    project = create_project(client)
    invoices = graph.folder("Invoices")
    july = graph.folder("July", invoices)
    graph.file("inv-7.pdf", sample_pdf.read_bytes(), july)
    notes = graph.file("Notes.md", b"# Notes\n\nCall the supplier.", invoices)
    graph.items["book"] = {
        "id": "book",
        "name": "Meetings",
        "drive": "me",
        "parent": invoices,
        "package": {"type": "oneNote"},
    }

    started = client.post(
        f"/api/projects/{project['id']}/connectors", json={"provider": "onedrive"}
    )
    url = urlsplit(started.json()["url"])
    assert url.path == "/onedrive/login/common/oauth2/v2.0/authorize"
    assert parse_qs(url.query)["scope"] == [onedrive.SCOPE]
    connector = connected(client, graph, project["id"], "onedrive")
    assert connector["account"] == "ann@contoso.example"
    base = f"/api/projects/{project['id']}/connectors/{connector['id']}/folders"
    tops = client.get(base).json()["folders"]
    assert [(f["name"], f["ref"]) for f in tops] == [
        ("My files", "me/me-root"),
        ("Finance", "site/site-root"),
    ]
    assert [
        f["name"] for f in client.get(base, params={"parent": "me/me-root"}).json()["folders"]
    ] == ["Invoices"]
    chose = client.put(
        f"/api/projects/{project['id']}/connectors/{connector['id']}",
        json={"folder_ref": f"me/{invoices}", "target_folder_id": None},
    )
    assert chose.status_code == 200, chose.text
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf", "Notes.md"]
    rejected = listed(client, project["id"])["connectors"][0]["rejected"]
    assert rejected == [{"path": "Meetings", "reason": onedrive.PACKAGE}]
    # Microsoft gives a new refresh token with each access token, and the newest is kept.
    stored = the_connector(session)
    assert decrypt(CONNECTOR_TOKENS, stored.id, stored.refresh_token) == max(graph.grants)

    graph.requests.clear()
    due(session)
    queue_due_syncs()
    synced(services)
    assert "/onedrive/graph/v1.0/drives/me/root/delta" in [r.url.path for r in graph.requests]
    assert not any(r.url.path.endswith("/children") for r in graph.requests)

    graph.change(notes, b"# Notes\n\nCall the supplier on Monday.")
    due(session)
    queue_due_syncs()
    synced(services)
    assert library(session, project["id"]) == ["inv-7.pdf", "Notes.md"]
    assert library(session, project["id"], trashed=True) == []
    assert session.scalar(select(Document.version).where(Document.filename == "Notes.md")) == 2


def test_a_onedrive_root_chosen_is_named_by_its_drive(client, graph):
    project = create_project(client)
    connector = chosen(client, graph, project["id"], "site/site-root", provider="onedrive")
    assert connector["folder"] == "Documents"
