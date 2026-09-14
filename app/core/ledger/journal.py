"""The debt journal: every change to `debts` happens inside a declared operation, or not at all.

Programme 015, phase B step 4 (design v2 §1, §6, §7). `docs/ru/02-protocol-spec.md` §11.2.1 withdrew
the zero-sum check as a tautology of the edge model and named its replacement in the same sentence -
"расхождение между `debts` и журналом операций, попарная сверка рёбер с историей, которая их
породила". This module is that journal's write side.

WHAT IT IS NOT. It is not tamper-evident and nothing here may be described as such: the checksum
chain is deferred by owner decision of 2026-09-12 until an external trusted anchor exists (design
v2 §5). What this gives is a RECORD, refused-on-absence: money cannot move through the ORM without
an operation saying who moved it and why, and the record of what moved is written in the same
database transaction as the movement.

SLICE A REGISTERS NOTHING. `install_journal` exists and is called by nobody in `app/` today. The
listeners live on the engine and session target you hand it, so the mechanism can be proven on a
private engine before any production writer is instrumented (slice C).

=================================================================================================
THE FOUR PIECES
=================================================================================================

1. THE REGISTRY is keyed by the Core `RootTransaction`, not by the Session and not by the
   Connection. Both of those were tried and both are wrong:

   * `session.info` dies with the Session while the DATABASE transaction lives on. A Session
     joined to an external connection can end its own root without any DB rollback
     (`sqlalchemy/orm/session.py:1361`, `:1377`), and the external owner then commits - carrying
     with it whatever the dead Session wrote.
   * `Connection.info` is the POOL RECORD's dict and survives checkouts, so state would leak from
     one unit of work into the next.

   The `RootTransaction` is the object whose lifetime is exactly the database transaction's, which
   is the lifetime the record has to have.

2. THE OPERATION CONTEXT (`debt_operation`) registers a record in that transaction, writes an
   envelope, and on clean exit completes it. Anything else - an exception, a cancellation, a
   session that closed underneath it - leaves the record NOT COMPLETED, and a not-completed record
   refuses the commit.

3. THE FLUSH HOOK reads the effects out of the unit of work BEFORE any SQL is sent, refuses what
   it cannot record, and writes the per-edge entries in `after_flush`.

4. THE WRITE GUARD refuses every route into `debts` that the flush hook cannot see - Core DML, the
   three `bulk_*` entry points, a DML CTE hidden in a SELECT - and every route into the journal's
   own tables that is not this module.

=================================================================================================
THREE THINGS THAT LOOK LIKE DETAILS AND ARE NOT
=================================================================================================

A ROLLBACK EVENT IS NOT A REPORT THAT A ROLLBACK HAPPENED (binding acceptance condition 1).
SQLAlchemy dispatches `rollback` and `rollback_savepoint` BEFORE the SQL
(`sqlalchemy/engine/base.py:1105`, `:1150`) and deactivates the transaction object in a `finally`
either way. Clearing the registry from those events therefore treats a FAILED rollback as a
success: the reviewer's probe raised inside the event, the savepoint's rows stayed in the
transaction, the registry was empty, and the root commit stored a debt of 42. So this module does
not clear anything from a rollback event. A savepoint drop is confirmed by `after_cursor_execute`
seeing the `ROLLBACK TO SAVEPOINT` statement actually execute, and a root's state is not cleared at
all - it dies with its key, because a rolled-back root is never reused (`Connection.begin()` builds
a new `RootTransaction`). A rollback that fails therefore keeps the refusal, which is the
requirement.

NOTHING HOLDS THE ROOT ALIVE (binding acceptance condition 2). The registry is a
`WeakKeyDictionary` keyed by the root, so a value holding `op.root` as an ordinary attribute would
be a strong reference from the value back to its own key - and the reviewer measured exactly that:
`root_alive True` after commit and a full collection, every finished transaction retained for the
life of the process. `_OpRecord` therefore holds a WEAK reference to the root and to the Session,
and its effects hold UUIDs rather than ORM objects.

THE GRANT NAMES WRITES, NOT A WINDOW (binding acceptance condition 3). A grant valid "while the
session is flushing" is a window, and the reviewer walked through it twice: DML issued from another
listener's `after_flush` passed it, and a `before_flush` listener registered after this one changed
a debt after its effects had been read. The grant here is a MULTISET OF VERIFIED WRITES - for each
effect, the primary key, the directed edge and the exact amount the hook recorded - and each
statement's rows are matched against it and consumed. A row the hook never verified matches nothing;
a row whose amount was changed after the hook read it matches nothing either. Batching is untouched,
because matching is per row and not per statement.

AND A GRANT IS CHECKED BEFORE EXECUTION, SO IT CANNOT BE THE LAST WORD (T1528, 2026-09-13). Three
holes were measured in the grant, and all three came from one decision: the guard read a statement's
PARAMETER DICT and inferred meaning from the types in it and from the keys missing from it. A column
written with a SQL expression is in neither - `debt.amount = Debt.amount + 1` compiles
`SET amount=(debts.amount + :amount_1)` and carries no `amount` parameter at all - so the absence
that means "this UPDATE does not touch the money" was produced by an UPDATE that moved it, and a
debt went from 10 to 11 with an empty entry list. The same absence moved an edge; and "the amount" as
"some Decimal in the row" let a row writing 12 consume a grant for 11.

The parameter reading is FIXED (`_statement_values`, `_written`, and the amount by its own name), but
the grant is no longer where this rests. After the flush's SQL has run, `_reconcile` reads every row
the journal claims BACK OUT OF THE DATABASE and requires it to be what the entries say: present or
gone, on the recorded edge, holding the recorded amount. That is a check on what was executed rather
than on what a statement appeared to say, and it holds whatever the parameters looked like, whoever
changed them, and in whatever listener order - including a `before_execute` neighbour registered
after this module's, which changed an INSERT from 11 to 12 after verification and was measured
durable before this existed. One extra SELECT per flush that touches a debt is what it costs - every
flush, including a metadata-only one, because those add a `_RowState` too.

AND THEN WE READ THE DEBT ROW BACK AND NEVER READ OUR OWN RECORD (T1530, 2026-09-13, third review
circle of the T1528 delta). The entry INSERT went through the same pipeline as everything else, the
write guard waved it through because `_INTERNAL` was set and compared nothing, `delta` was not
required to be `amount_after - amount_before` by any constraint, and `_complete` digested the STORED
rows. Measured: `debts` holding 11 under an entry saying `10 -> 12, delta 2`, and under an entry
saying `10 -> 11, delta 2`, both with a completed envelope and a digest over the tampered row. The
answer is the same discipline applied to the journal's own tables - `_verify_entries` reads each
flush's entries back and requires them to be exactly the `_Effect` list, `_complete` requires every
stored row to be one this operation computed before the digest is taken - plus a PostgreSQL CHECK
constraint on the arithmetic that sits below every listener
(`app/db/journal_tables.py`, migration 024; SQLite cannot carry it and the measurement that says so
is with the constraint).

THE VERIFICATION READS GO THROUGH `exec_driver_sql`, AND THE MODULE'S OLD CLAIM ABOUT `text()` WAS
WRONG (T1531, 2026-09-13). `_reconcile`'s SELECT used to be a `select()` through `conn.execute`, which
dispatches `before_execute` - so the boundary the contract rests on was rewritable by the very
neighbour it was checking, measured with the amount projection replaced by a literal - and it carried
an execution option that was an exact ORACLE for finding it, spoofable in the other direction too.
Both are gone: the reads are hand-written SQL through `exec_driver_sql`, which dispatches no
`before_execute` at all, and provenance is now held by the mechanism (`_OWN`,
`journal_statement_is_own`) instead of asserted by the statement. VERIFIED on SQLAlchemy 2.0.25 in the
same test that falsifies this module's former claim that `text()` fires no event: it DOES fire
`before_execute`, as a `TextClause`. The conclusion that a `text()` write is unseen survives for a
different reason - `_dml_tables` only recognises `UpdateBase` - and `exec_driver_sql` is the only
entry point that dispatches no `before_execute`. WHAT IS NOT CLOSED: `before_cursor_execute` is still
dispatched for `exec_driver_sql` and a `retval=True` neighbour there can still rewrite the SQL. That
is measured OPEN by `tests/unit/test_p015_t1531_*` and is not claimed away.

LISTENER ORDER IS NOT WINNABLE, SO THE SAVEPOINT ACCOUNT STOPPED DEPENDING ON IT (T1532, 2026-09-13).
A `rollback_savepoint` listener on the `Connection` CLASS runs before every registration this module
can make, including the per-connection one T1528 added, in both registration orders - measured. With
the recorder pre-empted, the rollback statement was never issued, SQLAlchemy deactivated the nested
transaction anyway, and a debt of 42 the writer had asked to undo was committed. So the account is
kept from the SQL stream instead: `SAVEPOINT`, `RELEASE SAVEPOINT` and `ROLLBACK TO SAVEPOINT` are all
statements, and a savepoint an operation is bound to that left SQLAlchemy's nesting with neither of
the two closing statements observed is a refusal (`_lost_savepoint_closes`). The rollback confirmation
is inverted in the same spirit - the SQL is the primary fact, an unrecorded rollback of a bound
savepoint poisons - but the inversion alone does not reach the scenario above, because a prevented
event prevents the statement, and that is said where it matters rather than only here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import weakref
from collections import Counter
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Iterable, Iterator

from sqlalchemy import event, insert, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.base import RootTransaction, TwoPhaseTransaction
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.sql.expression import UpdateBase
from sqlalchemy.types import Numeric

from app.core.auth.canonical import canonical_json
from app.db.journal_tables import (
    DEBT_JOURNAL_TABLE_NAMES,
    OPERATION_KINDS,
    OPERATION_KINDS_WITH_TX,
    SCHEMA_VERSION,
    debt_journal_entries,
    debt_operation_equivalents,
    debt_operations,
)
from app.db.models.debt import Debt
from app.db.reconciliation_tables import debt_reconciliation_baselines
from app.db.sqlite_transaction_control import sqlite_transaction_control_is_installed

__all__ = [
    "DEBT_TABLE_NAME",
    "DebtJournalError",
    "DebtOperationIncomplete",
    "Reason",
    "debt_operation",
    "install_flush_hook",
    "install_journal",
    "install_write_guard",
    "journal_statement_is_own",
    "mapped_journal_tables",
    "journal_is_installed",
    "uninstall_flush_hook",
    "uninstall_journal",
    "uninstall_write_guard",
]

DEBT_TABLE_NAME = Debt.__tablename__

#: Exactly the scale every money column in this repository carries.
_MONEY_QUANTUM = Decimal("1E-8")

#: The exclusive magnitude bound of `NUMERIC(20, 8)`: twelve integer digits.
_MONEY_MAGNITUDE = Decimal("1E12")


class Reason:
    """Why a refusal happened, as a value a test can assert on instead of a message.

    A refusal that can only be recognised by its prose is a refusal whose mechanism nobody can
    pin down - and programme 015 exists because a constraint refusing the right value for the
    wrong reason reads exactly like one refusing it for the right one (`app/db/types.py`, the
    `NOT NULL` that answers a question about NaN).
    """

    NO_OPERATION = "no_operation"
    FOREIGN_OPERATION = "foreign_operation"
    ROOT_POISONED = "root_poisoned"
    OPERATION_NOT_COMPLETED = "operation_not_completed"
    ORPHANED_OPERATION = "orphaned_operation"
    NESTED_OPERATION = "nested_operation"
    STALE_OPERATION_HANDLE = "stale_operation_handle"
    PARTIAL_FLUSH = "partial_flush"
    UNCONFIRMED_ROLLBACK = "unconfirmed_rollback"
    KEY_FIELD_CHANGED = "key_field_changed"
    MISSING_HISTORY = "missing_history"
    INCOMPLETE_DEBT = "incomplete_debt"
    SAME_EDGE_TWICE = "same_edge_twice"
    OUT_OF_SCOPE = "out_of_scope"
    MONEY_FINITENESS = "money_finiteness"
    MONEY_MAGNITUDE = "money_magnitude"
    MONEY_QUANTIZATION = "money_quantization"
    MONEY_ROUND_TRIP = "money_round_trip"
    UNVERIFIED_DEBT_WRITE = "unverified_debt_write"
    VERIFIED_WRITE_MISSING = "verified_write_missing"
    UNRECONCILED_DEBT_ROW = "unreconciled_debt_row"
    UNRECORDED_JOURNAL_ENTRY = "unrecorded_journal_entry"
    UNREADABLE_VERIFICATION = "unreadable_verification"
    LOST_SAVEPOINT_CLOSE = "lost_savepoint_close"
    UNRECORDED_SAVEPOINT_ROLLBACK = "unrecorded_savepoint_rollback"
    JOURNAL_TABLE_WRITE = "journal_table_write"
    AUTOCOMMIT_ROOT = "autocommit_root"
    TWO_PHASE_ROOT = "two_phase_root"
    NO_TRANSACTION_CONTROL = "no_transaction_control"
    ENGINE_NOT_INSTRUMENTED = "engine_not_instrumented"
    BAD_ARGUMENT = "bad_argument"
    ENVELOPE_LOST = "envelope_lost"
    STALE_DB_TRANSACTION = "stale_db_transaction"
    UNMEASURED_DB_TRANSACTION = "unmeasured_db_transaction"
    UNVERIFIABLE_WRITER_AFTER_BASELINE = "unverifiable_writer_after_baseline"


#: Writers whose effects nothing can recompute (step 5 key review: `SEED/TEST_FIXTURE-PREBASELINE-ONLY`).
#: Once an equivalent has a reconciliation baseline, an operation of one of these kinds that touched it
#: is refused at completion rather than allowed to produce a `PASSED` over a change no rule explains.
_PRE_BASELINE_ONLY_KINDS = frozenset({"SEED", "TEST_FIXTURE"})


class DebtJournalError(RuntimeError):
    """A refusal by the debt journal.

    `RuntimeError` and deliberately NOT a `GeoException`: a `GeoException` is an answer to an API
    caller and `payments/service.py:990` turns one into a rejected payment. A journal refusal is
    not a business outcome - it says the process may not write money right now - and it must not
    be reshaped into one on the way out.
    """

    def __init__(self, reason: str, message: str, **context: Any) -> None:
        super().__init__(f"[{reason}] {message}")
        self.reason = reason
        self.context = context


class DebtOperationIncomplete(DebtJournalError):
    """A transaction tried to commit (or release) while an operation was not COMPLETED."""


# =================================================================================================
# Registry
# =================================================================================================


@dataclass(frozen=True)
class _Effect:
    """One edge's movement in one flush. UUIDs only - never an ORM object (condition 2)."""

    flush_ordinal: int
    debt_id: uuid.UUID
    equivalent_id: uuid.UUID
    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    effect: str
    amount_before: Decimal | None
    amount_after: Decimal | None
    delta: Decimal


@dataclass(frozen=True)
class _RowState:
    """What the DATABASE must hold for one row once this flush's SQL has run.

    This is the journal's claim about a row, restated as something that can be LOOKED UP rather than
    inferred: the edge the entry names, the amount the entry ends on, and whether the row is there at
    all. `_reconcile` reads the row back and compares. See its docstring for why the comparison is
    the boundary this mechanism needs and the parameter-level grant is not.
    """

    debt_id: uuid.UUID
    equivalent_id: uuid.UUID
    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    #: The amount the row must hold, or None when the row must be GONE.
    amount: Decimal | None
    present: bool


class _OpRecord:
    """One operation's registration inside one database transaction.

    Every reference out of this object that could keep a transaction, a connection or a session
    alive is a WEAK one. See the module docstring, condition 2.
    """

    OPENING = "OPENING"
    OPEN = "OPEN"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    POISONED = "POISONED"

    def __init__(
        self,
        *,
        kind: str,
        identity: str,
        tx_id: str | None,
        intent: Any,
        intent_digest: str,
        scope_equivalent_ids: frozenset[uuid.UUID] | None,
        intent_equivalent_ids: frozenset[uuid.UUID],
        root: RootTransaction,
        chain: tuple[str, ...],
        session: Session,
        session_boundary: Any,
        generation: int,
    ) -> None:
        self.id = uuid.uuid4()
        self.kind = kind
        self.identity = identity
        self.tx_id = tx_id
        self.intent = intent
        self.intent_digest = intent_digest
        self.scope_equivalent_ids = scope_equivalent_ids
        self.intent_equivalent_ids = intent_equivalent_ids
        self.chain = chain
        self.generation = generation
        self.state = self.OPENING
        self.flush_count = 0
        self.effects: list[_Effect] = []
        self._root_ref = weakref.ref(root)
        self._session_ref = weakref.ref(session)
        self._boundary_ref = weakref.ref(session_boundary) if session_boundary is not None else None

    @property
    def root(self) -> RootTransaction | None:
        return self._root_ref()

    @property
    def session(self) -> Session | None:
        return self._session_ref()

    @property
    def session_boundary(self) -> Any:
        return self._boundary_ref() if self._boundary_ref is not None else None

    @property
    def is_orphaned(self) -> bool:
        """True once the Session that owns this operation is gone while the DB transaction lives."""

        return self._session_ref() is None

    @property
    def is_settled(self) -> bool:
        return self.state == self.COMPLETED

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<_OpRecord {self.kind}/{self.identity} {self.state}>"


