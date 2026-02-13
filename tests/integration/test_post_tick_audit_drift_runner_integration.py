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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.simulator.storage as simulator_storage
import app.db.session as app_db_session
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.core.simulator.real_tick_persistence import RealTickPersistence
from app.db.base import Base
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine


_TEST_DB_PATH = ".pytest_post_tick_audit_drift_runner.db"
_TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pubkey(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def audit_engine():
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass

    eng = create_async_engine(
        _TEST_DB_URL,
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    await eng.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass


@pytest_asyncio.fixture
async def audit_session_factory(audit_engine):
    return async_sessionmaker(
        bind=audit_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def _seed_triangle(session: AsyncSession) -> tuple[str, list[str]]:
    eq = Equivalent(code="UAH", is_active=True, metadata_={})
    session.add(eq)

    pids = ["p-alice", "p-bob", "p-carol"]
    parts: list[Participant] = []
    for pid in pids:
        parts.append(
            Participant(
                pid=pid,
                display_name=pid,
                public_key=_pubkey(pid),
                type="person",
                status="active",
                profile={},
            )
        )
    session.add_all(parts)
    await session.flush()

    # TrustLine direction: creditor(from) -> debtor(to). Payments are planned from debtor -> creditor.
    tl_pairs = [(0, 1), (1, 2), (2, 0)]
    tls: list[TrustLine] = []
    for a, b in tl_pairs:
        tls.append(
            TrustLine(
                from_participant_id=parts[a].id,
                to_participant_id=parts[b].id,
                equivalent_id=eq.id,
                limit=Decimal("1000"),
                status="active",
            )
        )
    session.add_all(tls)
    await session.commit()

    return eq.code, pids


@pytest.mark.asyncio
async def test_post_tick_audit_drift_emits_sse_and_persists_integrity_log(
    audit_session_factory,
    monkeypatch,
) -> None:
    # Seed DB
    async with audit_session_factory() as seed:
        eq_code, pids = await _seed_triangle(seed)

    # Patch app session factory so RealRunner uses our isolated DB.
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", audit_session_factory)

    # Stub storage writes; not relevant for this test.
    async def _noop_write_tick_metrics(**_kw):
        return None

    async def _noop_write_tick_bottlenecks(**_kw):
        return None

    async def _noop_sync_artifacts(_run):
        return None

    async def _noop_upsert_run(_run):
        return None

    monkeypatch.setattr(simulator_storage, "write_tick_metrics", _noop_write_tick_metrics)
    monkeypatch.setattr(simulator_storage, "write_tick_bottlenecks", _noop_write_tick_bottlenecks)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", _noop_sync_artifacts)
    monkeypatch.setattr(simulator_storage, "upsert_run", _noop_upsert_run)

    # Minimal scenario: 3 participants + 3 trustlines; planner will generate at least 1 action.
    scenario = {
        "equivalents": [eq_code],
        "participants": [{"id": pid} for pid in pids],
        "trustlines": [
            {"from": pids[0], "to": pids[1], "equivalent": eq_code, "limit": "1000", "status": "active"},
            {"from": pids[1], "to": pids[2], "equivalent": eq_code, "limit": "1000", "status": "active"},
            {"from": pids[2], "to": pids[0], "equivalent": eq_code, "limit": "1000", "status": "active"},
        ],
        "behaviorProfiles": [],
    }

    run = RunRecord(run_id="audit-drift", scenario_id="s1", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1
    run.sim_time_ms = 1000
    run.intensity_percent = 100
    run._real_seeded = True

    # Pre-load participants + equivalents to avoid seeding path.
    async with audit_session_factory() as tmp:
        rows = (
            await tmp.execute(select(Participant).where(Participant.pid.in_(pids)))
        ).scalars().all()
        run._real_participants = [(p.id, p.pid) for p in rows]
        run._real_equivalents = [eq_code]

    captured: list[dict] = []

    class _DummySse:
        def next_event_id(self, _run: RunRecord) -> str:
            _run._event_seq += 1
            return f"e{_run._event_seq}"

        def broadcast(self, _run_id: str, payload: dict) -> None:
            if isinstance(payload, dict):
                captured.append(payload)

    sse = _DummySse()

    class _DummyArtifacts:
        def write_real_tick_artifact(self, *a, **kw):
            return None

        def enqueue_event_artifact(self, *a, **kw):
            return None

    # Inject a controlled drift after persist_tick_tail commits, but before post-tick audit runs.
    orig_persist = RealTickPersistence.persist_tick_tail
    injected = {"done": False}

    async def _patched_persist_tick_tail(self, *, session, **kwargs) -> None:
        await orig_persist(self, session=session, **kwargs)

        if injected["done"]:
            return

        tx = (
            await session.execute(
                select(Transaction)
                .where(Transaction.type == "PAYMENT", Transaction.state == "COMMITTED")
                .limit(1)
                .execution_options(populate_existing=True)
            )
        ).scalars().first()
        if tx is None:
            return

        payload = tx.payload or {}
        from_pid = str(payload.get("from") or "").strip()
        to_pid = str(payload.get("to") or "").strip()
        eq = str(payload.get("equivalent") or "").strip().upper()
        if not from_pid or not to_pid or not eq:
            return

        eq_id = (
            await session.execute(select(Equivalent.id).where(Equivalent.code == eq))
        ).scalar_one_or_none()
        from_id = (
            await session.execute(select(Participant.id).where(Participant.pid == from_pid))
        ).scalar_one_or_none()
        to_id = (
            await session.execute(select(Participant.id).where(Participant.pid == to_pid))
        ).scalar_one_or_none()
        if eq_id is None or from_id is None or to_id is None:
            return

        drift_amt = Decimal("0.01")
        debt = (
            await session.execute(
                select(Debt)
                .where(
                    Debt.debtor_id == from_id,
                    Debt.creditor_id == to_id,
                    Debt.equivalent_id == eq_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if debt is None:
            session.add(
                Debt(
                    debtor_id=from_id,
                    creditor_id=to_id,
                    equivalent_id=eq_id,
                    amount=drift_amt,
                )
            )
        else:
            current = Decimal(str(debt.amount or 0))
            new_amount = current - drift_amt
            if new_amount <= 0:
                new_amount = current + drift_amt
            debt.amount = new_amount
            session.add(debt)

        injected["done"] = True
        await session.flush()

    monkeypatch.setattr(RealTickPersistence, "persist_tick_tail", _patched_persist_tick_tail)

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: run,
        get_scenario_raw=lambda _run_id: scenario,
        sse=sse,
        artifacts=_DummyArtifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: True,
        actions_per_tick_max=3,
        clearing_every_n_ticks=10_000,  # keep clearing out of the way
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("test_post_tick_audit_drift"),
    )

    await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=10.0)

    assert injected["done"] is True, "Expected drift injection to run"

    drift_events = [e for e in captured if isinstance(e, dict) and e.get("type") == "audit.drift"]
    assert drift_events, f"Expected at least one audit.drift event, got types={[e.get('type') for e in captured]}"
    assert any(e.get("source") == "post_tick_audit" for e in drift_events), drift_events

    # Verify audit log persisted.
    async with audit_session_factory() as verify:
        row = (
            await verify.execute(
                select(IntegrityAuditLog)
                .where(IntegrityAuditLog.operation_type == "SIMULATOR_AUDIT_DRIFT")
                .limit(1)
            )
        ).scalars().first()
        assert row is not None
        assert row.verification_passed is False
        assert isinstance(row.affected_participants, dict)
