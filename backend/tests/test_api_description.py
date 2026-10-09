"""The API description file: it is the description of this code, and the check notices when it is not."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from qarz.interface.answers import Answer, Customer
from qarz.interface.api_description import DEFAULT_PATH, describe, described_settings, is_current, main, render
from qarz.interface.asgi import build


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return describe()


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [(method, path, op) for path, item in document["paths"].items() for method, op in item.items()]


def _answer_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    """The schema of the operation's successful JSON answer, or None when it has none."""
    for status, response in operation["responses"].items():
        if status.startswith("2"):
            schema = response.get("content", {}).get("application/json", {}).get("schema")
            return schema if isinstance(schema, dict) else None
    return None


def _is_typed(operation: dict[str, Any]) -> bool:
    schema = _answer_schema(operation)
    return schema is not None and "$ref" in schema


def test_the_committed_file_is_the_description_of_this_code(document: dict[str, Any]) -> None:
    assert is_current(DEFAULT_PATH, render(document)), (
        "backend/openapi.json is out of date: run `python -m qarz.interface.api_description` in backend/ "
        "and `npm run api:types` in frontend/"
    )
    assert main(["--check"]) == 0


def test_the_text_is_the_same_on_every_platform(document: dict[str, Any]) -> None:
    text = render(document)
    assert text.endswith("}\n")
    assert "\r" not in text
    assert json.loads(text) == document
    # Sorted keys: the order routes are added in, or a dictionary is built in, changes nothing.
    shuffled = {key: document[key] for key in reversed(list(document))}
    assert render(shuffled) == text
    assert b"\r" not in DEFAULT_PATH.read_bytes()


def test_a_changed_response_field_makes_the_check_fail(document: dict[str, Any], tmp_path: Path) -> None:
    """The negative case: the back end renames one field of one answer and the file is no longer current."""
    path = tmp_path / "openapi.json"
    assert main([str(path)]) == 0
    assert main(["--check", str(path)]) == 0

    changed = copy.deepcopy(document)
    properties = changed["components"]["schemas"]["Customer"]["properties"]
    properties["full_name"] = properties.pop("display_name")
    assert not is_current(path, render(changed))

    # The same through the command: a file written from other code is refused, and nothing is rewritten.
    path.write_bytes(render(changed).encode("utf-8"))
    stale = path.read_bytes()
    assert main(["--check", str(path)]) == 1
    assert path.read_bytes() == stale


def test_a_changed_field_type_or_a_new_route_changes_the_text(document: dict[str, Any]) -> None:
    retyped = copy.deepcopy(document)
    retyped["components"]["schemas"]["Customer"]["properties"]["balance"]["type"] = "string"
    assert render(retyped) != render(document)

    app = build(described_settings())
    before = render(app.openapi())

    @app.get("/api/v1/added-later")
    async def added_later() -> dict[str, str]:
        return {}

    app.openapi_schema = None
    assert render(app.openapi()) != before


def test_a_missing_file_or_other_line_endings_are_not_current(document: dict[str, Any], tmp_path: Path) -> None:
    text = render(document)
    assert not is_current(tmp_path / "absent.json", text)
    windows = tmp_path / "crlf.json"
    windows.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert not is_current(windows, text)
    assert main(["--check", str(windows)]) == 1


def test_both_sides_of_the_api_are_described_and_the_callbacks_are_not(document: dict[str, Any]) -> None:
    paths = set(document["paths"])
    assert {"/healthz", "/api/v1/me", "/api/v1/shops/{shop_id}/customers"} <= paths
    # The administrators' side is part of the same application and the same description.
    assert any(path.startswith("/api/admin/v1/") for path in paths)
    # Provider callbacks, the Telegram webhook, the metrics and the file links are not for the front end.
    hidden = ("/pay/", "/tg/", "/metrics", "/files/")
    assert not [path for path in paths if path.startswith(hidden)]
    served = {route.path for route in build(described_settings()).routes if isinstance(route, APIRoute)}
    assert {"/pay/payme", "/pay/click", "/tg/webhook"} <= served


def test_the_running_application_still_serves_no_description() -> None:
    app = build(described_settings())
    assert (app.openapi_url, app.docs_url, app.redoc_url) == (None, None, None)


def test_the_answers_that_are_typed(document: dict[str, Any]) -> None:
    """Which operations name their answer field by field. The rest answer with an open object."""
    typed = sorted(f"{method.upper()} {path}" for method, path, op in _operations(document) if _is_typed(op))
    assert typed == [
        # The page behind a customer's read-only link: closed, so nothing undeclared can reach a stranger.
        "GET /api/v1/customer-share",
        "GET /api/v1/me/shops",
        "GET /api/v1/shops/{shop_id}",
        "GET /api/v1/shops/{shop_id}/cash/categories",
        "GET /api/v1/shops/{shop_id}/cash/day",
        "GET /api/v1/shops/{shop_id}/cash/summary",
        "GET /api/v1/shops/{shop_id}/customers",
        "GET /api/v1/shops/{shop_id}/customers/{customer_id}",
        "GET /api/v1/shops/{shop_id}/customers/{customer_id}/share",
        "GET /api/v1/shops/{shop_id}/overview",
        "GET /api/v1/shops/{shop_id}/overview/debtors",
        "GET /api/v1/shops/{shop_id}/share-contact",
    ]


