"""035 A10 (F-035-8, second half): a payment that cannot get a pool connection is refused BY TIME, within its deadline.

WHAT WAS WRONG (read from the code and measured, 2026-10-09). The attempt's first checkout happened inside
`execute()`'s first statement, BEFORE the payment's deadline was entered. With the pool exhausted the payment waited
for the pool's own timeout (30 s against a 10 s budget; measured 30.3 s) and then left as a raw
`sqlalchemy.exc.TimeoutError`, answered 500/E010. The same raw error left from the two later checkouts - the route
reader's, and the attempt session's re-acquisition after routing - whenever the pool's timeout ran out first.

WHAT IS REQUIRED NOW.
* Whichever runs out first - the payment's deadline or the pool's timeout - the answer is the declared refusal by
  time, 504/E007, at each of the three checkouts.
* A request that was NEVER ADMITTED leaves nothing: no `transactions` row, no debt, the pool back to idle - and the
  same `tx_id` sent again is executed afresh, not answered from storage.
* A request ADMITTED BY AN EARLIER ATTEMPT (it reached the payment operation, met a retryable conflict, and the
  next attempt got no connection) is a refusal after admission: recorded `ABORTED`, replayed as stored (spec 019,
  "Окончательный отказ"). A concurrent `COMMITTED` row of the same request WINS and is not overwritten. When the
  recording itself cannot get a connection, the answer is the retryable 409/E008 and no row claims a refusal.
* A cancellation while waiting for the pool leaves nothing checked out.
* A service that runs several payments does not settle a failed checkout against the previous payment's state.
* A request the payment refuses WITHOUT ANY SQL (an amount the ledger cannot hold, a malformed equivalent code or
  `tx_id`) takes no connection for the payment and gets its own 400 - also when the pool is exhausted (§15 review
  of `c27a2fbe`, Н2: the first edition of A10 took the connection first, and answered such a request 504).
* AFTER ADMISSION ANY NON-RETRYABLE FAILURE OF THE CHECKOUT IS FINAL, not only a timeout (same review, Н1 - declared
  and pinned here, not changed): a one-off infrastructure failure of the next attempt's checkout is recorded
  `ABORTED/E010`, and the signed identity is spent - the same request again is answered `ABORTED`. Without an
  earlier admission the same failure leaves no row and the request may be sent again.

THE STAND IS THE PRODUCT'S WIRING ON A REAL POOL, as in the A9 test: an engine built by `_create_engine()` on the
mode-B clone (5 + 10 connections), the real `get_db` and payment session factory. The pool is EXHAUSTED by the test
holding real connections of that engine, at an exact point chosen by a hook on an existing method - before an
attempt, before or after the route graph is built.

WHICH CASES WAIT FOR A REAL TIMER, AND WHERE THEY RUN (§15 review of `c27a2fbe`; AGENTS §11). Nothing here sleeps,
and every wait of a test is for an answer or an event under the test's own `_BUDGET_SECONDS` (how long a BROKEN run
may take to say so). But the cases differ in what makes the refusal happen:

* NO TIMER (the ordinary tier). "The deadline comes first" hands the attempt a deadline that has ALREADY PASSED
  while the pool's timeout is 900 s, so the refusal is immediate; a cancellation is delivered on an event; a
  checkout failure that is not a timeout is injected. One case of each new behaviour is of this kind.
* A REAL TIMER MUST RUN OUT (`@pytest.mark.slow`; run with `scripts/verify_local.ps1 -IncludeExpensive`,
  PostgreSQL as for the whole tier, no other prerequisite). "The pool comes first" waits for the pool's own
  timeout, set to 1 s while every payment timer is 900 s - the attempt's checkout, the route reader's, the
  re-acquisition after routing; and a refusal that cannot be recorded waits out the recording's budget (the 500 ms
  grace) or the pool's 1 s. These five cannot be had without a timer: the pool's timeout is the subject.

Resources are registered with one `AsyncExitStack` and released on every exit: request tasks cancelled and awaited
first, then the held connections, the observer and the engine. A request that does not answer within the budget is
NOT cancelled-and-awaited by the guard (its recording runs shielded and would hold the test until the session
timeout): the guard frees the held connections, so the request can finish, and then fails.

NOT SEEN: a statement that hangs on a connection already taken (nothing here bounds it; recorded in the backlog);
Redis; several workers; the simulator's staged owner, whose recording of a refusal is not changed by A10.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import uuid

import asyncpg
import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.payments.service as payment_service
import app.db.session as product_db
from app.api.deps import get_db, get_payment_session_factory
from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.main import app
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import RetryablePaymentConflictException, TimeoutException
from tests.conftest import MODE_B
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

#: How long a BROKEN run may wait before it says so.
_BUDGET_SECONDS = 60.0
_FAR = 900  # seconds: a timer that must not fire in a test
_POOL = 15

_PAYMENT_TIMERS = {
    "PREPARE_TIMEOUT_SECONDS": _FAR,
    "PAYMENT_TOTAL_TIMEOUT_SECONDS": _FAR,
    "COMMIT_TIMEOUT_SECONDS": _FAR,
    "ROUTING_PATH_FINDING_TIMEOUT_MS": _FAR * 1000,
    "COMMIT_RETRY_ATTEMPTS": 3,
}


class _Stand:
    """One payer, one payee, one line of 100, on the product's wiring over a real pool of fifteen."""

    def __init__(self, client: AsyncClient, monkeypatch, stack: contextlib.AsyncExitStack) -> None:
        self.client, self.monkeypatch, self.stack = client, monkeypatch, stack
        self.launched: list[asyncio.Task] = []
        self.held: list = []
        self.code = f"PZ{uuid.uuid4().hex[:8].upper()}"  # its own equivalent: no route graph is cached for it

    async def open(self, db_session, *, pool_timeout: int) -> "_Stand":
        db_session.add(Equivalent(code=self.code, description=self.code, precision=2))
        await db_session.commit()
        self.payer = await register_and_login(self.client, "A10 Payer")
        self.payee = await register_and_login(self.client, "A10 Payee")
        line = await self.client.post(
            "/api/v1/trustlines",
            json={
                "to": self.payer["pid"],
                "equivalent": self.code,
                "limit": "100.00",
                "signature": _sign_trustline_create_request(
                    signing_key=SigningKey(base64.b64decode(self.payee["priv"])),
                    to_pid=self.payer["pid"],
                    equivalent=self.code,
                    limit="100.00",
                ),
            },
            headers=self.payee["headers"],
        )
        assert line.status_code == 201, line.text
        await db_session.commit()

        for name, value in _PAYMENT_TIMERS.items():
            self.monkeypatch.setattr(settings, name, value)
        self.monkeypatch.setattr(settings, "DB_POOL_TIMEOUT_SECONDS", pool_timeout)
        clone = db_session.info["geo_committed_database"].sessionmaker.kw["bind"].url
        self.monkeypatch.setattr(settings, "DATABASE_URL", clone.render_as_string(hide_password=False))
        self.engine = product_db._create_engine()
        self.stack.push_async_callback(self.engine.dispose)
        self.pool = self.engine.sync_engine.pool
        assert self.pool.size() + settings.DB_MAX_OVERFLOW == _POOL, "the stand is written for the settings' pool"
        self.checkouts = 0  # every connection handed out by the pool, to whoever asked (dies with the engine)

        def count_checkout(_connection, _record, _proxy) -> None:
            self.checkouts += 1

        event.listen(self.engine.sync_engine, "checkout", count_checkout)
        self.monkeypatch.setattr(product_db, "engine", self.engine)
        self.sessions = async_sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
        self.monkeypatch.setattr(product_db, "AsyncSessionLocal", self.sessions)
        self.monkeypatch.delitem(app.dependency_overrides, get_db)
        self.monkeypatch.delitem(app.dependency_overrides, get_payment_session_factory)

        self.observer = await asyncpg.connect(
            host=clone.host, port=clone.port or 5432, user=clone.username, password=clone.password,
            database=clone.database,
        )
        self.stack.push_async_callback(self.observer.close)
        self.stack.push_async_callback(self.release)
        self.stack.push_async_callback(self._drain_requests)  # registered last: runs first
        return self

    async def _drain_requests(self) -> None:
        for task in self.launched:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.launched, return_exceptions=True)

    async def exhaust(self) -> None:
        """Hold every connection the pool still has. Real connections of the product engine."""

        while self.pool.checkedout() < _POOL:
            self.held.append(await self.engine.connect())

    async def release(self) -> None:
        held, self.held = self.held, []
        for connection in held:
            await connection.close()

    def body(self, amount: str = "10.00") -> dict:
        tx_id = str(uuid.uuid4())
        return {
            "tx_id": tx_id,
            "to": self.payee["pid"],
            "equivalent": self.code,
            "amount": amount,
            "signature": _sign_payment_request(
                signing_key=SigningKey(base64.b64decode(self.payer["priv"])),
                tx_id=tx_id,
                from_pid=self.payer["pid"],
                to_pid=self.payee["pid"],
                equivalent=self.code,
                amount=amount,
            ),
        }

    def send(self, body: dict) -> asyncio.Task:
        task = asyncio.create_task(self.client.post("/api/v1/payments", json=body, headers=self.payer["headers"]))
        self.launched.append(task)
        return task

    async def answer(self, body: dict) -> tuple[int, dict]:
        request = self.send(body)
        # `wait`, not `wait_for`: it does not cancel-and-await the request, whose settlement may run shielded.
        done, _pending = await asyncio.wait({request}, timeout=_BUDGET_SECONDS)
        if not done:
            await self.release()  # the guard frees what the request waits for, then fails
            raise AssertionError(
                f"no answer within {_BUDGET_SECONDS:.0f} s: the payment is waiting for the pool past its deadline"
            )
        response = request.result()
        return response.status_code, response.json()

    async def row(self, tx_id: str) -> tuple[str, str | None] | None:
        found = await self.observer.fetchrow(
            "select state, error ->> 'code' as code from transactions where tx_id::text = $1", tx_id
        )
        return None if found is None else (found["state"], found["code"])

    async def debts(self) -> list[str]:
        return [str(r["amount"]) for r in await self.observer.fetch("select amount from debts order by amount")]

    async def nothing_is_left(self, tx_id: str) -> None:
        """What a refusal before admission must leave: no row, no debt, an idle pool."""

        await self.release()
        assert self.pool.checkedout() == 0, f"{self.pool.checkedout()} connection(s) still checked out"
        assert await self.row(tx_id) is None, f"a row was stored for a request that was never admitted: {await self.row(tx_id)}"
        assert await self.debts() == [], await self.debts()

    async def executes_afresh(self, body: dict) -> None:
        """The same `tx_id` again is a payment that runs now - not a stored answer."""

        status, answer = await self.answer(body)
        assert (status, answer.get("status")) == (200, "COMMITTED"), (status, answer)
        assert await self.row(body["tx_id"]) == ("COMMITTED", None)
        assert await self.debts() == ["10.00000000"], "the repeated request moved no money - it was answered, not run"
        assert self.pool.checkedout() == 0


