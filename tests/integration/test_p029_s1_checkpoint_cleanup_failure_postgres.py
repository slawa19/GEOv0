"""029 S1 (the cleanup-failure half of F-030-7): a failed deletion of old checkpoints does not cancel the new ones.

The deletion runs after the write in one transaction, and debt reconciliation runs only after the checkpoints
succeed (`maintenance_jobs.py`) - so a failed cleanup would cancel both (AGENTS.md section 12: a post-delivery step
must not undo the delivered result). REAL FAILURE on a mode-B clone, nothing injected: another session holds the old
row locked, and the writer's `lock_timeout` makes PostgreSQL refuse the DELETE (55P03).
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select, text

import app.core.integrity as integrity
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint


async def _rows(session) -> int:
    return (await session.execute(select(func.count()).select_from(IntegrityCheckpoint))).scalar_one()


async def test_a_failed_cleanup_keeps_the_new_checkpoints_and_is_logged(committed_database, monkeypatch, caplog) -> None:
    sessions = committed_database.sessionmaker
    async with sessions() as writer, sessions() as blocker:
        writer.add(Equivalent(code="P29C", description="P29C", precision=2))
        await writer.commit()
        assert await integrity.compute_and_store_integrity_checkpoints(writer) == 1
        monkeypatch.setattr(integrity, "INTEGRITY_CHECKPOINT_TTL", timedelta(0))
        await blocker.execute(select(IntegrityCheckpoint).with_for_update())  # the old row, held

        await writer.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with caplog.at_level(logging.ERROR, logger="app.core.integrity"):
            assert await integrity.compute_and_store_integrity_checkpoints(writer) == 1
        assert [r.getMessage() for r in caplog.records] == ["integrity.checkpoint_cleanup_failed"]
        assert "LockNotAvailable" in caplog.text  # control: the failure is PostgreSQL's refusal, not a stand-in
        assert await _rows(writer) == 2  # the new checkpoint is stored, the old one is still there

        await blocker.rollback()
        assert await integrity.compute_and_store_integrity_checkpoints(writer) == 1  # the next run cleans up
        assert await _rows(writer) == 1
