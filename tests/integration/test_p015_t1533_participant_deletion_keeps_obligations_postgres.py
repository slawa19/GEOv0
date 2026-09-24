"""T1533: deleting a participant must never delete obligations. The PostgreSQL half.

WHERE THE DEBT WENT. `debts.debtor_id` and `debts.creditor_id` were
`ForeignKey('participants.id', ondelete='CASCADE')` on the model and `fk_debts_debtor_id` /
`fk_debts_creditor_id` with `ondelete="CASCADE"` in migration 005. Deleting a participant removed
every obligation they owed or were owed INSIDE THE DATABASE: no `Debt` instance is loaded, so no
application code, no audit row, no journal grant, no `_RowState` and no journal entry sees a single
obligation disappear. It is the participant half of what T1524 closed for the equivalent, and it is
older than the journal. Measured on this database before migration 025:

    BEFORE: debts=1  sum(amount)=925.31000000
    DELETE FROM participants WHERE id = <debtor>  ->  DELETE 1  (no error)
    AFTER:  debts=0  sum(amount)=0

THE DEBT HERE HAS NO JOURNAL HISTORY, AND THAT IS THE WHOLE DESIGN OF THIS MODULE. Once an operation
has been journalled, `debt_journal_entries.debtor_id` and `.creditor_id` are already RESTRICT
(`C17`), so deleting a participant named by an entry is refused by THAT constraint - measured, on
this same database: `violates foreign key constraint "fk_debt_journal_entries_debtor"`. A test that
seeded its debt through `debt_fixture_setup` would therefore be GREEN UNDER CASCADE and would prove
nothing about `debts` at all. So the row is inserted with NO ENVELOPE - exactly the state of every
debt written before migration 022 and of every debt whose history has been disposed of.
`test_the_refusal_is_the_debts_own_constraint` asserts that emptiness rather than assuming it.

SINCE 018 STAGE B1 ONLY THE NAMED CORRUPTION HELPER CAN PRODUCE THAT ROW (spec 018 `FORK-4`, a named
use; `tests/ledger_corruption.py`): the database refuses a write to `debts` with no operation named in
the transaction (`GE001`), and a write inside an operation has history. The helper writes it on its own
connection with the journal's triggers off and commits; every deletion below then runs on an ordinary
connection with the triggers and foreign keys ON - asserted on that connection - so the refusal
measured is the one a real deletion meets.

ONE DISPOSABLE CLONE FOR THE MODULE (018 B1). The rows are committed and cannot be deleted row by row
any more (a `DELETE FROM debts` outside an operation is `GE001` too), so the module runs on a clone of
the migrated template (`tests/p018_support.py::module_clone`) whose drop is the only disposal. Each
test seeds its own equivalent and participants, so the tests do not see each other's rows.

HOW REACHABLE THIS IS, stated so that it is neither overstated nor used as an excuse. There is no
participant hard-delete endpoint in `app/`: the protocol's "deleted" is a `participants.status`
value (`docs/en/02-protocol-spec.md` section 3.1), not a missing row. What this closes is the path
below every application check - raw SQL, a future endpoint, an admin script, a repair tool - and it
closes it for the rows the journal's own RESTRICT does not reach.

WHY BOTH TIERS. `tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py` is the MODEL
half, on a schema `Base.metadata.create_all` builds. This half runs on a clone of the migrated template
and so proves the MIGRATION, on a schema `alembic upgrade head` built - which is the thing T1534 made
true by construction and the reason this task waited for it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.ledger_corruption import corrupt
from tests.p018_support import module_clone

#: The amount the reproducer lost. Kept as the literal that was measured disappearing.
AMOUNT = Decimal("925.31000000")


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018t1533") as url:
        yield url


@pytest.fixture
async def sessions(migrated_url):
    """Ordinary sessions over the module's clone: triggers and foreign keys ON (asserted)."""

    engine = create_async_engine(migrated_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as probe:
        role = (await probe.execute(text("SHOW session_replication_role"))).scalar_one()
    assert role == "origin", f"stand: the test's own connections run with triggers {role!r}"
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(sessions, url: str, *, with_debt: bool):
    """Two participants, an equivalent, and optionally ONE DEBT WITH NO JOURNAL HISTORY.

    The debt goes in through the named corruption helper on purpose - see the module docstring. It
    is not a way around the triggers for convenience; it is the only way to produce the state this
    test is about, and a journalled debt would make every assertion below vacuous.
    """

    nonce = uuid.uuid4().hex[:8]
    async with sessions() as s:
        eq = Equivalent(
            code=("P" + nonce).upper()[:16], description="T1533", precision=2, is_active=True
        )
        debtor = Participant(pid="pd" + nonce, display_name="D", public_key="pkpd-" + nonce)
        creditor = Participant(pid="pc" + nonce, display_name="C", public_key="pkpc-" + nonce)
        s.add_all([eq, debtor, creditor])
        await s.commit()
    ids = (eq.id, debtor.id, creditor.id)
    debt_id = None
    if with_debt:
        debt_id = uuid.uuid4()
        await corrupt(
            url,
            [
                "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                f"VALUES ('{debt_id}', '{uuid.UUID(str(debtor.id))}', "
                f"'{uuid.UUID(str(creditor.id))}', '{uuid.UUID(str(eq.id))}', {AMOUNT}, 0)"
            ],
        )
    return ids, debt_id


async def _debt_sum(sessions, eq_id) -> Decimal:
    async with sessions() as s:
        total = (
            await s.execute(
                select(Debt.amount).where(Debt.equivalent_id == eq_id)
            )
        ).scalars().all()
    return sum(total, Decimal("0"))


async def _delete_participant(sessions, participant_id) -> IntegrityError | None:
    """Delete the row the way anything below the application would, and report the refusal.

    BOTH THE STATEMENT AND THE COMMIT ARE INSIDE THE `try`, and that is not defensive padding.
    PostgreSQL's foreign keys are `NOT DEFERRABLE` here, so the refusal arrives on the DELETE
    itself, not at commit; a version of this helper that guarded only the commit reported the
    refusal as an ERROR in the test rather than as the result it is. Measured 2026-09-13.
    """

    async with sessions() as s:
        try:
            await s.execute(delete(Participant).where(Participant.id == participant_id))
            await s.commit()
        except IntegrityError as exc:
            await s.rollback()
            return exc
    return None


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_debtor_who_still_owes(sessions, migrated_url) -> None:
    """RED before T1533: the cascade removes the obligation and nothing refuses."""
    (eq_id, debtor_id, _creditor_id), _debt_id = await _seed(sessions, migrated_url, with_debt=True)
    before = await _debt_sum(sessions, eq_id)
    assert before == AMOUNT, f"stand: the unjournalled debt was not seeded, sum is {before}"

    error = await _delete_participant(sessions, debtor_id)

    after = await _debt_sum(sessions, eq_id)
    assert after == before, (
        f"deleting the debtor destroyed what they owed: the foreign key cascaded "
        f"{before - after} away without loading a single Debt row"
    )
    assert error is not None, "the database accepted deleting a participant who still owes"
    assert "fk_debts_debtor_id" in str(error), (
        f"the refusal did not come from the debtor foreign key this task changed: {error}"
    )


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_creditor_who_is_still_owed(
    sessions, migrated_url
) -> None:
    """The other half of the pair, and it is not a copy: it is a SECOND constraint.

    `debtor_id` and `creditor_id` are separate foreign keys with separate names. Changing one and
    leaving the other would close half the hole - which is precisely the shape T1524 left behind.
    """
    (eq_id, _debtor_id, creditor_id), _debt_id = await _seed(sessions, migrated_url, with_debt=True)
    before = await _debt_sum(sessions, eq_id)
    assert before == AMOUNT, f"stand: the unjournalled debt was not seeded, sum is {before}"

    error = await _delete_participant(sessions, creditor_id)

    after = await _debt_sum(sessions, eq_id)
    assert after == before, (
        f"deleting the creditor destroyed what they were owed: {before - after} cascaded away"
    )
    assert error is not None, "the database accepted deleting a participant who is still owed"
    assert "fk_debts_creditor_id" in str(error), (
        f"the refusal did not come from the creditor foreign key this task changed: {error}"
    )


@pytest.mark.asyncio
async def test_the_refusal_is_the_debts_own_constraint_and_not_the_journals_history(
    sessions, migrated_url
) -> None:
    """ANTI-VACUITY. Without this, the two tests above could be passing on `C17`, not on T1533.

    `debt_journal_entries` RESTRICTs both participant columns, so a participant named by an entry
    cannot be deleted whatever `debts` says - measured on this database as
    `violates foreign key constraint "fk_debt_journal_entries_debtor"`. This test asserts that no
    journal row names either participant, so the only constraint that can refuse is `debts`'.
    """
    (_eq_id, debtor_id, creditor_id), _debt_id = await _seed(sessions, migrated_url, with_debt=True)
    async with sessions() as s:
        named = (
            await s.execute(
                text(
                    "SELECT count(*) FROM debt_journal_entries "
                    "WHERE debtor_id IN (:d, :c) OR creditor_id IN (:d, :c)"
                ),
                {"d": debtor_id, "c": creditor_id},
            )
        ).scalar_one()
    assert named == 0, (
        f"{named} journal entries name these participants, so the refusals asserted by the "
        f"tests above are C17's RESTRICT and say nothing about debts' own foreign keys"
    )

    error = await _delete_participant(sessions, debtor_id)
    assert error is not None and "fk_debts_debtor_id" in str(error), (
        f"with no history at all, only debts' own foreign key can refuse, and it did not: "
        f"{error}"
    )


@pytest.mark.asyncio
async def test_a_participant_who_owes_nothing_still_deletes(sessions, migrated_url) -> None:
    """Control. RESTRICT must not turn every participant deletion into a refusal.

    This is the half a migration gets wrong: a policy that refuses everything would also pass the
    three tests above, and would break every teardown in this suite.
    """
    (_eq_id, debtor_id, _creditor_id), _debt_id = await _seed(
        sessions, migrated_url, with_debt=False
    )
    error = await _delete_participant(sessions, debtor_id)
    assert error is None, f"a participant with no obligations was refused deletion: {error}"

    async with sessions() as s:
        survived = (
            await s.execute(select(Participant.id).where(Participant.id == debtor_id))
        ).scalar_one_or_none()
    assert survived is None, "the delete reported success and the row is still there"


@pytest.mark.asyncio
async def test_the_migrated_schema_declares_restrict_on_both_participant_columns(sessions) -> None:
    """Migration 025 itself, read off the catalog of the schema the migrations built.

    The four tests above prove the BEHAVIOUR, and they would also pass on a schema built by
    `Base.metadata.create_all` from the changed model - which is what the SQLite half proves and
    what this tier ran on until T1534. This one names the constraint and its `ON DELETE`, so the
    evidence that the MIGRATION carries the change is separate from the evidence that the model
    does. `confdeltype` is `r` for RESTRICT and `c` for CASCADE.
    """
    async with sessions() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT con.conname, con.confdeltype::text "
                    "FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
                    "JOIN pg_class frel ON frel.oid = con.confrelid "
                    "WHERE con.contype = 'f' AND rel.relname = 'debts' "
                    "AND frel.relname = 'participants'"
                )
            )
        ).all()

    policies = {name: kind for name, kind in rows}
    assert policies == {"fk_debts_debtor_id": "r", "fk_debts_creditor_id": "r"}, (
        f"the migrated schema does not declare RESTRICT on both participant foreign keys of "
        f"debts: {policies}"
    )
