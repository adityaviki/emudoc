"""Where uploaded files and rendered pages are kept: a directory on this machine or an S3 bucket.

Objects are named by keys, never by paths, so that the data can move and one layout serves both
backends:

    orgs/{org}/projects/{project}/documents/{document}/original.pdf
    orgs/{org}/projects/{project}/documents/{document}/pages/{n}.png
    orgs/{org}/projects/{project}/documents/{document}/thumbnail.png

Everything a document owns sits under its prefix and every document of a project under the
project's, so deleting either is deleting a prefix. The database holds the original's key
(`documents.storage_key`) and the other keys derive from it (`Document.storage_prefix`).

The pages and the thumbnail are a cache: rendered from the original the first time they are
asked for, and removed again once they are older than PAGE_IMAGE_DAYS (`sweep_storage` in
app/maintenance.py), since a 500-page PDF would otherwise stay 500 pictures of a few megabytes
each for good. `is_rendered` tells those keys apart; a listing carries each object's age for it.
The same walk finds what a delete left behind when the storage failed after the rows were gone:
a prefix whose document or project has no row is removed, once it is old enough not to be an
upload still in flight.

The parsers open files by path, so `localize` gives the parse stage a local copy of an object
for as long as it needs one; the local storage hands out the file itself. Serving goes through
`response`, after the route has checked who asks: the local storage sends the file, a bucket
redirects to a URL signed for a few minutes, so a large original never streams through the app
and the bucket itself is never public. Either way the file is not a page of the app: it comes as
a download unless the route asks for it inline, and what the app sends itself carries a policy
that lets the browser run nothing in it (`SANDBOX`).

At rest, a bucket encrypts what it keeps by its own default encryption, which the app asks it
about at start and reports (`report_encryption`); it sends no encryption of its own with each
object, so the bucket's setting is the one place to choose the keys. A directory is as
encrypted as the disk under it, which only the operator can say (deploying.md, "Encryption").
"""

import logging
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from itertools import batched
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlsplit

from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError
from starlette.responses import FileResponse, RedirectResponse, Response

from app.config import Settings
from app.errors import AppError

log = logging.getLogger(__name__)

# How long a signed URL stays valid. The browser follows the redirect at once; the margin is
# for a download that starts late, and a redirect is cached for less than this (see `response`).
URL_LIFETIME = 15 * 60
# A bucket deletes up to a thousand objects per request.
DELETE_BATCH = 1000
# The prefix every key sits under, for a sweep that walks the whole storage.
ROOT_PREFIX = "orgs/"
_RENDERED = re.compile(r"orgs/\d+/projects/\d+/documents/\d+/(pages/\d+|thumbnail)\.png")
# The policy of a file the app sends from its own origin, in place of the page's. A browser that
# shows the file in a tab after all, a PDF in its viewer, gives it an origin of its own and lets
# it run no script, submit no form and open no window, so an uploaded file cannot act as the app.
# A signed URL leads to the bucket's origin, which holds nothing of the app's and needs none.
SANDBOX = "sandbox"


class StorageError(AppError):
    """The storage could not do what was asked, said in words the user or operator can act on."""


def organization_prefix(org_id: int) -> str:
    return f"{ROOT_PREFIX}{org_id}/"


def project_prefix(org_id: int, project_id: int) -> str:
    return f"{organization_prefix(org_id)}projects/{project_id}/"


def document_prefix(org_id: int, project_id: int, document_id: int) -> str:
    return f"{project_prefix(org_id, project_id)}documents/{document_id}/"


def original_key(org_id: int, project_id: int, document_id: int, suffix: str) -> str:
    """The key of an uploaded file; `suffix` is its lower-cased extension with the dot."""
    return f"{document_prefix(org_id, project_id, document_id)}original{suffix}"


def is_rendered(key: str) -> bool:
    """Whether the key names a rendered page or a thumbnail, which can be made again from the
    original, as opposed to the original itself."""
    return _RENDERED.fullmatch(key) is not None


@dataclass(frozen=True)
class StoredObject:
    """One object as a listing reports it; `modified_at` is when it was last written."""

    key: str
    size: int
    modified_at: datetime


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """A Content-Disposition that names the file, as a browser saves it; names outside ASCII go
    in the encoded form browsers read. A browser saves an attachment and shows an inline file in
    the tab, so only a file it can show without running anything, an image, is inline."""
    kind = "inline" if inline else "attachment"
    quoted = quote(filename)
    if quoted != filename:
        return f"{kind}; filename*=utf-8''{quoted}"
    return f'{kind}; filename="{filename}"'


