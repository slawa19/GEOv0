import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.payments.engine import PaymentEngine
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.utils.error_codes import ErrorCode
from tests.integration.test_scenarios import register_and_login


@pytest.mark.asyncio
async def test_payment_aborted_result_has_error_code(client: AsyncClient, db_session):
    alice = await register_and_login(client, "Alice_ABORT")
    bob = await register_and_login(client, "Bob_ABORT")

    alice_row = (
        await db_session.execute(select(Participant).where(Participant.pid == alice["pid"]))
    ).scalar_one()

    tx_id = str(uuid.uuid4())
    tx = Transaction(
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=alice_row.id,
        payload={
            "from": alice["pid"],
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "1.00",
        },
        state="NEW",
    )
    db_session.add(tx)
    await db_session.commit()

    engine = PaymentEngine(db_session)
    await engine.abort(tx_id, reason="Payment timeout", error_code=ErrorCode.E007)

    # Fetch via public payments endpoint (maps Transaction.error -> PaymentResult.error).
    resp = await client.get(f"/api/v1/payments/{tx_id}", headers=alice["headers"])
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "ABORTED"
    assert payload["error"]["code"] == ErrorCode.E007.value
    assert payload["error"]["code"] != "ERROR"

