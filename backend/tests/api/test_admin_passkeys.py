"""An administrator's passkeys (the owner's decision of 2026-10-10): the third further way in.

A device is played here by a key pair that signs what an authenticator signs. What must hold: a passkey
is registered only by an administrator who is inside, and for themselves; signing in gives the web
session of its owner and nobody else's; and an answer is refused, in one voice, when it is for another
site, another page, another purpose, an old challenge, an unverified holder, a wrong key, a counter that
went back, a passkey that was ended, or when it was accepted once already.
"""

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.admin_passkeys import PasskeySite
from qarz.application.admin_sign_in import AdminSignIn
from qarz.application.auth import AuthService
from qarz.domain import passkey
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

from .conftest import (
    ADMIN_API,
    TEST_BOT_TOKEN,
    TEST_PASSKEY_HOST,
    TEST_PASSKEY_SITE,
    AdminEnv,
    World,
    make_admin,
)

pytestmark = pytest.mark.db

HOST, SITE = TEST_PASSKEY_HOST, TEST_PASSKEY_SITE
ORIGIN = f"https://{HOST}"
ME = "/api/v1/me"
SIGN_IN = "/api/v1/auth/admin-passkey"
MINE = f"{ADMIN_API}/passkeys"
PRESENT, VERIFIED, ATTESTED = 0x01, 0x04, 0x40


class Device:
    """What holds a passkey: a private key, the credential's identifier and a count of signatures."""

    def __init__(self, *, counts: bool = False) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.credential = os.urandom(32)
        self.count = 1 if counts else 0
        self._counts = counts

    @property
    def public_key(self) -> str:
        return passkey.b64(
            self._key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )

    @staticmethod
    def client_data(kind: str, challenge: str, origin: str = ORIGIN) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def data(self, *, host: str = HOST, flags: int = PRESENT | VERIFIED, attested: bool = False) -> bytes:
        raw = hashlib.sha256(host.encode()).digest() + bytes([flags | (ATTESTED if attested else 0)])
        raw += self.count.to_bytes(4, "big")
        if attested:
            raw += bytes(16) + len(self.credential).to_bytes(2, "big") + self.credential + b"\xa0"
        return raw

    def register(self, challenge: str, label: str = "laptop", **changed: Any) -> dict[str, Any]:
        return {
            "label": label,
            "client_data": passkey.b64(self.client_data("webauthn.create", challenge, changed.get("origin", ORIGIN))),
            "authenticator_data": passkey.b64(
                self.data(attested=True, flags=changed.get("flags", PRESENT | VERIFIED), host=changed.get("host", HOST))
            ),
            "public_key": self.public_key,
            "algorithm": -7,
        }

    def answer(self, challenge: str, **changed: Any) -> dict[str, str]:
        if self._counts:
            self.count += 1
        client = self.client_data(changed.get("kind", "webauthn.get"), challenge, changed.get("origin", ORIGIN))
        data = self.data(flags=changed.get("flags", PRESENT | VERIFIED), host=changed.get("host", HOST))
        signer = changed.get("signer", self)._key
        signature = signer.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": passkey.b64(self.credential),
            "client_data": passkey.b64(client),
            "authenticator_data": passkey.b64(data),
            "signature": passkey.b64(signature),
        }


def _app(app_url: str, admin_url: str, env: AdminEnv, site: PasskeySite | None) -> Iterator[TestClient]:
    database, admin_database = Database(app_url), Database(admin_url)
    admin = AdminAccess(
        admin_database, allowed_tg_ids=env.allowed, cipher=env.box, now=env.clock.now, second_factor_required=False
    )
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=admin,
        admin_storage=admin_database,
        passkey_site=site,
        now=env.clock.now,
    )
    with TestClient(app, base_url=ORIGIN) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
        test_client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


