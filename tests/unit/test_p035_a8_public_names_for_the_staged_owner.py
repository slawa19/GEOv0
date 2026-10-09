"""035 A8 (a): `public_error_of_stored` and `drain_call` are public names of `app/core/payments/service.py`.

WHY. The simulator's money-phase owner (`app/core/simulator/money_replay.py`) imports the private
`_public_error_of_stored` and `_drain_call`. 035 gives them public names with the same behaviour; moving the
simulator's imports is 034 `T3431` and is not done here, so the private names must stay importable and be the same
objects.

RED ON THE BASE COMMIT (`7e21abc1`) as "the public name does not exist": `ImportError` at collection.

WHAT IS PINNED, with literal expectations and not by comparing a name with its own alias (which would hold for any
body): the exception class, status, code, message and details a stored refusal is classified as, for every branch
of the mapping; None for anything that is not a stored refusal; and the three terminal results of `drain_call` -
a value, the operation's error, and the caller's cancellation with the operation run to its end.

NOT SEEN: whether the simulator uses the public names (034 `T3431`); the behaviour of the staged owner itself
(`tests/integration/test_p030_s4_staged_refusal_publishes_the_winner_postgres.py`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.core.payments import service
from app.core.payments.service import drain_call, public_error_of_stored
from app.schemas.payment import PaymentError, PaymentResult
from app.utils.exceptions import (
    BadRequestException,
    ConflictException,
    GeoException,
    InvalidSignatureException,
    RoutingException,
    TimeoutException,
)


def _stored(status: str, error: PaymentError | None) -> PaymentResult:
    return PaymentResult(
        tx_id="00000000-0000-4000-8000-000000000001",
        status=status,
        **{"from": "payer"},
        to="payee",
        equivalent="USD",
        amount="1.00",
        routes=None,
        error=error,
        created_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        committed_at=None,
    )


def test_the_private_names_are_the_same_objects() -> None:
    assert service._public_error_of_stored is public_error_of_stored
    assert service._drain_call is drain_call


@pytest.mark.parametrize(
    "code, expected_class, expected_status, expected_code",
    [
        ("E007", TimeoutException, 504, "E007"),
        ("E001", RoutingException, 400, "E001"),
        ("E002", RoutingException, 400, "E002"),
        ("E008", ConflictException, 409, "E008"),
        ("E009", BadRequestException, 400, "E009"),
        ("E005", InvalidSignatureException, 400, "E005"),
        ("E010", GeoException, 500, "E010"),
        ("E003", GeoException, 400, "E003"),
    ],
)
def test_a_stored_refusal_is_the_exception_it_was_refused_with(
    code: str, expected_class: type, expected_status: int, expected_code: str
) -> None:
    details = {"reason": "other", "kept": [1, 2]}
    error = public_error_of_stored(_stored("ABORTED", PaymentError(code=code, message="as stored", details=details)))

    assert type(error) is expected_class, (code, type(error))
    assert error.status_code == expected_status, (code, error.status_code)
    assert getattr(error.code, "value", error.code) == expected_code, (code, error.code)
    assert error.message == "as stored" and error.details == details, (error.message, error.details)
    assert error.details is not details, "the stored details must be copied, not shared"


def test_what_is_not_a_stored_refusal_has_no_error() -> None:
    refused = PaymentError(code="E002", message="m", details={})
    assert public_error_of_stored(_stored("COMMITTED", None)) is None
    assert public_error_of_stored(_stored("COMMITTED", refused)) is None
    assert public_error_of_stored(_stored("ABORTED", None)) is None


@pytest.mark.asyncio
async def test_drain_call_returns_the_value_or_the_error() -> None:
    async def gives() -> str:
        return "value"

    boom = RuntimeError("the operation failed")

    async def fails() -> None:
        raise boom

    assert await drain_call(gives) == ("value", None)
    assert await drain_call(fails) == (None, boom)


@pytest.mark.asyncio
async def test_drain_call_finishes_the_operation_under_the_callers_cancellation() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    finished: list[str] = []

    async def operation() -> str:
        started.set()
        await release.wait()
        finished.append("done")
        return "value"

    owner = asyncio.create_task(drain_call(operation))
    await started.wait()
    owner.cancel()
    await asyncio.sleep(0)  # the cancellation is delivered while the operation is still waiting
    assert not owner.done(), "the caller's cancellation interrupted the operation"
    release.set()

    value, error = await owner
    assert finished == ["done"] and value == "value", (finished, value)
    assert isinstance(error, asyncio.CancelledError), error
