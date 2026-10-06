"""031 (BACKLOG item 14): a failed trust-drift COMMIT is not a rollback because the rollback after it succeeded.

WHAT IS WRONG (on `05d6e812`). Growth (`TrustDriftEngine.apply_trust_growth`) and decay
(`RealTick.apply_trust_decay_and_broadcast`) commit through `resolve_commit_under_cancellation`. When the COMMIT
fails without the server refusing it and the ROLLBACK after it succeeds, the outcome is resolved as a rollback.
If the COMMIT had landed and only its acknowledgement was lost, the new limits ARE persisted, but the in-memory
scenario is not updated (`apply_committed_effects`) and no `topology.changed` edge patch is published: the
simulator shows limits the database no longer has. Money is not involved - this is a report of limits.

THE TARGET. The unknown outcome is resolved by reading the persisted limits of the lines the drift wrote, on a new
session: equal to the intended new limits -> committed (effects applied, patch published once); otherwise ->
rolled back, as before. A COMMIT the server refused is not read (the money phase's `_commit_refused`).

THE BOUNDARY is the clearing stand's `ack_loss` (`test_clearing_commit_replay_postgres.py`, used by
`test_p030_s4_simulator_commit_outcome_postgres.py`): the real COMMIT lands in PostgreSQL and only its
acknowledgement is lost. Control: the connection is lost BEFORE the COMMIT is sent - nothing lands, and the
outcome must stay a rollback.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.core.simulator.tick as tick_module
import app.core.simulator.trust_drift_engine as drift_module
from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
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
from tests.simulator_tick_stand import install_tick_stand

DECAY_LINES = [("C", "D", "100.00", "active")]
DECAY_DEBTS = [("D", "C", "90.00")]  # ratio 0.9 > overload 0.8 -> 100.00 * 0.98 = 98.00


class _LostCommit:
    """The next armed COMMIT through `resolve_commit_under_cancellation` fails once: after it landed, or before
    it was sent. Patched in both modules the drift may commit through, so the stand does not depend on which one
    the code under test uses; only an ARMED commit is touched."""

    def __init__(self, monkeypatch, *, landed: bool) -> None:
        self.landed = landed
        self.armed = False
        self.seen: list[str] = []
        for module in (tick_module, drift_module):
            real = module.resolve_commit_under_cancellation
            monkeypatch.setattr(module, "resolve_commit_under_cancellation", self._wrap(real))

    def _wrap(self, real):
        async def resolve(*, commit, **kw):
            if not self.armed:
                return await real(commit=commit, **kw)
            self.armed = False

            async def failing_commit():
                if self.landed:
                    await commit()
                    self.seen.append("committed-then-lost")
                    raise RuntimeError("commit acknowledgement lost")
                self.seen.append("lost-before-commit")
                raise ConnectionError("connection lost before the commit was sent")

            return await real(commit=failing_commit, **kw)

        return resolve


def _decay_patches(sse, eq_code: str) -> list[dict]:
    return [
        e for e in sse.events
        if e.get("type") == "topology.changed" and e.get("reason") == "trust_drift_decay" and e.get("equivalent") == eq_code
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [True, False], ids=["commit_landed", "commit_not_sent"])
async def test_a_decay_whose_commit_acknowledgement_is_lost_is_resolved_by_the_persisted_limits(
    factory, monkeypatch, landed  # noqa: F811
) -> None:
    eq, people = await world(factory, ["C", "D"], DECAY_LINES, DECAY_DEBTS)
    scenario = scenario_for(eq, people, DECAY_LINES, DECAY)
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000)
    install_tick_stand(monkeypatch, factory)
    lost = _LostCommit(monkeypatch, landed=landed)

    real_decay = TrustDriftEngine.apply_trust_decay

    async def arming_decay(self, **kwargs):
        result = await real_decay(self, **kwargs)
        if result.updated_count:
            lost.armed = True  # the next commit is the decay's own
        return result

    monkeypatch.setattr(TrustDriftEngine, "apply_trust_decay", arming_decay)

    await ticks(runner, run, 1)

    # ── the mechanism, before the outcome: the decay's commit failed once, and the database says what landed ──
    assert lost.seen == (["committed-then-lost"] if landed else ["lost-before-commit"]), lost.seen
    after = await limits(factory, eq, people)
    expected = Decimal("98.00") if landed else Decimal("100.00")
    assert after[("C", "D")] == (expected, "active"), after

    # ── the outcome: what the simulator holds and publishes agrees with the database ──
    held = Decimal(str(scenario["trustlines"][0]["limit"]))
    patches = _decay_patches(runner._sse, eq.code)
    if landed:
        assert held == Decimal("98.00"), f"the scenario still holds {held} for a decay that landed"
        assert len(patches) == 1, f"{len(patches)} trust_drift_decay edge patches for a decay that landed"
        edges = patches[0]["payload"]["edge_patch"]
        pair = [e for e in edges if e.get("source") == people["C"].pid and e.get("target") == people["D"].pid]
        assert pair and Decimal(str(pair[0]["trust_limit"])) == Decimal("98.00"), edges
    else:
        assert held == Decimal("100.00"), held
        assert patches == [], patches


async def _growth_world(factory):  # noqa: F811
    n = uuid.uuid4().hex[:6].upper()
    async with factory() as s:
        eq = Equivalent(code=f"P31G{n}", precision=2, is_active=True, metadata_={})
        a, b = (
            Participant(pid=f"P31G_{r}_{n}", display_name=r, public_key=f"pk_p31_{r}_{n}", type="person",
                        status="active", profile={})
            for r in ("A", "B")
        )
        s.add_all([eq, a, b])
        await s.flush()
        line = TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=eq.id,
                         limit=Decimal("100.00"), status="active", policy={})
        s.add(line)
        await s.commit()
        return line.id, eq.code, (a.id, a.pid), (b.id, b.pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [True, False], ids=["commit_landed", "commit_not_sent"])
async def test_a_growth_whose_commit_acknowledgement_is_lost_is_resolved_by_the_persisted_limits(
    factory, monkeypatch, landed  # noqa: F811
) -> None:
    line_id, eq_code, (a_id, a_pid), (b_id, b_pid) = await _growth_world(factory)
    install_tick_stand(monkeypatch, factory)  # the fresh session the resolver reads on
    run = RunRecord(run_id=f"p031-g-{eq_code}", scenario_id="p031-g", mode="real", state="running")
    run._real_participants = [(a_id, a_pid), (b_id, b_pid)]
    run._trust_drift_config = TrustDriftConfig(enabled=True, growth_rate=0.05, max_growth=2.0)
    run._edge_clearing_history = {f"{a_pid}:{b_pid}:{eq_code}": EdgeClearingHistory(original_limit=Decimal("100"))}
    run._scenario_raw = {"trustlines": [{"from": a_pid, "to": b_pid, "equivalent": eq_code, "limit": "100.00"}]}
    engine = TrustDriftEngine(sse=None, utc_now=None, logger=logging.getLogger("tests.p031.growth"),
                              get_scenario_raw=lambda _s: run._scenario_raw)
    lost = _LostCommit(monkeypatch, landed=landed)
    lost.armed = True  # growth commits exactly once

    raised: BaseException | None = None
    async with factory() as session:
        try:
            result = await engine.apply_trust_growth(run, session, {(a_pid, b_pid)}, eq_code, 1)
        except Exception as exc:  # the caller (`tick._grow_trust_after_clearing`) logs and skips the edge patch
            raised = exc
            result = None

    assert lost.seen == (["committed-then-lost"] if landed else ["lost-before-commit"]), lost.seen
    async with factory() as s:
        stored = Decimal(str(await s.scalar(select(TrustLine.limit).where(TrustLine.id == line_id))))
    held = Decimal(str(run._scenario_raw["trustlines"][0]["limit"]))
    if landed:
        assert stored == Decimal("105.00"), stored
        assert raised is None and result is not None and result.updated_count == 1, (
            f"a growth that landed was reported as failed: {raised!r}"
        )
        assert held == Decimal("105.00"), f"the scenario still holds {held} for a growth that landed"
    else:
        assert stored == Decimal("100.00"), stored
        assert isinstance(raised, ConnectionError), raised
        assert held == Decimal("100.00"), held
