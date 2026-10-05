"""Reproducer R-3 (p030 external review): reconciliation does not tie `transactions` rows to operations.

(a) A real payment through the API, then ONLY `transactions.state` is set to `ABORTED` by raw SQL (envelope and
journal untouched). (b) A `PAYMENT`/`COMMITTED` row "A pays B 10.00" inserted with no operation and no debt, the
way `scripts/seed_db.py:494-536` imports transaction rows. The assertion is the CORRECT behaviour: reconciliation
of the equivalent is not `PASSED`. Red on the current tree means the finding reproduces.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.ledger.reconciliation import (
    PASSED,
    _has_baseline,
    open_verification_snapshot,
    take_baseline,
    verify_journal_equals_change,
)
from app.db.models.transaction import Transaction
from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    api,
    build_api_world,
    debts,
    envelopes,
    factory,
    payment_body,
    tx_row,
)


async def _ensure_baseline(factory, equivalent_id) -> bool:  # noqa: F811
    async with factory() as s:
        had = await _has_baseline(s, equivalent_id)
        if not had:
            await take_baseline(s, equivalent_id)
            await s.commit()
        else:
            await s.rollback()
    return had


async def _reconcile(factory, equivalent_id):  # noqa: F811
    async with factory() as s:
        await open_verification_snapshot(s)
        outcome = await verify_journal_equals_change(s, equivalent_id)
        await s.rollback()
    return outcome


async def _tx_rows(factory, equivalent_code):  # noqa: F811
    async with factory() as s:
        rows = (await s.execute(select(Transaction.tx_id, Transaction.type, Transaction.state, Transaction.payload))).all()
    return [(t, ty, st) for t, ty, st, p in rows if isinstance(p, dict) and p.get("equivalent") == equivalent_code]


@pytest.mark.asyncio
async def test_r3a_payment_row_flipped_to_aborted_is_not_passed(api, factory) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    await _ensure_baseline(factory, world.equivalent_id)
    body = payment_body(world, world.alice, world.bob, "10.00")
    resp = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])
    assert resp.status_code == 200 and resp.json()["status"] == "COMMITTED", resp.text
    assert await debts(factory, world) == {(world.alice["pid"], world.bob["pid"]): Decimal("10.00")}
    [(env_state, declared, entries)] = await envelopes(factory, body["tx_id"])
    assert env_state == "COMPLETED" and declared == entries > 0
    before = await _reconcile(factory, world.equivalent_id)
    assert before.status == PASSED, f"stand: not PASSED before tampering: {before.findings} {before.missing_evidence}"

    async with factory() as s:
        changed = await s.execute(
            text("UPDATE transactions SET state = 'ABORTED' WHERE tx_id = :t"), {"t": body["tx_id"]}
        )
        await s.commit()
    assert changed.rowcount == 1
    assert (await tx_row(factory, body["tx_id"]))[0] == "ABORTED", "stand: the row was not flipped"
    assert await debts(factory, world) == {(world.alice["pid"], world.bob["pid"]): Decimal("10.00")}
    [(env_state_after, _d, _e)] = await envelopes(factory, body["tx_id"])
    assert env_state_after == "COMPLETED", "stand: the envelope must stay untouched"

    after = await _reconcile(factory, world.equivalent_id)
    assert after.status != PASSED, (
        f"reconcile after the row says ABORTED while the money moved: status={after.status} "
        f"findings={list(after.findings)} edges={after.edges_checked} coverage={after.criterion_b_coverage}"
    )


@pytest.mark.asyncio
async def test_r3b_committed_payment_row_without_operation_is_not_passed(api, factory) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    had = await _ensure_baseline(factory, world.equivalent_id)
    before = await _reconcile(factory, world.equivalent_id)
    assert before.status == PASSED, f"stand: not PASSED before the insert: {before.findings} {before.missing_evidence}"

    tx_id = f"p030-r3b-{uuid.uuid4()}"
    async with factory() as s:
        s.add(
            Transaction(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"tx:{tx_id}"),
                tx_id=tx_id,
                idempotency_key=None,
                type="PAYMENT",
                initiator_id=world.ids[world.alice["pid"]],
                payload={
                    "from": world.alice["pid"],
                    "to": world.bob["pid"],
                    "amount": "10.00",
                    "equivalent": world.code,
                    "routes": [{"path": [world.alice["pid"], world.bob["pid"]], "amount": "10.00"}],
                },
                signatures=[],
                state="COMMITTED",
                error=None,
            )
        )
        await s.commit()
    assert (await tx_row(factory, tx_id))[0] == "COMMITTED", "stand: the row was not inserted"
    assert await debts(factory, world) == {}, "stand: no debt must exist"
    assert await envelopes(factory, tx_id) == [], "stand: no operation must exist"

    after = await _reconcile(factory, world.equivalent_id)
    assert after.status != PASSED, (
        f"reconcile with a COMMITTED payment row and no money/operation: status={after.status} "
        f"findings={list(after.findings)} edges={after.edges_checked} baseline_was_there={had} "
        f"rows={await _tx_rows(factory, world.code)}"
    )
