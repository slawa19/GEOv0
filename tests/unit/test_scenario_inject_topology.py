"""Unit-tests for inject-operations in _apply_due_scenario_events().

Covers:
  - add_participant (idempotent skip, cache invalidation)
  - create_trustline (idempotent skip, cache invalidation, unknown eq)
  - freeze_participant (suspend, freeze_trustlines=false, idempotent)

The create / freeze-incident-trustlines cases live in
``tests/integration/test_simulator_network_growth.py`` (025 ``T2504.2``: the copies here were
proven duplicates and removed).
  - General: env-disabled inject, malformed effects

Uses real SQLite ``db_session`` fixture for DB-touching tests and lightweight
mock session for pure-logic tests (env disabled / malformed).
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _DummyArtifacts:
    """Minimal artifacts stub that records enqueued payloads."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def enqueue_event_artifact(self, run_id: str, payload: dict) -> None:
        self.payloads.append(payload)

    def write_real_tick_artifact(self, run: RunRecord, payload: dict) -> None:
        return None


def _make_runner(
    *,
    inject_enabled: bool = True,
    artifacts: _DummyArtifacts | None = None,
) -> tuple[RealRunnerImpl, _DummyArtifacts]:
    """Create a ``RealRunnerImpl`` instance with inject flag pre-set."""
    arts = artifacts or _DummyArtifacts()
    runner = RealRunnerImpl(
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
        logger=logging.getLogger("test.inject"),
    )
    # Override the env-derived flag directly.
    runner._real_enable_inject = inject_enabled
    return runner, arts


class _DummySse:
    """Minimal SSE stub."""

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"evt_{run.run_id}_{run._event_seq:06d}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        pass


def _make_run(
    *,
    participants: list[tuple[uuid.UUID, str]] | None = None,
    equivalents: list[str] | None = None,
    edges_by_equivalent: dict[str, list[tuple[str, str]]] | None = None,
    sim_time_ms: int = 1000,
) -> RunRecord:
    """Create a ``RunRecord`` pre-configured for inject tests."""
    run = RunRecord(
        run_id="run-inject-test",
        scenario_id="sc-inject-test",
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


def _nonce() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Lightweight mock session for tests that don't touch DB
# ---------------------------------------------------------------------------

class _MockResult:
    """Minimal query result proxy."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> "_MockResult":
        return self

    def all(self) -> list:
        if isinstance(self._value, list):
            return self._value
        return [] if self._value is None else [self._value]

    def one_or_none(self) -> Any:
        return self._value


class _MockSession:
    """Minimal async session mock for non-DB tests."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = False
        # The due-events phase owns its transactions (programme 015, phase B step 3): it refuses
        # a session with unflushed changes and ends any transaction it finds open.
        self.new: set[Any] = set()
        self.dirty: set[Any] = set()
        self.deleted: set[Any] = set()

    def in_transaction(self) -> bool:
        return False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if hasattr(obj, "id") and obj.id is None:
                obj.id = uuid.uuid4()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def execute(self, stmt: Any) -> _MockResult:
        return _MockResult(None)


# ===================================================================
# add_participant tests
# ===================================================================


@pytest.mark.asyncio
async def test_add_participant_idempotent_skip(db_session) -> None:
    """If participant with same PID already exists → skip, no error."""
    n = _nonce()

    existing = Participant(
        pid=f"EXIST_{n}",
        display_name="Existing",
        public_key=f"pk_exist_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add(existing)
    await db_session.flush()

    run = _make_run(participants=[(existing.id, existing.pid)])

    scenario: dict[str, Any] = {
        "participants": [{"id": existing.pid}],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "add_participant",
                        "participant": {
                            "id": existing.pid,
                            "name": "Duplicate",
                        },
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner()
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # Count participants with this PID — should remain 1.
    from sqlalchemy import func

    cnt = (
        await db_session.execute(
            select(func.count()).select_from(Participant).where(
                Participant.pid == existing.pid
            )
        )
    ).scalar_one()
    assert cnt == 1

    # Event should be marked as fired.
    assert 0 in run._real_fired_scenario_event_indexes


@pytest.mark.asyncio
async def test_add_participant_updates_caches(db_session) -> None:
    """After add_participant: run caches + scenario dicts + graph cache invalidated."""
    n = _nonce()

    eq = Equivalent(code=f"U{n}".upper()[:16], precision=2, is_active=True)
    sponsor = Participant(
        pid=f"SP_{n}",
        display_name="Sponsor",
        public_key=f"pk_sp_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, sponsor])
    await db_session.flush()

    eq_code = eq.code
    new_pid = f"NP_{n}"

    run = _make_run(
        participants=[(sponsor.id, sponsor.pid)],
        equivalents=[eq_code],
        edges_by_equivalent={eq_code: []},
    )
    run._real_viz_by_eq[eq_code] = "dummy_viz"

    # Pre-populate PaymentRouter graph cache.
    original_cache = PaymentRouter._graph_cache.copy()
    PaymentRouter._graph_cache[eq_code] = (0.0, {}, {}, {}, {}, {})  # type: ignore[assignment]

    scenario: dict[str, Any] = {
        "participants": [{"id": sponsor.pid}],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "add_participant",
                        "participant": {
                            "id": new_pid,
                            "name": "Cached New",
                            "type": "person",
                            "groupId": "g1",
                            "behaviorProfileId": "bp1",
                        },
                        "initial_trustlines": [
                            {
                                "sponsor": sponsor.pid,
                                "equivalent": eq_code,
                                "limit": "200",
                                "direction": "sponsor_credits_new",
                            },
                        ],
                    },
                ],
            },
        ],
    }

    runner, _arts = _make_runner()
    try:
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # 1. run._real_participants contains new participant.
        pids_in_cache = [pid for (_uid, pid) in run._real_participants]
        assert new_pid in pids_in_cache

        # 2. scenario["participants"] updated.
        s_pids = [p["id"] for p in scenario["participants"]]
        assert new_pid in s_pids

        # 3. scenario["trustlines"] has new entry.
        assert len(scenario["trustlines"]) >= 1
        tl_froms = [t["from"] for t in scenario["trustlines"]]
        assert sponsor.pid in tl_froms

        # 4. PaymentRouter._graph_cache evicted for this eq.
        assert eq_code not in PaymentRouter._graph_cache

        # 5. run._real_viz_by_eq evicted for this eq.
        assert eq_code not in run._real_viz_by_eq

        # 6. run._edges_by_equivalent has new edge.
        edges = run._edges_by_equivalent.get(eq_code, [])
        assert len(edges) >= 1
    finally:
        # Restore original graph cache to avoid leaking state.
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(original_cache)


