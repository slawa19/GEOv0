from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.exc import OperationalError

from sqlalchemy import delete, select, update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

import app.db.session as db
from app.config import settings
from app.core.simulator.helpers import artifact_content_type, artifact_sha256
from app.core.simulator.models import RunRecord
from app.db.models.simulator_storage import (
    SimulatorRun,
    SimulatorRunArtifact,
    SimulatorRunBottleneck,
    SimulatorRunMetric,
)

logger = logging.getLogger(__name__)


async def _retry_on_locked(
    coro_factory: Callable,
    max_retries: int = 3,
    base_delay: float = 0.5,
):
    """Retry async DB operation on SQLite 'database is locked' errors."""
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "simulator.storage db_locked attempt=%d/%d, retrying in %.1fs",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise


def db_enabled() -> bool:
    return bool(getattr(settings, "SIMULATOR_DB_ENABLED", False))


def _validate_run_for_storage(run: RunRecord) -> None:
    if not str(getattr(run, "run_id", "") or "").strip():
        raise ValueError("run_id is required")
    if not str(getattr(run, "scenario_id", "") or "").strip():
        raise ValueError("scenario_id is required")
    if not str(getattr(run, "mode", "") or "").strip():
        raise ValueError("mode is required")
    if not str(getattr(run, "state", "") or "").strip():
        raise ValueError("state is required")
    # New runs must always have an owner_id (cookie/participant/admin/cli).
    # Legacy rows in DB are handled by migration backfill (owner_id='legacy:unknown').
    if not str(getattr(run, "owner_id", "") or "").strip():
        raise ValueError("owner_id is required")


async def upsert_run(run: RunRecord) -> None:
    if not db_enabled():
        return
    try:
        _validate_run_for_storage(run)

        async def _do():
            async with db.AsyncSessionLocal() as session:
                row = SimulatorRun(
                    run_id=run.run_id,
                    scenario_id=run.scenario_id,
                    mode=str(run.mode),
                    state=str(run.state),
                    started_at=run.started_at,
                    stopped_at=run.stopped_at,
                    sim_time_ms=(
                        int(run.sim_time_ms) if run.sim_time_ms is not None else None
                    ),
                    tick_index=int(run.tick_index) if run.tick_index is not None else None,
                    seed=int(run.seed) if run.seed is not None else None,
                    intensity_percent=(
                        int(run.intensity_percent)
                        if run.intensity_percent is not None
                        else None
                    ),
                    ops_sec=float(run.ops_sec) if run.ops_sec is not None else None,
                    queue_depth=(
                        int(run.queue_depth) if run.queue_depth is not None else None
                    ),
                    errors_total=(
                        int(run.errors_total) if run.errors_total is not None else None
                    ),
                    last_event_type=run.last_event_type,
                    current_phase=run.current_phase,
                    last_error=run.last_error,
                    owner_id=run.owner_id if run.owner_id else None,
                    owner_kind=run.owner_kind if run.owner_kind else None,
                )
                try:
                    await session.merge(row)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        await _retry_on_locked(_do)
    except Exception:
        logger.exception(
            "simulator.storage.upsert_run_failed run_id=%s", getattr(run, "run_id", "")
        )
        return


