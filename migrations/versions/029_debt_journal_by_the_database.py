"""The debt journal is written by the database: triggers, guards, `ordinal`, `schema_version = 2`.

Revision ID: 029_debt_journal_by_the_database
Revises: 028_equivalent_integrity_hold
Create Date: 2026-09-24

Programme 018, stage B (`specs/018-single-debt-writer/spec.md`, `T1803`), slice B1. Until this
migration the listener journal (`app/core/ledger/journal.py`, deleted in the same slice) wrote
`debt_journal_entries` from the ORM's flush. From here on the DATABASE writes them:

* `geo_debts_journal()` on `debts` (`AFTER INSERT OR UPDATE OR DELETE FOR EACH ROW`): refuses with
  SQLSTATE `GE001` unless the transaction's `geo.operation_id` names an `OPEN` envelope, refuses a key
  change with `GE002`, records nothing for an UPDATE that left `amount` alone, and otherwise inserts
  one entry from `OLD`/`NEW` with `ordinal` from `debt_journal_entries_ordinal_seq`.
* `BEFORE TRUNCATE` refusals on `debts` and on the three journal tables.
* Row guards on the three journal tables: an envelope is inserted `OPEN` and may only become
  `COMPLETED` with its declaration unchanged; an entry is inserted only from inside the `debts`
  trigger (`pg_trigger_depth() >= 2`); a membership row only inside its own `OPEN` operation. No
  UPDATE or DELETE of entries or membership, no DELETE of envelopes.
* A deferred constraint trigger that re-reads each new envelope's FINAL state at commit and refuses
  a commit that would leave one `OPEN`.

SCHEMA CHANGES. `debt_journal_entries.flush_ordinal` becomes `ordinal bigint` - historical rows keep
their values, so the order inside an old operation is unchanged; the sequence starts at 1 and only
orders within an operation (gaps allowed, never a count). `debt_operations.flush_count` is dropped
with its part of `chk_debt_operations_completion`: nothing reads it. `schema_version` admits 2, which
every new envelope carries; historical digests are not recomputed.

THE SAME SQL IS DECLARED FOR `Base.metadata.create_all` in `app/db/journal_triggers.py`. It is spelled
here again, not imported, because an applied migration may not change when an application module
does; `tests/integration/test_p018_b_schema_parity_postgres.py` compares both paths by definition and
by behaviour.

THE CUTOVER STOPS THE WRITERS (spec, "Перевод на B — с остановкой"). Old code writes `flush_count` and
sets no context, so there is no mixed operation: stop the server, the simulator and the seeders,
apply this migration, start the new code. It REFUSES while any envelope is `OPEN`.

DOWNGRADE REFUSES WHILE ANY `schema_version = 2` ENVELOPE EXISTS: its history cannot be expressed in
the old schema without loss. Without one, it restores the old shape; `flush_count` of historical
COMPLETED envelopes is reconstructed as the number of distinct ordinals of their entries - a lower
bound of the flushes the listener counted (a retried flush left no entry), which the old CHECK admits
and which nothing reads.

NO `%` IN THE SQL: the metadata copy goes through SQLAlchemy's `DDL`, which applies `%` formatting.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "029_debt_journal_by_the_database"
down_revision = "028_equivalent_integrity_hold"
branch_labels = None
depends_on = None

_COMPLETION = "chk_debt_operations_completion"
_SCHEMA_VERSION = "chk_debt_operations_schema_version"

_COMPLETION_NEW = (
    "("
    " state = 'OPEN'"
    " AND completed_at IS NULL"
    " AND effect_count IS NULL AND effect_digest IS NULL"
    ") OR ("
    " state = 'COMPLETED'"
    " AND completed_at IS NOT NULL"
    " AND effect_count IS NOT NULL AND effect_digest IS NOT NULL"
    " AND length(effect_digest) = 64"
    " AND effect_count >= 0"
    ")"
)

#: Migration 023's predicate, restored by the downgrade.
_COMPLETION_OLD = (
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
    " AND (flush_count > 0 OR effect_count = 0)"
    ")"
)

_CREATE_SEQUENCE = (
    "CREATE SEQUENCE debt_journal_entries_ordinal_seq AS bigint START WITH 1 INCREMENT BY 1 "
    "OWNED BY debt_journal_entries.ordinal"
)

_DEBTS_JOURNAL = r"""CREATE OR REPLACE FUNCTION geo_debts_journal() RETURNS trigger
LANGUAGE plpgsql AS $geo$
DECLARE
    ctx text := nullif(current_setting('geo.operation_id', true), '');
