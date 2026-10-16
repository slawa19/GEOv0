"""The `b4_counterexample` marker stays retired: `pytest.ini` does not register it.

Programme 015 phase B step 2 put 107 counterexamples in the tree that were red on purpose and carried
`pytest.mark.b4_counterexample`; the runner deselected that marker from every tier. Step 4 slice C armed
`app/core/ledger/journal.py`, the counterexamples went green through the journal with their assertions
untouched, and the marker was removed from `pytest.ini`, `scripts/verify_local.ps1` and seven modules.

Under `--strict-markers` an unregistered marker fails collection, so registration is the one door through
which the marker could come back and take a real failure out of the gate (`AGENTS.md` §5). Narrowed
2026-10-16 (041 `T4104`) to that one check: the AST scan of `tests/` and the string scan of the runner were
second lines behind it; `--strict-markers` itself is held by
`tooling-tests/portable/test_backend_marker_policy.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MARKER = "b4_counterexample"


def test_pytest_does_not_register_the_marker() -> None:
    config = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    # Anti-vacuum: the `markers` list this check reads is where it is expected.
    assert re.search(r"(?m)^markers\s*=", config), "pytest.ini has no `markers` list any more"
    assert not re.search(rf"(?m)^\s+{_MARKER}\s*(?:\(|:)", config), (
        f"`{_MARKER}` is registered in pytest.ini again. The marker was retired with programme 015 step 4 "
        f"and the counterexamples it covered run in both canonical tiers; registering it again can only "
        f"take a real failure out of the gate."
    )