async def sync_artifacts(run: RunRecord) -> None:
    if not db_enabled():
        return
    base = run.artifacts_dir
    if base is None or not base.exists():
        return

    try:
        sha_max_bytes = int(
            os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288") or "524288"
        )

        items: list[dict[str, object]] = []
        for p in sorted(base.iterdir()):
            if not p.is_file():
                continue
            url = f"/api/v1/simulator/runs/{run.run_id}/artifacts/{p.name}"
            sha = None
            size = None
            try:
                size = int(p.stat().st_size)
                if size <= sha_max_bytes:
                    sha = artifact_sha256(p)
            except Exception:
                logger.exception(
                    "simulator.storage.artifact_hash_failed run_id=%s name=%s",
                    getattr(run, "run_id", ""),
                    getattr(p, "name", ""),
                )
            items.append(
                {
                    "run_id": str(run.run_id),
                    "name": str(p.name),
                    "content_type": artifact_content_type(p.name),
                    "size_bytes": size,
                    "sha256": sha,
                    "storage_url": url,
                }
            )

        async def _do_sync():
            async with db.AsyncSessionLocal() as session:
                # Diff-based sync: delete missing + upsert changed/new.
                existing_rows = (
                    await session.execute(
                        select(
                            SimulatorRunArtifact.name,
                            SimulatorRunArtifact.content_type,
                            SimulatorRunArtifact.size_bytes,
                            SimulatorRunArtifact.sha256,
                            SimulatorRunArtifact.storage_url,
                        ).where(SimulatorRunArtifact.run_id == run.run_id)
                    )
                ).all()

                existing: dict[str, tuple[object, object, object, object]] = {
                    str(name): (content_type, size_bytes, sha256, storage_url)
                    for (name, content_type, size_bytes, sha256, storage_url) in existing_rows
                }

                names_now: set[str] = set()
                rows_to_upsert: list[dict[str, object]] = []
                for row in items:
                    name = str(row.get("name") or "")
                    names_now.add(name)
                    prev = existing.get(name)
                    cur = (
                        row.get("content_type"),
                        row.get("size_bytes"),
                        row.get("sha256"),
                        row.get("storage_url"),
                    )
                    if prev == cur:
                        continue
                    rows_to_upsert.append(row)

                names_to_delete = [n for n in existing.keys() if n not in names_now]
                if names_to_delete:
                    await session.execute(
                        delete(SimulatorRunArtifact).where(
                            SimulatorRunArtifact.run_id == run.run_id,
                            SimulatorRunArtifact.name.in_(names_to_delete),
                        )
                    )

                if rows_to_upsert:
                    bind = None
                    try:
                        bind = session.get_bind()
                    except Exception:
                        bind = getattr(session, "bind", None)

                    dialect_name = None
                    try:
                        dialect_name = bind.dialect.name if bind is not None else None
                    except Exception:
                        dialect_name = None

                    if dialect_name == "sqlite":
                        insert_fn = sqlite_insert
                    elif dialect_name in {"postgresql", "postgres"}:
                        insert_fn = pg_insert
                    else:
                        raise RuntimeError(
                            f"Unsupported SQL dialect for simulator_run_artifacts upsert: {dialect_name!r}"
                        )

                    table = SimulatorRunArtifact.__table__
                    stmt = insert_fn(table)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[table.c.run_id, table.c.name],
                        set_={
                            table.c.content_type: stmt.excluded.content_type,
                            table.c.size_bytes: stmt.excluded.size_bytes,
                            table.c.sha256: stmt.excluded.sha256,
                            table.c.storage_url: stmt.excluded.storage_url,
                        },
                    )
                    await session.execute(stmt, rows_to_upsert)

                await session.commit()

        await _retry_on_locked(_do_sync)
    except Exception:
        logger.exception(
            "simulator.storage.sync_artifacts_failed run_id=%s",
            getattr(run, "run_id", ""),
        )
        return


# 2026-08-20 / p007_t715. The two series the domain model declares as amounts
# in the selected equivalent, i.e. the ones that carry money.
MONEY_METRIC_KEYS = frozenset({"total_debt", "clearing_volume"})

# Run ids already warned about lossy SQLite money persistence. Bounded so a long
# lived process cannot grow this set without limit; when the cap is reached the
# set is cleared, which at worst re-emits the warning once more for a run.
_SQLITE_MONEY_WARNED_RUN_IDS: set[str] = set()
_SQLITE_MONEY_WARNED_MAX_RUN_IDS = 512