BEGIN
    IF ctx IS NULL
       OR ctx !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'GE001',
            MESSAGE = 'debts: ' || TG_OP || ' outside a debt operation: geo.operation_id is '
                || coalesce(quote_literal(ctx), 'not set') || ' in this transaction',
            HINT = 'every change to debts goes through app.core.ledger.book.Book';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM debt_operations WHERE id = ctx::uuid AND state = 'OPEN'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = 'GE001',
            MESSAGE = 'debts: ' || TG_OP || ' under geo.operation_id ' || ctx
                || ', which names no OPEN debt operation',
            HINT = 'every change to debts goes through app.core.ledger.book.Book';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.debtor_id IS DISTINCT FROM OLD.debtor_id
           OR NEW.creditor_id IS DISTINCT FROM OLD.creditor_id
           OR NEW.equivalent_id IS DISTINCT FROM OLD.equivalent_id THEN
            RAISE EXCEPTION USING
                ERRCODE = 'GE002',
                MESSAGE = 'debts: UPDATE moves debt ' || OLD.id
                    || ' to another edge or equivalent; the key of a debt is immutable';
        END IF;
        IF NEW.amount = OLD.amount THEN
            RETURN NULL;
        END IF;
        INSERT INTO debt_journal_entries (
            id, operation_id, ordinal, equivalent_id, debtor_id, creditor_id,
            effect, amount_before, amount_after, delta
        ) VALUES (
            gen_random_uuid(), ctx::uuid, nextval('debt_journal_entries_ordinal_seq'),
            NEW.equivalent_id, NEW.debtor_id, NEW.creditor_id,
            'U', OLD.amount, NEW.amount, NEW.amount - OLD.amount
        );
    ELSIF TG_OP = 'INSERT' THEN
        INSERT INTO debt_journal_entries (
            id, operation_id, ordinal, equivalent_id, debtor_id, creditor_id,
            effect, amount_before, amount_after, delta
        ) VALUES (
            gen_random_uuid(), ctx::uuid, nextval('debt_journal_entries_ordinal_seq'),
            NEW.equivalent_id, NEW.debtor_id, NEW.creditor_id,
            'I', NULL, NEW.amount, NEW.amount
        );
    ELSE
        INSERT INTO debt_journal_entries (
            id, operation_id, ordinal, equivalent_id, debtor_id, creditor_id,
            effect, amount_before, amount_after, delta
        ) VALUES (
            gen_random_uuid(), ctx::uuid, nextval('debt_journal_entries_ordinal_seq'),
            OLD.equivalent_id, OLD.debtor_id, OLD.creditor_id,
            'D', OLD.amount, NULL, -OLD.amount
        );
    END IF;
    RETURN NULL;
END
$geo$"""

_REFUSE_TRUNCATE = r"""CREATE OR REPLACE FUNCTION geo_journal_refuse_truncate() RETURNS trigger
LANGUAGE plpgsql AS $geo$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23000',
        MESSAGE = TG_TABLE_NAME || ': TRUNCATE is refused; the debt journal and the debts it '
            || 'explains are disposed of with their database, never row by row';
END
$geo$"""

_OPERATIONS_GUARD = r"""CREATE OR REPLACE FUNCTION geo_debt_operations_guard() RETURNS trigger
LANGUAGE plpgsql AS $geo$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state IS DISTINCT FROM 'OPEN' THEN
            RAISE EXCEPTION USING
                ERRCODE = '23000',
                MESSAGE = 'debt_operations: an envelope is inserted OPEN, got state '
                    || coalesce(quote_literal(NEW.state), 'NULL');
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.state = 'OPEN' AND NEW.state = 'COMPLETED'
           AND NEW.id = OLD.id
           AND NEW.kind = OLD.kind
           AND NEW.identity = OLD.identity
           AND NEW.tx_id IS NOT DISTINCT FROM OLD.tx_id
           AND NEW.intent::text = OLD.intent::text
           AND NEW.intent_digest = OLD.intent_digest
           AND NEW.schema_version = OLD.schema_version
           AND NEW.money_encoding_version = OLD.money_encoding_version
           AND NEW.intent_encoding_version = OLD.intent_encoding_version
           AND NEW.opened_at = OLD.opened_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'debt_operations: envelope ' || OLD.id || ' may only go from OPEN to '
                || 'COMPLETED with its declaration unchanged (was ' || OLD.state || ')';
    END IF;
    RAISE EXCEPTION USING
        ERRCODE = '23000',
        MESSAGE = 'debt_operations: DELETE of envelope ' || OLD.id || ' is refused; the journal '
            || 'is evidence';
