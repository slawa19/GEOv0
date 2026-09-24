"""The test-local operation context, and the static guard that keeps its blocks honest.

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
test. The no-op path below remains, and is not dead: it is what an engine the journal has been
stood down on still runs (`uninstall_write_guard`), and it is what makes `journal_is_active` an
honest question rather than a constant.

WHAT IT DELIBERATELY DOES NOT DO: it never flushes. Design v2 §8 R2 forbids inserting a flush where
none existed, because a flush is SQL and SQL is behaviour. The block ends just before whatever
`flush()`/`commit()` the test already had; when the journal is live, `debt_operation`'s own
completion flush covers the block's work, and the test's own flush that follows finds nothing
pending. So the number of round trips is the same before and after activation.

THE STATIC GUARD (`fixture_block_violations`). The runtime half of C21 - the journal's refusal to
nest operations - only catches application code that opens an operation OF ITS OWN. Code like
`_apply_flow` or `stage_inject_event` running inside a fixture block opens nothing and would
silently have its effects recorded as the fixture's own. Nothing at runtime can see that, so it is
checked statically: the body of an `async with debt_fixture_setup(...)` block may contain only
model construction, `session.add/add_all/delete`, assignment to a local or to an attribute of a
local, and `await session.flush()`. Per binding condition 7 the walk is RECURSIVE OVER EXPRESSIONS:
a whitelist that allows "attribute assignment on a local" does not by itself forbid an application
call on the right-hand side, and that hidden call is exactly the interesting case.
"""

from __future__ import annotations

import ast
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Sequence

__all__ = [
    "FIXTURE_OPERATION_KIND",
    "Violation",
    "add_debts",
    "debt_fixture_setup",
    "fixture_block_violations",
    "writer_operation",
    "journal_is_active",
    "purge_test_ledger",
]

#: The operation kind reserved for fixture setup. `TEST_FIXTURE` and `SEED` are the only kinds the
#: journal lets open without a declared equivalent scope, because neither holds a lock set it could
#: derive one from (`app/core/ledger/journal.py`, `_validate_arguments`).
FIXTURE_OPERATION_KIND = "TEST_FIXTURE"


# =================================================================================================
# The runtime half
# =================================================================================================


def _engine_of(session: Any) -> Any:
    """The `Engine` this session writes through, or `None` if it cannot be determined.

    `None` is not "assume installed": a session with no discoverable bind cannot have the journal's
    connection-level listeners either, since those live on an engine.
    """

    sync_session = getattr(session, "sync_session", session)
    try:
        bind = sync_session.get_bind()
    except Exception:  # noqa: BLE001 - an unbound session simply has no engine to inspect
        return None
    return getattr(bind, "engine", bind)