# ===================================================================
# create_trustline tests
# ===================================================================


@pytest.mark.asyncio
async def test_create_trustline_idempotent_skip(db_session) -> None:
    """Duplicate trustline → skip, no extra rows."""
    n = _nonce()

    eq = Equivalent(code=f"D{n}".upper()[:16], precision=2, is_active=True)
    p_a = Participant(
        pid=f"A_{n}",
        display_name="A",
        public_key=f"pk_a_{n}"[:64],
        type="person",
        status="active",
    )
    p_b = Participant(
        pid=f"B_{n}",
        display_name="B",
        public_key=f"pk_b_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, p_a, p_b])
    await db_session.flush()

    # Pre-create trustline.
    existing_tl = TrustLine(
        from_participant_id=p_a.id,
        to_participant_id=p_b.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
    )
    db_session.add(existing_tl)
    await db_session.commit()

    eq_code = eq.code

    run = _make_run(
        participants=[(p_a.id, p_a.pid), (p_b.id, p_b.pid)],
        equivalents=[eq_code],
    )

    scenario: dict[str, Any] = {
        "participants": [],
        "trustlines": [],
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
                        "limit": "100",
                    },
                ],
            },
        ],
    }

    runner, _arts = _make_runner()
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    from sqlalchemy import func

    cnt = (
        await db_session.execute(
            select(func.count()).select_from(TrustLine).where(
                TrustLine.from_participant_id == p_a.id,
                TrustLine.to_participant_id == p_b.id,
                TrustLine.equivalent_id == eq.id,
            )
        )
    ).scalar_one()
    assert cnt == 1, "No duplicate trustline should be created"

    assert 0 in run._real_fired_scenario_event_indexes


