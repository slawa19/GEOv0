"""027 stage 1 (`T2701`): the route is a hint built outside the money transaction; ONE re-route per request.

Real commits on a mode-B clone; the route cache is made stale by writing `trust_lines` behind the router's back
(an ORM UPDATE, which invalidates nothing), so the cached graph over- or under-states a capacity for real:

* over-states, a fresh graph finds another route -> COMMITTED over it, two attempts, one fresh build;
* over-states, a fresh graph finds nothing -> `E002`, the refusal recorded `ABORTED` exactly once;
* under-states (the cached search says "no route") -> one fresh build finds it -> COMMITTED, nothing recorded;
* the core refuses every bind -> exactly one re-route, then `E002` (never a second one).

Plus: every route build of `pay()` runs in a `READ COMMITTED READ ONLY` transaction of its own, a money commit
leaves the cache in place, a replay answers without routing, concurrent cold builds of one equivalent are one
build (single-flight), and the 500 ms budget bounds the path search, not the graph build.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest


@pytest_asyncio.fixture
async def stand(committed_database, monkeypatch):
    engine = create_async_engine(committed_database.url, pool_size=8, max_overflow=0, isolation_level="SERIALIZABLE")
    f = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 3600)
    st = {"f": f, "builds": [], "attempts": 0, "build_delay": 0.0}
    original_build, original_attempt = PaymentRouter._build_graph_impl, PaymentService._pay_attempt

    async def counted_build(self, *a, **k):
        await asyncio.sleep(st["build_delay"])
        level = await self.session.scalar(text("SHOW transaction_isolation"))
        st["builds"].append((level, await self.session.scalar(text("SHOW transaction_read_only"))))
        return await original_build(self, *a, **k)

    async def counted_attempt(self, *a, **k):
        st["attempts"] += 1
        return await original_attempt(self, *a, **k)

    monkeypatch.setattr(PaymentRouter, "_build_graph_impl", counted_build)
    monkeypatch.setattr(PaymentService, "_pay_attempt", counted_attempt)
    n = uuid.uuid4().hex[:6].upper()
    async with f() as s:
        eq = Equivalent(code=f"P27S{n}", precision=2, is_active=True)
        people = {r: Participant(pid=f"P27S_{r}_{n}", display_name=r, public_key=f"pk27s_{r}_{n}", type="person",
                                 status="active") for r in "SMR"}
        s.add_all([eq, *people.values()])
        await s.flush()
        for creditor, debtor in (("R", "S"), ("M", "S"), ("R", "M")):  # S -> R direct, and S -> M -> R
            s.add(TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                            equivalent_id=eq.id, limit=Decimal("100"), status="active"))
        await s.commit()
    st.update(eq=eq, people=people)
    try:
        yield st
    finally:
        PaymentRouter.invalidate_cache(eq.code)
        await engine.dispose()


async def _set_limit(st, creditor: str, debtor: str, limit: str) -> None:
    """Behind the router's back: no invalidation, so a warm cache keeps the old capacity."""
    async with st["f"]() as s:
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == st["people"][creditor].id,
                                                TrustLine.to_participant_id == st["people"][debtor].id)
                        .values(limit=Decimal(limit)))
        await s.commit()


async def _warm(st) -> None:
    async with st["f"]() as s:
        await PaymentRouter(s).build_graph(st["eq"].code, use_shared_cache=True)
        await s.rollback()


def _request(st, tx_id: str | None = None) -> PaymentCreateRequest:
    return PaymentCreateRequest(tx_id=tx_id or str(uuid.uuid4()), to=st["people"]["R"].pid, equivalent=st["eq"].code,
                                amount="50.00", signature="__internal__")


async def _pay(st, request):
    return await PaymentService.pay(st["f"], st["people"]["S"].id, request, require_signature=False)


async def _rows(st, tx_id: str) -> list[str]:
    async with st["f"]() as s:
        return list((await s.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["over_found", "over_not_found", "under_found", "never_twice"])
async def test_one_reroute_per_request_on_a_fresh_graph(stand, monkeypatch, case) -> None:
    st = stand
    if case == "under_found":
        await _set_limit(st, "R", "S", "1")
        await _set_limit(st, "M", "S", "1")
    await _warm(st)  # the cache now holds the capacities as they are
    if case in ("over_found", "over_not_found"):
        await _set_limit(st, "R", "S", "1")
    if case == "over_not_found":
        await _set_limit(st, "M", "S", "1")
    if case == "under_found":
        await _set_limit(st, "R", "S", "100")
    if case == "never_twice":
        original = PaymentService._segment

        async def refused(self, *a, **k):
            _capacity, lines = await original(self, *a, **k)
            return Decimal("0"), lines

        monkeypatch.setattr(PaymentService, "_segment", refused)
    request = _request(st)
    st["builds"].clear()
    try:
        result = await _pay(st, request)
    except Exception as exc:  # recorded for the assert message
        result = exc
    assert st["attempts"] == 2, (case, st["attempts"], result)
    assert len(st["builds"]) == 1, (case, st["builds"])  # the re-route's fresh build; the first attempt hit the cache
    assert set(st["builds"]) == {("read committed", "on")}, st["builds"]
    if case in ("over_found", "under_found"):
        assert getattr(result, "status", None) == "COMMITTED", (case, result)
        expected_path = ["S", "M", "R"] if case == "over_found" else ["S", "R"]
        assert [r.path for r in result.routes] == [[st["people"][x].pid for x in expected_path]], result.routes
        assert await _rows(st, request.tx_id) == ["COMMITTED"]
        assert PaymentRouter._graph_cache.get(st["eq"].code) is not None, "a money commit dropped the route cache"
        st["builds"].clear()
        PaymentRouter.invalidate_cache(st["eq"].code)
        replay = await _pay(st, _request(st, request.tx_id))
        assert replay.status == "COMMITTED" and st["builds"] == [], ("a replay must answer without routing", st)
    else:
        assert getattr(result, "status", None) == "ABORTED" and result.error.code == "E002", (case, result)
        assert await _rows(st, request.tx_id) == ["ABORTED"]


@pytest.mark.asyncio
async def test_concurrent_cold_builds_of_one_equivalent_are_one_build(stand) -> None:
    st = stand
    st["build_delay"] = 0.2
    sessions = [st["f"]() for _ in range(5)]
    try:
        routers = [PaymentRouter(s) for s in sessions]
        await asyncio.gather(*(r.build_graph(st["eq"].code, use_shared_cache=True) for r in routers))
    finally:
        for s in sessions:
            await s.close()
    assert len(st["builds"]) == 1, st["builds"]
    assert all(r.graph == routers[0].graph and r.graph for r in routers), [r.graph for r in routers]


@pytest.mark.asyncio
async def test_the_routing_budget_bounds_the_search_not_the_graph_build(stand, monkeypatch) -> None:
    st = stand
    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 50)
    st["build_delay"] = 0.3  # a cold build slower than the search budget, well inside the 10 s payment deadline
    request = _request(st)
    result = await _pay(st, request)
    assert result.status == "COMMITTED" and len(st["builds"]) == 1, (result, st["builds"])
    async with st["f"]() as s:
        assert await s.scalar(select(func.count()).select_from(Transaction).where(
            Transaction.tx_id == request.tx_id)) == 1
