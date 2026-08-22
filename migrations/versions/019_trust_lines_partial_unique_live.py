"""Make trust-line uniqueness match the protocol: unique among live rows only.

Revision ID: 019_trust_lines_partial_unique_live
Revises: 018_simulator_run_metrics_numeric_value
Create Date: 2026-08-21

Spec 009 / T903 (findings F-009-3 = B-A3-004, F-009-4 = B-A1a-016).

WHAT WAS WRONG.  The constraint was stricter than the protocol.

* `docs/ru/02-protocol-spec.md:333` states the precondition of TRUST_LINE_CREATE as
  «Не существует **активной** линии `(from, to, equivalent)`» — a *closed* incarnation
  therefore must not block a new one.
* `docs/ru/02-protocol-spec.md:379` states that TRUST_LINE_CLOSE *sets* `status='closed'`
  and keeps the row, so history survives.
* But `uq_trust_lines_from_to_equivalent` was declared **without** `status` (model and
  `001_initial_schema.py:103-108`, mirroring `docs/ru/03-architecture.md:500`), so the
  database refused the very row the protocol permits.

The observable effect was HTTP 500 on two ordinary calls by one user: create → close →
create surfaced an unhandled `IntegrityError` out of the service flush and out of the
Interact Mode route.  Reproduced on PostgreSQL 16 before this migration.

WHAT THIS MIGRATION DOES.  Replaces the unconditional unique constraint with a **partial**
unique index over live statuses only (`status <> 'closed'`).  Consequences:

* at most one live (`active` or `frozen`) line per `(from, to, equivalent)` — the money
  path is unchanged, since routing, clearing and invariants all read active lines only;
* any number of historical `closed` rows may coexist, each with its own `id`, so audit
  payloads that reference `trustline_id` keep their referent;
* a concurrent double create still raises `IntegrityError`, which the callers now map to a
  declared 409 instead of letting it escape.

Both PostgreSQL and SQLite support partial indexes, so the same shape is used on both.

DOWNGRADE IS CONDITIONAL AND FAIL-CLOSED.  Restoring the unconditional constraint is only
possible while no triple has more than one row.  If a second incarnation already exists,
the downgrade raises instead of destroying history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "019_trust_lines_partial_unique_live"
down_revision = "018_simulator_run_metrics_numeric_value"
branch_labels = None
depends_on = None

_LIVE_INDEX = "uq_trust_lines_live_from_to_equivalent"
_OLD_CONSTRAINT = "uq_trust_lines_from_to_equivalent"
_COLUMNS = ("from_participant_id", "to_participant_id", "equivalent_id")


def _dialect() -> str:
    return op.get_bind().dialect.name


def _existing_names(bind) -> tuple[set[str], set[str]]:
    """Actual unique-constraint and index names on `trust_lines`, whatever created them."""
    inspector = sa.inspect(bind)
    constraints = {
        c.get("name")
        for c in inspector.get_unique_constraints("trust_lines")
        if c.get("name")
    }
    indexes = {i.get("name") for i in inspector.get_indexes("trust_lines") if i.get("name")}
    return constraints, indexes


def upgrade() -> None:
    bind = op.get_bind()
    dialect = _dialect()
    constraints, indexes = _existing_names(bind)

    if dialect in {"postgresql", "postgres"}:
        # The old uniqueness may be materialised as a table constraint or as a standalone
        # unique index depending on how the schema was created; both forms are dropped
        # idempotently.
        op.execute(f'ALTER TABLE trust_lines DROP CONSTRAINT IF EXISTS "{_OLD_CONSTRAINT}"')
        op.execute(f'DROP INDEX IF EXISTS "{_OLD_CONSTRAINT}"')
        op.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "{_LIVE_INDEX}" '
            f'ON trust_lines ({", ".join(_COLUMNS)}) '
            "WHERE status <> 'closed'"
        )
        return

    # SQLite and anything else.
    #
    # A table-level constraint can only be removed by recreating the table, which is what
    # batch mode does.  But batch mode raises `ValueError: No such constraint` *on exit*
    # when the reflected table does not carry that name — and that exception escapes any
    # try/except placed around `drop_constraint` itself.  So the batch step runs ONLY when
    # the constraint is actually there.  A schema built by `Base.metadata.create_all` from
    # the current model has nothing to drop, and that is a normal, expected state.
    if _OLD_CONSTRAINT in constraints:
        with op.batch_alter_table("trust_lines") as batch:
            batch.drop_constraint(_OLD_CONSTRAINT, type_="unique")
    elif _OLD_CONSTRAINT in indexes:
        op.execute(f'DROP INDEX IF EXISTS "{_OLD_CONSTRAINT}"')

    # Re-read: batch mode recreates the table, so index names must be checked again.
    _, indexes_after = _existing_names(bind)
    if _LIVE_INDEX not in indexes_after:
        op.execute(
            f'CREATE UNIQUE INDEX "{_LIVE_INDEX}" '
            f'ON trust_lines ({", ".join(_COLUMNS)}) '
            "WHERE status <> 'closed'"
        )


def downgrade() -> None:
    bind = op.get_bind()

    duplicates = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "  SELECT 1 FROM trust_lines"
            "  GROUP BY from_participant_id, to_participant_id, equivalent_id"
            "  HAVING COUNT(*) > 1"
            ") AS d"
        )
    ).scalar()

    if int(duplicates or 0) > 0:
        raise RuntimeError(
            "Cannot restore the unconditional unique constraint: "
            f"{duplicates} triple(s) already hold more than one trust-line incarnation. "
            "Downgrading would require deleting historical closed rows, which this "
            "migration refuses to do."
        )

    dialect = _dialect()
    op.execute(f'DROP INDEX IF EXISTS "{_LIVE_INDEX}"')

    if dialect in {"postgresql", "postgres"}:
        op.execute(
            f'ALTER TABLE trust_lines ADD CONSTRAINT "{_OLD_CONSTRAINT}" '
            f'UNIQUE ({", ".join(_COLUMNS)})'
        )
        return

    with op.batch_alter_table("trust_lines") as batch:
        batch.create_unique_constraint(_OLD_CONSTRAINT, list(_COLUMNS))