def _hook_attempts(monkeypatch, before: dict) -> list[int]:
    """Run `before[n]` (an async callable taking the call's kwargs) ahead of the n-th `_pay_attempt`."""

    original = PaymentService._pay_attempt
    calls: list[int] = []

    async def counted(service, *args, **kwargs):
        calls.append(len(calls) + 1)
        action = before.get(calls[-1])
        if action is not None:
            await action(kwargs)
        return await original(service, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_pay_attempt", counted)
    return calls


def _conflict_on_the_first_operation(monkeypatch) -> list[int]:
    """The first payment operation meets a retryable conflict - AFTER admission, as a real one does."""

    original = PaymentService._run_payment_operation
    calls: list[int] = []

    async def conflicting(service, attempt, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            assert attempt.admitted, "premise: the operation runs after admission"
            raise RetryablePaymentConflictException()
        return await original(service, attempt, **kwargs)

    monkeypatch.setattr(PaymentService, "_run_payment_operation", conflicting)
    return calls


def _past_deadline(kwargs: dict) -> None:
    kwargs["deadline"] = asyncio.get_running_loop().time() - 1.0


# ---------------------------------------------------------------------------------------------- never admitted


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["the deadline", pytest.param("the pool timeout", marks=pytest.mark.slow)])
async def test_no_connection_for_the_attempt_is_the_timeout_refusal_and_leaves_nothing(
    client: AsyncClient, db_session, monkeypatch, first: str
) -> None:
    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(
            db_session, pool_timeout=_FAR if first == "the deadline" else 1
        )

        async def exhausted(kwargs: dict) -> None:
            await stand.exhaust()
            if first == "the deadline":
                _past_deadline(kwargs)

        attempts = _hook_attempts(monkeypatch, {1: exhausted})
        body = stand.body()

        status, answer = await stand.answer(body)

        assert (status, answer["error"]["code"]) == (504, "E007"), (
            f"{first} ran out first while the attempt waited for a connection: answered {status} {answer}, "
            f"expected the declared refusal by time (504/E007)"
        )
        assert attempts == [1], f"a checkout that timed out is not retried: {attempts}"
        await stand.nothing_is_left(body["tx_id"])
        await stand.executes_afresh(body)  # the second `_pay_attempt` of the test: not hooked
        assert attempts == [1, 2]


@pytest.mark.slow
@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["the route reader's checkout", "the re-acquisition after routing"])
async def test_no_connection_around_routing_is_the_timeout_refusal_and_leaves_nothing(
    client: AsyncClient, db_session, monkeypatch, where: str
) -> None:
    """The two later checkouts, the pool's timeout first (the deadline first was already translated there)."""

    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=1)
        original_build = PaymentService._build_route_graph
        builds: list[bool] = []

        async def build(service, equivalent_code, *, cached):
            builds.append(cached)
            if len(builds) > 1:
                return await original_build(service, equivalent_code, cached=cached)
            assert stand.pool.checkedout() == 0, "premise: the attempt gave its connection back before routing"
            if where == "the route reader's checkout":
                await stand.exhaust()
                return await original_build(service, equivalent_code, cached=cached)
            await original_build(service, equivalent_code, cached=cached)
            await stand.exhaust()

        monkeypatch.setattr(PaymentService, "_build_route_graph", build)
        body = stand.body()

        status, answer = await stand.answer(body)

        assert (status, answer["error"]["code"]) == (504, "E007"), (
            f"the pool timed out at {where}: answered {status} {answer}, expected 504/E007"
        )
        await stand.nothing_is_left(body["tx_id"])
        await stand.executes_afresh(body)


