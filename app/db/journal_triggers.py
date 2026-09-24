"""The debt journal's database half: the sequence, the functions and the triggers (018 stage B).

Programme 018, stage B (`specs/018-single-debt-writer/spec.md`, "Стадия B"). From here on the
DATABASE writes `debt_journal_entries`, from `OLD`/`NEW`, in the same statement that changes a row of
`debts`; the listener journal (`app/core/ledger/journal.py`) is gone. What this module declares:

* `geo_debts_journal()` - `AFTER INSERT OR UPDATE OR DELETE ON debts FOR EACH ROW`. Refuses with
  SQLSTATE `GE001` unless the transaction's `geo.operation_id` (set with `set_config(..., true)` by
  `app/core/ledger/book.py`) names an existing `OPEN` envelope; refuses a key change with `GE002`;
  writes nothing for an UPDATE that did not change `amount`; otherwise inserts one entry.
* `geo_journal_refuse_truncate()` - `BEFORE TRUNCATE ... FOR EACH STATEMENT` on `debts` and on the
  three journal tables: a row trigger never sees a TRUNCATE.
* `geo_debt_operations_guard()`, `geo_debt_journal_entries_guard()`,
  `geo_debt_operation_equivalents_guard()` - `BEFORE ... FOR EACH ROW` guards of the three journal
  tables (spec table "Охрана трёх журнальных таблиц"). An entry is admitted only at
  `pg_trigger_depth() >= 2`, i.e. from inside the `debts` trigger: a direct INSERT runs the guard at
  depth 1 (spec "Как отличается вставка записи журнала от прямой").
* `geo_debt_operation_must_complete()` - the deferred constraint trigger on `debt_operations` that
  re-reads the envelope's FINAL stored state at commit: `OPEN` refuses the commit.

WHAT IT IS NOT (spec, "Что даёт триггер — узко"): not a trust boundary. Code with the same role can
set the context itself, and the table owner can disable the triggers. It is record-and-refuse for
un-instrumented DML of the supported writers.

TWO COPIES, ON PURPOSE. Migration `029_debt_journal_by_the_database` spells the same SQL:
an applied migration must not change when this module does, so it cannot import it. The two
construction paths - `alembic upgrade head` and `Base.metadata.create_all` (mode A of the test
fixtures) - are compared by DEFINITION and by BEHAVIOUR in
`tests/integration/test_p018_b_schema_parity_postgres.py`; an edit to one copy only reddens it.

NO `%` ANYWHERE in the SQL below: SQLAlchemy's `DDL` applies Python `%` formatting to its text.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from app.db.journal_tables import (
    debt_journal_entries,
    debt_operation_equivalents,
    debt_operations,
)
from app.db.models.debt import Debt

__all__ = [
    "GUC_OPERATION_ID",
    "SQLSTATE_GUARD",
    "SQLSTATE_KEY_CHANGED",
    "SQLSTATE_NO_OPEN_OPERATION",
    "JOURNAL_SEQUENCE",
    "JOURNAL_FUNCTIONS",
    "JOURNAL_TRIGGERS",
]

#: The transaction-local setting that names the operation a statement belongs to.
GUC_OPERATION_ID = "geo.operation_id"

#: A write to `debts` with no OPEN operation named by `geo.operation_id` in this transaction.
SQLSTATE_NO_OPEN_OPERATION = "GE001"
#: An UPDATE of `debts` that moves the row to another edge or equivalent.
SQLSTATE_KEY_CHANGED = "GE002"
#: Every other refusal of this module (journal-table guards, TRUNCATE, the deferred completion
#: check): a standard integrity-constraint class. The spec names two own SQLSTATEs and no more.
SQLSTATE_GUARD = "23000"

JOURNAL_SEQUENCE = "debt_journal_entries_ordinal_seq"

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

#: Function name -> its CREATE OR REPLACE statement.
JOURNAL_FUNCTIONS: dict[str, str] = {
    "geo_debts_journal": _DEBTS_JOURNAL,
    "geo_journal_refuse_truncate": _REFUSE_TRUNCATE,
    "geo_debt_operations_guard": _OPERATIONS_GUARD,
    "geo_debt_journal_entries_guard": _ENTRIES_GUARD,
    "geo_debt_operation_equivalents_guard": _EQUIVALENTS_GUARD,
    "geo_debt_operation_must_complete": _MUST_COMPLETE,
}

#: (table, trigger name, CREATE TRIGGER statement), in creation order.
JOURNAL_TRIGGERS: tuple[tuple[str, str, str], ...] = (
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

#: Which functions each table's triggers call; created (OR REPLACE) with the table.
_FUNCTIONS_OF_TABLE = {
    "debts": ("geo_debts_journal", "geo_journal_refuse_truncate"),
    "debt_operations": (
        "geo_debt_operations_guard",
        "geo_journal_refuse_truncate",
        "geo_debt_operation_must_complete",
    ),
    "debt_journal_entries": ("geo_debt_journal_entries_guard", "geo_journal_refuse_truncate"),
    "debt_operation_equivalents": (
        "geo_debt_operation_equivalents_guard",
        "geo_journal_refuse_truncate",
    ),
}


def _install_metadata_ddl() -> None:
    """Attach the SQL above to each table's `after_create`, for the `create_all` path.

    PER TABLE, so a table created on its own still gets its triggers. The functions are `CREATE OR
    REPLACE` because `drop_all` drops tables (and with them their triggers and the owned sequence)
    but not functions, and the next `create_all` must not fail on a function left standing.
    """

    tables = {
        "debts": Debt.__table__,
        "debt_operations": debt_operations,
        "debt_journal_entries": debt_journal_entries,
        "debt_operation_equivalents": debt_operation_equivalents,
    }
    for name, table in tables.items():
        if name == "debt_journal_entries":
            event.listen(table, "after_create", DDL(_CREATE_SEQUENCE))
        for function in _FUNCTIONS_OF_TABLE[name]:
            event.listen(table, "after_create", DDL(JOURNAL_FUNCTIONS[function]))
        for trigger_table, _trigger, statement in JOURNAL_TRIGGERS:
            if trigger_table == name:
                event.listen(table, "after_create", DDL(statement))


_install_metadata_ddl()
