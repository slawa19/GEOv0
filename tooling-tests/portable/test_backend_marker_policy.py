"""Form guard for the backend tier markers (U-G-1).

WHAT THIS IS: a check of `pytest.ini` and `scripts/verify_local.ps1` as the runner reads them: strict
markers stay on, and no empty tier marker is registered or filtered. It reads no documentation:
documentation is outside the checks (owner's decision 2026-10-16, programme 041 `T4102`; the former
command-form guard over six Markdown documents was deleted then). The shape of the
`required-backend` job is owned by `tooling-tests/portable/test_p017_required_gate_runs_on_postgres.py`.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


# U-G-1: with `--strict-markers` an unregistered marker cannot be applied (collection fails), so
# the obligation is: no empty tier is registered or filtered, and strict markers stay on.
def _empty_tier_violations(ini: str, runner: str, marker: str) -> list[str]:
    # The option line itself (an addopts continuation), not any mention: pytest.ini also names it in a comment.
    found = [] if re.search(r"(?m)^[ \t]+--strict-markers[ \t]*\r?$", ini) else ["pytest.ini lost --strict-markers"]
    if re.search(rf"(?m)^\s*{marker}(?:\([^)]*\))?\s*:", ini):
        found.append(f"pytest.ini registers {marker}")
    if f"not {marker}" in runner:
        found.append(f"verify_local.ps1 filters {marker}")
    if "'-m', 'not slow'" not in runner:  # anti-vacuum: the filter being checked was located
        found.append("verify_local.ps1 marker filter not found")
    return found


@pytest.mark.parametrize("marker", ["e2e", "scenario"])
def test_no_empty_backend_tier_is_registered_or_filtered(marker: str) -> None:
    ini = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    assert _empty_tier_violations(ini, runner, marker) == []
    planted_ini = ini.replace("markers =\n", f"markers =\n    {marker}: planted\n", 1)
    # Keeps the `'-m', 'not slow'` anchor, so only the filter rule itself can fire.
    planted_runner = runner.replace("'-m', 'not slow'", f"'-m', 'not slow', 'and not {marker}'", 1)
    assert planted_ini != ini and planted_runner != runner
    assert _empty_tier_violations(planted_ini, runner, marker) != []
    assert _empty_tier_violations(ini, planted_runner, marker) == [f"verify_local.ps1 filters {marker}"]
    # Remove only the option line; the comment that mentions the flag stays, as in the real file.
    no_option = re.sub(r"(?m)^[ \t]+--strict-markers[ \t]*\r?\n", "", ini, count=1)
    assert no_option != ini and "--strict-markers" in no_option
    assert _empty_tier_violations(no_option, runner, marker) == ["pytest.ini lost --strict-markers"]
