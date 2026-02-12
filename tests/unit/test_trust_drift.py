"""Unit-tests for Phase 2 Trust Drift.

Covers:
  - TrustDriftConfig.from_scenario() — parsing trust_drift settings
  - _init_trust_drift() — initializing edge clearing history from scenario
  - _apply_trust_growth() — limit growth after clearing
  - _apply_trust_decay() — limit decay for overloaded edges

Uses the same mocking approach as test_warmup_and_capacity.py —
lightweight RealRunner with mocked DB session for async methods.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.real_runner import RealRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Stable UUIDs for participants and equivalents.
_UID_ALICE = uuid.UUID("00000000-0000-0000-0000-000000000001")
_UID_BOB = uuid.UUID("00000000-0000-0000-0000-000000000002")
_UID_CAROL = uuid.UUID("00000000-0000-0000-0000-000000000003")
_UID_EQ_UAH = uuid.UUID("00000000-0000-0000-0000-0000000000e1")


class _DummySse:
    """Minimal SSE stub."""

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"evt_{run.run_id}_{run._event_seq:06d}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        pass


class _DummyArtifacts:
    """Minimal artifacts stub."""

    def enqueue_event_artifact(self, run_id: str, payload: dict) -> None:
        pass

    def write_real_tick_artifact(self, run: RunRecord, payload: dict) -> None:
        pass


def _make_scenario(
    *,
    trust_drift: dict[str, Any] | None = None,
    trustlines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a scenario dict with optional trust_drift settings."""
    tls = trustlines if trustlines is not None else [
        {
            "equivalent": "UAH",
            "from": "alice",
            "to": "bob",
            "limit": 1000,
            "status": "active",
        },
        {
            "equivalent": "UAH",
            "from": "bob",
            "to": "carol",
            "limit": 500,
            "status": "active",
        },
    ]

    scenario: dict[str, Any] = {
        "scenario_id": "s-td",
        "equivalents": ["UAH"],
        "participants": [
            {"id": "alice", "type": "person", "groupId": "g1",
             "behaviorProfileId": "default"},
            {"id": "bob", "type": "person", "groupId": "g1",
             "behaviorProfileId": "default"},
            {"id": "carol", "type": "person", "groupId": "g1",
             "behaviorProfileId": "default"},
        ],
        "behaviorProfiles": [
            {"id": "default", "props": {"tx_rate": 1.0,
                                         "equivalent_weights": {"UAH": 1.0}}},
        ],
        "trustlines": tls,
    }

    if trust_drift is not None:
        scenario.setdefault("settings", {})["trust_drift"] = trust_drift

    return scenario


def _make_runner(
    *,
    scenario: dict[str, Any] | None = None,
) -> RealRunner:
    """Create a lightweight RealRunner for unit tests (no DB, no SSE)."""
    _scenario = scenario or {}
    return RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: None,  # type: ignore[arg-type]
        get_scenario_raw=lambda _sid: _scenario,
        sse=_DummySse(),  # type: ignore[arg-type]
        artifacts=_DummyArtifacts(),  # type: ignore[arg-type]
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: False,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger("test.trust_drift"),
    )


def _make_run(
    *,
    participants: list[tuple[uuid.UUID, str]] | None = None,
) -> RunRecord:
    """Create a RunRecord pre-configured for trust drift tests."""
    run = RunRecord(
        run_id="r-td",
        scenario_id="s-td",
        mode="real",
        state="running",
        started_at=_utc_now(),
    )
    run.tick_index = 5
    run.sim_time_ms = 5000
    run._real_seeded = True
    run._real_participants = participants or [
        (_UID_ALICE, "alice"),
        (_UID_BOB, "bob"),
        (_UID_CAROL, "carol"),
    ]
    run._real_equivalents = ["UAH"]
    run._edges_by_equivalent = {
        "UAH": [("alice", "bob"), ("bob", "carol")],
    }
    return run


