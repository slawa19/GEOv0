"""R-021-4 (programme 021, stage 1, `T2102`): a failed decay is rolled back by the owner of its transaction.

WHAT IS WRONG (on `87c7200`, T2100 P2-4). `RealTickTrustDriftCoordinator.apply_trust_decay_and_broadcast`
catches any exception of the decay and returns WITHOUT a rollback (`real_tick_trust_drift_coordinator.py:61-78`).
Whatever the decay had already written stays in the tail's session, and the tail's own commit
(`real_tick_persistence.py:155`) makes it durable: a limit change nobody reported, with no audit row.

THE TARGET (spec, "Решения" item 7). The coordinator owns the decay's transaction and rolls it back before it
continues, so after the tick neither the first line's new limit nor any audit row of the failed decay exists -
and the tick itself goes on (the run is not failed by it).

TWO FAILURE POINTS, both non-database exceptions after an earlier mutation of the same transaction:
* the engine's per-line log record of the SECOND line (a logging filter raises): reachable on the code before
  and after stage 1, so the old behaviour is shown failing for its own reason;
* the trust-line service's after-mutation checkpoint (stage 1's own failure point). Before stage 1 the decay
  computes no checkpoint at all, which is part of what the target says.

THE CONTROL (green before and after, the boundary that must NOT move, "Решения" item 8): a failure in the
tail's persistence AFTER the decay committed does not undo the decay.

These are CALLER-LEVEL checks: the service tests that roll back themselves
(`tests/unit/test_trustline_audit_fail_closed.py`) do not show that the simulator does.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from app.core.simulator.real_tick_persistence import RealTickPersistence
from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    DECAY,
    factory,
    limits,
    run_for,
    runner_for,
    scenario_for,
    ticks,
    world,
)
from tests.p021_support import TrustLineCheckpoints, require_target, target_xfail_021, trust_line_audit_rows
from tests.simulator_tick_stand import install_tick_stand

LINES = [("C", "D", "100.00", "active"), ("C", "G", "100.00", "active")]
DEBTS = [("D", "C", "90.00"), ("G", "C", "90.00")]


class _FailOnSecondDecayRecord(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.seen = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage().startswith("simulator.real.trust_drift.decay key="):
            self.seen += 1
            if self.seen == 2:
                raise RuntimeError("p021: failure while the decay handles its second line")
        return True


async def _audit_count(factory) -> int:  # noqa: F811
    async with factory() as s:
        return len(await trust_line_audit_rows(s))


@target_xfail_021("T2102", "the decay handler returns without a rollback; the tail's commit keeps the mutation")
@pytest.mark.asyncio
async def test_a_decay_failing_after_its_first_line_leaves_nothing_behind(factory, monkeypatch) -> None:  # noqa: F811
    eq, people = await world(factory, ["C", "D", "G"], LINES, DEBTS)
    scenario = scenario_for(eq, people, LINES, DECAY)
    run = run_for(list(people.values()), eq.code)
    logger = logging.getLogger(f"tests.p021.decay_rollback.{run.run_id}")
    logger.setLevel(logging.INFO)
    failing = _FailOnSecondDecayRecord()
    logger.addFilter(failing)
    runner = runner_for(run, scenario, clearing_every=10_000, logger=logger)
    install_tick_stand(monkeypatch, factory)

    await ticks(runner, run, 1)

    # ── controls: the failure fired on the second line, the tick went on ─────────────────────
    assert failing.seen == 2, f"premise: the failure point was not reached ({failing.seen} decay records)"
    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)

    after = await limits(factory, eq, people)
    audit = await _audit_count(factory)
    require_target(
        after[("C", "D")] == (Decimal("100.00"), "active")
        and after[("C", "G")] == (Decimal("100.00"), "active")
        and audit == 0,
        f"after the failed decay: limits {after}, {audit} trust-line audit rows persisted",
    )


@target_xfail_021("T2102", "the decay computes no checkpoint, and its handler never rolls back")
@pytest.mark.asyncio
async def test_a_decay_whose_checkpoint_fails_leaves_nothing_behind(factory, monkeypatch) -> None:  # noqa: F811
    eq, people = await world(factory, ["C", "D", "G"], LINES, DEBTS)
    scenario = scenario_for(eq, people, LINES, DECAY)
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000)
    install_tick_stand(monkeypatch, factory)
    checkpoints = TrustLineCheckpoints(monkeypatch)
    # One equivalent: call 1 is the before-checkpoint, call 2 the after-mutation one.
    checkpoints.fail_on_call = 2

    await ticks(runner, run, 1)

    # ── control: the tick went on ────────────────────────────────────────────────────────────
    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)

    after = await limits(factory, eq, people)
    audit = await _audit_count(factory)
    require_target(
        checkpoints.count == 2
        and after[("C", "D")] == (Decimal("100.00"), "active")
        and after[("C", "G")] == (Decimal("100.00"), "active")
        and audit == 0,
        f"checkpoint failure point reached: {checkpoints.count >= 2} ({checkpoints.count} computations); "
        f"limits {after}; {audit} trust-line audit rows persisted",
    )


@pytest.mark.asyncio
async def test_control_a_failure_after_the_decay_commit_does_not_undo_the_decay(factory, monkeypatch) -> None:  # noqa: F811
    """The real durability boundary: the decay commits on its own before the tail persists metrics."""

    eq, people = await world(factory, ["C", "D", "G"], LINES, DEBTS)
    scenario = scenario_for(eq, people, LINES, DECAY)
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000)
    install_tick_stand(monkeypatch, factory)
    calls: list[int] = []

    async def failing_tail(self, **kwargs):
        calls.append(1)
        raise RuntimeError("p021 control: the tail's persistence fails after the decay committed")

    monkeypatch.setattr(RealTickPersistence, "persist_tick_tail", failing_tail)

    await ticks(runner, run, 1)

    assert calls == [1], "premise: the tail's persistence was reached"
    after = await limits(factory, eq, people)
    assert after[("C", "D")] == (Decimal("98.00"), "active"), after
    assert after[("C", "G")] == (Decimal("98.00"), "active"), after
