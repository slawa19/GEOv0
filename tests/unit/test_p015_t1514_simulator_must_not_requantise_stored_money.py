"""RT-015-13 / T1514: the simulator re-quantises money the core already stored.

WHAT SHARES THESE TABLES. `debts` and `trust_lines` are written by the production core AND by the
simulator. The core stores at the column's own scale - `Numeric(20, 8)` - and since 012/T1201 the
money door refuses anything the column cannot hold unchanged, so a ledger row legitimately carries
eight fraction digits.

THE DEFECT. The simulator read such a row back and re-quantised it to cents with `ROUND_DOWN` before
writing it back: a debt of `5.12345678` became `6.12` after an injected `1.00`, a limit of
`100.12345678` became a cent-grained number on the first growth tick. `0.00345678` destroyed by a
rounding nobody asked for, in a table the core owns, and the result is the input to the next
operation.

SCOPE TODAY. This module covers the trust-limit half (growth, its ceiling, the scenario copy of the
limit). The debt half - the `inject_debt` effect and its three tests - left with the effect itself
(030 S3b): a simulator that no longer writes `debts` cannot re-quantise them. `_seed` and
`_scenario` stay because `test_p015_step5b_criterion_b.py` and `test_p015_t1544_*` still build their
worlds with them; `_scenario` builds a scenario carrying an `inject_debt` effect, which the
scenario schema now refuses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.trustline import TrustLine
from tests.unit.test_scenario_inject_topology import _nonce

from tests.debt_setup import debt_fixture_setup


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _drift_engine(run=None) -> TrustDriftEngine:
    """The engine with its four collaborators, matching the existing drift test's shape."""
    from tests.unit.test_simulator_sse_trust_drift_decay_topology_patch import (
        FakeSseBroadcast,
    )

    return TrustDriftEngine(
        sse=FakeSseBroadcast(),
        utc_now=_utc_now,
        logger=logging.getLogger("t1514"),
        get_scenario_raw=lambda _sid: (getattr(run, "_scenario_raw", None) or {}),
    )


async def _seed(db_session, *, existing_amount: Decimal, limit: Decimal, precision: int = 2):
    n = _nonce()
    eq = Equivalent(code=f"T{n}".upper()[:16], precision=precision, is_active=True)
    creditor = Participant(
        pid=f"TCRED_{n}", display_name="Creditor", public_key=f"pk_tcred_{n}"[:64],
        type="person", status="active",
    )
    debtor = Participant(
        pid=f"TDEBT_{n}", display_name="Debtor", public_key=f"pk_tdebt_{n}"[:64],
        type="person", status="active",
    )
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()

    db_session.add(
        TrustLine(
            from_participant_id=creditor.id,
            to_participant_id=debtor.id,
            equivalent_id=eq.id,
            limit=limit,
            status="active",
        )
    )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=existing_amount,
            )
        )
    await db_session.flush()
    return eq, creditor, debtor


