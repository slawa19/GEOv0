"""Programme 015, T1528 on PostgreSQL: the halves that are about the driver, not about the rule.

WHAT IS HERE AND WHY IT CANNOT BE ON THE SQLITE TIER. The rules themselves - read the statement's own
values, read the amount by name, read the row back - are tier-independent and are measured in
`tests/unit/test_p015_t1528_the_guard_reads_what_the_statement_writes.py`. Three things in them are
NOT tier-independent, and this programme has already been bitten by each:

* UUID SPELLING. `Uuid(as_uuid=True)` is 32 hex characters on SQLite and a native `uuid` type on
  PostgreSQL, and two counterexamples in this programme went falsely green because a comparison was
  written against one tier's spelling. The edge read out of a statement's own `_values` - through
  `literal(other.id)` - and the edge read back out of `debts` are both such comparisons.
* WHAT THE DRIVER RETURNS. The readback compares the stored amount with the recorded one. asyncpg
  returns `NUMERIC` as `Decimal` untouched, and full-width money (twelve integer digits) only exists
  on this tier (design v2 §4), so "the comparison does not quantize" is only measurable here.
* THE TRANSACTION PROBE. `asyncpg.Connection.is_in_transaction()` is a METHOD where
  `sqlite3.Connection.in_transaction` is an attribute. The `begin` guard now REFUSES when the driver
  will not answer, so "asyncpg always answers" is what keeps that refusal from being a refusal of
  every transaction in the system - a claim about this driver, measurable only against it.

Every verdict is read on a NEW session, each test names the mutation that must turn it red, and the
stand purges what it created.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, literal, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.ledger import journal
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, arm_stand, identity

#: What the rest of a scenario raises once a refusal has been swallowed to reach the assertions.
_SCENARIO_END = (journal.DebtJournalError, InvalidRequestError)


@pytest_asyncio.fixture
async def stand():
    """This module's own engine with a real pool, and the journal armed on it.

    BUILT HERE, NEXT TO ITS REFUSAL, for the reason
    `tests/integration/test_p015_b4a_journal_postgres.py` states: every SQLite-capable engine
    construction had to be paired with `install_sqlite_transaction_control` (T1525, deleted with
    SQLite in 017 stage 3 S7), and a construction
    that can only ever be PostgreSQL is exempt only where a refusal in the same module says so. The
    `pytest.skip` below is that refusal.

    `NullPool` is deliberately not used: a released connection would be CLOSED, and "the transaction
    ended" could not be told from "the connection died", which is exactly the distinction the probe
    test below turns on.
    """

    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=4, max_overflow=0, pool_timeout=15
    )
    built = await arm_stand(engine, extra_participants=1)
    try:
        yield built
    finally:
        await built.close(purge=True)


async def _refusal_of(awaitable) -> BaseException | None:
    try:
        await awaitable
    except journal.DebtJournalError as exc:
        return exc
    return None


async def _committed_debt(stand: Stand, amount: Decimal, **edge) -> uuid.UUID:
    async with stand.factory() as session:
        subject = stand.debt("0", raw_amount=amount, **edge)
        async with stand.operation("t1528-p-seed", session=session):
            session.add(subject)
            await session.flush()
        await session.commit()
        return subject.id


async def _stored(stand: Stand, debt_id: uuid.UUID):
    """One debt as PostgreSQL holds it, read on a new session, or None."""

    async with stand.factory() as fresh:
        return (
            await fresh.execute(
                select(Debt.amount, Debt.creditor_id).where(Debt.id == debt_id)
            )
        ).one_or_none()


@pytest.mark.asyncio
async def test_t1528_p_an_edge_moved_by_a_literal_is_refused_with_native_uuids(
    stand: Stand,
) -> None:
    """T1528, review item 2, on the tier where a UUID is a UUID.

    `debt.creditor_id = literal(other.id)` puts the new participant in the statement's own `_values`
    rather than in its parameters, and the guard now reads it from there. Reading it means comparing
    it with the edge the journal recorded, and THAT is the comparison this programme has twice got
    wrong by writing it against one tier's spelling: here both sides are native `uuid.UUID` objects
    arriving through asyncpg, not 32-character hex strings.

    MUTATION that must redden this again: compare the written edge with the recorded one as `str` of
    whatever arrived instead of through `_key_text`/`_key_written` (`uuid.UUID` normalisation), or
    make `_row_edge` ignore `_statement_values`.
    """

    ident = identity("t1528-p-literal-edge")
    debt_id = await _committed_debt(stand, Decimal("10.00000000"))
    other_creditor = stand.extra_ids[0]
    fired: list[str] = []
    refusal: BaseException | None = None

    async with stand.factory() as session:

        @event.listens_for(session.sync_session, "before_flush")
        def _a_late_listener(sync_session, _flush_context, _instances) -> None:
            if fired:
                return
            for obj in list(sync_session.dirty):
                if isinstance(obj, Debt) and obj.id == debt_id:
                    obj.creditor_id = literal(other_creditor)
                    fired.append("before_flush")

        try:
            async with stand.operation("t1528-p-literal-edge", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = Decimal("11.00000000")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
        except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
            refusal = refusal if refusal is not None else exc

    stored = await _stored(stand, debt_id)
    entries = await stand.entries(ident)

    assert fired == ["before_flush"], (
        f"stand: the late listener never reassigned `creditor_id` ({fired}); nothing moved"
    )
    assert refusal is not None, (
        f"a stored debt was moved to another creditor through a SQL expression and nothing refused "
        f"it: `debts` holds {stored} while the journal recorded {entries}"
    )
    assert stored is not None and stored[1] == stand.creditor_id, (
        f"the debt is durable on a creditor the journal never recorded: {stored}"
    )
    assert stored[0] == Decimal("10.00000000"), f"the amount moved as well: {stored}"
    assert entries == [], f"a refused operation still wrote entries: {entries}"
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal


@pytest.mark.asyncio
async def test_t1528_p_the_readback_catches_full_width_money_changed_after_verification(
    stand: Stand,
) -> None:
    """T1528, the execution boundary, on the tier where money is full width.

    The write guard verifies a statement's parameters BEFORE it executes, so a `before_execute`
    listener on the engine INSTANCE - which SQLAlchemy runs after every class-level listener, and the
    journal is armed on the `Engine` class - changes what is written after the check has passed. Only
    reading the row back can see it.

    AND THE AMOUNT IS THE LARGEST `NUMERIC(20, 8)` HOLDS, which is this tier's own concern: the
    comparison in `_reconcile` must be a comparison of numbers that does not quantize. A readback
    that went through `_money_text` would raise `InvalidOperation` on values this column legitimately
    holds, and a journal whose verification crashes on a legal amount refuses legal work.

    MUTATION that must redden this: remove the `_reconcile` call from `_after_flush`. Nothing at the
    parameter level can see this write - the parameters the guard checked were the honest ones.
    """

    ident = identity("t1528-p-readback")
    verified = Decimal("999999999999.99999998")
    actually_written = Decimal("999999999999.99999999")
    debt_id = uuid.uuid4()
    rewritten: list[Decimal] = []
    refusal: BaseException | None = None

    @event.listens_for(stand.engine.sync_engine, "before_execute", retval=True)
    def _tamper(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        if (
            getattr(getattr(clause, "table", None), "name", None) == "debts"
            and isinstance(params, dict)
            and params.get("amount") == verified
        ):
            changed = dict(params)
            changed["amount"] = actually_written
            rewritten.append(actually_written)
            return clause, multiparams, changed
        return clause, multiparams, params

    try:
        async with stand.factory() as session:
            try:
                async with stand.operation("t1528-p-readback", session=session, identity=ident):
                    session.add(stand.debt("0", id=debt_id, raw_amount=verified))
                    refusal = await _refusal_of(session.flush())
                if refusal is None:
                    refusal = await _refusal_of(session.commit())
            except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
                refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", _tamper)

    stored = await _stored(stand, debt_id)
    entries = await stand.entries(ident)

    assert rewritten == [actually_written], (
        f"stand: the engine-level listener never rewrote the INSERT's amount ({rewritten}), so "
        f"nothing was written behind the guard's back"
    )
    assert refusal is not None, (
        f"a neighbour changed the amount after verification and the row committed: `debts` holds "
        f"{stored} while the journal recorded {entries}"
    )
    assert stored is None, f"the tampered row is durable: {stored}"
    assert entries == [], f"a refused operation still wrote entries: {entries}"
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNRECONCILED_DEBT_ROW, refusal


@pytest.mark.asyncio
async def test_t1528_p_control_full_width_money_passes_the_readback(stand: Stand) -> None:
    """T1528 ANTI-VACUUM CONTROL on this tier: the readback must pass the widest legal money.

    The refusal above is only evidence if the same amount commits when nobody tampers with it. This
    is also the case a quantizing comparison would break: `999999999999.99999999` cannot be
    quantized to scale 8 without `InvalidOperation`, so a `_reconcile` written with `_money_text`
    would refuse the largest legal debt in the system.

    GREEN BEFORE AND AFTER. The mutation that must redden it: compare the stored amount with the
    recorded one through `_money_text` in `_same_money`.
    """

    ident = identity("t1528-p-control")
    largest = Decimal("999999999999.99999999")
    debt_id = uuid.uuid4()

    async with stand.factory() as session:
        async with stand.operation("t1528-p-control", session=session, identity=ident):
            session.add(stand.debt("0", id=debt_id, raw_amount=largest))
            await session.flush()
        await session.commit()

    stored = await _stored(stand, debt_id)
    entries = await stand.entries(ident)

    assert stored is not None and stored[0] == largest, (
        f"the largest legal debt did not survive the readback: {stored}"
    )
    assert [row["amount_after"] for row in entries] == [largest], entries


@pytest.mark.asyncio
async def test_t1528_p_asyncpg_answers_the_transaction_probe_with_a_bool(stand: Stand) -> None:
    """T1528, review item 4: the contract behind the refusal, measured on this driver.

    `_on_begin` now REFUSES a `begin` whose driver will not say whether a transaction is already
    open - "unmeasured" is not "clean" (`AGENTS.md` §1). That refusal is only harmless because every
    driver this repository runs on answers, every time, and on this tier the answer comes from a
    METHOD rather than from an attribute, which is the half SQLite cannot measure.

    WITHOUT THIS the new refusal could be refusing every transaction in the system and the policy
    table on the SQLite tier would still pass, because that one asks the listener and not the driver.

    MUTATION that must redden this: return `None` from `_driver_transaction_probe` whenever the
    driver answers through a method rather than through an attribute.
    """

    async with stand.engine.connect() as aconn:
        sync_connection = aconn.sync_connection
        driver = sync_connection.connection.driver_connection
        idle = journal._driver_transaction_is_live(sync_connection)
        transaction = await aconn.begin()
        await aconn.execute(select(Debt.id).limit(1))
        live, how = journal._driver_transaction_probe(sync_connection)
        await transaction.rollback()

    assert callable(getattr(driver, "is_in_transaction", None)), (
        f"asyncpg's connection no longer exposes `is_in_transaction()` ({type(driver).__name__}); "
        f"the `begin` guard's only positive finding is unreachable and every begin is refused"
    )
    assert idle is False, (
        f"asyncpg did not answer the probe with False on an idle connection ({idle!r})"
    )
    assert live is True, (
        f"asyncpg did not report an open transaction as live ({live!r}, via {how})"
    )
    assert "is_in_transaction" in how, how
