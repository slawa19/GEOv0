"""026 S3 (`T2603.1`): a close request racing a payment or a clearing, on real PostgreSQL schedules.

Owner В1: the line closes in the transaction that brings the debt it supports to 0 - never stays requested over a
zero debt, never closes over a non-zero one, and a rolled-back attempt leaves no completion row. The close takes
the line `FOR UPDATE` and reads the debt; the payment holds the pair's lines `FOR SHARE` (024 `T2415.3`) and
upgrades a requested line to `FOR UPDATE` before it writes any debt (the lock upgrade); the book's completion
UPDATEs only the lines it closes. Every
schedule is forced by patching a step of the real code (no sleep, no mock of the database); SERIALIZABLE and the
owners' existing retries (`40001`/`40P01`) decide - the stands assert the outcome AND the mechanism.

WHAT THIS DOES NOT SEE: the simulator's tick (S4), a three-party deadlock, a retry budget exhausted.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.clearing.runner import run_clearing_pass
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.core.trustlines.service import TrustLineService
from app.db.models.trustline import TrustLine
from app.db.sqlstate import sqlstate
from app.schemas.trustline import TrustLineCloseRequest
from app.utils.exceptions import GeoException
from tests.conftest import MODE_B
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _pay, _world
from tests.integration.test_p026_s3_close_request_postgres import _audit
from tests.p019_support import TargetMismatch, require_target


async def _close(session, line_id, creditor_id) -> str:
    service = TrustLineService(session)
    batch = service.begin_internal_batch()
    try:
        line = await service.execute_close(batch, line_id, creditor_id, TrustLineCloseRequest(signature="-"),
                                           require_signature=False)
    except GeoException as exc:
        raise TargetMismatch(f"setup stops: the close of A -> B with B's debt 50 was refused: {exc}") from exc
    await batch.finish()
    status = str(line.status)
    await session.commit()
    return status


async def _close_inside(factory, line_id, creditor_id) -> str:
    """The close injected into a money operation's attempt: a refusal is recorded, never raised into it."""

    async with factory() as other:
        try:
            return await _close(other, line_id, creditor_id)
        except TargetMismatch as exc:
            return str(exc)


async def _status(factory, line_id) -> str:
    async with factory() as s:
        return (await s.execute(select(TrustLine.status).where(TrustLine.id == line_id))).scalar_one()


async def _stand(client, db_session):
    code, p, lines, factory = await _world(client, db_session)
    assert await _pay(factory, p["B"], p["A"], code, "50")
    return code, p, uuid.UUID(lines["AB"]), lines["AB"], factory


def _retries(caplog, event: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if event in r.getMessage()]


