from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction


@pytest.mark.asyncio
async def test_admin_whoami_requires_admin_token(client):
    r = await client.get("/api/v1/admin/whoami")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_whoami_returns_role_admin(client):
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}
    r = await client.get("/api/v1/admin/whoami", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"role": "admin"}


@pytest.mark.asyncio
async def test_admin_dev_auth_allows_missing_token_for_allowlisted_ip(client, monkeypatch):
    # NOTE: httpx ASGI transport client host can vary (e.g. 'testclient').
    monkeypatch.setattr(settings, "ENV", "dev", raising=False)
    monkeypatch.setattr(settings, "ADMIN_DEV_MODE", True, raising=False)
    monkeypatch.setattr(
        settings,
        "ADMIN_DEV_ALLOWLIST",
        "127.0.0.1,::1,testclient,test",
        raising=False,
    )

    r = await client.get("/api/v1/admin/whoami")
    assert r.status_code == 200
    assert r.json().get("role") == "admin"


@pytest.mark.asyncio
async def test_admin_equivalents_include_inactive(client, db_session):
    # Arrange
    db_session.add_all(
        [
            Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True),
            Equivalent(code="USD", symbol="$", description="US Dollar", precision=2, metadata_={}, is_active=False),
        ]
    )
    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Default: only active
    r1 = await client.get("/api/v1/admin/equivalents", headers=headers)
    assert r1.status_code == 200
    assert [e["code"] for e in r1.json().get("items", [])] == ["UAH"]

    # include_inactive=true: all
    r2 = await client.get("/api/v1/admin/equivalents?include_inactive=true", headers=headers)
    assert r2.status_code == 200
    assert [e["code"] for e in r2.json().get("items", [])] == ["UAH", "USD"]


@pytest.mark.asyncio
async def test_admin_feature_flags_partial_patch(client, monkeypatch):
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Arrange
    monkeypatch.setattr(settings, "FEATURE_FLAGS_MULTIPATH_ENABLED", True)
    monkeypatch.setattr(settings, "FEATURE_FLAGS_FULL_MULTIPATH_ENABLED", False)
    monkeypatch.setattr(settings, "CLEARING_ENABLED", True)

    # Act: patch only one field
    r = await client.patch(
        "/api/v1/admin/feature-flags",
        headers=headers,
        json={"multipath_enabled": False, "reason": "test"},
    )
    assert r.status_code == 200

    # Assert
    body = r.json()
    assert body["multipath_enabled"] is False
    assert body["full_multipath_enabled"] is False
    assert body["clearing_enabled"] is True


@pytest.mark.asyncio
async def test_admin_graph_snapshot_include_extras_smoke(client, db_session, monkeypatch):
    # Arrange minimal data
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    db_session.add(alice)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    stuck_at = now - timedelta(seconds=int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120) + 10)

    tx = Transaction(
        tx_id="tx_test_1",
        idempotency_key=None,
        type="PAYMENT",
        initiator_id=alice.id,
        payload={"equivalent": "UAH"},
        signatures=[],
        state="PREPARED",
        error=None,
        created_at=stuck_at,
        updated_at=stuck_at,
    )
    db_session.add(tx)

    db_session.add(
        AuditLog(
            actor_id=None,
            actor_role="admin",
            action="admin.test",
            object_type="test",
            object_id="1",
            reason="test",
            before_state=None,
            after_state=None,
            request_id="rid",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )

    await db_session.commit()

    # Keep payloads small and deterministic in unit tests
    monkeypatch.setattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_INCIDENTS", 10, raising=False)
    monkeypatch.setattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_AUDIT_EVENTS", 10, raising=False)
    monkeypatch.setattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS", 10, raising=False)

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/admin/graph/snapshot?include=incidents,audit_log,transactions",
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()

    # Assert: keys exist and are non-empty
    assert isinstance(payload.get("incidents"), list)
    assert isinstance(payload.get("audit_log"), list)
    assert isinstance(payload.get("transactions"), list)

    assert len(payload["incidents"]) >= 1
    assert len(payload["audit_log"]) >= 1
    assert len(payload["transactions"]) >= 1