def _model_of(route: APIRoute) -> type[BaseModel] | None:
    """The route's response model. An annotation such as `dict[str, Any]` describes nothing and is none."""
    model = route.response_model
    return model if isinstance(model, type) and issubclass(model, BaseModel) else None


def _write_routes_with_a_model(app: FastAPI) -> list[str]:
    return sorted(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and _model_of(route) is not None and route.methods != {"GET"}
    )


def test_only_reads_carry_a_response_model() -> None:
    """A write may answer with a stored result of an older shape (ADR-006); a model could refuse it."""
    app = build(described_settings())
    assert _write_routes_with_a_model(app) == []

    # The negative case: the rule notices a write that was given one.
    @app.post("/api/v1/typed-write", response_model=Customer)
    async def typed_write() -> dict[str, str]:
        return {}

    assert _write_routes_with_a_model(app) == ["/api/v1/typed-write"]


def _open_or_defaulted(model: type[Answer]) -> list[str]:
    faults = [] if model.model_config.get("extra") == "forbid" and model.model_config.get("strict") else ["open"]
    return faults + [name for name, field in model.model_fields.items() if not field.is_required()]


def _response_models(app: FastAPI) -> set[type[Answer]]:
    found: set[type[Answer]] = set()

    def walk(annotation: Any) -> None:
        if isinstance(annotation, type) and issubclass(annotation, Answer):
            if annotation not in found:
                found.add(annotation)
                for field in annotation.model_fields.values():
                    walk(field.annotation)
            return
        for inner in getattr(annotation, "__args__", ()):
            walk(inner)

    for route in app.routes:
        model = _model_of(route) if isinstance(route, APIRoute) else None
        if model is not None:
            assert issubclass(model, Answer), f"{model.__name__} is not an Answer"
            walk(model)
    return found


# The only fields with a default: what a shop that works in US dollars gets beside its so'm figures
# (answers.py, "Dollars"). A route whose model has one leaves an unset field out of its answer, which the
# test below this one holds it to, so the default is never written into an answer.
DOLLAR_FIELDS = {
    "Customer": ["usd"],
    "CustomerDetail": ["usd"],
    "CustomerDollars": ["overdue", "payment_history"],
    "Debtor": ["usd"],
    "Entry": ["currency"],
    "Overview": ["usd"],
    "SharedAccount": ["usd"],
    "SharedEntry": ["currency"],
    "Shop": ["usd_on"],
    "StaffPaymentNotice": ["currency"],
}


def test_every_response_model_is_closed_strict_and_without_defaults() -> None:
    """So that a model can only refuse an answer, never drop, convert or fill in a field of it."""
    models = _response_models(build(described_settings()))
    assert len(models) >= 15  # the nested ones are found too
    faults = {model.__name__: _open_or_defaulted(model) for model in models if _open_or_defaulted(model)}
    assert faults == DOLLAR_FIELDS

    # The negative cases: an open model and a field with a default are both noticed.
    class Loose(Answer):
        model_config = ConfigDict(extra="ignore")
        name: str

    class Defaulted(Answer):
        name: str
        note: str | None = None

    assert _open_or_defaulted(Loose) == ["open"]
    assert _open_or_defaulted(Defaulted) == ["note"]


def _fills_in_defaults(app: FastAPI) -> list[str]:
    """Routes whose model has a field with a default and that would write the default into the answer."""

    def defaulted(model: type[Answer], seen: set[type[Answer]]) -> bool:
        if model in seen:
            return False
        seen.add(model)
        if any(not field.is_required() for field in model.model_fields.values()):
            return True
        nested: set[type[Answer]] = set()

        def collect(annotation: Any) -> None:
            if isinstance(annotation, type) and issubclass(annotation, Answer):
                nested.add(annotation)
            for inner in getattr(annotation, "__args__", ()):
                collect(inner)

        for field in model.model_fields.values():
            collect(field.annotation)
        return any(defaulted(inner, seen) for inner in nested)

    return sorted(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and (model := _model_of(route)) is not None
        and defaulted(model, set())
        and not route.response_model_exclude_unset
    )


def test_a_field_with_a_default_is_left_out_of_an_answer_never_filled_in() -> None:
    """An answer of a shop without dollars has no dollar field at all: not null, not zero, not there."""
    app = build(described_settings())
    assert _fills_in_defaults(app) == []

    # The negative case: a route with such a model that would write `"usd": null` is noticed.
    @app.get("/api/v1/filled-in", response_model=Customer)
    async def filled_in() -> dict[str, str]:
        return {}

    assert _fills_in_defaults(app) == ["/api/v1/filled-in"]
