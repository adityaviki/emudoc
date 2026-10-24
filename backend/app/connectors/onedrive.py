"""OneDrive and SharePoint, through Microsoft Graph, for the connected folders (app/connectors/).

The consent is Microsoft's identity platform's (`ONEDRIVE_TENANT`, "common" for work, school and
personal accounts alike), asking for `Files.Read.All`, every file the member may read, and
`Sites.Read.All`, the SharePoint sites they follow, with `offline_access` for a refresh token,
which Microsoft gives anew with each access token, so the newest is kept. A folder is named by
its drive and its item (`<drive id>/<item id>`), since a SharePoint site's document library is a
drive of its own. Browsing starts at the member's OneDrive and the libraries of the sites they
follow. A folder is read whole, a request per folder below it (`children`); a file's version is
its `cTag`, which changes with its content alone. Between syncs the drive's delta, asked from the
link the last listing took (`root/delta?token=latest`), says whether anything changed in the
drive: Microsoft's delta of a folder below the root is OneDrive's alone, so the drive's is asked.
A OneNote notebook is a package of many files and cannot be read; a file's bytes are where Graph
redirects its `content` to, which is asked without the token, as Graph's own link carries leave.
"""

from typing import Any
from urllib.parse import quote

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

SCOPE = "offline_access User.Read Files.Read.All Sites.Read.All"
SELECT = "id,name,folder,file,package,size,cTag,parentReference"
PAGE = 999
# How many followed sites browsing offers beside the member's own OneDrive.
SITES = 20
PACKAGE = "Microsoft packs this item of many files (a OneNote notebook), which cannot be read."


