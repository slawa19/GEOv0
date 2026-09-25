"""019 `T1903`: the SQL statement sequence of every lock-primitive consumer - a re-runnable probe.

NOT PART OF THE TIER. The file name does not match `python_files = test_*.py`, so the tier never
collects it; pytest collects it only when it is named explicitly:

    .\\scripts\\verify_local.ps1 -TaskSlug p019s2 -BackendOnly -BackendSelector tests/p019_t1903_statement_sequence_probe.py

WHAT IT IS FOR. Stage 2 of 019 moves every lock primitive out of `app/core/payments/engine.py` into
`app/core/money_boundary.py` WITHOUT a behaviour change. "Without a behaviour change" is checked here
the way 018 stage A checked its move: the text of every statement SQLAlchemy sends while one operation
runs (`before_cursor_execute` on the `Engine` class, so a session the operation opens itself counts
too), recorded on the tree before the move and on the tree after it, must be identical. The probe
writes `p019_t1903_statements.json` under `GEO_TEST_ARTIFACT_ROOT`; the comparison is a plain diff of
the two files.

NORMALISED, AND ONLY THIS: the millisecond value inside `SET LOCAL lock_timeout = '<n>ms'` is the
remaining advisory-lock budget, a clock reading, and is replaced by `<n>`; whitespace runs collapse to
one space. Parameters are never recorded - they carry fresh UUIDs and codes per run.

THE OPERATIONS, one each, each on its own freshly seeded world in one mode-B clone:

* `payment_api` - `PaymentService.create_payment_internal`: idempotency, then (since 019 stage 4) the
  direct execution - owner, transaction and pair locks, the money phase's `FOR SHARE`, the delta check;
* (`payment_commit` - `PaymentEngine.commit` of an already prepared payment - was an operation here until
  019 stage 4 deleted the engine; a prepared payment no longer exists, so the operation has no subject);
* `clearing` - `ClearingService.execute_clearing_with_amount` on an engine-bound session: the pinned
  connection's session-level owner lock, its release, and the plain stop/hold read;
* `inject_event` - the mixed inject event of 018 (`_apply_due_scenario_events`): staged owner locks and
  the inject's `FOR SHARE`;
* `admin_patch`, `admin_hold_clear`, `admin_delete` - the three admin paths through the real routes;
* `staged_owner_locks` - `PaymentService.acquire_staged_equivalent_owner_locks` (the tick's entry);
* `reconciliation_baseline`, `reconciliation_reaction` - `take_baseline` and the hold reaction.

WHAT IT DOES NOT SEE: statements sent outside SQLAlchemy (the corruption helper's raw asyncpg), timing,
lock waits, and any consumer that sends no SQL of its own (`simulator.py` and `real_clearing_engine.py`
read only the refusal constants).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import event, insert, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_db
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.ledger.reconciliation import HOLD_SET, PASSED, run_scheduled_reconciliation, take_baseline
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import debt_reconciliation_results
from app.main import app
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_b4_wrong_writer_is_recorded_faithfully_postgres import (
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
from tests.ledger_corruption import corrupt
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

LIMIT = Decimal("1000.00")
ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
_LOCK_TIMEOUT = re.compile(r"lock_timeout = '\d+ms'")
_SPACES = re.compile(r"\s+")


def _normalise(statement: str) -> str:
    return _SPACES.sub(" ", _LOCK_TIMEOUT.sub("lock_timeout = '<n>ms'", statement)).strip()


class _Recorder:
    def __init__(self) -> None:
        self.armed = False
        self.statements: list[str] = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if self.armed:
            self.statements.append(_normalise(statement))


async def _record(recorder: _Recorder, operation: Callable[[], Awaitable[Any]]) -> list[str]:
    recorder.statements = []
    recorder.armed = True
    try:
        await operation()
    finally:
        recorder.armed = False
    return list(recorder.statements)


def _factory(url: str):
    engine = create_async_engine(url, isolation_level="SERIALIZABLE", poolclass=NullPool)
    return engine, async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _debt(factory, triangle, debtor: str, creditor: str, amount: str, label: str) -> None:
    row = Debt(
        id=uuid.uuid4(),
        debtor_id=getattr(triangle, debtor).id,
        creditor_id=getattr(triangle, creditor).id,
        equivalent_id=triangle.equivalent_id,
        amount=Decimal(amount),
        version=0,
    )
    async with factory() as session:
        async with debt_fixture_setup(session, label=label):
            session.add(row)
        await session.commit()


class _Admin:
    """The real admin routes over this clone: `get_db` yields a session of the probe's factory."""

    def __init__(self, factory) -> None:
        self.factory = factory

    async def __aenter__(self) -> AsyncClient:
        factory = self.factory

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.client = AsyncClient(app=app, base_url="http://test")
        return await self.client.__aenter__()

    async def __aexit__(self, *exc) -> None:
        await self.client.__aexit__(*exc)
        app.dependency_overrides.clear()


# --- the operations -------------------------------------------------------------------------------


