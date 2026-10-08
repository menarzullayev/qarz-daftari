"""Request identifiers, log lines and metrics (operations document: Monitoring, Logging)."""

import json
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app
from qarz.interface.observability import LOG_FIELDS, JsonFormatter, Metrics, configure_logging, security_kind

from .conftest import TEST_BOT_TOKEN, WEBHOOK_SECRET, HeaderAuthenticator, World, as_user

pytestmark = pytest.mark.db

TOKEN = "a-metrics-token-for-tests"


@pytest.fixture
def observed(app_database_url: str) -> Iterator[TestClient]:
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        webhook_secret=WEBHOOK_SECRET,
        metrics_token=TOKEN,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
        client.portal.call(database.dispose)  # type: ignore[union-attr]


def lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    formatter = JsonFormatter()
    parsed = [json.loads(formatter.format(record)) for record in caplog.records if record.name == "qarz.request"]
    return [line for line in parsed if line["event"] == event]


def metrics(client: TestClient) -> str:
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    return response.text


def test_every_answer_carries_a_request_identifier_and_is_logged_with_it(
    observed: TestClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="qarz.request")
    first = observed.get(f"/api/v1/shops/{world.shop_a}/customers?q=Vali", headers=as_user(world.owner_a))
    second = observed.get("/healthz")
    ids = [first.headers["X-Request-Id"], second.headers["X-Request-Id"]]
    assert all(len(value) == 32 for value in ids) and ids[0] != ids[1]

    logged = lines(caplog, "request")
    assert [line["request_id"] for line in logged] == ids
    assert logged[0] == {
        "ts": logged[0]["ts"],
        "level": "info",
        "logger": "qarz.request",
        "event": "request",
        "request_id": ids[0],
        "method": "GET",
        "route": "/api/v1/shops/{shop_id}/customers",
        "status": 200,
        "ms": logged[0]["ms"],
        "user_id": str(world.owner_a),
        "shop_id": str(world.shop_a),
    }
    assert logged[0]["ms"] >= 0
    assert (logged[1]["route"], logged[1]["status"]) == ("/healthz", 200)
    assert "user_id" not in logged[1] and "shop_id" not in logged[1]
    # Neither what was searched for nor anything found is in the log.
    assert "Vali" not in json.dumps(logged, ensure_ascii=False)


def test_an_identifier_given_by_the_proxy_is_kept_and_anything_else_is_replaced(observed: TestClient) -> None:
    kept = observed.get("/healthz", headers={"X-Request-Id": "proxy-0123456789"})
    assert kept.headers["X-Request-Id"] == "proxy-0123456789"
    for given in ("short", "has space in it!", "x" * 65, 'quote"and\\slash', "ünïcödé-ïdentifier"):
        answered = observed.get("/healthz", headers={"X-Request-Id": given.encode("utf-8")})
        assert answered.headers["X-Request-Id"] != given
        assert len(answered.headers["X-Request-Id"]) == 32
    assert observed.get("/healthz").headers.get_list("X-Request-Id").__len__() == 1


def test_paths_nobody_routes_are_one_label(observed: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """Paths are chosen by the caller: as labels they would grow without bound and could carry anything."""
    caplog.set_level(logging.INFO, logger="qarz.request")
    for path in ("/nothing/here", "/api/v1/unknown/+998901234567", "/wp-login.php"):
        assert observed.get(path).status_code == 404
    assert [line["route"] for line in lines(caplog, "request")] == ["unmatched"] * 3
    text = metrics(observed)
    assert 'qd_requests_total{method="GET",route="unmatched",status="404"} 3' in text
    assert "998901234567" not in text and "wp-login" not in text


def test_requests_are_counted_and_timed_by_route(observed: TestClient, world: World) -> None:
    for _ in range(3):
        observed.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a))
    observed.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.manager_a))
    observed.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.seller_a))
    text = metrics(observed)
    assert 'qd_requests_total{method="GET",route="/api/v1/shops/{shop_id}",status="200"} 4' in text
    assert 'qd_requests_total{method="GET",route="/api/v1/shops/{shop_id}/subscription",status="403"} 1' in text
    labels = 'method="GET",route="/api/v1/shops/{shop_id}"'
    assert f'qd_request_duration_ms_bucket{{{labels},le="+Inf"}} 4' in text
    assert f"qd_request_duration_ms_count{{{labels}}} 4" in text
    assert str(world.shop_a) not in text and str(world.owner_a) not in text


