"""P027 measurement stand: why disjoint payments conflict under SERIALIZABLE, and what it costs a user.

An INSTRUMENT, not a gate (`slow`). It changes nothing in `app/`; every ablation is a monkeypatch, a setting or a
session GUC set here. Its numbers go to `p027_*.json` under `GEO_TEST_ARTIFACT_ROOT` and are printed.

Q1 ATTRIBUTION (`test_q1_siread_attribution`, graph size parametrized). Two `pay()` calls are held open together at
a barrier placed AFTER the payment's last statement of the money phase (the integrity audit row, flushed) and BEFORE
its commit - every read and write of the attempt is done. At the barrier a third, autocommit connection snapshots
`pg_locks` (`mode = 'SIReadLock'`) of both backends, and each payment session reports its own
`pg_stat_xact_user_tables` (seq/idx scans, rows written by THIS transaction). A relation one payment holds at
relation (or whole-index page) granularity and the other one WRITES is the rw-antidependency SSI acts on.
Cells: route cache cold (TTL=0, production) / warm (TTL=3600, prebuilt); `enable_seqscan` on / off (off is a
DIAGNOSTIC only - production never runs it); the same pair as the positive control.

Q2 USER-VISIBLE HARM (`test_q2_disjoint_payers_throughput`). N disjoint payers (each its own 1- or 2-hop route),
`PER_PAYER` sequential payments each, all payers concurrent, no barrier, production retry settings. Per cell:
committed, exhausted 409/E008, attempts with 40001, p50/p95 latency, throughput.

Q3 CLEARING vs PAYMENTS (`test_q3_clearing_against_payments`). N=5 payers as in Q2 while one clearing pass runs over
`TRIANGLES` cycles elsewhere in the graph, and once where some cycles share a payer's first-hop pair. Payment waits
for the shared equivalent lock and clearing waits for the exclusive one are timed at the lock call.

MECHANISM ASSERTS (AGENTS.md §15, "can the stand see the outcome"): M1 every held attempt is `serializable`; M2 the
same pair yields 40001 (positive control); M3 both payments reached the barrier; M4 the warm cell really skipped the
router's graph build on first attempts and the cold cell really built it; M5 `enable_seqscan` is `off` exactly in
the diagnostic cells (SHOW, in the payment session); M6 every payment the stand meant to run was run and every
40001 the driver reported is attributed to a payer or to the clearing (no untagged error).
Repetitions describe THIS stand (one PostgreSQL 16 on localhost, one process); they prove nothing elsewhere.

ACCEPTANCE (027 `T2701`, spec "Verification plan" 1): R-027-1/2 (Q2, N=10, production cell: committed >= 95 %, E007 <= 5 %),
R-027-3 (Q1, disjoint, production cache: no attempt retried for a classified `40001`), R-027-4 (Q3, the payers' wait
next to a clearing: p95 <= 50 ms over >= 20 observations). Conflicts are counted by their classified cause per attempt
at `pay()`'s retry point (`_conflict_cause`), never by `handle_error`. Every cell also RECONCILES the final debts with
the committed payments' routes (pair nets; participant positions where a clearing ran). The verdict goes into the
artifact before any assert.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import statistics
import time
import uuid
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.clearing.runner import run_clearing_pass
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService, _conflict_cause, _payment_db_sqlstate
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.debt_setup import debt_fixture_setup

pytestmark = [pytest.mark.slow, pytest.mark.timeout(1500)]

Q1_REPS = 10
PER_PAYER = 20
TRIANGLES = 40
_who: contextvars.ContextVar[str | None] = contextvars.ContextVar("p027_who", default=None)
_FAILURES: list[str] = []
_hold: contextvars.ContextVar[dict | None] = contextvars.ContextVar("p027_hold", default=None)
_COMMITTED: list = []  # the PaymentResult of every COMMITTED payment since the last reset


def _artifact(name: str, report: dict) -> None:
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/p027meas/artifacts")
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n=== {name} ===\n" + json.dumps(report, indent=2, default=str))


class _Probe:
    def __init__(self) -> None:
        self.errors: Counter = Counter()  # (who, sqlstate) -> n
        self.builds = 0
        self.attempts: Counter = Counter()  # who -> pay attempts
        self.lock_wait: dict[str, list[float]] = defaultdict(list)  # who -> seconds waiting at the equivalent lock
        self.causes: Counter = Counter()  # (who, classified conflict cause) -> attempts, at pay()'s retry point

    def reset(self) -> None:
        self.__init__()

    def count(self, code: str, who: str | None = None) -> int:
        return sum(n for (w, c), n in self.errors.items() if c == code and (who is None or w == who))


@pytest_asyncio.fixture
async def stand(committed_database, monkeypatch):
    url = committed_database.url
    engines = {
        "seq_on": create_async_engine(url, pool_size=16, max_overflow=4, isolation_level="SERIALIZABLE"),
        "seq_off": create_async_engine(url, pool_size=4, max_overflow=0, isolation_level="SERIALIZABLE",
                                       connect_args={"server_settings": {"enable_seqscan": "off"}}),
    }
    observer = create_async_engine(url, pool_size=1, max_overflow=0, isolation_level="AUTOCOMMIT")
    probe = _Probe()

    def count_error(ctx):
        code = _payment_db_sqlstate(ctx.sqlalchemy_exception or ctx.original_exception)
        if code:
            probe.errors[(_who.get(), code)] += 1

    for e in engines.values():
        event.listen(e.sync_engine, "handle_error", count_error)

    original_build = PaymentRouter._build_graph_impl

    async def counted_build(self, *a, **k):
        probe.builds += 1
        return await original_build(self, *a, **k)

    original_attempt = PaymentService._pay_attempt

    async def counted_attempt(self, *a, **k):
        probe.attempts[_who.get()] += 1
        return await original_attempt(self, *a, **k)

    original_retry = PaymentService._retry_or_none

    def classified_retry(self, exc, **k):
        probe.causes[(_who.get(), _conflict_cause(exc))] += 1
        return original_retry(self, exc, **k)

    monkeypatch.setattr(PaymentService, "_retry_or_none", classified_retry)
    original_shared = MoneyBoundary._acquire_shared_equivalent_locks_in_order
    original_exclusive = MoneyBoundary.acquire_exclusive_equivalent_session_lock

    async def timed_shared(self, *a, **k):
        t = time.perf_counter()
        try:
            return await original_shared(self, *a, **k)
        finally:
            probe.lock_wait[_who.get() or "?"].append(time.perf_counter() - t)

    async def timed_exclusive(self, *a, **k):
        t = time.perf_counter()
        try:
            return await original_exclusive(self, *a, **k)
        finally:
            probe.lock_wait["clearing_exclusive"].append(time.perf_counter() - t)

    async def hold_here(session, where: str) -> None:
        hold = _hold.get()
        if hold is None or hold["used"] or hold["where"] != where:
            return
        hold["used"] = True
        s = session
        if where == "late":
            await s.flush()
        me = {
            "pid": (await s.execute(text("SELECT pg_backend_pid()"))).scalar_one(),
            "isolation": (await s.execute(text("SHOW transaction_isolation"))).scalar_one(),
            "enable_seqscan": (await s.execute(text("SHOW enable_seqscan"))).scalar_one(),
            "stats": {r[0]: dict(seq=r[1], idx=r[2] or 0, ins=r[3], upd=r[4], dele=r[5]) for r in (await s.execute(text(
                "SELECT relname, seq_scan, idx_scan, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_xact_user_tables "
                "WHERE seq_scan + coalesce(idx_scan, 0) + n_tup_ins + n_tup_upd + n_tup_del > 0"))).all()},
        }
        shared = hold["shared"]
        shared["members"].append(me)
        try:
            index = await asyncio.wait_for(shared["barrier"].wait(), timeout=5)
        except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
            shared["missed"] += 1
            return
        if index == 0:
            pids = [m["pid"] for m in shared["members"]]
            t = time.perf_counter()
            try:
                async with observer.connect() as c:
                    rows = (await c.execute(text(
                        "SELECT l.pid, l.locktype, c.relname, c.relkind, l.page, l.tuple, c.relpages FROM pg_locks l "
                        "LEFT JOIN pg_class c ON c.oid = l.relation WHERE l.mode = 'SIReadLock' AND l.pid = ANY(:p)"),
                        {"p": pids})).all()
                shared["siread"] = [tuple(r) for r in rows]
            finally:
                shared["snap_ms"] = round((time.perf_counter() - t) * 1000, 1)
                shared["snap"].set()
        else:
            await asyncio.wait_for(shared["snap"].wait(), timeout=10)

    original_audit = PaymentService._write_integrity_audit
    original_refuse = MoneyBoundary.refuse_inactive_equivalents

    async def held_audit(self, *a, **k):
        await original_audit(self, *a, **k)
        await hold_here(self.session, "late")

    async def held_refuse(self, *a, **k):
        await original_refuse(self, *a, **k)
        await hold_here(self.session, "early")

    monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", held_refuse)
    monkeypatch.setattr(PaymentRouter, "_build_graph_impl", counted_build)
    monkeypatch.setattr(PaymentService, "_pay_attempt", counted_attempt)
    monkeypatch.setattr(MoneyBoundary, "_acquire_shared_equivalent_locks_in_order", timed_shared)
    monkeypatch.setattr(MoneyBoundary, "acquire_exclusive_equivalent_session_lock", timed_exclusive)
    monkeypatch.setattr(PaymentService, "_write_integrity_audit", held_audit)
    factories = {k: async_sessionmaker(bind=e, class_=AsyncSession, expire_on_commit=False, autoflush=False)
                 for k, e in engines.items()}
    try:
        yield {"factories": factories, "probe": probe, "observer": observer}
    finally:
        for e in [*engines.values(), observer]:
            await e.dispose()


async def _seed(factory, *, filler: int, payers: int, triangles: int = 0, shared_triangles: int = 0) -> dict:
    """Filler graph (2 lines per participant, an ACYCLIC debt chain), disjoint payer routes, clearing triangles."""

    n = uuid.uuid4().hex[:6].upper()
    people: dict[str, Participant] = {}

    def person(name: str) -> Participant:
        if name not in people:
            people[name] = Participant(pid=f"P27_{name}_{n}", display_name=name, public_key=f"pk27_{name}_{n}",
                                       type="person", status="active")
        return people[name]

    lines: list[tuple[str, str, str]] = []  # (creditor, debtor, limit)
    debts: list[tuple[str, str, str]] = []  # (debtor, creditor, amount)
    for i in range(filler):
        person(f"F{i}")
    for i in range(filler):
        for k in (1, 7):
            lines.append((f"F{i}", f"F{(i + k) % filler}", "1000.00"))
    debts += [(f"F{i + 1}", f"F{i}", "100.00") for i in range(filler - 1)]  # chain, no wrap: no cycle
    routes = []
    for j in range(payers):
        path = [f"S{j}", f"R{j}"] if j % 2 == 0 else [f"S{j}", f"M{j}", f"R{j}"]
        for name in path:
            person(name)
        for payer, payee in zip(path, path[1:]):
            lines.append((payee, payer, "100000.00"))  # creditor = payee, debtor = payer
        routes.append(path)
    for t in range(triangles):
        if t < shared_triangles:  # the cycle shares the payer's first-hop pair
            a, b, c = routes[t][0], routes[t][1], f"Y{t}"
        else:
            a, b, c = f"Ca{t}", f"Cb{t}", f"Cc{t}"
        for name in (a, b, c):
            person(name)
        for debtor, creditor in ((a, b), (b, c), (c, a)):
            if (creditor, debtor) not in {(x, y) for x, y, _ in lines}:
                lines.append((creditor, debtor, "1000.00"))
            debts.append((debtor, creditor, "10.00"))
    async with factory() as s:
        eq = Equivalent(code=f"P27{n}", precision=2, is_active=True)
        s.add_all([eq, *people.values()])
        await s.flush()
        for creditor, debtor, limit in lines:
            s.add(TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                            equivalent_id=eq.id, limit=Decimal(limit), status="active"))
        await s.flush()
        rows = [Debt(debtor_id=people[d].id, creditor_id=people[c].id, equivalent_id=eq.id, amount=Decimal(a))
                for d, c, a in debts]
        async with debt_fixture_setup(s, label="p027-stand"):
            s.add_all(rows)
        await s.commit()
    async with factory() as s:
        for table in ("debts", "trust_lines", "participants", "transactions", "equivalents"):
            await s.execute(text(f"ANALYZE {table}"))
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return {"code": eq.code, "eq_id": eq.id, "people": people, "routes": routes,
            "sizes": {"participants": len(people), "trust_lines": len(lines), "debts": len(debts)}}


HISTORY = 3000  # real payments over the filler before measuring: a hub with a past, not a fresh database
STREAMS = 1  # sequential: the past is not itself a contention experiment


async def _history(factory, observer, world, monkeypatch, count: int) -> dict:
    """`count` committed real payments F(i+1) -> F(i) (the debt chain's direction: no cycle), then VACUUM ANALYZE.

    The route cache is pinned warm for speed only (TTL=3600 and the post-commit invalidation disabled), and restored.
    Grows `transactions`, `debt_operations`, `debt_journal_entries`, `integrity_audit_log` the way production does."""

    filler = sum(1 for k in world["people"] if k.startswith("F"))
    if count:
        monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 3600)
        await _warm(factory, world)
        original = PaymentRouter.__dict__["invalidate_cache"]
        monkeypatch.setattr(PaymentRouter, "invalidate_cache", classmethod(lambda cls, code=None: None))
        done = Counter()

        async def stream(k):
            _who.set("history")
            i = k
            while done["COMMITTED"] < count and sum(done.values()) < 2 * count:
                a = i % (filler - 1)
                outcome, _ = await _pay(factory, world, f"F{a + 1}", f"F{a}", amount="0.01")
                done[outcome] += 1
                i += STREAMS * 7

        await asyncio.gather(*(stream(k) for k in range(STREAMS)))
        monkeypatch.setattr(PaymentRouter, "invalidate_cache", original)
        monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 0)
        PaymentRouter.invalidate_cache(world["code"])
    async with observer.connect() as c:
        for table in ("transactions", "debt_operations", "debt_operation_equivalents", "debt_journal_entries",
                      "integrity_audit_log", "debts", "trust_lines", "participants", "equivalents"):
            await c.execute(text(f"VACUUM ANALYZE {table}"))
        rows = {t: (await c.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() for t in (
            "transactions", "debt_operations", "debt_journal_entries", "integrity_audit_log", "debts", "trust_lines")}
        pages = dict((await c.execute(text(
            "SELECT relname, relpages FROM pg_class WHERE relname IN ('idx_transactions_tx_id', 'pk_debt_operations', "
            "'ix_debt_operations_open', 'debts_pkey', 'idx_debts_debtor', 'idx_debts_creditor', 'transactions', 'debts')"))).all())
    return {"history_payments": dict(done) if count else {}, "rows": rows, "relpages": pages}


async def _pay(factory, world, sender: str, receiver: str, amount: str = "1.00") -> tuple[str, float]:
    started = time.perf_counter()
    try:
        result = await PaymentService.pay(
            factory, world["people"][sender].id,
            PaymentCreateRequest(tx_id=str(uuid.uuid4()), to=world["people"][receiver].pid, equivalent=world["code"],
                                 amount=amount, signature="__internal__"),
            require_signature=False)
        outcome = result.status
        if outcome == "COMMITTED":
            _COMMITTED.append(result)
    except Exception as exc:  # recorded, not hidden
        code = getattr(getattr(exc, "code", None), "value", getattr(exc, "code", None))
        outcome = f"{type(exc).__name__}:{code}"
        _FAILURES.append(f"{outcome} {str(exc)[:160]} | cause={type(exc.__cause__).__name__}:{str(exc.__cause__)[:160]}")
    return outcome, time.perf_counter() - started


async def _nets(factory, world) -> Counter:
    """(a, b) -> what a owes b net, from the debts table of the world's equivalent."""
    pid = {p.id: p.pid for p in world["people"].values()}
    async with factory() as s:  # READ COMMITTED: a SERIALIZABLE full read here exhausts the predicate-lock table
        await s.connection(execution_options={"isolation_level": "READ COMMITTED"})
        rows = (await s.execute(text("SELECT debtor_id, creditor_id, amount FROM debts WHERE equivalent_id = :e"),
                                {"e": world["eq_id"]})).all()
        await s.rollback()
    net: Counter = Counter()
    for d, c, a in rows:
        net[(pid[d], pid[c])] += a
        net[(pid[c], pid[d])] -= a
    return net


async def _reconcile(factory, world, before: Counter, *, pairwise: bool = True) -> dict:
    """Final debts vs the committed payments: pair nets move by each hop (pairwise), or - a clearing ran, which keeps
    positions but not pair nets - each participant's position moves by what it paid and received."""
    after, expected = await _nets(factory, world), Counter(before)
    for r in _COMMITTED:
        for route in r.routes:
            hops = list(zip(route.path, route.path[1:])) if pairwise else [(route.path[0], route.path[-1])]
            for u, v in hops:
                expected[(u, v)] += Decimal(route.amount)
                expected[(v, u)] -= Decimal(route.amount)
    if not pairwise:
        positions = []
        for m in (after, expected):
            pos: Counter = Counter()
            for (x, _), v in m.items():
                pos[x] += v
            positions.append(pos)
        after, expected = positions
    wrong = {str(k): [str(expected[k]), str(after[k])] for k in set(after) | set(expected) if expected[k] != after[k]}
    return {"committed": len(_COMMITTED), "pairwise": pairwise, "mismatches": wrong}


async def _warm(factory, world) -> None:
    PaymentRouter.invalidate_cache(world["code"])
    async with factory() as s:
        await PaymentRouter(s).build_graph(world["code"], use_shared_cache=True)
        await s.rollback()


async def _pg_settings(observer) -> dict:
    async with observer.connect() as c:
        return {name: (await c.execute(text(f"SHOW {name}"))).scalar_one() for name in (
            "server_version", "max_pred_locks_per_transaction", "max_pred_locks_per_relation", "max_pred_locks_per_page")}


# ------------------------------------------------------------------------------------------------------------- Q1


def _summarize_locks(members: list[dict], siread: list[tuple]) -> dict:
    """Per payment (A/B by barrier arrival): relation -> granularity; plus written relations of each."""

    by_pid = {m["pid"]: tag for tag, m in zip("AB", members)}
    held: dict[str, dict[str, dict]] = {"A": {}, "B": {}}
    for pid, locktype, relname, relkind, page, tup, relpages in siread:
        tag = by_pid.get(pid)
        if tag is None or relname is None:
            continue
        e = held[tag].setdefault(relname, {"relation": 0, "page": 0, "tuple": 0, "relkind": relkind,
                                            "relpages": relpages})
        e[locktype] = e.get(locktype, 0) + 1
    writes = {tag: {r for r, st in m["stats"].items() if st["ins"] + st["upd"] + st["dele"]}
              for tag, m in zip("AB", members)}
    return {"held": held, "writes": {k: sorted(v) for k, v in writes.items()},
            "seq_scanned": {tag: sorted(r for r, st in m["stats"].items() if st["seq"]) for tag, m in zip("AB", members)}}


def _granularity(e: dict) -> str:
    if e["relation"]:
        return "relation"
    if e["page"]:
        return "page(all)" if e["relpages"] and e["page"] >= e["relpages"] else "page"
    return "tuple"


@pytest.mark.asyncio
@pytest.mark.parametrize("filler,history", [(200, 0), (2000, 0), (2000, HISTORY)], ids=["small", "large", "large_hist"])
async def test_q1_siread_attribution(stand, filler, history, monkeypatch) -> None:
    probe: _Probe = stand["probe"]
    fs = stand["factories"]
    world = await _seed(fs["seq_on"], filler=filler, payers=4)
    world["history"] = await _history(fs["seq_on"], stand["observer"], world, monkeypatch, history)
    (s1, r1), (s2, r2) = world["routes"][0], world["routes"][2]  # two disjoint 1-hop routes
    # Hold point: "late" = after the last money statement (every read AND write done); "early" = after the stop/hold
    # read, before the first write (the R-024-11 position) - the same pair cannot meet "late": its second payment
    # waits on the first one's debt row lock.
    cells = {
        "disjoint/ttl0/seq_on/late": ([(s1, r1), (s2, r2)], 0, "seq_on", "late"),
        "disjoint/warm/seq_on/late": ([(s1, r1), (s2, r2)], 3600, "seq_on", "late"),
        "disjoint/ttl0/seq_off_DIAG/late": ([(s1, r1), (s2, r2)], 0, "seq_off", "late"),
        "disjoint/warm/seq_off_DIAG/late": ([(s1, r1), (s2, r2)], 3600, "seq_off", "late"),
        "disjoint/ttl0/seq_on/early": ([(s1, r1), (s2, r2)], 0, "seq_on", "early"),
        "same_pair/ttl0/seq_on/early": ([(s1, r1), (s1, r1)], 0, "seq_on", "early"),
    }
    report: dict = {"filler": filler, "sizes": world["sizes"], "history": world["history"], "reps": Q1_REPS,
                    "pg": await _pg_settings(stand["observer"]), "cells": {}}
    tables_of = await _index_tables(stand["observer"])
    for _ in range(Q1_REPS):
        for name, (flows, ttl, fk, where) in cells.items():
            monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", ttl)
            f = fs[fk]
            PaymentRouter.invalidate_cache(world["code"])
            if ttl:
                await _warm(f, world)
            probe.reset()
            _COMMITTED.clear()
            before = await _nets(f, world)
            shared = {"barrier": asyncio.Barrier(2), "members": [], "missed": 0, "siread": None,
                      "snap": asyncio.Event()}

            async def one(i, sender, receiver):
                _who.set(f"p{i}")
                _hold.set({"used": False, "shared": shared, "where": where})
                return await _pay(f, world, sender, receiver)

            _FAILURES.clear()
            outcomes = await asyncio.gather(*(one(i, s, r) for i, (s, r) in enumerate(flows)))
            row = report["cells"].setdefault(name, {
                "reps_with_40001": 0, "errors_40001": 0, "other_sqlstates": Counter(), "outcomes": Counter(),
                "overlap": 0, "missed": 0, "builds_first_attempts": [], "attempts": 0, "isolation": Counter(),
                "enable_seqscan": Counter(), "locks": Counter(), "conflict_candidates": Counter(),
                "seq_scanned": Counter(), "latency_ms": [], "failures": [], "snap_ms": [], "shared_pages": Counter(),
                "reps_with_classified_40001": 0, "classified_causes": Counter(), "reconciled_committed": 0,
                "reconcile_mismatches": []})
            recon = await _reconcile(f, world, before)
            row["reconciled_committed"] += recon["committed"]
            row["reconcile_mismatches"] += [recon["mismatches"]] if recon["mismatches"] else []
            for (_w, cause), k in probe.causes.items():
                row["classified_causes"][cause] += k
            row["reps_with_classified_40001"] += any(c == "40001" for (_w, c) in probe.causes)
            row["latency_ms"] += [round(t * 1000) for _, t in outcomes]
            row["failures"] += list(_FAILURES)
            row["snap_ms"].append(shared.get("snap_ms"))
            conflicts = probe.count("40001")
            row["errors_40001"] += conflicts
            row["reps_with_40001"] += bool(conflicts)
            for (w, c), k in probe.errors.items():
                if c != "40001":
                    row["other_sqlstates"][c] += k
            row["outcomes"].update(o for o, _ in outcomes)
            row["missed"] += shared["missed"]
            attempts = sum(probe.attempts.values())
            row["attempts"] += attempts
            row["builds_first_attempts"].append(probe.builds - (attempts - len(flows)))
            members = shared["members"]
            if len(members) == 2 and shared["siread"] is not None:
                row["overlap"] += 1
                for m in members:
                    row["isolation"][m["isolation"]] += 1
                    row["enable_seqscan"][m["enable_seqscan"]] += 1
                summary = _summarize_locks(members, shared["siread"])
                for tag in "AB":
                    for rel, e in summary["held"][tag].items():
                        row["locks"][f"{tag}:{rel}:{_granularity(e)}"] += 1
                    for rel in summary["seq_scanned"][tag]:
                        row["seq_scanned"][f"{tag}:{rel}"] += 1
                # A read lock on a relation (or on pages of an index) whose table the OTHER payment writes.
                pages = defaultdict(lambda: {"A": set(), "B": set()})
                for pid, locktype, relname, relkind, page, tup, relpages in shared["siread"]:
                    tag = {m["pid"]: t for t, m in zip("AB", members)}.get(pid)
                    if tag and locktype == "page" and relkind == "i":
                        pages[relname][tag].add(page)
                for rel, pp in pages.items():
                    if pp["A"] & pp["B"]:
                        row["shared_pages"][rel] += 1
                for reader, writer in (("A", "B"), ("B", "A")):
                    for rel, e in summary["held"][reader].items():
                        table = tables_of.get(rel, rel)
                        if table in summary["writes"][writer] and _granularity(e) in ("relation", "page(all)", "page"):
                            row["conflict_candidates"][f"{reader}-reads/{writer}-writes:{rel}({_granularity(e)})"] += 1
                if not report.get("first_snapshot", {}).get(name):
                    report.setdefault("first_snapshot", {})[name] = {"summary": summary,
                                                                     "stats": [m["stats"] for m in members]}
    prod = report["cells"]["disjoint/ttl0/seq_on/late"]
    report["acceptance"] = {"R-027-3": {"cell": "disjoint/ttl0/seq_on/late", "reps_with_classified_40001":
                                        prod["reps_with_classified_40001"], "threshold": 0, "of": Q1_REPS}}
    _artifact(f"p027_q1_filler{filler}_hist{history}.json", report)

    for name, row in report["cells"].items():
        # A payment refused before the hold point (E007 "Routing timed out": a 2 000-participant graph build over the
        # 500 ms routing budget) cannot meet the barrier; such reps are counted, not hidden, and the rest must meet.
        early_fail = sum(1 for f in row["failures"] if "Routing timed out" in f)
        assert row["overlap"] >= Q1_REPS // 2 and row["overlap"] + early_fail >= Q1_REPS - 1, ("M3", name, row)
        assert set(row["isolation"]) == {"serializable"}, ("M1", name, row["isolation"])
        assert set(row["enable_seqscan"]) == ({"off"} if "seq_off" in name else {"on"}), ("M5", name, row)
        firsts = row["builds_first_attempts"]
        assert all(b == (0 if "/warm/" in name else 2) for b in firsts), ("M4", name, firsts)
        assert sum(row["outcomes"].values()) == 2 * Q1_REPS, ("M6", name, row["outcomes"])
        assert not row["reconcile_mismatches"] and row["reconciled_committed"] > 0, ("reconcile", name, row)
    control = report["cells"]["same_pair/ttl0/seq_on/early"]
    assert control["reps_with_40001"] >= control["overlap"] - 1, ("M2", control)
    assert prod["reps_with_classified_40001"] == 0, ("R-027-3", report["acceptance"])


async def _index_tables(observer) -> dict:
    async with observer.connect() as c:
        return dict((await c.execute(text(
            "SELECT i.relname, t.relname FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_class t ON t.oid = x.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'public'"))).all())


# ------------------------------------------------------------------------------------------------------------- Q2/Q3


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))] * 1000, 1)


