"""Programme 015, `B4` step 2: what the journal must RECORD, and what it must refuse to record.

WHAT THIS MODULE IS. The second slice of the step-2 acceptance bound to `B4` on 2026-09-11. The
first slice (commit `a4533c6`) covered the transaction contract and the write guard - when a write
is allowed to happen at all. This one covers the other half: given that a write is allowed, what the
journal must write down about it, and which values it must refuse before the write happens.

Design v2 §9 counterexamples carried here: `C4` (the entry sequence of one edge's life), `C12`
(money the dialect would silently change), `C13` (an identity is spent once), `C15` (a process that
imports only the models), `C17` (deleting the rows the history names), `C18` (a concurrent version
bump), `C19` (forged journal rows), plus **binding condition 4** - exact storability of the computed
DELTA, not only of the endpoints.

`C5` and `C6` live in `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py`, because they
need a real payment and a real clearing cycle rather than a bare session. The PostgreSQL tier is
`tests/integration/test_p015_b4_entries_and_money_postgres.py`.

TWO KINDS OF RED, the same two the first slice established and they read differently:

* DEFECT-SHAPED - the scenario runs today to its end and the database keeps what the journal would
  have refused. The failure quotes real stored money.
* API-SHAPED - the property has no carrier: there is no entries table to look in, no envelope to
  conflict with. These assert non-vacuity FIRST, so they cannot pass having measured nothing.

TIER. PostgreSQL: the tier database for every test whose subject is the journal's recording rule, and
a disposable mode-B clone for `C15`, whose writer runs in a process of its own. Money stays inside
`|v| < 2^26` (design v2 §4).

WHAT LEFT THIS MODULE WITH SQLITE (programme 017 stage 3, slice S3). Five tests ran on a SQLite file
of their own because their premise was a measurement of SQLite: `C12` and its control and condition
4 (what SQLite's float binding does to a value) and both halves of `C19` (which CHECKs SQLite's
schema carries). On PostgreSQL those premises are false - every `C12` value inside the domain is
stored exactly, and migration 024 makes `delta = after - before` a CHECK. The PostgreSQL inventory,
with the rule that speaks there, is `tests/integration/test_p015_b4_entries_and_money_postgres.py`
(`test_c12_p_*`, `test_c19_p_*`).

MARKER, HISTORICAL. This module carried `b4_counterexample` and was deselected from the canonical
gate while the debt journal did not exist. Step 4 slice C built it and REMOVED THE MARKER, not the
assertions: every test below still asserts exactly what it asserted while it was red, and each one
names in its docstring the mutation that must turn it red again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Uuid as SaUuid, bindparam, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm.exc import StaleDataError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.debt_setup import debt_fixture_setup
from tests.p015_b4_support import (
    ENTRIES_TABLE,
    OPERATIONS_TABLE,
    World,
    drop_world,
    exact_money,
    journal_api,
    missing_journal_tables,
    operation,
    seed_world,
    stored_debts,
    stored_entries,
    stored_operations,
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


def _money(rows: list[dict] | None, key: str) -> list[Decimal | None]:
    """A column of `stored_entries` as Decimals, so a float from SQLite cannot compare unequal."""
    return [None if row[key] is None else Decimal(str(row[key])) for row in rows or []]


async def _refusal_or_database_error(api, awaitable):
    """`refusal_of`, widened to the database's own complaint.

    Some counterexamples here are about a refusal that design v2 §5 places in the SCHEMA -
    `UNIQUE(kind, identity)`, a CHECK - rather than in the hook. Catching only the journal's
    exception types would let the test die on an `IntegrityError` instead of reading it as the
    refusal it is.
    """
    try:
        await awaitable
    except api.refusals as exc:  # noqa: B902 - the refusal contract is the subject under test
        return exc
    except DatabaseError as exc:
        return exc
    return None


# ==============================================================================================
# C4 - the entry sequence of one edge's life
# ==============================================================================================


@pytest.mark.asyncio
async def test_c4_one_edge_through_insert_update_update_delete_is_four_linked_entries(
    db_session,
) -> None:
    """C4, API-SHAPED. The journal is a chain per edge, not a diff of the two endpoints.

    An edge that goes 10 -> 7 -> 12 -> gone inside one operation must leave four entries whose
    `amount_before` is the previous entry's `amount_after`, whose `delta` is the difference, and
    whose last entry records the removal as a negative delta of the whole remaining amount. The
    property that makes this worth a counterexample is CONTINUITY: a journal that recorded only
    (first before, last after) would be indistinguishable from one that recorded the truth here, and
    step 6's verifier would have nothing to check the middle against.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so the non-vacuity assertion below -
    placed FIRST - fails naming the missing table.
    MUTATION once step 4 exists: aggregate the four flushes into one entry per operation per edge
    (net -0 here, so the operation would report "nothing happened"), or write `amount_before` from
    the in-memory attribute's ORIGINAL loaded value instead of the previous entry's `amount_after`.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("edge-life")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "edge-life", identity=identity):
                debt = world.debt("10.00")
                session.add(debt)
                await session.flush()
                debt.amount = exact_money("7.00")
                await session.flush()
                debt.amount = exact_money("12.00")
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST. Without the table there is nothing to be right or wrong about, and
        # every assertion below would be about an empty list.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: the four flushes really happened and really cancelled out.
        assert after == {}, f"stand: the edge was not removed by the end of the operation: {after}"

        assert [row["flush_ordinal"] for row in entries] == [1, 2, 3, 4], entries
        assert [row["effect"] for row in entries] == ["I", "U", "U", "D"], (
            f"the four flushes were not recorded as INSERT, UPDATE, UPDATE, DELETE: {entries}"
        )
        assert _money(entries, "amount_before") == [
            None,
            Decimal("10.00000000"),
            Decimal("7.00000000"),
            Decimal("12.00000000"),
        ], f"an entry's `amount_before` is not the previous entry's `amount_after`: {entries}"
        assert _money(entries, "amount_after") == [
            Decimal("10.00000000"),
            Decimal("7.00000000"),
            Decimal("12.00000000"),
            None,
        ], entries
        assert _money(entries, "delta") == [
            Decimal("10.00000000"),
            Decimal("-3.00000000"),
            Decimal("5.00000000"),
            Decimal("-12.00000000"),
        ], (
            f"the deltas do not reconstruct the edge's life; a DELETE in particular must record "
            f"the whole remaining amount as a negative delta: {entries}"
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c4_an_amount_change_and_a_delete_in_one_flush_are_one_effect(db_session) -> None:
    """C4, API-SHAPED. Per-flush aggregation per key: one flush, one entry for one edge.

    Design v2 §6 aggregates effects per key per flush. The case that decides the rule is "set it to
    zero, then delete it" in a single flush: the ORM emits only the DELETE, so a journal that
    listened to attribute events instead of to the flush plan would invent a `U` to `0.00000000`
    that never reached the database - and `0` is not even a value `debts` can hold
    (`chk_debt_amount_positive`). One entry, effect `D`, `amount_before` the value that was really
    stored.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist.
    MUTATION once step 4 exists: record effects from `attributes.get_history` per attribute change
    instead of from the flush plan - two entries appear, one of them describing a state the database
    never held.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("zero-then-delete")
    try:
        starting_edge = world.debt("10.00")
        async with factory() as setup:
            # Built before the block: `fixture_block_violations` allows only constructors and session
            # calls inside one, and `world.debt(...)` is indistinguishable in the AST from a helper that
            # drives a writer. Same object, same single `add`, same flush.
            async with debt_fixture_setup(setup, label="starting-edge"):
                setup.add(starting_edge)
            await setup.commit()

        async with factory() as session:
            async with await _open(api, session, world, "zero-then-delete", identity=identity):
                debt = (
                    await session.execute(
                        select(Debt).where(Debt.equivalent_id == world.equivalent.id)
                    )
                ).scalar_one()
                debt.amount = Decimal("0")
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: the row really went away, in one flush.
        assert after == {}, f"stand: the debt survived the delete: {after}"

        assert len(entries) == 1, (
            f"one flush touching one edge produced {len(entries)} entries: {entries}. The extra one "
            f"describes an amount the database never stored."
        )
        assert entries[0]["effect"] == "D", entries
        assert _money(entries, "amount_before") == [Decimal("10.00000000")], entries
        assert _money(entries, "amount_after") == [None], entries
        assert _money(entries, "delta") == [Decimal("-10.00000000")], entries
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c4_an_edge_created_and_removed_again_still_counts_two_effects(db_session) -> None:
    """C4, API-SHAPED. Net zero is not "nothing happened".

    An operation that creates an edge at 10 and later removes it leaves `debts` exactly as it found
    it. Its `effect_count` must still be 2. This is the counterexample against summarising an
    operation by its net effect: the money moved twice, and step 6 reconstructs a period by
    replaying deltas, not by diffing endpoints.

    RED TODAY BECAUSE: `debt_operations` does not exist, so `effect_count` has nowhere to live.
    MUTATION once step 4 exists: compute `effect_count` from the number of distinct edges whose
    final state differs from their initial state.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("net-zero")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "net-zero", identity=identity):
                debt = world.debt("10.00")
                session.add(debt)
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        envelopes = await stored_operations(factory, identity)
        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the operation really ended where it started.
        assert after == {}, f"stand: the edge outlived the operation: {after}"

        assert len(envelopes) == 1, envelopes
        assert envelopes[0]["effect_count"] == 2, (
            f"an operation that inserted an edge and removed it again reported "
            f"effect_count={envelopes[0]['effect_count']}: {envelopes}. Net zero is two effects, "
            f"not none."
        )
        assert [row["effect"] for row in entries or []] == ["I", "D"], entries
        assert _money(entries, "delta") == [Decimal("10.00000000"), Decimal("-10.00000000")], entries
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c4_a_deleted_edge_that_comes_back_is_an_insert_and_not_an_update(db_session) -> None:
    """C4, API-SHAPED. The journal is keyed by the EDGE, not by the row id.

    `debts` has `UNIQUE(debtor, creditor, equivalent)`, so an edge that is deleted and later
    recreated comes back under a NEW primary key. The journal keys its entries by the edge, so the
    two entries land in the same chain - and the second one must be an `I` with `amount_before`
    NULL, not a `U` from the value the deleted row used to hold. A journal that carried the old
    amount forward would claim continuity across a gap in which the edge genuinely did not exist.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist.
    MUTATION once step 4 exists: derive `amount_before` for an `I` from the last entry on the same
    edge instead of from NULL.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("reinsert")
    try:
        starting_edge = world.debt("10.00")
        async with factory() as setup:
            # Built before the block: `fixture_block_violations` allows only constructors and session
            # calls inside one, and `world.debt(...)` is indistinguishable in the AST from a helper that
            # drives a writer. Same object, same single `add`, same flush.
            async with debt_fixture_setup(setup, label="starting-edge"):
                setup.add(starting_edge)
            await setup.commit()

        async with factory() as session:
            async with await _open(api, session, world, "reinsert", identity=identity):
                debt = (
                    await session.execute(
                        select(Debt).where(Debt.equivalent_id == world.equivalent.id)
                    )
                ).scalar_one()
                first_id = debt.id
                await session.delete(debt)
                await session.flush()
                reborn = world.debt("4.00")
                session.add(reborn)
                await session.flush()
                second_id = reborn.id
            await session.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: it really is a new row on the same edge, which is the whole premise.
        assert first_id != second_id, "stand: the edge came back under the same primary key"
        assert after == {("debtor", "creditor", "eq"): Decimal("4.00000000")}, after

        assert [row["effect"] for row in entries] == ["D", "I"], entries
        assert _money(entries, "amount_before") == [Decimal("10.00000000"), None], (
            f"the re-created edge was recorded as a continuation of the row that was deleted: "
            f"{entries}"
        )
        assert _money(entries, "delta") == [Decimal("-10.00000000"), Decimal("4.00000000")], entries
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C13 - an identity is spent once
# ==============================================================================================


@pytest.mark.parametrize("second_intent", ["the same intent", "a different intent"])
@pytest.mark.asyncio
async def test_c13_a_second_operation_with_a_spent_identity_is_refused(
    db_session, second_intent
) -> None:
    """C13, API-SHAPED. `UNIQUE(kind, identity)` is the idempotency of the journal itself.

    An operation's identity is what makes a retried writer recognisable. Opening a second operation
    under an identity that already has an envelope must be refused by the DATABASE - design v2 §5's
    `UNIQUE(kind, identity)` - and not by a preceding SELECT, because two concurrent writers can
    both read "no envelope" and both proceed. Both parametrisations must be refused: an identical
    intent is still a second operation, and a DIFFERENT intent under the same identity is the case
    that would otherwise let a replay quietly rewrite what an operation claimed to be doing.

    RED TODAY BECAUSE: `debt_operations` does not exist, so there is no envelope to collide with.
    MUTATION once step 4 exists: pre-check the identity with a SELECT and skip the INSERT when a row
    is found - the test still passes single-threaded, so the PostgreSQL sibling
    (`test_p015_b4_entries_and_money_postgres.py`) runs the two openers concurrently.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("spent-identity")
    try:
        async with factory() as first:
            async with await _open(
                api, first, world, "spent-identity", identity=identity, intent=_intent(attempt=1)
            ):
                first.add(world.debt("19.00"))
                await first.flush()
            await first.commit()

        envelopes = await stored_operations(factory, identity)

        # NON-VACUITY, FIRST: the identity really was spent by a completed operation.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", (
            f"stand: the first operation did not leave a completed envelope: {envelopes}"
        )

        intent = _intent(attempt=1) if second_intent == "the same intent" else _intent(attempt=2)
        async with factory() as second:
            reopen = await _refusal_or_database_error(
                api, _reopen(api, second, world, identity, intent)
            )
            await second.rollback()

        assert reopen is not None, (
            f"a second operation opened under the spent identity {identity!r} with "
            f"{second_intent}; `UNIQUE(kind, identity)` is what stops a retried writer from "
            f"journalling its effects twice under the same name"
        )
        assert len(await stored_operations(factory, identity) or []) == 1, (
            "the refused second opening still left an envelope behind"
        )
    finally:
        await drop_world(factory, world)


