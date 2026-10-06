"""030 S6b (`T3006`; §15 review of S3 `T3093` #1, #2; review of S5 `T3095` #2).

#1 The seeder took its pair locks ONE PAIR AT A TIME in participant-id order while a payment takes every line of
its route in ONE statement in `trust_lines.id` order: with A < B < C, the line A -> B carrying a big id and B -> C a
small one, a seeder adding the reverse lines held A -> B, a payment C -> B -> A held B -> C and waited for A -> B,
the seeder then asked for B -> C - a deadlock (`40P01`), and the seeder's owners (`tick.py`, `simulator.py`) roll
back and raise without a retry. Since S6b the batch locks every live line of all its pairs in one statement in the
writers' order before the first creation. #2 The equivalent row `FOR SHARE` of a creation ran without the caller's
`lock_timeout_ms` budget: an inject held its lines while it waited on a held equivalent for as long as the holder
pleased. The "other writer" of #1 is the payment's own line statement (`MoneyBoundary.lock_pair_lines` over the
route's pairs, `payments/service.py`), not a whole payment: a payment retries a `40P01`, which would hide which
side was the victim; the statement is the finding. Mode B (`stand`: sessions of their own).
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder, simulated_public_key
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.trustlines.service import TrustLineService
from app.db.sqlstate import sqlstate
from app.schemas.trustline import TrustLineCreateRequest
from app.utils.exceptions import ConflictException
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import _seed_pair, stand  # noqa: F401
from tests.integration.test_p027_t2706_fix_delta_postgres import _blocked_by, _inject_create, _live
from tests.integration.test_scenarios import (_sign_trustline_create_request, expected_state_of, register_and_login,
                                              signed_trustline_update, utc_now_rfc3339)


def _key(user: dict) -> SigningKey:
    return SigningKey(base64.b64decode(user["priv"]))


async def _waits_on(stand, pid: int) -> bool:  # noqa: F811
    async with stand() as w:
        for _ in range(300):
            if await w.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE :p = ANY(pg_blocking_pids(pid))"),
                              {"p": pid}):
                return True
            await w.rollback()
            await asyncio.sleep(0.02)
    return False


def _uuid(*, big: bool) -> uuid.UUID:
    bit = 1 << 127
    return uuid.UUID(int=(uuid.uuid4().int | bit) if big else (uuid.uuid4().int & (bit - 1)))


@pytest.mark.asyncio
async def test_t3093_1_the_seeder_takes_its_lines_in_the_writers_order(stand, monkeypatch) -> None:  # noqa: F811
    n, ids = uuid.uuid4().hex[:6].upper(), sorted(uuid.uuid4() for _ in range(3))
    pids = {k: f"S6B_{k}_{n}" for k in "ABC"}
    async with stand() as s:
        eq = Equivalent(code=f"S6B{n}", precision=2, is_active=True)
        s.add(eq)
        s.add_all([Participant(id=i, pid=pids[k], display_name=k, public_key=simulated_public_key(pids[k]),
                               type="person", status="active") for k, i in zip("ABC", ids)])
        await s.flush()
        s.add_all([TrustLine(id=_uuid(big=True), from_participant_id=ids[0], to_participant_id=ids[1],
                             equivalent_id=eq.id, limit=Decimal("10"), status="active"),
                   TrustLine(id=_uuid(big=False), from_participant_id=ids[1], to_participant_id=ids[2],
                             equivalent_id=eq.id, limit=Decimal("10"), status="active")])
        await s.commit()
    scenario = {"equivalents": [eq.code], "participants": [{"id": p} for p in pids.values()],
                "trustlines": [{"from": pids[a], "to": pids[b], "equivalent": eq.code, "limit": "10"}
                               for a, b in ("AB", "BC", "BA", "CB")]}
    paused, go, seeder_pid, lock = asyncio.Event(), asyncio.Event(), [], MoneyBoundary.lock_pair_lines

    async def lock_then_pause(self, *args, **kwargs):  # the seeder's FIRST line statement pauses holding its rows
        rows = await lock(self, *args, **kwargs)
        if not seeder_pid:
            seeder_pid.append(await self.session.scalar(text("SELECT pg_backend_pid()")))
            paused.set()
            await go.wait()
        return rows

    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", lock_then_pause)

    async def seed():
        async with stand() as s:
            try:
                await RealScenarioSeeder().seed_scenario_into_db(session=s, scenario=scenario)
                await s.commit()
            except DBAPIError as exc:  # what the owners do: roll back and raise, no retry
                await s.rollback()
                return sqlstate(exc.orig, walk=False)
            return "seeded"

    async def pay():  # the payment C -> B -> A: participants, then BOTH pairs' lines in one statement by id
        async with stand() as s:
            await MoneyBoundary(s).lock_participants(ids)
            try:
                rows = await lock(MoneyBoundary(s), [(eq.id, ids[1], ids[2]), (eq.id, ids[0], ids[1])])
            except DBAPIError as exc:
                await s.rollback()
                return sqlstate(exc.orig, walk=False)
            await s.rollback()
            return len(rows)

    seeding = asyncio.create_task(seed())
    await asyncio.wait_for(paused.wait(), 20)
    paying = asyncio.create_task(pay())
    assert await _waits_on(stand, seeder_pid[0]), "the payment never queued on the seeder's line lock"
    go.set()
    outcomes = await asyncio.wait_for(asyncio.gather(seeding, paying), 30)
    assert "40P01" not in outcomes, (
        f"the seeder (pair by pair, participant order) and the payment (one statement, trust_lines.id order) "
        f"deadlocked: seeder={outcomes[0]!r}, payment={outcomes[1]!r}")
    assert outcomes == ["seeded", 2], outcomes
    async with stand() as s:
        assert await s.scalar(select(func.count()).select_from(TrustLine).where(
            TrustLine.equivalent_id == eq.id, TrustLine.status != "closed")) == 4


@pytest.mark.asyncio
async def test_a_batch_holding_its_locks_still_runs_every_check(stand, monkeypatch) -> None:  # noqa: F811
    """Anti-vacuum of the once-per-batch locks: after `lock()` a creation takes no new line lock, yet the live-line
    re-check and the participant status still run at every creation (the status: re-read, 028 E3 (г))."""
    eq, x, y = await _seed_pair(stand, "S6V")
    lock, statements = MoneyBoundary.lock_pair_lines, []
    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", lambda self, pairs, **kw: (statements.append(list(pairs)),
                                                                                       lock(self, pairs, **kw))[1])
    async with stand() as s:
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == y.id).values(status="closed"))
        await s.commit()
        service, batch = TrustLineService(s), TrustLineService(s).begin_internal_batch()
        assert len(await batch.lock([x.id, y.id], [(eq.id, x.id, y.id)])) == 1  # X -> Y is the pair's one live line

        def create(creditor, debtor):
            return service.execute_create(batch, creditor.id, TrustLineCreateRequest(
                to=debtor.pid, equivalent=eq.code, limit="5", signature="-"), require_signature=False)

        await create(y, x)  # the line the pre-lock did not see is created...
        with pytest.raises(ConflictException, match="already exists"):
            await create(y, x)  # ...and found by the next creation's re-check, under the pair lock the batch holds
        await s.execute(update(Participant).where(Participant.id == x.id).values(status="suspended"))  # this txn
        with pytest.raises(ConflictException, match="not active"):
            await create(x, y)
        await s.rollback()
    assert len(statements) == 1, statements  # one line statement for the whole batch: the pre-lock


@pytest.mark.asyncio
async def test_t3093_2_the_inject_wait_on_a_held_equivalent_is_bounded(stand, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 1)
    eq, x, y = await _seed_pair(stand, "S6")
    async with stand() as s:  # Y -> X closed: the event creates it
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == y.id).values(status="closed"))
        await s.commit()
    runner, run, scenario = _inject_create(eq, x, y)
    async with stand() as holder, stand() as s:
        await holder.execute(select(Equivalent.id).where(Equivalent.id == eq.id).with_for_update())  # a PATCH / hold
        started = time.monotonic()
        task = asyncio.create_task(asyncio.wait_for(
            runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario), 6))
        waited = await _blocked_by(stand, holder)
        try:
            outcome = await task
        except Exception as exc:  # noqa: BLE001 - compared below
            outcome = exc
        elapsed = time.monotonic() - started
        await holder.rollback()
    assert waited, "the inject never waited on the held equivalent row"
    assert isinstance(outcome, DBAPIError) and sqlstate(outcome.orig, walk=False) == "55P03", (
        f"the inject waited on the held equivalent past its 1 s budget: {outcome!r} after {elapsed:.1f} s")
    assert 0 not in run._real_fired_scenario_event_indexes and not await _live(stand, eq, y, x)


@pytest.mark.asyncio
async def test_t3095_2_a_non_rfc3339_issued_at_is_refused_over_http(client, db_session) -> None:
    code = "S6" + uuid.uuid4().hex[:8].upper()
    db_session.add(Equivalent(code=code, symbol="S", precision=2, metadata_={}, is_active=True))
    await db_session.commit()
    a, b = await register_and_login(client, f"S6A{code}"), await register_and_login(client, f"S6B{code}")
    created = await client.post("/api/v1/trustlines", headers=a["headers"], json={
        "to": b["pid"], "equivalent": code, "limit": "10.00",
        "signature": _sign_trustline_create_request(signing_key=_key(a), to_pid=b["pid"], equivalent=code,
                                                    limit="10.00")})
    line_id = created.json()["id"]
    body = signed_trustline_update(signing_key=_key(a), trustline_id=line_id, limit="20.00",
                                   expected=await expected_state_of(client, a["headers"], line_id),
                                   issued_at=utc_now_rfc3339().replace("T", "X"))  # `fromisoformat` takes any letter
    response = await client.patch(f"/api/v1/trustlines/{line_id}", headers=a["headers"], json=body)
    assert response.status_code == 400, f"an issued_at that is not RFC 3339 was accepted: {response.text}"
    assert response.json()["error"]["details"] == {"field": "issued_at", "reason": "not_rfc3339"}, response.text
