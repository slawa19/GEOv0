"""Unit-tests for Phase 2 Trust Drift.

Covers:
  - TrustDriftConfig.from_scenario() — parsing trust_drift settings
  - _init_trust_drift() — initializing edge clearing history from scenario
  - _apply_trust_growth() — limit growth after clearing
  - _apply_trust_decay() — limit decay for overloaded edges

A lightweight RealRunnerImpl (no SSE, no artifacts). The growth and decay calls run on a real mode-A
session (`_drift_session`) since programme 021 stage 1 moved their writes onto the trust-line
service; until then they ran on `AsyncMock` sessions answering statements by position.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.payments.router import PaymentRouter
from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.real_runner_impl import RealRunnerImpl


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
) -> RealRunnerImpl:
    """Create a lightweight RealRunnerImpl for unit tests (no DB, no SSE)."""
    _scenario = scenario or {}
    return RealRunnerImpl(
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


async def _owes_alice(db_session, amount: str) -> None:
    """029 S3 (`T2993`): the decay reads the debt behind the line lock, not the tick's snapshot - so bob's debt to
    alice is a row. Without it the two "skips" tests below would pass on an edge that carries no debt at all."""

    from sqlalchemy import select

    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from tests.debt_setup import debt_fixture_setup

    eq_id = await db_session.scalar(select(Equivalent.id).where(Equivalent.code == "UAH"))
    async with debt_fixture_setup(db_session, label="trust-drift"):
        db_session.add(Debt(debtor_id=_UID_BOB, creditor_id=_UID_ALICE, equivalent_id=eq_id, amount=Decimal(amount)))
    await db_session.commit()


async def _drift_session(
    db_session,
    *,
    alice_bob_limit: float = 1000.0,
    bob_carol_limit: float = 500.0,
):
    """The session a drift call runs on: a REAL one (mode A, rolled back by the fixture).

    Programme 021, stage 1: growth and decay write through `TrustLineService`'s internal path, which reads the
    trust line it changes, the debt it must stay above, and computes integrity checkpoints - a stub answering
    `execute()` by call position can no longer stand in for the database. Until then this module answered the
    engine's statements from `AsyncMock` sessions by their order. The assertions below are unchanged; only the
    stand moved. The world is the one those stubs described: alice, bob, carol with the fixed ids of
    `_make_run()`, equivalent UAH, the active lines alice -> bob and bob -> carol, no debts.

    Called again on the same session, it only resets the alice -> bob limit (the second call of
    `test_growth_updates_clearing_history`).
    """

    from sqlalchemy import select

    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine

    existing = await db_session.get(Participant, _UID_ALICE)
    if existing is None:
        eq = (await db_session.execute(select(Equivalent).where(Equivalent.code == "UAH"))).scalar_one_or_none()
        if eq is None:
            eq = Equivalent(id=_UID_EQ_UAH, code="UAH", precision=2, is_active=True, metadata_={})
            db_session.add(eq)
        for uid, pid in ((_UID_ALICE, "alice"), (_UID_BOB, "bob"), (_UID_CAROL, "carol")):
            db_session.add(
                Participant(id=uid, pid=pid, display_name=pid, public_key=f"pk_td_{pid}", type="person",
                            status="active", profile={})
            )
        await db_session.flush()
        db_session.add_all([
            TrustLine(from_participant_id=_UID_ALICE, to_participant_id=_UID_BOB, equivalent_id=eq.id,
                      limit=Decimal(str(alice_bob_limit)), status="active", policy={}),
            TrustLine(from_participant_id=_UID_BOB, to_participant_id=_UID_CAROL, equivalent_id=eq.id,
                      limit=Decimal(str(bob_carol_limit)), status="active", policy={}),
        ])
        await db_session.commit()
        return db_session

    line = (
        await db_session.execute(
            select(TrustLine).where(
                TrustLine.from_participant_id == _UID_ALICE,
                TrustLine.to_participant_id == _UID_BOB,
                TrustLine.status == "active",
            )
        )
    ).scalar_one()
    line.limit = Decimal(str(alice_bob_limit))
    await db_session.commit()
    return db_session


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


# ===================================================================
# _apply_trust_growth() tests
# ===================================================================


class TestApplyTrustGrowth:
    """Tests for _apply_trust_growth() — limit growth after clearing."""

    async def test_growth_increases_limit_after_clearing(self, db_session) -> None:
        """Cleared edge → limit increased by growth_rate."""
        scenario = _make_scenario(trust_drift={"enabled": True, "growth_rate": 0.05})
        runner = _make_runner(scenario=scenario)
        run = _make_run()

        # Init trust drift
        runner._init_trust_drift(run, scenario)

        current_limit = 1000.0
        session = await _drift_session(db_session, alice_bob_limit=current_limit)
        observed_limit_at_commit: list[float] = []

        async def commit() -> None:
            observed_limit_at_commit.append(float(scenario["trustlines"][0]["limit"]))

        session.commit = AsyncMock(side_effect=commit)
        PaymentRouter._graph_cache["UAH"] = object()

        touched_edges: set[tuple[str, str]] = {("alice", "bob")}

        res = await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=5,
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
        # T1514: compare the VALUE, not the representation. The scenario used to carry the
        # limit as `float(...)`, and these assertions pinned that - `1050.0 == 1050.0`. Money
        # that is written back to `trust_lines.limit` must not pass through binary floating
        # point, so the entry is a string now; the number it denotes is unchanged.
        assert Decimal(str(ab_tl["limit"])) == Decimal(str(expected_limit))
        assert observed_limit_at_commit == [1000.0]
        assert "UAH" not in PaymentRouter._graph_cache

    async def test_growth_commit_failure_keeps_scenario_and_cache_unchanged(self, db_session) -> None:
        scenario = _make_scenario(trust_drift={"enabled": True, "growth_rate": 0.05})
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)
        session = await _drift_session(db_session, alice_bob_limit=1000.0)
        session.commit = AsyncMock(side_effect=RuntimeError("growth commit failed"))
        session.rollback = AsyncMock()
        PaymentRouter._graph_cache["UAH"] = object()

        with pytest.raises(RuntimeError, match="growth commit failed"):
            await runner._apply_trust_growth(
                run,
                session,
                {("alice", "bob")},
                "UAH",
                tick_index=8,
            )

        assert scenario["trustlines"][0]["limit"] == 1000
        assert "UAH" in PaymentRouter._graph_cache
        session.rollback.assert_awaited_once()
        history = run._edge_clearing_history["alice:bob:UAH"]
        assert history.clearing_count == 1
        assert history.last_clearing_tick == 8
        PaymentRouter._graph_cache.pop("UAH", None)

    async def test_growth_cancellation_after_commit_applies_committed_effects(self, db_session) -> None:
        scenario = _make_scenario(trust_drift={"enabled": True, "growth_rate": 0.05})
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)
        session = await _drift_session(db_session, alice_bob_limit=1000.0)
        commit_started = asyncio.Event()
        release_commit = asyncio.Event()

        async def commit() -> None:
            commit_started.set()
            await release_commit.wait()

        session.commit = AsyncMock(side_effect=commit)
        PaymentRouter._graph_cache["UAH"] = object()
        task = asyncio.create_task(
            runner._apply_trust_growth(
                run,
                session,
                {("alice", "bob")},
                "UAH",
                tick_index=9,
            )
        )

        await commit_started.wait()
        task.cancel("growth cancelled")
        release_commit.set()
        with pytest.raises(asyncio.CancelledError, match="growth cancelled"):
            await task

        # T1514: compare the VALUE, not the representation. The scenario used to carry the
        # limit as `float(...)`, and these assertions pinned that - `1050.0 == 1050.0`. Money
        # that is written back to `trust_lines.limit` must not pass through binary floating
        # point, so the entry is a string now; the number it denotes is unchanged.
        assert Decimal(str(scenario["trustlines"][0]["limit"])) == Decimal("1050")
        assert "UAH" not in PaymentRouter._graph_cache

    async def test_growth_capped_by_max_growth(self, db_session) -> None:
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
        session = await _drift_session(db_session, alice_bob_limit=current_limit)

        touched_edges: set[tuple[str, str]] = {("alice", "bob")}

        res = await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=5,
        )

        assert res.updated_count == 1
        # new_limit = min(1490 * 1.05, 1000 * 1.5) = min(1564.5, 1500) = 1500.0
        cap = run._edge_clearing_history["alice:bob:UAH"].original_limit * Decimal("1.5")
        s_tls = scenario["trustlines"]
        ab_tl = next(
            t for t in s_tls
            if t["from"] == "alice" and t["to"] == "bob"
        )
        # T1514: compare the VALUE, not the representation. The scenario used to carry the
        # limit as `float(...)`, and these assertions pinned that - `1050.0 == 1050.0`. Money
        # that is written back to `trust_lines.limit` must not pass through binary floating
        # point, so the entry is a string now; the number it denotes is unchanged.
        assert Decimal(str(ab_tl["limit"])) == Decimal(cap).quantize(Decimal("1E-8"))

    async def test_growth_updates_clearing_history(self, db_session) -> None:
        """After growth: clearing_count += 1, last_clearing_tick updated, and no float volume (028 F-028-33)."""
        scenario = _make_scenario(trust_drift={"enabled": True, "growth_rate": 0.05})
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        session = await _drift_session(db_session, alice_bob_limit=1000.0)
        touched_edges: set[tuple[str, str]] = {("alice", "bob")}
        tick = 7

        await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=tick,
        )

        hist = run._edge_clearing_history["alice:bob:UAH"]
        assert hist.clearing_count == 1
        assert hist.last_clearing_tick == tick

        # Second growth call — history accumulates
        session2 = await _drift_session(db_session, alice_bob_limit=1050.0)
        tick2 = 12

        await runner._apply_trust_growth(
            run, session2, touched_edges, "UAH", tick_index=tick2,
        )

        assert hist.clearing_count == 2
        assert hist.last_clearing_tick == tick2

    async def test_growth_skipped_when_disabled(self) -> None:
        """enabled=False → 0 updated, no DB calls."""
        scenario = _make_scenario()  # no trust_drift → disabled
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)

        session = AsyncMock()

        touched_edges: set[tuple[str, str]] = {("alice", "bob")}

        res = await runner._apply_trust_growth(
            run, session, touched_edges, "UAH", tick_index=5,
        )

        assert res.updated_count == 0
        # Session should not have been used for execute
        session.execute.assert_not_called()


# ===================================================================
# _apply_trust_decay() tests
# ===================================================================


class TestApplyTrustDecay:
    """Tests for _apply_trust_decay() — limit decay for overloaded edges."""

    async def test_decay_reduces_limit_when_overloaded(self, db_session) -> None:
        """debt/limit ≥ 0.8 → limit decreased by decay_rate."""
        scenario = _make_scenario(trust_drift={
            "enabled": True,
            "decay_rate": 0.02,
            "overload_threshold": 0.8,
        })
        runner = _make_runner(scenario=scenario)
        run = _make_run()
        runner._init_trust_drift(run, scenario)


        session = await _drift_session(db_session)
        await _owes_alice(session, "850")
        PaymentRouter._graph_cache["UAH"] = object()

        res = await runner._apply_trust_decay(
            run, session, tick_index=10, scenario=scenario,
        )

        assert res.updated_count == 1
        assert scenario["trustlines"][0]["limit"] == 1000
        assert "UAH" in PaymentRouter._graph_cache
        runner._trust_drift_engine.apply_committed_effects(
            scenario=scenario,
            result=res,
        )
        assert "UAH" not in PaymentRouter._graph_cache
        # new_limit = max(1000 * (1 - 0.02), 1000 * 0.3) = max(980, 300) = 980.0
        expected = round(1000.0 * (1 - 0.02), 2)
        ab_tl = next(
            t for t in scenario["trustlines"]
            if t["from"] == "alice" and t["to"] == "bob"
        )
        # T1514: compare the VALUE, not the representation. The scenario used to carry the
        # limit as `float(...)`, and these assertions pinned that - `1050.0 == 1050.0`. Money
        # that is written back to `trust_lines.limit` must not pass through binary floating
        # point, so the entry is a string now; the number it denotes is unchanged.
        assert Decimal(str(ab_tl["limit"])) == Decimal(str(expected))

    async def test_decay_floored_by_min_limit_ratio(self, db_session) -> None:
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


        session = await _drift_session(db_session, alice_bob_limit=350.0)
        await _owes_alice(session, "280")

        res = await runner._apply_trust_decay(
            run, session, tick_index=10, scenario=scenario,
        )

        assert res.updated_count == 1
        assert scenario["trustlines"][0]["limit"] == 350
        runner._trust_drift_engine.apply_committed_effects(
            scenario=scenario,
            result=res,
        )
        # new_limit = max(350 * (1 - 0.5), 1000 * 0.3) = max(175, 300) = 300.0
        ab_tl = scenario["trustlines"][0]
        # T1514: compare the VALUE, not the representation. The scenario used to carry the
        # limit as `float(...)`, and these assertions pinned that - `1050.0 == 1050.0`. Money
        # that is written back to `trust_lines.limit` must not pass through binary floating
        # point, so the entry is a string now; the number it denotes is unchanged.
        assert Decimal(str(ab_tl["limit"])) == Decimal("300")

    async def test_decay_skips_underloaded_edges(self, db_session) -> None:
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


        session = await _drift_session(db_session)
        await _owes_alice(session, "500")

        res = await runner._apply_trust_decay(
            run, session, tick_index=10, scenario=scenario,
        )

        assert res.updated_count == 0
        # Limit should remain unchanged
        ab_tl = next(
            t for t in scenario["trustlines"]
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == original_limit

    async def test_decay_skips_just_cleared_edges(self, db_session) -> None:
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


        session = await _drift_session(db_session)
        await _owes_alice(session, "850")

        # Call with tick_index = 10 (same as last_clearing_tick)
        res = await runner._apply_trust_decay(
            run, session, tick_index=10, scenario=scenario,
        )

        assert res.updated_count == 0
        ab_tl = next(
            t for t in scenario["trustlines"]
            if t["from"] == "alice" and t["to"] == "bob"
        )
        assert ab_tl["limit"] == original_limit