class _MockResult:
    """Minimal async query result proxy."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


def _make_growth_session(
    *,
    eq_id: uuid.UUID = _UID_EQ_UAH,
    current_limit: float = 1000.0,
) -> AsyncMock:
    """Build an async mock session for ``_apply_trust_growth``.

    Call sequence:
      1. ``select(Equivalent.id)`` → eq_id
      2+. For each edge: ``select(TrustLine.limit)`` → current_limit,
          then optionally ``update(TrustLine)``
    """
    call_idx = 0

    async def _execute(stmt):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            # Equivalent lookup
            return _MockResult(eq_id)
        # Even calls after first: TrustLine limit lookup or update
        # For selects, return current_limit; for updates, return anything
        return _MockResult(Decimal(str(current_limit)))

    session = AsyncMock()
    session.execute = _execute
    session.commit = AsyncMock()
    return session


def _make_decay_session(
    *,
    eq_id: uuid.UUID = _UID_EQ_UAH,
) -> AsyncMock:
    """Build an async mock session for ``_apply_trust_decay``.

    Call sequence:
      1. ``select(Equivalent.id)`` → eq_id
      2+. ``update(TrustLine)`` calls
    """
    call_idx = 0

    async def _execute(stmt):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            # Equivalent lookup
            return _MockResult(eq_id)
        return _MockResult(None)

    session = AsyncMock()
    session.execute = _execute
    session.commit = AsyncMock()
    return session


# ===================================================================
# TrustDriftConfig.from_scenario() tests
# ===================================================================


class TestTrustDriftConfig:
    """Pure unit tests for TrustDriftConfig.from_scenario()."""

    def test_config_from_scenario_enabled(self) -> None:
        """Scenario with trust_drift.enabled=true → all params parsed."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "growth_rate": 0.1,
            "decay_rate": 0.03,
            "max_growth": 3.0,
            "min_limit_ratio": 0.2,
            "overload_threshold": 0.9,
        })

        cfg = TrustDriftConfig.from_scenario(scenario)

        assert cfg.enabled is True
        assert cfg.growth_rate == 0.1
        assert cfg.decay_rate == 0.03
        assert cfg.max_growth == 3.0
        assert cfg.min_limit_ratio == 0.2
        assert cfg.overload_threshold == 0.9

    def test_config_from_scenario_disabled(self) -> None:
        """Scenario without trust_drift → enabled=False, defaults."""
        scenario = _make_scenario()  # no trust_drift key

        cfg = TrustDriftConfig.from_scenario(scenario)

        assert cfg.enabled is False
        # Defaults should be applied
        assert cfg.growth_rate == 0.05
        assert cfg.decay_rate == 0.02
        assert cfg.max_growth == 2.0
        assert cfg.min_limit_ratio == 0.3
        assert cfg.overload_threshold == 0.8

    def test_config_from_scenario_partial(self) -> None:
        """Scenario with trust_drift but missing some fields → defaults for absent."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "growth_rate": 0.15,
            # decay_rate, max_growth, min_limit_ratio, overload_threshold — missing
        })

        cfg = TrustDriftConfig.from_scenario(scenario)

        assert cfg.enabled is True
        assert cfg.growth_rate == 0.15
        # Missing fields use defaults
        assert cfg.decay_rate == 0.02
        assert cfg.max_growth == 2.0
        assert cfg.min_limit_ratio == 0.3
        assert cfg.overload_threshold == 0.8


# ===================================================================
# _init_trust_drift() tests
# ===================================================================


class TestInitTrustDrift:
    """Tests for _init_trust_drift()."""

    def test_init_creates_history_for_all_trustlines(self) -> None:
        """After init, _edge_clearing_history has an entry for each trustline."""
        scenario = _make_scenario(trust_drift={"enabled": True})
        runner = _make_runner(scenario=scenario)
        run = _make_run()

        runner._init_trust_drift(run, scenario)

        # Scenario has 2 trustlines: alice→bob and bob→carol
        assert len(run._edge_clearing_history) == 2
        assert "alice:bob:UAH" in run._edge_clearing_history
        assert "bob:carol:UAH" in run._edge_clearing_history

    def test_init_stores_original_limit(self) -> None:
        """original_limit in EdgeClearingHistory = limit from scenario trustline."""
        scenario = _make_scenario(trust_drift={"enabled": True})
        runner = _make_runner(scenario=scenario)
        run = _make_run()

        runner._init_trust_drift(run, scenario)

        hist_ab = run._edge_clearing_history["alice:bob:UAH"]
        hist_bc = run._edge_clearing_history["bob:carol:UAH"]

        assert hist_ab.original_limit == 1000.0
        assert hist_bc.original_limit == 500.0
        # Initial counters must be zero
        assert hist_ab.clearing_count == 0
        assert hist_ab.last_clearing_tick == -1
        assert hist_ab.cleared_volume == 0.0


# ===================================================================
# _apply_trust_growth() tests
# ===================================================================


class TestApplyTrustGrowth:
    """Tests for _apply_trust_growth() — limit growth after clearing."""

    async def test_growth_increases_limit_after_clearing(self) -> None:
        """Cleared edge → limit increased by growth_rate."""
        scenario = _make_scenario(trust_drift={"enabled": True, "growth_rate": 0.05})
        runner = _make_runner(scenario=scenario)
        run = _make_run()

        # Init trust drift
        runner._init_trust_drift(run, scenario)

        current_limit = 1000.0
        session = _make_growth_session(current_limit=current_limit)

        touched_edges: set[tuple[str, str]] = {("alice", "bob")}
        cleared_amounts: dict[tuple[str, str], float] = {("alice", "bob"): 200.0}

        res = await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=5,
            cleared_amount_per_edge=cleared_amounts,
        )

        assert res.updated_count == 1
        # new_limit = min(1000 * 1.05, 1000 * 2.0) = 1050.0
        expected_limit = round(current_limit * 1.05, 2)
        # Check scenario in-memory update
        s_tls = scenario["trustlines"]
        ab_tl = next(
            t for t in s_tls
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == expected_limit

    async def test_growth_capped_by_max_growth(self) -> None:
        """When limit already near cap, growth is bounded by original_limit × max_growth."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "growth_rate": 0.05,
            "max_growth": 1.5,
        })
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        # Simulate limit already at 1490 (near cap of 1000 * 1.5 = 1500)
        current_limit = 1490.0
        session = _make_growth_session(current_limit=current_limit)

        touched_edges: set[tuple[str, str]] = {("alice", "bob")}
        cleared_amounts: dict[tuple[str, str], float] = {("alice", "bob"): 100.0}

        res = await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=5,
            cleared_amount_per_edge=cleared_amounts,
        )

        assert res.updated_count == 1
        # new_limit = min(1490 * 1.05, 1000 * 1.5) = min(1564.5, 1500) = 1500.0
        cap = run._edge_clearing_history["alice:bob:UAH"].original_limit * Decimal("1.5")
        s_tls = scenario["trustlines"]
        ab_tl = next(
            t for t in s_tls
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == round(float(cap), 2)

    async def test_growth_updates_clearing_history(self) -> None:
        """After growth: clearing_count += 1, last_clearing_tick updated, cleared_volume accumulated."""
        scenario = _make_scenario(trust_drift={"enabled": True, "growth_rate": 0.05})
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        session = _make_growth_session(current_limit=1000.0)
        touched_edges: set[tuple[str, str]] = {("alice", "bob")}
        cleared_amounts: dict[tuple[str, str], float] = {("alice", "bob"): 300.0}
        tick = 7

        await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=tick,
            cleared_amount_per_edge=cleared_amounts,
        )

        hist = run._edge_clearing_history["alice:bob:UAH"]
        assert hist.clearing_count == 1
        assert hist.last_clearing_tick == tick
        assert hist.cleared_volume == 300.0

        # Second growth call — history accumulates
        session2 = _make_growth_session(current_limit=1050.0)
        cleared_amounts2: dict[tuple[str, str], float] = {("alice", "bob"): 150.0}
        tick2 = 12

        await runner._apply_trust_growth(
            run, session2, touched_edges, "UAH", tick_index=tick2,
            cleared_amount_per_edge=cleared_amounts2,
        )

        assert hist.clearing_count == 2
        assert hist.last_clearing_tick == tick2
        assert hist.cleared_volume == 450.0  # 300 + 150

    async def test_growth_skipped_when_disabled(self) -> None:
        """enabled=False → 0 updated, no DB calls."""
        scenario = _make_scenario()  # no trust_drift → disabled
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        session = AsyncMock()

        touched_edges: set[tuple[str, str]] = {("alice", "bob")}
        cleared_amounts: dict[tuple[str, str], float] = {("alice", "bob"): 200.0}

        res = await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=5,
            cleared_amount_per_edge=cleared_amounts,
        )

        assert res.updated_count == 0
        # Session should not have been used for execute
        session.execute.assert_not_called()


