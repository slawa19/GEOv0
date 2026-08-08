from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.integrity import compute_and_store_integrity_checkpoints
from app.db.models.equivalent import Equivalent
from app.schemas.integrity import EquivalentIntegrityStatus
from tests.integration.test_scenarios import register_and_login


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
async def test_integrity_status_and_verify_and_audit_log(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")
    user = await register_and_login(client, "IntegrityUser")

    resp = await client.get("/api/v1/integrity/status", headers=user["headers"])
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] in {"healthy", "warning", "critical"}
    assert "USD" in payload["equivalents"]

    resp = await client.post("/api/v1/integrity/verify", json={}, headers=user["headers"])
    assert resp.status_code == 200
    verify_payload = resp.json()
    assert "USD" in verify_payload["equivalents"]

    resp = await client.get("/api/v1/integrity/audit-log", headers=user["headers"])
    assert resp.status_code == 200
    log_payload = resp.json()
    assert isinstance(log_payload.get("items"), list)
    assert any(item.get("action") == "integrity.verify" for item in log_payload["items"])


@pytest.mark.asyncio
async def test_integrity_checksum_returns_404_until_checkpoint_exists(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")
    user = await register_and_login(client, "IntegrityUser2")

    resp = await client.get("/api/v1/integrity/checksum/USD", headers=user["headers"])
    assert resp.status_code == 404

    await compute_and_store_integrity_checkpoints(db_session)

    resp = await client.get("/api/v1/integrity/checksum/USD", headers=user["headers"])
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["equivalent"] == "USD"
    assert isinstance(payload.get("checksum"), str) and len(payload["checksum"]) == 64

    invariants_status = payload.get("invariants_status")
    assert isinstance(invariants_status, dict)

    checks = invariants_status.get("checks")
    assert isinstance(checks, dict)
    assert set(checks.keys()) == {"zero_sum", "trust_limits", "debt_symmetry"}
    assert checks["zero_sum"]["passed"] is True
    assert checks["trust_limits"]["passed"] is True
    assert checks["debt_symmetry"]["passed"] is True


def _assert_datetime_has_offset(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None


def test_equivalent_integrity_status_preserves_aware_timestamp_offset() -> None:
    source = datetime(2026, 8, 8, 12, 0, tzinfo=timezone(timedelta(hours=3)))

    parsed = EquivalentIntegrityStatus(
        status="healthy",
        last_verified=source,
    ).last_verified

    assert parsed == source
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=3)


@pytest.mark.asyncio
async def test_integrity_status_and_verify_serialize_sqlite_checkpoint_as_utc(
    client: AsyncClient,
    db_session,
):
    await _seed_equivalent(db_session, "TZCHK")
    user = await register_and_login(client, "IntegrityTimezoneUser")
    await compute_and_store_integrity_checkpoints(db_session)

    status = await client.get("/api/v1/integrity/status", headers=user["headers"])
    assert status.status_code == 200, status.text
    status_last_verified = status.json()["equivalents"]["TZCHK"]["last_verified"]
    _assert_datetime_has_offset(status_last_verified)

    verify = await client.post(
        "/api/v1/integrity/verify",
        json={"equivalent": "TZCHK"},
        headers=user["headers"],
    )
    assert verify.status_code == 200, verify.text
    verify_last_verified = verify.json()["equivalents"]["TZCHK"]["last_verified"]
    _assert_datetime_has_offset(verify_last_verified)
