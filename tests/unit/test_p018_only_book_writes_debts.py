"""Only `app/core/ledger/book.py` writes `debts` - a structural guard over `app/` and `scripts/`.

Programme 018, stage A, `T1801` (the structural reproducer of Verification §1) and `T1807` (the
guard). Before stage A this file was RED: `book.py` did not exist and five modules wrote debts -
`payments/engine.py`, `clearing/service.py`, `simulator/inject_executor.py`, `api/v1/integrity.py`
and `scripts/seed_db.py` - plus a sixth the spec's inventory missed,
`scripts/measure_clearing_min_amount_plan.py`.

WHAT IT LOOKS FOR - six forms, each read from the source text:

1. `Debt(...)` - a constructor call (also `models.Debt(...)`, and an import alias `Debt as D`);
2. an assignment (`=`, `+=`, `-=`, annotated) to an attribute named `amount`, in a module that
   imports `Debt`;
3. `<x>.delete(<name>)` where `<name>`, in the same function, is a Debt: bound from `Debt(...)`,
   bound from an expression that names `Debt` (`select(Debt)...`, `await session.get(Debt, ...)`),
   iterated out of such an expression, or given an `.amount` assignment;
4. `delete(Debt)`; 5. `update(Debt)`; 6. `insert(Debt)`.

WHAT IT DOES NOT SEE - its silence is not evidence about these, and they are closed by the database
trigger of stage B, not by this guard (spec 018, Verification §1):

* re-binding the class (`D = Debt`) - an IMPORT alias is followed, an assignment is not;
* objects that come back from a call and are never named as a Debt in the function
  (`thing = helper(); thing.amount = x` in a module that does not import `Debt`);
* `setattr(debt, "amount", ...)`, `session.bulk_*`, `bulk_insert_mappings`, `session.merge`;
* Core DML through `Debt.__table__` or the `debts` `Table` object;
* raw SQL - `text(...)`, `exec_driver_sql(...)`, `COPY`;
* helpers outside `app/` and `scripts/` - `tests/` is not scanned, so test code that builds rows
  inside a `TEST_FIXTURE` block (`tests/debt_setup.py`) is outside this guard by construction.

It is a bounded MAINTENANCE sentinel: it stops the ordinary way of adding a second writer, and names
the ways it cannot stop.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_ROOTS = ("app", "scripts")
_THE_WRITER = "app/core/ledger/book.py"


_SQL_DML = frozenset({"delete", "update", "insert"})

BLIND_SPOTS = (
    "re-binding the class (D = Debt); objects returned from calls and never named as a Debt; "
    "setattr(); bulk_*/mappings/merge; Core DML through Debt.__table__; raw SQL (text(), "
    "exec_driver_sql, COPY); helpers outside app/ and scripts/. These are closed by the stage-B "
    "database trigger, not by this guard."
)


def _debt_names(tree: ast.AST) -> set[str]:
    """The local names under which this module imported the `Debt` class."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "Debt":
                    names.add(alias.asname or alias.name)
    return names


def _is_debt_ref(node: ast.AST, debt_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in debt_names or node.id == "Debt"
    if isinstance(node, ast.Attribute):
        return node.attr == "Debt"
    return False


def _mentions_debt(node: ast.AST, debt_names: set[str]) -> bool:
    return any(_is_debt_ref(child, debt_names) for child in ast.walk(node))


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _target_names(element)]
    return []


def _debt_bound_names(scope: ast.AST, debt_names: set[str]) -> set[str]:
    """Names that, somewhere in this function (or module body), are a Debt."""

    bound: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _mentions_debt(node.value, debt_names):
                for target in targets:
                    bound.update(_target_names(target))
        elif isinstance(node, (ast.For, ast.AsyncFor)) and _mentions_debt(node.iter, debt_names):
            bound.update(_target_names(node.target))
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "amount"
                    and isinstance(target.value, ast.Name)
                ):
                    bound.add(target.value.id)
    return bound


def debt_writes(source: str, *, path: str = "<source>") -> list[str]:
    """Every debt write of the six forms in `source`, as `path:line: form` strings."""

    tree = ast.parse(source, filename=path)
    debt_names = _debt_names(tree)
    found: list[str] = []

    def report(node: ast.AST, form: str) -> None:
        found.append(f"{path}:{node.lineno}: {form}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_debt_ref(node.func, debt_names):
                report(node, "Debt(...) constructed")
            func_name = node.func.id if isinstance(node.func, ast.Name) else None
            if func_name in _SQL_DML and node.args and _is_debt_ref(node.args[0], debt_names):
                report(node, f"{func_name}(Debt)")
        if debt_names and isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "amount":
                    report(node, "assignment to .amount in a module that imports Debt")

    scopes: list[ast.AST] = [tree] + [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    seen: set[int] = set()
    for scope in scopes:
        bound = _debt_bound_names(scope, debt_names)
        if not bound:
            continue
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Call)
                and id(node) not in seen
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "delete"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in bound
            ):
                seen.add(id(node))
                report(node, "session.delete(<Debt>)")
    return sorted(set(found))


