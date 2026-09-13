"""Programme 015, `B4` step 2: entries and money on the tier that actually holds the money.

WHAT THIS MODULE IS. The PostgreSQL half of the second step-2 slice. Its SQLite sibling is
`tests/unit/test_p015_b4_entries_and_money.py`, and the division between them is not "the same
tests twice on a bigger database". Design v2 §4 rule 1 makes PostgreSQL **the money-acceptance
tier**: expectations are integer atoms read off literals, compared exactly, with no tolerance
anywhere. Everything here is something SQLite cannot answer at all:

* `C4-P` - the entry sequence at FULL money size. The sibling owns the SHAPES (I/U/U/D, set-zero-
  then-delete, net zero, delete-then-reinsert) inside `|v| < 2^26`, which is the only domain that
  tier round-trips exactly. The subject here is EXACTNESS at `999999999999.99999999`, the largest
  value `debts.amount NUMERIC(20,8)` can hold, which SQLite cannot store at all.
* `C8` - a REAL `40001`. SQLite has no serialization failures; it has `database is locked`.
* `C12-P` - the half of design v2 §4 rule 3 that makes it a PER-DIALECT rule rather than a constant.
  `100000000000.00000001` is REFUSED on SQLite (measured there: stored as `100000000000.00000000`)
  and must be ACCEPTED here and stored exactly. The refusals that are NOT dialect-dependent - not
  finite, past the `10^12` ceiling - must still be refused before any SQL on both tiers, and this
  module measures what PostgreSQL really does with each of them rather than assuming.
* `C13-P` - TWO CONCURRENT openers of one identity. The sibling's own docstring says why this test
  has to exist: a pre-checking `SELECT` passes its single-threaded version, and only two independent
  connections released together can tell a SELECT-then-INSERT from a real `UNIQUE(kind, identity)`.
* `C14` - payment and clearing driven by their REAL owners, which need advisory locks, a pinned work
  connection and an engine-bound session; none of that exists on SQLite.
* `C17-P` and design v2 §10.1 - the equivalent-deletion race, with the owner lock that is a no-op off
  PostgreSQL (`app/core/payments/engine.py:152`).
* `C18-P` - the `40001` variant of the concurrent version bump. The sibling runs the `StaleDataError`
  variant on one shared connection, because two independent SQLite writers cannot coexist.
* `C19-P` - the same forged-row inventory against the PORTABLE CHECK constraints, plus the
  shape-valid lie that must be accepted and is therefore a step-6 verifier item.

THE STAND. Its own engine with `isolation_level="SERIALIZABLE"` and a real pool, never the
`db_session` fixture: that fixture wraps every test in an outer transaction on a checked-out
connection, which hides the commit boundaries these counterexamples are about and which PostgreSQL
clearing refuses outright (`app/core/clearing/service.py:1494-1504`). Every verdict is read back on
a NEW session or a NEW connection, never through the session under test.

THE DATABASE IS SHARED. `geov0_test_ci` is used by several sessions working in this tree at once, so
every cleanup and every leak check below is scoped to the ids THIS PROCESS created. A check written
against a name prefix would be measuring a neighbour's rows; see `_SEEDED` and
`every_seeded_row_is_gone_when_the_test_ends`.

READ `tests/p015_b4_support.py` for the import rule and the two kinds of red.

MARKER, HISTORICAL. This module carried `b4_counterexample` alongside `postgres` and was deselected
from every tier while the debt journal did not exist. Step 4 slice C built it and REMOVED THE
MARKER, not the assertions: every test below still asserts exactly what it asserted while it was
red, and each one names in its docstring the mutation that must turn it red again. `postgres`
stays - this tier is about PostgreSQL semantics, not about the journal's absence.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import DatabaseError, DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.audit_log import AuditLog, IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup, purge_test_ledger

# The proven inject stand, imported rather than rebuilt: `observed_factory` is its own SERIALIZABLE
# engine with the session class whose debt flushes are observed, and `C8`'s inject half drives the
# real owner through it. A second stand for the same conflict would be a second thing to keep right.
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (  # noqa: F401
    observed_factory,
)
from tests.p015_b4_support import (
    ENTRIES_TABLE,
    JOURNAL_MODULE,
    OPERATION_EQUIVALENTS_TABLE,
    OPERATIONS_TABLE,
    World,
    drop_world,
    journal_api,
    missing_journal_tables,
    operation,
    seed_world,
    stored_debts,
    stored_entries,
    stored_operations,
    stored_rows,
)

pytestmark = pytest.mark.postgres


# ==============================================================================================
# Money, in integer atoms
# ==============================================================================================

#: One scale-8 atom. Design v2 §4 rule 1: on this tier expectations are INTEGER ATOMS taken from
#: literals and compared exactly. Nothing in this module compares money with a tolerance.
ATOM = Decimal("0.00000001")

#: The largest amount `debts.amount NUMERIC(20,8)` can hold: twelve integer digits and eight
#: fractional ones. Measured against this database on 2026-09-12 (PostgreSQL 16.9): stored and read
#: back byte-identically. The same literal on SQLite is outside the proven-exact domain by four
#: orders of magnitude, which is why `C4`'s full-size case lives here and not there.
FULL_SIZE = Decimal("999999999999.99999999")


def _atoms(value) -> int:
    """`value` as an exact whole number of scale-8 atoms, or a loud failure.

    The division is done in a widened context on purpose: `999999999999.99999999 / 1E-8` is a
    twenty-digit integer, and the default `decimal` context carries twenty-eight significant digits,
    so the value is exact today - but a comparison that silently became approximate is precisely the
    failure design v2 §4 rule 1 exists to forbid, and the equality check below makes that impossible
    rather than merely unlikely.
    """
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    with localcontext() as ctx:
        ctx.prec = 60
        quotient = amount / ATOM
    integral = int(quotient)
    assert Decimal(integral) == quotient, (
        f"{amount} is not a whole number of scale-8 atoms ({quotient}); this module compares money "
        f"as integers and has nothing meaningful to say about a value that is not one"
    )
    return integral


def _atoms_or_none(value) -> int | None:
    return None if value is None else _atoms(value)


def _atom_column(rows: list[dict] | None, key: str) -> list[int | None]:
    return [_atoms_or_none(row[key]) for row in rows or []]


def _obeys_the_money_rules(value: Decimal) -> bool:
    """Design v2 §4 rule 3 MINUS its dialect clause, restated here for the ANTI-VACUUM only.

    It is deliberately a restatement and not an import: the rule it describes does not exist yet,
    and the module that will own it is what these counterexamples are written against. The SQLite
    sibling carries the same three lines for the same single purpose - to say "this value is
    forbidden for a reason OTHER than the round-trip" - and the duplication is the point: if step 4
    legalises one of these values on one tier only, the two copies disagree and both non-vacuity
    assertions speak up instead of letting a test pass having measured nothing.
    """
    if not value.is_finite():
        return False
    if value != value.quantize(ATOM):
        return False
    return abs(value) < Decimal(10) ** 12


# ==============================================================================================
# The stand
# ==============================================================================================


@pytest_asyncio.fixture
async def serializable_engine():
    """This module's own SERIALIZABLE engine with a real pool.

    `pool_size=8` is not generosity. PostgreSQL clearing checks out a PINNED work connection of its
    own on top of the caller's session (`app/core/clearing/service.py:1612-1644`), the owner-lock
    races below run three sessions plus an observer at once, and a pool that ran dry would surface as
    a `pool_timeout` inside a counterexample - a broken stand reported as a property failure, which
    is the one failure mode this programme refuses to ship.

    `NullPool` is deliberately NOT used: a released connection would be closed, and several
    assertions here turn on a connection surviving its transaction.
    """
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=8,
        max_overflow=0,
        pool_timeout=20,
        isolation_level="SERIALIZABLE",
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def serializable_factory(serializable_engine):
    """A sessionmaker over this module's own engine. NEVER `db_session` - see the module docstring."""
    return async_sessionmaker(
        bind=serializable_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


@pytest_asyncio.fixture
async def observer():
    """A connection that is never one of the connections under test, for reading `pg_locks`.

    It has its own engine so that a counterexample cannot starve it: an observer sharing the pool
    with the sessions it is supposed to watch would block exactly when the thing worth watching is
    happening.
    """
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=10)

    async def _waiting_on_an_advisory_lock(backend_pid: int) -> bool:
        """True once `backend_pid` is blocked on an advisory lock it has not been granted."""
        async with engine.connect() as connection:
            return bool(
                await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid "
                        "AND locktype = 'advisory' AND NOT granted)"
                    ),
                    {"pid": backend_pid},
                )
            )

    try:
        yield _waiting_on_an_advisory_lock
    finally:
        await engine.dispose()


@dataclass
class _Seeded:
    """One world this PROCESS committed, plus everything the real owners hung off it."""

    world: World
    tx_ids: list[str] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [self.world.equivalent.code, self.world.other_equivalent.code]


#: Every world this PROCESS seeded. The leak check is scoped to these ids rather than to the `B4`
#: code prefix, because `geov0_test_ci` is SHARED between sessions working in this tree: a check that
#: read the prefix would redden on a neighbour's rows and go quiet about its own.
_SEEDED: list[_Seeded] = []


async def _seed(factory, *, extra_participants: int = 0) -> _Seeded:
    seeded = _Seeded(await seed_world(factory, extra_participants=extra_participants))
    _SEEDED.append(seeded)
    return seeded


async def _cleanup(factory, seeded: _Seeded) -> None:
    """Remove exactly what this test committed, by id. Never a blanket delete: the database is shared.

    THE ORDER IS NOT ARBITRARY. `prepare_locks.tx_id` is a foreign key to `transactions.tx_id` and
    `transactions.initiator_id` is RESTRICT, so locks go before transactions and transactions before
    participants. `debts.equivalent_id` is RESTRICT since T1524, so debts go before the equivalent -
    `drop_world` does that part. `debts.debtor_id`/`creditor_id` are still CASCADE (R2-13), so
    deleting participants first WOULD take the debts with them silently, which is the exact removal
    T1524 exists to make impossible; this teardown never leans on a cascade.
    """
    world = seeded.world
    async with factory() as session:
        # THE JOURNAL FIRST, and through the driver. `debt_operations.tx_id` is a RESTRICT reference
        # to `transactions.tx_id`, so an envelope still standing refuses the transaction delete
        # below it; and `session.execute(delete(Debt))` - which `drop_world` used to do at the end -
        # is Core DML the write guard refuses (`C2`). `purge_test_ledger` does both, scoped to this
        # world's ids. `drop_world` still runs at the end for the participants and equivalents.
        await purge_test_ledger(
            session, equivalent_ids=world.equivalent_ids, tx_ids=seeded.tx_ids
        )
        await session.execute(
            delete(PrepareLock).where(PrepareLock.participant_id.in_(world.participant_ids))
        )
        await session.execute(
            delete(IntegrityAuditLog).where(IntegrityAuditLog.equivalent_code.in_(seeded.codes))
        )
        await session.execute(delete(AuditLog).where(AuditLog.object_id.in_(seeded.codes)))
        await session.execute(
            delete(Transaction).where(Transaction.initiator_id.in_(world.participant_ids))
        )
        if seeded.tx_ids:
            await session.execute(
                delete(PrepareLock).where(PrepareLock.tx_id.in_(seeded.tx_ids))
            )
            await session.execute(
                delete(Transaction).where(Transaction.tx_id.in_(seeded.tx_ids))
            )
        await session.execute(
            delete(TrustLine).where(TrustLine.equivalent_id.in_(world.equivalent_ids))
        )
        await session.commit()
    await drop_world(factory, world)


@pytest_asyncio.fixture(autouse=True)
async def every_seeded_row_is_gone_when_the_test_ends():
    """The CHECK, not the cleanup - the cleanup is each test's own `finally`.

    A `finally` per test is only as good as the next author remembering to write one, so this
    asserts the OUTCOME instead of trusting the habit: a test added later that forgets its `finally`
    reddens here, naming the rows it left in a database other sessions are using. The same shape
    closed the same defect in `test_p015_t1525_classification_reads_deliberate_wrapping_only_
    postgres.py` on 2026-09-12, where three tests seeded and none cleaned up.

    It reads through `TestingSessionLocal` rather than this module's own engine, because that
    engine's fixture may already have been disposed by the time this teardown runs, and a plain
    count needs none of what it provides.
    """
    yield

    if not _SEEDED:
        return
    from tests.conftest import TestingSessionLocal

    equivalent_ids = [i for s in _SEEDED for i in s.world.equivalent_ids]
    participant_ids = [i for s in _SEEDED for i in s.world.participant_ids]
    codes = [code for s in _SEEDED for code in s.codes]

    async def _count(session, model, condition) -> int:
        return int(await session.scalar(select(func.count()).select_from(model).where(condition)))

    async with TestingSessionLocal() as session:
        left = {
            "debts": await _count(session, Debt, Debt.equivalent_id.in_(equivalent_ids)),
            "trustlines": await _count(
                session, TrustLine, TrustLine.equivalent_id.in_(equivalent_ids)
            ),
            "prepare_locks": await _count(
                session, PrepareLock, PrepareLock.participant_id.in_(participant_ids)
            ),
            "transactions": await _count(
                session, Transaction, Transaction.initiator_id.in_(participant_ids)
            ),
            "integrity_audit_logs": await _count(
                session, IntegrityAuditLog, IntegrityAuditLog.equivalent_code.in_(codes)
            ),
            "audit_logs": await _count(session, AuditLog, AuditLog.object_id.in_(codes)),
            "participants": await _count(session, Participant, Participant.id.in_(participant_ids)),
            "equivalents": await _count(session, Equivalent, Equivalent.id.in_(equivalent_ids)),
        }

    assert not any(left.values()), (
        f"this module left rows behind in a SHARED database: {left}, out of the "
        f"{len(_SEEDED)} world(s) it has seeded in this process. Every test that calls `_seed` must "
        f"call `_cleanup` from a `finally`."
    )


def _identity(name: str) -> str:
    return f"p015-b4/{name}/{uuid.uuid4().hex[:12]}"


def _intent(**fields) -> dict:
    return {"source": "p015-b4-counterexample", **fields}


async def _open(api, session, world: World, name: str, **kw):
    """`operation()` with this module's standard arguments, so each test shows only what differs."""
    return operation(
        api,
        session,
        kind=kw.pop("kind", "TEST_FIXTURE"),
        identity=kw.pop("identity", _identity(name)),
        intent=kw.pop("intent", _intent(name=name)),
        scope_equivalent_ids=kw.pop("scope_equivalent_ids", frozenset({world.equivalent.id})),
        **kw,
    )


