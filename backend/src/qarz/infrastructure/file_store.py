"""File stores (ADR-020): a directory for development and tests, an S3-compatible service for production.

Both refuse any key that `qarz.domain.files.is_safe_key` does not accept, so nothing a caller passes can
address a file outside the store. Neither logs or reports file content.
"""

import asyncio
import contextlib
import hashlib
import hmac
import http.client
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from qarz.application.ports import FileMissing, FileStore, FileStoreError
from qarz.domain.files import is_safe_key
from qarz.infrastructure.settings import Settings

FILESYSTEM, S3 = "filesystem", "s3"


def _checked(key: str) -> str:
    if not is_safe_key(key):
        # The key is not echoed: a refused key is by definition not one of ours.
        raise FileStoreError("unsafe object key")
    return key


class FilesystemFileStore:
    """Objects as files under one directory. For development and tests."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / _checked(key)).resolve()
        # Belt and braces: even a key that passed the check must still resolve inside the root.
        if self._root not in path.parents:
            raise FileStoreError("unsafe object key")
        return path

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed, so a reader never sees half a file.
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix="tmp-")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise

    def _get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise FileMissing() from None

    def _delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def put(self, key: str, data: bytes, mime: str) -> None:
        await asyncio.to_thread(self._put, key, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get, key)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete, key)


# --- S3 ------------------------------------------------------------------------------------------------

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_UNRESERVED = "-_.~"


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sign_v4(
    *,
    method: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
    payload_sha256: str,
    access_key: str,
    secret_key: str,
    region: str,
    amz_date: str,
    service: str = "s3",
) -> str:
    """The `Authorization` header of AWS Signature Version 4 for one request.

    `path` is the path as sent (already percent-encoded), `query` the canonical query string, and
    `headers` every header that is to be signed, `host` and `x-amz-date` among them.
    """
    canonical_headers = sorted((name.lower(), " ".join(value.split())) for name, value in headers.items())
    signed = ";".join(name for name, _ in canonical_headers)
    canonical_request = "\n".join(
        [
            method,
            path,
            query,
            "".join(f"{name}:{value}\n" for name, value in canonical_headers),
            signed,
            payload_sha256,
        ]
    )
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
    )
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode("utf-8"), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},SignedHeaders={signed},Signature={signature}"


# method, URL path, headers, body -> status and response body
Transport = Callable[[str, str, dict[str, str], bytes | None], Awaitable[tuple[int, bytes]]]


class HttpTransport:
    """One request per call over the standard library: no redirects followed, no proxy from the environment."""

    def __init__(self, endpoint: str, *, timeout: float = 15.0, max_response_bytes: int) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("the file store endpoint must be an http or https URL")
        self._secure = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port
        self._timeout = timeout
        self._limit = max_response_bytes

    def _send(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        factory = http.client.HTTPSConnection if self._secure else http.client.HTTPConnection
        connection = factory(self._host, self._port, timeout=self._timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            # One byte more than allowed is read so that an oversized answer is noticed, not truncated.
            return response.status, response.read(self._limit + 1)
        finally:
            connection.close()

    async def __call__(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        return await asyncio.to_thread(self._send, method, path, headers, body)


class S3FileStore:
    """Objects in one bucket of an S3-compatible service, addressed path-style (ADR-020)."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        max_object_bytes: int,
        transport: Transport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        parts = urlsplit(endpoint)
        if not parts.netloc or parts.path not in ("", "/"):
            raise ValueError("the file store endpoint must be scheme://host[:port] with no path")
        if not bucket or not is_safe_key(bucket.replace(".", "-")) or "/" in bucket:
            raise ValueError("the file store bucket name is not usable")
        if not access_key or not secret_key:
            raise ValueError("the file store credentials are not set")
        self._host = parts.netloc
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._limit = max_object_bytes
        self._transport: Transport = transport or HttpTransport(endpoint, max_response_bytes=max_object_bytes)
        self._now = now or (lambda: datetime.now(UTC))

    async def _request(self, method: str, key: str, body: bytes | None, mime: str | None) -> tuple[int, bytes]:
        path = "/" + quote(f"{self._bucket}/{_checked(key)}", safe="/" + _UNRESERVED)
        payload_sha256 = hashlib.sha256(body).hexdigest() if body is not None else EMPTY_SHA256
        headers = {
            "host": self._host,
            "x-amz-content-sha256": payload_sha256,
            "x-amz-date": self._now().strftime("%Y%m%dT%H%M%SZ"),
        }
        if mime is not None:
            headers["content-type"] = mime
        headers["authorization"] = sign_v4(
            method=method,
            path=path,
            query="",
            headers=headers,
            payload_sha256=payload_sha256,
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            amz_date=headers["x-amz-date"],
        )
        try:
            return await self._transport(method, path, headers, body)
        except (OSError, http.client.HTTPException) as error:
            # Only the kind of failure: an error text could repeat the URL, never the content.
            raise FileStoreError(type(error).__name__) from None

    async def put(self, key: str, data: bytes, mime: str) -> None:
        status, _ = await self._request("PUT", key, data, mime)
        if status not in (200, 201, 204):
            raise FileStoreError(f"put answered {status}")

    async def get(self, key: str) -> bytes:
        status, body = await self._request("GET", key, None, None)
        if status == 404:
            raise FileMissing()
        if status != 200:
            raise FileStoreError(f"get answered {status}")
        if len(body) > self._limit:
            raise FileStoreError("the object is larger than any the service stores")
        return body

    async def delete(self, key: str) -> None:
        status, _ = await self._request("DELETE", key, None, None)
        if status not in (200, 204, 404):
            raise FileStoreError(f"delete answered {status}")


def build_file_store(settings: Settings, *, max_object_bytes: int) -> FileStore | None:
    """The store the environment names; None when none is configured (files are then refused)."""
    kind = settings.file_store.strip().lower()
    if not kind:
        return None
    if kind == FILESYSTEM:
        if not settings.file_root:
            raise ValueError("QD_FILE_ROOT is not set")
        return FilesystemFileStore(settings.file_root)
    if kind == S3:
        return S3FileStore(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            max_object_bytes=max_object_bytes,
        )
    raise ValueError("QD_FILE_STORE must be 'filesystem' or 's3'")
