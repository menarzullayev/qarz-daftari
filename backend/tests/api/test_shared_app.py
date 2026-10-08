"""The application that all tests of a session share (`shared_app`, conftest.py), and why that is safe.

Each test puts its own database connections, clock, allow-list, cipher, file store and Telegram fakes
behind the application and takes them away again. Two things could still carry something from one test to
the next, and both are held here:

- the stand-ins themselves, if one handed a test another test's object, or went on working with nothing
  behind it;
- the application object, if it kept something between requests. Everything it holds is walked: each
  object of this project's own classes, whatever the route handlers closed over, the middleware and the
  error handlers. An attribute that could change and is neither a stand-in nor the one known store (the
  request counters, which are set back before each test) fails the walk. A cache added to a service
  tomorrow is found by it.
"""

import asyncio
import datetime as dt
import enum
import pathlib
import re
import types
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.infrastructure.secret_box import SecretBox
from qarz.interface.observability import Metrics

from .conftest import (
    TEST_SECRETS_KEY,
    AdminEnv,
    AllowListStandIn,
    FakeChatMembers,
    FakeTelegramFiles,
    MovableClock,
    NothingBehind,
    SharedApp,
    Stage,
    StandIn,
    World,
    as_new,
    as_user,
    build_app,
    metrics_of,
)

pytestmark = pytest.mark.db

# --- everything the application holds -----------------------------------------------------------------------

_FIXED = (str, int, float, bool, bytes, type(None), re.Pattern, pathlib.PurePath, dt.timedelta, dt.datetime, dt.date)
_FIXED += (enum.Enum, uuid.UUID, type, types.ModuleType, types.BuiltinFunctionType, types.MethodWrapperType)
# The stores the application object is known to keep between requests, each set back by `as_new`.
SET_BACK_BEFORE_EACH_TEST = {Metrics: {"_requests", "_buckets", "_sums", "_security"}}


def _ours(value: object) -> bool:
    return (type(value).__module__ or "").startswith(("qarz.", "tests."))


def kept_state(roots: list[tuple[str, object]]) -> tuple[list[str], set[str]]:
    """Walk what the roots hold. Returns what could change between requests and is not accounted for,
    and the names of this project's classes that were met (so that a walk that saw nothing is noticed)."""
    unaccounted: list[str] = []
    met: set[str] = set()
    seen: set[int] = set()

    def walk(value: object, path: str) -> None:
        if isinstance(value, _FIXED) or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, StandIn | AllowListStandIn | Stage):
            return  # the running test's own object is behind it, and nothing after the test
        if isinstance(value, tuple | frozenset):
            for item in value:
                walk(item, f"{path}[]")
        elif isinstance(value, types.MethodType):
            walk(value.__self__, f"{path}.__self__")
            walk(value.__func__, path)
        elif isinstance(value, types.FunctionType):
            for name, cell in zip(value.__code__.co_freevars, value.__closure__ or (), strict=True):
                try:
                    walk(cell.cell_contents, f"{path}<{name}>")
                except ValueError:  # a cell that was never filled
                    continue
            for default in (*(value.__defaults__ or ()), *(value.__kwdefaults__ or {}).values()):
                walk(default, f"{path}(default)")
        elif _ours(value):
            met.add(type(value).__name__)
            set_back = SET_BACK_BEFORE_EACH_TEST.get(type(value), set())
            attributes = dict(vars(value)) if hasattr(value, "__dict__") else {}
            attributes |= {
                name: getattr(value, name) for name in getattr(type(value), "__slots__", ()) if hasattr(value, name)
            }
            for name, attribute in attributes.items():
                if name not in set_back:
                    walk(attribute, f"{path}.{name}")
        else:
            # A dict, a list, a set, or an object of somebody else's class: it can change, or we cannot tell.
            unaccounted.append(f"{path}: {type(value).__module__}.{type(value).__qualname__}")

    for path, root in roots:
        walk(root, path)
    return unaccounted, met