@MODE_B
@pytest.mark.asyncio
async def test_a_cancellation_while_waiting_for_the_pool_leaves_nothing(client: AsyncClient, db_session, monkeypatch) -> None:
    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)
        about_to_wait = asyncio.Event()

        async def exhausted(_kwargs: dict) -> None:
            await stand.exhaust()
            about_to_wait.set()  # the request's next suspension is the wait for a connection

        _hook_attempts(monkeypatch, {1: exhausted})
        body = stand.body()
        request = stand.send(body)
        await asyncio.wait_for(about_to_wait.wait(), _BUDGET_SECONDS)
        assert not request.done() and stand.pool.checkedout() == _POOL, "premise: the request is waiting for the pool"

        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, _BUDGET_SECONDS)

        assert len(stand.held) == stand.pool.checkedout() == _POOL, "the cancelled wait took or leaked a connection"
        await stand.nothing_is_left(body["tx_id"])
        await stand.executes_afresh(body)  # the second `_pay_attempt` of the test: not hooked


@MODE_B
@pytest.mark.asyncio
async def test_a_request_refused_without_sql_takes_no_connection_and_keeps_its_own_answer(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    """An amount the ledger cannot hold is refused by `execute()` before its first statement. Such a request must
    not check a connection out for the payment (the request's authentication takes the only one), and with the
    pool exhausted it is still answered 400 - not made to wait and refused by time."""

    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)

        async def exhausted(kwargs: dict) -> None:
            await stand.exhaust()
            _past_deadline(kwargs)

        _hook_attempts(monkeypatch, {2: exhausted})

        before = stand.checkouts
        status, answer = await stand.answer(stand.body("0.000000001"))
        assert (status, answer["error"]["code"]) == (400, "E009"), (status, answer)
        assert stand.checkouts - before == 1, (
            f"a request refused without SQL checked out {stand.checkouts - before} connection(s): "
            f"only the request's authentication needs one"
        )

        body = stand.body("0.000000001")
        status, answer = await stand.answer(body)  # the second `_pay_attempt`: the pool is exhausted
        assert stand.pool.checkedout() == _POOL, "premise: the pool was exhausted when the payment was refused"
        assert (status, answer["error"]["code"]) == (400, "E009"), (
            f"the pool is exhausted and the request is invalid without any SQL: answered {status} {answer}, "
            f"expected its own 400/E009"
        )
        await stand.nothing_is_left(body["tx_id"])