def test_metrics_exist_only_with_the_token(observed: TestClient, client: TestClient) -> None:
    missing = observed.get("/nothing/here")
    for headers in ({}, {"Authorization": "Bearer wrong-token-0123456789"}, {"Authorization": TOKEN}):
        refused = observed.get("/metrics", headers=headers)
        assert (refused.status_code, refused.json()) == (404, missing.json())
    # The application of the other tests was built without a token: no endpoint at all.
    assert client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    with pytest.raises(ValueError, match="too short"):
        create_app(lambda: None, metrics_token="short")  # type: ignore[arg-type,return-value]


def test_security_events_are_logged_and_counted(
    observed: TestClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="qarz.request")
    observed.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.owner_b))
    observed.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.stranger))
    observed.post("/api/v1/auth/telegram-webapp", json={"init_data": "user=%7B%7D&hash=00"})
    observed.post("/tg/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    # None of these is one: nobody is signed in; a member is refused by role; a member's missing record.
    observed.get(f"/api/v1/shops/{world.shop_a}/customers")
    observed.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.seller_a))
    observed.get("/api/v1/me", headers=as_user(world.owner_a))

    events = lines(caplog, "security")
    assert [(line["kind"], line["status"], line["level"]) for line in events] == [
        ("shop_not_member", 404, "warning"),
        ("shop_not_member", 404, "warning"),
        ("bad_sign_in", 401, "warning"),
        ("bad_webhook_secret", 403, "warning"),
    ]
    assert (events[0]["user_id"], events[0]["shop_id"]) == (str(world.owner_b), str(world.shop_a))
    text = metrics(observed)
    assert 'qd_security_events_total{kind="shop_not_member"} 2' in text
    assert 'qd_security_events_total{kind="bad_sign_in"} 1' in text
    assert 'qd_security_events_total{kind="bad_webhook_secret"} 1' in text


def test_which_answers_are_security_events() -> None:
    shop = "/api/v1/shops/{shop_id}/customers/{customer_id}"
    assert security_kind(shop, 404, signed_in=True) == "shop_not_member"
    assert security_kind(shop, 404, signed_in=False) is None
    assert security_kind(shop, 403, signed_in=True) is None
    assert security_kind("/api/v1/me/accounts/{link_id}", 404, signed_in=True) is None
    assert security_kind("/api/v1/auth/telegram-login", 401, signed_in=False) == "bad_sign_in"
    assert security_kind("/api/v1/auth/telegram-login", 422, signed_in=False) is None
    assert security_kind("/api/v1/me", 401, signed_in=False) is None
    assert security_kind("/tg/webhook", 403, signed_in=False) == "bad_webhook_secret"
    assert security_kind("/tg/webhook", 400, signed_in=False) is None


def test_a_failure_nobody_handled_is_answered_with_the_identifier_and_nothing_else(
    app_database_url: str, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="qarz.request")
    database = Database(app_database_url)

    class Broken(HeaderAuthenticator):
        async def user_id(self, request: Any) -> uuid.UUID | None:
            raise RuntimeError("Maxfiy Ism +998901234567")

    app = create_app(database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), authenticator=Broken())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/me", headers=as_user(world.owner_a))
        client.portal.call(database.dispose)  # type: ignore[union-attr]
    request_id = response.headers["X-Request-Id"]
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "ERROR", "message": "Xatolik yuz berdi.", "fields": {}, "request_id": request_id}
    }
    failed = lines(caplog, "request_failed")
    assert len(failed) == 1
    assert (failed[0]["request_id"], failed[0]["status"], failed[0]["error"]) == (request_id, 500, "RuntimeError")
    assert failed[0]["level"] == "error" and failed[0]["trace"]
    # The exception's own text may hold anything; it is in neither the answer nor the log line.
    assert "Maxfiy" not in json.dumps(failed, ensure_ascii=False) and "Maxfiy" not in response.text
    assert lines(caplog, "request") == []