async def _run_payers(f, world, probe: _Probe, n: int, *, clearing=None) -> dict:
    probe.reset()
    _FAILURES.clear()
    _COMMITTED.clear()
    before = await _nets(f, world)
    results: dict[int, list] = {}

    async def payer(j):
        _who.set(f"payer{j}")
        path = world["routes"][j]
        out = []
        for _ in range(PER_PAYER):
            out.append(await _pay(f, world, path[0], path[-1]))
        results[j] = out

    clearing_out: dict = {}

    async def clear():
        _who.set("clearing")
        await asyncio.sleep(0.15)  # payments already flowing
        t = time.perf_counter()
        try:
            r = await run_clearing_pass(f, world["code"])
            clearing_out.update(status=r.status, reason=None if r.reason is None else r.reason.value,
                                committed=len(r.committed), plans=r.plans, remaining=r.remaining_cycles)
        except Exception as exc:  # recorded
            clearing_out.update(error=type(exc).__name__)
        clearing_out["wall_s"] = round(time.perf_counter() - t, 3)

    started = time.perf_counter()
    tasks = [payer(j) for j in range(n)] + ([clear()] if clearing else [])
    await asyncio.gather(*tasks)
    wall = time.perf_counter() - started
    flat = [r for j in range(n) for r in results[j]]
    outcomes = Counter(o for o, _ in flat)
    lat_ok = [t for o, t in flat if o == "COMMITTED"]
    payer_waits = [w for k, v in probe.lock_wait.items() if k.startswith("payer") for w in v]
    payer_attempts = sum(v for k, v in probe.attempts.items() if (k or "").startswith("payer"))
    row = {
        "payments": len(flat), "committed": outcomes.get("COMMITTED", 0),
        "outcomes": dict(outcomes),
        "exhausted_409_E008": sum(v for k, v in outcomes.items() if "E008" in k),
        "attempts": payer_attempts, "retries": payer_attempts - len(flat),
        "payer_40001": sum(probe.count("40001", f"payer{j}") for j in range(n)),
        "payer_other_sqlstates": {f"{c}": k for (w, c), k in probe.errors.items()
                                  if (w or "").startswith("payer") and c != "40001"},
        "untagged_errors": {f"{c}": k for (w, c), k in probe.errors.items() if w is None},
        "router_builds": probe.builds,
        "failure_reasons": dict(Counter(x.split(" | ")[0][:60] for x in _FAILURES)),
        "wall_s": round(wall, 2), "throughput_per_s": round(outcomes.get("COMMITTED", 0) / wall, 1),
        "lat_p50_ms": _pct(lat_ok, 0.5), "lat_p95_ms": _pct(lat_ok, 0.95), "lat_max_ms": _pct(lat_ok, 1.0),
        "shared_lock_wait_p50_ms": _pct(payer_waits, 0.5), "shared_lock_wait_p95_ms": _pct(payer_waits, 0.95),
        "shared_lock_wait_max_ms": _pct(payer_waits, 1.0), "shared_lock_wait_n": len(payer_waits),
        "payer_classified_causes": {c: k for (w, c), k in probe.causes.items() if (w or "").startswith("payer")},
        "E007": sum(v for k, v in outcomes.items() if "E007" in k),
        "reconcile": await _reconcile(f, world, before, pairwise=not clearing),
    }
    if clearing:
        row["clearing"] = {**clearing_out,
                           "clearing_40001": probe.count("40001", "clearing"),
                           "clearing_other": {c: k for (w, c), k in probe.errors.items() if w == "clearing" and c != "40001"},
                           "exclusive_wait_ms": [round(w * 1000, 1) for w in probe.lock_wait.get("clearing_exclusive", [])][:5],
                           "exclusive_waits_n": len(probe.lock_wait.get("clearing_exclusive", [])),
                           "exclusive_wait_max_ms": _pct(probe.lock_wait.get("clearing_exclusive", []), 1.0)}
    return row


