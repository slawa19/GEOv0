import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup


@pytest.mark.asyncio
async def test_edge_patch_builder_equivalent_and_pairs_shapes(db_session: AsyncSession) -> None:
    nonce = uuid.uuid4().hex[:10]

    eq = Equivalent(code=("T" + nonce).upper()[:16], precision=2, is_active=True)
    creditor = Participant(
        pid=f"A{nonce}",
        display_name="A",
        public_key=f"pkA-{nonce}",
        type="person",
        status="active",
        profile={},
    )
    debtor = Participant(
        pid=f"B{nonce}",
        display_name="B",
        public_key=f"pkB-{nonce}",
        type="person",
        status="active",
        profile={},
    )

    db_session.add_all([eq, creditor, debtor])
    await db_session.commit()

    tl = TrustLine(
        from_participant_id=creditor.id,
        to_participant_id=debtor.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
        policy={"auto_clearing": True},
    )
    async with debt_fixture_setup(db_session, label="setup"):
        debt = Debt(
            debtor_id=debtor.id,
            creditor_id=creditor.id,
            equivalent_id=eq.id,
            amount=Decimal("30"),
        )

        db_session.add_all([tl, debt])
    await db_session.commit()

    run = RunRecord(
        run_id=f"run-{nonce}",
        scenario_id=f"scn-{nonce}",
        mode="real",
        state="running",
    )
    run._real_participants = [(creditor.id, creditor.pid), (debtor.id, debtor.pid)]

    builder = EdgePatchBuilder(logger=logging.getLogger("test.edge_patch_builder"))

    # --- DB-authoritative equivalent patch ---
    patches = await builder.build_edge_patch_for_equivalent(
        session=db_session,
        run=run,
        equivalent_code=eq.code,
    )
    assert len(patches) == 1
    p = patches[0]
    assert set(p.keys()) >= {
        "source",
        "target",
        "trust_limit",
        "used",
        "available",
        "viz_alpha_key",
        "viz_width_key",
    }
    assert p["source"] == creditor.pid
    assert p["target"] == debtor.pid
    assert p["trust_limit"] == "100.00"
    assert p["used"] == "30.00"
    assert p["available"] == "70.00"

    patches_no_width = await builder.build_edge_patch_for_equivalent(
        session=db_session,
        run=run,
        equivalent_code=eq.code,
        include_width_keys=False,
    )
    assert len(patches_no_width) == 1
    assert "viz_width_key" not in patches_no_width[0]

    patches_filtered = await builder.build_edge_patch_for_equivalent(
        session=db_session,
        run=run,
        equivalent_code=eq.code,
        only_edges={(creditor.pid, debtor.pid)},
    )
    assert len(patches_filtered) == 1

    patches_filtered_empty = await builder.build_edge_patch_for_equivalent(
        session=db_session,
        run=run,
        equivalent_code=eq.code,
        only_edges={(debtor.pid, creditor.pid)},
    )
    assert patches_filtered_empty == []

    # --- Incremental patches (tx.updated / clearing.done) ---
    helper = await VizPatchHelper.create(db_session, equivalent_code=eq.code, refresh_every_ticks=1)
    await helper.maybe_refresh_quantiles(
        db_session,
        tick_index=0,
        participant_ids=[creditor.id, debtor.id],
    )

    edge_patches = await builder.build_edge_patch_for_pairs(
        session=db_session,
        helper=helper,
        edges_pairs=[(creditor.pid, debtor.pid)],
        pid_to_participant={creditor.pid: creditor, debtor.pid: debtor},
    )
    assert len(edge_patches) == 1
    ep = edge_patches[0]

    # Shape must match what frontend normalizers expect. 026 `T2603.2` fix-delta P3: `close_requested_at` is always
    # carried (null here: no request), so a re-created pair clears an earlier incarnation's request in the browser.
    assert set(ep.keys()) == {
        "source",
        "target",
        "used",
        "available",
        "close_requested_at",
        "viz_alpha_key",
        "viz_width_key",
    }
    assert ep["close_requested_at"] is None
    assert ep["source"] == creditor.pid
    assert ep["target"] == debtor.pid
    assert Decimal(str(ep["used"])) == Decimal("30")
    assert Decimal(str(ep["available"])) == Decimal("70")


@pytest.mark.parametrize("include_closed", [False, True], ids=["default-payment", "post-commit-clearing"])
@pytest.mark.parametrize("state", ["absent", "requested", "closed-zero", "closed-nonzero", "recreated"])
@pytest.mark.asyncio
async def test_clearing_closed_zero_opt_in_preserves_db_truth_and_live_incarnations(db_session, state, include_closed):
    """053 anti-vacuum: opt-in is not missing=zero, nor closed=zero.

    closed-nonzero is deliberately contradictory input to the builder's guard,
    not a claim that the application's money writer can create it.
    """
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("F" + nonce).upper(), precision=2, is_active=True)
    people = [Participant(pid=f"{role}{nonce}", display_name=role, public_key=f"pk{role}{nonce}",
        type="person", status="active", profile={}) for role in ("a", "b")]
    db_session.add_all([eq, *people])
    await db_session.flush()
    creditor, debtor = people
    asked = datetime.now(timezone.utc)
    if state != "absent":
        db_session.add(TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id,
            equivalent_id=eq.id, limit=Decimal("0"), status="active" if state == "requested" else "closed",
            close_requested_at=asked))
    if state == "recreated":
        db_session.add(TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id,
            equivalent_id=eq.id, limit=Decimal("5"), status="active"))
    amount = Decimal("2") if state == "recreated" else Decimal("7")
    if state in {"requested", "closed-nonzero", "recreated"}:
        async with debt_fixture_setup(db_session, label="053 builder truth control"):
            db_session.add(Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=amount))
    await db_session.commit()
    helper = await VizPatchHelper.create(db_session, equivalent_code=eq.code, refresh_every_ticks=1)
    pair = (creditor.pid, debtor.pid)
    closed = set()
    patches = await EdgePatchBuilder(logger=logging.getLogger(__name__)).build_edge_patch_for_pairs(
        session=db_session, helper=helper, edges_pairs=[pair], pid_to_participant={p.pid: p for p in people},
        closed=closed, include_closed_zero_debt=include_closed)
    assert closed == ({pair} if state.startswith("closed") else set())
    if state == "absent" or state == "closed-nonzero" or (state == "closed-zero" and not include_closed):
        assert patches == []
    else:
        [patch] = patches
        expected = {"requested": ("7.00", "-7.00"), "closed-zero": ("0.00", "0.00"), "recreated": ("2.00", "3.00")}[state]
        assert (patch["used"], patch["available"]) == expected
        assert patch["close_requested_at"] == (None if state == "recreated" else asked.isoformat())
