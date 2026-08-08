from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import event, select

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.request_id import validate_request_id


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _audit_count(db_session, *, action: str, object_id: str) -> int:
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == action,
                AuditLog.object_id == object_id,
            )
        )
    ).scalars()
    return len(rows.all())


async def _expect_audit_failure(
    *,
    db_session,
    request: Callable[[], object],
) -> None:
    def fail_when_audit_is_flushed(session, _flush_context, _instances) -> None:
        if any(isinstance(item, AuditLog) for item in session.new):
            raise RuntimeError("required audit flush failed")

    event.listen(db_session.sync_session, "before_flush", fail_when_audit_is_flushed)
    try:
        with pytest.raises(RuntimeError, match="required audit flush failed"):
            await request()
    finally:
        event.remove(
            db_session.sync_session,
            "before_flush",
            fail_when_audit_is_flushed,
        )


@pytest.mark.asyncio
async def test_admin_participant_status_and_equivalent_mutations_commit_with_audit(
    client,
    db_session,
) -> None:
    participant = Participant(
        pid="atomic-admin-user",
        display_name="Atomic Admin User",
        public_key="A" * 64,
        type="person",
        status="active",
    )
    db_session.add(participant)
    await db_session.commit()

    freeze = await client.post(
        "/api/v1/admin/participants/atomic-admin-user/freeze",
        headers=_admin_headers(),
        json={"reason": "atomic-freeze"},
    )
    assert freeze.status_code == 200, freeze.text
    assert freeze.json() == {"pid": "atomic-admin-user", "status": "suspended"}
    assert await _audit_count(
        db_session,
        action="admin.participants.freeze",
        object_id="atomic-admin-user",
    ) == 1

    create = await client.post(
        "/api/v1/admin/equivalents",
        headers=_admin_headers(),
        json={
            "code": "ATM",
            "description": "before",
            "is_active": False,
            "reason": "atomic-create",
        },
    )
    assert create.status_code == 200, create.text
    assert create.json()["code"] == "ATM"
    assert await _audit_count(
        db_session,
        action="admin.equivalents.create",
        object_id="ATM",
    ) == 1

    patch = await client.patch(
        "/api/v1/admin/equivalents/ATM",
        headers=_admin_headers(),
        json={"description": "after", "reason": "atomic-patch"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["description"] == "after"
    assert await _audit_count(
        db_session,
        action="admin.equivalents.patch",
        object_id="ATM",
    ) == 1

    delete = await client.request(
        "DELETE",
        "/api/v1/admin/equivalents/ATM",
        headers=_admin_headers(),
        json={"reason": "atomic-delete"},
    )
    assert delete.status_code == 200, delete.text
    assert delete.json() == {"deleted": "ATM"}
    assert await _audit_count(
        db_session,
        action="admin.equivalents.delete",
        object_id="ATM",
    ) == 1


@pytest.mark.asyncio
async def test_admin_mutation_uses_canonical_request_id_for_mandatory_audit(
    client,
    db_session,
) -> None:
    participant = Participant(
        pid="canonical-request-id-admin",
        display_name="Canonical Request ID Admin",
        public_key="R" * 64,
        type="person",
        status="active",
    )
    db_session.add(participant)
    await db_session.commit()

    headers = _admin_headers()
    headers["X-Request-ID"] = "x" * 65
    response = await client.post(
        "/api/v1/admin/participants/canonical-request-id-admin/freeze",
        headers=headers,
        json={"reason": "canonical-request-id"},
    )

    assert response.status_code == 200, response.text
    response_request_id = response.headers["X-Request-ID"]
    assert validate_request_id(response_request_id) == response_request_id
    assert len(response_request_id) <= 64
    await db_session.refresh(participant)
    assert participant.status == "suspended"

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.participants.freeze",
                AuditLog.object_id == "canonical-request-id-admin",
            )
        )
    ).scalar_one()
    assert audit.request_id == response_request_id


@pytest.mark.asyncio
async def test_admin_participant_status_rolls_back_when_audit_flush_fails(
    client,
    db_session,
) -> None:
    participant = Participant(
        pid="atomic-freeze-failure",
        display_name="Atomic Freeze Failure",
        public_key="B" * 64,
        type="person",
        status="active",
    )
    db_session.add(participant)
    await db_session.commit()

    await _expect_audit_failure(
        db_session=db_session,
        request=lambda: client.post(
            "/api/v1/admin/participants/atomic-freeze-failure/freeze",
            headers=_admin_headers(),
            json={"reason": "must-rollback"},
        ),
    )

    await db_session.refresh(participant)
    assert participant.status == "active"
    assert await _audit_count(
        db_session,
        action="admin.participants.freeze",
        object_id="atomic-freeze-failure",
    ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "patch", "delete"])
async def test_admin_equivalent_mutation_rolls_back_when_audit_flush_fails(
    client,
    db_session,
    operation: str,
) -> None:
    if operation != "create":
        db_session.add(
            Equivalent(
                code="ATF",
                description="before",
                precision=2,
                is_active=False,
            )
        )
        await db_session.commit()

    if operation == "create":
        method = "POST"
        path = "/api/v1/admin/equivalents"
        payload = {"code": "ATF", "reason": "must-rollback"}
    elif operation == "patch":
        method = "PATCH"
        path = "/api/v1/admin/equivalents/ATF"
        payload = {"description": "after", "reason": "must-rollback"}
    else:
        method = "DELETE"
        path = "/api/v1/admin/equivalents/ATF"
        payload = {"reason": "must-rollback"}

    async def request():
        return await client.request(
            method,
            path,
            headers=_admin_headers(),
            json=payload,
        )

    await _expect_audit_failure(db_session=db_session, request=request)

    equivalent = (
        await db_session.execute(select(Equivalent).where(Equivalent.code == "ATF"))
    ).scalar_one_or_none()
    if operation == "create":
        assert equivalent is None
    else:
        assert equivalent is not None
        assert equivalent.description == "before"
    assert await _audit_count(
        db_session,
        action=f"admin.equivalents.{operation}",
        object_id="ATF",
    ) == 0


@pytest.mark.asyncio
async def test_admin_delete_active_equivalent_is_rejected_without_audit(
    client,
    db_session,
) -> None:
    db_session.add(Equivalent(code="ACT", precision=2, is_active=True))
    await db_session.commit()

    response = await client.request(
        "DELETE",
        "/api/v1/admin/equivalents/ACT",
        headers=_admin_headers(),
        json={"reason": "must-not-delete"},
    )

    assert response.status_code == 409
    assert (
        await db_session.execute(select(Equivalent).where(Equivalent.code == "ACT"))
    ).scalar_one_or_none() is not None
    assert await _audit_count(
        db_session,
        action="admin.equivalents.delete",
        object_id="ACT",
    ) == 0