def _scenario(eq, creditor, debtor, *, inject_amount: str, limit: str) -> dict[str, Any]:
    return {
        "participants": [{"id": creditor.pid}, {"id": debtor.pid}],
        "trustlines": [
            {
                "from": creditor.pid,
                "to": debtor.pid,
                "equivalent": eq.code,
                "limit": limit,
                "status": "active",
            }
        ],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {
                        "op": "inject_debt",
                        "from": creditor.pid,
                        "to": debtor.pid,
                        "equivalent": eq.code,
                        "amount": inject_amount,
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# The trust-limit half. Same defect, a different table, and it repeats every tick.
# ---------------------------------------------------------------------------


def _drift_run(creditor, debtor, eq, scenario_limit: str):
    """A RunRecord the growth path will accept, with drift enabled."""
    from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig

    run = RunRecord(
        run_id="run-t1514-drift",
        scenario_id="sc-t1514-drift",
        mode="real",
        state="running",
        started_at=_utc_now(),
    )
    run.sim_time_ms = 1000
    run.tick_index = 1
    run._real_seeded = True
    run._real_participants = [(creditor.id, creditor.pid), (debtor.id, debtor.pid)]
    run._real_equivalents = [eq.code]
    run._edges_by_equivalent = {}
    run._real_viz_by_eq = {}
    run._trust_drift_config = TrustDriftConfig(
        enabled=True, growth_rate=0.10, max_growth=2.0, min_limit_ratio=0.3
    )
    run._scenario_raw = {
        "participants": [{"id": creditor.pid}, {"id": debtor.pid}],
        "trustlines": [
            {
                "from": creditor.pid,
                "to": debtor.pid,
                "equivalent": eq.code,
                "limit": scenario_limit,
                "status": "active",
            }
        ],
    }
    # The key is a STRING, `"creditor:debtor:EQ"` - not a tuple. The first edition of this
    # helper used a tuple, the lookup missed, the loop skipped every edge and the reproducer
    # reported "the limit did not change" while never reaching the code under test. A stand that
    # cannot reach its subject is worse than no stand.
    run._edge_clearing_history = {
        f"{creditor.pid}:{debtor.pid}:{eq.code.upper()}": EdgeClearingHistory(
            original_limit=Decimal(scenario_limit)
        )
    }
    return run


async def _stored_limit(db_session, eq, creditor, debtor) -> Decimal:
    row = (
        await db_session.execute(
            select(TrustLine.limit).where(
                TrustLine.from_participant_id == creditor.id,
                TrustLine.to_participant_id == debtor.id,
                TrustLine.equivalent_id == eq.id,
                TrustLine.status == "active",
            )
        )
    ).scalar_one()
    return Decimal(str(row))


@pytest.mark.asyncio
async def test_trust_growth_preserves_the_stored_limit_precision(db_session) -> None:
    """The second reproducer. RED before T1514.

    The trustline holds `100.12345678` - a value the column keeps and the core can write. Growth
    at 10% should raise it. Instead the engine reads the limit back, TRUNCATES IT TO CENTS before
    multiplying, and writes a cent-grained result: the eight stored digits are gone on the first
    tick, and every later tick starts from the flattened number.

    The assertion is on the exact stored value, not on a rounded comparison.
    """
    stored_limit = Decimal("100.12345678")
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=Decimal("1.00000000"), limit=stored_limit, precision=8  # 028 В-4: an 8-digit limit is in the step only at precision 8
    )
    await db_session.commit()

    run = _drift_run(creditor, debtor, eq, str(stored_limit))
    engine = _drift_engine(run)

    await engine.apply_trust_growth(
        run=run,
        clearing_session=db_session,
        touched_edges={(creditor.pid, debtor.pid)},
        eq_code=eq.code,
        tick_index=1,
    )

    after = await _stored_limit(db_session, eq, creditor, debtor)
    # 100.12345678 * 1.1 = 110.135802458, storable at scale 8 as 110.13580245 (ROUND_DOWN).
    assert after == Decimal("110.13580245"), (
        f"trust growth flattened a stored limit to cents: {stored_limit} became {after}. The "
        f"column is Numeric(20, 8) and the production core writes eight digits into it; the "
        f"simulator truncating them on every tick walks the limit away from what was agreed."
    )


@pytest.mark.asyncio
async def test_trust_growth_still_respects_the_max_growth_ceiling(db_session) -> None:
    """Control. Removing the truncation must not remove the cap.

    `max_growth` bounds the limit at `original_limit * 2.0`; the quantisation sat in the same
    expression as that `min(...)`, so an edit that dropped one could drop the other.
    """
    stored_limit = Decimal("100.00000000")
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=Decimal("1.00000000"), limit=stored_limit, precision=8  # 028 В-4: an 8-digit limit is in the step only at precision 8
    )
    await db_session.commit()

    run = _drift_run(creditor, debtor, eq, str(stored_limit))
    engine = _drift_engine(run)
    run._trust_drift_config.growth_rate = 5.0  # would multiply by 6 in one tick
    run._trust_drift_config.max_growth = 2.0

    await engine.apply_trust_growth(
        run=run,
        clearing_session=db_session,
        touched_edges={(creditor.pid, debtor.pid)},
        eq_code=eq.code,
        tick_index=1,
    )

    after = await _stored_limit(db_session, eq, creditor, debtor)
    assert after == Decimal("200.00000000"), (
        f"the ceiling is original_limit * max_growth = 200; the limit is now {after}"
    )


@pytest.mark.asyncio
async def test_the_scenario_limit_is_not_laundered_through_float(db_session) -> None:
    """The site that is not one of the eleven, and without which the fix does not hold.

    After a committed growth the engine writes the new limit back into the in-memory scenario as
    `float(...)`. The decay path reads that entry on the next tick and turns it into a DB write, so
    a limit the column holds exactly is round-tripped through binary floating point between the two
    halves of the same feature.
    """
    stored_limit = Decimal("100.12345678")
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=Decimal("1.00000000"), limit=stored_limit, precision=8  # 028 В-4: an 8-digit limit is in the step only at precision 8
    )
    await db_session.commit()

    run = _drift_run(creditor, debtor, eq, str(stored_limit))
    engine = _drift_engine(run)

    result = await engine.apply_trust_growth(
        run=run,
        clearing_session=db_session,
        touched_edges={(creditor.pid, debtor.pid)},
        eq_code=eq.code,
        tick_index=1,
    )
    engine.apply_committed_effects(scenario=run._scenario_raw, result=result)

    carried = run._scenario_raw["trustlines"][0]["limit"]
    assert not isinstance(carried, float), (
        f"the scenario carries the limit as {type(carried).__name__} ({carried!r}); money that "
        f"goes back to the database must not pass through binary floating point"
    )
    assert Decimal(str(carried)) == await _stored_limit(db_session, eq, creditor, debtor)
