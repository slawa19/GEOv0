from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from starlette.requests import Request

from app.api import deps
from app.api.v1 import admin as admin_api
from app.config import settings
from app.db.models.audit_log import AuditLog
from app.main import app
from app.schemas.admin import AdminConfigPatchRequest


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _config_value(client, key: str):
    response = await client.get("/api/v1/admin/config", headers=_admin_headers())
    assert response.status_code == 200, response.text
    items = {item["key"]: item["value"] for item in response.json()["items"]}
    return items[key]


class ControlledAuditDB:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        commit_started: asyncio.Event | None = None,
        release_commit: asyncio.Event | None = None,
        observe_commit: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.fail_commit = fail_commit
        self.commit_started = commit_started
        self.release_commit = release_commit
        self.observe_commit = observe_commit
        self.pending: list[AuditLog] = []
        self.durable: list[AuditLog] = []
        self.observed_at_commit: dict[str, Any] | None = None
        self.rollback_calls = 0

    def add(self, item: AuditLog) -> None:
        self.pending.append(item)

    async def commit(self) -> None:
        if self.commit_started is not None:
            self.commit_started.set()
        if self.release_commit is not None:
            await self.release_commit.wait()
        if self.observe_commit is not None:
            self.observed_at_commit = self.observe_commit()
        if self.fail_commit:
            raise RuntimeError("audit commit failed")
        self.durable.extend(self.pending)
        self.pending.clear()

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.pending.clear()


def _use_audit_db(controlled_db: ControlledAuditDB) -> None:
    async def override_get_db():
        yield controlled_db

    app.dependency_overrides[deps.get_db] = override_get_db


def _config_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/api/v1/admin/config",
            "headers": [],
        }
    )


@pytest.mark.asyncio
async def test_config_patch_rejects_entire_batch_when_later_key_is_not_mutable(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)

    response = await client.patch(
        "/api/v1/admin/config",
        headers=_admin_headers(),
        json={
            "updates": {
                "ROUTING_MAX_PATHS": 4,
                "NOT_MUTABLE": 1,
            },
            "reason": "atomicity-test",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "E009",
            "message": "Config key not mutable: NOT_MUTABLE",
            "details": {},
            "request_id": response.headers["X-Request-ID"],
        }
    }
    assert settings.ROUTING_MAX_PATHS == 3
    assert await _config_value(client, "ROUTING_MAX_PATHS") == 3


@pytest.mark.asyncio
async def test_config_patch_rejects_string_for_boolean_without_mutation(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

    response = await client.patch(
        "/api/v1/admin/config",
        headers=_admin_headers(),
        json={"updates": {"RATE_LIMIT_ENABLED": "false"}},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "E009",
            "message": "Invalid value for config key: RATE_LIMIT_ENABLED",
            "details": {"key": "RATE_LIMIT_ENABLED"},
            "request_id": response.headers["X-Request-ID"],
        }
    }
    assert settings.RATE_LIMIT_ENABLED is True