def test_only_the_allowed_fields_reach_a_log_line() -> None:
    record = logging.LogRecord("qarz.x", logging.INFO, __file__, 1, "event_name", None, None)
    record.__dict__.update(
        {"update_id": 7, "job": "reminders", "name": "qarz.x", "customer_name": "Vali", "phone": "+998901234567"}
    )
    line = json.loads(JsonFormatter().format(record))
    assert line == {
        "ts": line["ts"],
        "level": "info",
        "logger": "qarz.x",
        "event": "event_name",
        "update_id": 7,
        "job": "reminders",
    }
    assert not {"name", "phone", "text", "token", "display_name", "recipient"} & set(LOG_FIELDS)


def test_configuring_logs_twice_adds_one_handler() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        # From a logger that was never configured, whatever ran before in this process: a test that
        # built the application the way the server does has left its handler there.
        root.handlers[:] = [handler for handler in before if not isinstance(handler.formatter, JsonFormatter)]
        unconfigured = list(root.handlers)
        configure_logging()
        configure_logging()
        added = [handler for handler in root.handlers if handler not in unconfigured]
        assert len(added) == 1 and isinstance(added[0].formatter, JsonFormatter)
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        root.handlers[:] = before
        root.setLevel(level)
        logging.getLogger("uvicorn.access").disabled = False


def test_the_health_figures_tell_how_long_messages_and_jobs_have_waited(
    observed: TestClient, owner: psycopg.Connection
) -> None:
    owner.execute("DELETE FROM outbox_message")
    owner.execute("DELETE FROM job_run WHERE job LIKE 'obs-%'")
    owner.execute(
        "INSERT INTO outbox_message (id, channel, recipient, payload, next_try_at, status) VALUES "
        "(gen_random_uuid(), 'telegram', '1', '{}', now() - interval '15 minutes', 'pending'), "
        "(gen_random_uuid(), 'telegram', '1', '{}', now() - interval '5 minutes', 'pending'), "
        "(gen_random_uuid(), 'telegram', '1', '{}', now() - interval '2 hours', 'sent'), "
        "(gen_random_uuid(), 'sms', '+998900000000', '{}', now() + interval '1 hour', 'pending')"
    )
    owner.execute(
        "INSERT INTO job_run (job, period, finished_at) VALUES ('obs-job', 'a', now() - interval '3 hours'), "
        "('obs-job', 'b', now() - interval '10 minutes')"
    )
    try:
        text = metrics(observed)
    finally:
        owner.execute("DELETE FROM outbox_message")
        owner.execute("DELETE FROM job_run WHERE job LIKE 'obs-%'")
    values = dict(line.rsplit(" ", 1) for line in text.splitlines() if not line.startswith("#"))
    waited = float(values['qd_outbox_oldest_due_seconds{channel="telegram"}'])
    assert 900 <= waited < 960, "the oldest message that is due and not sent"
    assert 'qd_outbox_oldest_due_seconds{channel="sms"}' not in values, "a message due later is not waiting"
    finished = float(values['qd_job_last_finished_seconds{job="obs-job"}'])
    assert 600 <= finished < 660, "the latest time the job finished"
    assert "998900000000" not in text


def sms_figures(observed: TestClient) -> tuple[dict[str, float], str]:
    text = metrics(observed)
    values = dict(line.rsplit(" ", 1) for line in text.splitlines() if not line.startswith("#"))
    prefix = 'qd_sms_messages_last_day{status="'
    return {name[len(prefix) : -2]: float(value) for name, value in values.items() if name.startswith(prefix)}, text


