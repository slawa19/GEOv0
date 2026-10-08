"""034 S1a (§15 review of `62cce627`, finding G): a line closed by a payment of the REAL tick leaves the run.

Since 034 `F-034-2` the pairs a payment closed are read after the money commit, together with the visual patches
(`RealPaymentsExecutor.build_patches_after_commit`), by the owner of the money phase
(`money_replay._publish_committed`). The 026 S4 stand (`test_p026_s4_tick_close_publication_postgres.py`) calls
`build_post_commit_patches` itself, so it proves the buffer and not the production wiring. This test goes through
`RealRunnerImpl.tick_real_mode`: if the owner publishes without reading the patches, nothing here is removed.

THE STAND is 026 S4's (mode B, real PostgreSQL): B owes A 50 by a real payment, A asks to close A -> B by the signed
`DELETE` and the request stands. The tick then carries one payment, A pays B 50 - the only thing given to the tick
ready-made is that plan; the lock, the staged payment, the commit, the patch read and the publication are its own.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.simulator.real_payment_action import _RealPaymentAction
from tests.conftest import MODE_B
from tests.integration.test_p015_p1_money_replay_postgres import _runner
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts
from tests.integration.test_p026_s3_close_request_postgres import _line
from tests.integration.test_p026_s4_tick_close_publication_postgres import _removals, _requested, _run
from tests.simulator_tick_stand import RecordingSse, install_tick_stand


@MODE_B
@pytest.mark.asyncio
async def test_a_line_closed_by_a_payment_of_the_real_tick_is_removed_after_the_commit(client, db_session, monkeypatch) -> None:
    code, p, lines, factory = await _requested(client, db_session)
    a, b = p["A"]["pid"], p["B"]["pid"]
    run, sse = _run(code, p), RecordingSse()
    run.tick_index, run.sim_time_ms, run.intensity_percent = 1, 1000, 100
    run._real_equivalents = [code]
    runner = _runner(run, run._scenario_raw, sse)
    install_tick_stand(monkeypatch, factory)
    monkeypatch.setattr(runner, "_plan_real_payments", lambda *_a, **_kw: [_RealPaymentAction(0, code, a, b, "50")])

    await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

    # Controls: the tick's payment was carried and it is what closed the line.
    assert (run.last_error, run.committed_total, sse.published("tx.updated")) == (None, 1, 1), (run.last_error, sse.events)
    line = await _line(client, db_session, p["A"], lines["AB"])
    assert line["status"] == "closed" and await _debts(factory, code) == {}, line

    removals = _removals(sse.events)
    assert removals == [[{"from_pid": a, "to_pid": b, "equivalent_code": code, "limit": None}]] and (
        (a, b) not in run._edges_by_equivalent[code]
        and not any(t["from"] == a and t["to"] == b for t in run._scenario_raw["trustlines"])
    ), (
        f"the tick's committed payment closed {a} -> {b}, but the run kept it: events "
        f"{[e['type'] for e in sse.events]}, removals {removals}, cache {run._edges_by_equivalent[code]}"
    )