@pytest.fixture
def client(app_database_url: str, admin_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    yield from _app(app_database_url, admin_database_url, admin_env, SITE)


def _key_for(app_url: str, admin_url: str, env: AdminEnv, tg_id: int) -> dict[str, str]:
    """A way inside for the tests: a service key of this administrator, as a bearer header."""

    async def make() -> str:
        databases = Database(admin_url), Database(app_url)
        try:
            service = AdminSignIn(databases[0], sessions=databases[1], allowed_tg_ids=env.allowed, now=env.clock.now)
            return await service.create_key(tg_id, f"t-{uuid.uuid4().hex[:12]}")
        finally:
            for database in databases:
                await database.dispose()

    return {"Authorization": f"Bearer {asyncio.run(make())}"}


def _tg_id(owner: psycopg.Connection, user_id: uuid.UUID) -> int:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user_id,)).fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture
def inside(
    world: World, owner: psycopg.Connection, admin_env: AdminEnv, app_database_url: str, admin_database_url: str
) -> Iterator[dict[str, str]]:
    """The administrator of the world, inside: the header that says so."""
    admin_env.clock.freeze()
    make_admin(owner, admin_env, world.admin)
    yield _key_for(app_database_url, admin_database_url, admin_env, _tg_id(owner, world.admin))
    owner.execute("DELETE FROM user_session WHERE kind = 'service'")


def _registered(client: TestClient, inside: dict[str, str], device: Device, label: str = "laptop") -> str:
    start = client.get(f"{MINE}/challenge", headers=inside)
    assert start.status_code == 200, start.text
    assert start.json()["rp"]["id"] == HOST
    body = device.register(start.json()["challenge"], label)
    made = client.post(MINE, json=body, headers={**inside, **_once()})
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def _challenge(client: TestClient) -> str:
    """Asked for the way a browser asks: with no answer, and so refused, with the challenge beside it."""
    asked = client.post(SIGN_IN, json={})
    assert asked.status_code == 401 and "set-cookie" not in asked.headers
    wanted = asked.headers["WWW-Authenticate"]
    assert f'rp_id="{HOST}"' in wanted
    found = re.search(r'challenge="([A-Za-z0-9_-]+)"', wanted)
    assert found is not None
    return found.group(1)


def _once() -> dict[str, str]:
    return {"Idempotency-Key": f"passkey-{uuid.uuid4().hex}"}


def _audit(owner: psycopg.Connection, action: str, admin: uuid.UUID) -> int:
    row = owner.execute(
        "SELECT count(*) FROM admin_audit WHERE action = %s AND admin_id = %s", (action, admin)
    ).fetchone()
    assert row is not None
    return int(row[0])


# --- registering and signing in -------------------------------------------------------------------------


def test_a_registered_passkey_signs_its_administrator_in(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection
) -> None:
    device = Device()
    passkey_id = _registered(client, inside, device)
    listed = client.get(MINE, headers=inside).json()["items"]
    assert [(one["id"], one["label"], one["last_used_at"]) for one in listed] == [(passkey_id, "laptop", None)]

    answer = client.post(SIGN_IN, json=device.answer(_challenge(client)))
    assert answer.status_code == 200 and answer.json()["csrf_token"]
    assert client.get(ME).json()["id"] == str(world.admin)
    assert client.get(f"{ADMIN_API}/auth").json()["elevated"] is True
    assert _audit(owner, "admin.passkey_added", world.admin) == 1
    assert _audit(owner, "admin.signed_in_with_passkey", world.admin) == 1
    assert client.get(MINE, headers=inside).json()["items"][0]["last_used_at"] is not None
    # What is kept is the public key: nothing of it signs.
    stored = owner.execute("SELECT public_key FROM admin_passkey WHERE id = %s", (passkey_id,)).fetchone()
    assert stored is not None and passkey.b64(bytes(stored[0])) == device.public_key


def test_an_accepted_answer_is_accepted_once(client: TestClient, inside: dict[str, str]) -> None:
    device = Device()
    _registered(client, inside, device)
    answer = device.answer(_challenge(client))
    assert client.post(SIGN_IN, json=answer).status_code == 200
    client.cookies.clear()
    assert client.post(SIGN_IN, json=answer).status_code == 401
    assert client.get(ME).status_code == 401


