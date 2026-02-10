"""Unit-tests for Phase 4: Flow Directionality, Periodicity, Reciprocity.

Covers:
  - Flow Directionality (4.1): _choose_receiver() prefers target group via flow_chains
  - Reciprocity Bonus (4.2): limit increased when reverse debt exists
  - Periodicity (4.3): stateless frequency filter based on amount vs p50

Uses the same lightweight mocking as test_warmup_and_capacity.py —
RealRunner with no DB, direct calls to _plan_real_payments().
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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
    tick_index: int = 100,
    intensity_percent: int = 100,
) -> RunRecord:
    """Create a RunRecord pre-configured for planner tests."""
    run = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run.seed = seed
    run.tick_index = tick_index
    run.intensity_percent = intensity_percent
    run.sim_time_ms = tick_index * 1000
    return run


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------


def _flow_scenario(
    *,
    flow_enabled: bool = True,
    default_affinity: float = 0.7,
    reciprocity_bonus: float = 0.0,
    household_flow_chains: list[Any] | None = None,
    household_flow_affinity: float | None = None,
) -> dict[str, Any]:
    """Scenario with 3 groups: household (H1, H2), retail (R1, R2), producer (P1).

    Full mesh of trustlines so every participant can reach every other
    (via direct neighbors and multi-hop BFS).  This ensures _choose_receiver()
    has a rich reachable set containing multiple groups.
    """
    participants = [
        {"id": "H1", "groupId": "household", "behaviorProfileId": "hp"},
        {"id": "H2", "groupId": "household", "behaviorProfileId": "hp"},
        {"id": "R1", "groupId": "retail", "behaviorProfileId": "rp"},
        {"id": "R2", "groupId": "retail", "behaviorProfileId": "rp"},
        {"id": "P1", "groupId": "producer", "behaviorProfileId": "pp"},
    ]

    hp_props: dict[str, Any] = {
        "tx_rate": 1.0,
        "equivalent_weights": {"UAH": 1.0},
    }
    if household_flow_chains is not None:
        hp_props["flow_chains"] = household_flow_chains
    if household_flow_affinity is not None:
        hp_props["flow_affinity"] = household_flow_affinity

    profiles = [
        {"id": "hp", "props": hp_props},
        {"id": "rp", "props": {"tx_rate": 1.0, "equivalent_weights": {"UAH": 1.0}}},
        {"id": "pp", "props": {"tx_rate": 1.0, "equivalent_weights": {"UAH": 1.0}}},
    ]

    # Full mesh: every ordered pair gets a trustline (creditor→debtor).
    all_pids = ["H1", "H2", "R1", "R2", "P1"]
    trustlines = []
    for frm in all_pids:
        for to in all_pids:
            if frm != to:
                trustlines.append(
                    {
                        "equivalent": "UAH",
                        "from": frm,
                        "to": to,
                        "limit": "1000",
                        "status": "active",
                    }
                )

    return {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": participants,
        "behaviorProfiles": profiles,
        "trustlines": trustlines,
        "settings": {
            "flow": {
                "enabled": flow_enabled,
                "default_affinity": default_affinity,
                "reciprocity_bonus": reciprocity_bonus,
            },
        },
    }


def _periodicity_scenario(
    *,
    periodicity_factor: float | None = None,
    tx_rate: float = 0.03,
) -> dict[str, Any]:
    """5-participant mesh with configurable periodicity_factor.

    Uses a low tx_rate so max_iters becomes the binding constraint when
    periodicity further rejects payments.  With limit=1000 and default
    p50=50, most randomly generated amounts are >> p50.
    """
    pids = [f"P{i}" for i in range(5)]
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
                        "limit": "1000",
                        "status": "active",
                    }
                )

    props: dict[str, Any] = {
        "tx_rate": tx_rate,
        "equivalent_weights": {"UAH": 1.0},
    }
    if periodicity_factor is not None:
        props["periodicity_factor"] = periodicity_factor

    return {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": participants,
        "behaviorProfiles": [{"id": "default", "props": props}],
        "trustlines": trustlines,
    }


def _reciprocity_scenario(
    *,
    reciprocity_bonus: float = 0.0,
) -> dict[str, Any]:
    """2-participant scenario (A ↔ B) for reciprocity bonus tests.

    Bidirectional trustlines with limit=100.  When combined with a
    debt_snapshot, capacity-aware reduction narrows the effective limit,
    and reciprocity_bonus can widen it again if reverse debt exists.
    """
    return {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": [
            {
                "id": "A",
                "type": "person",
                "groupId": "g1",
                "behaviorProfileId": "default",
            },
            {
                "id": "B",
                "type": "person",
                "groupId": "g1",
                "behaviorProfileId": "default",
            },
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
            # A credits B → payment direction B→A
            {
                "equivalent": "UAH",
                "from": "A",
                "to": "B",
                "limit": "100",
                "status": "active",
            },
            # B credits A → payment direction A→B
            {
                "equivalent": "UAH",
                "from": "B",
                "to": "A",
                "limit": "100",
                "status": "active",
            },
        ],
        "settings": {
            "flow": {
                "enabled": True,
                "default_affinity": 0.7,
                "reciprocity_bonus": reciprocity_bonus,
            },
        },
    }


# ===================================================================
# Flow Directionality (Phase 4.1)
# ===================================================================


class TestFlowDirectionality:
    """Tests for flow_chains-based receiver selection in _choose_receiver()."""

    def test_flow_directs_household_to_retail(self) -> None:
        """With affinity=1.0 and flow_chains=[["household","retail"]],
        all household senders must pick a retail receiver."""
        runner = _runner(actions_per_tick_max=100)
        scenario = _flow_scenario(
            flow_enabled=True,
            household_flow_chains=[["household", "retail"]],
            household_flow_affinity=1.0,
        )
        run = _make_run(seed=42)
        planned = runner._plan_real_payments(run, scenario)

        assert len(planned) > 0, "Should produce actions"

        household_pids = {"H1", "H2"}
        retail_pids = {"R1", "R2"}

        household_actions = [a for a in planned if a.sender_pid in household_pids]
        assert len(household_actions) > 0, "Should have household sender actions"

        for a in household_actions:
            assert a.receiver_pid in retail_pids, (
                f"Household sender {a.sender_pid} picked non-retail receiver "
                f"{a.receiver_pid} — with affinity=1.0, flow_chains should "
                f"restrict to {retail_pids}"
            )

    def test_flow_fallback_without_chains(self) -> None:
        """Profile without flow_chains falls back to standard receiver
        selection logic.  Planned actions should still be generated."""
        runner = _runner(actions_per_tick_max=100)
        scenario = _flow_scenario(
            flow_enabled=True,
            household_flow_chains=None,  # no chains → fallback
        )
        run = _make_run(seed=42)
        planned = runner._plan_real_payments(run, scenario)

        assert len(planned) > 0, (
            "Fallback (no flow_chains) should still produce actions"
        )

    def test_flow_affinity_zero_ignores_chains(self) -> None:
        """With flow_affinity=0.0, flow chains are never activated.

        Standard receiver selection distributes across all reachable groups,
        so at least one household sender should pick a non-retail receiver.
        """
        runner = _runner(actions_per_tick_max=100)
        scenario = _flow_scenario(
            flow_enabled=True,
            household_flow_chains=[["household", "retail"]],
            household_flow_affinity=0.0,
        )
        run = _make_run(seed=42)
        planned = runner._plan_real_payments(run, scenario)

        assert len(planned) > 0, "Should produce actions with affinity=0"

        household_pids = {"H1", "H2"}
        retail_pids = {"R1", "R2"}

        household_actions = [a for a in planned if a.sender_pid in household_pids]
        non_retail = [
            a for a in household_actions if a.receiver_pid not in retail_pids
        ]

        # With affinity=0, flow chains are bypassed.  Standard group-shuffle
        # logic picks from any reachable group.  Over ~40+ household actions,
        # the probability of *all* landing on retail is ~(1/3)^40 ≈ 0.
        assert len(non_retail) > 0, (
            "With affinity=0, at least one household sender should pick "
            "a non-retail receiver (standard logic distributes across groups)"
        )

    def test_flow_disabled_setting_skips_all(self) -> None:
        """With settings.flow.enabled=False, the entire flow block is
        skipped — even if flow_chains and affinity are set in profiles."""
        runner = _runner(actions_per_tick_max=100)
        scenario = _flow_scenario(
            flow_enabled=False,  # disabled
            household_flow_chains=[["household", "retail"]],
            household_flow_affinity=1.0,
        )
        run = _make_run(seed=42)
        planned = runner._plan_real_payments(run, scenario)

        assert len(planned) > 0, "Should produce actions when flow disabled"

        household_pids = {"H1", "H2"}
        retail_pids = {"R1", "R2"}

        household_actions = [a for a in planned if a.sender_pid in household_pids]
        non_retail = [
            a for a in household_actions if a.receiver_pid not in retail_pids
        ]

        # Flow disabled → standard logic → household senders are not
        # restricted to retail.
        assert len(non_retail) > 0, (
            "With flow disabled, household senders should not be restricted "
            "to retail receivers — standard logic applies"
        )


# ===================================================================
# Periodicity (Phase 4.3)
# ===================================================================


class TestPeriodicity:
    """Tests for the periodicity frequency filter in _plan_real_payments().

    The filter formula:
        accept_prob = 1 / (1 + log(max(amount/p50, 0.1)) * periodicity_factor)

    With factor=1.0 the block is skipped entirely.  Higher factors make
    large payments less likely to be planned.

    A low ``tx_rate=0.03`` is used so that ``max_iters`` (= 50 × target)
    becomes the binding constraint when periodicity rejects payments.
    """

    def test_periodicity_large_payments_less_frequent(self) -> None:
        """periodicity_factor=2.0 with amounts >> p50 (default 50) produces
        fewer planned actions than the baseline (factor=1.0, filter skipped).

        With tx_rate=0.03 and actions_per_tick_max=200:
          - factor=1.0: ~300 pass tx_rate → fills 200
          - factor=2.0: ~300 * avg_accept(≈0.2) ≈ 60 → far fewer
        """
        runner = _runner(actions_per_tick_max=200)

        scenario_factor2 = _periodicity_scenario(periodicity_factor=2.0)
        scenario_factor1 = _periodicity_scenario(periodicity_factor=1.0)

        run_f2 = _make_run(seed=42)
        run_f1 = _make_run(seed=42)

        planned_f2 = runner._plan_real_payments(run_f2, scenario_factor2)
        planned_f1 = runner._plan_real_payments(run_f1, scenario_factor1)

        assert len(planned_f2) < len(planned_f1), (
            f"periodicity_factor=2.0 should produce fewer actions than 1.0: "
            f"got {len(planned_f2)} vs {len(planned_f1)}"
        )

    def test_periodicity_factor_one_no_change(self) -> None:
        """periodicity_factor=1.0 (default) skips the filter entirely.

        An explicit factor=1.0 in profile props must produce identical
        results to having no periodicity_factor at all (both paths
        evaluate ``_periodicity_factor != 1.0`` as False).
        """
        runner = _runner(actions_per_tick_max=200)

        scenario_explicit = _periodicity_scenario(periodicity_factor=1.0)
        scenario_none = _periodicity_scenario(periodicity_factor=None)

        run_e = _make_run(seed=42)
        run_n = _make_run(seed=42)

        planned_e = runner._plan_real_payments(run_e, scenario_explicit)
        planned_n = runner._plan_real_payments(run_n, scenario_none)

        # Identical count.
        assert len(planned_e) == len(planned_n), (
            f"factor=1.0 should be identical to no factor: "
            f"{len(planned_e)} vs {len(planned_n)}"
        )

        # Identical actions (sender, receiver, amount).
        actions_e = [
            (a.sender_pid, a.receiver_pid, a.amount) for a in planned_e
        ]
        actions_n = [
            (a.sender_pid, a.receiver_pid, a.amount) for a in planned_n
        ]
        assert actions_e == actions_n, (
            "Planned actions should be identical for factor=1.0 and no factor"
        )

    def test_periodicity_small_payments_more_frequent(self) -> None:
        """periodicity_factor=0.5 is less strict than factor=2.0, so it
        lets more payments through the filter.

        The formula denominator ``1 + log(a/p50) * factor`` shrinks more
        slowly with factor=0.5 than with 2.0, yielding higher accept_prob.
        """
        runner = _runner(actions_per_tick_max=200)

        scenario_half = _periodicity_scenario(periodicity_factor=0.5)
        scenario_double = _periodicity_scenario(periodicity_factor=2.0)

        run_h = _make_run(seed=42)
        run_d = _make_run(seed=42)

        planned_half = runner._plan_real_payments(run_h, scenario_half)
        planned_double = runner._plan_real_payments(run_d, scenario_double)

        assert len(planned_half) > len(planned_double), (
            f"factor=0.5 should produce more actions than factor=2.0: "
            f"got {len(planned_half)} vs {len(planned_double)}"
        )


# ===================================================================
# Reciprocity Bonus (Phase 4.2)
# ===================================================================


class TestReciprocityBonus:
    """Tests for reciprocity bonus limit increase in _plan_real_payments().

    Setup: 2 participants A ↔ B, trustline limit=100 each direction.
    debt_snapshot provides existing debt so capacity is narrowed:

        A→B edge: used=90, available=10 (without bonus)
        B→A reverse debt=50

    With reciprocity_bonus=1.0:
        limit_A→B = 10 * (1 + 1.0) = 20

    This allows amounts up to 20 for A→B, exceeding the non-bonus cap of 10.
    """

    def test_reciprocity_increases_limit_with_reverse_debt(self) -> None:
        """With reciprocity_bonus=1.0 and reverse debt (B→A = 50),
        the A→B limit doubles from 10 to 20.  At least one A→B amount
        must exceed the non-bonus capacity of 10."""
        runner = _runner(actions_per_tick_max=50)
        scenario = _reciprocity_scenario(reciprocity_bonus=1.0)

        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("A", "B", "UAH"): Decimal("90"),  # A owes B 90 of 100
            ("B", "A", "UAH"): Decimal("50"),  # reverse debt → triggers bonus
        }

        run = _make_run(seed=42)
        planned = runner._plan_real_payments(
            run, scenario, debt_snapshot=debt_snapshot
        )

        assert len(planned) > 0, "Should produce actions"

        a_to_b = [
            a for a in planned
            if a.sender_pid == "A" and a.receiver_pid == "B"
        ]
        assert len(a_to_b) > 0, "Should have A→B actions"

        max_amount = max(Decimal(a.amount) for a in a_to_b)
        assert max_amount > Decimal("10"), (
            f"With reciprocity bonus=1.0 and reverse debt, A→B limit should "
            f"be ~20 (doubled from 10), but max amount is {max_amount}"
        )

    def test_reciprocity_no_reverse_debt_no_bonus(self) -> None:
        """Without reverse debt (no B→A entry in snapshot), no bonus is
        applied even though reciprocity_bonus=1.0.  All A→B amounts
        must stay within the available capacity of 10."""
        runner = _runner(actions_per_tick_max=50)
        scenario = _reciprocity_scenario(reciprocity_bonus=1.0)

        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("A", "B", "UAH"): Decimal("90"),
            # No (B, A, UAH) → no reverse debt for A→B direction
        }

        run = _make_run(seed=42)
        planned = runner._plan_real_payments(
            run, scenario, debt_snapshot=debt_snapshot
        )

        assert len(planned) > 0, "Should produce actions"

        a_to_b = [
            a for a in planned
            if a.sender_pid == "A" and a.receiver_pid == "B"
        ]
        for a in a_to_b:
            amt = Decimal(a.amount)
            assert amt <= Decimal("10"), (
                f"Without reverse debt, A→B amount {amt} should not exceed "
                f"available capacity 10"
            )

    def test_reciprocity_disabled_no_bonus(self) -> None:
        """With reciprocity_bonus=0, reverse debt is ignored entirely.
        All A→B amounts must stay within the available capacity of 10."""
        runner = _runner(actions_per_tick_max=50)
        scenario = _reciprocity_scenario(reciprocity_bonus=0.0)

        debt_snapshot: dict[tuple[str, str, str], Decimal] = {
            ("A", "B", "UAH"): Decimal("90"),
            ("B", "A", "UAH"): Decimal("50"),  # reverse exists but bonus=0
        }

        run = _make_run(seed=42)
        planned = runner._plan_real_payments(
            run, scenario, debt_snapshot=debt_snapshot
        )

        assert len(planned) > 0, "Should produce actions"

        a_to_b = [
            a for a in planned
            if a.sender_pid == "A" and a.receiver_pid == "B"
        ]
        for a in a_to_b:
            amt = Decimal(a.amount)
            assert amt <= Decimal("10"), (
                f"With bonus=0, A→B amount {amt} should not exceed "
                f"available capacity 10 (reverse debt ignored)"
            )
