"""T1523 cells 3 and 5 on PostgreSQL at the application's isolation level.

WHY THESE TWO ARE HERE AND NOT IN `test_payment_idempotency_postgres.py`. That module pins its
sessions to READ COMMITTED (`:116-123`) and says so; after `T1549` the shared test engine runs at
`settings.DB_POSTGRES_ISOLATION_LEVEL` - SERIALIZABLE, what the application actually runs at - and
the two cells below take their sessions from it unchanged. The older module stays as the named
READ COMMITTED counter-probe; this one is the acceptance cell. Every assertion here is preceded by
a premise that the intended situation really occurred, because a duplicate-payment test that
silently serialised into two sequential payments would otherwise be green.

* Cell 3: a second request for a `tx_id` whose first payment is PREPARED - a state reached AFTER
  prepare, not the `NEW` the inventory found covered - is refused 409/E008 "in progress", and the
  refusal writes nothing.
* Cell 5: the insert race. The loser passes the initial lookup while no row exists, the winner
  commits its row, and the loser's own INSERT meets `UNIQUE(tx_id)`. The service has two defined
  answers for that (`app/core/payments/service.py:930-946` re-reads a `23505` and reports "in
  progress"; `:947-995` classifies a real `40001` as a retryable conflict), and WHICH ONE
  PostgreSQL produces at SERIALIZABLE was not established before this test: it is recorded from
  the run rather than assumed. A 500, or a second effect, would be a defect, not a branch.

WHAT NEITHER CELL CLAIMS. They measure the request-level idempotency path. The engine-level
duplicate commit is covered at `test_payment_commit_advisory_locks_postgres.py:702` and is not
touched here.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text

from tests.debt_setup import purge_test_ledger


async def _effects(session, tx_id: str, equivalent_id) -> dict[str, object]:
    """The triple, read on a connection of its own: debts, transaction rows, the journal."""

    from app.db.journal_tables import debt_journal_entries, debt_operations
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction

    debts = sorted(
        (str(debtor), str(creditor), str(amount))
        for debtor, creditor, amount in (
            await session.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == equivalent_id
                )
            )
        ).all()
    )
    transactions = sorted(
        (str(tx_uuid), str(state))
        for tx_uuid, state in (
            await session.execute(
                select(Transaction.id, Transaction.state).where(Transaction.tx_id == tx_id)
            )
        ).all()
    )
    envelopes = (
        await session.execute(
            select(
                debt_operations.c.id,
                debt_operations.c.state,
                debt_operations.c.effect_count,
            ).where(
                debt_operations.c.kind == "PAYMENT",
                debt_operations.c.identity == tx_id,
            )
        )
    ).all()
    entries: list[tuple[str, str]] = []
    for operation_id, _state, _count in envelopes:
        rows = (
            await session.execute(
                select(debt_journal_entries.c.effect, debt_journal_entries.c.delta).where(
                    debt_journal_entries.c.operation_id == operation_id
                )
            )
        ).all()
        entries.extend((str(effect), str(delta)) for effect, delta in rows)
    entries.sort()
    return {
        "debts": debts,
        "transactions": transactions,
        "envelopes": sorted((str(state), count) for _id, state, count in envelopes),
        "entries": entries,
    }


def _assert_one_committed_payment(effects: dict[str, object], amount: str) -> None:
    """The anti-vacuum premise, and the property, in the same shape for both cells."""

    assert len(effects["transactions"]) == 1, effects["transactions"]
    assert effects["transactions"][0][1] == "COMMITTED", effects["transactions"]
    assert effects["envelopes"] == [("COMPLETED", len(effects["entries"]))], effects
    assert len(effects["entries"]) > 0, effects
    assert len(effects["debts"]) == 1, effects["debts"]
    assert Decimal(effects["debts"][0][2]) == Decimal(amount), effects["debts"]


class _Stand:
    """One equivalent, one sender, one receiver, one trust line - committed for real.

    The `db_session` fixture wraps PostgreSQL tests in an outer transaction, so rows written
    through it are invisible to any other connection. These cells need two connections to see one
    another, so the stand owns its sessions and cleans up after itself.
    """

    def __init__(self, tag: str) -> None:
        nonce = uuid.uuid4().hex[:10]
        self.tag = tag
        self.tx_id = str(uuid.uuid4())
        self.equivalent_id = uuid.uuid4()
        self.sender_id = uuid.uuid4()
        self.receiver_id = uuid.uuid4()
        self.equivalent_code = f"{tag}{nonce}".upper()[:16]
        self.sender_pid = f"A_{tag}_{nonce}"
        self.receiver_pid = f"B_{tag}_{nonce}"

    async def seed(self) -> None:
        from app.db.models.equivalent import Equivalent
        from app.db.models.participant import Participant
        from app.db.models.trustline import TrustLine
        from tests.conftest import TestingSessionLocal

        async with TestingSessionLocal() as setup:
            setup.add_all(
                [
                    Equivalent(
                        id=self.equivalent_id,
                        code=self.equivalent_code,
                        description="T1523 acceptance cell",
                        precision=2,
                    ),
                    Participant(
                        id=self.sender_id,
                        pid=self.sender_pid,
                        display_name="A",
                        public_key=f"pk_A_{self.sender_pid}",
                        type="person",
                        status="active",
                    ),
                    Participant(
                        id=self.receiver_id,
                        pid=self.receiver_pid,
                        display_name="B",
                        public_key=f"pk_B_{self.receiver_pid}",
                        type="person",
                        status="active",
                    ),
                ]
            )
            await setup.commit()
            setup.add(
                TrustLine(
                    from_participant_id=self.receiver_id,
                    to_participant_id=self.sender_id,
                    equivalent_id=self.equivalent_id,
                    limit=Decimal("100.00"),
                    status="active",
                )
            )
            await setup.commit()

    async def session_at_application_isolation(self):
        """A session on the shared engine, with the isolation level asserted, not assumed."""

        from app.config import settings
        from tests.conftest import TestingSessionLocal

        session = TestingSessionLocal()
        isolation = (
            await session.execute(text("SHOW transaction_isolation"))
        ).scalar_one()
        assert str(isolation).upper() == settings.DB_POSTGRES_ISOLATION_LEVEL.upper(), (
            f"the stand runs at {isolation!r}, the application at "
            f"{settings.DB_POSTGRES_ISOLATION_LEVEL!r}: this cell would measure another "
            f"database's behaviour (T1549)"
        )
        return session

    async def cleanup(self) -> None:
        from app.db.models.audit_log import IntegrityAuditLog
        from app.db.models.equivalent import Equivalent
        from app.db.models.participant import Participant
        from app.db.models.prepare_lock import PrepareLock
        from app.db.models.transaction import Transaction
        from app.db.models.trustline import TrustLine
        from tests.conftest import TestingSessionLocal

        async with TestingSessionLocal() as cleanup:
            # The journal rows first, through the driver: `debt_operations.tx_id` RESTRICTs the
            # transaction row, and Core DML against `debts` is refused by the write guard.
            await purge_test_ledger(cleanup, equivalent_ids=[self.equivalent_id])
            await cleanup.execute(
                delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id == self.tx_id)
            )
            await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id == self.tx_id))
            await cleanup.execute(delete(Transaction).where(Transaction.tx_id == self.tx_id))
            await cleanup.execute(
                delete(TrustLine).where(TrustLine.equivalent_id == self.equivalent_id)
            )
            await cleanup.execute(
                delete(Participant).where(
                    Participant.id.in_([self.sender_id, self.receiver_id])
                )
            )
            await cleanup.execute(
                delete(Equivalent).where(Equivalent.id == self.equivalent_id)
            )
            await cleanup.commit()


async def _stop_tasks(tasks) -> None:
    live = [task for task in tasks if task is not None]
    if not live:
        return
    done, pending = await asyncio.wait(live, timeout=5.0)
    for task in pending:
        task.cancel()
    if pending:
        finished, still_pending = await asyncio.wait(pending, timeout=2.0)
        done.update(finished)
        assert not still_pending, "a payment worker did not stop after cancellation"
    for task in done:
        if not task.cancelled():
            task.exception()


@pytest.mark.asyncio
async def test_a_second_request_while_the_first_is_prepared_is_refused_in_progress_postgres(
    db_session, monkeypatch
):
    """Cell 3: the in-progress refusal at a state reached after prepare, and it writes nothing."""

    from app.config import settings
    from app.core.payments.service import PaymentService
    from app.db.models.transaction import Transaction
    from app.utils.exceptions import ConflictException
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 5000)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 30)

    stand = _Stand("P3")
    reached_commit = asyncio.Event()
    release_commit = asyncio.Event()
    winner_task = None
    winner_session = None
    second_session = None

    try:
        await stand.seed()
        winner_session = await stand.session_at_application_isolation()
        second_session = await stand.session_at_application_isolation()

        winner_service = PaymentService(winner_session)
        original_commit = winner_service.engine.commit

        async def _hold_after_prepare(tx_id_str, *, commit=True):
            reached_commit.set()
            await release_commit.wait()
            return await original_commit(tx_id_str, commit=commit)

        monkeypatch.setattr(winner_service.engine, "commit", _hold_after_prepare)

        async def _pay(service):
            try:
                return await service.create_payment_internal(
                    stand.sender_id,
                    to_pid=stand.receiver_pid,
                    equivalent=stand.equivalent_code,
                    amount="10.00",
                    idempotency_key=stand.tx_id,
                )
            except Exception as exc:
                return exc

        winner_task = asyncio.create_task(_pay(winner_service))
        await asyncio.wait_for(reached_commit.wait(), timeout=20.0)

        # PREMISE, on a third connection: the first payment is PREPARED. Without it this cell
        # would be the `NEW` case the inventory already had - the refusal is the same line of
        # code, but the state it refuses from is the point of the cell.
        async with TestingSessionLocal() as observer:
            state_while_held = (
                await observer.execute(
                    select(Transaction.state).where(Transaction.tx_id == stand.tx_id)
                )
            ).scalar_one()
            before = await _effects(observer, stand.tx_id, stand.equivalent_id)
        assert state_while_held == "PREPARED", state_while_held
        assert before["envelopes"] == [], before
        assert before["debts"] == [], before

        # ADVERSARIAL PREMISE, added after the first version of this cell passed without it.
        # The second request's refusal must come from the LOOKUP that precedes routing. Had its
        # SERIALIZABLE snapshot been taken before the winner's row was committed, the lookup would
        # have missed, the request would have routed, inserted, met `UNIQUE(tx_id)` and been
        # answered "in progress" by the insert-race handler instead - the same words, from the
        # other entrance, and this cell would have been a silent duplicate of the one below.
        # Routing is reached only when the lookup found nothing, so "build_graph never ran" is
        # exactly the discriminator.
        second_service = PaymentService(second_session)
        routed_after_lookup: list[str] = []
        second_build_graph = second_service.router.build_graph

        async def _note_routing(*args, **kwargs):
            routed_after_lookup.append("routed")
            return await second_build_graph(*args, **kwargs)

        monkeypatch.setattr(second_service.router, "build_graph", _note_routing)

        second_result = await asyncio.wait_for(_pay(second_service), timeout=20.0)

        assert routed_after_lookup == [], (
            "the second request routed, so its lookup missed the PREPARED row and the refusal "
            "came from the insert race, not from the in-progress branch this cell measures"
        )
        assert isinstance(second_result, ConflictException), second_result
        assert second_result.status_code == 409, second_result
        assert second_result.code == "E008", second_result
        assert "in progress" in second_result.message, second_result.message
        assert (second_result.details or {}).get("retryable") is not True, (
            "the in-progress refusal is not the retryable serialization conflict"
        )

        # The refusal moved nothing: same one row, still no journal, still no debt.
        async with TestingSessionLocal() as observer:
            assert await _effects(observer, stand.tx_id, stand.equivalent_id) == before, (
                "the refused second request touched the row, the debts or the journal"
            )

        release_commit.set()
        winner_result = await asyncio.wait_for(winner_task, timeout=30.0)
        assert not isinstance(winner_result, Exception), winner_result
        assert winner_result.status == "COMMITTED", winner_result

        # And the first payment did move money - once.
        async with TestingSessionLocal() as observer:
            _assert_one_committed_payment(
                await _effects(observer, stand.tx_id, stand.equivalent_id), "10.00"
            )
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_commit.set()
            await _stop_tasks([winner_task])
            for session in (winner_session, second_session):
                if session is not None:
                    await session.rollback()
                    await session.close()
            await stand.cleanup()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"T1523 cell 3 teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


@pytest.mark.asyncio
async def test_the_insert_race_at_serializable_leaves_one_payment_postgres(
    db_session, monkeypatch, request
):
    """Cell 5: the loser's own INSERT meets the winner's committed row.

    The branch is RECORDED, not assumed: the test reports which of the two defined answers the
    database produced, and asserts that one. Both are 409/E008; a 500 or a second effect would be
    a defect and fails here.
    """

    from app.config import settings
    from app.core.payments import service as payment_service_module
    from app.core.payments.service import PaymentService
    from app.utils.exceptions import ConflictException
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 5000)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 30)

    stand = _Stand("P5")
    loser_passed_lookup = asyncio.Event()
    release_loser = asyncio.Event()
    winner_row_committed = asyncio.Event()
    release_winner = asyncio.Event()
    loser_task = None
    winner_task = None
    loser_session = None
    winner_session = None

    # What the loser's insert actually hit. `_resolve_existing_payment` on the loser's own service
    # can only be reached from the IntegrityError handler here (its initial lookup found nothing,
    # which is why routing ran at all), and every database error that reaches the classifier is
    # recorded with its SQLSTATE.
    resolve_calls: list[str] = []
    classified: list[tuple[str, str | None]] = []
    original_classify = payment_service_module._classify_payment_db_error

    def _recording_classify(exc):
        classified.append(
            (type(exc).__name__, payment_service_module._payment_db_sqlstate(exc))
        )
        return original_classify(exc)

    monkeypatch.setattr(
        payment_service_module, "_classify_payment_db_error", _recording_classify
    )

    try:
        await stand.seed()
        loser_session = await stand.session_at_application_isolation()
        winner_session = await stand.session_at_application_isolation()

        loser_service = PaymentService(loser_session)
        winner_service = PaymentService(winner_session)

        loser_build_graph = loser_service.router.build_graph
        loser_resolve = loser_service._resolve_existing_payment
        winner_prepare = winner_service.engine.prepare

        async def _hold_loser_after_the_lookup(*args, **kwargs):
            graph = await loser_build_graph(*args, **kwargs)
            loser_passed_lookup.set()
            await release_loser.wait()
            return graph

        def _record_resolve(existing_tx, **kwargs):
            resolve_calls.append(str(existing_tx.state))
            return loser_resolve(existing_tx, **kwargs)

        loser_commit = loser_session.commit
        insert_failures: list[tuple[str, str | None, str | None]] = []

        async def _record_what_the_insert_hit():
            try:
                return await loser_commit()
            except Exception as exc:  # recorded, then re-raised for the service to handle
                orig = getattr(exc, "orig", None)
                insert_failures.append(
                    (
                        type(exc).__name__,
                        payment_service_module._payment_db_sqlstate(exc),
                        getattr(orig, "constraint_name", None)
                        or getattr(getattr(orig, "__cause__", None), "constraint_name", None),
                    )
                )
                raise

        monkeypatch.setattr(loser_session, "commit", _record_what_the_insert_hit)

        async def _hold_winner_before_prepare(*args, **kwargs):
            winner_row_committed.set()
            await release_winner.wait()
            return await winner_prepare(*args, **kwargs)

        monkeypatch.setattr(
            loser_service.router, "build_graph", _hold_loser_after_the_lookup
        )
        monkeypatch.setattr(loser_service, "_resolve_existing_payment", _record_resolve)
        monkeypatch.setattr(winner_service.engine, "prepare", _hold_winner_before_prepare)

        async def _pay(service):
            try:
                return await service.create_payment_internal(
                    stand.sender_id,
                    to_pid=stand.receiver_pid,
                    equivalent=stand.equivalent_code,
                    amount="10.00",
                    idempotency_key=stand.tx_id,
                )
            except Exception as exc:
                return exc

        loser_task = asyncio.create_task(_pay(loser_service))
        await asyncio.wait_for(loser_passed_lookup.wait(), timeout=20.0)

        # PREMISE 1: the loser reached routing, which is only reachable when its lookup found no
        # row - so the refusal below cannot have come from the lookup.
        assert resolve_calls == [], resolve_calls

        winner_task = asyncio.create_task(_pay(winner_service))
        await asyncio.wait_for(winner_row_committed.wait(), timeout=20.0)
        assert not winner_task.done()

        # PREMISE 2: the winner's row is committed and visible to a third connection BEFORE the
        # loser is released - that is what makes the loser's INSERT a race and not a lookup hit.
        async with TestingSessionLocal() as observer:
            during = await _effects(observer, stand.tx_id, stand.equivalent_id)
        assert len(during["transactions"]) == 1, during["transactions"]
        winner_row_id = during["transactions"][0][0]
        assert during["envelopes"] == [], during
        assert during["debts"] == [], during

        release_loser.set()
        loser_result = await asyncio.wait_for(loser_task, timeout=20.0)

        assert isinstance(loser_result, ConflictException), (
            f"the insert race answered {loser_result!r}; a 500 here would be a defect"
        )
        assert loser_result.status_code == 409, loser_result
        assert loser_result.code == "E008", loser_result

        # WHICH BRANCH. Recorded from the run, then asserted - the two are not interchangeable
        # for a caller: one says "wait for the payment you already sent", the other "send it
        # again".
        # PREMISE 3: the loser's INSERT really failed, and it failed for one of the two reasons
        # this race can produce at SERIALIZABLE. BOTH WERE OBSERVED on this stand, 2026-09-21:
        #
        #   ('IntegrityError', '23505', 'transactions_tx_id_key')  - running this module alone
        #   ('DBAPIError',     '40001', None)                       - in the full PostgreSQL tier
        #
        # which is why the brief forbids assuming one of them. `23505` is the unique index on
        # `tx_id` refusing a row that is already committed; `40001` is SSI refusing the whole
        # transaction, because the loser had READ the rows the winner then wrote (the router's
        # process-wide graph cache decides whether those reads happen at all, so the outcome
        # moves with what else has run in the process). A duplicate on any OTHER constraint, or
        # no failure at all, means this cell measured something it was not built for.
        assert insert_failures, (
            "the loser's insert never failed, so nothing here measured the race"
        )
        assert all(
            state == "40001"
            or state == "23505"
            or "unique" in (name or "").lower()
            for _type, state, name in insert_failures
        ), insert_failures

        details = loser_result.details or {}
        if resolve_calls:
            branch = f"IntegrityError -> re-read (stored state {resolve_calls[-1]})"
            assert any(
                state == "23505" or "unique" in (name or "").lower()
                for _type, state, name in insert_failures
            ), (
                "the policy was re-applied from the IntegrityError handler, but the insert did "
                f"not report a unique violation: {insert_failures!r}"
            )
            assert "in progress" in loser_result.message, loser_result.message
            assert details.get("retryable") is not True, details
        else:
            branch = "DBAPIError -> retryable conflict"
            assert any(
                state == "40001" for _type, state, _name in insert_failures
            ), insert_failures
            assert any(
                state == "40001" for _name, state in classified
            ), f"the retryable answer was not produced by a classified 40001: {classified!r}"
            assert details.get("retryable") is True, details
            assert details.get("conflict_kind") == "database_concurrency", details
        request.node.add_report_section(
            "call",
            "t1523-cell-5-branch",
            f"{branch}; insert_failures={insert_failures!r}; classified={classified!r}",
        )
        print(
            f"T1523 cell 5 branch: {branch}; insert_failures={insert_failures!r}; "
            f"classified={classified!r}"
        )

        # The losing INSERT left nothing behind: still the winner's row, and only it.
        async with TestingSessionLocal() as observer:
            after_race = await _effects(observer, stand.tx_id, stand.equivalent_id)
        assert after_race["transactions"] == [(winner_row_id, "NEW")], after_race["transactions"]
        assert after_race["envelopes"] == [], after_race

        release_winner.set()
        winner_result = await asyncio.wait_for(winner_task, timeout=30.0)
        assert not isinstance(winner_result, Exception), winner_result
        assert winner_result.status == "COMMITTED", winner_result

        # One transaction, one debt effect, one envelope - the property of the cell.
        async with TestingSessionLocal() as observer:
            final = await _effects(observer, stand.tx_id, stand.equivalent_id)
        _assert_one_committed_payment(final, "10.00")
        assert final["transactions"][0][0] == winner_row_id, final["transactions"]

        async with TestingSessionLocal() as observer:
            from app.db.models.prepare_lock import PrepareLock

            remaining_locks = (
                await observer.execute(
                    select(func.count())
                    .select_from(PrepareLock)
                    .where(PrepareLock.tx_id == stand.tx_id)
                )
            ).scalar_one()
        assert remaining_locks == 0, remaining_locks
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_loser.set()
            release_winner.set()
            await _stop_tasks([loser_task, winner_task])
            for session in (loser_session, winner_session):
                if session is not None:
                    await session.rollback()
                    await session.close()
            await stand.cleanup()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"T1523 cell 5 teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
