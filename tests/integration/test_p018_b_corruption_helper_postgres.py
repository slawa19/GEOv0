"""018 `FORK-4`: the named corruption helper (`tests/ledger_corruption.py`) does what it says and no more.

* It models a write with the triggers off: one atom committed through it is NOT journalled, and the
  scheduled verifier's criterion (a) turns that into `FAILED` - the independent evidence the spec keeps
  for the `T1508` forms (a helper that could not produce a `FAILED` would make every rewritten
  corruption test vacuous).
* A CHECK probe through it meets the CHECK and not the guard trigger: `23514`, rolled back.
* The `replica` setting does not outlive the helper's own transaction.
* It refuses a database that is not a disposable clone of this tier, before connecting.
* It refuses a role that may not set `session_replication_role`, with the grant to ask for - and the
  refusal matches reality: under that role the `SET LOCAL` itself is denied (`42501`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger.book import Book, NewDebt, operation_for
from app.core.ledger.reconciliation import FAILED, PASSED, take_baseline, verify_journal_equals_change
from tests.ledger_corruption import CorruptionHelperError, corrupt, probe, require_the_privilege
from tests.p018_support import (
    CHECK_VIOLATION,
    module_clone,
    seed_world,
    serializable_engine,
    sqlstate_of,
)


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018bcorrupt") as url:
        yield url


@pytest.mark.asyncio
async def test_one_atom_through_the_helper_is_unjournalled_and_criterion_a_fails_it(
    migrated_url,
) -> None:
    engine = serializable_engine(migrated_url)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session)
            await Book.post(
                session,
                operation_for("TEST_FIXTURE", f"helper-{uuid.uuid4()}", {"seed": True}),
                [NewDebt(world.p(0), world.p(1), world.eq, Decimal("10"))],
            )
            await session.commit()
            await take_baseline(session, world.eq)
            await session.commit()
            assert (await verify_journal_equals_change(session, world.eq)).status == PASSED
            await session.commit()

        await corrupt(
            migrated_url,
            [
                "UPDATE debts SET amount = amount + 0.00000001 "
                f"WHERE debtor_id = '{world.p(0)}' AND creditor_id = '{world.p(1)}'"
            ],
        )

        async with engine.connect() as observer:
            assert (
                await observer.execute(text("SHOW session_replication_role"))
            ).scalar_one() == "origin"
            amount = (
                await observer.execute(
                    text("SELECT amount FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
                )
            ).scalar_one()
            assert amount == Decimal("10.00000001")
            entries = (
                await observer.execute(
                    text("SELECT count(*) FROM debt_journal_entries WHERE equivalent_id = :eq"),
                    {"eq": world.eq},
                )
            ).scalar_one()
            assert entries == 1  # the seed's insert only: the atom left no entry

        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            outcome = await verify_journal_equals_change(session, world.eq)
            assert outcome.status == FAILED
            assert any(f.get("kind") == "edge_residual" for f in outcome.findings), outcome.findings
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_check_probe_through_the_helper_meets_the_check_not_the_guard(migrated_url) -> None:
    engine = serializable_engine(migrated_url)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session)
            await session.commit()
        operation_id = uuid.uuid4()
        entry = (
            "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, debtor_id, "
            "creditor_id, effect, amount_before, amount_after, delta) VALUES "
            f"('{uuid.uuid4()}', '{operation_id}', 1, '{world.eq}', '{world.p(0)}', '{world.p(1)}', "
            "'U', 10, 11, {delta})"
        )
        # Contradictory arithmetic: the CHECK refuses it under replica (the guard does not run).
        assert await probe(migrated_url, entry.format(delta=2)) == CHECK_VIOLATION
        # The control: the same row with honest arithmetic is admitted (and rolled back) - FK
        # triggers are off too, which is why the operation id need not exist.
        assert await probe(migrated_url, entry.format(delta=1)) is None
        async with engine.connect() as observer:
            assert (
                await observer.execute(
                    text("SELECT count(*) FROM debt_journal_entries WHERE operation_id = :op"),
                    {"op": operation_id},
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_helper_refuses_a_database_that_is_not_a_disposable_clone() -> None:
    from tests.conftest import TEST_DATABASE_URL

    with pytest.raises(CorruptionHelperError, match="disposable clone"):
        await corrupt(TEST_DATABASE_URL, ["SELECT 1"])
    with pytest.raises(CorruptionHelperError, match="disposable clone"):
        await probe(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres", "SELECT 1")


@pytest.mark.asyncio
async def test_the_helper_refuses_a_role_without_the_privilege_and_the_refusal_matches_reality(
    migrated_url,
) -> None:
    """`pg_read_all_data` is a predefined role with no SET privilege on the parameter; a superuser may
    `SET ROLE` to it, which gives a real unprivileged role without creating one on the cluster."""

    engine = serializable_engine(migrated_url)
    try:
        async with engine.connect() as connection:
            await require_the_privilege(connection)  # the test role itself has it
            await connection.exec_driver_sql("SET ROLE pg_read_all_data")
            with pytest.raises(CorruptionHelperError, match="GRANT SET ON PARAMETER"):
                await require_the_privilege(connection)
            with pytest.raises(Exception) as denied:
                await connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
            assert sqlstate_of(denied.value) == "42501"
            await connection.rollback()
    finally:
        await engine.dispose()
