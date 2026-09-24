"""Programme 015, T1530/T1531 on PostgreSQL: the constraint on BOTH construction paths, and asyncpg.

WHY THIS MODULE BUILDS ITS OWN DATABASES INSTEAD OF TRUSTING THE GATE'S, and why that is still true
after T1534. When this module was written, `GEO_TEST_USE_MIGRATED_SCHEMA=1` verified nothing at all -
measured by the owner on 2026-09-13, it only checked that `alembic_version` was readable, and
`geov0_test_ci` was in fact a `create_all` schema wearing a stamp. Since T1534 the flag BUILDS the
gate's schema with the migrations, so the gate's database is now genuinely migrated. It still cannot
answer this module's question: one database is one construction path, and the subject here is whether
the TWO paths agree. So this module keeps creating two scratch databases, builds one with
`Base.metadata.create_all` and the other with `alembic -c migrations/alembic.ini upgrade head`, and
compares what PostgreSQL then holds.

THE ALEMBIC PATH NEEDS A SUBPROCESS, and that is a fact about this tree rather than a choice:
`migrations/env.py` ends in `asyncio.run(...)`, so it cannot be invoked from inside a running event
loop. `tests/migrated_schema.py::run_alembic_upgrade_head` is where that subprocess lives.

IT ALSO NEEDED A PREFLIGHT, AND NO LONGER DOES HERE (T1701, 2026-09-21). `alembic upgrade head` on a
fresh database dies at 010 -> 011 unless `alembic_version.version_num` is widened to `VARCHAR(128)`
first. That used to be true of a bare command and false of `docker/docker-entrypoint.sh`, and three
copies of the same DDL kept the difference alive. `migrations/env.py` now establishes the precondition
itself, so every caller of the migration entry - this module included - gets it, and the sentence
"a bare command does not" is no longer true of this tree.

WHAT ELSE IS HERE, and it cannot be on the SQLite tier:

* THE PLACEHOLDERS. The journal's verification reads are hand-written SQL through `exec_driver_sql`
  (T1531), so they spell their own placeholders: aiosqlite is `qmark` and asyncpg is `numeric_dollar`.
  A read written against one tier binds nothing on the other, and "binds nothing" returns no rows, which
  reads exactly like "the row is gone".
* THE UUID SPELLING. `Uuid(as_uuid=True)` is 32 hex characters on SQLite and a native `uuid` on
  PostgreSQL. Two counterexamples in this programme have already gone falsely green on that difference.
* WHAT THE DRIVER RETURNS. asyncpg hands back `NUMERIC` as `Decimal` untouched, and its result processor
  cannot even be built without a result-set column type - the branch `_money_out` has for it.

Every verdict is read on a new session, each test names the mutation that must turn it red, and the
stand purges what it created.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, InvalidRequestError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.ledger import journal
from app.db.base import Base
from app.db.journal_tables import debt_journal_entries
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.migrated_schema import run_alembic_upgrade_head, scratch_databases
from tests.p015_b4a_stand import Stand, arm_stand, identity

_SCENARIO_END = (journal.DebtJournalError, InvalidRequestError)

#: The constraint this slice adds, and the predicate both construction paths must produce.
_CONSTRAINT = "chk_debt_journal_entries_delta_arithmetic"

#: SQLSTATE for a CHECK violation. Asserted rather than matched on prose, per `Reason`'s own rule.
_CHECK_VIOLATION = "23514"


def _postgres_url() -> str:
    """The gate's PostgreSQL URL, or a refusal.

    THE REFUSAL THAT MAKES THIS MODULE POSTGRESQL-ONLY (T1525): every SQLite-capable engine
    construction in this repository must install the transaction control, and a construction that can
    only ever be PostgreSQL is exempt only through a refusal that exists in the code. This is it.
    """

    from tests.conftest import TEST_DATABASE_URL

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def stand():
    """This module's own engine on the gate's database, with the journal armed."""

    from tests.conftest import _ensure_schema_initialized

    url = _postgres_url()
    await _ensure_schema_initialized()
    engine = create_async_engine(url, pool_size=4, max_overflow=0, pool_timeout=15)
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


# =================================================================================================
# The two construction paths
# =================================================================================================


async def _check_constraints(url: str, table: str) -> dict[str, str]:
    """Every CHECK constraint PostgreSQL holds for `table`, by name, with its stored definition."""

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = :table ::regclass AND contype = 'c'"
                    ),
                    {"table": table},
                )
            ).all()
        return {row[0]: " ".join(row[1].split()) for row in rows}
    finally:
        await engine.dispose()


async def _arithmetic_bites(url: str) -> str | None:
    """Insert a row whose delta contradicts its ends. Returns the SQLSTATE that refused it, or None.

    THE ROW IS OTHERWISE VALID, which is what makes this a test of the arithmetic clause and not of
    the shape clause next to it: effect `U`, both ends present and different, a non-zero bounded delta
    - and `delta = 2` where `after - before = 1`. Its foreign keys point at rows this function creates
    first, so a refusal can only be the arithmetic: a probe refused by a foreign key would look exactly
    like a probe refused by the constraint, and that is the class of false evidence `Reason` exists for.
    """

    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            tag = uuid.uuid4().hex[:8]
            participants = [uuid.uuid4(), uuid.uuid4()]
            equivalents = [uuid.uuid4()]
            await connection.execute(
                Equivalent.__table__.insert(),
                [{"id": equivalents[0], "code": f"T15{tag[:4].upper()}", "precision": 2,
                  "is_active": True, "metadata_": {}}],
            )
            await connection.execute(
                Participant.__table__.insert(),
                [
                    {
                        "id": participant,
                        "pid": f"T1530_{index}_{tag}",
                        "display_name": f"probe {index}",
                        "public_key": f"pk_t1530_{index}_{tag}",
                        "type": "person",
                        "status": "active",
                        "profile": {},
                    }
                    for index, participant in enumerate(participants)
                ],
            )
            operation_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
                    "schema_version, money_encoding_version, intent_encoding_version, state) "
                    "VALUES (:id, 'TEST_FIXTURE', :identity, '{}', :digest, 1, 1, 1, 'OPEN')"
                ),
                {"id": operation_id, "identity": f"t1530-probe/{operation_id}", "digest": "0" * 64},
            )
            try:
                await connection.execute(
                    text(
                        "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, "
                        "equivalent_id, debtor_id, creditor_id, effect, amount_before, amount_after, "
                        "delta) VALUES (:id, :operation_id, 1, :equivalent_id, :debtor_id, "
                        ":creditor_id, 'U', 10, 11, 2)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "operation_id": operation_id,
                        "equivalent_id": equivalents[0],
                        "debtor_id": participants[0],
                        "creditor_id": participants[1],
                    },
                )
            except DBAPIError as exc:
                return getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
                    exc.orig, "pgcode", None
                )
            return None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_t1530_p_the_constraint_exists_and_bites_on_both_construction_paths() -> None:
    """T1530, layer 1. `create_all` and `alembic upgrade head` produce the SAME constraint, and it bites.

    WHAT A GREEN RUN HERE MEANS AND WHAT IT WOULD HAVE MEANT WITHOUT THE TWO DATABASES: the gate's flag
    does not verify that its schema came from the migrations, so a single-database assertion would have
    proved the constraint exists on one path and said nothing about the other. Both are built here,
    from scratch, in this test.

    ASSERTED: the constraint is present under the same NAME and the same stored DEFINITION in both, and
    in both an otherwise-valid entry whose delta contradicts its own ends is refused with SQLSTATE
    23514. The names of the other CHECK constraints are compared as a set too, because a migration that
    stops producing one of them is the same class of defect found one table later.

    MUTATION that must redden this: remove the `op.create_check_constraint` call from migration
    `024_debt_journal_delta` - the migrated database then lacks it while the metadata one has it, which
    is precisely the divergence this test exists for. (The constraint's former
    `.ddl_if(dialect="postgresql")` left with SQLite in 017 stage 3, slice S7; on PostgreSQL it
    was always emitted, so this comparison is unchanged by that.)
    """

    # NOT a skip when the role cannot create databases (T1701). This module used to say, at length,
    # that a missing CREATE DATABASE right made the comparison "an ABSENT measurement, not a passing
    # one" - and then report a pass. `scratch_databases` raises instead: since the PostgreSQL tier
    # provisions its schema template by cloning a database, the right is a precondition of the tier.
    async with scratch_databases(_postgres_url(), "t1530mig", "t1530meta") as (
        migrated_url,
        metadata_url,
    ):
        # PATH 1: the metadata, which is what every SQLite tier and the gate's default path use.
        engine = create_async_engine(metadata_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

        # PATH 2: the migrations. The `alembic_version` preflight is no longer spelled here or in
        # `docker/docker-entrypoint.sh`: since T1701 `migrations/env.py` owns it and establishes it
        # inside this very run, so a bare command is no longer a different thing from this one.
        # Raises `MigratedSchemaError` carrying stdout and stderr if the run does not reach head, so
        # the migrated path can never be silently unmeasured.
        run_alembic_upgrade_head(migrated_url)

        migrated = await _check_constraints(migrated_url, debt_journal_entries.name)
        from_metadata = await _check_constraints(metadata_url, debt_journal_entries.name)

        # NON-VACUITY: both paths really built the table.
        assert migrated, "the migrated database has no CHECK constraints on the entries table at all"
        assert from_metadata, "the metadata database has no CHECK constraints on the entries table"

        assert _CONSTRAINT in from_metadata, (
            f"`Base.metadata.create_all` did not produce {_CONSTRAINT}: {sorted(from_metadata)}"
        )
        assert _CONSTRAINT in migrated, (
            f"`alembic upgrade head` did not produce {_CONSTRAINT}: {sorted(migrated)}"
        )
        assert migrated[_CONSTRAINT] == from_metadata[_CONSTRAINT], (
            f"the two construction paths produced DIFFERENT predicates for {_CONSTRAINT}:\n"
            f"  alembic:  {migrated[_CONSTRAINT]}\n  metadata: {from_metadata[_CONSTRAINT]}"
        )
        assert set(migrated) == set(from_metadata), (
            f"the two construction paths disagree on which CHECK constraints the entries table has:\n"
            f"  only in alembic:  {sorted(set(migrated) - set(from_metadata))}\n"
            f"  only in metadata: {sorted(set(from_metadata) - set(migrated))}"
        )

        # AND IT BITES, on both, with the same SQLSTATE.
        for label, scratch in (("metadata", metadata_url), ("alembic", migrated_url)):
            sqlstate = await _arithmetic_bites(scratch)
            assert sqlstate == _CHECK_VIOLATION, (
                f"{label}: an entry saying `10 -> 11, delta 2` was accepted (sqlstate={sqlstate!r}). "
                f"The constraint exists in the catalogue and does not refuse, which is worse than "
                f"its absence because the catalogue then lies."
            )


# =================================================================================================
# The in-process layer, on asyncpg's spellings
# =================================================================================================


@pytest.mark.asyncio
async def test_t1530_p_a_rewritten_entry_is_refused_with_asyncpg_spellings(stand: Stand) -> None:
    """T1530 layer 2 / T1531, on this driver. The readback binds `$1` and a native `uuid`.

    WHY THIS IS NOT A COPY OF THE SQLITE TEST. The readback is hand-written SQL now, so its
    placeholders and its bound values are per-dialect: `numeric_dollar` here against `qmark` there, a
    native `uuid` here against 32 hex characters there. A read that bound nothing would return no rows,
    and no rows reads as "the entries are missing" - a refusal for the wrong reason, which is the exact
    failure mode `Reason` exists to make visible. So the refusal's NAME is asserted, and the statement's
    own placeholders are asserted with it.

    MUTATION that must redden this: spell the placeholders `?` unconditionally in `_raw_params`. The
    statement then fails to execute at all on asyncpg and the refusal's name changes.
    """

    ident = identity("t1530-p-amount")
    observed: list[str] = []
    fired: list[str] = []

    def _watch(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        flattened = " ".join(statement.split())
        if flattened.startswith("SELECT flush_ordinal"):
            observed.append(flattened)

    def _rewrite(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        table = getattr(getattr(clause, "table", None), "name", None)
        if table != debt_journal_entries.name or not type(clause).__name__.endswith("Insert"):
            return clause, multiparams, params
        if isinstance(params, dict) and params and params.get("amount_after") is not None:
            changed = dict(params)
            changed["amount_after"] = Decimal(changed["amount_after"]) + 1
            changed["delta"] = Decimal(changed["delta"]) + 1
            fired.append("rewrote")
            return clause, multiparams, changed
        return clause, multiparams, params

    # A FULL-WIDTH AMOUNT, which exists on this tier only (design v2 §4): twelve integer digits, so
    # the comparison has to be exact rather than float-shaped.
    before = Decimal("999999999990.00000000")
    after = Decimal("999999999991.00000000")

    # SEEDED BEFORE THE TAMPER IS INSTALLED. Registering the listener first would rewrite the SEED's
    # own entry, and the refusal would then be about the fixture rather than about the scenario -
    # measured, on the first run of this test.
    async with stand.factory() as session:
        subject = stand.debt("0", raw_amount=before)
        async with stand.operation("t1530-p-seed", session=session):
            session.add(subject)
            await session.flush()
        await session.commit()
        debt_id = subject.id

    event.listen(stand.engine.sync_engine, "after_cursor_execute", _watch)
    event.listen(stand.engine.sync_engine, "before_execute", _rewrite, retval=True)
    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            async with stand.operation("t1530-p-amount", session=session, identity=ident):
                row = await session.get(Debt, debt_id)
                row.amount = after
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", _rewrite)
        event.remove(stand.engine.sync_engine, "after_cursor_execute", _watch)

    async with stand.factory() as fresh:
        stored = (
            await fresh.execute(select(Debt.amount).where(Debt.id == debt_id))
        ).scalar_one_or_none()
    entries = await stand.entries(ident)

    assert fired == ["rewrote"], f"stand: the entry INSERT was never rewritten ({fired})"
    assert observed, "stand: the entry readback never ran, so nothing was verified"
    assert "$1" in observed[0] and "$2" in observed[0], (
        f"the entry readback is not using asyncpg's placeholders: {observed[0]!r}"
    )
    assert refusal is not None, (
        f"the record said something else than the table and nothing refused: `debts` holds {stored}, "
        f"entries {entries}"
    )
    assert isinstance(refusal, journal.DebtJournalError), repr(refusal)
    assert refusal.reason in (
        journal.Reason.UNRECORDED_JOURNAL_ENTRY,
        journal.Reason.ROOT_POISONED,
    ), refusal
    assert stored == before, f"the refused flush is durable: {stored}"
    assert entries == [], f"a refused operation still has entries: {entries}"


@pytest.mark.asyncio
async def test_t1530_p_an_ordinary_full_width_movement_still_records(stand: Stand) -> None:
    """T1530, ANTI-VACUUM on this tier: the readback must not refuse a legitimate exact amount.

    The readbacks decode money through the column type's own result processor, and on asyncpg that
    processor cannot be built at all (it raises without a result-set column type) - so the value arrives
    as `Decimal` and is used untouched. If that branch were wrong, every full-width movement would be
    refused as a disagreement. Twelve integer digits exist only here, which is why this control is here.

    MUTATION that must redden this: make `_money_out` return `_as_decimal(str(value))` through a float
    (`float(value)`), which loses the twentieth digit and turns every full-width amount into a
    disagreement.
    """

    ident = identity("t1530-p-ok")
    amount = Decimal("999999999999.99999999")

    async with stand.factory() as session:
        subject = stand.debt("0", raw_amount=amount)
        async with stand.operation("t1530-p-ok", session=session, identity=ident):
            session.add(subject)
            await session.flush()
        await session.commit()
        debt_id = subject.id

    entries = await stand.entries(ident)
    envelopes = await stand.envelopes(ident)
    async with stand.factory() as fresh:
        stored = (await fresh.execute(select(Debt.amount).where(Debt.id == debt_id))).scalar_one()

    assert stored == amount, f"the amount did not round-trip at full width: {stored}"
    assert len(entries) == 1, entries
    assert entries[0]["amount_after"] == amount, entries
    assert entries[0]["delta"] == amount, entries
    assert envelopes and envelopes[0]["state"] == "COMPLETED", envelopes
