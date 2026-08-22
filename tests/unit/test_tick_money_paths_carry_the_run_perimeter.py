"""RT-010-6: the tick's money paths must carry the run perimeter.

Program 010, finding `F-010-4`.

`F-010-3` closed the two Interact Mode routes.  The tick reaches the same money code by
itself, and it reached it unbounded: clearing searched the whole equivalent and payments
routed through it, so a run could reduce another run's obligations or consume their trust
capacity without anyone acting.

Two halves, and they are guarded differently on purpose.

The BEHAVIOUR of the perimeter is already proven where it lives - the service refuses a
foreign cycle and a foreign hop, on both dialects, each layer pinned separately and
mutation-checked (`test_p1_clearing_run_perimeter.py`, `test_p1_payment_run_perimeter.py`,
and the PostgreSQL interlock case).  What was missing on the tick path is not behaviour but
WIRING: `create_payment_internal_staged` did not accept the perimeter at all, and the three
clearing calls did not pass it.  The staged path gets a behavioural test of its own, because
its parameter is new; the call sites get this structural guard, because what can go wrong
there is exactly one thing and it is visible in the source - a forgotten keyword.

That is the same trade made for the replay resolvers, and it is stated rather than assumed:
building a full tick stand per call site to catch a missing keyword would cost more than it
protects, while a claim of complete wiring with nothing able to fail is what this program
keeps finding in other people's work.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EXECUTOR = _ROOT / "app" / "core" / "simulator" / "real_payments_executor.py"
_CLEARING = _ROOT / "app" / "core" / "simulator" / "real_clearing_engine.py"

# Every money entry point the tick uses that accepts a run perimeter.
_GUARDED = {
    "create_payment_internal_staged",
    "find_cycles",
    "execute_clearing_with_amount",
}


def _guarded_calls(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in _GUARDED:
            yield node


@pytest.mark.parametrize(
    ("path", "expected_calls"),
    [(_EXECUTOR, 1), (_CLEARING, 3)],
    ids=["payments_executor", "clearing_engine"],
)
def test_every_tick_money_call_passes_the_perimeter(path: pathlib.Path, expected_calls: int) -> None:
    calls = list(_guarded_calls(path))

    assert len(calls) == expected_calls, (
        f"{path.name}: expected {expected_calls} guarded money calls, found {len(calls)} - "
        "the set this test watches is out of date with the code, which makes it worthless"
    )

    missing = [
        f"{call.func.attr} at line {call.lineno}"
        for call in calls
        if not any(kw.arg == "allowed_participant_pids" for kw in call.keywords)
    ]
    assert not missing, (
        f"{path.name}: these tick money paths run unbounded across the whole equivalent, so "
        "a run can touch another run's participants: " + ", ".join(missing)
    )


def test_the_perimeter_helper_distinguishes_unseeded_from_empty() -> None:
    """None means "not applied"; an empty set would mean "nobody", and they are not the same.

    Getting this backwards in either direction is a defect this wave has already made:
    reading "cannot establish" as "no restriction" is F-009-1, while reading "not loaded yet"
    as "nobody" would stop the tick working at all.  The first version of this helper made
    the first mistake - an empty participant list returned None, so a run with an empty
    scenario would have cleared and routed across the whole equivalent.
    """

    from types import SimpleNamespace

    from app.core.simulator.run_perimeter import run_perimeter_pids

    # Not loaded yet -> no perimeter can be applied. The tick loads the list before any
    # money path is reached, so this state is not reachable from clearing or payments.
    assert run_perimeter_pids(SimpleNamespace(_real_participants=None)) is None
    assert run_perimeter_pids(SimpleNamespace()) is None

    # Loaded and empty -> a perimeter admitting NOBODY, not the absence of one. Collapsing
    # this into None would let a run with an empty scenario clear and route across the whole
    # equivalent, which is the shape of F-009-1 again.
    assert run_perimeter_pids(SimpleNamespace(_real_participants=[])) == set()

    import uuid

    seeded = SimpleNamespace(
        _real_participants=[(uuid.uuid4(), "a1"), (uuid.uuid4(), "a2")]
    )
    assert run_perimeter_pids(seeded) == {"a1", "a2"}