@pytest.mark.asyncio
async def test_create_trustline_updates_caches(db_session) -> None:
    """create_trustline invalidates graph_cache, viz, and appends edges."""
    n = _nonce()

    eq = Equivalent(code=f"E{n}".upper()[:16], precision=2, is_active=True)
    p_a = Participant(
        pid=f"CA_{n}",
        display_name="A",
        public_key=f"pk_ca_{n}"[:64],
        type="person",
        status="active",
    )
    p_b = Participant(
        pid=f"CB_{n}",
        display_name="B",
        public_key=f"pk_cb_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, p_a, p_b])
    await db_session.flush()

    eq_code = eq.code

    run = _make_run(
        participants=[(p_a.id, p_a.pid), (p_b.id, p_b.pid)],
        equivalents=[eq_code],
        edges_by_equivalent={eq_code: []},
    )
    run._real_viz_by_eq[eq_code] = "old_viz"

    original_cache = PaymentRouter._graph_cache.copy()
    PaymentRouter._graph_cache[eq_code] = (0.0, {}, {}, {}, {}, {})  # type: ignore[assignment]

    scenario: dict[str, Any] = {
        "participants": [],
        "trustlines": [],
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
                        "limit": "300",
                    },
                ],
            },
        ],
    }

    runner, _arts = _make_runner()
    try:
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        # graph_cache evicted.
        assert eq_code not in PaymentRouter._graph_cache

        # viz evicted.
        assert eq_code not in run._real_viz_by_eq

        # edges_by_equivalent updated.
        edges = run._edges_by_equivalent.get(eq_code, [])
        assert (p_a.pid, p_b.pid) in edges

        # scenario["trustlines"] updated.
        assert len(scenario["trustlines"]) == 1
        assert scenario["trustlines"][0]["from"] == p_a.pid
        assert scenario["trustlines"][0]["to"] == p_b.pid
    finally:
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(original_cache)