def test_the_health_figures_count_the_sms_of_the_last_day_by_what_became_of_them(
    observed: TestClient, owner: psycopg.Connection
) -> None:
    """What the rules SmsRefused and SmsNotGoingOut read (deploy/monitoring/alerts.yml)."""
    owner.execute("DELETE FROM outbox_message")
    try:
        nothing, _ = sms_figures(observed)
        assert nothing == {"sent": 0, "failed": 0, "retrying": 0}, "each is there from the start, at zero"
        owner.execute(
            "INSERT INTO outbox_message (id, channel, recipient, payload, next_try_at, status) VALUES "
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() - interval '1 hour', 'sent'), "
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() - interval '23 hours', 'sent'), "
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() - interval '2 hours', 'failed'), "
            # Waiting after an attempt that did not succeed: pending, and its time has not come.
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() + interval '40 seconds', 'pending'), "
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() + interval '1 hour', 'pending'), "
            # Not counted: only just queued and due; older than a day; another channel.
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() - interval '1 second', 'pending'), "
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() - interval '25 hours', 'sent'), "
            "(gen_random_uuid(), 'sms', '+998900000001', '{}', now() - interval '3 days', 'failed'), "
            "(gen_random_uuid(), 'telegram', '1', '{}', now() - interval '1 hour', 'sent'), "
            "(gen_random_uuid(), 'telegram', '1', '{}', now() - interval '1 hour', 'failed'), "
            "(gen_random_uuid(), 'telegram', '1', '{}', now() + interval '1 hour', 'pending')"
        )
        figures, text = sms_figures(observed)
    finally:
        owner.execute("DELETE FROM outbox_message")
    assert figures == {"sent": 2, "failed": 1, "retrying": 2}
    assert "# TYPE qd_sms_messages_last_day gauge" in text
    assert "998900000001" not in text, "counts only: nobody's number"


def test_the_sms_figures_are_read_through_their_own_small_index(owner: psycopg.Connection) -> None:
    """Migration 0032. The outbox keeps every message; without it each reading of /metrics reads it all."""
    found = owner.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'outbox_sms_recent'"
    ).fetchone()
    assert found is not None
    assert found[0].split(" USING ", 1)[1] == "btree (next_try_at) WHERE (channel = 'sms'::text)"


def test_metrics_render_cumulative_buckets() -> None:
    held = Metrics()
    for ms in (10, 25, 30, 250, 6000):  # 25 is itself the edge of the first bucket
        held.observe("GET", "/x", 200, ms)
    text = held.render()
    counts = [line.rsplit(" ", 1)[1] for line in text.splitlines() if line.startswith("qd_request_duration_ms_bucket")]
    assert counts == ["2", "3", "3", "3", "4", "4", "4", "4", "4", "4", "5"]
    assert 'qd_request_duration_ms_sum{method="GET",route="/x"} 6315.0' in text


