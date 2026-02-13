"""System-level A/B benchmark: static vs adaptive clearing policy (§7.5.4.2).

Runs RealRunner on an isolated SQLite DB with a fixed seed and compares
static vs adaptive across non-flaky invariants:
  - budgets respected
  - errors_total == 0
  - adaptive does not degrade committed_rate beyond EPS
  - adaptive does not increase no_capacity_rate beyond EPS

Marked @pytest.mark.slow — run separately: pytest -m slow
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WARMUP_TICKS = 10
_TOTAL_TICKS = 40  # keep moderate for CI speed
_EPS_COMMITTED_RATE = 0.15  # adaptive may be at most 15pp worse
_EPS_NO_CAPACITY_RATE = 0.15
_SEED = 42
_INTENSITY = 100
# Budget guardrails: adaptive clearing budget must not exceed these
_MAX_CLEARING_TIME_BUDGET_MS = 250
_MAX_CLEARING_DEPTH = 6


# ---------------------------------------------------------------------------
# DB fixture (fresh per-test)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def ab_db():
    """Create a fresh SQLite engine + schema for A/B test."""
    db_path = ".pytest_ab_benchmark.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(db_path + suffix).unlink(missing_ok=True)
        except Exception:
            pass

    eng = create_async_engine(url, echo=False, poolclass=NullPool, connect_args={"timeout": 5})
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    yield eng, factory, db_path

    await eng.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(db_path + suffix).unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now():
    return datetime.now(timezone.utc)

def _make_pid(name: str) -> str:
    return f"p-{name}"

def _pubkey(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


async def _seed_network(session: AsyncSession) -> tuple[str, list[str]]:
    """Seed a 5-participant ring network with debts."""
    eq = Equivalent(code="UAH", is_active=True, metadata_={})
    session.add(eq)

    names = ["alice", "bob", "carol", "dave", "eve"]
    parts: list[Participant] = []
    for n in names:
        p = Participant(
            pid=_make_pid(n), display_name=n.title(),
            public_key=_pubkey(n), type="person", status="active", profile={},
        )
        session.add(p)
        parts.append(p)

    await session.flush()

    # Ring topology: 0→1→2→3→4→0
    for i in range(len(parts)):
        j = (i + 1) % len(parts)
        session.add(TrustLine(
            from_participant_id=parts[i].id, to_participant_id=parts[j].id,
            equivalent_id=eq.id, limit=Decimal("500.00"), status="active",
            policy={"auto_clearing": True, "can_be_intermediate": True},
        ))
        session.add(Debt(
            debtor_id=parts[i].id, creditor_id=parts[j].id,
            equivalent_id=eq.id, amount=Decimal("30.00"),
        ))

    await session.commit()
    return "UAH", [p.pid for p in parts]


class _DummySse:
    def __init__(self, *, get_tick):
        self.events: list[dict] = []
        self._get_tick = get_tick
    def next_event_id(self, run):
        run._event_seq += 1
        return f"e{run._event_seq}"
    def broadcast(self, run_id, payload):
        # Attach the current tick at broadcast time so warmup filtering can be
        # done by tick/time, not by "first N clearing events".
        if isinstance(payload, dict):
            payload = {**payload, "_tick": int(self._get_tick() or 0)}
        self.events.append(payload)


class _DummyArtifacts:
    def write_real_tick_artifact(self, *a, **kw): pass
    def enqueue_event_artifact(self, *a, **kw): pass


async def _anoop(*a, **kw): pass


async def _run_benchmark(
    factory,
    monkeypatch,
    *,
    policy: str,
    eq_code: str,
    pids: list[str],
    scenario: dict,
) -> dict:
    """Run TOTAL_TICKS ticks and collect summary metrics."""
    import app.db.session as app_db_session
    import app.core.simulator.storage as simulator_storage

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    monkeypatch.setattr(simulator_storage, "write_tick_metrics", _anoop)
    monkeypatch.setattr(simulator_storage, "write_tick_bottlenecks", _anoop)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", _anoop)
    monkeypatch.setattr(simulator_storage, "upsert_run", _anoop)

    monkeypatch.setenv("SIMULATOR_CLEARING_POLICY", policy)
    if policy == "adaptive":
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS", "5")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH", "0.40")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW", "0.15")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS", "3")

    run = RunRecord(run_id=f"ab-{policy}", scenario_id="s1", mode="real", state="running")
    run.seed = _SEED
    run.tick_index = 0
    run.sim_time_ms = 0
    run.intensity_percent = _INTENSITY
    run._real_seeded = True

    async with factory() as tmp:
        from sqlalchemy import select
        rows = (await tmp.execute(
            select(Participant).where(Participant.pid.in_(pids))
        )).scalars().all()
        run._real_participants = [(p.id, p.pid) for p in rows]
        run._real_equivalents = [eq_code]

    sse = _DummySse(get_tick=lambda: int(run.tick_index or 0))
    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _: run,
        get_scenario_raw=lambda _: scenario,
        sse=sse,
        artifacts=_DummyArtifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _: None,
        db_enabled=lambda: True,
        actions_per_tick_max=5,
        clearing_every_n_ticks=10,  # static fires every 10
        real_max_consec_tick_failures_default=5,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=100,
        logger=logging.getLogger(f"ab_{policy}"),
    )
    runner._real_clearing_time_budget_ms = 2000

    clearing_override_calls: list[tuple[int | None, int | None]] = []
    _orig_tick_real_mode_clearing = runner.tick_real_mode_clearing

    async def _spy_tick_real_mode_clearing(
        session,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        *,
        time_budget_ms_override: int | None = None,
        max_depth_override: int | None = None,
    ) -> dict[str, float]:
        clearing_override_calls.append((time_budget_ms_override, max_depth_override))
        return await _orig_tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            time_budget_ms_override=time_budget_ms_override,
            max_depth_override=max_depth_override,
        )

    runner.tick_real_mode_clearing = _spy_tick_real_mode_clearing  # type: ignore[assignment]

    metrics = {
        "policy": policy,
        "committed": 0,
        "committed_after_warmup": 0,
        "rejected": 0,
        "rejected_after_warmup": 0,
        "no_capacity_count": 0,
        "no_capacity_after_warmup": 0,
        "errors_total": 0,
        "clearing_count": 0,
        "clearing_after_warmup": 0,
        "clearing_override_calls": clearing_override_calls,
    }

    for tick in range(_TOTAL_TICKS):
        run.tick_index = tick
        run.sim_time_ms = tick * 1000
        try:
            await asyncio.wait_for(runner.tick_real_mode(f"ab-{policy}"), timeout=10.0)
        except asyncio.TimeoutError:
            pytest.fail(f"Deadlock at tick {tick} (policy={policy})")
        except Exception:
            metrics["errors_total"] += 1

    for evt in sse.events:
        if not isinstance(evt, dict):
            continue

        evt_type = evt.get("type", "")
        tick = int(evt.get("_tick", 0) or 0)
        in_warmup = tick < int(_WARMUP_TICKS)

        if evt_type == "clearing.done":
            metrics["clearing_count"] += 1
            if not in_warmup:
                metrics["clearing_after_warmup"] += 1

        elif evt_type == "tx.updated":
            # tx.updated is emitted only for COMMITTED transactions
            metrics["committed"] += 1
            if not in_warmup:
                metrics["committed_after_warmup"] += 1

        elif evt_type == "tx.failed":
            # tx.failed covers both rejected and errors; for this benchmark we treat
            # any tx.failed as a rejection attempt and count no_capacity via error.code.
            metrics["rejected"] += 1
            if not in_warmup:
                metrics["rejected_after_warmup"] += 1

            err = evt.get("error") or {}
            err_code = (err.get("code") or "") if isinstance(err, dict) else ""
            if err_code == "ROUTING_NO_CAPACITY":
                metrics["no_capacity_count"] += 1
                if not in_warmup:
                    metrics["no_capacity_after_warmup"] += 1

    return metrics


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.asyncio
async def test_adaptive_does_not_degrade_vs_static(ab_db, monkeypatch):
    """A/B comparison: adaptive must not significantly degrade committed_rate
    or increase no_capacity_rate compared to static baseline."""

    engine, factory, db_path = ab_db

    async with factory() as session:
        eq_code, pids = await _seed_network(session)

    scenario = {
        "equivalents": [eq_code],
        "participants": [{"id": pid} for pid in pids],
        "trustlines": [
            {"from": pids[i], "to": pids[(i+1) % len(pids)], "equivalent": eq_code, "limit": "500", "status": "active"}
            for i in range(len(pids))
        ],
        "behaviorProfiles": [],
    }

    # Run static baseline
    static_metrics = await _run_benchmark(
        factory, monkeypatch, policy="static",
        eq_code=eq_code, pids=pids, scenario=scenario,
    )

    # Re-seed DB (clean state for adaptive run)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await _seed_network(session)

    # Run adaptive
    adaptive_metrics = await _run_benchmark(
        factory, monkeypatch, policy="adaptive",
        eq_code=eq_code, pids=pids, scenario=scenario,
    )

    # ── Assertions (non-flaky invariants) ───────────────────────
    # 1. Errors must be 0
    assert static_metrics["errors_total"] == 0, f"Static had errors: {static_metrics}"
    assert adaptive_metrics["errors_total"] == 0, f"Adaptive had errors: {adaptive_metrics}"

    # 1.5. Ensure benchmark actually observed meaningful data (regression guard)
    static_attempted = static_metrics["committed"] + static_metrics["rejected"]
    adaptive_attempted = adaptive_metrics["committed"] + adaptive_metrics["rejected"]
    assert static_attempted > 0, f"Static benchmark produced no tx events: {static_metrics}"
    assert adaptive_attempted > 0, f"Adaptive benchmark produced no tx events: {adaptive_metrics}"

    # 2. Compute rates (after warmup for more stable comparison)
    static_attempted_aw = static_metrics["committed_after_warmup"] + static_metrics["rejected_after_warmup"]
    adaptive_attempted_aw = adaptive_metrics["committed_after_warmup"] + adaptive_metrics["rejected_after_warmup"]

    # Also compute total rates as fallback
    static_attempted = static_attempted
    adaptive_attempted = adaptive_attempted

    if static_attempted_aw > 0 and adaptive_attempted_aw > 0:
        static_committed_rate = static_metrics["committed_after_warmup"] / static_attempted_aw
        adaptive_committed_rate = adaptive_metrics["committed_after_warmup"] / adaptive_attempted_aw

        # Use ROUTING_NO_CAPACITY specifically, not all rejections
        static_no_cap_rate = static_metrics["no_capacity_after_warmup"] / static_attempted_aw
        adaptive_no_cap_rate = adaptive_metrics["no_capacity_after_warmup"] / adaptive_attempted_aw

        # Adaptive should not degrade committed_rate by more than EPS
        assert adaptive_committed_rate >= static_committed_rate - _EPS_COMMITTED_RATE, (
            f"Adaptive degraded committed_rate too much (after warmup): "
            f"{adaptive_committed_rate:.3f} vs static {static_committed_rate:.3f} "
            f"(EPS={_EPS_COMMITTED_RATE})"
        )

        # Adaptive should not increase no_capacity_rate by more than EPS
        assert adaptive_no_cap_rate <= static_no_cap_rate + _EPS_NO_CAPACITY_RATE, (
            f"Adaptive increased no_capacity_rate too much (after warmup): "
            f"{adaptive_no_cap_rate:.3f} vs static {static_no_cap_rate:.3f} "
            f"(EPS={_EPS_NO_CAPACITY_RATE})"
        )
    elif static_attempted > 0 and adaptive_attempted > 0:
        # Fallback: use total counts if warmup filtering yields no data
        static_committed_rate = static_metrics["committed"] / static_attempted
        adaptive_committed_rate = adaptive_metrics["committed"] / adaptive_attempted
        static_no_cap_rate = static_metrics["no_capacity_count"] / static_attempted
        adaptive_no_cap_rate = adaptive_metrics["no_capacity_count"] / adaptive_attempted

        assert adaptive_committed_rate >= static_committed_rate - _EPS_COMMITTED_RATE, (
            f"Adaptive degraded committed_rate: "
            f"{adaptive_committed_rate:.3f} vs static {static_committed_rate:.3f}"
        )
        assert adaptive_no_cap_rate <= static_no_cap_rate + _EPS_NO_CAPACITY_RATE, (
            f"Adaptive increased no_capacity_rate: "
            f"{adaptive_no_cap_rate:.3f} vs static {static_no_cap_rate:.3f}"
        )

    # 3. Budget guardrail: verify adaptive clearing time budget and depth
    #    are within configured limits (checked by intercepting per-call overrides)
    assert _MAX_CLEARING_TIME_BUDGET_MS >= 50, "Sanity: budget max is reasonable"
    assert _MAX_CLEARING_DEPTH >= 3, "Sanity: depth max is reasonable"

    overrides = adaptive_metrics.get("clearing_override_calls") or []
    seen_budgets = [b for (b, _) in overrides if b is not None]
    seen_depths = [d for (_, d) in overrides if d is not None]

    # We expect at least one adaptive per-eq clearing call during the benchmark.
    assert seen_budgets, "Adaptive benchmark did not record any time_budget_ms_override"
    assert seen_depths, "Adaptive benchmark did not record any max_depth_override"

    assert all(1 <= int(b) <= int(_MAX_CLEARING_TIME_BUDGET_MS) for b in seen_budgets)
    assert all(1 <= int(d) <= int(_MAX_CLEARING_DEPTH) for d in seen_depths)

    # 4. Log report for human inspection
    print("\n=== A/B Benchmark Report ===")
    print(f"Static:   {static_metrics}")
    print(f"Adaptive: {adaptive_metrics}")
    if static_attempted > 0 and adaptive_attempted > 0:
        print(f"Static committed_rate (total):      {static_metrics['committed'] / static_attempted:.3f}")
        print(f"Adaptive committed_rate (total):     {adaptive_metrics['committed'] / adaptive_attempted:.3f}")
        print(f"Static no_capacity_count (total):    {static_metrics['no_capacity_count']}")
        print(f"Adaptive no_capacity_count (total):  {adaptive_metrics['no_capacity_count']}")
    if static_attempted_aw > 0 and adaptive_attempted_aw > 0:
        print(f"Static committed_rate (after warmup):  {static_committed_rate:.3f}")
        print(f"Adaptive committed_rate (after warmup): {adaptive_committed_rate:.3f}")
        print(f"Static no_cap_rate (after warmup):     {static_no_cap_rate:.3f}")
        print(f"Adaptive no_cap_rate (after warmup):   {adaptive_no_cap_rate:.3f}")
