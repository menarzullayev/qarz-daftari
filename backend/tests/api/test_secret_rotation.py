"""Rotating the server secret without locking administrators out (operations runbook 4)."""

import asyncio
import base64
import hashlib
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.application.ports import SecretUnreadable
from qarz.application.secret_rotation import RotationResult, rotate_admin_secrets
from qarz.domain import totp
from qarz.domain.file_links import derive_key as link_key
from qarz.domain.file_links import expiry, sign
from qarz.domain.files import object_key
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.infrastructure.secret_box import SecretBox, derive_key
from qarz.infrastructure.settings import Settings
from qarz.interface import rotate_secrets
from qarz.interface.http import create_app

from .conftest import (
    ADMIN_API,
    TEST_BOT_TOKEN,
    AdminEnv,
    HeaderAuthenticator,
    World,
    allow_list,
    as_user,
    fresh_code,
)

pytestmark = pytest.mark.db

OLD = "the-old-server-secret-0123456789abcdef"
NEW = "the-new-server-secret-0123456789abcdef"
WRONG = "some-other-server-secret-0123456789abc"


@pytest.fixture(autouse=True)
def _only_these_accounts(owner: psycopg.Connection) -> None:
    """The command reads every administrator account, and the test database is shared by the session:
    start from none, so that the counts are this test's."""
    owner.execute("DELETE FROM admin_session")
    owner.execute("DELETE FROM admin_account")


def _person(owner: psycopg.Connection) -> uuid.UUID:
    user = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user, uuid.uuid4().int % 10**15))
    return user


def _account(owner: psycopg.Connection, sealed_by: Any) -> tuple[uuid.UUID, bytes]:
    """An administrator whose second-factor secret is stored as `sealed_by` makes it."""
    user, secret = _person(owner), os.urandom(totp.SECRET_BYTES)
    owner.execute(
        "INSERT INTO admin_account (user_id, totp_secret, confirmed_at) VALUES (%s, %s, now())",
        (user, sealed_by(secret, user.bytes)),
    )
    return user, secret


def _before_key_ids(server_secret: str) -> Any:
    """Seals as the application did before ciphertexts said which key made them."""

    def seal(plaintext: bytes, context: bytes) -> bytes:
        nonce = os.urandom(12)
        return b"\x01" + nonce + AESGCM(derive_key(server_secret)).encrypt(nonce, plaintext, context)

    return seal


def _stored(owner: psycopg.Connection) -> dict[uuid.UUID, bytes]:
    return {row[0]: bytes(row[1]) for row in owner.execute("SELECT user_id, totp_secret FROM admin_account")}


def _everything_else(owner: psycopg.Connection) -> list[Any]:
    return owner.execute(
        "SELECT user_id, status, confirmed_at, failed_codes, locked_until, last_step FROM admin_account ORDER BY 1"
    ).fetchall()


def _rotate(app_database_url: str, current: str, previous: str = "") -> RotationResult:
    """The command's work, as the application role: what it may do after migration 0027 is enough."""
    return asyncio.run(
        rotate_secrets.run(Settings(database_url=app_database_url, secrets_key=current, secrets_key_previous=previous))
    )


def test_the_command_moves_what_the_previous_key_reads_and_counts_the_rest(
    app_database_url: str, owner: psycopg.Connection
) -> None:
    old_a, secret_a = _account(owner, SecretBox(OLD).encrypt)
    old_b, secret_b = _account(owner, _before_key_ids(OLD))
    new_c, secret_c = _account(owner, SecretBox(NEW).encrypt)
    new_d, secret_d = _account(owner, _before_key_ids(NEW))
    lost_e, _ = _account(owner, SecretBox(WRONG).encrypt)
    lost_f, _ = _account(owner, lambda secret, context: b"test-only")
    before, untouched = _stored(owner), _everything_else(owner)

    result = _rotate(app_database_url, NEW, OLD)
    assert (result.reencrypted, result.already_current) == (2, 2)
    assert set(result.unreadable) == {lost_e, lost_f}

    after = _stored(owner)
    only_new = SecretBox(NEW)
    for user, secret in ((old_a, secret_a), (old_b, secret_b)):
        assert after[user] != before[user]
        assert only_new.decrypt(after[user], user.bytes) == secret, "readable once the previous key is gone"
    for user in (new_c, new_d, lost_e, lost_f):
        assert after[user] == before[user], "what was current or unreadable is left exactly as it was"
    assert only_new.decrypt(after[new_c], new_c.bytes) == secret_c
    assert only_new.decrypt(after[new_d], new_d.bytes) == secret_d
    assert _everything_else(owner) == untouched, "only the secret's bytes change"


