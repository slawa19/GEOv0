"""p029 adversarial concurrency stands (review only, not for merge).

Each stand: writer 1 parks INSIDE its money transaction (after its locks), writer 2 starts; the stand proves
writer 2 is queued on a lock writer 1 holds (pg_locks / pg_blocking_pids) or records that it was not; then
writer 1 is released. Mechanism asserts: the isolation level every guarded writer ran at; who waited on whom.
Target asserts: the serial result, both directions never coexist, criterion (b) clean, and the full
reconciliation (baseline taken after the seed) PASSED.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService
from app.core.ledger.reconciliation import (
    PASSED,
    open_verification_snapshot,
    take_baseline,
    verify_journal_equals_change,
)
from app.core.money_boundary import MoneyBoundary
from app.core.payments import service as payment_service_module
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.trustlines.service import TrustLineService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from app.schemas.trustline import TrustLineCloseRequest, TrustLineUpdateRequest
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import _finish, count_conflicts
from tests.p023_support import occurrence_of

from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

D = Decimal


@dataclass
class Rig:
    sessions: async_sessionmaker
    levels: list = field(default_factory=list)


@pytest_asyncio.fixture
async def rig(committed_database, monkeypatch):
    engine = create_async_engine(committed_database.url, pool_size=10, max_overflow=0,
                                 isolation_level="READ COMMITTED")
    levels: list = []
    original = MoneyBoundary.require_read_committed

    async def guard(session, *, writer: str) -> None:
        levels.append((writer, str(await session.scalar(text("SHOW transaction_isolation")))))
        await original(session, writer=writer)

    monkeypatch.setattr(MoneyBoundary, "require_read_committed", staticmethod(guard))
    try:
        yield Rig(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False), levels)
    finally:
        await engine.dispose()


async def _seed(rig: Rig, names, lines, debts, *, baseline=True):
    """lines: (creditor, debtor, limit, close_requested); debts: (debtor, creditor, amount)."""
    n = uuid.uuid4().hex[:8].upper()
    async with rig.sessions() as s:
        eq = Equivalent(code=f"ADV{n}", precision=2, is_active=True)
        ps = {k: Participant(pid=f"{k}_ADV_{n}", display_name=k, public_key=f"pk_{k}_{n}", type="person",
                             status="active") for k in names}
        s.add_all([eq, *ps.values()])
        await s.flush()
        tl = {}
        for c, d, limit, req in lines:
            tl[(c, d)] = TrustLine(from_participant_id=ps[c].id, to_participant_id=ps[d].id, equivalent_id=eq.id,
                                   limit=D(limit), status="active", policy={"auto_clearing": True},
                                   close_requested_at=datetime.now(timezone.utc) if req else None)
        s.add_all(tl.values())
        await s.flush()
        ids = []
        async with debt_fixture_setup(s, label="adv"):
            for debtor, creditor, amount in debts:
                debt = Debt(id=uuid.uuid4(), debtor_id=ps[debtor].id, creditor_id=ps[creditor].id,
                            equivalent_id=eq.id, amount=D(amount))
                ids.append(debt.id)
                s.add(debt)
        await s.commit()
    if baseline:
        async with rig.sessions() as s:
            await take_baseline(s, eq.id)
            await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, ps, tl, ids


async def _pay(rig, sender, receiver, code, amount, tx_id=None):
    request = PaymentCreateRequest(tx_id=tx_id or str(uuid.uuid4()), to=receiver.pid, equivalent=code,
                                   amount=amount, signature="__internal__")
    try:
        return await PaymentService.pay(rig.sessions, sender.id, request, require_signature=False)
    except Exception as exc:  # noqa: BLE001
        return exc


def _code(o):
    return str(getattr(o, "status", None) or getattr(o, "code", None) or type(o).__name__)


async def _debts(rig, eq):
    async with rig.sessions() as s:
        return {(d.debtor_id, d.creditor_id): D(str(d.amount))
                for d in (await s.scalars(select(Debt).where(Debt.equivalent_id == eq.id))).all()}


async def _reconcile(rig, eq):
    async with rig.sessions() as s:
        await open_verification_snapshot(s)
        out = await verify_journal_equals_change(s, eq.id)
        await s.rollback()
    return out


async def _waiters(rig, blocker_pid):
    async with rig.sessions() as s:
        rows = (await s.execute(text(
            "SELECT a.pid, l.locktype, left(a.query, 90) FROM pg_stat_activity a "
            "JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted "
            "WHERE a.datname = current_database() AND a.wait_event_type = 'Lock' "
            "AND :b = ANY(pg_blocking_pids(a.pid))"), {"b": blocker_pid})).all()
        await s.rollback()
    return [tuple(r) for r in rows]


class Park:
    """Park the first arrival; record its backend pid."""

    def __init__(self):
        self.event, self.release, self.pid = asyncio.Event(), asyncio.Event(), None

    async def here(self, session):
        if self.event.is_set():
            return
        self.pid = int(await session.scalar(text("SELECT pg_backend_pid()")))
        self.event.set()
        await self.release.wait()


def park_payment_after_prestate(monkeypatch, park: Park):
    original = payment_service_module._read_payment_prestate

    async def hooked(session, flows):
        result = await original(session, flows)
        await park.here(session)
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", hooked)


async def _race(rig, park: Park, first, second, *, wait_s=3.0):
    """Run `first` until parked, start `second`, observe who waits on the parked backend, release."""
    t1 = asyncio.create_task(first())
    await asyncio.wait_for(park.event.wait(), timeout=20)
    t2 = asyncio.create_task(second())
    loop = asyncio.get_running_loop()
    deadline, waiters = loop.time() + wait_s, []
    while loop.time() < deadline and not t2.done():
        waiters = await _waiters(rig, park.pid)
        if waiters:
            break
        await asyncio.sleep(0.03)
    second_done_while_parked = t2.done()
    park.release.set()
    try:
        r = await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=60)
    finally:
        park.release.set()
        await _finish(t1, t2)
    return r, waiters, second_done_while_parked


def _report(name, **kw):
    import os
    from pathlib import Path

    line = f"P029ADV {name} " + " ".join(f"{k}={v!r}" for k, v in kw.items())
    print(line)
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/p029_adv/artifacts")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "p029_adv.log").open("a", encoding="utf-8") as out:
        out.write(line + "\n")


def _rc(rig):
    assert rig.levels, "no writer reached the isolation guard"
    assert {lvl for _w, lvl in rig.levels} == {"read committed"}, rig.levels


# ── F1: same pair, same direction, sum over the limit (UPDATE path, existing debt) ─────────────


@pytest.mark.asyncio
async def test_f1_same_direction_over_limit(rig, monkeypatch):
    eq, p, _tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False)], [("X", "Y", "50")])
    conflicts = count_conflicts(monkeypatch)
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "30"),
                                         lambda: _pay(rig, p["X"], p["Y"], eq.code, "30"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f1", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, rec=rec.status,
            conflicts=conflicts.payment, levels=rig.levels)
    _rc(rig)
    assert waiters and not early, "second payment did not queue on the first one's lock"
    assert sorted([_code(a), _code(b)]) == ["COMMITTED", "E002"], (a, b)
    assert debts == {(p["X"].id, p["Y"].id): D("80.00000000")}
    assert rec.status == PASSED, rec.findings


# ── F2: opposite directions on a pair with debt ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_f2_opposite_directions_existing_debt(rig, monkeypatch):
    eq, p, _tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False), ("X", "Y", "100", False)],
                                [("X", "Y", "50")])
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "30"),
                                         lambda: _pay(rig, p["Y"], p["X"], eq.code, "70"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f2", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, rec=rec.status)
    _rc(rig)
    assert waiters and not early
    assert (_code(a), _code(b)) == ("COMMITTED", "COMMITTED"), (a, b)
    assert debts == {(p["X"].id, p["Y"].id): D("10.00000000")}
    assert rec.status == PASSED, rec.findings


# ── F3: a close-requested line with debt; payment that settles it ‖ payment over the same pair ──


@pytest.mark.asyncio
async def test_f3_settling_payment_closes_line_while_second_waits(rig, monkeypatch):
    # X trusts Y (close requested, limit 0), Y owes X 50; Y trusts X 100.
    eq, p, tl, _ = await _seed(rig, "XY", [("X", "Y", "0", True), ("Y", "X", "100", False)], [("Y", "X", "50")])
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "50"),
                                         lambda: _pay(rig, p["X"], p["Y"], eq.code, "10"))
    debts = await _debts(rig, eq)
    async with rig.sessions() as s:
        statuses = dict((await s.execute(select(TrustLine.id, TrustLine.status).where(
            TrustLine.equivalent_id == eq.id))).all())
    rec = await _reconcile(rig, eq)
    _report("f3", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, statuses=statuses,
            rec=rec.status)
    _rc(rig)
    assert waiters and not early
    assert _code(a) == "COMMITTED"
    assert statuses[tl[("X", "Y")].id] == "closed"
    # serial: the second grows X's debt to Y under Y's live line
    assert _code(b) == "COMMITTED" and debts == {(p["X"].id, p["Y"].id): D("10.00000000")}, (b, debts)
    assert rec.status == PASSED, rec.findings


@pytest.mark.asyncio
async def test_f3b_two_partial_settlements_on_requested_close(rig, monkeypatch):
    # Only the close-requested line: X trusts Y (limit 0, requested), Y owes X 50. Two X->Y 30.
    eq, p, tl, _ = await _seed(rig, "XY", [("X", "Y", "0", True)], [("Y", "X", "50")])
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "30"),
                                         lambda: _pay(rig, p["X"], p["Y"], eq.code, "30"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f3b", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, rec=rec.status)
    _rc(rig)
    assert waiters and not early
    assert sorted([_code(a), _code(b)]) == ["COMMITTED", "E002"], (a, b)
    assert debts == {(p["Y"].id, p["X"].id): D("20.00000000")}
    assert rec.status == PASSED, rec.findings


# ── F4: limit lowered / line closed while a payment is in flight, both orders ─────────────────


async def _trustline_op(rig, park: Park, kind, line, owner, limit=None):
    async with rig.sessions() as s:
        svc = TrustLineService(s)
        batch = svc.begin_internal_batch()
        if kind == "update":
            await svc.execute_update(batch, line.id, owner.id, TrustLineUpdateRequest(limit=limit, signature="x"),
                                     require_signature=False)
        else:
            await svc.execute_close(batch, line.id, owner.id, TrustLineCloseRequest(signature="x"),
                                    require_signature=False)
        await batch.finish()
        await park.here(s)
        await s.commit()
    return "TL_COMMITTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["update", "close"])
async def test_f4_limit_change_first_then_payment(rig, monkeypatch, kind):
    eq, p, tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False)], [("X", "Y", "50")])
    park = Park()
    (t, pay), waiters, early = await _race(
        rig, park, lambda: _trustline_op(rig, park, kind, tl[("Y", "X")], p["Y"], "60"),
        lambda: _pay(rig, p["X"], p["Y"], eq.code, "40"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report(f"f4_first_{kind}", t=t, pay=_code(pay), waiters=waiters, early=early, debts=debts, rec=rec.status)
    _rc(rig)
    assert waiters and not early, "payment did not wait for the line change"
    assert t == "TL_COMMITTED" and _code(pay) == "E002", pay
    assert debts == {(p["X"].id, p["Y"].id): D("50.00000000")}
    assert rec.status == PASSED, rec.findings


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["update", "close"])
async def test_f4_payment_first_then_limit_change(rig, monkeypatch, kind):
    eq, p, tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False)], [("X", "Y", "50")])
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    tl_park = Park()
    tl_park.event.set()  # never parks
    (pay, t), waiters, early = await _race(
        rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "40"),
        lambda: _trustline_op(rig, tl_park, kind, tl[("Y", "X")], p["Y"], "60"))
    debts = await _debts(rig, eq)
    async with rig.sessions() as s:
        line = (await s.execute(select(TrustLine.limit, TrustLine.status, TrustLine.close_requested_at).where(
            TrustLine.id == tl[("Y", "X")].id))).one()
    rec = await _reconcile(rig, eq)
    _report(f"f4_pay_first_{kind}", t=t, pay=_code(pay), waiters=waiters, early=early, debts=debts, line=line,
            rec=rec.status)
    _rc(rig)
    assert waiters and not early, "line change did not wait for the payment"
    assert _code(pay) == "COMMITTED" and t == "TL_COMMITTED"
    assert debts == {(p["X"].id, p["Y"].id): D("90.00000000")}
    if kind == "close":  # supported debt 90 > 0: a request, never a close with debt
        assert line.status == "active" and line.close_requested_at is not None and line.limit == 0, line
    assert rec.status == PASSED, rec.findings


# ── F5: clearing ‖ clearing over overlapping cycles; clearing parked ‖ payment reducing an edge ──


def _park_clearing(monkeypatch, park: Park):
    original = ClearingService._cycle_respects_auto_clearing

    async def hooked(self, debts):
        await park.here(self.session)
        return await original(self, debts)

    monkeypatch.setattr(ClearingService, "_cycle_respects_auto_clearing", hooked)


@pytest.mark.asyncio
async def test_f5_overlapping_clearings(rig, monkeypatch):
    # A->B 100, B->C 30, C->A 40 (cycle1, 30); A->B, B->D 80, D->A 80 (cycle2, 80, planned on the old snapshot)
    eq, p, _tl, ids = await _seed(
        rig, "ABCD",
        [("B", "A", "200", False), ("C", "B", "200", False), ("A", "C", "200", False),
         ("D", "B", "200", False), ("A", "D", "200", False)],
        [("A", "B", "100"), ("B", "C", "30"), ("C", "A", "40"), ("B", "D", "80"), ("D", "A", "80")])
    plan = uuid.uuid4()
    occ1 = occurrence_of([ids[0], ids[1], ids[2]], equivalent_id=eq.id, amount="30.00", plan_id=plan, ordinal=0)
    occ2 = occurrence_of([ids[0], ids[3], ids[4]], equivalent_id=eq.id, amount="80.00", plan_id=plan, ordinal=1)
    park = Park()
    _park_clearing(monkeypatch, park)

    async def clear(occ):
        async with rig.sessions() as s:
            try:
                return await ClearingService(s).execute_occurrence(occ)
            except Exception as exc:  # noqa: BLE001
                return exc

    (c1, c2), waiters, early = await _race(rig, park, lambda: clear(occ1), lambda: clear(occ2))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f5_cc", c1=c1, c2=c2, waiters=waiters, early=early, debts=debts, rec=rec.status)
    _rc(rig)
    assert waiters and not early
    assert c1 == D("30.00000000") and c2 is None, (c1, c2)
    assert all(v > 0 for v in debts.values())
    assert debts == {(p["A"].id, p["B"].id): D("70.00000000"), (p["C"].id, p["A"].id): D("10.00000000"),
                     (p["B"].id, p["D"].id): D("80.00000000"), (p["D"].id, p["A"].id): D("80.00000000")}
    assert rec.status == PASSED, rec.findings


@pytest.mark.asyncio
async def test_f5_clearing_parked_payment_reduces_cycle_edge(rig, monkeypatch):
    # Cycle A->B 100, B->C 30, C->A 40; clearing (30) parked after its locks; C pays B 20 (B owes C: nets B->C to 10).
    eq, p, _tl, ids = await _seed(
        rig, "ABC", [("B", "A", "200", False), ("C", "B", "200", False), ("A", "C", "200", False),
                     ("B", "C", "200", False)],
        [("A", "B", "100"), ("B", "C", "30"), ("C", "A", "40")])
    occ = occurrence_of(ids, equivalent_id=eq.id, amount="30.00", plan_id=uuid.uuid4(), ordinal=0)
    park = Park()
    _park_clearing(monkeypatch, park)

    async def clear():
        async with rig.sessions() as s:
            return await ClearingService(s).execute_occurrence(occ)

    (c, pay), waiters, early = await _race(rig, park, clear, lambda: _pay(rig, p["C"], p["B"], eq.code, "20"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f5_cp", c=c, pay=_code(pay), waiters=waiters, early=early, debts=debts, rec=rec.status)
    _rc(rig)
    assert waiters and not early
    # serial: clearing 30 -> A->B 70, C->A 10, B->C gone; then C pays B 20: B owes C nothing, C owes B 20
    assert c == D("30.00000000") and _code(pay) == "COMMITTED", (c, pay)
    assert debts == {(p["A"].id, p["B"].id): D("70.00000000"), (p["C"].id, p["A"].id): D("10.00000000"),
                     (p["C"].id, p["B"].id): D("20.00000000")}, debts
    assert rec.status == PASSED, rec.findings


# ── F6: multipath payment ‖ payment sharing one edge, opposite lock arrival ───────────────────


@pytest.mark.asyncio
async def test_f6_multipath_vs_shared_edge(rig, monkeypatch):
    # S pays R 150: direct line capacity 100 + via M 100 -> multipath. Concurrently M pays R 80 over M-R.
    eq, p, _tl, _ = await _seed(rig, "SRM", [("R", "S", "100", False), ("M", "S", "100", False),
                                             ("R", "M", "100", False)], [])
    conflicts = count_conflicts(monkeypatch)
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["S"], p["R"], eq.code, "150"),
                                         lambda: _pay(rig, p["M"], p["R"], eq.code, "80"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f6", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, rec=rec.status,
            conflicts=conflicts.payment, a_routes=getattr(a, "routes", None))
    _rc(rig)
    assert waiters and not early
    lim = {(p["S"].id, p["R"].id): D(100), (p["S"].id, p["M"].id): D(100), (p["M"].id, p["R"].id): D(100)}
    assert all(amount <= lim.get(edge, D(0)) for edge, amount in debts.items()), debts
    assert rec.status == PASSED, rec.findings


# ── F7: the second payment routes on a graph that predates the first one's commit ─────────────


@pytest.mark.asyncio
async def test_f7_stale_route_never_overspends(rig, monkeypatch):
    # Direct X->Y capacity 100 (Y trusts X), detour X->M->Y 100.
    eq, p, _tl, _ = await _seed(rig, "XYM", [("Y", "X", "100", False), ("M", "X", "100", False),
                                             ("Y", "M", "100", False)], [])
    builds = []
    original_build = PaymentService._build_route_graph

    async def counting(self, code, *, cached):
        builds.append(cached)
        return await original_build(self, code, cached=cached)

    monkeypatch.setattr(PaymentService, "_build_route_graph", counting)
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "100"),
                                         lambda: _pay(rig, p["X"], p["Y"], eq.code, "50"))
    debts = await _debts(rig, eq)
    rec = await _reconcile(rig, eq)
    _report("f7", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, builds=builds, rec=rec.status,
            b_routes=getattr(b, "routes", None))
    _rc(rig)
    assert waiters and not early
    assert _code(a) == "COMMITTED"
    lim = {(p["X"].id, p["Y"].id): D(100), (p["X"].id, p["M"].id): D(100), (p["M"].id, p["Y"].id): D(100)}
    assert all(v <= lim.get(e, D(0)) for e, v in debts.items()), debts
    assert _code(b) == "COMMITTED", b  # the one re-route finds the detour
    assert debts == {(p["X"].id, p["Y"].id): D("100.00000000"), (p["X"].id, p["M"].id): D("50.00000000"),
                     (p["M"].id, p["Y"].id): D("50.00000000")}, debts
    assert rec.status == PASSED, rec.findings


# ── F9: the scheduled reconciliation with a payment committed between its reads ───────────────


@pytest.mark.asyncio
async def test_f9_payment_between_reconciliation_reads_is_passed_and_no_hold(rig, monkeypatch):
    from app.core.ledger import reconciliation as rec_module

    eq, p, _tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False)], [("X", "Y", "10")])
    seen = []
    original = rec_module._current_debts

    async def pay_then_read(session, equivalent_id):
        if not seen:
            seen.append(str(await session.scalar(text("SHOW transaction_isolation"))))
            seen.append(_code(await _pay(rig, p["X"], p["Y"], eq.code, "25")))
        return await original(session, equivalent_id)

    monkeypatch.setattr(rec_module, "_current_debts", pay_then_read)
    counts = await rec_module.run_scheduled_reconciliation(rig.sessions, equivalent_ids=[eq.id])
    async with rig.sessions() as s:
        hold = await s.scalar(select(Equivalent.integrity_hold_result_id).where(Equivalent.id == eq.id))
    debts = await _debts(rig, eq)
    _report("f9", seen=seen, counts=counts, hold=hold, debts=debts)
    assert seen == ["repeatable read", "COMMITTED"], seen
    assert debts == {(p["X"].id, p["Y"].id): D("35.00000000")}  # the payment really landed mid-verification
    assert counts[PASSED] == 1, counts
    assert hold is None


# ── F10: the same tx_id twice at once ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_f10_same_tx_id_concurrently(rig, monkeypatch):
    eq, p, _tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False)], [])
    park = Park()
    park_payment_after_prestate(monkeypatch, park)
    tx = str(uuid.uuid4())
    (a, b), waiters, early = await _race(rig, park, lambda: _pay(rig, p["X"], p["Y"], eq.code, "30", tx),
                                         lambda: _pay(rig, p["X"], p["Y"], eq.code, "30", tx))
    debts = await _debts(rig, eq)
    async with rig.sessions() as s:
        rows = (await s.execute(select(Transaction.state).where(Transaction.tx_id == tx))).all()
    rec = await _reconcile(rig, eq)
    _report("f10", a=_code(a), b=_code(b), waiters=waiters, early=early, debts=debts, rows=rows, rec=rec.status)
    _rc(rig)
    assert waiters and not early
    assert (_code(a), _code(b)) == ("COMMITTED", "COMMITTED"), (a, b)
    assert debts == {(p["X"].id, p["Y"].id): D("30.00000000")} and len(rows) == 1
    assert rec.status == PASSED, rec.findings


# ── POSITIVE CONTROL: the stands can see a broken protocol. Line locks off (same statement, no FOR UPDATE) ──


def _lines_unlocked(monkeypatch):
    from sqlalchemy import tuple_

    async def unlocked(self, pairs, *, timeout_ms=None):
        keys = sorted({(e, x, y) for e, a, b in pairs for x, y in ((a, b), (b, a))}, key=str)
        if not keys:
            return []
        return list((await self.session.execute(
            select(*self._LINE).where(tuple_(TrustLine.equivalent_id, TrustLine.from_participant_id,
                                             TrustLine.to_participant_id).in_(keys), TrustLine.status != "closed")
            .order_by(TrustLine.id))).all())

    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", unlocked)


@pytest.mark.asyncio
async def test_control_f4_update_first_without_line_locks_breaks_the_limit(rig, monkeypatch):
    _lines_unlocked(monkeypatch)
    eq, p, tl, _ = await _seed(rig, "XY", [("Y", "X", "100", False)], [("X", "Y", "50")])
    park = Park()
    (t, pay), waiters, early = await _race(
        rig, park, lambda: _trustline_op(rig, park, "update", tl[("Y", "X")], p["Y"], "60"),
        lambda: _pay(rig, p["X"], p["Y"], eq.code, "40"))
    debts = await _debts(rig, eq)
    _report("control_f4", t=t, pay=_code(pay), waiters=waiters, early=early, debts=debts)
    assert early and not waiters, "without line locks the payment must not wait"
    # the payment decided on limit 100 while the PATCH to 60 was uncommitted: growth 50 -> 90 over limit 60
    assert _code(pay) == "COMMITTED" and debts == {(p["X"].id, p["Y"].id): D("90.00000000")}, (pay, debts)
