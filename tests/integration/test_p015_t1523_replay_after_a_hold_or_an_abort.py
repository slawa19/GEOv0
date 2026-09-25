"""T1523, cells 1 (integrity-hold parameter) and 2 (replay after ABORTED), on the HTTP path.

WHAT THESE TWO CELLS ARE FOR. The acceptance matrix of T1523 asks one question of every replay
path: does the second request move money a second time? The answer is the no-double-effect triple -
debts, `transactions` rows for that `tx_id`, and the operation envelope with its journal entries -
and it is only worth anything when the first request is known to have moved money, or known to have
moved none.

* Cell 1, hold parameter: after the equivalent is put under an integrity hold (step 5c, `T1546`),
  a replay of an already committed `tx_id` still answers with its stored result and moves nothing.
  The hold check sits AFTER the idempotency decision (`app/core/payments/service.py:740-749`), and
  this is the parameter the T1523 inventory found uncovered.
* Cell 2: a `tx_id` whose payment ended ABORTED replays as the stored ABORTED result - the same
  error, no money, no new transaction row. The inventory found nothing exercising it at all.

WHAT MAKES EACH CELL NON-VACUOUS, named here because a cell that stays green with its mechanism
deleted is worthless (`AGENTS.md` §15, §16):

* the hold cell asserts, after the replay, that a NEW payment in that equivalent is refused - so
  the 200 cannot be explained by a hold that was never in force;
* the abort cell removes the fault before replaying, then pays once more with a fresh `tx_id` and
  watches money move - so the stored ABORTED answer cannot be explained by a path that is simply
  broken for everyone.

TIER. SQLite, through the real HTTP endpoint, which is where both stored-result paths already live.
Concurrency is not the subject here: one request follows the other. The PostgreSQL cells of this
matrix (3, 5, 8) are in their own modules.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import func, insert, select, update

from app.config import settings
from app.core.ledger.reconciliation import FAILED
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)
from tests.conftest import MODE_B

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


def _code() -> str:
    return "T23" + uuid.uuid4().hex[:8].upper()


async def _create_equivalent(client, code: str) -> None:
    resp = await client.post(
        "/api/v1/admin/equivalents",
        json={"code": code, "precision": 2, "reason": "t1523"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text


async def _trust(client, truster, trusted, code: str, limit: str = "100.00") -> None:
    key = SigningKey(base64.b64decode(truster["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": trusted["pid"],
            "equivalent": code,
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=key,
                to_pid=trusted["pid"],
                equivalent=code,
                limit=limit,
            ),
        },
        headers=truster["headers"],
    )
    assert resp.status_code == 201, resp.text


def _payment_body(sender, receiver, code: str, amount: str) -> dict:
    tx_id = str(uuid.uuid4())
    key = SigningKey(base64.b64decode(sender["priv"]))
    return {
        "tx_id": tx_id,
        "to": receiver["pid"],
        "equivalent": code,
        "amount": amount,
        "signature": _sign_payment_request(
            signing_key=key,
            tx_id=tx_id,
            from_pid=sender["pid"],
            to_pid=receiver["pid"],
            equivalent=code,
            amount=amount,
        ),
    }


async def _effects(db_session, code: str, tx_id: str) -> dict[str, object]:
    """The triple, read as independent queries so that any one of the three can move alone.

    Column selects and not entity loads: the request handler shares this session, so an entity
    read could be answered from an identity map filled before the payment ran.
    """

    debts = sorted(
        (str(debtor), str(creditor), str(amount))
        for debtor, creditor, amount in (
            await db_session.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount)
                .join(Equivalent, Equivalent.id == Debt.equivalent_id)
                .where(Equivalent.code == code)
            )
        ).all()
    )
    transactions = sorted(
        (str(state), repr(payload), repr(error))
        for state, payload, error in (
            await db_session.execute(
                select(Transaction.state, Transaction.payload, Transaction.error).where(
                    Transaction.tx_id == tx_id
                )
            )
        ).all()
    )
    envelopes = (
        await db_session.execute(
            select(
                debt_operations.c.id,
                debt_operations.c.state,
                debt_operations.c.effect_count,
            ).where(
                debt_operations.c.kind == "PAYMENT",
                debt_operations.c.identity == tx_id,
            )
        )
    ).all()
    entries: list[tuple[str, str, str, str]] = []
    for operation_id, _state, _count in envelopes:
        rows = (
            await db_session.execute(
                select(
                    debt_journal_entries.c.amount_before,
                    debt_journal_entries.c.amount_after,
                    debt_journal_entries.c.delta,
                ).where(debt_journal_entries.c.operation_id == operation_id)
            )
        ).all()
        entries.extend(
            (str(operation_id), str(before), str(after), str(delta))
            for before, after, delta in rows
        )
    entries.sort()
    return {
        "debts": debts,
        "transactions": transactions,
        "envelopes": sorted(
            (str(state), count) for _id, state, count in envelopes
        ),
        "entries": entries,
    }


@MODE_B
@pytest.mark.asyncio
async def test_a_committed_payment_still_replays_its_result_under_an_integrity_hold(
    client, db_session
) -> None:
    """Cell 1, hold parameter: the hold refuses new money, not a stored result."""

    code = _code()
    await _create_equivalent(client, code)
    alice = await register_and_login(client, "A_" + code)
    bob = await register_and_login(client, "B_" + code)
    await _trust(client, bob, alice, code)

    body = _payment_body(alice, bob, code, "10.00")
    first = await client.post("/api/v1/payments", json=body, headers=alice["headers"])
    assert first.status_code == 200 and first.json()["status"] == "COMMITTED", first.text

    # Premise: the first payment really moved money - one COMPLETED envelope whose effect_count
    # equals its entry rows, and a debt to show for it.
    before = await _effects(db_session, code, body["tx_id"])
    assert len(before["debts"]) == 1, before["debts"]
    assert Decimal(before["debts"][0][2]) == Decimal("10.00"), before["debts"]
    assert before["envelopes"] == [("COMPLETED", len(before["entries"]))], before["envelopes"]
    assert len(before["entries"]) > 0, before

    # The hold itself: a FAILED reconciliation result and the equivalent pointed at it. Written
    # directly because the subject here is the REFUSAL, not how a hold comes about - the reaction
    # that sets one is exercised end to end by the step 5c modules.
    equivalent_id = (
        await db_session.execute(select(Equivalent.id).where(Equivalent.code == code))
    ).scalar_one()
    result_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    await db_session.execute(
        insert(debt_reconciliation_results).values(
            id=result_id,
            equivalent_id=equivalent_id,
            status=FAILED,
            fingerprint="f" * 64,
            detail={"stand": "t1523 cell 1 hold parameter"},
            checked_at=now,
            last_checked_at=now,
            is_latest=True,
        )
    )
    await db_session.execute(
        update(Equivalent)
        .where(Equivalent.id == equivalent_id)
        .values(integrity_hold_result_id=result_id)
    )
    await db_session.commit()

    replay = await client.post("/api/v1/payments", json=body, headers=alice["headers"])

    assert replay.status_code == 200, replay.text
    assert replay.json()["tx_id"] == body["tx_id"]
    assert replay.json()["status"] == "COMMITTED"
    assert await _effects(db_session, code, body["tx_id"]) == before, (
        "the replay under the hold moved debts, wrote a transaction row or touched the journal"
    )

    # Can this stand see the outcome it was built for? Only if the hold is really in force: a NEW
    # payment must be refused here. Without this the 200 above would be equally green on a hold
    # that was never applied, and the cell would be measuring nothing.
    fresh = await client.post(
        "/api/v1/payments",
        json=_payment_body(alice, bob, code, "10.00"),
        headers=alice["headers"],
    )
    assert fresh.status_code == 409, fresh.text
    error = fresh.json()["error"]
    assert error["code"] == "E008", error
    assert error["details"]["reason"] == MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON, error
    assert error["details"]["equivalents"] == [code], error


@MODE_B
@pytest.mark.asyncio
async def test_a_tx_id_whose_payment_aborted_replays_the_stored_aborted_result(
    client, db_session, monkeypatch
) -> None:
    """Cell 2: the stored ABORTED result comes back, and no money moves either time.

    THE ABORT IS REAL, not seeded: the payment times out in prepare, so the row, its fingerprint
    and its error are all written by the application. A hand-built ABORTED row would prove only
    that the test can write one.
    """

    code = _code()
    await _create_equivalent(client, code)
    alice = await register_and_login(client, "A_" + code)
    bob = await register_and_login(client, "B_" + code)
    await _trust(client, bob, alice, code)

    # 019 stage 4: the binding phase of the direct execution (the engine's prepare before), still bounded
    # by `PREPARE_TIMEOUT_SECONDS`.
    original_prepare = PaymentService._bind_payment
    slow = {"on": True}

    async def _prepare_slowly(self, *args, **kwargs):
        if slow["on"]:
            await asyncio.sleep(0.2)
        return await original_prepare(self, *args, **kwargs)

    original_prepare_timeout = settings.PREPARE_TIMEOUT_SECONDS
    monkeypatch.setattr(PaymentService, "_bind_payment", _prepare_slowly)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)

    body = _payment_body(alice, bob, code, "10.00")
    aborted = await client.post("/api/v1/payments", json=body, headers=alice["headers"])
    assert aborted.status_code == 504, aborted.text

    # Premise, the inverse of the other cells': this payment moved NO money, and that is what the
    # replay has to keep true. The row exists, carries a fingerprint (so the replay below travels
    # the idempotent path and not a fresh payment), and has no envelope at all.
    before = await _effects(db_session, code, body["tx_id"])
    assert len(before["transactions"]) == 1, before["transactions"]
    state, payload_repr, error_repr = before["transactions"][0]
    assert state == "ABORTED", before["transactions"]
    assert "'fingerprint'" in payload_repr, payload_repr
    assert "Payment timeout" in error_repr, error_repr
    assert before["debts"] == [], before["debts"]
    assert before["envelopes"] == [], before["envelopes"]
    assert before["entries"] == [], before["entries"]

    # The fault is lifted BEFORE the replay - both halves of it, the slow prepare and the 10 ms
    # timeout that turned it into an abort. From here on the path is able to move money, so an
    # ABORTED answer can only be the stored one. (Lifting only the sleep is not enough: at a 10 ms
    # prepare timeout a genuinely fresh payment would abort again, and the cell would pass while
    # measuring nothing.)
    slow["on"] = False
    monkeypatch.setattr(
        settings, "PREPARE_TIMEOUT_SECONDS", original_prepare_timeout, raising=False
    )

    replay = await client.post("/api/v1/payments", json=body, headers=alice["headers"])

    assert replay.status_code == 200, replay.text
    stored = replay.json()
    assert stored["tx_id"] == body["tx_id"]
    assert stored["status"] == "ABORTED", stored
    assert stored["error"]["message"] == "Payment timeout", stored
    assert stored["error"]["code"] == "E007", stored
    assert await _effects(db_session, code, body["tx_id"]) == before, (
        "the replay of an ABORTED tx_id moved debts, rewrote its row or opened an envelope"
    )

    # The control that makes the ABORTED answer meaningful: the same two participants, the same
    # amount, a fresh tx_id - and money moves. Without it, a stand where every payment fails would
    # pass the assertions above.
    fresh_body = _payment_body(alice, bob, code, "10.00")
    fresh = await client.post("/api/v1/payments", json=fresh_body, headers=alice["headers"])
    assert fresh.status_code == 200 and fresh.json()["status"] == "COMMITTED", fresh.text
    after_fresh = await _effects(db_session, code, fresh_body["tx_id"])
    assert after_fresh["envelopes"] == [("COMPLETED", len(after_fresh["entries"]))], after_fresh
    assert len(after_fresh["entries"]) > 0, after_fresh
    moved = (
        await db_session.execute(
            select(func.coalesce(func.sum(Debt.amount), 0))
            .join(Equivalent, Equivalent.id == Debt.equivalent_id)
            .where(Equivalent.code == code)
        )
    ).scalar_one()
    assert Decimal(str(moved)) == Decimal("10.00"), moved
