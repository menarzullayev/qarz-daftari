"""Turning measured response times into a table against the performance targets.

Pure functions: no database, no network. The targets are those of the technical specification
(docs/08-technical-spec/OUTPUT.md, "Performance targets"); all of them are stated at the 95th percentile.
"""

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

MET, MISSED, NO_TARGET, NO_DATA = "met", "NOT met", "no target", "not measured"

# operation -> (milliseconds at the 95th percentile, the requirement that sets it)
TARGETS: dict[str, tuple[int, str]] = {
    "chat_credit": (500, "NFR-001"),
    "chat_payment": (500, "NFR-001"),
    "customers_list": (300, "NFR-005"),
    "customers_search": (300, "NFR-005"),
    "customers_search_phone": (300, "NFR-005"),
    "overview": (300, "NFR-005"),
    "debtors": (300, "NFR-005"),
    "debtors_overdue": (300, "NFR-005"),
    "api_itemized_10": (400, "NFR-009"),
    "report_period_year": (5000, "NFR-011"),
}
# In the order they are reported.
OPERATIONS = (
    "chat_credit",
    "chat_payment",
    "api_credit",
    "api_payment",
    "api_itemized_10",
    "customers_list",
    "customers_search",
    "customers_search_phone",
    "overview",
    "debtors",
    "debtors_overdue",
    "customer_page",
    "report_period_year",
    "report_overdue",
)
SCOPES = ("large", "other")


def percentile(values: Sequence[float], share: float) -> float:
    """Nearest-rank percentile: the smallest value that at least `share` of the values do not exceed."""
    if not values:
        raise ValueError("no values")
    if not 0 < share <= 1:
        raise ValueError("share must be above 0 and at most 1")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass
class Samples:
    """What was observed for one operation in one scope."""

    ok_ms: list[float] = field(default_factory=list)
    refused: Counter[str] = field(default_factory=Counter)  # 4xx answers other than 429, by code
    errors: Counter[str] = field(default_factory=Counter)  # 5xx answers, timeouts, broken connections
    # 429 answers: the application's rate limit held the request back. Not a fault of the server and
    # not an answer either, so they are counted on their own.
    limited: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "ok_ms": [round(ms, 2) for ms in self.ok_ms],
            "refused": dict(self.refused),
            "errors": dict(self.errors),
            "limited": self.limited,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Samples":
        return cls(list(data["ok_ms"]), Counter(data["refused"]), Counter(data["errors"]), int(data.get("limited", 0)))


@dataclass(frozen=True)
class Line:
    operation: str
    scope: str
    count: int
    p50: float | None
    p95: float | None
    p99: float | None
    worst: float | None
    refused: int
    limited: int
    errors: int
    target_ms: int | None
    requirement: str | None
    verdict: str


def summarize(operation: str, scope: str, samples: Samples) -> Line:
    target = TARGETS.get(operation)
    target_ms, requirement = target if target is not None else (None, None)
    refused, errors, limited = sum(samples.refused.values()), sum(samples.errors.values()), samples.limited
    # For a target, a request that was held back or failed is a request that was not served in time.
    unserved = errors + limited
    values = samples.ok_ms
    if not values:
        # Nothing answered: with errors that is a failure of the target, without them it was not exercised.
        verdict = NO_TARGET if target is None else (MISSED if unserved else NO_DATA)
        return Line(
            operation, scope, 0, None, None, None, None, refused, limited, errors, target_ms, requirement, verdict
        )
    p95 = percentile(values, 0.95)
    if target is None:
        verdict = NO_TARGET
    else:
        # A target is met only if the calls that failed, had they answered late, could not have moved
        # the 95th percentile over it: every error is counted as a response slower than the target.
        slow = sum(1 for ms in values if ms > target[0]) + unserved
        verdict = MET if slow <= (len(values) + unserved) * 0.05 else MISSED
    return Line(
        operation,
        scope,
        len(values),
        percentile(values, 0.5),
        p95,
        percentile(values, 0.99),
        max(values),
        refused,
        limited,
        errors,
        target_ms,
        requirement,
        verdict,
    )


def lines(results: dict[tuple[str, str], Samples]) -> list[Line]:
    found = []
    for operation in OPERATIONS:
        for scope in SCOPES:
            samples = results.get((operation, scope))
            if samples is not None:
                found.append(summarize(operation, scope, samples))
    return found


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def markdown(table: Iterable[Line]) -> str:
    out = [
        "| Operation | Shop | Calls | p50 ms | p95 ms | p99 ms | max ms | Refused | 429 | Errors "
        "| Target (p95) | Result |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for line in table:
        target = "-" if line.target_ms is None else f"{line.target_ms:,} ms ({line.requirement})"
        out.append(
            f"| {line.operation} | {line.scope} | {line.count:,} | {_ms(line.p50)} | {_ms(line.p95)} | {_ms(line.p99)} "
            f"| {_ms(line.worst)} | {line.refused:,} | {line.limited:,} | {line.errors:,} | {target} | {line.verdict} |"
        )
    return "\n".join(out)
