"""Google Drive, for the connected folders (app/connectors/).

The consent asks for `drive.readonly`, which reads every file the member may read, and Google
then asks the deployment's OAuth client to be verified as a restricted scope's (deploying.md,
"Connected folders"). `drive.file` with Google's Picker would need less verifying, but it reads
only the files picked one by one, and its page is Google's script on the app's; a folder whose
files arrive after it was chosen is the point of a connected folder. Google asks the member
scope by scope, so the consent is checked for the one syncing needs.

A folder is read whole, a request per folder below it: `files.list` of the files whose parent it
is, outside the trash. Google's own documents have no bytes of their own and are exported, a
Doc as a Word document, a Sheet as an Excel workbook, Slides and Drawings as PDFs; forms, sites
and the like cannot be. Between syncs Google's change log says whether anything changed at all
(`changes.list` from the start page token the last listing took): one request when nothing did.
The log is the member's own, of everything they may read, or a shared drive's, so a change
anywhere in it reads the folder again.
"""

from typing import Any

import httpx

from app.connectors.base import (
    ConnectorError,
    Listing,
    Provider,
    RemoteFile,
    RemoteFolder,
    clean,
)

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER = "application/vnd.google-apps.folder"
# A Drive item of its own that points at another, which a folder's listing passes over: the
# file it points at is wherever it is.
SHORTCUT = "application/vnd.google-apps.shortcut"
NATIVE = "application/vnd.google-apps."
# Google's own documents, by type, as the file each is exported to and its type.
EXPORTS = {
    "application/vnd.google-apps.document": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "application/vnd.google-apps.spreadsheet": (
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "application/vnd.google-apps.presentation": (".pdf", "application/pdf"),
    "application/vnd.google-apps.drawing": (".pdf", "application/pdf"),
}
NOT_EXPORTED = (
    "Google Drive exports no file Emudoc reads from this kind of Google document; only Docs, "
    "Sheets, Slides and Drawings are."
)
# Where browsing starts besides My Drive and the shared drives, which is no folder of its own.
SHARED_WITH_ME = "shared-with-me"
PAGE = 1000
FILE_FIELDS = "id, name, mimeType, size, md5Checksum, modifiedTime"
# What Google answers a request it wants asked again later with, beside 429 and the 5xx.
LATER = ("rateLimitExceeded", "userRateLimitExceeded", "backendError")
# Why Google would not hand a file over, by its reason, in the members' words.
REFUSALS = {
    "exportSizeLimitExceeded": (
        "Google Drive exports a Google document of at most 10 MB, and this one is larger."
    ),
    "cannotDownloadFile": (
        "Google Drive does not let this file be downloaded: its owner may have turned downloads "
        "off."
    ),
    "cannotDownloadAbusiveFile": "Google Drive flagged this file as abusive and keeps it back.",
    "insufficientFilePermissions": (
        "Google Drive does not let the account that connected the folder read this file."
    ),
    "appNotAuthorizedToFile": (
        "Google Drive does not let the account that connected the folder read this file."
    ),
}


class GoogleDrive(Provider):
    name = "google"
    label = "Google Drive"
    scope = SCOPE

    @property
    def authorize_url(self) -> str:
        return (
            f"{self.base}/google/auth"
            if self.base
            else "https://accounts.google.com/o/oauth2/v2/auth"
        )

    @property
    def token_url(self) -> str:
        return f"{self.base}/google/token" if self.base else "https://oauth2.googleapis.com/token"

    @property
    def revoke_url(self) -> str:
        return f"{self.base}/google/revoke" if self.base else "https://oauth2.googleapis.com/revoke"

    @property
    def api(self) -> str:
        return (
            f"{self.base}/google/drive/v3" if self.base else "https://www.googleapis.com/drive/v3"
        )

    def consent_params(self) -> dict[str, str]:
        # A refresh token comes with an offline consent, and only from a consent screen shown:
        # one Google skips for a member who consented before gives none.
        return {"access_type": "offline", "prompt": "consent"}

    def not_granted(self, answer: dict[str, Any]) -> str | None:
        granted = str(answer.get("scope") or "").split()
        if SCOPE in granted:
            return None
        return (
            "Google Drive was connected without leave to see its files, which Google asks for "
            "on its own line. Connect again, and tick it."
        )

    def revoke(self, http: httpx.Client, refresh_token: str) -> None:
        answer = http.post(self.revoke_url, data={"token": refresh_token}, timeout=10)
        # A grant Google no longer holds is given back already.
        if answer.status_code not in (200, 400):
            answer.raise_for_status()

    def later(self, answer: httpx.Response) -> bool:
        return answer.status_code == 403 and _reason(answer) in LATER

    def refused(self, answer: httpx.Response) -> str | None:
        return REFUSALS.get(_reason(answer))

    def account(self, http: httpx.Client, token: str) -> str:
        found = self.call(
            http,
            "GET",
            f"{self.api}/about",
            token,
            params={"fields": "user(emailAddress, displayName)"},
        )
        user = (found or {}).get("user") or {}
        return str(user.get("emailAddress") or user.get("displayName") or "")[:320]

    def tops(self, http: httpx.Client, token: str) -> list[RemoteFolder]:
        drives = self._pages(
            http,
            token,
            f"{self.api}/drives",
            {"pageSize": 100, "fields": "nextPageToken, drives(id, name)"},
            "drives",
        )
        return [
            RemoteFolder("root", "My Drive"),
            *(
                RemoteFolder(drive["id"], drive.get("name") or "Shared drive", drive=drive["id"])
                for drive in drives
            ),
            RemoteFolder(SHARED_WITH_ME, "Shared with me", choosable=False),
        ]

    def folders(self, http: httpx.Client, token: str, parent: str) -> list[RemoteFolder]:
        if parent == SHARED_WITH_ME:
            query = f"sharedWithMe = true and mimeType = '{FOLDER}' and trashed = false"
        else:
            query = f"{_quoted(parent)} in parents and mimeType = '{FOLDER}' and trashed = false"
        # Browsing goes wherever the member's folders are, a shared drive's among them.
        found = self._files(
            http, token, query, "files(id, name, driveId)", order="name", every=True
        )
        return [
            RemoteFolder(
                item["id"], item.get("name") or "Untitled folder", drive=item.get("driveId") or ""
            )
            for item in found
        ]

    def folder(self, http: httpx.Client, token: str, ref: str) -> RemoteFolder | None:
        try:
            found = self.call(
                http,
                "GET",
                f"{self.api}/files/{ref}",
                token,
                params={
                    "fields": "id, name, mimeType, trashed, driveId",
                    "supportsAllDrives": "true",
                },
            )
        except ConnectorError as error:
            if error.gone:
                return None
            raise
        if not isinstance(found, dict) or found.get("trashed") or found.get("mimeType") != FOLDER:
            return None
        return RemoteFolder(
            ref, found.get("name") or "Untitled folder", drive=found.get("driveId") or ""
        )

    def changed(self, http: httpx.Client, token: str, folder: RemoteFolder, cursor: str) -> bool:
        params = {
            "pageToken": cursor,
            "pageSize": 1,
            "fields": "nextPageToken, newStartPageToken, changes(fileId)",
            "includeRemoved": "true",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            **({"driveId": folder.drive} if folder.drive else {}),
        }
        try:
            found = self.call(http, "GET", f"{self.api}/changes", token, params=params)
        except ConnectorError as error:
            if error.transient:
                raise
            # A mark Google no longer takes: the folder is read whole, which takes a new one.
            return True
        found = found or {}
        return bool(found.get("changes")) or bool(found.get("nextPageToken"))

    def listing(self, http: httpx.Client, token: str, folder: RemoteFolder, limit: int) -> Listing:
        # Taken before the folder is read, so that a change while it is read is seen next time.
        started = self.call(
            http,
            "GET",
            f"{self.api}/changes/startPageToken",
            token,
            params={
                "supportsAllDrives": "true",
                **({"driveId": folder.drive} if folder.drive else {}),
            },
        )
        cursor = (started or {}).get("startPageToken")
        files: list[RemoteFile] = []
        waiting: list[tuple[str, tuple[str, ...]]] = [(folder.ref, ())]
        while waiting and len(files) <= limit:
            parent, folders = waiting.pop(0)
            query = f"{_quoted(parent)} in parents and trashed = false"
            for item in self._files(
                http, token, query, f"files({FILE_FIELDS})", drive=folder.drive
            ):
                kind = item.get("mimeType") or ""
                name = clean(item.get("name") or "")
                if kind == FOLDER:
                    waiting.append((item["id"], (*folders, name)))
                elif kind != SHORTCUT:
                    files.append(self._file(item, folders, name))
        return Listing(files, cursor if isinstance(cursor, str) else None)

    def _file(self, item: dict[str, Any], folders: tuple[str, ...], name: str) -> RemoteFile:
        kind = item.get("mimeType") or ""
        ref = item["id"]
        size = int(item["size"]) if str(item.get("size") or "").isdigit() else None
        if kind.startswith(NATIVE):
            export = EXPORTS.get(kind)
            if export is None:
                return RemoteFile(
                    ref, str(item.get("modifiedTime") or ""), name, folders, None, "", NOT_EXPORTED
                )
            suffix, exported = export
            if not name.lower().endswith(suffix):
                name = f"{name}{suffix}"
            source = f"{self.api}/files/{ref}/export?{httpx.QueryParams({'mimeType': exported})}"
            # A Google document has no checksum; its last change stands for its bytes.
            return RemoteFile(
                ref, f"modified:{item.get('modifiedTime') or ''}", name, folders, None, source
            )
        source = f"{self.api}/files/{ref}?alt=media&supportsAllDrives=true"
        version = item.get("md5Checksum") or f"modified:{item.get('modifiedTime') or ''}"
        return RemoteFile(ref, str(version), name, folders, size, source)

    def _files(
        self,
        http: httpx.Client,
        token: str,
        query: str,
        fields: str,
        *,
        order: str | None = None,
        drive: str = "",
        every: bool = False,
    ) -> list[dict[str, Any]]:
        """The files a query finds: in the shared drive `drive`, in `every` drive the member
        reaches, or else in theirs, which holds what was shared with them too."""
        params: dict[str, Any] = {
            "q": query,
            "pageSize": PAGE,
            "fields": f"nextPageToken, {fields}",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if drive:
            params.update(corpora="drive", driveId=drive)
        elif every:
            params["corpora"] = "allDrives"
        if order:
            params["orderBy"] = order
        return self._pages(http, token, f"{self.api}/files", params, "files")

    def _pages(
        self, http: httpx.Client, token: str, url: str, params: dict[str, Any], key: str
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            answer = self.call(
                http, "GET", url, token, params={**params, **({"pageToken": page} if page else {})}
            )
            answer = answer if isinstance(answer, dict) else {}
            found += [
                item for item in answer.get(key) or [] if isinstance(item, dict) and item.get("id")
            ]
            page = answer.get("nextPageToken")
            if not page:
                return found


def _quoted(ref: str) -> str:
    """An id as a string of Drive's query language."""
    return "'" + ref.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _reason(answer: httpx.Response) -> str:
    try:
        errors = answer.json()["error"]["errors"]
        return str(errors[0].get("reason") or "")
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
