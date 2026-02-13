from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.db.base import Base
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException


_TEST_DB_PATH = ".pytest_audit_drift_delta_check_sse.db"
_TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pubkey(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def engine():
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
async def session_factory(engine):
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@dataclass(frozen=True)
class _PlannedAction:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


@pytest.mark.asyncio
async def test_delta_check_drift_emits_audit_drift_sse(monkeypatch, session_factory) -> None:
    async with session_factory() as session:
        eq = Equivalent(code="UAH", is_active=True, precision=2, metadata_={})
        p1 = Participant(
            pid="p1",
            display_name="p1",
            public_key=_pubkey("p1"),
            type="person",
            status="active",
            profile={},
        )
        p2 = Participant(
            pid="p2",
            display_name="p2",
            public_key=_pubkey("p2"),
            type="person",
            status="active",
            profile={},
        )
        session.add_all([eq, p1, p2])
        await session.commit()

        # Force the executor's payment call to fail with a delta-check invariant violation.
        async def _raise_delta_drift(self, *args, **kwargs):
            raise IntegrityViolationException(
                "Per-participant delta check failed",
                details={
                    "invariant": "PAYMENT_DELTA_DRIFT",
                    "source": "delta_check",
                    "equivalent": "UAH",
                    "total_drift": "1.00",
                    "drifts": [
                        {
                            "participant_id": "p1",
                            "expected_delta": "-1.00",
                            "actual_delta": "0.00",
                            "drift": "1.00",
                        }
                    ],
                },
            )

        from app.core.payments.service import PaymentService

        monkeypatch.setattr(PaymentService, "create_payment_internal", _raise_delta_drift)

        captured: list[dict[str, Any]] = []

        class _DummySse:
            def next_event_id(self, run: RunRecord) -> str:  # type: ignore[override]
                run._event_seq += 1
                return f"evt_{run.run_id}_{run._event_seq:06d}"

            def broadcast(self, _run_id: str, payload: dict[str, Any]) -> None:  # type: ignore[override]
                if isinstance(payload, dict):
                    captured.append(payload)

        run = RunRecord(run_id="run_delta_check_sse", scenario_id="s1", mode="real", state="running")
        run.tick_index = 7

        def _should_warn(_run: RunRecord, _key: str) -> bool:
            return False

        def _sim_idempotency_key(**kwargs) -> str:
            # RealPaymentsExecutor requires it, but our stubbed PaymentService never uses it.
            s = "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
            return hashlib.sha256(s.encode("utf-8")).hexdigest()

        executor = RealPaymentsExecutor(
            lock=threading.RLock(),
            sse=_DummySse(),  # type: ignore[arg-type]
            utc_now=_utc_now,
            logger=logging.getLogger("tests.delta_check_sse"),
            edge_patch_builder=EdgePatchBuilder(logger=logging.getLogger("tests.delta_check_sse")),
            should_warn_this_tick=_should_warn,
            sim_idempotency_key=_sim_idempotency_key,
        )

        # RealPaymentsExecutor drains results starting at seq=0.
        planned = [_PlannedAction(seq=0, equivalent="UAH", sender_pid="p1", receiver_pid="p2", amount="1.00")]

        await asyncio.wait_for(
            executor.execute_planned_payments(
                session=session,
                run_id=run.run_id,
                run=run,
                planned=planned,
                equivalents=["UAH"],
                sender_id_by_pid={"p1": p1.id},
                max_in_flight=1,
                max_timeouts_per_tick=0,
                fail_run=lambda *_a, **_kw: None,
            ),
            timeout=10.0,
        )

        drift_events = [e for e in captured if e.get("type") == "audit.drift" and e.get("source") == "delta_check"]
        assert drift_events, f"Expected audit.drift(source=delta_check). Got types={[e.get('type') for e in captured]}"