def _warn_once_sqlite_money_precision(run_id: str, keys: list[str]) -> None:
    """Warn once per run that money metrics on SQLite are NOT exact.

    `simulator_run_metrics.value` is `Numeric(20, 8)` and `MetricPoint.v` is a
    decimal string, but SQLite has no native decimal: SQLAlchemy round-trips the
    value through binary floating point, so `Decimal("12345678901.12345678")`
    comes back as `Decimal("12345678901.12345695")`. The wire format would then
    present an inexact number in the shape reserved for exact money.

    SQLite stays a supported backend on purpose - it is the documented
    no-Docker development path (`README.md`, `app/config.py` default) - so this
    is an announced limitation, not a silent one. PostgreSQL is the only backend
    where these series are exact, the same split AGENTS.md 4 already makes for
    locking semantics.
    """

    key = str(run_id)
    if key in _SQLITE_MONEY_WARNED_RUN_IDS:
        return
    if len(_SQLITE_MONEY_WARNED_RUN_IDS) >= _SQLITE_MONEY_WARNED_MAX_RUN_IDS:
        _SQLITE_MONEY_WARNED_RUN_IDS.clear()
    _SQLITE_MONEY_WARNED_RUN_IDS.add(key)
    logger.warning(
        "simulator.storage.sqlite_money_metrics_are_not_exact run_id=%s keys=%s "
        "detail=%s",
        key,
        ",".join(sorted(keys)),
        "SQLite stores Numeric through binary floating point, so the money "
        "metric series persisted for this run are approximations and the "
        "decimal strings served for them are NOT exact amounts. Use PostgreSQL "
        "for exact simulator money metrics.",
    )


def _measured_value(
    values: dict[str, Optional[Decimal | float]], key: str
) -> Optional[Decimal]:
    """Return the measured value for `key` as Decimal, or None when unmeasured.

    A metric the producer did not measure must reach the DB as NULL; defaulting
    it to 0 would be indistinguishable from a measured zero (spec 007, F-007-1).

    2026-08-20 / p007_t715: the column is Numeric(20, 8). A Decimal produced
    upstream (the money series) is passed through untouched — converting it to
    float here would reintroduce exactly the narrowing this slice removes.
    """

    raw = values.get(key)
    if raw is None:
        return None
    return raw if isinstance(raw, Decimal) else Decimal(str(raw))