def _one_checkout_fails(monkeypatch) -> dict:
    """Arm it, and the next `AsyncSession.connection()` fails once with an error that is not a timeout and carries
    no retryable SQLSTATE - a one-off infrastructure failure of a checkout."""

    original = AsyncSession.connection
    state = {"armed": False, "failed": 0}

    async def connection(session, *args, **kwargs):
        if state["armed"]:
            state["armed"] = False
            state["failed"] += 1
            raise OperationalError("checkout", None, RuntimeError("the server closed the connection"))
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "connection", connection)
    return state


@MODE_B
@pytest.mark.asyncio
async def test_a_checkout_failure_that_is_not_a_timeout_leaves_nothing_before_admission(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)
        failure = _one_checkout_fails(monkeypatch)

        async def arm(_kwargs: dict) -> None:
            failure["armed"] = True

        _hook_attempts(monkeypatch, {1: arm})
        body = stand.body()

        with pytest.raises(OperationalError):  # the in-process client re-raises what a server answers 500
            await stand.answer(body)

        assert failure["failed"] == 1, "premise: the attempt's checkout is what failed"
        await stand.nothing_is_left(body["tx_id"])
        await stand.executes_afresh(body)


# ------------------------------------------------------------------------------- admitted by an earlier attempt


async def _admitted_then_no_connection(stand: _Stand, monkeypatch, *, deadline_first: bool) -> tuple[list[int], list[int]]:
    """Attempt 1 is admitted and meets a retryable conflict; attempt 2 finds the pool exhausted."""

    operations = _conflict_on_the_first_operation(monkeypatch)

    async def exhausted(kwargs: dict) -> None:
        assert stand.pool.checkedout() == 0, "premise: the first attempt's session is closed"
        await stand.exhaust()
        if deadline_first:
            _past_deadline(kwargs)

    attempts = _hook_attempts(monkeypatch, {2: exhausted})
    return attempts, operations


