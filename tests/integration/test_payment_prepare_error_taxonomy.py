from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, OperationalError

from app.config import settings
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import (
    ConflictException,
    GeoException,
    RetryablePaymentConflictException,
    RoutingException,
    TimeoutException,
)
from tests.integration.test_scenarios import register_and_login, _sign_payment_request


async def _seed_http_payment(client: AsyncClient, db_session, *, suffix: str):
    equivalent = (
        await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    ).scalar_one_or_none()
    if equivalent is None:
        db_session.add(Equivalent(code="USD", precision=2, is_active=True))
        await db_session.commit()

    sender = await register_and_login(client, f"PrepareSender_{suffix}")
    receiver = await register_and_login(client, f"PrepareReceiver_{suffix}")
    return sender, receiver


def _signed_payment_body(sender: dict, receiver: dict, *, tx_id: str) -> dict[str, str]:
    amount = "2.00"
    signing_key = SigningKey(base64.b64decode(sender["priv"]))
    return {
        "tx_id": tx_id,
        "to": receiver["pid"],
        "equivalent": "USD",
        "amount": amount,
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=sender["pid"],
            to_pid=receiver["pid"],
            equivalent="USD",
            amount=amount,
        ),
    }


def _install_routes_and_prepare_failure(
    monkeypatch,
    *,
    route_count: int,
    error: Exception,
) -> list[str]:
    calls: list[str] = []

    async def build_graph(self, equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(self, from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        if route_count == 1:
            return [([from_pid, to_pid], amount)]
        per_route = amount / Decimal(route_count)
        return [([from_pid, to_pid], per_route) for _ in range(route_count)]

    async def fail_single(self, *args, **kwargs):
        calls.append("single")
        raise error

    async def fail_multipath(self, *args, **kwargs):
        calls.append("multipath")
        raise error

    monkeypatch.setattr(PaymentRouter, "build_graph", build_graph)
    monkeypatch.setattr(PaymentRouter, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(PaymentEngine, "prepare", fail_single)
    monkeypatch.setattr(PaymentEngine, "prepare_routes", fail_multipath)
    return calls


async def _build_direct_payment(db_session, *, suffix: str):
    sender_public, sender_private = generate_keypair()
    receiver_public, _ = generate_keypair()
    sender = Participant(
        id=uuid.uuid4(),
        pid=get_pid_from_public_key(sender_public),
        display_name=f"DirectSender_{suffix}",
        public_key=sender_public,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid=get_pid_from_public_key(receiver_public),
        display_name=f"DirectReceiver_{suffix}",
        public_key=receiver_public,
        type="person",
        status="active",
        profile={},
    )
    equivalent = (
        await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    ).scalar_one_or_none()
    db_session.add_all([sender, receiver])
    if equivalent is None:
        db_session.add(Equivalent(code="USD", precision=2, is_active=True))
    await db_session.commit()

    tx_id = str(uuid.uuid4())
    amount = "2.00"
    request = PaymentCreateRequest(
        tx_id=tx_id,
        to=receiver.pid,
        equivalent="USD",
        amount=amount,
        signature=_sign_payment_request(
            signing_key=SigningKey(base64.b64decode(sender_private)),
            tx_id=tx_id,
            from_pid=sender.pid,
            to_pid=receiver.pid,
            equivalent="USD",
            amount=amount,
        ),
    )
    return PaymentService(db_session), sender, request, tx_id


def _serialization_failure() -> DBAPIError:
    class _DriverSerializationFailure(Exception):
        sqlstate = "40001"

    return DBAPIError(
        statement="SERIALIZABLE payment phase",
        params=None,
        orig=_DriverSerializationFailure("serialization failure"),
        connection_invalidated=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare", "commit"])
async def test_retryable_database_failure_uses_e008_at_service_boundary(
    db_session,
    monkeypatch,
    phase: str,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix=f"retryable_{phase}",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def prepare(*args, **kwargs) -> None:
        if phase == "prepare":
            raise _serialization_failure()

    async def commit(*args, **kwargs) -> None:
        if phase == "commit":
            raise _serialization_failure()

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", prepare)
    monkeypatch.setattr(service.engine, "commit", commit)

    with pytest.raises(RetryablePaymentConflictException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.status_code == 409
    assert raised.value.code == "E008"
    assert raised.value.details == {
        "retryable": True,
        "conflict_kind": "database_concurrency",
    }
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E008",
        "message": "State conflict",
        "details": raised.value.details,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["public", "internal"])
async def test_insert_serialization_failure_is_typed_before_any_tx_is_persisted(
    db_session,
    monkeypatch,
    entrypoint: str,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix=f"insert_{entrypoint}",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def fail_insert_commit() -> None:
        raise _serialization_failure()

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(db_session, "commit", fail_insert_commit)

    with pytest.raises(RetryablePaymentConflictException):
        if entrypoint == "public":
            await service.create_payment(sender.id, request)
        else:
            await service.create_payment_internal(
                sender.id,
                to_pid=request.to,
                equivalent=request.equivalent,
                amount=request.amount,
                idempotency_key=tx_id,
            )

    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    assert transaction is None


@pytest.mark.asyncio
async def test_http_insert_serialization_failure_returns_declared_conflict(
    client,
    db_session,
    monkeypatch,
) -> None:
    sender, receiver = await _seed_http_payment(
        client,
        db_session,
        suffix="insert_http",
    )
    tx_id = str(uuid.uuid4())

    async def build_graph(self, equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(self, from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def fail_insert_commit() -> None:
        raise _serialization_failure()

    monkeypatch.setattr(PaymentRouter, "build_graph", build_graph)
    monkeypatch.setattr(PaymentRouter, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(db_session, "commit", fail_insert_commit)

    response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=_signed_payment_body(sender, receiver, tx_id=tx_id),
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "E008",
        "message": "State conflict",
        "details": {
            "retryable": True,
            "conflict_kind": "database_concurrency",
        },
    }


@pytest.mark.asyncio
async def test_staged_insert_serialization_failure_propagates_without_local_rollback(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="insert_staged",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def fail_insert_flush(*args, **kwargs) -> None:
        raise _serialization_failure()

    rollback_calls = 0

    async def track_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(db_session, "flush", fail_insert_flush)
    monkeypatch.setattr(db_session, "rollback", track_rollback)

    with pytest.raises(RetryablePaymentConflictException):
        await service.create_payment_internal_staged(
            sender.id,
            to_pid=request.to,
            equivalent=request.equivalent,
            amount=request.amount,
            idempotency_key=tx_id,
        )

    assert rollback_calls == 0
    assert db_session.in_transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_count", "error_factory", "expected_status", "expected_code"),
    [
        (
            1,
            lambda: RoutingException(
                "No route at prepare",
                details={"phase": "prepare"},
            ),
            400,
            "E001",
        ),
        (
            1,
            lambda: RoutingException(
                "Capacity changed at prepare",
                insufficient_capacity=True,
                details={"phase": "prepare", "available": "0"},
            ),
            400,
            "E002",
        ),
        (
            2,
            lambda: ConflictException(
                "Prepare state conflict",
                details={"state": "PREPARED"},
            ),
            409,
            "E008",
        ),
    ],
)
async def test_prepare_preserves_typed_client_error_in_http_and_transaction(
    client: AsyncClient,
    db_session,
    monkeypatch,
    route_count: int,
    error_factory: Callable[[], GeoException],
    expected_status: int,
    expected_code: str,
) -> None:
    monkeypatch.setattr(settings, "FEATURE_FLAGS_MULTIPATH_ENABLED", True)
    sender, receiver = await _seed_http_payment(
        client,
        db_session,
        suffix=f"typed_{expected_code}",
    )
    expected_error = error_factory()
    calls = _install_routes_and_prepare_failure(
        monkeypatch,
        route_count=route_count,
        error=expected_error,
    )
    tx_id = str(uuid.uuid4())
    body = _signed_payment_body(sender, receiver, tx_id=tx_id)

    response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=body,
    )

    expected_payload = {
        "code": expected_code,
        "message": expected_error.message,
        "details": expected_error.details,
    }
    assert response.status_code == expected_status, response.text
    assert response.json()["error"] == expected_payload
    assert calls == ["single" if route_count == 1 else "multipath"]

    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == expected_payload

    get_response = await client.get(
        f"/api/v1/payments/{tx_id}",
        headers=sender["headers"],
    )
    retry_response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=body,
    )
    assert get_response.status_code == 200, get_response.text
    assert retry_response.status_code == 200, retry_response.text
    assert get_response.json()["error"] == expected_payload
    assert retry_response.json()["error"] == expected_payload
    assert calls == ["single" if route_count == 1 else "multipath"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route_count", [1, 2])
async def test_operational_prepare_error_is_sanitized_everywhere(
    client: AsyncClient,
    db_session,
    monkeypatch,
    caplog,
    route_count: int,
) -> None:
    monkeypatch.setattr(settings, "FEATURE_FLAGS_MULTIPATH_ENABLED", True)
    sender, receiver = await _seed_http_payment(
        client,
        db_session,
        suffix=f"operational_{route_count}",
    )
    raw_sentinel = "prepare-secret-sentinel"
    operational_error = OperationalError(
        f"INSERT private_table token={raw_sentinel}",
        {"password": raw_sentinel},
        RuntimeError(f"driver password={raw_sentinel}"),
    )
    calls = _install_routes_and_prepare_failure(
        monkeypatch,
        route_count=route_count,
        error=operational_error,
    )
    tx_id = str(uuid.uuid4())
    body = _signed_payment_body(sender, receiver, tx_id=tx_id)
    safe_error = {
        "code": "E010",
        "message": "Internal server error",
        "details": {},
    }
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    first_response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=body,
    )
    assert first_response.status_code == 500, first_response.text
    assert first_response.json()["error"] == safe_error
    assert calls == ["single" if route_count == 1 else "multipath"]

    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == safe_error

    get_response = await client.get(
        f"/api/v1/payments/{tx_id}",
        headers=sender["headers"],
    )
    retry_response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=body,
    )
    list_response = await client.get(
        "/api/v1/payments",
        headers=sender["headers"],
        params={"status": "ABORTED"},
    )

    assert get_response.status_code == 200, get_response.text
    assert retry_response.status_code == 200, retry_response.text
    assert list_response.status_code == 200, list_response.text
    assert get_response.json()["error"] == safe_error
    assert retry_response.json()["error"] == safe_error
    assert [item["error"] for item in list_response.json()["items"]] == [safe_error]

    exposed_payload = json.dumps(
        {
            "first": first_response.json(),
            "transaction": transaction.error,
            "get": get_response.json(),
            "retry": retry_response.json(),
            "list": list_response.json(),
        }
    )
    assert raw_sentinel not in exposed_payload
    prepare_log = next(
        record
        for record in caplog.records
        if "event=payment.prepare_failed" in record.getMessage()
    )
    assert "error_type=OperationalError" in prepare_log.getMessage()
    assert raw_sentinel not in prepare_log.getMessage()
    assert prepare_log.exc_info is None


@pytest.mark.asyncio
async def test_typed_server_prepare_error_is_sanitized(
    client: AsyncClient,
    db_session,
    monkeypatch,
) -> None:
    sender, receiver = await _seed_http_payment(
        client,
        db_session,
        suffix="typed_server",
    )
    raw_sentinel = "typed-server-secret"
    calls = _install_routes_and_prepare_failure(
        monkeypatch,
        route_count=1,
        error=GeoException(
            f"Internal dependency failed: {raw_sentinel}",
            details={"private": raw_sentinel},
        ),
    )
    tx_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=_signed_payment_body(sender, receiver, tx_id=tx_id),
    )

    safe_error = {
        "code": "E010",
        "message": "Internal server error",
        "details": {},
    }
    assert response.status_code == 500, response.text
    assert response.json()["error"] == safe_error
    assert calls == ["single"]
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == safe_error
    assert raw_sentinel not in json.dumps(response.json())
    assert raw_sentinel not in json.dumps(transaction.error)


@pytest.mark.asyncio
async def test_public_prepare_failure_rolls_back_session_before_abort(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="cleanup_order",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def fail_prepare(*args, **kwargs):
        raise OperationalError("SELECT private", {}, RuntimeError("driver private"))

    order: list[str] = []
    abort_call: tuple[tuple, dict] | None = None
    original_rollback = db_session.rollback
    original_abort = service.engine.abort

    async def tracked_rollback() -> None:
        order.append("rollback")
        await original_rollback()

    async def tracked_abort(*args, **kwargs):
        nonlocal abort_call
        order.append("abort")
        abort_call = (args, kwargs)
        return await original_abort(*args, **kwargs)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    monkeypatch.setattr(service.engine, "abort", tracked_abort)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert order == ["rollback", "abort"]
    assert abort_call == (
        (tx_id,),
        {
            "reason": "Internal server error",
            "error_code": "E010",
            "details": {},
            "commit": True,
        },
    )
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E010",
        "message": "Internal server error",
        "details": {},
    }


@pytest.mark.asyncio
async def test_direct_prepare_reraises_same_typed_error_after_durable_abort(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="typed_identity",
    )
    original_error = ConflictException(
        "prepare identity conflict",
        details={"state": "PREPARED"},
    )

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def fail_prepare(*args, **kwargs):
        raise original_error

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)

    with pytest.raises(ConflictException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value is original_error
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E008",
        "message": "prepare identity conflict",
        "details": {"state": "PREPARED"},
    }


@pytest.mark.asyncio
async def test_prepare_rollback_failure_does_not_abort_poisoned_session(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, _ = await _build_direct_payment(
        db_session,
        suffix="rollback_failure",
    )

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def fail_prepare(*args, **kwargs):
        raise RoutingException("original client error")

    order: list[str] = []
    original_rollback = db_session.rollback

    async def fail_rollback() -> None:
        order.append("rollback")
        raise RuntimeError("rollback-secret")

    async def forbidden_abort(*args, **kwargs):
        order.append("abort")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    monkeypatch.setattr(service.engine, "abort", forbidden_abort)
    monkeypatch.setattr(db_session, "rollback", fail_rollback)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "rollback-secret" not in str(raised.value)
    assert order == ["rollback"]
    await original_rollback()


@pytest.mark.asyncio
async def test_prepare_abort_failure_replaces_original_client_error_with_safe_500(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, _ = await _build_direct_payment(
        db_session,
        suffix="abort_failure",
    )

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def fail_prepare(*args, **kwargs):
        raise RoutingException("original client error")

    order: list[str] = []
    original_rollback = db_session.rollback

    async def tracked_rollback() -> None:
        order.append("rollback")
        await original_rollback()

    async def fail_abort(*args, **kwargs):
        order.append("abort")
        raise RuntimeError("abort-secret")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    monkeypatch.setattr(service.engine, "abort", fail_abort)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "original client error" not in str(raised.value)
    assert "abort-secret" not in str(raised.value)
    assert order == ["rollback", "abort"]


@pytest.mark.asyncio
async def test_operational_commit_error_is_sanitized_in_response_and_transaction(
    client: AsyncClient,
    db_session,
    monkeypatch,
    caplog,
) -> None:
    sender, receiver = await _seed_http_payment(
        client,
        db_session,
        suffix="commit_failure",
    )
    raw_sentinel = "commit-secret-sentinel"

    async def build_graph(self, equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(self, from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def prepare(self, *args, **kwargs) -> None:
        return None

    async def fail_commit(self, *args, **kwargs) -> None:
        raise OperationalError(
            f"UPDATE private_table token={raw_sentinel}",
            {"password": raw_sentinel},
            RuntimeError(f"driver password={raw_sentinel}"),
        )

    monkeypatch.setattr(PaymentRouter, "build_graph", build_graph)
    monkeypatch.setattr(PaymentRouter, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(PaymentEngine, "prepare", prepare)
    monkeypatch.setattr(PaymentEngine, "commit", fail_commit)
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    tx_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=_signed_payment_body(sender, receiver, tx_id=tx_id),
    )

    safe_error = {
        "code": "E010",
        "message": "Internal server error",
        "details": {},
    }
    assert response.status_code == 500, response.text
    assert response.json()["error"] == safe_error
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == safe_error
    assert raw_sentinel not in json.dumps(response.json())
    assert raw_sentinel not in json.dumps(transaction.error)
    commit_log = next(
        record
        for record in caplog.records
        if "event=payment.commit_failed" in record.getMessage()
    )
    assert "error_type=OperationalError" in commit_log.getMessage()
    assert raw_sentinel not in commit_log.getMessage()
    assert commit_log.exc_info is None


@pytest.mark.asyncio
async def test_typed_commit_error_is_reraised_only_after_durable_abort(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="typed_commit_successful_cleanup",
    )
    original_error = ConflictException(
        "typed commit conflict",
        details={"state": "PREPARED"},
    )

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def prepare(*args, **kwargs) -> None:
        return None

    async def fail_commit(*args, **kwargs) -> None:
        raise original_error

    order: list[str] = []
    abort_call: tuple[tuple, dict] | None = None
    original_rollback = db_session.rollback
    original_abort = service.engine.abort

    async def tracked_rollback() -> None:
        order.append("rollback")
        await original_rollback()

    async def tracked_abort(*args, **kwargs):
        nonlocal abort_call
        order.append("abort")
        abort_call = (args, kwargs)
        return await original_abort(*args, **kwargs)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", prepare)
    monkeypatch.setattr(service.engine, "commit", fail_commit)
    monkeypatch.setattr(service.engine, "abort", tracked_abort)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)

    with pytest.raises(ConflictException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value is original_error
    assert order == ["rollback", "abort"]
    assert abort_call == (
        (tx_id,),
        {
            "reason": "typed commit conflict",
            "error_code": "E008",
            "details": {"state": "PREPARED"},
            "commit": True,
        },
    )
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E008",
        "message": "typed commit conflict",
        "details": {"state": "PREPARED"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "error_kind"),
    [
        (
            lambda: OperationalError(
                "UPDATE private_table token=generic-secret",
                {"password": "generic-secret"},
                RuntimeError("driver generic-secret"),
            ),
            "generic",
        ),
        (
            lambda: ConflictException(
                "typed commit conflict",
                details={"state": "PREPARED"},
            ),
            "typed",
        ),
    ],
)
@pytest.mark.parametrize("cleanup_failure", ["rollback", "abort"])
async def test_commit_cleanup_failure_is_safe_and_ordered(
    db_session,
    monkeypatch,
    error_factory: Callable[[], Exception],
    error_kind: str,
    cleanup_failure: str,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix=f"commit_{error_kind}_{cleanup_failure}",
    )
    commit_error = error_factory()

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def prepare(*args, **kwargs) -> None:
        return None

    async def fail_commit(*args, **kwargs) -> None:
        raise commit_error

    order: list[str] = []
    abort_call: tuple[tuple, dict] | None = None
    original_rollback = db_session.rollback

    async def controlled_rollback() -> None:
        order.append("rollback")
        if cleanup_failure == "rollback":
            raise RuntimeError("commit-rollback-secret")
        await original_rollback()

    async def controlled_abort(*args, **kwargs) -> None:
        nonlocal abort_call
        order.append("abort")
        abort_call = (args, kwargs)
        raise RuntimeError("commit-abort-secret")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", prepare)
    monkeypatch.setattr(service.engine, "commit", fail_commit)
    monkeypatch.setattr(service.engine, "abort", controlled_abort)
    monkeypatch.setattr(db_session, "rollback", controlled_rollback)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value is not commit_error
    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "secret" not in str(raised.value)
    if cleanup_failure == "rollback":
        assert order == ["rollback"]
        await original_rollback()
    else:
        assert order == ["rollback", "abort"]
        expected_error = (
            {
                "reason": "typed commit conflict",
                "error_code": "E008",
                "details": {"state": "PREPARED"},
                "commit": True,
            }
            if error_kind == "typed"
            else {
                "reason": "Internal server error",
                "error_code": "E010",
                "details": {},
                "commit": True,
            }
        )
        assert abort_call == ((tx_id,), expected_error)


@pytest.mark.asyncio
async def test_prepare_cancellation_preserves_cancel_and_durably_aborts(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="cancelled",
    )

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def cancel_prepare(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", cancel_prepare)

    with pytest.raises(asyncio.CancelledError):
        await service.create_payment(sender.id, request)

    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E007",
        "message": "Payment cancelled",
        "details": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["insert", "commit"])
async def test_cancellation_at_other_payment_phases_has_terminal_state(
    db_session,
    monkeypatch,
    phase: str,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix=f"cancel_{phase}",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)

    if phase == "insert":
        original_commit = db_session.commit
        commit_calls = 0

        async def commit_then_cancel_once() -> None:
            nonlocal commit_calls
            commit_calls += 1
            await original_commit()
            if commit_calls == 1:
                raise asyncio.CancelledError

        monkeypatch.setattr(db_session, "commit", commit_then_cancel_once)
    else:
        async def prepare(tx_id_value: str, *args, **kwargs) -> None:
            await db_session.execute(
                update(Transaction)
                .where(Transaction.tx_id == tx_id_value)
                .values(state="PREPARED")
            )
            await db_session.commit()

        async def cancel_commit(*args, **kwargs) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(service.engine, "prepare", prepare)
        monkeypatch.setattr(service.engine, "commit", cancel_commit)

    with pytest.raises(asyncio.CancelledError):
        await service.create_payment(sender.id, request)

    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E007",
        "message": "Payment cancelled",
        "details": {},
    }


@pytest.mark.asyncio
async def test_staged_prepare_cancellation_aborts_before_outer_rollback(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="cancel_staged_prepare",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def cancel_prepare(*args, **kwargs) -> None:
        raise asyncio.CancelledError

    observed_states: list[str] = []
    original_abort = service.engine.abort

    async def tracked_abort(*args, **kwargs):
        result = await original_abort(*args, **kwargs)
        state = (
            await db_session.execute(
                select(Transaction.state).where(Transaction.tx_id == tx_id)
            )
        ).scalar_one()
        observed_states.append(state)
        return result

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", cancel_prepare)
    monkeypatch.setattr(service.engine, "abort", tracked_abort)

    with pytest.raises(asyncio.CancelledError):
        async with db_session.begin_nested():
            await service.create_payment_internal_staged(
                sender.id,
                to_pid=request.to,
                equivalent=request.equivalent,
                amount=request.amount,
                idempotency_key=tx_id,
            )

    assert observed_states == ["ABORTED"]
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    assert transaction is None


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancellation", "timeout"])
async def test_repeated_cancellation_during_recovery_read_still_aborts(
    db_session,
    monkeypatch,
    interruption: str,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix=f"recovery_read_{interruption}",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    prepare_started = asyncio.Event()

    async def interrupted_prepare(*args, **kwargs) -> None:
        prepare_started.set()
        await asyncio.Event().wait()

    original_rollback = db_session.rollback
    original_execute = db_session.execute
    recovery_read_started = asyncio.Event()
    release_recovery_read = asyncio.Event()
    cleanup_started = False
    recovery_read_intercepted = False
    abort_calls = 0

    async def tracked_rollback() -> None:
        nonlocal cleanup_started
        await original_rollback()
        cleanup_started = True

    async def blocking_recovery_read(*args, **kwargs):
        nonlocal recovery_read_intercepted
        if cleanup_started and not recovery_read_intercepted:
            recovery_read_intercepted = True
            recovery_read_started.set()
            await release_recovery_read.wait()
        return await original_execute(*args, **kwargs)

    original_abort = service.engine.abort

    async def tracked_abort(*args, **kwargs):
        nonlocal abort_calls
        abort_calls += 1
        return await original_abort(*args, **kwargs)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", interrupted_prepare)
    monkeypatch.setattr(service.engine, "abort", tracked_abort)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)
    monkeypatch.setattr(db_session, "execute", blocking_recovery_read)

    if interruption == "timeout":
        monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)

    owner = asyncio.create_task(service.create_payment(sender.id, request))
    await asyncio.wait_for(prepare_started.wait(), timeout=1)
    if interruption == "cancellation":
        owner.cancel("initial payment cancellation")

    await asyncio.wait_for(recovery_read_started.wait(), timeout=1)
    owner.cancel("cancellation during recovery read")
    await asyncio.sleep(0)
    release_recovery_read.set()

    with pytest.raises(asyncio.CancelledError):
        await owner

    transaction = (
        await original_execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert abort_calls == 1


@pytest.mark.asyncio
async def test_timeout_rollback_failure_is_safe_without_read_or_abort(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, _ = await _build_direct_payment(
        db_session,
        suffix="timeout_rollback_failure",
    )
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def slow_prepare(*args, **kwargs) -> None:
        await asyncio.sleep(0.05)

    cleanup_started = False
    order: list[str] = []
    original_rollback = db_session.rollback
    original_execute = db_session.execute

    async def fail_rollback() -> None:
        nonlocal cleanup_started
        cleanup_started = True
        order.append("rollback")
        raise RuntimeError("timeout-rollback-secret")

    async def tracked_execute(*args, **kwargs):
        if cleanup_started:
            order.append("execute")
            raise AssertionError("DB read must not follow a failed rollback")
        return await original_execute(*args, **kwargs)

    async def forbidden_abort(*args, **kwargs) -> None:
        order.append("abort")
        raise AssertionError("abort must not follow a failed rollback")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    monkeypatch.setattr(service.engine, "abort", forbidden_abort)
    monkeypatch.setattr(db_session, "execute", tracked_execute)
    monkeypatch.setattr(db_session, "rollback", fail_rollback)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "timeout-rollback-secret" not in str(raised.value)
    assert order == ["rollback"]
    await original_rollback()


@pytest.mark.asyncio
async def test_timeout_recovery_read_failure_is_safe_without_abort(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    service, sender, request, _ = await _build_direct_payment(
        db_session,
        suffix="timeout_read_failure",
    )
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    raw_sentinel = "timeout-read-secret"

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def slow_prepare(*args, **kwargs) -> None:
        await asyncio.sleep(0.05)

    cleanup_started = False
    order: list[str] = []
    original_rollback = db_session.rollback
    original_execute = db_session.execute

    async def tracked_rollback() -> None:
        nonlocal cleanup_started
        order.append("rollback")
        await original_rollback()
        cleanup_started = True

    async def fail_recovery_read(*args, **kwargs):
        if cleanup_started:
            order.append("execute")
            raise OperationalError(
                f"SELECT private token={raw_sentinel}",
                {"password": raw_sentinel},
                RuntimeError(f"driver password={raw_sentinel}"),
            )
        return await original_execute(*args, **kwargs)

    async def forbidden_abort(*args, **kwargs) -> None:
        order.append("abort")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    monkeypatch.setattr(service.engine, "abort", forbidden_abort)
    monkeypatch.setattr(db_session, "execute", fail_recovery_read)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert raw_sentinel not in str(raised.value)
    assert order == ["rollback", "execute"]
    read_log = next(
        record
        for record in caplog.records
        if "event=payment.timeout_recovery_read_failed" in record.getMessage()
    )
    assert "error_type=OperationalError" in read_log.getMessage()
    assert raw_sentinel not in read_log.getMessage()
    assert read_log.exc_info is None


@pytest.mark.asyncio
async def test_timeout_abort_failure_is_safe_after_recovery_read(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="timeout_abort_failure",
    )
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    raw_sentinel = "timeout-abort-secret"

    async def build_graph(equivalent_code: str) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def slow_prepare(*args, **kwargs) -> None:
        await asyncio.sleep(0.05)

    cleanup_started = False
    order: list[str] = []
    abort_call: tuple[tuple, dict] | None = None
    original_rollback = db_session.rollback
    original_execute = db_session.execute

    async def tracked_rollback() -> None:
        nonlocal cleanup_started
        order.append("rollback")
        await original_rollback()
        cleanup_started = True

    async def tracked_execute(*args, **kwargs):
        if cleanup_started:
            order.append("execute")
        return await original_execute(*args, **kwargs)

    async def fail_abort(*args, **kwargs) -> None:
        nonlocal abort_call
        order.append("abort")
        abort_call = (args, kwargs)
        raise RuntimeError(raw_sentinel)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    monkeypatch.setattr(service.engine, "abort", fail_abort)
    monkeypatch.setattr(db_session, "execute", tracked_execute)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert raw_sentinel not in str(raised.value)
    assert order == ["rollback", "execute", "abort"]
    assert abort_call == (
        (tx_id,),
        {
            "reason": "Payment timeout",
            "commit": True,
            "error_code": "E007",
        },
    )
    abort_log = next(
        record
        for record in caplog.records
        if "event=payment.timeout_abort_failed" in record.getMessage()
    )
    assert "error_type=RuntimeError" in abort_log.getMessage()
    assert raw_sentinel not in abort_log.getMessage()
    assert abort_log.exc_info is None


@pytest.mark.asyncio
async def test_staged_timeout_abort_failure_has_symmetric_safe_log(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="staged_timeout_abort_failure",
    )
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    raw_sentinel = "staged-timeout-abort-secret"

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, amount: Decimal, **kwargs):
        return [([from_pid, to_pid], amount)]

    async def slow_prepare(*args, **kwargs) -> None:
        await asyncio.sleep(0.05)

    async def fail_abort(*args, **kwargs) -> None:
        raise RuntimeError(raw_sentinel)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    monkeypatch.setattr(service.engine, "abort", fail_abort)
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    with pytest.raises(TimeoutException):
        async with db_session.begin_nested():
            await service.create_payment_internal_staged(
                sender.id,
                to_pid=request.to,
                equivalent=request.equivalent,
                amount=request.amount,
                idempotency_key=tx_id,
            )

    abort_log = next(
        record
        for record in caplog.records
        if "event=payment.timeout_abort_failed" in record.getMessage()
    )
    assert "error_type=RuntimeError" in abort_log.getMessage()
    assert raw_sentinel not in abort_log.getMessage()
    assert abort_log.exc_info is None
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    assert transaction is None


@pytest.mark.asyncio
async def test_staged_generic_prepare_failure_is_safe_without_session_commit_or_rollback(
    db_session,
    monkeypatch,
) -> None:
    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="staged_uow",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    raw_sentinel = "staged-prepare-secret"

    async def fail_prepare(*args, **kwargs):
        raise OperationalError(
            f"INSERT private_table token={raw_sentinel}",
            {"password": raw_sentinel},
            RuntimeError(f"driver password={raw_sentinel}"),
        )

    session_calls: list[str] = []
    original_rollback = db_session.rollback

    async def forbidden_rollback() -> None:
        session_calls.append("rollback")

    async def forbidden_commit() -> None:
        session_calls.append("commit")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    monkeypatch.setattr(db_session, "rollback", forbidden_rollback)
    monkeypatch.setattr(db_session, "commit", forbidden_commit)

    with pytest.raises(GeoException) as raised:
        await service.create_payment_internal_staged(
            sender.id,
            to_pid=request.to,
            equivalent=request.equivalent,
            amount=request.amount,
            idempotency_key=tx_id,
        )

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert session_calls == []
    assert db_session.in_transaction()
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert transaction.error == {
        "code": "E010",
        "message": "Internal server error",
        "details": {},
    }
    assert raw_sentinel not in json.dumps(transaction.error)
    await original_rollback()
