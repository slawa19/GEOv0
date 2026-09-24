"""019 `T1903` (review of stage 2, P2): one `PaymentService` has ONE advisory-lock deadline.

BEFORE STAGE 2, `PaymentService.acquire_staged_equivalent_owner_locks` called the service's own engine,
so the owner-lock budget started at the first staged acquisition and was shared by every later staged
acquisition on the same service and by the engine's later non-savepoint unit of work, which takes
`min(previous deadline, fresh budget)` (`PaymentEngine._run_uow_with_retry`). The first stage-2 cut built a
fresh `MoneyBoundary` per call and silently gave each call a full new budget. This pins the shared deadline.

The clock is frozen and advanced by hand (both modules read `time.monotonic`), so the deadline is an exact
value, not a timing.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core import money_boundary as money_boundary_module
from app.core.payments import engine as engine_module
from app.core.payments.service import PaymentService


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    """Answers the equivalent lookup and records every lock-timeout statement."""

    def __init__(self, equivalent_id: uuid.UUID, code: str) -> None:
        self.rows = [(equivalent_id, code)]
        self.timeouts: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "lock_timeout" in sql:
            self.timeouts.append(sql)
        return _Rows(self.rows if sql.lstrip().upper().startswith("SELECT EQUIVALENTS") else [])

    async def scalar(self, stmt, params=None):
        return "0"


@pytest.mark.asyncio
async def test_staged_acquisitions_and_the_engine_share_one_deadline(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 5)  # budget = min(10, 5) = 5 s
    clock = SimpleNamespace(now=100.0)
    frozen = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(money_boundary_module, "time", frozen)
    monkeypatch.setattr(engine_module, "time", frozen)

    session = _Session(uuid.uuid4(), "T1903")
    service = PaymentService(session)

    await service.acquire_staged_equivalent_owner_locks(["T1903"])
    assert service.engine._advisory_lock_deadline == 105.0, (
        "the staged acquisition did not start the service engine's deadline: it ran on a boundary of "
        "its own, so later locks of this service get a fresh budget"
    )
    clock.now = 101.0
    await service.acquire_staged_equivalent_owner_locks(["T1903"])
    assert service.engine._advisory_lock_deadline == 105.0
    lock_timeouts = [sql for sql in session.timeouts if "SET LOCAL" in sql]
    assert lock_timeouts == [
        "SET LOCAL lock_timeout = '5000ms'",
        "SET LOCAL lock_timeout = '4000ms'",
    ], lock_timeouts

    clock.now = 102.0
    seen: list[float | None] = []

    async def _unit_of_work():
        seen.append(service.engine._advisory_lock_deadline)

    await service.engine._run_uow_with_retry(op="t1903", fn=_unit_of_work)
    assert seen == [105.0], (
        f"the engine's later unit of work saw deadline {seen}, not the staged one (105.0): "
        "min(previous, fresh) had no previous"
    )