@MODE_B
@pytest.mark.asyncio
async def test_a_request_admitted_earlier_is_recorded_aborted_and_replays(client: AsyncClient, db_session, monkeypatch) -> None:
    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)
        attempts, operations = await _admitted_then_no_connection(stand, monkeypatch, deadline_first=True)
        original_record = payment_service.record_definitive_refusal
        recorded: list[str] = []

        async def record_once_the_pool_is_free(sessions, refusal, **kwargs):
            recorded.append(refusal.tx_id)
            await stand.release()
            return await original_record(sessions, refusal, **kwargs)

        monkeypatch.setattr(payment_service, "record_definitive_refusal", record_once_the_pool_is_free)
        body = stand.body()

        status, answer = await stand.answer(body)

        assert (attempts, operations) == ([1, 2], [1]), (attempts, operations)
        assert (status, answer["error"]["code"]) == (504, "E007"), (status, answer)
        assert recorded == [body["tx_id"]], "the refusal of a request admitted by an earlier attempt was not recorded"
        assert await stand.row(body["tx_id"]) == ("ABORTED", "E007"), await stand.row(body["tx_id"])
        assert await stand.debts() == [] and stand.pool.checkedout() == 0

        # The same identity again: the stored refusal, not a payment.
        status, replay = await stand.answer(body)
        assert (status, replay["status"], replay["error"]["code"]) == (200, "ABORTED", "E007"), (status, replay)
        assert attempts == [1, 2, 3] and operations == [1], "the replay ran a payment operation"
        assert await stand.debts() == []