def _debt_row(world: World, amount: Decimal, **kw) -> Debt:
    """A `Debt` on this world's edge with a raw amount.

    `World.debt` guards the proven-exact SQLite domain, which is right for that tier and wrong for
    this one: every value this module is about is outside it by construction.
    """
    return Debt(
        id=kw.pop("id", uuid.uuid4()),
        debtor_id=kw.pop("debtor_id", world.debtor.id),
        creditor_id=kw.pop("creditor_id", world.creditor.id),
        equivalent_id=kw.pop("equivalent_id", world.equivalent.id),
        amount=amount,
        version=kw.pop("version", 0),
    )


class _DebtStatements:
    """Records every statement reaching the connection that names the `debts` table.

    "Refused BEFORE any SQL" is a requirement about the ORDER of two things, and the only way to see
    it is to watch the connection. Reading the table afterwards cannot: a statement that ran and was
    rolled back leaves the same empty table as one that never ran, and design v2 §4 rule 3 is
    explicitly a refusal before SQL - a value the dialect would change, or that the column would
    reject, must never be SENT, not sent and then undone.
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, _conn, clauseelement, _multiparams, _params, _options) -> None:
        table = getattr(getattr(clauseelement, "table", None), "name", None)
        if table == "debts":
            self.seen.append(type(clauseelement).__name__)


def _watch_debt_statements(engine) -> _DebtStatements:
    recorder = _DebtStatements()
    event.listen(engine.sync_engine, "before_execute", recorder)
    return recorder


async def _refusal_or_database_error(api, awaitable):
    """`refusal_of`, widened to the database's own complaint.

    Some refusals live in the SCHEMA (`UNIQUE(kind, identity)`, a CHECK) rather than in the hook.
    Catching only the journal's exception types would let a test die on an `IntegrityError` instead
    of reading it as the refusal it is - and the assertions then say WHICH of the two arrived.
    """
    try:
        await awaitable
    except api.refusals as exc:  # noqa: B902 - the refusal contract is the subject under test
        return exc
    except (DatabaseError, StatementError) as exc:
        return exc
    return None


async def _round_trip(factory, world: World, value: Decimal):
    """Store `value` on this world's edge, read it back on a NEW session, remove it again.

    Returns `(stored, error)`; exactly one is not None. This is the MEASUREMENT every money
    counterexample here stands on - never an analytical claim about NUMERIC, always what this
    database on this dialect actually kept (`AGENTS.md` §1, "никаких гипотез из памяти сессии").
    """
    from app.core.ledger import journal

    row_id = uuid.uuid4()
    engine = factory.kw.get("bind")
    # THE JOURNAL STANDS DOWN FOR THIS MEASUREMENT, and it must. What is measured here is what THE
    # DIALECT does with a value - the ground truth every money counterexample in this module stands
    # on - and a journal that refused the write would replace that measurement with its own opinion:
    # the non-vacuity assertions that say "this database really stores/changes this number" would
    # then be proven by the very rule they exist to justify. Per engine, and re-armed immediately
    # (`app/core/ledger/journal.py`, `uninstall_write_guard`).
    journal.uninstall_write_guard(engine)
    try:
        try:
            async with factory() as session:
                session.add(_debt_row(world, value, id=row_id))
                await session.commit()
        except (DatabaseError, StatementError) as exc:
            return None, exc
        async with factory() as fresh:
            stored = (
                await fresh.execute(select(Debt.amount).where(Debt.id == row_id))
            ).scalar_one_or_none()
        async with factory() as cleanup:
            await cleanup.execute(Debt.__table__.delete().where(Debt.id == row_id))
            await cleanup.commit()
    finally:
        journal.install_write_guard(engine)
    return (None if stored is None else Decimal(str(stored))), None


async def _envelopes_with_intent(factory, *, identity: str | None = None, tx_id: str | None = None):
    """Envelope rows including the `intent` column, read fresh. None when the table is absent."""
    column, value = ("identity", identity) if identity is not None else ("tx_id", tx_id)
    return await stored_rows(
        factory,
        f"SELECT id, kind, identity, state, tx_id, intent, effect_count, flush_count "  # noqa: S608
        f"FROM {OPERATIONS_TABLE} WHERE {column} = :value",
        {"value": value},
    )


def _decoded_json(value):
    """JSON as Python. Raw `text()` SQL carries no type information, so asyncpg may hand a `json`
    column back as a string; a counterexample must not depend on which."""
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _decoded_intent(row: dict):
    return _decoded_json(row.get("intent"))


# ==============================================================================================
# C4-P - the entry sequence at full money size, in exact integer atoms
# ==============================================================================================


@pytest.mark.asyncio
async def test_c4_p_the_life_of_one_edge_at_full_money_size_is_exact_integer_atoms(
    serializable_factory,
):
    """C4-P, API-SHAPED. The sibling owns the shapes; this owns EXACTNESS at a size SQLite cannot hold.

    `tests/unit/test_p015_b4_entries_and_money.py` already establishes that an edge's life is a chain
    of entries and not a diff of its endpoints - I/U/U/D with `amount_before(n) == amount_after(n-1)`,
    set-to-zero-then-delete as one effect, net zero counted as two, a re-created edge recorded as an
    `I`. Those are shape properties and they hold inside `|v| < 2^26`, which is all SQLite can
    round-trip. NONE of them says the journal can carry a real payment: design v2 §4 rule 1 makes
    PostgreSQL the money-acceptance tier precisely because the expectations there are INTEGER ATOMS
    and the comparison is exact.

    So the edge here goes `999999999999.99999999` -> one atom -> gone: the largest value
    `NUMERIC(20,8)` can hold, down to the smallest, and out. Every assertion below is an integer
    count of atoms. The `U` entry's delta is `-99999999999999999998` atoms - a number that has no
    exact `float` and therefore cannot be checked on the other tier at all.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so the non-vacuity assertion below -
    placed FIRST - fails naming the missing table.
    MUTATION once step 4 exists: compute `delta` as `float(after) - float(before)`, or round the
    money columns to the equivalent's `precision` (2 here) on the way into the journal. Both survive
    the SQLite tier's values and both destroy this one.
    """
    api = journal_api()
    seeded = await _seed(serializable_factory)
    world = seeded.world
    identity = _identity("full-size-edge")
    try:
        stored_full, full_error = await _round_trip(serializable_factory, world, FULL_SIZE)

        async with serializable_factory() as session:
            async with await _open(api, session, world, "full-size-edge", identity=identity):
                debt = _debt_row(world, FULL_SIZE)
                session.add(debt)
                await session.flush()
                debt.amount = ATOM
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(serializable_factory, identity)
        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY, FIRST. Without the table there is nothing to be right or wrong about, and
        # every assertion below would be about an empty list.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY, MEASURED: this tier really holds the full size exactly. If it did not, the
        # atom comparisons below would be measuring the driver rather than the journal - which is
        # exactly the confusion `EXACT_DOMAIN_LIMIT` exists to prevent on the other tier.
        assert full_error is None and stored_full is not None, (
            f"stand: this database refused {FULL_SIZE} outright ({full_error!r}), so it is not the "
            f"money-acceptance tier design v2 §4 rule 1 describes"
        )
        assert _atoms(stored_full) == _atoms(FULL_SIZE), (
            f"stand: {FULL_SIZE} came back as {stored_full} - {_atoms(stored_full)} atoms instead "
            f"of {_atoms(FULL_SIZE)}. Exact acceptance at full size is the premise of this test."
        )

        # NON-VACUITY: the three flushes really happened and the edge really ended gone.
        assert after == {}, f"stand: the edge survived the operation: {after}"

        # VERDICT.
        assert [row["flush_ordinal"] for row in entries] == [1, 2, 3], entries
        assert [row["effect"] for row in entries] == ["I", "U", "D"], (
            f"the three flushes were not recorded as INSERT, UPDATE, DELETE: {entries}"
        )
        assert _atom_column(entries, "amount_before") == [
            None,
            99999999999999999999,
            1,
        ], f"an entry's `amount_before` is not the previous entry's `amount_after`: {entries}"
        assert _atom_column(entries, "amount_after") == [
            99999999999999999999,
            1,
            None,
        ], entries
        assert _atom_column(entries, "delta") == [
            99999999999999999999,
            -99999999999999999998,
            -1,
        ], (
            f"the deltas are not exact at full money size: {entries}. Every number here is a whole "
            f"count of 1e-8 atoms and is compared as an integer; there is no tolerance on this tier "
            f"(design v2 §4 rule 1)."
        )
    finally:
        await _cleanup(serializable_factory, seeded)


# ==============================================================================================
# C8 - a REAL serialization failure, retried
# ==============================================================================================


@pytest.mark.asyncio
async def test_c8_a_real_40001_leaves_one_envelope_and_only_the_successful_attempts_entries(
    serializable_factory,
):
    """C8, API-SHAPED. The `40001` is GENUINE, produced by real concurrency, never injected.

    WHY THAT DISTINCTION IS THE TEST. Design v2 §9 keeps a fake-`40001` test as a control and puts
    the real one here, and `AGENTS.md` §15 says why: a stand that constructs the error it is
    supposed to observe can hold even when the mechanism it describes does not exist. So nothing
    here raises anything. Two independent SERIALIZABLE transactions both READ the same `debts` row;
    one writes and commits; the other then writes, and PostgreSQL refuses it. The SQLSTATE actually
    received is asserted, so a stand that stopped producing a conflict - a downgraded isolation
    level, a lock wait instead of a conflict - fails as a stand and not as a property.

    WHAT THE JOURNAL MUST DO WITH IT. The losing attempt wrote nothing durable, so it must leave no
    entry. The retry repeats the WHOLE unit of work under the SAME identity - the shape of
    `_run_uow_with_retry` (`app/core/payments/engine.py:472-581`) - and `UNIQUE(kind, identity)`
    must not turn that into a second envelope: exactly one COMPLETED envelope per identity, whose
    entries describe only the attempt that reached the database. `debts`, `transactions`,
    `prepare_locks` and all three journal tables are checked, because a retry that duplicated its
    effects would show up in whichever of them the implementation happened to write first.

    WHAT THIS DOES NOT COVER, and where it is covered now. The retry loop here is written out rather
    than driven through `PaymentEngine`, so it proves nothing about `_is_retryable_db_error`'s
    predicate. Design v2 §9 asks for the real `40001` "on Debt write for INJECT and PAYMENT COMMIT",
    which is the two owners' own loops - `_run_uow_with_retry` and `_apply_inject_event`'s
    `while True:`. Those are the two tests that follow this one (added 2026-09-13 after the external
    review named the gap); this one isolates the journal's obligation under a real conflict with no
    owner in the way, which is why it is kept rather than replaced.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so the non-vacuity assertion placed
    FIRST fails naming the missing table.
    MUTATION once step 4 exists: write journal entries in `before_flush` instead of `after_flush`, so
    the attempt that got the `40001` leaves one behind; or drop the registry on the retry's rollback
    without dropping the OPEN envelope, so the reopen writes a second one.
    """
    api = journal_api()
    seeded = await _seed(serializable_factory)
    world = seeded.world
    identity = _identity("real-40001")
    conflict: DBAPIError | None = None
    sqlstate: str | None = None
    try:
        starting_edge = _debt_row(world, Decimal("10.00000000"))
        async with serializable_factory() as setup:
            async with debt_fixture_setup(setup, label="starting-edge"):
                setup.add(starting_edge)
            await setup.commit()

        mine = select(Debt).where(Debt.equivalent_id == world.equivalent.id)

        async with serializable_factory() as loser, serializable_factory() as winner:
            # Both transactions READ the row first: that is what makes the later write a
            # serialization failure rather than a plain lock wait.
            await loser.execute(mine)

            # BOTH WRITES GO THROUGH THE ORM. They used to be Core `Debt.__table__.update()`,
            # which the journal's write guard refuses outright - that is `C2`, and it is refused
            # whether or not an operation is open, because a Core statement is not in the flush plan
            # the hook verified. The serialization failure this case is about is produced by two
            # SERIALIZABLE transactions that read the same row and then write it, which is exactly
            # what these two ORM writes do.
            winning = (await winner.execute(mine)).scalar_one()
            async with debt_fixture_setup(winner, label="the-winner"):
                winning.amount = Decimal("31.00000000")
            await winner.commit()

            try:
                losing = (await loser.execute(mine)).scalar_one()
                async with await _open(api, loser, world, "real-40001", identity=identity):
                    losing.amount = Decimal("44.00000000")
                    await loser.flush()
                await loser.commit()
            except DBAPIError as exc:
                conflict = exc
                sqlstate = getattr(exc.orig, "sqlstate", None)
            await loser.rollback()

        # THE RETRY: the whole unit of work again, under the same identity, on a fresh transaction.
        async with serializable_factory() as retry:
            debt = (await retry.execute(mine)).scalar_one()
            async with await _open(api, retry, world, "real-40001", identity=identity):
                debt.amount = Decimal("44.00000000")
                await retry.flush()
            await retry.commit()

        entries = await stored_entries(serializable_factory, identity)
        envelopes = await stored_operations(serializable_factory, identity)
        after = await stored_debts(serializable_factory, world)
        async with serializable_factory() as fresh:
            transactions = (
                await fresh.execute(
                    select(Transaction.tx_id).where(
                        Transaction.initiator_id.in_(world.participant_ids)
                    )
                )
            ).scalars().all()
            locks = (
                await fresh.execute(
                    select(PrepareLock.tx_id).where(
                        PrepareLock.participant_id.in_(world.participant_ids)
                    )
                )
            ).scalars().all()

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: the conflict was real, and it was the one PostgreSQL raises for a
        # serialization failure. A stand that produced a lock wait, a deadlock or nothing at all
        # would measure none of what this test is named for.
        assert conflict is not None and sqlstate == "40001", (
            f"stand: the concurrent write was not refused with a genuine serialization failure "
            f"(exception={conflict!r}, sqlstate={sqlstate!r}). Without a real 40001 this test "
            f"observes an ordinary retry and says nothing about C8."
        )
        # NON-VACUITY: the retry really won the edge.
        assert after == {("debtor", "creditor", "eq"): Decimal("44.00000000")}, (
            f"stand: the retry did not end up owning the edge: {after}"
        )

        # VERDICT.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", (
            f"a unit of work that was refused with a 40001 and retried under the same identity left "
            f"{envelopes}. Exactly one COMPLETED envelope per identity is what makes a retried "
            f"writer recognisable instead of counted twice."
        )
        assert len(entries) == 1 and entries[0]["effect"] == "U", (
            f"the attempt that was refused with a 40001 left a trace in the journal: {entries}. "
            f"Only the flush that reached the database may produce an entry."
        )
        assert _atom_column(entries, "amount_before") == [3100000000], (
            f"the retry's entry does not record the value the database actually held when it ran "
            f"({entries}); `amount_before` must be the concurrent writer's value, not the one this "
            f"session had loaded before losing the race"
        )
        assert _atom_column(entries, "delta") == [1300000000], entries
        assert list(transactions) == [] and list(locks) == [], (
            f"the retried operation invented payment state: transactions={list(transactions)}, "
            f"prepare_locks={list(locks)}. A TEST_FIXTURE operation owns neither."
        )
        equivalents = await stored_rows(
            serializable_factory,
            f"SELECT equivalent_id, effect_count FROM {OPERATION_EQUIVALENTS_TABLE} "  # noqa: S608
            f"WHERE operation_id = :id",
            {"id": envelopes[0]["id"]},
        )
        assert equivalents is not None and len(equivalents) == 1, (
            f"the completion did not write exactly one `{OPERATION_EQUIVALENTS_TABLE}` row for the "
            f"one equivalent the retry touched: {equivalents}"
        )
    finally:
        await _cleanup(serializable_factory, seeded)




# ----------------------------------------------------------------------------------------------
# C8, through the PRODUCTION OWNERS. Design v2 §9: "real 40001 on Debt write for inject and
# payment commit". The test above isolates the journal's obligation under a hand-written retry;
# these two drive the loops the application really has.
# ----------------------------------------------------------------------------------------------


def _a_competitor_commits_before_the_first_flow(factory, seeded: _Seeded, amount: Decimal):
    """Make one independent transaction commit a change to the payment's edge, once.

    WHAT IS REAL AND WHAT IS ONLY SEQUENCED. The `40001` is produced by PostgreSQL, not injected:
    two SERIALIZABLE transactions, the payment's snapshot already taken, a committed change to a row
    the payment then writes. What this helper does is decide WHEN the competitor commits - after the
    payment's snapshot and before its first `_apply_flow` - because a conflict that depends on
    scheduling would make the counterexample flaky rather than absent. Nothing here raises anything,
    and `_is_retryable_db_error` is the predicate under test rather than a thing being bypassed.

    THE COMPETITOR WRITES THROUGH THE ORM, inside a declared fixture operation. A Core
    `update(Debt)` would be refused by the write guard (`C2`), correctly - and a competitor that had
    to stand the journal down would be a competitor whose own write the journal never saw, which is
    not the scenario.
    """

    from app.core.payments.engine import PaymentEngine

    world = seeded.world
    real_apply_flow = PaymentEngine._apply_flow
    calls: list[tuple] = []
    sqlstates: list[str | None] = []

    async def _competitor() -> None:
        async with factory() as other:
            debt = (
                await other.execute(select(Debt).where(Debt.equivalent_id == world.equivalent.id))
            ).scalar_one()
            async with debt_fixture_setup(other, label="the-competitor"):
                debt.amount = amount
            await other.commit()

    async def _wrapper(self, *args, **kwargs):
        # The signature is not restated: `_apply_flow(self, from_id, to_id, amount, equivalent_id)`
        # is the engine's, and a wrapper that spelled it out would have to be edited the day it
        # changes - silently passing the wrong argument in the meantime.
        calls.append((args, tuple(sorted(kwargs))))
        if len(calls) == 1:
            await _competitor()
        try:
            return await real_apply_flow(self, *args, **kwargs)
        except DBAPIError as exc:
            sqlstates.append(
                getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            )
            raise

    return _wrapper, calls, sqlstates


@pytest.mark.asyncio
async def test_c8_the_payment_owners_own_retry_leaves_one_envelope_and_the_winning_entries(
    serializable_factory, monkeypatch
):
    """C8, payment owner, API-SHAPED. `PaymentEngine.commit`'s OWN retry loop, on a real `40001`.

    WHY THIS EXISTS SEPARATELY FROM THE TEST ABOVE (external review, 2026-09-13). That one writes
    its retry out by hand - "read, open, write, commit; on 40001 rollback and do it all again" - and
    its own docstring admits it proves nothing about `_is_retryable_db_error`. Design v2 §9 asks for
    the `40001` "on Debt write for inject and PAYMENT COMMIT", which is `_run_uow_with_retry`
    (`app/core/payments/engine.py:513-581`): it decides whether the error is retryable, rolls the
    session back, and re-runs the WHOLE unit of work - including `debt_operation`'s open. A journal
    that only survived a retry the test wrote itself would be worth nothing.

    WHAT MUST HOLD, and every number is read on a session that is not the writer's:
    * exactly ONE `40001`, raised by PostgreSQL at the payment's own `_apply_flow` write;
    * the whole unit of work ran twice - `_apply_flow` is called once per attempt;
    * exactly ONE COMPLETED envelope for this `tx_id`. `UNIQUE(tx_id)` is what would have made a
      second envelope an IntegrityError instead of a duplicate record, so the envelope of the losing
      attempt has to have gone back with its rollback;
    * the entries describe only the attempt that reached the database: one `U`, whose
      `amount_before` is the COMPETITOR's value and not the one this session had loaded;
    * `prepare_locks` are gone, the transaction is COMMITTED, and the equivalents row is written once.

    RED BEFORE STEP 4 BECAUSE: `debt_journal_entries` does not exist, so the non-vacuity assertion
    placed FIRST fails naming the missing table.
    MUTATION: write journal entries in `before_flush` instead of `after_flush`, so the attempt that
    got the `40001` leaves one behind; or complete the envelope without the `state = 'OPEN'`
    predicate, so the retry's completion updates the losing attempt's row.
    """
    from app.core.payments.engine import PaymentEngine

    competitor_amount = Decimal("31.00000000")
    starting = Decimal("10.00000000")
    paid = Decimal("8.00")

    seeded = await _seed(serializable_factory)
    world = seeded.world
    try:
        # Built BEFORE the block: `fixture_block_violations` allows only constructors and session
        # calls inside one, and `_debt_row(...)` is indistinguishable in the AST from a helper that
        # drives a writer. Same object, same single `add`, same flush.
        starting_edge = _debt_row(world, starting)
        async with serializable_factory() as setup:
            async with debt_fixture_setup(setup, label="c8-owner-starting-edge"):
                setup.add(starting_edge)
            await setup.commit()

        tx_id = await _seed_payment(serializable_factory, seeded, amount=str(paid))

        wrapper, calls, sqlstates = _a_competitor_commits_before_the_first_flow(
            serializable_factory, seeded, competitor_amount
        )
        monkeypatch.setattr(PaymentEngine, "_apply_flow", wrapper)
        async with serializable_factory() as commit_session:
            await PaymentEngine(commit_session).commit(tx_id)

        entries = await stored_entries(serializable_factory, tx_id)
        envelopes = await _envelopes_with_intent(serializable_factory, tx_id=tx_id)
        after = await stored_debts(serializable_factory, world)
        tx_state = await stored_rows(
            serializable_factory,
            "SELECT state FROM transactions WHERE tx_id = :tx_id",
            {"tx_id": tx_id},
        )
        surviving_locks = await stored_rows(
            serializable_factory,
            "SELECT id FROM prepare_locks WHERE tx_id = :tx_id",
            {"tx_id": tx_id},
        )

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the conflict was real, it was a serialization failure, and the OWNER retried.
        # A stand that produced a lock wait, or that never conflicted at all, would measure an
        # ordinary payment and say nothing about C8.
        assert sqlstates == ["40001"], (
            f"stand: the payment's own write was not refused with exactly one genuine serialization "
            f"failure (observed {sqlstates}). Without a real 40001 at `_apply_flow` this test "
            f"observes an ordinary commit."
        )
        assert len(calls) == 2, (
            f"stand: `_apply_flow` ran {len(calls)} time(s), so `_run_uow_with_retry` did not re-run "
            f"the whole unit of work and this is not the owner's retry"
        )
        assert after == {
            ("debtor", "creditor", "eq"): competitor_amount + paid
        }, (
            f"stand: the retry did not apply the payment on top of the competitor's value: {after}. "
            f"An amount of {starting + paid} would mean the retry replayed its own stale snapshot "
            f"and silently discarded the concurrent write."
        )
        assert [row["state"] for row in tx_state or []] == ["COMMITTED"], tx_state
        assert surviving_locks == [], (
            f"stand: the prepare locks outlived the committed payment: {surviving_locks}"
        )

        # VERDICT.
        assert len(envelopes) == 1, (
            f"a payment that was refused with a 40001 and retried by its OWN loop left "
            f"{envelopes}. Exactly one envelope per tx_id is what makes a retried writer "
            f"recognisable instead of counted twice; the losing attempt's envelope must have gone "
            f"back with its rollback."
        )
        assert envelopes[0]["kind"] == "PAYMENT" and envelopes[0]["state"] == "COMPLETED", envelopes
        assert entries is not None and len(entries) == 1 and entries[0]["effect"] == "U", (
            f"the attempt that was refused with a 40001 left a trace in the journal: {entries}. "
            f"Only the flush that reached the database may produce an entry."
        )
        assert _atom_column(entries, "amount_before") == [_atoms(competitor_amount)], (
            f"the retry's entry does not record the value the database actually held when it ran "
            f"({entries}); `amount_before` must be the competitor's {competitor_amount}, not the "
            f"{starting} this session had loaded before losing the race"
        )
        assert _atom_column(entries, "delta") == [_atoms(paid)], entries
        equivalents = await stored_rows(
            serializable_factory,
            f"SELECT equivalent_id, effect_count FROM {OPERATION_EQUIVALENTS_TABLE} "  # noqa: S608
            f"WHERE operation_id = :id",
            {"id": envelopes[0]["id"]},
        )
        assert equivalents is not None and len(equivalents) == 1, (
            f"the completion did not write exactly one `{OPERATION_EQUIVALENTS_TABLE}` row for the "
            f"one equivalent the payment touched: {equivalents}"
        )
    finally:
        await _cleanup(serializable_factory, seeded)


@pytest.mark.asyncio
async def test_c8_the_inject_owners_own_retry_leaves_one_envelope_and_the_winning_entries(
    observed_factory,  # noqa: F811 - the proven inject stand, imported rather than rebuilt
):
    """C8, inject owner, API-SHAPED. The other writer design v2 §9 names, on a real `40001`.

    THE STAND IS NOT NEW AND THAT IS DELIBERATE. `tests/integration/
    test_p015_inject_retries_a_serialization_failure_postgres.py` already produces this exact
    conflict against the real inject owner - `_apply_due_scenario_events` ->
    `_apply_inject_event`'s `while True:` loop - and asserts that the unit of work ran again and
    that the stored amount is the concurrent value plus the injected one exactly once. Its helpers
    are imported here rather than rewritten: a second stand for the same conflict would be a second
    thing to keep correct, and the part C8 adds is about the JOURNAL, not about the retry.

    WHAT THIS ADDS. The inject owner opens its envelope INSIDE the retry loop, once per attempt
    (`app/core/simulator/real_runner_impl.py:653`), under an identity that is the same string on
    every attempt (`run_id:event_index`). Both halves of that are load-bearing and neither is
    covered by the retry test: if the rolled-back attempt's envelope survived, the second attempt
    would collide on `UNIQUE(kind, identity)`; if the envelope were opened OUTSIDE the loop, the
    second attempt would be refused for nesting inside the first. Exactly one COMPLETED envelope
    with exactly the winning attempt's entries is the only outcome consistent with both.

    RED BEFORE STEP 4 BECAUSE: `debt_operations` does not exist, and the non-vacuity assertion
    placed FIRST says so.
    MUTATION: open the inject's operation outside the `while True:` loop, or leave the rolled-back
    attempt's envelope in place (complete it without the `state = 'OPEN'` predicate). Both produce
    either two envelopes or a refusal, and this test names which.
    """
    from sqlalchemy import update as sa_update

    from app.core.ledger import journal
    from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (
        _Artifacts,
        _cleanup as _cleanup_inject_world,
        _run,
        _runner,
        _seed as _seed_inject_world,
        _stored as _stored_inject,
    )

    existing = Decimal("5.00")
    concurrent = Decimal("7.12345678")
    injected = Decimal("3.00")

    world = await _seed_inject_world(observed_factory)
    equivalent = world.equivalents[0]
    run_id = f"p015-b4-c8-inject-{uuid.uuid4().hex[:8]}"
    identity = f"{run_id}:0"
    try:
        async with observed_factory() as setup:
            async with debt_fixture_setup(setup, label="c8-inject-starting-edge"):
                setup.add(
                    Debt(
                        debtor_id=world.debtor.id,
                        creditor_id=world.creditor.id,
                        equivalent_id=equivalent.id,
                        amount=existing,
                    )
                )
            await setup.commit()

        creditor, debtor = world.creditor.pid, world.debtor.pid
        scenario = {
            "equivalents": [eq.code for eq in world.equivalents],
            "participants": [{"id": creditor}, {"id": debtor}],
            "trustlines": [
                {
                    "from": creditor,
                    "to": debtor,
                    "equivalent": equivalent.code,
                    "limit": "100.00",
                    "status": "active",
                }
            ],
            "behaviorProfiles": [],
            "events": [
                {
                    "type": "inject",
                    "time": 0,
                    "effects": [
                        {
                            "op": "inject_debt",
                            "from": creditor,
                            "to": debtor,
                            "equivalent": equivalent.code,
                            "amount": str(injected),
                        }
                    ],
                }
            ],
        }
        run = _run(world, run_id)
        artifacts = _Artifacts()
        runner = _runner(run, scenario, artifacts)

        real_stage = runner._inject_executor.stage_inject_event
        stage_calls = 0

        async def _stage_then_a_competitor_commits(session, **kwargs):
            nonlocal stage_calls
            stage_calls += 1
            staged = await real_stage(session, **kwargs)  # has read the debt at 5.00
            if stage_calls == 1:
                # THE COMPETITOR, exactly as the step-3 retry stand builds it: a Core `update(Debt)`
                # with the journal stood down on this engine only. It has to stay a Core statement -
                # routing it through the ORM would add a third debt flush to `_observations`, which
                # the sibling stand counts - and standing the journal down is what lets a Core
                # statement through at all (`C2`). What it stands for is "somebody else committed
                # the row", and the `40001` that follows is PostgreSQL's, not this helper's.
                engine = observed_factory.kw.get("bind")
                journal.uninstall_write_guard(engine)
                try:
                    async with observed_factory() as other:
                        await other.execute(
                            sa_update(Debt)
                            .where(
                                Debt.debtor_id == world.debtor.id,
                                Debt.creditor_id == world.creditor.id,
                                Debt.equivalent_id == equivalent.id,
                            )
                            .values(amount=concurrent)
                        )
                        await other.commit()
                finally:
                    journal.install_write_guard(engine)
            return staged

        runner._inject_executor.stage_inject_event = _stage_then_a_competitor_commits

        sqlstates: list[str | None] = []

        async with observed_factory() as session:
            real_flush = session.flush

            async def _flush(*args, **kwargs):
                try:
                    return await real_flush(*args, **kwargs)
                except DBAPIError as exc:
                    sqlstates.append(
                        getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                    )
                    raise

            session.flush = _flush  # type: ignore[method-assign]
            await runner._apply_due_scenario_events(
                session, run_id=run.run_id, run=run, scenario=scenario
            )

        envelopes = await stored_operations(observed_factory, identity)
        entries = await stored_entries(observed_factory, identity)
        stored = await _stored_inject(observed_factory, world)

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the conflict was real, at the owner's explicit flush of the staged writes,
        # and the owner re-ran the WHOLE unit of work rather than only the commit.
        assert sqlstates == ["40001"], (
            f"stand: the inject's staged write was not refused with exactly one genuine "
            f"serialization failure at the owner's flush (observed {sqlstates})"
        )
        assert stage_calls == 2, (
            f"stand: the unit of work was staged {stage_calls} time(s), so the owner did not re-run "
            f"it and this is not the inject owner's retry"
        )
        assert stored == {equivalent.id: concurrent + injected}, (
            f"stand: expected the concurrent {concurrent} plus the injected {injected} exactly once, "
            f"stored {stored}"
        )

        # VERDICT.
        assert len(envelopes) == 1, (
            f"an inject that was refused with a 40001 and retried by its own loop left {envelopes} "
            f"for identity {identity}. The envelope is opened per ATTEMPT under an identity that is "
            f"per EVENT, so the rolled-back attempt's envelope must be gone - otherwise the retry "
            f"collides on UNIQUE(kind, identity) instead of recording the work it did."
        )
        assert envelopes[0]["kind"] == "INJECT" and envelopes[0]["state"] == "COMPLETED", envelopes
        assert envelopes[0]["tx_id"] is None, (
            f"an INJECT envelope carries no tx_id (design v2 §5): {envelopes}"
        )
        assert entries is not None and len(entries) == 1 and entries[0]["effect"] == "U", (
            f"the attempt that was refused with a 40001 left a trace in the journal: {entries}"
        )
        assert _atom_column(entries, "amount_before") == [_atoms(concurrent)], (
            f"the retry's entry does not record the value the database held when it ran ({entries}); "
            f"`amount_before` must be the competitor's {concurrent}, not the {existing} the first "
            f"attempt had read"
        )
        assert _atom_column(entries, "delta") == [_atoms(injected)], entries
    finally:
        await _cleanup_inject_world(observed_factory, world)


# ==============================================================================================
# C12-P - what this dialect really does with money, measured
# ==============================================================================================


#: `(label, value, what THIS database did with it on 2026-09-12, is the refusal dialect-dependent)`.
#: The third element is measured, not asserted from theory, and is quoted in the failure so the
#: reader of a red run sees what is really in the database. PostgreSQL 16.9, `NUMERIC(20,8)`.
_REFUSED_ON_POSTGRES = [
    (
        "more than eight decimal places",
        "0.123456789",
        "stored as 0.12345679 - the ninth place is silently rounded away",
        True,
    ),
    (
        "not a number",
        "NaN",
        "STORED AS NaN, and `chk_debt_amount_positive` lets it through because in PostgreSQL "
        "NaN > 0 is TRUE - the database now holds a debt that is not a number",
        False,
    ),
    (
        "infinite",
        "Infinity",
        "refused by the column with SQLSTATE 22003 (numeric field overflow), AFTER the statement "
        "was sent and naming the column rather than the money domain",
        False,
    ),
    (
        "at the magnitude ceiling",
        "1E12",
        "refused by the column with SQLSTATE 22003, AFTER the statement was sent",
        False,
    ),
]


@pytest.mark.parametrize(
    "label,value,today,dialect_dependent",
    _REFUSED_ON_POSTGRES,
    ids=[row[0] for row in _REFUSED_ON_POSTGRES],
)
@pytest.mark.asyncio
async def test_c12_p_a_value_outside_the_money_domain_is_refused_before_any_debt_sql(
    serializable_factory, serializable_engine, label, value, today, dialect_dependent
):
    """C12-P, DEFECT-SHAPED. Two different reasons to refuse, and only one of them is about SQLite.

    DESIGN V2 §4 RULE 3 HAS A DIALECT CLAUSE AND A CONSTANT PART, and this parametrisation is the
    line between them.

    * `0.123456789` is refused HERE for the same reason it is refused on SQLite - the dialect would
      silently change it - but the two dialects change DIFFERENT values, which is why the rule is
      `result_processor(bind_processor(v)) == v` for the session's actual dialect and not a constant
      scale check. The control below shows the other side: `100000000000.00000001` is refused on
      SQLite and must be ACCEPTED here.
    * `NaN`, `Infinity` and `1E12` are refused on EVERY dialect, and none of the three is caught by
      a round-trip check. `NaN` is the sharpest: this database stores it happily, and
      `chk_debt_amount_positive` does not object because PostgreSQL orders NaN above every number.
      A money core whose only defence is the column definition therefore has NO defence against it.

    THE ORDER IS THE OTHER HALF OF THE REQUIREMENT. This test watches the connection instead of
    reading the table afterwards, because a statement that ran and was rolled back leaves the same
    empty table as one that never ran. Accepting the first would be leaning on a rollback to undo a
    corruption - the "compensation further downstream" `AGENTS.md` §9 forbids - and for `NaN` there
    would be nothing to lean on at all.

    RED TODAY BECAUSE: nothing checks storability. The INSERT is sent every time, and this database
    then {today}.
    MUTATION once step 4 exists: check the value against `Decimal`'s own scale and range instead of
    against the dialect's round-trip; `0.123456789` then passes on PostgreSQL because `Decimal`
    represents it perfectly and only the COLUMN would lose it. Or move the check into `after_flush`,
    where every case here has already been sent.
    """
    api = journal_api()
    seeded = await _seed(serializable_factory)
    world = seeded.world
    amount = Decimal(value)
    try:
        stored, error = await _round_trip(serializable_factory, world, amount)

        # NON-VACUITY, FIRST, and MEASURED: this value really is one this money core must not
        # accept - either because the dialect changes it, or because the column itself objects, or
        # because it is outside the domain design v2 §4 defines at all. Two of the four cases here
        # are round-trip failures and two are not, so a bare "it changed" assertion would be false
        # for half of them and a test that asserted nothing here could pass having measured a value
        # that is perfectly legal.
        assert stored != amount or error is not None or not _obeys_the_money_rules(amount), (
            f"stand: this database stored {value} unchanged (read back {stored!r}) and the money "
            f"rules of design v2 §4 permit it, so `{label}` is no longer an unacceptable value here "
            f"and this counterexample has lost its subject"
        )
        # NON-VACUITY: the parametrisation's claim about WHY it is unacceptable is checked, not
        # asserted in prose. A dialect-dependent case must be one the database changed silently; a
        # dialect-independent one must be forbidden by the rule itself, whatever the database does.
        if dialect_dependent:
            assert error is None and stored is not None and stored != amount, (
                f"stand: `{label}` is recorded as dialect-dependent, but this database did not "
                f"silently change it (stored={stored!r}, error={error!r})"
            )
        else:
            assert not _obeys_the_money_rules(amount), (
                f"stand: `{label}` is recorded as forbidden on every dialect, but the restated "
                f"rules of design v2 §4 accept {value}"
            )

        recorder = _watch_debt_statements(serializable_engine)
        refusal = None
        try:
            async with serializable_factory() as session:
                async with await _open(api, session, world, "unstorable"):
                    session.add(_debt_row(world, amount))
                    refusal = await _refusal_or_database_error(api, session.flush())
                if refusal is None:
                    refusal = await _refusal_or_database_error(api, session.commit())
        except (DatabaseError, StatementError, *api.refusals) as exc:
            # The database's own complaint is not the journal's refusal, and the `seen` assertion
            # below is what says so. Recorded here only so the scenario finishes.
            #
            # A journal refusal can arrive here as well, from the operation's own completion: the
            # hook refused the flush and poisoned the root, the Debt is still pending, and closing
            # the block flushes it again. The FIRST refusal is the one that names the money
            # predicate, so it is the one kept.
            refusal = refusal if refusal is not None else exc
        finally:
            event.remove(serializable_engine.sync_engine, "before_execute", recorder)

        after = await stored_debts(serializable_factory, world)

        # VERDICT.
        assert not recorder.seen, (
            f"a debt amount of {value} ({label}) reached the `debts` table as {recorder.seen}; this "
            f"database {today}. A value outside the money domain of design v2 §4 must be refused by "
            f"{JOURNAL_MODULE} BEFORE the statement is sent, not stored and corrected afterwards. "
            f"The table now holds {after or 'nothing, because the database itself objected'}."
        )
        assert isinstance(refusal, api.refusals), (
            f"the refusal of a debt amount of {value} ({label}) was {refusal!r}, which is not one "
            f"of {JOURNAL_MODULE}'s refusal types. This database {today}: the column's own complaint "
            f"arrives after the statement, names the wrong problem, and for the cases this backend "
            f"stores happily it does not exist at all."
        )
        assert after == {}, f"the unacceptable amount is durable: {after}"
    finally:
        await _cleanup(serializable_factory, seeded)


@pytest.mark.parametrize(
    "value,why_here",
    [
        (
            "100000000000.00000001",
            "REFUSED on SQLite, where it reads back as 100000000000.00000000 - this single value "
            "is what makes design v2 §4 rule 3 a per-dialect rule instead of a constant",
        ),
        (
            "999999999999.99999999",
            "the largest amount NUMERIC(20,8) can hold, four orders of magnitude past anything the "
            "SQLite tier is allowed to touch",
        ),
        ("1.000000000", "nine decimal places whose VALUE is unchanged by quantisation to scale 8"),
        ("0.00000001", "one atom - the smallest amount the money domain contains"),
    ],
)
@pytest.mark.asyncio
async def test_c12_p_control_this_dialect_stores_the_whole_domain_exactly(
    serializable_factory, value, why_here
):
    """C12-P, anti-vacuum control. GREEN today and after step 4, and it carries the dialect clause.

    The refusals above are only meaningful if the rule they encode has a passing side, and design
    v2 §4 rule 1 names that side exactly: PostgreSQL is the money-acceptance tier, so every value
    inside the domain must be stored and read back as the SAME COUNT OF ATOMS. A storability rule
    that refused any of these would stop payments from happening at all.

    The first case is the one that earns this test its place on the PostgreSQL tier:
    `tests/unit/test_p015_b4_entries_and_money.py` lists `100000000000.00000001` among the values
    SQLite silently changes and requires it to be REFUSED there. Here it must be accepted. Two tiers,
    the same rule, opposite answers - which is precisely why the rule is written against the
    session's actual dialect.
    """
    seeded = await _seed(serializable_factory)
    try:
        stored, error = await _round_trip(serializable_factory, seeded.world, Decimal(value))
        assert error is None, f"this database refused a legitimate amount {value} ({why_here}): {error!r}"
        assert stored is not None and _atoms(stored) == _atoms(Decimal(value)), (
            f"{value} is supposed to be stored exactly on this dialect ({why_here}) and came back "
            f"as {stored!r} - {_atoms_or_none(stored)} atoms instead of {_atoms(Decimal(value))}"
        )
    finally:
        await _cleanup(serializable_factory, seeded)


# ==============================================================================================
# C13-P - two concurrent openers of one identity
# ==============================================================================================


@pytest.mark.asyncio
async def test_c13_p_two_concurrent_openers_of_one_identity_and_the_database_refuses_one(
    serializable_factory,
):
    """C13-P, API-SHAPED. The test the SQLite sibling names in its own mutation line.

    WHY IT HAS TO EXIST. The sibling's `C13` opens a second operation under a spent identity one
    statement after the first one finished, and its docstring says what that cannot rule out: an
    implementation that pre-checks the identity with a `SELECT` and skips the INSERT when a row is
    found passes it every time. Two writers that both read "no envelope" and both proceed are
    invisible to any single-threaded test. Only `UNIQUE(kind, identity)` - a DATABASE constraint,
    design v2 §5 - can decide this, and only two independent connections released together can ask.

    HOW THE OVERLAP IS FORCED. `asyncio.Barrier(2)`, not a sleep. Both openers reach the barrier,
    are released in the same event-loop step, and only then touch the database; neither has taken
    any action the other could have observed. The event log below records that both arrived before
    either was released, so a stand whose barrier had silently degraded into a sequence - a task
    that failed early, a barrier that broke - fails as a stand.

    The two writers deliberately write DIFFERENT edges, so nothing about `debts` can serialise them:
    if one of them is refused, the refusal is about the identity and nothing else.

    RED TODAY BECAUSE: `debt_operations` does not exist, so there is no envelope to collide with and
    the non-vacuity assertion placed FIRST fails naming the table.
    MUTATION once step 4 exists: pre-check the identity with a `SELECT` and skip the INSERT when a
    row is found. The SQLite sibling stays green; this one goes red, and it is the only thing in the
    suite that can.
    """
    api = journal_api()
    seeded = await _seed(serializable_factory, extra_participants=1)
    world = seeded.world
    identity = _identity("concurrent-identity")
    barrier = asyncio.Barrier(2)
    log: list[str] = []

    async def _opener(name: str, creditor_id) -> BaseException | None:
        async with serializable_factory() as session:
            # Take the connection BEFORE the barrier, so the rendezvous measures the openers and
            # not the pool.
            await session.execute(text("SELECT 1"))
            log.append(f"{name}:at-barrier")
            await barrier.wait()
            log.append(f"{name}:released")
            try:
                async with await _open(
                    api,
                    session,
                    world,
                    "concurrent-identity",
                    identity=identity,
                    intent=_intent(opener=name),
                ):
                    session.add(_debt_row(world, Decimal("5.00000000"), creditor_id=creditor_id))
                    await session.flush()
                await session.commit()
            except BaseException as exc:  # recorded, classified below
                await session.rollback()
                return exc
            return None

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                _opener("a", world.creditor.id),
                _opener("b", world.extra_participants[0].id),
                return_exceptions=True,
            ),
            timeout=30,
        )
        envelopes = await stored_operations(serializable_factory, identity)
        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the two openers really overlapped. Both reached the barrier before either
        # was released, so neither could have seen the other's envelope by ordinary sequencing.
        assert log[:2] == ["a:at-barrier", "b:at-barrier"] or log[:2] == [
            "b:at-barrier",
            "a:at-barrier",
        ], f"stand: the two openers did not both reach the barrier before either proceeded: {log}"
        assert not barrier.broken, "stand: the barrier broke, so the two openers never overlapped"

        refused = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        succeeded = [outcome for outcome in outcomes if outcome is None]

        # VERDICT.
        assert len(succeeded) == 1 and len(refused) == 1, (
            f"two concurrent openers of the identity {identity!r} produced {outcomes} - "
            f"{len(succeeded)} success(es) and {len(refused)} refusal(s). An identity is spent once: "
            f"`UNIQUE(kind, identity)` must let exactly one of them through, and it must be the "
            f"DATABASE that decides, because both of them read the table before either wrote to it."
        )
        assert isinstance(refused[0], (DatabaseError, StatementError, *api.refusals)), (
            f"the loser of the race was refused by {refused[0]!r}, which is neither the database's "
            f"integrity error nor one of {JOURNAL_MODULE}'s refusal types"
        )
        assert len(envelopes) == 1, f"the refused opener still left an envelope behind: {envelopes}"
        assert len(after) == 1, (
            f"both openers' debts are durable, so neither operation was refused at all: {after}"
        )
    finally:
        await _cleanup(serializable_factory, seeded)


# ==============================================================================================
# C14 - the real owners: PaymentEngine.prepare/.commit and ClearingService
# ==============================================================================================


async def _seed_payment(factory, seeded: _Seeded, *, amount: str, limit: str = "500.00"):
    """A trustline, a NEW transaction and a real `PaymentEngine.prepare`. Returns the tx id.

    The trustline runs CREDITOR -> DEBTOR (`AGENTS.md` §8): the receiver's line to the sender is what
    limits how much the sender may come to owe, and `_get_segment_capacity_and_reserved_usage`
    (`app/core/payments/engine.py:640-661`) reads exactly that pair.
    """
    from app.core.payments.engine import PaymentEngine

    world = seeded.world
    tx_id = str(uuid.uuid4())
    seeded.tx_ids.append(tx_id)
    async with factory() as setup:
        setup.add(
            TrustLine(
                from_participant_id=world.creditor.id,
                to_participant_id=world.debtor.id,
                equivalent_id=world.equivalent.id,
                limit=Decimal(limit),
                policy={"auto_clearing": True},
                status="active",
            )
        )
        setup.add(
            Transaction(
                id=uuid.UUID(tx_id),
                tx_id=tx_id,
                type="PAYMENT",
                initiator_id=world.debtor.id,
                payload={},
                state="NEW",
            )
        )
        await setup.commit()

    async with factory() as prepare_session:
        await PaymentEngine(prepare_session).prepare(
            tx_id,
            [world.debtor.pid, world.creditor.pid],
            Decimal(amount),
            world.equivalent.id,
        )
    return tx_id


class _PrepareLockDeleteWatcher:
    """Reads the journal's envelope table ON THE CONNECTION that is deleting the prepare locks.

    WHY THIS SHAPE AND NOT A READ AFTERWARDS. Design v2 §7 requires the payment's envelope to be
    "flushed at open i.e. before delete(PrepareLock)". After the commit, both statements are equally
    durable and their order is gone. The only place the order is observable is inside the
    transaction, on the connection doing the work - so this listener fires on the `DELETE FROM
    prepare_locks` and asks that same connection whether the envelope row is already there.

    `to_regclass` is asked first and answers NULL for a table that does not exist, instead of
    raising. That matters on PostgreSQL: a failed statement aborts the surrounding transaction, so a
    probe that raised would destroy the very payment it is observing and this counterexample would
    be red for a reason of its own making.
    """

    def __init__(self, tx_id: str) -> None:
        self.tx_id = tx_id
        self.deletes = 0
        self.table_present: bool | None = None
        self.envelopes_visible: int | None = None
        self._busy = False

    def __call__(self, conn, clauseelement, _multiparams, _params, _options) -> None:
        table = getattr(getattr(clauseelement, "table", None), "name", None)
        if table != "prepare_locks" or not getattr(clauseelement, "is_delete", False):
            return
        if self._busy:
            return
        self._busy = True
        try:
            self.deletes += 1
            present = conn.exec_driver_sql(
                f"SELECT to_regclass('{OPERATIONS_TABLE}')"  # noqa: S608
            ).scalar()
            self.table_present = present is not None
            if self.table_present:
                self.envelopes_visible = conn.exec_driver_sql(
                    f"SELECT count(*) FROM {OPERATIONS_TABLE} "  # noqa: S608
                    f"WHERE tx_id = '{self.tx_id}'"
                ).scalar()
        finally:
            self._busy = False


@pytest.mark.asyncio
async def test_c14_the_payment_envelope_is_written_before_its_prepare_locks_are_deleted(
    serializable_factory, serializable_engine
):
    """C14, payment half, API-SHAPED. Driven by the REAL owner, not by a bare session.

    WHAT IS REAL HERE. `PaymentEngine.prepare` writes the `PrepareLock` rows and
    `PaymentEngine.commit` consumes them, applies the flows, writes the integrity audit row, deletes
    the locks and marks the transaction COMMITTED (`app/core/payments/engine.py:1069`, `:1437-1447`).
    Nothing is simulated. That is the whole point of C14: every other counterexample in this
    programme opens its own operation, and a journal that worked only for operations opened by tests
    would be worth nothing.

    THE THREE OBLIGATIONS, and why each needs where it is read from named:

    1. The envelope is COMPLETED, read on an INDEPENDENT connection after the commit - never through
       the session under test, whose identity map answers from memory.
    2. The recorded intent equals a `PrepareLock.effects` snapshot captured BEFORE the commit. The
       snapshot has to be taken first because `commit` deletes the locks: an intent compared against
       what survives the commit could be compared against nothing at all and still look right.
    3. The ORDER: a listener on `DELETE FROM prepare_locks` must see the envelope row ALREADY
       PRESENT on that same connection. Design v2 §7 puts the envelope flush at open, before the
       delete, and after the commit the two statements are indistinguishable - so this is the only
       moment at which the requirement is observable at all.

    RED TODAY BECAUSE: `debt_operations` does not exist. The non-vacuity assertion placed FIRST says
    so, and the listener's own measurement - which runs today and reports `table_present=False` -
    is what will carry the ordering requirement once it does.
    MUTATION once step 4 exists: open the payment's operation AFTER the flows are applied, or flush
    the envelope lazily at completion instead of at open. Both leave assertions 1 and 2 green and
    turn assertion 3 red, which is exactly the asymmetry this test is built to catch.
    """
    from app.core.payments.engine import PaymentEngine

    api = journal_api()
    assert api is not None  # the handle is resolved inside the body; see tests/p015_b4_support.py
    seeded = await _seed(serializable_factory)
    world = seeded.world
    try:
        tx_id = await _seed_payment(serializable_factory, seeded, amount="8.00")

        # The snapshot BEFORE the commit, on its own session: `commit` is about to delete these rows.
        snapshot = await stored_rows(
            serializable_factory,
            "SELECT participant_id::text AS participant_id, effects FROM prepare_locks "
            "WHERE tx_id = :tx_id ORDER BY participant_id",
            {"tx_id": tx_id},
        )

        watcher = _PrepareLockDeleteWatcher(tx_id)
        event.listen(serializable_engine.sync_engine, "before_execute", watcher)
        try:
            async with serializable_factory() as commit_session:
                await PaymentEngine(commit_session).commit(tx_id)
        finally:
            event.remove(serializable_engine.sync_engine, "before_execute", watcher)

        envelopes = await _envelopes_with_intent(serializable_factory, tx_id=tx_id)
        after = await stored_debts(serializable_factory, world)
        tx_state = await stored_rows(
            serializable_factory,
            "SELECT state FROM transactions WHERE tx_id = :tx_id",
            {"tx_id": tx_id},
        )
        surviving_locks = await stored_rows(
            serializable_factory,
            "SELECT id FROM prepare_locks WHERE tx_id = :tx_id",
            {"tx_id": tx_id},
        )

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the real owner really ran, really deleted its locks, and really moved money.
        assert snapshot, f"stand: `prepare` wrote no PrepareLock for {tx_id}, so there was nothing to delete"
        assert watcher.deletes == 1, (
            f"stand: the listener saw {watcher.deletes} DELETE(s) against `prepare_locks` during the "
            f"commit; the ordering requirement below has nothing to attach to"
        )
        assert surviving_locks == [], f"stand: the prepare locks outlived the commit: {surviving_locks}"
        assert [row["state"] for row in tx_state or []] == ["COMMITTED"], tx_state
        assert after == {("debtor", "creditor", "eq"): Decimal("8.00000000")}, (
            f"stand: the real payment did not move the money it was prepared for: {after}"
        )

        # VERDICT.
        assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", (
            f"a committed payment left {envelopes} instead of exactly one COMPLETED envelope for "
            f"tx_id={tx_id}"
        )
        assert envelopes[0]["kind"] == "PAYMENT", envelopes
        recorded = _decoded_intent(envelopes[0])
        expected_flows = [_decoded_json(row["effects"]) for row in snapshot]
        assert _flows_of(recorded) == _flows_of({"flows": expected_flows}), (
            f"the envelope's intent does not describe the prepared effects it committed. Recorded: "
            f"{recorded}. The locks held, immediately before the commit deleted them: "
            f"{expected_flows}. Design v2 §7 requires the intent to be the validated flows per lock, "
            f"as exact scale-8 strings, so step 6 can recompute the payment from the envelope alone."
        )
        assert watcher.table_present is True and watcher.envelopes_visible == 1, (
            f"at the moment the commit deleted the prepare locks, the connection doing the deleting "
            f"could see {watcher.envelopes_visible!r} envelope row(s) for this payment "
            f"(table_present={watcher.table_present!r}). Design v2 §7 flushes the envelope AT OPEN, "
            f"before `delete(PrepareLock)`: an envelope written later is an envelope that a crash "
            f"between the two statements would lose, leaving a payment whose locks are gone and "
            f"whose journal never began."
        )
    finally:
        await _cleanup(serializable_factory, seeded)


def _flows_of(intent) -> list[dict]:
    """Every `{from,to,amount,equivalent}` flow inside a payment intent, normalised and sorted.

    Design v2 §7 fixes the CONTENT ("validated flows per lock as exact scale-8 strings") and not the
    nesting, so this reads whatever shape step 4 chooses and compares the flows themselves. Amounts
    are compared as integer atoms, because `"8.00"` and `"8.00000000"` are the same money and a
    counterexample that failed on the spelling would be testing a serializer.
    """
    if intent is None:
        return []
    found: list[dict] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            if {"from", "to", "amount"} <= set(node):
                found.append(
                    {
                        "from": str(node["from"]),
                        "to": str(node["to"]),
                        "atoms": _atoms(Decimal(str(node["amount"]))),
                        "equivalent": str(node.get("equivalent", "")),
                    }
                )
                return
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(intent)
    return sorted(found, key=lambda flow: (flow["from"], flow["to"], flow["atoms"]))


@pytest.mark.asyncio
async def test_c14_the_clearing_envelope_records_the_pre_amounts_it_actually_cleared(
    serializable_factory,
):
    """C14, clearing half, API-SHAPED. `ClearingService.execute_clearing_with_amount`, for real.

    WHAT IS REAL HERE. A genuine three-edge cycle with genuine auto-clearing trustlines, cleared by
    the service's own entry point - which on PostgreSQL rolls the caller's session back, checks out
    a PINNED work connection, takes the session-level equivalent owner lock and runs the whole
    clearing on a work session of its own (`app/core/clearing/service.py:1494-1644`). A test that
    handed it a connection-bound session would be refused outright, which is a second reason this
    counterexample cannot live on the `db_session` fixture.

    WHAT THE ENVELOPE MUST CARRY. Design v2 §7: the clearing intent includes the cycle's debt ids
    with their `FOR UPDATE` amounts and the clear amount. The pre-amounts are captured here on an
    independent session BEFORE the clearing runs, because after it runs they are gone - each edge is
    reduced by the cleared amount, and the minimum edge is deleted entirely. An intent checked
    against post-clearing state could only ever be checked against the answer.

    RED TODAY BECAUSE: `debt_operations` does not exist; the non-vacuity assertion placed FIRST says
    so.
    MUTATION once step 4 exists: build the clearing intent from the amounts read at candidate
    detection rather than from the locked read at `:1715`. The cycle is unchanged whenever nothing
    else is writing, so every single-threaded clearing test stays green and the envelope quietly
    starts describing a state the clearing did not act on.
    """
    from app.core.clearing.service import ClearingService

    api = journal_api()
    assert api is not None  # resolved inside the body; see tests/p015_b4_support.py
    seeded = await _seed(serializable_factory, extra_participants=1)
    world = seeded.world
    a, b, c = world.debtor, world.creditor, world.extra_participants[0]
    debt_ids = [uuid.uuid4() for _ in range(3)]
    try:
        # Built before the fixture block: `fixture_block_violations` allows only constructors and
        # session calls inside one, and a comprehension is control flow.
        cycle_debts = [
            Debt(
                id=debt_id,
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=world.equivalent.id,
                amount=Decimal(amount),
                version=0,
            )
            for debt_id, debtor, creditor, amount in (
                (debt_ids[0], a, b, "100.00000000"),
                (debt_ids[1], b, c, "30.00000000"),
                (debt_ids[2], c, a, "40.00000000"),
            )
        ]
        async with serializable_factory() as setup:
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=creditor.id,
                        to_participant_id=debtor.id,
                        equivalent_id=world.equivalent.id,
                        limit=Decimal("500.00"),
                        policy={"auto_clearing": True},
                        status="active",
                    )
                    for debtor, creditor in ((a, b), (b, c), (c, a))
                ]
            )
            async with debt_fixture_setup(setup, label="cycle"):
                setup.add_all(cycle_debts)
            await setup.commit()

        # The pre-amounts, captured independently and BEFORE the clearing runs.
        async with serializable_factory() as reader:
            pre_amounts = {
                str(debt_id): _atoms(amount)
                for debt_id, amount in (
                    await reader.execute(
                        select(Debt.id, Debt.amount).where(Debt.id.in_(debt_ids))
                    )
                ).all()
            }

        cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
        async with serializable_factory() as clearing_session:
            service = ClearingService(clearing_session)
            execution_tx_id = service._execution_tx_id(debt_ids)
            seeded.tx_ids.append(execution_tx_id)
            cleared = await asyncio.wait_for(
                service.execute_clearing_with_amount(cycle), timeout=60
            )

        envelopes = await _envelopes_with_intent(serializable_factory, tx_id=execution_tx_id)
        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: a real clearing really ran and really moved the exact amount it should have.
        assert cleared is not None and _atoms(cleared) == 3000000000, (
            f"stand: the clearing returned {cleared!r} instead of the cycle minimum 30.00000000, so "
            f"it was skipped and this test observes nothing"
        )
        assert {key: _atoms(value) for key, value in after.items()} == {
            ("debtor", "creditor", "eq"): 7000000000,
            ("extra0", "debtor", "eq"): 1000000000,
        }, f"stand: the cycle was not reduced by exactly 30.00000000 on every edge: {after}"

        # VERDICT.
        assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", (
            f"a committed clearing left {envelopes} instead of exactly one COMPLETED envelope for "
            f"tx_id={execution_tx_id}"
        )
        assert envelopes[0]["kind"] == "CLEARING", envelopes
        recorded = _decoded_intent(envelopes[0])
        assert _debt_atoms_in(recorded) == pre_amounts, (
            f"the clearing's intent does not record the amounts it actually acted on. Recorded: "
            f"{_debt_atoms_in(recorded)}. Captured independently before the clearing ran: "
            f"{pre_amounts}. Design v2 §7 requires the cycle's debt ids with their locked "
            f"(`FOR UPDATE`) amounts, because step 6 reconstructs the cleared cycle from the "
            f"envelope and nothing else survives the clearing to be compared against."
        )
    finally:
        await _cleanup(serializable_factory, seeded)


def _debt_atoms_in(intent) -> dict[str, int]:
    """Every `{debt_id, amount}` pair inside a clearing intent, as `{id: atoms}`.

    Like `_flows_of`, this reads content rather than nesting: design v2 §7 fixes what the intent must
    say, not how step 4 arranges it.
    """
    found: dict[str, int] = {}

    def _walk(node) -> None:
        if isinstance(node, dict):
            if "debt_id" in node and "amount" in node:
                found[str(node["debt_id"])] = _atoms(Decimal(str(node["amount"])))
                return
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(intent)
    return found


# ==============================================================================================
# C17-P and design v2 §10.1 - deleting an equivalent the history names
# ==============================================================================================


def _admin_request():
    """The minimum ASGI scope `admin_delete_equivalent` reads: `.headers` and `.client`.

    The route function is called DIRECTLY rather than over HTTP. That is a real limit and it is
    named here rather than left implicit: this exercises the handler, its owner lock, its usage
    check and its `IntegrityError` translation, and it exercises NONE of the HTTP layer - no auth
    dependency, no request validation, no response serialisation, no status code. The 409 asserted
    below is `ConflictException.status_code`, which is what the exception handler would turn into a
    response, not a response that was observed.
    """
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "DELETE",
            "path": "/api/v1/admin/equivalents",
            "raw_path": b"/api/v1/admin/equivalents",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("testserver", 80),
        }
    )


async def _deactivate(factory, world: World) -> None:
    """`admin_delete_equivalent` refuses an ACTIVE equivalent before anything else it does."""
    async with factory() as session:
        await session.execute(
            Equivalent.__table__.update()
            .where(Equivalent.id == world.equivalent.id)
            .values(is_active=False)
        )
        await session.commit()


async def _history_only_equivalent(api, factory, world: World, identity: str) -> None:
    """An operation that creates one debt and removes it again: history, and no `debts` row left."""
    async with factory() as session:
        async with await _open(api, session, world, "history", identity=identity):
            debt = _debt_row(world, Decimal("23.00000000"))
            session.add(debt)
            await session.flush()
            await session.delete(debt)
            await session.flush()
        await session.commit()


@pytest.mark.asyncio
async def test_c17_p_deleting_an_equivalent_whose_only_debt_was_cleared_is_refused_with_409(
    serializable_factory,
):
    """§10.1 case (1), API-SHAPED. The case T1524's RESTRICT does NOT already cover.

    WHY THIS IS NOT A REPEAT OF T1524. `debts.equivalent_id` became RESTRICT in T1524, so an
    equivalent with LIVE debts cannot be deleted. An equivalent whose only debt has been cleared has
    no `debts` row at all: T1524's foreign key has nothing to hold, the route's usage counts are all
    zero, and the delete goes through - taking with it the meaning of every journal entry
    denominated in it. The entries would survive as money history pointing at a row that no longer
    exists. Design v2 §5 makes every journal foreign key RESTRICT for exactly this, and the answer
    the route must give is the 409 it already gives an equivalent in use:
    `referenced_by_existing_rows` (`app/api/v1/admin.py:1459-1468`).

    WHAT THIS EXERCISES AND WHAT IT DOES NOT: see `_admin_request`. The handler is called directly.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so there is no history to protect; the
    non-vacuity assertion placed FIRST fails naming the table.
    MUTATION once step 4 exists: make the journal's foreign keys CASCADE. Migration 021 then deletes
    the history along with the equivalent and this test goes red again - the mutation design v2 §10.1
    names, "journal FK CASCADE → (1) and (3) red".
    """
    from app.api.v1.admin import admin_delete_equivalent
    from app.schemas.admin import AdminEquivalentDeleteRequest
    from app.utils.exceptions import ConflictException

    api = journal_api()
    seeded = await _seed(serializable_factory)
    world = seeded.world
    identity = _identity("history-outlives-equivalent")
    try:
        await _history_only_equivalent(api, serializable_factory, world, identity)
        await _deactivate(serializable_factory, world)

        entries = await stored_entries(serializable_factory, identity)
        debts_now = await stored_debts(serializable_factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)
        assert len(entries) == 2, f"stand: the operation left {entries} instead of an I and a D"
        # NON-VACUITY: nothing else can refuse this delete. With a live debt the refusal would be
        # T1524's RESTRICT and with a trustline it would be the route's own usage count, and this
        # test would pass without a journal existing at all.
        assert debts_now == {}, (
            f"stand: a debt is still live, so any refusal below would be T1524's, not the "
            f"journal's: {debts_now}"
        )

        failure: BaseException | None = None
        async with serializable_factory() as admin_session:
            try:
                await admin_delete_equivalent(
                    world.equivalent.code,
                    AdminEquivalentDeleteRequest(reason="p015 b4 counterexample"),
                    _admin_request(),
                    db=admin_session,
                )
            except BaseException as exc:  # classified below
                failure = exc

        async with serializable_factory() as fresh:
            survivor = (
                await fresh.execute(
                    select(Equivalent.id).where(Equivalent.id == world.equivalent.id)
                )
            ).scalar_one_or_none()

        # VERDICT.
        assert isinstance(failure, ConflictException) and failure.status_code == 409, (
            f"the admin route deleted an equivalent that {len(entries)} journal entries name, and "
            f"answered {failure!r}. Those entries are now money history denominated in a row that "
            f"does not exist. Every journal foreign key is RESTRICT for this reason (design v2 §5), "
            f"and the route reports the resulting refusal as the same 409 an equivalent in use gets."
        )
        assert (failure.details or {}).get("reason") == "referenced_by_existing_rows", (
            f"the 409 does not name the reason the delete was refused: {failure.details}. "
            f"`referenced_by_existing_rows` is the answer `app/api/v1/admin.py:1465-1467` gives when "
            f"the database itself refuses; an equivalent with journal history and no debts must "
            f"reach that branch, not the usage-count branch above it."
        )
        assert survivor is not None, "the equivalent is gone despite the refusal"
        assert len(await stored_entries(serializable_factory, identity) or []) == 2, (
            "the journal entries did not survive the refused delete"
        )
    finally:
        await _cleanup(serializable_factory, seeded)


@pytest.mark.parametrize("order", ["payment first", "delete first"])
@pytest.mark.asyncio
async def test_c17_p_the_owner_lock_race_never_leaves_an_equivalent_gone_with_its_history(
    serializable_factory, observer, order
):
    """§10.1 case (2), API-SHAPED for the envelope, DEFECT-SHAPED for what the race really does.

    THE RACE, WITH BOTH ORDERS FORCED. A payment commit takes the equivalent's owner lock
    (`app/core/payments/engine.py:414`, reached from `commit` via
    `_preacquire_equivalent_owner_locks_for_tx` at `:1083`) and writes its debts and its envelope
    under it. The admin delete takes the SAME lock (`app/api/v1/admin.py:1428`). One of them
    therefore waits for the other, and design v2 §10.1 requires both orders to be exercised.

    HOW THE ORDER IS FORCED, WITHOUT PATCHING EITHER OWNER. A third session takes the same advisory
    lock first and holds it. The participant that must go FIRST is started and observed, in
    `pg_locks`, actually WAITING on an advisory lock it has not been granted; only then is the second
    started and observed waiting too; only then is the gate released. PostgreSQL grants advisory
    locks in request order, so the observed queue IS the order - and both observations are asserted,
    so a run in which the gate was never contended fails as a stand rather than passing as a race.
    No sleeps, no patched methods, and neither owner knows it is in a test.

    TWO THINGS DESIGN V2 §10.1 ASKS FOR THAT THIS ROUTE CANNOT GIVE, reported rather than papered
    over:

    * §10.1 expects "payment first → delete 409". The 409 arrives, but it is the USAGE-COUNT 409 and
      not `referenced_by_existing_rows`, because a payment that can commit at all needs a trustline
      denominated in this equivalent and `_equivalent_usage_counts` counts trustlines
      (`app/api/v1/admin.py:1370-1387`). The FK branch is unreachable while a payment is possible.
      Case (1) above reaches it, precisely because it needs no trustline. Measured here, and worth
      recording on its own: in the payment-first order those counts come back `debts: 0` even though
      the payment has already committed a debt, because the route's snapshot is fixed by the SELECT
      that loads the equivalent at `:1418-1420`, BEFORE it takes the owner lock at `:1428`, and
      production PostgreSQL runs SERIALIZABLE (`app/config.py:68`). The lock therefore does not make
      the usage count fresh; T1524's RESTRICT foreign key is what actually holds, exactly as the
      comment at `:1424-1427` says in its last sentence.
    * §10.1 expects "delete first → payment fails, no Debt, no envelope". Unreachable for the same
      reason: the delete refuses on the trustline count and never deletes anything, so the payment
      that was waiting behind it then succeeds. The raw-DELETE case below is where an equivalent
      really does disappear under the lock.

    So what this test asserts is the invariant §10.1 states universally and that IS reachable: the
    equivalent is never gone while debts or journal rows denominated in it remain - plus, for the
    payment, exactly one COMPLETED envelope.

    RED TODAY BECAUSE: `debt_operations` does not exist; the non-vacuity assertion placed FIRST says
    so.
    MUTATION once step 4 exists: take the payment's journal envelope OUTSIDE the owner lock - open
    the operation before `_preacquire_equivalent_owner_locks_for_tx` - so an envelope can be written
    for an equivalent another transaction is about to delete.
    """
    from app.api.v1.admin import admin_delete_equivalent
    from app.core.payments.engine import (
        _EQUIVALENT_OWNER_LOCK_NAMESPACE,
        PaymentEngine,
    )
    from app.schemas.admin import AdminEquivalentDeleteRequest
    from app.utils.exceptions import ConflictException

    api = journal_api()
    assert api is not None  # resolved inside the body; see tests/p015_b4_support.py
    seeded = await _seed(serializable_factory)
    world = seeded.world
    lock_key = PaymentEngine._equivalent_owner_lock_key(world.equivalent.id)
    try:
        tx_id = await _seed_payment(serializable_factory, seeded, amount="9.00")
        await _deactivate(serializable_factory, world)

        payment_session = serializable_factory()
        admin_session = serializable_factory()
        gate = serializable_factory()
        try:
            payment_pid = int(await payment_session.scalar(text("SELECT pg_backend_pid()")))
            admin_pid = int(await admin_session.scalar(text("SELECT pg_backend_pid()")))

            # The gate: the same advisory lock, held in its own transaction, so both real owners
            # have to queue behind it in the order they ask.
            await gate.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :key)"),
                {"ns": _EQUIVALENT_OWNER_LOCK_NAMESPACE, "key": lock_key},
            )

            async def _run_payment():
                return await PaymentEngine(payment_session).commit(tx_id)

            async def _run_delete():
                try:
                    return await admin_delete_equivalent(
                        world.equivalent.code,
                        AdminEquivalentDeleteRequest(reason="p015 b4 counterexample"),
                        _admin_request(),
                        db=admin_session,
                    )
                finally:
                    # THE REQUEST'S SESSION ENDS WHEN THE REQUEST DOES, and here that is not
                    # decoration - it is what releases the owner lock. The route raises its
                    # usage-count `ConflictException` BEFORE the `try` that rolls back
                    # (`app/api/v1/admin.py:1435-1437`), so the transaction-scoped advisory lock it
                    # took at `:1428` is still held when the exception leaves the function. In
                    # production `get_db_session` (`app/db/session.py:92-94`) closes the session as
                    # the request unwinds and the lock goes with it. Measured without this line: the
                    # payment queued behind the delete never got the lock and failed with "Payment
                    # advisory lock timed out" after its five-second budget - a stand artefact that
                    # would have read as a property failure.
                    await admin_session.rollback()

            if order == "payment first":
                first, first_pid, second, second_pid = (
                    _run_payment, payment_pid, _run_delete, admin_pid,
                )
            else:
                first, first_pid, second, second_pid = (
                    _run_delete, admin_pid, _run_payment, payment_pid,
                )

            first_task = asyncio.create_task(first())
            first_queued = await _until(lambda: observer(first_pid))
            second_task = asyncio.create_task(second())
            second_queued = await _until(lambda: observer(second_pid))

            await gate.rollback()
            outcomes = await asyncio.wait_for(
                asyncio.gather(first_task, second_task, return_exceptions=True), timeout=60
            )
        finally:
            for session in (gate, admin_session, payment_session):
                try:
                    await session.rollback()
                finally:
                    await session.close()

        payment_outcome, delete_outcome = (
            outcomes if order == "payment first" else tuple(reversed(outcomes))
        )
        envelopes = await _envelopes_with_intent(serializable_factory, tx_id=tx_id)
        entries = await stored_rows(
            serializable_factory,
            f"SELECT id FROM {ENTRIES_TABLE} WHERE equivalent_id = :eq",  # noqa: S608
            {"eq": str(world.equivalent.id)},
        )
        debts_now = await stored_debts(serializable_factory, world)
        async with serializable_factory() as fresh:
            survivor = (
                await fresh.execute(
                    select(Equivalent.id).where(Equivalent.id == world.equivalent.id)
                )
            ).scalar_one_or_none()

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the race really happened in the intended order, and both participants really
        # contended for the SAME lock. Without these two observations the gather below would be
        # measuring whatever the scheduler happened to do.
        assert first_queued and second_queued, (
            f"stand: the two owners did not both queue on the equivalent owner lock before the gate "
            f"released it (first={first_queued}, second={second_queued}); the order '{order}' was "
            f"not forced and this run measured no race"
        )

        # VERDICT - the invariant §10.1 states, and the one outcome that is reachable through the
        # real route in both orders.
        assert isinstance(delete_outcome, ConflictException), (
            f"the admin delete answered {delete_outcome!r} in the '{order}' order. An equivalent "
            f"that a payment holds a trustline and an envelope for must not be deletable, whichever "
            f"of the two reached the owner lock first."
        )
        assert survivor is not None, (
            f"the equivalent is GONE after the '{order}' order, while the database still holds "
            f"debts={debts_now} and {len(entries or [])} journal entr(ies) denominated in it. That "
            f"is the state design v2 §10.1 forbids outright."
        )
        assert not isinstance(payment_outcome, BaseException), (
            f"the payment failed in the '{order}' order: {payment_outcome!r}"
        )
        assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", (
            f"the committed payment left {envelopes} instead of exactly one COMPLETED envelope, in "
            f"the '{order}' order"
        )
        assert debts_now == {("debtor", "creditor", "eq"): Decimal("9.00000000")}, debts_now
    finally:
        await _cleanup(serializable_factory, seeded)


async def _until(predicate, *, attempts: int = 500, step: float = 0.01) -> bool:
    """Poll `predicate` until it is true. Not a sleep on the clock: a wait on an observable state.

    `AGENTS.md` §11 forbids a real sleep where a clock, an event or a barrier will do. A backend
    blocking on an advisory lock is not something this process can be notified about - the wait
    happens inside PostgreSQL - so the only honest form is to watch `pg_locks` until the wait is
    visible, and to report failure to the caller rather than continuing as if it had been seen.
    """
    for _ in range(attempts):
        if await predicate():
            return True
        await asyncio.sleep(step)
    return False


@pytest.mark.asyncio
async def test_c17_p_a_raw_delete_of_an_equivalent_with_history_is_refused_by_the_foreign_key(
    serializable_factory,
):
    """§10.1 case (3), API-SHAPED. The route is not the last line; the foreign key is.

    WHY A RAW DELETE AND NOT THE ROUTE. Case (1) asks whether the application refuses. This asks
    whether the DATABASE does - and it is a different question, because the delete here is issued as
    raw SQL while holding the equivalent's owner lock, which is exactly what
    `app/api/v1/admin.py:1428-1458` does and exactly what a repair script, a migration helper or an
    operator's `psql` session would do without the route at all. `trust_lines.equivalent_id` is
    CASCADE, so nothing in the schema as it stands today stops this statement once the last debt is
    gone: the equivalent disappears and the journal is left denominated in it.

    Design v2 §5 makes every journal foreign key RESTRICT. This is the counterexample that decision
    is for, and it is the second half of §10.1's mutation - "journal FK CASCADE → (1) and (3) red".

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so there is no history for a foreign
    key to protect; the non-vacuity assertion placed FIRST fails naming the table.
    MUTATION once step 4 exists: give migration 021's foreign keys `ondelete='CASCADE'`. The delete
    then succeeds and takes the history with it, silently.
    """
    from app.core.payments.engine import _EQUIVALENT_OWNER_LOCK_NAMESPACE, PaymentEngine

    api = journal_api()
    seeded = await _seed(serializable_factory)
    world = seeded.world
    identity = _identity("raw-delete-with-history")
    try:
        await _history_only_equivalent(api, serializable_factory, world, identity)

        entries = await stored_entries(serializable_factory, identity)
        debts_now = await stored_debts(serializable_factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)
        assert len(entries) == 2, f"stand: the operation left {entries} instead of an I and a D"
        # NON-VACUITY: T1524's RESTRICT has nothing to hold here, so any refusal is the journal's.
        assert debts_now == {}, (
            f"stand: a debt is still live, so the refusal below would be T1524's: {debts_now}"
        )

        error = None
        async with serializable_factory() as remover:
            await remover.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :key)"),
                {
                    "ns": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                    "key": PaymentEngine._equivalent_owner_lock_key(world.equivalent.id),
                },
            )
            try:
                await remover.execute(
                    text("DELETE FROM equivalents WHERE id = :id"),
                    {"id": str(world.equivalent.id)},
                )
                await remover.commit()
            except DatabaseError as exc:
                error = exc
                await remover.rollback()

        async with serializable_factory() as fresh:
            survivor = (
                await fresh.execute(
                    select(Equivalent.id).where(Equivalent.id == world.equivalent.id)
                )
            ).scalar_one_or_none()

        # VERDICT.
        assert error is not None, (
            f"a raw DELETE removed the equivalent that {len(entries)} journal entries name, and the "
            f"database allowed it. The entries are now money history denominated in a row that does "
            f"not exist, and no application code was involved that could have noticed. Every journal "
            f"foreign key is RESTRICT for this reason (design v2 §5)."
        )
        assert survivor is not None, "the equivalent is gone despite the refusal"
        assert len(await stored_entries(serializable_factory, identity) or []) == 2, (
            "the journal entries did not survive the refused delete"
        )
    finally:
        await _cleanup(serializable_factory, seeded)


# ==============================================================================================
# C18-P - the 40001 variant of the concurrent version bump
# ==============================================================================================


@pytest.mark.asyncio
async def test_c18_p_entries_come_from_the_retry_and_carry_the_concurrent_value(
    serializable_factory,
):
    """C18-P, API-SHAPED. The same property as the sibling's, through the failure mode SQLite lacks.

    WHAT IS DIFFERENT FROM THE SQLITE SIBLING. There, a concurrent version bump surfaces as
    `StaleDataError` - SQLAlchemy noticing that its `UPDATE ... WHERE version = n` matched no row -
    and both sessions share one connection, because since T1525 two independent SQLite writers cannot
    coexist. Here they are two real backends under SERIALIZABLE, and the loser does not get a
    rowcount of zero: PostgreSQL refuses its write outright with a `40001` before the optimistic lock
    is ever consulted. The journal's obligation is identical in both worlds and the paths into it are
    not, which is why design v2 §9 lists `C18` on both tiers.

    THE OBLIGATION. The attempt that was refused wrote no row, so it must contribute no entry; and
    the retry's entry must carry the CONCURRENT value as `amount_before`, not the value this session
    had loaded before losing the race. A journal that captured `amount_before` when the attribute was
    first loaded would record a continuity that never existed, and step 6 would reconstruct the edge
    from a number no database ever held.

    THE `40001` IS GENUINE. Two transactions read the same row, one commits, the other writes. The
    SQLSTATE actually received is asserted, so a stand that stopped conflicting fails as a stand.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist; the non-vacuity assertion placed FIRST
    says so.
    MUTATION once step 4 exists: capture `amount_before` in `before_flush` from the attribute's
    originally loaded value instead of re-reading after the retry's `expire_all()`, or write entries
    in `before_flush` rather than `after_flush` so the refused attempt leaves one behind.
    """
    api = journal_api()
    seeded = await _seed(serializable_factory)
    world = seeded.world
    identity = _identity("40001-version-bump")
    sqlstates: list[str | None] = []
    try:
        starting_edge = _debt_row(world, Decimal("10.00000000"))
        async with serializable_factory() as setup:
            async with debt_fixture_setup(setup, label="starting-edge"):
                setup.add(starting_edge)
            await setup.commit()

        mine = select(Debt).where(Debt.equivalent_id == world.equivalent.id)

        async with serializable_factory() as loser, serializable_factory() as winner:
            losing = (await loser.execute(mine)).scalar_one()
            competing = (await winner.execute(mine)).scalar_one()

            # The competitor bumps the row through the ORM, so `version` really moves - and it
            # declares itself, because the journal asks every movement of money to name the
            # operation that made it, a competitor's included.
            async with debt_fixture_setup(winner, label="the-competitor"):
                competing.amount = Decimal("31.00000000")
            await winner.commit()

            try:
                async with await _open(api, loser, world, "40001-version-bump", identity=identity):
                    losing.amount = Decimal("44.00000000")
                    await loser.flush()
                await loser.commit()
            except DBAPIError as exc:
                sqlstates.append(getattr(exc.orig, "sqlstate", None))
            await loser.rollback()

        async with serializable_factory() as retry:
            debt = (await retry.execute(mine)).scalar_one()
            async with await _open(api, retry, world, "40001-version-bump", identity=identity):
                debt.amount = Decimal("44.00000000")
                await retry.flush()
            await retry.commit()

        entries = await stored_entries(serializable_factory, identity)
        envelopes = await stored_operations(serializable_factory, identity)
        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: the race produced a genuine serialization failure, exactly once.
        assert sqlstates == ["40001"], (
            f"stand: the concurrent version bump produced SQLSTATE(s) {sqlstates} instead of exactly "
            f"one 40001; without it this test measures nothing about a losing attempt"
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("44.00000000")}, (
            f"stand: the retry did not win the edge: {after}"
        )

        # VERDICT.
        assert len(entries) == 1 and entries[0]["effect"] == "U", (
            f"the losing attempt left a trace in the journal: {entries}. Only the flush that reached "
            f"the database may produce an entry."
        )
        assert _atom_column(entries, "amount_before") == [3100000000], (
            f"the retry's entry records `amount_before` as the value this session had loaded before "
            f"losing the race, not the value the database actually held: {entries}"
        )
        assert _atom_column(entries, "delta") == [1300000000], (
            f"the delta was computed against a state that never existed: {entries}"
        )
        assert envelopes is not None and len(envelopes) == 1, (
            f"the refused attempt and the retry left {envelopes} instead of one envelope"
        )
    finally:
        await _cleanup(serializable_factory, seeded)


# ==============================================================================================
# C19-P - forged journal rows against the PORTABLE constraints
# ==============================================================================================


#: Each row: `(label, table, column overrides, why the database must refuse it)`. The overrides are
#: applied on top of a well-formed row, so each case differs from a legal one in exactly the way it
#: names - a forgery that failed for two reasons at once would not show which CHECK caught it.
_SHAPE_INVALID_FORGERIES = [
    (
        "an insert that claims a previous amount",
        "entry",
        {"effect": "I", "amount_before": "5.00000000"},
        "check:chk_debt_journal_entries_shape",
        "an I has no before; a forged one would let a reconstruction start from an invented state",
    ),
    (
        "an update whose endpoints are equal",
        "entry",
        # `delta` stays legal (non-zero), so the only thing wrong with this row is its shape. Until
        # 2026-09-13 this case set `delta = 0` as well and violated BOTH CHECKs while the test
        # asserted only `error is not None` - so dropping either one left it green and the mutation
        # proved nothing.
        {
            "effect": "U",
            "amount_before": "5.00000000",
            "amount_after": "5.00000000",
            "delta": "1.00000000",
        },
        # AND ON THIS TIER IT IS NO LONGER THE SHAPE RULE THAT SPEAKS (T1530, 2026-09-13). With
        # `chk_debt_journal_entries_delta_arithmetic` in place, `delta = after - before` plus
        # `delta <> 0` together IMPLY `before <> after`, so a U with equal endpoints cannot be posed
        # as a shape-only forgery on PostgreSQL at all: any non-zero delta contradicts the
        # arithmetic, and a zero delta contradicts `chk_debt_journal_entries_delta`. The expectation
        # therefore names the rule that actually refuses it HERE, and the SQLite copy of this
        # inventory still names the shape rule, because that tier carries no arithmetic constraint
        # (measured: the equality is floating point there and false for ordinary money - see
        # `app/db/journal_tables.py`). The shape rule keeps its own naming cases above and below:
        # an `I` carrying a before and a `D` carrying an after satisfy the arithmetic and violate
        # only the NULL pattern.
        "check:chk_debt_journal_entries_delta_arithmetic",
        "a U whose endpoints are equal recorded a movement that did not happen",
    ),
    (
        "an entry whose delta is zero",
        "entry",
        {
            "effect": "U",
            "amount_before": "5.00000000",
            "amount_after": "6.00000000",
            "delta": "0.00000000",
        },
        "check:chk_debt_journal_entries_delta",
        "`delta <> 0`: an entry that moved nothing is not an effect and must not be counted as one",
    ),
    (
        "an amount past the magnitude ceiling",
        "entry",
        {
            "effect": "U",
            "amount_before": "5.00000000",
            # PAST the ceiling, which the case is named for. It used to be
            # `999999999999.99999999` - the largest value the domain CONTAINS, asserted as legal by
            # `test_c12_p_control_this_dialect_stores_the_whole_domain_exactly` two hundred lines
            # up. The case never measured a refusal: it passed only because the forged row's UUIDs
            # were bound as dashed strings and the FOREIGN KEY refused it first (fixed 2026-09-12,
            # step 4 slice C).
            #
            # ON POSTGRESQL THE REFUSAL COMES FROM `NUMERIC(20, 8)` ITSELF, and that is the honest
            # description: thirteen integer digits do not fit the column, so the type stops the row
            # before the CHECK is consulted. The magnitude CHECK is the only line on SQLite, where
            # the column type is not enforced at all, and it is what excludes `NaN` here - see
            # `app/db/journal_tables.py::_money`.
            "amount_after": "1000000000000.00000000",
            "delta": "999999999995.00000000",
        },
        # NOT A CHECK NAME, and that is the measured truth rather than a convenience: thirteen
        # integer digits do not fit `NUMERIC(20, 8)`, so the COLUMN TYPE refuses the row with
        # SQLSTATE 22003 before any CHECK is consulted. Writing `chk_debt_journal_entries_after`
        # here would assert a mechanism that never speaks on this tier - which is the same class of
        # false green this whole correction is about. The magnitude CHECK is the only line on SQLite,
        # where the column type is not enforced at all.
        "sqlstate:22003",
        "`amount_after < 1e12` - and this is the case ONLY this tier can pose, because the value is "
        "four orders of magnitude outside the domain the SQLite tier is allowed to write",
    ),
    (
        "a completed envelope with no digest",
        "operation",
        {"state": "COMPLETED", "effect_digest": None},
        "check:chk_debt_operations_completion",
        "COMPLETED implies every completion column is present (design v2 §5)",
    ),
    (
        "a completed envelope with a negative effect count",
        "operation",
        {"state": "COMPLETED", "effect_count": -1},
        "check:chk_debt_operations_completion",
        "`effect_count >= 0`, which design v2 §5 puts inside the completion CHECK rather than in a "
        "clause of its own",
    ),
    (
        "an envelope written by a schema this build does not know",
        "operation",
        {"schema_version": 2},
        "check:chk_debt_operations_schema_version",
        "`schema_version IN (1)` - a row from a future encoding must not be read as if it were this one",
    ),
]


#: What a refused forgery must be refused BY, as a string a test can compare. Two kinds, because two
#: mechanisms really speak here: a named CHECK, and the column type itself. PostgreSQL spells the
#: first `violates check constraint "<name>"` and reports the second as an SQLSTATE.
_CHECK_IN_ERROR = re.compile(r'violates check constraint "([A-Za-z0-9_]+)"')


def _refused_by(error: BaseException | None) -> str | None:
    """`check:<name>`, `sqlstate:<code>`, or None when the error names neither.

    None is a distinct answer from "the wrong rule": an error that names no constraint and carries no
    SQLSTATE is not a refusal by the schema at all, and the assertion that reads this quotes the
    whole error so a red run shows what really spoke.
    """

    if error is None:
        return None
    found = _CHECK_IN_ERROR.search(str(error))
    if found:
        return f"check:{found.group(1)}"
    orig = getattr(error, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return f"sqlstate:{sqlstate}" if sqlstate else None


@pytest.mark.parametrize(
    "label,table,overrides,refused_by,why",
    _SHAPE_INVALID_FORGERIES,
    ids=[row[0] for row in _SHAPE_INVALID_FORGERIES],
)
@pytest.mark.asyncio
async def test_c19_p_a_shape_invalid_forged_row_is_refused_by_the_named_rule(
    serializable_factory, label, table, overrides, refused_by, why
):
    """C19-P, API-SHAPED. The write guard is not the last line; the CHECK constraints are.

    Design v2 §6's `before_execute` guard does not see `text()` or `exec_driver_sql`, and says so.
    That documented hole is only acceptable because the SHAPE of a journal row is enforced by the
    database itself: a forgery that gets past the guard still has to satisfy every CHECK in design
    v2 §5.

    WHY THE SAME INVENTORY RUNS ON BOTH TIERS. The constraints of migration 021 are portable ones
    and a CHECK is only a promise on the backend that enforces it: SQLite's are declared in the same
    DDL and evaluated by a different engine, and `NUMERIC(20,8)` does not exist there at all. The
    magnitude case above is the one that can ONLY be posed here - `999999999999.99999999` is outside
    anything the SQLite tier may write, so on that tier the `|amount| < 1e12` CHECK has no forgery
    that reaches it.

    AND EACH CASE NAMES THE RULE THAT MAY REFUSE IT (corrected 2026-09-13, external review). `error
    is not None` is satisfied by any error at all - a foreign key, a NOT NULL, the other CHECK on the
    same table - and on this tier that has already happened once: every entry forgery passed on a
    `FOREIGN KEY constraint failed` until the UUID binding was fixed in slice C. So each row above
    differs from a legal one in exactly one way and says what must speak, as `check:<name>` or as
    `sqlstate:<code>` where the COLUMN TYPE is the thing that refuses.

    RED BEFORE STEP 4 BECAUSE: the journal tables do not exist, so the forged INSERT fails with
    "relation does not exist" - which is NOT a CHECK refusal and must not be allowed to read as one.
    The non-vacuity assertion is therefore placed first and demands the table.
    MUTATION: drop `refused_by`'s CHECK from migration 021 and from `app/db/journal_tables.py`. Only
    the cases naming it go red; the rest stay green, and a case that had been caught by a
    neighbouring rule now fails on the NAME instead of passing on the error.
    """
    target = OPERATIONS_TABLE if table == "operation" else ENTRIES_TABLE
    probe = await stored_rows(serializable_factory, f"SELECT 1 FROM {target} LIMIT 1")  # noqa: S608

    # NON-VACUITY, FIRST.
    assert probe is not None, missing_journal_tables(probe, target)

    seeded = await _seed(serializable_factory)
    try:
        error = await _forge(serializable_factory, seeded.world, table, overrides)

        # VERDICT.
        assert error is not None, (
            f"the database accepted a forged journal row - {label}. {why}. A raw writer that gets "
            f"past the `before_execute` guard (design v2 §6 does not intercept `text()` or "
            f"`exec_driver_sql`, and says so) must still be stopped by the CHECK constraints of "
            f"migration 021; otherwise the guard's documented hole is a hole in the money history."
        )
        assert _refused_by(error) == refused_by, (
            f"the forgery `{label}` was refused by {_refused_by(error)!r}, not by `{refused_by}`: "
            f"{error}. {why}. A case caught by a neighbouring rule says nothing about the rule it is "
            f"named after, and the mutation `drop {refused_by}` would leave it green."
        )
    finally:
        await _cleanup(serializable_factory, seeded)


@pytest.mark.asyncio
async def test_c19_p_the_boundary_with_step_6_moved_on_this_tier_and_here_is_where_it_is_now(
    serializable_factory,
):
    """C19-P, API-SHAPED. The boundary this test records MOVED on 2026-09-13, and by decision.

    WHAT THIS TEST USED TO SAY, and it was right when it was written: `after - before = delta` is not
    a CHECK, so a forged entry whose three money columns are individually legal but do not agree with
    each other WILL be accepted, and catching it is step 6's verifier's job. It asserted the
    acceptance, and it said in as many words that a refusal here would be "a specification change,
    not a passing test".

    IT IS NOW EXACTLY THAT SPECIFICATION CHANGE, and this test is how it was found. `T1530` added
    `chk_debt_journal_entries_delta_arithmetic` (migration 024) after an external review measured a
    journal entry reading `10 -> 11, delta 2` surviving every constraint the table had. The two
    reasons design v2 §5 gave for leaving the arithmetic to step 6 were not equally true, and the
    difference is the whole of what changed:

    * "a cross-row constraint is not portable at all" - NOT APPLICABLE. This is not a cross-row
      constraint: all three columns are in the same row, and PostgreSQL's `NUMERIC(20,8)` computes
      the equality exactly.
    * "on SQLite the money columns are REAL" - TRUE, and measured again for this change: on sqlite3
      the legitimate movement `10.00000001 -> 10.00000002, delta 0.00000001` gives a left-hand side
      of `9.99999905104687e-09`, so the constraint would refuse REAL money there. It is therefore
      installed on PostgreSQL only (`ddl_if`), and the SQLite copy of this test still records the old
      boundary, correctly, for that tier.

    THE SPEC OWNS THE RECORD OF THIS, NOT THIS TEST. Design v2 §5 and step 6's acceptance list have
    to be updated to say that on PostgreSQL the arithmetic is now the schema's promise and only the
    CROSS-ROW lies remain step 6's - this file cannot make that change and does not pretend to.

    WHAT IS STILL STEP 6'S JOB IS ASSERTED HERE, because deleting the boundary would have been the
    easy way to make this green and would have lost the thing the test is for: a row whose three money
    columns agree PERFECTLY and which is nevertheless a lie about the world - a `U` that claims the
    edge went from 5 to 6 when the edge holds something else entirely. No single-row CHECK can see
    that, on any dialect, and it is accepted.

    MUTATION that must redden this: drop `chk_debt_journal_entries_delta_arithmetic` from migration
    024 - the first half goes green-then-red on the refusal assertion. Making the second forgery
    arithmetically false instead reddens the second half.
    """
    probe = await stored_rows(
        serializable_factory, f"SELECT 1 FROM {ENTRIES_TABLE} LIMIT 1"  # noqa: S608
    )
    assert probe is not None, missing_journal_tables(probe, ENTRIES_TABLE)

    seeded = await _seed(serializable_factory)
    try:
        # HALF ONE: the arithmetic lie is now the DATABASE's to refuse.
        arithmetic_lie = await _forge(
            serializable_factory,
            seeded.world,
            "entry",
            {
                "effect": "U",
                "amount_before": "5.00000000",
                "amount_after": "6.00000000",
                # Every column is legal on its own; together they contradict each other.
                "delta": "99.00000000",
            },
        )
        assert arithmetic_lie is not None, (
            "the database accepted an entry whose delta contradicts its own endpoints. T1530 added "
            "`chk_debt_journal_entries_delta_arithmetic` for exactly this row, and without it a "
            "journal entry can say `5 -> 6, delta 99` with nothing refusing."
        )
        assert _refused_by(arithmetic_lie) == "check:chk_debt_journal_entries_delta_arithmetic", (
            f"the arithmetic lie was refused by {_refused_by(arithmetic_lie)!r} rather than by the "
            f"constraint that names the arithmetic: {arithmetic_lie}. A row caught by a neighbouring "
            f"rule says nothing about this one."
        )

        # HALF TWO: what NO single-row CHECK can see remains step 6's, and is still accepted.
        cross_row_lie = await _forge(
            serializable_factory,
            seeded.world,
            "entry",
            {
                "effect": "U",
                # Internally consistent to the last digit, and a lie about the edge it names: the
                # edge never stood at 5, so no reconstruction from these entries reaches the stored
                # debt. That is a statement about OTHER rows, and it is step 6's to check.
                "amount_before": "5.00000000",
                "amount_after": "6.00000000",
                "delta": "1.00000000",
            },
        )
        assert cross_row_lie is None, (
            f"the database refused an entry that is internally consistent and false only in relation "
            f"to other rows: {cross_row_lie!r}. If a single-row CHECK can now reject this, the "
            f"boundary has moved AGAIN and step 6's acceptance list is out of date - which is a "
            f"specification change, not a passing test."
        )
    finally:
        await _cleanup(serializable_factory, seeded)


def _envelope_row(operation_id: uuid.UUID, overrides: dict) -> dict:
    """A well-formed OPEN envelope, then `overrides` on top. Completion columns are NULL."""
    row = {
        "id": str(operation_id),
        "kind": "TEST_FIXTURE",
        "identity": _identity("forgery"),
        "tx_id": None,
        "intent": "{}",
        "intent_digest": "0" * 64,
        "schema_version": 1,
        "money_encoding_version": 1,
        "intent_encoding_version": 1,
        "state": "OPEN",
        "completed_at": None,
        "flush_count": None,
        "effect_count": None,
        "effect_digest": None,
    }
    row.update(overrides)
    # A COMPLETED envelope needs every completion column, so the cases that forge one supply only
    # the column they are lying about and this fills in legal values for the rest. Without it a
    # "COMPLETED with no digest" forgery would also be missing three other NOT-NULL-when-COMPLETED
    # columns, and a refusal would not say which CHECK caught it.
    if row["state"] == "COMPLETED":
        defaults = {
            "completed_at": datetime(2026, 9, 12, tzinfo=timezone.utc),
            "flush_count": 1,
            "effect_count": 1,
            "effect_digest": "1" * 64,
        }
        for column, value in defaults.items():
            if column not in overrides:
                row[column] = value
    return row


def _entry_row(operation_id: uuid.UUID, world: World, overrides: dict) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "operation_id": str(operation_id),
        "flush_ordinal": 1,
        "equivalent_id": str(world.equivalent.id),
        "debtor_id": str(world.debtor.id),
        "creditor_id": str(world.creditor.id),
        "effect": "U",
        "amount_before": "5.00000000",
        "amount_after": "6.00000000",
        "delta": "1.00000000",
    }
    row.update(overrides)
    return row


def _insert(table: str, row: dict) -> str:
    columns = list(row)
    return (
        f"INSERT INTO {table} "  # noqa: S608
        f"({', '.join(columns)}) VALUES ({', '.join(':' + name for name in columns)})"
    )


async def _forge(factory, world: World, table: str, overrides: dict):
    """INSERT a raw journal row, well-formed except for `overrides`. Returns the error, or None.

    An entry needs an envelope to point at (`operation_id` is a RESTRICT foreign key), so the entry
    cases insert BOTH in one transaction under the same `operation_id`. The envelope is inserted
    unmodified in that case, so a refusal can only have come from the entry.
    """
    operation_id = uuid.uuid4()
    async with factory() as session:
        try:
            envelope = _envelope_row(operation_id, overrides if table == "operation" else {})
            await session.execute(text(_insert(OPERATIONS_TABLE, envelope)), envelope)
            if table == "entry":
                entry = _entry_row(operation_id, world, overrides)
                await session.execute(text(_insert(ENTRIES_TABLE, entry)), entry)
            await session.commit()
        except DBAPIError as exc:
            # `DBAPIError` and not `DatabaseError`: a value past `NUMERIC(20, 8)` reaches asyncpg as
            # `NumericValueOutOfRangeError`, which SQLAlchemy wraps in the BASE class. Catching only
            # `DatabaseError` let that one escape the helper entirely, so the case named for the
            # magnitude ceiling reported an error instead of a refusal.
            await session.rollback()
            return exc
        finally:
            # The forged rows are this test's own litter in a SHARED database, and the journal
            # tables are outside `_cleanup`'s reach because they do not exist yet. When they do, an
            # accepted forgery must still be removed.
            await _unforge(factory, operation_id)
    return None


async def _unforge(factory, operation_id: uuid.UUID) -> None:
    async with factory() as session:
        try:
            await session.execute(
                text(f"DELETE FROM {ENTRIES_TABLE} WHERE operation_id = :id"),  # noqa: S608
                {"id": str(operation_id)},
            )
            await session.execute(
                text(f"DELETE FROM {OPERATIONS_TABLE} WHERE id = :id"),  # noqa: S608
                {"id": str(operation_id)},
            )
            await session.commit()
        except DatabaseError:
            await session.rollback()