@dataclass
class _TxState:
    """The journal's state for ONE database transaction."""

    ops: list[_OpRecord] = field(default_factory=list)
    poison: str | None = None
    #: savepoint name -> True, set when a rollback was REQUESTED and cleared when the SQL ran.
    pending_savepoint_rollbacks: dict[str, bool] = field(default_factory=dict)
    #: The savepoints the SQL STREAM shows as open, outermost first - `SAVEPOINT x` seen and no
    #: `RELEASE SAVEPOINT x` or `ROLLBACK TO SAVEPOINT x` seen since (T1532).
    #:
    #: WHY THE SQL AND NOT THE EVENTS. The events are pre-emptable and the SQL is not: measured
    #: 2026-09-13, a `rollback_savepoint` listener on the `Connection` CLASS runs before this
    #: module's - before even the per-connection registration T1528 added - and an exception there
    #: stops the journal from ever learning that a rollback was asked for. `after_cursor_execute`,
    #: on the other hand, can only be pre-empted by a listener that lets the statement run first.
    savepoints_open: list[str] = field(default_factory=list)
    generation: int = 0


def _stood_down(engine: Any) -> bool:
    """Whether the journal has been stood down on this engine (see `uninstall_write_guard`).

    Declared here, above every listener, because each of them has to ask: under global arming the
    listeners are on the `Engine` class and a stood-down engine's statements still reach them.
    """

    return _sync_engine(engine) in _STOOD_DOWN


#: transaction -> state. Weak keys: the state may never outlive the transaction it describes, and
#: no value in it may point back at the key (condition 2).
_REGISTRY: "weakref.WeakKeyDictionary[RootTransaction, _TxState]" = weakref.WeakKeyDictionary()

def _state_for(conn: Connection, *, create: bool = False) -> _TxState | None:
    if _stood_down(conn.engine):
        return None
    root = conn.get_transaction()
    if root is None:
        return None
    state = _REGISTRY.get(root)
    if state is None and create:
        state = _TxState()
        _REGISTRY[root] = state
    return state


def _poison(state: _TxState, reason: str) -> None:
    if state.poison is None:
        state.poison = reason


def _core_chain(conn: Connection) -> tuple[str, ...]:
    """The Core savepoint names on this connection, outermost first.

    Pinned to SQLAlchemy 2.0.25: `NestedTransaction._savepoint` and `._previous_nested` are
    private. `tests/unit/test_p015_b4a_journal_pins_sqlalchemy_internals.py` turns red on a
    version that renames them, which is the point of using them knowingly rather than by accident.
    """

    names: list[str] = []
    nested = conn.get_nested_transaction()
    while nested is not None:
        names.append(nested._savepoint)
        nested = nested._previous_nested
    names.reverse()
    return tuple(names)


# =================================================================================================
# Money: the four storability predicates, each refusing under its own name
# =================================================================================================


def _as_decimal(value: Any) -> Decimal | None:
    """A money value as a `Decimal`, or None when it is not a number at all.

    `float('nan')` becomes `Decimal('NaN')` rather than None: a NaN IS a value that arrived, and
    it must be refused by the FINITENESS predicate and named as such. Answering None here would
    send the refusal out under a different name, which is the defect T1526 is about.
    """

    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


def _round_trip(value: Decimal, dialect: Any) -> Decimal | None:
    """What this dialect would store and read back for `value`, or None if it cannot be asked.

    Built from the column type's own processors so the answer is the dialect's, not this module's
    opinion of it. On SQLite `Numeric` binds as a float and reads back through a scale-8 decimal
    processor, which is where values above 2^26 stop round-tripping; on PostgreSQL both processors
    are the identity.
    """

    impl = Numeric(20, 8).dialect_impl(dialect)
    bind = impl.bind_processor(dialect)
    bound = bind(value) if bind is not None else value
    try:
        result = impl.result_processor(dialect, None)
    except Exception:  # noqa: BLE001
        # A dialect whose result processor needs the column type of a real result set, which this
        # has no way to supply. asyncpg is the one in this repository (it raises "Unknown PG
        # numeric type"), and there the answer is the identity anyway: asyncpg returns `NUMERIC`
        # as `Decimal` untouched, which the full-width money test measures end to end rather than
        # assuming here (`tests/integration/test_p015_b4a_journal_postgres.py`).
        result = None
    if result is None:
        return bound if isinstance(bound, Decimal) else _as_decimal(bound)
    return _as_decimal(result(bound))


def _check_storable(value: Decimal | None, dialect: Any, *, what: str) -> None:
    """The four predicates of design v2 §4 rule 3, in order, each refusing under its own name.

    They are FOUR and not one list called "storability", because two of them pass on values the
    others refuse: `1E12` and `Infinity` round-trip on SQLite byte for byte, so a round-trip
    assertion on them proves nothing while looking like proof.
    """

    if value is None:
        return
    if not value.is_finite():
        raise DebtJournalError(
            Reason.MONEY_FINITENESS,
            f"{what} is {value!r}, which is not a finite number. A NaN amount does not make the "
            f"book wrong by a nameable sum - it makes every total over the equivalent NaN.",
            value=str(value),
        )
    if abs(value) >= _MONEY_MAGNITUDE:
        raise DebtJournalError(
            Reason.MONEY_MAGNITUDE,
            f"{what} is {value}, outside NUMERIC(20, 8): twelve integer digits at most.",
            value=str(value),
        )
    if value != value.quantize(_MONEY_QUANTUM):
        raise DebtJournalError(
            Reason.MONEY_QUANTIZATION,
            f"{what} is {value}, which does not fit scale 8 without changing it.",
            value=str(value),
        )
    stored = _round_trip(value, dialect)
    if stored != value:
        raise DebtJournalError(
            Reason.MONEY_ROUND_TRIP,
            f"{what} is {value}, and {dialect.name} would read it back as {stored}. The journal "
            f"will not record a number the database is going to change.",
            value=str(value),
            stored=str(stored),
        )


def _money_text(value: Decimal | None) -> str:
    """A money value as its exact scale-8 decimal string - the digest's only encoding."""

    if value is None:
        return ""
    return format(value.quantize(_MONEY_QUANTUM), "f")


# =================================================================================================
# Digests. NOT a tamper-evidence chain - see the module docstring.
# =================================================================================================


def _entry_digest(rows: Iterable[tuple[Any, ...]]) -> str:
    """A summary of a set of entries, for the step-6 verifier to compare against its own recount.

    This is a SUMMARY, not a seal: anyone who can rewrite the entries can recompute it. The
    checksum chain that would have been the seal is deferred (design v2 §5), and nothing that
    reads this value may call it tamper-evidence.
    """

    digest = hashlib.sha256()
    for row in rows:
        digest.update(("|".join("" if part is None else str(part) for part in row) + "\n").encode())
    return digest.hexdigest()


def _intent_digest(intent: Any) -> tuple[Any, str]:
    """The intent as it will be stored, and its digest over the same bytes.

    Stored value and digest come from ONE canonicalisation, so a reader recomputing the digest from
    the stored column gets the same answer. Digesting the caller's object and storing something
    else would make the column unverifiable by construction.
    """

    try:
        canonical = canonical_json(intent)
    except Exception as exc:  # noqa: BLE001 - any canonicalisation failure is the same refusal
        raise DebtJournalError(
            Reason.BAD_ARGUMENT,
            f"intent cannot be canonicalised, so it cannot be recorded: {exc}",
        ) from exc
    return json.loads(canonical.decode()), hashlib.sha256(canonical).hexdigest()


# =================================================================================================
# Grants: a multiset of verified writes, per connection (condition 3)
# =================================================================================================


#: A verified write, in full: effect kind, the debt's primary key, the DIRECTED EDGE the hook
#: recorded it on - `equivalent_id`, `debtor_id`, `creditor_id` - and the exact amount.
#:
#: THE EDGE IS PART OF THE IDENTITY, and leaving it out was a defect measured 2026-09-13 (T1527).
#: With a signature of `(kind, id, amount)` the grant could not tell one edge from another, so a
#: row whose edge differed from the one the journal had just recorded still matched it: the entry
#: said `(A, B, eq1)` while `debts` stored `(A, C, eq2)`, and the write was allowed. That is the
#: single failure this mechanism exists to prevent - criterion (a), the journal's per-edge deltas
#: equalling the edge's final minus initial, is false from that moment on, with nothing refusing at
#: write time. Two routes reached it on this tree and neither needed a patched SQLAlchemy: see
#: `_row_edge` below for the one that needs no listener at all.
_Signature = tuple[str, str, str, str, str, str]

#: Stands in a row's edge for a key column whose parameter value is not a UUID at all. It is a
#: value no signature component can equal - every component is a canonical dashed UUID string - so
#: an unreadable key column matches NOTHING rather than reading as "not named by this statement".
_UNREADABLE_KEY = "?"

#: The amount component of a write that moves NO money: an UPDATE the hook saw as dirty for
#: something other than the amount (a version bump). `_money_text` only ever produces digits or the
#: empty string, so this is a value no real amount can collide with.
#:
#: IT IS A STATE OF ITS OWN, and conflating it with "the amount is unchanged" was a defect measured
#: 2026-09-13 (T1527, review item 3). The hook already granted such a write - the `U` branch below
#: says so in as many words - but it granted it under the amount the row was NOT changing, and a
#: metadata-only UPDATE carries no amount parameter at all (`SET version=?` with the primary key in
#: the WHERE clause). So the grant could never be consumed: `debt.version += 1` inside a perfectly
#: ordinary operation was refused as `unverified_debt_write`, and had it not been, `after_flush`
#: would have refused the same flush again for a verified write that never reached the connection.
#: A guard that refuses legitimate writes is a defect in the same mechanism as one that admits
#: illegitimate ones, so this state is named on both sides: the hook writes it, and a row carrying
#: no amount is matched against it and against nothing else.
_NO_MONEY_MOVED = "-"

#: The parameter names an ORM persistence statement keys this table's primary key under. Derived
#: from the mapped table rather than spelled out, but the SHAPE is SQLAlchemy's and is pinned with
#: the other private couplings in this module (2.0.25): `_key_getters_for_crud_column` names a
#: WHERE-clause primary key `"%s_%s" % (table.name, col.key)`, while an INSERT's values and a
#: DELETE's own parameters use the column key itself. Measured on this tree: INSERT `id`, UPDATE
#: `debts_id`, DELETE `id`.
#:
#: WHY IDENTITY IS ESTABLISHED AND NOT INFERRED (T1527, review item 2). `_matches_any` used to try
#: every UUID in the row as the primary key, so the identity it matched on could itself diverge from
#: the row's: a late substitution of `Debt.id` that left the old id behind in another UUID column
#: still found its expectation. Asking the row which parameter IS the primary key removes the guess.
_PK_COLUMN = tuple(Debt.__table__.primary_key.columns)[0].key
_PK_PARAM_NAMES = (_PK_COLUMN, f"{DEBT_TABLE_NAME}_{_PK_COLUMN}")

#: The three columns that make an edge a directed edge. Defined here rather than with the flush hook
#: because the write guard reads them out of a statement before the hook's half is reached.
_KEY_COLUMNS = ("debtor_id", "creditor_id", "equivalent_id")

#: Exactly the columns a grant speaks about: which row, which edge, how much.
#:
#: IT NARROWS WHAT IS READ AND NOTHING DEPENDS ON IT, which is stated because the opposite would be
#: easy to assume: `_statement_values` is only ever consulted through `_written` for these same
#: columns, so including `version` or `updated_at` would change no decision. It is here so that the
#: set of columns the guard reasons about is written down in one place, and so that a declared value
#: for a column nobody checks is not carried around.
_GRANTED_COLUMNS = frozenset({_PK_COLUMN, "amount", *_KEY_COLUMNS})


def _key_text(value: Any) -> str | None:
    """One UUID in its canonical dashed spelling, whatever spelling it arrived in.

    THE SPELLING TRAP THIS PROGRAMME ALREADY HIT. `Uuid(as_uuid=True)` is 32 hex characters on
    SQLite and a native `uuid` object on PostgreSQL, so a comparison written against one tier can
    silently match nothing on the other. `before_execute` sees the parameters BEFORE any bind
    processor, so in practice both tiers hand this function `uuid.UUID` objects - but "in practice"
    is what the trap is made of, so every spelling is normalised through `uuid.UUID` and anything
    that is not a UUID at all answers `None` instead of comparing as a string.
    """

    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (str, bytes)):
        try:
            return str(uuid.UUID(value.decode() if isinstance(value, bytes) else value))
        except (ValueError, UnicodeDecodeError):
            return None
    return None


@dataclass
class _Grant:
    """The writes one flush verified, and nothing else.

    `expected` maps a write signature to how many times it may still be seen. A statement's rows
    are matched against it one by one and consumed; anything unmatched is refused, and anything
    left over at `after_flush` means a verified write never happened.
    """

    session_id: int
    expected: dict[_Signature, int]
    matched: int = 0

    def take(self, signature: _Signature) -> bool:
        remaining = self.expected.get(signature, 0)
        if remaining <= 0:
            return False
        if remaining == 1:
            del self.expected[signature]
        else:
            self.expected[signature] = remaining - 1
        self.matched += 1
        return True

    def take_row(
        self,
        effect: str,
        identity: str,
        amount: str,
        edge: dict[str, str | None],
    ) -> bool:
        """Consume the one expectation this row IS, edge included.

        ONE IDENTITY AND ONE AMOUNT, not a set of candidates (T1528, review item 3). This used to
        take every value the row might have meant - every `Decimal` in the parameter dict as a
        possible amount - and offered them all to the grant, so a row that wrote 12 matched a grant
        for 11 because some other parameter carried an 11. What a statement writes is not a guess
        about its values, so the caller now reads the amount from the `amount` bind BY NAME and
        hands over exactly one.

        Still a scan over the expectations rather than a dict lookup, and for one reason only: an
        UPDATE or a DELETE need not name its edge at all, so the signature cannot be reconstructed
        from the row to be looked up.

        `edge[column] is None` means THIS STATEMENT DOES NOT WRITE THAT COLUMN, so the database
        keeps whatever it already held for the row and the hook read its value from that same
        stored row. A value that is present must equal the recorded one; `_UNREADABLE_KEY` equals
        nothing. For an INSERT every key column is always named - all three are `NOT NULL` with no
        default, and the hook refuses a Debt that reaches the flush without them - so `_matches_any`
        refuses an INSERT with an unnamed key column outright rather than letting it through here.
        """

        for signature in list(self.expected):
            kind, debt_id, equivalent_id, debtor_id, creditor_id, expected_amount = signature
            if kind != effect or debt_id != identity or expected_amount != amount:
                continue
            recorded = {
                "equivalent_id": equivalent_id,
                "debtor_id": debtor_id,
                "creditor_id": creditor_id,
            }
            if any(
                written is not None and written != recorded[column]
                for column, written in edge.items()
            ):
                continue
            return self.take(signature)
        return False


#: connection -> the grant its current flush installed.
_GRANTS: "weakref.WeakKeyDictionary[Connection, _Grant]" = weakref.WeakKeyDictionary()

#: connection -> depth of this module's own writes to the journal tables.
_INTERNAL: "weakref.WeakKeyDictionary[Connection, int]" = weakref.WeakKeyDictionary()