async def _reopen(api, session, world: World, identity: str, intent: dict) -> None:
    """Enter and leave an operation under an identity that is already spent."""
    async with await _open(api, session, world, "spent-identity", identity=identity, intent=intent):
        pass


# ==============================================================================================
# C15 - a process that imports only the models
# ==============================================================================================


#: Run OUT OF PROCESS on purpose. Everything else in this module runs inside a pytest process where
#: `tests/conftest.py` has already imported half the application; a listener registered there would
#: make this counterexample pass for a reason that does not exist in production. The subprocess
#: imports `app.db.models` and nothing else - the minimum a script, a migration helper or an
#: operator's REPL would import - and the journal's protection must survive that.
_UNINSTRUMENTED_WRITER = '''
import asyncio, sys, uuid
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

URL = sys.argv[1]


async def main() -> str:
    # The database is a clone of the migrated template: the schema is already there.
    engine = create_async_engine(URL)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8].upper()
    async with factory() as session:
        equivalent = Equivalent(code="C15" + tag, precision=2, is_active=True, metadata_={})
        debtor = Participant(pid="C15D" + tag, display_name="d", public_key="c15d" + tag,
                             type="person", status="active", profile={})
        creditor = Participant(pid="C15C" + tag, display_name="c", public_key="c15c" + tag,
                               type="person", status="active", profile={})
        session.add_all([equivalent, debtor, creditor])
        await session.commit()
        ids = (equivalent.id, debtor.id, creditor.id)
    try:
        async with factory() as session:
            session.add(Debt(id=uuid.uuid4(), debtor_id=ids[1], creditor_id=ids[2],
                             equivalent_id=ids[0], amount=Decimal("21.00000000"), version=0))
            await session.commit()
    except BaseException as exc:
        await engine.dispose()
        return "REFUSED:" + type(exc).__name__
    async with factory() as fresh:
        from sqlalchemy import select
        stored = (await fresh.execute(select(Debt.amount))).scalars().all()
    await engine.dispose()
    return "STORED:" + ",".join(str(value) for value in stored)


print(asyncio.run(main()))
'''