async def _payment_api(url: str, recorder: _Recorder) -> list[str]:
    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        await _debt(factory, triangle, "a", "b", "2.00", "t1903-payment-api")
        code = await _code(factory, triangle.equivalent_id)

        async def operation() -> None:
            async with factory() as session:
                result = await PaymentService(session).create_payment_internal(
                    triangle.a.id,
                    to_pid=triangle.b.pid,
                    equivalent=code,
                    amount="5.00",
                    idempotency_key=str(uuid.uuid4()),
                )
            assert result.status == "COMMITTED", result

        return await _record(recorder, operation)
    finally:
        await engine.dispose()


async def _clearing(url: str, recorder: _Recorder) -> list[str]:
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
            async with debt_fixture_setup(session, label="t1903-cycle"):
                session.add_all(debts)
            await session.commit()
        cleared: list[Any] = []

        async def operation() -> None:
            async with factory() as session:
                cleared.append(
                    await ClearingService(session).execute_clearing_with_amount(
                        [{"debt_id": debt_id} for debt_id in ids]
                    )
                )

        statements = await _record(recorder, operation)
        assert Decimal(str(cleared[0])) == Decimal("10.00"), cleared
        return statements
    finally:
        await engine.dispose()


async def _inject_event(url: str, recorder: _Recorder) -> list[str]:
    """The mixed event of `test_p018_mixed_inject_event_is_one_operation_postgres.py`, as 018 `T1809`."""

    engine, factory = _factory(url)
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
        return {"op": "inject_debt", "from": creditor, "to": debtor, "equivalent": eq.code, "amount": amount}

    effects = [
        debt(a.pid, b.pid, "3.00"),
        {"op": "add_participant", "participant": {"id": c_pid, "name": "C"}},
        {"op": "create_trustline", "from": a.pid, "to": c_pid, "equivalent": eq.code, "limit": "50"},
        debt(a.pid, c_pid, "4.00"),
        {"op": "freeze_participant", "participant_id": b.pid},
        debt(a.pid, b.pid, "2.00"),
        {"op": "add_participant", "participant": {"id": d_pid, "name": "D"}},
        debt(a.pid, c_pid, "1.00"),
        debt(a.pid, b.pid, "1.00"),
    ]
    line = {"equivalent": eq.code, "limit": "100.00", "status": "active"}
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": a.pid}, {"id": b.pid}],
        "trustlines": [{"from": a.pid, "to": b.pid, **line}, {"from": b.pid, "to": a.pid, **line}],
        "behaviorProfiles": [],
        "events": [{"type": "inject", "time": 0, "effects": effects}],
    }
    run = _run(world, f"t1903-{n}")
    artifacts = _Artifacts()
    runner = _runner(run, scenario, artifacts)
    engine, factory = _factory(url)
    try:

        async def operation() -> None:
            async with factory() as session:
                await runner._apply_due_scenario_events(
                    session, run_id=run.run_id, run=run, scenario=scenario
                )

        statements = await _record(recorder, operation)
    finally:
        await engine.dispose()
    notes = [p["scenario"] for p in artifacts.events if p.get("type") == "note"]
    assert notes and notes[0]["stats"] == {"applied": 7, "skipped": 2, "total_amount": "6.00"}, notes
    return statements


async def _code(factory, equivalent_id) -> str:
    async with factory() as session:
        return str(await session.scalar(_select_code(equivalent_id)))


def _select_code(equivalent_id):
    from sqlalchemy import select

    return select(Equivalent.code).where(Equivalent.id == equivalent_id)


async def _admin_patch(url: str, recorder: _Recorder) -> list[str]:
    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        code = await _code(factory, triangle.equivalent_id)
        async with _Admin(factory) as client:

            async def operation() -> None:
                resp = await client.patch(
                    f"/api/v1/admin/equivalents/{code}",
                    json={"is_active": False, "reason": "t1903 probe"},
                    headers=ADMIN,
                )
                assert resp.status_code == 200, resp.text

            return await _record(recorder, operation)
    finally:
        await engine.dispose()


async def _admin_hold_clear(url: str, recorder: _Recorder) -> list[str]:
    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        code = await _code(factory, triangle.equivalent_id)
        hold_id = await hold_directly(factory, triangle.equivalent_id)
        now = datetime.now(timezone.utc)
        async with factory() as session:
            await session.execute(
                update(debt_reconciliation_results)
                .where(debt_reconciliation_results.c.id == hold_id)
                .values(is_latest=False)
            )
            await session.execute(
                insert(debt_reconciliation_results).values(
                    id=uuid.uuid4(), equivalent_id=triangle.equivalent_id, status=PASSED,
                    fingerprint="p" * 64, detail={"stand": "t1903"}, checked_at=now,
                    last_checked_at=now, is_latest=True,
                )
            )
            await session.commit()
        async with _Admin(factory) as client:

            async def operation() -> None:
                resp = await client.post(
                    f"/api/v1/admin/equivalents/{code}/integrity-hold/clear",
                    json={"reason": "t1903 probe"},
                    headers=ADMIN,
                )
                assert resp.status_code == 200, resp.text

            return await _record(recorder, operation)
    finally:
        await engine.dispose()


