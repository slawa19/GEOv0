"""Programme 015, step 5a on PostgreSQL: both schema paths, asyncpg spellings, and the cutover race.

WHAT THIS TIER ADDS over `tests/unit/test_p015_step5a_reconciliation.py`:

* THE TWO CONSTRUCTION PATHS. `Base.metadata.create_all` and `alembic upgrade head` must build the same
  three tables - columns, primary keys, foreign keys with their `ON DELETE`, CHECKs, indexes and the
  baseline's comment - and the constraints must bite on both.
* ASYNCPG. Native `uuid`, `Decimal` untouched, `$n` placeholders, JSON binding of the result row.
* THE CUTOVER RACE. The book refuses a SEED written after the baseline by READING the baseline at
  completion (`app/core/ledger/book.py::_complete`, moved there from the deleted listener journal by 018
  stage B1). A baseline committed while such an operation is open is invisible to that read under a
  snapshot. The claim in `_complete` is that `SERIALIZABLE` - the application's isolation level -
  refuses one of the two with `40001`. It is measured here, not argued.

Every test that commits does so on a disposable clone of the migrated template (`committed_database`),
and the clone's drop is the only disposal of what it wrote (018 B0b; until then each test deleted its
rows by id, journal included). The construction-path test builds its own scratch databases.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.ledger.book import Book, BookError, Refusal, operation_for
from app.core.ledger.reconciliation import FAILED, PASSED, UNVERIFIABLE, take_baseline
from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.reconciliation_tables import BASELINE_COMMENT
from tests.p019_support import allow_below_serializable_for_a_diagnostic
from tests.debt_setup import debt_fixture_setup
from tests.migrated_schema import run_alembic_upgrade_head, scratch_databases
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - fixture, requested by `factory`
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import (
    _edges,
    _seed_triangle,
)
from tests.unit.test_p015_step5a_reconciliation import (
    _around_the_application,
    _baseline,
    _fixture_debts,
    _literal,
    _pay,
    _results,
    _scheduled_run,
    _verify,
)

_TABLES = (
    "debt_reconciliation_baselines",
    "debt_reconciliation_baseline_offsets",
    "debt_reconciliation_results",
)


def _postgres_url() -> str:
    from tests.conftest import TEST_DATABASE_URL

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def factory(tier_on_a_clone):
    """The clone's sessionmaker, with `tests.conftest.TestingSessionLocal` rebound to the same clone:
    shared helpers that observe through that name (`pg_locks ... current_database()`) must look at the
    database the test writes to (018 B0b)."""
    yield tier_on_a_clone.sessionmaker


# =================================================================================================
# The two construction paths
# =================================================================================================


_CATALOGUE = {
    "constraints": (
        "SELECT conname, CAST(contype AS text), pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = CAST(:table AS regclass)"
    ),
    "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table",
    "columns": (
        "SELECT column_name, data_type, is_nullable, numeric_precision, numeric_scale, "
        "character_maximum_length, column_default FROM information_schema.columns "
        "WHERE table_name = :table ORDER BY ordinal_position"
    ),
    "comment": "SELECT obj_description(CAST(:table AS regclass), 'pg_class')",
}


async def _describe(url: str) -> dict:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            described = {}
            for table in _TABLES:
                constraints = (await connection.execute(text(_CATALOGUE["constraints"]), {"table": table})).all()
                indexes = (await connection.execute(text(_CATALOGUE["indexes"]), {"table": table})).all()
                columns = (await connection.execute(text(_CATALOGUE["columns"]), {"table": table})).all()
                comment = (await connection.execute(text(_CATALOGUE["comment"]), {"table": table})).scalar()
                described[table] = {
                    "constraints": {row[0]: (row[1], " ".join(row[2].split())) for row in constraints},
                    "indexes": {row[0]: " ".join(row[1].split()) for row in indexes},
                    "columns": [tuple(row) for row in columns],
                    "comment": comment,
                }
            return described
    finally:
        await engine.dispose()


async def _bites(url: str) -> tuple[str | None, str | None]:
    """(SQLSTATE of a zero offset, SQLSTATE of a second baseline) on this database."""

    engine = create_async_engine(url, poolclass=NullPool)
    tag = uuid.uuid4().hex[:6].upper()
    equivalent_id, debtor_id, creditor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def _sqlstate(statement: str, params: dict) -> str | None:
        try:
            async with engine.begin() as connection:
                await connection.execute(text(statement), params)
        except Exception as exc:  # noqa: BLE001 - the SQLSTATE is the subject
            orig = getattr(exc, "orig", None)
            return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or repr(exc)
        return None

    try:
        async with engine.begin() as connection:
            # Through the model tables, so column defaults the probe does not care about are applied.
            await connection.execute(
                Equivalent.__table__.insert(),
                [{"id": equivalent_id, "code": f"S5{tag}", "precision": 2, "is_active": True, "metadata_": {}}],
            )
            await connection.execute(
                Participant.__table__.insert(),
                [
                    {
                        "id": participant,
                        "pid": f"S5A_{index}_{tag}",
                        "display_name": "probe",
                        "public_key": f"pk_s5a_{index}_{tag}",
                        "type": "person",
                        "status": "active",
                        "profile": {},
                    }
                    for index, participant in enumerate((debtor_id, creditor_id))
                ],
            )
            await connection.execute(
                text("INSERT INTO debt_reconciliation_baselines (equivalent_id) VALUES (:id)"),
                {"id": equivalent_id},
            )
        zero = await _sqlstate(
            "INSERT INTO debt_reconciliation_baseline_offsets "
            "(equivalent_id, debtor_id, creditor_id, offset_amount) VALUES (:e, :d, :c, 0)",
            {"e": equivalent_id, "d": debtor_id, "c": creditor_id},
        )
        second = await _sqlstate(
            "INSERT INTO debt_reconciliation_baselines (equivalent_id) VALUES (:id)",
            {"id": equivalent_id},
        )
        return zero, second
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_step5a_p_both_construction_paths_build_the_same_tables_and_both_bite() -> None:
    """`create_all` and `alembic upgrade head` agree on the three tables, and their constraints refuse.

    MUTATION that must redden this: change the offsets' debtor foreign key in migration 026 to
    `ondelete="CASCADE"` - the constraint definitions then differ between the two databases.
    """

    # NOT a skip when the role cannot create databases (T1701): `scratch_databases` raises. Until
    # 2026-09-21 this said "an ABSENT measurement, not a pass" and then reported a pass anyway.
    async with scratch_databases(_postgres_url(), "s5amig", "s5ameta") as (
        migrated_url,
        metadata_url,
    ):
        engine = create_async_engine(metadata_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

        # No preconditioning here: `migrations/env.py` owns the `alembic_version` widening and
        # establishes it inside this run (T1701).
        run_alembic_upgrade_head(migrated_url)

        from_metadata = await _describe(metadata_url)
        migrated = await _describe(migrated_url)

        for table in _TABLES:
            assert from_metadata[table]["columns"], f"create_all did not build {table}"
            assert migrated[table]["columns"], f"alembic did not build {table}"
        assert migrated == from_metadata, (
            "the two construction paths disagree:\n"
            + "\n".join(
                f"{table}.{part}:\n  alembic:  {migrated[table][part]}\n  metadata: {from_metadata[table][part]}"
                for table in _TABLES
                for part in migrated[table]
                if migrated[table][part] != from_metadata[table][part]
            )
        )

        constraints = migrated["debt_reconciliation_baseline_offsets"]["constraints"]
        assert all(
            "ON DELETE RESTRICT" in definition for kind, definition in constraints.values() if kind == "f"
        ), constraints
        assert sum(1 for kind, _ in constraints.values() if kind == "f") == 3, constraints
        assert "ON DELETE RESTRICT" in migrated["debt_reconciliation_baselines"]["constraints"][
            "fk_debt_reconciliation_baselines_equivalent"
        ][1]
        assert "ON DELETE CASCADE" in migrated["debt_reconciliation_results"]["constraints"][
            "fk_debt_reconciliation_results_equivalent"
        ][1]
        assert migrated["debt_reconciliation_baselines"]["comment"] == BASELINE_COMMENT

        for label, scratch in (("metadata", metadata_url), ("alembic", migrated_url)):
            zero, second = await _bites(scratch)
            assert (zero, second) == ("23514", "23505"), (
                f"{label}: a zero offset gave {zero!r} (expected CHECK 23514) and a second baseline gave "
                f"{second!r} (expected unique 23505)"
            )


# =================================================================================================
# The controls on this driver, and the scheduled host
# =================================================================================================


@pytest.mark.asyncio
async def test_step5a_p_passed_failed_unverifiable_and_the_scheduled_row(factory, monkeypatch) -> None:
    """On the migrated schema: honest payment PASSED, one atom FAILED, no baseline UNVERIFIABLE."""

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100")])
    unbaselined = await _seed_triangle(factory, trustlines=[])
    await _fixture_debts(factory, triangle, [("b", "a", "3")])
    await _fixture_debts(factory, unbaselined, [("a", "b", "2")])
    await _baseline(factory, triangle.equivalent.id)

    await _pay(factory, triangle, ["a", "b", "c"], "5")
    assert await _edges(factory, triangle) == {
        ("a", "b"): Decimal("2.00000000"),
        ("b", "c"): Decimal("5.00000000"),
    }
    honest = await _verify(factory, triangle.equivalent.id)
    assert (honest.status, honest.edges_checked) == (PASSED, 3), honest

    async with factory() as session:
        debt_id = (
            await session.execute(
                select(Debt.id).where(
                    Debt.equivalent_id == triangle.equivalent.id, Debt.debtor_id == triangle.b.id
                )
            )
        ).scalar_one()
    await _around_the_application(
        factory,
        lambda d: f"UPDATE debts SET amount = '5.00000001' WHERE id = '{_literal(d, debt_id)}'",
    )
    failed = await _verify(factory, triangle.equivalent.id)
    assert failed.status == FAILED, failed
    assert [f["unexplained"] for f in failed.findings] == ["0.00000001"], failed

    assert (await _verify(factory, unbaselined.equivalent.id)).status == UNVERIFIABLE

    await _scheduled_run(monkeypatch, factory)
    # And a repeat is a transition of nothing: still one row each (boolean marker and partial
    # unique index on this dialect).
    await _scheduled_run(monkeypatch, factory)
    assert [s for s, _ in await _results(factory, triangle.equivalent.id)] == [FAILED]
    assert [s for s, _ in await _results(factory, unbaselined.equivalent.id)] == [UNVERIFIABLE]


@pytest.mark.asyncio
async def test_step5a_p_a_fixture_write_after_the_baseline_is_refused(factory) -> None:
    """The refusal's `IN (...)` over native uuids, on asyncpg."""

    triangle = await _seed_triangle(factory, trustlines=[])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    late = Debt(
        id=uuid.uuid4(), debtor_id=triangle.b.id, creditor_id=triangle.c.id,
        equivalent_id=triangle.equivalent.id, amount=Decimal("4"), version=0,
    )
    with pytest.raises(BookError) as refused:
        async with factory() as session:
            async with debt_fixture_setup(session, label="after-baseline"):
                session.add(late)
            await session.commit()
    assert refused.value.reason == Refusal.UNVERIFIABLE_WRITER_AFTER_BASELINE, refused.value
    assert await _edges(factory, triangle) == {("a", "b"): Decimal("10.00000000")}


