"""The test-local operation context.

Programme 015, phase B step 4, slices B and C (design v2 §7 "Test-local context", §8 "Test
migration and codemod safety", §9 C21).

WHY THIS EXISTS. Slice A built the debt journal (`app/core/ledger/journal.py`): once its listeners
are registered, a row in `debts` may only change inside a declared operation, and a bare
`session.add(Debt(...))` is refused. The test suite writes debts in ~130 places that declare
nothing. Slice C registers the listeners; if the suite reached that day unmigrated, every one of
those places would fail at once and the activation could not be told apart from a defect in the
journal. This module is the declaration those places migrate onto.

IT IS LIVE (slice C, 2026-09-12). While slice B was landing, the journal was installed on no engine
at all and `debt_fixture_setup` did nothing: no SQL, no flush, no session state. That is what made
the migration behaviour-neutral and let both canonical gates keep their exact baseline counts -
the evidence that wrapping ~130 setup sites changed no test's meaning. Slice C armed the journal on
the `Engine` and `Session` classes (`app/core/ledger/journal.py`, `arm_journal_globally`), and the
same call sites now open and complete a real `TEST_FIXTURE` operation with no further edit to any
test. Since 018 stage B1 the journal is the database's trigger, the listener module and its stand-down
are gone, and so is the no-op path that served a stood-down engine.

WHAT IT DELIBERATELY DOES NOT DO: it never flushes. Design v2 §8 R2 forbids inserting a flush where
none existed, because a flush is SQL and SQL is behaviour. The block ends just before whatever
`flush()`/`commit()` the test already had; `Book.operation`'s own completion flush covers the block's
work, and the test's own flush that follows finds nothing pending.

DISPOSAL (018 stage B1, 2026-09-24). `purge_test_ledger`, which deleted a test's debts and journal rows
through the driver, left with its last caller (`tests/p015_b4_support.drop_world`): the database now
refuses every one of those deletes, and a test that commits runs on a disposable clone whose drop is
the disposal (`tests/tier_on_a_clone.py`, `tests/p018_support.py::module_clone`). `_uuid_literals` stays: other
modules still render ids through it.

THE STATIC GUARD over fixture-block bodies (`fixture_block_violations`) lost its only caller with
the repository-wide guard and was deleted 2026-10-16 (041 `T4104`).
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Sequence

__all__ = [
    "FIXTURE_OPERATION_KIND",
    "add_debts",
    "debt_fixture_setup",
    "writer_operation",
]

#: The operation kind reserved for fixture setup. `TEST_FIXTURE` and `SEED` are the only kinds the
#: journal lets open without a declared equivalent scope, because neither holds a lock set it could
#: derive one from (`app/core/ledger/journal.py`, `_validate_arguments`).
FIXTURE_OPERATION_KIND = "TEST_FIXTURE"


# =================================================================================================
# The runtime half
# =================================================================================================


def _node_id() -> str:
    """The pytest node id of the test currently running, or a stable stand-in.

    `PYTEST_CURRENT_TEST` is "<nodeid> (setup|call|teardown)"; the phase is dropped so that setup
    and call under one test share an identity prefix.
    """

    current = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not current:
        return "<no-pytest-node>"
    return current.rsplit(" (", 1)[0]


@asynccontextmanager
async def debt_fixture_setup(session: Any, *, label: str) -> AsyncIterator[Any]:
    """Declare that the debts written in this block are fixture setup, not a payment.

    Yields the book's posting (018 stage A). Callers must not depend on the yielded value: it exists
    so that a test which needs the envelope can reach it, not as part of the setup contract.

    `label` names what the block is setting up. It is part of the operation's identity, so two
    blocks in the same test are distinguishable in the journal; `uuid4` makes the identity unique
    across runs, which is what lets C13's duplicate-identity refusal stay a refusal of a real
    duplicate rather than of a re-run.
    """

    if not label:
        raise ValueError("debt_fixture_setup needs a label naming what this block sets up")

    # THERE IS NO NO-OP PATH ANY MORE (018 stage B1). It served an engine the listener journal had
    # been stood down on; the journal is now the database's trigger, which no engine can stand down,
    # so every fixture block is a real `TEST_FIXTURE` operation.
    #
    # THE ENVELOPE IS THE BOOK'S (018 stage A): the fixture operation opens through `Book`, the
    # single writer of `debts`, like every application writer. The rows the test builds inside the
    # block are test code, outside `app/` and `scripts/` and so outside the single-writer guard by
    # construction (`tests/unit/test_p018_only_book_writes_debts.py`); the journal records them as
    # this operation's effects exactly as before.
    from app.core.ledger.book import Book, operation_for

    node_id = _node_id()
    async with Book.operation(
        session,
        operation_for(
            FIXTURE_OPERATION_KIND,
            f"{node_id}:{label}:{uuid.uuid4()}",
            {"node_id": node_id, "label": label},
            scope_equivalent_ids=None,
        ),
    ) as posting:
        yield posting


@asynccontextmanager
async def writer_operation(
    session: Any,
    *,
    kind: str,
    equivalent_ids: Iterable[Any],
    initiator_id: Any = None,
    label: str = "writer-internals",
) -> AsyncIterator[Any]:
    """The operation a WRITER would have opened, for a test that drives the writer's internals.

    WHY IT IS NOT `debt_fixture_setup` (design v2 §8 R5/F7). A test that calls
    the book's payment flow or `InjectExecutor.stage_inject_event` directly is exercising a
    production writer, not setting up a fixture. Wrapping those calls in a `TEST_FIXTURE` context
    would journal a payment's effects under the kind reserved for scaffolding, and `C21`'s runtime
    half - the journal's refusal to nest operations - only catches application code that opens an
    operation OF ITS OWN, which `_apply_flow` does not. So these tests declare the writer's REAL
    kind, and the journal records what a payment or an inject actually did.

    `PAYMENT` and `CLEARING` carry a `tx_id` that references `transactions.tx_id`, so this creates
    the minimal `Transaction` row the reference needs when the caller has not. That row is an extra
    write these tests did not make before; it is named here rather than hidden, and none of them
    asserts anything about `transactions`.
    """

    from app.core.ledger.book import Book, operation_for
    from app.db.journal_tables import OPERATION_KINDS_WITH_TX

    tx_id = None
    if kind in OPERATION_KINDS_WITH_TX:
        from app.db.models.transaction import Transaction

        tx_id = f"WRITER-{uuid.uuid4()}"
        session.add(
            Transaction(
                tx_id=tx_id,
                type="PAYMENT" if kind == "PAYMENT" else "CLEARING",
                initiator_id=initiator_id,
                payload={},
                # A PAYMENT row is terminal since programme 019 stage 4 (migration 030's CHECK
                # refuses any other state even uncommitted); a CLEARING row may still be NEW.
                state="COMMITTED" if kind == "PAYMENT" else "NEW",
            )
        )
        await session.flush()

    # THE BOOK'S OPERATION (018 stage A). The writer internals these tests drive
    # (the book's payment flow, `InjectExecutor.stage_inject_event`) apply their effects through
    # `Book.current(session)`, so the operation they run under has to be a `Book` posting.
    equivalent_ids = set(equivalent_ids)
    async with Book.operation(
        session,
        operation_for(
            kind,
            f"{_node_id()}:{label}:{uuid.uuid4()}",
            {"node_id": _node_id(), "label": label, "driven_directly": True},
            tx_id=tx_id,
            scope_equivalent_ids=equivalent_ids,
            intent_equivalent_ids=equivalent_ids,
        ),
    ) as posting:
        yield posting


def _uuid_literals(values: Iterable[Any]) -> list[str]:
    """The values as SQL literals of PostgreSQL's native `uuid`, or a loud failure.

    EVERY VALUE GOES THROUGH `uuid.UUID` FIRST, which is what makes interpolating them safe -
    nothing that is not a UUID can reach a statement.

    THE SPELLING WAS PER DIALECT until 017 stage 3, which is not cosmetic. `sqlalchemy.Uuid(as_uuid=True)`
    stored the 32-character hex WITHOUT dashes on SQLite and a native `uuid` on PostgreSQL, so a
    statement written with the canonical dashed form matched nothing at all on the SQLite tier - it
    deleted no rows, raised nothing, and the next `DELETE FROM participants` failed on a foreign key
    whose referent the teardown believed it had removed. Measured 2026-09-12 while activating the
    journal, and it is exactly the shape of a cleanup that silently does nothing.

    KEPT when `purge_test_ledger` left (018 stage B1): other modules still render ids through it
    (`tests/integration/test_simulator_real_snapshot_db_enrichment.py`).
    """

    ids = [uuid.UUID(str(value)) for value in values]
    return [str(value) for value in ids]


async def add_debts(session: Any, debts: Iterable[Any], *, label: str = "setup") -> Sequence[Any]:
    """Add a batch of already-constructed debts inside one fixture operation.

    The convenience form of `debt_fixture_setup` for the common shape - build the rows, hand them
    over, let the test's own `flush()`/`commit()` follow. It does NOT flush, for the reason given in
    this module's docstring: inserting a flush where the test had none is a behaviour change.

    Returns the debts it added, in order, so it can replace `session.add_all([...])` without the
    caller losing its handles.
    """

    items = list(debts)
    async with debt_fixture_setup(session, label=label):
        session.add_all(items)
    return items


def transactions_of(participant_ids: Iterable[Any]) -> Any:
    """`transactions` rows of a test world: a payment by its initiator, a clearing by a debtor of its `edges`.

    028 F-028-45 (migration 036): a clearing records no initiator, so `initiator_id IN (...)` no longer finds it."""
    from sqlalchemy import exists, or_, text

    from app.db.models.participant import Participant
    from app.db.models.transaction import Transaction

    ids = list(participant_ids)
    debtor_of_an_edge = exists().where(
        Participant.id.in_(ids),
        text("CAST(transactions.payload AS jsonb) -> 'edges' @> "
             "jsonb_build_array(jsonb_build_object('debtor', participants.pid))"),
    )
    return or_(Transaction.initiator_id.in_(ids), debtor_of_an_edge)