def _folder(prefix: str) -> str:
    # `documents/1/` and `documents/10/` share the characters of `documents/1`, so a prefix
    # that is deleted or listed must name a folder, and a folder ends with the separator.
    if not prefix.endswith("/"):
        raise ValueError(f"{prefix!r} does not name a folder of keys: a prefix ends with '/'")
    return prefix


class Storage(ABC):
    """What the app asks of wherever its files are kept; one implementation per place."""

    @abstractmethod
    def check(self) -> None:
        """Make sure the storage can be used, or raise `StorageError` saying what to fix."""

    @abstractmethod
    def put(self, key: str, source: Path | bytes, content_type: str | None = None) -> None:
        """Store the bytes, or a copy of the local file, under the key, replacing what was there."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """The whole object; `FileNotFoundError` when there is none."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def localize(self, key: str) -> AbstractContextManager[Path]:
        """The object as a file on this machine for the duration of the block, for the parsers
        and renderers, which open files by path; `FileNotFoundError` when there is none."""

    @abstractmethod
    def presigned_url(
        self,
        key: str,
        *,
        content_type: str,
        filename: str | None = None,
        inline: bool = False,
        expires_in: int = URL_LIFETIME,
    ) -> str | None:
        """A URL that serves the object to whoever holds it until it expires, as `response`
        would, or None when the storage has no such thing and the app serves the object itself."""

    @property
    def origin(self) -> str | None:
        """The origin the signed URLs lead to, which the browser must be allowed to load images
        from (`content_security_policy` in app/headers.py); None when the app serves the files
        itself."""
        return None

    @property
    def region(self) -> str | None:
        """The region the objects are kept in, as the bucket's service names it, for the
        residency checks (app/residency.py); None for a directory, which is wherever the app
        runs, and for a bucket that would not say."""
        return None

    @property
    def encryption(self) -> str | None:
        """How the bucket encrypts every object it keeps by default, as its service names the
        algorithm (`AES256`, `aws:kms` and its key), for `report_encryption`; empty when the
        bucket says it has no default encryption, and None for a directory, whose disk only the
        operator can speak for, and for a bucket that would not say."""
        return None

    @abstractmethod
    def list(self, prefix: str) -> Iterator[StoredObject]:
        """Every object under the prefix, which names a folder and ends with '/', in key order."""

    @abstractmethod
    def delete(self, keys: Iterable[str]) -> None:
        """Delete these objects; one that is already gone is nothing to report."""

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Delete every object under the prefix, which names a folder and ends with '/'."""

    def response(
        self,
        key: str,
        *,
        media_type: str,
        filename: str | None = None,
        inline: bool = False,
        max_age: int | None = None,
    ) -> Response:
        """Hand the object to the browser, once the route has checked who asks: named by
        `filename` as a download, or `inline` for a file the page shows itself.

        A storage that signs URLs gets a redirect to one, so the bytes never pass through the
        app. The redirect may be cached, but for less time than the URL it leads to stays
        valid, so a cached redirect never leads to an expired URL.
        """
        url = self.presigned_url(key, content_type=media_type, filename=filename, inline=inline)
        if url is None:
            headers = _file_headers(filename, inline, max_age)
            return Response(self.get(key), media_type=media_type, headers=headers)
        max_age = min(max_age, URL_LIFETIME - 60) if max_age else None
        return RedirectResponse(url, status_code=307, headers=_cache_headers(max_age))

    def discard(self, *prefixes: str) -> None:
        """Delete what rows that are already gone owned.

        The user's delete has happened by the time this runs, so a storage that fails here is
        logged rather than reported as a failed request; what it leaves behind is for the
        orphan sweep to find.
        """
        for prefix in prefixes:
            try:
                self.delete_prefix(prefix)
            except StorageError as error:
                log.warning("Could not delete the objects under %s: %s", prefix, error)


def _cache_headers(max_age: int | None) -> dict[str, str]:
    # What is served belongs to one organisation, so only the browser that asked may keep it.
    return {"Cache-Control": f"private, max-age={max_age}"} if max_age else {}


def _file_headers(filename: str | None, inline: bool, max_age: int | None) -> dict[str, str]:
    """The headers of a file the app sends from its own origin."""
    headers = {"Content-Security-Policy": SANDBOX, **_cache_headers(max_age)}
    if filename:
        headers["Content-Disposition"] = content_disposition(filename, inline=inline)
    return headers


class LocalStorage(Storage):
    """A directory on this machine; keys are paths under it."""

    def __init__(self, root: Path):
        self.root = root

    def check(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StorageError(
                f"The storage directory {self.root} cannot be created ({error.strerror}). "
                "Point STORAGE_URL at a directory this process may write to."
            ) from error
        if not os.access(self.root, os.W_OK):
            raise StorageError(
                f"The storage directory {self.root} is not writable by this process. Fix its "
                "permissions or point STORAGE_URL at another directory."
            )

    def _path(self, key: str) -> Path:
        # Keys are the app's own, built from ids, but one that could leave the root must never
        # reach the filesystem.
        parts = Path(key).parts
        if not key or key.startswith("/") or ".." in parts or not parts:
            raise ValueError(f"{key!r} is not a storage key")
        return self.root / key

    def put(self, key: str, source: Path | bytes, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed into place, so a reader never sees half an
        # object and two renders of the same page do not interleave.
        handle, name = tempfile.mkstemp(dir=path.parent, prefix=".part-")
        partial = Path(name)
        try:
            if isinstance(source, bytes):
                with os.fdopen(handle, "wb") as out:
                    out.write(source)
            else:
                os.close(handle)
                shutil.copyfile(source, partial)
            os.replace(partial, path)
        except OSError as error:
            partial.unlink(missing_ok=True)
            raise StorageError(
                f"The file could not be written to {self.root} ({error.strerror}). Check the "
                "disk space and permissions of the storage directory."
            ) from error

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError(key) from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    @contextmanager
    def localize(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        yield path

    def presigned_url(
        self,
        key: str,
        *,
        content_type: str,
        filename: str | None = None,
        inline: bool = False,
        expires_in: int = URL_LIFETIME,
    ) -> str | None:
        # A directory has no URLs of its own; `response` serves the file.
        return None

    def response(
        self,
        key: str,
        *,
        media_type: str,
        filename: str | None = None,
        inline: bool = False,
        max_age: int | None = None,
    ) -> Response:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return FileResponse(
            path, media_type=media_type, headers=_file_headers(filename, inline, max_age)
        )

    def list(self, prefix: str) -> Iterator[StoredObject]:
        folder = self._path(_folder(prefix))
        if not folder.is_dir():
            return
        for path in sorted(path for path in folder.rglob("*") if path.is_file()):
            try:
                stat = path.stat()
            except FileNotFoundError:
                # Deleted since the walk, by a sweep or a delete running beside this one.
                continue
            yield StoredObject(
                path.relative_to(self.root).as_posix(),
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime, UTC),
            )

    def delete(self, keys: Iterable[str]) -> None:
        for key in keys:
            path = self._path(key)
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise StorageError(
                    f"The file {key} could not be deleted ({error.strerror})."
                ) from error
            self._prune(path.parent)

    def delete_prefix(self, prefix: str) -> None:
        folder = self._path(_folder(prefix))
        if not folder.exists():
            return
        try:
            shutil.rmtree(folder)
        except OSError as error:
            raise StorageError(
                f"The files under {folder} could not be deleted ({error.strerror})."
            ) from error
        self._prune(folder.parent)

    def _prune(self, folder: Path) -> None:
        # A folder holds nothing but keys, so one left empty means nothing and goes too, and so
        # do its parents, up to the root.
        while folder != self.root and folder.is_relative_to(self.root):
            try:
                folder.rmdir()
            except OSError:
                # Not empty, or already gone: either way there is nothing more to prune.
                return
            folder = folder.parent


class S3Client(Protocol):
    """The calls the bucket storage makes on boto3's S3 client; the tests stand in for it."""

    def head_bucket(self, *, Bucket: str) -> Any: ...

    def get_bucket_location(self, *, Bucket: str) -> Any: ...

    def get_bucket_encryption(self, *, Bucket: str) -> Any: ...

    def put_object(self, *, Bucket: str, Key: str, Body: Any, ContentType: str = ...) -> Any: ...

    def get_object(self, *, Bucket: str, Key: str) -> Any: ...

    def head_object(self, *, Bucket: str, Key: str) -> Any: ...

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken: str = ...) -> Any: ...

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> Any: ...

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str: ...


