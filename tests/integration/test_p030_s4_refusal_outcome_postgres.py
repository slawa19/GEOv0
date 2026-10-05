"""030 S4 (`T3004`, `F-030-11`): a refusal answers the stored outcome, or says the outcome is not established.

Owner rule 028 В-6: a repeated payment after a refusal returns the SAME stored result. The two R2-2 schedules
(`specs/030-zero-sum-protection/evidence-2026-10-05/final-r2b.md`) on `PaymentService.pay` (the no-Redis path), with
barriers only - nothing injected into the driver. Each asserts its mechanism (`pg_blocking_pids`, the order of the
rows) before the outcome: (1) A times out on a held line and pauses before recording; B, the same request, refuses
`participant_suspended` (the transit frozen mid-flight) and records it; A must answer B's stored row, not its own
timeout. (2) B holds its successful row uncommitted; A's refusal insert queues on it until the bounded wait gives up;
A must answer "not established" (the retryable 409), not its original timeout.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid

import pytest
from sqlalchemy import select, text

import app.core.payments.service as payment_service
from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import RetryablePaymentConflictException, TimeoutException
from tests.integration.p019_stand import finish, tx_row
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import stand  # noqa: F401
from tests.integration.test_p028_e3_freeze_boundary_postgres import _admin_status, _world
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

_ROLE: contextvars.ContextVar[str | None] = contextvars.ContextVar("p030_s4_role", default=None)
_BUDGETS = ("PREPARE_TIMEOUT_SECONDS", "COMMIT_TIMEOUT_SECONDS", "PAYMENT_TOTAL_TIMEOUT_SECONDS")


def _budget(monkeypatch, seconds: int) -> None:
    """Read by `pay()` (the deadline) and `execute()` (its phases) when they START - a running request keeps its own."""
    for name in _BUDGETS:
        monkeypatch.setattr(settings, name, seconds, raising=False)


async def _pay(stand, payer, request: PaymentCreateRequest, role: str) -> object:  # noqa: F811
    _ROLE.set(role)
    try:
        return await PaymentService.pay(stand, payer.id, request, require_signature=False)
    except Exception as exc:  # noqa: BLE001 - the outcome under test
        return exc


async def _waiters(stand, blocker_pid: int, *, query_prefix: str | None = None, timeout: float = 10.0) -> bool:  # noqa: F811
    """Some backend of this database waits on a lock `blocker_pid` holds (and runs `query_prefix...`)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with stand() as observer:
        while True:
            rows = (await observer.execute(text(
                "SELECT a.query FROM pg_stat_activity a WHERE a.datname = current_database() "
                "AND a.wait_event_type = 'Lock' AND :b = ANY(pg_blocking_pids(a.pid))"), {"b": blocker_pid})).all()
            await observer.rollback()
            if any(query_prefix is None or str(q).lstrip().upper().startswith(query_prefix) for (q,) in rows):
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)


def _reason(outcome: object) -> tuple:
    """(status, code, reason) of an answer: a stored result, or the error it was refused with."""
    if isinstance(outcome, Exception):
        return ("raised", type(outcome).__name__, str(getattr(outcome, "code", "")),
                (getattr(outcome, "details", None) or {}).get("reason"))
    error = getattr(outcome, "error", None)
    return (outcome.status, str(getattr(error, "code", None)), ((error and error.details) or {}).get("reason"))


