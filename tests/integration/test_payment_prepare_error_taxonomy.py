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
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
import app.core.payments.service as payment_service_module
from app.core.payments.service import PaymentService, PaymentTransactionUnusable
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
from tests.conftest import MODE_B
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


def _fail_the_payment_insert(monkeypatch, error_factory) -> list[int]:
    """Make the flush that inserts the payment's `Transaction` raise; every other flush runs.

    Since 019 stage 3 the payment's row is inserted by a flush inside its one transaction (before it
    was a commit of its own), on whichever session `pay()` opened - so the seam is the flush of a
    pending `Transaction`, not a session method of the test's session.
    """

    calls: list[int] = []
    original_flush = AsyncSession.flush

    async def flush(self, *args, **kwargs):
        if any(isinstance(obj, Transaction) and obj.state == "NEW" for obj in self.sync_session.new):
            calls.append(1)
            raise error_factory()
        return await original_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", flush)
    return calls


def _track_the_attempt_end_and_the_record(monkeypatch, service, *, end_fails=False, record_fails=None):
    """Since 019 stage 3 a failed payment is terminalized in two steps: the attempt's transaction is
    ENDED (`_end_failed_attempt`: committed when nothing of the payment can be in it, else rolled
    back), and only then is the refusal RECORDED `ABORTED` in a short transaction of its own
    (`record_definitive_refusal`). Before stage 3 the same two steps were `session.rollback()` and
    `engine.abort(commit=True)`. This records their order and the refusal recorded."""

    order: list[str] = []
    recorded: list[tuple] = []
    original_end = service._end_failed_attempt
    original_record = payment_service_module.record_definitive_refusal

    async def end():
        order.append("end_attempt")
        if end_fails:
            async def fail():
                raise RuntimeError("attempt-end-secret")

            monkeypatch.setattr(service.session, "commit", fail)
            monkeypatch.setattr(service.session, "rollback", fail)
        return await original_end()

    async def record(sessions, refusal, **kwargs):
        order.append("record")
        recorded.append((refusal.error["message"], refusal.error["code"], refusal.error["details"]))
        if record_fails is not None:
            raise RuntimeError(record_fails)
        return await original_record(sessions, refusal, **kwargs)

    monkeypatch.setattr(service, "_end_failed_attempt", end)
    monkeypatch.setattr(payment_service_module, "record_definitive_refusal", record)
    return order, recorded


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
    # 019 `T1905` (FORK-4): an exhausted retryable conflict is a conflict, never a definitive refusal -
    # nothing is recorded, so the client's resubmission of the same tx_id executes. Until `T1905` this
    # pinned a stored `ABORTED` with the retryable error.
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    assert transaction is None, (transaction.state, transaction.error)


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

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    inserts = _fail_the_payment_insert(monkeypatch, _serialization_failure)

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

    # Retried as a whole attempt until the budget was spent (019 stage 3), never recorded.
    assert len(inserts) == int(settings.COMMIT_RETRY_ATTEMPTS), inserts
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    assert transaction is None


@MODE_B
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

    monkeypatch.setattr(PaymentRouter, "build_graph", build_graph)
    monkeypatch.setattr(PaymentRouter, "find_flow_routes", find_flow_routes)
    inserts = _fail_the_payment_insert(monkeypatch, _serialization_failure)

    response = await client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json=_signed_payment_body(sender, receiver, tx_id=tx_id),
    )

    assert inserts, "premise: the payment's insert was never reached"
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


@MODE_B
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


@MODE_B
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