@pytest.mark.asyncio
async def test_step5a_p_a_payment_committed_between_the_verifiers_reads_is_still_passed(
    factory, committed_database, monkeypatch
) -> None:
    """The forced interleaving on PostgreSQL - A DIAGNOSTIC COUNTER-PROBE AT READ COMMITTED.

    The verifier must not depend on the engine happening to be `SERIALIZABLE`, so the whole stand - seed,
    payment and the scheduled verifier - runs on an engine that ASKS for READ COMMITTED, where every
    statement would otherwise take a new snapshot. The verifier's own REPEATABLE READ READ ONLY is then the
    only thing that can keep its reads in one snapshot.

    T1549, 2026-09-14: this used to inherit READ COMMITTED from the shared test engine, which now runs at the
    application's isolation. Inherited, it would have stayed green while silently no longer testing what it
    says; the level is requested explicitly here and checked.

    MUTATION: commit the session between `_journal_sums` and `_current_debts` - red.
    """

    from tests.unit.test_p015_step5a_reconciliation import (
        _assert_interleave,
        interleave_a_payment_between_the_verifiers_reads,
    )

    engine = create_async_engine(
        committed_database.url, isolation_level="READ COMMITTED", poolclass=NullPool
    )
    read_committed = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    seen = None
    try:
        async with read_committed() as probe:
            level = (await probe.execute(text("SHOW transaction_isolation"))).scalar_one()
        assert str(level).lower() == "read committed", f"stand: the counter-probe is not at READ COMMITTED: {level}"
        # 019 stage 5 (`T1907`, item 7): the payment refuses READ COMMITTED; this stand is about the
        # VERIFIER's own snapshot, so the payment's check is switched off for this test only - a named
        # diagnostic below the supported level (`allow_below_serializable_for_a_diagnostic`).
        skipped = allow_below_serializable_for_a_diagnostic(monkeypatch)
        seen = await interleave_a_payment_between_the_verifiers_reads(read_committed, monkeypatch)
        assert "payment" in skipped, "the diagnostic switch was not on the payment's path"
        _assert_interleave(seen)
    finally:
        await engine.dispose()