@pytest.mark.asyncio
async def test_create_trustline_unknown_equivalent_skips(db_session) -> None:
    """Unknown equivalent code → skip + warning, no crash."""
    n = _nonce()

    p_a = Participant(
        pid=f"UA_{n}",
        display_name="A",
        public_key=f"pk_ua_{n}"[:64],
        type="person",
        status="active",
    )
    p_b = Participant(
        pid=f"UB_{n}",
        display_name="B",
        public_key=f"pk_ub_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([p_a, p_b])
    await db_session.flush()

    run = _make_run(
        participants=[(p_a.id, p_a.pid), (p_b.id, p_b.pid)],
    )

    scenario: dict[str, Any] = {
        "participants": [],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "create_trustline",
                        "from": p_a.pid,
                        "to": p_b.pid,
                        "equivalent": "NONEXISTENT_EQ",
                        "limit": "100",
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner()
    # Should NOT raise.
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # No trustline created.
    from sqlalchemy import func

    cnt = (
        await db_session.execute(
            select(func.count()).select_from(TrustLine).where(
                TrustLine.from_participant_id == p_a.id,
                TrustLine.to_participant_id == p_b.id,
            )
        )
    ).scalar_one()
    assert cnt == 0

    # Event is still marked as fired (processed, even if effects skipped).
    assert 0 in run._real_fired_scenario_event_indexes


# ===================================================================
# freeze_participant tests
# ===================================================================


@pytest.mark.asyncio
async def test_freeze_sets_participant_suspended(db_session) -> None:
    """freeze_participant sets participant.status = 'suspended'."""
    n = _nonce()

    eq = Equivalent(code=f"F{n}".upper()[:16], precision=2, is_active=True)
    target = Participant(
        pid=f"FRZ_{n}",
        display_name="Target",
        public_key=f"pk_frz_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, target])
    await db_session.flush()

    eq_code = eq.code

    run = _make_run(
        participants=[(target.id, target.pid)],
        equivalents=[eq_code],
    )

    scenario: dict[str, Any] = {
        "participants": [{"id": target.pid, "status": "active"}],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
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

    runner, _arts = _make_runner()
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    await db_session.refresh(target)
    assert target.status == "suspended"


@pytest.mark.asyncio
async def test_freeze_with_freeze_trustlines_false(db_session) -> None:
    """freeze_trustlines=false → trustlines NOT changed."""
    n = _nonce()

    eq = Equivalent(code=f"H{n}".upper()[:16], precision=2, is_active=True)
    target = Participant(
        pid=f"NF_{n}",
        display_name="NoFreezeTL",
        public_key=f"pk_nf_{n}"[:64],
        type="person",
        status="active",
    )
    other = Participant(
        pid=f"NF2_{n}",
        display_name="Other",
        public_key=f"pk_nf2_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, target, other])
    await db_session.flush()

    eq_code = eq.code

    tl = TrustLine(
        from_participant_id=target.id,
        to_participant_id=other.id,
        equivalent_id=eq.id,
        limit=Decimal("100"),
        status="active",
    )
    db_session.add(tl)
    await db_session.commit()

    run = _make_run(
        participants=[(target.id, target.pid), (other.id, other.pid)],
        equivalents=[eq_code],
    )

    scenario: dict[str, Any] = {
        "participants": [{"id": target.pid, "status": "active"}],
        "trustlines": [{"from": target.pid, "to": other.pid, "status": "active"}],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "freeze_participant",
                        "participant_id": target.pid,
                        "freeze_trustlines": False,
                    },
                ],
            },
        ],
    }

    runner, _arts = _make_runner()
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # Participant should be suspended.
    await db_session.refresh(target)
    assert target.status == "suspended"

    # TrustLine should remain active.
    await db_session.refresh(tl)
    assert tl.status == "active"


@pytest.mark.asyncio
async def test_freeze_invalidation_is_narrow_per_incident_equivalents(db_session) -> None:
    """freeze_participant should evict caches only for incident equivalents."""
    n = _nonce()

    eq1 = Equivalent(code=f"J{n}".upper()[:16], precision=2, is_active=True)
    eq2 = Equivalent(code=f"K{n}".upper()[:16], precision=2, is_active=True)
    a = Participant(
        pid=f"FZ_A_{n}",
        display_name="A",
        public_key=f"pk_fz_a_{n}"[:64],
        type="person",
        status="active",
    )
    b = Participant(
        pid=f"FZ_B_{n}",
        display_name="B",
        public_key=f"pk_fz_b_{n}"[:64],
        type="person",
        status="active",
    )
    c = Participant(
        pid=f"FZ_C_{n}",
        display_name="C",
        public_key=f"pk_fz_c_{n}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq1, eq2, a, b, c])
    await db_session.flush()

    tl1 = TrustLine(
        from_participant_id=a.id,
        to_participant_id=b.id,
        equivalent_id=eq1.id,
        limit=Decimal("10"),
        status="active",
    )
    tl2 = TrustLine(
        from_participant_id=c.id,
        to_participant_id=b.id,
        equivalent_id=eq2.id,
        limit=Decimal("10"),
        status="active",
    )
    db_session.add_all([tl1, tl2])
    await db_session.commit()

    run = _make_run(
        participants=[(a.id, a.pid), (b.id, b.pid), (c.id, c.pid)],
        equivalents=[eq1.code, eq2.code],
    )

    scenario: dict[str, Any] = {
        "participants": [
            {"id": a.pid, "status": "active"},
            {"id": b.pid, "status": "active"},
            {"id": c.pid, "status": "active"},
        ],
        "trustlines": [
            {"from": a.pid, "to": b.pid, "equivalent": eq1.code, "status": "active"},
            {"from": c.pid, "to": b.pid, "equivalent": eq2.code, "status": "active"},
        ],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "freeze_participant",
                        "participant_id": a.pid,
                        "freeze_trustlines": False,
                    },
                ],
            },
        ],
    }

    original_cache = dict(PaymentRouter._graph_cache)
    try:
        PaymentRouter._graph_cache[eq1.code] = object()
        PaymentRouter._graph_cache[eq2.code] = object()
        run._real_viz_by_eq[eq1.code] = object()  # type: ignore[assignment]
        run._real_viz_by_eq[eq2.code] = object()  # type: ignore[assignment]

        runner, _arts = _make_runner()
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=scenario
        )

        assert eq1.code not in PaymentRouter._graph_cache
        assert eq1.code not in run._real_viz_by_eq
        assert eq2.code in PaymentRouter._graph_cache
        assert eq2.code in run._real_viz_by_eq
    finally:
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(original_cache)


@pytest.mark.asyncio
async def test_freeze_idempotent(db_session) -> None:
    """Already suspended participant → skip, no error."""
    n = _nonce()

    target = Participant(
        pid=f"IDF_{n}",
        display_name="AlreadySuspended",
        public_key=f"pk_idf_{n}"[:64],
        type="person",
        status="suspended",
    )
    db_session.add(target)
    await db_session.flush()

    run = _make_run(
        participants=[(target.id, target.pid)],
    )

    scenario: dict[str, Any] = {
        "participants": [],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "freeze_participant",
                        "participant_id": target.pid,
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner()
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    await db_session.refresh(target)
    assert target.status == "suspended"
    assert 0 in run._real_fired_scenario_event_indexes


# ===================================================================
# General tests
# ===================================================================


@pytest.mark.asyncio
async def test_inject_skipped_when_env_disabled() -> None:
    """SIMULATOR_REAL_ENABLE_INJECT=0 → inject events are skipped."""
    run = _make_run()
    session = _MockSession()

    scenario: dict[str, Any] = {
        "participants": [],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "add_participant",
                        "participant": {"id": "PID_NEW", "name": "New"},
                    },
                ],
            },
        ],
    }

    runner, arts = _make_runner(inject_enabled=False)
    await runner._apply_due_scenario_events(
        session, run_id="r1", run=run, scenario=scenario
    )

    # Event marked as fired.
    assert 0 in run._real_fired_scenario_event_indexes

    # No DB writes.
    assert not session.committed
    assert len(session.added) == 0

    # Artifact enqueued with "inject skipped" note.
    assert any("inject skipped" in str(p.get("scenario", {}).get("description", "")) for p in arts.payloads)


