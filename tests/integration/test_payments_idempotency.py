import base64
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from tests.integration.test_scenarios import (
    register_and_login,
    _sign_payment_request,
    _sign_trustline_create_request,
)


# ----------------------------------------------------------------------------------------------
# T1523 cells 1 and 4 (2026-09-21).  Both tests below already drove their path and asserted a
# status; a status is not money.  What is added here is the no-double-effect triple - debts,
# `transactions` rows for this `tx_id`, and the operation envelope with its journal entries - and,
# before it, the premise that the first payment actually moved money.  Without that premise
# "unchanged" is satisfied by a payment that wrote nothing at all.
#
# Column selects, never `select(Debt)`: the HTTP request handler shares THIS session
# (`tests/conftest.py`, `override_get_db`), so entity loads could be answered from an identity map
# populated before the payment ran, and the snapshot would then be of the test's memory rather
# than of the database.
# ----------------------------------------------------------------------------------------------


async def _payment_effects(db_session, tx_id: str) -> dict[str, object]:
    """Everything a replay is forbidden to move, read as four independent queries."""

    debts = sorted(
        (str(debtor), str(creditor), str(equivalent), str(amount))
        for debtor, creditor, equivalent, amount in (
            await db_session.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id, Debt.amount)
            )
        ).all()
    )
    transactions = sorted(
        (str(state), repr(payload))
        for state, payload in (
            await db_session.execute(
                select(Transaction.state, Transaction.payload).where(
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
    for operation_id, _state, _effect_count in envelopes:
        entries.extend(
            (str(operation_id), str(before), str(after), str(delta))
            for before, after, delta in (
                await db_session.execute(
                    select(
                        debt_journal_entries.c.amount_before,
                        debt_journal_entries.c.amount_after,
                        debt_journal_entries.c.delta,
                    ).where(debt_journal_entries.c.operation_id == operation_id)
                )
            ).all()
        )
    return {
        "debts": debts,
        "transactions": transactions,
        "envelopes": sorted(
            (str(state), effect_count) for _id, state, effect_count in envelopes
        ),
        "entries": sorted(entries),
    }


async def _effects_of_a_payment_that_moved_money(db_session, tx_id: str) -> dict[str, object]:
    """The anti-vacuum premise: assert the first payment wrote money, then snapshot it."""

    effects = await _payment_effects(db_session, tx_id)
    entry_count = len(effects["entries"])  # type: ignore[arg-type]
    assert effects["envelopes"] == [("COMPLETED", entry_count)], (
        f"premise: one COMPLETED envelope whose effect_count matches its entry rows, "
        f"got {effects['envelopes']!r} against {entry_count} entries"
    )
    assert entry_count > 0, "premise: the payment recorded no journal entry, so it moved nothing"
    assert len(effects["transactions"]) == 1, effects["transactions"]
    assert any(
        amount not in {"0", "0.00000000"} for *_rest, amount in effects["debts"]
    ), f"premise: no debt carries a balance after the payment: {effects['debts']!r}"
    return effects


async def _seed_equivalent(db_session, code: str):
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    eq = result.scalar_one_or_none()
    if not eq:
        eq = Equivalent(code=code, description=code, precision=2)
        db_session.add(eq)
        await db_session.commit()
        await db_session.refresh(eq)
    return eq


@pytest.mark.asyncio
async def test_payments_tx_id_returns_same_result(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_Idempotency")
    bob = await register_and_login(client, "Bob_Idempotency")

    # Bob must trust Alice for Alice -> Bob payments.
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    signing_key = SigningKey(base64.b64decode(alice["priv"]))

    tx_id = str(uuid.uuid4())

    body = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "10.00",
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="10.00",
        ),
    }

    headers = dict(alice["headers"])

    resp1 = await client.post("/api/v1/payments", json=body, headers=headers)
    assert resp1.status_code == 200
    p1 = resp1.json()
    assert p1["tx_id"] == tx_id
    assert p1["status"] == "COMMITTED"

    # T1523 cell 1: the first payment moved money, and this is what it moved.
    before = await _effects_of_a_payment_that_moved_money(db_session, tx_id)

    resp2 = await client.post("/api/v1/payments", json=body, headers=headers)
    assert resp2.status_code == 200
    p2 = resp2.json()
    assert p2["tx_id"] == p1["tx_id"]
    assert p2["status"] == p1["status"]

    # The triple: debts, the `transactions` rows for this tx_id, and the envelope with its
    # entries. The replay is answered from the stored row, so none of the three may move.
    assert await _payment_effects(db_session, tx_id) == before, (
        "the replay moved debts, wrote a second transaction row or opened a second envelope"
    )


@pytest.mark.asyncio
async def test_payments_tx_id_reuse_with_different_payload_conflicts(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_Idempotency_Conflict")
    bob = await register_and_login(client, "Bob_Idempotency_Conflict")

    # Bob must trust Alice for Alice -> Bob payments.
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    signing_key = SigningKey(base64.b64decode(alice["priv"]))

    headers = dict(alice["headers"])

    tx_id = str(uuid.uuid4())

    body1 = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "10.00",
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="10.00",
        ),
    }

    body2 = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "11.00",
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="11.00",
        ),
    }

    resp1 = await client.post("/api/v1/payments", json=body1, headers=headers)
    assert resp1.status_code == 200

    # T1523 cell 4: the 10.00 payment moved money; the 11.00 request that reuses its tx_id must
    # move none. The premise matters here as much as the refusal: a conflict over a tx_id that
    # never bought anything proves nothing about money.
    before = await _effects_of_a_payment_that_moved_money(db_session, tx_id)

    resp2 = await client.post("/api/v1/payments", json=body2, headers=headers)
    assert resp2.status_code == 409
    payload = resp2.json()
    assert payload["error"]["code"] == "E008"

    assert await _payment_effects(db_session, tx_id) == before, (
        "the refused different-payload request still moved debts, wrote a transaction row or "
        "opened an envelope"
    )


@pytest.mark.asyncio
async def test_payments_missing_tx_id_is_bad_request(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_MissingTxId")
    bob = await register_and_login(client, "Bob_MissingTxId")

    # Bob must trust Alice for Alice -> Bob payments.
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    # No tx_id (and no Idempotency-Key fallback): should fail at API boundary.
    resp = await client.post(
        "/api/v1/payments",
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": "x",
        },
        headers=alice["headers"],
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["code"] == "E009"
