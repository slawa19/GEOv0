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
nothing about `debts` at all. So the row is inserted through `exec_driver_sql`, which dispatches no
`before_execute` and so reaches `debts` without the journal write guard and without an envelope -
exactly the state of every debt written before migration 022, every debt written by a path that does
not journal, and every debt whose history has been disposed of. `test_the_refusal_is_the_debts_own_
constraint` asserts that emptiness rather than assuming it.

HOW REACHABLE THIS IS, stated so that it is neither overstated nor used as an excuse. There is no
participant hard-delete endpoint in `app/`: the protocol's "deleted" is a `participants.status`
value (`docs/en/02-protocol-spec.md` section 3.1), not a missing row. What this closes is the path
below every application check - raw SQL, a future endpoint, an admin script, a repair tool - and it
closes it for the rows the journal's own RESTRICT does not reach.

WHY BOTH TIERS. `tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py` is the SQLite
half and proves the MODEL, whose schema `Base.metadata.create_all` builds. This half runs with
`GEO_TEST_USE_MIGRATED_SCHEMA=1` and so proves the MIGRATION, on a schema `alembic upgrade head`
built - which is the thing T1534 made true by construction and the reason this task waited for it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

#: The amount the reproducer lost. Kept as the literal that was measured disappearing.
AMOUNT = Decimal("925.31000000")


def _uuid_literal(value, dialect: str) -> str:
    """A UUID as a SQL literal of this dialect's storage, or a loud failure.

    `uuid.UUID` first, which is what makes the interpolation below safe. And the spelling is per
    dialect for the reason `tests/debt_setup.py::_uuid_literals` records: `Uuid(as_uuid=True)`
    stores 32-character hex on SQLite and a native `uuid` on PostgreSQL, so a statement written with
    the wrong one matches or inserts nothing and raises nothing.
    """

    parsed = uuid.UUID(str(value))
    return parsed.hex if dialect == "sqlite" else str(parsed)