def roots_of(app: FastAPI) -> list[tuple[str, object]]:
    """Everything of ours the application calls: handlers, their dependencies, middleware, error handlers."""
    roots: list[tuple[str, object]] = []
    for route in app.routes:
        name = getattr(route, "name", "?")
        roots.append((f"route {name}", getattr(route, "endpoint", None)))
        pending = [dependant] if (dependant := getattr(route, "dependant", None)) is not None else []
        while pending:
            dependant = pending.pop()
            roots.append((f"dependency of {name}", dependant.call))
            pending.extend(dependant.dependencies)
    for middleware in app.user_middleware:
        roots.append((f"middleware {middleware.cls.__name__}", tuple(middleware.args)))
        roots.extend(
            (f"middleware {middleware.cls.__name__}({key})", value) for key, value in middleware.kwargs.items()
        )
    roots.extend((f"error handler {key}", handler) for key, handler in app.exception_handlers.items())
    return roots


def test_the_shared_application_keeps_nothing_between_requests_but_its_counters(shared_app: SharedApp) -> None:
    unaccounted, met = kept_state(roots_of(shared_app.app))
    assert unaccounted == []
    # The walk did reach the services, the middleware's counters and the test authenticator.
    assert {"ShopService", "LedgerService", "AdminAccess", "ChatService", "UpdateProcessor", "FileService"} <= met
    assert {"Metrics", "HeaderAuthenticator", "Allowance", "OnlinePaymentService"} <= met
    assert len(met) >= 30
    assert len(shared_app.app.routes) > 100


def test_everything_a_test_has_of_its_own_is_behind_a_stand_in(shared_app: SharedApp) -> None:
    # None of these may be reachable from the application itself: each is some test's.
    _, met = kept_state(roots_of(shared_app.app))
    assert not met & {"Database", "FilesystemFileStore", "FakeTelegramFiles", "FakeChatMembers", "SecretBox"}
    assert not met & {"MovableClock", "AdminEnv"}


@dataclass
class _Service:
    storage: object
    cache: Any = None


def _app_with(service: object, **middleware: object) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # as the application itself is made

    @app.get("/thing")
    async def thing() -> str:
        return str(service)

    if middleware:
        app.add_middleware(_Passes, **middleware)
    return app


class _Passes:
    def __init__(self, app: Any, **kept: object) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._app(scope, receive, send)


@pytest.mark.parametrize(
    ("holds", "found"),
    [
        (_Service(StandIn("database connection", Database), cache={}), "route thing<service>.cache: builtins.dict"),
        (_Service(StandIn("database connection", Database), cache=[]), "route thing<service>.cache: builtins.list"),
        (_Service(StandIn("database connection", Database), cache=set()), "route thing<service>.cache: builtins.set"),
        (
            _Service(_Service(StandIn("database connection", Database), cache={})),
            "route thing<service>.storage.cache: builtins.dict",
        ),
        (_Service(SecretBox(TEST_SECRETS_KEY)), "route thing<service>.storage._current.aead: "),
        (_Service(FakeTelegramFiles()), "route thing<service>.storage.files: builtins.dict"),
        ({"a dict the handler closed over": 1}, "route thing<service>: builtins.dict"),
    ],
)
def test_the_walk_finds_what_could_carry_over(holds: object, found: str) -> None:
    unaccounted, _ = kept_state(roots_of(_app_with(holds)))
    assert any(line.startswith(found) for line in unaccounted), unaccounted


def test_the_walk_finds_a_store_in_the_middleware_and_one_in_a_default() -> None:
    unaccounted, _ = kept_state(roots_of(_app_with(None, counters={"requests": 0})))
    assert unaccounted == ["middleware _Passes(counters): builtins.dict"]

    def handler(seen: list[str] = []) -> None:  # noqa: B006 - the mistake the walk is to find
        seen.append("x")

    unaccounted, _ = kept_state([("handler", handler)])
    assert unaccounted == ["handler(default): builtins.list"]


def test_the_walk_passes_an_application_that_holds_only_stand_ins_and_fixed_values() -> None:
    unaccounted, met = kept_state(roots_of(_app_with(_Service(StandIn("database connection", Database), cache=None))))
    assert (unaccounted, met) == ([], {"_Service"})


def test_a_counter_that_is_not_set_back_is_found(monkeypatch: pytest.MonkeyPatch, shared_app: SharedApp) -> None:
    monkeypatch.setitem(SET_BACK_BEFORE_EACH_TEST, Metrics, {"_requests", "_buckets", "_sums"})
    unaccounted, _ = kept_state(roots_of(shared_app.app))
    assert unaccounted == ["middleware Observe(metrics)._security: builtins.dict"]