@pytest.mark.asyncio
async def test_q2_disjoint_payers_throughput(stand, monkeypatch) -> None:
    probe: _Probe = stand["probe"]
    f = stand["factories"]["seq_on"]
    world = await _seed(f, filler=2000, payers=10)
    world["history"] = await _history(f, stand["observer"], world, monkeypatch, HISTORY)
    report: dict = {"sizes": world["sizes"], "history": world["history"], "per_payer": PER_PAYER, "pg": await _pg_settings(stand["observer"]),
                    "settings": {k: getattr(settings, k) for k in ("COMMIT_RETRY_ATTEMPTS", "COMMIT_RETRY_BASE_DELAY_MS",
                                                                   "COMMIT_RETRY_MAX_DELAY_MS", "PAYMENT_TOTAL_TIMEOUT_SECONDS")},
                    "cells": {}}
    original_invalidate = PaymentRouter.invalidate_cache.__func__
    routing_ms = settings.ROUTING_PATH_FINDING_TIMEOUT_MS
    report["settings"]["ROUTING_PATH_FINDING_TIMEOUT_MS"] = routing_ms
    for n in (2, 5, 10):
        # `*_rt10s_DIAG`: the 500 ms routing budget raised to 10 s, so the SSI cost is seen apart from the
        # routing timeouts a 2 000-participant graph build causes under concurrency (not a production setting).
        for mode in ("ttl0", "ttl3600", "pinned_warm_DIAG", "ttl0_rt10s_DIAG", "pinned_warm_rt10s_DIAG"):
            monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 10000 if "rt10s" in mode else routing_ms)
            monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 0 if mode.startswith("ttl0") else 3600)
            PaymentRouter.invalidate_cache(world["code"])
            if mode.startswith("pinned_warm"):  # the post-commit invalidation disabled: the cache never goes cold
                await _warm(f, world)
                monkeypatch.setattr(PaymentRouter, "invalidate_cache", classmethod(lambda cls, code=None: None))
            elif mode == "ttl3600":
                await _warm(f, world)
            row = await _run_payers(f, world, probe, n)
            monkeypatch.setattr(PaymentRouter, "invalidate_cache", classmethod(original_invalidate))
            report["cells"][f"N={n}/{mode}"] = row
    prod = report["cells"]["N=10/ttl0"]
    report["acceptance"] = {"cell": "N=10/ttl0",
                            "R-027-1": {"committed": prod["committed"], "of": prod["payments"], "threshold": ">= 95 %"},
                            "R-027-2": {"E007": prod["E007"], "of": prod["payments"], "threshold": "<= 5 %"}}
    _artifact("p027_q2_throughput.json", report)
    for name, row in report["cells"].items():
        assert row["payments"] == int(name.split("/")[0][2:]) * PER_PAYER, ("M6", name, row)
        assert not row["untagged_errors"], ("M6", name, row)
        assert not row["reconcile"]["mismatches"] and row["reconcile"]["committed"] == row["committed"], (name, row)
        if "/ttl0" in name:
            assert row["router_builds"] == row["attempts"], ("M4", name, row)
        if "/pinned_warm" in name:
            assert row["router_builds"] == row["retries"], ("M4", name, row)  # retries read the graph afresh
    assert prod["E007"] <= 0.05 * prod["payments"], ("R-027-2", report["acceptance"])
    assert prod["committed"] >= 0.95 * prod["payments"], ("R-027-1", report["acceptance"])