@MODE_B
@pytest.mark.asyncio
async def test_a_close_committed_between_routing_and_binding_is_completed_by_the_retry(
        client, db_session, monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    code, p, line_id, line_key, factory = await _stand(client, db_session)
    bind, attempts = PaymentService._bind_payment, []

    async def close_then_bind(self, *args, **kwargs):
        if not attempts:  # the payment has routed (its snapshot is taken); the close commits now
            attempts.append(await _close_inside(factory, line_id, p["A"]["id"]))
        else:
            attempts.append("retry")
        return await bind(self, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_bind_payment", close_then_bind)
    repaid = await _pay(factory, p["A"], p["B"], code, "50")
    status = await _status(factory, line_id)
    audit = [(op, x.get("completed_by")) for op, x in await _audit(factory, line_key)]
    require_target(attempts[:1] == ["active"] and repaid and status == "closed" and await _debts(factory, code) == {},
                   f"repaid {repaid}, line {status}, attempts {attempts}")
    assert audit == [("TRUST_LINE_CLOSE", "PAYMENT"), ("TRUST_LINE_CLOSE_REQUEST", None)], audit
    assert not _retries(caplog, "payment.attempt_retry"), "027 stage 2: the close is read after the line lock"


@MODE_B
@pytest.mark.asyncio
async def test_a_repayment_committed_after_the_close_snapshot_refuses_the_stale_close(client, db_session) -> None:
    code, p, line_id, line_key, factory = await _stand(client, db_session)
    async with factory() as closer:
        await closer.execute(select(TrustLine.id).where(TrustLine.id == line_id))  # the close's snapshot
        assert await _pay(factory, p["A"], p["B"], code, "50")  # commits: the supported debt is 0 now
        try:
            outcome = await _close(closer, line_id, p["A"]["id"])
        except DBAPIError as exc:
            await closer.rollback()
            outcome = sqlstate(exc)
        except TargetMismatch as exc:  # the old code refuses the stale close on its stale debt
            await closer.rollback()
            outcome = str(exc)
    stale = (outcome, await _status(factory, line_id), [op for op, _ in await _audit(factory, line_key)])
    require_target(stale == ("closed", "closed", ["TRUST_LINE_CLOSE"]), f"close after the repayment -> {stale}")


@MODE_B
@pytest.mark.asyncio
async def test_a_close_committed_inside_a_clearing_is_completed_by_its_retry(
        client, db_session, monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    code, p, line_id, line_key, factory = await _stand(client, db_session)
    assert await _pay(factory, p["A"], p["C"], code, "50") and await _pay(factory, p["C"], p["B"], code, "50")
    lock, injected = MoneyBoundary.lock_pair_lines, []

    async def close_then_lock(self, pairs, **kw):  # 027 stage 2: the request commits just before the clearing's lines
        if not injected:
            injected.append(await _close_inside(factory, line_id, p["A"]["id"]))
        return await lock(self, pairs, **kw)

    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", close_then_lock)
    result = await run_clearing_pass(factory, code)
    status = await _status(factory, line_id)
    audit = [(op, x.get("completed_by")) for op, x in await _audit(factory, line_key)]
    require_target(injected == ["active"] and status == "closed" and await _debts(factory, code) == {},
                   f"injected close {injected}; clearing {result.status} {len(result.committed)}; line {status}")
    assert audit == [("TRUST_LINE_CLOSE", "CLEARING"), ("TRUST_LINE_CLOSE_REQUEST", None)], audit
    assert not _retries(caplog, "clearing.attempt_retry"), "027 stage 2: the request is read after the line lock"


@MODE_B
@pytest.mark.asyncio
async def test_a_second_repayment_waits_for_the_closing_one_and_retries_on_its_result(
        client, db_session, monkeypatch, caplog) -> None:
    # Two payments repay the requested line's debt to zero. P2 reads A -> B - the router's hint makes it lock the
    # line `FOR UPDATE` at once (probed: a third session's `FOR SHARE NOWAIT` fails 55P03) - and waits; P1 routes
    # and reaches the same lock while P2 completes. P1 must WAIT
    # (not deadlock), fail 40001 on the line P2 closed, and its retry routes around it (A -> C -> B). Measured before this
    # shape: a `FOR SHARE -> FOR UPDATE` upgrade deadlocked both payments on every retry until both spent
    # their budget (`refused RetryablePaymentConflictException` twice, 40P01 and pivot 40001 alternating).
    caplog.set_level(logging.INFO)
    code, p, line_id, line_key, factory = await _stand(client, db_session)
    async with factory() as s:
        assert await _close(s, line_id, p["A"]["id"]) == "active"
    debt_amount, segment, who = PaymentService._debt_amount, MoneyBoundary.lock_pair_lines, contextvars.ContextVar("p")
    p2_locked, release = asyncio.Event(), asyncio.Event()

    async def debt_amount_of(self, *args):
        if who.get(None) == "p2" and not p2_locked.is_set():
            p2_locked.set()  # P2 has just read A -> B with its lock, before any upgrade or money
            await release.wait()
        return await debt_amount(self, *args)

    async def segment_of(self, pairs, **kw):  # 027 stage 2: P1 asks for the lines P2 holds
        if who.get(None) == "p1":
            release.set()
        return await segment(self, pairs, **kw)

    monkeypatch.setattr(PaymentService, "_debt_amount", debt_amount_of)
    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", segment_of)

    async def pay(tx_id: str) -> str:
        who.set(tx_id)  # the payment's phases run in tasks of their own (`wait_for`): a context var follows
        async with factory() as s:
            try:
                return (await PaymentService(s).create_payment_internal(
                    p["A"]["id"], to_pid=p["B"]["pid"], equivalent=code, amount="50", idempotency_key=tx_id)).status
            except GeoException as exc:
                return f"refused {type(exc).__name__}"

    p2 = asyncio.create_task(pay("p2"))
    await asyncio.wait_for(p2_locked.wait(), timeout=30)
    async with factory() as probe:
        try:
            await probe.execute(select(TrustLine.id).where(TrustLine.id == line_id).with_for_update(read=True, nowait=True))
            held = "shared"
        except DBAPIError as exc:
            held = sqlstate(exc)
        await probe.rollback()
    outcomes = await asyncio.wait_for(asyncio.gather(pay("p1"), p2), timeout=60)
    conflicts = [m for m in _retries(caplog, "payment.attempt_retry") if "40P01" in m or "40001" in m]
    audit = [op for op, _ in await _audit(factory, line_key)]
    assert release.is_set() and not conflicts, f"027 stage 2: P1 waits on the line, no conflict: {conflicts}"
    a, b, c = (p[n]["id"] for n in "ABC")
    require_target(held == "55P03" and outcomes == ["COMMITTED", "COMMITTED"] and not [m for m in conflicts if "40P01" in m]
                   and await _status(factory, line_id) == "closed"
                   and await _debts(factory, code) == {(a, c): Decimal("50"), (c, b): Decimal("50")}
                   and audit == ["TRUST_LINE_CLOSE", "TRUST_LINE_CLOSE_REQUEST"],
                   f"P2's lock {held}; p1, p2 -> {outcomes}, audit {audit}, retries {conflicts}")
