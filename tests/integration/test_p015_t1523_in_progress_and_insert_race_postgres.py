"""T1523 cells 3 and 5 on PostgreSQL at the application's isolation level.

WHY THESE ARE HERE AND NOT IN `test_payment_idempotency_postgres.py`. That module pins its sessions to
READ COMMITTED and says so; after `T1549` the shared test engine runs at
`settings.DB_POSTGRES_ISOLATION_LEVEL` - SERIALIZABLE, what the application actually runs at - and the
cell below takes its sessions from it unchanged. Every assertion is preceded by a premise that the
intended situation really occurred, because a duplicate-payment test that silently serialised into two
sequential payments would otherwise be green.

WHAT PROGRAMME 019 STAGE 3 (`T1904`) CHANGED. A payment is ONE transaction: its row is inserted inside
it and becomes visible only with its single commit. The two cells collapse into one schedule:

* Cell 3 was "a second request while the first is PREPARED is refused 409 `in progress`". No request
  can see a PREPARED (or NEW) row any more, so there is no in-progress answer to give. Its
  reformulation (spec, Verification plan §3 (В), "Идентичность tx_id"): the concurrent duplicate
  WAITS on the first one's uncommitted unique-index entry and, once the first commits, gets the
  first's result. That is the test below.
* Cell 5 was "the loser's INSERT meets the winner's COMMITTED `NEW` row". Its precondition - a
  committed `NEW` row of a payment still in flight - no longer exists, so the test that built it is
  removed with the contract it measured. The race it guarded is the schedule below. What stays open
  is `T1905`'s: the exact identity resolver (only `transactions_tx_id_key`, a new transaction, type /
  initiator / fingerprint compared; no row - one new attempt) and its staged variant (§1 №5).

WHAT THE CELL DOES NOT CLAIM. It measures the request-level idempotency path. The engine-level
duplicate commit is covered at `test_payment_commit_advisory_locks_postgres.py` and is not touched here.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text


# Every test here commits through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop and nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


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
async def test_a_concurrent_duplicate_waits_for_the_first_and_gets_its_result_postgres(
    db_session, monkeypatch, request
):
    """Cell 3 since 019 stage 3: the duplicate waits on the index and gets the first's result.

    The first request is held inside its transaction after prepare - its row inserted, not committed.
    The second request with the same `tx_id` finds no row, routes, inserts, and must WAIT on the
    first's unique-index entry (premise, measured in `pg_locks`). When the first commits, the second
    is answered with the first's stored result - by retrying on a fresh snapshot after the `40001`
    SSI raises for a key it had read, or by re-reading after a `23505`; which one is recorded, both
    end the same - and the payment's effects exist once.
    """

    from app.config import settings
    from app.core.payments import service as payment_service_module
    from app.core.payments.service import PaymentService
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 5000)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 30)

    stand = _Stand("P3")
    reached_commit = asyncio.Event()
    release_commit = asyncio.Event()
    winner_task = second_task = None
    winner_session = second_session = None
    classified: list[tuple[str, str | None]] = []
    original_classify = payment_service_module._classify_payment_db_error

    def _recording_classify(exc):
        classified.append((type(exc).__name__, payment_service_module._payment_db_sqlstate(exc)))
        return original_classify(exc)

    monkeypatch.setattr(payment_service_module, "_classify_payment_db_error", _recording_classify)

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

        # PREMISE: nothing of the first payment is visible to another transaction while it is held.
        async with TestingSessionLocal() as observer:
            before = await _effects(observer, stand.tx_id, stand.equivalent_id)
        assert before == {"debts": [], "transactions": [], "envelopes": [], "entries": []}, before

        second_task = asyncio.create_task(_pay(PaymentService(second_session)))
        # PREMISE: the second request's insert queues on the first one's uncommitted key.
        assert await _a_backend_waits_on_a_transaction(TestingSessionLocal), (
            "the second request did not wait on the first one's uncommitted row"
        )
        assert not second_task.done()

        release_commit.set()
        winner_result = await asyncio.wait_for(winner_task, timeout=30.0)
        second_result = await asyncio.wait_for(second_task, timeout=30.0)
        assert not isinstance(winner_result, Exception), winner_result
        assert winner_result.status == "COMMITTED", winner_result
        assert not isinstance(second_result, Exception), (
            f"the duplicate was answered {second_result!r}, not with the first one's result"
        )
        assert second_result.status == "COMMITTED", second_result
        assert str(second_result.tx_id) == stand.tx_id

        branch = (
            "40001 -> pay() retried on a fresh snapshot -> stored result"
            if any(state == "40001" for _name, state in classified)
            else "23505 -> re-read -> stored result"
        )
        request.node.add_report_section("call", "t1523-cell-3-branch", f"{branch}; {classified!r}")
        print(f"T1523 cell 3 branch: {branch}; classified={classified!r}")

        async with TestingSessionLocal() as observer:
            _assert_one_committed_payment(
                await _effects(observer, stand.tx_id, stand.equivalent_id), "10.00"
            )
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
            release_commit.set()
            await _stop_tasks([winner_task, second_task])
            for session in (winner_session, second_session):
                if session is not None:
                    await session.rollback()
                    await session.close()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"T1523 cell 3 teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


async def _a_backend_waits_on_a_transaction(sessionmaker, *, timeout: float = 10.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with sessionmaker() as observer:
        while True:
            waiting = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted "
                    "AND locktype = 'transactionid')"
                )
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)
