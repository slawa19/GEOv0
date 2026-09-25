"""Programme 020 stage 1: an unknown clearing commit is not retried when its resolution fails.

THE FINDING (019 closing review, class 2; `specs/BACKLOG.md`, "Класс 2 из закрывающего ревью программы 019").
`_commit_to_terminal` hands back a COMMIT error whose outcome the service cannot know (the connection went
away), and the resolver `_reconcile_committed_execution` is asked whether the occurrence committed. When
the RESOLVER itself fails with 40001/40P01, its error - not the commit's - reached `_end_attempt_on_error`,
which classified it as a transaction conflict, resolved once more, found nothing and raised
`_ClearingAttemptConflict`: `_run_attempts` then began a second money attempt. Retry eligibility came from
a secondary read. Intended (019, precondition 3): an unknown commit is never retried; only a rollback that
PostgreSQL reported on COMMIT may be; a verified committed occurrence answers with its durable amount.

THIS IS FAULT INJECTION, NOT A REAL CONNECTION-LOSS SCHEDULE. What is injected and what is PostgreSQL's:

* the unknown COMMIT is INJECTED. `_commit_to_terminal` is overridden for the first attempt only: it
  either rolls the transaction back or commits it for real, and in both cases reports
  `ConnectionError` - the service is told what a lost connection tells it, and the test knows what
  actually happened. The classification boundary is this method's return value, so that is where the
  fault sits;
* THE FAULT SCHEDULE (binding, Codex round 2 on 020, P2-3): the FIRST COMPLETE reconciliation invocation
  fails, and every later one runs untouched. A single failed read would not do - the resolver reads up
  to three times and swallows an earlier failure - and a resolver that always fails would also fail the
  classifier's second reconciliation (`_end_attempt_on_error`), ending in `E010` without a second money
  attempt on the baseline too: a false pass. So the invocation's LAST read (its third) is the one that
  fails, and the failure itself is PostgreSQL's: that read takes `ACCESS SHARE` on one barrier table and
  then waits on a second one held by a blocker, which then asks for the first; PostgreSQL detects the
  deadlock in the resolver's backend (the blocker's own check is pushed to 30 s) and raises `40P01`.
  Controls: the resolver was seen waiting on the blocker, the blocker on the resolver,
  `pg_stat_database.deadlocks` counted it, the first invocation raised `40P01`, and no later invocation
  raised. The retry budget is set to 3 attempts and a 60 s deadline, so the baseline CAN reach a second
  attempt (it does: `_end_attempt_on_error` -> `_ClearingAttemptConflict` -> `_run_attempts`);
* attempts are counted at entry into `_execute_clearing_with_amount`, the one attempt body;
* the confirmed rollback of control (a) is REAL: a concurrent SERIALIZABLE transaction reads the debts the
  clearing has written (uncommitted) and updates a participant row the clearing has read, then commits
  first; PostgreSQL dooms the clearing and its COMMIT fails with 40001. Control: the recorded commit error
  carries `40001`.

The contract an unknown commit already has elsewhere (control (c), unchanged by this stage): no second
attempt, the sanitized internal error `GeoException` `E010`, and nothing durable when nothing committed.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.clearing.service import ClearingService
from app.core.payments.router import PaymentRouter
from tests.integration.p019_interlock_support import _seed_interlock_case, _use_serializable
from tests.p019_support import require_target

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

BARRIER_HELD = "p020_s1_resolver_holds"
BARRIER_WAITED = "p020_s1_resolver_waits"
_RESOLVER_READ_THAT_DEADLOCKS = 3  # the first invocation's last read: its error is the one it surfaces


def _pg_codes(exc: BaseException) -> list[str]:
    return sorted(ClearingService._postgres_error_codes(exc) & {"40001", "40P01"})


def _stand(
    commit_mode: str,
    *,
    seed: dict,
    deadlock_the_resolver: bool = False,
    fail_the_earlier_reads: bool = False,
    cancel_during_the_second_resolution: dict | None = None,
):
    """A `ClearingService` whose FIRST commit ends as `commit_mode`, and that records what happened.

    `commit_mode`:
    * `unknown_not_committed` - rolled back, reported as `ConnectionError` (fault injection, see module doc);
    * `unknown_committed` - committed for real, reported as `ConnectionError` (fault injection);
    * `real_rollback` - a concurrent transaction makes PostgreSQL refuse the COMMIT with 40001 (real).

    `fail_the_earlier_reads`: the first resolution's reads 1 and 2 raise an INJECTED `RuntimeError`, so an
    occurrence that is durable cannot answer them; only its third read - the one whose error the resolver
    surfaces - is PostgreSQL's deadlock. Needed only when the commit really landed.

    `cancel_during_the_second_resolution` (`{"second_started": Event, "release": Event}`): the first two
    resolution invocations run for real and then raise an INJECTED `RuntimeError`; the second one first
    sets `second_started` and waits for `release`, so the test can cancel the caller while it runs.
    Later invocations are untouched.
    """

    from tests.conftest import TestingSessionLocal

    state = {
        "attempts": 0,
        "armed": False,
        "reads_after_commit": 0,
        "commit_errors": [],
        "resolver_errors": [],
        "resolver_pid": None,
        "reconciliations": [],
    }

    class _Stand(ClearingService):
        async def _execute_clearing_with_amount(self, cycle, **kwargs):
            # The one attempt body: every entry is one money attempt.
            state["attempts"] += 1
            return await super()._execute_clearing_with_amount(cycle, **kwargs)

        async def _reconcile_committed_execution(self, tx_id, *, allowed_participant_pids=None):
            invocation = len(state["reconciliations"]) + 1
            script = cancel_during_the_second_resolution
            try:
                if script is not None and invocation == 2:
                    script["second_started"].set()
                    await script["release"].wait()
                amount = await super()._reconcile_committed_execution(
                    tx_id, allowed_participant_pids=allowed_participant_pids
                )
                if script is not None and invocation <= 2:
                    raise RuntimeError(f"injected: resolution {invocation} failed")
            except Exception as exc:
                state["reconciliations"].append(("raised", _pg_codes(exc)))
                raise
            state["reconciliations"].append(("returned", amount))
            return amount

        @staticmethod
        async def _read_committed_execution_amount(session, tx_id, *, allowed_participant_pids=None):
            if state["armed"]:
                state["reads_after_commit"] += 1
                if fail_the_earlier_reads and state["reads_after_commit"] < _RESOLVER_READ_THAT_DEADLOCKS:
                    raise RuntimeError("injected: an earlier read of the first resolution failed")
                if deadlock_the_resolver and state["reads_after_commit"] == _RESOLVER_READ_THAT_DEADLOCKS:
                    try:
                        state["resolver_pid"] = int(await session.scalar(text("SELECT pg_backend_pid()")))
                        await session.execute(text(f"SELECT count(*) FROM {BARRIER_HELD}"))
                        await session.execute(text(f"SELECT count(*) FROM {BARRIER_WAITED}"))
                    except Exception as exc:
                        state["resolver_errors"].append(exc)
                        raise
            return await ClearingService._read_committed_execution_amount(
                session, tx_id, allowed_participant_pids=allowed_participant_pids
            )

        async def _commit_to_terminal(self):
            if state["armed"] or state["attempts"] != 1:
                return await super()._commit_to_terminal()
            state["armed"] = True
            if commit_mode == "unknown_not_committed":
                await self.session.rollback()
                error: BaseException = ConnectionError("injected: connection lost during COMMIT")
            elif commit_mode == "unknown_committed":
                cancellation, error = await super()._commit_to_terminal()
                assert (cancellation, error) == (None, None), "premise: the injected commit is durable"
                error = ConnectionError("injected: acknowledgement of a durable COMMIT lost")
            elif commit_mode == "real_rollback":
                a_id = seed["participant_ids"][0]
                async with TestingSessionLocal() as other:
                    await _use_serializable(other)
                    # Reads the debts this clearing has written (uncommitted): other -> clearing.
                    await other.execute(text("SELECT id, amount FROM debts WHERE id = ANY(:ids)"),
                                        {"ids": list(seed["debt_ids"])})
                    # Writes a participant row this clearing has read: clearing -> other. Commits first.
                    await other.execute(
                        text("UPDATE participants SET display_name = display_name || '.' WHERE id = :id"),
                        {"id": a_id},
                    )
                    await other.commit()
                cancellation, error = await super()._commit_to_terminal()
                assert cancellation is None
                assert error is not None, "premise: PostgreSQL refused the clearing's COMMIT"
            else:  # pragma: no cover - a typo in a test
                raise AssertionError(commit_mode)
            state["commit_errors"].append(error)
            return None, error

    return _Stand, state


async def _evidence(seed: dict) -> tuple[list[tuple[str, Decimal]], dict]:
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction
    from tests.conftest import TestingSessionLocal

    execution_tx_id = ClearingService._execution_tx_id(seed["debt_ids"])
    async with TestingSessionLocal() as verify:
        transactions = [
            (row.state, Decimal(str(row.payload["amount"])))
            for row in (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.tx_id == execution_tx_id, Transaction.type == "CLEARING"
                    )
                )
            ).all()
        ]
        debts = {
            debt.id: debt.amount
            for debt in (
                await verify.scalars(select(Debt).where(Debt.equivalent_id == seed["equivalent_id"]))
            ).all()
        }
    return transactions, debts


async def _run(stand_cls, seed: dict):
    from tests.conftest import TestingSessionLocal

    owner = TestingSessionLocal()
    try:
        await _use_serializable(owner)
        try:
            return await asyncio.wait_for(stand_cls(owner).execute_clearing_with_amount(seed["cycle"]), 60)
        except Exception as exc:  # noqa: BLE001 - compared by the caller
            return exc
    finally:
        await owner.rollback()
        await owner.close()
        PaymentRouter.invalidate_cache(seed["equivalent_code"])


def _untouched(seed: dict) -> dict:
    d_ab, d_bc, d_ca = seed["debt_ids"]
    return {d_ab: Decimal("100.00000000"), d_bc: Decimal("30.00000000"), d_ca: Decimal("40.00000000")}


def _cleared(seed: dict) -> dict:
    d_ab, _d_bc, d_ca = seed["debt_ids"]
    return {d_ab: Decimal("70.00000000"), d_ca: Decimal("10.00000000")}


async def _poll(check, timeout: float = 15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(0.005)
    return None


async def _deadlocks() -> int:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
        value = await s.scalar(text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()"))
        await s.rollback()
    return int(value or 0)


async def _create_barriers() -> None:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as setup:
        await setup.execute(text(f"CREATE TABLE {BARRIER_HELD} (id int)"))
        await setup.execute(text(f"CREATE TABLE {BARRIER_WAITED} (id int)"))
        await setup.commit()


async def _run_with_the_first_resolution_deadlocked(stand_cls, state: dict, seed: dict):
    """Run the clearing while the first resolution's third read is made PostgreSQL's deadlock victim.

    Returns `(outcome, blocker_waited)`; the caller asserts the controls.
    """

    from tests.conftest import TestingSessionLocal

    blocker = TestingSessionLocal()
    observer = TestingSessionLocal()
    clearing_task = blocker_task = None
    try:
        blocker_pid = int(await blocker.scalar(text("SELECT pg_backend_pid()")))
        # The resolver must be the victim: its check runs 1 s into its wait, the blocker's never first.
        await blocker.execute(text("SET deadlock_timeout = '30s'"))
        await blocker.execute(text(f"LOCK TABLE {BARRIER_WAITED} IN ACCESS EXCLUSIVE MODE"))

        clearing_task = asyncio.create_task(_run(stand_cls, seed))

        async def resolver_waits_on_the_blocker() -> bool:
            pid = state["resolver_pid"]
            if pid is None:
                return False
            blocked = bool(
                await observer.scalar(
                    text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                    {"holder": blocker_pid, "waiter": pid},
                )
            )
            await observer.rollback()
            return blocked

        assert await _poll(resolver_waits_on_the_blocker), "the resolver never waited on the blocker"
        resolver_pid = state["resolver_pid"]
        blocker_task = asyncio.create_task(
            blocker.execute(text(f"LOCK TABLE {BARRIER_HELD} IN ACCESS EXCLUSIVE MODE"))
        )

        async def blocker_waits_on_the_resolver() -> bool:
            blocked = bool(
                await observer.scalar(
                    text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                    {"holder": resolver_pid, "waiter": blocker_pid},
                )
            )
            await observer.rollback()
            return blocked

        blocker_waited = await _poll(blocker_waits_on_the_resolver, timeout=5.0)
        await asyncio.wait_for(blocker_task, timeout=30)
        await blocker.rollback()
        outcome = await asyncio.wait_for(clearing_task, timeout=60)
    finally:
        try:
            await blocker.rollback()
        finally:
            await blocker.close()
            await observer.close()
        for task in (clearing_task, blocker_task):
            if task is not None and not task.done():
                task.cancel()

    return outcome, blocker_waited


def _assert_the_first_resolution_deadlocked(state: dict, blocker_waited) -> None:
    assert [type(e).__name__ for e in state["commit_errors"]] == ["ConnectionError"], state["commit_errors"]
    assert blocker_waited, "the blocker never queued on the resolver: no deadlock was formed"
    assert [_pg_codes(e) for e in state["resolver_errors"]] == [["40P01"]], state["resolver_errors"]
    # The schedule: the first complete invocation failed with 40P01, every later one ran untouched.
    assert state["reconciliations"][:1] == [("raised", ["40P01"])], state["reconciliations"]
    assert all(kind == "returned" for kind, _ in state["reconciliations"][1:]), state["reconciliations"]


@pytest.mark.asyncio
async def test_an_unknown_commit_whose_resolver_deadlocks_is_not_retried(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)

    seed = await _seed_interlock_case()
    await _create_barriers()
    stand_cls, state = _stand("unknown_not_committed", seed=seed, deadlock_the_resolver=True)
    deadlocks_before = await _deadlocks()

    outcome, blocker_waited = await _run_with_the_first_resolution_deadlocked(stand_cls, state, seed)

    # Controls: the original commit is the injected unknown one; the resolver really deadlocked.
    _assert_the_first_resolution_deadlocked(state, blocker_waited)

    async def counted() -> bool:
        return await _deadlocks() > deadlocks_before

    assert await _poll(counted, timeout=10.0), "PostgreSQL counted no deadlock"
    transactions, debts = await _evidence(seed)

    require_target(
        state["attempts"] == 1
        and getattr(outcome, "code", None) == "E010"
        and transactions == []
        and debts == _untouched(seed),
        f"an unknown commit whose resolver deadlocked began {state['attempts']} attempt(s) and ended as "
        f"{outcome!r}; transactions {transactions}, debts {debts}, "
        f"reconciliations {state['reconciliations']}",
    )


@pytest.mark.asyncio
async def test_control_a_rollback_postgres_reported_on_commit_is_still_retried(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)
    seed = await _seed_interlock_case()
    stand_cls, state = _stand("real_rollback", seed=seed)

    outcome = await _run(stand_cls, seed)

    assert [_pg_codes(e) for e in state["commit_errors"]] == [["40001"]], state["commit_errors"]
    transactions, debts = await _evidence(seed)
    assert state["attempts"] == 2, state["attempts"]
    assert outcome == Decimal("30.00000000"), outcome
    assert transactions == [("COMMITTED", Decimal("30.00"))], transactions
    assert debts == _cleared(seed), debts


@pytest.mark.asyncio
async def test_control_b_a_verified_committed_occurrence_returns_its_durable_amount() -> None:
    seed = await _seed_interlock_case()
    stand_cls, state = _stand("unknown_committed", seed=seed)

    outcome = await _run(stand_cls, seed)

    assert state["reconciliations"] == [("returned", Decimal("30.00"))], state["reconciliations"]

    assert [type(e).__name__ for e in state["commit_errors"]] == ["ConnectionError"], state["commit_errors"]
    transactions, debts = await _evidence(seed)
    assert state["attempts"] == 1, state["attempts"]
    assert outcome == Decimal("30.00"), outcome
    assert transactions == [("COMMITTED", Decimal("30.00"))], transactions
    assert debts == _cleared(seed), debts


@pytest.mark.asyncio
async def test_control_c_an_unresolved_unknown_commit_is_the_sanitized_error_without_a_retry() -> None:
    """The existing unknown-commit contract, with a resolver that works: this is what the target expects."""

    seed = await _seed_interlock_case()
    stand_cls, state = _stand("unknown_not_committed", seed=seed)

    outcome = await _run(stand_cls, seed)

    assert [type(e).__name__ for e in state["commit_errors"]] == ["ConnectionError"], state["commit_errors"]
    assert state["reads_after_commit"] >= 3, state  # the resolver ran and found nothing
    transactions, debts = await _evidence(seed)
    assert state["attempts"] == 1, state["attempts"]
    assert getattr(outcome, "code", None) == "E010", outcome
    assert transactions == [] and debts == _untouched(seed), (transactions, debts)


@pytest.mark.asyncio
async def test_control_d_a_durable_commit_whose_first_resolution_deadlocks_returns_its_amount(
    monkeypatch,
) -> None:
    """The committed half of the same schedule: the money is durable, so the clearing must say so.

    The commit really lands and `ConnectionError` is injected; the first complete resolution fails (its
    reads 1-2 by injection, its surfaced third read by PostgreSQL's deadlock); the next one runs untouched
    and finds the occurrence. A durable clearing is never reported as not having happened.
    """

    from app.config import settings

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)

    seed = await _seed_interlock_case()
    await _create_barriers()
    stand_cls, state = _stand(
        "unknown_committed", seed=seed, deadlock_the_resolver=True, fail_the_earlier_reads=True
    )
    deadlocks_before = await _deadlocks()

    outcome, blocker_waited = await _run_with_the_first_resolution_deadlocked(stand_cls, state, seed)

    _assert_the_first_resolution_deadlocked(state, blocker_waited)

    async def counted() -> bool:
        return await _deadlocks() > deadlocks_before

    assert await _poll(counted, timeout=10.0), "PostgreSQL counted no deadlock"
    transactions, debts = await _evidence(seed)
    # Premise: the money is durable whatever the clearing reports.
    assert transactions == [("COMMITTED", Decimal("30.00"))], transactions
    assert debts == _cleared(seed), debts

    require_target(
        state["attempts"] == 1
        and outcome == Decimal("30.00")
        and state["reconciliations"] == [("raised", ["40P01"]), ("returned", Decimal("30.00"))],
        f"a durable clearing whose first resolution deadlocked began {state['attempts']} attempt(s) and "
        f"ended as {outcome!r}; reconciliations {state['reconciliations']}",
    )


async def _cancel_while_the_second_resolution_runs(commit_mode: str):
    """Unknown or refused COMMIT; resolution 1 fails; the caller cancels during resolution 2, which fails.

    FAULT INJECTION: both resolution failures are injected (`RuntimeError` after a real resolution); the
    cancellation is a real `Task.cancel()` of the caller, delivered while resolution 2 is still waiting.
    """

    seed = await _seed_interlock_case()
    script = {"second_started": asyncio.Event(), "release": asyncio.Event()}
    stand_cls, state = _stand(commit_mode, seed=seed, cancel_during_the_second_resolution=script)

    from tests.conftest import TestingSessionLocal

    owner = TestingSessionLocal()
    clearing_task = None
    try:
        await _use_serializable(owner)
        clearing_task = asyncio.create_task(stand_cls(owner).execute_clearing_with_amount(seed["cycle"]))
        await asyncio.wait_for(script["second_started"].wait(), timeout=30)
        assert not clearing_task.done(), "premise: the caller is still waiting on resolution 2"
        clearing_task.cancel()
        script["release"].set()
        try:
            outcome: object = await asyncio.wait_for(asyncio.shield(clearing_task), timeout=60)
        except asyncio.CancelledError as cancelled:
            outcome = cancelled
        except Exception as exc:  # noqa: BLE001 - compared by the caller
            outcome = exc
    finally:
        script["release"].set()
        if clearing_task is not None and not clearing_task.done():
            clearing_task.cancel()
        await owner.rollback()
        await owner.close()
        PaymentRouter.invalidate_cache(seed["equivalent_code"])
    transactions, debts = await _evidence(seed)
    return seed, state, outcome, transactions, debts


@pytest.mark.asyncio
async def test_a_cancellation_during_a_failing_second_resolution_propagates(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)

    seed, state, outcome, transactions, debts = await _cancel_while_the_second_resolution_runs(
        "unknown_not_committed"
    )

    # Controls: the unknown commit was the injected one, and both scripted resolutions failed.
    assert [type(e).__name__ for e in state["commit_errors"]] == ["ConnectionError"], state["commit_errors"]
    assert state["reconciliations"][:2] == [("raised", []), ("raised", [])], state["reconciliations"]

    require_target(
        isinstance(outcome, asyncio.CancelledError)
        and state["attempts"] == 1
        and transactions == []
        and debts == _untouched(seed),
        f"a caller cancelled during a failing resolution got {outcome!r} after {state['attempts']} "
        f"attempt(s); transactions {transactions}, reconciliations {state['reconciliations']}",
    )


@pytest.mark.asyncio
async def test_a_cancellation_during_a_failing_resolution_after_a_refused_commit_is_not_retried(
    monkeypatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)

    seed, state, outcome, transactions, debts = await _cancel_while_the_second_resolution_runs("real_rollback")

    # Controls: PostgreSQL really refused the COMMIT, and both scripted resolutions failed.
    assert [_pg_codes(e) for e in state["commit_errors"]] == [["40001"]], state["commit_errors"]
    assert state["reconciliations"][:2] == [("raised", []), ("raised", [])], state["reconciliations"]

    require_target(
        isinstance(outcome, asyncio.CancelledError)
        and state["attempts"] == 1
        and transactions == []
        and debts == _untouched(seed),
        f"a caller cancelled during a failing resolution after a refused COMMIT got {outcome!r} after "
        f"{state['attempts']} attempt(s); transactions {transactions}, reconciliations {state['reconciliations']}",
    )
