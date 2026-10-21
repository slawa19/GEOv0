"""BACKLOG 049-1 (2026-10-21): the run snapshot of a run its first tick has not seeded yet.

A run's participants are fresh rows under identities of that run (044 `T4405`), created by the run's FIRST TICK
(`tick.py`, `seed_scenario_into_db`). The Simulator UI asks for the run snapshot right after creating the run - measured
on a live stand 2026-10-21: run created at +286 ms, snapshot at +323 ms, before the first tick - and then only patches
it through SSE `node_patch`, which name the participants a payment touched. Until this fix the snapshot builder left
`net_sign` null for every participant without a row (`snapshot_builder.py`, `_load_state` returned None when none of
the run's pids had a row), so the first view showed "?" (unknown) on every participant no payment had reached yet:
75 of 100 on `greenfield-village-100-realistic-v2` after 32 s.

The signal is in the ledger: a debt row names both ends by `participants.id` (foreign keys), so a pid with no row has
no debt in any equivalent - its net is zero, which `visual-language.md` draws as "0 / ровно". A pid held by a row the
simulator did not create is still not read (024 fix-delta B1) and keeps null: nothing is known about it here.

WHAT THIS DOES NOT SEE: the scenario preview (`build_scenario_preview`, 044: no database read, null by design), a
browser, the SSE stream. The live counts are in the BACKLOG 049-1 record.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulator.models import RunRecord
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.core.simulator.snapshot_builder import SnapshotBuilder
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.debt_setup import debt_fixture_setup


def _utc_now() -> datetime:
    return datetime(2026, 10, 21, tzinfo=timezone.utc)


def _builder_for(pids: list[str]) -> tuple[SnapshotBuilder, str]:
    run = RunRecord(run_id=f"p049-{uuid.uuid4().hex[:8]}", scenario_id="scn-p049", mode="real", state="running")
    run._scenario_raw = {
        "participants": [{"id": pid, "name": pid, "type": "person"} for pid in pids],
        "trustlines": [],
    }
    builder = SnapshotBuilder(
        lock=threading.RLock(), runs={run.run_id: run}, scenarios={}, utc_now=_utc_now, db_enabled=lambda: True
    )
    return builder, run.run_id


async def _equivalent(session: AsyncSession, precision: int = 2) -> Equivalent:
    eq = Equivalent(code=("E" + uuid.uuid4().hex[:9]).upper(), precision=precision, is_active=True)
    session.add(eq)
    await session.commit()
    return eq


@pytest.mark.asyncio
async def test_a_run_before_its_first_tick_shows_every_participant_at_zero_not_unknown(db_session: AsyncSession) -> None:
    eq = await _equivalent(db_session, precision=2)
    nonce = uuid.uuid4().hex[:8]
    pids = [f"p049-{nonce}:p{i}" for i in range(1, 4)]
    builder, run_id = _builder_for(pids)

    snap = await builder.build_graph_snapshot(run_id=run_id, equivalent=eq.code, session=db_session)

    assert [n.id for n in snap.nodes] == pids, "the snapshot dropped a participant: the assertions below would be vacuous"
    for n in snap.nodes:
        got = (n.net_sign, n.net_balance, n.net_balance_atoms)
        assert got == (0, "0.00", "0"), f"{n.id}: a participant with no row has no debt; expected (0, '0.00', '0'), got {got}"
        assert n.viz_color_key is not None and not n.viz_color_key.startswith("debt-"), f"{n.id}: {n.viz_color_key!r}"


@pytest.mark.asyncio
async def test_the_zero_has_the_equivalents_precision(db_session: AsyncSession) -> None:
    eq = await _equivalent(db_session, precision=8)
    builder, run_id = _builder_for([f"p049-{uuid.uuid4().hex[:8]}:p1"])

    (node,) = (await builder.build_graph_snapshot(run_id=run_id, equivalent=eq.code, session=db_session)).nodes

    assert (node.net_sign, node.net_balance, node.net_balance_atoms) == (0, "0.00000000", "0")


@pytest.mark.asyncio
async def test_a_mixed_population_keeps_the_signs_it_has_and_the_unknown_it_does_not_read(db_session: AsyncSession) -> None:
    """Counter-checks of the rule: the stored participants keep their real signs, and a pid held by a row the
    simulator did not create is still not read (024) - it stays unknown, it is not made "even"."""

    eq = await _equivalent(db_session, precision=2)
    nonce = uuid.uuid4().hex[:8]
    cr, db, foreign, absent = (f"p049-{nonce}:{x}" for x in ("cr", "db", "real", "absent"))
    creditor = Participant(pid=cr, display_name="cr", public_key=simulated_public_key(cr), type="person", status="active", profile={})
    debtor = Participant(pid=db, display_name="db", public_key=simulated_public_key(db), type="person", status="active", profile={})
    real = Participant(pid=foreign, display_name="real", public_key="f" * 64, type="person", status="active", profile={})
    db_session.add_all([creditor, debtor, real])
    await db_session.commit()
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal("12.34")))
    await db_session.commit()

    builder, run_id = _builder_for([cr, db, foreign, absent])
    by_id = {n.id: n for n in (await builder.build_graph_snapshot(run_id=run_id, equivalent=eq.code, session=db_session)).nodes}

    assert (by_id[cr].net_sign, by_id[cr].net_balance) == (1, "12.34")
    assert (by_id[db].net_sign, by_id[db].net_balance) == (-1, "-12.34")
    assert (by_id[foreign].net_sign, by_id[foreign].net_balance, by_id[foreign].net_balance_atoms) == (None, None, None)
    assert (by_id[absent].net_sign, by_id[absent].net_balance) == (0, "0.00")


@pytest.mark.asyncio
async def test_an_equivalent_the_database_does_not_hold_stays_unknown(db_session: AsyncSession) -> None:
    """No equivalent row: no precision to write the zero in and no ledger to read - unknown, not even."""

    builder, run_id = _builder_for([f"p049-{uuid.uuid4().hex[:8]}:p1"])

    (node,) = (await builder.build_graph_snapshot(run_id=run_id, equivalent="NOSUCHEQ", session=db_session)).nodes

    assert (node.net_sign, node.net_balance, node.net_balance_atoms) == (None, None, None)