# --- the counters are set back ------------------------------------------------------------------------------


def test_the_counters_start_from_nothing_in_every_test(client: TestClient, world: World, shared_app: SharedApp) -> None:
    metrics = metrics_of(client.app)  # type: ignore[arg-type]
    assert vars(metrics) == vars(Metrics()), "as a newly built application has them"
    assert client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a)).status_code == 200
    assert client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.stranger)).status_code == 404
    assert vars(metrics) != vars(Metrics()), "the two requests were counted"
    counted = dict(vars(metrics))
    as_new(client.app)  # type: ignore[arg-type]
    assert vars(metrics) == vars(Metrics())
    assert set(counted) == SET_BACK_BEFORE_EACH_TEST[Metrics], "every field of the counters is one that is set back"


def test_setting_back_refuses_an_application_without_exactly_one_set_of_counters() -> None:
    with pytest.raises(RuntimeError, match="one Metrics, found 0"):
        as_new(FastAPI())
    twice = _app_with(None, metrics=Metrics())
    twice.add_middleware(_Passes, metrics=Metrics())
    with pytest.raises(RuntimeError, match="one Metrics, found 2"):
        as_new(twice)


# --- the stand-ins ------------------------------------------------------------------------------------------


def _things(tmp_path: pathlib.Path, url: str = "postgresql://qd_app:x@127.0.0.1:1/none") -> dict[str, Any]:
    return {
        "database": Database(url),
        "admin_database": Database(url),
        "file_store": FilesystemFileStore(tmp_path),
        "telegram_files": FakeTelegramFiles(),
        "telegram_members": FakeChatMembers(),
        "admin_env": AdminEnv(set(), MovableClock(), SecretBox(TEST_SECRETS_KEY)),
    }


def test_a_stand_in_reaches_what_is_behind_it_when_it_is_called_not_when_it_is_asked(tmp_path: pathlib.Path) -> None:
    stage = Stage()
    fetch = stage.telegram_files.fetch  # taken while nothing is behind, as the application does when built
    contains = stage.allowed.__contains__
    now = stage.now
    first, second = _things(tmp_path), _things(tmp_path)
    first["telegram_files"].files["a"] = b"first"
    second["telegram_files"].files["a"] = b"second"
    first["admin_env"].allowed.add(1)
    second["admin_env"].clock.offset = dt.timedelta(days=400)

    async def fetched() -> bytes | None:
        return await fetch("a", 100)  # type: ignore[no-any-return]

    stage.put(**first)
    assert asyncio.run(fetched()) == b"first"
    assert contains(1) and 1 in stage.allowed
    assert abs(now() - dt.datetime.now(dt.UTC)) < dt.timedelta(minutes=1)
    assert stage.cipher.decrypt(stage.cipher.encrypt(b"s", b"c"), b"c") == b"s"
    stage.clear()

    stage.put(**second)
    assert asyncio.run(fetched()) == b"second"
    assert not contains(1) and 1 not in stage.allowed
    assert now() - dt.datetime.now(dt.UTC) > dt.timedelta(days=399)
    assert first["telegram_files"].asked == ["a"] and second["telegram_files"].asked == ["a"]
    stage.clear()


def test_with_nothing_behind_a_stand_in_every_call_fails(tmp_path: pathlib.Path) -> None:
    stage = Stage()
    calls = [
        lambda: stage.database.tenant(uuid.uuid4()),
        lambda: stage.database.reachable(),
        lambda: stage.admin_database.platform(),
        lambda: stage.file_store.get("k"),
        lambda: stage.telegram_files.fetch("a", 1),
        lambda: stage.telegram_members.status(1, 2),
        lambda: stage.cipher.encrypt(b"s", b"c"),
        lambda: 1 in stage.allowed,
        stage.now,
    ]
    for call in calls:
        with pytest.raises(NothingBehind):
            call()
    stage.put(**_things(tmp_path))
    stage.clear()
    for call in calls:
        with pytest.raises(NothingBehind):
            call()
    assert stage.empty()