class OneDrive(Provider):
    name = "onedrive"
    label = "OneDrive"
    scope = SCOPE

    def __init__(self, client_id: str, client_secret: str, base: str = "", tenant: str = "common"):
        super().__init__(client_id, client_secret, base)
        self.tenant = tenant or "common"

    @property
    def _login(self) -> str:
        root = f"{self.base}/onedrive/login" if self.base else "https://login.microsoftonline.com"
        return f"{root}/{quote(self.tenant)}/oauth2/v2.0"

    @property
    def authorize_url(self) -> str:
        return f"{self._login}/authorize"

    @property
    def token_url(self) -> str:
        return f"{self._login}/token"

    @property
    def _graph(self) -> str:
        return (
            f"{self.base}/onedrive/graph/v1.0" if self.base else "https://graph.microsoft.com/v1.0"
        )

    def not_granted(self, answer: dict[str, Any]) -> str | None:
        granted = {
            scope.rsplit("/", 1)[-1].lower() for scope in str(answer.get("scope") or "").split()
        }
        if "files.read.all" in granted:
            return None
        return (
            "OneDrive was connected without leave to read the files. Connect again, and accept "
            "what Emudoc asks for; an organisation's administrator may have to allow it first."
        )

    def account(self, http: httpx.Client, token: str) -> str:
        found = (
            self.call(
                http,
                "GET",
                f"{self._graph}/me",
                token,
                params={"$select": "mail,userPrincipalName,displayName"},
            )
            or {}
        )
        return str(
            found.get("mail") or found.get("userPrincipalName") or found.get("displayName") or ""
        )[:320]

    def tops(self, http: httpx.Client, token: str) -> list[RemoteFolder]:
        own = (
            self.call(
                http, "GET", f"{self._graph}/me/drive/root", token, params={"$select": SELECT}
            )
            or {}
        )
        found = [RemoteFolder(_ref(own), "My files", drive=_drive(own))]
        try:
            sites = (
                self.call(
                    http,
                    "GET",
                    f"{self._graph}/me/followedSites",
                    token,
                    params={"$select": "id,displayName"},
                )
                or {}
            )
        except ConnectorError as error:
            if error.transient:
                raise
            # A personal account follows no sites, and some directories let it be asked of none.
            sites = {}
        for site in (sites.get("value") or [])[:SITES]:
            try:
                library = (
                    self.call(
                        http,
                        "GET",
                        f"{self._graph}/sites/{site['id']}/drive/root",
                        token,
                        params={"$select": SELECT},
                    )
                    or {}
                )
            except ConnectorError as error:
                if error.transient:
                    raise
                continue
            found.append(
                RemoteFolder(
                    _ref(library),
                    site.get("displayName") or "SharePoint site",
                    drive=_drive(library),
                )
            )
        return found

    def folders(self, http: httpx.Client, token: str, parent: str) -> list[RemoteFolder]:
        drive, item = _parts(parent)
        found = [
            RemoteFolder(
                f"{drive}/{child['id']}", child.get("name") or "Untitled folder", drive=drive
            )
            for child in self._children(http, token, drive, item)
            if "folder" in child
        ]
        return sorted(found, key=lambda folder: folder.name.lower())

    def folder(self, http: httpx.Client, token: str, ref: str) -> RemoteFolder | None:
        drive, item = _parts(ref)
        try:
            found = self.call(
                http,
                "GET",
                f"{self._graph}/drives/{drive}/items/{item}",
                token,
                params={"$select": SELECT + ",deleted,root"},
            )
        except ConnectorError as error:
            if error.gone:
                return None
            raise
        if not isinstance(found, dict) or "folder" not in found or found.get("deleted"):
            return None
        name = found.get("name") or "Untitled folder"
        if "root" in found:
            # Graph names every drive's top "root"; the drive's own name says which it is
            # ("OneDrive", a SharePoint library's "Documents").
            whole = self.call(
                http, "GET", f"{self._graph}/drives/{drive}", token, params={"$select": "name"}
            )
            name = (whole or {}).get("name") or "OneDrive"
        return RemoteFolder(ref, name, drive=drive)

    def changed(self, http: httpx.Client, token: str, folder: RemoteFolder, cursor: str) -> bool:
        try:
            found = self.call(http, "GET", cursor, token)
        except ConnectorError as error:
            if error.transient:
                raise
            # A link Graph no longer takes (410, resync): the folder is read whole.
            return True
        found = found or {}
        return bool(found.get("value")) or bool(found.get("@odata.nextLink"))

    def listing(self, http: httpx.Client, token: str, folder: RemoteFolder, limit: int) -> Listing:
        drive, item = _parts(folder.ref)
        # Taken before the folder is read, so that a change while it is read is seen next time.
        latest = (
            self.call(
                http,
                "GET",
                f"{self._graph}/drives/{drive}/root/delta",
                token,
                params={"token": "latest"},
            )
            or {}
        )
        cursor = latest.get("@odata.deltaLink")
        files: list[RemoteFile] = []
        waiting: list[tuple[str, tuple[str, ...]]] = [(item, ())]
        while waiting and len(files) <= limit:
            parent, folders = waiting.pop(0)
            for child in self._children(http, token, drive, parent):
                name = clean(child.get("name") or "")
                if "folder" in child:
                    waiting.append((child["id"], (*folders, name)))
                elif "package" in child:
                    files.append(
                        RemoteFile(
                            f"{drive}/{child['id']}",
                            str(child.get("cTag") or ""),
                            name,
                            folders,
                            None,
                            "",
                            PACKAGE,
                        )
                    )
                elif "file" in child:
                    files.append(self._file(drive, child, folders, name))
        return Listing(files, cursor if isinstance(cursor, str) else None)

    def download(self, http: httpx.Client, token: str, file: RemoteFile) -> Download:
        # Graph answers with a redirect to the bytes, whose link needs no token: httpx drops the
        # Authorization header on a redirect to another host.
        return Download(lambda: self.streamed(http, file.source, token, follow_redirects=True))

    def _file(
        self, drive: str, child: dict[str, Any], folders: tuple[str, ...], name: str
    ) -> RemoteFile:
        ref = f"{drive}/{child['id']}"
        size = child.get("size") if isinstance(child.get("size"), int) else None
        source = f"{self._graph}/drives/{drive}/items/{child['id']}/content"
        return RemoteFile(
            ref, str(child.get("cTag") or child.get("eTag") or ""), name, folders, size, source
        )

    def _children(
        self, http: httpx.Client, token: str, drive: str, item: str
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        url: str | None = f"{self._graph}/drives/{drive}/items/{item}/children"
        params: dict[str, Any] | None = {"$select": SELECT, "$top": PAGE}
        while url:
            answer = (
                self.call(http, "GET", url, token, **({"params": params} if params else {})) or {}
            )
            found += [
                child
                for child in answer.get("value") or []
                if isinstance(child, dict) and child.get("id")
            ]
            # The next page's link carries the query.
            url, params = answer.get("@odata.nextLink"), None
        return found


def _ref(item: dict[str, Any]) -> str:
    return f"{_drive(item)}/{item.get('id', '')}"


def _drive(item: dict[str, Any]) -> str:
    return str((item.get("parentReference") or {}).get("driveId") or "")


def _parts(ref: str) -> tuple[str, str]:
    """A folder's drive and its item, from its ref; a ref of no drive is refused as gone."""
    drive, _, item = ref.partition("/")
    if not drive or not item:
        raise ConnectorError("OneDrive holds no such folder.", gone=True)
    return quote(drive, safe="!"), quote(item, safe="!")