@MODE_B
@pytest.mark.asyncio
async def test_after_admission_a_checkout_failure_that_is_not_a_timeout_is_final_too(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    """DECLARED, NOT CHANGED (§15 review of `c27a2fbe`, Н1). Spec 019's table: a non-retryable internal failure
    after admission is recorded `ABORTED`. A10 made the next attempt's checkout a place where that can happen: a
    one-off failure of it, after an earlier attempt was admitted, spends the signed identity - the caller gets the
    500, the row says `ABORTED/E010`, and the same request again is answered with it instead of being run."""

    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)
        operations = _conflict_on_the_first_operation(monkeypatch)
        failure = _one_checkout_fails(monkeypatch)

        async def arm(_kwargs: dict) -> None:
            failure["armed"] = True

        attempts = _hook_attempts(monkeypatch, {2: arm})
        body = stand.body()

        with pytest.raises(OperationalError):
            await stand.answer(body)

        assert (attempts, operations, failure["failed"]) == ([1, 2], [1], 1), (attempts, operations, failure)
        assert await stand.row(body["tx_id"]) == ("ABORTED", "E010"), await stand.row(body["tx_id"])
        assert await stand.debts() == [] and stand.pool.checkedout() == 0

        status, replay = await stand.answer(body)
        assert (status, replay["status"], replay["error"]["code"]) == (200, "ABORTED", "E010"), (status, replay)
        assert operations == [1] and await stand.debts() == [], "the replay ran a payment operation"


@MODE_B
@pytest.mark.asyncio
async def test_a_committed_row_of_the_same_request_wins_and_is_not_overwritten(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)
        await _admitted_then_no_connection(stand, monkeypatch, deadline_first=True)
        original_record = payment_service.record_definitive_refusal

        async def a_winner_committed_first(sessions, refusal, **kwargs):
            await stand.release()
            async with stand.sessions() as other:  # another attempt of the SAME request, committed
                other.add(Transaction(**dict(refusal.row), state="COMMITTED", signatures=[]))
                await other.commit()
            return await original_record(sessions, refusal, **kwargs)

        monkeypatch.setattr(payment_service, "record_definitive_refusal", a_winner_committed_first)
        body = stand.body()

        status, answer = await stand.answer(body)

        assert (status, answer.get("status")) == (200, "COMMITTED"), (
            f"a COMMITTED row of the same request exists: it is the answer, got {status} {answer}"
        )
        assert await stand.row(body["tx_id"]) == ("COMMITTED", None), "the winner's row was overwritten"
        assert stand.pool.checkedout() == 0


@pytest.mark.slow
@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["the recording's budget", "the pool timeout"])
async def test_a_refusal_that_cannot_be_recorded_is_the_retryable_conflict_and_claims_nothing(
    client: AsyncClient, db_session, monkeypatch, first: str
) -> None:
    """The pool is STILL exhausted when the refusal is to be recorded: the outcome is not established."""

    async with contextlib.AsyncExitStack() as stack:
        deadline_first = first == "the recording's budget"
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR if deadline_first else 1)
        attempts, _operations = await _admitted_then_no_connection(stand, monkeypatch, deadline_first=deadline_first)
        body = stand.body()

        status, answer = await stand.answer(body)

        assert (status, answer["error"]["code"]) == (409, "E008"), (
            f"{first} ran out while recording: answered {status} {answer}, expected the retryable 409/E008"
        )
        assert answer["error"]["details"].get("retryable") is True, answer
        assert attempts == [1, 2]
        await stand.nothing_is_left(body["tx_id"])
        await stand.executes_afresh(body)