@MODE_B
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
    """The attempt's transaction is ended BEFORE the refusal is recorded (019 stage 3 form)."""

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

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    order, recorded = _track_the_attempt_end_and_the_record(monkeypatch, service)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert order == ["end_attempt", "record"]
    assert recorded == [("Internal server error", "E010", {})]
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
    """An attempt whose transaction cannot be ended records nothing: safe 500 (019 stage 3 form)."""

    service, sender, request, _ = await _build_direct_payment(
        db_session,
        suffix="rollback_failure",
    )

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def fail_prepare(*args, **kwargs):
        raise RoutingException("original client error")

    original_rollback = db_session.rollback
    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    order, recorded = _track_the_attempt_end_and_the_record(monkeypatch, service, end_fails=True)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "attempt-end-secret" not in str(raised.value)
    assert order == ["end_attempt"]
    assert recorded == []
    monkeypatch.undo()
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

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def fail_prepare(*args, **kwargs):
        raise RoutingException("original client error")

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", fail_prepare)
    order, recorded = _track_the_attempt_end_and_the_record(
        monkeypatch, service, record_fails="abort-secret"
    )

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "original client error" not in str(raised.value)
    assert "abort-secret" not in str(raised.value)
    assert order == ["end_attempt", "record"]
    assert recorded == [("original client error", "E001", {})]


@MODE_B
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

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def prepare(*args, **kwargs) -> None:
        return None

    async def fail_commit(*args, **kwargs) -> None:
        raise original_error

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", prepare)
    monkeypatch.setattr(service.engine, "commit", fail_commit)
    order, recorded = _track_the_attempt_end_and_the_record(monkeypatch, service)

    with pytest.raises(ConflictException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value is original_error
    assert order == ["end_attempt", "record"]
    assert recorded == [("typed commit conflict", "E008", {"state": "PREPARED"})]
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
    """`rollback` - the attempt's transaction cannot be ended; `abort` - the refusal cannot be
    recorded (the two cleanup steps since 019 stage 3). Either way a safe 500, in order."""

    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix=f"commit_{error_kind}_{cleanup_failure}",
    )
    commit_error = error_factory()

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def prepare(*args, **kwargs) -> None:
        return None

    async def fail_commit(*args, **kwargs) -> None:
        raise commit_error

    original_rollback = db_session.rollback
    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", prepare)
    monkeypatch.setattr(service.engine, "commit", fail_commit)
    order, recorded = _track_the_attempt_end_and_the_record(
        monkeypatch,
        service,
        end_fails=cleanup_failure == "rollback",
        record_fails="commit-abort-secret" if cleanup_failure == "abort" else None,
    )

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value is not commit_error
    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "secret" not in str(raised.value)
    if cleanup_failure == "rollback":
        assert order == ["end_attempt"]
        assert recorded == []
        monkeypatch.undo()
        await original_rollback()
    else:
        assert order == ["end_attempt", "record"]
        expected = (
            ("typed commit conflict", "E008", {"state": "PREPARED"})
            if error_kind == "typed"
            else ("Internal server error", "E010", {})
        )
        assert recorded == [expected]


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
        # Since 019 stage 3 the insert is a flush inside the payment's transaction: cancel right
        # after it.
        original_flush = AsyncSession.flush
        cancelled: list[int] = []

        async def flush_then_cancel_once(self, *args, **kwargs):
            inserting = any(
                isinstance(obj, Transaction) and obj.state == "NEW" for obj in self.sync_session.new
            )
            await original_flush(self, *args, **kwargs)
            if inserting and not cancelled:
                cancelled.append(1)
                raise asyncio.CancelledError

        monkeypatch.setattr(AsyncSession, "flush", flush_then_cancel_once)
    else:
        async def prepare(*args, **kwargs) -> None:
            return None

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
    """Staged: the refusal is written into the caller's transaction (since 019 stage 3 after the
    payment operation's rollback, by `_record_refusal_in_transaction`), and the caller's own
    savepoint then takes it back."""

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
    original_record = service._record_refusal_in_transaction

    async def tracked_record(attempt):
        result = await original_record(attempt)
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
    monkeypatch.setattr(service, "_record_refusal_in_transaction", tracked_record)

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
    """A cancellation that arrives WHILE the refusal is being recorded does not stop the recording.

    Since 019 stage 3 there is no read before terminalizing a payment whose own transaction never
    committed; the cleanup step a second cancellation can hit is the recording itself.
    """

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

    record_started = asyncio.Event()
    release_record = asyncio.Event()
    record_calls = 0
    original_record = payment_service_module.record_definitive_refusal

    async def blocking_record(sessions, refusal, **kwargs):
        nonlocal record_calls
        record_calls += 1
        record_started.set()
        await release_record.wait()
        return await original_record(sessions, refusal, **kwargs)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", interrupted_prepare)
    monkeypatch.setattr(payment_service_module, "record_definitive_refusal", blocking_record)

    if interruption == "timeout":
        monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)

    owner = asyncio.create_task(service.create_payment(sender.id, request))
    await asyncio.wait_for(prepare_started.wait(), timeout=1)
    if interruption == "cancellation":
        owner.cancel("initial payment cancellation")

    await asyncio.wait_for(record_started.wait(), timeout=1)
    owner.cancel("cancellation during the recording")
    await asyncio.sleep(0)
    release_record.set()

    with pytest.raises(asyncio.CancelledError):
        await owner

    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()
    assert transaction.state == "ABORTED"
    assert record_calls == 1


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

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def slow_prepare(*args, **kwargs) -> None:
        await asyncio.sleep(0.05)

    original_rollback = db_session.rollback
    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    order, recorded = _track_the_attempt_end_and_the_record(monkeypatch, service, end_fails=True)

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert "attempt-end-secret" not in str(raised.value)
    assert order == ["end_attempt"]
    assert recorded == []
    monkeypatch.undo()
    await original_rollback()


