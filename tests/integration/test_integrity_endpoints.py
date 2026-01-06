import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from app.core.integrity import compute_and_store_integrity_checkpoints
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
