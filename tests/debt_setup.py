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
test. Since 018 stage B1 the journal is the database's trigger, the listener module and its stand-down
are gone, and so is the no-op path that served a stood-down engine.

WHAT IT DELIBERATELY DOES NOT DO: it never flushes. Design v2 §8 R2 forbids inserting a flush where
none existed, because a flush is SQL and SQL is behaviour. The block ends just before whatever
`flush()`/`commit()` the test already had; `Book.operation`'s own completion flush covers the block's
work, and the test's own flush that follows finds nothing pending.

THE STATIC GUARD (`fixture_block_violations`). The runtime half of C21 - the journal's refusal to
nest operations - only catches application code that opens an operation OF ITS OWN. Code like
`_apply_flow` or `stage_inject_event` running inside a fixture block opens nothing and would
silently have its effects recorded as the fixture's own. Nothing at runtime can see that, so it is
checked statically: the body of an `async with debt_fixture_setup(...)` block may contain only
model construction, `session.add/add_all/delete`, assignment to a local or to an attribute of a
local, and `await session.flush()`. Per binding condition 7 the walk is RECURSIVE OVER EXPRESSIONS:
a whitelist that allows "attribute assignment on a local" does not by itself forbid an application
call on the right-hand side, and that hidden call is exactly the interesting case.

DISPOSAL (018 stage B1, 2026-09-24). `purge_test_ledger`, which deleted a test's debts and journal rows
through the driver, left with its last caller (`tests/p015_b4_support.drop_world`): the database now
refuses every one of those deletes, and a test that commits runs on a disposable clone whose drop is
the disposal (`tests/tier_on_a_clone.py`, `tests/p018_support.py::module_clone`).
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
]

#: The operation kind reserved for fixture setup. `TEST_FIXTURE` and `SEED` are the only kinds the
#: journal lets open without a declared equivalent scope, because neither holds a lock set it could
#: derive one from (`app/core/ledger/journal.py`, `_validate_arguments`).
FIXTURE_OPERATION_KIND = "TEST_FIXTURE"


# =================================================================================================
# The runtime half
# =================================================================================================


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

    Yields the book's posting (018 stage A). Callers must not depend on the yielded value: it exists
    so that a test which needs the envelope can reach it, not as part of the setup contract.

    `label` names what the block is setting up. It is part of the operation's identity, so two
    blocks in the same test are distinguishable in the journal; `uuid4` makes the identity unique
    across runs, which is what lets C13's duplicate-identity refusal stay a refusal of a real
    duplicate rather than of a re-run.
    """

    if not label:
        raise ValueError("debt_fixture_setup needs a label naming what this block sets up")

    # THERE IS NO NO-OP PATH ANY MORE (018 stage B1). It served an engine the listener journal had
    # been stood down on; the journal is now the database's trigger, which no engine can stand down,
    # so every fixture block is a real `TEST_FIXTURE` operation.
    #
    # THE ENVELOPE IS THE BOOK'S (018 stage A): the fixture operation opens through `Book`, the
    # single writer of `debts`, like every application writer. The rows the test builds inside the
    # block are test code, outside `app/` and `scripts/` and so outside the single-writer guard by
    # construction (`tests/unit/test_p018_only_book_writes_debts.py`); the journal records them as
    # this operation's effects exactly as before.
    from app.core.ledger.book import Book, operation_for

    node_id = _node_id()
    async with Book.operation(
        session,
        operation_for(
            FIXTURE_OPERATION_KIND,
            f"{node_id}:{label}:{uuid.uuid4()}",
            {"node_id": node_id, "label": label},
            scope_equivalent_ids=None,
        ),
    ) as posting:
        yield posting


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

    from app.core.ledger.book import Book, operation_for
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

    # THE BOOK'S OPERATION (018 stage A). The writer internals these tests drive
    # (`PaymentEngine._apply_flow`, `InjectExecutor.stage_inject_event`) apply their effects through
    # `Book.current(session)`, so the operation they run under has to be a `Book` posting.
    equivalent_ids = set(equivalent_ids)
    async with Book.operation(
        session,
        operation_for(
            kind,
            f"{_node_id()}:{label}:{uuid.uuid4()}",
            {"node_id": _node_id(), "label": label, "driven_directly": True},
            tx_id=tx_id,
            scope_equivalent_ids=equivalent_ids,
            intent_equivalent_ids=equivalent_ids,
        ),
    ) as posting:
        yield posting


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
