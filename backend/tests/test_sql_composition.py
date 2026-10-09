"""SQL in the storage layer may be composed only from module-level constants.

Ruff's S608 is switched off for `infrastructure/db.py` because it cannot tell a constant fragment from
user input. This check replaces it with the precise rule: inside an f-string or a `.format()` call, only
a module-level constant (a name like `_CUSTOMER_COLUMNS`) or a string literal may appear. Values always
travel as bound parameters.
"""

import ast
import re
from pathlib import Path

DB_MODULE = Path(__file__).resolve().parents[1] / "src" / "qarz" / "infrastructure" / "db.py"
# The parts of the storage layer kept in files of their own are held to the same rule.
DB_MODULES = (DB_MODULE, DB_MODULE.with_name("db_cash.py"))
_CONSTANT = re.compile(r"_?[A-Z][A-Z0-9_]*")


def _is_constant(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Name):
        return bool(_CONSTANT.fullmatch(node.id))
    # CONSTANT.format(name="literal")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _is_constant(node.func.value) and not node.args and all(_is_constant(k.value) for k in node.keywords)
    return False


def unsafe_interpolations(source: str) -> list[int]:
    """Line numbers where something other than a constant is interpolated into a string."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FormattedValue) and not _is_constant(node.value):
            # f"request_key:{self._shop_id}:{key}" builds an advisory-lock name, which is passed as a
            # bound parameter; it is the one f-string in the module that is not SQL.
            lines.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and not _is_constant(node)
        ):
            lines.append(node.lineno)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant):
            lines.append(node.lineno)
    return sorted(set(lines))


def _allowed_lines(source: str) -> set[int]:
    return {
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if 'f"request_key:{self._shop_id}:{key}"' in line
        or 'return f"%{escaped}%"' in line
        or 'f"%{phone_digits}%"' in line
    }


def test_storage_sql_is_built_only_from_constants() -> None:
    for module in DB_MODULES:
        source = module.read_text(encoding="utf-8")
        assert set(unsafe_interpolations(source)) - _allowed_lines(source) == set(), module.name


def test_the_three_allowed_lines_are_bound_parameters_not_sql() -> None:
    source = DB_MODULE.read_text(encoding="utf-8")
    assert len(_allowed_lines(source)) == 3
    for number in _allowed_lines(source):
        line = source.splitlines()[number - 1]
        assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|FROM)\b", line), line


def test_the_stock_storage_sql_is_built_only_from_constants_with_no_exception() -> None:
    """`infrastructure/db_stock.py` (module I) has S608 switched off for the same reason and is held to
    the same rule, without a single allowed line."""
    source = (DB_MODULE.parent / "db_stock.py").read_text(encoding="utf-8")
    assert "text(" in source, "the module that is checked is the one that holds the statements"
    assert unsafe_interpolations(source) == []


def test_the_check_catches_a_value_put_into_sql() -> None:
    assert unsafe_interpolations('q = f"SELECT * FROM customer WHERE id = {customer_id}"\n') == [1]
    assert unsafe_interpolations("q = f\"SELECT {_COLUMNS} FROM customer WHERE name = '{name}'\"\n") == [1]
    assert unsafe_interpolations('q = "SELECT * FROM t WHERE id = {}".format(customer_id)\n') == [1]
    assert unsafe_interpolations("q = _PROMISED.format(entry=alias)\n") == [1]
    assert unsafe_interpolations('q = "SELECT * FROM t WHERE id = %s" % customer_id\n') == [1]


def test_the_check_accepts_constants() -> None:
    assert unsafe_interpolations('q = f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE c.id = :id"\n') == []
    assert unsafe_interpolations("q = f\"x {_PROMISED.format(entry='e')} y\"\n") == []