@pytest.mark.asyncio
async def test_config_patch_accepts_native_boolean_and_integer_values(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)

    response = await client.patch(
        "/api/v1/admin/config",
        headers=_admin_headers(),
        json={
            "updates": {
                "RATE_LIMIT_ENABLED": False,
                "ROUTING_MAX_PATHS": 4,
            }
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": ["RATE_LIMIT_ENABLED", "ROUTING_MAX_PATHS"]
    }
    assert settings.RATE_LIMIT_ENABLED is False
    assert settings.ROUTING_MAX_PATHS == 4


@pytest.mark.asyncio
async def test_config_patch_keeps_old_values_visible_until_audit_is_durable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    monkeypatch.setattr(admin_api, "_runtime_config_lock", asyncio.Lock())
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    controlled_db = ControlledAuditDB(
        commit_started=commit_started,
        release_commit=release_commit,
    )

    patch = asyncio.create_task(
        admin_api.patch_admin_config(
            AdminConfigPatchRequest(
                updates={"ROUTING_MAX_PATHS": 4},
                reason="required-audit-barrier",
            ),
            _config_request(),
            controlled_db,
        )
    )
    await asyncio.wait_for(commit_started.wait(), timeout=1.0)

    response = await admin_api.get_admin_config()
    visible = {item.key: item.value for item in response.items}
    assert visible["ROUTING_MAX_PATHS"] == 3
    assert settings.ROUTING_MAX_PATHS == 3
    assert controlled_db.durable == []

    release_commit.set()
    result = await asyncio.wait_for(patch, timeout=1.0)

    assert result.updated == ["ROUTING_MAX_PATHS"]
    assert settings.ROUTING_MAX_PATHS == 4
    assert len(controlled_db.durable) == 1


@pytest.mark.asyncio
async def test_config_patch_audit_failure_never_publishes_values(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    monkeypatch.setattr(admin_api, "_runtime_config_lock", asyncio.Lock())
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    controlled_db = ControlledAuditDB(
        fail_commit=True,
        commit_started=commit_started,
        release_commit=release_commit,
    )

    patch = asyncio.create_task(
        admin_api.patch_admin_config(
            AdminConfigPatchRequest(updates={"ROUTING_MAX_PATHS": 4}),
            _config_request(),
            controlled_db,
        )
    )
    await asyncio.wait_for(commit_started.wait(), timeout=1.0)
    assert settings.ROUTING_MAX_PATHS == 3

    release_commit.set()

    with pytest.raises(RuntimeError, match="audit commit failed"):
        await patch

    assert settings.ROUTING_MAX_PATHS == 3
    assert controlled_db.rollback_calls == 1
    assert controlled_db.pending == []
    assert controlled_db.durable == []


@pytest.mark.asyncio
async def test_config_patch_persists_exact_audit_before_and_after(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    controlled_db = ControlledAuditDB(
        observe_commit=lambda: {"ROUTING_MAX_PATHS": settings.ROUTING_MAX_PATHS}
    )
    _use_audit_db(controlled_db)

    response = await client.patch(
        "/api/v1/admin/config",
        headers={**_admin_headers(), "X-Request-ID": "config-audit-test"},
        json={
            "updates": {"ROUTING_MAX_PATHS": 4},
            "reason": "required-audit-success",
        },
    )

    assert response.status_code == 200, response.text
    assert settings.ROUTING_MAX_PATHS == 4
    assert controlled_db.rollback_calls == 0
    assert controlled_db.pending == []
    assert controlled_db.observed_at_commit == {"ROUTING_MAX_PATHS": 3}
    assert len(controlled_db.durable) == 1
    audit = controlled_db.durable[0]
    assert audit.action == "admin.config.patch"
    assert audit.object_type == "config"
    assert audit.object_id is None
    assert audit.reason == "required-audit-success"
    assert audit.before_state == {"ROUTING_MAX_PATHS": 3}
    assert audit.after_state == {"ROUTING_MAX_PATHS": 4}
    assert audit.request_id == "config-audit-test"


@pytest.mark.asyncio
async def test_config_patch_cancellation_waits_for_durable_audit_and_publish(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    monkeypatch.setattr(admin_api, "_runtime_config_lock", asyncio.Lock())
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    controlled_db = ControlledAuditDB(
        commit_started=commit_started,
        release_commit=release_commit,
    )

    patch = asyncio.create_task(
        admin_api.patch_admin_config(
            AdminConfigPatchRequest(updates={"ROUTING_MAX_PATHS": 4}),
            _config_request(),
            controlled_db,
        )
    )
    await asyncio.wait_for(commit_started.wait(), timeout=1.0)
    assert settings.ROUTING_MAX_PATHS == 3

    patch.cancel()
    await asyncio.sleep(0)
    assert not patch.done()
    release_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await patch

    assert settings.ROUTING_MAX_PATHS == 4
    assert controlled_db.rollback_calls == 0
    assert controlled_db.pending == []
    assert len(controlled_db.durable) == 1


@pytest.mark.asyncio
async def test_repeated_config_patch_cancellation_cancels_audit_before_unlock(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    monkeypatch.setattr(admin_api, "_runtime_config_lock", asyncio.Lock())
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    first_db = ControlledAuditDB(
        commit_started=commit_started,
        release_commit=release_commit,
    )
    second_db = ControlledAuditDB()

    first_patch = asyncio.create_task(
        admin_api.patch_admin_config(
            AdminConfigPatchRequest(updates={"ROUTING_MAX_PATHS": 4}),
            _config_request(),
            first_db,
        )
    )
    await asyncio.wait_for(commit_started.wait(), timeout=1.0)

    second_patch = asyncio.create_task(
        admin_api.patch_admin_config(
            AdminConfigPatchRequest(updates={"ROUTING_MAX_PATHS": 5}),
            _config_request(),
            second_db,
        )
    )
    await asyncio.sleep(0)
    assert not second_patch.done()

    first_patch.cancel("first cancellation")
    await asyncio.sleep(0)
    first_patch.cancel("second cancellation")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(first_patch, timeout=1.0)
    assert cancelled.value.args == ("first cancellation",)

    response = await asyncio.wait_for(second_patch, timeout=1.0)
    assert response.updated == ["ROUTING_MAX_PATHS"]
    assert settings.ROUTING_MAX_PATHS == 5
    assert first_db.rollback_calls == 1
    assert first_db.pending == []
    assert first_db.durable == []
    assert len(second_db.durable) == 1