def test_running_it_twice_changes_nothing_the_second_time(app_database_url: str, owner: psycopg.Connection) -> None:
    _account(owner, SecretBox(OLD).encrypt)
    _account(owner, _before_key_ids(OLD))
    lost, _ = _account(owner, SecretBox(WRONG).encrypt)
    first = _rotate(app_database_url, NEW, OLD)
    assert (first.reencrypted, first.already_current, first.unreadable) == (2, 0, (lost,))
    once = _stored(owner)

    second = _rotate(app_database_url, NEW, OLD)
    assert (second.reencrypted, second.already_current, second.unreadable) == (0, 2, (lost,))
    assert _stored(owner) == once, "not one byte is written again"
    # Nor after the previous secret has been taken out of the environment.
    third = _rotate(app_database_url, NEW)
    assert (third.reencrypted, third.already_current, third.unreadable) == (0, 2, (lost,))
    assert _stored(owner) == once


@pytest.mark.parametrize("previous", [WRONG, "", NEW], ids=["a wrong previous key", "none given", "the same key"])
def test_a_wrong_previous_key_changes_nothing(app_database_url: str, owner: psycopg.Connection, previous: str) -> None:
    user, secret = _account(owner, SecretBox(OLD).encrypt)
    legacy, _ = _account(owner, _before_key_ids(OLD))
    before = _stored(owner)
    result = _rotate(app_database_url, NEW, previous)
    assert (result.reencrypted, result.already_current) == (0, 0)
    assert set(result.unreadable) == {user, legacy}
    assert _stored(owner) == before
    # The right previous key afterwards still finds everything where it was.
    assert _rotate(app_database_url, NEW, OLD).reencrypted == 2
    assert SecretBox(NEW).decrypt(_stored(owner)[user], user.bytes) == secret


def test_it_is_one_transaction(app_database_url: str, owner: psycopg.Connection) -> None:
    """A failure part of the way leaves every secret as it was: never half under one key, half under another."""
    for _ in range(3):
        _account(owner, SecretBox(OLD).encrypt)
    before = _stored(owner)

    class FailsOnTheThird:
        def __init__(self) -> None:
            self.box, self.seen = SecretBox(NEW, OLD), 0

        def reseal(self, ciphertext: bytes, context: bytes) -> bytes | None:
            self.seen += 1
            if self.seen == 3:
                raise RuntimeError("the third one fails")
            return self.box.reseal(ciphertext, context)

    async def attempt() -> None:
        database = Database(app_database_url)
        try:
            await rotate_admin_secrets(database, FailsOnTheThird())
        finally:
            await database.dispose()

    with pytest.raises(RuntimeError, match="the third one fails"):
        asyncio.run(attempt())
    assert _stored(owner) == before