def _sqlstates(exc: BaseException | None) -> set[str]:
    found: set[str] = set()
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        for candidate in (exc, getattr(exc, "orig", None)):
            for attribute in ("sqlstate", "pgcode"):
                value = getattr(candidate, attribute, None)
                if isinstance(value, str):
                    found.add(value)
        exc = exc.__cause__ or exc.__context__
    return found


@pytest.mark.asyncio
async def test_step5a_p_a_baseline_committed_while_a_seed_is_open_cannot_commit_alongside_it(
    factory, committed_database
) -> None:
    """THE CUTOVER RACE, under the application's isolation level.

    A SEED operation flushes a debt; before it completes, a baseline for the same equivalent is taken
    and COMMITTED on another `SERIALIZABLE` transaction. The SEED's completion read of the baseline
    runs in a snapshot older than that commit and finds nothing. ASSERTED: the SEED still does not
    commit, and what stopped it is a serialization failure (`40001`), and the baseline stands.

    If this goes green for any other reason the assertion on the SQLSTATE says so; if the SEED commits,
    the refusal in `book._complete` is racy and the claim in its comment is false.
    """

    url = committed_database.url
    serializable = create_async_engine(url, isolation_level="SERIALIZABLE", poolclass=NullPool)
    sessions = async_sessionmaker(serializable, expire_on_commit=False, autoflush=False)
    triangle = await _seed_triangle(factory, trustlines=[])
    seed_error: BaseException | None = None
    try:
        late = Debt(
            id=uuid.uuid4(), debtor_id=triangle.a.id, creditor_id=triangle.b.id,
            equivalent_id=triangle.equivalent.id, amount=Decimal("4"), version=0,
        )
        async with sessions() as seed:
            try:
                async with Book.operation(
                    seed,
                    operation_for(
                        "SEED",
                        f"step5a-race/{uuid.uuid4()}",
                        {"probe": "cutover-race"},
                        scope_equivalent_ids=None,
                    ),
                ):
                    seed.add(late)
                    await seed.flush()
                    async with sessions() as cutover:
                        taken = await take_baseline(cutover, triangle.equivalent.id)
                        await cutover.commit()
                    assert taken.edges_seen == 0, f"stand: the baseline saw the uncommitted seed: {taken}"
                await seed.commit()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                seed_error = exc
                await seed.rollback()

        assert await _edges(factory, triangle) == {}, (
            f"a SEED committed alongside a concurrently committed baseline (error: {seed_error!r}). "
            f"The post-baseline refusal is racy under SERIALIZABLE."
        )
        assert "40001" in _sqlstates(seed_error), (
            f"the SEED did not commit, but not because of a serialization failure: {seed_error!r}"
        )
        async with factory() as session:
            headers = (
                await session.execute(
                    text("SELECT count(*) FROM debt_reconciliation_baselines WHERE equivalent_id = :id"),
                    {"id": triangle.equivalent.id},
                )
            ).scalar_one()
        assert headers == 1, "the baseline did not stand"
    finally:
        await serializable.dispose()
