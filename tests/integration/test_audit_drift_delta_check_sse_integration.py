from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.db.base import Base
from app.db.sqlite_transaction_control import install_sqlite_transaction_control
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException
from tests.scratch_db import install_test_sqlite_pragmas, scratch_db_path, scratch_db_url


# T1406: this module used to build its engine from a RELATIVE path, so the database landed
# in the repository root - against AGENTS.md §7/§12, and unnoticed because the test-database
# guard validates a URL and never looks at the filesystem. `tests/scratch_db` gives it a
# directory of its own under `.local-run/test-runs/`, which also keeps concurrent sessions in
# the shared working tree from colliding.
_TEST_DB_SLUG = "audit-drift-delta-check-sse"
_TEST_DB_PATH = str(scratch_db_path(_TEST_DB_SLUG))
_TEST_DB_URL = scratch_db_url(_TEST_DB_SLUG)


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
    # T1525: the same SQLite transaction control AND the same connection pragmas as the application
    # engine - WAL, foreign keys, busy timeout. Without the pragmas this module ran in the rollback
    # journal with foreign keys unenforced, so neither its concurrency nor its referential results
    # transferred to the application. Held by the pragma test at the bottom of this module.
    install_test_sqlite_pragmas(eng.sync_engine, url=_TEST_DB_URL)
    install_sqlite_transaction_control(eng.sync_engine)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    await eng.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass


async def test_this_modules_engine_has_the_application_sqlite_pragmas(engine) -> None:
    """T1525: WAL and enforced foreign keys, or this module's results do not transfer.

    This engine is built here rather than taken from `tests/conftest.py`, and until 2026-09-12 it
    received only the transaction control. In the rollback journal a reader holds a SHARED lock and
    blocks writers, where under WAL a reader that then writes is refused outright - opposite
    failure modes - and with `foreign_keys` off this module could not see a referential violation
    the application refuses.
    """
    from sqlalchemy import text

    async with engine.connect() as conn:
        journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
    assert str(journal_mode).lower() == "wal", journal_mode
    assert int(foreign_keys) == 1, foreign_keys


@dataclass(frozen=True)
class _PlannedAction:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


@pytest.mark.asyncio
async def test_delta_check_drift_emits_audit_drift_sse(monkeypatch, db_session) -> None:
    # MODE A (017 stage 3, slice S2a): the executor works on the ONE session it is handed and opens
    # none of its own, so the savepoint-wrapped `db_session` is enough, and cheaper than a clone.
    # Until then this test ran on a SQLite file of its own. `nullcontext` only keeps the body as it
    # was: the fixture, not this block, owns the session.
    async with contextlib.nullcontext(db_session) as session:
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
        injected = 0

        async def _raise_delta_drift(self, *args, **kwargs):
            nonlocal injected
            injected += 1
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

        monkeypatch.setattr(
            PaymentService,
            "create_payment_internal_staged",
            _raise_delta_drift,
        )

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

        result = await asyncio.wait_for(
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
        assert injected == 1
        assert captured == []

        await session.rollback()
        assert result.deferred_effects is not None
        result.deferred_effects.apply_after_rollback()

        drift_events = [e for e in captured if e.get("type") == "audit.drift" and e.get("source") == "delta_check"]
        assert drift_events, f"Expected audit.drift(source=delta_check). Got types={[e.get('type') for e in captured]}"