async def _admin_delete(url: str, recorder: _Recorder) -> list[str]:
    engine, factory = _factory(url)
    try:
        code = f"T19{uuid.uuid4().hex[:6]}".upper()
        async with factory() as session:
            session.add(Equivalent(code=code, precision=2, is_active=False, metadata_={}))
            await session.commit()
        async with _Admin(factory) as client:

            async def operation() -> None:
                resp = await client.request(
                    "DELETE",
                    f"/api/v1/admin/equivalents/{code}",
                    json={"reason": "t1903 probe"},
                    headers=ADMIN,
                )
                assert resp.status_code == 200, resp.text

            return await _record(recorder, operation)
    finally:
        await engine.dispose()


async def _staged_owner_locks(url: str, recorder: _Recorder) -> list[str]:
    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        code = await _code(factory, triangle.equivalent_id)

        async def operation() -> None:
            async with factory() as session:
                await PaymentService(session).acquire_staged_equivalent_owner_locks([code])
                await session.rollback()

        return await _record(recorder, operation)
    finally:
        await engine.dispose()


async def _reconciliation_baseline(url: str, recorder: _Recorder) -> list[str]:
    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        await _debt(factory, triangle, "a", "b", "2.00", "t1903-baseline")

        async def operation() -> None:
            async with factory() as session:
                await take_baseline(session, triangle.equivalent_id)
                await session.commit()

        return await _record(recorder, operation)
    finally:
        await engine.dispose()


async def _reconciliation_reaction(url: str, recorder: _Recorder) -> list[str]:
    """A one-atom fault after the baseline: the scheduled run verifies FAILED and the reaction holds."""

    engine, factory = _factory(url)
    try:
        triangle = await _seed_triangle(factory, trustlines=[("b", "a", LIMIT)])
        await _debt(factory, triangle, "a", "b", "2.00", "t1903-reaction")
        async with factory() as session:
            await take_baseline(session, triangle.equivalent_id)
            await session.commit()
        await corrupt(
            url,
            [
                "UPDATE debts SET amount = amount + 0.00000001 "
                f"WHERE equivalent_id = '{triangle.equivalent_id}'"
            ],
        )
        counts: list[Any] = []

        async def operation() -> None:
            counts.append(
                await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent_id])
            )

        statements = await _record(recorder, operation)
        assert counts[0][f"hold_{HOLD_SET}"] == 1, counts
        return statements
    finally:
        await engine.dispose()


_OPERATIONS: dict[str, Callable[[str, _Recorder], Awaitable[list[str]]]] = {
    "payment_api": _payment_api,
    "clearing": _clearing,
    "inject_event": _inject_event,
    "admin_patch": _admin_patch,
    "admin_hold_clear": _admin_hold_clear,
    "admin_delete": _admin_delete,
    "staged_owner_locks": _staged_owner_locks,
    "reconciliation_baseline": _reconciliation_baseline,
    "reconciliation_reaction": _reconciliation_reaction,
}

# Anti-vacuum: the lock statement each operation exists to show. An operation whose recording lacks
# its marker did not run the path this probe claims to compare.
_MARKERS = {
    "payment_api": ["pg_advisory_xact_lock($1, $2)", "pg_advisory_xact_lock($1)", "FOR SHARE"],
    "clearing": ["pg_advisory_lock($1, $2)", "pg_advisory_unlock($1, $2)"],
    "inject_event": ["pg_advisory_xact_lock($1, $2)", "FOR SHARE"],
    "admin_patch": ["pg_advisory_xact_lock($1, $2)"],
    "admin_hold_clear": ["pg_advisory_xact_lock($1, $2)", "FOR UPDATE"],
    "admin_delete": ["pg_advisory_xact_lock($1, $2)", "DELETE FROM equivalents"],
    "staged_owner_locks": ["pg_advisory_xact_lock($1, $2)"],
    "reconciliation_baseline": ["pg_advisory_xact_lock($1, $2)"],
    "reconciliation_reaction": ["pg_advisory_xact_lock($1, $2)"],
}


@pytest.mark.asyncio
async def test_t1903_statement_sequence(committed_database) -> None:
    url = committed_database.url
    recorder = _Recorder()
    event.listen(Engine, "before_cursor_execute", recorder)
    try:
        report = {name: await operation(url, recorder) for name, operation in _OPERATIONS.items()}
    finally:
        event.remove(Engine, "before_cursor_execute", recorder)

    for name, statements in report.items():
        text = "\n".join(statements)
        missing = [marker for marker in _MARKERS[name] if marker not in text]
        assert statements and not missing, (name, missing, statements)

    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT", ".local-run/test-runs/t1903/artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("T1903_PROBE_NAME", "p019_t1903_statements")
    (root / f"{name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for op, statements in report.items():
        print(op, len(statements))
