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
durable before this existed. One extra SELECT per flush that moves money is what it costs.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import weakref
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Iterable, Iterator

from sqlalchemy import event, insert, select, update
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
from app.db.sqlite_transaction_control import sqlite_transaction_control_is_installed

__all__ = [
    "DEBT_TABLE_NAME",
    "JOURNAL_STATEMENT_OPTION",
    "DebtJournalError",
    "DebtOperationIncomplete",
    "Reason",
    "debt_operation",
    "install_flush_hook",
    "install_journal",
    "install_write_guard",
    "mapped_journal_tables",
    "journal_is_installed",
    "uninstall_flush_hook",
    "uninstall_journal",
    "uninstall_write_guard",
]

DEBT_TABLE_NAME = Debt.__tablename__

#: Execution option marking a statement as the JOURNAL'S OWN rather than a writer's. Only the
#: verification read needs it: every other statement this module issues names a journal table and is
#: recognisable by that. A test that compares the SQL of an armed run against a stood-down one has to
#: be able to tell the mechanism's statements from the work's, and the statement's table name cannot
#: do it for a read of `debts` (T1528).
JOURNAL_STATEMENT_OPTION = "geo_journal_statement"

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
    JOURNAL_TABLE_WRITE = "journal_table_write"
    AUTOCOMMIT_ROOT = "autocommit_root"
    TWO_PHASE_ROOT = "two_phase_root"
    NO_TRANSACTION_CONTROL = "no_transaction_control"
    ENGINE_NOT_INSTRUMENTED = "engine_not_instrumented"
    BAD_ARGUMENT = "bad_argument"
    ENVELOPE_LOST = "envelope_lost"
    STALE_DB_TRANSACTION = "stale_db_transaction"
    UNMEASURED_DB_TRANSACTION = "unmeasured_db_transaction"


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


@contextmanager
def _journal_write(conn: Connection) -> Iterator[None]:
    """Mark this connection as executing the journal's OWN statements.

    Scoped to a connection rather than to the process: two units of work on two connections write
    their journals concurrently, and a process-wide flag would let one of them authorise the
    other's statement.
    """

    _INTERNAL[conn] = _INTERNAL.get(conn, 0) + 1
    try:
        yield
    finally:
        depth = _INTERNAL.get(conn, 0) - 1
        if depth <= 0:
            _INTERNAL.pop(conn, None)
        else:
            _INTERNAL[conn] = depth


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


def _blocking_problem(state: _TxState, *, ops: list[_OpRecord]) -> tuple[str, str] | None:
    if state.poison is not None:
        return (Reason.ROOT_POISONED, f"this transaction was poisoned: {state.poison}")
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
    problem = _blocking_problem(state, ops=state.ops)
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
    problem = _blocking_problem(state, ops=bound)
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
    if state.pending_savepoint_rollbacks.pop(name, None) is None:
        return
    # The root's poison is KEPT: a refusal that happened inside the savepoint was about this
    # transaction's right to write money, and undoing the savepoint does not undo that.
    state.ops = [op for op in state.ops if name not in op.chain]


def _on_after_cursor_execute(
    conn: Connection,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    """Confirm a savepoint rollback by watching the SQL run, not by trusting the event.

    This is the other half of condition 1. `do_rollback_to_savepoint` issues its statement through
    `exec_driver_sql`, which fires the cursor events (measured on pysqlite and aiosqlite), so a
    rollback that actually ran is observable and one that was prevented is not.
    """

    # The cheapest possible test first: this listener sees EVERY statement, and all but a handful
    # are not the one it is waiting for.
    if statement[:8].upper() != "ROLLBACK":
        return
    if statement.lstrip()[:21].upper() != "ROLLBACK TO SAVEPOINT":
        return
    state = _state_for(conn)
    if state is None or not state.pending_savepoint_rollbacks:
        return
    name = statement.strip().rsplit(None, 1)[-1].strip('"`[]')
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
    `exec_driver_sql` and `text()` are documented exceptions: they fire no `before_execute` at all
    (`sqlalchemy/engine/base.py:1712-1778`), and this module does not claim to cover them.
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
    and stays so. And it runs at the END OF THIS FLUSH, so a change made after it - by a later
    listener using `text()` or `exec_driver_sql`, which fire no `before_execute` and are this module's
    one documented blind spot - is outside it. That exposure is reported, not claimed away.

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

    rows = conn.execute(
        select(
            table.c.id,
            table.c.equivalent_id,
            table.c.debtor_id,
            table.c.creditor_id,
            table.c.amount,
        )
        .where(table.c.id.in_([state.debt_id for state in states]))
        # MARKED AS THE JOURNAL'S OWN STATEMENT. It is a read against `debts` rather than against a
        # journal table, so a tracer that recognises the journal's noise by its table names cannot
        # tell it from a writer's own read (`tests/unit/test_p015_b4_r4_*` does exactly that). The
        # option is the only non-textual way to say "this statement is the mechanism, not the work".
        .execution_options(**{JOURNAL_STATEMENT_OPTION: True})
    ).all()
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
        if not _same_money(_as_decimal(row[4]), state.amount):
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
        rows = (
            await async_conn.execute(
                select(
                    debt_journal_entries.c.flush_ordinal,
                    debt_journal_entries.c.equivalent_id,
                    debt_journal_entries.c.debtor_id,
                    debt_journal_entries.c.creditor_id,
                    debt_journal_entries.c.effect,
                    debt_journal_entries.c.amount_before,
                    debt_journal_entries.c.amount_after,
                    debt_journal_entries.c.delta,
                ).where(debt_journal_entries.c.operation_id == record.id)
            )
        ).all()
        ordered = sorted(
            rows,
            key=lambda row: (
                row[0],
                uuid.UUID(str(row[1])).bytes,
                uuid.UUID(str(row[2])).bytes,
                uuid.UUID(str(row[3])).bytes,
            ),
        )
        encoded = [
            (
                row[0],
                str(row[1]),
                str(row[2]),
                str(row[3]),
                row[4],
                _money_text(_as_decimal(row[5])),
                _money_text(_as_decimal(row[6])),
                _money_text(_as_decimal(row[7])),
            )
            for row in ordered
        ]

        per_equivalent: dict[uuid.UUID, list[tuple[Any, ...]]] = {}
        for row, code in zip(ordered, encoded):
            per_equivalent.setdefault(uuid.UUID(str(row[1])), []).append(code)
        for equivalent_id in record.intent_equivalent_ids:
            per_equivalent.setdefault(equivalent_id, [])

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