#: connection -> depth of ANY statement this module is issuing, read or write.
#:
#: PROVENANCE THE MECHANISM OWNS, AND THAT IS THE WHOLE POINT (T1531, 2026-09-13). Until today the
#: journal said "this statement is mine" by putting an execution option on the statement
#: (`geo_journal_statement`), and a mark carried BY a statement is a mark anyone can carry: a writer
#: could set the same option on its own statement and be filtered out by R4's tracer as journal
#: noise, and - worse - the option was an exact ORACLE for recognising the verification read, which
#: is precisely the statement a neighbour wants to find. There was no anti-spoof test. This counter
#: is held by the journal for the duration of its own execution, so a statement cannot assert it and
#: a reader asks the mechanism (`journal_statement_is_own`) instead of sniffing SQL or options.
_OWN: "weakref.WeakKeyDictionary[Connection, int]" = weakref.WeakKeyDictionary()


def _bump(registry: "weakref.WeakKeyDictionary[Connection, int]", conn: Connection, by: int) -> None:
    depth = registry.get(conn, 0) + by
    if depth <= 0:
        registry.pop(conn, None)
    else:
        registry[conn] = depth


@contextmanager
def _journal_write(conn: Connection) -> Iterator[None]:
    """Mark this connection as executing the journal's OWN DML against the journal tables.

    Scoped to a connection rather than to the process: two units of work on two connections write
    their journals concurrently, and a process-wide flag would let one of them authorise the
    other's statement.
    """

    _bump(_INTERNAL, conn, 1)
    _bump(_OWN, conn, 1)
    try:
        yield
    finally:
        _bump(_INTERNAL, conn, -1)
        _bump(_OWN, conn, -1)


@contextmanager
def _journal_read(conn: Connection) -> Iterator[None]:
    """Mark this connection as executing one of the journal's own VERIFICATION READS.

    Deliberately NOT `_journal_write`: a read needs no authority over the journal tables, and
    widening `_INTERNAL` around it would authorise DML for the duration of a SELECT.
    """

    _bump(_OWN, conn, 1)
    try:
        yield
    finally:
        _bump(_OWN, conn, -1)


def journal_statement_is_own(conn: Any) -> bool:
    """Whether the statement this connection is executing right now is the JOURNAL'S OWN.

    The question a tracer has to be able to ask: "arming adds the journal's own statements and
    nothing else" is only checkable if the journal's statements can be told from the work's, and for
    the verification read of `debts` the table name cannot do it. Ask the mechanism, not the
    statement - see `_OWN` for why the execution option this replaces was the wrong answer.

    Accepts an `AsyncConnection` as well as a `Connection`, because a caller holding the async face
    of the same connection is asking about the same statement.
    """

    target = getattr(conn, "sync_connection", conn)
    return bool(_OWN.get(target))


def _dml_tables(clause: Any) -> set[str]:
    """Every table this statement WRITES, including through a CTE the outer statement only reads.

    `visitors.iterate` rather than `clause.table`, because a DML statement can sit inside a SELECT:
    `select(...).add_cte(insert(...))`, a DML CTE in a FROM, `insert().from_select()`. Those
    statements answer False to `is_dml` and reach the database all the same.
    """

    tables: set[str] = set()
    for element in visitors.iterate(clause):
        if isinstance(element, UpdateBase):
            table = getattr(element, "table", None)
            name = getattr(table, "name", None)
            if name is not None:
                tables.add(name)
    return tables


def _statement_effect(clause: Any) -> str | None:
    name = type(clause).__name__
    if name.endswith("Insert"):
        return "I"
    if name.endswith("Update"):
        return "U"
    if name.endswith("Delete"):
        return "D"
    return None


def _rows_of(multiparams: Any, params: Any) -> list[dict]:
    """The parameter dicts of this execution, single-row and executemany alike.

    SQLAlchemy hands the event either one dict in `params` or a list of dicts in `multiparams`
    (`sqlalchemy/engine/base.py::_invoke_before_exec_event`). A statement that carries its values
    inside the SQL - `insert(Debt).values(...)` - produces NEITHER, and that is not a hole: a
    statement whose rows cannot be read cannot be matched against the writes the hook verified, so
    it is refused. Fail-closed, per `AGENTS.md` §9.
    """

    if isinstance(params, dict) and params:
        return [params]
    rows: list[dict] = []
    for item in multiparams or ():
        if isinstance(item, dict):
            rows.append(item)
        elif isinstance(item, (list, tuple)):
            rows.extend(entry for entry in item if isinstance(entry, dict))
    return rows


#: A value the statement writes that this module cannot read: a SQL expression, evaluated by the
#: database and not by anything here. It is its OWN state, and the whole of T1528 review items 1 and
#: 2 is that it used to be indistinguishable from a column the statement does not write at all.
_OPAQUE = object()


def _statement_values(clause: Any) -> dict[str, Any]:
    """Columns this statement writes with a value of its OWN rather than from the caller's params.

    WHY THIS EXISTS AT ALL (measured 2026-09-13, T1528 review items 1 and 2). An ORM flush does not
    always put a column's new value in the parameter dict. `_collect_update_commands` separates
    values that are SQL EXPRESSIONS into `value_params`, and `_emit_update_statements` then executes
    `statement.values(value_params)` (`sqlalchemy/orm/persistence.py`), so the column is named by the
    STATEMENT and never appears among the parameters. Measured on this tree:
    `debt.amount = Debt.amount + 1` compiles `SET amount=(debts.amount + :amount_1)` with parameters
    `{version, debts_version, debts_id}` - no `amount` key anywhere.

    The guard read that absence as "this statement does not write the amount", which is the
    metadata-only signature, and the grant for a write that moves no money was consumed by a write
    that moved 1. The same absence let `debt.creditor_id = literal(other.id)` move the obligation to
    another participant with the full-key check of T1527 comparing nothing.

    WHAT IS READABLE HERE, and the rest is `_OPAQUE`. A `BindParameter` carrying a literal value -
    what `literal(x)` and `.values(col=x)` produce - IS the value, and reading it lets the edge be
    compared honestly instead of refused blindly. Anything else is a SQL expression whose result
    only the database knows, so it is unreadable, and unreadable matches no verified write.

    Only the columns the grant speaks about are returned - the primary key, the three key columns and
    the amount. That is a narrowing of what is carried and not a rule: nothing consults a column
    outside that set, so see `_GRANTED_COLUMNS` before reading it as a guard. (The `updated_at=now()`
    of every ORM UPDATE of this table does not arrive here at all - the compiler applies an `onupdate`
    itself, measured on this tree.)
    """

    declared: dict[str, Any] = {}
    values = getattr(clause, "_values", None)
    if not values:
        return declared
    for column, value in values.items():
        name = getattr(column, "key", None)
        if name not in _GRANTED_COLUMNS:
            continue
        if (
            isinstance(value, BindParameter)
            and not value.required
            and getattr(value, "callable_", None) is None
        ):
            declared[name] = value.value
        else:
            declared[name] = _OPAQUE
    return declared


#: A column this statement does not write at all. The database keeps what it already held, and the
#: hook read its value from that same stored row - which is a different fact from `_OPAQUE`.
_ABSENT = object()


def _written(row: dict, declared: dict[str, Any], column: str) -> Any:
    """What this statement writes into `column`: a value, `_OPAQUE`, or `_ABSENT`.

    The statement's own values win over the parameter dict, because that is the order the compiler
    applies them in; a column named by both with different values is unreadable rather than guessed.
    """

    if column in declared:
        value = declared[column]
        if column in row and value is not _OPAQUE and row[column] != value:
            return _OPAQUE
        return value
    if column in row:
        return row[column]
    return _ABSENT


def _key_written(value: Any) -> str:
    """One key column's written value as a canonical UUID string, or `_UNREADABLE_KEY`."""

    if value is _OPAQUE:
        return _UNREADABLE_KEY
    text = _key_text(value)
    return text if text is not None else _UNREADABLE_KEY


def _amount_text_or_none(value: Any) -> str | None:
    """A written amount as its exact scale-8 string, or None when it is not a storable number.

    `_money_text` quantizes, which RAISES on a NaN or on a value too wide for scale 8. A row
    carrying one of those is not a verified write, so it answers None and matches nothing - rather
    than taking the whole `before_execute` listener down with an `InvalidOperation`.
    """

    amount = _as_decimal(value)
    if amount is None or not amount.is_finite():
        return None
    try:
        return _money_text(amount)
    except InvalidOperation:
        return None


def _row_edge(row: dict, declared: dict[str, Any]) -> dict[str, str | None]:
    """The directed edge this statement writes, as far as the statement and its parameters name it.

    READ BY COLUMN NAME, and here that is sound where it was not sound for the primary key: a key
    column appears in the parameters under its own name (`debtor_id`) whether the statement is an
    INSERT or an UPDATE, while the primary key is `id` in one and `debts_id` in the other.

    WHY THIS IS READ AT ALL (measured 2026-09-13, T1527). The journal entry's edge comes from the
    ORM column attributes at `before_flush`. The row's edge comes from the same attributes read
    LATER - and SQLAlchemy's many-to-one dependency processor writes those attributes in between,
    after `before_flush` and before the statement is built. So `Debt(debtor_id=X, ..., creditor=q)`
    - plain, legal ORM with no listener anywhere - is journalled on the edge its columns named and
    stored on the edge its relationship named; the same construction on a stored Debt
    (`row.creditor = q`) puts `creditor_id` into the UPDATE's SET clause and moves the obligation to
    other participants, which is exactly what `C3` forbids and what `C3`'s own column-history check
    cannot see, because at `before_flush` the column has no history yet.

    AND THE STATEMENT'S OWN VALUES ARE READ TOO (T1528, review item 2). `debt.creditor_id =
    literal(other.id)` names the column in the STATEMENT rather than in the parameters, so reading
    the parameters alone answered "this statement does not write the creditor" about a statement
    whose whole purpose was to write it. `_written` answers from both, statement first.

    A column this statement does not write is `None`: the database keeps what it already held, and
    the hook read its value from that same stored row. A column written with something this module
    cannot read - a SQL expression, or a value that is not a UUID at all - is `_UNREADABLE_KEY`,
    which matches nothing.
    """

    edge: dict[str, str | None] = {}
    for column in _KEY_COLUMNS:
        value = _written(row, declared, column)
        edge[column] = None if value is _ABSENT else _key_written(value)
    return edge


def _row_identity(row: dict, declared: dict[str, Any]) -> str | None:
    """WHICH ROW this statement is about, read from the parameter that says so.

    `None` when nothing names the primary key, and `_UNREADABLE_KEY` when two names disagree - an
    UPDATE that puts a NEW `id` in its SET clause while the WHERE clause still finds the old one.
    Both are refusals rather than a guess: see `_PK_PARAM_NAMES`. The SET clause's `id` is read from
    the statement's own values as well, so substituting it with a SQL expression is unreadable
    rather than invisible (T1528).
    """

    found = {
        _key_written(value)
        for name in _PK_PARAM_NAMES
        if (value := _written(row, declared, name)) is not _ABSENT
    }
    if not found:
        return None
    if len(found) > 1:
        return _UNREADABLE_KEY
    return found.pop()


def _matches_any(effect: str, row: dict, declared: dict[str, Any], grant: _Grant) -> bool:
    """Consume the one expectation this row is: its identity, its edge and its amount, all by name.

    NOTHING HERE IS INFERRED FROM A VALUE'S TYPE OR FROM A MISSING KEY any more, and those were two
    separate defects in the same decision.

    * BY TYPE (T1527 review item 2, and T1528 review item 3 for the amount). An INSERT row carries
      four UUIDs and a Decimal. Reading "the primary key" as "some UUID" let a tampered row find an
      expectation that was not about it; reading "the amount" as "some Decimal" let a row that wrote
      12 consume a grant for 11 because an unused parameter happened to carry 11. Identity comes
      from `_row_identity`, the amount from the `amount` bind alone.
    * BY ABSENCE (T1528 review items 1 and 2). A column written with a SQL expression is not in the
      parameter dict at all, and "not in the dict" used to mean "not written". It now means what the
      statement says it means - see `_statement_values`.

    Three states, not two, for every column: written and readable, written and UNREADABLE, not
    written. Only the last one may be read as "the database keeps what it already held".
    """

    edge = _row_edge(row, declared)
    if effect == "I" and any(written is None for written in edge.values()):
        # FAIL-CLOSED, and not a hole that was tidied away. An ORM INSERT always names all three:
        # they are `NOT NULL` with no default, and `_effects_of_flush` refuses a Debt that reaches
        # the flush without them. A row that nevertheless arrives without one cannot be compared
        # with the edge the hook recorded, and a write that cannot be compared is not a verified
        # write (`AGENTS.md` §9).
        return False

    identity = _row_identity(row, declared)
    if identity is None:
        # A statement against `debts` whose parameters do not say which row it is about cannot be
        # matched against a verified write. Fail-closed, per `AGENTS.md` §9.
        return False

    written_amount = _written(row, declared, "amount")
    if effect == "D":
        # A DELETE has no SET clause: its parameters are the primary key and the version it expects.
        # The expectation the hook wrote for it carries no amount either.
        text: str | None = ""
    elif written_amount is _ABSENT:
        if effect != "U":
            # An INSERT with no amount. `amount` is `NOT NULL` with no default, so this cannot be an
            # ORM insert of a Debt; whatever it is, it is not a write the hook verified.
            return False
        # No amount anywhere: `SET version=?` with the primary key in the WHERE clause. This is the
        # metadata-only UPDATE, and it matches the expectation the hook wrote for exactly that -
        # `_NO_MONEY_MOVED` - and no other.
        text = _NO_MONEY_MOVED
    else:
        # THE AMOUNT UNDER THE `amount` NAME, WHATEVER TYPE IT ARRIVED AS, and nothing else. A caller
        # that writes `Debt(amount=5)` - an int, which the ORM accepts and the column converts on the
        # way out - produces a parameter dict whose amount is an `int`, and the hook's expectation is
        # built from the attribute through `_as_decimal`, so `5` and `Decimal("5")` are one
        # expectation. `None` here is an unreadable or non-finite amount, which matches nothing.
        text = _amount_text_or_none(written_amount)
        if text is None:
            return False
    return grant.take_row(effect, identity, text, edge)


# =================================================================================================
# Connection-level listeners
# =================================================================================================


def _refuse_at_db_level(conn: Connection, *, savepoint: str | None) -> None:
    """End the refused scope in the DATABASE before raising, and invalidate if that fails.

    Raising out of a Core transaction event is not enough on its own: `_commit_impl` dispatches
    `commit` before `do_commit` (`sqlalchemy/engine/base.py:1123-1138`), and when the event raises,
    `RootTransaction._do_commit` deactivates the transaction while leaving it current (`:2720`), so
    the later `rollback()` skips the DB rollback because `is_active` is already False (`:2698`).
    Measured on that path: the refused row was committed by the NEXT commit on the same connection.
    """

    dialect = conn.engine.dialect
    dbapi_connection = conn.connection
    try:
        if savepoint is None:
            dialect.do_rollback(dbapi_connection)
        else:
            dialect.do_rollback_to_savepoint(conn, savepoint)
            _release_refused_nested(conn, savepoint)
    except BaseException:
        # A connection whose refused scope could not be ended is a connection nobody can reason
        # about. Throwing it away is the only honest option left.
        conn.invalidate()


def _release_refused_nested(conn: Connection, savepoint: str) -> None:
    """Detach the savepoint SQLAlchemy was releasing, so the ROOT is left usable.

    WHY THIS EXISTS (measured 2026-09-12, activating the journal). A refusal raised out of
    `release_savepoint` leaves `NestedTransaction.is_active` False - `_do_commit` sets it in a
    `finally` - while the connection still POINTS at it (`_deactivate_from_connection` runs only on
    a successful release, `sqlalchemy/engine/base.py:2842-2853`). From then on
    `Connection._invalid_transaction` raises `PendingRollbackError` for every further statement,
    including the root commit.

    That contradicts what a release refusal is FOR. Design v2 §1.2 splits the two recipes
    deliberately: a root-commit refusal ends the whole transaction, a release refusal rolls back
    ONLY to the savepoint and leaves the root open and poisoned, so that a sibling operation's work
    is not destroyed by a neighbour's incomplete one. A root that cannot answer any statement is
    not "open"; and the refusal of the root commit that must follow would come from SQLAlchemy's
    bookkeeping rather than from the poison - a refusal by the wrong mechanism, which is the exact
    class of false green this programme exists to remove (`C10`, release half).

    The savepoint has already been rolled back in the DATABASE by the caller, so nothing here
    changes what is durable: this only puts the connection's own bookkeeping back where the
    rollback left it. Pinned to SQLAlchemy 2.0.25 along with the other private attributes
    (`_core_chain`).
    """

    nested = conn.get_nested_transaction()
    if nested is None or nested._savepoint != savepoint:
        return
    nested.is_active = False
    nested._deactivate_from_connection(warn=False)