def _missing(error: ClientError) -> bool:
    return _code(error) in {"404", "NoSuchKey", "NotFound"}


def _code(error: ClientError) -> str | None:
    return error.response.get("Error", {}).get("Code")


def _describe(error: Exception) -> str:
    if isinstance(error, ClientError):
        details = error.response.get("Error", {})
        return f"{details.get('Code', 'error')}: {details.get('Message', '')}".rstrip(": ")
    return str(error)


# What a bucket answers when asked for a default encryption it does not have.
NO_DEFAULT_ENCRYPTION = "ServerSideEncryptionConfigurationNotFoundError"
# What a bucket answers while it is throttling or overloaded: S3 says SlowDown with a 503 and
# RequestTimeout with a 400, and other services use the status alone.
_TRANSIENT_CODES = {"SlowDown", "RequestTimeout", "InternalError", "ServiceUnavailable"}
_TRANSIENT_STATUSES = {408, 429}


def _transient(error: Exception) -> bool:
    """Whether the bucket may answer if asked again in a while: it is throttling, overloaded or
    out of reach, rather than refusing the key, the bucket or the request."""
    if isinstance(error, ClientError):
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        code = _code(error)
        return code in _TRANSIENT_CODES or status in _TRANSIENT_STATUSES or status >= 500
    return isinstance(error, BotoConnectionError | HTTPClientError)


