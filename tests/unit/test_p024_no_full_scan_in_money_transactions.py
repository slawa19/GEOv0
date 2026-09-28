"""R-024-12 (programme 024, step Ш3, `T2413.2`): the full-equivalent checkpoint stays out of money transactions.

`compute_integrity_checkpoint_for_equivalent` reads every debt and trust line of an equivalent and runs the
invariant checks over all of them. Since `T2413.2` it is reached only by the periodic checkpoint job
(`compute_and_store_integrity_checkpoints`, the module that defines it) and by the explicit
`POST /integrity/verify`; a payment, a clearing or a trust-line batch records its audit row with
`verification_passed = null` instead (spec 024, "Ш3 (`T2413`)").

WHAT THIS GUARD SEES, AND WHAT IT DOES NOT. It is a FORM check over the AST of `app/`: any import of the name,
any bare name or attribute access spelled `compute_integrity_checkpoint_for_equivalent`, outside the two
allowed modules. It does not see a call through `getattr(module, "<name>")`, a re-export under another
name made inside an allowed module, or a different full scan written by hand. If it fails: move the call
out of the money path (periodic job or explicit verify); if the call is genuinely not in a money
transaction, the allowlist below is the place to argue it, with the spec entry that decides it.
"""

from __future__ import annotations

import ast
from pathlib import Path

NAME = "compute_integrity_checkpoint_for_equivalent"
ALLOWED = {"app/core/integrity.py", "app/api/v1/integrity.py"}
REPO = Path(__file__).resolve().parents[2]


def references(source: str) -> list[int]:
    """Line numbers where `source` imports, names or reaches the full-equivalent checkpoint."""

    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and any(alias.name == NAME for alias in node.names):
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == NAME:
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == NAME:
            lines.append(node.lineno)
    return sorted(set(lines))


def _scan() -> dict[str, list[int]]:
    found = {}
    for path in sorted((REPO / "app").rglob("*.py")):
        relative = path.relative_to(REPO).as_posix()
        hits = references(path.read_text(encoding="utf-8"))
        if hits:
            found[relative] = hits
    return found


def test_no_money_writer_reaches_the_full_equivalent_checkpoint() -> None:
    found = _scan()
    outside = {path: lines for path, lines in found.items() if path not in ALLOWED}
    assert outside == {}, (
        f"{NAME} is reached outside the periodic job and the explicit verify: {outside}. "
        "This is a form check (see the module docstring for what it cannot see)."
    )
    # Anti-vacuum: the scan still finds the one allowed caller, so an emptied result means "none", not "blind".
    assert "app/api/v1/integrity.py" in found, found


def test_the_guard_sees_a_planted_call() -> None:
    planted = {
        "from app.core.integrity import compute_integrity_checkpoint_for_equivalent as cp\n": [1],
        "import app.core.integrity as i\nasync def f(s, e):\n"
        "    return await i.compute_integrity_checkpoint_for_equivalent(s, equivalent_id=e)\n": [3],
        "async def f(s, e):\n    return await compute_integrity_checkpoint_for_equivalent(s, equivalent_id=e)\n": [2],
    }
    for source, lines in planted.items():
        assert references(source) == lines, source
    assert references("async def f(s):\n    return await compute_and_store_integrity_checkpoints(s)\n") == []
