"""Only `app/core/ledger/book.py` writes `debts` - a structural guard over `app/` and `scripts/`.

Programme 018, stage A, `T1801` (the structural reproducer of Verification §1) and `T1807` (the
guard). Before stage A this file was RED: `book.py` did not exist and five modules wrote debts -
`payments/engine.py`, `clearing/service.py`, `simulator/inject_executor.py`, `api/v1/integrity.py`
and `scripts/seed_db.py` - plus a sixth the spec's inventory missed,
`scripts/measure_clearing_min_amount_plan.py`.

WHAT IT LOOKS FOR - six forms, each read from the source text:

1. `Debt(...)` - a constructor call (also `models.Debt(...)`, and an import alias `Debt as D`);
2. an assignment (`=`, `+=`, `-=`, annotated) to an attribute named `amount`, in a module that
   imports `Debt` - also as one element of a tuple/list target or behind a star
   (`debt.amount, other = ...`, `*debt.amount, = ...`);
3. `<x>.delete(<name>)` where `<name>`, in the same function, is a Debt: bound from `Debt(...)`,
   bound from an expression that names `Debt` (`select(Debt)...`, `await session.get(Debt, ...)`),
   iterated out of such an expression, or given an `.amount` assignment;
4. `delete(Debt)`; 5. `update(Debt)`; 6. `insert(Debt)` - under the bare name, under an import
   alias from a `sqlalchemy` module (`from sqlalchemy import update as upd`,
   `from sqlalchemy.dialects.postgresql import insert as pg_insert`), and as an attribute of a
   `sqlalchemy` module name (`sqlalchemy.update(Debt)`, `sa.delete(Debt)`, `postgresql.insert(Debt)`).

WHAT IT DOES NOT SEE - its silence is not evidence about these, and they are closed by the database
trigger of stage B, not by this guard (spec 018, Verification §1):

* a DML function imported under an alias from a module that is not `sqlalchemy` (a re-export:
  `from app.somewhere import update as u`), or fetched dynamically (`getattr(sa, "update")`);

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

IT IS NOT THE BARRIER (since stage B, migration `029`, 2026-09-24). The database now sees every DML on
`debts` whatever its source form: the row trigger refuses a write outside an `OPEN` operation envelope
(`GE001`) and journals every accepted one from `OLD`/`NEW`, and `TRUNCATE` is refused. A write this
guard misses is therefore refused or recorded by the database, not lost. What the guard still buys is
earlier, cheaper feedback at review time - that a second place in `app/`/`scripts/` has started
writing debts, even one that opens its own envelope correctly and so passes the trigger - and that is
all it claims. Extending it to the forms above (tuple targets, `sqlalchemy.update(Debt)`, aliased DML,
added 2026-09-24 from the stage-A external review, manifest `T1808` item 15) narrows its blind spots;
it does not make its silence a proof.
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
    "exec_driver_sql, COPY); DML functions aliased from a non-sqlalchemy module or fetched with "
    "getattr(); helpers outside app/ and scripts/. The stage-B database trigger sees all of these "
    "(refuses a write without an OPEN envelope, journals an accepted one); this guard is a "
    "maintenance aid, not the barrier."
)


def _is_sqlalchemy_module(module: str | None) -> bool:
    return module is not None and (module == "sqlalchemy" or module.startswith("sqlalchemy."))


def _dml_bindings(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """How this module can reach a SQLAlchemy DML constructor.

    Returns `(function aliases, module names)`:

    * function aliases - local name -> DML verb, for `from sqlalchemy[...] import update as upd`
      (the bare names `delete`/`update`/`insert` are matched whatever their origin, as before);
    * module names - local names bound to a `sqlalchemy` module: `import sqlalchemy`,
      `import sqlalchemy as sa`, `import sqlalchemy.dialects.postgresql as pg`, and any
      non-DML name imported from a `sqlalchemy` module (`from sqlalchemy.dialects import
      postgresql`, `from sqlalchemy import sql`), whose attribute `update`/`delete`/`insert` is
      then a DML constructor.
    """

    aliases: dict[str, str] = {}
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_sqlalchemy_module(alias.name):
                    modules.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and _is_sqlalchemy_module(node.module):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in _SQL_DML:
                    aliases[local] = alias.name
                else:
                    modules.add(local)
    return aliases, modules


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _dml_verb(func: ast.AST, aliases: dict[str, str], modules: set[str]) -> str | None:
    """The DML verb `func` names, or `None`: bare name, sqlalchemy alias, or `<sa module>.<verb>`."""

    if isinstance(func, ast.Name):
        if func.id in _SQL_DML:
            return func.id
        return aliases.get(func.id)
    if isinstance(func, ast.Attribute) and func.attr in _SQL_DML and _root_name(func.value) in modules:
        return func.attr
    return None


def _flat_targets(target: ast.AST) -> list[ast.AST]:
    """Every leaf of an assignment target: tuple/list elements and starred values, recursively."""

    if isinstance(target, (ast.Tuple, ast.List)):
        return [leaf for element in target.elts for leaf in _flat_targets(element)]
    if isinstance(target, ast.Starred):
        return _flat_targets(target.value)
    return [target]


def _assigned_leaves(node: ast.AST) -> list[ast.AST]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [leaf for target in targets for leaf in _flat_targets(target)]


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
            for target in _assigned_leaves(node):
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
    dml_aliases, sqlalchemy_modules = _dml_bindings(tree)
    found: list[str] = []

    def report(node: ast.AST, form: str) -> None:
        found.append(f"{path}:{node.lineno}: {form}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_debt_ref(node.func, debt_names):
                report(node, "Debt(...) constructed")
            verb = _dml_verb(node.func, dml_aliases, sqlalchemy_modules)
            if verb is not None and node.args and _is_debt_ref(node.args[0], debt_names):
                report(node, f"{verb}(Debt)")
        if debt_names and isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for target in _assigned_leaves(node):
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
        # The payment writer: `engine.py` until 019 stage 4 deleted it, the service since.
        "app/core/payments/service.py",
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
    # Manifest T1808 item 15 (stage-A external review, 2026-09-24): the forms the first version
    # could not see.
    "amount in a tuple target": """
        from app.db.models import Debt
        async def f(debt, x, y):
            debt.amount, other = x, y
    """,
    "amount in a nested list target": """
        from app.db.models import Debt
        async def f(debt, pairs):
            [first, (debt.amount, second)] = pairs
    """,
    "amount behind a star": """
        from app.db.models import Debt
        async def f(debt, values):
            head, *debt.amount = values
    """,
    "session.delete of a Debt named by a tuple amount assignment": """
        from app.db.models import Debt
        async def f(session, debt, x):
            debt.amount, n = x, 0
            await session.delete(debt)
    """,
    "sqlalchemy.update(Debt)": """
        import sqlalchemy
        from app.db.models import Debt
        async def f(session):
            await session.execute(sqlalchemy.update(Debt).values(amount=1))
    """,
    "sa.delete(Debt)": """
        import sqlalchemy as sa
        from app.db.models import Debt
        async def f(session):
            await session.execute(sa.delete(Debt))
    """,
    "sqlalchemy.sql.expression.insert(Debt)": """
        import sqlalchemy
        from app.db.models import Debt
        async def f(session):
            await session.execute(sqlalchemy.sql.expression.insert(Debt).values(amount=1))
    """,
    "postgresql.insert(Debt) from a sqlalchemy submodule": """
        from sqlalchemy.dialects import postgresql
        from app.db.models import Debt
        async def f(session):
            await session.execute(postgresql.insert(Debt).values(amount=1))
    """,
    "aliased update": """
        from sqlalchemy import update as sa_update
        from app.db.models import Debt
        async def f(session):
            await session.execute(sa_update(Debt).values(amount=1))
    """,
    "aliased delete": """
        from sqlalchemy.sql import delete as remove
        from app.db.models import Debt
        async def f(session):
            await session.execute(remove(Debt))
    """,
    "aliased postgresql insert": """
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.db.models import Debt
        async def f(session):
            await session.execute(pg_insert(Debt).values(amount=1).on_conflict_do_nothing())
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
    # Counter-checks for the item-15 extension: the widened recognition must not swallow these.
    "a tuple assignment of other attributes": """
        from app.db.models import Debt
        async def f(debt, x, y):
            debt.version, debt.updated_at = x, y
    """,
    "sa.select(Debt) and sa.update of another model": """
        import sqlalchemy as sa
        from app.db.models import Debt, TrustLine
        async def f(session):
            await session.execute(sa.select(Debt))
            await session.execute(sa.update(TrustLine).values(limit=1))
    """,
    "aliased sqlalchemy update of another model": """
        from sqlalchemy import update as sa_update
        from app.db.models import Debt, TrustLine
        async def f(session):
            await session.execute(sa_update(TrustLine).values(limit=1))
    """,
    "update() on something that is not a sqlalchemy module": """
        from app.db.models import Debt
        def f(registry):
            registry.update(Debt)
            {}.update(Debt)
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
