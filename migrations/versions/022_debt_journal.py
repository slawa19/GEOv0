"""The debt journal: operation envelopes, per-edge entries, per-equivalent completion rows.

Revision ID: 022_debt_journal
Revises: 021_money_columns_reject_nan
Create Date: 2026-09-12

Spec 015 / `B4`, step 4 slice A. Design: `specs/015-financial-core-verification/step4-design/
design-v2.md` §5.

WHAT THESE TABLES ARE FOR. `docs/ru/02-protocol-spec.md` §11.2.1 withdrew the zero-sum check as a
tautology of the edge model and named its replacement in the same sentence: a comparison of `debts`
against a journal of operations, edge by edge, with the history that produced them. There is no
such history in this database today, which is why the check it replaces cannot be written. These
three tables are that history:

* `debt_operations` - one row per unit of work that intended to move money: who, under what
  identity, with what declared intent, and whether it finished.
* `debt_journal_entries` - one row per directed edge per flush: what the amount was, what it
  became, and the signed difference.
* `debt_operation_equivalents` - written once at completion: which equivalents the operation said
  it would touch, which it did touch, and how much of each.

THERE IS NO CHECKSUM CHAIN, and its absence is a decision rather than an omission. `head_hash`,
`prev_hash`, `hash` and `algorithm_version` were in the design and were REMOVED by owner decision
of 2026-09-12: an unkeyed chain detects change only relative to hashes the same database holds, so
anyone able to rewrite the rows recomputes it, and the only adversary it stops is one who could not
have altered `debts` in the first place. It would have read as tamper-evidence while not being it.
Nothing in these tables is tamper-evident; the guarantee they carry is that the record is written
in the SAME database transaction as the money, so a transaction that moved money without a record
does not commit.

MONEY COLUMNS ARE `MoneyNumeric`, NOT BARE `Numeric` (T1526). The DDL is identical - `NUMERIC(20,
8)` on both dialects - so this migration emits exactly what the model builds. What the type adds is
a refusal to BIND a non-finite value, and it is not interchangeable with the CHECK constraints
below. Measured 2026-09-12: on PostgreSQL the CHECKs refuse `NaN` through the MAGNITUDE clause
(`'NaN' > 0` is true, every upper bound is false), while on SQLite a bound `NaN` arrives as `NULL`
and never reaches a CHECK at all. `Infinity`, by contrast, does reach it and is refused there. The
two guards cover different values on different dialects, which is why both exist.

CONSTRAINTS ARE NAMED EXPLICITLY, all of them. An unnamed CHECK gets a name from the database, and
migration 020 is the record of what that costs: a later migration could not drop a constraint whose
name it had assumed. Every constraint here is named in this file and in `app/db/journal_tables.py`
with the same string, and the SQLite tier (schema from the model) and the PostgreSQL tier (schema
from these migrations) are both exercised by the step-4 tests, which is what holds the two
descriptions together.

DOWNGRADE REFUSES WHILE THE TABLES HOLD ROWS. Dropping them is not a schema change that can be
undone - it destroys the only record of what every instrumented writer did, and does it silently.
So `downgrade` counts first and refuses, naming the counts. An operator who genuinely wants the
journal gone empties it deliberately, which is a decision somebody makes rather than a side effect
of running a downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "022_debt_journal"
down_revision = "021_money_columns_reject_nan"
branch_labels = None
depends_on = None


#: The largest value `NUMERIC(20, 8)` can hold. Written out rather than imported from
#: `app.db.types`, as in migration 021 and for the same reason: a migration must keep describing
#: the schema change it made even if the application constant later moves.
MONEY_COLUMN_MAX = "999999999999.99999999"

#: The three tables, in creation order (each one references the ones before it).
_TABLES = ("debt_operations", "debt_journal_entries", "debt_operation_equivalents")

_KINDS = "'PAYMENT', 'CLEARING', 'INJECT', 'SEED', 'TEST_FIXTURE'"
_TX_KINDS = "'PAYMENT', 'CLEARING'"


def _money(column: str, *, nullable: bool) -> str:
    """Sign, magnitude, and not-a-number - the same three jobs as `debts.amount` (migration 021).

    The magnitude clause is what excludes `NaN` on PostgreSQL and is not decoration; the `<> 'NaN'`
    clause says so explicitly so that relaxing the bound later cannot silently remove the NaN
    guard. See `app/db/types.py::finite_money_clauses`.
    """

    predicate = f"{column} > 0 AND {column} <= {MONEY_COLUMN_MAX} AND {column} <> 'NaN'"
    return f"({column} IS NULL OR ({predicate}))" if nullable else f"({predicate})"


def upgrade() -> None:
    op.create_table(
        "debt_operations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("identity", sa.String(length=256), nullable=False),
        sa.Column("tx_id", sa.String(length=64), nullable=True),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("intent_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("money_encoding_version", sa.SmallInteger(), nullable=False),
        sa.Column("intent_encoding_version", sa.SmallInteger(), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flush_count", sa.Integer(), nullable=True),
        sa.Column("effect_count", sa.Integer(), nullable=True),
        sa.Column("effect_digest", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_debt_operations"),
        sa.ForeignKeyConstraint(
            ["tx_id"],
            ["transactions.tx_id"],
            name="fk_debt_operations_tx_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name="chk_debt_operations_kind"),
        sa.CheckConstraint("length(identity) > 0", name="chk_debt_operations_identity_present"),
        sa.CheckConstraint("length(intent_digest) = 64", name="chk_debt_operations_intent_digest"),
        sa.CheckConstraint("schema_version IN (1)", name="chk_debt_operations_schema_version"),
        sa.CheckConstraint("money_encoding_version IN (1)", name="chk_debt_operations_money_version"),
        sa.CheckConstraint(
            "intent_encoding_version IN (1)", name="chk_debt_operations_intent_version"
        ),
        sa.CheckConstraint("state IN ('OPEN', 'COMPLETED')", name="chk_debt_operations_state"),
        sa.CheckConstraint(
            f"(tx_id IS NOT NULL) = (kind IN ({_TX_KINDS}))",
            name="chk_debt_operations_tx_id_iff_kind",
        ),
        sa.CheckConstraint(
            "("
            " state = 'OPEN'"
            " AND completed_at IS NULL AND flush_count IS NULL"
            " AND effect_count IS NULL AND effect_digest IS NULL"
            ") OR ("
            " state = 'COMPLETED'"
            " AND completed_at IS NOT NULL AND flush_count IS NOT NULL"
            " AND effect_count IS NOT NULL AND effect_digest IS NOT NULL"
            " AND length(effect_digest) = 64"
            " AND flush_count >= 0 AND effect_count >= 0"
            " AND flush_count <= effect_count"
            " AND ((flush_count = 0 AND effect_count = 0)"
            "      OR (flush_count > 0 AND effect_count > 0))"
            ")",
            name="chk_debt_operations_completion",
        ),
        sa.UniqueConstraint("kind", "identity", name="uq_debt_operations_kind_identity"),
        sa.UniqueConstraint("tx_id", name="uq_debt_operations_tx_id"),
    )
    op.create_index(
        "ix_debt_operations_open",
        "debt_operations",
        ["kind", "identity"],
        unique=False,
        postgresql_where=sa.text("state = 'OPEN'"),
        sqlite_where=sa.text("state = 'OPEN'"),
    )

    op.create_table(
        "debt_journal_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("operation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("flush_ordinal", sa.Integer(), nullable=False),
        sa.Column("equivalent_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("debtor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("creditor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effect", sa.String(length=1), nullable=False),
        sa.Column("amount_before", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("amount_after", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("delta", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_debt_journal_entries"),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["debt_operations.id"],
            name="fk_debt_journal_entries_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["equivalent_id"],
            ["equivalents.id"],
            name="fk_debt_journal_entries_equivalent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["debtor_id"],
            ["participants.id"],
            name="fk_debt_journal_entries_debtor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["creditor_id"],
            ["participants.id"],
            name="fk_debt_journal_entries_creditor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("flush_ordinal >= 1", name="chk_debt_journal_entries_ordinal"),
        sa.CheckConstraint("debtor_id <> creditor_id", name="chk_debt_journal_entries_no_self_loop"),
        sa.CheckConstraint("effect IN ('I', 'U', 'D')", name="chk_debt_journal_entries_effect"),
        sa.CheckConstraint(
            "(effect = 'I' AND amount_before IS NULL AND amount_after IS NOT NULL)"
            " OR (effect = 'U' AND amount_before IS NOT NULL AND amount_after IS NOT NULL"
            "     AND amount_before <> amount_after)"
            " OR (effect = 'D' AND amount_before IS NOT NULL AND amount_after IS NULL)",
            name="chk_debt_journal_entries_shape",
        ),
        sa.CheckConstraint(
            _money("amount_before", nullable=True), name="chk_debt_journal_entries_before"
        ),
        sa.CheckConstraint(
            _money("amount_after", nullable=True), name="chk_debt_journal_entries_after"
        ),
        sa.CheckConstraint(
            f"delta <> 0 AND abs(delta) <= {MONEY_COLUMN_MAX} AND delta <> 'NaN'",
            name="chk_debt_journal_entries_delta",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "flush_ordinal",
            "equivalent_id",
            "debtor_id",
            "creditor_id",
            name="uq_debt_journal_entries_op_flush_edge",
        ),
    )
    op.create_index(
        "ix_debt_journal_entries_edge",
        "debt_journal_entries",
        ["equivalent_id", "debtor_id", "creditor_id"],
        unique=False,
    )
    op.create_index(
        "ix_debt_journal_entries_operation",
        "debt_journal_entries",
        ["operation_id"],
        unique=False,
    )

    op.create_table(
        "debt_operation_equivalents",
        sa.Column("operation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("equivalent_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("in_intent", sa.Boolean(), nullable=False),
        sa.Column("in_scope", sa.Boolean(), nullable=False),
        sa.Column("effect_count", sa.Integer(), nullable=False),
        sa.Column("effect_digest", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint(
            "operation_id", "equivalent_id", name="pk_debt_operation_equivalents"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["debt_operations.id"],
            name="fk_debt_operation_equivalents_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["equivalent_id"],
            ["equivalents.id"],
            name="fk_debt_operation_equivalents_equivalent",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("effect_count >= 0", name="chk_debt_operation_equivalents_count"),
        sa.CheckConstraint(
            "length(effect_digest) = 64", name="chk_debt_operation_equivalents_digest"
        ),
        sa.CheckConstraint("effect_count > 0 OR in_intent", name="chk_debt_operation_equivalents_why"),
        sa.CheckConstraint(
            "in_scope OR effect_count = 0", name="chk_debt_operation_equivalents_scope"
        ),
    )


def _rows_in(table: str) -> int | None:
    """How many rows `table` holds, or None when it does not exist on this database.

    Reflected rather than assumed, which is the lesson migration 020 paid for: a database built by
    `create_all` and one built by migrations do not always agree about what is there.
    """

    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table):
        return None
    return int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def downgrade() -> None:
    """Drop the journal, but only while it is empty. See the module docstring."""

    populated = {
        table: count for table in _TABLES if (count := _rows_in(table))
    }
    if populated:
        listed = ", ".join(f"{table}={count}" for table, count in sorted(populated.items()))
        raise RuntimeError(
            f"refusing to drop the debt journal while it holds rows ({listed}). These rows are the "
            f"only record of what the instrumented writers did; dropping them destroys evidence "
            f"and no upgrade can bring it back. If the journal is genuinely to be removed, empty "
            f"it deliberately first - that is a decision somebody makes, not a side effect of a "
            f"downgrade."
        )
    for table in reversed(_TABLES):
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