async def write_tick_metrics(
    *,
    run_id: str,
    t_ms: int,
    per_equivalent: dict[str, dict[str, int]],
    metric_values_by_eq: Optional[dict[str, dict[str, Optional[Decimal | float]]]] = None,
    session=None,
    commit: bool = True,
) -> None:
    """Upsert one tick worth of metric points. Best-effort: never raises.

    Transaction contract (2026-08-20 / p007_t715): when `session` is supplied it
    belongs to the caller. This function wraps its statements in a SAVEPOINT and
    rolls back only that savepoint on failure, so a failed metrics write cannot
    discard work the caller staged in the same transaction. It commits the
    caller's session only when `commit=True`.

    Backend limitation (2026-08-20 / p007_t715): `SimulatorRunMetric.value` is
    `Numeric(20, 8)` and the money series (`MONEY_METRIC_KEYS`) are served as
    decimal strings, but **only PostgreSQL stores them exactly**. On SQLite -
    the documented no-Docker development backend and the `app/config.py`
    default - SQLAlchemy round-trips Numeric through binary floating point, so
    those values are approximations wearing an exact-money shape. The condition
    is announced once per run at WARNING level rather than hidden; see
    `_warn_once_sqlite_money_precision` and
    `docs/ru/simulator/backend/run-storage.md`.
    """

    if not db_enabled():
        return

    try:

        async def _write(s) -> None:
            bind = None
            try:
                bind = s.get_bind()
            except Exception:
                bind = getattr(s, "bind", None)

            dialect_name = None
            try:
                dialect_name = bind.dialect.name if bind is not None else None
            except Exception:
                dialect_name = None

            if dialect_name == "sqlite":
                insert_fn = sqlite_insert
            elif dialect_name in {"postgresql", "postgres"}:
                insert_fn = pg_insert
            else:
                raise RuntimeError(
                    f"Unsupported SQL dialect for simulator_run_metrics upsert: {dialect_name!r}"
                )

            rows: list[dict[str, object]] = []
            for eq, counters in (per_equivalent or {}).items():
                committed = int(counters.get("committed", 0))
                rejected = int(counters.get("rejected", 0))
                errors = int(counters.get("errors", 0))
                timeouts = int(counters.get("timeouts", 0))

                # "Not measured" is persisted as NULL, never as 0.0: the reader
                # must be able to tell a missing measurement from a measured
                # zero (spec 007, F-007-1). The column is nullable.
                # 2026-08-20 / p007_t715: the column is Numeric(20, 8), so the
                # ratios are computed in Decimal as well; no float reaches the DB.
                denom = committed + rejected
                success_rate = (
                    (Decimal(committed) / Decimal(denom)) * Decimal(100)
                    if denom > 0
                    else None
                )
                attempts = committed + rejected + errors
                bottlenecks_score = (
                    (Decimal(errors + timeouts) / Decimal(attempts)) * Decimal(100)
                    if attempts > 0
                    else None
                )

                mv = (metric_values_by_eq or {}).get(str(eq), {}) or {}
                avg_route_length = _measured_value(mv, "avg_route_length")
                total_debt = _measured_value(mv, "total_debt")
                clearing_volume = _measured_value(mv, "clearing_volume")
                active_participants = _measured_value(mv, "active_participants")
                active_trustlines = _measured_value(mv, "active_trustlines")

                eq_code = str(eq)
                rows.extend(
                    [
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "success_rate",
                            "t_ms": int(t_ms),
                            "value": success_rate,
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "bottlenecks_score",
                            "t_ms": int(t_ms),
                            "value": bottlenecks_score,
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "avg_route_length",
                            "t_ms": int(t_ms),
                            "value": avg_route_length,
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "total_debt",
                            "t_ms": int(t_ms),
                            "value": total_debt,
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "clearing_volume",
                            "t_ms": int(t_ms),
                            "value": clearing_volume,
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "active_participants",
                            "t_ms": int(t_ms),
                            "value": active_participants,
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "active_trustlines",
                            "t_ms": int(t_ms),
                            "value": active_trustlines,
                        },
                    ]
                )

            if not rows:
                return

            # p007_t715: announce the SQLite money-precision limitation once per
            # run, and only when a money series actually carries a measurement -
            # a warning that fires with nothing at stake teaches nothing.
            if dialect_name == "sqlite":
                lossy_keys = sorted(
                    {
                        str(row["key"])
                        for row in rows
                        if str(row["key"]) in MONEY_METRIC_KEYS
                        and row.get("value") is not None
                    }
                )
                if lossy_keys:
                    _warn_once_sqlite_money_precision(str(run_id), lossy_keys)

            table = SimulatorRunMetric.__table__
            stmt = insert_fn(table)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    table.c.run_id,
                    table.c.equivalent_code,
                    table.c.key,
                    table.c.t_ms,
                ],
                set_={table.c.value: stmt.excluded.value},
            )
            await s.execute(stmt, rows)
            await s.flush()

        if session is None:
            async def _do_write():
                async with db.AsyncSessionLocal() as s:
                    # This session is ours: rolling it back on failure is
                    # within our mandate.
                    try:
                        await _write(s)
                        if commit:
                            await s.commit()
                    except Exception:
                        try:
                            await s.rollback()
                        except Exception:
                            pass
                        raise
            await _retry_on_locked(_do_write)
        else:
            # 2026-08-20 / p007_t715: the session belongs to the caller - the
            # real-mode tick passes its own session with commit=False. A failed
            # metrics write must undo ONLY this write. The previous
            # `await session.rollback()` discarded the caller's whole
            # transaction, and the outer `except` below swallowed the failure,
            # so everything the tick had staged vanished with no signal. That is
            # the same defect family as finding B-A2b-001 (registry 008); the
            # Numeric(20, 8) column made it reachable through a new failure
            # class (numeric field overflow above 10^12).
            savepoint = await session.begin_nested()
            try:
                await _write(session)
                await savepoint.commit()
            except Exception:
                try:
                    await savepoint.rollback()
                except Exception:
                    pass
                raise

            if commit:
                # 2026-08-20 / p007_t715: this is NOT a relapse of the defect
                # fixed just above, it is its mirror image. There the caller
                # passed `commit=False`, kept ownership of the transaction and
                # never asked us to end it - so rolling it back was deciding for
                # them. Here `commit=True` means the caller delegated the commit
                # to us (`real_tick_persistence.py` opens a session, hands it
                # over without `commit=`, and then reuses it for
                # `write_tick_bottlenecks`). Failing the delegated commit and
                # leaving the session in pending-rollback would make the caller's
                # *next* statement die of `PendingRollbackError`, an error with
                # no connection to the real cause. Returning the session to a
                # usable state is part of the job we accepted.
                try:
                    await session.commit()
                except Exception:
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    raise
    except Exception:
        logger.exception(
            "simulator.storage.write_tick_metrics_failed run_id=%s t_ms=%s",
            str(run_id),
            int(t_ms),
        )
        return


