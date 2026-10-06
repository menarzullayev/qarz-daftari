"""The two file stores (ADR-020): a directory, and an S3-compatible service.

The S3 store is exercised against a stubbed transport and against a tiny server on the loopback interface.
It has not been run against a real bucket.
"""

import asyncio
import hashlib
import http.server
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qarz.application.ports import FileMissing, FileStore, FileStoreError
from qarz.infrastructure.file_store import (
    EMPTY_SHA256,
    FilesystemFileStore,
    HttpTransport,
    S3FileStore,
    build_file_store,
    sign_v4,
)
from qarz.infrastructure.settings import Settings

KEY = "ab/" + "ab" + "c" * 62
UNSAFE_KEYS = [
    "",
    "..",
    "../outside",
    "ab/../../outside",
    "ab/..",
    "/etc/passwd",
    "//server/share/x",
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "..\\outside",
    "ab\\..\\..\\outside",
    "ab/./x",
    "ab//x",
    "ab/x.txt",
    "ab/x\x00y",
    "ab/x y",
    "%2e%2e/outside",
    "ab/x/",
    "a/b/c/d/e",
]


def run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


# --- the directory store -------------------------------------------------------------------------------


def test_the_directory_store_keeps_reads_replaces_and_deletes(tmp_path: Path) -> None:
    store: FileStore = FilesystemFileStore(tmp_path / "store")
    content = bytes(range(256)) * 10
    run(store.put(KEY, content, "image/jpeg"))
    assert run(store.get(KEY)) == content
    assert (tmp_path / "store" / "ab" / KEY[3:]).read_bytes() == content
    assert [p.name for p in (tmp_path / "store" / "ab").iterdir()] == [KEY[3:]], "no temporary file is left behind"

    run(store.put(KEY, b"second", "image/png"))
    assert run(store.get(KEY)) == b"second"

    run(store.delete(KEY))
    with pytest.raises(FileMissing):
        run(store.get(KEY))
    run(store.delete(KEY))  # deleting what is not there is not an error
    with pytest.raises(FileMissing):
        run(store.get("ab/neverstored"))


@pytest.mark.parametrize("key", UNSAFE_KEYS)
def test_no_key_reaches_outside_the_directory(tmp_path: Path, key: str) -> None:
    root = tmp_path / "store"
    root.mkdir()
    secret = tmp_path / "outside"
    secret.write_bytes(b"not yours")
    store = FilesystemFileStore(root)
    for action in (store.put(key, b"written", "image/png"), store.get(key), store.delete(key)):
        with pytest.raises(FileStoreError):
            run(action)
    assert secret.read_bytes() == b"not yours"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["outside", "store"]
    assert list(root.iterdir()) == []


def test_a_link_inside_the_directory_is_not_followed_out_of_it(tmp_path: Path) -> None:
    root, outside = tmp_path / "store", tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    (outside / "target").write_bytes(b"not yours")
    try:
        (root / "ab").symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows without the privilege to create symbolic links: a directory junction needs none.
        import _winapi

        _winapi.CreateJunction(str(outside), str(root / "ab"))
    store = FilesystemFileStore(root)
    for action in (store.get("ab/target"), store.put("ab/target", b"x", "image/png"), store.delete("ab/target")):
        with pytest.raises(FileStoreError):
            run(action)
    assert (outside / "target").read_bytes() == b"not yours"


# --- signing -------------------------------------------------------------------------------------------

# The worked examples of the AWS documentation, "Signature Calculations for the Authorization Header:
# Transferring Payload in a Single Chunk (AWS Signature Version 4)".
AWS = {
    # The documentation's own example credentials, split so that no scanner takes them for a real key.
    "access_key": "AKIA" + "IOSFODNN7" + "EXAMPLE",
    "secret_key": "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCY" + "EXAMPLEKEY",
    "region": "us-east-1",
    "amz_date": "20130524T000000Z",
}
AWS_HOST = "examplebucket.s3.amazonaws.com"
SCOPE = f"Credential={AWS['access_key']}/20130524/us-east-1/s3/aws4_request"


