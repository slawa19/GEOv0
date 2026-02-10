"""Integration tests for network growth inject operations.

Tests validate inject ops (add_participant, create_trustline, freeze_participant)
in a multi-tick, multi-event pipeline context with a real SQLite DB.

Approach: "thick unit test" — real SQLite via ``db_session`` fixture from conftest,
direct ``RealRunner._apply_due_scenario_events()`` calls with advancing sim_time
to simulate multi-tick processing.  More integration-like than pure unit tests
because each test exercises a complete scenario with multiple events at different
times, verifying cumulative DB + cache state across ticks.

Env-variable: ``SIMULATOR_REAL_ENABLE_INJECT=1`` is set via ``runner._real_enable_inject``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


# ---------------------------------------------------------------------------
# Helpers (shared across all tests in this module)
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _nonce() -> str:
    return uuid.uuid4().hex[:8]


class _DummyArtifacts:
    """Minimal artifacts stub that records enqueued payloads."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def enqueue_event_artifact(self, run_id: str, payload: dict) -> None:
        self.payloads.append(payload)

    def write_real_tick_artifact(self, run: RunRecord, payload: dict) -> None:
        return None


class _DummySse:
    """Minimal SSE stub."""

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"evt_{run.run_id}_{run._event_seq:06d}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        pass


def _make_runner(*, inject_enabled: bool = True) -> tuple[RealRunner, _DummyArtifacts]:
    """Create a ``RealRunner`` with inject flag pre-set."""
    arts = _DummyArtifacts()
    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: None,  # type: ignore[arg-type]
        get_scenario_raw=lambda _sid: {},
        sse=_DummySse(),
        artifacts=arts,
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: False,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger("test.integration.network_growth"),
    )
    runner._real_enable_inject = inject_enabled
    return runner, arts


def _make_run(
    *,
    participants: list[tuple[uuid.UUID, str]] | None = None,
    equivalents: list[str] | None = None,
    edges_by_equivalent: dict[str, list[tuple[str, str]]] | None = None,
    sim_time_ms: int = 0,
) -> RunRecord:
    """Create a ``RunRecord`` pre-configured for inject integration tests."""
    run = RunRecord(
        run_id="run-integ-growth",
        scenario_id="sc-integ-growth",
        mode="real",
        state="running",
        started_at=_utc_now(),
    )
    run.sim_time_ms = sim_time_ms
    run.tick_index = 1
    run._real_seeded = True
    run._real_participants = participants if participants is not None else []
    run._real_equivalents = equivalents if equivalents is not None else []
    run._edges_by_equivalent = edges_by_equivalent if edges_by_equivalent is not None else {}
    run._real_viz_by_eq = {}
    return run


# ===================================================================
# Test 1: add_participant multi-tick — appears in DB + snapshot
# ===================================================================


