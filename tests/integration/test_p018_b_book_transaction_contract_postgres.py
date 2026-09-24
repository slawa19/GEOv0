"""018 stage B: `Book`'s transaction contract (spec "Контракт транзакции `Book.post`", `FORK-2`, `FORK-3`,
`FORK-6`), measured on PostgreSQL against the migrated schema.

What is held, item by item:

1. A FRESH session whose first statement is the book's works (the connection is taken before the
   transaction is checked); AUTOCOMMIT is refused before any write - both as SQLAlchemy knows it and,
   with that check substituted away, by the DRIVER's own report.
2. Nesting is refused in the same session, and through the transaction on a connection shared by a
   second session (`geo.operation_id` is already set); two operations on DIFFERENT connections run
   concurrently. A `Debt` pending before the operation is refused and is NOT flushed into it.
3. The envelope is COMPLETED, `schema_version = 2`, one entry per changed row with strictly increasing
   `ordinal`, `effect_count` by rows; an intent equivalent with no effects gets its membership row.
4. A failure inside leaves nothing - envelope, entries, debts, context - and the operation beside it
   in the same transaction commits (the accepted `C11` flip). `FORK-2`: a failure AFTER `COMPLETED`
   whose savepoint rollback succeeds is an ordinary refusal; one whose rollback FAILS makes the
   transaction unusable - the commit is refused and nothing, the sibling included, is durable.
6. An effect outside the declared scope is refused before it writes; a raw write outside it is refused
   at completion; `SEED` after a baseline is refused.

`FORK-3` (attribution): a raw write by another session on the SAME connection while an operation is
open is journalled under that operation - the narrow claim, not a defect - and `Book.current` of that
other session still refuses.

One clone of the migrated template per module; each test reads only its own world's rows.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.ledger.book as book_module
from app.core.ledger.book import (
    Book,
    BookError,
    NewDebt,
    PaymentFlow,
    Refusal,
    operation_for,
)
from app.core.ledger.reconciliation import take_baseline
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from tests.p018_support import (
    context_of,
    entries_of,
    envelopes_named,
    module_clone,
    seed_world,
    serializable_engine,
    sqlstate_of,
)


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018bbook") as url:
        yield url


@pytest.fixture
async def engine(migrated_url):
    built = serializable_engine(migrated_url)
    try:
        yield built
    finally:
        await built.dispose()


def _session(engine) -> AsyncSession:
    return AsyncSession(bind=engine, expire_on_commit=False, autoflush=False)


def _fixture(identity: str, **kwargs):
    return operation_for("TEST_FIXTURE", identity, {"contract": identity}, **kwargs)


async def _world(engine, participants: int = 3):
    async with _session(engine) as session:
        world = await seed_world(session, participants=participants)
        await session.commit()
    return world


async def _count(connection, sql: str, **params) -> int:
    return (await connection.execute(text(sql), params)).scalar_one()


# =================================================================================================
# Items 1, 3: a fresh session, the envelope, the entries, the membership
# =================================================================================================


@pytest.mark.asyncio
async def test_a_fresh_session_opens_completes_and_records_one_entry_per_row(engine) -> None:
    world = await _world(engine)
    second = await _world(engine, participants=0)
    identity = f"contract-fresh-{uuid.uuid4()}"

    async with _session(engine) as session:
        assert not session.in_transaction()  # the book's statement is the first one
        async with Book.operation(
            session, _fixture(identity, intent_equivalent_ids={second.eq})
        ):
            # I, then U, then a multi-row statement of two inserts.
            session.add(
                Debt(debtor_id=world.p(0), creditor_id=world.p(1), equivalent_id=world.eq,
                     amount=Decimal("10"))
            )
            await session.flush()
            await session.execute(
                text(
                    "UPDATE debts SET amount = 12 WHERE debtor_id = :d AND creditor_id = :c"
                ),
                {"d": world.p(0), "c": world.p(1)},
            )
            await session.execute(
                text(
                    "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                    "VALUES (:i1, :p1, :p2, :eq, 32, 0), (:i2, :p2, :p0, :eq, 33, 0)"
                ),
                {"i1": uuid.uuid4(), "i2": uuid.uuid4(), "p0": world.p(0), "p1": world.p(1),
                 "p2": world.p(2), "eq": world.eq},
            )
        await session.commit()

    async with engine.connect() as observer:
        [envelope] = await envelopes_named(observer, identity)
        assert (envelope.state, envelope.schema_version, envelope.effect_count) == (
            "COMPLETED", 2, 4,
        )
        entries = await entries_of(observer, envelope.id)
        assert [(row.effect, row.amount_before, row.amount_after, row.delta) for row in entries] == [
            ("I", None, Decimal("10.00000000"), Decimal("10.00000000")),
            ("U", Decimal("10.00000000"), Decimal("12.00000000"), Decimal("2.00000000")),
            ("I", None, Decimal("32.00000000"), Decimal("32.00000000")),
            ("I", None, Decimal("33.00000000"), Decimal("33.00000000")),
        ]
        ordinals = [row.ordinal for row in entries]
        assert ordinals == sorted(set(ordinals)), "ordinal must increase strictly within an operation"
        membership = (
            await observer.execute(
                text(
                    "SELECT equivalent_id, in_intent, in_scope, effect_count, length(effect_digest) "
                    "FROM debt_operation_equivalents WHERE operation_id = :op"
                ),
                {"op": envelope.id},
            )
        ).all()
        assert sorted((row[0] == world.eq, row[1], row[2], row[3], row[4]) for row in membership) == [
            (False, True, True, 0, 64),  # the intent equivalent with no effects
            (True, False, True, 4, 64),
        ]
        assert await context_of(observer) in (None, "")


@pytest.mark.asyncio
async def test_an_operation_with_no_effects_completes_with_zero(engine) -> None:
    world = await _world(engine, participants=0)
    identity = f"contract-empty-{uuid.uuid4()}"
    async with _session(engine) as session:
        await Book.post(session, _fixture(identity, intent_equivalent_ids={world.eq}), [])
        await session.commit()
    async with engine.connect() as observer:
        [envelope] = await envelopes_named(observer, identity)
        assert (envelope.state, envelope.effect_count) == ("COMPLETED", 0)
        assert (
            await _count(
                observer,
                "SELECT count(*) FROM debt_operation_equivalents WHERE operation_id = :op "
                "AND in_intent AND effect_count = 0",
                op=envelope.id,
            )
            == 1
        )


# =================================================================================================
# Item 1: AUTOCOMMIT, as configured and as the driver reports it
# =================================================================================================


@pytest.mark.asyncio
async def test_autocommit_is_refused_before_any_write_and_the_driver_check_stands_alone(
    migrated_url, engine, monkeypatch
) -> None:
    world = await _world(engine)
    autocommit = create_async_engine(migrated_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        identities = []
        for blind in (False, True):
            if blind:
                # SQLAlchemy's view substituted away: only the driver's report is left to refuse.
                monkeypatch.setattr(book_module, "_is_autocommit_configured", lambda conn: False)
            identity = f"contract-autocommit-{blind}-{uuid.uuid4()}"
            identities.append(identity)
            async with _session(autocommit) as session:
                with pytest.raises(BookError) as caught:
                    await Book.post(
                        session,
                        _fixture(identity),
                        [NewDebt(world.p(0), world.p(1), world.eq, Decimal("1"))],
                    )
                assert caught.value.reason == Refusal.AUTOCOMMIT_ROOT
        async with engine.connect() as observer:
            for identity in identities:
                assert await envelopes_named(observer, identity) == []
            assert (
                await _count(observer, "SELECT count(*) FROM debts WHERE equivalent_id = :eq",
                             eq=world.eq)
                == 0
            )
    finally:
        await autocommit.dispose()


# =================================================================================================
# Item 2: nesting, sharing, concurrency, pending debts
# =================================================================================================


@pytest.mark.asyncio
async def test_nesting_is_refused_in_the_session_and_through_a_shared_connection(engine) -> None:
    """And `FORK-3`: the second session's raw write is journalled under the first's operation."""

    world = await _world(engine)
    outer = f"contract-outer-{uuid.uuid4()}"
    async with engine.connect() as connection:
        await connection.begin()
        first = AsyncSession(bind=connection, autoflush=False, expire_on_commit=False)
        second = AsyncSession(bind=connection, autoflush=False, expire_on_commit=False)
        async with Book.operation(first, _fixture(outer)) as posting:
            await posting.apply(NewDebt(world.p(0), world.p(1), world.eq, Decimal("5")))
            await first.flush()

            with pytest.raises(BookError) as same_session:
                async with Book.operation(first, _fixture(f"inner-{uuid.uuid4()}")):
                    pass
            assert same_session.value.reason == Refusal.NESTED_OPERATION

            with pytest.raises(BookError) as shared:
                async with Book.operation(second, _fixture(f"shared-{uuid.uuid4()}")):
                    pass
            assert shared.value.reason == Refusal.NESTED_OPERATION
            with pytest.raises(BookError) as current:
                Book.current(second)
            assert current.value.reason == Refusal.NO_OPERATION

            # FORK-3: DML of this physical transaction while the context is open is this operation's.
            await second.execute(
                text("UPDATE debts SET amount = 6 WHERE debtor_id = :d AND creditor_id = :c"),
                {"d": world.p(0), "c": world.p(1)},
            )
            operation_id = posting.operation_id
        await connection.commit()

    async with engine.connect() as observer:
        [envelope] = await envelopes_named(observer, outer)
        assert envelope.id == operation_id and envelope.state == "COMPLETED"
        entries = await entries_of(observer, operation_id)
        assert [(row.effect, row.delta) for row in entries] == [
            ("I", Decimal("5.00000000")),
            ("U", Decimal("1.00000000")),
        ]


@pytest.mark.asyncio
async def test_two_operations_on_different_connections_run_at_the_same_time(engine) -> None:
    """The book's ownership is per session: two operations open AT ONCE on two connections are not
    refused by it (a per-process flag would refuse the second).

    WHAT THE DATABASE MAY STILL DO, measured and not new: at SERIALIZABLE two operations whose windows
    overlap read and write the same journal index pages, and PostgreSQL may cancel one with `40001`
    ("identification as a pivot"). Measured 5 of 5 on `2fb1056` with the LISTENER journal and two
    overlapping `debt_fixture_setup` blocks - so it predates stage B, and the application's answer is
    the unit-of-work retry. The workers here retry the same way; what may not happen is a `BookError`.
    """

    world = await _world(engine, participants=4)
    identities = [f"contract-concurrent-{index}-{uuid.uuid4()}" for index in range(2)]
    both_open = asyncio.Barrier(2)
    retried: list[int] = []

    async def one(index: int) -> None:
        for attempt in range(5):
            try:
                async with _session(engine) as session:
                    async with Book.operation(session, _fixture(identities[index])) as posting:
                        if attempt == 0:
                            await both_open.wait()  # both envelopes are OPEN at this point
                        await posting.apply(
                            NewDebt(world.p(2 * index), world.p(2 * index + 1), world.eq,
                                    Decimal("2"))
                        )
                    await session.commit()
                return
            except DBAPIError as exc:
                if sqlstate_of(exc) != "40001":
                    raise
                retried.append(index)
        raise AssertionError(f"worker {index} never committed")

    await asyncio.gather(one(0), one(1))
    async with engine.connect() as observer:
        for identity in identities:
            assert [row.state for row in await envelopes_named(observer, identity)] == ["COMPLETED"]
    assert len(retried) <= 4, retried


@pytest.mark.asyncio
async def test_a_debt_pending_before_the_operation_is_refused_and_not_flushed_into_it(engine) -> None:
    world = await _world(engine)
    identity = f"contract-pending-{uuid.uuid4()}"
    async with _session(engine) as session:
        session.add(
            Debt(debtor_id=world.p(0), creditor_id=world.p(1), equivalent_id=world.eq,
                 amount=Decimal("4"))
        )
        with pytest.raises(BookError) as caught:
            async with Book.operation(session, _fixture(identity)):
                pass
        assert caught.value.reason == Refusal.INCOMPLETE_DEBT
        connection = await session.connection()
        # Checked BEFORE `begin_nested()`, which would have flushed the pending debt.
        assert (
            await _count(connection, "SELECT count(*) FROM debts WHERE equivalent_id = :eq",
                         eq=world.eq)
            == 0
        )
        assert await envelopes_named(connection, identity) == []
        await session.rollback()


# =================================================================================================
# Item 4 and FORK-2: failures leave nothing; a failed rollback leaves an unusable transaction
# =================================================================================================


async def _sibling_then(session, world, *, sibling: str) -> None:
    await Book.post(
        session, _fixture(sibling), [NewDebt(world.p(0), world.p(1), world.eq, Decimal("12"))]
    )


@pytest.mark.asyncio
async def test_a_failure_inside_leaves_nothing_and_the_sibling_operation_commits(engine) -> None:
    world = await _world(engine)
    sibling, failed = f"contract-sibling-{uuid.uuid4()}", f"contract-failed-{uuid.uuid4()}"
    async with _session(engine) as session:
        await _sibling_then(session, world, sibling=sibling)
        with pytest.raises(RuntimeError, match="business"):
            async with Book.operation(session, _fixture(failed)) as posting:
                await posting.apply(NewDebt(world.p(1), world.p(2), world.eq, Decimal("3")))
                await session.flush()
                raise RuntimeError("business refusal inside the block")
        connection = await session.connection()
        assert await context_of(connection) == ""
        await session.commit()

    async with engine.connect() as observer:
        assert [row.state for row in await envelopes_named(observer, sibling)] == ["COMPLETED"]
        assert await envelopes_named(observer, failed) == []
        amounts = (
            await observer.execute(
                text("SELECT amount FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
            )
        ).scalars().all()
        assert amounts == [Decimal("12.00000000")]


@pytest.mark.asyncio
async def test_fork2_a_failure_after_completed_whose_rollback_succeeds_is_an_ordinary_refusal(
    engine, monkeypatch
) -> None:
    world = await _world(engine)
    sibling, failed = f"fork2-sibling-{uuid.uuid4()}", f"fork2-after-{uuid.uuid4()}"
    real_clear = book_module._clear_context
    seen_completed: list[str] = []

    async def failing_clear(async_conn):
        # Fires AFTER the COMPLETED update: prove it, then fail the context clearing.
        seen_completed.append(
            (
                await async_conn.execute(
                    text("SELECT state FROM debt_operations WHERE identity = :i"), {"i": failed}
                )
            ).scalar_one()
        )
        raise RuntimeError("clearing the context failed after COMPLETED")

    async with _session(engine) as session:
        await _sibling_then(session, world, sibling=sibling)
        monkeypatch.setattr(book_module, "_clear_context", failing_clear)
        with pytest.raises(RuntimeError, match="after COMPLETED"):
            await Book.post(
                session, _fixture(failed), [NewDebt(world.p(1), world.p(2), world.eq, Decimal("3"))]
            )
        monkeypatch.setattr(book_module, "_clear_context", real_clear)
        assert seen_completed == ["COMPLETED"]
        await session.commit()  # the rollback succeeded: an ordinary refusal, the sibling commits

    async with engine.connect() as observer:
        assert [row.state for row in await envelopes_named(observer, sibling)] == ["COMPLETED"]
        assert await envelopes_named(observer, failed) == []


@pytest.mark.asyncio
async def test_fork2_a_failed_rollback_after_completed_makes_the_transaction_unusable(
    engine, monkeypatch
) -> None:
    """The case `FORK-2` exists for: COMPLETED is written, the context clearing fails, and the
    savepoint rollback fails too. The deferred check would let a COMPLETED envelope commit, so the
    book must end the transaction itself: the original exception reaches the caller (the rollback
    failure attached to it), the commit is refused, and NOTHING of the transaction is durable - the
    sibling operation included."""

    world = await _world(engine)
    sibling, failed = f"fork2u-sibling-{uuid.uuid4()}", f"fork2u-after-{uuid.uuid4()}"

    async def failing_clear(async_conn):
        raise RuntimeError("clearing the context failed after COMPLETED")

    async def failing_rollback(nested):
        raise RuntimeError("ROLLBACK TO SAVEPOINT failed")

    async with _session(engine) as session:
        await _sibling_then(session, world, sibling=sibling)
        monkeypatch.setattr(book_module, "_clear_context", failing_clear)
        monkeypatch.setattr(book_module, "_roll_back_savepoint", failing_rollback)
        with pytest.raises(RuntimeError, match="after COMPLETED") as caught:
            await Book.post(
                session, _fixture(failed), [NewDebt(world.p(1), world.p(2), world.eq, Decimal("3"))]
            )
        assert isinstance(caught.value.book_rollback_error, RuntimeError)
        assert any("ROLLBACK TO SAVEPOINT failed" in note for note in caught.value.__notes__)
        with pytest.raises((PendingRollbackError, DBAPIError)):
            await session.commit()
        await session.rollback()

    async with engine.connect() as observer:
        assert await envelopes_named(observer, sibling) == []
        assert await envelopes_named(observer, failed) == []
        assert (
            await _count(observer, "SELECT count(*) FROM debts WHERE equivalent_id = :eq",
                         eq=world.eq)
            == 0
        )


@pytest.mark.asyncio
async def test_a_failed_envelope_insert_reaches_the_caller_unchanged(engine) -> None:
    """The first write of the operation fails (a duplicate identity, `23505`): the caller sees that
    exact SQLSTATE - the retry predicate reads it - and the transaction is usable after the book's
    rollback."""

    world = await _world(engine)
    identity = f"contract-duplicate-{uuid.uuid4()}"
    async with _session(engine) as session:
        await Book.post(session, _fixture(identity), [])
        with pytest.raises(DBAPIError) as caught:
            await Book.post(
                session, _fixture(identity), [NewDebt(world.p(0), world.p(1), world.eq, Decimal("1"))]
            )
        assert sqlstate_of(caught.value) == "23505"
        connection = await session.connection()
        assert await context_of(connection) in (None, "")
        await session.commit()
    async with engine.connect() as observer:
        assert [row.effect_count for row in await envelopes_named(observer, identity)] == [0]


# =================================================================================================
# Item 6: scope and the baseline
# =================================================================================================


@pytest.mark.asyncio
async def test_an_out_of_scope_effect_is_refused_before_it_writes_and_a_raw_one_at_completion(
    engine,
) -> None:
    world = await _world(engine)
    other = await _world(engine, participants=0)
    async with _session(engine) as session:
        tx_ids = [f"SCOPE-{uuid.uuid4()}" for _ in range(2)]
        session.add_all(
            Transaction(tx_id=tx_id, type="PAYMENT", initiator_id=world.p(0), payload={},
                        state="NEW")
            for tx_id in tx_ids
        )
        await session.commit()

        def payment(tx_id):
            return operation_for(
                "PAYMENT", tx_id, {"tx_id": tx_id}, tx_id=tx_id,
                scope_equivalent_ids={world.eq}, intent_equivalent_ids={world.eq},
            )

        with pytest.raises(BookError) as before_write:
            await Book.post(
                session, payment(tx_ids[0]),
                [PaymentFlow(world.p(0), world.p(1), Decimal("1"), other.eq)],
            )
        assert before_write.value.reason == Refusal.OUT_OF_SCOPE

        with pytest.raises(BookError) as at_completion:
            async with Book.operation(session, payment(tx_ids[1])):
                await session.execute(
                    text(
                        "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, "
                        "version) VALUES (:id, :d, :c, :eq, 1, 0)"
                    ),
                    {"id": uuid.uuid4(), "d": world.p(0), "c": world.p(1), "eq": other.eq},
                )
        assert at_completion.value.reason == Refusal.OUT_OF_SCOPE
        await session.commit()

    async with engine.connect() as observer:
        for tx_id in tx_ids:
            assert await envelopes_named(observer, tx_id) == []
        assert (
            await _count(observer, "SELECT count(*) FROM debts WHERE equivalent_id = :eq",
                         eq=other.eq)
            == 0
        )


@pytest.mark.asyncio
async def test_a_seed_after_the_baseline_is_refused_at_completion(engine) -> None:
    world = await _world(engine)
    async with _session(engine) as session:
        await take_baseline(session, world.eq)
        await session.commit()
        identity = f"contract-seed-{uuid.uuid4()}"
        with pytest.raises(BookError) as caught:
            await Book.post(
                session,
                operation_for("SEED", identity, {"seed": True}),
                [NewDebt(world.p(0), world.p(1), world.eq, Decimal("1"))],
            )
        assert caught.value.reason == Refusal.UNVERIFIABLE_WRITER_AFTER_BASELINE
        assert caught.value.context["equivalent_ids"] == [str(world.eq)]
        await session.commit()
    async with engine.connect() as observer:
        assert await envelopes_named(observer, identity) == []
        assert (
            await _count(observer, "SELECT count(*) FROM debts WHERE equivalent_id = :eq",
                         eq=world.eq)
            == 0
        )
