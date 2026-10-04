"""028 E3 (`T2831`, `F-028-28`, owner В-1): a suspended participant takes no part in money - reproducers.

THE BOUNDARY (spec 028, "Граница заморозки"): the freeze takes the participant row `FOR UPDATE`; every money writer
takes `FOR SHARE` on the rows of every participant it touches - first, in `participants.id` order, before its lines -
and decides from the status read under that lock. So a freeze either waits for a writer in flight or makes it refuse.

THE CRITERION of the stand (Verification plan, п. 5): no money write lands with `suspended` committed before it.
"Writer first": the writer holds its locks, the freeze must wait, the writer's effect lands, then the freeze. "Freeze
first": the freeze holds its lock uncommitted, the writer must wait and then refuse - its effect never lands.

Red on `86742876` (nothing read the participant's status): every case below.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import or_, select, text, update
from starlette.requests import Request

from app.api.v1.admin import _set_participant_status
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.trustlines.service import TrustLineService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.admin import AdminParticipantActionRequest
from app.schemas.trustline import TrustLineCreateRequest
from app.utils.exceptions import GeoException
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import stand  # noqa: F401
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import _Artifacts, _runner
from tests.p023_support import TEST_PLAN_ID, occurrence_of
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

_PAUSE: contextvars.ContextVar[asyncio.Event | None] = contextvars.ContextVar("p028_e3_pause", default=None)


async def _world(stand, *, lines=None, debts=()):  # noqa: F811
    """Participants A, B, C, D in a fresh equivalent; `lines` (creditor, debtor) limit 100, default all six among
    A, B, C; `debts` (debtor, creditor, amount). D has no line."""
    n = uuid.uuid4().hex[:8].upper()
    async with stand() as s:
        eq = Equivalent(code=f"FZ{n}", precision=2, is_active=True)
        p = {k: Participant(pid=f"{k}_FZ_{n}", display_name=k, public_key=f"pk_{k}_{n}", type="person",
                            status="active") for k in "ABCD"}
        s.add_all([eq, *p.values()])
        await s.flush()
        for cr, dr in lines if lines is not None else [(x, y) for x in "ABC" for y in "ABC" if x != y]:
            s.add(TrustLine(from_participant_id=p[cr].id, to_participant_id=p[dr].id, equivalent_id=eq.id,
                            limit=Decimal("100"), status="active", policy={"auto_clearing": True}))
        await s.flush()
        rows = [Debt(id=uuid.uuid4(), debtor_id=p[d].id, creditor_id=p[c].id, equivalent_id=eq.id,
                     amount=Decimal(a)) for d, c, a in debts]
        if rows:
            async with debt_fixture_setup(s, label="p028-e3"):
                s.add_all(rows)
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, p, [r.id for r in rows]


def _request() -> Request:
    return Request({"type": "http", "headers": [], "client": ("p028-e3", 0)})


async def _admin_status(session, pid: str, status: str = "suspended") -> None:
    await _set_participant_status(pid=pid, audit_action=f"admin.participants.{status}", status_value=status,
                                  body=AdminParticipantActionRequest(reason="p028-e3"), request=_request(), db=session)


def _inject(eq, p, effects):
    from app.core.simulator.models import RunRecord

    members = [p[k] for k in "ABCD"]
    scenario = {"equivalents": [eq.code], "participants": [{"id": x.pid} for x in members], "trustlines": [],
                "behaviorProfiles": [], "events": [{"type": "inject", "time": 0, "effects": effects}]}
    run = RunRecord(run_id=f"p028-e3-{uuid.uuid4().hex[:8]}", scenario_id="p028-e3", mode="real", state="running")
    run.seed, run.tick_index, run.sim_time_ms, run.intensity_percent = 7, 1, 1_000, 0
    run._real_seeded, run._real_participants = True, [(x.id, x.pid) for x in members]
    run._real_equivalents, run._edges_by_equivalent, run._real_viz_by_eq = [eq.code], {}, {}
    artifacts = _Artifacts()
    return _runner(run, scenario, artifacts), run, scenario, artifacts


def _debt(eq, creditor, debtor, amount="1.00") -> dict:
    return {"op": "inject_debt", "from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "amount": amount}


async def _pay(session, eq, p, path: str, amount: str = "1.00"):
    service = PaymentService(session)
    if path:  # a route handed to the core, past the router (the core must not trust it)
        route = [p[k].pid for k in path]
        service.router.find_flow_routes = lambda *_a, **_k: [(route, Decimal(amount))]
    return await service.create_payment_internal(p[path[0] if path else "A"].id, to_pid=p[path[-1] if path else "C"].pid,
                                                 equivalent=eq.code, amount=amount)


async def _footprint(stand, eq, who) -> tuple:  # noqa: F811
    """Everything money-like that touches `who`: its debts and its live lines."""
    async with stand() as s:
        debts = sorted((str(d), str(c), str(a)) for d, c, a in (await s.execute(select(
            Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq.id, or_(
                Debt.debtor_id == who.id, Debt.creditor_id == who.id)))).all())
        lines = sorted((str(f), str(t)) for f, t in (await s.execute(select(
            TrustLine.from_participant_id, TrustLine.to_participant_id).where(
                TrustLine.equivalent_id == eq.id, TrustLine.status != "closed", or_(
                    TrustLine.from_participant_id == who.id, TrustLine.to_participant_id == who.id)))).all())
    return debts, lines


async def _status(stand, who) -> str:  # noqa: F811
    async with stand() as s:
        return await s.scalar(select(Participant.status).where(Participant.id == who.id))


@pytest.fixture
def generous_budgets(monkeypatch):
    for name in ("PREPARE_TIMEOUT_SECONDS", "COMMIT_TIMEOUT_SECONDS", "PAYMENT_TOTAL_TIMEOUT_SECONDS"):
        monkeypatch.setattr(settings, name, 30)


@pytest.mark.asyncio
async def test_a_suspended_intermediate_carries_no_payment(stand, generous_budgets) -> None:  # noqa: F811
    """(а) reviewer 017, p. 1348: B -> A and C -> B 10; B frozen by the admin; A pays C 1 with max_hops=2."""
    eq, p, _ = await _world(stand, lines=[("B", "A"), ("C", "B")])
    async with stand() as s:
        await _admin_status(s, p["B"].pid)
    before = await _footprint(stand, eq, p["B"])
    outcomes = []
    for forced in ("", "ABC"):  # the router's route, then a route handed to the core
        async with stand() as s:
            try:
                outcomes.append((await _pay(s, eq, p, forced)).status)
            except GeoException as exc:
                outcomes.append(exc.details.get("reason") or type(exc).__name__)
    assert await _footprint(stand, eq, p["B"]) == before, f"a payment moved money through suspended B: {outcomes}"
    assert outcomes[1] == "participant_suspended", outcomes
    async with stand() as s:  # positive control: unfrozen, the same payment passes
        await _admin_status(s, p["B"].pid, "active")
    PaymentRouter.invalidate_cache(eq.code)
    async with stand() as s:
        assert (await _pay(s, eq, p, "")).status == "COMMITTED"


@pytest.mark.asyncio
async def test_an_inject_debt_toward_a_suspended_participant_is_skipped(stand) -> None:  # noqa: F811
    """(в) B is suspended, its lines stay `active` (after F-028-29 every line is): the effect is skipped, by reason."""
    eq, p, _ = await _world(stand)
    async with stand() as s:
        await _admin_status(s, p["B"].pid)
    runner, run, scenario, artifacts = _inject(eq, p, [_debt(eq, p["A"], p["B"]), _debt(eq, p["B"], p["C"])])
    async with stand() as s:
        await runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)
    debts, _lines = await _footprint(stand, eq, p["B"])
    stats = [e["scenario"]["stats"] for e in artifacts.events if e.get("type") == "note"]
    assert debts == [] and 0 in run._real_fired_scenario_event_indexes, (debts, stats)
    assert stats and stats[-1].get("skipped_reasons", {}).get("participant_suspended") == 2, stats


@pytest.mark.asyncio
async def test_a_debt_after_the_freeze_in_the_same_event_is_skipped(stand) -> None:  # noqa: F811
    """(г) one event: debt A -> B, freeze B, debt A -> B. The first lands, the one after the freeze does not."""
    eq, p, _ = await _world(stand)
    effects = [_debt(eq, p["A"], p["B"], "3.00"), {"op": "freeze_participant", "participant_id": p["B"].pid},
               _debt(eq, p["A"], p["B"], "2.00")]
    runner, run, scenario, artifacts = _inject(eq, p, effects)
    async with stand() as s:
        await runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)
    debts, _ = await _footprint(stand, eq, p["B"])
    assert debts == [(str(p["B"].id), str(p["A"].id), "3.00")], debts
    assert await _status(stand, p["B"]) == "suspended"


# --- the stand: freeze (admin, mixed inject event) x writer x arrival order ------------------------------------------

_DEBTS = (("A", "B", "30.00"), ("B", "C", "30.00"), ("C", "A", "30.00"))  # a cycle through B, for the clearing


async def _writer(kind, stand, eq, p, debt_ids):  # noqa: F811
    async with stand() as s:
        if kind == "payment":
            return (await _pay(s, eq, p, "ABC")).status
        if kind == "clearing":
            occurrence = occurrence_of(debt_ids, equivalent_id=eq.id, amount="10.00", plan_id=TEST_PLAN_ID, ordinal=0)
            return await ClearingService(s).execute_occurrence(occurrence)
        if kind == "inject":
            runner, run, scenario, _ = _inject(eq, p, [_debt(eq, p["B"], p["A"])])
            return await runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)
        service = TrustLineService(s)
        batch = service.begin_internal_batch()
        await service.execute_create(batch, p["B"].id, TrustLineCreateRequest(
            to=p["D"].pid, equivalent=eq.code, limit="10", signature="-"), require_signature=False)
        await batch.finish()
        await s.commit()


async def _freezer(kind, stand, eq, p, hold: asyncio.Event | None):  # noqa: F811
    async with stand() as s:
        if hold is not None:  # pause right before COMMIT, every lock of the freeze held
            commit = s.commit

            async def held_commit():
                await hold.wait()
                return await commit()

            s.commit = held_commit
        if kind == "admin":
            return await _admin_status(s, p["B"].pid)
        runner, run, scenario, _ = _inject(eq, p, [{"op": "freeze_participant", "participant_id": p["B"].pid},
                                                   _debt(eq, p["C"], p["B"])])
        return await runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)


async def _settle(*tasks):
    out = []
    for task in tasks:
        try:
            out.append(await asyncio.wait_for(task, 40))
        except GeoException as exc:  # a refusal is an outcome, never a pass
            out.append(exc.details.get("reason") or type(exc).__name__)
    return out


@pytest.mark.parametrize("order", ["writer_first", "freeze_first"])
@pytest.mark.parametrize("writer", ["payment", "clearing", "inject", "create"])
@pytest.mark.parametrize("freezer", ["admin", "inject_event"])
@pytest.mark.asyncio
async def test_no_money_write_lands_after_a_committed_freeze(stand, monkeypatch, generous_budgets,  # noqa: F811
                                                             freezer, writer, order) -> None:
    eq, p, debt_ids = await _world(stand, debts=_DEBTS)
    before = await _footprint(stand, eq, p["B"])
    paused, go = asyncio.Event(), asyncio.Event()
    lock = MoneyBoundary.lock_pair_lines

    async def lock_then_pause(self, *args, **kwargs):  # the writer holds its participants and its lines
        rows = await lock(self, *args, **kwargs)
        if (gate := _PAUSE.get()) is not None and not paused.is_set():
            paused.set()
            await gate.wait()
        return rows

    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", lock_then_pause)

    async def as_writer():
        _PAUSE.set(go if order == "writer_first" else None)
        return await _writer(writer, stand, eq, p, debt_ids)

    if order == "writer_first":
        writing = asyncio.create_task(as_writer())
        await asyncio.wait_for(paused.wait(), 20)
        freezing = asyncio.create_task(_freezer(freezer, stand, eq, p, None))
        await asyncio.wait([freezing], timeout=1.5)
        frozen_early = freezing.done()
        go.set()
        outcome = await _settle(writing, freezing)
        assert not frozen_early, f"the freeze committed while {writer} held its locks: {outcome}"
        assert await _footprint(stand, eq, p["B"]) != before, f"positive control: {writer} wrote nothing: {outcome}"
    else:
        hold = asyncio.Event()
        freezing = asyncio.create_task(_freezer(freezer, stand, eq, p, hold))
        await asyncio.sleep(1.0)  # the freeze has taken its lock and waits at COMMIT
        writing = asyncio.create_task(as_writer())
        await asyncio.wait([writing], timeout=1.5)
        hold.set()
        outcome = await _settle(freezing, writing)
        assert await _footprint(stand, eq, p["B"]) == before, f"{writer} wrote after the freeze: {outcome}"
    assert await _status(stand, p["B"]) == "suspended", outcome


@pytest.mark.asyncio
async def test_a_frozen_line_status_is_refused_by_the_database(stand) -> None:  # noqa: F811
    """F-028-29: `frozen` is no longer a status of a line - the CHECK refuses it (accepted on `86742876`)."""
    eq, p, _ = await _world(stand, lines=[("A", "B")])
    async with stand() as s:
        with pytest.raises(Exception, match="chk_trust_line_status"):
            await s.execute(update(TrustLine).where(TrustLine.equivalent_id == eq.id).values(status="frozen"))
        await s.rollback()
    async with stand() as s:
        assert await s.scalar(text("SELECT count(*) FROM trust_lines WHERE status = 'frozen'")) == 0