def _blocking_problem(
    state: _TxState, *, ops: list[_OpRecord], lost_savepoints: list[str] | None = None
) -> tuple[str, str] | None:
    if state.poison is not None:
        return (Reason.ROOT_POISONED, f"this transaction was poisoned: {state.poison}")
    if lost_savepoints:
        # Binding condition 1, the half that needs no event at all (T1532). SQLAlchemy has stopped
        # holding a nested transaction for these savepoints and the SQL stream never showed either of
        # the two statements that can end one. Someone's close was lost, and what is still inside the
        # transaction is then unknown - which is the state the reviewer's class-level listener
        # produced, with a debt of 42 made durable by the commit that followed.
        return (
            Reason.LOST_SAVEPOINT_CLOSE,
            f"savepoint(s) {sorted(lost_savepoints)} left SQLAlchemy's nesting without a RELEASE or "
            f"a ROLLBACK TO reaching the database, so what an operation bound to them wrote is "
            f"neither known to be kept nor known to be undone",
        )
    if state.pending_savepoint_rollbacks:
        # Binding condition 1. SQLAlchemy ASKED for these savepoints to be rolled back and this
        # module never saw the SQL run - the reviewer's probe made exactly that happen by raising
        # inside the event. What is still inside the transaction is then unknown, and committing
        # an unknown is how a debt of 42 became durable. Refuse and say which savepoint.
        return (
            Reason.UNCONFIRMED_ROLLBACK,
            f"a rollback of savepoint(s) "
            f"{sorted(state.pending_savepoint_rollbacks)} was requested and never completed",
        )
    for op in ops:
        if op.is_orphaned and not op.is_settled:
            return (
                Reason.ORPHANED_OPERATION,
                f"operation {op.kind}/{op.identity} lost its session while the database "
                f"transaction was still open",
            )
        if not op.is_settled:
            return (
                Reason.OPERATION_NOT_COMPLETED,
                f"operation {op.kind}/{op.identity} is {op.state}, not COMPLETED",
            )
    return None


def _on_commit(conn: Connection) -> None:
    state = _state_for(conn)
    if state is None:
        return
    problem = _blocking_problem(
        state, ops=state.ops, lost_savepoints=_lost_savepoint_closes(conn, state, state.ops)
    )
    if problem is None:
        return
    reason, message = problem
    _poison(state, reason)
    _refuse_at_db_level(conn, savepoint=None)
    raise DebtOperationIncomplete(
        reason,
        f"refusing to commit: {message}. The transaction has been rolled back at the database "
        f"level before this was raised.",
    )


def _on_release_savepoint(conn: Connection, name: str, context: Any) -> None:
    state = _state_for(conn)
    if state is None:
        return
    bound = [op for op in state.ops if name in op.chain]
    # The savepoint being released is still in SQLAlchemy's live chain at this point - the event is
    # dispatched before `do_release_savepoint` - so an ordinary release reports nothing lost. What
    # this catches is a DEEPER savepoint of the same operation whose close never reached the database.
    problem = _blocking_problem(
        state, ops=bound, lost_savepoints=_lost_savepoint_closes(conn, state, bound)
    )
    if problem is None:
        return
    reason, message = problem
    _poison(state, reason)
    _refuse_at_db_level(conn, savepoint=name)
    raise DebtOperationIncomplete(
        reason,
        f"refusing to release savepoint {name}: {message}. The savepoint has been rolled back and "
        f"the root is poisoned - releasing would have merged unrecorded work into it.",
    )


def _driver_transaction_probe(conn: Connection) -> tuple[bool | None, str]:
    """Does the DRIVER have a transaction open right now, and how do we know? `None` when it will not say.

    PUBLIC DRIVER API ON BOTH TIERS, deliberately, and the two do not share a spelling:
    `asyncpg.Connection.is_in_transaction()` is a method, `sqlite3.Connection.in_transaction` an
    attribute (aiosqlite forwards it). Neither is SQLAlchemy's opinion, which is the point - this is
    asked precisely where SQLAlchemy's bookkeeping and the database have been measured to disagree.

    THREE ANSWERS AND NOT TWO, and collapsing them was the defect (T1528, review item 4). The old
    body returned `bool(probe())`, which maps a probe answering `None` - or anything else that is not
    a boolean - onto `False`, "no transaction is open". That is a measurement this module never made
    being reported as a clean bill of health, and `AGENTS.md` §1 forbids exactly that: an absent
    measurement may not read as a zero one. It was wrong in both directions - `bool(1)` reported a
    POSITIVE finding just as confidently.

    So only a real `bool` is an answer. Everything else - no probe at all, an answer of another type,
    a probe that raises - is `None` with a reason, and the caller refuses rather than proceeds.
    """

    try:
        dbapi_connection = getattr(conn, "connection", None)
    except BaseException:  # noqa: BLE001 - a connection that cannot hand over its driver is unmeasured
        return None, "the connection would not hand over its DBAPI connection"
    driver = getattr(dbapi_connection, "driver_connection", None)
    if driver is None:
        return None, "there is no driver connection to ask"
    probe = getattr(driver, "is_in_transaction", None)
    if callable(probe):
        try:
            answer = probe()
        except BaseException as exc:  # noqa: BLE001 - a probe may not be the thing that breaks a transaction
            return None, f"{type(driver).__name__}.is_in_transaction() raised {exc!r}"
        if isinstance(answer, bool):
            return answer, f"{type(driver).__name__}.is_in_transaction()"
        return None, (
            f"{type(driver).__name__}.is_in_transaction() answered {answer!r}, which is not a bool"
        )
    flag = getattr(driver, "in_transaction", _ABSENT)
    if isinstance(flag, bool):
        return flag, f"{type(driver).__name__}.in_transaction"
    if flag is _ABSENT:
        return None, (
            f"{type(driver).__name__} exposes neither `is_in_transaction()` nor `in_transaction`"
        )
    return None, f"{type(driver).__name__}.in_transaction is {flag!r}, which is not a bool"


def _driver_transaction_is_live(conn: Connection) -> bool | None:
    """The probe's answer alone: True, False, or None when the driver would not say."""

    return _driver_transaction_probe(conn)[0]


def _on_begin(conn: Connection) -> None:
    """A new root may not be born on top of a database transaction that is already open.

    SLICE A'S FINDING 23 WAS WRONG, and this is what replaces it. That finding concluded that no
    root-rollback handler was needed because "a rolled-back root is never reused, so the state dies
    with its weak key". Measured on PostgreSQL 2026-09-13 (T1527, review item 4), it is reused:

    * `RootTransaction._do_rollback` -> `_close_impl` dispatches `rollback` BEFORE the SQL
      (`sqlalchemy/engine/base.py:1105-1107`, `:2702-2717`) and detaches the root in a `finally`
      either way, so an exception out of a neighbour's `rollback` listener leaves the root detached
      with `do_rollback` never called.
    * On asyncpg the adapter's transaction is still open then (its `rollback()` is guarded by
      `_started`, `dialects/postgresql/asyncpg.py:860`) and `do_begin` does nothing, so
      `Connection.begin()` builds a NEW `RootTransaction` over the SAME database transaction.
    * The new root has no entry in `_REGISTRY`, so `_on_commit` finds no state and refuses nothing.
      Measured: an abandoned operation's debt of 71.00000000 and its envelope still in state `OPEN`
      were both made durable by the second root's commit.

    AND A `rollback` HANDLER DOES NOT SUBSTITUTE FOR IT, which is why the guard is here. Two reasons
    were given for that in T1527 and only one of them has survived:

    * ORDERING, WHICH IS NO LONGER THE REASON (T1528). A `rollback` listener of this module's own was
      measured never to run at all, because `_JoinedListener` runs every CONNECTION-level listener
      before every ENGINE-level one (`sqlalchemy/event/attr.py:607-636`, `:492-498`) and `insert=True`
      could only order this module among engine-level ones. That is now removable: `_on_engine_connect`
      registers per connection, at the level the neighbour occupies, and `_on_rollback_savepoint` is
      registered that way.
    * WHAT A ROLLBACK EVENT IS, WHICH STILL IS the reason. The event is dispatched BEFORE the SQL and
      the transaction is deactivated either way, so a handler there cannot tell a rollback that
      happened from one that was prevented - the module docstring's first condition. A guard built on
      it would treat a FAILED rollback as a success, which is the defect, not the fix.

    `begin` IS safe ground, and not because nobody can pre-empt it: a neighbour who pre-empts this
    by raising has aborted the `begin` itself, and `RootTransaction.__init__` assigns
    `connection._transaction` only AFTER `_connection_begin_impl()` returns
    (`sqlalchemy/engine/base.py:2667-2674`), so a refused begin leaves the connection with NO
    transaction object - `Connection.commit()` is then a no-op and the stale database transaction
    is rolled back when the connection is returned to the pool. Either way the commit does not
    happen, which is the requirement.

    NOT RESTRICTED TO CONNECTIONS THIS MODULE HAS STATE ON, and that is deliberate. The state that
    would say "this module had business here" is keyed by the root that just died; asking for it is
    exactly what is impossible at this point. The invariant stated positively - SQLAlchemy's `begin`
    must really begin a transaction - needs no such state, and is the same family of defect as
    T1525's SQLite transaction control.
    """

    if _stood_down(conn.engine):
        return
    if conn.closed or conn.invalidated:
        return
    live, how = _driver_transaction_probe(conn)
    if live is None:
        # UNMEASURED IS NOT CLEAN (T1528, review item 4). The carry-over this guard exists to stop is
        # not excluded by a question that was never answered, and the answer is the only thing
        # standing between an abandoned operation's debt and the next root's commit. Both drivers
        # this repository runs on answer every time, which is measured per tier rather than assumed
        # (`tests/unit/test_p015_t1528_*`, `tests/integration/test_p015_t1528_*_postgres.py`), so
        # this refusal is unreachable on a supported engine - and on an unsupported one it is the
        # honest outcome.
        raise DebtJournalError(
            Reason.UNMEASURED_DB_TRANSACTION,
            f"a new database transaction is being opened and this driver will not say whether it "
            f"already has one: {how}. A transaction that may have been carried over cannot be "
            f"distinguished from one that was not, and committing the difference is how an "
            f"abandoned operation's money became durable.",
            probe=how,
        )
    if not live:
        return
    raise DebtJournalError(
        Reason.STALE_DB_TRANSACTION,
        "a new database transaction is being opened on a connection whose driver still has one "
        "open. Whatever that transaction holds was never committed and never rolled back, and a "
        "commit on the new transaction would make it durable under a record that does not "
        "describe it.",
        probe=how,
    )


def _on_rollback_savepoint(conn: Connection, name: str, context: Any) -> None:
    """Record that a savepoint rollback was REQUESTED. Nothing is dropped here (condition 1).

    Registered with `insert=True` so it runs before any listener a caller adds later - but read the
    next paragraph for how far that reaches, because the sentence that used to stand here was wrong.

    `insert=True` ORDERS THIS MODULE AMONG ENGINE-LEVEL LISTENERS ONLY, measured 2026-09-13 (T1527).
    `_JoinedListener` puts a CONNECTION-level listener's functions in `parent_listeners` and the
    ENGINE's in `listeners` (`sqlalchemy/event/attr.py:607-636`, `:492-498`), and
    `_CompoundListener.__call__` runs `parent_listeners` first. So a neighbour who registers on the
    `Connection` runs before an ENGINE-level handler whatever `insert` says, and CAN stop it from
    ever learning that a rollback was asked for - measured, with a debt of 42 made durable by the
    root commit that followed.

    WHICH IS WHY THIS HANDLER IS ALSO REGISTERED PER CONNECTION (T1528). `_on_engine_connect` adds it
    to every `Connection` as the connection is created, with `insert=True`, so it is first among that
    connection's own listeners and a neighbour added later cannot pre-empt it. Read that function for
    what is still open: a neighbour who registers its own `engine_connect` handler on the `Engine`
    class ahead of the journal's. The handler runs twice per event as a result, and assigns one dict
    key, so the second run is a no-op.
    """

    state = _state_for(conn)
    if state is None or not any(name in op.chain for op in state.ops):
        return
    state.pending_savepoint_rollbacks[name] = True


def _on_engine_connect(conn: Connection) -> None:
    """Put the savepoint recorder FIRST among this connection's OWN listeners, as it is created.

    WHY THIS LISTENER EXISTS AND WHY IT IS THE ONE (T1528, review item 5). `_on_rollback_savepoint`
    is the only listener in this module whose job is to RECORD something: every other one refuses, and
    a refusal that is pre-empted by a neighbour's exception still ends in no commit. Being skipped
    loses information, and the reviewer measured exactly that - a connection-level neighbour raising
    in `rollback_savepoint`, `pending_savepoint_rollbacks` empty, the savepoint's rows never rolled
    back in the database, and the root commit making a debt of 42 durable under a record that says the
    savepoint was undone.

    `insert=True` ON THE ENGINE CLASS COULD NOT FIX IT. SQLAlchemy's `_JoinedListener` runs every
    CONNECTION-level listener before every ENGINE-level one (`sqlalchemy/event/attr.py:607-636`,
    `:492-498`), so ordering among engine-level listeners is ordering inside the losing half. The
    answer is to occupy the same level: `engine_connect` is dispatched as the `Connection` is built,
    which is the earliest moment at which there is a connection to register on, so a listener added
    here with `insert=True` precedes every listener a caller adds to that connection afterwards.
    Measured on this tree - `tests/unit/test_p015_t1528_*` is that measurement.

    WHAT IS STILL OPEN, and it is narrower rather than gone. The recorder is first among the
    listeners of every connection only because it is registered as the connection is built, so anyone
    whose own `engine_connect` handler runs BEFORE this one can register ahead of it on that
    connection and pre-empt it again. Which class-level handler runs first is SQLAlchemy's order and
    not something this module enforces: measured on this tree (2026-09-13), this handler ran first
    even against a LATER `insert=True` class-level registration - it is registered when
    `app.db.models` is imported, before any application code - but that is an observation, not a
    guarantee this module can make.

    DOUBLE REGISTRATION IS HARMLESS AND DELIBERATE. The handler stays on the `Engine` class as well,
    so it runs twice per event; it assigns one key in a dict, so the second run is a no-op. Removing
    the class-level one would narrow the journal to connections this listener saw being created.

    AND NOT EVERY CONNECTION ACCEPTS A LISTENER AT ALL - see the `InvalidRequestError` branch, where
    the skip is explained and, in the test, measured.
    """

    try:
        if event.contains(conn, "rollback_savepoint", _on_rollback_savepoint):
            return
        event.listen(conn, "rollback_savepoint", _on_rollback_savepoint, insert=True)
    except InvalidRequestError:
        # A CONNECTION THAT TAKES NO LISTENER FROM ANYONE, which is why this is a skip and not a
        # hole (measured 2026-09-13). A connection born from an `execution_options()` engine - an
        # `OptionEngine`, which `tests/integration/test_clearing_commit_replay_postgres.py` builds
        # for SERIALIZABLE - carries a doubly-joined dispatch that `Events._accept_with` does not
        # recognise, so `event.listen(conn, <any ConnectionEvents event>, ...)` raises
        # `InvalidRequestError` whoever calls it. The neighbour this registration exists to get ahead
        # of therefore cannot register on such a connection either, and the class-level listener is
        # the whole field there. The test asserts that symmetry rather than trusting it.
        return


