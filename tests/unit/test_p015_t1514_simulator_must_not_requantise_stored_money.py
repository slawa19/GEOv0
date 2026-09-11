"""RT-015-13 / T1514: the simulator re-quantises money the core already stored.

WHAT SHARES THESE TABLES. `debts` and `trust_lines` are written by the production core AND by the
simulator. The core stores at the column's own scale - `Numeric(20, 8)` - and since 012/T1201 the
money door refuses anything the column cannot hold unchanged, so a ledger row legitimately carries
eight fraction digits.

THE DEFECT. `app/core/simulator/inject_executor.py` reads such a row back, adds its own amount, and
re-quantises the SUM to cents with `ROUND_DOWN` before writing it back. A debt of `5.12345678`
created by a real payment becomes `6.12` after an injected `1.00`: `0.00345678` destroyed by a
rounding nobody asked for, in a table the core owns, and the result is the input to the next
operation.

WHY A TEST AND NOT A ONE-LINE FIX. The existing coverage of this path,
`test_inject_debt_updates_existing_debt_row`, ends with
`Decimal(str(row.amount)).quantize(Decimal("0.01")) == Decimal("15.00")` - it normalises away
exactly the thing that is wrong before comparing. It passes with the truncation and would pass
without it. That is the shape programme 014 spent itself on, and it is why this file asserts the
stored value EXACTLY.

SCOPE. This module covers the debt path. Quantising the simulator's own INPUT amount is not the
defect and is not touched - the spec withdrew that claim explicitly.
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
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner, _nonce


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


async def _seed(db_session, *, existing_amount: Decimal, limit: Decimal):
    n = _nonce()
    eq = Equivalent(code=f"T{n}".upper()[:16], precision=2, is_active=True)
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


async def _stored_debt(db_session, eq, creditor, debtor) -> Decimal:
    row = (
        await db_session.execute(
            select(Debt).where(
                Debt.debtor_id == debtor.id,
                Debt.creditor_id == creditor.id,
                Debt.equivalent_id == eq.id,
            )
        )
    ).scalar_one()
    return Decimal(str(row.amount))


@pytest.mark.asyncio
async def test_injecting_into_a_core_created_debt_preserves_its_stored_precision(
    db_session,
) -> None:
    """The reproducer. RED before T1514.

    `5.12345678` is a value the core can and does store. Adding an injected `1.00` must leave
    `6.12345678`. Today it leaves `6.12`, and the assertion below states the exact figure rather
    than rounding both sides until they agree.
    """
    existing = Decimal("5.12345678")
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=existing, limit=Decimal("100.00")
    )

    runner, _artifacts = _make_runner(inject_enabled=True)
    await runner._apply_due_scenario_events(
        db_session,
        run_id="r-t1514",
        run=_make_run(
            participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid)],
            equivalents=[eq.code],
        ),
        scenario=_scenario(eq, creditor, debtor, inject_amount="1.00", limit="100.00"),
    )

    stored = await _stored_debt(db_session, eq, creditor, debtor)
    assert stored == Decimal("6.12345678"), (
        f"the simulator re-quantised a debt the core stored: {existing} + 1.00 became {stored}. "
        f"Storage is Numeric(20, 8) and the core writes eight fraction digits; rounding them off "
        f"here destroys money in a table the simulator does not own."
    )


@pytest.mark.asyncio
async def test_the_injected_amount_itself_is_still_normalised(db_session) -> None:
    """Control, and a boundary: the INPUT may be quantised, the STORED value may not.

    The spec withdrew the claim about input quantisation - the simulator is entitled to normalise
    the amount a scenario hands it. Without this control the fix could be "remove every quantize
    in the file", which changes what the simulator accepts rather than what it preserves.
    """
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=Decimal("1.00"), limit=Decimal("100.00")
    )

    runner, _artifacts = _make_runner(inject_enabled=True)
    await runner._apply_due_scenario_events(
        db_session,
        run_id="r-t1514-input",
        run=_make_run(
            participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid)],
            equivalents=[eq.code],
        ),
        # Three fraction digits in, and the injector's own input rule truncates to 2.
        scenario=_scenario(eq, creditor, debtor, inject_amount="2.009", limit="100.00"),
    )

    stored = await _stored_debt(db_session, eq, creditor, debtor)
    assert stored == Decimal("3.00"), (
        f"the injected amount should still be normalised to the simulator's own input scale "
        f"before it is added; stored {stored}"
    )


@pytest.mark.asyncio
async def test_the_trust_limit_still_bounds_the_result(db_session) -> None:
    """Control on the guard that sits beside the quantisation, so the fix cannot remove it.

    The injector refuses when the accumulated debt would exceed the trustline limit. That check
    reads the same value the quantisation used to truncate, so a careless edit could drop it.
    """
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=Decimal("99.50000000"), limit=Decimal("100.00")
    )

    runner, _artifacts = _make_runner(inject_enabled=True)
    await runner._apply_due_scenario_events(
        db_session,
        run_id="r-t1514-limit",
        run=_make_run(
            participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid)],
            equivalents=[eq.code],
        ),
        scenario=_scenario(eq, creditor, debtor, inject_amount="5.00", limit="100.00"),
    )

    stored = await _stored_debt(db_session, eq, creditor, debtor)
    assert stored == Decimal("99.50000000"), (
        f"the injection would have taken the debt past the trustline limit and must have been "
        f"refused; the row now holds {stored}"
    )


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
        db_session, existing_amount=Decimal("1.00000000"), limit=stored_limit
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
        cleared_amount_per_edge={},
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
        db_session, existing_amount=Decimal("1.00000000"), limit=stored_limit
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
        cleared_amount_per_edge={},
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
        db_session, existing_amount=Decimal("1.00000000"), limit=stored_limit
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
        cleared_amount_per_edge={},
    )
    engine.apply_committed_effects(scenario=run._scenario_raw, result=result)

    carried = run._scenario_raw["trustlines"][0]["limit"]
    assert not isinstance(carried, float), (
        f"the scenario carries the limit as {type(carried).__name__} ({carried!r}); money that "
        f"goes back to the database must not pass through binary floating point"
    )
    assert Decimal(str(carried)) == await _stored_limit(db_session, eq, creditor, debtor)