END
$geo$"""

_ENTRIES_GUARD = r"""CREATE OR REPLACE FUNCTION geo_debt_journal_entries_guard() RETURNS trigger
LANGUAGE plpgsql AS $geo$
BEGIN
    IF TG_OP = 'INSERT' AND pg_trigger_depth() >= 2 THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION USING
        ERRCODE = '23000',
        MESSAGE = 'debt_journal_entries: ' || TG_OP || ' is refused; entries are written only by '
            || 'the debts trigger, from the row the statement changed';
END
$geo$"""

_EQUIVALENTS_GUARD = r"""CREATE OR REPLACE FUNCTION geo_debt_operation_equivalents_guard() RETURNS trigger
LANGUAGE plpgsql AS $geo$
DECLARE
    ctx text := nullif(current_setting('geo.operation_id', true), '');
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'debt_operation_equivalents: ' || TG_OP || ' is refused; the journal is '
                || 'evidence';
    END IF;
    IF ctx IS NULL OR ctx <> NEW.operation_id::text THEN
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'debt_operation_equivalents: a row of operation ' || NEW.operation_id
                || ' is written only inside that operation (geo.operation_id is '
                || coalesce(quote_literal(ctx), 'not set') || ')';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM debt_operations WHERE id = NEW.operation_id AND state = 'OPEN'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'debt_operation_equivalents: operation ' || NEW.operation_id
                || ' is not OPEN';
    END IF;
    RETURN NEW;
END
$geo$"""

_MUST_COMPLETE = r"""CREATE OR REPLACE FUNCTION geo_debt_operation_must_complete() RETURNS trigger
LANGUAGE plpgsql AS $geo$
DECLARE
    final_state text;
BEGIN
    SELECT state INTO final_state FROM debt_operations WHERE id = NEW.id;
    IF FOUND AND final_state = 'OPEN' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'debt_operations: operation ' || NEW.id || ' (' || NEW.kind || ' '
                || NEW.identity || ') is still OPEN at commit; an operation that did not '
                || 'complete cannot be committed';
    END IF;
    RETURN NULL;
