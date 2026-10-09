"""The permission catalogue and the one function that decides (expansion module G).

No database here: the catalogue against a table written out by hand, the catalogue against the registered
operations and the role table, and the rules of `effective`. The API's side is tests/api/test_permissions.py.
"""

import re
import uuid
from pathlib import Path

import pytest

from qarz.application import authorization
from qarz.application.errors import BeyondOwnPermissions, ForbiddenPermission, ForbiddenRole
from qarz.application.operations import Operation, all_operations
from qarz.application.ports import Membership, StaffContact
from qarz.application.staff import _within_own
from qarz.domain import permissions
from qarz.domain.access import Capability, Role, allows
from qarz.domain.permissions import InvalidOverrides, clean_overrides, effective, holds, source
from qarz.interface.asgi import build  # noqa: F401  (importing the application registers every operation)

S, M, OWN = Role.SELLER, Role.MANAGER, Role.OWNER
BACKEND = Path(__file__).resolve().parents[1]

# Key -> (roles that hold it by default, whether it is fixed). Written by hand: a change of the catalogue
# has to be made here as well, by someone who means it.
EXPECTED: dict[str, tuple[set[Role], bool]] = {
    "ledger.view": ({S, M, OWN}, False),
    "customers.create": ({S, M, OWN}, False),
    "customers.edit": ({M, OWN}, False),
    "customers.share": ({M, OWN}, False),
    "credits.record": ({S, M, OWN}, False),
    "payments.record": ({S, M, OWN}, False),
    "payment_notices.decide": ({S, M, OWN}, False),
    "entries.others": ({M, OWN}, False),
    "entries.over_limit": ({M, OWN}, False),
    "entries.cancel": ({M, OWN}, False),
    "promises.change": ({M, OWN}, False),
    "disputes.decide": ({M, OWN}, False),
    "goods.edit": ({M, OWN}, False),
    "reminders.send": ({M, OWN}, False),
    "reports.view": ({M, OWN}, False),
    "reports.export": ({M, OWN}, False),
    "imports.run": ({M, OWN}, False),
    "settings.view": ({M, OWN}, False),
    "settings.edit": ({M, OWN}, False),
    "shop.edit": ({OWN}, False),
    "staff.manage": ({OWN}, False),
    "activity.view": ({OWN}, False),
    "membership.own": ({S, M, OWN}, True),
    "permissions.manage": ({OWN}, True),
    "ownership.transfer": ({OWN}, True),
    "ownership.receive": ({M, OWN}, True),
    "subscription.manage": ({OWN}, True),
    "support.manage": ({OWN}, True),
    "shop.delete": ({OWN}, True),
}
MOVABLE = sorted(key for key, (_, fixed) in EXPECTED.items() if not fixed)
FIXED = sorted(key for key, (_, fixed) in EXPECTED.items() if fixed)
NONE: frozenset[str] = frozenset()


def shop_operations() -> list[Operation]:
    return [op for op in all_operations() if op.scope == "shop"]


# --- the catalogue ------------------------------------------------------------------------------------


def test_the_catalogue_is_the_expected_table() -> None:
    assert {p.key: (set(p.roles), p.fixed) for p in permissions.CATALOGUE} == EXPECTED
    assert set(EXPECTED) == permissions.ALL_KEYS
    assert set(FIXED) == permissions.FIXED_KEYS


def test_what_stays_the_owners_is_fixed() -> None:
    """Deleting the shop, the ownership, the permissions themselves, the subscription, support access."""
    for key in ("shop.delete", "ownership.transfer", "permissions.manage", "subscription.manage", "support.manage"):
        assert permissions.get(key).fixed and permissions.get(key).roles == {OWN}


def test_every_permission_and_group_is_named_in_uzbek_and_in_russian() -> None:
    cyrillic = re.compile("[а-яА-ЯёЁ]")
    for item in (*permissions.CATALOGUE, *permissions.GROUPS):
        assert item.uz.strip() and item.ru.strip(), item.key
        assert not cyrillic.search(item.uz), f"{item.key}: the Uzbek label is in Latin script"
        assert cyrillic.search(item.ru), f"{item.key}: the Russian label is in Russian"
    assert {p.group for p in permissions.CATALOGUE} == {g.key for g in permissions.GROUPS}, "no empty group"


def test_every_key_has_the_shape_the_database_accepts() -> None:
    shape = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$")
    for key in permissions.ALL_KEYS:
        assert shape.match(key) and len(key) <= 80, key
    assert len(permissions.ALL_KEYS) <= permissions.MAX_OVERRIDES, "a member's changes must fit what is stored"


def without_a_permission(operations: list[Operation]) -> list[str]:
    return sorted(op.name for op in operations if not permissions.permissions_of_operation(op.name))


def test_every_shop_operation_has_a_permission() -> None:
    assert without_a_permission(shop_operations()) == [], (
        "add the new operation to a Permission of qarz.domain.permissions.CATALOGUE"
    )
    assert len(shop_operations()) > 50


