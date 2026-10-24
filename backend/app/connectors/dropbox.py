"""Dropbox, for the connected folders (app/connectors/).

The app asks for the scopes its console names (`files.metadata.read`, `files.content.read`,
`account_info.read`: deploying.md, "Connected folders"), and `token_access_type=offline` for a
refresh token. Dropbox's API is calls of JSON over POST: a folder is read whole in one listing
(`files/list_folder` with `recursive`, continued by its cursor), whose last cursor is the mark
`files/list_folder/continue` asks from at the next sync, one request when nothing changed. A
file's version is its `content_hash`. A Paper document has no bytes to download and is exported
as Markdown; another file Dropbox will not download (a Google document kept there) cannot be read.
Dropbox names a folder by its id (`id:…`), which stays as the folder moves, and the paths of the
files below it from its own, which the listing's start reads.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorError,
    Download,
    Listing,
    Provider,
    RemoteFile,
    RemoteFolder,
    clean,
)

# What the app's console grants, which the consent then asks for whole.
SCOPES = ("files.metadata.read", "files.content.read", "account_info.read")
# Where browsing starts, the account's own Dropbox, which no id names.
ROOT = "root"
PAGE = 2000
NOT_DOWNLOADABLE = (
    "Dropbox does not let this kind of file be downloaded; export it as a file in Dropbox, "
    "and the next sync takes it."
)


class Dropbox(Provider):
    name = "dropbox"
    label = "Dropbox"

    @property
    def authorize_url(self) -> str:
        if self.base:
            return f"{self.base}/dropbox/oauth2/authorize"
        return "https://www.dropbox.com/oauth2/authorize"

    @property
    def token_url(self) -> str:
        return f"{self._api}/oauth2/token"

    @property
    def _api(self) -> str:
        return f"{self.base}/dropbox/api" if self.base else "https://api.dropboxapi.com"

    @property
    def _content(self) -> str:
        return f"{self.base}/dropbox/content" if self.base else "https://content.dropboxapi.com"

    def consent_params(self) -> dict[str, str]:
        return {"token_access_type": "offline"}

    def not_granted(self, answer: dict[str, Any]) -> str | None:
        granted = set(str(answer.get("scope") or "").split())
        # A token answer without scopes is one of an app made before Dropbox's scopes.
        if not granted or granted >= set(SCOPES[:2]):
            return None
        return (
            "Dropbox was connected without leave to read the files. Ask whoever runs Emudoc to "
            "give its Dropbox app the scopes files.metadata.read and files.content.read."
        )

    def revoke(self, http: httpx.Client, refresh_token: str) -> None:
        # Dropbox takes back a grant through an access token of it, and the refresh token goes
        # with it.
        token = self.access(http, refresh_token).token
        self._rpc(http, token, "auth/token/revoke", None)

    def later(self, answer: httpx.Response) -> bool:
        return _summary(answer).startswith("too_many")

    def missing(self, answer: httpx.Response) -> bool:
        summary = _summary(answer)
        return answer.status_code == 409 and "not_found" in summary

    def account(self, http: httpx.Client, token: str) -> str:
        found = self._rpc(http, token, "users/get_current_account", None) or {}
        name = (found.get("name") or {}).get("display_name")
        return str(found.get("email") or name or "")[:320]

    def tops(self, http: httpx.Client, token: str) -> list[RemoteFolder]:
        return [RemoteFolder(ROOT, "Dropbox")]

    def folders(self, http: httpx.Client, token: str, parent: str) -> list[RemoteFolder]:
        entries = self._entries(http, token, _path(parent), recursive=False)
        found = [
            RemoteFolder(entry["id"], entry.get("name") or "Untitled folder")
            for entry in entries
            if entry.get(".tag") == "folder" and entry.get("id")
        ]
        return sorted(found, key=lambda folder: folder.name.lower())

    def folder(self, http: httpx.Client, token: str, ref: str) -> RemoteFolder | None:
        if ref == ROOT:
            return RemoteFolder(ROOT, "Dropbox")
        try:
            found = self._rpc(http, token, "files/get_metadata", {"path": ref})
        except ConnectorError as error:
            if error.gone:
                return None
            raise
        if not isinstance(found, dict) or found.get(".tag") != "folder":
            return None
        return RemoteFolder(ref, found.get("name") or "Untitled folder")

    def changed(self, http: httpx.Client, token: str, folder: RemoteFolder, cursor: str) -> bool:
        try:
            found = self._rpc(http, token, "files/list_folder/continue", {"cursor": cursor})
        except ConnectorError as error:
            if error.transient:
                raise
            # A cursor Dropbox reset: the folder is read whole, which gives a new one.
            return True
        found = found or {}
        return bool(found.get("entries")) or bool(found.get("has_more"))

    def listing(self, http: httpx.Client, token: str, folder: RemoteFolder, limit: int) -> Listing:
        # Paths below the folder are cut from its own, as Dropbox names it now.
        if folder.ref == ROOT:
            depth = 0
        else:
            found = self._rpc(http, token, "files/get_metadata", {"path": folder.ref}) or {}
            depth = len(str(found.get("path_lower") or "").strip("/").split("/"))
        files: list[RemoteFile] = []
        answer = self._rpc(
            http,
            token,
            "files/list_folder",
            {"path": _path(folder.ref), "recursive": True, "limit": PAGE},
        )
        while True:
            answer = answer if isinstance(answer, dict) else {}
            for entry in answer.get("entries") or []:
                if entry.get(".tag") == "file" and entry.get("id"):
                    files.append(self._file(entry, depth))
            if not answer.get("has_more") or len(files) > limit:
                break
            answer = self._rpc(
                http, token, "files/list_folder/continue", {"cursor": answer.get("cursor")}
            )
        cursor = answer.get("cursor")
        return Listing(files, cursor if isinstance(cursor, str) else None)

    def download(self, http: httpx.Client, token: str, file: RemoteFile) -> Download:
        route, argument = json.loads(file.source)
        return Download(lambda: self._streamed(http, token, route, argument))

    def _file(self, entry: dict[str, Any], depth: int) -> RemoteFile:
        parts = str(entry.get("path_display") or entry.get("name") or "").strip("/").split("/")
        folders = tuple(clean(part) for part in parts[depth:-1])
        name = clean(entry.get("name") or parts[-1])
        ref = entry["id"]
        version = str(entry.get("content_hash") or entry.get("rev") or "")
        size = entry.get("size") if isinstance(entry.get("size"), int) else None
        if entry.get("is_downloadable", True):
            source = json.dumps(["files/download", {"path": ref}])
            return RemoteFile(ref, version, name, folders, size, source)
        options = (entry.get("export_info") or {}).get("export_options") or []
        if "markdown" in options:
            source = json.dumps(["files/export", {"path": ref, "export_format": "markdown"}])
            stem = name.rsplit(".", 1)[0] if name.lower().endswith(".paper") else name
            return RemoteFile(ref, version, f"{stem}.md", folders, None, source)
        return RemoteFile(ref, version, name, folders, size, "", NOT_DOWNLOADABLE)

    def _entries(
        self, http: httpx.Client, token: str, path: str, *, recursive: bool
    ) -> list[dict[str, Any]]:
        answer = self._rpc(
            http, token, "files/list_folder", {"path": path, "recursive": recursive, "limit": PAGE}
        )
        found: list[dict[str, Any]] = []
        while True:
            answer = answer if isinstance(answer, dict) else {}
            found += [entry for entry in answer.get("entries") or [] if isinstance(entry, dict)]
            if not answer.get("has_more"):
                return found
            answer = self._rpc(
                http, token, "files/list_folder/continue", {"cursor": answer.get("cursor")}
            )

    def _rpc(self, http: httpx.Client, token: str, route: str, argument: Any) -> Any:
        """A call of Dropbox's API, its argument the JSON body: `null` for a route that takes
        none, as Dropbox's own libraries send."""
        return self.call(
            http,
            "POST",
            f"{self._api}/2/{route}",
            token,
            content=json.dumps(argument).encode(),
            headers={"Content-Type": "application/json"},
        )

    @contextmanager
    def _streamed(
        self, http: httpx.Client, token: str, route: str, argument: dict[str, Any]
    ) -> Iterator[Iterator[bytes]]:
        # A content route takes its argument in a header, as JSON whose every character is
        # ASCII, since a header holds no other.
        headers = {"Dropbox-API-Arg": json.dumps(argument, ensure_ascii=True)}
        with self.streamed(
            http, f"{self._content}/2/{route}", token, method="POST", headers=headers
        ) as chunks:
            yield chunks


def _path(ref: str) -> str:
    """A folder as Dropbox's routes name it: the root as the empty path, any other by its id."""
    return "" if ref == ROOT else ref


def _summary(answer: httpx.Response) -> str:
    try:
        return str(answer.json().get("error_summary") or "")
    except (ValueError, AttributeError):
        return ""
