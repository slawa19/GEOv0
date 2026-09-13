"""debts.debtor_id / creditor_id: CASCADE -> RESTRICT. Deleting a participant must not delete debt.

Revision ID: 025_debts_participant_fk_restrict
Revises: 024_debt_journal_delta
Create Date: 2026-09-13

Spec 015 / T1533. The participant half of the defect T1524 closed for the equivalent; migration 020
is the same change one column over, and this migration follows its shape deliberately.

WHAT WAS WRONG. `fk_debts_debtor_id` and `fk_debts_creditor_id` were created `ondelete="CASCADE"`
(migration 005), and the model declared the same. Deleting a participant therefore removed every
obligation they owed or were owed INSIDE THE DATABASE. No `Debt` instance is loaded, so no
application code, no audit row, no journal grant, no `_RowState` and no journal entry observes a
single obligation disappearing - the debt journal cannot see this by construction, which is why it
is closed at the constraint rather than instrumented.

REPRODUCED, not described, on 2026-09-13 against `geov0_test_t1533` built by
`alembic upgrade head` (stamp `024_debt_journal_delta` = repository head), with the constraint still
CASCADE as this migration found it:

    BEFORE: debts=1  sum(amount)=925.31000000
    DELETE FROM participants WHERE id = <debtor>  ->  DELETE 1      (no error)
    AFTER:  debts=0  sum(amount)=0                                  (lost 925.31000000)
    debt_operations rows: 0      debt_journal_entries rows: 0

HOW REACHABLE IT IS, MEASURED RATHER THAN ASSUMED, because overstating it would be as wrong as
skipping it. There is no participant hard-delete endpoint anywhere in `app/` - the protocol's
"deleted" is a `participants.status` value (`docs/en/02-protocol-spec.md` section 3.1:
`active | suspended | left | deleted`), not a missing row. And once a debt has history,
`debt_journal_entries` already refuses: measured on the same database, deleting a participant named
by an entry raised

    ForeignKeyViolationError ... violates foreign key constraint
    "fk_debt_journal_entries_debtor" on table "debt_journal_entries"

with the debt still standing. WHAT THAT LEAVES, and it is the whole point of this migration: the
debt is protected by SOMETHING ELSE'S history, not by its own foreign key. Delete the journal rows
for that operation and the very same `DELETE FROM participants` is accepted again and the
100.00000000 debt is gone - measured in the same script, one statement later. Every debt written
before the journal existed (migration 022), every debt written by a path that does not journal, and
every debt whose history has been disposed of is in exactly that state. There is no domain reason to
keep CASCADE for any of them.

WHAT THIS MIGRATION DOES. Recreates both foreign keys with `ondelete="RESTRICT"`. A participant who
still owes or is owed anything can no longer be removed by any statement, through any driver, below
every application check.

WHAT IT DOES NOT COVER, stated because a cheap fix with an unstated remainder is how a hole gets
called closed:
  * A DATABASE THAT ALREADY LOST ROWS. A cascade that ran before this migration left nothing behind
    to find - no tombstone, no journal entry - so nothing here can detect or restore it.
  * A CALLER THAT DELETES THE DEBTS FIRST and the participant second. That is an ordinary sequence
    of statements, each legal; RESTRICT is about the implicit deletion, not about intent. The debt
    deletion itself is what the journal and `C2`'s write guard cover.
  * ANYTHING THAT BYPASSES FOREIGN KEYS: `session_replication_role = replica`, a restore that loads
    data with constraints dropped, and SQLite with `PRAGMA foreign_keys=OFF` (which is how this
    repository's own test engine ran until 2026-09-11).
  * TRUST LINES AND PREPARE LOCKS still cascade on participant deletion
    (`fk_trust_lines_from_participant_id`, `fk_trust_lines_to_participant_id`,
    `fk_prepare_locks_participant_id`). Those are credit agreements and transient reservations
    rather than money obligations - the same boundary migration 020 drew - and after this change the
    participant delete that would have cascaded them is refused before it can, whenever any debt
    exists. Changing them is outside T1533.

ROWS THE NEW CONSTRAINT CANNOT BE APPLIED OVER, and what happens then. The set is exactly the set
the OLD constraint could not be applied over either: a `debts` row whose `debtor_id` or
`creditor_id` names a participant that does not exist. `ON DELETE` changes nothing about which rows
satisfy a foreign key, so any database that carries the CASCADE constraint today can carry this one.
A database that acquired orphans while the constraint was absent or unenforced fails LOUDLY and by
name - measured on `geov0_test_t1533` by manufacturing one:

    ADD CONSTRAINT refused: ForeignKeyViolationError
      insert or update on table "debts" violates foreign key constraint "fk_debts_debtor_id"
      detail: Key (debtor_id)=(87f319b1-...) is not present in table "participants".

That is the right outcome and it is deliberately not pre-empted: a migration that silently deleted
or repointed such a row would be destroying an obligation to install a constraint against destroying
obligations. The operator is told which key is dangling and decides.

THE CONSTRAINT NAMES ARE LOOKED UP, NOT ASSUMED, for the reason migration 020 records: migration 005
renames PostgreSQL's defaults (`debts_debtor_id_fkey`) to `fk_debts_debtor_id`, but a database whose
schema was built by `Base.metadata.create_all` and then stamped carries the default names, because
`Base` declares no `naming_convention` (that divergence is registered as T1540). So the foreign keys
on `debts` are reflected and whatever they are called is dropped; both are recreated under the names
005 chose.

Batch mode, as in 016 and 020, so the same migration is valid on SQLite. Both constraints are
replaced inside ONE batch block: on SQLite that is one table rebuild rather than two. SQLite unit
tests build the schema from the model and do not run Alembic, which is why the model carries the
same change.

DOWNGRADE restores CASCADE, exactly as 020's does. That reintroduces the defect and is provided only
for symmetry; it does not refuse on rows, because unlike 022 it destroys nothing by itself.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "025_debts_participant_fk_restrict"
down_revision = "024_debt_journal_delta"
branch_labels = None
depends_on = None

#: The migration-canonical name for each participant foreign key on `debts`, by column. These are
#: the names migration 005 created; the names actually found on the database are reflected below.
_TARGET_NAMES = {
    "debtor_id": "fk_debts_debtor_id",
    "creditor_id": "fk_debts_creditor_id",
}


def _participant_fk_names() -> dict[str, str]:
    """The actual names of the two debts -> participants foreign keys on THIS database.

    Refuses rather than guessing, for the reason 020 records: the name depends on how the database
    was built, and dropping a constraint by an assumed name rolls the whole migration back.
    """

    inspector = sa.inspect(op.get_bind())
    found: dict[str, str] = {}
    for fk in inspector.get_foreign_keys("debts"):
        if fk.get("referred_table") != "participants":
            continue
        columns = list(fk.get("constrained_columns") or [])
        if len(columns) != 1 or columns[0] not in _TARGET_NAMES:
            continue
        if columns[0] in found or not fk.get("name"):
            raise RuntimeError(
                f"expected exactly one named foreign key debts.{columns[0]} -> participants, "
                f"found a second or unnamed one ({fk.get('name')!r}); refusing to guess which "
                f"constraint to replace"
            )
        found[columns[0]] = fk["name"]
    if set(found) != set(_TARGET_NAMES):
        raise RuntimeError(
            f"expected foreign keys on both debts.debtor_id and debts.creditor_id -> participants, "
            f"found {found!r}; refusing to change a deletion policy on half the pair"
        )
    return found


def _replace(ondelete: str) -> None:
    existing = _participant_fk_names()
    with op.batch_alter_table("debts") as batch_op:
        for column, target_name in _TARGET_NAMES.items():
            batch_op.drop_constraint(existing[column], type_="foreignkey")
            batch_op.create_foreign_key(
                target_name,
                "participants",
                [column],
                ["id"],
                ondelete=ondelete,
            )


def upgrade() -> None:
    _replace("RESTRICT")


def downgrade() -> None:
    _replace("CASCADE")
