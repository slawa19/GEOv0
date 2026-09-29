"""R-024-11 (programme 024, step Ш3, `T2413.1`): what the full-equivalent checkpoint costs a payment.

ONE bounded measurement (spec, "Стадия 1 — сужение"), `slow`: it is an instrument, not a gate. The table
it writes (`p024_t2413_r02411.json` under `GEO_TEST_ARTIFACT_ROOT`) is copied into the spec verbatim;
the thresholds that read it were committed in the spec BEFORE the first timed run.

ARMS. The same world, interleaved per repetition: `as_is` runs the code as it is; `no_scan` replaces the
payment's `compute_integrity_checkpoint_for_equivalent` with a coroutine returning None - the audit row
is still written, only the two full-equivalent scans are gone (`monkeypatch -> None`, R-024-11).

WHAT IS MEASURED. (A) one checkpoint alone: statements, rows fetched, tuples PostgreSQL read
(`pg_stat_xact_user_tables`, same transaction), median ms. (B) sequential payments: median ms and
statements per payment. (C) two concurrent `pay()` whose transactions are held open together at a
barrier after every read of the binding phase and before the first write; `40001` counted PER ATTEMPT
at the driver (`handle_error`), because `pay()` retries and a final result hides the conflict. Three
schedules: disjoint pairs with the router reading the graph in the transaction (production default,
`ROUTING_GRAPH_CACHE_TTL_SECONDS = 0`), disjoint pairs with a warm route cache (isolates the checkpoint
from the router's own full read), and the SAME pair (a known conflict).

WHY THE STAND CAN SEE WHAT IT MEASURES (lesson of 010; §15 AGENTS.md), each asserted, not assumed:
M1 every measured attempt reports `transaction_isolation = serializable` on ITS OWN session;
M2 a read-write-only (write skew) schedule on this engine yields a `40001` the same counter counts;
M3 the same-pair schedule conflicts in >= 18/20 repetitions of each arm;
M4 both transactions reached the barrier in >= 18/20 repetitions of each schedule and arm;
M5 `as_is` computed checkpoints (>= 2 per payment attempt that reached the audit), `no_scan` none.
Twenty repetitions describe this stand; they do not prove the absence of conflicts anywhere else.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import statistics
import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.payments.service as payments_module
from app.config import settings
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService, _payment_db_sqlstate
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.debt_setup import debt_fixture_setup

pytestmark = [pytest.mark.slow]

REPS = 20
FILLER = 200  # background participants of the same equivalent: 400 trust lines, 200 debts
_ORIGINAL_CHECKPOINT = compute_integrity_checkpoint_for_equivalent
#: Before `T2413.2` the payment module binds the scan and the stand runs both arms; after it only `as_is`
#: exists (the same code path, now without the scan) - the "after" of the before/after pair.
_SCAN_IN_PAYMENT = hasattr(payments_module, "compute_integrity_checkpoint_for_equivalent")
_turn: contextvars.ContextVar[dict | None] = contextvars.ContextVar("p024_t2413_turn", default=None)


class _Probe:
    """Driver-level counters for the arm in progress (both tasks of a run share the arm)."""

    def __init__(self) -> None:
        self.statements = 0
        self.sqlstates: list[str] = []
        self.checkpoints = 0
        self.isolation: list[str] = []
        self.overlap = 0
        self.overlap_missed = 0

    def reset(self) -> None:
        self.__init__()


@pytest_asyncio.fixture
async def factory(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=6, max_overflow=0, isolation_level="SERIALIZABLE"
    )
    try:
        yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


async def _seed(factory) -> dict:
    n = uuid.uuid4().hex[:6].upper()
    async with factory() as s:
        eq = Equivalent(code=f"R11{n}", precision=2, is_active=True)
        names = ["S1", "R1", "S2", "R2"] + [f"F{i}" for i in range(FILLER)]
        people = {
            name: Participant(pid=f"R11_{name}_{n}", display_name=name, public_key=f"pk_r11_{name}_{n}",
                              type="person", status="active")
            for name in names
        }
        s.add_all([eq, *people.values()])
        await s.flush()
        lines = [("R1", "S1"), ("R2", "S2")]
        lines += [(f"F{i}", f"F{(i + k) % FILLER}") for i in range(FILLER) for k in (1, 7)]
        for creditor, debtor in lines:
            s.add(TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                            equivalent_id=eq.id, limit=Decimal("1000.00"), status="active"))
        await s.flush()
        pairs = [("S1", "R1"), ("S2", "R2")] + [(f"F{(i + 1) % FILLER}", f"F{i}") for i in range(FILLER)]
        debts = [Debt(debtor_id=people[d].id, creditor_id=people[c].id, equivalent_id=eq.id,
                      amount=Decimal("100.00")) for d, c in pairs]
        async with debt_fixture_setup(s, label="p024-t2413-stand"):
            s.add_all(debts)
        await s.commit()
    async with factory() as s:
        for table in ("debts", "trust_lines", "participants", "transactions"):
            await s.execute(text(f"ANALYZE {table}"))
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return {"code": eq.code, "eq_id": eq.id, **{k: people[k] for k in ("S1", "R1", "S2", "R2")}}


def _request(world, receiver: str) -> PaymentCreateRequest:
    return PaymentCreateRequest(tx_id=str(uuid.uuid4()), to=world[receiver].pid, equivalent=world["code"],
                                amount="1.00", signature="__internal__")


async def _pay(factory, world, sender: str, receiver: str) -> str:
    result = await PaymentService.pay(factory, world[sender].id, _request(world, receiver), require_signature=False)
    return result.status


async def _write_skew_probe(factory) -> None:
    """M2: a schedule that only SSI refuses - each transaction reads the row the other one writes."""

    async with factory() as s:
        await s.execute(text("CREATE TABLE p024_ws (id INT PRIMARY KEY, v INT)"))
        await s.execute(text("INSERT INTO p024_ws VALUES (1, 0), (2, 0)"))
        await s.commit()
    async with factory() as a, factory() as b:
        try:
            await a.execute(text("SELECT v FROM p024_ws WHERE id = 1"))
            await b.execute(text("SELECT v FROM p024_ws WHERE id = 2"))
            await a.execute(text("UPDATE p024_ws SET v = 1 WHERE id = 2"))
            await b.execute(text("UPDATE p024_ws SET v = 1 WHERE id = 1"))
            await a.commit()
            await b.commit()
        except DBAPIError:
            await a.rollback()
            await b.rollback()


async def _pair(factory, world, probe: _Probe, flows) -> list[str]:
    barrier = asyncio.Barrier(2)

    async def one(sender, receiver):
        _turn.set({"barrier": barrier, "used": False})
        try:
            return await _pay(factory, world, sender, receiver)
        except Exception as exc:  # recorded, not hidden: the table shows final outcomes too
            return type(exc).__name__

    return list(await asyncio.gather(*(one(s, r) for s, r in flows)))


@pytest.mark.asyncio
async def test_r024_11_checkpoint_cost_and_ssi_conflicts(factory, monkeypatch) -> None:
    engine = factory.kw["bind"]
    probe = _Probe()

    def count_statement(*_a, **_k):
        probe.statements += 1

    def count_error(ctx):
        code = _payment_db_sqlstate(ctx.sqlalchemy_exception or ctx.original_exception)
        if code:
            probe.sqlstates.append(code)

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    event.listen(engine.sync_engine, "handle_error", count_error)

    # M2 first: the counter sees an rw-only serialization failure on this engine.
    await _write_skew_probe(factory)
    assert probe.sqlstates.count("40001") >= 1, f"M2: write skew not counted as 40001: {probe.sqlstates}"

    world = await _seed(factory)
    original_refuse = MoneyBoundary.refuse_inactive_equivalents

    async def held_open(self, equivalent_ids):
        await original_refuse(self, equivalent_ids)
        turn = _turn.get()
        if turn is None or turn["used"]:
            return
        turn["used"] = True
        probe.isolation.append((await self.session.execute(text("SHOW transaction_isolation"))).scalar_one())
        try:
            await asyncio.wait_for(turn["barrier"].wait(), timeout=5)
            probe.overlap += 1
        except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
            probe.overlap_missed += 1

    async def counted_checkpoint(session, *, equivalent_id):
        probe.checkpoints += 1
        return await _ORIGINAL_CHECKPOINT(session, equivalent_id=equivalent_id)

    async def no_scan(session, *, equivalent_id):
        return None

    monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", held_open)
    arms = {"as_is": counted_checkpoint, "no_scan": no_scan} if _SCAN_IN_PAYMENT else {"as_is": None}
    report: dict = {"scan_in_payment": _SCAN_IN_PAYMENT, "reps": REPS, "filler_participants": FILLER, "commit_retry_attempts": settings.COMMIT_RETRY_ATTEMPTS}

    # (A) one checkpoint alone, in one transaction.
    samples, cost = [], {}
    for _ in range(REPS):
        async with factory() as s:
            stats = "SELECT relname, seq_tup_read + coalesce(idx_tup_fetch, 0) FROM pg_stat_xact_user_tables " \
                    "WHERE relname IN ('debts', 'trust_lines')"
            before = dict((await s.execute(text(stats))).all())
            probe.reset()
            started = time.perf_counter()
            cp = await _ORIGINAL_CHECKPOINT(s, equivalent_id=world["eq_id"])
            samples.append((time.perf_counter() - started) * 1000)
            statements = probe.statements
            after = dict((await s.execute(text(stats))).all())
            cost = {"statements": statements,
                    "rows_fetched": cp.invariants_status["debts_count"] + cp.invariants_status["trustlines_count"],
                    "tuples_read": {k: after.get(k, 0) - before.get(k, 0) for k in ("debts", "trust_lines")}}
            await s.rollback()
    report["checkpoint_alone"] = {**cost, "median_ms": round(statistics.median(samples), 2)}

    # (B) sequential payments, arms interleaved.
    seq = {arm: {"ms": [], "statements": []} for arm in arms}
    for _ in range(REPS):
        for arm, fn in arms.items():
            if fn is not None:
                monkeypatch.setattr(payments_module, "compute_integrity_checkpoint_for_equivalent", fn)
            probe.reset()
            started = time.perf_counter()
            assert await _pay(factory, world, "S1", "R1") == "COMMITTED"
            seq[arm]["ms"].append((time.perf_counter() - started) * 1000)
            seq[arm]["statements"].append(probe.statements)
    report["sequential"] = {arm: {"median_ms": round(statistics.median(v["ms"]), 2),
                                  "median_statements": statistics.median(v["statements"])} for arm, v in seq.items()}

    # (C) concurrent schedules.
    schedules = {
        "disjoint_router_in_tx": ([("S1", "R1"), ("S2", "R2")], 0),
        "disjoint_warm_route_cache": ([("S1", "R1"), ("S2", "R2")], 3600),
        "same_pair_known_conflict": ([("S1", "R1"), ("S1", "R1")], 0),
    }
    table: dict = {}
    for _ in range(REPS):
        for name, (flows, ttl) in schedules.items():
            monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", ttl)
            for arm, fn in arms.items():
                if fn is not None:
                    monkeypatch.setattr(payments_module, "compute_integrity_checkpoint_for_equivalent", fn)
                PaymentRouter.invalidate_cache(world["code"])
                if ttl:
                    async with factory() as warm:
                        await PaymentRouter(warm).build_graph(world["code"])
                        await warm.rollback()
                probe.reset()
                outcomes = await _pair(factory, world, probe, flows)
                row = table.setdefault(f"{name}/{arm}", {"reps_with_40001": 0, "attempts_40001": 0,
                                                          "other_sqlstates": [], "overlap": 0, "overlap_missed": 0,
                                                          "checkpoints": 0, "outcomes": {}})
                conflicts = probe.sqlstates.count("40001")
                row["attempts_40001"] += conflicts
                row["reps_with_40001"] += 1 if conflicts else 0
                row["other_sqlstates"] += [c for c in probe.sqlstates if c != "40001"]
                row["overlap"] += probe.overlap
                row["overlap_missed"] += probe.overlap_missed
                row["checkpoints"] += probe.checkpoints
                for outcome in outcomes:
                    row["outcomes"][outcome] = row["outcomes"].get(outcome, 0) + 1
                # M1, on every attempt that reached the hold point.
                assert probe.isolation and set(probe.isolation) == {"serializable"}, probe.isolation
    report["concurrent"] = table

    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/p024sh3/artifacts")
    root.mkdir(parents=True, exist_ok=True)
    (root / "p024_t2413_r02411.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))

    # M3-M5: the stand could see what it measured. Checked after the table is written, so a blind
    # stand still leaves its numbers behind for the record.
    for arm in arms:
        assert table[f"same_pair_known_conflict/{arm}"]["reps_with_40001"] >= 18, ("M3", arm, table)
        for name in schedules:
            assert table[f"{name}/{arm}"]["overlap"] >= 2 * 18, ("M4", name, arm, table)
            if arm == "as_is" and _SCAN_IN_PAYMENT:
                assert table[f"{name}/{arm}"]["checkpoints"] >= 2 * 2 * REPS, ("M5", name, table)
            else:  # after `T2413.2` the payment cannot reach the scan at all: R-024-12 holds that
                assert table[f"{name}/{arm}"]["checkpoints"] == 0, ("M5", name, table)
