"""Drive the real application against a generated database and measure it against the targets.

    python -m loadtest.drive --database qd_load_full --duration 1800 --out results.json

What runs: the production wiring, `uvicorn qarz.interface.asgi:build --factory`, as separate server
processes that connect as the restricted role `qd_app`, so row-level security is in force and every
request is authenticated through a real server-side session, as deployed. The driver talks to them over
HTTP on the loopback interface, taking them in turn as a proxy would. With `--base-url` nothing is
started and an already running server is driven instead (a staging server, for example).

Why a separate server and not the application in the driver's own process: in one process the driver and
the application share one event loop and one core, so each request would wait for the driver's own work
and no HTTP would be parsed. A separate server costs the loopback round trip, which a real client pays too.

The load is open: requests start at their scheduled instants (Poisson arrivals) whether or not earlier
ones have answered, so a slow server meets a growing queue instead of a patient client. Response time is
taken from the moment the request is sent to the last byte of the answer. How late the driver itself
started requests is reported, to show whether the driver kept up.

Streams:

- writes across all shops at `--rate` a second (NFR-006): chat messages through the Telegram webhook,
  amount-only credit sales and payments through the API, and credit sales with ten goods lines;
- the same writes in the large shop (the NFR-005 size) at `--large-write-rate`;
- reads in the large shop at `--large-read-rate`, and across the other shops at `--read-rate`:
  the customer list, search by name and by phone, the overview, the debtors lists, one customer's page;
- the one-year period report and the overdue report of the large shop, one every `--report-every` seconds.
"""

import argparse
import asyncio
import contextlib
import json
import os
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg

from loadtest import report
from loadtest.dataset import FIRST_NAMES, GOODS, SURNAMES, session_token
from loadtest.load import ADMIN_URL_VARIABLE, BACKEND, app_url, database_url, read_manifest
from qarz.domain.promise import tashkent_date

WEBHOOK_SECRET = "loadtest-webhook-secret-0123456789"  # noqa: S105  (for the throwaway server only)
BOT_TOKEN = "1234567890:LOADTEST-ONLY-token-not-a-real-bot"  # noqa: S105
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105  (a header name)
REQUEST_TIMEOUT = 30.0
WRITE_MIX = (
    ("chat_credit", 0.30),
    ("chat_payment", 0.10),
    ("api_credit", 0.25),
    ("api_payment", 0.20),
    ("api_itemized_10", 0.15),
)
READ_MIX = (
    ("customers_list", 0.15),
    ("customers_search", 0.30),
    ("customers_search_phone", 0.05),
    ("overview", 0.15),
    ("debtors", 0.10),
    ("debtors_overdue", 0.05),
    ("customer_page", 0.20),
)


@dataclass(frozen=True)
class Staff:
    user_id: UUID
    tg_id: int
    role: str
    token: str


@dataclass(frozen=True)
class Customer:
    customer_id: UUID
    name: str
    name_is_unique: bool  # in its shop: a chat message naming this customer needs no question back
    phone: str | None


@dataclass
class Shop:
    shop_id: UUID
    staff: list[Staff] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)
    catalog: list[UUID] = field(default_factory=list)


@dataclass
class Owed:
    """A credit sale this run recorded and may pay back, so that payments never exceed a balance."""

    shop: Shop
    customer: Customer
    amount: int


@dataclass
class World:
    large: list[Shop]
    other: list[Shop]


