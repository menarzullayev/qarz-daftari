"""API fixtures: the real application wired to the real database as the restricted role qd_app.

Only authentication is replaced: until story S2.1 delivers Telegram sign-in, a test authenticator reads
the caller's user identifier from a header. It exists only in the test suite.
"""

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

TEST_USER_HEADER = "X-Test-User"
WEBHOOK_SECRET = "test-webhook-secret-0123456789"
TEST_BOT_TOKEN = "1234567890:TEST-ONLY-token-not-a-real-bot"


class HeaderAuthenticator:
    async def user_id(self, request: Request) -> uuid.UUID | None:
        raw = request.headers.get(TEST_USER_HEADER)
        if raw is None:
            return None
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None


@pytest.fixture
def client(app_database_url: str) -> Iterator[TestClient]:
    database = Database(app_database_url)
    auth = AuthService(database, TEST_BOT_TOKEN)
    app = create_app(
        database.reachable, database, auth=auth, authenticator=HeaderAuthenticator(), webhook_secret=WEBHOOK_SECRET
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


@dataclass(frozen=True)
class World:
    """Two shops and every kind of caller the authorization suite needs."""

    shop_a: uuid.UUID
    shop_b: uuid.UUID
    owner_a: uuid.UUID
    manager_a: uuid.UUID
    seller_a: uuid.UUID
    suspended_a: uuid.UUID
    owner_b: uuid.UUID
    customer_of_a: uuid.UUID
    admin: uuid.UUID
    stranger: uuid.UUID
    seller_a_membership: uuid.UUID
    manager_a_membership: uuid.UUID
    owner_a_membership: uuid.UUID
    invitation_a: str  # identifier (hex of the token hash) of an issued staff invitation in shop A
    invitation_a_token: str
    customer_a: uuid.UUID  # owes 50 000 UZS through entry_a; linked to the user customer_of_a
    settled_customer_a: uuid.UUID  # owes nothing
    archived_customer_a: uuid.UUID
    entry_a: uuid.UUID  # a credit sale of 50 000 UZS to customer_a, promised a week from today


def _user(conn: psycopg.Connection, lang: str = "uz") -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, %s)", (user_id, uuid.uuid4().int % 10**15, lang)
    )
    return user_id


def _member(conn: psycopg.Connection, shop: uuid.UUID, user: uuid.UUID, role: str, status: str = "active") -> uuid.UUID:
    membership_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, %s, %s)",
        (membership_id, shop, user, role, status),
    )
    return membership_id


@pytest.fixture
def world(owner: psycopg.Connection) -> World:
    shop_a, shop_b = uuid.uuid4(), uuid.uuid4()
    owner.execute("INSERT INTO shop (id, name) VALUES (%s, 'Shop A'), (%s, 'Shop B')", (shop_a, shop_b))
    users = {name: _user(owner) for name in ("owner_a", "manager_a", "seller_a", "suspended_a", "owner_b")}
    users |= {name: _user(owner) for name in ("customer_of_a", "admin", "stranger")}
    owner_membership = _member(owner, shop_a, users["owner_a"], "owner")
    manager_membership = _member(owner, shop_a, users["manager_a"], "manager")
    seller_membership = _member(owner, shop_a, users["seller_a"], "seller")
    _member(owner, shop_a, users["suspended_a"], "manager", status="suspended")
    _member(owner, shop_b, users["owner_b"], "owner")

    # A customer of shop A with an active, consented link: a real user of the service, but not staff.
    customer_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Ali', 'ali')",
        (customer_id, shop_a),
    )
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (uuid.uuid4(), shop_a, customer_id, users["customer_of_a"]),
    )
    settled_customer, archived_customer, entry_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm, status) VALUES "
        "(%s, %s, 'Vali', 'vali', 'active'), (%s, %s, 'Sobir', 'sobir', 'archived')",
        (settled_customer, shop_a, archived_customer, shop_a),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'credit', 50000, %s)",
        (entry_id, shop_a, customer_id, seller_membership),
    )
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
        "VALUES (%s, %s, %s, current_date + 7, 'default')",
        (uuid.uuid4(), shop_a, entry_id),
    )
    owner.execute(
        "INSERT INTO subscription (shop_id, state, trial_ends) VALUES "
        "(%s, 'trial', current_date + 30), (%s, 'trial', current_date + 30)",
        (shop_a, shop_b),
    )
    # A platform administrator with no support access to any shop.
    owner.execute("INSERT INTO admin_account (user_id, totp_secret) VALUES (%s, %s)", (users["admin"], b"test-only"))
    token = f"world-invitation-{uuid.uuid4().hex}"
    digest = hashlib.sha256(token.encode()).digest()
    owner.execute(
        "INSERT INTO invitation (token_hash, shop_id, kind, role, expires_at) "
        "VALUES (%s, %s, 'staff', 'seller', now() + interval '7 days')",
        (digest, shop_a),
    )
    return World(
        shop_a=shop_a,
        shop_b=shop_b,
        seller_a_membership=seller_membership,
        manager_a_membership=manager_membership,
        owner_a_membership=owner_membership,
        invitation_a=digest.hex(),
        invitation_a_token=token,
        customer_a=customer_id,
        settled_customer_a=settled_customer,
        archived_customer_a=archived_customer,
        entry_a=entry_id,
        **users,
    )


def as_user(user_id: uuid.UUID) -> dict[str, str]:
    return {TEST_USER_HEADER: str(user_id)}


class MovableClock:
    def __init__(self) -> None:
        self.offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset


@dataclass
class SessionClient:
    http: TestClient
    clock: MovableClock


@pytest.fixture
def session_client(app_database_url: str) -> Iterator[SessionClient]:
    """The application exactly as deployed: Telegram-backed sessions, no test authenticator."""
    database = Database(app_database_url)
    clock = MovableClock()
    auth = AuthService(database, TEST_BOT_TOKEN, clock.now)
    app = create_app(database.reachable, database, auth=auth, webhook_secret=WEBHOOK_SECRET)
    with TestClient(app) as test_client:
        yield SessionClient(test_client, clock)
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