async def _seed(*, with_debt: bool):
    """Two participants, an equivalent, and optionally ONE DEBT WITH NO JOURNAL HISTORY.

    The debt goes in through `exec_driver_sql` on purpose - see the module docstring. It is not a
    way around the write guard for convenience; it is the only way to produce the state this test
    is about, and a journalled debt would make every assertion below vacuous.
    """

    from tests.conftest import TestingSessionLocal, _ensure_schema_initialized

    await _ensure_schema_initialized()
    nonce = uuid.uuid4().hex[:8]
    async with TestingSessionLocal() as s:
        eq = Equivalent(
            code=("P" + nonce).upper()[:16], description="T1533", precision=2, is_active=True
        )
        debtor = Participant(pid="pd" + nonce, display_name="D", public_key="pkpd-" + nonce)
        creditor = Participant(pid="pc" + nonce, display_name="C", public_key="pkpc-" + nonce)
        s.add_all([eq, debtor, creditor])
        await s.flush()
        ids = (eq.id, debtor.id, creditor.id)
        debt_id = None
        if with_debt:
            debt_id = uuid.uuid4()
            connection = await s.connection()
            dialect = connection.dialect.name
            await connection.exec_driver_sql(
                "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                f"VALUES ('{_uuid_literal(debt_id, dialect)}', "  # noqa: S608 - UUID-parsed above
                f"'{_uuid_literal(debtor.id, dialect)}', "
                f"'{_uuid_literal(creditor.id, dialect)}', "
                f"'{_uuid_literal(eq.id, dialect)}', {AMOUNT}, 0)"
            )
        await s.commit()
    return ids, debt_id


async def _cleanup(eq_id, participant_ids) -> None:
    """The debt goes through the driver, for the reason `purge_test_ledger` documents.

    `session.execute(delete(Debt))` is Core DML against `debts` outside a verified flush and the
    journal's write guard refuses it (`C2`). There are no journal rows to dispose of here: this
    module never opens an operation.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
        connection = await s.connection()
        literal = _uuid_literal(eq_id, connection.dialect.name)
        await connection.exec_driver_sql(
            f"DELETE FROM debts WHERE equivalent_id = '{literal}'"  # noqa: S608 - UUID-parsed
        )
        await s.execute(delete(Participant).where(Participant.id.in_(participant_ids)))
        await s.execute(delete(Equivalent).where(Equivalent.id == eq_id))
        await s.commit()


async def _debt_sum(eq_id) -> Decimal:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
        total = (
            await s.execute(
                select(Debt.amount).where(Debt.equivalent_id == eq_id)
            )
        ).scalars().all()
    return sum(total, Decimal("0"))


async def _delete_participant(participant_id) -> IntegrityError | None:
    """Delete the row the way anything below the application would, and report the refusal.

    BOTH THE STATEMENT AND THE COMMIT ARE INSIDE THE `try`, and that is not defensive padding.
    PostgreSQL's foreign keys are `NOT DEFERRABLE` here, so the refusal arrives on the DELETE
    itself, not at commit; a version of this helper that guarded only the commit reported the
    refusal as an ERROR in the test rather than as the result it is. Measured 2026-09-13.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
        try:
            await s.execute(delete(Participant).where(Participant.id == participant_id))
            await s.commit()
        except IntegrityError as exc:
            await s.rollback()
            return exc
    return None


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_debtor_who_still_owes() -> None:
    """RED before T1533: the cascade removes the obligation and nothing refuses."""
    (eq_id, debtor_id, creditor_id), _debt_id = await _seed(with_debt=True)
    try:
        before = await _debt_sum(eq_id)
        assert before == AMOUNT, f"stand: the unjournalled debt was not seeded, sum is {before}"

        error = await _delete_participant(debtor_id)

        after = await _debt_sum(eq_id)
        assert after == before, (
            f"deleting the debtor destroyed what they owed: the foreign key cascaded "
            f"{before - after} away without loading a single Debt row"
        )
        assert error is not None, "the database accepted deleting a participant who still owes"
        assert "fk_debts_debtor_id" in str(error), (
            f"the refusal did not come from the debtor foreign key this task changed: {error}"
        )
    finally:
        await _cleanup(eq_id, [debtor_id, creditor_id])


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_creditor_who_is_still_owed() -> None:
    """The other half of the pair, and it is not a copy: it is a SECOND constraint.

    `debtor_id` and `creditor_id` are separate foreign keys with separate names. Changing one and
    leaving the other would close half the hole - which is precisely the shape T1524 left behind.
    """
    (eq_id, debtor_id, creditor_id), _debt_id = await _seed(with_debt=True)
    try:
        before = await _debt_sum(eq_id)

        error = await _delete_participant(creditor_id)

        after = await _debt_sum(eq_id)
        assert after == before, (
            f"deleting the creditor destroyed what they were owed: {before - after} cascaded away"
        )
        assert error is not None, "the database accepted deleting a participant who is still owed"
        assert "fk_debts_creditor_id" in str(error), (
            f"the refusal did not come from the creditor foreign key this task changed: {error}"
        )
    finally:
        await _cleanup(eq_id, [debtor_id, creditor_id])


@pytest.mark.asyncio
async def test_the_refusal_is_the_debts_own_constraint_and_not_the_journals_history() -> None:
    """ANTI-VACUITY. Without this, the two tests above could be passing on `C17`, not on T1533.

    `debt_journal_entries` RESTRICTs both participant columns, so a participant named by an entry
    cannot be deleted whatever `debts` says - measured on this database as
    `violates foreign key constraint "fk_debt_journal_entries_debtor"`. This test asserts that no
    journal row names either participant, so the only constraint that can refuse is `debts`'.
    """
    from tests.conftest import TestingSessionLocal

    (eq_id, debtor_id, creditor_id), _debt_id = await _seed(with_debt=True)
    try:
        async with TestingSessionLocal() as s:
            connection = await s.connection()
            dialect = connection.dialect.name
            named = (
                await connection.exec_driver_sql(
                    "SELECT count(*) FROM debt_journal_entries WHERE debtor_id IN "
                    f"('{_uuid_literal(debtor_id, dialect)}', "  # noqa: S608 - UUID-parsed
                    f"'{_uuid_literal(creditor_id, dialect)}') "
                    "OR creditor_id IN "
                    f"('{_uuid_literal(debtor_id, dialect)}', "
                    f"'{_uuid_literal(creditor_id, dialect)}')"
                )
            ).scalar_one()
        assert named == 0, (
            f"{named} journal entries name these participants, so the refusals asserted by the "
            f"tests above are C17's RESTRICT and say nothing about debts' own foreign keys"
        )

        error = await _delete_participant(debtor_id)
        assert error is not None and "fk_debts_debtor_id" in str(error), (
            f"with no history at all, only debts' own foreign key can refuse, and it did not: "
            f"{error}"
        )
    finally:
        await _cleanup(eq_id, [debtor_id, creditor_id])


@pytest.mark.asyncio
async def test_a_participant_who_owes_nothing_still_deletes() -> None:
    """Control. RESTRICT must not turn every participant deletion into a refusal.

    This is the half a migration gets wrong: a policy that refuses everything would also pass the
    three tests above, and would break every teardown in this suite.
    """
    from tests.conftest import TestingSessionLocal

    (eq_id, debtor_id, creditor_id), _debt_id = await _seed(with_debt=False)
    try:
        error = await _delete_participant(debtor_id)
        assert error is None, f"a participant with no obligations was refused deletion: {error}"

        async with TestingSessionLocal() as s:
            survived = (
                await s.execute(select(Participant.id).where(Participant.id == debtor_id))
            ).scalar_one_or_none()
        assert survived is None, "the delete reported success and the row is still there"
    finally:
        await _cleanup(eq_id, [debtor_id, creditor_id])


@pytest.mark.asyncio
async def test_the_migrated_schema_declares_restrict_on_both_participant_columns() -> None:
    """Migration 025 itself, read off the catalog of the schema the migrations built.

    The four tests above prove the BEHAVIOUR, and they would also pass on a schema built by
    `Base.metadata.create_all` from the changed model - which is what the SQLite half proves and
    what this tier ran on until T1534. This one names the constraint and its `ON DELETE`, so the
    evidence that the MIGRATION carries the change is separate from the evidence that the model
    does. `confdeltype` is `r` for RESTRICT and `c` for CASCADE.
    """
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
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
