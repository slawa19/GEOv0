"""026 S3 (`T2603.1`): a close request racing a payment or a clearing, on real PostgreSQL schedules.

Owner В1: the line closes in the transaction that brings the debt it supports to 0 - never stays requested over a
zero debt, never closes over a non-zero one, and a rolled-back attempt leaves no completion row. The close takes
the line `FOR UPDATE` and reads the debt; the payment holds the pair's lines `FOR SHARE` (024 `T2415.3`); the
book's completion reads the requested lines and UPDATEs only the ones it closes (the lock upgrade). Every
schedule is forced by patching a step of the real code (no sleep, no mock of the database); SERIALIZABLE and the
owners' existing retries (`40001`/`40P01`) decide - the stands assert the outcome AND the mechanism.

WHAT THIS DOES NOT SEE: the simulator's tick (S4), a three-party deadlock, a retry budget exhausted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

import app.core.ledger.book as book_module
from app.core.clearing.runner import run_clearing_pass
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

_XFAIL = pytest.mark.xfail(raises=TargetMismatch, strict=True,
                           reason="026 target, delivered by T2603.1: a close request racing money")


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


@_XFAIL
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
    assert [m for m in _retries(caplog, "payment.attempt_retry") if "pgcode=40001" in m], "no 40001 retry"


@_XFAIL
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
    stale = (outcome, await _status(factory, line_id), await _audit(factory, line_key))
    async with factory() as fresh:
        again = await _close(fresh, line_id, p["A"]["id"])
    # Never a request left pending over a zero debt: the stale close fails 40001 and changes nothing.
    require_target(stale == ("40001", "active", []) and again == "closed",
                   f"stale close -> {stale}; a fresh close -> {again}")


@_XFAIL
@MODE_B
@pytest.mark.asyncio
async def test_a_close_committed_inside_a_clearing_is_completed_by_its_retry(
        client, db_session, monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    code, p, line_id, line_key, factory = await _stand(client, db_session)
    assert await _pay(factory, p["A"], p["C"], code, "50") and await _pay(factory, p["C"], p["B"], code, "50")
    complete, injected = book_module._complete, []

    async def close_then_complete(session, async_conn, op, operation_id):
        if op.kind == "CLEARING" and not injected:  # the clearing's snapshot predates the request
            injected.append(await _close_inside(factory, line_id, p["A"]["id"]))
        return await complete(session, async_conn, op, operation_id)

    monkeypatch.setattr(book_module, "_complete", close_then_complete)
    result = await run_clearing_pass(factory, code)
    status = await _status(factory, line_id)
    audit = [(op, x.get("completed_by")) for op, x in await _audit(factory, line_key)]
    require_target(injected == ["active"] and status == "closed" and await _debts(factory, code) == {},
                   f"injected close {injected}; clearing {result.status} {len(result.committed)}; line {status}")
    assert audit == [("TRUST_LINE_CLOSE", "CLEARING"), ("TRUST_LINE_CLOSE_REQUEST", None)], audit
    assert [m for m in _retries(caplog, "clearing.attempt_retry") if "40001" in m], "no 40001 retry"


@_XFAIL
@MODE_B
@pytest.mark.asyncio
async def test_the_lock_upgrade_against_a_payment_holding_the_line_resolves_by_retry(
        client, db_session, monkeypatch, caplog) -> None:
    # P2 binds (FOR SHARE on A -> B) and waits; P1 repays to zero and its completion UPDATEs A -> B (the
    # upgrade) while P2 then writes the same debt: a deadlock PostgreSQL breaks, one side retries and is refused.
    caplog.set_level(logging.INFO)
    code, p, line_id, line_key, factory = await _stand(client, db_session)
    async with factory() as s:
        assert await _close(s, line_id, p["A"]["id"]) == "active"
    apply, settle = PaymentService._apply_payment, book_module._settle_requested_closes
    p2_bound, release = asyncio.Event(), asyncio.Event()

    async def apply_payment(self, declaration, **kwargs):
        if declaration.tx_id == "p2" and not p2_bound.is_set():
            p2_bound.set()
            await release.wait()
        return await apply(self, declaration, **kwargs)

    async def settle_requested(session, op, rows):
        if op.tx_id == "p1":
            release.set()  # P1 is about to read and UPDATE the requested line P2 holds FOR SHARE
        return await settle(session, op, rows)

    monkeypatch.setattr(PaymentService, "_apply_payment", apply_payment)
    monkeypatch.setattr(book_module, "_settle_requested_closes", settle_requested)

    async def pay(tx_id: str) -> str:
        async with factory() as s:
            try:
                return (await PaymentService(s).create_payment_internal(
                    p["A"]["id"], to_pid=p["B"]["pid"], equivalent=code, amount="50", idempotency_key=tx_id)).status
            except GeoException as exc:
                return f"refused {type(exc).__name__}"

    p2 = asyncio.create_task(pay("p2"))
    await asyncio.wait_for(p2_bound.wait(), timeout=30)
    outcomes = sorted(await asyncio.wait_for(asyncio.gather(pay("p1"), p2), timeout=60))
    conflicts = [m for m in _retries(caplog, "payment.attempt_retry") if "40P01" in m or "40001" in m]
    audit = [op for op, _ in await _audit(factory, line_key)]
    assert outcomes[0] == "COMMITTED" and outcomes[1].startswith("refused"), outcomes
    assert conflicts, "no deadlock or serialization retry: the schedule did not race"
    require_target(await _status(factory, line_id) == "closed" and await _debts(factory, code) == {}
                   and audit == ["TRUST_LINE_CLOSE", "TRUST_LINE_CLOSE_REQUEST"],
                   f"outcomes {outcomes}, audit {audit}, retries {conflicts}")

