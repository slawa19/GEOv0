"""018 `T1803`: the three journal tables are guarded by the database, and an OPEN envelope cannot commit.

Spec 018, "Охрана трёх журнальных таблиц", "Как отличается вставка записи журнала от прямой",
"Отложенная проверка завершения". What each test holds:

* THE DEPTH RULE (mandatory test, spec): a direct `INSERT` into `debt_journal_entries` is refused
  EVEN WITH a valid OPEN context - the guard runs it at `pg_trigger_depth() = 1` - while the entry the
  `debts` trigger writes (depth 2) passes. A guard written with `pg_trigger_depth() > 0` would pass
  the direct INSERT, because the guard itself already runs at depth 1.
* The rest of the table in the spec: entries never updated or deleted; membership inserted only inside
  its own OPEN operation, never updated or deleted; an envelope inserted OPEN only, updated only
  OPEN -> COMPLETED with its declaration unchanged, never deleted; no TRUNCATE of any of them.
* THE DEFERRED COMPLETION CHECK re-reads the envelope's final state at COMMIT - four outcomes:
  completed (commits), abandoned OPEN (refused, nothing durable, the backend left idle), rolled back
  with a caller's savepoint (commits, no trace), and a cancellation whose savepoint rollback was
  PREVENTED (the commit is refused by this check - the last line under `Book`'s own contract).

Everything runs on ONE clone of the MIGRATED template shared by the module: statement-level refusals
inside a transaction that is rolled back, the commit outcomes with real commits whose rows each test
reads by its own equivalent - the clone is dropped when the module ends.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.ledger.book as book_module
from app.core.ledger.book import Book, NewDebt, operation_for
from app.db.models.debt import Debt
from tests.p018_support import (
    GUARD,
    context_of,
    entries_of,
    envelopes_named,
    module_clone,
    refused,
    rolled_back_session,
    seed_world,
    serializable_engine,
    sqlstate_of,
)


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018b1803") as url:
        yield url


def _fixture(identity: str, **kwargs):
    return operation_for("TEST_FIXTURE", identity, {"t1803": identity}, **kwargs)


async def _set_context(connection, operation_id) -> None:
    await connection.execute(
        text("SELECT set_config('geo.operation_id', :id, true)"), {"id": str(operation_id)}
    )


async def _insert_open_envelope(connection) -> uuid.UUID:
    operation_id = uuid.uuid4()
    await connection.execute(
        text(
            "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
            "schema_version, money_encoding_version, intent_encoding_version, state) "
            "VALUES (:id, 'TEST_FIXTURE', :identity, '{}', :digest, 2, 1, 1, 'OPEN')"
        ),
        {"id": operation_id, "identity": f"t1803/{operation_id}", "digest": "0" * 64},
    )
    return operation_id


def _entry_insert(operation_id, world, *, ordinal: int = 1) -> str:
    return (
        "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, debtor_id, "
        "creditor_id, effect, amount_before, amount_after, delta) VALUES "
        f"('{uuid.uuid4()}', '{operation_id}', {ordinal}, '{world.eq}', '{world.p(0)}', "
        f"'{world.p(1)}', 'I', NULL, 5, 5)"
    )


@pytest.mark.asyncio
async def test_t1803_a_direct_entry_insert_is_refused_even_with_a_valid_open_context(
    migrated_url,
) -> None:
    """The depth rule, both halves, on one OPEN envelope and one context."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world = await seed_world(session)
        operation_id = await _insert_open_envelope(connection)
        await _set_context(connection, operation_id)

        # Refused: a well-shaped entry the debts trigger never wrote.
        assert await refused(connection, _entry_insert(operation_id, world)) == GUARD
        assert await entries_of(connection, operation_id) == []

        # Admitted: the same operation's entry, written by the debts trigger at depth 2.
        await connection.exec_driver_sql(
            "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) VALUES "
            f"('{uuid.uuid4()}', '{world.p(0)}', '{world.p(1)}', '{world.eq}', 5, 0)"
        )
        entries = await entries_of(connection, operation_id)
        assert [(row.effect, row.amount_after, row.delta) for row in entries] == [
            ("I", Decimal("5.00000000"), Decimal("5.00000000"))
        ]


