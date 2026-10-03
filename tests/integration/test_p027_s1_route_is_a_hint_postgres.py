"""027 stage 1 (`T2701`): the route is a hint built outside the money transaction; ONE re-route per request.

Real commits on a mode-B clone. The route cache is made stale by an ORM UPDATE of `trust_lines` (no invalidation),
so the cached graph over- or under-states a capacity for real: over-states and a fresh graph finds another route or
nothing (`E002`, `ABORTED` once); under-states ("no route" from the cache) and the fresh graph finds it; the core
refuses every bind and there is exactly one re-route. Plus: route builds run `READ COMMITTED READ ONLY`, a money
commit keeps the cache, a replay answers without routing, cold builds are single-flight, the 500 ms budget bounds
the search and not the build.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
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
    engine = create_async_engine(committed_database.url, pool_size=8, max_overflow=0,
                                 isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL)
    f = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 3600)
    st = {"f": f, "url": committed_database.url, "builds": [], "attempts": 0, "build_delay": 0.0, "fail_next": False}
    original_build, original_attempt = PaymentRouter._build_graph_impl, PaymentService._pay_attempt

    async def counted_build(self, *a, **k):
        await asyncio.sleep(st["build_delay"])
        level = await self.session.scalar(text("SHOW transaction_isolation"))
        st["builds"].append((level, await self.session.scalar(text("SHOW transaction_read_only"))))
        if st["fail_next"]:
            st["fail_next"] = False
            raise RuntimeError("the leader's build failed")
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
    for creditor, debtor, limit in {"under_found": [("R", "S", "1"), ("M", "S", "1")]}.get(case, []):
        await _set_limit(st, creditor, debtor, limit)
    await _warm(st)  # the cache now holds the capacities as they are; then they change behind it
    for creditor, debtor, limit in {"over_found": [("R", "S", "1")], "over_not_found": [("R", "S", "1"), ("M", "S", "1")],
                                    "under_found": [("R", "S", "100")]}.get(case, []):
        await _set_limit(st, creditor, debtor, limit)
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
        paths = [r.path for r in result.routes]  # over_found: the fresh graph sends the remainder over M
        assert [st["people"][x].pid for x in ("SMR" if case == "over_found" else "SR")] in paths, result.routes
        assert await _rows(st, request.tx_id) == ["COMMITTED"]
        assert PaymentRouter._graph_cache.get(st["eq"].code) is not None, "a money commit dropped the route cache"
        st["builds"].clear()
        PaymentRouter.invalidate_cache(st["eq"].code)
        replay = await _pay(st, _request(st, request.tx_id))
        assert replay.status == "COMMITTED" and st["builds"] == [], ("a replay must answer without routing", st)
    else:
        code = getattr(result, "code", None)
        assert getattr(code, "value", code) == "E002", (case, result)  # raised after the refusal is recorded
        assert await _rows(st, request.tx_id) == ["ABORTED"]


@pytest.mark.asyncio
async def test_concurrent_cold_builds_of_one_equivalent_are_one_build(stand) -> None:
    st = stand
    st["build_delay"] = 0.2
    routers = [PaymentRouter(st["f"]()) for _ in range(5)]
    await asyncio.gather(*(r.build_graph(st["eq"].code, use_shared_cache=True) for r in routers))
    for r in routers:
        await r.session.close()
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
    assert await _rows(st, request.tx_id) == ["COMMITTED"]


@pytest.mark.asyncio
async def test_a_failed_leader_and_concurrent_refreshes_stay_single_flight(stand) -> None:
    """§15 review of stage 1, #2: waiters woken by a FAILED leader follow one new leader (1 + 1 builds, not 1 + 3);
    concurrent fresh re-route builds (`refresh=True`) join the one in flight."""
    st = stand
    st["build_delay"], st["fail_next"] = 0.2, True
    routers = [PaymentRouter(st["f"]()) for _ in range(4)]
    outcomes = await asyncio.gather(*(r.build_graph(st["eq"].code, use_shared_cache=True) for r in routers),
                                    return_exceptions=True)
    assert isinstance(outcomes[0], RuntimeError) and outcomes[1:] == [None] * 3, outcomes
    assert len(st["builds"]) == 2 and all(r.graph for r in routers[1:]), st["builds"]
    st["builds"].clear()
    await asyncio.gather(*(r.build_graph(st["eq"].code, refresh=True) for r in routers))
    for r in routers:
        await r.session.close()
    assert len(st["builds"]) == 1, st["builds"]


@pytest.mark.asyncio
async def test_cold_concurrent_payments_do_not_wait_on_their_own_pool(stand) -> None:
    """§15 review of stage 1, #1: a `pay()` attempt holding a pooled connection while its route reader asks the same
    pool for another starves a small pool (attempts hold, the leader's reader waits). Pool 1 + 1, three payers."""
    engine = create_async_engine(stand["url"], pool_size=1, max_overflow=1, pool_timeout=2,
                                 isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL)
    small = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    p, code = stand["people"], stand["eq"].code
    flows = [("S", "R"), ("M", "R"), ("S", "M")]
    try:
        outcomes = await asyncio.gather(*(PaymentService.pay(small, p[a].id, PaymentCreateRequest(
            tx_id=str(uuid.uuid4()), to=p[b].pid, equivalent=code, amount="1.00", signature="__internal__"),
            require_signature=False) for a, b in flows), return_exceptions=True)
    finally:
        await engine.dispose()
    # 027 `T2704` (b): progress (a COMMITTED, a graph build); the only allowed failure is a retryable E008.
    committed = [o for o in outcomes if getattr(o, "status", None) == "COMMITTED"]
    failed = [o for o in outcomes if o not in committed and (getattr(o, "details", None) or {}).get("retryable") is not True]
    assert committed and stand["builds"] and not failed, (outcomes, stand["builds"])


@pytest.mark.asyncio
async def test_a_reroute_never_takes_a_graph_read_before_its_refusal(stand, monkeypatch) -> None:
    """027 `T2704` (a): a re-route never takes the graph of a leader that read before the direct S->R was used up."""
    st, original, held = stand, PaymentRouter._build_graph_impl, asyncio.Event()

    async def read_then_hold(self, *a, **k):  # the leader: its read is done, it publishes only later
        result = await original(self, *a, **k)
        if not held.is_set():
            held.set()
            await asyncio.sleep(1.5)
        return result

    monkeypatch.setattr(PaymentRouter, "_build_graph_impl", read_then_hold)
    lead = PaymentRouter(st["f"]())
    leader = asyncio.create_task(lead.build_graph(st["eq"].code, refresh=True))
    await held.wait()
    exhaust = await PaymentService.pay(st["f"], st["people"]["S"].id, PaymentCreateRequest(
        tx_id=str(uuid.uuid4()), to=st["people"]["R"].pid, equivalent=st["eq"].code, amount="100.00",
        signature="__internal__"), require_signature=False)
    paid = await _pay(st, _request(st))
    await leader
    await lead.session.close()
    assert exhaust.status == "COMMITTED", exhaust
    assert getattr(paid, "status", None) == "COMMITTED", paid
    assert [st["people"][x].pid for x in "SMR"] in [r.path for r in paid.routes], paid.routes


@pytest.mark.asyncio
async def test_a_reroute_after_a_failed_leader_builds_anew_over_a_warm_cache(stand) -> None:
    """027 `T2704` (a): the leader a re-route waits for fails; the warm cache predates the re-route - build anew."""
    st = stand
    await _warm(st)
    st["builds"].clear()
    st["build_delay"], st["fail_next"] = 0.2, True
    routers = [PaymentRouter(st["f"]()) for _ in range(2)]
    outcomes = await asyncio.gather(*(r.build_graph(st["eq"].code, refresh=True) for r in routers),
                                    return_exceptions=True)
    for r in routers:
        await r.session.close()
    assert isinstance(outcomes[0], RuntimeError) and outcomes[1] is None and len(st["builds"]) == 2, (outcomes, st)
