"""T1526: `NaN` is a storable `debts.amount`, and it destroys every sum it touches.

THE DEFECT, measured on the backend that holds the money. `debts.amount` is `NUMERIC(20, 8)` and
its only guard is `CHECK (amount > 0)` (`app/db/models/debt.py:31`). PostgreSQL orders `NaN` ABOVE
every number, so the positivity check admits it:

    geov0_test_ci=> SELECT 'NaN'::numeric(20,8) > 0;
     t

WHY IT IS WORSE THAN A WRONG NUMBER. A debt of the wrong size makes the book wrong by an amount
someone can find and correct. A debt of `NaN` makes every aggregate that includes it `NaN`, so
"the sum of all debts is zero" - the property programme 015 exists to establish - stops being
REACHABLE AT ALL. The second test measures exactly that: one `NaN` row and `SUM(amount)` over the
equivalent is no longer a number.

THREE TESTS, THREE DOORS, and they are not redundant - each closes a different way in:

* `test_a` writes through the ORM, the path every application writer uses.
* `test_b` measures the consequence in the database: the sum of the book.
* `test_c` writes with RAW SQL, going around every Python-side guard. Only the CHECK CONSTRAINT
  can refuse that one, which is why `test_c` is the test that the MIGRATION has to satisfy: a
  type-level guard in `app/db/types.py` would leave `test_c` red. `AGENTS.md` §16 calls this
  looking for the path around the defence rather than for its absence.
* `test_d` is `test_c` for `trust_lines."limit"`, the other money column carrying the same hole -
  `CHECK ("limit" >= 0)` is TRUE for `NaN` for exactly the same reason, and a `NaN` limit makes
  every capacity computed from it `NaN`. It is here because migration 021 changes that column too,
  and a fix without a counterexample is a claim rather than a result.

EVERY VERDICT IS READ BACK ON A NEW SESSION. A value that never reached the database and a value
that reached it look identical through the session that wrote it - its identity map answers from
memory.

NON-VACUITY FIRST. Each test seeds and stores a LEGITIMATE debt through the same path before it
asserts anything about `NaN`, so a stand that cannot store money at all can never be mistaken for
one that refused `NaN`.

RED TODAY (2026-09-12, PostgreSQL 16.9, `geov0_test_ci` at `020_debts_equivalent_fk_restrict`):
the ORM write is accepted, the raw write is accepted, and the sum of the book is `NaN`.

THE MUTATION that must turn these red again once they are green, and it is deliberately not a
single line. `chk_debt_amount_positive` ends up with TWO clauses that each refuse a `NaN` on
PostgreSQL on their own - the magnitude bound `<= 999999999999.99999999` and the explicit
`<> 'NaN'` - so removing either ONE leaves `test_c` green, and that redundancy is the point: the
explicit clause exists so that relaxing the bound cannot silently reopen the hole. The mutations
are therefore:

* `test_c` (the database's own guarantee): restore `CHECK (amount > 0)` as the whole predicate,
  i.e. drop BOTH added clauses. Dropping one is measured to keep it green.
* `test_a` and `test_b` (the guarantee for writers that go through SQLAlchemy): remove
  `MoneyNumeric` from `Debt.amount` in `app/db/models/debt.py` AND restore the bare predicate -
  either guard alone still refuses the ORM write, which is exactly why there are two of them.

THE DATABASE IS SHARED. `geov0_test_ci` is used by several sessions working in this tree at once,
so every cleanup below is scoped to the ids THIS PROCESS created; a check written against the
`NAN` name prefix would be measuring a neighbour's rows.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

from tests.debt_setup import debt_fixture_setup


@pytest_asyncio.fixture
async def factory(committed_database):
    """A real pool of its own, never the `db_session` fixture.

    `db_session` wraps every test in an outer transaction on one checked-out connection and rolls
    it back, so a row that was really committed and a row that was never written are the same
    observation. These tests commit and then read back on a different session.

    Over this test's disposable clone of the migrated template (018 B0b): the clone's drop is the
    only disposal of what the test committed.
    """
    engine = create_async_engine(
        committed_database.url, pool_size=4, max_overflow=0, pool_timeout=10
    )
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


class _World:
    def __init__(self, equivalent_id, debtor_id, creditor_id, third_id):
        self.equivalent_id = equivalent_id
        self.debtor_id = debtor_id
        self.creditor_id = creditor_id
        # A THIRD participant, so that every write below can use an edge of its own. The first
        # edition of `test_c` reused the seeded edge, and `uq_debts_debtor_creditor_equivalent`
        # refused the NaN row with a 23505 before the amount was ever examined - the stand was
        # measuring its own duplicate key and would have reported a refusal that does not exist.
        self.third_id = third_id


async def _seed(session_factory) -> _World:
    """One equivalent and three participants, committed, plus ONE legitimate debt of 5.00000000."""
    n = uuid.uuid4().hex[:8]
    async with session_factory() as session:
        equivalent = Equivalent(code=f"NAN{n}".upper()[:16], precision=2, is_active=True)
        debtor = Participant(
            pid=f"NAND_{n}", display_name="Debtor", public_key=f"pk_nand_{n}",
            type="person", status="active", profile={},
        )
        creditor = Participant(
            pid=f"NANC_{n}", display_name="Creditor", public_key=f"pk_nanc_{n}",
            type="person", status="active", profile={},
        )
        third = Participant(
            pid=f"NANT_{n}", display_name="Third", public_key=f"pk_nant_{n}",
            type="person", status="active", profile={},
        )
        session.add_all([equivalent, debtor, creditor, third])
        await session.flush()
        async with debt_fixture_setup(session, label="setup"):
            session.add(
                Debt(
                    debtor_id=debtor.id,
                    creditor_id=creditor.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("5.00000000"),
                )
            )
        await session.commit()
        return _World(equivalent.id, debtor.id, creditor.id, third.id)


def _refusal_details(exc: DBAPIError) -> tuple[str | None, str | None]:
    """`(sqlstate, constraint_name)` out of a wrapped asyncpg error.

    `exc.orig` is SQLAlchemy's asyncpg ADAPTER exception, which carries `sqlstate` but not
    `constraint_name`; the asyncpg error that knows the constraint is its `__cause__`. Reading only
    `exc.orig` answered `None` and would have made "the right constraint refused this" untestable.
    """

    sqlstate = None
    constraint = None
    candidate: BaseException | None = exc.orig
    while candidate is not None:
        sqlstate = sqlstate or getattr(candidate, "sqlstate", None)
        constraint = constraint or getattr(candidate, "constraint_name", None)
        candidate = candidate.__cause__
    return sqlstate, constraint


async def _amounts(session_factory, world: _World) -> list[str]:
    """Every amount of this world as the DATABASE holds it, read on a NEW session."""
    async with session_factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.amount).where(Debt.equivalent_id == world.equivalent_id)
            )
        ).scalars().all()
    return [str(value) for value in rows]


@pytest.mark.asyncio
async def test_a_an_orm_write_of_nan_must_not_reach_the_money_column(factory):
    """The ordinary path: a `Debt` built in Python and flushed by a session."""
    world = await _seed(factory)
    # NON-VACUITY: this stand really can store money, so "nothing was stored" below can only
    # mean the write was refused.
    assert await _amounts(factory, world) == ["5.00000000"], (
        "the stand could not store an ordinary debt, so it cannot tell a refusal from a "
        "broken stand"
    )

    refusal: BaseException | None = None
    second = uuid.uuid4()
    # NOTHING STANDS DOWN (018 stage B1). Until B1 the listener journal refused a non-finite amount by
    # its OWN predicate before any SQL, and this test uninstalled it for the write so that
    # `MoneyNumeric` and the column's CHECK - this module's subject - were reached. The journal is now
    # the database's trigger, which checks no money domain, and the `Debt` is added directly (not
    # through `Posting.apply`, whose storability check would answer first), so the first thing to meet
    # the NaN is `MoneyNumeric` at bind. The book flushes when its block ends, so the refusal is raised
    # at the block's exit and the `try` spans the whole block.
    async with factory() as session:
        try:
            async with debt_fixture_setup(session, label="setup"):
                session.add(
                    Debt(
                        id=second,
                        debtor_id=world.creditor_id,
                        creditor_id=world.debtor_id,
                        equivalent_id=world.equivalent_id,
                        amount=Decimal("NaN"),
                    )
                )
            await session.commit()
        except (StatementError, DBAPIError, ValueError) as exc:
            refusal = exc
            await session.rollback()

    stored = await _amounts(factory, world)
    assert stored == ["5.00000000"], (
        f"a debt whose amount is NOT A NUMBER is in the money column: the database holds "
        f"{stored!r} for this equivalent. `chk_debt_amount_positive` is CHECK (amount > 0), "
        f"and PostgreSQL orders NaN above every number, so the positivity check admits it "
        f"(measured: SELECT 'NaN'::numeric(20,8) > 0 -> t)."
    )
    assert refusal is not None, (
        "the write was not refused; nothing between the application and the column objected "
        "to an amount that is not a number"
    )
    # THE REFUSAL NAMES THE MONEY RULE, and it is `MoneyNumeric`'s (`app/db/types.py`). Carried
    # over from `tests/unit/test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py`
    # (017 stage 3), which held this message on the SQLite stand only. On this backend the
    # column's CHECK would refuse too, one line later and in its own words; this pins that the
    # refusal comes BEFORE the statement is sent and says what is wrong with the value.
    message = str(refusal)
    assert "NOT NULL" not in message, (
        f"the refusal comes from the WRONG CONSTRAINT: {message!r}. An amount WAS supplied; it "
        f"is not a number."
    )
    assert "non-finite" in message.lower(), (
        f"the refusal does not say what is wrong with the value: {message!r}. It must name the "
        f"value's own defect - that it is not a finite number."
    )


@pytest.mark.asyncio
async def test_b_one_nan_debt_makes_the_sum_of_the_book_stop_being_a_number(factory):
    """THE CONSEQUENCE, in the database, in the units programme 015 is about.

    This is the test that says why `NaN` is not just another out-of-domain value. The programme's
    goal is an auditable "the sum of all debts is zero". With one `NaN` row that sum is `NaN` - not
    wrong by some amount, but not a number at all, and no reconciliation can ever close it.
    """
    world = await _seed(factory)
    async with factory() as session:
        before = (
            await session.execute(
                select(func.sum(Debt.amount)).where(Debt.equivalent_id == world.equivalent_id)
            )
        ).scalar_one()
    # NON-VACUITY: the sum is a real number before the NaN attempt.
    assert str(before) == "5.00000000", f"the seeded book does not sum to 5: {before!r}"

    # No stand-down since 018 B1, for the reason given in `test_a`; the refusal surfaces at the book's
    # block exit, so the `try` spans the block.
    async with factory() as session:
        try:
            async with debt_fixture_setup(session, label="setup"):
                session.add(
                    Debt(
                        debtor_id=world.creditor_id,
                        creditor_id=world.debtor_id,
                        equivalent_id=world.equivalent_id,
                        amount=Decimal("NaN"),
                    )
                )
            await session.commit()
        except (StatementError, DBAPIError, ValueError):
            await session.rollback()

    async with factory() as fresh:
        after = (
            await fresh.execute(
                select(func.sum(Debt.amount)).where(Debt.equivalent_id == world.equivalent_id)
            )
        ).scalar_one()

    assert str(after) == "5.00000000", (
        f"the sum of this equivalent's debts is {after!r}. One row that is not a number makes "
        f"every aggregate over the book not a number, so 'the sum of all debts is zero' is not "
        f"reachable at all - the whole goal of programme 015 - and no audit can name the "
        f"amount by which the book is wrong."
    )


@pytest.mark.asyncio
async def test_c_the_database_itself_refuses_nan_when_python_is_bypassed(factory):
    """RAW SQL. No ORM, no type, no application validation - only the CHECK constraint is left.

    THIS is the test the migration has to satisfy. A guard in Python protects the writers that go
    through Python; `app/core/clearing/service.py` and `scripts/` are not the only things that can
    reach this table, and a database-level guarantee has to hold for the `psql` session too.
    """
    world = await _seed(factory)
    # INSIDE AN OPEN OPERATION (018 stage B1). A raw INSERT with no operation named in the transaction
    # is refused by the `debts` trigger (`GE001`) whatever its amount, which would make both halves
    # below measure the journal instead of the value. Both statements run inside a fixture operation
    # on the same session, so `geo.operation_id` is set and the only thing left to refuse the NaN is
    # the column's CHECK (a row CHECK is evaluated before an AFTER-row trigger runs anyway).
    #
    # NON-VACUITY: the same raw statement stores an ordinary amount, so a refusal below is
    # about the VALUE and not about the statement being malformed.
    async with factory() as session:
        async with debt_fixture_setup(session, label="raw-control"):
            await session.execute(
                text(
                    "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version)"
                    " VALUES (:id, :d, :c, :e, 7.00000000, 0)"
                ),
                {
                    "id": uuid.uuid4(),
                    "d": world.creditor_id,
                    "c": world.debtor_id,
                    "e": world.equivalent_id,
                },
            )
        await session.commit()
    assert sorted(await _amounts(factory, world)) == ["5.00000000", "7.00000000"], (
        "the raw INSERT could not store an ordinary amount, so this stand cannot tell a "
        "constraint refusal from a broken statement"
    )

    sqlstate = None
    constraint = None
    async with factory() as session:
        try:
            async with debt_fixture_setup(session, label="raw-nan"):
                await session.execute(
                    text(
                        "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount,"
                        " version) VALUES (:id, :d, :c, :e, 'NaN', 0)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        # An edge of its own: the seeded edge and the 7.00000000 edge are both
                        # taken, and the unique constraint would refuse this row before the
                        # amount was looked at.
                        "d": world.third_id,
                        "c": world.debtor_id,
                        "e": world.equivalent_id,
                    },
                )
            await session.commit()
        except DBAPIError as exc:
            await session.rollback()
            sqlstate, constraint = _refusal_details(exc)

    stored = sorted(await _amounts(factory, world))
    assert stored == ["5.00000000", "7.00000000"], (
        f"raw SQL put NaN into debts.amount and the database kept it: {stored!r}. The only "
        f"thing standing between a NaN and this column is chk_debt_amount_positive, and "
        f"CHECK (amount > 0) is TRUE for NaN on PostgreSQL."
    )
    assert sqlstate == "23514", (
        f"the refusal did not come from a CHECK constraint (SQLSTATE {sqlstate!r}, constraint "
        f"{constraint!r}). 23514 is check_violation; anything else means the value was "
        f"refused for a reason that is not 'this is not a valid amount'."
    )
    assert constraint == "chk_debt_amount_positive", (
        f"the refusing constraint is {constraint!r}, not the amount-domain check. The guard "
        f"must be the one that OWNS the meaning of a valid amount, so that the next reader "
        f"finds the rule where the rule belongs."
    )


@pytest.mark.asyncio
async def test_d_the_same_hole_in_the_other_money_column_is_closed_too(factory):
    """`trust_lines."limit"`: `CHECK ("limit" >= 0)` admits `NaN` for the same reason.

    Raw SQL again, for the same reason as `test_c`: this is the database's own guarantee, and the
    bind-layer guard cannot speak for a statement that does not go through SQLAlchemy's type.
    """
    world = await _seed(factory)
    # NON-VACUITY: an ordinary limit goes in through the same statement.
    legitimate = uuid.uuid4()
    async with factory() as session:
        await session.execute(
            text(
                'INSERT INTO trust_lines (id, from_participant_id, to_participant_id,'
                ' equivalent_id, "limit", policy, status) VALUES (:id, :f, :t, :e,'
                " 100.00000000, '{}', 'active')"
            ),
            {
                "id": legitimate,
                "f": world.creditor_id,
                "t": world.debtor_id,
                "e": world.equivalent_id,
            },
        )
        await session.commit()

    async with factory() as fresh:
        stored = (
            await fresh.execute(
                text('SELECT "limit" FROM trust_lines WHERE id = :id'), {"id": legitimate}
            )
        ).scalar_one()
    assert str(stored) == "100.00000000", (
        f"the raw INSERT could not store an ordinary limit ({stored!r}), so this stand cannot "
        f"tell a constraint refusal from a broken statement"
    )

    sqlstate = None
    constraint = None
    forged = uuid.uuid4()
    async with factory() as session:
        try:
            await session.execute(
                text(
                    'INSERT INTO trust_lines (id, from_participant_id, to_participant_id,'
                    ' equivalent_id, "limit", policy, status) VALUES (:id, :f, :t, :e,'
                    " 'NaN', '{}', 'active')"
                ),
                {
                    "id": forged,
                    "f": world.third_id,
                    "t": world.debtor_id,
                    "e": world.equivalent_id,
                },
            )
            await session.commit()
        except DBAPIError as exc:
            await session.rollback()
            sqlstate, constraint = _refusal_details(exc)

    async with factory() as fresh:
        kept = (
            await fresh.execute(
                text('SELECT "limit"::text FROM trust_lines WHERE id = :id'), {"id": forged}
            )
        ).scalars().all()
    assert kept == [], (
        f"a trust limit that is not a number is in the database: {kept!r}. Every capacity "
        f"computed from that line - and every sum of capacities across the graph - is NaN."
    )
    assert sqlstate == "23514" and constraint == "chk_trust_line_limit_positive", (
        f"the refusal did not come from the limit-domain check (SQLSTATE {sqlstate!r}, "
        f"constraint {constraint!r})"
    )