@pytest.mark.asyncio
async def test_t1803_entries_and_membership_are_never_rewritten_and_no_journal_table_truncates(
    migrated_url,
) -> None:
    async with rolled_back_session(migrated_url) as (connection, session):
        world = await seed_world(session)
        await Book.post(
            session,
            _fixture(f"t1803-seed-{uuid.uuid4()}"),
            [NewDebt(world.p(0), world.p(1), world.eq, Decimal("7.00"))],
        )
        operation_id = (
            await connection.execute(
                text(
                    "SELECT operation_id FROM debt_journal_entries WHERE equivalent_id = :eq"
                ),
                {"eq": world.eq},
            )
        ).scalar_one()
        # Even under that operation's own (re-set) context.
        await _set_context(connection, operation_id)
        for statement in (
            f"UPDATE debt_journal_entries SET delta = 8, amount_after = 8 WHERE operation_id = "
            f"'{operation_id}'",
            f"DELETE FROM debt_journal_entries WHERE operation_id = '{operation_id}'",
            f"UPDATE debt_operation_equivalents SET effect_count = 0 WHERE operation_id = "
            f"'{operation_id}'",
            f"DELETE FROM debt_operation_equivalents WHERE operation_id = '{operation_id}'",
        ):
            assert await refused(connection, statement) == GUARD, statement
        entries = await entries_of(connection, operation_id)
        assert [(row.effect, row.delta) for row in entries] == [("I", Decimal("7.00000000"))]

    # TRUNCATE, in a transaction with no pending deferred events (see the T1801 module).
    async with rolled_back_session(migrated_url) as (connection, _session):
        for table in ("debt_journal_entries", "debt_operation_equivalents", "debt_operations"):
            assert await refused(connection, f"TRUNCATE {table} CASCADE") == GUARD, table


@pytest.mark.asyncio
async def test_t1803_membership_is_written_only_inside_its_own_open_operation(migrated_url) -> None:
    async with rolled_back_session(migrated_url) as (connection, session):
        world = await seed_world(session)
        mine = await _insert_open_envelope(connection)
        other = await _insert_open_envelope(connection)

        def row(operation_id) -> str:
            return (
                "INSERT INTO debt_operation_equivalents (operation_id, equivalent_id, in_intent, "
                f"in_scope, effect_count, effect_digest) VALUES ('{operation_id}', '{world.eq}', "
                f"true, true, 0, '{'0' * 64}')"
            )

        assert await refused(connection, row(mine)) == GUARD  # no context at all
        await _set_context(connection, other)
        assert await refused(connection, row(mine)) == GUARD  # another operation's context
        await _set_context(connection, mine)
        assert await refused(connection, row(mine)) is None  # its own OPEN operation: admitted