def test_the_alert_rules_name_routes_jobs_and_events_that_exist(observed: TestClient) -> None:
    """A rule written against a route that was renamed would never fire, and nobody would notice."""
    import re
    from pathlib import Path

    from qarz.application import scheduler
    from qarz.interface import observability

    rules = (Path(__file__).resolve().parents[3] / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    served = {route.path for route in observed.app.routes}  # type: ignore[attr-defined]
    exact = set(re.findall(r'route="([^"]+)"', rules))
    assert exact and exact <= served, exact - served
    for pattern in re.findall(r'route=~"([^"]+)"', rules):
        assert any(re.fullmatch(pattern, path) for path in served), pattern
    jobs = set(re.findall(r'job="([^"]+)"', rules))
    assert jobs == {scheduler.REMINDERS}
    kinds = {value for name, value in vars(observability).items() if name.startswith("SECURITY_")}
    named = set(re.findall(r'kind="([^"]+)"', rules)) | {
        kind for group in re.findall(r'kind=~"([^"]+)"', rules) for kind in group.split("|")
    }
    assert named == kinds
    metrics_named = set(re.findall(r"\bqd_[a-z_]+", rules))
    # Host-level figures the application does not expose: the backup scripts write them on the standby
    # for node_exporter's textfile collector (deploy/backup/scripts; rule group qarz-backup). Each is
    # checked against what the scripts write, so a figure renamed on one side only fails here.
    host_level = {
        "qd_backup_last_run_success",
        "qd_backup_last_success_timestamp_seconds",
        "qd_backup_check_timestamp_seconds",
        "qd_wal_archive_newest_age_seconds",
        "qd_backup_restore_test_last_success_timestamp_seconds",
    }
    assert metrics_named <= {
        "qd_requests_total",
        "qd_request_duration_ms_bucket",
        "qd_security_events_total",
        "qd_outbox_oldest_due_seconds",
        "qd_job_last_finished_seconds",
        "qd_receipts_oldest_waiting_seconds",
        "qd_sms_messages_last_day",
        *host_level,
    }
    # The SMS rules read states the application exposes, and both the states that mean trouble are watched.
    from qarz.infrastructure.db import SMS_STATES

    watched = set(re.findall(r'qd_sms_messages_last_day\{status="([^"]+)"\}', rules))
    assert watched == {"failed", "retrying"} and watched <= set(SMS_STATES)
    scripts = "".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).resolve().parents[3] / "deploy" / "backup" / "scripts").glob("*.sh")
    )
    for name in sorted(host_level):
        assert name in metrics_named, f"no alert rule reads {name}"
        assert f"# TYPE {name} gauge" in scripts, f"no backup script writes {name}"


def test_the_health_figures_tell_how_long_the_oldest_receipt_has_awaited_a_decision(
    observed: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Operations document, monitoring: "receipts awaiting decision, older than 24 hours"."""

    def receipt(shop: Any, status: str, hours: int) -> None:
        owner.execute(
            "INSERT INTO subscription_receipt (id, shop_id, stated_amount, stated_months, status, months, created_at) "
            "VALUES (gen_random_uuid(), %s, 100000, 1, %s, %s, now() - make_interval(hours => %s))",
            (shop, status, 1 if status == "approved" else None, hours),
        )

    def waited() -> float | None:
        values = dict(line.rsplit(" ", 1) for line in metrics(observed).splitlines() if not line.startswith("#"))
        value = values.get('qd_receipts_oldest_waiting_seconds{status="submitted"}')
        return None if value is None else float(value)

    # The database is shared with other tests: what they left waiting is decided first.
    owner.execute(
        "UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'test' WHERE status = 'submitted'"
    )
    assert waited() is None, "nothing waits, so there is nothing to be late with"
    receipt(world.shop_a, "approved", 90)
    receipt(world.shop_a, "rejected", 80)
    assert waited() is None, "a decided receipt does not wait"
    receipt(world.shop_a, "submitted", 2)
    receipt(world.shop_b, "submitted", 30)
    age = waited()
    assert age is not None and 30 * 3600 <= age < 30 * 3600 + 120, "the oldest one, of any shop"
    text = metrics(observed)
    assert str(world.shop_a) not in text and str(world.shop_b) not in text and "Shop" not in text
    # The rule that watches it fires above a day, which this is.
    rules = (Path(__file__).resolve().parents[3] / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    assert "expr: qd_receipts_oldest_waiting_seconds > 86400" in rules
    owner.execute(
        "UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'test' WHERE status = 'submitted'"
    )
    assert waited() is None


def test_every_security_event_is_exposed_at_zero_from_the_start() -> None:
    """The alert rules read increases: a counter that first appears at 1 would hide the first event."""
    from qarz.interface import observability

    text = Metrics().render()
    kinds = {value for name, value in vars(observability).items() if name.startswith("SECURITY_")}
    assert set(observability.EVERY_SECURITY_KIND) == kinds, "a new kind must be exposed from the start too"
    for kind in kinds:
        assert f'qd_security_events_total{{kind="{kind}"}} 0' in text