def test_a_stand_in_hands_out_methods_of_its_kind_and_nothing_else(tmp_path: pathlib.Path) -> None:
    stage = Stage()
    stage.put(**_things(tmp_path))
    for stand_in, name in (
        (stage.telegram_files, "files"),  # a plain attribute of the fake: some test's dictionary
        (stage.telegram_members, "unreachable"),
        (stage.database, "_engine"),
        (stage.database, "no_such_method"),
        (stage.file_store, "_root"),
        (stage.cipher, "_current"),
    ):
        with pytest.raises(AttributeError, match="methods only"):
            getattr(stand_in, name)
    assert not hasattr(stage.database, "__aenter__")
    assert callable(stage.database.tenant) and callable(stage.database.user_language)
    stage.clear()


def test_a_second_test_cannot_put_its_things_behind_while_the_first_ones_are_there(tmp_path: pathlib.Path) -> None:
    stage = Stage()
    stage.put(**_things(tmp_path))
    with pytest.raises(RuntimeError, match="still behind"):
        stage.put(**_things(tmp_path))
    stage.clear()
    stage.put(**_things(tmp_path))
    stage.clear()


def test_only_the_right_kind_of_thing_goes_behind_a_stand_in(tmp_path: pathlib.Path) -> None:
    stage = Stage()
    wrong = _things(tmp_path) | {"telegram_files": FakeChatMembers()}
    with pytest.raises(TypeError, match="Telegram files fake"):
        stage.put(**wrong)
    stage.clear()
    assert stage.empty()


def test_the_application_is_built_with_nothing_behind_and_calls_nothing_while_it_is_built(
    tmp_path: pathlib.Path,
) -> None:
    stage = Stage()
    app = build_app(stage)  # anything called through a stand-in here would have raised NothingBehind
    assert stage.empty() and len(app.routes) > 100
    stage.put(**_things(tmp_path))
    with pytest.raises(RuntimeError, match="nothing behind"):
        build_app(stage)
    stage.clear()


# --- through the fixture ------------------------------------------------------------------------------------


def test_between_tests_nothing_is_behind_the_shared_application(shared_app: SharedApp) -> None:
    # This test does not ask for `client`: whatever the test before put behind has been taken away.
    assert shared_app.stage.empty()
    with pytest.raises(NothingBehind):
        shared_app.stage.now()


def test_a_test_gets_the_shared_application_with_its_own_things_behind(
    client: TestClient,
    shared_app: SharedApp,
    admin_env: AdminEnv,
    telegram_files: FakeTelegramFiles,
    request: pytest.FixtureRequest,
) -> None:
    stage = shared_app.stage
    if request.config.getoption("--app-per-test"):
        assert client.app is not shared_app.app and stage.empty()
        return
    assert client.app is shared_app.app
    assert stage.admin_env() is admin_env
    assert stage.telegram_files._target is telegram_files
    assert stage.cipher._target is admin_env.box
    assert isinstance(stage.database._target, Database) and stage.database._target is not stage.admin_database._target


_BEHIND_IN_EARLIER_ROUNDS: list[tuple[object, object, object]] = []


@pytest.mark.parametrize("round_", [1, 2, 3])
def test_no_two_tests_have_the_same_connections_clock_or_fakes_behind_the_application(
    client: TestClient, shared_app: SharedApp, request: pytest.FixtureRequest, round_: int
) -> None:
    if request.config.getoption("--app-per-test"):
        return
    # The rounds may run in any order and far apart; each compares itself with those that ran before it.
    stage = shared_app.stage
    mine = (stage.database._target, stage.admin_env().clock, stage.telegram_members._target)
    for theirs in _BEHIND_IN_EARLIER_ROUNDS:
        assert all(one is not other for one, other in zip(mine, theirs, strict=True))
    _BEHIND_IN_EARLIER_ROUNDS.append(mine)
    # What this round does to its clock and its fake must not be there for the next.
    assert stage.admin_env().clock.offset == dt.timedelta(0)
    assert stage.telegram_members._target.statuses == {}
    stage.admin_env().clock.offset += dt.timedelta(days=30)
    stage.telegram_members._target.statuses[(1, 2)] = "administrator"
    assert stage.now() - dt.datetime.now(dt.UTC) > dt.timedelta(days=29)
