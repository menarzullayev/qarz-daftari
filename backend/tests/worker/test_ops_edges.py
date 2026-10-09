"""The edges of the operations watch: Telegram for its alerts, the figure files of the backup jobs, the
disk, the API as a neighbour, the settings, and the command that sends the test alert. Telegram and the
API are always replaced; the files are in a temporary directory.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.methods import SendMessage

from qarz.application.ops_watch import AlertNotDelivered, WorkerHealth
from qarz.domain import ops_alerts as rules
from qarz.infrastructure.ops_probes import ApiProbe, disk_shares, read_figure_files
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.telegram_alerts import TelegramAlerts
from qarz.interface import alert_test
from qarz.interface.worker import build_watch

METHOD = SendMessage(chat_id=1, text="x")


class FakeBot:
    def __init__(self, error: BaseException | None = None, hangs: bool = False) -> None:
        self.error = error
        self.hangs = hangs
        self.calls: list[dict[str, Any]] = []

    async def _answer(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.hangs:
            await asyncio.sleep(3600)
        if self.error is not None:
            raise self.error

    async def send_message(self, **kwargs: Any) -> None:
        await self._answer(**kwargs)

    async def get_me(self, **kwargs: Any) -> None:
        await self._answer(**kwargs)


def alerts(bot: FakeBot, timeout: int = 10) -> TelegramAlerts:
    return TelegramAlerts(bot, timeout=timeout)  # type: ignore[arg-type]


# --- Telegram ----------------------------------------------------------------------------------------------


def test_an_alert_goes_to_the_chat_as_plain_text_with_a_short_timeout() -> None:
    bot = FakeBot()
    asyncio.run(alerts(bot).send(-100123, "⚠️ test"))
    assert bot.calls == [{"chat_id": -100123, "text": "⚠️ test", "request_timeout": 10}]


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (TelegramUnauthorizedError(METHOD, "Unauthorized"), "refused"),
        (TelegramNetworkError(METHOD, "connection reset"), "unreachable"),
        (TelegramServerError(METHOD, "Bad Gateway"), "unreachable"),
        (TelegramRetryAfter(METHOD, "Too Many Requests", retry_after=30), "unreachable"),
        (TimeoutError(), "unreachable"),
        (OSError("no route"), "unreachable"),
        (TelegramBadRequest(METHOD, "Bad Request: chat not found"), "rejected"),
        (TelegramForbiddenError(METHOD, "Forbidden: bot is not a member of the supergroup chat"), "rejected"),
    ],
)
def test_what_telegram_answers_becomes_one_fixed_word(error: BaseException, outcome: str) -> None:
    with pytest.raises(AlertNotDelivered) as raised:
        asyncio.run(alerts(FakeBot(error)).send(1, "x"))
    assert raised.value.outcome == outcome
    # Nothing of Telegram's own words is carried along.
    assert str(raised.value) == outcome and raised.value.__cause__ is None


def test_a_send_that_never_answers_is_given_up() -> None:
    with pytest.raises(AlertNotDelivered) as raised:
        asyncio.run(alerts(FakeBot(hangs=True), timeout=-5).send(1, "x"))  # the outer limit is then at once
    assert raised.value.outcome == "unreachable"


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (None, "ok"),
        (TelegramUnauthorizedError(METHOD, "Unauthorized"), "refused"),
        (TelegramNetworkError(METHOD, "connection reset"), "unreachable"),
        (TelegramServerError(METHOD, "Bad Gateway"), "unreachable"),
        (RuntimeError("anything"), "unreachable"),
    ],
)
def test_whether_telegram_accepts_the_bot(error: BaseException | None, state: str) -> None:
    assert asyncio.run(alerts(FakeBot(error)).state()) == state


# --- the figures the backup jobs write ---------------------------------------------------------------------


def test_the_figure_files_of_a_directory_are_read(tmp_path: Path) -> None:
    (tmp_path / "qd_backup_repo.prom").write_text(
        "# TYPE qd_wal_archive_newest_age_seconds gauge\n"
        "qd_wal_archive_newest_age_seconds 42\n"
        'qd_backup_last_success_timestamp_seconds{type="full"} 1790000000\n',
        encoding="utf-8",
    )
    (tmp_path / "qd_backup_run_diff.prom").write_text('qd_backup_last_run_success{type="diff"} 0\n', encoding="utf-8")
    (tmp_path / "sync.last-success").write_text("1790000123\n", encoding="utf-8")
    # Not figures: a file being written, a file of another kind, one that is not a number, one too large.
    (tmp_path / ".qd_backup_repo.Xy12ab").write_text("qd_wal_archive_newest_age_seconds 9999\n", encoding="utf-8")
    (tmp_path / "check.status").write_text("0 1790000000\n", encoding="utf-8")
    (tmp_path / "expire.last-success").write_text("never\n", encoding="utf-8")
    (tmp_path / "huge.prom").write_text("x 1\n" * 40_000, encoding="utf-8")
    (tmp_path / "scheduler").mkdir()
    assert read_figure_files(str(tmp_path)) == {
        "qd_wal_archive_newest_age_seconds": 42.0,
        'qd_backup_last_success_timestamp_seconds{type="full"}': 1790000000.0,
        'qd_backup_last_run_success{type="diff"}': 0.0,
        "sync.last-success": 1790000123.0,
    }


def test_a_directory_that_is_missing_or_empty_gives_no_figures(tmp_path: Path) -> None:
    assert read_figure_files(str(tmp_path)) == {}
    assert read_figure_files(str(tmp_path / "not-there")) == {}


def test_the_share_of_a_disk_in_use(tmp_path: Path) -> None:
    shares = disk_shares([str(tmp_path), str(tmp_path / "not-there")])
    assert list(shares) == [str(tmp_path)]
    assert 0.0 < shares[str(tmp_path)] < 1.0


# --- the API as a neighbour --------------------------------------------------------------------------------


class FakeTransport:
    def __init__(self, answers: dict[str, tuple[int, bytes] | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def __call__(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        self.calls.append((method, path, headers))
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.parametrize(
    ("answer", "healthy"),
    [((200, b'{"status":"ok"}'), True), ((503, b'{"status":"down"}'), False), (ConnectionRefusedError(), False)],
)
def test_the_api_is_healthy_only_when_healthz_says_so(answer: Any, healthy: bool) -> None:
    transport = FakeTransport({"/healthz": answer})
    assert asyncio.run(ApiProbe("http://api:8000", transport=transport).healthy()) is healthy
    assert transport.calls == [("GET", "/healthz", {})]


def test_the_counters_are_read_with_the_metrics_token() -> None:
    body = b'# TYPE qd_requests_total counter\nqd_requests_total{method="GET",route="/healthz",status="200"} 7\n'
    transport = FakeTransport({"/metrics": (200, body)})
    counters = asyncio.run(ApiProbe("http://api:8000", "a-metrics-token-of-the-proof", transport).counters())
    assert counters == {'qd_requests_total{method="GET",route="/healthz",status="200"}': 7.0}
    assert transport.calls == [("GET", "/metrics", {"Authorization": "Bearer a-metrics-token-of-the-proof"})]


@pytest.mark.parametrize("answer", [(404, b"{}"), (500, b""), TimeoutError()])
def test_counters_that_cannot_be_read_are_none_and_not_an_error(answer: Any) -> None:
    assert asyncio.run(ApiProbe("http://api:8000", "t" * 16, FakeTransport({"/metrics": answer})).counters()) is None


@pytest.mark.parametrize("url", ["api:8000", "ftp://api", "http://api:8000/healthz", "http://"])
def test_an_address_of_the_api_that_is_not_a_plain_origin_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="QD_ALERT_API_URL"):
        ApiProbe(url)


# --- the settings ------------------------------------------------------------------------------------------


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, bot_token="1000000000:test", **values)  # type: ignore[call-arg]


def test_the_alert_chats_are_whole_numbers_other_than_zero() -> None:
    assert settings().alert_chats() == ()
    assert settings(alert_chat_ids=" 700100 , -1001234567890,700100, ").alert_chats() == (700100, -1001234567890)


@pytest.mark.parametrize("value", ["@operators", "700100;700200", "0", "-0", "7 00100", "１２３", "--5", "1.5"])
def test_a_chat_that_is_not_a_number_refuses_to_start(value: str) -> None:
    """A typing mistake must not silently mean "tell nobody"."""
    with pytest.raises(ValueError, match="QD_ALERT_CHAT_IDS"):
        settings(alert_chat_ids=value).alert_chats()


def test_the_alert_settings_come_from_the_environment_and_are_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    names = (
        "ALERT_CHAT_IDS",
        "ALERT_API_URL",
        "ALERT_BACKUP_FIGURES_DIR",
        "ALERT_FILES_FIGURES_DIR",
        "ALERT_DISK_PATHS",
    )
    for name in names:
        monkeypatch.delenv(f"QD_{name}", raising=False)
    empty = Settings(_env_file=None)  # type: ignore[call-arg]
    assert [getattr(empty, name.lower()) for name in names] == [""] * 5
    monkeypatch.setenv("QD_ALERT_CHAT_IDS", "-1009")
    monkeypatch.setenv("QD_ALERT_DISK_PATHS", "/var/lib/qarz/files, /data")
    filled = Settings(_env_file=None)  # type: ignore[call-arg]
    assert filled.alert_chats() == (-1009,) and filled.alert_disks() == ("/var/lib/qarz/files", "/data")
    # The chats are not shown when the settings are printed: an identifier of a person may be among them.
    assert "-1009" not in repr(filled)


def test_the_worker_watches_what_is_configured_and_nothing_else(tmp_path: Path) -> None:
    health = WorkerHealth(started_at=datetime.now(UTC))
    bare = build_watch(settings(), None, FakeBot(), health)  # type: ignore[arg-type]
    assert bare._sources.configured() == frozenset({rules.DATABASE, rules.WORKER})
    assert bare._chats == ()
    full = build_watch(
        settings(
            alert_chat_ids="-1009",
            alert_api_url="http://api:8000",
            metrics_token="a-metrics-token-of-the-proof",
            alert_backup_figures_dir=str(tmp_path),
            alert_files_figures_dir=str(tmp_path),
            alert_disk_paths=str(tmp_path),
        ),
        None,  # type: ignore[arg-type]
        FakeBot(),  # type: ignore[arg-type]
        health,
    )
    assert full._chats == (-1009,)
    assert full._sources.configured() == frozenset(
        {
            rules.DATABASE,
            rules.WORKER,
            rules.BACKUP,
            rules.FILES,
            rules.DISK,
            rules.API,
            rules.METRICS,
        }
    )
    assert full._sources.disk is not None and list(full._sources.disk()) == [str(tmp_path)]
    # The API without a metrics token: its health is watched, its counters are not.
    half = build_watch(settings(alert_api_url="http://api:8000"), None, FakeBot(), health)  # type: ignore[arg-type]
    assert rules.API in half._sources.configured() and rules.METRICS not in half._sources.configured()


def test_a_wrong_chat_setting_stops_the_worker_before_it_watches_anything() -> None:
    health = WorkerHealth(started_at=datetime.now(UTC))
    with pytest.raises(ValueError, match="QD_ALERT_CHAT_IDS"):
        build_watch(settings(alert_chat_ids="operators"), None, FakeBot(), health)  # type: ignore[arg-type]


# --- the command that sends the test alert -----------------------------------------------------------------


class FakeChannel:
    def __init__(self, refuse: dict[int, str] | None = None) -> None:
        self.refuse = refuse or {}
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        if chat_id in self.refuse:
            raise AlertNotDelivered(self.refuse[chat_id])
        self.sent.append((chat_id, text))

    async def state(self) -> str:
        return "ok"


def test_the_command_reports_that_telegram_took_the_test_alert(capsys: pytest.CaptureFixture[str]) -> None:
    channel = FakeChannel()
    code = asyncio.run(alert_test.run(settings(alert_chat_ids="700100,-1001234567890"), channel))
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert out == [
        "ACCEPTED: chat 1 (...0100): Telegram took the test alert. Look for it there.",
        "ACCEPTED: chat 2 (...7890): Telegram took the test alert. Look for it there.",
    ]
    assert [chat for chat, _ in channel.sent] == [700100, -1001234567890]
    assert all("SINOV" in text and "ПРОБНОЕ" in text for _, text in channel.sent)
    # The identifiers themselves are not printed.
    assert "700100" not in "".join(out) and "1001234567890" not in "".join(out)


def test_the_command_fails_when_one_chat_did_not_take_it(capsys: pytest.CaptureFixture[str]) -> None:
    channel = FakeChannel(refuse={-1001234567890: "rejected"})
    code = asyncio.run(alert_test.run(settings(alert_chat_ids="700100,-1001234567890"), channel))
    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert out[0].startswith("ACCEPTED: chat 1") and out[1].startswith(
        "NOT DELIVERED: chat 2 (...7890): Telegram refused"
    )


@pytest.mark.parametrize("outcome", ["unreachable", "refused", "rejected"])
def test_every_way_of_not_being_delivered_is_explained(outcome: str) -> None:
    lines, code = alert_test.report({700100: outcome})
    assert code == 1 and lines[0].startswith("NOT DELIVERED: chat 1 (...0100): ") and len(lines[0]) > 60


def test_the_command_says_so_when_nothing_is_configured(capsys: pytest.CaptureFixture[str]) -> None:
    assert asyncio.run(alert_test.run(settings(), FakeChannel())) == 2
    assert capsys.readouterr().out.startswith("NOT CONFIGURED: QD_ALERT_CHAT_IDS")
    # Without a bot token there is nothing to send with, and no bot is made.
    without_token = Settings(_env_file=None, bot_token="", alert_chat_ids="700100")  # type: ignore[call-arg]
    assert asyncio.run(alert_test.run(without_token)) == 2
    assert capsys.readouterr().out.startswith("NOT CONFIGURED: QD_BOT_TOKEN")


def test_the_modules_of_the_watch_read_no_setting_but_through_the_settings() -> None:
    """Nothing of the watch reads the environment by itself: every name it uses is one the deployment
    files are checked against (tests/test_deploy_files.py)."""
    source = Path(alert_test.__file__).resolve().parents[1]
    for module in ("application/ops_watch.py", "infrastructure/ops_probes.py", "infrastructure/telegram_alerts.py"):
        text = (source / module).read_text(encoding="utf-8")
        assert "environ" not in text and "getenv" not in text, module