def test_an_operation_without_a_permission_is_noticed_and_reachable_by_nobody() -> None:
    forgotten = Operation("cash.entries.create", "shop", Capability.RECORD)
    assert without_a_permission([*shop_operations(), forgotten]) == ["cash.entries.create"]
    for role in Role:
        with pytest.raises(LookupError):
            authorization.require_operation(Membership(uuid.uuid4(), role), forgotten)


def test_the_catalogue_names_only_operations_that_exist() -> None:
    registered = {op.name for op in shop_operations()}
    named = {name for permission in permissions.CATALOGUE for name in permission.operations}
    assert named <= registered, sorted(named - registered)


@pytest.mark.parametrize("op", shop_operations(), ids=lambda op: op.name)
def test_the_defaults_of_an_operations_permissions_are_the_role_table(op: Operation) -> None:
    """With nothing changed for a member, a permission opens an operation to exactly the roles the role
    table (qarz.domain.access) opens it to. This is what makes "switch off" the behaviour of before."""
    assert op.capability is not None
    opening = permissions.permissions_of_operation(op.name)
    for permission in opening:
        for role in Role:
            assert (role in permission.roles) is allows(role, op.capability), (op.name, permission.key, role)


def test_only_recording_an_entry_is_opened_by_two_permissions() -> None:
    several = {op.name for op in shop_operations() if len(permissions.permissions_of_operation(op.name)) > 1}
    assert several == {"ledger.entry.create"}, "a service must ask for the one the request needs"


def test_role_defaults_only_grow_upward() -> None:
    assert permissions.role_defaults(S) < permissions.role_defaults(M) < permissions.role_defaults(OWN)
    assert permissions.role_defaults(OWN) == permissions.ALL_KEYS


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_the_lowest_role_holding_a_permission(key: str) -> None:
    assert permissions.lowest_role_holding(key) is min(EXPECTED[key][0], key=[S, M, OWN].index)


# --- the rules of `effective` -------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(EXPECTED))
@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("state", ["none", "granted", "denied"])
def test_every_permission_for_every_role_and_override(key: str, role: Role, state: str) -> None:
    roles, fixed = EXPECTED[key]
    granted = frozenset({key}) if state == "granted" else NONE
    denied = frozenset({key}) if state == "denied" else NONE
    if role is OWN:
        expected = True  # the owner holds everything, always
    elif fixed or state == "none":
        expected = role in roles  # the role alone
    else:
        expected = state == "granted"
    assert holds(role, granted, denied, key) is expected
    assert source(role, granted, denied, key) == (
        "role" if role is OWN or fixed else {"none": "role"}.get(state, state)
    )


def test_nothing_reduces_the_owner() -> None:
    assert effective(OWN, NONE, permissions.ALL_KEYS) == permissions.ALL_KEYS
    assert effective(OWN, NONE, frozenset({"shop.delete", "ledger.view"})) == permissions.ALL_KEYS


@pytest.mark.parametrize("role", [S, M])
def test_a_fixed_permission_cannot_be_granted(role: Role) -> None:
    everything = permissions.ALL_KEYS
    held = effective(role, everything, NONE)
    assert held == (permissions.ALL_KEYS - permissions.FIXED_KEYS) | (permissions.role_defaults(role))
    for key in ("permissions.manage", "shop.delete", "ownership.transfer", "subscription.manage", "support.manage"):
        assert key not in held


def test_reading_ones_own_permissions_cannot_be_denied() -> None:
    assert "membership.own" in effective(S, NONE, permissions.ALL_KEYS)


def test_a_fixed_permission_cannot_be_denied() -> None:
    assert "ownership.receive" in effective(M, NONE, frozenset({"ownership.receive"}))


def test_a_key_the_catalogue_does_not_know_is_ignored() -> None:
    assert effective(S, frozenset({"cash.record", "nonsense"}), frozenset({"gone.away"})) == effective(S)
    with pytest.raises(LookupError):
        holds(S, NONE, NONE, "cash.record")


def test_a_denial_wins_over_a_grant_of_the_same_key() -> None:
    # The database refuses to store both; if both ever arrive, the member does not hold the permission.
    assert "reports.view" not in effective(S, frozenset({"reports.view"}), frozenset({"reports.view"}))


# --- what may be stored -------------------------------------------------------------------------------


def test_only_real_changes_are_kept() -> None:
    granted, denied = clean_overrides(S, ["reports.view", "ledger.view"], ["payments.record", "entries.cancel"])
    assert granted == {"reports.view"}, "the seller's role already gives ledger.view"
    assert denied == {"payments.record"}, "the seller's role does not give entries.cancel"
    assert clean_overrides(M, [], []) == (NONE, NONE)


@pytest.mark.parametrize(
    ("granted", "denied", "field"),
    [
        (["cash.record"], [], "granted"),
        ([], ["nonsense"], "denied"),
        (["permissions.manage"], [], "granted"),
        (["shop.delete"], [], "granted"),
        (["ownership.transfer"], [], "granted"),
        (["subscription.manage"], [], "granted"),
        (["support.manage"], [], "granted"),
        ([], ["ownership.receive"], "denied"),
        ([], ["membership.own"], "denied"),
        (["reports.view", "reports.view"], [], "granted"),
        (["reports.view"], ["reports.view"], "denied"),
        ([f"area.key_{'x' * n}" for n in range(65)], [], "granted"),
    ],
)
def test_changes_that_cannot_be_stored(granted: list[str], denied: list[str], field: str) -> None:
    with pytest.raises(InvalidOverrides) as refused:
        clean_overrides(S, granted, denied)
    assert field in refused.value.fields