@pytest.mark.parametrize(
    "changed",
    [
        {"origin": "https://evil.example"},
        {"host": "evil.example"},
        {"flags": PRESENT},  # the holder was not verified
        {"flags": VERIFIED},  # nobody touched the device
        {"kind": "webauthn.create"},
        {"signer": Device()},
    ],
    ids=["another page", "another site", "holder not verified", "nobody present", "another purpose", "another key"],
)
def test_an_answer_that_is_not_ours_is_refused(
    client: TestClient, inside: dict[str, str], changed: dict[str, Any]
) -> None:
    device = Device()
    _registered(client, inside, device)
    refused = client.post(SIGN_IN, json=device.answer(_challenge(client), **changed))
    assert refused.status_code == 401
    assert refused.json() == client.post(SIGN_IN, json=Device().answer(_challenge(client))).json()
    assert client.get(ME).status_code == 401


def test_a_challenge_is_good_for_five_minutes_and_one_purpose(
    client: TestClient, inside: dict[str, str], admin_env: AdminEnv
) -> None:
    device = Device()
    _registered(client, inside, device)
    # A registration challenge is not a sign-in challenge, and a made-up one is nobody's.
    register_challenge = client.get(f"{MINE}/challenge", headers=inside).json()["challenge"]
    assert client.post(SIGN_IN, json=device.answer(register_challenge)).status_code == 401
    assert client.post(SIGN_IN, json=device.answer(passkey.b64(os.urandom(41)))).status_code == 401
    old = _challenge(client)
    admin_env.clock.offset += passkey.CHALLENGE_LIFE - timedelta(seconds=1)
    still = _challenge(client)
    admin_env.clock.offset += timedelta(seconds=2)
    assert client.post(SIGN_IN, json=device.answer(old)).status_code == 401
    assert client.post(SIGN_IN, json=device.answer(still)).status_code == 200


def test_a_device_that_counts_must_count_upwards(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection
) -> None:
    device = Device(counts=True)
    _registered(client, inside, device)
    assert client.post(SIGN_IN, json=device.answer(_challenge(client))).status_code == 200
    assert client.post(SIGN_IN, json=device.answer(_challenge(client))).status_code == 200
    device.count -= 2  # a copy of the key, made before those two
    client.cookies.clear()
    assert client.post(SIGN_IN, json=device.answer(_challenge(client))).status_code == 401
    assert _audit(owner, "admin.passkey_counter_went_back", world.admin) == 1


def test_an_ended_passkey_signs_nobody_in(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection
) -> None:
    device = Device()
    passkey_id = _registered(client, inside, device)
    assert client.post(f"{MINE}/{passkey_id}/remove", headers={**inside, **_once()}).json() == {"removed": True}
    assert client.get(MINE, headers=inside).json()["items"] == []
    assert client.post(SIGN_IN, json=device.answer(_challenge(client))).status_code == 401
    assert client.post(f"{MINE}/{passkey_id}/remove", headers={**inside, **_once()}).status_code == 404
    assert _audit(owner, "admin.passkey_removed", world.admin) == 1