def test_the_signature_matches_the_published_get_example() -> None:
    header = sign_v4(
        method="GET",
        path="/test.txt",
        query="",
        headers={
            "Host": AWS_HOST,
            "Range": "bytes=0-9",
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": AWS["amz_date"],
        },
        payload_sha256=EMPTY_SHA256,
        **AWS,
    )
    assert header == (
        f"AWS4-HMAC-SHA256 {SCOPE},SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_the_signature_matches_the_published_put_example() -> None:
    payload = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
    header = sign_v4(
        method="PUT",
        path="/test%24file.text",
        query="",
        headers={
            "host": AWS_HOST,
            "date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-content-sha256": payload,
            "x-amz-date": AWS["amz_date"],
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
        },
        payload_sha256=payload,
        **AWS,
    )
    assert header.endswith("Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")
    assert "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class," in header


def test_the_signature_matches_the_published_query_example() -> None:
    header = sign_v4(
        method="GET",
        path="/",
        query="lifecycle=",
        headers={"host": AWS_HOST, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": AWS["amz_date"]},
        payload_sha256=EMPTY_SHA256,
        **AWS,
    )
    assert header.endswith("Signature=fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543")


@pytest.mark.parametrize("change", ["method", "path", "payload_sha256", "secret_key", "region", "amz_date"])
def test_every_part_of_the_request_is_signed(change: str) -> None:
    arguments: dict[str, Any] = {
        "method": "GET",
        "path": "/test.txt",
        "query": "",
        "headers": {"host": AWS_HOST, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": AWS["amz_date"]},
        "payload_sha256": EMPTY_SHA256,
        **AWS,
    }
    original = sign_v4(**arguments)
    changed = {
        "method": "DELETE",
        "path": "/test2.txt",
        "payload_sha256": hashlib.sha256(b"x").hexdigest(),
        "secret_key": AWS["secret_key"] + "x",
        "region": "eu-west-1",
        "amz_date": "20130525T000000Z",
    }
    assert sign_v4(**{**arguments, change: changed[change]})[-64:] != original[-64:]


# --- the S3 store against a stubbed transport ------------------------------------------------------------

FIXED_NOW = datetime(2026, 10, 7, 9, 30, 15, tzinfo=UTC)


class StubS3:
    """Remembers objects by URL path, the way a path-style S3 endpoint would, and records every request."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []
        self.fail_with: int | None = None
        self.error: Exception | None = None

    async def __call__(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        self.requests.append((method, path, dict(headers), body))
        if self.error is not None:
            raise self.error
        if self.fail_with is not None:
            return self.fail_with, b"<Error><Code>Nope</Code></Error>"
        if method == "PUT":
            assert body is not None
            self.objects[path] = body
            return 200, b""
        if method == "GET":
            return (200, self.objects[path]) if path in self.objects else (404, b"<Error/>")
        if method == "DELETE":
            return (204, b"") if self.objects.pop(path, None) is not None else (404, b"")
        return 405, b""


def s3_store(stub: StubS3, **overrides: Any) -> S3FileStore:
    arguments: dict[str, Any] = {
        "endpoint": "http://files.internal:3900",
        "bucket": "qd-files",
        "region": "garage",
        "access_key": "GKtest",
        "secret_key": "test-only-secret",
        "max_object_bytes": 1024,
        "transport": stub,
        "now": lambda: FIXED_NOW,
    }
    return S3FileStore(**{**arguments, **overrides})


def test_the_s3_store_puts_gets_and_deletes_path_style_with_signed_requests() -> None:
    stub = StubS3()
    store: FileStore = s3_store(stub)
    content = b"\xff\xd8\xff receipt"
    run(store.put(KEY, content, "image/jpeg"))
    assert stub.objects == {f"/qd-files/{KEY}": content}
    assert run(store.get(KEY)) == content
    run(store.delete(KEY))
    assert stub.objects == {}
    run(store.delete(KEY))  # a second delete is answered 404, which is not an error
    with pytest.raises(FileMissing):
        run(store.get(KEY))

    assert [(method, path) for method, path, _, _ in stub.requests] == [
        ("PUT", f"/qd-files/{KEY}"),
        ("GET", f"/qd-files/{KEY}"),
        ("DELETE", f"/qd-files/{KEY}"),
        ("DELETE", f"/qd-files/{KEY}"),
        ("GET", f"/qd-files/{KEY}"),
    ]
    method, path, headers, body = stub.requests[0]
    assert body == content
    assert headers["host"] == "files.internal:3900"
    assert headers["content-type"] == "image/jpeg"
    assert headers["x-amz-date"] == "20261007T093015Z"
    assert headers["x-amz-content-sha256"] == hashlib.sha256(content).hexdigest()
    # The header is exactly what an independent signing of the same request gives.
    signed = {name: value for name, value in headers.items() if name != "authorization"}
    assert headers["authorization"] == sign_v4(
        method=method,
        path=path,
        query="",
        headers=signed,
        payload_sha256=hashlib.sha256(content).hexdigest(),
        access_key="GKtest",
        secret_key="test-only-secret",
        region="garage",
        amz_date="20261007T093015Z",
    )
    assert "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date," in headers["authorization"]
    assert "Credential=GKtest/20261007/garage/s3/aws4_request" in headers["authorization"]
    assert "test-only-secret" not in str(stub.requests)
    assert stub.requests[1][2]["x-amz-content-sha256"] == EMPTY_SHA256 and stub.requests[1][3] is None


@pytest.mark.parametrize("key", UNSAFE_KEYS)
def test_the_s3_store_sends_nothing_for_an_unsafe_key(key: str) -> None:
    stub = StubS3()
    store = s3_store(stub)
    for action in (store.put(key, b"x", "image/png"), store.get(key), store.delete(key)):
        with pytest.raises(FileStoreError):
            run(action)
    assert stub.requests == []


@pytest.mark.parametrize("status", [301, 400, 403, 500, 503])
def test_an_answer_that_is_not_success_is_an_error_that_carries_no_content(status: int) -> None:
    stub = StubS3()
    stub.fail_with = status
    store = s3_store(stub)
    for action in (store.put(KEY, b"private receipt", "image/png"), store.get(KEY), store.delete(KEY)):
        with pytest.raises(FileStoreError) as raised:
            run(action)
        assert str(status) in str(raised.value)
        assert "private receipt" not in str(raised.value) and "Nope" not in str(raised.value)


def test_a_network_failure_is_reported_by_kind_only() -> None:
    stub = StubS3()
    stub.error = ConnectionRefusedError("http://files.internal:3900/qd-files/secret refused")
    with pytest.raises(FileStoreError) as raised:
        run(s3_store(stub).put(KEY, b"x", "image/png"))
    assert str(raised.value) == "ConnectionRefusedError"
    assert raised.value.__cause__ is None, "the original text, with its address, is not chained"


def test_an_object_larger_than_any_the_service_stores_is_not_returned() -> None:
    stub = StubS3()
    store = s3_store(stub, max_object_bytes=8)
    stub.objects[f"/qd-files/{KEY}"] = b"12345678"
    assert run(store.get(KEY)) == b"12345678"
    stub.objects[f"/qd-files/{KEY}"] = b"123456789"
    with pytest.raises(FileStoreError):
        run(store.get(KEY))


@pytest.mark.parametrize(
    "overrides",
    [
        {"endpoint": "files.internal:3900"},
        {"endpoint": "http://files.internal:3900/some/path"},
        {"endpoint": ""},
        {"bucket": ""},
        {"bucket": "a/b"},
        {"bucket": "../x"},
        {"access_key": ""},
        {"secret_key": ""},
    ],
)
def test_a_misconfigured_s3_store_is_refused_at_start(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="file store") as raised:
        s3_store(StubS3(), **overrides)
    assert "test-only-secret" not in str(raised.value)


# --- the S3 store over real sockets, on the loopback interface only --------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    objects: dict[str, bytes] = {}  # noqa: RUF012  (shared on purpose: one server, one store)
    seen: list[dict[str, str]] = []  # noqa: RUF012

    def _record(self) -> None:
        self.seen.append({name.lower(): value for name, value in self.headers.items()} | {"method": self.command})

    def do_PUT(self) -> None:
        self._record()
        body = self.rfile.read(int(self.headers["Content-Length"]))
        ok = hashlib.sha256(body).hexdigest() == self.headers["x-amz-content-sha256"]
        if ok:
            self.objects[self.path] = body
        self.send_response(200 if ok else 400)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._record()
        if self.path == "/qd-files/re/direct":
            self.send_response(302)
            self.send_header("Location", "/qd-files/" + KEY)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = self.objects.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def do_DELETE(self) -> None:
        self._record()
        self.send_response(204 if self.objects.pop(self.path, None) is not None else 404)
        self.end_headers()

    def log_message(self, *arguments: Any) -> None:
        pass


@pytest.fixture
def loopback_s3() -> Iterator[str]:
    _Handler.objects, _Handler.seen = {}, []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_s3_store_over_http_round_trips_binary_content(loopback_s3: str) -> None:
    store = S3FileStore(
        endpoint=loopback_s3,
        bucket="qd-files",
        region="garage",
        access_key="GKtest",
        secret_key="test-only-secret",
        max_object_bytes=4096,
    )
    content = bytes(range(256)) * 8
    run(store.put(KEY, content, "application/pdf"))
    assert _Handler.objects == {f"/qd-files/{KEY}": content}
    assert run(store.get(KEY)) == content
    run(store.delete(KEY))
    run(store.delete(KEY))
    with pytest.raises(FileMissing):
        run(store.get(KEY))

    put = _Handler.seen[0]
    assert put["method"] == "PUT" and put["content-type"] == "application/pdf"
    assert put["host"] == loopback_s3.removeprefix("http://")
    assert put["authorization"].startswith("AWS4-HMAC-SHA256 Credential=GKtest/")
    assert all(request["authorization"] and request["x-amz-date"] for request in _Handler.seen)
    assert "test-only-secret" not in str(_Handler.seen)


def test_a_redirect_is_not_followed_and_an_oversized_answer_is_refused(loopback_s3: str) -> None:
    store = S3FileStore(
        endpoint=loopback_s3,
        bucket="qd-files",
        region="garage",
        access_key="GKtest",
        secret_key="test-only-secret",
        max_object_bytes=16,
    )
    _Handler.objects[f"/qd-files/{KEY}"] = b"x" * 17
    with pytest.raises(FileStoreError):
        run(store.get(KEY))
    _Handler.objects[f"/qd-files/{KEY}"] = b"x" * 16
    assert run(store.get(KEY)) == b"x" * 16
    with pytest.raises(FileStoreError, match="302"):
        run(store.get("re/direct"))


def test_an_unreachable_store_is_an_error_not_a_hang() -> None:
    transport = HttpTransport("http://127.0.0.1:1", timeout=2, max_response_bytes=16)
    store = S3FileStore(
        endpoint="http://127.0.0.1:1",
        bucket="qd-files",
        region="garage",
        access_key="GKtest",
        secret_key="test-only-secret",
        max_object_bytes=16,
        transport=transport,
    )
    with pytest.raises(FileStoreError):
        run(store.get(KEY))


@pytest.mark.parametrize("endpoint", ["ftp://files.internal", "files.internal", "http://", ""])
def test_the_transport_speaks_http_or_https_only(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        HttpTransport(endpoint, max_response_bytes=16)


# --- which store the environment names -------------------------------------------------------------------

DB = "postgresql://qd_app:unused@127.0.0.1:1/unused"


def test_the_store_is_chosen_by_the_environment(tmp_path: Path) -> None:
    assert build_file_store(Settings(database_url=DB, file_store=""), max_object_bytes=16) is None
    directory = build_file_store(
        Settings(database_url=DB, file_store="filesystem", file_root=str(tmp_path)), max_object_bytes=16
    )
    assert isinstance(directory, FilesystemFileStore)
    remote = build_file_store(
        Settings(
            database_url=DB,
            file_store="S3",
            s3_endpoint="http://127.0.0.1:3900",
            s3_bucket="qd-files",
            s3_region="garage",
            s3_access_key="GKtest",
            s3_secret_key="test-only-secret",
        ),
        max_object_bytes=16,
    )
    assert isinstance(remote, S3FileStore)


@pytest.mark.parametrize(
    "settings",
    [
        {"file_store": "filesystem"},  # no directory
        {"file_store": "s3"},  # nothing about the bucket
        {"file_store": "s3", "s3_endpoint": "http://127.0.0.1:3900", "s3_bucket": "qd-files"},  # no credentials
        {"file_store": "minio"},
        {"file_store": "database"},
    ],
)
def test_a_store_that_is_named_but_not_configured_stops_the_start(settings: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=r"QD_FILE|file store"):
        build_file_store(Settings(database_url=DB, **settings), max_object_bytes=16)


def test_every_setting_is_named_in_the_example_environment_file_without_a_secret() -> None:
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in example.splitlines() if "=" in line[:40]}
    for field in Settings.model_fields:
        assert f"QD_{field.upper()}" in lines, f"add QD_{field.upper()} to .env.example"
    for secret in ("QD_S3_ACCESS_KEY", "QD_S3_SECRET_KEY", "QD_BOT_TOKEN", "QD_WEBHOOK_SECRET"):
        assert lines[secret] == "", f"{secret} must have no value in the example file"