def _scanned_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for top in _SCANNED_ROOTS:
        base = root / top
        if base.is_dir():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def writers_under(root: Path) -> dict[str, list[str]]:
    """`{relative path: [findings]}` for every scanned file with at least one debt write."""

    writers: dict[str, list[str]] = {}
    for path in _scanned_files(root):
        relative = path.relative_to(root).as_posix()
        findings = debt_writes(path.read_text(encoding="utf-8"), path=relative)
        if findings:
            writers[relative] = findings
    return writers


# =================================================================================================
# The rule, over the tree
# =================================================================================================


def test_only_book_writes_debts_in_app_and_scripts() -> None:
    writers = writers_under(_ROOT)
    unexpected = {
        path: findings
        for path, findings in writers.items()
        if path != _THE_WRITER
    }
    assert not unexpected, (
        "debts are written outside app/core/ledger/book.py:\n"
        + "\n".join(line for findings in unexpected.values() for line in findings)
        + "\nRoute the write through Book.operation(...)/Book.post(...). This guard checks the "
        "FORM of the source only; it does not see: " + BLIND_SPOTS
    )
    assert _THE_WRITER in writers, (
        f"{_THE_WRITER} writes no debt the guard can see - either the writer moved (update "
        f"_THE_WRITER) or the scan went blind. Found writers: {sorted(writers)}"
    )


def test_the_scan_is_not_vacuous() -> None:
    """Anti-vacuum: the scan reads the tree, and reads the files the old writers lived in."""

    files = {p.relative_to(_ROOT).as_posix() for p in _scanned_files(_ROOT)}
    assert len(files) > 100, f"only {len(files)} files scanned under {_SCANNED_ROOTS}"
    for expected in (
        "app/core/payments/engine.py",
        "app/core/clearing/service.py",
        "app/core/simulator/inject_executor.py",
        "scripts/seed_db.py",
    ):
        assert expected in files, f"{expected} was not scanned"


# =================================================================================================
# Counter-checks: each form is found; neighbours are not
# =================================================================================================

_POSITIVE = {
    "constructor": """
        from app.db.models import Debt
        def f(session):
            session.add(Debt(debtor_id=a, creditor_id=b, equivalent_id=e, amount=x))
    """,
    "constructor through an import alias": """
        from app.db.models.debt import Debt as D
        def f(session):
            session.add(D(debtor_id=a, creditor_id=b, equivalent_id=e, amount=x))
    """,
    "constructor through a module attribute": """
        from app.db import models
        def f(session):
            session.add(models.Debt(amount=x))
    """,
    "amount assignment": """
        from app.db.models import Debt
        async def f(session):
            debt = await helper()
            debt.amount = x
    """,
    "amount augmented assignment": """
        from app.db.models import Debt
        async def f(session):
            for debt in debts:
                debt.amount -= x
    """,
    "session.delete of a Debt": """
        from app.db.models import Debt
        async def f(session):
            debt = (await session.execute(select(Debt))).scalar_one()
            await session.delete(debt)
    """,
    "delete(Debt)": """
        from sqlalchemy import delete
        from app.db.models import Debt
        async def f(session):
            await session.execute(delete(Debt).where(Debt.amount == 0))
    """,
    "update(Debt)": """
        from sqlalchemy import update
        from app.db.models import Debt
        async def f(session):
            await session.execute(update(Debt).values(amount=1))
    """,
    "insert(Debt)": """
        from sqlalchemy import insert
        from app.db.models import Debt
        async def f(session):
            await session.execute(insert(Debt).values(amount=1))
    """,
}

_NEGATIVE = {
    "reading debts": """
        from app.db.models import Debt
        async def f(session):
            rows = (await session.execute(select(Debt.amount))).all()
            return sum(r.amount for r in rows)
    """,
    "deleting an equivalent in a module that imports Debt": """
        from app.db.models import Debt, Equivalent
        async def f(db, code):
            eq = await db.get(Equivalent, code)
            await db.delete(eq)
    """,
    "amount assignment in a module that does not import Debt": """
        def f(payload):
            payload.amount = 1
    """,
    "a router decorator named delete": """
        from app.db.models import Debt
        @router.delete("/x")
        async def f():
            return None
    """,
}


@pytest.mark.parametrize("name", sorted(_POSITIVE))
def test_each_form_is_found(name: str) -> None:
    findings = debt_writes(textwrap.dedent(_POSITIVE[name]), path=name)
    assert findings, f"the guard did not find the form {name!r}"


@pytest.mark.parametrize("name", sorted(_NEGATIVE))
def test_neighbours_are_not_reported(name: str) -> None:
    findings = debt_writes(textwrap.dedent(_NEGATIVE[name]), path=name)
    assert not findings, f"the guard reported a non-write {name!r}: {findings}"


def test_a_planted_writer_is_found_in_a_tree(tmp_path: Path) -> None:
    """Mutation: a second writer planted into a copy-shaped tree turns the rule red."""

    (tmp_path / "app" / "core" / "ledger").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "app" / "core" / "ledger" / "book.py").write_text(
        "from app.db.models import Debt\n\ndef w(s):\n    s.add(Debt(amount=1))\n",
        encoding="utf-8",
    )
    assert set(writers_under(tmp_path)) == {_THE_WRITER}
    (tmp_path / "scripts" / "planted.py").write_text(
        "from app.db.models.debt import Debt\n\nasync def w(s, d):\n    d.amount += 1\n",
        encoding="utf-8",
    )
    assert set(writers_under(tmp_path)) == {_THE_WRITER, "scripts/planted.py"}