@pytest.mark.asyncio
async def test_malformed_inject_effect_skipped(db_session) -> None:
    """Malformed payload (missing fields) → skip + no crash.

    A REAL SESSION, and it stopped being optional. `_MockSession` carried this test until the inject
    unit of work began to take the participant locks and to read the database before it stages
    anything - so a double that answers `add`, `flush` and `commit` no longer reaches the code under
    test at all.
    The subject is unchanged: every effect here is malformed, none of them is applied, and nothing
    raises.
    """
    from sqlalchemy import select as _select

    from app.db.models.participant import Participant as _Participant
    from app.db.models.trustline import TrustLine as _TrustLine

    run = _make_run()
    session = db_session

    scenario: dict[str, Any] = {
        "participants": [],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    # add_participant without participant dict.
                    {"op": "add_participant"},
                    # create_trustline without required fields.
                    {"op": "create_trustline"},
                    # create_trustline with empty from/to.
                    {"op": "create_trustline", "from": "", "to": "", "equivalent": ""},
                    # freeze_participant without participant_id.
                    {"op": "freeze_participant"},
                    # Unknown op — silently skipped.
                    {"op": "unknown_op_xyz"},
                    # Non-dict effect.
                    42,
                ],
            },
        ],
    }

    runner, arts = _make_runner(inject_enabled=True)
    # Should NOT raise.
    await runner._apply_due_scenario_events(
        session, run_id="r1", run=run, scenario=scenario
    )

    # Event marked as fired.
    assert 0 in run._real_fired_scenario_event_indexes

    # Nothing was created, read back from the database rather than from a list the double kept.
    assert (
        await session.execute(_select(_Participant.id).where(_Participant.pid == "PID_NEW"))
    ).scalars().all() == []
    assert (await session.execute(_select(_TrustLine.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_removed_inject_debt_op_is_skipped_and_writes_nothing(db_session) -> None:
    """`inject_debt` no longer exists: the scenario schema refuses it, and the executor, handed one anyway,
    counts it skipped and writes neither a debt nor a line (a refusal that is only in the schema would let a
    hand-built event still move money).
    """
    n = _nonce()
    eq = Equivalent(code=f"R{n}".upper()[:16], precision=2, is_active=True)
    creditor = Participant(
        pid=f"RC_{n}", display_name="C", public_key=f"pk_rc_{n}"[:64], type="person", status="active"
    )
    debtor = Participant(
        pid=f"RD_{n}", display_name="D", public_key=f"pk_rd_{n}"[:64], type="person", status="active"
    )
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()

    run = _make_run(
        participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid)],
        equivalents=[eq.code],
    )
    scenario: dict[str, Any] = {
        "participants": [{"id": creditor.pid}, {"id": debtor.pid}],
        "trustlines": [],
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
                        "amount": "10.00",
                    }
                ],
            }
        ],
    }

    runner, arts = _make_runner(inject_enabled=True)
    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=scenario
    )

    # The event was consumed, and the note says one effect was skipped and none applied.
    assert 0 in run._real_fired_scenario_event_indexes
    notes = [p["scenario"] for p in arts.payloads if p.get("type") == "note"]
    assert [n_["description"] for n_ in notes] == ["inject applied"], notes
    assert notes[0]["stats"]["applied"] == 0 and notes[0]["stats"]["skipped"] == 1, notes

    # Nothing was written, read back from the database.
    assert (await db_session.execute(select(Debt.id).where(Debt.equivalent_id == eq.id))).scalars().all() == []
    assert (
        await db_session.execute(
            select(TrustLine.id).where(TrustLine.equivalent_id == eq.id)
        )
    ).scalars().all() == []