# ----------------------------------------------------------------------------------- a service that is reused


@MODE_B
@pytest.mark.asyncio
async def test_a_reused_service_does_not_settle_against_the_previous_payment(client: AsyncClient, db_session, monkeypatch) -> None:
    """`create_payment` runs every attempt on ONE service (`_service_for` hands out `self`). The second payment's
    checkout fails before `execute()`; it must be settled as itself - never admitted - not as the first, whose
    state (admitted, with a row) is what the service last held."""

    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)
        payer_id = await stand.observer.fetchval("select id from participants where pid = $1", stand.payer["pid"])
        first, second = stand.body(), stand.body()

        async def exhausted(kwargs: dict) -> None:  # the second payment's attempt: no connection, the deadline passed
            await stand.exhaust()
            _past_deadline(kwargs)

        _hook_attempts(monkeypatch, {2: exhausted})

        async with stand.sessions() as session:
            service = PaymentService(session)
            done = await service.create_payment(payer_id, PaymentCreateRequest.model_validate(first))
            assert done.status == "COMMITTED" and stand.pool.checkedout() == 0, done

            with pytest.raises(TimeoutException) as refused:
                await asyncio.wait_for(
                    service.create_payment(payer_id, PaymentCreateRequest.model_validate(second)), _BUDGET_SECONDS
                )
        assert refused.value.status_code == 504, refused.value

        await stand.release()
        assert await stand.row(first["tx_id"]) == ("COMMITTED", None)
        assert await stand.row(second["tx_id"]) is None, "the second payment was settled with a stored row"
        assert await stand.debts() == ["10.00000000"]


# ------------------------------------------------------- 035 A11: a cancellation between admission and an outcome
#
# Spec 019, "Окончательный отказ...": a cancellation AFTER ADMISSION, with the rollback confirmed, is a final
# `ABORTED/E007` (the cancellation still propagates), and the same signed identity must not execute later. The
# windows `pay()` already settled write exactly that - `_definitive_refusal`: code `E007`, message
# "Payment cancelled". The closing review of programme 035 read two windows where it was NOT written, and the
# enumeration of the windows for A11 found a third of the same kind:
#
#   "the first read"  - the next attempt has its connection and is cancelled on `execute()`'s first statement,
#                       before `execute()` has re-established the request's identity;
#   "the backoff"     - cancelled while `pay()` waits before the next attempt;
#   "the close"       - cancelled while the failed attempt's session is being closed, between the two.
#
# Each is held on an EVENT at that exact point and the request task is cancelled there: no timer, no sleep. The
# control of each: WITHOUT an earlier admission the same cancellation leaves no row and the request runs when sent
# again - "never admitted" must not become `ABORTED`.

_WINDOWS = ["the first read", "the backoff", "the close"]


class _AsyncioWithItsSleepHeld:
    """`asyncio` as `app.core.payments.service` sees it, with `sleep` replaced - its only `sleep` is the backoff."""

    def __init__(self, sleep) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str):
        return getattr(asyncio, name)