def load_world(conn: psycopg.Connection[Any], seed: int, large_shops: int) -> World:
    shops: dict[UUID, Shop] = {}
    for (shop_id,) in conn.execute("SELECT id FROM shop WHERE status = 'active'"):
        shops[shop_id] = Shop(shop_id)
    for shop_id, user_id, role, tg_id in conn.execute(
        "SELECT m.shop_id, m.user_id, m.role, u.tg_id FROM membership m JOIN app_user u ON u.id = m.user_id "
        "WHERE m.status = 'active' ORDER BY m.id"
    ):
        shops[shop_id].staff.append(Staff(user_id, tg_id, role, session_token(seed, user_id)))
    for shop_id, customer_id, name, phone, same in conn.execute(
        "SELECT shop_id, id, display_name, phone, count(*) OVER (PARTITION BY shop_id, name_norm) "
        "FROM customer WHERE status = 'active' ORDER BY id"
    ):
        shops[shop_id].customers.append(Customer(customer_id, name, same == 1, phone))
    for shop_id, item_id in conn.execute("SELECT shop_id, id FROM catalog_item WHERE status = 'active' ORDER BY id"):
        shops[shop_id].catalog.append(item_id)
    biggest = [
        row[0]
        for row in conn.execute(
            "SELECT shop_id FROM ledger_entry GROUP BY shop_id ORDER BY count(*) DESC, shop_id LIMIT %s", (large_shops,)
        )
    ]
    usable = [shop for shop in shops.values() if shop.staff and shop.customers]
    return World(
        large=[shop for shop in usable if shop.shop_id in biggest],
        other=[shop for shop in usable if shop.shop_id not in biggest],
    )


def _choose(rng: random.Random, mix: tuple[tuple[str, float], ...]) -> str:
    return rng.choices([name for name, _ in mix], weights=[weight for _, weight in mix])[0]


