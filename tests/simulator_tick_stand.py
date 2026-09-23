"""Pieces of a real-mode TICK stand shared by the modules that drive `RealRunner.tick_real_mode`.

NOT a test module. It exists so the tick stands do not import from one another's test modules
(017 stage 3, slice S2a). `test_p015_t1544_operator_stop_through_the_tick_sqlite.py` and
`test_p015_step5c_hold_through_the_tick_sqlite.py` used to import these two helpers from
`test_p015_p1_money_replay_sqlite.py`, a SQLite stand that stage 3 deletes; had the deletion come
first, both modules - which run on PostgreSQL - would have stopped collecting with an `ImportError`.

`install_tick_stand` routes the simulator to WHATEVER sessionmaker it is given; the tick stands give
it `pooled_sessionmaker_over(committed_database.url)`, a pooled engine over their mode-B clone.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.simulator.models import RunRecord


class RecordingSse:
    """The SSE surface a tick needs, recording every dict it broadcasts."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            self.events.append(payload)

    def published(self, event_type: str) -> int:
        return sum(1 for e in self.events if str(e.get("type")) == event_type)


def install_tick_stand(monkeypatch, session_factory) -> None:
    """Point the tick's own sessions at `session_factory` and silence the storage side-writes.

    The tick opens its sessions through `app.db.session.AsyncSessionLocal`, looked up at call time,
    so rebinding that name is what makes the tick write where the test reads. The four storage
    writers are artifacts of a run, not of its money, and are no subject of any stand using this.
    """
    import app.core.simulator.storage as simulator_storage
    import app.db.session as app_db_session

    async def _noop(*_a, **_kw):
        return None

    for name in ("write_tick_metrics", "write_tick_bottlenecks", "sync_artifacts", "upsert_run"):
        monkeypatch.setattr(simulator_storage, name, _noop)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", session_factory)


@asynccontextmanager
async def pooled_sessionmaker_over(url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker over a mode-B clone's URL, POOLED the way the application's engine is.

    WHY NOT `committed_database.sessionmaker`. That one sits on a `NullPool` engine, so every session
    the tick opens - and a real-mode tick with clearing opens many - pays for a new PostgreSQL
    connection. Measured 2026-09-24 on the T1544 clearing stand (five clearing ticks): 2.46 s with
    NullPool, 0.5 s with this pool. The application does not run on NullPool either: the pool size,
    overflow, timeouts, pre-ping and recycle below are its own settings (`app/db/session.py`), and so
    is the isolation level, read from the same setting and never a literal.

    PostgreSQL only, and refused otherwise: a clone is made by `CREATE DATABASE ... TEMPLATE`, so this
    cannot happen, and a SQLite engine here would need the T1525 transaction control.
    """
    if not url.startswith("postgresql"):
        raise RuntimeError(f"a mode-B clone must be PostgreSQL, got {url!r}")
    engine = create_async_engine(
        url,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL,
    )
    try:
        yield async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    finally:
        # Before the clone is dropped: a pooled connection still open would make the drop race it.
        await engine.dispose()
