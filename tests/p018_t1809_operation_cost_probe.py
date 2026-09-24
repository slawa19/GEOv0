"""018 `T1809`: what one payment commit, one clearing and one mixed inject event cost - a re-runnable probe.

NOT PART OF THE TIER. The file name does not match `python_files = test_*.py`, so the tier never
collects it; pytest collects it only when it is named explicitly. It is a MEASUREMENT, run on purpose,
on two trees - the listener journal (`2fb1056`) and the trigger journal (B1) - with the same file:

    .\\scripts\\verify_local.ps1 -TaskSlug t1809 -BackendOnly -BackendSelector tests/p018_t1809_operation_cost_probe.py

It writes `t1809_operation_cost.json` under `GEO_TEST_ARTIFACT_ROOT` (the runner sets it to
`.local-run/test-runs/<slug>/artifacts/`) and prints the same table with `-s`. On the listener tree the
file is copied in uncommitted; it imports only helpers both trees have.

WHAT IS MEASURED, per operation, over `REPS` repetitions, each on its own freshly seeded world in one
mode-B clone of the migrated template:

* **client round trips** - every statement SQLAlchemy sends while the operation runs
  (`before_cursor_execute` on the `Engine` class, so a session the operation opens itself counts too);
* **database work** - the delta of `pg_stat_database` for the clone, read from a connection to a
  DIFFERENT database (`postgres`) after every backend of the clone has exited, so the reader's own
  queries never land in the numbers: `active_time` (ms the server spent executing statements, trigger
  functions included), blocks touched (`blks_hit + blks_read`), tuples read (`tup_returned +
  tup_fetched`) and written (`tup_inserted + tup_updated + tup_deleted`), transactions;
* client wall time of the operation, for scale only (it includes connecting).

`pg_stat_statements` is not used: it needs `shared_preload_libraries` and a server restart, and neither
the local portable server nor the CI service has it.

WHAT IT DOES NOT MEASURE: contention (one operation at a time), a warm pool (the operation's engine is
`NullPool`, so every session connects - the same on both trees), and anything off PostgreSQL 16 on this
machine. `active_time` has millisecond resolution per backend report; the medians, not single runs,
are the result.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import asyncpg
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.clearing.service import ClearingService
from app.core.payments.engine import PaymentEngine
from app.db.models.debt import Debt
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_b4_wrong_writer_is_recorded_faithfully_postgres import (
    _edges,
    _prepare_payment,
    _seed_triangle,
)
from tests.integration.test_p015_f01512_inject_refuses_an_opposing_debt_postgres import (
    _baseline,
)
from tests.integration.test_p015_f01512_inject_refuses_an_opposing_debt_postgres import (
    _seed as _seed_pair,
)
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (
    _Artifacts,
    _run,
    _runner,
)

REPS = int(os.environ.get("T1809_REPS", "9"))
LIMIT = Decimal("1000.00")

_STATS = (
    "SELECT sessions, active_time, blks_hit + blks_read AS blocks, "
    "tup_returned + tup_fetched AS tuples_read, "
    "tup_inserted + tup_updated + tup_deleted AS tuples_written, "
    "xact_commit + xact_rollback AS xacts "
    "FROM pg_stat_database WHERE datname = $1"
)


class _Statements:
    def __init__(self) -> None:
        self.count = 0
        self.armed = False

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if self.armed:
            self.count += 1


async def _settled_stats(url: str) -> dict[str, float]:
    """`pg_stat_database` of the clone once nothing is connected to it, read from `postgres`."""

    parsed = make_url(url)
    name = parsed.database
    connection = await asyncpg.connect(
        host=parsed.host,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database="postgres",
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            busy = await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = $1", name
            )
            if busy == 0:
                break
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"{busy} backend(s) still connected to {name}; stats not settled"
                )
            await asyncio.sleep(0.05)
        await connection.execute("SELECT pg_stat_clear_snapshot()")
        row = await connection.fetchrow(_STATS, name)
        return {key: float(row[key]) for key in row.keys()}
    finally:
        await connection.close()


async def _measure(url: str, statements: _Statements, operation: Callable[[], Awaitable[Any]]):
    before = await _settled_stats(url)
    statements.count = 0
    statements.armed = True
    started = time.perf_counter()
    try:
        await operation()
    finally:
        statements.armed = False
    wall_ms = (time.perf_counter() - started) * 1000
    after = await _settled_stats(url)
    delta = {key: after[key] - before[key] for key in before}
    return {
        "statements": statements.count,
        "active_ms": round(delta["active_time"], 3),
        "blocks": int(delta["blocks"]),
        "tuples_read": int(delta["tuples_read"]),
        "tuples_written": int(delta["tuples_written"]),
        "xacts": int(delta["xacts"]),
        "sessions": int(delta["sessions"]),
        "wall_ms": round(wall_ms, 1),
    }


def _factory(url: str, **pool: Any):
    engine = create_async_engine(
        url, isolation_level="SERIALIZABLE", **(pool or {"poolclass": NullPool})
    )
    return engine, async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


# --- the three operations: setup (not measured) and the operation (measured) ---------------------


async def _payment(url: str, statements: _Statements) -> dict:
    """A pays B 5.00 over a direct line while A already owes B 2.00: one `U` on a stored edge."""

    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        async with factory() as session:
            async with debt_fixture_setup(session, label="t1809-payment"):
                session.add(
                    Debt(
                        id=uuid.uuid4(),
                        debtor_id=triangle.a.id,
                        creditor_id=triangle.b.id,
                        equivalent_id=triangle.equivalent_id,
                        amount=Decimal("2.00"),
                        version=0,
                    )
                )
            await session.commit()
        tx_id = await _prepare_payment(factory, triangle, ["a", "b"], Decimal("5.00"))
    finally:
        await engine.dispose()

    engine, factory = _factory(url)

    async def operation() -> None:
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)

    try:
        measured = await _measure(url, statements, operation)
        edges = await _edges(factory, triangle)
    finally:
        await engine.dispose()
    assert edges == {("a", "b"): 700_000_000}, edges  # 7.00 in atoms: the commit really wrote
    return measured


async def _clearing(url: str, statements: _Statements) -> dict:
    """A full-size three-edge cycle of 10.00 cleared: three `D`."""

    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(
            factory, trustlines=[("b", "a", LIMIT), ("c", "b", LIMIT), ("a", "c", LIMIT)]
        )
        debts = [
            Debt(
                id=uuid.uuid4(),
                debtor_id=getattr(triangle, d).id,
                creditor_id=getattr(triangle, c).id,
                equivalent_id=triangle.equivalent_id,
                amount=Decimal("10.00"),
                version=0,
            )
            for d, c in (("a", "b"), ("b", "c"), ("c", "a"))
        ]
        ids = [str(debt.id) for debt in debts]
        async with factory() as session:
            async with debt_fixture_setup(session, label="t1809-cycle"):
                session.add_all(debts)
            await session.commit()
    finally:
        await engine.dispose()

    engine, factory = _factory(url)
    cleared: list[Any] = []

    async def operation() -> None:
        async with factory() as session:
            cleared.append(
                await ClearingService(session).execute_clearing_with_amount(
                    [{"debt_id": debt_id} for debt_id in ids]
                )
            )

    try:
        measured = await _measure(url, statements, operation)
        edges = await _edges(factory, triangle)
    finally:
        await engine.dispose()
    assert Decimal(str(cleared[0])) == Decimal("10.00") and edges == {}, (cleared, edges)
    return measured


async def _inject(url: str, statements: _Statements) -> dict:
    """The mixed event of `test_p018_mixed_inject_event_is_one_operation_postgres.py`, verbatim."""

    engine, factory = _factory(url, pool_size=2, max_overflow=0)
    try:
        world = await _seed_pair(factory)
        await _baseline(factory, world)
    finally:
        await engine.dispose()
    a, b = world.creditor, world.debtor
    eq = world.equivalents[0]
    n = uuid.uuid4().hex[:8]
    c_pid, d_pid = f"MXC_{n}", f"MXD_{n}"

    def debt(creditor: str, debtor: str, amount: str) -> dict[str, Any]:
        return {
            "op": "inject_debt",
            "from": creditor,
            "to": debtor,
            "equivalent": eq.code,
            "amount": amount,
        }

    effects = [
        debt(a.pid, b.pid, "3.00"),
        {"op": "add_participant", "participant": {"id": c_pid, "name": "C"}},
        {
            "op": "create_trustline",
            "from": a.pid,
            "to": c_pid,
            "equivalent": eq.code,
            "limit": "50",
        },
        debt(a.pid, c_pid, "4.00"),
        {"op": "freeze_participant", "participant_id": b.pid},
        debt(a.pid, b.pid, "2.00"),
        {"op": "add_participant", "participant": {"id": d_pid, "name": "D"}},
        debt(a.pid, c_pid, "1.00"),
        debt(a.pid, b.pid, "1.00"),
    ]
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": a.pid}, {"id": b.pid}],
        "trustlines": [
            {
                "from": a.pid,
                "to": b.pid,
                "equivalent": eq.code,
                "limit": "100.00",
                "status": "active",
            },
            {
                "from": b.pid,
                "to": a.pid,
                "equivalent": eq.code,
                "limit": "100.00",
                "status": "active",
            },
        ],
        "behaviorProfiles": [],
        "events": [{"type": "inject", "time": 0, "effects": effects}],
    }
    run = _run(world, f"t1809-{n}")
    artifacts = _Artifacts()
    runner = _runner(run, scenario, artifacts)
    engine, factory = _factory(url)

    async def operation() -> None:
        async with factory() as session:
            await runner._apply_due_scenario_events(
                session, run_id=run.run_id, run=run, scenario=scenario
            )

    try:
        measured = await _measure(url, statements, operation)
    finally:
        await engine.dispose()
    notes = [p["scenario"] for p in artifacts.events if p.get("type") == "note"]
    assert notes and notes[0]["stats"] == {
        "applied": 7,
        "skipped": 2,
        "total_amount": "6.00",
    }, notes
    return measured


async def _control(url: str, statements: _Statements) -> dict:
    """One session, one `SELECT 1`: the fixed cost of a connection (catalog loads, auth) on this server.

    Not an operation. It is here so the per-operation numbers can be read net of what merely
    connecting costs - the payment connects once, the clearing twice, the inject event three times.
    """

    from sqlalchemy import text

    engine, factory = _factory(url)

    async def operation() -> None:
        async with factory() as session:
            await session.execute(text("SELECT 1"))

    try:
        return await _measure(url, statements, operation)
    finally:
        await engine.dispose()


# --- the probe --------------------------------------------------------------------------------


def _summary(samples: list[dict]) -> dict:
    out = {}
    for key in samples[0]:
        values = [sample[key] for sample in samples]
        out[key] = {"median": statistics.median(values), "min": min(values), "max": max(values)}
    return out


@pytest.mark.asyncio
async def test_t1809_operation_cost(committed_database) -> None:
    url = committed_database.url
    statements = _Statements()
    event.listen(Engine, "before_cursor_execute", statements)
    try:
        results: dict[str, list[dict]] = {
            "connect_control": [],
            "payment_commit": [],
            "clearing": [],
            "inject_event": [],
        }
        for _ in range(REPS):
            results["connect_control"].append(await _control(url, statements))
            results["payment_commit"].append(await _payment(url, statements))
            results["clearing"].append(await _clearing(url, statements))
            results["inject_event"].append(await _inject(url, statements))
    finally:
        event.remove(Engine, "before_cursor_execute", statements)

    for name, samples in results.items():
        if name == "connect_control":
            continue
        # Anti-vacuum: every repetition counted statements and saw the server do work.
        assert all(
            s["statements"] > 0 and s["xacts"] > 0 and s["tuples_written"] > 0 for s in samples
        ), (
            name,
            samples,
        )

    report = {
        "reps": REPS,
        "summary": {name: _summary(samples) for name, samples in results.items()},
        "samples": results,
    }
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT", ".local-run/test-runs/t1809/artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    (root / "t1809_operation_cost.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, summary in report["summary"].items():
        print(name, {k: (v["median"], v["min"], v["max"]) for k, v in summary.items()})
