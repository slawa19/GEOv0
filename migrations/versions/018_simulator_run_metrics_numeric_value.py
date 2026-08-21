"""Archive float simulator metrics and switch the column to Numeric(20, 8).

Revision ID: 018_simulator_run_metrics_numeric_value
Revises: 017_add_owner_to_simulator_runs
Create Date: 2026-08-20

Spec 007 / T715 (finding B-D1-002, owner decision 2026-08-20).

`simulator_run_metrics.value` carries the money series `total_debt` and
`clearing_volume`, which the domain model declares as "amount in the selected
equivalent". Money must be exact decimal (AGENTS.md §8), so the column becomes
`Numeric(20, 8)` — the same precision/scale the `debts.amount` and
`trust_lines.limit_amount` columns already use.

**Existing rows are archived, not converted.** The precision of an already
written binary `float` is irrecoverably lost; re-reading it as `Decimal` would
present a lossy number as a restored exact amount. So the migration:

1. creates `simulator_run_metrics_float_archive` — the same identifying keys
   plus the original value rendered **as text**, so the move reinterprets
   nothing;
2. copies every existing row into it with `extra_float_digits` pinned to 3 for
   the migration transaction, and then **verifies** that every archived string
   casts back to the identical `float8`. Without the pin the text form of a
   `double precision` value is whatever the session GUC says: under
   `extra_float_digits = 0` (the PostgreSQL default before v12, and a common
   pooler setting) `0.30000000000000004` renders as `0.3` and
   `12345678901.123457` as `12345678901.1235`. Step 3 deletes the only other
   copy in the same transaction, so an unpinned cast would destroy those
   digits for good — inside the migration whose whole point is refusing to
   launder inexact money. The verification is a gate, not a log line: if one
   row fails to round-trip the migration aborts and the transaction rolls
   back with the live rows still in place;
3. empties the live table;
4. changes the column type.

Steps 2-4 are correct only as a unit, so `upgrade()` takes
`LOCK TABLE simulator_run_metrics IN ACCESS EXCLUSIVE MODE` before step 1 and
holds it to the end: **this migration deliberately serialises with writers**. A
row committed between the copy and the DELETE would be deleted without ever
reaching the archive, and a row committed between the gate and the ALTER would
be converted from float to numeric, i.e. handed the exact appearance it never
had. `lock_timeout` is pinned to `LOCK_TIMEOUT_MS`, so a busy table makes the
migration fail with a clear lock-timeout error instead of blocking a deployment
indefinitely; rerun it in a quiet window.

What the archive guarantees is therefore precise: `value_text` is a decimal
string that casts back to **exactly** the `double precision` value that was
stored. It is not a claim that the number is an exact amount — it never was.

An empty live table after the upgrade is the intended outcome: simulator run
metrics are disposable by construction (`scripts/cleanup_simulator_runs.py`
already deletes them for inactive runs).

Downgrade is honest and deliberately partial: it restores the column **type**
only. It does **not** move archived rows back into the live table, because the
archived text is exactly the lossy history this revision refused to launder.
If those rows are needed, restore them by hand from
`simulator_run_metrics_float_archive` before or after downgrading, deciding
explicitly how each text value should be interpreted. Downgrade also **keeps**
the archive table: it is the only remaining copy of that history, and a schema
rollback must not destroy data. Re-running the upgrade tolerates the existing
archive table.

PostgreSQL only, like every revision in this repository (`migrations/env.py`
refuses any other backend before a revision runs).
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "018_simulator_run_metrics_numeric_value"
down_revision = "017_add_owner_to_simulator_runs"
branch_labels = None
depends_on = None


ARCHIVE_TABLE = "simulator_run_metrics_float_archive"

# How long the migration is willing to wait for the exclusive lock before giving
# up. Failing fast with a clear error beats blocking a deployment behind an
# arbitrarily long-running writer; rerun the migration in a quiet window.
LOCK_TIMEOUT_MS = 5000


def _archive_table_exists(bind) -> bool:
    return sa.inspect(bind).has_table(ARCHIVE_TABLE)


def upgrade() -> None:
    bind = op.get_bind()

    # Step 0: serialise with concurrent writers, deliberately, for the whole
    # critical section. The archive copy, the round-trip gate, the DELETE and
    # the ALTER are only correct together: a row committed between the copy and
    # the DELETE would be deleted without ever being archived, and a row
    # committed between the gate and the ALTER would be converted from float to
    # numeric - handed the exact appearance it never had, which is the very
    # laundering this revision exists to prevent.
    #
    # This costs nothing extra: the ALTER COLUMN below takes ACCESS EXCLUSIVE
    # anyway. The lock is simply taken earlier and held across the whole window.
    op.execute(sa.text(f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MS}"))
    op.execute(sa.text("LOCK TABLE simulator_run_metrics IN ACCESS EXCLUSIVE MODE"))

    # Step 1: archive table. Append-only with a surrogate key, so a re-run after
    # a downgrade (which keeps the archive) cannot collide on the natural key.
    if not _archive_table_exists(bind):
        op.create_table(
            ARCHIVE_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("equivalent_code", sa.String(length=50), nullable=False),
            sa.Column("key", sa.String(length=50), nullable=False),
            sa.Column("t_ms", sa.Integer(), nullable=False),
            # Text, not a number: the original float is preserved verbatim and is
            # never reinterpreted as an exact amount by this migration.
            sa.Column("value_text", sa.String(length=64), nullable=True),
            sa.Column(
                "archived_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_simulator_run_metrics_float_archive_run_key",
            ARCHIVE_TABLE,
            ["run_id", "key"],
        )

    # Step 2: move every existing row across. NULL ("not measured") stays NULL —
    # it must not become the string "None" or a zero.
    #
    # extra_float_digits is pinned for this transaction so the text form is
    # round-trippable whatever the server or the pooler configured; see the
    # module docstring for the digits that disappear without the pin.
    op.execute(sa.text("SET LOCAL extra_float_digits = 3"))
    op.execute(
        sa.text(
            f"INSERT INTO {ARCHIVE_TABLE} "
            "(run_id, equivalent_code, key, t_ms, value_text) "
            "SELECT run_id, equivalent_code, key, t_ms, CAST(value AS TEXT) "
            "FROM simulator_run_metrics"
        )
    )

    # Step 2b: a gate, not a log line. Every non-NULL live value must have an
    # archived string that casts back to the identical float8. If any does not,
    # abort while the live rows are still there; the transaction rolls back.
    not_round_tripped = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM simulator_run_metrics m "
                "WHERE m.value IS NOT NULL AND NOT EXISTS ("
                f"  SELECT 1 FROM {ARCHIVE_TABLE} a "
                "   WHERE a.run_id = m.run_id "
                "     AND a.equivalent_code = m.equivalent_code "
                "     AND a.key = m.key "
                "     AND a.t_ms = m.t_ms "
                "     AND a.value_text IS NOT NULL "
                "     AND a.value_text::float8 = m.value)"
            )
        ).scalar_one()
    )
    if not_round_tripped != 0:
        raise RuntimeError(
            "018_simulator_run_metrics_numeric_value: "
            f"{not_round_tripped} simulator_run_metrics row(s) did not survive "
            "archiving as round-trippable text. Refusing to delete the live "
            "rows. Check extra_float_digits on the connection and retry."
        )

    # Step 3: empty the live table. Everything in it is now in the archive.
    op.execute(sa.text("DELETE FROM simulator_run_metrics"))

    # Step 4: change the column type. The table is empty, so USING converts
    # nothing; it is spelled out because PostgreSQL requires a cast expression
    # for float8 -> numeric regardless of row count.
    op.alter_column(
        "simulator_run_metrics",
        "value",
        existing_type=sa.Float(),
        type_=sa.Numeric(precision=20, scale=8),
        existing_nullable=True,
        postgresql_using="value::numeric(20,8)",
    )


def downgrade() -> None:
    # The ALTER below takes ACCESS EXCLUSIVE by itself; the timeout only keeps a
    # rollback from blocking indefinitely behind a writer.
    op.execute(sa.text(f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MS}"))

    # Type only. Archived rows are NOT restored (see the module docstring), and
    # the archive table is intentionally left in place.
    op.alter_column(
        "simulator_run_metrics",
        "value",
        existing_type=sa.Numeric(precision=20, scale=8),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="value::double precision",
    )