@pytest.mark.asyncio
async def test_c15_a_process_that_imports_only_the_models_still_cannot_write_a_debt(
    tmp_path, committed_database
) -> None:
    """C15, DEFECT-SHAPED. The protection must live with the models, not with the application.

    WHY OUT OF PROCESS. Every other counterexample here runs where `tests/conftest.py` has already
    imported the world. If the journal's listeners were registered by an application entry point -
    `app/main.py`, a service constructor, a FastAPI dependency - every one of those tests would
    still pass and a `python -c` one-liner, a data-fix script or `scripts/seed_db.py` run by hand
    would write debts with no journal at all. Design v2 §1.3 puts the listeners on class-level
    `ConnectionEvents`, which is what makes importing the models enough; this is the counterexample
    that can tell the two designs apart.

    RED TODAY BECAUSE: the subprocess stores `21.00000000` and nothing objects. The failure below
    quotes what it stored.
    MUTATION once step 4 exists: register the listeners from `app/main.py`'s startup or from
    `app/db/session.py`'s engine construction instead of from the module that defines the tables.
    """
    script = tmp_path / "uninstrumented_writer.py"
    script.write_text(textwrap.dedent(_UNINSTRUMENTED_WRITER), encoding="utf-8")
    # A disposable mode-B clone of the migrated template (017 stage 3, slice S3; a SQLite file in
    # `tmp_path` before). The subprocess commits for real, so it must not write into the tier's
    # database; the clone is dropped when the test ends.
    url = committed_database.url

    # `app` is not installed into the virtualenv, and `python script.py` puts the SCRIPT's
    # directory on `sys.path`, not the working directory - so the repository root is passed
    # explicitly. That is what a real operator script does too.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(_repo_root())
    completed = subprocess.run(
        [sys.executable, str(script), url],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(_repo_root()),
        env=environment,
    )

    # NON-VACUITY, FIRST: the subprocess really ran its scenario. A crashed import, a missing
    # dependency or a schema failure would otherwise read as "the write was refused".
    assert completed.returncode == 0, (
        f"stand: the subprocess did not finish its scenario (exit {completed.returncode}).\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr[-2000:]}"
    )
    verdict = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    assert verdict.startswith(("REFUSED:", "STORED:")), (
        f"stand: the subprocess printed no verdict.\nstdout: {completed.stdout}\n"
        f"stderr: {completed.stderr[-2000:]}"
    )

    # VERDICT.
    assert verdict.startswith("REFUSED:"), (
        f"a process that imported only `app.db.models` wrote a debt with no operation open and "
        f"nothing refused it: the database now holds {verdict.removeprefix('STORED:')}. The "
        f"journal's protection must arrive with the models, not with an application entry point."
    )


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


