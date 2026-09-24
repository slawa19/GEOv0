"""019 `T1903`: every lock primitive lives in `app/core/money_boundary.py`, and nobody reaches it through the engine.

WHY. Stage 4 of 019 deletes `app/core/payments/engine.py`. The lock primitives (owner, staged owner,
session owner, transaction and pair locks, their keys and namespaces), the stop/hold guard with its refusal
constants and factories, and the payment delta check moved to `MoneyBoundary` in stage 2 so that the
deletion cannot silently drop the coordination clearing, admin, the inject, the tick and reconciliation
depend on (third consultation, `FORK-2`). This guard keeps it that way until stage 5 removes the
primitives on evidence:

1. **No second home.** No module other than `money_boundary.py` defines the lock namespaces, the drift
   tolerance or a lock-key function, nor spells a namespace tag (`0x474551`, `0x475458`) as a literal.
   And no class in `app/` other than `MoneyBoundary` defines a method under a moved name - an override
   in `PaymentEngine` would make a test that patches `MoneyBoundary` pass without its perturbation ever
   running on the payment path. The one wrapper that shares a name is listed with its reason.
2. **No access through the engine** in `app/`, `tests/` and `scripts/`: no `from ...engine import X`,
   no `PaymentEngine.X`, no `PaymentEngine(session).X`, no `engine_module.X`, no `setattr(PaymentEngine,
   "X", ...)` and no dotted patch string `"app.core.payments.engine[.PaymentEngine].X"` for a moved `X`.

WHAT IT DOES NOT SEE - its silence is not proof of these: access through an engine INSTANCE held in a
variable (`engine = PaymentEngine(s); engine._acquire_tx_advisory_lock(...)` - legal today, the instance
IS a `MoneyBoundary`, and those engine-behaviour tests go with the engine at stage 4); `getattr` with a
computed name; a namespace tag computed rather than written; raw SQL that takes an advisory lock with its
own numbers; prose in comments and docstrings. What decides whether a move changed behaviour is the
statement-sequence probe `tests/p019_t1903_statement_sequence_probe.py` and the tier, not this file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core.money_boundary import (
    _EQUIVALENT_OWNER_LOCK_NAMESPACE,
    _TX_ADVISORY_LOCK_NAMESPACE,
    MoneyBoundary,
)
from app.core.payments.engine import PaymentEngine

REPO = Path(__file__).resolve().parents[2]
HOME = "app/core/money_boundary.py"
ENGINE_MODULE = "app.core.payments.engine"
SCANNED_ROOTS = ("app", "tests", "scripts")

#: Every name stage 2 moved out of the engine (019 spec, "Дом примитивов локов"; manifest §3).
MOVED = frozenset(
    {
        "_EQUIVALENT_OWNER_LOCK_NAMESPACE",
        "_TX_ADVISORY_LOCK_NAMESPACE",
        "_DELTA_DRIFT_TOLERANCE",
        "_segment_lock_key",
        "_tx_lock_key",
        "_equivalent_owner_lock_key",
        "_acquire_equivalent_owner_locks",
        "acquire_staged_equivalent_owner_locks",
        "acquire_session_equivalent_owner_lock",
        "release_session_equivalent_owner_lock",
        "_acquire_tx_advisory_lock",
        "_acquire_segment_advisory_locks",
        "_acquire_segment_advisory_lock_keys",
        "_set_local_advisory_lock_timeout",
        "EQUIVALENT_INACTIVE_REASON",
        "EQUIVALENT_INTEGRITY_HOLD_REASON",
        "MONEY_STOP_REASONS",
        "inactive_equivalent_conflict",
        "integrity_hold_conflict",
        "refuse_inactive_equivalents",
        "check_payment_delta",
        "_snapshot_net_positions",
    }
)
MODULE_CONSTANTS = frozenset(
    {"_EQUIVALENT_OWNER_LOCK_NAMESPACE", "_TX_ADVISORY_LOCK_NAMESPACE", "_DELTA_DRIFT_TOLERANCE"}
)
KEY_FUNCTIONS = frozenset({"_segment_lock_key", "_tx_lock_key", "_equivalent_owner_lock_key"})
#: Read from the home, never spelled here - a literal in this file would be the very finding it looks for.
NAMESPACE_TAGS = frozenset({_EQUIVALENT_OWNER_LOCK_NAMESPACE, _TX_ADVISORY_LOCK_NAMESPACE})

#: A method in `app/` that shares a moved name and is NOT a second primitive, with the reason.
ALLOWED_METHODS = {
    ("app/core/payments/service.py", "PaymentService", "acquire_staged_equivalent_owner_locks"):
        "resolves equivalent CODES to ids and calls MoneyBoundary.acquire_staged_equivalent_owner_locks",
}

_PATCH_STRING = re.compile(r"app\.core\.payments\.engine\.(?:PaymentEngine\.)?(\w+)")


def findings_for(source: str, relative: str) -> list[str]:
    """Every violation of rules 1 and 2 in one module's source."""

    tree = ast.parse(source)
    found: list[str] = []
    engine_classes = {"PaymentEngine"}
    engine_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == ENGINE_MODULE:
            for alias in node.names:
                if alias.name in MOVED:
                    found.append(f"{relative}:{node.lineno}: imports `{alias.name}` from the engine")
                if alias.name == "PaymentEngine":
                    engine_classes.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "app.core.payments":
            engine_modules.update(a.asname or a.name for a in node.names if a.name == "engine")
        elif isinstance(node, ast.Import):
            engine_modules.update(a.asname for a in node.names if a.name == ENGINE_MODULE and a.asname)

    def is_engine(expr: ast.AST) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in engine_classes or expr.id in engine_modules
        if isinstance(expr, ast.Call):
            return isinstance(expr.func, ast.Name) and expr.func.id in engine_classes
        dotted = ast.unparse(expr)
        return dotted in {ENGINE_MODULE, f"{ENGINE_MODULE}.PaymentEngine"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in MOVED and is_engine(node.value):
            found.append(f"{relative}:{node.lineno}: `{ast.unparse(node)}` reaches a moved name through the engine")
        if isinstance(node, ast.Call) and len(node.args) >= 2:
            callee = ast.unparse(node.func)
            name = node.args[1]
            if (
                callee.endswith("setattr")
                and isinstance(name, ast.Constant)
                and name.value in MOVED
                and is_engine(node.args[0])
            ):
                found.append(f"{relative}:{node.lineno}: patches `{name.value}` on the engine")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = _PATCH_STRING.fullmatch(node.value)
            if match and match.group(1) in MOVED:
                found.append(f"{relative}:{node.lineno}: patch string `{node.value}` targets the engine")

    if relative == HOME:
        return found

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in MODULE_CONSTANTS:
                found.append(f"{relative}:{node.lineno}: defines `{target.id}` outside {HOME}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in KEY_FUNCTIONS:
            found.append(f"{relative}:{node.lineno}: defines lock-key function `{node.name}` outside {HOME}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and node.value in NAMESPACE_TAGS
        ):
            found.append(f"{relative}:{node.lineno}: spells lock namespace tag {node.value:#x} outside {HOME}")

    if relative.startswith("app/"):
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for item in cls.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name in MOVED
                    and (relative, cls.name, item.name) not in ALLOWED_METHODS
                ):
                    found.append(
                        f"{relative}:{item.lineno}: `{cls.name}.{item.name}` redefines a moved primitive "
                        f"outside {HOME}"
                    )
    return found


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        files.extend(p for p in (REPO / root).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def _tree_findings() -> list[str]:
    found: list[str] = []
    for path in _scanned_files():
        found.extend(findings_for(path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix()))
    return found


def test_no_lock_primitive_has_a_second_home_or_is_reached_through_the_engine() -> None:
    found = _tree_findings()
    assert not found, (
        "A lock primitive, stop/hold constant or the delta check is defined outside "
        f"{HOME} or reached through `app.core.payments.engine`:\n  "
        + "\n  ".join(found)
        + "\n\nImport it from `app.core.money_boundary` (`MoneyBoundary.<name>`, or the module constant) "
        "and patch `MoneyBoundary`, not `PaymentEngine`. This guard checks FORM only - see the module "
        "docstring for what it cannot see; whether a move changed behaviour is decided by "
        "`tests/p019_t1903_statement_sequence_probe.py` and the tier."
    )


def test_the_scan_is_not_vacuous() -> None:
    """The scan reads the tree, the home really holds every moved name, and consumers really use it."""

    files = {p.relative_to(REPO).as_posix() for p in _scanned_files()}
    assert len(files) > 400, f"only {len(files)} files scanned under {SCANNED_ROOTS}"
    for expected in (
        HOME,
        "app/core/payments/engine.py",
        "app/core/clearing/service.py",
        "app/api/v1/admin.py",
        "tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py",
    ):
        assert expected in files, f"{expected} was not scanned"

    home = ast.parse((REPO / HOME).read_text(encoding="utf-8"))
    defined = {
        target.id
        for node in home.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    boundary = next(n for n in home.body if isinstance(n, ast.ClassDef) and n.name == "MoneyBoundary")
    for item in boundary.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(item.name)
        elif isinstance(item, ast.Assign):
            defined.update(t.id for t in item.targets if isinstance(t, ast.Name))
    assert MOVED <= defined, f"MOVED names {sorted(MOVED - defined)} are not defined in {HOME}"
    tags = {n.value for n in ast.walk(home) if isinstance(n, ast.Constant) and isinstance(n.value, int)}
    assert NAMESPACE_TAGS <= tags, "the namespace tags are not spelled in the home any more"

    # Each app consumer that the owner surface names reaches the primitives through MoneyBoundary.
    for consumer in (
        "app/api/v1/admin.py",
        "app/api/v1/simulator.py",
        "app/core/clearing/service.py",
        "app/core/ledger/reconciliation.py",
        "app/core/payments/service.py",
        "app/core/simulator/real_runner_impl.py",
        "app/core/simulator/real_clearing_engine.py",
    ):
        assert "MoneyBoundary" in (REPO / consumer).read_text(encoding="utf-8"), consumer


def test_the_engine_inherits_the_primitives_and_overrides_none() -> None:
    """A patch of `MoneyBoundary.<primitive>` must reach the payment path: the engine may not shadow it."""

    assert issubclass(PaymentEngine, MoneyBoundary)
    shadowed = sorted(name for name in MOVED if name in vars(PaymentEngine))
    assert not shadowed, f"PaymentEngine shadows moved primitives: {shadowed}"


# --- counter-checks: the rules fire on each shape they claim, and stay quiet on the legal ones ---------

_VIOLATIONS = {
    "import-from-engine": "from app.core.payments.engine import _EQUIVALENT_OWNER_LOCK_NAMESPACE\n",
    "class-attribute": "from app.core.payments.engine import PaymentEngine\nPaymentEngine.MONEY_STOP_REASONS\n",
    "aliased-class": "from app.core.payments.engine import PaymentEngine as PE\nPE._equivalent_owner_lock_key(x)\n",
    "module-alias": "from app.core.payments import engine as em\nem._DELTA_DRIFT_TOLERANCE\n",
    "module-alias-setattr": (
        "import app.core.payments.engine as em\nmonkeypatch.setattr(em, '_DELTA_DRIFT_TOLERANCE', 1)\n"
    ),
    "class-setattr": "monkeypatch.setattr(PaymentEngine, 'refuse_inactive_equivalents', f)\n",
    "patch-string": "monkeypatch.setattr('app.core.payments.engine.PaymentEngine._acquire_tx_advisory_lock', f)\n",
    "instance-call": "async def f(s):\n    await PaymentEngine(s).acquire_staged_equivalent_owner_locks([1])\n",
    "dotted-module": "import app.core.payments.engine\napp.core.payments.engine.PaymentEngine.check_payment_delta\n",
    "constant-redefined": "_EQUIVALENT_OWNER_LOCK_NAMESPACE = 1\n",
    "key-function-redefined": "def _equivalent_owner_lock_key(equivalent_id):\n    return 1\n",
    "namespace-literal": "text('SELECT pg_advisory_xact_lock(:n, :k)'), {'n': 0x475458}\n",
}

_LEGAL = {
    "boundary-constant": "from app.core.money_boundary import MoneyBoundary\nMoneyBoundary.MONEY_STOP_REASONS\n",
    "boundary-patch": "monkeypatch.setattr(MoneyBoundary, 'refuse_inactive_equivalents', f)\n",
    "engine-non-moved": "PaymentEngine.commit\nmonkeypatch.setattr(PaymentEngine, 'commit', f)\n",
    "boundary-import": "from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE\n",
}


@pytest.mark.parametrize("name", sorted(_VIOLATIONS))
def test_counter_check_each_violation_shape_is_found(name: str) -> None:
    assert findings_for(_VIOLATIONS[name], "tests/unit/synthetic.py"), name


def test_counter_check_an_override_in_the_engine_is_found() -> None:
    source = (
        "class PaymentEngine(MoneyBoundary):\n"
        "    async def _acquire_equivalent_owner_locks(self, ids):\n"
        "        return None\n"
    )
    assert findings_for(source, "app/core/payments/engine.py")


@pytest.mark.parametrize("name", sorted(_LEGAL))
def test_counter_check_legal_shapes_are_not_found(name: str) -> None:
    assert findings_for(_LEGAL[name], "tests/unit/synthetic.py") == [], name


def test_counter_check_the_listed_wrapper_is_allowed_and_only_where_listed() -> None:
    source = "class PaymentService:\n    async def acquire_staged_equivalent_owner_locks(self, codes):\n        pass\n"
    assert findings_for(source, "app/core/payments/service.py") == []
    assert findings_for(source, "app/core/payments/other.py")
