"""039 D-fix (2026-10-19): in a REAL tick, `clearing.done` is published BEFORE the trust growth it causes.

WHAT WAS WRONG (on `4e9f083c`). `tick.py::RealTick._run_clearing` ran `_grow_trust_after_clearing` before
`emit_clearing_done`. When growth raised a limit it broadcast `topology.changed` (`reason: trust_drift_growth`) with an
edge patch of EVERY edge of the equivalent, read after the clearing had committed: the post-clearing debts reached the
Simulator UI one event before `clearing.done`. The UI takes the clearing's "was -> now" around the patches of
`clearing.done` (`simulator-ui/v2/src/composables/debtChange.ts`), found nothing left to change and stated no pair - on
the live demo the line never appeared after an automatic clearing (2 of 2 observed), only after a manual one (whose
path grows no trust).

THE TARGET (advisor's decision (a), 2026-10-19). Order within the tick: `clearing.done` (patches as of the clearing's
commit, limits not yet grown), then `topology.changed` `trust_drift_growth` (the grown limits). Names and shapes of
both events are unchanged; only the order moves.

THE STAND. The real `RealRunnerImpl.tick_real_mode` on PostgreSQL (`tests/simulator_tick_stand.py`), the ring of
`test_p021_trust_drift_is_audited_postgres.py` with UNEQUAL debts, so the clearing leaves debt on two lines and the
"as of the clearing" claim is measured on non-zero `used`: A owes B 10, B owes C 20, C owes A 30; clearing takes 10
off each line; growth raises every touched line 1000 -> 1050.

The CONTROLS make the stand able to see the defect: growth really ran (the limits in the database are 1050) and
really broadcast its event, and the event carries the grown limit - otherwise "clearing.done carries 1000" would hold
with no growth at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    GROWTH,
    factory,
    limits,
    run_for,
    runner_for,
    scenario_for,
    ticks,
    world,
)
from tests.simulator_tick_stand import install_tick_stand

LINES = [("B", "A", "1000.00", "active"), ("C", "B", "1000.00", "active"), ("A", "C", "1000.00", "active")]
DEBTS = [("A", "B", "10.00"), ("B", "C", "20.00"), ("C", "A", "30.00")]
#: (creditor, debtor) -> debt after the clearing of 10 on the ring.
AFTER_CLEARING = {("B", "A"): Decimal("0"), ("C", "B"): Decimal("10"), ("A", "C"): Decimal("20")}


def _by_pair(patch: list[dict], names: dict[str, str]) -> dict[tuple[str, str], dict]:
    return {(names[p["source"]], names[p["target"]]): p for p in patch or [] if p["source"] in names and p["target"] in names}


@pytest.mark.asyncio
async def test_clearing_done_precedes_the_trust_growth_it_causes_and_is_as_of_the_clearing(factory, monkeypatch) -> None:  # noqa: F811
    eq, people = await world(factory, ["A", "B", "C"], LINES, DEBTS)
    scenario = scenario_for(eq, people, LINES, GROWTH)
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=1)
    install_tick_stand(monkeypatch, factory)
    names = {p.pid: r for r, p in people.items()}

    await ticks(runner, run, 1)

    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)
    # ── controls: the clearing happened and growth really raised every touched line ──────────────────────────────
    after = await limits(factory, eq, people)
    assert {k: v[0] for k, v in after.items()} == {k: Decimal("1050.00") for k, _l in AFTER_CLEARING.items()}, after
    events = runner._sse.events  # the stand's `RecordingSse`, in broadcast order
    types = [(e.get("type"), (e.get("reason") if e.get("type") == "topology.changed" else None)) for e in events]
    done_at = [i for i, e in enumerate(events) if e.get("type") == "clearing.done"]
    growth_at = [
        i for i, e in enumerate(events) if e.get("type") == "topology.changed" and e.get("reason") == "trust_drift_growth"
    ]
    assert len(done_at) == 1 and len(growth_at) == 1, types
    growth = events[growth_at[0]]
    grown = _by_pair(growth["payload"]["edge_patch"], names)
    assert set(grown) == set(AFTER_CLEARING), grown
    for pair, debt in AFTER_CLEARING.items():  # the growth event carries the grown limit
        assert Decimal(grown[pair]["available"]) == Decimal("1050.00") - debt, (pair, grown[pair])

    # ── the subject: the order, and clearing.done as of the clearing (before growth) ─────────────────────────────
    assert done_at[0] < growth_at[0], f"clearing.done must precede the growth it causes; events in order: {types}"
    done = events[done_at[0]]
    patched = _by_pair(done["edge_patch"], names)
    assert set(patched) == set(AFTER_CLEARING), patched
    for pair, debt in AFTER_CLEARING.items():
        assert Decimal(patched[pair]["used"]) == debt, (pair, patched[pair])
        assert Decimal(patched[pair]["available"]) == Decimal("1000.00") - debt, (
            f"clearing.done must be as of the clearing (limit 1000, not grown): {pair} {patched[pair]}"
        )
