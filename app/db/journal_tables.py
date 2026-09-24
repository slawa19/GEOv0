"""The debt journal's three tables: unmapped Core `Table`s, on purpose.

Programme 015, phase B step 4 (design v2 §5). These hold the operation envelope (what a writer
declared it was about to do), the per-edge signed effects of every flush it made, and the
per-equivalent summary written once at completion.

WHY THEY ARE NOT ORM MODELS, and it is enforcement rather than a style choice. A mapped class is
reachable from `session.add`, `session.merge`, `bulk_save_objects`, `bulk_insert_mappings`,
`bulk_update_mappings` and a cascade - five write paths whose refusal would then have to be built
and kept in step. A Core `Table` that no mapper mentions has none of them: the ORM cannot address
these tables at all, so the only way in is Core DML, and Core DML is what
`app/core/ledger/journal.py`'s write guard inspects. The guard's own test fails if any mapper ever
maps one of these tables.

They still live on `Base.metadata`, because the SQLite tiers build their schema with
`Base.metadata.create_all` and a table outside the metadata would simply not exist there.

MONEY COLUMNS ARE `MoneyNumeric`, NOT `Numeric` (T1526, measured 2026-09-12). The CHECK constraints
below exclude `NaN` through the MAGNITUDE clause - `abs('NaN') <= 1e12` is false on PostgreSQL -
and that reasoning does not reach SQLite at all: a bound `NaN` arrives there as SQL `NULL`, so it
never meets the CHECK and would be refused, if at all, by a `NOT NULL` that names a different
problem. `MoneyNumeric` refuses the bind on every dialect, before the statement is sent. See
`app/db/types.py`.

NO CHECKSUM CHAIN. `head_hash`, `prev_hash`, `hash` and `algorithm_version` are deliberately absent:
the chain is deferred by owner decision of 2026-09-12 until an external trusted anchor exists
(design v2 §5). Nothing here is tamper-evident and nothing downstream may say that it is.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
    text,
)

from app.db.base import Base
from app.db.types import MONEY_COLUMN_MAX, MoneyNumeric, finite_money_clauses

__all__ = [
    "DEBT_JOURNAL_TABLE_NAMES",
    "INTENT_ENCODING_VERSION",
    "MONEY_ENCODING_VERSION",
    "OPERATION_KINDS",
    "OPERATION_KINDS_WITH_TX",
    "PAYMENT_INTENT_ENCODING_VERSION",
    "SCHEMA_VERSION",
    "STORABLE_INTENT_ENCODING_VERSIONS",
    "debt_journal_entries",
    "debt_operation_equivalents",
    "debt_operations",
    "intent_encoding_version_for",
]

#: The kinds of writer that may own a debt operation. `INTEGRITY_REPAIR` is deliberately not here:
#: repairs are not instrumented in step 4 and are blocked by construction (design v2 §2).
OPERATION_KINDS = ("PAYMENT", "CLEARING", "INJECT", "SEED", "TEST_FIXTURE")

#: The kinds that own a `transactions.tx_id`, and the only ones allowed to carry one.
OPERATION_KINDS_WITH_TX = ("PAYMENT", "CLEARING")

#: Bumped when the meaning of a stored row changes. Three separate versions rather than one,
#: because a change to how money is encoded and a change to how intent is encoded are read back by
#: different code and can move independently.
SCHEMA_VERSION = 1
MONEY_ENCODING_VERSION = 1
INTENT_ENCODING_VERSION = 1

#: Step 5b (spec 015, "Ключевое ревью шага 5"): a PAYMENT intent also records the pre-state of both
#: directions of every flow pair, which is a change to how the intent is encoded and to nothing else.
#: Only this one kind moved; every other kind still writes `INTENT_ENCODING_VERSION`. Version 1
#: payments written before stay as they are and are read structurally (`reconciliation.py`).
PAYMENT_INTENT_ENCODING_VERSION = 2

#: Every intent encoding version the database admits (migration 027 widened the CHECK to these).
STORABLE_INTENT_ENCODING_VERSIONS = (INTENT_ENCODING_VERSION, PAYMENT_INTENT_ENCODING_VERSION)


def intent_encoding_version_for(kind: str) -> int:
    """The intent encoding version an operation of `kind` writes today."""

    return PAYMENT_INTENT_ENCODING_VERSION if kind == "PAYMENT" else INTENT_ENCODING_VERSION

_KIND_LIST = ", ".join("'%s'" % kind for kind in OPERATION_KINDS)
_TX_KIND_LIST = ", ".join("'%s'" % kind for kind in OPERATION_KINDS_WITH_TX)


def _money(column: str, *, nullable: bool) -> str:
    """The money predicate for one journal column: sign, magnitude, and not-a-number.

    The magnitude clause is LOAD-BEARING and not decoration: it is what excludes `NaN` on
    PostgreSQL, where `'NaN' > 0` is true and every upper bound is false. Do not "simplify" this
    into a positivity test (design v2 §5, `app/db/types.py::finite_money_clauses`).
    """

    predicate = column + " > 0 AND " + finite_money_clauses(column)
    if nullable:
        return "(" + column + " IS NULL OR (" + predicate + "))"
    return "(" + predicate + ")"


debt_operations = Table(
    "debt_operations",
    Base.metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("kind", String(32), nullable=False),
    Column("identity", String(256), nullable=False),
    # RESTRICT: a transaction row may not be deleted out from under the operation that describes
    # it. The journal is evidence, and deleting evidence silently is the failure T1524 closed at
    # the debts -> equivalents foreign key.
    Column(
        "tx_id",
        String(64),
        ForeignKey("transactions.tx_id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("intent", JSON, nullable=False),
    Column("intent_digest", String(64), nullable=False),
    Column("schema_version", SmallInteger, nullable=False, default=SCHEMA_VERSION),
    Column("money_encoding_version", SmallInteger, nullable=False, default=MONEY_ENCODING_VERSION),
    Column("intent_encoding_version", SmallInteger, nullable=False, default=INTENT_ENCODING_VERSION),
    Column("opened_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("state", String(16), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("flush_count", Integer, nullable=True),
    Column("effect_count", Integer, nullable=True),
    Column("effect_digest", String(64), nullable=True),
    CheckConstraint("kind IN (" + _KIND_LIST + ")", name="chk_debt_operations_kind"),
    CheckConstraint("length(identity) > 0", name="chk_debt_operations_identity_present"),
    CheckConstraint("length(intent_digest) = 64", name="chk_debt_operations_intent_digest"),
    CheckConstraint("schema_version IN (1)", name="chk_debt_operations_schema_version"),
    CheckConstraint("money_encoding_version IN (1)", name="chk_debt_operations_money_version"),
    # Widened by migration 027 (step 5b) for the version-2 PAYMENT intent; the other two stay `IN (1)`.
    CheckConstraint(
        "intent_encoding_version IN ("
        + ", ".join(str(version) for version in STORABLE_INTENT_ENCODING_VERSIONS)
        + ")",
        name="chk_debt_operations_intent_version",
    ),
    CheckConstraint("state IN ('OPEN', 'COMPLETED')", name="chk_debt_operations_state"),
    # A tx_id exactly when the kind owns one. Written as an equality of two truth values so that
    # neither direction can be forgotten: a PAYMENT without a tx_id and a SEED carrying one are
    # the same defect seen from two sides.
    CheckConstraint(
        "(tx_id IS NOT NULL) = (kind IN (" + _TX_KIND_LIST + "))",
        name="chk_debt_operations_tx_id_iff_kind",
    ),
    # The completion columns move together or not at all. An envelope that says COMPLETED while
    # its counts are NULL is a half-written completion, and a reader cannot tell it from a
    # finished one.
    #
    # THE COUNTS ARE NOT BOUNDED BY EACH OTHER, and the clause that said they were was removed by
    # migration 023 (measured when the journal was armed, 2026-09-12). `flush_count <= effect_count`
    # and the all-or-nothing zero pair both assume every flush leaves an entry. A `StaleDataError`
    # retry in `PaymentEngine._apply_flow` breaks that on the main payment path: the losing attempt
    # flushed, so it was counted, and its entries went back with its savepoint. What is true is only
    # the one-way implication - an entry needs a flush that wrote it - and that is what is written
    # here now.
    CheckConstraint(
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
        ")",
        name="chk_debt_operations_completion",
    ),
    UniqueConstraint("kind", "identity", name="uq_debt_operations_kind_identity"),
    UniqueConstraint("tx_id", name="uq_debt_operations_tx_id"),
)

#: Open envelopes are the only ones a running transaction looks for, and they are a vanishing
#: fraction of the table once the journal has any history.
Index(
    "ix_debt_operations_open",
    debt_operations.c.kind,
    debt_operations.c.identity,
    postgresql_where=text("state = 'OPEN'"),
)


debt_journal_entries = Table(
    "debt_journal_entries",
    Base.metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column(
        "operation_id",
        Uuid(as_uuid=True),
        ForeignKey("debt_operations.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("flush_ordinal", Integer, nullable=False),
    Column(
        "equivalent_id",
        Uuid(as_uuid=True),
        ForeignKey("equivalents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "debtor_id",
        Uuid(as_uuid=True),
        ForeignKey("participants.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "creditor_id",
        Uuid(as_uuid=True),
        ForeignKey("participants.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("effect", String(1), nullable=False),
    Column("amount_before", MoneyNumeric(20, 8), nullable=True),
    Column("amount_after", MoneyNumeric(20, 8), nullable=True),
    Column("delta", MoneyNumeric(20, 8), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("flush_ordinal >= 1", name="chk_debt_journal_entries_ordinal"),
    CheckConstraint("debtor_id <> creditor_id", name="chk_debt_journal_entries_no_self_loop"),
    CheckConstraint("effect IN ('I', 'U', 'D')", name="chk_debt_journal_entries_effect"),
    # The three effects have three shapes, and the shape is what makes a row readable without the
    # debt it describes: an insert has no before, a delete has no after, and an update that
    # changed nothing is not an effect at all.
    CheckConstraint(
        "(effect = 'I' AND amount_before IS NULL AND amount_after IS NOT NULL)"
        " OR (effect = 'U' AND amount_before IS NOT NULL AND amount_after IS NOT NULL"
        "     AND amount_before <> amount_after)"
        " OR (effect = 'D' AND amount_before IS NOT NULL AND amount_after IS NULL)",
        name="chk_debt_journal_entries_shape",
    ),
    CheckConstraint(
        _money("amount_before", nullable=True), name="chk_debt_journal_entries_before"
    ),
    CheckConstraint(
        _money("amount_after", nullable=True), name="chk_debt_journal_entries_after"
    ),
    # `delta` is the only signed money column, so it gets its own predicate rather than `_money`:
    # a delta of zero is not an effect, and the magnitude bound is again what excludes NaN.
    CheckConstraint(
        "delta <> 0 AND abs(delta) <= " + MONEY_COLUMN_MAX + " AND delta <> 'NaN'",
        name="chk_debt_journal_entries_delta",
    ),
    # AND `delta` IS THE ARITHMETIC IT CLAIMS TO BE (T1530, measured 2026-09-13). The predicate
    # above bounds the magnitude and excludes zero and NaN; it says NOTHING about whether the number
    # is `amount_after - amount_before`, so an entry reading `10 -> 11, delta 2` satisfied every
    # constraint this table had. That entry is not a wrong amount - it is a row that contradicts
    # itself, and criterion (a) of design v2 (the per-edge deltas equal the edge's final minus its
    # initial) is false from the moment one exists. COALESCE because the shape differs per effect:
    # an `I` has no before, a `D` has no after, and the missing end is the zero the delta is
    # measured from.
    #
    # HISTORY - WHY THIS WAS POSTGRESQL ONLY while SQLite was a second tier (until programme 017,
    # stage 3, which removed SQLite and with it the `ddl_if(dialect="postgresql")` that was here;
    # on PostgreSQL the constraint is emitted exactly as before). On SQLite `Numeric`
    # binds through `float` (`processors.to_float`, no native decimal), so this equality is floating
    # point there and it is FALSE FOR ORDINARY MONEY: measured 2026-09-13 on sqlite3, a legitimate
    # movement of `10.00000001 -> 10.00000002, delta 0.00000001` gives a left-hand side of
    # `9.99999905104687e-09`, and `33554431.99999999 -> 33554432.00000001, delta 0.00000002` gives
    # `1.862645149230957e-08`. Installing it on SQLite would refuse real writes, which is the same
    # defect as admitting false ones. The SQLite tier's guarantee for this class is the in-process
    # readback of the stored entries (`app/core/ledger/journal.py::_verify_entries`), which compares
    # scale-8 `Decimal`s reconstructed by the column's own result processor rather than floats.
    #
    # `Base.metadata.create_all` must produce the SAME constraint as `alembic upgrade head` does,
    # and the two paths are compared by name in
    # `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`.
    CheckConstraint(
        "delta = COALESCE(amount_after, 0) - COALESCE(amount_before, 0)",
        name="chk_debt_journal_entries_delta_arithmetic",
    ),
    UniqueConstraint(
        "operation_id",
        "flush_ordinal",
        "equivalent_id",
        "debtor_id",
        "creditor_id",
        name="uq_debt_journal_entries_op_flush_edge",
    ),
    Index("ix_debt_journal_entries_edge", "equivalent_id", "debtor_id", "creditor_id"),
    Index("ix_debt_journal_entries_operation", "operation_id"),
)


debt_operation_equivalents = Table(
    "debt_operation_equivalents",
    Base.metadata,
    Column(
        "operation_id",
        Uuid(as_uuid=True),
        ForeignKey("debt_operations.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "equivalent_id",
        Uuid(as_uuid=True),
        ForeignKey("equivalents.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("in_intent", Boolean, nullable=False),
    Column("in_scope", Boolean, nullable=False),
    Column("effect_count", Integer, nullable=False),
    Column("effect_digest", String(64), nullable=False),
    CheckConstraint("effect_count >= 0", name="chk_debt_operation_equivalents_count"),
    CheckConstraint("length(effect_digest) = 64", name="chk_debt_operation_equivalents_digest"),
    # A row exists because the operation SAID it would touch this equivalent, or because it DID.
    # A row that is neither is a row nobody can explain.
    CheckConstraint("effect_count > 0 OR in_intent", name="chk_debt_operation_equivalents_why"),
    # And an effect outside the declared scope is not something to record: it is something the
    # flush hook must already have refused.
    CheckConstraint("in_scope OR effect_count = 0", name="chk_debt_operation_equivalents_scope"),
)


#: Every table the write guard protects on the journal's own side. Named once so the guard, the
#: migration and the tests cannot drift apart.
DEBT_JOURNAL_TABLE_NAMES = frozenset(
    {
        debt_operations.name,
        debt_journal_entries.name,
        debt_operation_equivalents.name,
    }
)