def test_a_passkey_of_someone_taken_off_the_allow_list_opens_nothing(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    device = Device()
    _registered(client, inside, device)
    admin_env.allowed.discard(_tg_id(owner, world.admin))
    assert client.post(SIGN_IN, json=device.answer(_challenge(client))).status_code == 401


# --- who may register, and what -------------------------------------------------------------------------


def test_only_an_administrator_inside_reaches_their_passkeys(client: TestClient) -> None:
    calls = (
        client.get(MINE),
        client.get(f"{MINE}/challenge"),
        client.post(MINE, json={}, headers=_once()),
        client.post(f"{MINE}/{uuid.uuid4()}/remove", headers=_once()),
    )
    assert [call.status_code for call in calls] == [401, 401, 401, 401]


def test_a_write_wants_a_key_of_its_own_and_a_repeat_changes_nothing(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection
) -> None:
    device = Device()
    body = device.register(client.get(f"{MINE}/challenge", headers=inside).json()["challenge"])
    keyless = client.post(MINE, json=body, headers=inside)
    assert keyless.status_code == 422 and "Idempotency-Key" in keyless.json()["error"]["fields"]
    once = _once()
    first = client.post(MINE, json=body, headers={**inside, **once})
    again = client.post(MINE, json=body, headers={**inside, **once})
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    row = owner.execute("SELECT count(*) FROM admin_passkey WHERE user_id = %s", (world.admin,)).fetchone()
    assert row is not None and row[0] == 1


@pytest.mark.parametrize(
    "changed",
    [{"origin": "https://evil.example"}, {"host": "evil.example"}, {"flags": PRESENT}],
    ids=["another page", "another site", "holder not verified"],
)
def test_a_registration_that_is_not_ours_stores_nothing(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection, changed: dict[str, Any]
) -> None:
    challenge = client.get(f"{MINE}/challenge", headers=inside).json()["challenge"]
    refused = client.post(MINE, json=Device().register(challenge, **changed), headers={**inside, **_once()})
    assert refused.status_code == 422 and list(refused.json()["error"]["fields"]) == ["passkey"]
    row = owner.execute("SELECT count(*) FROM admin_passkey WHERE user_id = %s", (world.admin,)).fetchone()
    assert row is not None and row[0] == 0


def test_a_registration_answers_the_challenge_of_the_one_who_asked(
    client: TestClient, inside: dict[str, str], world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    # A challenge of the sign-in kind, and one a different administrator asked for, register nothing.
    assert (
        client.post(MINE, json=Device().register(_challenge(client)), headers={**inside, **_once()}).status_code == 422
    )
    other = SITE.key, passkey.REGISTER, admin_env.clock.now(), uuid.uuid4().bytes
    theirs = passkey.b64(passkey.new_challenge(*other))
    assert client.post(MINE, json=Device().register(theirs), headers={**inside, **_once()}).status_code == 422
    row = owner.execute("SELECT count(*) FROM admin_passkey WHERE user_id = %s", (world.admin,)).fetchone()
    assert row is not None and row[0] == 0


def test_a_credential_is_registered_once_and_a_key_must_be_one(client: TestClient, inside: dict[str, str]) -> None:
    device = Device()
    _registered(client, inside, device)
    again = client.get(f"{MINE}/challenge", headers=inside).json()
    assert again["exclude"] == [passkey.b64(device.credential)]
    twice = client.post(MINE, json=device.register(again["challenge"]), headers={**inside, **_once()})
    assert twice.status_code == 422 and twice.json()["error"]["fields"] == {"passkey": "exists"}
    broken = Device().register(client.get(f"{MINE}/challenge", headers=inside).json()["challenge"])
    broken["public_key"] = passkey.b64(os.urandom(91))
    assert client.post(MINE, json=broken, headers={**inside, **_once()}).status_code == 422


def test_without_a_site_there_are_no_passkeys(
    app_database_url: str, admin_database_url: str, admin_env: AdminEnv, inside: dict[str, str]
) -> None:
    for bare in _app(app_database_url, admin_database_url, admin_env, None):
        assert bare.post(SIGN_IN, json={}).status_code == 404
        assert bare.get(MINE, headers=inside).status_code == 404


def test_a_host_that_is_not_a_bare_host_name_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from qarz.infrastructure.settings import Settings

    monkeypatch.delenv("QD_PASSKEY_HOST", raising=False)
    assert Settings().passkey_site_host() is None
    monkeypatch.setenv("QD_PASSKEY_HOST", "Admin.Hisobox.UZ")
    assert Settings().passkey_site_host() == "admin.hisobox.uz"
    for bad in ("https://admin.hisobox.uz", "admin.hisobox.uz:443", "admin.hisobox.uz/panel", "localhost", "a b.uz"):
        monkeypatch.setenv("QD_PASSKEY_HOST", bad)
        with pytest.raises(ValueError, match="QD_PASSKEY_HOST"):
            Settings().passkey_site_host()