# ==============================================================================================
# C17 - deleting the rows the history names
# ==============================================================================================


@pytest.mark.parametrize("target", ["equivalent", "participant"])
@pytest.mark.asyncio
async def test_c17_a_row_the_journal_history_names_cannot_be_deleted(db_session, target) -> None:
    """C17, API-SHAPED. History outlives the debts, so it must outlive their referents too.

    T1524 made `debts.equivalent_id` RESTRICT, so an equivalent with live debts cannot be deleted.
    That is not enough once a journal exists: an equivalent whose ONLY debt has been cleared has no
    `debts` row left, and deleting it would take the meaning of every journal entry denominated in
    it with it - the entries would survive as numbers pointing at nothing. `debts.debtor_id` and
    `debts.creditor_id` are still CASCADE (`app/db/models/debt.py:11-12`, deliberately, R2-13), so
    the participant half is worse: today the delete SUCCEEDS and removes the rows silently.

    Design v2 §5 makes every journal FK RESTRICT. This counterexample is what that decision is for.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so there is no history to protect and
    nothing to refuse the delete.
    MUTATION once step 4 exists: make the journal's FKs CASCADE - migration 021 then deletes the
    history along with the row and this test goes red again, which is exactly the mutation design
    v2 §10.1 names ("Mutation: journal FK CASCADE").
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity(f"history-outlives-{target}")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "history", identity=identity):
                debt = world.debt("23.00")
                session.add(debt)
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(factory, identity)
        debts_now = await stored_debts(factory, world)

        # NON-VACUITY, FIRST: there is history, and there are no debts left to hide behind. Without
        # this the delete below would be refused by T1524's RESTRICT on `debts` and the test would
        # pass without the journal existing at all.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)
        assert len(entries) == 2, f"stand: the operation left {entries} instead of an I and a D"
        assert debts_now == {}, (
            f"stand: a debt is still live, so any refusal below would be T1524's and not the "
            f"journal's: {debts_now}"
        )

        # THE ID IS BOUND AS A UUID, not as its dashed string: `Uuid(as_uuid=True)` stores the
        # 32-character hex on SQLite, so `WHERE id = '<dashed>'` matched no row at all, deleted
        # nothing, raised nothing - and "the database allowed it" below was reporting a DELETE that
        # never happened. Measured 2026-09-12 while arming the journal. `text()` is still what is
        # executed, so this remains the raw-SQL path the case is about.
        uuid_bind = bindparam("id", type_=SaUuid(as_uuid=True))
        if target == "equivalent":
            statement = text("DELETE FROM equivalents WHERE id = :id").bindparams(uuid_bind)
            params = {"id": world.equivalent.id}
            survivors = select(Equivalent.id).where(Equivalent.id == world.equivalent.id)
        else:
            statement = text("DELETE FROM participants WHERE id = :id").bindparams(uuid_bind)
            params = {"id": world.debtor.id}
            survivors = select(Participant.id).where(Participant.id == world.debtor.id)

        error = None
        async with factory() as remover:
            try:
                await remover.execute(statement, params)
                await remover.commit()
            except DatabaseError as exc:
                error = exc
                await remover.rollback()

        async with factory() as fresh:
            still_there = (await fresh.execute(survivors)).scalar_one_or_none()

        # VERDICT.
        assert error is not None, (
            f"a raw DELETE removed the {target} that {len(entries)} journal entries name, and the "
            f"database allowed it. The entries are now money history denominated in a row that does "
            f"not exist. Every journal foreign key is RESTRICT for this reason (design v2 §5)."
        )
        assert still_there is not None, f"the {target} is gone despite the refusal"
        assert len(await stored_entries(factory, identity) or []) == 2, (
            "the journal entries did not survive the refused delete"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C18 - a concurrent version bump
# ==============================================================================================


@pytest.mark.asyncio
async def test_c18_entries_come_from_the_attempt_that_succeeded_and_not_the_stale_one(
    db_session,
) -> None:
    """C18, API-SHAPED. A losing optimistic-lock attempt must leave no trace in the journal.

    `Debt` carries `version_id_col` (`app/db/models/debt.py:23`), so an ORM writer whose row was
    bumped underneath it raises `StaleDataError` on flush, and `PaymentEngine._apply_flow`
    (`app/core/payments/engine.py:1465-1553`) answers by expiring the identity map and retrying up
    to three times. The journal has to survive that: the attempt that raised wrote no row, so it
    must contribute no entry, and the entry the retry writes must carry the CONCURRENT value as
    `amount_before` - not the value this session had loaded before losing the race. A journal that
    captured `amount_before` when the attribute was first loaded would record a continuity that
    never existed, and step 6 would reconstruct the edge from a number no database ever held.

    THE COMPETITOR IS A RAW DRIVER UPDATE, and the reason is a measurement, not a preference.
    Since T1525 this tier takes real snapshots, so two independent SQLite writers cannot coexist -
    the second fails with `database is locked`, which is a fact about the stand. This test therefore
    used to bump the version from a SECOND `Session` on the SAME `Connection`; once the journal was
    armed (step 4 slice C) that stopped being possible, and correctly: two sessions on one
    connection are one Core root transaction, an operation is bound to the session that opened it,
    and a foreign session's Debt write is refused - which is `C9`'s property, asserted there. The
    competitor is not what this counterexample is about, so it moved to `exec_driver_sql`, the
    documented unintercepted path (design v2 §6, §8 R6). What it produces is unchanged: the row's
    `version` is bumped underneath a session that has already loaded it, which is what makes the
    flush below raise `StaleDataError`. The two-backend, two-writer form is on PostgreSQL.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist.
    MUTATION once step 4 exists: capture `amount_before` in `before_flush` from
    `attributes.get_history(...).unchanged[0]` without re-reading after the retry's `expire_all()`,
    or write entries in `before_flush` rather than `after_flush`, so the failed attempt leaves one.
    """
    from tests.conftest import TestingSessionLocal as factory, engine
    from sqlalchemy.ext.asyncio import AsyncSession

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("stale-retry")
    stale_errors: list[StaleDataError] = []
    try:
        starting_edge = world.debt("10.00")
        async with factory() as setup:
            # Built before the block: `fixture_block_violations` allows only constructors and session
            # calls inside one, and `world.debt(...)` is indistinguishable in the AST from a helper that
            # drives a writer. Same object, same single `add`, same flush.
            async with debt_fixture_setup(setup, label="starting-edge"):
                setup.add(starting_edge)
            await setup.commit()

        mine_debt = select(Debt).where(Debt.equivalent_id == world.equivalent.id)
        async with engine.connect() as connection:
            await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as mine:
                async with await _open(api, mine, world, "stale-retry", identity=identity):
                    debt = (await mine.execute(mine_debt)).scalar_one()

                    # The competitor bumps `version` AFTER this session loaded the row and BEFORE
                    # it flushes - outside the savepoint below, so the rollback of the losing
                    # attempt cannot undo the competitor's work as well. Through the driver: see
                    # the docstring.
                    # The id is written in PostgreSQL's canonical form and inlined rather than
                    # bound: `exec_driver_sql` takes the DRIVER's placeholder syntax (`$1` for
                    # asyncpg). The value comes from a `uuid.UUID`, so nothing here is interpolated
                    # from data.
                    stored_id = str(debt.id)
                    await connection.exec_driver_sql(
                        "UPDATE debts SET amount = 31.00000000, version = version + 1 "
                        f"WHERE id = '{stored_id}'"  # noqa: S608
                    )

                    # The shape of `_apply_flow` (`app/core/payments/engine.py:1474-1553`): each
                    # attempt inside its own savepoint, `expire_all()` between them.
                    for attempt in range(2):
                        try:
                            async with mine.begin_nested():
                                debt.amount = exact_money("44.00")
                                await mine.flush()
                            break
                        except StaleDataError as exc:
                            stale_errors.append(exc)
                            mine.expire_all()
                            debt = (await mine.execute(mine_debt)).scalar_one()
                    else:  # pragma: no cover - the loop breaks on the retry
                        pytest.fail("stand: the retry never succeeded")
                await connection.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST: the race really happened, exactly once, and the retry really won.
        assert len(stale_errors) == 1, (
            f"stand: the concurrent version bump produced {len(stale_errors)} StaleDataError(s), "
            f"not one; without it this test measures nothing about a losing attempt"
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("44.00000000")}, (
            f"stand: the retry did not win the edge: {after}"
        )
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # VERDICT.
        updates = [row for row in entries if row["effect"] == "U"]
        assert len(entries) == 1 and len(updates) == 1, (
            f"the losing attempt left a trace in the journal: {entries}. Only the flush that "
            f"reached the database may produce an entry."
        )
        assert _money(entries, "amount_before") == [Decimal("31.00000000")], (
            f"the retry's entry records `amount_before` as the value this session had loaded before "
            f"losing the race, not the value the database actually held: {entries}"
        )
        assert _money(entries, "delta") == [Decimal("13.00000000")], (
            f"the delta was computed against a state that never existed: {entries}"
        )
    finally:
        await drop_world(factory, world)
