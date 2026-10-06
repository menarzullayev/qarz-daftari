"""The role table against REQ-033, written out by hand."""

import pytest

from qarz.domain.access import ROLE_CAPABILITIES, Capability, Role, allows, lowest_role_with

EXPECTED = {
    Capability.RECORD: {Role.SELLER, Role.MANAGER, Role.OWNER},
    Capability.DECIDE_PAYMENT_NOTICE: {Role.SELLER, Role.MANAGER, Role.OWNER},
    Capability.MANAGE: {Role.MANAGER, Role.OWNER},
    Capability.READ_SHOP: {Role.MANAGER, Role.OWNER},
    Capability.ADMINISTER_SHOP: {Role.OWNER},
}


def test_every_capability_is_in_the_expected_table() -> None:
    assert set(EXPECTED) == set(Capability)
    assert set(ROLE_CAPABILITIES) == set(Role)


@pytest.mark.parametrize("capability", list(Capability))
@pytest.mark.parametrize("role", list(Role))
def test_allows(role: Role, capability: Capability) -> None:
    assert allows(role, capability) is (role in EXPECTED[capability])


def test_a_seller_cannot_manage_or_administer() -> None:
    assert not allows(Role.SELLER, Capability.MANAGE)
    assert not allows(Role.SELLER, Capability.READ_SHOP)
    assert not allows(Role.SELLER, Capability.ADMINISTER_SHOP)


def test_a_manager_cannot_administer() -> None:
    assert not allows(Role.MANAGER, Capability.ADMINISTER_SHOP)


def test_roles_only_ever_gain_capabilities_upward() -> None:
    assert ROLE_CAPABILITIES[Role.SELLER] < ROLE_CAPABILITIES[Role.MANAGER] < ROLE_CAPABILITIES[Role.OWNER]


@pytest.mark.parametrize(
    ("capability", "role"),
    [
        (Capability.RECORD, Role.SELLER),
        (Capability.MANAGE, Role.MANAGER),
        (Capability.READ_SHOP, Role.MANAGER),
        (Capability.ADMINISTER_SHOP, Role.OWNER),
    ],
)
def test_lowest_role_with(capability: Capability, role: Role) -> None:
    assert lowest_role_with(capability) is role