@pytest.mark.asyncio
async def test_inject_add_participant_appears_in_snapshot(db_session) -> None:
    """Multi-tick simulation: add_participant injects fire at correct times.

    Scenario timeline:
      - t=500:  add_participant (wave-2 household) with initial trustlines
      - t=1500: add_participant (wave-3 service) with initial trustlines

    Tick progression:
      1. sim_time=300  → no events fire (both are in the future)
      2. sim_time=700  → first add_participant fires, second still future
      3. sim_time=2000 → second add_participant fires

    Verifications after each tick:
      - DB: new participant row, correct status, type
      - DB: initial trustlines created
      - run._real_participants cache updated
    """
    n = _nonce()
    eq_code = f"IG{n}".upper()[:16]

    # Seed: equivalent + 2 sponsor participants.
    eq = Equivalent(code=eq_code, precision=2, is_active=True)
    sponsor_a = Participant(
        pid=f"SPA_{n}",
        display_name="Sponsor A",
        public_key=f"pk_spa_{n}"[:64],
        type="person",
        status="active",
    )
    sponsor_b = Participant(
        pid=f"SPB_{n}",
        display_name="Sponsor B",
        public_key=f"pk_spb_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, sponsor_a, sponsor_b])
    await db_session.flush()

    new_pid_1 = f"W51_{n}"
    new_pid_2 = f"W52_{n}"

    scenario: dict[str, Any] = {
        "participants": [
            {"id": sponsor_a.pid, "name": "Sponsor A"},
            {"id": sponsor_b.pid, "name": "Sponsor B"},
        ],
        "trustlines": [],
        "equivalents": [eq_code],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "add_participant",
                        "participant": {
                            "id": new_pid_1,
                            "name": "Wave-2 Household",
                            "type": "person",
                            "groupId": "households",
                            "behaviorProfileId": "bp1",
                        },
                        "initial_trustlines": [
                            {
                                "sponsor": sponsor_a.pid,
                                "equivalent": eq_code,
                                "limit": "500",
                                "direction": "sponsor_credits_new",
                            },
                        ],
                    },
                ],
            },
            {
                "type": "inject",
                "time": 1500,
                "effects": [
                    {
                        "op": "add_participant",
                        "participant": {
                            "id": new_pid_2,
                            "name": "Wave-3 Service",
                            "type": "person",
                            "groupId": "services",
                            "behaviorProfileId": "bp2",
                        },
                        "initial_trustlines": [
                            {
                                "sponsor": sponsor_b.pid,
                                "equivalent": eq_code,
                                "limit": "300",
                                "direction": "sponsor_credits_new",
                            },
                        ],
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner(inject_enabled=True)
    run = _make_run(
        participants=[
            (sponsor_a.id, sponsor_a.pid),
            (sponsor_b.id, sponsor_b.pid),
        ],
        equivalents=[eq_code],
        edges_by_equivalent={eq_code: []},
        sim_time_ms=0,
    )

    # ---- Tick 1: sim_time=300 — no events should fire -----------------
    run.sim_time_ms = 300
    run.tick_index = 1
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # No new participants yet.
    p1_check = (
        await db_session.execute(
            select(Participant).where(Participant.pid == new_pid_1)
        )
    ).scalar_one_or_none()
    assert p1_check is None, "Participant should NOT exist before event time"

    assert len(run._real_participants) == 2, "Only original sponsors"
    assert len(run._real_fired_scenario_event_indexes) == 0

    # ---- Tick 2: sim_time=700 — first add_participant fires -----------
    run.sim_time_ms = 700
    run.tick_index = 2
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # Verify: first participant in DB.
    p1 = (
        await db_session.execute(
            select(Participant).where(Participant.pid == new_pid_1)
        )
    ).scalar_one_or_none()
    assert p1 is not None, "First participant should be created at t=500"
    assert p1.status == "active"
    assert p1.type == "person"

    # Verify: initial trustline created (sponsor_a → new_pid_1).
    tl1 = (
        await db_session.execute(
            select(TrustLine).where(
                TrustLine.from_participant_id == sponsor_a.id,
                TrustLine.to_participant_id == p1.id,
                TrustLine.equivalent_id == eq.id,
            )
        )
    ).scalar_one_or_none()
    assert tl1 is not None, "Initial trustline should be created"
    assert tl1.limit == Decimal("500")
    assert tl1.status == "active"

    # Verify: run._real_participants updated with new participant.
    pids_in_cache = [pid for (_uid, pid) in run._real_participants]
    assert new_pid_1 in pids_in_cache

    # Second participant not yet created.
    p2_check = (
        await db_session.execute(
            select(Participant).where(Participant.pid == new_pid_2)
        )
    ).scalar_one_or_none()
    assert p2_check is None, "Second participant should NOT exist yet (t=1500)"

    assert 0 in run._real_fired_scenario_event_indexes
    assert 1 not in run._real_fired_scenario_event_indexes

    # ---- Tick 3: sim_time=2000 — second add_participant fires ---------
    run.sim_time_ms = 2000
    run.tick_index = 3
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # Verify: second participant in DB.
    p2 = (
        await db_session.execute(
            select(Participant).where(Participant.pid == new_pid_2)
        )
    ).scalar_one_or_none()
    assert p2 is not None, "Second participant should be created at t=1500"
    assert p2.status == "active"

    # Verify: initial trustline for second participant.
    tl2 = (
        await db_session.execute(
            select(TrustLine).where(
                TrustLine.from_participant_id == sponsor_b.id,
                TrustLine.to_participant_id == p2.id,
                TrustLine.equivalent_id == eq.id,
            )
        )
    ).scalar_one_or_none()
    assert tl2 is not None, "Second participant's trustline should be created"
    assert tl2.limit == Decimal("300")

    # Verify: run cache has all 4 participants (2 sponsors + 2 new).
    pids_final = [pid for (_uid, pid) in run._real_participants]
    assert new_pid_1 in pids_final
    assert new_pid_2 in pids_final
    assert len(pids_final) == 4

    # Both events should be marked as fired.
    assert 0 in run._real_fired_scenario_event_indexes
    assert 1 in run._real_fired_scenario_event_indexes

    # Verify: scenario["participants"] was updated in-place with new entries.
    scenario_pids = [p["id"] for p in scenario["participants"]]
    assert new_pid_1 in scenario_pids
    assert new_pid_2 in scenario_pids

    # Verify: edges_by_equivalent has entries for the new trustlines.
    edges = run._edges_by_equivalent.get(eq_code, [])
    assert len(edges) >= 2, f"Expected ≥2 edges from initial trustlines, got {edges}"


# ===================================================================
# Test 2: create_trustline — visible in routing + cache invalidation
# ===================================================================


@pytest.mark.asyncio
async def test_inject_create_trustline_visible_in_routing(db_session) -> None:
    """Multi-tick simulation: create_trustline injects create edges and invalidate caches.

    Scenario timeline:
      - t=500: create_trustline (A→B)
      - t=1000: create_trustline (B→C)

    Verifications:
      - DB: trustline rows with correct limit/status
      - PaymentRouter._graph_cache evicted for the equivalent
      - run._edges_by_equivalent updated with new edges
      - run._real_viz_by_eq evicted
      - Idempotent: re-running same tick doesn't create duplicates
    """
    n = _nonce()
    eq_code = f"RT{n}".upper()[:16]

    # Seed: equivalent + 3 participants, no trustlines initially.
    eq = Equivalent(code=eq_code, precision=2, is_active=True)
    p_a = Participant(
        pid=f"RA_{n}",
        display_name="Alice",
        public_key=f"pk_ra_{n}"[:64],
        type="person",
        status="active",
    )
    p_b = Participant(
        pid=f"RB_{n}",
        display_name="Bob",
        public_key=f"pk_rb_{n}"[:64],
        type="person",
        status="active",
    )
    p_c = Participant(
        pid=f"RC_{n}",
        display_name="Carol",
        public_key=f"pk_rc_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, p_a, p_b, p_c])
    await db_session.flush()

    scenario: dict[str, Any] = {
        "participants": [
            {"id": p_a.pid},
            {"id": p_b.pid},
            {"id": p_c.pid},
        ],
        "trustlines": [],
        "equivalents": [eq_code],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "create_trustline",
                        "from": p_a.pid,
                        "to": p_b.pid,
                        "equivalent": eq_code,
                        "limit": "1000.50",
                    },
                ],
            },
            {
                "type": "inject",
                "time": 1000,
                "effects": [
                    {
                        "op": "create_trustline",
                        "from": p_b.pid,
                        "to": p_c.pid,
                        "equivalent": eq_code,
                        "limit": "750",
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner(inject_enabled=True)
    run = _make_run(
        participants=[
            (p_a.id, p_a.pid),
            (p_b.id, p_b.pid),
            (p_c.id, p_c.pid),
        ],
        equivalents=[eq_code],
        edges_by_equivalent={eq_code: []},
        sim_time_ms=0,
    )
    run._real_viz_by_eq[eq_code] = "old_viz"

    # Pre-populate graph cache to verify invalidation.
    original_cache = PaymentRouter._graph_cache.copy()
    PaymentRouter._graph_cache[eq_code] = (0.0, {}, {}, {}, {}, {})  # type: ignore[assignment]

    try:
        # ---- Tick 1: sim_time=600 — first trustline fires ----------------
        run.sim_time_ms = 600
        run.tick_index = 1
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # Verify: trustline A→B in DB.
        tl_ab = (
            await db_session.execute(
                select(TrustLine).where(
                    TrustLine.from_participant_id == p_a.id,
                    TrustLine.to_participant_id == p_b.id,
                    TrustLine.equivalent_id == eq.id,
                )
            )
        ).scalar_one_or_none()
        assert tl_ab is not None, "Trustline A→B should be created"
        assert tl_ab.status == "active"
        assert tl_ab.limit == Decimal("1000.50")

        # Verify: graph cache evicted.
        assert eq_code not in PaymentRouter._graph_cache, (
            "PaymentRouter graph cache should be invalidated after create_trustline"
        )

        # Verify: viz cache evicted.
        assert eq_code not in run._real_viz_by_eq

        # Verify: edges updated.
        edges = run._edges_by_equivalent.get(eq_code, [])
        assert (p_a.pid, p_b.pid) in edges

        # Verify: scenario["trustlines"] updated.
        assert len(scenario["trustlines"]) == 1
        assert scenario["trustlines"][0]["from"] == p_a.pid

        # B→C not yet created.
        tl_bc = (
            await db_session.execute(
                select(TrustLine).where(
                    TrustLine.from_participant_id == p_b.id,
                    TrustLine.to_participant_id == p_c.id,
                    TrustLine.equivalent_id == eq.id,
                )
            )
        ).scalar_one_or_none()
        assert tl_bc is None, "B→C trustline should NOT exist yet"

        # ---- Re-populate graph cache for second invalidation check --------
        PaymentRouter._graph_cache[eq_code] = (0.0, {}, {}, {}, {}, {})  # type: ignore[assignment]
        run._real_viz_by_eq[eq_code] = "refreshed_viz"

        # ---- Tick 2: sim_time=1200 — second trustline fires ---------------
        run.sim_time_ms = 1200
        run.tick_index = 2
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # Verify: trustline B→C in DB.
        tl_bc = (
            await db_session.execute(
                select(TrustLine).where(
                    TrustLine.from_participant_id == p_b.id,
                    TrustLine.to_participant_id == p_c.id,
                    TrustLine.equivalent_id == eq.id,
                )
            )
        ).scalar_one_or_none()
        assert tl_bc is not None, "Trustline B→C should be created at t=1000"
        assert tl_bc.limit == Decimal("750")

        # Verify: second cache invalidation.
        assert eq_code not in PaymentRouter._graph_cache
        assert eq_code not in run._real_viz_by_eq

        # Verify: both edges present.
        edges = run._edges_by_equivalent.get(eq_code, [])
        assert (p_a.pid, p_b.pid) in edges
        assert (p_b.pid, p_c.pid) in edges

        # Both events fired.
        assert 0 in run._real_fired_scenario_event_indexes
        assert 1 in run._real_fired_scenario_event_indexes

        # ---- Tick 3: sim_time=1500, re-run — idempotent (no duplicates) ---
        run.sim_time_ms = 1500
        run.tick_index = 3
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # Count trustlines — still exactly 2.
        tl_count = (
            await db_session.execute(
                select(func.count()).select_from(TrustLine).where(
                    TrustLine.equivalent_id == eq.id,
                )
            )
        ).scalar_one()
        assert tl_count == 2, f"Expected 2 trustlines (no duplicates), got {tl_count}"

    finally:
        # Restore original graph cache to avoid leaking state.
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(original_cache)


# ===================================================================
# Test 3: freeze_participant — removes from active routing
# ===================================================================


@pytest.mark.asyncio
async def test_inject_freeze_removes_from_active_routing(db_session) -> None:
    """Multi-tick simulation: freeze_participant suspends participant and freezes trustlines.

    Scenario timeline:
      - t=500: create_trustline (B→C) — adds a new edge
      - t=1000: freeze_participant (target=A, freeze_trustlines=true)

    Initial topology: A↔B, A↔C (4 trustlines).
    After t=500: + B→C edge.
    After t=1000: A suspended, all A-incident trustlines frozen, edges pruned.

    Verifications:
      - DB: participant.status = 'suspended'
      - DB: incident trustlines status = 'frozen'
      - run._edges_by_equivalent: no edges involving frozen PID
      - scenario dicts updated in-place
      - Non-incident trustlines (B→C) remain active
    """
    n = _nonce()
    eq_code = f"FZ{n}".upper()[:16]

    # Seed: equivalent + 3 participants with trustlines forming a triangle.
    eq = Equivalent(code=eq_code, precision=2, is_active=True)
    target = Participant(
        pid=f"FA_{n}",
        display_name="Alice (to freeze)",
        public_key=f"pk_fa_{n}"[:64],
        type="person",
        status="active",
    )
    p_b = Participant(
        pid=f"FB_{n}",
        display_name="Bob",
        public_key=f"pk_fb_{n}"[:64],
        type="person",
        status="active",
    )
    p_c = Participant(
        pid=f"FC_{n}",
        display_name="Carol",
        public_key=f"pk_fc_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, target, p_b, p_c])
    await db_session.flush()

    # Pre-create trustlines: A↔B, A↔C (4 directional edges).
    tl_ab = TrustLine(
        from_participant_id=target.id,
        to_participant_id=p_b.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
    )
    tl_ba = TrustLine(
        from_participant_id=p_b.id,
        to_participant_id=target.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
    )
    tl_ac = TrustLine(
        from_participant_id=target.id,
        to_participant_id=p_c.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
    )
    tl_ca = TrustLine(
        from_participant_id=p_c.id,
        to_participant_id=target.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
    )
    db_session.add_all([tl_ab, tl_ba, tl_ac, tl_ca])
    await db_session.commit()

    scenario: dict[str, Any] = {
        "participants": [
            {"id": target.pid, "status": "active"},
            {"id": p_b.pid, "status": "active"},
            {"id": p_c.pid, "status": "active"},
        ],
        "trustlines": [
            {"from": target.pid, "to": p_b.pid, "equivalent": eq_code, "status": "active"},
            {"from": p_b.pid, "to": target.pid, "equivalent": eq_code, "status": "active"},
            {"from": target.pid, "to": p_c.pid, "equivalent": eq_code, "status": "active"},
            {"from": p_c.pid, "to": target.pid, "equivalent": eq_code, "status": "active"},
        ],
        "equivalents": [eq_code],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "create_trustline",
                        "from": p_b.pid,
                        "to": p_c.pid,
                        "equivalent": eq_code,
                        "limit": "200",
                    },
                ],
            },
            {
                "type": "inject",
                "time": 1000,
                "effects": [
                    {
                        "op": "freeze_participant",
                        "participant_id": target.pid,
                        "freeze_trustlines": True,
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner(inject_enabled=True)

    initial_edges = [
        (target.pid, p_b.pid),
        (p_b.pid, target.pid),
        (target.pid, p_c.pid),
        (p_c.pid, target.pid),
    ]
    run = _make_run(
        participants=[
            (target.id, target.pid),
            (p_b.id, p_b.pid),
            (p_c.id, p_c.pid),
        ],
        equivalents=[eq_code],
        edges_by_equivalent={eq_code: list(initial_edges)},
        sim_time_ms=0,
    )

    original_cache = PaymentRouter._graph_cache.copy()
    PaymentRouter._graph_cache[eq_code] = (0.0, {}, {}, {}, {}, {})  # type: ignore[assignment]

    try:
        # ---- Tick 1: sim_time=600 — create_trustline B→C fires ----------
        run.sim_time_ms = 600
        run.tick_index = 1
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # Verify: trustline B→C created.
        tl_bc = (
            await db_session.execute(
                select(TrustLine).where(
                    TrustLine.from_participant_id == p_b.id,
                    TrustLine.to_participant_id == p_c.id,
                    TrustLine.equivalent_id == eq.id,
                )
            )
        ).scalar_one_or_none()
        assert tl_bc is not None, "B→C trustline should be created"
        assert tl_bc.status == "active"

        # Verify: edges includes B→C.
        edges = run._edges_by_equivalent.get(eq_code, [])
        assert (p_b.pid, p_c.pid) in edges

        # Target participant still active.
        await db_session.refresh(target)
        assert target.status == "active"

        # Re-populate graph cache for freeze invalidation check.
        PaymentRouter._graph_cache[eq_code] = (0.0, {}, {}, {}, {}, {})  # type: ignore[assignment]

        # ---- Tick 2: sim_time=1200 — freeze_participant fires ------------
        run.sim_time_ms = 1200
        run.tick_index = 2
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # Verify: target participant status = 'suspended'.
        await db_session.refresh(target)
        assert target.status == "suspended", (
            f"Frozen participant should be 'suspended', got '{target.status}'"
        )

        # Verify: all incident trustlines are frozen.
        await db_session.refresh(tl_ab)
        await db_session.refresh(tl_ba)
        await db_session.refresh(tl_ac)
        await db_session.refresh(tl_ca)
        assert tl_ab.status == "frozen", f"A→B should be frozen, got '{tl_ab.status}'"
        assert tl_ba.status == "frozen", f"B→A should be frozen, got '{tl_ba.status}'"
        assert tl_ac.status == "frozen", f"A→C should be frozen, got '{tl_ac.status}'"
        assert tl_ca.status == "frozen", f"C→A should be frozen, got '{tl_ca.status}'"

        # Verify: non-incident trustline B→C remains active.
        await db_session.refresh(tl_bc)
        assert tl_bc.status == "active", (
            f"B→C (non-incident) should remain active, got '{tl_bc.status}'"
        )

        # Verify: graph cache evicted.
        assert eq_code not in PaymentRouter._graph_cache

        # Verify: run._edges_by_equivalent has NO edges involving target.
        edges_after = run._edges_by_equivalent.get(eq_code, [])
        for s, d in edges_after:
            assert s != target.pid and d != target.pid, (
                f"Edge ({s}, {d}) involves frozen participant {target.pid}"
            )

        # Verify: B→C edge still present (not incident to frozen participant).
        assert (p_b.pid, p_c.pid) in edges_after, (
            "Non-incident edge B→C should remain in edges"
        )

        # Verify: scenario dicts updated in-place.
        # Participant status in scenario.
        target_in_scenario = next(
            (p for p in scenario["participants"] if p.get("id") == target.pid),
            None,
        )
        assert target_in_scenario is not None
        assert target_in_scenario["status"] == "suspended"

        # Incident trustlines in scenario marked frozen.
        frozen_tl_count = sum(
            1
            for tl in scenario["trustlines"]
            if tl.get("status") == "frozen"
            and (tl.get("from") == target.pid or tl.get("to") == target.pid)
        )
        assert frozen_tl_count == 4, (
            f"Expected 4 frozen trustlines in scenario, got {frozen_tl_count}"
        )

        # Non-incident trustline B→C in scenario remains active.
        bc_in_scenario = next(
            (
                tl
                for tl in scenario["trustlines"]
                if tl.get("from") == p_b.pid and tl.get("to") == p_c.pid
            ),
            None,
        )
        assert bc_in_scenario is not None
        assert bc_in_scenario.get("status") == "active"

        # Both events should be marked as fired.
        assert 0 in run._real_fired_scenario_event_indexes
        assert 1 in run._real_fired_scenario_event_indexes

    finally:
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(original_cache)