def _confirm_savepoint_rollback(state: _TxState, name: str) -> None:
    """The SQL for a savepoint rollback has run. Drop what it undid - or refuse, if it was a surprise.

    THE SQL IS THE PRIMARY FACT AND THE EVENT IS CORROBORATION, which is the inversion T1532 asks
    for. Before it, a rollback observed with no pending record returned early: the journal had no
    record of a request, so it silently did nothing, and a pre-empted recorder was indistinguishable
    from a savepoint no operation cared about. Now an observed rollback of a savepoint an operation
    is BOUND TO, with no request ever recorded, poisons the transaction.

    IT POISONS RATHER THAN RAISING, and that is deliberate: this runs inside `after_cursor_execute`,
    where the statement has already executed, so raising would only move the failure. The poison is
    refused at the commit and at the release - the boundaries that can still stop something.

    THE GATE IS `name in op.chain`, THE SAME GATE `_on_rollback_savepoint` RECORDS UNDER, and the
    symmetry is what keeps this from refusing ordinary work. A savepoint no operation is bound to is
    rolled back on the main payment path every time `PaymentEngine._apply_flow` answers a
    `StaleDataError` (migration 023), and neither half of this pair looks at those.
    """

    bound_to_an_operation = any(name in op.chain for op in state.ops)
    if state.pending_savepoint_rollbacks.pop(name, None) is None:
        if bound_to_an_operation:
            _poison(state, Reason.UNRECORDED_SAVEPOINT_ROLLBACK)
        return
    # The root's poison is KEPT: a refusal that happened inside the savepoint was about this
    # transaction's right to write money, and undoing the savepoint does not undo that.
    state.ops = [op for op in state.ops if name not in op.chain]


#: The three savepoint statements SQLAlchemy's dialects emit, and what each one does to the stack.
#: Pinned to the spellings of `do_savepoint`, `do_release_savepoint` and `do_rollback_to_savepoint`
#: (SQLAlchemy 2.0.25, `DefaultDialect`), measured rather than assumed by
#: `tests/unit/test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py`.
_SAVEPOINT_PREFIXES = (
    ("ROLLBACK TO SAVEPOINT ", "rollback"),
    ("RELEASE SAVEPOINT ", "close"),
    ("SAVEPOINT ", "open"),
)

#: What a savepoint name is stripped of. SQLAlchemy quotes a savepoint through the dialect's
#: identifier preparer, so the name in the statement may be quoted on some dialect even though it is
#: not on these two.
_SAVEPOINT_QUOTES = "\"`[]"


def _savepoint_statement(statement: str) -> tuple[str, str] | None:
    """`(what it does, which savepoint)` for a savepoint statement, else None."""

    # THE FIRST CHARACTER BEFORE ANY COPYING. This runs on EVERY statement the process sends, and
    # `lstrip()` copies the whole string - which for a batched INSERT is not free. Only a statement
    # that starts with whitespace pays for the copy, and only one starting with S or R goes further.
    first = statement[:1]
    if first.isspace():
        head = statement.lstrip()
        first = head[:1]
    else:
        head = statement
    if first not in ("S", "R", "s", "r"):
        return None
    upper = head[:24].upper()
    for prefix, kind in _SAVEPOINT_PREFIXES:
        if upper.startswith(prefix):
            return kind, head.strip().rsplit(None, 1)[-1].strip(_SAVEPOINT_QUOTES)
    return None


def _observe_savepoint(state: _TxState, kind: str, name: str) -> None:
    """Keep `savepoints_open` as the SQL stream leaves it.

    A `RELEASE` and a `ROLLBACK TO` both end every savepoint established AFTER their own, which is
    SQL's rule and not a convenience: those savepoints get no statement of their own, so a stack that
    kept them would report a lost close for every nested savepoint in an ordinary rollback. `ROLLBACK
    TO` leaves its own savepoint usable in SQL but finished as far as SQLAlchemy is concerned - it
    issues no `RELEASE` afterwards - and this stack exists to be compared against SQLAlchemy's
    nesting, so it follows SQLAlchemy.
    """

    if kind == "open":
        if name not in state.savepoints_open:
            state.savepoints_open.append(name)
        return
    if name in state.savepoints_open:
        del state.savepoints_open[state.savepoints_open.index(name) :]


def _lost_savepoint_closes(conn: Connection, state: _TxState, ops: list[_OpRecord]) -> list[str]:
    """Savepoints an operation is bound to that left SQLAlchemy's nesting with no statement.

    THE EVENT-INDEPENDENT HALF OF T1532, and the one that reaches what the inversion above does not.
    Measured 2026-09-13: a `rollback_savepoint` listener on the `Connection` CLASS that raises runs
    before every registration this module can make, so `pending_savepoint_rollbacks` stayed empty AND
    the `ROLLBACK TO SAVEPOINT` statement was never issued - there was no SQL to observe, the
    inversion had nothing to invert, SQLAlchemy deactivated the nested transaction in its `finally`
    anyway, and the root commit made a debt of 42 durable that the writer had asked to undo.

    What is left observable in that state is exactly this: the SQL stream shows the savepoint OPEN, an
    operation is bound to it, and SQLAlchemy no longer holds a nested transaction for it. No event is
    consulted, so no listener can take it away; a neighbour can only reach it by stopping the
    `SAVEPOINT` statement from being observed, which means stopping it from running.
    """

    if not state.savepoints_open:
        return []
    bound = {name for op in ops for name in op.chain}
    if not bound:
        return []
    live = set(_core_chain(conn))
    return [name for name in state.savepoints_open if name in bound and name not in live]