class Driver:
    def __init__(self, clients: list[httpx.AsyncClient], world: World, rng: random.Random, *, warmup: float) -> None:
        if not clients:
            raise ValueError("at least one server to drive")
        self._clients = clients
        self._turn = 0
        self._world = world
        self._rng = rng
        self._warmup = warmup
        self._started = 0.0
        self._update_ids = int(time.time()) * 1_000_000
        self._owed: dict[str, list[Owed]] = {"large": [], "other": []}
        self._tasks: set[asyncio.Task[None]] = set()
        self.results: dict[tuple[str, str], report.Samples] = defaultdict(report.Samples)
        self.lateness_ms: list[float] = []
        self.sent = 0
        self.abandoned = 0

    # --- what a request needs -----------------------------------------------------------------------

    @property
    def _client(self) -> httpx.AsyncClient:
        """The next server process in turn, as a proxy in front of them would choose."""
        self._turn = (self._turn + 1) % len(self._clients)
        return self._clients[self._turn]

    def _shop(self, scope: str) -> Shop:
        return self._rng.choice(self._world.large if scope == "large" else self._world.other)

    def _bearer(self, shop: Shop, *, manager: bool = False) -> dict[str, str]:
        staff = [member for member in shop.staff if member.role != "seller"] if manager else shop.staff
        return {"Authorization": f"Bearer {self._rng.choice(staff).token}"}

    def _take_owed(self, scope: str, *, named: bool) -> Owed | None:
        pool = self._owed[scope]
        for _ in range(8):
            if not pool:
                return None
            position = self._rng.randrange(len(pool))
            if named and not pool[position].customer.name_is_unique:
                continue
            pool[position], pool[-1] = pool[-1], pool[position]
            return pool.pop()
        return None

    def _part(self, owed: Owed, scope: str) -> int:
        """Pay all of it or half; what is left stays owed for a later payment."""
        amount = owed.amount if owed.amount < 4000 or self._rng.random() < 0.5 else owed.amount // 2000 * 1000
        if owed.amount > amount:
            self._owed[scope].append(Owed(owed.shop, owed.customer, owed.amount - amount))
        return amount

    async def _webhook(self, staff: Staff, text: str) -> httpx.Response:
        self._update_ids += 1
        update = {
            "update_id": self._update_ids,
            "message": {
                "message_id": self._update_ids % 1_000_000_000,
                "chat": {"id": staff.tg_id, "type": "private"},
                "from": {"id": staff.tg_id, "language_code": "uz", "first_name": "Sotuvchi"},
                "text": text,
            },
        }
        return await self._client.post("/tg/webhook", json=update, headers={SECRET_HEADER: WEBHOOK_SECRET})

    async def _entry(self, shop: Shop, customer: Customer, body: dict[str, Any]) -> httpx.Response:
        headers = {**self._bearer(shop), "Idempotency-Key": uuid4().hex}
        return await self._client.post(
            f"/api/v1/shops/{shop.shop_id}/customers/{customer.customer_id}/entries", json=body, headers=headers
        )

    # --- operations: each returns the name it is reported under and a call that performs it -----------

    def _write(self, scope: str) -> tuple[str, Callable[[], Awaitable[httpx.Response]]]:
        rng = self._rng
        operation = _choose(rng, WRITE_MIX)
        if operation.endswith("_payment"):
            owed = self._take_owed(scope, named=operation == "chat_payment")
            if owed is None:
                # Nothing is owed yet that this run may pay: record a credit sale by the same route.
                operation = operation.replace("_payment", "_credit")
            else:
                amount = self._part(owed, scope)
                if operation == "chat_payment":
                    staff = rng.choice(owed.shop.staff)
                    return operation, lambda: self._webhook(staff, f"{owed.customer.name} -{amount}")
                return operation, lambda: self._entry(owed.shop, owed.customer, {"kind": "payment", "amount": amount})

        shop = self._shop(scope)
        customer = rng.choice(shop.customers)
        if operation == "chat_credit":
            # One message records a sale only when the name means one customer; otherwise the bot asks
            # which one, and that is a different, cheaper exchange than the one NFR-001 is about.
            sampled = rng.sample(shop.customers, k=min(8, len(shop.customers)))
            named = next((candidate for candidate in sampled if candidate.name_is_unique), None)
            if named is None:
                operation = "api_credit"
            else:
                staff = rng.choice(shop.staff)
                sum_typed = rng.randint(5, 200) * 1000
                return operation, lambda: self._webhook(staff, f"{named.name} {sum_typed}")
        if operation == "api_itemized_10":
            lines: list[dict[str, Any]] = []
            for position in range(10):
                price = rng.randint(10, 400) * 100
                if position < 6 and shop.catalog:
                    lines.append({"catalog_item_id": str(rng.choice(shop.catalog)), "qty": "2", "unit_price": price})
                else:
                    name = f"{rng.choice(GOODS)[0]} yuk"
                    lines.append({"name": name, "qty": "1.250", "unit": "kg", "unit_price": price})
            return operation, lambda: self._entry(shop, customer, {"kind": "credit", "lines": lines})

        amount = rng.randint(5, 200) * 1000

        async def credit() -> httpx.Response:
            response = await self._entry(shop, customer, {"kind": "credit", "amount": amount})
            if response.status_code == 201:
                self._owed[scope].append(Owed(shop, customer, amount))
            return response

        return "api_credit", credit

    def _read(self, scope: str) -> tuple[str, Callable[[], Awaitable[httpx.Response]]]:
        rng = self._rng
        operation = _choose(rng, READ_MIX)
        shop = self._shop(scope)
        headers = self._bearer(shop)
        base = f"/api/v1/shops/{shop.shop_id}"
        params: dict[str, str] = {}
        path = f"{base}/customers"
        if operation == "customers_search":
            # What a seller types: the first letters of a first name or a surname.
            word = rng.choice(FIRST_NAMES if rng.random() < 0.6 else SURNAMES)
            params = {"q": word[: rng.randint(3, 5)]}
        elif operation == "customers_search_phone":
            phones = [c.phone for c in rng.sample(shop.customers, k=min(20, len(shop.customers))) if c.phone]
            digits = (phones[0] if phones else "+998901234567")[1:]
            start = rng.randint(3, 6)
            params = {"q": digits[start : start + rng.randint(4, 6)]}
        elif operation == "overview":
            path = f"{base}/overview"
        elif operation == "debtors":
            path = f"{base}/overview/debtors"
        elif operation == "debtors_overdue":
            path, params = f"{base}/overview/debtors", {"overdue": "true"}
        elif operation == "customer_page":
            path = f"{base}/customers/{rng.choice(shop.customers).customer_id}"
        return operation, lambda: self._client.get(path, params=params, headers=headers)

    def _report(self, scope: str) -> tuple[str, Callable[[], Awaitable[httpx.Response]]]:
        shop = self._shop(scope)
        headers = self._bearer(shop, manager=True)
        base = f"/api/v1/shops/{shop.shop_id}/reports"
        if self._rng.random() < 0.5:
            return "report_overdue", lambda: self._client.get(f"{base}/overdue", headers=headers)
        today = tashkent_date(datetime.now(UTC))
        params = {"from": (today - timedelta(days=364)).isoformat(), "to": today.isoformat()}
        return "report_period_year", lambda: self._client.get(f"{base}/period", params=params, headers=headers)

    # --- running --------------------------------------------------------------------------------------

    async def _run(self, operation: str, scope: str, call: Callable[[], Awaitable[httpx.Response]], due: float) -> None:
        sent = time.perf_counter()
        outcome, detail = "error", "exception"
        try:
            # One limit for the whole exchange; httpx's own timeouts apply to each phase separately.
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await call()
            if response.status_code < 400:
                outcome = "ok"
            else:
                outcome = "error" if response.status_code >= 500 else "refused"
                detail = str(response.status_code)
                with contextlib.suppress(ValueError, KeyError, TypeError):
                    detail = f"{response.status_code} {response.json()['error']['code']}"
        except (httpx.TimeoutException, TimeoutError):
            detail = "timeout"
        except httpx.HTTPError as error:
            detail = type(error).__name__
        elapsed_ms = (time.perf_counter() - sent) * 1000
        if due - self._started < self._warmup:
            return
        samples = self.results[(operation, scope)]
        self.lateness_ms.append((sent - due) * 1000)
        if outcome == "ok":
            samples.ok_ms.append(elapsed_ms)
        elif outcome == "refused":
            samples.refused[detail] += 1
        else:
            samples.errors[detail] += 1

    async def _stream(
        self,
        rate: float,
        duration: float,
        scope: str,
        pick: Callable[[str], tuple[str, Callable[[], Awaitable[httpx.Response]]]],
    ) -> None:
        if rate <= 0:
            return
        due = self._started + self._rng.expovariate(rate)
        while due - self._started < duration:
            delay = due - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            operation, call = pick(scope)
            task = asyncio.create_task(self._run(operation, scope, call, due))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self.sent += 1
            due += self._rng.expovariate(rate)

    async def run(
        self,
        *,
        duration: float,
        rate: float,
        large_write_rate: float,
        read_rate: float,
        large_read_rate: float,
        report_every: float,
    ) -> None:
        # perf_counter, not the event loop clock: on Windows that one moves in steps of about 16 ms.
        self._started = time.perf_counter()
        large = bool(self._world.large)
        await asyncio.gather(
            self._stream(rate, duration, "other", self._write),
            self._stream(large_write_rate if large else 0, duration, "large", self._write),
            self._stream(read_rate, duration, "other", self._read),
            self._stream(large_read_rate if large else 0, duration, "large", self._read),
            self._stream(1 / report_every if large and report_every > 0 else 0, duration, "large", self._report),
        )
        if self._tasks:
            # Every request gives up after REQUEST_TIMEOUT; anything still running after that is stuck.
            _, stuck = await asyncio.wait(self._tasks, timeout=REQUEST_TIMEOUT + 10)
            for task in stuck:
                task.cancel()
            self.abandoned = len(stuck)