def _unreadable(error: Exception) -> StorageError:
    return StorageError(
        f"The file could not be read from the bucket ({_describe(error)}).",
        transient=_transient(error),
    )


class S3Storage(Storage):
    """An S3-compatible bucket, reached through boto3, with every key under one prefix."""

    def __init__(self, client: S3Client, bucket: str, prefix: str = ""):
        self.client = client
        self.bucket = bucket
        prefix = prefix.strip("/")
        self.prefix = f"{prefix}/" if prefix else ""

    def _key(self, key: str) -> str:
        return self.prefix + key

    def check(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (ClientError, BotoCoreError) as error:
            raise StorageError(
                f"The bucket {self.bucket!r} cannot be used ({_describe(error)}). Check the "
                "bucket in STORAGE_URL, and AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, "
                "AWS_DEFAULT_REGION and, for a service other than AWS, AWS_ENDPOINT_URL in the "
                "environment."
            ) from error

    def put(self, key: str, source: Path | bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        try:
            if isinstance(source, bytes):
                self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=source, **extra)
            else:
                with source.open("rb") as body:
                    self.client.put_object(
                        Bucket=self.bucket, Key=self._key(key), Body=body, **extra
                    )
        except (ClientError, BotoCoreError) as error:
            raise StorageError(
                f"The file could not be stored in the bucket ({_describe(error)}). Try again, "
                "and check the bucket's permissions if it keeps failing.",
                transient=_transient(error),
            ) from error

    def _body(self, key: str) -> Any:
        """The object's streaming body; `FileNotFoundError` when the bucket has no such key."""
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]
        except (ClientError, BotoCoreError) as error:
            if isinstance(error, ClientError) and _missing(error):
                raise FileNotFoundError(key) from None
            raise _unreadable(error) from error

    def get(self, key: str) -> bytes:
        return self._body(key).read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except (ClientError, BotoCoreError) as error:
            if isinstance(error, ClientError) and _missing(error):
                return False
            raise StorageError(
                f"The bucket could not be asked for the file ({_describe(error)}).",
                transient=_transient(error),
            ) from error
        return True

    @contextmanager
    def localize(self, key: str) -> Iterator[Path]:
        body = self._body(key)
        # The copy keeps the extension: the parsers pick their format from the file name too.
        handle, name = tempfile.mkstemp(prefix="emudoc-", suffix=Path(key).suffix)
        path = Path(name)
        try:
            try:
                with os.fdopen(handle, "wb") as out:
                    shutil.copyfileobj(body, out)
            except (ClientError, BotoCoreError) as error:
                # The connection dropped partway through the object.
                raise _unreadable(error) from error
            yield path
        finally:
            path.unlink(missing_ok=True)

    def presigned_url(
        self,
        key: str,
        *,
        content_type: str,
        filename: str | None = None,
        inline: bool = False,
        expires_in: int = URL_LIFETIME,
    ) -> str:
        # The type, the name and whether it downloads are fixed when the URL is signed, so
        # whoever holds the URL gets the object as the app would have served it.
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._key(key),
            "ResponseContentType": content_type,
        }
        if filename:
            params["ResponseContentDisposition"] = content_disposition(filename, inline=inline)
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)

    @cached_property
    def origin(self) -> str:
        # Signing happens on this machine, and the host it signs for follows the endpoint, the
        # region and the addressing style boto3 settled on, so the surest way to learn where
        # every signed URL leads is to sign one.
        parts = urlsplit(self.presigned_url("origin", content_type="application/octet-stream"))
        return f"{parts.scheme}://{parts.netloc}"

    @cached_property
    def region(self) -> str | None:
        # Asked of the bucket itself: the client's region is only where it signs first, and
        # boto3 follows a bucket elsewhere without saying so.
        try:
            answer = self.client.get_bucket_location(Bucket=self.bucket)
        except (ClientError, BotoCoreError) as error:
            log.warning(
                "The bucket %r did not say which region it is in (%s).",
                self.bucket,
                _describe(error),
            )
            return None
        # AWS answers nothing for its first region, and EU for a bucket made in Ireland before
        # regions had the names they have now.
        location = answer.get("LocationConstraint") or "us-east-1"
        return "eu-west-1" if location == "EU" else location

    @cached_property
    def encryption(self) -> str | None:
        try:
            answer = self.client.get_bucket_encryption(Bucket=self.bucket)
        except (ClientError, BotoCoreError) as error:
            code = _code(error) if isinstance(error, ClientError) else None
            if code == NO_DEFAULT_ENCRYPTION:
                return ""
            # Refused to the app, the question has an answer; a service that does not
            # implement it (versitygw, some S3-compatible stores) has to be asked another way.
            advice = (
                "allow the app s3:GetEncryptionConfiguration on it to have it say"
                if code == "AccessDenied"
                else "confirm with its service that it encrypts everything it stores"
            )
            log.warning(
                "The bucket %r did not say whether it encrypts what it keeps (%s); %s.",
                self.bucket,
                _describe(error),
                advice,
            )
            return None
        # A bucket has one rule; a list of them is the API's shape, not a second encryption.
        named = []
        for rule in answer.get("ServerSideEncryptionConfiguration", {}).get("Rules", []):
            default = rule.get("ApplyServerSideEncryptionByDefault") or {}
            if algorithm := default.get("SSEAlgorithm"):
                key = default.get("KMSMasterKeyID")
                named.append(f"{algorithm} with the key {key}" if key else algorithm)
        return "; ".join(named)

    def list(self, prefix: str) -> Iterator[StoredObject]:
        folder = self._key(_folder(prefix))
        token: str | None = None
        while True:
            more = {"ContinuationToken": token} if token else {}
            try:
                page = self.client.list_objects_v2(Bucket=self.bucket, Prefix=folder, **more)
            except (ClientError, BotoCoreError) as error:
                raise StorageError(
                    f"The bucket could not be listed ({_describe(error)}).",
                    transient=_transient(error),
                ) from error
            for entry in page.get("Contents", []):
                yield StoredObject(
                    entry["Key"][len(self.prefix) :], entry["Size"], entry["LastModified"]
                )
            if not page.get("IsTruncated"):
                return
            token = page["NextContinuationToken"]

    def delete(self, keys: Iterable[str]) -> None:
        for batch in batched(keys, DELETE_BATCH, strict=False):
            try:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": self._key(key)} for key in batch], "Quiet": True},
                )
            except (ClientError, BotoCoreError) as error:
                raise StorageError(
                    f"The objects could not be deleted from the bucket ({_describe(error)}).",
                    transient=_transient(error),
                ) from error

    def delete_prefix(self, prefix: str) -> None:
        # Deleted as they are listed; a listing continues after the last key it returned, so
        # what is gone behind it does not move it.
        self.delete(entry.key for entry in self.list(prefix))