@pytest.mark.asyncio
async def test_q3_clearing_against_payments(stand, monkeypatch) -> None:
    probe: _Probe = stand["probe"]
    f = stand["factories"]["seq_on"]
    monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 0)
    report: dict = {"per_payer": PER_PAYER, "triangles": TRIANGLES, "cells": {}}
    # Warm the planner process pool on an equivalent with no debts, so its spawn is not measured.
    warm_world = await _seed(f, filler=10, payers=0)
    await run_clearing_pass(f, warm_world["code"])
    routing_ms = settings.ROUTING_PATH_FINDING_TIMEOUT_MS
    for name, shared_triangles, clearing in (("N=5/no_clearing", 0, False),
                                             ("N=5/clearing_elsewhere", 0, True),
                                             ("N=5/clearing_shares_payer_pairs", 5, True),
                                             ("N=5/rt10s_DIAG/no_clearing", 0, False),
                                             ("N=5/rt10s_DIAG/clearing_elsewhere", 0, True),
                                             ("N=5/rt10s_DIAG/clearing_shares_payer_pairs", 5, True)):
        monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 10000 if "rt10s" in name else routing_ms)
        world = await _seed(f, filler=2000, payers=5, triangles=TRIANGLES, shared_triangles=shared_triangles)
        report.setdefault("history", {})[name] = await _history(  # the tables are shared: one past for all cells
            f, stand["observer"], world, monkeypatch, HISTORY if name == "N=5/no_clearing" else 0)
        monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 0)
        row = await _run_payers(f, world, probe, 5, clearing=clearing)
        async with f() as s:
            row["cycle_debts_left"] = (await s.execute(text(
                "SELECT count(*) FROM debts d WHERE d.equivalent_id = :e AND d.amount > 0 AND EXISTS ("
                "SELECT 1 FROM participants p WHERE p.id = d.debtor_id AND (p.display_name LIKE 'C%' OR p.display_name LIKE 'Y%'))"),
                {"e": world["eq_id"]})).scalar_one()
            await s.rollback()
        report["cells"][name] = row
    waits = {k: (r["shared_lock_wait_p95_ms"], r["shared_lock_wait_n"]) for k, r in report["cells"].items()
             if "clearing_" in k and "DIAG" not in k}
    report["acceptance"] = {"R-027-4": {"p95_ms_and_n": waits, "threshold": "p95 <= 50 ms, n >= 20"}}
    _artifact("p027_q3_clearing.json", report)
    for name, row in report["cells"].items():
        assert row["payments"] == 5 * PER_PAYER, ("M6", name, row)
        assert not row["untagged_errors"], ("M6", name, row)
        assert not row["reconcile"]["mismatches"] and row["reconcile"]["committed"] == row["committed"], (name, row)
        if "clearing_" in name:
            assert row["clearing"].get("committed", 0) >= 1, ("anti-vacuum: clearing executed nothing", name, row)
    assert all(n >= 20 and p95 is not None and p95 <= 50 for p95, n in waits.values()), ("R-027-4", waits)