def test_the_owner_has_no_changes() -> None:
    with pytest.raises(InvalidOverrides):
        clean_overrides(OWN, [], [])
    with pytest.raises(InvalidOverrides):
        clean_overrides(OWN, [], ["reports.view"])


# --- the application's side ---------------------------------------------------------------------------


def member(role: Role, *, on: bool, granted: set[str] | None = None, denied: set[str] | None = None) -> Membership:
    return Membership(uuid.uuid4(), role, on, frozenset(granted or ()), frozenset(denied or ()))


@pytest.mark.parametrize("key", MOVABLE)
@pytest.mark.parametrize("role", [S, M])
def test_with_the_switch_off_stored_changes_are_ignored(key: str, role: Role) -> None:
    by_role = role in EXPECTED[key][0]
    assert authorization.may(member(role, on=False, granted={key}), key) is by_role
    assert authorization.may(member(role, on=False, denied={key}), key) is by_role
    assert authorization.may(member(role, on=True, granted={key}), key) is True
    assert authorization.may(member(role, on=True, denied={key}), key) is False


def test_the_refusal_is_by_role_with_the_switch_off_and_by_permission_with_it_on() -> None:
    off = authorization.refusal(member(S, on=False), "entries.cancel")
    assert type(off) is ForbiddenRole and off.code == "FORBIDDEN_ROLE" and off.fields == {"needed_role": "manager"}
    on = authorization.refusal(member(S, on=True), "entries.cancel")
    assert type(on) is ForbiddenPermission and on.code == "FORBIDDEN_PERMISSION"
    assert on.fields == {"permission": "entries.cancel"}
    assert isinstance(on, ForbiddenRole), "whatever refuses by role refuses this too"


def test_an_unknown_permission_is_an_error_not_a_refusal() -> None:
    with pytest.raises(LookupError):
        authorization.may(member(OWN, on=True), "cash.record")


def test_only_holders_are_told() -> None:
    contacts = [
        StaffContact(1, "uz", member(OWN, on=True)),
        StaffContact(2, "ru", member(M, on=True, denied={"disputes.decide"})),
        StaffContact(3, "uz", member(S, on=True, granted={"disputes.decide"})),
        StaffContact(4, "uz", member(S, on=True)),
    ]
    assert authorization.holders(contacts, "disputes.decide") == [(1, "uz"), (3, "uz")]
    off = [StaffContact(c.tg_id, c.lang, Membership(c.member.membership_id, c.member.role)) for c in contacts]
    assert authorization.holders(off, "disputes.decide") == [(1, "uz"), (2, "ru")], "by role, as before"


# --- a member who manages staff without being the owner ------------------------------------------------


def test_the_owner_is_never_held_to_their_own_rights() -> None:
    owner = member(OWN, on=True)
    _within_own(owner, M)
    _within_own(owner, S, frozenset(permissions.ALL_KEYS))
    _within_own(owner, S, target=owner.membership_id)


def test_a_staff_manager_acts_on_sellers_only() -> None:
    manager = member(M, on=True, granted={"staff.manage"})
    _within_own(manager, S)
    for role in (M, OWN):
        with pytest.raises(BeyondOwnPermissions):
            _within_own(manager, role)


def test_a_staff_manager_cannot_act_on_themselves() -> None:
    seller = member(S, on=True, granted={"staff.manage"})
    with pytest.raises(BeyondOwnPermissions):
        _within_own(seller, S, target=seller.membership_id)


def test_a_staff_manager_cannot_bring_in_what_they_do_not_hold() -> None:
    manager = member(M, on=True, granted={"staff.manage"}, denied={"payments.record"})
    with pytest.raises(BeyondOwnPermissions):
        _within_own(manager, S)  # a seller takes payments by default; this manager may not
    full = member(M, on=True, granted={"staff.manage"})
    _within_own(full, S, frozenset({"reports.view"}))
    with pytest.raises(BeyondOwnPermissions):
        _within_own(full, S, frozenset({"shop.edit"}))  # granted to the seller by the owner; not the manager's


# --- the client's fallback table ------------------------------------------------------------------------


def test_the_clients_table_of_role_defaults_is_the_catalogues() -> None:
    """While the switch is off a client is told nothing and offers by role: its table of the lowest role
    holding each permission (frontend/src/shared/permissions.ts) must be this catalogue's."""
    source = (BACKEND.parent / "frontend" / "src" / "shared" / "permissions.ts").read_text(encoding="utf-8")
    table = source.split("export const MIN_ROLE = {", 1)[1].split("} as const", 1)[0]
    found = dict(re.findall(r'^\s*"?([a-z_.]+)"?: "(seller|manager|owner)",$', table, flags=re.MULTILINE))
    assert found == {key: permissions.lowest_role_holding(key).value for key in permissions.ALL_KEYS}