def _on_after_cursor_execute(
    conn: Connection,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    """Keep the savepoint account from the SQL, not from the events.

    This is the other half of condition 1. `do_savepoint`, `do_release_savepoint` and
    `do_rollback_to_savepoint` all issue their statements through `exec_driver_sql`, which fires the
    cursor events (measured on pysqlite and aiosqlite), so a savepoint statement that actually ran is
    observable and one that was prevented is not - which is the whole asymmetry T1532 rests on.
    """

    # The cheapest possible test first: this listener sees EVERY statement, and all but a handful
    # are not the ones it is waiting for.
    observed = _savepoint_statement(statement)
    if observed is None:
        return
    kind, name = observed
    # `create=True`: a transaction that opens a savepoint before it opens an operation would
    # otherwise have no state to record the `SAVEPOINT` in, and the account would start in the
    # middle. An empty state refuses nothing - `_lost_savepoint_closes` needs an operation bound to
    # the name - so creating it costs a weak dictionary entry and buys the beginning of the stack.
    state = _state_for(conn, create=True)
    if state is None:
        return
    _observe_savepoint(state, kind, name)
    if kind == "rollback":
        _confirm_savepoint_rollback(state, name)


def _on_before_execute(
    conn: Connection,
    clauseelement: Any,
    multiparams: Any,
    params: Any,
    execution_options: Any,
) -> None:
    """Refuse every write into `debts` and the journal tables that was not verified.

    Runs on EVERY statement, including SELECTs, because a DML CTE hides inside one.

    TWO BLIND SPOTS, AND THEY ARE NOT THE SAME ONE - the sentence that used to stand here said they
    were, and it was wrong about the mechanism (T1531, measured on SQLAlchemy 2.0.25):

    * `exec_driver_sql` dispatches no `before_execute` at all, so a write issued that way is not seen
      by this guard. That is the real exception, and it is the one the journal's own verification reads
      now use deliberately.
    * `text()` DOES dispatch `before_execute`, as a `TextClause`. A `text()` write is nevertheless
      unseen here, because `_dml_tables` recognises only `UpdateBase` - the conclusion is the same and
      the reason is different, which is exactly the kind of correct-conclusion-under-a-false-premise
      this programme keeps finding in itself.

    Neither is claimed to be covered. `tests/unit/test_p015_t1531_*` measures both halves, so the
    sentence above cannot drift from the library again.
    """

    if _stood_down(conn.engine):
        return

    tables = _dml_tables(clauseelement)
    if not tables:
        return

    journal_targets = tables & DEBT_JOURNAL_TABLE_NAMES
    if journal_targets and not _INTERNAL.get(conn):
        raise DebtJournalError(
            Reason.JOURNAL_TABLE_WRITE,
            f"{', '.join(sorted(journal_targets))} may only be written by the journal itself; "
            f"these tables are the record of what the writers did and are not a writer's scratch "
            f"space.",
            tables=sorted(journal_targets),
        )

    if DEBT_TABLE_NAME not in tables:
        return

    grant = _GRANTS.get(conn)
    effect = _statement_effect(clauseelement)
    rows = _rows_of(multiparams, params)
    if grant is None or effect is None or not rows:
        _poison_connection(conn, Reason.UNVERIFIED_DEBT_WRITE)
        raise DebtJournalError(
            Reason.UNVERIFIED_DEBT_WRITE,
            f"a {type(clauseelement).__name__} against {DEBT_TABLE_NAME} reached the connection "
            f"outside a verified flush. Money moves through the ORM inside a debt operation, or "
            f"it does not move.",
            statement=type(clauseelement).__name__,
        )
    declared = _statement_values(clauseelement)
    for row in rows:
        if not _matches_any(effect, row, declared, grant):
            _poison_connection(conn, Reason.UNVERIFIED_DEBT_WRITE)
            raise DebtJournalError(
                Reason.UNVERIFIED_DEBT_WRITE,
                f"a {effect} row against {DEBT_TABLE_NAME} does not match any write the flush "
                f"hook verified. The grant names the writes that were checked, not the interval "
                f"in which the check happened.",
                statement=type(clauseelement).__name__,
            )


def _poison_connection(conn: Connection, reason: str) -> None:
    """Poison this connection's transaction, CREATING the state if there is none yet.

    A transaction that never opened an operation is exactly the one that most needs poisoning: it
    is the transaction that tried to move money with no record. Without creating the state here,
    the refusal would have nowhere to live and the caller that swallowed it would commit whatever
    else the transaction was holding.
    """

    state = _state_for(conn, create=True)
    if state is not None:
        _poison(state, reason)


# =================================================================================================
# Session-level listeners: the flush hook
# =================================================================================================


def _history(obj: Any, attribute: str) -> Any:
    return get_history(obj, attribute)


def _find_operation(session: Session, conn: Connection, state: _TxState) -> _OpRecord:
    chain = _core_chain(conn)
    session_nesting = _session_nesting(session)
    candidates = [
        op
        for op in state.ops
        # COMPLETING BELONGS HERE, and leaving it out was a defect measured on activation
        # (2026-09-12). `_complete` sets the record to COMPLETING and THEN flushes, and that flush
        # is the one that carries the block's work whenever the block did not flush for itself -
        # which is the normal shape, and the deliberate shape of `tests/debt_setup.py`, whose
        # context adds rows and never flushes so that migrating a test adds no round trip. With
        # only OPEN accepted, every such operation refused its own completion flush with
        # `no_operation`: the effects it was opened to record were the ones it could not see.
        # The window is narrow and single-occupant - a record is COMPLETING only inside `_complete`,
        # between its own two statements - so nothing else can attach to it.
        if op.state in (_OpRecord.OPEN, _OpRecord.COMPLETING)
        and not op.is_orphaned
        and op.session is session
        and chain[: len(op.chain)] == op.chain
        and (op.session_boundary is None or op.session_boundary in session_nesting)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise DebtJournalError(
            Reason.NO_OPERATION,
            "a Debt was created, changed or deleted with no debt operation covering this session "
            "in this database transaction. Every movement of money names the operation that moved "
            "it.",
        )
    raise DebtJournalError(
        Reason.NESTED_OPERATION,
        f"{len(candidates)} open debt operations cover this flush; effects would be ambiguous.",
    )


def _session_nesting(session: Session) -> list[Any]:
    """Every SessionTransaction from the innermost one outwards."""

    chain: list[Any] = []
    transaction = session.get_transaction()
    nested = session.get_nested_transaction()
    current = nested if nested is not None else transaction
    while current is not None:
        chain.append(current)
        current = current.parent
    return chain


def _signature_of(
    kind: str,
    debt_id: uuid.UUID,
    edge: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    amount_text: str,
) -> _Signature:
    """One verified write, named completely: effect, row, EDGE, amount.

    Built from the same `edge` triple the journal entry is built from, so the grant and the record
    can never describe different edges - the guard's identity is exactly as wide as the thing it
    guards. `amount_text` is `_money_text(after)` for money that moved, `""` for a delete (which has
    no amount after it) and `_NO_MONEY_MOVED` for an update that changes no money at all.
    """

    equivalent_id, debtor_id, creditor_id = edge
    return (
        kind,
        str(debt_id),
        _key_text(equivalent_id) or str(equivalent_id),
        _key_text(debtor_id) or str(debtor_id),
        _key_text(creditor_id) or str(creditor_id),
        amount_text,
    )


def _effects_of_flush(
    session: Session,
    op: _OpRecord,
    dialect: Any,
    ordinal: int,
) -> tuple[list[_Effect], dict[_Signature, int], list[_RowState]]:
    """Read this flush's Debt movements out of the unit of work, before any SQL is sent.

    Three things come out of one reading, and they must come out of ONE reading: the entries that go
    into the journal, the grant that lets the statements through, and the row states `_reconcile`
    will hold the database to. Building any of them separately would let the record, the permission
    and the verification describe different things.
    """

    effects: list[_Effect] = []
    expected: dict[_Signature, int] = {}
    states: list[_RowState] = []
    seen_edges: set[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = set()

    plan: list[tuple[Debt, str]] = []
    plan.extend((obj, "I") for obj in session.new if isinstance(obj, Debt))
    plan.extend((obj, "D") for obj in session.deleted if isinstance(obj, Debt))
    plan.extend(
        (obj, "U")
        for obj in session.dirty
        if isinstance(obj, Debt) and session.is_modified(obj, include_collections=False)
    )

    for obj, kind in plan:
        debt_id = obj.id
        if debt_id is None and kind == "I":
            # THE PRIMARY KEY IS MATERIALISED HERE, and the alternative was to refuse (measured
            # 2026-09-12, activating the journal). `Debt.id` carries a PYTHON-SIDE default
            # (`default=uuid.uuid4`, app/db/models/debt.py:11), and a Python-side default is
            # applied when the INSERT is built - which is AFTER this hook. So every writer that
            # constructs `Debt(...)` without naming an id - `_apply_flow` at
            # app/core/payments/engine.py:1566 among them - reaches `before_flush` with `obj.id`
            # still `None`, and the refusal below would have fired on the most ordinary payment in
            # the system.
            #
            # Assigning it is exactly what the ORM is about to do, a moment later and with the same
            # function; `before_flush` is the documented place to modify the objects of a flush.
            # Doing it here is what lets the journal entry and the `debts` row carry the SAME id,
            # which is the whole point of recording the edge by its key.
            obj.id = debt_id = uuid.uuid4()
        if debt_id is None:
            raise DebtJournalError(
                Reason.INCOMPLETE_DEBT,
                "a Debt reached the flush with no primary key; the journal cannot name the edge "
                "it belongs to.",
            )
        key_values: dict[str, uuid.UUID] = {}
        for column in _KEY_COLUMNS:
            history = _history(obj, column)
            value = getattr(obj, column)
            if value is None:
                raise DebtJournalError(
                    Reason.INCOMPLETE_DEBT,
                    f"a Debt reached the flush with {column} unset - typically because it was "
                    f"keyed only through a relationship. The journal records edges, and an edge "
                    f"with a missing end is not one.",
                    debt_id=str(debt_id),
                )
            if kind != "I" and history.deleted:
                raise DebtJournalError(
                    Reason.KEY_FIELD_CHANGED,
                    f"a stored Debt's {column} was changed from {history.deleted[0]} to {value}. "
                    f"An edge is its identity: moving a debt to another edge is a delete and an "
                    f"insert, and each has to be recorded as one.",
                    debt_id=str(debt_id),
                )
            if kind != "I" and history.added:
                # THE ASSIGNMENT WITH NO PREVIOUS VALUE, and the check above cannot see it (measured
                # 2026-09-13, T1527, review item 1). `get_history` reports `deleted` only when the
                # attribute's committed value is LOADED; after `session.expire(debt,
                # ["creditor_id"])` - or after any commit on an `expire_on_commit` session - an
                # assignment leaves `added=[new]`, `deleted=()` and `unchanged=()`. The check above
                # then passed and the hook recorded the effect on the NEW edge with the OLD edge's
                # amounts: the old edge silently lost its balance with no entry at all, and the new
                # edge was recorded as moving 10 -> 11 when it had in fact moved 0 -> 11. Criterion
                # (a) is then false on BOTH edges, and widening the grant cannot repair it - the
                # entry and the row agree, because the hook had already written a false history.
                #
                # This is the same fail-closed rule the amount already follows a few lines below
                # ("the previous value is not in the session's history, so the entry would have to
                # invent one"), applied to the column that decides WHOSE money it is.
                raise DebtJournalError(
                    Reason.KEY_FIELD_CHANGED,
                    f"a stored Debt's {column} was assigned ({value}) and its previous value is "
                    f"not in the session's history - typically because the attribute had been "
                    f"expired. The journal cannot tell whether the edge moved, and an edge that "
                    f"may have moved cannot be recorded as one that did not.",
                    debt_id=str(debt_id),
                )
            key_values[column] = value

        edge = (
            key_values["equivalent_id"],
            key_values["debtor_id"],
            key_values["creditor_id"],
        )
        if edge in seen_edges:
            raise DebtJournalError(
                Reason.SAME_EDGE_TWICE,
                f"edge {edge} moves twice in one flush; the two movements cannot be told apart "
                f"in the record.",
            )

        if op.scope_equivalent_ids is not None and edge[0] not in op.scope_equivalent_ids:
            raise DebtJournalError(
                Reason.OUT_OF_SCOPE,
                f"operation {op.kind}/{op.identity} declared it would touch "
                f"{sorted(str(x) for x in op.scope_equivalent_ids)} and moved money in {edge[0]}.",
                equivalent_id=str(edge[0]),
            )

        amount_history = _history(obj, "amount")
        current = _as_decimal(getattr(obj, "amount", None))

        if kind == "I":
            before, after = None, current
        elif kind == "D":
            if amount_history.deleted:
                before = _as_decimal(amount_history.deleted[0])
            elif amount_history.unchanged:
                before = _as_decimal(amount_history.unchanged[0])
            else:
                raise DebtJournalError(
                    Reason.MISSING_HISTORY,
                    f"Debt {debt_id} is being deleted and its previous amount is not in the "
                    f"session's history, so the entry would have to invent one.",
                    debt_id=str(debt_id),
                )
            after = None
        else:
            if not amount_history.has_changes():
                # The row is dirty for something that is not money (a version bump, a timestamp).
                # No money moved, so there is no entry - but the WRITE still has to be granted, or
                # the guard would refuse a statement the hook itself approved.
                unchanged = _signature_of("U", debt_id, edge, _NO_MONEY_MOVED)
                expected[unchanged] = expected.get(unchanged, 0) + 1
                seen_edges.add(edge)
                # AND THE AMOUNT IS STILL CLAIMED, which is the whole of T1528 review item 1: "no
                # money moved" is a statement about the row that the row can be held to. The amount
                # the hook READ is what must still be there afterwards.
                states.append(
                    _RowState(
                        debt_id=debt_id,
                        equivalent_id=edge[0],
                        debtor_id=edge[1],
                        creditor_id=edge[2],
                        amount=current,
                        present=True,
                    )
                )
                continue
            if amount_history.deleted:
                before = _as_decimal(amount_history.deleted[0])
            elif amount_history.unchanged:
                before = _as_decimal(amount_history.unchanged[0])
            else:
                raise DebtJournalError(
                    Reason.MISSING_HISTORY,
                    f"Debt {debt_id}'s amount changed and the previous value is not in the "
                    f"session's history; `amount_before` would be a guess.",
                    debt_id=str(debt_id),
                )
            after = current

        _check_storable(before, dialect, what=f"Debt {debt_id} amount_before")
        _check_storable(after, dialect, what=f"Debt {debt_id} amount_after")
        delta = (after or Decimal(0)) - (before or Decimal(0))
        _check_storable(delta, dialect, what=f"Debt {debt_id} delta")
        if delta == 0:
            raise DebtJournalError(
                Reason.MISSING_HISTORY,
                f"Debt {debt_id} reports a change of zero; an effect that moves nothing is a "
                f"history the journal could not have produced.",
                debt_id=str(debt_id),
            )

        seen_edges.add(edge)
        effects.append(
            _Effect(
                flush_ordinal=ordinal,
                debt_id=debt_id,
                equivalent_id=edge[0],
                debtor_id=edge[1],
                creditor_id=edge[2],
                effect=kind,
                amount_before=before,
                amount_after=after,
                delta=delta,
            )
        )
        signature = _signature_of(
            kind, debt_id, edge, "" if kind == "D" else _money_text(after)
        )
        expected[signature] = expected.get(signature, 0) + 1
        states.append(
            _RowState(
                debt_id=debt_id,
                equivalent_id=edge[0],
                debtor_id=edge[1],
                creditor_id=edge[2],
                amount=after,
                present=kind != "D",
            )
        )

    return effects, expected, states


def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    if not any(
        isinstance(obj, Debt)
        for obj in list(session.new) + list(session.deleted) + list(session.dirty)
    ):
        return

    conn = session.connection()
    if _stood_down(conn.engine):
        return
    # Created rather than looked up: a transaction with no operation in it is the one this hook
    # exists for, and its refusal has to be remembered somewhere.
    state = _state_for(conn, create=True)
    if state is None:
        # No Core transaction at all under a session that is flushing Debts. Nothing here can be
        # recorded and nothing can be rolled back, so this is a refusal and not a pass-through.
        raise DebtJournalError(
            Reason.NO_OPERATION,
            "a Debt was flushed on a connection with no database transaction open.",
        )
    if state.poison is not None:
        raise DebtJournalError(
            Reason.ROOT_POISONED,
            f"this transaction was poisoned earlier ({state.poison}) and may not write money "
            f"again until it is rolled back.",
        )
    if instances is not None:
        _poison(state, Reason.PARTIAL_FLUSH)
        raise DebtJournalError(
            Reason.PARTIAL_FLUSH,
            "a partial flush (`session.flush([...])`) carries Debt changes. A partial flush hides "
            "the rest of the unit of work from the hook, so the entries would describe a subset "
            "of what the transaction is about to do.",
        )

    try:
        op = _find_operation(session, conn, state)
        ordinal = op.flush_count + 1
        effects, expected, states = _effects_of_flush(session, op, conn.engine.dialect, ordinal)
    except DebtJournalError as exc:
        _poison(state, exc.reason)
        raise

    op.flush_count = ordinal
    op.effects.extend(effects)
    _GRANTS[conn] = _Grant(session_id=id(session), expected=expected)
    session.info["geo.journal.flush"] = (op, effects, states)


# =================================================================================================
# The journal's own verification reads: hand-written SQL through `exec_driver_sql` (T1531)
# =================================================================================================


#: How each DBAPI paramstyle spells its Nth placeholder, and whether the values travel as a sequence
#: or as a mapping. MEASURED, not assumed (2026-09-13): aiosqlite is `qmark`, asyncpg is
#: `numeric_dollar`. The other four are here because a dialect this module has never run on must
#: either be spelled correctly or REFUSED - a verification read that silently binds nothing would be
#: the vacuous guard `AGENTS.md` §9 forbids.
_PARAMSTYLES = {
    "qmark": ("?", False),
    "format": ("%s", False),
    "numeric_dollar": ("$%d", False),
    "numeric": (":%d", False),
    "named": (":p%d", True),
    "pyformat": ("%%(p%d)s", True),
}


def _raw_params(dialect: Any, values: list[Any]) -> tuple[list[str], Any]:
    """Placeholders for this dialect, and the parameters in the shape its DBAPI wants."""

    spelling = _PARAMSTYLES.get(dialect.paramstyle)
    if spelling is None:
        raise DebtJournalError(
            Reason.UNREADABLE_VERIFICATION,
            f"paramstyle {dialect.paramstyle!r} is one this module cannot write a verification read "
            f"for, so it cannot verify its own record on this dialect and will not pretend to.",
            paramstyle=dialect.paramstyle,
        )
    template, by_name = spelling
    if by_name:
        return (
            [template % index for index in range(len(values))],
            {f"p{index}": value for index, value in enumerate(values)},
        )
    marks = [
        template % (index + 1) if "%d" in template else template for index in range(len(values))
    ]
    return marks, tuple(values)


def _bind_as(column: Any, value: Any, dialect: Any) -> Any:
    """One value spelled the way this dialect's DBAPI expects it for THAT column.

    THE SPELLING TRAP, AND WHY THE COLUMN'S OWN PROCESSOR ANSWERS IT. `Uuid(as_uuid=True)` is 32 hex
    characters on SQLite and a native `uuid` on PostgreSQL, and this programme has already produced
    two false greens from a comparison written against one tier (`_key_text`). `exec_driver_sql`
    sends parameters to the DBAPI with NO bind processing of its own, so the processing has to happen
    here - and it is taken from the column's own type rather than written out, so the statement binds
    exactly what `conn.execute` would have bound.
    """

    impl = column.type.dialect_impl(dialect)
    processor = impl.bind_processor(dialect)
    return processor(value) if processor is not None else value


def _money_out(value: Any, dialect: Any) -> Decimal | None:
    """A money column's RAW DBAPI value as the `Decimal` `conn.execute` would have produced.

    Built from the column type's own result processor for the same reason `_round_trip` is: on SQLite
    `Numeric` comes back as a `float` and the scale-8 decimal processor is what makes it money again,
    so reading the raw value and comparing it would compare floats. asyncpg's processor refuses to be
    built without a result-set column type and returns `Decimal` untouched anyway, which is the
    `None` branch here and is measured end to end on the PostgreSQL tier.
    """

    if value is None:
        return None
    impl = Numeric(20, 8).dialect_impl(dialect)
    try:
        processor = impl.result_processor(dialect, None)
    except Exception:  # noqa: BLE001 - see the docstring: asyncpg is the dialect that raises
        processor = None
    return _as_decimal(processor(value) if processor is not None else value)


def _own_select(
    dialect: Any, table: Any, columns: tuple[Any, ...], *, where: str, binds: list[tuple[Any, Any]]
) -> tuple[str, Any]:
    """The SQL and parameters of one verification read. `where` spells placeholders as `{0}`, `{1}`...

    WHY HAND-WRITTEN SQL AT ALL (T1531). A `select()` executed through `conn.execute` dispatches
    `before_execute`, where a neighbour registered after this module's listener can replace the
    statement outright - measured 2026-09-13: the projection of `debts.amount` was replaced by the
    literal 11 while the row held 12, the journal recorded 11, and nothing refused. `exec_driver_sql`
    dispatches NO `before_execute` at all (measured on SQLAlchemy 2.0.25, both the `Engine` and the
    `Connection` class), so the statement that reaches the cursor is the one written here.

    WHAT IT DOES NOT REACH, and this is the honest half. `exec_driver_sql` still dispatches
    `before_cursor_execute`, which a neighbour may register with `retval=True` and use to replace the
    SQL string and the parameters. That surface is NARROWER - it is raw SQL rather than a typed clause
    object, and the oracle that used to point at this statement is gone (see `_OWN`) - but it is not
    closed, and nothing here may be described as closing it. It is measured as still open by
    `tests/unit/test_p015_t1531_the_verification_read_is_not_rewritable.py`.
    """

    preparer = dialect.identifier_preparer
    values = [_bind_as(column, value, dialect) for column, value in binds]
    marks, params = _raw_params(dialect, values)
    projection = ", ".join(preparer.quote(column.name) for column in columns)
    sql = (
        f"SELECT {projection} FROM {preparer.format_table(table)} "
        f"WHERE {where.format(*marks)}"
    )
    return sql, params


def _same_money(written: Decimal | None, recorded: Decimal | None) -> bool:
    """Whether two money values are the same number, whatever spelling each arrived in.

    `Decimal("10.0") == Decimal("10.00000000")` is already True, so no quantization is needed - and
    quantization must be AVOIDED here, because it raises on values the database can nevertheless be
    holding (a NaN from before T1526, a magnitude SQLite never refused). Those compare by their text,
    so they are comparable without being storable.
    """

    if written is None or recorded is None:
        return written is None and recorded is None
    if written.is_finite() and recorded.is_finite():
        return written == recorded
    return str(written) == str(recorded)


def _reconcile(conn: Connection, states: list[_RowState]) -> None:
    """Read the rows this flush claims to have recorded BACK OUT OF THE DATABASE, and compare.

    THIS IS THE BOUNDARY THE CONTRACT NEEDED, and the three holes of T1528 are what proved that no
    amount of parameter reading could be it. The write guard inspects a statement BEFORE it executes:
    it sees the parameters it was handed, in the listener order it happens to occupy, and it has to
    decide what they mean. Every one of those is a place to be wrong -

    * a value that is a SQL expression is not in the parameters at all (review items 1, 2);
    * a value's meaning read from its TYPE is a guess about which number is the amount (item 3);
    * and a `before_execute` listener registered after this module's - an `Engine`-instance listener,
      which SQLAlchemy runs after every class-level one - changes the parameters AFTER verification,
      so the guard's answer was about a statement that no longer exists (item 5, write-guard half).

    What this does instead asks the database. After the flush's SQL has run, every row the journal
    claims is read back by primary key and required to be exactly what the entries say: present or
    gone, on the edge the entry names, holding the amount the entry ends on. No inference, no
    listener order, no statement shape. A row that differs means the record and the table disagree,
    and a transaction whose record is false is refused before its entries are even written.

    WHAT IT DOES NOT CLAIM. It is not a second opinion on somebody else's rows: only the rows this
    flush recorded are read, so a write to a DIFFERENT row is the parameter-level guard's business
    and stays so - including the retargeting case registered as its own finding (an authorised
    metadata-only UPDATE of debt A turned into a money UPDATE of debt B: A still satisfies its
    `_RowState`, and B is not among the rows this flush recorded). And it runs at the END OF THIS
    FLUSH, so a change made after it - by a later listener using `exec_driver_sql`, which dispatches no
    `before_execute`, or by a `before_cursor_execute` neighbour rewriting this very read - is outside
    it. Those exposures are reported, not claimed away.

    ONE STATEMENT, whatever the flush's size: a single `IN` read, not one per row.
    """

    if not states:
        return
    table = Debt.__table__
    by_id: dict[str, _RowState] = {}
    for state in states:
        key = str(state.debt_id)
        if key in by_id:
            # One flush naming the same primary key twice cannot be held to either claim, and
            # nothing in the ORM produces it (the identity map admits one object per key). Refuse
            # rather than pick.
            raise DebtJournalError(
                Reason.UNRECONCILED_DEBT_ROW,
                f"this flush recorded two different states for debt {key}; neither can be verified "
                f"against the stored row.",
                debt_id=key,
            )
        by_id[key] = state

    dialect = conn.engine.dialect
    columns = (
        table.c.id,
        table.c.equivalent_id,
        table.c.debtor_id,
        table.c.creditor_id,
        table.c.amount,
    )
    # THROUGH `exec_driver_sql`, WHICH DISPATCHES NO `before_execute` (T1531). The statement used to
    # be a `select()` through `conn.execute`, where a neighbour could replace it - measured, with the
    # amount projection swapped for a literal while the row held something else - and it used to
    # carry an execution option that was an exact oracle for FINDING it. Neither is true now; what
    # remains open is `before_cursor_execute`, and `_own_select` says so.
    sql, params = _own_select(
        dialect,
        table,
        columns,
        where=table.c.id.name
        + " IN ("
        + ", ".join("{%d}" % index for index in range(len(states)))
        + ")",
        binds=[(table.c.id, state.debt_id) for state in states],
    )
    with _journal_read(conn):
        rows = conn.exec_driver_sql(sql, params).fetchall()
    stored = {_key_text(row[0]) or str(row[0]): row for row in rows}

    problems: list[str] = []
    for key, state in by_id.items():
        row = stored.get(key)
        if not state.present:
            if row is not None:
                problems.append(f"{key} was recorded as deleted and is still in `debts`")
            continue
        if row is None:
            problems.append(f"{key} was recorded as written and is not in `debts`")
            continue
        written_edge = (_key_written(row[1]), _key_written(row[2]), _key_written(row[3]))
        recorded_edge = (
            _key_written(state.equivalent_id),
            _key_written(state.debtor_id),
            _key_written(state.creditor_id),
        )
        if written_edge != recorded_edge:
            problems.append(
                f"{key} is stored on edge {written_edge} and was recorded on {recorded_edge}"
            )
        if not _same_money(_money_out(row[4], dialect), state.amount):
            problems.append(f"{key} holds {row[4]!r} and was recorded as {state.amount!r}")
    if not problems:
        return
    _poison_connection(conn, Reason.UNRECONCILED_DEBT_ROW)
    raise DebtJournalError(
        Reason.UNRECONCILED_DEBT_ROW,
        "the database does not hold what this flush recorded: "
        + "; ".join(problems)
        + ". The journal will not write a record of a movement that did not happen as recorded.",
        problems=problems,
    )


def _money_key(value: Decimal | None) -> str:
    """One money value as a comparison key. Tolerates what `_money_text` refuses to quantize.

    `_money_text` is the digest's encoding and it quantizes, which RAISES on a value the database
    can nevertheless be holding - a NaN from before T1526, a magnitude SQLite never refused. A
    comparison that raises on the very row it is meant to catch would turn a refusal into a crash
    under a different name, so a non-finite value compares by its text (the same split `_same_money`
    makes, and for the same reason).
    """

    if value is None:
        return ""
    if not value.is_finite():
        return str(value)
    return _money_text(value)


def _effect_code(effect: _Effect) -> tuple[Any, ...]:
    """The entry the journal INTENDS to have stored for one effect, as a comparable tuple."""

    return (
        int(effect.flush_ordinal),
        _key_text(effect.equivalent_id) or str(effect.equivalent_id),
        _key_text(effect.debtor_id) or str(effect.debtor_id),
        _key_text(effect.creditor_id) or str(effect.creditor_id),
        str(effect.effect),
        _money_key(effect.amount_before),
        _money_key(effect.amount_after),
        _money_key(effect.delta),
    )


#: The columns an entry is compared on, in the order `_stored_entry_code` reads them. Named once so
#: the read and the encoding cannot drift apart.
_ENTRY_COLUMNS = (
    debt_journal_entries.c.flush_ordinal,
    debt_journal_entries.c.equivalent_id,
    debt_journal_entries.c.debtor_id,
    debt_journal_entries.c.creditor_id,
    debt_journal_entries.c.effect,
    debt_journal_entries.c.amount_before,
    debt_journal_entries.c.amount_after,
    debt_journal_entries.c.delta,
)


def _stored_entry_code(row: Any, dialect: Any) -> tuple[Any, ...]:
    """The same tuple, built from the row AS THE DATABASE HOLDS IT."""

    return (
        int(row[0]),
        _key_text(row[1]) or str(row[1]),
        _key_text(row[2]) or str(row[2]),
        _key_text(row[3]) or str(row[3]),
        str(row[4]),
        _money_key(_money_out(row[5], dialect)),
        _money_key(_money_out(row[6], dialect)),
        _money_key(_money_out(row[7], dialect)),
    )


def _entry_read(dialect: Any, *, operation_id: uuid.UUID, ordinal: int | None) -> tuple[str, Any]:
    """The verification read of one operation's entries, optionally narrowed to one flush."""

    table = debt_journal_entries
    where = f"{table.c.operation_id.name} = {{0}}"
    binds: list[tuple[Any, Any]] = [(table.c.operation_id, operation_id)]
    if ordinal is not None:
        where += f" AND {table.c.flush_ordinal.name} = {{1}}"
        binds.append((table.c.flush_ordinal, ordinal))
    return _own_select(dialect, table, _ENTRY_COLUMNS, where=where, binds=binds)


def _describe_entry_disagreement(
    stored: "Counter[tuple[Any, ...]]", recorded: "Counter[tuple[Any, ...]]"
) -> str:
    extra = stored - recorded
    missing = recorded - stored
    parts = []
    if extra:
        parts.append(f"stored and not recorded: {sorted(extra.elements())}")
    if missing:
        parts.append(f"recorded and not stored: {sorted(missing.elements())}")
    return "; ".join(parts)


def _verify_entries(conn: Connection, op: _OpRecord, ordinal: int, effects: list[_Effect]) -> None:
    """Read the entries this flush just wrote BACK OUT OF THE DATABASE and require them to be the
    effects the hook computed.

    THE OTHER HALF OF THE SAME DISCIPLINE AS `_reconcile`, AND IT WAS MISSING (T1530, found by the
    third review circle of the T1528 delta, 2026-09-13). `_reconcile` read the DEBT row back and the
    journal then inserted its entry and never read THAT back. The write guard passed the entry INSERT
    because `_INTERNAL` was set and compared nothing; `delta` was bounded and non-zero and was not
    required to be `after - before`; and `_complete` digested the STORED rows. So a `before_execute`
    neighbour registered after this module's could rewrite the entry INSERT, and the measurement on
    this tree was: `debts` held 11, the entry said `10 -> 12, delta 2`, the envelope completed with a
    digest taken over the tampered row, and nothing refused. That is the mirror of the hole T1528
    closed - there the table disagreed with the record, here the record disagrees with the table -
    and one is no more acceptable than the other.

    EXACT EQUALITY IN BOTH DIRECTIONS, as a multiset. A row stored that the hook never computed is a
    record of a movement that did not happen; an effect the hook computed that is not stored is a
    movement with no record. The digest must be taken over rows that have been held to the effects,
    which is why this runs before the operation can complete and not as a later audit.

    WHICH HALF IS LOAD-BEARING HERE, MEASURED AND NOT ASSUMED (2026-09-13). The `stored - recorded`
    half overlaps with `_complete`'s membership check: removing it alone changes no verdict, because
    an invented or altered row is refused one step later, at the digest. The `recorded - stored` half
    does NOT overlap with anything - a MISSING entry is legitimate at completion time (a savepoint
    rollback takes a flush's entries with it, migration 023), so this readback is the only place in
    the mechanism that can see one. Both halves are kept: the overlapping one refuses earlier and
    names the flush, and a check that is only correct because another check exists is the
    downstream-compensation reasoning `AGENTS.md` §9 forbids.

    THROUGH `exec_driver_sql` (T1531): this read must not be rewritable by the same neighbour that
    rewrote the INSERT it is checking.
    """

    dialect = conn.engine.dialect
    sql, params = _entry_read(dialect, operation_id=op.id, ordinal=ordinal)
    with _journal_read(conn):
        rows = conn.exec_driver_sql(sql, params).fetchall()
    stored = Counter(_stored_entry_code(row, dialect) for row in rows)
    recorded = Counter(_effect_code(effect) for effect in effects)
    if stored == recorded:
        return
    _poison_connection(conn, Reason.UNRECORDED_JOURNAL_ENTRY)
    raise DebtJournalError(
        Reason.UNRECORDED_JOURNAL_ENTRY,
        "the entries stored for this flush are not the effects the journal computed: "
        + _describe_entry_disagreement(stored, recorded)
        + ". A record that disagrees with the movement it describes is the one thing this "
        "mechanism exists to prevent.",
        operation_id=str(op.id),
        flush_ordinal=ordinal,
    )


def _verify_completed_entries(record: _OpRecord, stored: list[tuple[Any, ...]]) -> None:
    """Every entry stored for this operation must be one the operation COMPUTED (T1530).

    `_verify_entries` held each flush's entries to that flush's effects; this holds the whole set at
    the moment the completion digest is taken, which is the only way the digest describes verified
    rows rather than whatever the table happens to contain by then. The window between the two is
    real and not theoretical: `exec_driver_sql` dispatches no `before_execute`, so a neighbour can
    alter a stored entry after the flush that wrote it was verified.

    MEMBERSHIP AND NOT EQUALITY, and the asymmetry is measured rather than convenient: entries CAN
    legitimately be missing, because a `StaleDataError` retry in `PaymentEngine._apply_flow` rolls its
    flush back to a savepoint and takes that flush's entries with it (migration 023). Nothing, though,
    can legitimately ADD or ALTER a row - so the one-way check is the strongest one that is true here,
    and saying so is the point.

    A FUNCTION AND NOT AN INLINE BLOCK, because a guard that cannot be addressed cannot be stood down
    in the one place that has to stand every guard down in turn - the counter-check in
    `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py`, which reproduces
    `F-012-1` by disabling each guard that stands in front of it and asserting each refusal on the way.
    """

    computed = Counter(_effect_code(effect) for effect in record.effects)
    unaccounted = Counter(stored) - computed
    if not unaccounted:
        return
    raise DebtJournalError(
        Reason.UNRECORDED_JOURNAL_ENTRY,
        f"entries stored for {record.kind}/{record.identity} describe movements this operation never "
        f"computed: {sorted(unaccounted.elements())}. The completion digest will not be taken over "
        f"rows the journal cannot account for.",
        operation_id=str(record.id),
    )


def _after_flush(session: Session, flush_context: Any) -> None:
    pending = session.info.pop("geo.journal.flush", None)
    if pending is None:
        return
    op, effects, states = pending
    conn = session.connection()
    grant = _GRANTS.pop(conn, None)

    if grant is not None and grant.expected:
        _poison_connection(conn, Reason.VERIFIED_WRITE_MISSING)
        raise DebtJournalError(
            Reason.VERIFIED_WRITE_MISSING,
            f"{sum(grant.expected.values())} write(s) the hook verified never reached the "
            f"connection. The record and the table would disagree.",
        )

    # THE EXECUTION BOUNDARY, and it runs before the entries are written: an entry that describes a
    # row the database does not hold may not exist even for the length of a transaction.
    _reconcile(conn, states)

    if not effects:
        return
    rows = [
        {
            "id": uuid.uuid4(),
            "operation_id": op.id,
            "flush_ordinal": effect.flush_ordinal,
            "equivalent_id": effect.equivalent_id,
            "debtor_id": effect.debtor_id,
            "creditor_id": effect.creditor_id,
            "effect": effect.effect,
            "amount_before": effect.amount_before,
            "amount_after": effect.amount_after,
            "delta": effect.delta,
        }
        for effect in effects
    ]
    with _journal_write(conn):
        conn.execute(insert(debt_journal_entries), rows)
    # AND READ THEM BACK. The INSERT above went through the same `before_execute` pipeline as
    # everything else, so what it stored is not known until it is read (T1530).
    _verify_entries(conn, op, effects[0].flush_ordinal, effects)


def _after_flush_postexec(session: Session, flush_context: Any) -> None:
    session.info.pop("geo.journal.flush", None)
    _drop_grant(session)


def _after_soft_rollback(session: Session, previous_transaction: Any) -> None:
    session.info.pop("geo.journal.flush", None)
    _drop_grant(session)


def _drop_grant(session: Session) -> None:
    """Drop every grant this session installed, wherever it landed.

    Keyed by session identity rather than by asking the session for its connection: by the time
    `after_soft_rollback` runs, asking would AUTOBEGIN a new transaction, and the grant to remove
    belongs to the old one.
    """

    for conn in [conn for conn, grant in _GRANTS.items() if grant.session_id == id(session)]:
        _GRANTS.pop(conn, None)


# =================================================================================================
# Installation. Slice C arms this on the `Engine` and `Session` CLASSES at the bottom of this
# module; the per-target installers below remain for stands that arm or stand down one engine.
# =================================================================================================


_CONNECTION_LISTENERS = (
    ("commit", _on_commit),
    ("begin", _on_begin),
    ("engine_connect", _on_engine_connect),
    ("release_savepoint", _on_release_savepoint),
    ("rollback_savepoint", _on_rollback_savepoint),
    ("after_cursor_execute", _on_after_cursor_execute),
    ("before_execute", _on_before_execute),
)

_SESSION_LISTENERS = (
    ("before_flush", _before_flush),
    ("after_flush", _after_flush),
    ("after_flush_postexec", _after_flush_postexec),
    ("after_soft_rollback", _after_soft_rollback),
)


def _sync_engine(engine: Any) -> Engine:
    return getattr(engine, "sync_engine", engine)


#: True once `arm_journal_globally()` has registered the listeners on the `Engine` and `Session`
#: CLASSES. Slice C, the activation: before it, the journal only existed where a stand installed it.
_ARMED_GLOBALLY = False

#: Engines the journal has been stood down on, under global arming. Class-level listeners cannot be
#: removed for one engine, so "this engine is not instrumented" - a state the mechanism is required
#: to refuse an operation in, and therefore a state that has to stay REACHABLE - is carried here
#: instead. Weak, because it may not keep a disposed engine alive.
_STOOD_DOWN: "weakref.WeakSet[Engine]" = weakref.WeakSet()


def arm_journal_globally() -> None:
    """Register the journal on the `Engine` and `Session` classes, for this whole process.

    THIS IS THE ACTIVATION. Everything else in this module was built and proven by slice A without
    changing a single existing behaviour, because nothing called an installer. From here on, a row
    in `debts` may only change inside a declared operation, on every engine this process creates.

    CLASS LEVEL AND NOT PER ENGINE, for a reason measured in slice A (§1.3): class-level
    `ConnectionEvents` on `Engine` apply to engines created BEFORE the registration as well as
    after, sync and async alike. Per-engine arming would have to be repeated at every
    `create_async_engine` in the tree - the application's, the test suite's, and every scratch
    engine a test builds - and the one that was forgotten would be the one that wrote money with no
    record.

    Idempotent, and it must be: `app/db/session.py` imports at whatever moment the process first
    touches the database, and more than one importer is normal.
    """

    global _ARMED_GLOBALLY
    if _ARMED_GLOBALLY:
        return
    for name, handler in _CONNECTION_LISTENERS:
        if not event.contains(Engine, name, handler):
            event.listen(Engine, name, handler, insert=True)
    for name, handler in _SESSION_LISTENERS:
        if not event.contains(Session, name, handler):
            event.listen(Session, name, handler)
    _ARMED_GLOBALLY = True


def journal_is_armed_globally() -> bool:
    """Whether the class-level registration happened in this process."""

    return _ARMED_GLOBALLY


def _is_guarded(engine: Any) -> bool:
    """Whether this engine's statements reach the journal right now."""

    sync_engine = _sync_engine(engine)
    if sync_engine in _STOOD_DOWN:
        return False
    if _ARMED_GLOBALLY:
        return True
    return all(
        event.contains(sync_engine, name, handler) for name, handler in _CONNECTION_LISTENERS
    )


def install_write_guard(engine: Any) -> None:
    """Arm the connection-level half - the write guard and the transaction events. Idempotent.

    Under global arming this only lifts a stand-down: registering the same handlers a second time,
    on an instance whose class already carries them, would make every listener fire twice.
    """

    sync_engine = _sync_engine(engine)
    _STOOD_DOWN.discard(sync_engine)
    if _ARMED_GLOBALLY:
        return
    for name, handler in _CONNECTION_LISTENERS:
        if not event.contains(sync_engine, name, handler):
            # `insert=True`: the journal's listeners run before anything registered on this ENGINE
            # afterwards. That is not what protects the recorder from a neighbour on the CONNECTION -
            # `_on_engine_connect` is (T1528), and it is in this same list.
            event.listen(sync_engine, name, handler, insert=True)


def uninstall_write_guard(engine: Any) -> None:
    """Stand the journal down on ONE engine.

    Under global arming the listeners live on the `Engine` class and cannot be removed for a single
    instance, so the engine is recorded as stood down and every listener returns early for it. The
    observable effect is the same one this function always had - `journal_is_installed` goes False
    and an operation opened on this engine is refused as un-instrumented - which is what the tests
    that stand the journal down are about.
    """

    sync_engine = _sync_engine(engine)
    for name, handler in _CONNECTION_LISTENERS:
        if event.contains(sync_engine, name, handler):
            event.remove(sync_engine, name, handler)
    if _ARMED_GLOBALLY:
        _STOOD_DOWN.add(sync_engine)


def install_flush_hook(session_target: Any) -> None:
    """Arm the session-level half on one target. Idempotent.

    `session_target` is anything `event.listen` accepts for Session events - the `Session` class, a
    `sessionmaker`, or a single `Session`.

    ONE TARGET AT A TIME, and this is a SQLAlchemy property rather than a preference. Class-level
    Session events share a single `_ClsLevelDispatch` across the whole `Session` hierarchy, and its
    listener bookkeeping is keyed by (dispatch, function). Registering the SAME function on two
    `Session` subclasses at once therefore stores one entry, and removing it twice raises
    `KeyError` - which is why `uninstall_flush_hook` tolerates that one exception and says so.
    """

    if _ARMED_GLOBALLY:
        # Already registered on the `Session` class itself, which every target here inherits from.
        # A second registration on a subclass or a sessionmaker would double every flush hook.
        return
    for name, handler in _SESSION_LISTENERS:
        if not event.contains(session_target, name, handler):
            event.listen(session_target, name, handler)


def uninstall_flush_hook(session_target: Any) -> None:
    if _ARMED_GLOBALLY:
        # Nothing was registered on this target, and the class-level registration is not one
        # target's to remove. Standing the journal down is per ENGINE (`uninstall_write_guard`).
        return
    for name, handler in _SESSION_LISTENERS:
        if event.contains(session_target, name, handler):
            try:
                event.remove(session_target, name, handler)
            except KeyError:
                # See `install_flush_hook`: a second `Session` subclass carrying the same handler
                # already consumed the shared bookkeeping entry. The listener is gone either way;
                # raising here would only turn a teardown into a failure.
                pass


def install_journal(engine: Any, session_target: Any) -> None:
    """Arm both halves. Idempotent.

    Taking the engine and the session target as arguments, rather than listening on the `Session`
    class unconditionally, is what lets the mechanism be proven on a private stand without changing
    a single existing behaviour - which is the whole shape of step 4 slice A.
    """

    install_write_guard(engine)
    install_flush_hook(session_target)


def uninstall_journal(engine: Any, session_target: Any) -> None:
    """Remove what `install_journal` added, so a test engine leaves nothing behind."""

    uninstall_write_guard(engine)
    uninstall_flush_hook(session_target)


def journal_is_installed(engine: Any) -> bool:
    """True when the connection-level half is armed on this engine. Registration, not effect."""

    return _is_guarded(engine)


# =================================================================================================
# The operation context
# =================================================================================================


def _is_autocommit(conn: Connection) -> bool:
    """Whether this connection is in AUTOCOMMIT, INCLUDING the engine-level form.

    `create_engine(url, isolation_level="AUTOCOMMIT")` leaves `Connection._execution_options`
    EMPTY - measured 2026-09-12 - and stores the level on the dialect instead. A check that looked
    only at the execution options would therefore pass an engine on which every statement commits
    itself and no rollback undoes anything (binding acceptance condition 2).
    """

    level = conn._execution_options.get("isolation_level")
    if level is None:
        level = getattr(conn.engine.dialect, "_on_connect_isolation_level", None)
    return level == "AUTOCOMMIT"


def _refuse_unusable_transaction(conn: Connection, root: RootTransaction | None) -> None:
    if not journal_is_installed(conn.engine):
        raise DebtJournalError(
            Reason.ENGINE_NOT_INSTRUMENTED,
            "this engine has no journal write guard installed, so an operation opened on it would "
            "record what it was told and miss what it was not.",
        )
    if _is_autocommit(conn):
        raise DebtJournalError(
            Reason.AUTOCOMMIT_ROOT,
            "this connection is in AUTOCOMMIT: every statement commits itself, so the envelope, "
            "the entries and the debts would be three separate durable facts and a refusal could "
            "undo none of them.",
        )
    if isinstance(root, TwoPhaseTransaction):
        raise DebtJournalError(
            Reason.TWO_PHASE_ROOT,
            "this is a two-phase transaction. Its second phase happens after this process has "
            "stopped watching, so 'the record and the money commit together' is not something "
            "this journal can promise here.",
        )
    if conn.engine.dialect.name == "sqlite":
        if not sqlite_transaction_control_is_installed(conn.engine):
            raise DebtJournalError(
                Reason.NO_TRANSACTION_CONTROL,
                "this SQLite engine has no explicit transaction control (T1525), so a savepoint "
                "opened before the first write is its own transaction and its RELEASE commits it "
                "past any rollback. See app/db/sqlite_transaction_control.py.",
            )
        # THE SAME PROBE AS THE `begin` GUARD, and not a second reading of the same attribute
        # (T1528): two implementations of "does the driver have a transaction open" drift apart, and
        # this one had already lost the distinction between False and unmeasured that the other one
        # was being fixed for.
        if _driver_transaction_is_live(conn) is False:
            raise DebtJournalError(
                Reason.NO_TRANSACTION_CONTROL,
                "this SQLite connection has no database transaction open even though SQLAlchemy "
                "believes one began - registration of the transaction control is retroactive to "
                "neither open connections nor open transactions.",
            )


def _validate_arguments(
    kind: str,
    identity: str,
    tx_id: str | None,
    scope_equivalent_ids: Iterable[uuid.UUID] | None,
) -> None:
    if kind not in OPERATION_KINDS:
        raise DebtJournalError(
            Reason.BAD_ARGUMENT, f"unknown operation kind {kind!r}; expected one of {OPERATION_KINDS}"
        )
    if not identity:
        raise DebtJournalError(Reason.BAD_ARGUMENT, "an operation needs a non-empty identity")
    if (tx_id is not None) != (kind in OPERATION_KINDS_WITH_TX):
        raise DebtJournalError(
            Reason.BAD_ARGUMENT,
            f"kind {kind} {'requires' if kind in OPERATION_KINDS_WITH_TX else 'must not carry'} a "
            f"tx_id",
        )
    if scope_equivalent_ids is None and kind not in ("SEED", "TEST_FIXTURE"):
        raise DebtJournalError(
            Reason.BAD_ARGUMENT,
            f"kind {kind} must declare the equivalents it is allowed to touch; an unscoped "
            f"operation cannot be told from one that touched the wrong book.",
        )


@asynccontextmanager
async def debt_operation(
    session: Any,
    *,
    kind: str,
    identity: str,
    intent: Any,
    tx_id: str | None = None,
    scope_equivalent_ids: Iterable[uuid.UUID] | None = None,
    intent_equivalent_ids: Iterable[uuid.UUID] = (),
) -> AsyncIterator[_OpRecord]:
    """Declare an operation, then move money inside it.

    Everything this refuses at OPEN is a state in which the record could not be trusted afterwards:
    AUTOCOMMIT, a two-phase root, SQLite without transaction control, a poisoned transaction, an
    operation already open in the same transaction, an orphaned record, or a Debt already pending
    in the session (which would be attributed to this operation without ever having been declared).
    """

    _validate_arguments(kind, identity, tx_id, scope_equivalent_ids)
    scope = None if scope_equivalent_ids is None else frozenset(scope_equivalent_ids)
    intent_ids = frozenset(intent_equivalent_ids)
    stored_intent, digest = _intent_digest(intent)

    async_conn = await session.connection()
    conn: Connection = async_conn.sync_connection
    sync_session: Session = getattr(session, "sync_session", session)
    root = conn.get_transaction()

    _refuse_unusable_transaction(conn, root)
    if root is None:
        raise DebtJournalError(
            Reason.NO_OPERATION, "no database transaction is open on this connection"
        )

    state = _state_for(conn, create=True)
    if state is None:
        # No Core transaction at all under a session that is flushing Debts. Nothing here can be
        # recorded and nothing can be rolled back, so this is a refusal and not a pass-through.
        raise DebtJournalError(
            Reason.NO_OPERATION,
            "a Debt was flushed on a connection with no database transaction open.",
        )
    if state.poison is not None:
        raise DebtJournalError(
            Reason.ROOT_POISONED,
            f"this transaction was poisoned ({state.poison}); it must be rolled back before any "
            f"further money is written.",
        )
    for existing in state.ops:
        if existing.is_orphaned and not existing.is_settled:
            raise DebtJournalError(
                Reason.ORPHANED_OPERATION,
                f"operation {existing.kind}/{existing.identity} lost its session while this "
                f"transaction stayed open.",
            )
        if not existing.is_settled:
            raise DebtJournalError(
                Reason.NESTED_OPERATION,
                f"operation {existing.kind}/{existing.identity} is still {existing.state} in this "
                f"transaction. Operations do not nest: two open envelopes cannot divide one "
                f"flush's effects between them.",
            )
    if any(
        isinstance(obj, Debt)
        for obj in list(sync_session.new) + list(sync_session.dirty) + list(sync_session.deleted)
    ):
        raise DebtJournalError(
            Reason.INCOMPLETE_DEBT,
            "a Debt is already pending in this session. It was not declared by this operation and "
            "would be recorded as if it had been.",
        )

    state.generation += 1
    record = _OpRecord(
        kind=kind,
        identity=identity,
        tx_id=tx_id,
        intent=stored_intent,
        intent_digest=digest,
        scope_equivalent_ids=scope,
        intent_equivalent_ids=intent_ids,
        root=root,
        chain=_core_chain(conn),
        session=sync_session,
        session_boundary=sync_session.get_nested_transaction(),
        generation=state.generation,
    )
    state.ops.append(record)

    try:
        await session.flush()
        with _journal_write(conn):
            await async_conn.execute(
                insert(debt_operations).values(
                    id=record.id,
                    kind=kind,
                    identity=identity,
                    tx_id=tx_id,
                    intent=stored_intent,
                    intent_digest=digest,
                    schema_version=SCHEMA_VERSION,
                    money_encoding_version=SCHEMA_VERSION,
                    intent_encoding_version=SCHEMA_VERSION,
                    opened_at=datetime.now(timezone.utc),
                    state="OPEN",
                )
            )
    except BaseException:
        # A failure of the journal's OWN machinery, not of the caller's work: the transaction can
        # no longer be trusted to be recorded, so it is poisoned rather than left to continue.
        _poison(state, "operation open failed")
        record.state = _OpRecord.POISONED
        raise

    record.state = _OpRecord.OPEN
    try:
        yield record
    except BaseException:
        # A business failure inside the block. The operation is discarded, NOT the whole root: a
        # staged payment rejected inside its own savepoint must not kill its siblings. If the
        # savepoint it is bound to rolls back, the record goes with it; otherwise the commit that
        # would cover it is refused.
        record.state = _OpRecord.POISONED
        raise
    await _complete(session, conn, state, record)


async def _complete(session: Any, conn: Connection, state: _TxState, record: _OpRecord) -> None:
    """Close the envelope, or refuse and poison the root.

    Everything checked here is a way for the block to have exited somewhere other than where it
    started: a different transaction, a different savepoint depth, a record that was dropped by a
    rollback and re-registered by someone else. Completing from any of those would attach this
    operation's effects to a transaction that did not produce them.
    """

    async_conn = await session.connection()
    live_conn: Connection = async_conn.sync_connection
    sync_session: Session = getattr(session, "sync_session", session)

    problems = []
    if record not in state.ops:
        problems.append("the record is no longer registered in this transaction")
    if record.state != _OpRecord.OPEN:
        problems.append(f"the record is {record.state}, not OPEN")
    if record.root is not live_conn.get_transaction():
        problems.append("the database transaction is not the one the operation opened in")
    if _core_chain(live_conn) != record.chain:
        problems.append("the savepoint chain is not the one the operation opened in")
    if record.session_boundary is not sync_session.get_nested_transaction():
        problems.append("the session's nesting is not the one the operation opened in")
    if problems:
        _poison(state, Reason.STALE_OPERATION_HANDLE)
        raise DebtOperationIncomplete(
            Reason.STALE_OPERATION_HANDLE,
            "refusing to complete the operation: " + "; ".join(problems),
        )

    record.state = _OpRecord.COMPLETING
    try:
        await session.flush()
        # THE SAME READ AS `_verify_entries`, THROUGH THE SAME DOOR (T1531): `exec_driver_sql`,
        # which dispatches no `before_execute`. The digest below is taken over what this returns, so
        # a rewritable read here would mean a digest over whatever a neighbour chose to show.
        dialect = live_conn.engine.dialect
        sql, params = _entry_read(dialect, operation_id=record.id, ordinal=None)
        with _journal_read(live_conn):
            rows = (await async_conn.exec_driver_sql(sql, params)).fetchall()
        encoded_rows = [_stored_entry_code(row, dialect) for row in rows]

        _verify_completed_entries(record, encoded_rows)

        encoded = sorted(encoded_rows, key=lambda code: (code[0], code[1], code[2], code[3]))
        ordered = [
            (code[0], uuid.UUID(code[1]), code[2], code[3]) for code in encoded
        ]

        per_equivalent: dict[uuid.UUID, list[tuple[Any, ...]]] = {}
        for row, code in zip(ordered, encoded):
            per_equivalent.setdefault(row[1], []).append(code)
        for equivalent_id in record.intent_equivalent_ids:
            per_equivalent.setdefault(equivalent_id, [])

        # STEP 5a: A SEED OR TEST_FIXTURE WRITE AFTER THE BASELINE IS REFUSED, never recorded. It is
        # read from the STORED entries above, so it covers every flush of the operation. Raising here
        # poisons the root through the `except` below, so the commit that would carry it is refused.
        # Under PostgreSQL SERIALIZABLE a baseline taken concurrently with such an operation cannot
        # commit alongside it: each transaction reads what the other writes, and one of the two gets
        # `40001` (measured in `tests/integration/test_p015_step5a_reconciliation_postgres.py`).
        if record.kind in _PRE_BASELINE_ONLY_KINDS:
            touched = sorted(
                (equivalent_id for equivalent_id, codes in per_equivalent.items() if codes),
                key=lambda value: value.bytes,
            )
            if touched:
                # THE JOURNAL'S OWN READ, through the same door as its other verification reads
                # (`_own_select` + `exec_driver_sql` under `_journal_read`): no `before_execute`
                # neighbour can rewrite it, and `journal_statement_is_own` answers for it, which is what
                # keeps "arming adds the journal's own statements and nothing else" (R4) true.
                # Found by the SQLite gate on 2026-09-14, when this was a plain `conn.execute(select)`.
                column = debt_reconciliation_baselines.c.equivalent_id
                sql, params = _own_select(
                    dialect,
                    debt_reconciliation_baselines,
                    (column,),
                    where=f"{column.name} IN ({', '.join('{%d}' % i for i in range(len(touched)))})",
                    binds=[(column, equivalent_id) for equivalent_id in touched],
                )
                with _journal_read(live_conn):
                    baselined = [
                        row[0] for row in (await async_conn.exec_driver_sql(sql, params)).fetchall()
                    ]
                if baselined:
                    raise DebtJournalError(
                        Reason.UNVERIFIABLE_WRITER_AFTER_BASELINE,
                        f"{record.kind} operation {record.identity} changed debts in "
                        f"{len(baselined)} equivalent(s) that already have a reconciliation "
                        f"baseline. Such a write can only precede the baseline: nothing can recompute "
                        f"it, and nothing re-baselines.",
                        equivalent_ids=sorted(str(value) for value in baselined),
                    )

        equivalent_rows = [
            {
                "operation_id": record.id,
                "equivalent_id": equivalent_id,
                "in_intent": equivalent_id in record.intent_equivalent_ids,
                "in_scope": record.scope_equivalent_ids is None
                or equivalent_id in record.scope_equivalent_ids,
                "effect_count": len(codes),
                "effect_digest": _entry_digest(codes),
            }
            for equivalent_id, codes in sorted(per_equivalent.items(), key=lambda kv: kv[0].bytes)
        ]

        with _journal_write(conn):
            if equivalent_rows:
                await async_conn.execute(insert(debt_operation_equivalents), equivalent_rows)
            result = await async_conn.execute(
                update(debt_operations)
                .where(
                    debt_operations.c.id == record.id,
                    debt_operations.c.state == "OPEN",
                )
                .values(
                    state="COMPLETED",
                    completed_at=datetime.now(timezone.utc),
                    flush_count=record.flush_count,
                    effect_count=len(encoded),
                    effect_digest=_entry_digest(encoded),
                )
            )
        if result.rowcount != 1:
            raise DebtJournalError(
                Reason.ENVELOPE_LOST,
                f"completing {record.kind}/{record.identity} updated {result.rowcount} envelope "
                f"rows; exactly one OPEN envelope was expected.",
            )
    except BaseException:
        _poison(state, "operation completion failed")
        record.state = _OpRecord.POISONED
        raise

    record.state = _OpRecord.COMPLETED


def mapped_journal_tables() -> set[str]:
    """Journal tables that some mapper has started mapping. Must always be empty.

    The guard's whole enforcement rests on the journal tables being unreachable from the ORM
    (design v2 §6): the day one of them acquires a mapper, `session.add`, `merge`, the three
    `bulk_*` entry points and every cascade become write paths this module does not inspect. That
    is a change nobody would make on purpose, which is exactly why it needs a test rather than a
    comment - `tests/unit/test_p015_b4a_journal_write_guard.py`.
    """

    mapped: set[str] = set()
    for mapper in Debt.registry.mappers:
        for table in mapper.tables:
            if table.name in DEBT_JOURNAL_TABLE_NAMES:
                mapped.add(table.name)
    return mapped


# =================================================================================================
# Activation
# =================================================================================================

# THE JOURNAL ARMS ITSELF WHEN IT IS IMPORTED, and `app/db/models/__init__.py` imports it. That
# pairing is the whole of C15: a `python -c`, a data-fix script or `scripts/seed_db.py` run by hand
# imports the models and nothing else, and the protection has to arrive with the tables rather than
# with an application entry point. Registering from `app/main.py`'s startup or from
# `app/db/session.py` would leave every one of those writers unjournalled while every in-process
# test still passed.
#
# It is a module-body call rather than something `app/db/models/__init__.py` invokes, because the
# import is circular by construction - this module needs `Debt`, and the models package needs this
# module - and in the direction "journal first" the models package would reach a half-initialised
# module whose installer does not exist yet. A module arming itself at the end of its own body is
# well-defined in both directions.
arm_journal_globally()