# --- the server under test ------------------------------------------------------------------------------


def start_servers(app_database_url: str, first_port: int, count: int) -> list[subprocess.Popen[bytes]]:
    """Start `count` server processes on consecutive ports, each the production wiring with one worker.

    Separate processes on separate ports, with the driver taking them in turn, stand in for the proxy and
    the "several worker processes" of docs/06-architecture. uvicorn's own `--workers` is not used: its
    workers share one listening socket, which fails on Windows.
    """
    environment = {
        **os.environ,
        "QD_DATABASE_URL": app_database_url,
        "QD_BOT_TOKEN": BOT_TOKEN,
        "QD_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "PYTHONPATH": str(BACKEND / "src"),
    }
    servers = []
    for port in range(first_port, first_port + count):
        command = [sys.executable, "-m", "uvicorn", "qarz.interface.asgi:build", "--factory", "--host", "127.0.0.1"]
        command += ["--port", str(port), "--no-access-log", "--log-level", "warning"]
        servers.append(subprocess.Popen(command, cwd=BACKEND, env=environment))  # noqa: S603  (fixed arguments)
    return servers


def stop_servers(servers: list[subprocess.Popen[bytes]]) -> None:
    for process in servers:
        if process.poll() is None:
            process.terminate()
    for process in servers:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=20)