async def _cancelled_in(stand: _Stand, monkeypatch, window: str, *, admitted: bool) -> tuple[dict, list[int]]:
    """Send one payment whose first attempt meets a retryable conflict - after admission, or (the control) before
    it - hold the request in `window`, cancel it there, and wait for it. Returns the body and the operations run."""

    reached, release = asyncio.Event(), asyncio.Event()
    armed = {"on": False}

    async def hold() -> None:
        armed["on"] = False
        reached.set()
        await release.wait()

    # --- the conflict that sends the first attempt to a retry ---
    original_operation = PaymentService._run_payment_operation
    original_build = PaymentService._build_route_graph
    operations: list[int] = []
    conflicted = {"done": False}

    def conflict() -> None:
        conflicted["done"] = True
        if window != "the first read":
            armed["on"] = True  # the next backoff / the next session close is the one to hold
        raise RetryablePaymentConflictException()

    async def operation(service, attempt, **kwargs):
        operations.append(1)
        if admitted and not conflicted["done"]:
            assert attempt.admitted, "premise: the operation runs after admission"
            conflict()
        return await original_operation(service, attempt, **kwargs)

    async def build(service, equivalent_code, *, cached):
        if not admitted and not conflicted["done"]:
            assert not service._attempt.admitted, "premise: routing runs before admission"
            conflict()
        return await original_build(service, equivalent_code, cached=cached)

    monkeypatch.setattr(PaymentService, "_run_payment_operation", operation)
    monkeypatch.setattr(PaymentService, "_build_route_graph", build)

    # --- the three barriers; only the one of `window` is ever armed ---
    if window == "the first read":
        original_read = MoneyBoundary.require_read_committed

        async def first_read(session, *, writer):
            if armed["on"]:
                await hold()
            return await original_read(session, writer=writer)

        monkeypatch.setattr(MoneyBoundary, "require_read_committed", staticmethod(first_read))

        async def arm(_kwargs: dict) -> None:
            armed["on"] = True

        _hook_attempts(monkeypatch, {2: arm})  # the attempt after the conflict
    elif window == "the backoff":

        async def backoff(delay: float) -> None:
            if armed["on"]:
                await hold()

        monkeypatch.setattr(payment_service, "asyncio", _AsyncioWithItsSleepHeld(backoff))
    else:
        original_close = AsyncSession.close

        async def close(session) -> None:
            if armed["on"]:
                await hold()
            await original_close(session)

        monkeypatch.setattr(AsyncSession, "close", close)

    body = stand.body()
    request = stand.send(body)
    waiting = asyncio.ensure_future(reached.wait())
    stand.launched.append(waiting)
    await asyncio.wait({waiting, request}, timeout=_BUDGET_SECONDS, return_when=asyncio.FIRST_COMPLETED)
    assert reached.is_set() and not request.done(), f"premise: the request is held in {window}"

    request.cancel()
    if window == "the close":
        # The close runs in a task of its own, shielded: the cancellation is delivered to the request while the
        # close is still held. Let the request take the cancellation, then let the close finish.
        await asyncio.wait({request}, timeout=_BUDGET_SECONDS)
        release.set()
    outcome = await asyncio.wait({request}, timeout=_BUDGET_SECONDS)
    release.set()
    assert request in outcome[0] and request.cancelled(), "the cancellation must still reach the caller"
    return body, operations


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("window", _WINDOWS)
async def test_a_cancellation_after_admission_is_recorded_and_the_identity_does_not_run_later(
    client: AsyncClient, db_session, monkeypatch, window: str
) -> None:
    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)

        body, operations = await _cancelled_in(stand, monkeypatch, window, admitted=True)

        assert await stand.row(body["tx_id"]) == ("ABORTED", "E007"), (
            f"cancelled in {window} after an earlier attempt was admitted: the stored row is "
            f"{await stand.row(body['tx_id'])}, expected ('ABORTED', 'E007') - without it the same signed request "
            f"runs when it is sent again"
        )
        assert await stand.debts() == [] and operations == [1]

        status, replay = await stand.answer(body)
        assert (status, replay["status"], replay["error"]["code"]) == (200, "ABORTED", "E007"), (status, replay)
        assert replay["error"]["message"] == "Payment cancelled", replay
        assert operations == [1] and await stand.debts() == [], "the same identity ran a payment after its refusal"
        assert stand.pool.checkedout() == 0


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("window", _WINDOWS)
async def test_a_cancellation_before_any_admission_leaves_nothing_in_the_same_windows(
    client: AsyncClient, db_session, monkeypatch, window: str
) -> None:
    """The control: the same cancellation, the first attempt refused by a conflict BEFORE admission."""

    async with contextlib.AsyncExitStack() as stack:
        stand = await _Stand(client, monkeypatch, stack).open(db_session, pool_timeout=_FAR)

        body, operations = await _cancelled_in(stand, monkeypatch, window, admitted=False)

        assert operations == [], "premise: no attempt of this request reached the payment operation"
        await stand.nothing_is_left(body["tx_id"])
        await stand.executes_afresh(body)
        assert operations == [1]
