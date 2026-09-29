"""026 S2 fix-delta (§15 P1): an inject must see a trust lowering committed before its check.

Schedule (Codex, §15 on `32e5975`): the inject reads the line (limit 100, debt 80 from an earlier real inject)
-> the Interact-path update lowers it to 0 and COMMITS -> the inject adds 1. SSI accepts it as the serial order
"inject, then update" (the update reads nothing the inject writes). Target, as for payments (decision A, 024
`T2415.3`): the committed lowering is seen, the inject is refused or retried onto it - the debt stays 80.
Two PostgreSQL sessions; the order is forced by events at the inject's line read (after its snapshot is
taken, before the read), no sleeps - the barrier sits before the read so the stand cannot deadlock on the lock
the fix takes.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.trustlines.service import TrustLineService
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineUpdateRequest
from tests.integration.test_p015_f01512_inject_refuses_an_opposing_debt_postgres import (  # noqa: F401
    _baseline, _debts, _effect, _inject, _seed, factory)
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import _Artifacts, _run, _runner
from tests.p019_support import TargetMismatch, require_target
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


@pytest.mark.xfail(raises=TargetMismatch, strict=True,
                   reason="026 target, delivered by the T2602 fix-delta: the inject reads its line FOR SHARE")
@pytest.mark.asyncio
async def test_an_inject_does_not_grow_a_debt_past_a_lowering_committed_before_it(factory) -> None:
    world = await _seed(factory)
    a, b, eq = world.creditor, world.debtor, world.equivalents[0]
    await _baseline(factory, world)
    await _inject(factory, world, [_effect(world, creditor=a, debtor=b, amount="80")], "p026s2-first")
    line_read, lowered, statements, line_reads = asyncio.Event(), asyncio.Event(), [], []
    scenario = {"equivalents": [eq.code], "participants": [{"id": a.pid}, {"id": b.pid}], "trustlines": [],
                "behaviorProfiles": [], "events": [{"type": "inject", "time": 0,
                                                    "effects": [_effect(world, creditor=a, debtor=b, amount="1")]}]}
    run = _run(world, "p026s2-race")
    runner = _runner(run, scenario, _Artifacts())

    async def inject() -> None:
        async with factory() as session:
            original = session.execute

            async def execute(stmt, *args, **kwargs):
                if str(stmt).startswith('SELECT trust_lines."limit", trust_lines.status'):
                    line_reads.append(len(statements))
                    if not line_read.is_set():
                        line_read.set()  # the snapshot is taken (statements ran), the line is not read yet
                        await lowered.wait()
                statements.append(stmt)
                return await original(stmt, *args, **kwargs)

            session.execute = execute
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)

    task = asyncio.create_task(inject())
    await asyncio.wait_for(line_read.wait(), timeout=30)
    async with factory() as s:  # the Interact path's update: unsigned `execute_update`, finish, commit
        line = (await s.execute(select(TrustLine.id).where(
            TrustLine.from_participant_id == a.id, TrustLine.to_participant_id == b.id,
            TrustLine.status != "closed"))).scalar_one()
        service = TrustLineService(s)
        batch = service.begin_internal_batch()
        await service.execute_update(batch, line, a.id, TrustLineUpdateRequest(limit="0", signature="-"),
                                     require_signature=False)
        await batch.finish()
        await s.commit()
    lowered.set()
    await asyncio.wait_for(task, timeout=60)

    assert line_reads and line_reads[0] > 0, f"CONTROL: no snapshot before the line read: {line_reads}"
    debt = (await _debts(factory, world))[("B", "A")]
    require_target(debt == Decimal("80"), f"the inject grew B's debt 80 -> {debt} past a limit lowered to 0 "
                                          f"and committed before it (line reads: {len(line_reads)})")