async def write_tick_bottlenecks(
    *,
    run_id: str,
    equivalent: str,
    computed_at: datetime,
    edge_stats: dict[tuple[str, str], dict[str, int]],
    session,
    limit: int = 50,
    commit: bool = True,
) -> None:
    if not db_enabled():
        return

    try:
        items: list[SimulatorRunBottleneck] = []
        for (src, dst), st in edge_stats.items():
            attempts = int(st.get("attempts", 0))
            if attempts <= 0:
                continue
            errors = int(st.get("errors", 0))
            timeouts = int(st.get("timeouts", 0))
            rejected = int(st.get("rejected", 0))
            committed = int(st.get("committed", 0))

            bad = errors + timeouts + rejected
            score = max(0.0, min(1.0, float(bad) / float(attempts)))
            if score <= 0:
                continue

            if timeouts > 0 and float(timeouts) / float(attempts) >= 0.2:
                reason: str = "TOO_MANY_TIMEOUTS"
                label = "Too many timeouts"
                suggested = "Reduce load or increase routing timeouts"
            elif rejected > 0 or errors > 0:
                reason = "FREQUENT_ABORTS"
                label = "Frequent failures"
                suggested = "Increase trust limits, add alternative routes, or clear"
            else:
                reason = "HIGH_USED"
                label = "High utilization"
                suggested = "Consider clearing or adding alternative routes"

            items.append(
                SimulatorRunBottleneck(
                    run_id=run_id,
                    equivalent_code=str(equivalent),
                    computed_at=computed_at,
                    target_type="edge",
                    target_id=f"{src}->{dst}",
                    score=float(score),
                    reason_code=str(reason),
                    details={
                        "attempts": attempts,
                        "committed": committed,
                        "rejected": rejected,
                        "errors": errors,
                        "timeouts": timeouts,
                        "label": label,
                        "suggested_action": suggested,
                    },
                )
            )

        items.sort(key=lambda r: (float(r.score), str(r.target_id)), reverse=True)
        items = items[: int(limit)]
        if not items:
            return

        session.add_all(items)
        if commit:
            await session.commit()
        else:
            await session.flush()
    except Exception:
        logger.exception(
            "simulator.storage.write_tick_bottlenecks_failed run_id=%s equivalent=%s",
            str(run_id),
            str(equivalent),
        )
        try:
            await session.rollback()
        except Exception:
            pass
        return


async def reconcile_stale_runs() -> int:
    """Mark runs stuck in non-terminal state as error (server restart recovery).

    Called once at startup (§12 Recovery reconciliation).  Any run still in
    'running', 'paused', or 'stopping' state has no active process after a
    restart, so we transition it to 'error' with last_error={"reason":
    "server_restart"}.

    Returns the number of reconciled runs (0 if DB is disabled or none found).
    This is best-effort: DB errors are logged and swallowed so the server still
    starts up cleanly.
    """
    if not db_enabled():
        return 0

    stale_states = ("running", "paused", "stopping")
    try:
        now = datetime.now(timezone.utc)
        count_holder: list[int] = [0]

        async def _do_reconcile():
            async with db.AsyncSessionLocal() as session:
                result = await session.execute(
                    sql_update(SimulatorRun)
                    .where(SimulatorRun.state.in_(stale_states))
                    .values(
                        state="error",
                        last_error={"reason": "server_restart"},
                        stopped_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                count_holder[0] = result.rowcount if result.rowcount is not None else 0
                await session.commit()

        await _retry_on_locked(_do_reconcile)
        count = count_holder[0]

        if count:
            logger.warning(
                "simulator.reconcile stale_runs=%d — marked as error (reason: server_restart)",
                count,
            )
        else:
            logger.info("simulator.reconcile no stale runs found at startup")
        return count
    except Exception:
        logger.exception("simulator.reconcile_stale_runs failed (best-effort, server will still start)")
        return 0