@pytest.mark.asyncio
async def test_timeout_recovery_read_failure_is_safe_without_abort(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    """The payment's ONE commit outlives the deadline, and the read that must precede any
    terminalization fails: safe 500, nothing recorded. Since 019 stage 3 this read belongs to a
    failed commit only - a timeout before the commit has nothing durable to look for."""

    service, sender, request, _ = await _build_direct_payment(
        db_session,
        suffix="timeout_read_failure",
    )
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 1, raising=False)
    raw_sentinel = "timeout-read-secret"

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def instant(*args, **kwargs) -> None:
        return None

    cleanup_started = False
    order: list[str] = []
    original_rollback = db_session.rollback
    original_execute = db_session.execute

    async def hanging_commit() -> None:
        order.append("commit")
        await asyncio.Event().wait()

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

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", instant)
    monkeypatch.setattr(service.engine, "commit", instant)
    monkeypatch.setattr(db_session, "commit", hanging_commit)
    monkeypatch.setattr(db_session, "execute", fail_recovery_read)
    monkeypatch.setattr(db_session, "rollback", tracked_rollback)
    _order, recorded = _track_the_attempt_end_and_the_record(monkeypatch, service)
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert raw_sentinel not in str(raised.value)
    assert order[:3] == ["commit", "rollback", "execute"], order
    assert recorded == []
    read_log = next(
        record
        for record in caplog.records
        if "event=payment.timeout_recovery_read_failed" in record.getMessage()
    )
    assert "error_type=OperationalError" in read_log.getMessage()
    assert raw_sentinel not in read_log.getMessage()
    assert read_log.exc_info is None
    monkeypatch.undo()
    await original_rollback()


