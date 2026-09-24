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

THE JOURNAL IS THE DATABASE'S (018 stage B1, 2026-09-24). The listener these counterexamples were
written against is gone: operations open through `Book` (`tests/p015_b4_support.operation`), entries
are written by the `debts` trigger from `OLD`/`NEW`, and a write outside an operation is `GE001`. Every
assertion below is the one the listener-era test made, re-expressed where the carrier changed: the
entry order is `ordinal` (strictly increasing, gapped - no longer the flush number 1..n); `C13`'s
refusal is the database's `UNIQUE(kind, identity)`; `C15`'s verdict is the SQLSTATE the database
returns to a process that imported only the models; `C18`'s competitor writes inside an operation of
its own BEFORE this one opens (a context is per TRANSACTION now, so a raw write inside this operation
would be recorded as this operation's - spec 018 `FORK-3`).

THE STAND. One clone of the migrated template for the whole module (`tests/p018_support.module_clone`),
real commits through a SERIALIZABLE engine over it; every test reads only its own world's rows and
the clone is dropped when the module ends. Money stays inside `|v| < 2^26`; full size is the
PostgreSQL sibling's.
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
import pytest_asyncio
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.debt_setup import debt_fixture_setup
from tests.p015_b4_support import (
    ENTRIES_TABLE,
    OPERATIONS_TABLE,
    World,
    exact_money,
    missing_journal_tables,
    operation,
    seed_world,
    stored_debts,
    stored_entries,
    stored_operations,
)
from tests.p018_support import module_clone, serializable_engine


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p015b4em") as url:
        yield url


@pytest_asyncio.fixture
async def engine(migrated_url):
    built = serializable_engine(migrated_url)
    try:
        yield built
    finally:
        await built.dispose()


@pytest_asyncio.fixture
async def factory(engine):
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _identity(name: str) -> str:
    return f"p015-b4/{name}/{uuid.uuid4().hex[:12]}"


def _intent(**fields) -> dict:
    return {"source": "p015-b4-counterexample", **fields}


def _open(session, world: World, name: str, **kw):
    """`operation()` with this module's standard arguments, so each test shows only what differs."""
    return operation(
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


async def _database_refusal(awaitable):
    """The database's refusal of `awaitable`, or None. Since 018 every refusal here is the schema's."""
    try:
        await awaitable
    except DatabaseError as exc:
        return exc
    return None


# ==============================================================================================
# C4 - the entry sequence of one edge's life
# ==============================================================================================


@pytest.mark.asyncio
async def test_c4_one_edge_through_insert_update_update_delete_is_four_linked_entries(
    factory,
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
    world = await seed_world(factory)
    identity = _identity("edge-life")
    async with factory() as session:
        async with _open(session, world, "edge-life", identity=identity):
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

    # The ORDER within the operation survives 018 stage B1; the exact numbers 1..4 do not. `ordinal`
    # is a sequence value per row - gapped, never a flush number - so only strictly increasing is a
    # property (manifest `T1808` §6, row `:173`; spec 018, "`ordinal` - порядок внутри операции").
    ordinals = [row["ordinal"] for row in entries]
    assert len(ordinals) == 4 and ordinals == sorted(set(ordinals)), entries
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


@pytest.mark.asyncio
async def test_c4_an_amount_change_and_a_delete_in_one_flush_are_one_effect(factory) -> None:
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
    world = await seed_world(factory)
    identity = _identity("zero-then-delete")
    starting_edge = world.debt("10.00")
    async with factory() as setup:
        # Built before the block: `fixture_block_violations` allows only constructors and session
        # calls inside one, and `world.debt(...)` is indistinguishable in the AST from a helper that
        # drives a writer. Same object, same single `add`, same flush.
        async with debt_fixture_setup(setup, label="starting-edge"):
            setup.add(starting_edge)
        await setup.commit()

    async with factory() as session:
        async with _open(session, world, "zero-then-delete", identity=identity):
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


@pytest.mark.asyncio
async def test_c4_an_edge_created_and_removed_again_still_counts_two_effects(factory) -> None:
    """C4, API-SHAPED. Net zero is not "nothing happened".

    An operation that creates an edge at 10 and later removes it leaves `debts` exactly as it found
    it. Its `effect_count` must still be 2. This is the counterexample against summarising an
    operation by its net effect: the money moved twice, and step 6 reconstructs a period by
    replaying deltas, not by diffing endpoints.

    RED TODAY BECAUSE: `debt_operations` does not exist, so `effect_count` has nowhere to live.
    MUTATION once step 4 exists: compute `effect_count` from the number of distinct edges whose
    final state differs from their initial state.
    """
    world = await seed_world(factory)
    identity = _identity("net-zero")
    async with factory() as session:
        async with _open(session, world, "net-zero", identity=identity):
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


@pytest.mark.asyncio
async def test_c4_a_deleted_edge_that_comes_back_is_an_insert_and_not_an_update(factory) -> None:
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
    world = await seed_world(factory)
    identity = _identity("reinsert")
    starting_edge = world.debt("10.00")
    async with factory() as setup:
        # Built before the block: `fixture_block_violations` allows only constructors and session
        # calls inside one, and `world.debt(...)` is indistinguishable in the AST from a helper that
        # drives a writer. Same object, same single `add`, same flush.
        async with debt_fixture_setup(setup, label="starting-edge"):
            setup.add(starting_edge)
        await setup.commit()

    async with factory() as session:
        async with _open(session, world, "reinsert", identity=identity):
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


# ==============================================================================================
# C13 - an identity is spent once
# ==============================================================================================


@pytest.mark.parametrize("second_intent", ["the same intent", "a different intent"])
@pytest.mark.asyncio
async def test_c13_a_second_operation_with_a_spent_identity_is_refused(
    factory, second_intent
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
    world = await seed_world(factory)
    identity = _identity("spent-identity")
    async with factory() as first:
        async with _open(
            first, world, "spent-identity", identity=identity, intent=_intent(attempt=1)
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
        reopen = await _database_refusal(_reopen(second, world, identity, intent))
        await second.rollback()

    assert reopen is not None, (
        f"a second operation opened under the spent identity {identity!r} with "
        f"{second_intent}; `UNIQUE(kind, identity)` is what stops a retried writer from "
        f"journalling its effects twice under the same name"
    )
    # WHICH refusal (018 stage B1): the database's unique constraint, not an application pre-check.
    assert isinstance(reopen, IntegrityError) and (
        "uq_debt_operations_kind_identity" in str(reopen)
    ), f"the second opening was refused by something other than UNIQUE(kind, identity): {reopen!r}"
    assert len(await stored_operations(factory, identity) or []) == 1, (
        "the refused second opening still left an envelope behind"
    )


async def _reopen(session, world: World, identity: str, intent: dict) -> None:
    """Enter and leave an operation under an identity that is already spent."""
    async with _open(session, world, "spent-identity", identity=identity, intent=intent):
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
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        return "REFUSED:" + type(exc).__name__ + ":" + str(sqlstate)
    async with factory() as fresh:
        from sqlalchemy import select
        stored = (await fresh.execute(select(Debt.amount))).scalars().all()
    await engine.dispose()
    return "STORED:" + ",".join(str(value) for value in stored)


print(asyncio.run(main()))
'''


@pytest.mark.asyncio
async def test_c15_a_process_that_imports_only_the_models_still_cannot_write_a_debt(
    tmp_path, migrated_url
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
    # The module's clone of the migrated template: the subprocess commits for real, so it must not
    # write into the tier's database; the clone is dropped when the module ends.
    url = migrated_url

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
    assert verdict.endswith(":GE001"), (
        f"the models-only write was refused, but not by the database's `debts` trigger: {verdict}"
    )


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


# ==============================================================================================
# C17 - deleting the rows the history names
# ==============================================================================================


@pytest.mark.parametrize("target", ["equivalent", "participant"])
@pytest.mark.asyncio
async def test_c17_a_row_the_journal_history_names_cannot_be_deleted(factory, target) -> None:
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
    world = await seed_world(factory)
    identity = _identity(f"history-outlives-{target}")
    async with factory() as session:
        async with _open(session, world, "history", identity=identity):
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


# ==============================================================================================
# C18 - a concurrent version bump
# ==============================================================================================


@pytest.mark.asyncio
async def test_c18_entries_come_from_the_attempt_that_succeeded_and_not_the_stale_one(
    factory, engine
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

    THE COMPETITOR IS A RAW UPDATE ON THE SAME CONNECTION, INSIDE AN OPERATION OF ITS OWN, AND IT RUNS
    BEFORE THIS ONE OPENS (018 stage B1, manifest `T1808` §6.C item 3). What it has to produce is
    unchanged: the row's `version` bumped underneath a session that has already loaded it, which is
    what makes the flush below raise `StaleDataError`. Where it runs is what changed. The listener
    journal let it through `exec_driver_sql`, its declared blind spot; the `debts` trigger refuses a
    write with no operation (`GE001`), and one issued INSIDE this operation would be recorded as this
    operation's own second entry - the context is per database transaction, not per session
    (`FORK-3`). So the competitor declares its own `TEST_FIXTURE` operation on a second session over
    the same connection, after this session loaded the row and before this operation opens. The
    two-backend form is the PostgreSQL sibling's.

    MUTATION: write the entry from the value the session loaded instead of `OLD` (the trigger's
    `amount_before`), or let the refused attempt's savepoint survive - the `amount_before`
    assertion, or the one-entry assertion, goes red.
    """
    world = await seed_world(factory)
    identity = _identity("stale-retry")
    stale_errors: list[StaleDataError] = []
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
            # This session loads the row first: version 0, amount 10.
            debt = (await mine.execute(mine_debt)).scalar_one()

            # The competitor bumps `version` AFTER this session loaded the row and BEFORE this
            # operation opens, inside an operation of its own on the same connection.
            async with AsyncSession(bind=connection, expire_on_commit=False) as competitor:
                async with _open(competitor, world, "the-competitor"):
                    await competitor.execute(
                        text(
                            "UPDATE debts SET amount = 31.00000000, version = version + 1 "
                            "WHERE id = :id"
                        ),
                        {"id": debt.id},
                    )

            async with _open(mine, world, "stale-retry", identity=identity):
                # The shape of `_apply_flow` (`app/core/ledger/book.py::_apply_payment_flow`):
                # each attempt inside its own savepoint, `expire_all()` between them.
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