@pytest.mark.asyncio
async def test_a_refusal_that_loses_to_an_aborted_winner_answers_the_stored_outcome(stand, monkeypatch) -> None:  # noqa: F811
    eq, p, _ = await _world(stand, lines=[("B", "A"), ("C", "B")])  # A pays C through the transit B
    request = PaymentCreateRequest(tx_id=str(uuid.uuid4()), to=p["C"].pid, equivalent=eq.code, amount="1.00",
                                   signature="__internal__")
    a_at_recorder, a_resume, b_at_participants, b_resume = (asyncio.Event() for _ in range(4))
    order: list[str] = []

    real_record = payment_service.record_definitive_refusal

    async def recorder(sessions, refusal, **kw):
        if _ROLE.get() == "A":
            a_at_recorder.set()
            await a_resume.wait()
        order.append(f"record:{_ROLE.get()}")
        return await real_record(sessions, refusal, **kw)

    real_refuse = MoneyBoundary.refuse_suspended_participants

    async def participants(self, ids, **kw):
        if _ROLE.get() == "B":
            b_at_participants.set()
            await b_resume.wait()
        return await real_refuse(self, ids, **kw)

    monkeypatch.setattr(payment_service, "record_definitive_refusal", recorder)
    monkeypatch.setattr(MoneyBoundary, "refuse_suspended_participants", participants)

    a = b = None
    async with stand() as holder:  # every line of the equivalent, FOR UPDATE: A's binding waits here
        await holder.execute(select(TrustLine.id).where(TrustLine.equivalent_id == eq.id).with_for_update())
        holder_pid = int(await holder.scalar(text("SELECT pg_backend_pid()")))
        try:
            _budget(monkeypatch, 1)
            PaymentRouter.invalidate_cache(eq.code)
            a = asyncio.create_task(_pay(stand, p["A"], request, "A"))
            a_waited_for_the_line = await _waiters(stand, holder_pid)
            await asyncio.wait_for(a_at_recorder.wait(), timeout=10)  # A timed out and rolled back
        finally:
            await holder.rollback()
        try:
            _budget(monkeypatch, 30)
            b = asyncio.create_task(_pay(stand, p["A"], request, "B"))
            await asyncio.wait_for(b_at_participants.wait(), timeout=10)  # B routed through B, no lock yet
            async with stand() as admin:
                await _admin_status(admin, p["B"].pid)  # the freeze commits while B is paused
            b_resume.set()
            b_outcome = await asyncio.wait_for(b, timeout=30)
            stored_before_a = await tx_row(stand, request.tx_id)
            a_resume.set()
            a_outcome = await asyncio.wait_for(a, timeout=30)
        finally:
            a_resume.set()
            b_resume.set()
            await finish(a)
            await finish(b)
    replay = await _pay(stand, p["A"], request, "replay")

    # The mechanism, before the outcome.
    assert a_waited_for_the_line, "premise: A never queued on the held line"
    assert order[:2] == ["record:B", "record:A"], order
    assert _reason(b_outcome)[-1] == "participant_suspended", b_outcome
    assert stored_before_a is not None and stored_before_a[0] == "ABORTED", stored_before_a
    assert (stored_before_a[1] or {}).get("details", {}).get("reason") == "participant_suspended", stored_before_a
    assert _reason(replay) == ("ABORTED", "E008", "participant_suspended"), replay
    # The outcome: A answers what the database keeps - not its own timeout.
    assert _reason(a_outcome) == _reason(replay), (a_outcome, replay)


@pytest.mark.asyncio
async def test_a_refusal_whose_recording_times_out_answers_outcome_not_established(stand, monkeypatch) -> None:  # noqa: F811
    eq, p, _ = await _world(stand, lines=[("B", "A"), ("C", "B")])
    request = PaymentCreateRequest(tx_id=str(uuid.uuid4()), to=p["C"].pid, equivalent=eq.code, amount="1.00",
                                   signature="__internal__")
    b_written, b_resume = asyncio.Event(), asyncio.Event()
    b_pid: list[int] = []

    real_execute = PaymentService.execute

    async def execute(self, *args, **kw):
        staged = await real_execute(self, *args, **kw)
        if _ROLE.get() == "B":  # B's row and debts are written, its COMMIT is next
            b_pid.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
            b_written.set()
            await b_resume.wait()
        return staged

    monkeypatch.setattr(PaymentService, "execute", execute)

    a = b = None
    try:
        _budget(monkeypatch, 30)
        PaymentRouter.invalidate_cache(eq.code)
        b = asyncio.create_task(_pay(stand, p["A"], request, "B"))
        await asyncio.wait_for(b_written.wait(), timeout=30)
        _budget(monkeypatch, 1)
        a = asyncio.create_task(_pay(stand, p["A"], request, "A"))
        a_queued_on_the_row = await _waiters(stand, b_pid[0], query_prefix="INSERT INTO TRANSACTIONS")
        a_outcome = await asyncio.wait_for(a, timeout=30)
        ended_before_b = not b.done()
        stored_while_b_open = await tx_row(stand, request.tx_id)
        b_resume.set()
        b_outcome = await asyncio.wait_for(b, timeout=30)
    finally:
        b_resume.set()
        await finish(a)
        await finish(b)
    _budget(monkeypatch, 30)
    replay = await _pay(stand, p["A"], request, "replay")

    # The mechanism, before the outcome.
    assert a_queued_on_the_row, "premise: A's refusal insert never queued on B's uncommitted row"
    assert ended_before_b and stored_while_b_open is None, (ended_before_b, stored_while_b_open)
    assert _reason(b_outcome)[0] == "COMMITTED" and _reason(replay)[0] == "COMMITTED", (b_outcome, replay)
    # The outcome: not A's original timeout refusal, but "not established - send the same request again".
    assert not isinstance(a_outcome, TimeoutException), a_outcome
    assert isinstance(a_outcome, RetryablePaymentConflictException), a_outcome
    assert a_outcome.details.get("retryable") is True, a_outcome.details
