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


def tick_unit_runner(**collaborators: Any):
    """A runner for a `RealTick` (`app/core/simulator/tick.py`) in a UNIT test, with no database.

    Programme 021 stage 4 folded the six `real_tick_*` classes into `RealTick`, which reads its collaborators from
    the runner at call time and captures the static intervals at construction. The unit tests of the old classes
    built each class with its collaborators as constructor arguments; they now build a `RealTick` over this runner
    and pass the same collaborators as keyword arguments (`_trust_drift_engine=...`, `_artifacts=...`, ...).
    Defaults: a real lock and logger, clearing on every tick with the default 250 ms budget, metrics and
    bottlenecks on every tick, at most 30 `clearing.done` cycle edges, no artifact writes, storage enabled, every
    warning let through.
    """

    import logging
    import threading
    from datetime import datetime, timezone
    from types import SimpleNamespace

    runner = SimpleNamespace(
        _lock=threading.RLock(),
        _logger=logging.getLogger("tick-unit"),
        _utc_now=lambda: datetime.now(timezone.utc),
        _clearing_every_n_ticks=1,
        _real_clearing_time_budget_ms=250,
        _clearing_max_fx_edges_limit=30,
        _real_db_metrics_every_n_ticks=1,
        _real_db_bottlenecks_every_n_ticks=1,
        _real_last_tick_write_every_ms=0,
        _real_artifacts_sync_every_ms=0,
        _db_enabled=lambda: True,
        _should_warn_this_tick=lambda _run, key: True,
        # 036 B1: a scenario with no scripted `payment` event (the unit stands have none): nothing joins the phase
        scripted_payments_due=lambda _run, _scenario, *, first_seq: ([], {}, 0),
        mark_scripted_events_fired=lambda _run, _indexes, _epoch, _progress=None: None,
    )
    for name, value in collaborators.items():
        setattr(runner, name, value)
    return runner


def unit_tick(**collaborators: Any):
    """`RealTick` over `tick_unit_runner(**collaborators)`."""

    from app.core.simulator.tick import RealTick

    return RealTick(tick_unit_runner(**collaborators))


def clearing_unit_tick(
    monkeypatch,
    *,
    sse: Any,
    session_factory: Any,
    apply_trust_growth: Any,
    runner_pass: Any = None,
    edge_patch_builder: Any = None,
    build_edge_patch_for_equivalent: Any = None,
    broadcast_topology_edge_patch: Any = None,
    max_fx_edges: int = 8,
    budget_ms: int = 10_000,
):
    """A `RealTick` whose clearing step (`RealTick._run_clearing`) runs against the given pieces.

    Programme 021 `T2109` removed the clearing driver (`RealClearingEngine`), whose unit and PostgreSQL tests built
    the driver with these collaborators as arguments and passed the runner seam as `clearing_pass=`. The tick reads
    its collaborators from the runner, and the runner entry (`app.core.clearing.runner.run_clearing_pass`) and its
    session factory (`app.db.session.AsyncSessionLocal`) at CALL time - so `runner_pass`, a double of the runner
    entry (omitted: the real runner), and `session_factory` are installed there, on `monkeypatch`.
    Drive it with `await tick._run_clearing(session=None, run_id=..., run=..., equivalents=[...], committed={})`;
    `committed` receives the committed volume per equivalent, which is what the tick reports.
    """

    from types import SimpleNamespace

    import app.core.clearing.runner as clearing_runner
    import app.db.session as app_db_session

    async def _no_edge_patch(**_kwargs) -> list:
        return []

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", session_factory)
    if runner_pass is not None:
        monkeypatch.setattr(clearing_runner, "run_clearing_pass", runner_pass)
    if edge_patch_builder is None:
        edge_patch_builder = SimpleNamespace(build_edge_patch_for_pairs=_no_edge_patch)
    return unit_tick(
        _sse=sse,
        _edge_patch_builder=edge_patch_builder,
        _trust_drift_engine=SimpleNamespace(apply_trust_growth=apply_trust_growth),
        _build_edge_patch_for_equivalent=build_edge_patch_for_equivalent or _no_edge_patch,
        _broadcast_topology_edge_patch=broadcast_topology_edge_patch or (lambda **_kwargs: None),
        _clearing_max_fx_edges_limit=int(max_fx_edges),
        _real_clearing_time_budget_ms=int(budget_ms),
    )