def test_the_command_prints_three_counts_and_who_must_enrol_again_but_never_a_secret(
    app_database_url: str,
    owner: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    moved, secret = _account(owner, SecretBox(OLD).encrypt)
    _account(owner, SecretBox(NEW).encrypt)
    lost, lost_secret = _account(owner, SecretBox(WRONG).encrypt)
    monkeypatch.setenv("QD_DATABASE_URL", app_database_url)
    monkeypatch.setenv("QD_SECRETS_KEY", NEW)
    monkeypatch.setenv("QD_SECRETS_KEY_PREVIOUS", OLD)
    monkeypatch.setattr("sys.argv", ["rotate_secrets"])

    with pytest.raises(SystemExit) as stopped:
        rotate_secrets.main()
    assert stopped.value.code == 1, "somebody has to enrol again: the operator's script must notice"
    printed = capsys.readouterr()
    assert printed.out == (
        "re-encrypted under the current key: 1\n"
        "already under the current key: 1\n"
        "readable under neither key: 1\n"
        f"  must enrol again: administrator account {lost}\n"
    )
    stored = _stored(owner)
    shown = printed.out + printed.err
    for value in (secret, lost_secret, *stored.values(), derive_key(NEW), derive_key(OLD)):
        for spelling in (value.hex(), base64.b64encode(value).decode(), base64.b32encode(value).decode()):
            assert spelling not in shown
    for value in (NEW, OLD, app_database_url):
        assert value not in shown
    assert str(moved) not in shown, "only the accounts that could not be read are named"

    # With nobody left unreadable the command ends normally.
    owner.execute("DELETE FROM admin_account WHERE user_id = %s", (lost,))
    rotate_secrets.main()
    assert capsys.readouterr().out == (
        "re-encrypted under the current key: 0\nalready under the current key: 2\nreadable under neither key: 0\n"
    )


@pytest.mark.parametrize(
    ("current", "previous"), [("", OLD), ("short", OLD), (NEW, "short")], ids=["no key", "short key", "short previous"]
)
def test_the_command_refuses_an_unusable_key_before_it_touches_anything(
    app_database_url: str,
    owner: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    current: str,
    previous: str,
) -> None:
    _account(owner, SecretBox(OLD).encrypt)
    before = _stored(owner)
    monkeypatch.setenv("QD_DATABASE_URL", app_database_url)
    monkeypatch.setenv("QD_SECRETS_KEY", current)
    monkeypatch.setenv("QD_SECRETS_KEY_PREVIOUS", previous)
    monkeypatch.setattr("sys.argv", ["rotate_secrets"])
    with pytest.raises(SystemExit) as stopped:
        rotate_secrets.main()
    assert stopped.value.code == 2
    printed = capsys.readouterr()
    assert "too short" in printed.err and printed.out == ""
    assert OLD not in printed.err and printed.err != "short\n"
    assert _stored(owner) == before


# --- the application across a rotation ------------------------------------------------------------------


@contextmanager
def _application(app_database_url: str, env: AdminEnv, current: str, previous: str = "") -> Iterator[TestClient]:
    """The application as it starts with these two settings."""
    database = Database(app_database_url)
    access = AdminAccess(database, allowed_tg_ids=env.allowed, cipher=SecretBox(current, previous), now=env.clock.now)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=access,
        authenticator=HeaderAuthenticator(),
        now=env.clock.now,
        secrets_key=current,
        previous_secrets_key=previous or None,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def _sign_in(client: TestClient, env: AdminEnv, user: uuid.UUID, secret: bytes) -> Any:
    return client.post(f"{ADMIN_API}/auth/session", json={"code": fresh_code(env, secret)}, headers=as_user(user))


def test_administrators_sign_in_with_their_old_device_before_during_and_after_the_rotation(
    app_database_url: str, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    admin_env.clock.freeze()
    people = [_account(owner, SecretBox(OLD).encrypt), _account(owner, _before_key_ids(OLD))]
    for user, _ in people:
        allow_list(owner, admin_env, user)

    def everyone_signs_in(client: TestClient, when: str) -> None:
        for user, secret in people:
            response = _sign_in(client, admin_env, user, secret)
            assert response.status_code == 201, (when, response.text)
            shops = client.get(
                f"{ADMIN_API}/shops", headers={**as_user(user), "Cookie": response.headers["set-cookie"].split(";")[0]}
            )
            assert shops.status_code == 200, (when, shops.text)

    with _application(app_database_url, admin_env, OLD) as before:
        everyone_signs_in(before, "before the rotation")

    with _application(app_database_url, admin_env, NEW, OLD) as during:
        everyone_signs_in(during, "restarted with both secrets, before the command")
        result = _rotate(app_database_url, NEW, OLD)
        assert (result.reencrypted, result.already_current, result.unreadable) == (2, 0, ())
        everyone_signs_in(during, "after the command, the previous secret still set")

    with _application(app_database_url, admin_env, NEW) as after:
        everyone_signs_in(after, "the previous secret removed")

    # The old secret alone no longer opens anything: the stored secrets are under the new key.
    with _application(app_database_url, admin_env, OLD) as stale:
        user, secret = people[0]
        refused = _sign_in(stale, admin_env, user, secret)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SECOND_FACTOR_INVALID")


def test_without_the_previous_secret_a_changed_key_locks_administrators_out(
    app_database_url: str, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """What the second setting is for: the same restart without it refuses a right code."""
    admin_env.clock.freeze()
    user, secret = _account(owner, SecretBox(OLD).encrypt)
    allow_list(owner, admin_env, user)
    with _application(app_database_url, admin_env, NEW) as changed:
        refused = _sign_in(changed, admin_env, user, secret)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SECOND_FACTOR_INVALID")
    with pytest.raises(SecretUnreadable):
        SecretBox(NEW).decrypt(_stored(owner)[user], user.bytes)
    with _application(app_database_url, admin_env, NEW, OLD) as both:
        assert _sign_in(both, admin_env, user, secret).status_code == 201


def test_a_new_enrolment_during_a_rotation_is_stored_under_the_current_key(
    app_database_url: str, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    user = _person(owner)
    allow_list(owner, admin_env, user)
    with _application(app_database_url, admin_env, NEW, OLD) as during:
        enrolled = during.post(
            f"{ADMIN_API}/auth/enrolment", headers={**as_user(user), "Idempotency-Key": f"enrol-{uuid.uuid4().hex}"}
        )
        assert enrolled.status_code == 201, enrolled.text
    stored = _stored(owner)[user]
    assert len(SecretBox(NEW).decrypt(stored, user.bytes)) == totp.SECRET_BYTES
    with pytest.raises(SecretUnreadable):
        SecretBox(OLD).decrypt(stored, user.bytes)
    assert _rotate(app_database_url, NEW, OLD).already_current == 1


# --- file links across a rotation -----------------------------------------------------------------------


def _stored_file(owner: psycopg.Connection, world: World, root: Path) -> uuid.UUID:
    file_id, key = uuid.uuid4(), object_key(uuid.uuid4().hex)
    asyncio.run(FilesystemFileStore(root).put(key, b"the file", "image/jpeg"))
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'payment_notice', %s, %s, 8, 'image/jpeg', now() + interval '30 days')",
        (file_id, world.shop_a, key, hashlib.sha256(b"the file").digest()),
    )
    return file_id


@contextmanager
def _file_application(
    app_database_url: str, env: AdminEnv, root: Path, current: str, previous: str | None
) -> Iterator[TestClient]:
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        now=env.clock.now,
        file_store=FilesystemFileStore(root),
        secrets_key=current,
        previous_secrets_key=previous,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def test_a_link_signed_just_before_the_restart_works_for_the_minutes_it_has_left(
    app_database_url: str, owner: psycopg.Connection, world: World, admin_env: AdminEnv, file_root: Path
) -> None:
    file_id = _stored_file(owner, world, file_root)
    admin_env.clock.freeze()
    ends = expiry(admin_env.clock.now())
    old_link = sign(link_key(OLD), world.shop_a, file_id, ends)
    new_link = sign(link_key(NEW), world.shop_a, file_id, ends)
    wrong_link = sign(link_key(WRONG), world.shop_a, file_id, ends)

    def served(client: TestClient, link: str) -> int:
        return client.get(f"/files/{link}").status_code

    # Without the previous secret, as before this change and as once the rotation is finished.
    with _file_application(app_database_url, admin_env, file_root, NEW, None) as alone:
        assert (served(alone, new_link), served(alone, old_link)) == (200, 404)

    with _file_application(app_database_url, admin_env, file_root, NEW, OLD) as during:
        assert (served(during, new_link), served(during, old_link), served(during, wrong_link)) == (200, 200, 404)
        # An old link gets no longer life from the rotation: its own five minutes, to the second.
        admin_env.clock.offset += ends - admin_env.clock.now()
        assert served(during, old_link) == 200
        admin_env.clock.offset += timedelta(seconds=1)
        assert (served(during, old_link), served(during, new_link)) == (404, 404)


def test_a_previous_secret_too_short_to_sign_with_stops_the_start(app_database_url: str) -> None:
    database = Database(app_database_url)
    with pytest.raises(ValueError, match="too short"):
        create_app(database.reachable, database, secrets_key=NEW, previous_secrets_key="short")
    asyncio.run(database.dispose())