async def wait_until_up(client: httpx.AsyncClient, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        with contextlib.suppress(httpx.HTTPError):
            if (await client.get("/healthz")).status_code == 200:
                return
        if time.monotonic() > deadline:
            raise RuntimeError("the server did not answer /healthz in time")
        await asyncio.sleep(0.5)


# --- the whole run --------------------------------------------------------------------------------------


def _counts(conn: psycopg.Connection[Any], since: datetime) -> dict[str, int]:
    rows = conn.execute("SELECT kind, count(*) FROM ledger_entry WHERE created_at >= %s GROUP BY kind", (since,))
    counts = {str(kind): int(count) for kind, count in rows}
    row = conn.execute("SELECT count(*) FROM outbox_message WHERE created_at >= %s", (since,)).fetchone()
    counts["outbox_messages"] = int(row[0]) if row else 0
    return counts


def _machine(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    settings = {}
    for name in ("server_version", "shared_buffers", "work_mem", "effective_cache_size", "max_connections", "fsync"):
        row = conn.execute("SELECT current_setting(%s)", (name,)).fetchone()
        settings[name] = row[0] if row else None
    return {
        "driver_platform": platform.platform(),
        "driver_processor": platform.processor(),
        "driver_cpus": os.cpu_count(),
        "python": platform.python_version(),
        "postgresql": settings,
    }


def verify(results: dict[tuple[str, str], report.Samples], recorded: dict[str, int]) -> dict[str, Any]:
    """Compare what the database gained with what the server said it saved.

    An API call that answered 201 saved exactly one entry. A chat message answers 200 whatever it did
    (the reply goes to the outbox), so the chat entries are what remains, and their share of the chat
    messages answered shows whether the chat figures measure recording or something quicker.
    The database is counted over the whole run, so the comparison is exact only without a warm-up.
    """

    def answered(prefix: str, suffix: str) -> int:
        return sum(
            len(s.ok_ms) for (name, _), s in results.items() if name.startswith(prefix) and name.endswith(suffix)
        )

    api = answered("api_", "")
    chat = answered("chat_", "")
    entries = recorded.get("credit", 0) + recorded.get("payment", 0)
    return {
        "entries_in_database": entries,
        "api_calls_answered_201": api,
        "chat_messages_answered_200": chat,
        "chat_entries_in_database": entries - api,
        "outbox_messages": recorded.get("outbox_messages", 0),
    }


async def measure(arguments: argparse.Namespace, admin_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url(admin_url, arguments.database), autocommit=True) as conn:
        manifest = read_manifest(conn)
        world = load_world(conn, int(manifest["seed"]), int(manifest["profile"]["large_shops"]))
        machine = _machine(conn)
    if not world.other and not world.large:
        raise RuntimeError("the database has no shop that can be driven")

    servers: list[subprocess.Popen[bytes]] = []
    if arguments.base_url:
        base_urls = [arguments.base_url]
    else:
        servers = start_servers(app_url(admin_url, arguments.database), arguments.port, arguments.workers)
        base_urls = [f"http://127.0.0.1:{arguments.port + index}" for index in range(arguments.workers)]
    each = max(1, arguments.connections // len(base_urls))
    limits = httpx.Limits(max_connections=each, max_keepalive_connections=each)
    try:
        async with contextlib.AsyncExitStack() as stack:
            clients = [
                await stack.enter_async_context(httpx.AsyncClient(base_url=url, timeout=REQUEST_TIMEOUT, limits=limits))
                for url in base_urls
            ]
            for client in clients:
                await wait_until_up(client, 90)
            driver = Driver(clients, world, random.Random(arguments.seed), warmup=arguments.warmup)  # noqa: S311
            started_at = datetime.now(UTC)
            clock = time.perf_counter()
            await driver.run(
                duration=arguments.duration,
                rate=arguments.rate,
                large_write_rate=arguments.large_write_rate,
                read_rate=arguments.read_rate,
                large_read_rate=arguments.large_read_rate,
                report_every=arguments.report_every,
            )
            elapsed = time.perf_counter() - clock
            died = [process.args for process in servers if process.poll() is not None]
            if died:
                raise RuntimeError(f"{len(died)} server process(es) ended during the run; the figures mean nothing")
    finally:
        stop_servers(servers)

    with psycopg.connect(database_url(admin_url, arguments.database), autocommit=True) as conn:
        recorded = _counts(conn, started_at)
    measured_seconds = max(1e-9, arguments.duration - arguments.warmup)
    checks = verify(driver.results, recorded)
    lateness = sorted(driver.lateness_ms)
    return {
        "started_at": started_at.isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "arguments": {key: value for key, value in vars(arguments).items() if key != "out"},
        "server": f"{len(servers)} processes started by the driver"
        if servers
        else f"already running at {base_urls[0]}",
        "machine": machine,
        "dataset": manifest,
        "shops_driven": {"large": len(world.large), "other": len(world.other)},
        "requests_sent": driver.sent,
        "requests_abandoned": driver.abandoned,
        "entries_recorded_in_run": recorded,
        "entries_per_second_whole_run": round(
            (recorded.get("credit", 0) + recorded.get("payment", 0)) / max(1e-9, arguments.duration), 2
        ),
        "measured_seconds": measured_seconds,
        "checks": checks,
        "driver_lateness_ms": {
            "p50": round(report.percentile(lateness, 0.5), 2) if lateness else None,
            "p99": round(report.percentile(lateness, 0.99), 2) if lateness else None,
            "max": round(lateness[-1], 2) if lateness else None,
        },
        "results": {
            f"{operation}|{scope}": samples.as_json() for (operation, scope), samples in driver.results.items()
        },
    }


def table(result: dict[str, Any]) -> str:
    samples = {
        (key.split("|")[0], key.split("|")[1]): report.Samples.from_json(value)
        for key, value in result["results"].items()
    }
    return report.markdown(report.lines(samples))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database", required=True, help="a database filled by loadtest.load")
    parser.add_argument("--duration", type=float, default=1800, help="seconds; NFR-006 asks for 30 minutes")
    parser.add_argument("--warmup", type=float, default=0, help="seconds at the start left out of the figures")
    parser.add_argument("--rate", type=float, default=50, help="recorded entries a second across all shops")
    parser.add_argument("--large-write-rate", type=float, default=1, help="more of them, in the large shop")
    parser.add_argument("--read-rate", type=float, default=48, help="reads a second across the other shops")
    parser.add_argument("--large-read-rate", type=float, default=2, help="reads a second in the large shop")
    parser.add_argument("--report-every", type=float, default=30, help="seconds between reports in the large shop")
    parser.add_argument("--workers", type=int, default=4, help="server processes to start, on consecutive ports")
    parser.add_argument("--port", type=int, default=8765, help="port of the first server process")
    parser.add_argument("--connections", type=int, default=200, help="most HTTP connections open at once")
    parser.add_argument("--base-url", help="drive a server that is already running instead of starting one")
    parser.add_argument("--seed", type=int, default=1, help="seed of the request sequence")
    parser.add_argument("--out", type=Path, help="write the full result, every sample included, as JSON")
    arguments = parser.parse_args(argv)
    admin_url = os.environ.get(ADMIN_URL_VARIABLE)
    if not admin_url:
        parser.error(f"set {ADMIN_URL_VARIABLE} to a superuser connection string of the load test server")

    result = asyncio.run(measure(arguments, admin_url))
    if arguments.out:
        arguments.out.write_text(json.dumps(result), encoding="utf-8")
    summary = {key: value for key, value in result.items() if key not in ("results", "dataset")}
    sys.stdout.write(json.dumps(summary, indent=2, default=str) + "\n\n" + table(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
