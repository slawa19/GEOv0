"""019 `T1902` №8 / `T1906`: no application code writes an intermediate PAYMENT state or uses the engine.

Programme 019 (`specs/019-payment-one-transaction/spec.md`, Verification plan §1, the structural item):
an AST scan of `app/` for

* a write of a `PAYMENT` `transactions.state` from `NEW/ROUTED/PREPARE_IN_PROGRESS/PREPARED/PROPOSED/
  WAITING` - a `Transaction(...)` construction with such a `state=` (or with NO `state=`: the model's
  default is `NEW`), an `.values(state=...)` of an insert/update, an assignment `<x>.state = ...`;
* an import of `PaymentEngine` (the two-phase engine) and of `PrepareLock` (the reservations).

Expected: nothing. Since stage 4, part b (`T1906`) the whole tree is held: the engine and recovery are
deleted and admin abort no longer uses the engine, so the part-b allowed set that stage 4, part a kept
is gone and the whole-tree node is an ordinary green assertion (its `xfail` taken off). Since stage 5 (`T1909`) the reservations'
node is green too: the `PrepareLock` model, its module and every reader are deleted (migration `031`
drops the table), and the node's strict `TargetMismatch` expectation is taken off.

WHAT THIS DOES NOT SEE, and why it is only a guard of FORM: raw SQL text (`text("UPDATE transactions
SET state = 'NEW'")`), `setattr(tx, "state", ...)`, a state carried in a dict that is unpacked into a
call (`Transaction(**row)` with `row["state"]`), a value computed at run time. Those are closed by
behaviour, not by this scan: the immediate CHECK of migration `030` refuses a non-terminal `PAYMENT`
row whatever wrote it (stage 4, part b), and the payment path's own tests observe the rows it leaves.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from tests.p019_support import require_target

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "app"

INTERMEDIATE_STATES = frozenset(
    {"NEW", "ROUTED", "PREPARE_IN_PROGRESS", "PREPARED", "PROPOSED", "WAITING"}
)



@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    what: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.what}"


def _constant(node: ast.AST | None) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _called_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def scan_source(source: str, path: str) -> tuple[list[Finding], list[Finding], int]:
    """(state writes, forbidden imports, Transaction constructions seen) of one module's source."""

    tree = ast.parse(source, filename=path)
    writes: list[Finding] = []
    imports: list[Finding] = []
    constructions = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
            name = _called_name(node.func)
            if name == "Transaction":
                constructions += 1
                tx_type = _constant(keywords.get("type"))
                if tx_type is not None and tx_type != "PAYMENT":
                    continue
                if "state" not in keywords:
                    writes.append(
                        Finding(path, node.lineno, "Transaction(...) without state= (model default NEW)")
                    )
                    continue
                state = _constant(keywords["state"])
                if state in INTERMEDIATE_STATES:
                    writes.append(Finding(path, node.lineno, f"Transaction(state={state!r})"))
            elif name == "values":
                state = _constant(keywords.get("state"))
                if state in INTERMEDIATE_STATES:
                    writes.append(Finding(path, node.lineno, f".values(state={state!r})"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            state = _constant(node.value)
            if state in INTERMEDIATE_STATES:
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "state":
                        writes.append(Finding(path, node.lineno, f".state = {state!r}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if module == "app.core.payments.engine" or alias.name == "PaymentEngine":
                    imports.append(Finding(path, node.lineno, f"from {module} import {alias.name}"))
                elif alias.name == "PrepareLock" or module == "app.db.models.prepare_lock":
                    imports.append(Finding(path, node.lineno, f"from {module} import {alias.name}"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"app.core.payments.engine", "app.db.models.prepare_lock"}:
                    imports.append(Finding(path, node.lineno, f"import {alias.name}"))
    return writes, imports, constructions


def _scan_app() -> tuple[list[Finding], list[Finding], int]:
    writes: list[Finding] = []
    imports: list[Finding] = []
    constructions = 0
    for file in sorted(_APP.rglob("*.py")):
        rel = file.relative_to(_ROOT).as_posix()
        w, i, c = scan_source(file.read_text(encoding="utf-8"), rel)
        writes += w
        imports += i
        constructions += c
    return writes, imports, constructions


_BLIND = (
    "This scan sees FORM only: raw SQL, setattr and states carried in unpacked dicts are invisible to it "
    "(closed by the CHECK of migration 030 and by the payment path's behavioural tests)."
)


def _is_engine_import(finding: Finding) -> bool:
    return "PaymentEngine" in finding.what or "app.core.payments.engine" in finding.what


def _is_prepare_lock_import(finding: Finding) -> bool:
    return "PrepareLock" in finding.what or "prepare_lock" in finding.what


# ── the scanner itself: anti-vacuum ──────────────────────────────────────────────────────────────


def test_the_scanner_sees_each_form_it_claims_and_passes_the_ones_it_allows():
    source = '''
from app.core.payments.engine import PaymentEngine
from app.db.models.prepare_lock import PrepareLock
import app.core.payments.engine
a = Transaction(type="PAYMENT", state="NEW")
b = Transaction(tx_id="x")
c = update(Transaction).values(state="PREPARED")
tx.state = "WAITING"
ok1 = Transaction(type="CLEARING", state="NEW")
ok2 = Transaction(type="PAYMENT", state="COMMITTED")
ok3 = update(Transaction).values(state="ABORTED")
run.state = "running"
'''
    writes, imports, constructions = scan_source(source, "probe.py")
    assert constructions == 4
    assert sorted(f.line for f in writes) == [5, 6, 7, 8], writes
    assert len(imports) == 3, imports
    assert sum(_is_engine_import(f) for f in imports) == 2
    assert sum(_is_prepare_lock_import(f) for f in imports) == 1


def test_the_scan_reads_the_real_tree():
    """Not vacuous on the tree: the clearing's own `NEW` construction is seen and is not a finding."""

    writes, _imports, constructions = _scan_app()
    assert constructions >= 1, "no Transaction(...) construction found in app/ - the scan reads nothing"
    assert not any(f.path == "app/core/clearing/service.py" for f in writes), writes


# ── the targets ──────────────────────────────────────────────────────────────────────────────────


def test_no_application_module_writes_an_intermediate_payment_state_or_uses_the_engine():
    """Stage 4: no module of `app/` writes a non-terminal PAYMENT state or uses the payment engine.

    Red (`TargetMismatch`) on `7e16dd5` for `app/core/payments/service.py` (it inserted `NEW` and
    imported the engine) - green since stage 4, part a (`82cd214`) for every module outside the part-b
    set; the part-b set (`engine.py`, `recovery.py`, admin abort's engine use) kept this whole-tree
    node a strict `xfail` until stage 4, part b removed them. A regression is now an ordinary failure.
    """

    writes, imports, _ = _scan_app()
    engine = [f for f in imports if _is_engine_import(f)]
    require_target(
        not writes and not engine,
        "intermediate PAYMENT state writes or PaymentEngine imports: "
        + "; ".join(map(str, writes + engine))
        + ". "
        + _BLIND,
    )


def test_no_application_module_uses_the_reservations():
    """Stage 5 (`T1909`): the reservations are gone - no model module, no import of it anywhere in `app/`.

    A strict `xfail` (`TargetMismatch`) until stage 5; its model module and every reader are deleted now.
    """

    assert not (_APP / "db" / "models" / "prepare_lock.py").exists(), "the PrepareLock model module is back"
    _writes, imports, _ = _scan_app()
    readers = [f for f in imports if _is_prepare_lock_import(f)]
    require_target(not readers, "PrepareLock readers: " + "; ".join(map(str, readers)) + ". " + _BLIND)