@pytest.mark.asyncio
async def test_t1803_an_envelope_is_born_open_completes_once_and_is_never_deleted(
    migrated_url,
) -> None:
    async with rolled_back_session(migrated_url) as (connection, session):
        # Born COMPLETED: refused.
        assert (
            await refused(
                connection,
                "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
                "schema_version, money_encoding_version, intent_encoding_version, state, "
                "completed_at, effect_count, effect_digest) VALUES "
                f"('{uuid.uuid4()}', 'TEST_FIXTURE', 'born-completed-{uuid.uuid4()}', '{{}}', "
                f"'{'0' * 64}', 2, 1, 1, 'COMPLETED', now(), 0, '{'0' * 64}')",
            )
            == GUARD
        )

        operation_id = await _insert_open_envelope(connection)
        complete = (
            "UPDATE debt_operations SET state = 'COMPLETED', completed_at = now(), "
            f"effect_count = 0, effect_digest = '{'0' * 64}'{{extra}} WHERE id = '{operation_id}'"
        )
        # OPEN -> COMPLETED with the declaration changed: refused, field by field.
        for extra in (
            ", kind = 'SEED'",
            ", identity = 'renamed'",
            f", intent_digest = '{'1' * 64}'",
            """, intent = '{"x": 1}'""",
            ", intent_encoding_version = 2",
            ", opened_at = now() - interval '1 day'",
        ):
            assert await refused(connection, complete.format(extra=extra)) == GUARD, extra
        # The honest completion passes.
        assert await refused(connection, complete.format(extra="")) is None
        await connection.exec_driver_sql(complete.format(extra=""))
        # Once COMPLETED, nothing moves it and nothing deletes it.
        for statement in (
            f"UPDATE debt_operations SET state = 'OPEN', completed_at = NULL, effect_count = NULL, "
            f"effect_digest = NULL WHERE id = '{operation_id}'",
            f"UPDATE debt_operations SET effect_count = 5 WHERE id = '{operation_id}'",
            f"DELETE FROM debt_operations WHERE id = '{operation_id}'",
        ):
            assert await refused(connection, statement) == GUARD, statement
        state = (
            await connection.execute(
                text("SELECT state FROM debt_operations WHERE id = :id"), {"id": operation_id}
            )
        ).scalar_one()
        assert state == "COMPLETED"


# =================================================================================================
# The deferred completion check: four outcomes at a real COMMIT
# =================================================================================================


async def _world_committed(factory):
    async with factory() as session:
        world = await seed_world(session)
        await session.commit()
    return world


