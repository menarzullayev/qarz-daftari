"""In whose name a confirmed delivery note is posted on the supplier's side: the rule itself.

`author_of` (qarz.application.network_orders) decides it from the issuer's membership as it stands now
and the shop's owner. The database decides the same in `network_note_author` (migration 0048), which says
"holds `credits.record`" in SQL for that one key; what that SQL takes for granted about the catalogue is
held here, and tests/db/test_network_note_author.py holds the two answers together case by case.
"""

import uuid
from pathlib import Path

import pytest

from qarz.application.errors import NotFound
from qarz.application.network_orders import ISSUER_NOT_ACTIVE, ISSUER_NOT_PERMITTED, author_of
from qarz.application.ports import Membership
from qarz.domain import permissions
from qarz.domain.access import Role

ISSUER, OWNER = uuid.uuid4(), uuid.uuid4()
DENIED = frozenset({permissions.CREDITS_RECORD})
SQL = Path(__file__).resolve().parents[1] / "migrations" / "sql" / "0048_founder_decisions.sql"


def member(role: Role, *, on: bool = False, denied: frozenset[str] = frozenset()) -> Membership:
    return Membership(ISSUER, role, permissions_on=on, denied=denied)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("on", [False, True])
def test_an_active_issuer_who_may_record_a_credit_sale_stays_the_author(role: Role, on: bool) -> None:
    author, left = author_of(ISSUER, member(role, on=on), OWNER)
    # Written with an owner's authority, as before: the note was agreed when it was issued.
    assert (author, left) == (Membership(ISSUER, Role.OWNER), None)


def test_a_denial_counts_only_while_the_switch_is_on_and_never_for_the_owner() -> None:
    for role in (Role.SELLER, Role.MANAGER):
        assert author_of(ISSUER, member(role, on=False, denied=DENIED), OWNER)[0].membership_id == ISSUER
        assert author_of(ISSUER, member(role, on=True, denied=DENIED), OWNER) == (
            Membership(OWNER, Role.OWNER),
            ISSUER_NOT_PERMITTED,
        )
    # Nothing stored reduces the owner's rights (the database refuses to store it at all).
    assert author_of(ISSUER, member(Role.OWNER, on=True, denied=DENIED), OWNER)[0].membership_id == ISSUER


def test_an_issuer_who_is_no_longer_an_active_member_gives_way_to_the_owner() -> None:
    assert author_of(ISSUER, None, OWNER) == (Membership(OWNER, Role.OWNER), ISSUER_NOT_ACTIVE)


def test_only_the_permission_to_record_a_credit_sale_is_asked() -> None:
    """Issuing a note asks for `credits.record` alone, with or without money paid on delivery; so does
    this. A member denied everything else is still the author."""
    others = permissions.ALL_KEYS - permissions.FIXED_KEYS - {permissions.CREDITS_RECORD}
    assert permissions.PAYMENTS_RECORD in others and permissions.NETWORK_FULFIL in others
    assert author_of(ISSUER, member(Role.SELLER, on=True, denied=frozenset(others)), OWNER)[1] is None


def test_with_nobody_the_rule_allows_nothing_is_written() -> None:
    with pytest.raises(NotFound):
        author_of(ISSUER, None, None)
    with pytest.raises(NotFound):
        author_of(ISSUER, member(Role.MANAGER, on=True, denied=DENIED), None)


def test_what_the_database_takes_for_granted_about_the_catalogue_is_so() -> None:
    """`network_note_author` reads only the denials of `credits.record`: right while every role holds
    the permission by default (a grant then adds nothing) and it is not a fixed one (a denial counts).
    If the catalogue changes either, the function has to change with it, in a migration."""
    permission = permissions.get(permissions.CREDITS_RECORD)
    assert permission.roles == frozenset(Role)
    assert not permission.fixed
    # The statements of this decision's block of the migration, without its comments.
    block = SQL.read_text(encoding="utf-8").split("-- Decision 2:")[1].split("-- End of decision 2.")[0]
    sql = "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("--"))
    assert f"'{permissions.CREDITS_RECORD}' = ANY (m.permissions_denied)" in sql
    assert "permissions_granted" not in sql
    # Both entries of a note are compared with the rule, and nothing is compared with the issuer alone.
    assert sql.count("e.author_id = network_note_author(p_peer, theirs.issued_by)") == 2
    assert "author_id = theirs.issued_by" not in sql