def storage_from_settings(settings: Settings) -> Storage:
    """The storage STORAGE_URL names: a directory for `file:///...`, a bucket for `s3://...`.

    The bucket's credentials, region and, for a service other than AWS, endpoint come from the
    standard `AWS_*` variables in the process environment, which boto3 reads itself; in
    development `export_env_file` has copied them there from `.env`.
    """
    parts = urlsplit(settings.storage_url)
    if parts.scheme == "file":
        return LocalStorage(Path(unquote(parts.path)))
    import boto3
    from botocore.config import Config

    # Signature version 4 is what every current service accepts; boto3 would otherwise sign
    # requests to a bucket in the default region the old way, which some services refuse.
    client = boto3.client("s3", config=Config(signature_version="s3v4"))
    return S3Storage(client, parts.netloc, unquote(parts.path))


def report_encryption(storage: Storage) -> None:
    """Say at start how the bucket keeps the files at rest: encrypted, by its default
    encryption, or as they are, which is worth a warning in every log the operator reads.

    Not a refusal, even in production: a bucket on the deployment's own network may sit on an
    encrypted disk and say it has no default encryption, and a service may encrypt everything
    without answering the question (deploying.md, "Encryption"). A directory says nothing; its
    disk is the operator's.
    """
    if storage.origin is None:
        return
    encryption = storage.encryption
    if encryption:
        log.info("The bucket encrypts every file it keeps at rest (%s).", encryption)
    elif encryption is not None:
        log.warning(
            "The bucket says it has no default encryption, so uploads and page images are "
            "kept as they are unless its service encrypts its disks; turn on its default "
            "encryption (SSE-S3 or SSE-KMS), or confirm with the service that it encrypts "
            "everything it stores."
        )
