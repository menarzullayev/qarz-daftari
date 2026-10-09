"""The actions the network between shops writes into a shop's activity log, enumerated from the code.

The owner's activity log shows an action by the name the panel has for it and, for an action it has no
name for, by the server's raw word. So the set of actions is a contract with the panel, and it is kept in
one file both sides read: `tests/data/network_actions.json`. This test holds that file to the code: the
steps two shops share are logged by the database's `network_log` (migration 0045 and whatever replaces
its functions later), a shop's own steps by the application's `record_activity`. The panel's twin
(`frontend/src/panel/networkActivity.test.ts`) holds its names to the same file, in every language.
"""

import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LISTED = json.loads((BACKEND / "tests" / "data" / "network_actions.json").read_text(encoding="utf-8"))

_CALL = re.compile(r"PERFORM network_log\((.*?)\);", re.DOTALL)
_OUTCOMES = re.compile(r"outcome := CASE own\.role WHEN 'buyer' THEN '(\w+)' ELSE '(\w+)' END")
_LITERAL = re.compile(r"'([a-z_]+)'")


def logged_by_the_database(sql: str) -> tuple[set[str], set[str]]:
    """The kinds and the subjects of every `network_log` call: `network_log` writes the action
    `'network.' || kind` about a `'network_' || subject`."""
    outcomes = {name for pair in _OUTCOMES.findall(sql) for name in pair}
    actions: set[str] = set()
    subjects: set[str] = set()
    for call in _CALL.findall(sql):
        # The arguments before the acting member: both shops, the link, the subject, its id, the kind.
        head = call.split("p_member")[0]
        subject, *kinds = _LITERAL.findall(head)
        subjects.add(f"network_{subject}")
        if "|| outcome" in head:
            assert outcomes, "a kind built from an outcome, and no outcome found"
            actions |= {f"network.{kinds[0]}{outcome}" for outcome in outcomes}
        else:
            assert kinds, call
            actions |= {f"network.{kind}" for kind in kinds}
    return actions, subjects


def recorded_by_the_application() -> tuple[set[str], set[str]]:
    actions: set[str] = set()
    subjects: set[str] = set()
    for path in sorted((BACKEND / "src" / "qarz").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        actions |= set(re.findall(r'action="(network\.[a-z_]+)"', source))
        subjects |= set(re.findall(r'subject_type="(network_[a-z_]+)"', source))
    return actions, subjects


def in_the_code() -> tuple[set[str], set[str]]:
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted((BACKEND / "migrations" / "sql").glob("*.sql")))
    database = logged_by_the_database(sql)
    application = recorded_by_the_application()
    # An action written into the log by a statement of its own, not through `network_log`.
    direct = set(re.findall(r"'(network\.[a-z_]+)'", sql))
    return database[0] | application[0] | direct, database[1] | application[1]


def test_the_listed_actions_are_exactly_the_ones_the_code_writes() -> None:
    actions, subjects = in_the_code()
    assert sorted(actions) == LISTED["actions"]
    assert sorted(subjects) == LISTED["subjects"]
    # What is known today, so that an empty search does not pass as "nothing to name".
    assert (
        len(actions) == 20 and {"network.note_received", "network.order_cancelled", "network.invite_created"} <= actions
    )


def test_an_action_the_list_does_not_have_is_noticed() -> None:
    """The counterpart: a step added to a function, or to the application, is found by the search."""
    added = """
      PERFORM network_log(p_shop, p_peer, own.link_id, 'note', p_note, 'note_returned', p_member, p_now, NULL);
      outcome := CASE own.role WHEN 'buyer' THEN 'cancelled' ELSE 'declined' END;
      PERFORM network_log(p_shop, p_peer, own.link_id, 'order', p_order, 'order_' || outcome, p_member, p_now,
                          jsonb_build_object('number', own.number, 'reason', p_reason));
      PERFORM network_log(p_shop, p_peer, own.link_id, 'claim', p_claim,
                          CASE WHEN before.id IS NULL THEN 'claim_made' ELSE 'claim_changed' END,
                          p_member, p_now, NULL);
    """
    actions, subjects = logged_by_the_database(added)
    assert actions == {
        "network.note_returned",
        "network.order_cancelled",
        "network.order_declined",
        "network.claim_made",
        "network.claim_changed",
    }
    assert subjects == {"network_note", "network_order", "network_claim"}
    assert "network.note_returned" not in LISTED["actions"]