def _factory(engine):
    return lambda: AsyncSession(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.mark.asyncio
async def test_t1803_deferred_normal_and_caller_savepoint_rollback_both_commit(
    migrated_url,
) -> None:
    """Normal: a completed operation commits. Rolled back with a caller's savepoint: no trace, and
    the operation beside it in the same transaction commits."""

    engine = serializable_engine(migrated_url)
    factory = _factory(engine)
    try:
        world = await _world_committed(factory)
        kept, dropped = f"t1803-kept-{uuid.uuid4()}", f"t1803-dropped-{uuid.uuid4()}"
        async with factory() as session:
            await Book.post(session, _fixture(kept), [NewDebt(world.p(0), world.p(1), world.eq, Decimal("3"))])
            savepoint = await session.begin_nested()
            await Book.post(
                session, _fixture(dropped), [NewDebt(world.p(1), world.p(2), world.eq, Decimal("4"))]
            )
            await savepoint.rollback()
            await session.commit()

        async with engine.connect() as observer:
            assert [row.state for row in await envelopes_named(observer, kept)] == ["COMPLETED"]
            assert await envelopes_named(observer, dropped) == []
            amounts = (
                await observer.execute(
                    text("SELECT amount FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
                )
            ).scalars().all()
            assert amounts == [Decimal("3.00000000")]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_t1803_deferred_an_abandoned_open_envelope_refuses_the_commit_and_leaves_nothing(
    migrated_url,
) -> None:
    """Abandoned OPEN: the commit is refused by the constraint trigger, nothing of the transaction is
    durable - the debt written under that envelope included - and the backend is left idle."""

    engine = serializable_engine(migrated_url)
    factory = _factory(engine)
    try:
        world = await _world_committed(factory)
        async with engine.connect() as connection:
            pid = (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            operation_id = await _insert_open_envelope(connection)
            await _set_context(connection, operation_id)
            await connection.exec_driver_sql(
                "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                f"VALUES ('{uuid.uuid4()}', '{world.p(0)}', '{world.p(1)}', '{world.eq}', 6, 0)"
            )
            with pytest.raises(DBAPIError) as caught:
                await connection.commit()
            assert sqlstate_of(caught.value) == GUARD
            assert "still OPEN at commit" in str(caught.value)

            async with engine.connect() as observer:
                state = (
                    await observer.execute(
                        text("SELECT state FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
                    )
                ).scalar_one()
                assert state == "idle"
                assert (
                    await observer.execute(
                        text("SELECT count(*) FROM debt_operations WHERE id = :id"),
                        {"id": operation_id},
                    )
                ).scalar_one() == 0
                assert (
                    await observer.execute(
                        text("SELECT count(*) FROM debts WHERE equivalent_id = :eq"),
                        {"eq": world.eq},
                    )
                ).scalar_one() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_t1803_deferred_a_cancelled_operation_leaves_nothing_and_a_prevented_rollback_cannot_commit(
    migrated_url, monkeypatch
) -> None:
    """Cancellation, twice.

    1. The book's own path: a task cancelled inside the block - `Book` rolls its savepoint back, the
       caller's commit goes through and carries nothing of the cancelled operation.
    2. The last line: the same cancellation with the savepoint rollback PREVENTED and the book's
       invalidation disabled too (both substituted in the book module, which is what a neighbour that
       swallowed the rollback would amount to). The envelope is still OPEN at COMMIT, and the deferred
       check refuses it: the debt written inside does not become durable.
    """

    engine = serializable_engine(migrated_url)
    factory = _factory(engine)
    try:
        world = await _world_committed(factory)

        async def cancelled_operation(session, identity, entered: asyncio.Event):
            async with Book.operation(session, _fixture(identity)):
                session.add(
                    Debt(
                        debtor_id=world.p(0), creditor_id=world.p(1), equivalent_id=world.eq,
                        amount=Decimal("9"),
                    )
                )
                await session.flush()
                entered.set()
                await asyncio.sleep(3600)

        async def run(identity: str) -> None:
            async with factory() as session:
                entered = asyncio.Event()
                task = asyncio.create_task(cancelled_operation(session, identity, entered))
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                try:
                    await session.commit()
                except DBAPIError as exc:
                    raise _Refused(exc) from exc

        # 1. The book's own path.
        first = f"t1803-cancel-{uuid.uuid4()}"
        await run(first)
        async with engine.connect() as observer:
            assert await envelopes_named(observer, first) == []
            assert (
                await observer.execute(
                    text("SELECT count(*) FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
                )
            ).scalar_one() == 0

        # 2. The rollback prevented, the invalidation disabled: the deferred check is what is left.
        async def no_rollback(nested):
            raise RuntimeError("savepoint rollback prevented by the stand")

        async def no_invalidation(async_conn, original):
            return None

        monkeypatch.setattr(book_module, "_roll_back_savepoint", no_rollback)
        monkeypatch.setattr(book_module, "_make_unusable", no_invalidation)
        second = f"t1803-cancel-prevented-{uuid.uuid4()}"
        with pytest.raises(_Refused) as refused_commit:
            await run(second)
        assert sqlstate_of(refused_commit.value.error) == GUARD
        assert "still OPEN at commit" in str(refused_commit.value.error)
        async with engine.connect() as observer:
            assert await envelopes_named(observer, second) == []
            assert (
                await observer.execute(
                    text("SELECT count(*) FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()


class _Refused(Exception):
    def __init__(self, error: BaseException) -> None:
        super().__init__(str(error))
        self.error = error


@pytest.mark.asyncio
async def test_t1803_the_context_is_empty_after_every_operation_and_every_failure(migrated_url) -> None:
    """Contract items 3-4: `geo.operation_id` is cleared on success and restored by the rollback."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world = await seed_world(session)
        await Book.post(
            session, _fixture(f"t1803-ok-{uuid.uuid4()}"),
            [NewDebt(world.p(0), world.p(1), world.eq, Decimal("1"))],
        )
        assert await context_of(connection) == ""
        with pytest.raises(RuntimeError, match="business failure"):
            async with Book.operation(session, _fixture(f"t1803-fail-{uuid.uuid4()}")):
                raise RuntimeError("business failure")
        assert await context_of(connection) == ""