def journal_is_active(session: Any) -> bool:
    """Whether the debt journal is armed on this session's engine.

    Registration, not effect - the same question `journal_is_installed` answers, asked of the
    engine a given session happens to be bound to. Today this is `False` everywhere; slice C makes
    it `True` and nothing in the migrated tests changes.
    """

    engine = _engine_of(session)
    if engine is None:
        return False
    from app.core.ledger.journal import journal_is_installed

    return journal_is_installed(engine)


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

    Yields the journal's operation record when the journal is installed, and `None` when it is not.
    Callers must not depend on the yielded value: it exists so that a test which needs the envelope
    can reach it once the journal is live, not as part of the setup contract.

    `label` names what the block is setting up. It is part of the operation's identity, so two
    blocks in the same test are distinguishable in the journal; `uuid4` makes the identity unique
    across runs, which is what lets C13's duplicate-identity refusal stay a refusal of a real
    duplicate rather than of a re-run.
    """

    if not label:
        raise ValueError("debt_fixture_setup needs a label naming what this block sets up")

    if not journal_is_active(session):
        # The clean no-op. Nothing is read, nothing is written, nothing is flushed: a migrated test
        # executes exactly the statements it executed before this slice.
        yield None
        return

    from app.core.ledger.journal import debt_operation

    node_id = _node_id()
    async with debt_operation(
        session,
        kind=FIXTURE_OPERATION_KIND,
        identity=f"{node_id}:{label}:{uuid.uuid4()}",
        intent={"node_id": node_id, "label": label},
        scope_equivalent_ids=None,
    ) as record:
        yield record


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
    `PaymentEngine._apply_flow` or `InjectExecutor.stage_inject_event` directly is exercising a
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

    if not journal_is_active(session):
        yield None
        return

    from app.core.ledger.journal import debt_operation
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
                state="NEW",
            )
        )
        await session.flush()

    async with debt_operation(
        session,
        kind=kind,
        identity=f"{_node_id()}:{label}:{uuid.uuid4()}",
        tx_id=tx_id,
        intent={"node_id": _node_id(), "label": label, "driven_directly": True},
        scope_equivalent_ids=set(equivalent_ids),
        intent_equivalent_ids=set(equivalent_ids),
    ) as record:
        yield record


def _uuid_literals(values: Iterable[Any]) -> list[str]:
    """The values as SQL literals of PostgreSQL's native `uuid`, or a loud failure.

    EVERY VALUE GOES THROUGH `uuid.UUID` FIRST, which is what makes the interpolation below safe -
    nothing that is not a UUID can reach the statement.

    THE SPELLING WAS PER DIALECT until 017 stage 3, which is not cosmetic. `sqlalchemy.Uuid(as_uuid=True)`
    stored the 32-character hex WITHOUT dashes on SQLite and a native `uuid` on PostgreSQL, so a purge
    written with the canonical dashed form matched nothing at all on the SQLite tier - it deletes
    no rows, raises nothing, and the next `DELETE FROM participants` fails on a foreign key whose
    referent the teardown believed it had removed. Measured 2026-09-12 while activating the
    journal, and it is exactly the shape of a cleanup that silently does nothing.
    """

    ids = [uuid.UUID(str(value)) for value in values]
    return [str(value) for value in ids]


def _sql_in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


async def purge_test_ledger(
    session_or_connection: Any,
    *,
    equivalent_ids: Iterable[Any] = (),
    tx_ids: Iterable[str] = (),
) -> None:
    """Delete a test's debts AND the journal rows that describe them, through the driver.

    WHY IT EXISTS (design v2 §8 R6). Cleanups across this suite end with
    `session.execute(delete(Debt).where(...))`. Once the journal is armed that is Core DML against
    `debts` outside a verified flush, and the write guard refuses it - correctly, because it is
    indistinguishable from a writer moving money with no record. A teardown is not a money write,
    so it goes round the guard the only way this module documents as permitted: `exec_driver_sql`,
    which fires no `before_execute`.

    AND IT DELETES THE JOURNAL ROWS TOO, which the old one-liner had no reason to. The journal's
    foreign keys are RESTRICT: an entry naming an equivalent keeps that equivalent alive, so a
    teardown that removed only the debts would leave the next test's `DELETE FROM equivalents`
    failing on a reference it cannot see. That RESTRICT is deliberate (`C17`) - history must outlive
    the debts - which is exactly why the disposal has to name the history.

    SCOPED, NEVER "EVERYTHING". Rows are removed by the ids the caller names and by nothing else,
    and every id is put through `uuid.UUID` first, which is both the injection guard and the reason
    the statements can interpolate rather than bind (paramstyle differs between pysqlite and
    asyncpg, and a teardown helper that worked on one tier only would be worse than none).
    """

    transactions = [str(value) for value in tx_ids]
    if not list(equivalent_ids) and not transactions:
        return

    connection = session_or_connection
    if not hasattr(connection, "exec_driver_sql"):
        connection = await connection.connection()
    equivalents = _uuid_literals(equivalent_ids)

    conditions: list[str] = []
    if equivalents:
        conditions.append(
            f"id IN (SELECT operation_id FROM debt_journal_entries "
            f"WHERE equivalent_id IN ({_sql_in(equivalents)}))"
        )
        conditions.append(
            f"id IN (SELECT operation_id FROM debt_operation_equivalents "
            f"WHERE equivalent_id IN ({_sql_in(equivalents)}))"
        )
    if transactions:
        quoted = ", ".join(f"'{value}'" for value in transactions if "'" not in value)
        if quoted:
            conditions.append(f"tx_id IN ({quoted})")

    # THE OPERATION IDS ARE RESOLVED FIRST, INTO PYTHON, and that is not a style choice: the filter
    # below finds an envelope through its entries and its per-equivalent rows, and the first two
    # statements delete exactly those. A third statement that re-evaluated the same subquery would
    # match nothing at all, leaving the envelope behind - and an envelope holds a RESTRICT reference
    # to `transactions.tx_id`, so the caller's next `DELETE FROM transactions` failed on a row the
    # teardown believed it had removed. Measured 2026-09-12, arming the journal.
    operation_filter = " OR ".join(conditions)
    operation_ids = [
        row[0]
        for row in (
            await connection.exec_driver_sql(
                f"SELECT id FROM debt_operations WHERE {operation_filter}"  # noqa: S608
            )
        ).all()
    ]
    if operation_ids:
        targets = _sql_in(_uuid_literals(operation_ids))
        for statement in (
            f"DELETE FROM debt_journal_entries WHERE operation_id IN ({targets})",
            f"DELETE FROM debt_operation_equivalents WHERE operation_id IN ({targets})",
            f"DELETE FROM debt_operations WHERE id IN ({targets})",
        ):
            await connection.exec_driver_sql(statement)
    if equivalents:
        # The reconciliation baseline is RESTRICT on the equivalent (step 5a), so it has to be named by
        # the disposal too; the result rows would cascade, and are removed here so a purge is complete.
        # STEP 5c: a hold's evidence row is RESTRICT while the hold points at it, so the teardown releases
        # the hold first. A test-disposal statement, never an application path.
        await connection.exec_driver_sql(
            "UPDATE equivalents SET integrity_hold_result_id = NULL "
            f"WHERE id IN ({_sql_in(equivalents)}) AND integrity_hold_result_id IS NOT NULL"
        )
        for statement in (
            "DELETE FROM debt_reconciliation_baseline_offsets WHERE equivalent_id IN ({ids})",
            "DELETE FROM debt_reconciliation_baselines WHERE equivalent_id IN ({ids})",
            "DELETE FROM debt_reconciliation_results WHERE equivalent_id IN ({ids})",
        ):
            await connection.exec_driver_sql(statement.format(ids=_sql_in(equivalents)))
        await connection.exec_driver_sql(
            f"DELETE FROM debts WHERE equivalent_id IN ({_sql_in(equivalents)})"
        )


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


# =================================================================================================
# The static half: what may appear inside a fixture block
# =================================================================================================


@dataclass(frozen=True)
class Violation:
    """One thing inside an `async with debt_fixture_setup(...)` body that does not belong there."""

    path: str
    lineno: int
    col_offset: int
    reason: str

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.path}:{self.lineno}:{self.col_offset}: {self.reason}"


#: The three reasons, as values a test can assert on rather than prose (design v2 §7).
DISALLOWED_CALL = "disallowed_call"
DISALLOWED_STATEMENT = "disallowed_statement"
DISALLOWED_TARGET = "disallowed_target"

#: Session methods a fixture block may call, on a plain local name.
_SESSION_METHODS = frozenset({"add", "add_all", "delete", "flush"})

#: Dotted names that build a value and touch nothing. Kept short and explicit: every entry is a
#: hole in the whitelist, and a hole nobody named is the failure mode this guard exists to prevent.
_PURE_VALUE_CALLS = frozenset({"uuid.uuid4", "uuid.uuid1", "uuid.uuid5", "uuid.UUID"})

#: The context-manager name whose blocks are guarded.
_FIXTURE_CONTEXT = "debt_fixture_setup"


def _dotted(node: ast.AST) -> Any:
    """`a.b.c` as a string, or `None` if the expression is not a plain dotted name."""

    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _is_constructor_name(node: ast.AST) -> bool:
    """A bare `Name` that reads as a type: `Debt`, `Decimal`, `TrustLine`.

    Structural, not a list of model names: the rule is "the callee is a bare name whose first
    letter is upper case", which is the only signal a source file carries about constructor-ness.
    An application entry point reached as `PaymentEngine(session).commit(...)` is NOT this shape -
    its callee is an attribute on a call result - which is what makes the two C21 negative cases
    separable from the positive one.
    """

    return isinstance(node, ast.Name) and node.id[:1].isupper()


def _call_is_allowed(call: ast.Call) -> bool:
    func = call.func
    if _is_constructor_name(func):
        return True
    if isinstance(func, ast.Attribute):
        if func.attr in _SESSION_METHODS and isinstance(func.value, ast.Name):
            return True
        dotted = _dotted(func)
        if dotted is not None and dotted in _PURE_VALUE_CALLS:
            return True
    return False


def _expression_violations(node: ast.AST, path: str) -> list[Violation]:
    """Every disallowed call anywhere inside an expression.

    BINDING CONDITION 7 LIVES HERE. The walk is over the whole expression tree, so
    `debt.amount = (await PaymentEngine(session).quote(tx)).amount` is reported even though the
    statement shape - assignment to an attribute of a local - is allowed. A statement-level
    whitelist would accept it.
    """

    found: list[Violation] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and not _call_is_allowed(child):
            found.append(Violation(path, child.lineno, child.col_offset, DISALLOWED_CALL))
    return found


def _target_is_allowed(target: ast.expr) -> bool:
    """Assignment to a local, or to an attribute of a local. Nothing deeper, nothing subscripted."""

    if isinstance(target, ast.Name):
        return True
    if isinstance(target, ast.Attribute):
        return isinstance(target.value, ast.Name)
    if isinstance(target, (ast.Tuple, ast.List)):
        return all(_target_is_allowed(element) for element in target.elts)
    return False


def _statement_violations(stmt: ast.stmt, path: str) -> list[Violation]:
    found: list[Violation]
    if isinstance(stmt, (ast.Pass, ast.Expr)):
        found = []
    elif isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        found = [
            Violation(path, target.lineno, target.col_offset, DISALLOWED_TARGET)
            for target in targets
            if not _target_is_allowed(target)
        ]
    else:
        # A loop, a branch, a `with`, an `assert`, a `return`, a nested def: none of these is
        # fixture setup, and each of them is a place where control flow could hide a call that the
        # expression walk would then never be asked about.
        return [Violation(path, stmt.lineno, stmt.col_offset, DISALLOWED_STATEMENT)]

    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.expr):
            found.extend(_expression_violations(child, path))
    return found


def _is_fixture_context(item: ast.withitem) -> bool:
    expr = item.context_expr
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    if isinstance(func, ast.Name):
        return func.id == _FIXTURE_CONTEXT
    if isinstance(func, ast.Attribute):
        return func.attr == _FIXTURE_CONTEXT
    return False


def fixture_block_violations(
    source_or_tree: Any, *, path: str = "<source>"
) -> list[Violation]:
    """Everything that does not belong inside an `async with debt_fixture_setup(...)` body.

    An empty list means every fixture block in this source is clean. `source_or_tree` is either
    Python source or an already-parsed `ast.AST`; `path` is only used to label the results, so a
    caller scanning one file at a time gets findings it can act on.

    LIMIT, and it is the reason the runtime nesting refusal exists as well: this sees only text.
    A fixture block that calls a local helper which in turn drives a writer is invisible here.
    Design v2 §7 records the converse limit too - the runtime refusal sees only application code
    that opens an operation of its own. Neither half covers the other's blind spot.
    """

    if isinstance(source_or_tree, ast.AST):
        tree: ast.AST = source_or_tree
    else:
        text = (
            source_or_tree.decode("utf-8")
            if isinstance(source_or_tree, bytes)
            else source_or_tree
        )
        tree = ast.parse(text, filename=path)

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncWith, ast.With)):
            continue
        if not any(_is_fixture_context(item) for item in node.items):
            continue
        for stmt in node.body:
            violations.extend(_statement_violations(stmt, path))
    violations.sort(key=lambda v: (v.lineno, v.col_offset, v.reason))
    return violations