# ===================================================================
# _apply_trust_decay() tests
# ===================================================================


class TestApplyTrustDecay:
    """Tests for _apply_trust_decay() — limit decay for overloaded edges."""

    async def test_decay_reduces_limit_when_overloaded(self) -> None:
        """debt/limit ≥ 0.8 → limit decreased by decay_rate."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "decay_rate": 0.02,
            "overload_threshold": 0.8,
        })
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        # Debt of 850 on limit 1000 → ratio 0.85 ≥ 0.8
        # Note: debt key is (debtor_pid, creditor_pid, eq_code)
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("bob", "alice", "UAH"): Decimal("850"),
        }

        session = _make_decay_session()

        res = await runner._apply_trust_decay(
            run, session, tick_index=10, debt_snapshot=debt_snapshot,
            scenario=scenario,
        )

        assert res.updated_count == 1
        # new_limit = max(1000 * (1 - 0.02), 1000 * 0.3) = max(980, 300) = 980.0
        expected = round(1000.0 * (1 - 0.02), 2)
        ab_tl = next(
            t for t in scenario["trustlines"]
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == expected

    async def test_decay_floored_by_min_limit_ratio(self) -> None:
        """Repeated decay doesn't drop below original_limit × min_limit_ratio."""
        scenario = _make_scenario(
            trust_drift={
                "enabled": True,
                "decay_rate": 0.5,  # aggressive decay for testing
                "overload_threshold": 0.8,
                "min_limit_ratio": 0.3,
            },
            trustlines=[
                {
                    "equivalent": "UAH",
                    "from": "alice",
                    "to": "bob",
                    "limit": 350,  # already close to floor of 1000 * 0.3 = 300
                    "status": "active",
                },
            ],
        )
        runner = _make_runner(scenario=scenario)
        run = _make_run()

        # Manually set up trust drift config and history (original_limit = 1000)
        run._trust_drift_config = TrustDriftConfig(
            enabled=True,
            decay_rate=0.5,
            overload_threshold=0.8,
            min_limit_ratio=0.3,
        )
        run._edge_clearing_history = {
            "alice:bob:UAH": EdgeClearingHistory(original_limit=1000.0),
        }

        # Debt high enough to trigger decay (280/350 = 0.8)
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("bob", "alice", "UAH"): Decimal("280"),
        }

        session = _make_decay_session()

        res = await runner._apply_trust_decay(
            run, session, tick_index=10, debt_snapshot=debt_snapshot,
            scenario=scenario,
        )

        assert res.updated_count == 1
        # new_limit = max(350 * (1 - 0.5), 1000 * 0.3) = max(175, 300) = 300.0
        ab_tl = scenario["trustlines"][0]
        assert ab_tl["limit"] == 300.0

    async def test_decay_skips_underloaded_edges(self) -> None:
        """debt/limit < 0.8 → limit unchanged."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "decay_rate": 0.02,
            "overload_threshold": 0.8,
        })
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        original_limit = scenario["trustlines"][0]["limit"]

        # Debt of 500 on limit 1000 → ratio 0.5 < 0.8, should skip
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("bob", "alice", "UAH"): Decimal("500"),
        }

        session = _make_decay_session()

        res = await runner._apply_trust_decay(
            run, session, tick_index=10, debt_snapshot=debt_snapshot,
            scenario=scenario,
        )

        assert res.updated_count == 0
        # Limit should remain unchanged
        ab_tl = next(
            t for t in scenario["trustlines"]
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == original_limit

    async def test_decay_skips_just_cleared_edges(self) -> None:
        """If last_clearing_tick == tick_index → edge skipped (just had growth)."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "decay_rate": 0.02,
            "overload_threshold": 0.8,
        })
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        original_limit = scenario["trustlines"][0]["limit"]

        # Mark edge as just cleared on tick 10
        hist = run._edge_clearing_history["alice:bob:UAH"]
        hist.last_clearing_tick = 10

        # Debt high enough to normally trigger decay
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("bob", "alice", "UAH"): Decimal("850"),
        }

        session = _make_decay_session()

        # Call with tick_index = 10 (same as last_clearing_tick)
        res = await runner._apply_trust_decay(
            run, session, tick_index=10, debt_snapshot=debt_snapshot,
            scenario=scenario,
        )

        assert res.updated_count == 0
        ab_tl = next(
            t for t in scenario["trustlines"]
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == original_limit