@pytest.mark.asyncio
async def test_an_unresolved_commit_with_no_row_found_is_not_terminalized(
    db_session,
    monkeypatch,
) -> None:
    """019 `T1905` (the seven-row table, "Неразрешённый коммит"): the payment's ONE commit outlives the
    deadline, so its outcome is unknown; the read that follows finds no row. No row does NOT prove the
    rollback - a commit in flight may still land - so nothing is recorded and nothing is retried: the
    caller gets the timeout, and a resubmission of the same tx_id will read whichever outcome it was.
    Until `T1905` this recorded `ABORTED/E007` after the empty read."""

    service, sender, request, tx_id = await _build_direct_payment(
        db_session,
        suffix="unresolved_commit",
    )
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 1, raising=False)

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def instant(*args, **kwargs) -> None:
        return None

    commits: list[str] = []
    original_commit = db_session.commit

    async def hanging_commit() -> None:
        # Only the payment's own COMMIT hangs; anything committed after it (a recording) goes through.
        commits.append("commit")
        if len(commits) == 1:
            await asyncio.Event().wait()
        return await original_commit()

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", instant)
    monkeypatch.setattr(service.engine, "commit", instant)
    monkeypatch.setattr(db_session, "commit", hanging_commit)
    _order, recorded = _track_the_attempt_end_and_the_record(monkeypatch, service)

    with pytest.raises(TimeoutException) as raised:
        await service.create_payment(sender.id, request)

    assert commits[:1] == ["commit"], commits  # the one commit was attempted, and its outcome is unknown
    assert raised.value.code == "E007" and raised.value.status_code == 504
    assert recorded == []
    monkeypatch.undo()
    transaction = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    assert transaction is None


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

    async def build_graph(equivalent_code: str, **kwargs) -> None:
        return None

    def find_flow_routes(from_pid: str, to_pid: str, payment_amount: Decimal, **kwargs):
        return [([from_pid, to_pid], payment_amount)]

    async def slow_prepare(*args, **kwargs) -> None:
        await asyncio.sleep(0.05)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    order, recorded = _track_the_attempt_end_and_the_record(
        monkeypatch, service, record_fails=raw_sentinel
    )
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    with pytest.raises(GeoException) as raised:
        await service.create_payment(sender.id, request)

    assert raised.value.code == "E010"
    assert raised.value.status_code == 500
    assert raised.value.message == "Internal server error"
    assert raised.value.details == {}
    assert raw_sentinel not in str(raised.value)
    assert order == ["end_attempt", "record"]
    assert recorded == [("Payment timeout", "E007", {})]
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

    # The staged refusal write (since 019 stage 3 an `ABORTED` insert after the operation's
    # rollback) fails; every other statement runs. Since `T1905` a refusal that cannot be written into
    # the caller's transaction is handed to the transaction's owner as `PaymentTransactionUnusable`
    # (it records the refusal on its own transaction after rolling back, `T1912`) - no longer an
    # ordinary timeout the caller would count and carry on from.
    original_execute = AsyncSession.execute

    async def fail_the_refusal_write(self, statement, *args, **kwargs):
        if getattr(statement, "is_insert", False) and getattr(getattr(statement, "table", None), "name", None) == "transactions":
            raise RuntimeError(raw_sentinel)
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(service.router, "build_graph", build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", slow_prepare)
    monkeypatch.setattr(AsyncSession, "execute", fail_the_refusal_write)
    caplog.set_level(logging.ERROR, logger="app.core.payments.service")

    with pytest.raises(PaymentTransactionUnusable) as raised:
        async with db_session.begin_nested():
            await service.create_payment_internal_staged(
                sender.id,
                to_pid=request.to,
                equivalent=request.equivalent,
                amount=request.amount,
                idempotency_key=tx_id,
            )

    assert raised.value.refusal is not None and raised.value.refusal.error["code"] == "E007"
    assert raw_sentinel not in str(raised.value)
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

    # Since 019 `T1905` a definitive refusal after admission is RETURNED as a structured `ABORTED`
    # result through the caller's savepoint (spec, "Путь записи окончательного отказа"), carrying the
    # public error the executor classifies it by; before, the same error was raised.
    staged = await service.create_payment_internal_staged(
        sender.id,
        to_pid=request.to,
        equivalent=request.equivalent,
        amount=request.amount,
        idempotency_key=tx_id,
    )

    assert staged.post_commit_effects is None
    assert staged.result.status == "ABORTED"
    assert staged.result.error is not None and staged.result.error.code == "E010"
    assert staged.refusal is not None
    assert staged.refusal.code == "E010"
    assert staged.refusal.status_code == 500
    assert staged.refusal.message == "Internal server error"
    assert staged.refusal.details == {}
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
