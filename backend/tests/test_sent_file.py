"""Which file of a Telegram message is taken as a receipt, and what the deployed application wires for files."""

from typing import Any

import pytest
from fastapi.routing import APIRoute

from qarz.application.telegram_updates import _sent_file
from qarz.domain.files import MAX_FILE_BYTES
from qarz.infrastructure.settings import Settings
from qarz.interface.asgi import build

DB = "postgresql://qd_app:unused@127.0.0.1:1/unused"  # never connected to in these tests


def test_of_a_photo_the_largest_size_that_fits_is_taken() -> None:
    sizes = [
        {"file_id": "thumb", "file_size": 1_000},
        {"file_id": "medium", "file_size": 200_000},
        {"file_id": "large", "file_size": MAX_FILE_BYTES},
        {"file_id": "original", "file_size": MAX_FILE_BYTES + 1},
    ]
    assert _sent_file({"photo": sizes}) == "large"
    assert _sent_file({"photo": list(reversed(sizes))}) == "large", "whatever order Telegram lists them in"
    assert _sent_file({"photo": sizes[:2]}) == "medium"


def test_a_photo_with_no_size_that_fits_or_none_stated_is_still_named_and_refused_when_fetched() -> None:
    assert _sent_file({"photo": [{"file_id": "a", "file_size": MAX_FILE_BYTES + 1}]}) == "a"
    assert _sent_file({"photo": [{"file_id": "a"}, {"file_id": "b"}]}) == "b"
    assert _sent_file({"photo": [{"file_id": "a", "file_size": True}, {"file_id": "b", "file_size": "9"}]}) == "b"


def test_a_document_is_named_by_its_identifier() -> None:
    assert _sent_file({"document": {"file_id": "doc", "file_name": "chek.pdf", "file_size": 10}}) == "doc"


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"text": "Ali 45000"},
        {"photo": []},
        {"photo": "x"},
        {"photo": [{"file_size": 10}]},
        {"photo": [{"file_id": 5}]},
        {"photo": ["x"]},
        {"document": "x"},
        {"document": {}},
        {"document": {"file_id": None}},
        {"voice": {"file_id": "v"}},
        {"video": {"file_id": "v"}},
        {"sticker": {"file_id": "s"}},
    ],
)
def test_anything_else_carries_no_receipt(message: dict[str, Any]) -> None:
    assert _sent_file(message) is None


def _settings(**extra: str) -> Settings:
    return Settings(database_url=DB, bot_token="123:test", webhook_secret="a-long-enough-secret", **extra)


def test_the_deployed_application_serves_the_notice_routes_with_or_without_a_store(tmp_path: Any) -> None:
    for settings in (_settings(), _settings(file_store="filesystem", file_root=str(tmp_path))):
        paths = {route.path for route in build(settings).routes if isinstance(route, APIRoute)}
        assert {
            "/api/v1/me/accounts/{link_id}/payment-notices",
            "/api/v1/shops/{shop_id}/payment-notices",
            "/api/v1/shops/{shop_id}/payment-notices/{notice_id}/accept",
            "/api/v1/shops/{shop_id}/payment-notices/{notice_id}/decline",
            "/api/v1/shops/{shop_id}/payment-notices/{notice_id}/receipt",
        } <= paths
    # A shop's stored object is reachable in one way only: through a signed link (ADR-020). The one route
    # that takes a key serves the photos of the shared catalogue, which are nobody's data: the key is the
    # hash of the photo and names nothing outside `catalog/` (tests/api/test_shared_catalog.py).
    assert sorted(path for path in paths if "file" in path or "object" in path) == [
        "/files/catalog/{key}",
        "/files/{token}",
    ]


@pytest.mark.parametrize("extra", [{"file_store": "filesystem"}, {"file_store": "s3"}, {"file_store": "elsewhere"}])
def test_a_store_that_is_named_but_misconfigured_stops_the_application_from_starting(extra: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=r"QD_FILE|file store"):
        build(_settings(**extra))
