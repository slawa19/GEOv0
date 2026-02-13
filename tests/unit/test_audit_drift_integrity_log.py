import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_log import IntegrityAuditLog


@pytest.mark.asyncio
async def test_audit_drift_integrity_audit_log_round_trip(db_session: AsyncSession) -> None:
    log = IntegrityAuditLog(
        operation_type="SIMULATOR_AUDIT_DRIFT",
        tx_id=None,
        equivalent_code="USD",
        state_checksum_before="",
        state_checksum_after="",
        affected_participants={
            "drifts": [
                {
                    "participant_id": "p_alice",
                    "expected_delta": "-50.00",
                    "actual_delta": "-20.00",
                    "drift": "30.00",
                }
            ],
            "tick_index": 42,
            "source": "post_tick_audit",
        },
        invariants_checked={
            "post_tick_balance": {"passed": False, "total_drift": "30.00"}
        },
        verification_passed=False,
        error_details={
            "drifts": [
                {
                    "participant_id": "p_alice",
                    "expected_delta": "-50.00",
                    "actual_delta": "-20.00",
                    "drift": "30.00",
                }
            ],
            "severity": "warning",
        },
    )

    db_session.add(log)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(IntegrityAuditLog).where(IntegrityAuditLog.id == log.id)
        )
    ).scalar_one()

    assert row.operation_type == "SIMULATOR_AUDIT_DRIFT"
    assert row.tx_id is None
    assert row.equivalent_code == "USD"
    assert row.verification_passed is False

    affected = row.affected_participants
    assert affected.get("tick_index") == 42
    assert isinstance(affected.get("drifts"), list)

    inv = row.invariants_checked
    assert inv.get("post_tick_balance", {}).get("passed") is False
    assert inv.get("post_tick_balance", {}).get("total_drift") == "30.00"