END
$geo$"""

#: (function name, CREATE OR REPLACE statement), in creation order.
_FUNCTIONS = (
    ("geo_debts_journal", _DEBTS_JOURNAL),
    ("geo_journal_refuse_truncate", _REFUSE_TRUNCATE),
    ("geo_debt_operations_guard", _OPERATIONS_GUARD),
    ("geo_debt_journal_entries_guard", _ENTRIES_GUARD),
    ("geo_debt_operation_equivalents_guard", _EQUIVALENTS_GUARD),
    ("geo_debt_operation_must_complete", _MUST_COMPLETE),
)

#: (table, trigger name, CREATE TRIGGER statement), in creation order.
_TRIGGERS = (
    (
        "debts",
        "trg_debts_journal",
        "CREATE TRIGGER trg_debts_journal AFTER INSERT OR UPDATE OR DELETE ON debts "
        "FOR EACH ROW EXECUTE FUNCTION geo_debts_journal()",
    ),
    (
        "debts",
        "trg_debts_refuse_truncate",
        "CREATE TRIGGER trg_debts_refuse_truncate BEFORE TRUNCATE ON debts "
        "FOR EACH STATEMENT EXECUTE FUNCTION geo_journal_refuse_truncate()",
    ),
    (
        "debt_operations",
        "trg_debt_operations_guard",
        "CREATE TRIGGER trg_debt_operations_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON debt_operations FOR EACH ROW EXECUTE FUNCTION geo_debt_operations_guard()",
    ),
    (
        "debt_operations",
        "trg_debt_operations_refuse_truncate",
        "CREATE TRIGGER trg_debt_operations_refuse_truncate BEFORE TRUNCATE ON debt_operations "
        "FOR EACH STATEMENT EXECUTE FUNCTION geo_journal_refuse_truncate()",
    ),
    (
        "debt_operations",
        "trg_debt_operations_complete_at_commit",
        "CREATE CONSTRAINT TRIGGER trg_debt_operations_complete_at_commit AFTER INSERT "
        "ON debt_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION geo_debt_operation_must_complete()",
    ),
    (
        "debt_journal_entries",
        "trg_debt_journal_entries_guard",
        "CREATE TRIGGER trg_debt_journal_entries_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON debt_journal_entries FOR EACH ROW EXECUTE FUNCTION geo_debt_journal_entries_guard()",
    ),
    (
        "debt_journal_entries",
        "trg_debt_journal_entries_refuse_truncate",
        "CREATE TRIGGER trg_debt_journal_entries_refuse_truncate BEFORE TRUNCATE "
        "ON debt_journal_entries FOR EACH STATEMENT EXECUTE FUNCTION geo_journal_refuse_truncate()",
    ),
    (
        "debt_operation_equivalents",
        "trg_debt_operation_equivalents_guard",
        "CREATE TRIGGER trg_debt_operation_equivalents_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON debt_operation_equivalents FOR EACH ROW "
        "EXECUTE FUNCTION geo_debt_operation_equivalents_guard()",
    ),
    (
        "debt_operation_equivalents",
        "trg_debt_operation_equivalents_refuse_truncate",
        "CREATE TRIGGER trg_debt_operation_equivalents_refuse_truncate BEFORE TRUNCATE "
        "ON debt_operation_equivalents FOR EACH STATEMENT "
        "EXECUTE FUNCTION geo_journal_refuse_truncate()",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    still_open = int(
        bind.execute(
            sa.text("SELECT count(*) FROM debt_operations WHERE state = 'OPEN'")
        ).scalar_one()
    )
    if still_open:
        raise RuntimeError(
            f"refusing to migrate the debt journal while {still_open} envelope(s) are OPEN. Stop "
            f"every writer (server, simulator, seeders) and let their transactions end: an OPEN "
            f"envelope is an operation in flight that the old code would complete with flush_count."
        )

    # debt_operations: flush_count goes, the completion CHECK loses its clauses, version 2 is admitted.
    op.drop_constraint(_COMPLETION, "debt_operations", type_="check")
    op.drop_column("debt_operations", "flush_count")
    op.create_check_constraint(_COMPLETION, "debt_operations", _COMPLETION_NEW)
    op.drop_constraint(_SCHEMA_VERSION, "debt_operations", type_="check")
    op.create_check_constraint(_SCHEMA_VERSION, "debt_operations", "schema_version IN (1, 2)")

    # debt_journal_entries: flush_ordinal -> ordinal bigint. The CHECK `chk_debt_journal_entries_ordinal`
    # follows the rename by itself; the unique constraint (and its index) is renamed with the column.
    op.alter_column("debt_journal_entries", "flush_ordinal", new_column_name="ordinal")
    op.alter_column(
        "debt_journal_entries",
        "ordinal",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.execute(
        "ALTER TABLE debt_journal_entries RENAME CONSTRAINT "
        "uq_debt_journal_entries_op_flush_edge TO uq_debt_journal_entries_op_ordinal_edge"
    )
    op.execute(_CREATE_SEQUENCE)

    for _name, statement in _FUNCTIONS:
        op.execute(statement)
    for _table, _trigger, statement in _TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    newer = int(
        bind.execute(
            sa.text("SELECT count(*) FROM debt_operations WHERE schema_version = 2")
        ).scalar_one()
    )
    if newer:
        raise RuntimeError(
            f"refusing to downgrade the debt journal while {newer} envelope(s) carry schema_version "
            f"2. They were written by the database trigger, and the old schema cannot express their "
            f"history (ordinal is not a flush number, flush_count does not exist) without loss."
        )

    for table, trigger, _statement in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for name, _statement in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    op.execute("DROP SEQUENCE IF EXISTS debt_journal_entries_ordinal_seq")

    op.execute(
        "ALTER TABLE debt_journal_entries RENAME CONSTRAINT "
        "uq_debt_journal_entries_op_ordinal_edge TO uq_debt_journal_entries_op_flush_edge"
    )
    op.alter_column(
        "debt_journal_entries",
        "ordinal",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column("debt_journal_entries", "ordinal", new_column_name="flush_ordinal")

    op.drop_constraint(_SCHEMA_VERSION, "debt_operations", type_="check")
    op.create_check_constraint(_SCHEMA_VERSION, "debt_operations", "schema_version IN (1)")
    op.drop_constraint(_COMPLETION, "debt_operations", type_="check")
    op.add_column("debt_operations", sa.Column("flush_count", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE debt_operations AS o SET flush_count = ("
        " SELECT count(DISTINCT e.flush_ordinal) FROM debt_journal_entries AS e"
        " WHERE e.operation_id = o.id"
        ") WHERE o.state = 'COMPLETED'"
    )
    op.create_check_constraint(_COMPLETION, "debt_operations", _COMPLETION_OLD)
