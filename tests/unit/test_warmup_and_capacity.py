"""Unit-tests for Phase 1 Quick Wins: warm-up ramp and capacity-aware amounts.

Covers:
  - Warm-up ramp (Phase 1.1): linear intensity ramp via settings.warmup
  - Capacity-aware amounts (Phase 1.4): debt_snapshot reduces available limits
  - _load_debt_snapshot_by_pid(): PID-keyed mapping from DB

Uses the same mocking approach as test_simulator_real_amount_model.py —
lightweight RealRunner with no DB, direct calls to _plan_real_payments().
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

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _runner(*, actions_per_tick_max: int = 50) -> RealRunner:
    """Create a lightweight RealRunner for unit tests (no DB, no SSE)."""
    return RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: None,  # type: ignore[arg-type]
        get_scenario_raw=lambda _sid: {},
        sse=None,  # type: ignore[arg-type]
        artifacts=None,  # type: ignore[arg-type]
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: False,
        actions_per_tick_max=actions_per_tick_max,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )


def _make_run(
    *,
    seed: int = 42,
    tick_index: int = 0,
    intensity_percent: int = 100,
) -> RunRecord:
    """Create a RunRecord pre-configured for planner tests."""
    run = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run.seed = seed
    run.tick_index = tick_index
    run.intensity_percent = intensity_percent
    run.sim_time_ms = tick_index * 1000
    return run


def _mesh_scenario(
    *,
    warmup: dict[str, Any] | None = None,
    limit: str = "1000",
    num_participants: int = 5,
) -> dict[str, Any]:
    """Scenario with N participants and a complete trustline mesh.

    Every ordered pair (i, j) gets a trustline ``from=Pi, to=Pj`` (creditor→debtor).
    The candidate payment direction is Pj → Pi (debtor pays creditor).
    This yields N*(N-1) candidate edges — enough to saturate the planner
    with ``actions_per_tick_max ≤ 50`` and ``tx_rate=1.0``.
    """
    pids = [f"P{i}" for i in range(num_participants)]
    participants = [
        {
            "id": pid,
            "type": "person",
            "groupId": "g1",
            "behaviorProfileId": "default",
        }
        for pid in pids
    ]

    trustlines = []
    for frm in pids:
        for to in pids:
            if frm != to:
                trustlines.append(
                    {
                        "equivalent": "UAH",
                        "from": frm,
                        "to": to,
                        "limit": limit,
                        "status": "active",
                    }
                )

    scenario: dict[str, Any] = {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": participants,
        "behaviorProfiles": [
            {
                "id": "default",
                "props": {
                    "tx_rate": 1.0,
                    "equivalent_weights": {"UAH": 1.0},
                },
            },
        ],
        "trustlines": trustlines,
    }

    if warmup is not None:
        scenario["settings"] = {"warmup": warmup}

    return scenario


# ===================================================================
# Warm-up ramp tests (Phase 1.1)
# ===================================================================


class TestWarmupRamp:
    """Tests for the warm-up ramp feature in _plan_real_payments()."""

    def test_warmup_ramp_reduces_actions_at_tick_zero(self) -> None:
        """At tick_index=0 with warmup(ticks=50, floor=0.1), intensity ≈ 10%.

        ramp_factor = 0.1 + 0.9 * (0 / 50) = 0.1
        target_actions = max(1, int(50 * 1.0 * 0.1)) = 5
        """
        runner = _runner(actions_per_tick_max=50)

        scenario_warmup = _mesh_scenario(warmup={"ticks": 50, "floor": 0.1})
        run_w = _make_run(seed=1, tick_index=0, intensity_percent=100)
        planned_warmup = runner._plan_real_payments(run_w, scenario_warmup)

        scenario_full = _mesh_scenario()
        run_f = _make_run(seed=1, tick_index=0, intensity_percent=100)
        planned_full = runner._plan_real_payments(run_f, scenario_full)

        # With warmup at tick 0: target_actions = 5, without: 50.
        assert len(planned_warmup) < len(planned_full), (
            f"Warmup at tick 0 should reduce actions: "
            f"got {len(planned_warmup)} vs {len(planned_full)}"
        )
        assert len(planned_warmup) <= 5

    def test_warmup_ramp_linear_progression(self) -> None:
        """At tick_index=25 (half of warmup_ticks=50), ramp_factor ≈ 0.55.

        ramp_factor = 0.1 + 0.9 * (25 / 50) = 0.55
        target_actions = max(1, int(50 * 0.55)) = 27
        """
        runner = _runner(actions_per_tick_max=50)
        scenario = _mesh_scenario(warmup={"ticks": 50, "floor": 0.1})

        run_mid = _make_run(seed=1, tick_index=25, intensity_percent=100)
        planned_mid = runner._plan_real_payments(run_mid, scenario)

        run_start = _make_run(seed=1, tick_index=0, intensity_percent=100)
        planned_start = runner._plan_real_payments(run_start, scenario)

        # Mid-warmup should produce more than start (5 < x ≤ 27).
        assert len(planned_mid) > len(planned_start), (
            f"Tick 25 should produce more actions than tick 0: "
            f"{len(planned_mid)} vs {len(planned_start)}"
        )
        assert len(planned_mid) <= 27, (
            f"At tick 25/50 warmup expected ≤ 27 actions, got {len(planned_mid)}"
        )

    def test_warmup_ramp_full_intensity_after_warmup(self) -> None:
        """At tick_index >= warmup_ticks, ramp is not applied — full intensity."""
        runner = _runner(actions_per_tick_max=50)

        scenario_warmup = _mesh_scenario(warmup={"ticks": 50, "floor": 0.1})
        scenario_plain = _mesh_scenario()

        run_w = _make_run(seed=1, tick_index=50, intensity_percent=100)
        run_p = _make_run(seed=1, tick_index=50, intensity_percent=100)

        planned_w = runner._plan_real_payments(run_w, scenario_warmup)
        planned_p = runner._plan_real_payments(run_p, scenario_plain)

        # After warmup period, action counts must be identical.
        assert len(planned_w) == len(planned_p), (
            f"After warmup period counts should match: "
            f"{len(planned_w)} vs {len(planned_p)}"
        )

    def test_warmup_no_config_means_no_ramp(self) -> None:
        """Without warmup config, intensity stays unchanged (backward compat)."""
        runner = _runner(actions_per_tick_max=50)

        scenario_none = _mesh_scenario()  # no settings.warmup
        scenario_zero = _mesh_scenario(warmup={"ticks": 0})  # explicit ticks=0

        run1 = _make_run(seed=1, tick_index=0, intensity_percent=100)
        run2 = _make_run(seed=1, tick_index=0, intensity_percent=100)

        planned_none = runner._plan_real_payments(run1, scenario_none)
        planned_zero = runner._plan_real_payments(run2, scenario_zero)

        assert len(planned_none) == len(planned_zero), (
            f"No warmup vs ticks=0 should be identical: "
            f"{len(planned_none)} vs {len(planned_zero)}"
        )
        assert len(planned_none) > 0, "Should produce actions without warmup"

    def test_warmup_floor_clamp(self) -> None:
        """floor=0.0 must be accepted as an explicit value.

        At tick_index=0: ramp_factor = 0.0 + 1.0 * 0 = 0.0
        intensity becomes 0.0, therefore target_actions becomes 0.
        """
        runner = _runner(actions_per_tick_max=50)

        scenario_zero = _mesh_scenario(warmup={"ticks": 50, "floor": 0.0})
        scenario_point1 = _mesh_scenario(warmup={"ticks": 50, "floor": 0.1})

        run_z = _make_run(seed=1, tick_index=0, intensity_percent=100)
        run_p = _make_run(seed=1, tick_index=0, intensity_percent=100)

        planned_zero = runner._plan_real_payments(run_z, scenario_zero)
        planned_point1 = runner._plan_real_payments(run_p, scenario_point1)

        assert len(planned_zero) == 0, (
            f"floor=0.0 at tick 0 should produce 0 actions, got {len(planned_zero)}"
        )

        # floor=0.1 at tick 0 yields ramp_factor=0.1 -> ≤ 5 actions (max(1,int(50*0.1)) = 5)
        assert 1 <= len(planned_point1) <= 5, (
            f"floor=0.1 at tick 0 should produce 1..5 actions, got {len(planned_point1)}"
        )


# ===================================================================
# Capacity-aware amounts tests (Phase 1.4)
# ===================================================================


class TestCapacityAwareAmounts:
    """Tests for capacity-aware amount reduction via debt_snapshot."""

    def test_capacity_reduces_amount_with_existing_debt(self) -> None:
        """With debt_snapshot, amounts ≤ available (limit − used)."""
        runner = _runner(actions_per_tick_max=50)
        scenario = _mesh_scenario(limit="1000", num_participants=3)

        # Every directional edge already has 800 out of 1000 used.
        pids = [f"P{i}" for i in range(3)]
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {}
        for s in pids:
            for r in pids:
                if s != r:
                    debt_snapshot[(s, r, "UAH")] = Decimal("800")

        run = _make_run(seed=1, tick_index=100, intensity_percent=100)
        planned = runner._plan_real_payments(run, scenario, debt_snapshot=debt_snapshot)

        # available = 1000 − 800 = 200 on every edge.
        for a in planned:
            amt = Decimal(a.amount)
            assert amt <= Decimal("200"), (
                f"Amount {amt} exceeds available capacity 200 "
                f"(sender={a.sender_pid}, receiver={a.receiver_pid})"
            )

    def test_capacity_empty_snapshot_uses_static_limit(self) -> None:
        """Empty debt_snapshot → static limit used (backward compat)."""
        runner = _runner(actions_per_tick_max=50)
        scenario = _mesh_scenario(limit="500", num_participants=3)

        run1 = _make_run(seed=1, tick_index=100, intensity_percent=100)
        run2 = _make_run(seed=1, tick_index=100, intensity_percent=100)

        planned_none = runner._plan_real_payments(run1, scenario, debt_snapshot=None)
        planned_empty = runner._plan_real_payments(run2, scenario, debt_snapshot={})

        # Both should produce the same number of actions.
        assert len(planned_none) == len(planned_empty)

        for a in planned_none:
            amt = Decimal(a.amount)
            assert amt <= Decimal("500"), f"Amount {amt} exceeds static limit 500"

    def test_capacity_zero_available_skips_candidate(self) -> None:
        """used ≥ limit → available ≤ 0 → all candidates skipped → 0 actions."""
        runner = _runner(actions_per_tick_max=50)
        scenario = _mesh_scenario(limit="1000", num_participants=3)

        pids = [f"P{i}" for i in range(3)]
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {}
        for s in pids:
            for r in pids:
                if s != r:
                    debt_snapshot[(s, r, "UAH")] = Decimal("1000")

        run = _make_run(seed=1, tick_index=100, intensity_percent=100)
        planned = runner._plan_real_payments(run, scenario, debt_snapshot=debt_snapshot)

        assert len(planned) == 0, (
            f"Zero available capacity should produce 0 actions, got {len(planned)}"
        )

    def test_capacity_three_level_reduction(self) -> None:
        """The planner uses min(available_out, available_in, available_direct).

        Scenario with 3 participants (A, B, C) and asymmetric debt
        so ``available_in`` is the tightest constraint for A→B payments:

            available_out(A)       = 1000 − 200 − 200 = 600
            available_in(B)        = 1000 − 100 − 700 = 200  ← bottleneck
            available_direct(A→B)  = 1000 − 100        = 900
        """
        runner = _runner(actions_per_tick_max=50)

        scenario: dict[str, Any] = {
            "scenario_id": "s",
            "equivalents": ["UAH"],
            "participants": [
                {"id": "A", "type": "person", "groupId": "g1",
                 "behaviorProfileId": "default"},
                {"id": "B", "type": "person", "groupId": "g1",
                 "behaviorProfileId": "default"},
                {"id": "C", "type": "person", "groupId": "g1",
                 "behaviorProfileId": "default"},
            ],
            "behaviorProfiles": [
                {
                    "id": "default",
                    "props": {
                        "tx_rate": 1.0,
                        "equivalent_weights": {"UAH": 1.0},
                    },
                },
            ],
            "trustlines": [
                # creditor→debtor, payment direction is reversed:
                # B→A limit 1000  → candidate A→B
                {"equivalent": "UAH", "from": "B", "to": "A",
                 "limit": "1000", "status": "active"},
                # C→A limit 1000  → candidate A→C
                {"equivalent": "UAH", "from": "C", "to": "A",
                 "limit": "1000", "status": "active"},
                # A→B limit 1000  → candidate B→A
                {"equivalent": "UAH", "from": "A", "to": "B",
                 "limit": "1000", "status": "active"},
                # B→C limit 1000  → candidate C→B
                {"equivalent": "UAH", "from": "B", "to": "C",
                 "limit": "1000", "status": "active"},
            ],
        }

        # Asymmetric debt:
        # A→B: 100   A→C: 200   C→B: 700
        #
        # debt_out_agg(A, UAH) = 100 + 200 = 300
        # debt_in_agg(B, UAH) = 100 + 700 = 800
        #
        # For payment A→B:
        #   available_out  = 1000 − 300 = 700
        #   available_in   = 1000 − 800 = 200  ← tightest
        #   available_direct = 1000 − 100 = 900
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("A", "B", "UAH"): Decimal("100"),
            ("A", "C", "UAH"): Decimal("200"),
            ("C", "B", "UAH"): Decimal("700"),
        }

        run = _make_run(seed=1, tick_index=100, intensity_percent=100)
        planned = runner._plan_real_payments(
            run, scenario, debt_snapshot=debt_snapshot
        )

        assert len(planned) > 0, "Should produce at least some actions"

        # Check every A→B action is bounded by available_in = 200.
        for a in planned:
            if a.sender_pid == "A" and a.receiver_pid == "B":
                amt = Decimal(a.amount)
                assert amt <= Decimal("200"), (
                    f"A→B amount {amt} exceeds available_in bottleneck 200"
                )

    def test_load_debt_snapshot_maps_pids_correctly(self) -> None:
        """_load_debt_snapshot_by_pid returns PID-keyed dict (not UUID-keyed)."""
        runner = _runner()

        # UUIDs for participants
        uid_a = uuid.uuid4()
        uid_b = uuid.uuid4()
        participants = [(uid_a, "alice"), (uid_b, "bob")]
        equivalents = ["UAH"]

        # UUID for the equivalent
        eq_uid = uuid.uuid4()

        # --- Mock session ---
        # Two queries: Equivalent lookup → Debt lookup.

        class _EqRow:
            def __init__(self, id_: uuid.UUID, code: str) -> None:
                self.id = id_
                self.code = code

        class _DebtRow:
            def __init__(
                self,
                debtor_id: uuid.UUID,
                creditor_id: uuid.UUID,
                equivalent_id: uuid.UUID,
                total: Decimal,
            ) -> None:
                self.debtor_id = debtor_id
                self.creditor_id = creditor_id
                self.equivalent_id = equivalent_id
                self.total = total

        eq_rows = [_EqRow(eq_uid, "UAH")]
        debt_rows = [
            _DebtRow(uid_a, uid_b, eq_uid, Decimal("42.50")),
        ]

        # Build an async mock session that returns the right data for each
        # ``session.execute(...)`` call.
        call_count = 0

        async def _mock_execute(stmt):
            nonlocal call_count
            call_count += 1

            class _Result:
                def __init__(self, rows):
                    self._rows = rows

                def all(self):
                    return self._rows

            if call_count == 1:
                return _Result(eq_rows)
            return _Result(debt_rows)

        session = AsyncMock()
        session.execute = _mock_execute

        import asyncio

        snapshot = asyncio.get_event_loop().run_until_complete(
            runner._load_debt_snapshot_by_pid(session, participants, equivalents)
        )

        # Keys must be PID strings, not UUIDs.
        assert ("alice", "bob", "UAH") in snapshot
        assert snapshot[("alice", "bob", "UAH")] == Decimal("42.50")

        # No UUID keys.
        for key in snapshot:
            debtor, creditor, eq = key
            assert isinstance(debtor, str)
            assert isinstance(creditor, str)
            assert isinstance(eq, str)
