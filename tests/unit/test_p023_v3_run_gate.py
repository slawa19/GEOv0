"""023 protocol v3: the RUN-level gate and the process exit code (cross-pass finding F1 + F3, 2026-09-26).

The run passes only with all 212 cells present and every one of them PASS; a run that does not pass must leave the
process with a non-zero exit code, so a caller (a shell, a CI step, an agent reading only `$LASTEXITCODE`) cannot
take a FAIL or a short run for a pass.

WHAT THIS DOES NOT SEE: the cell judge (`test_p023_v3_acceptance_protocol.py`), the databases and child processes
of `main()`, and the recorded v3 result, which this does not re-run or alter. The exit code is checked on the
function `main()` returns through (`exit_code`), not by running `main()` against a server.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def v3():
    """The runner (and the v1/v2/020 modules under it) rewrite DATABASE_URL at import; restore the environment."""

    saved = dict(os.environ)
    try:
        from scripts import measure_p023_planner_acceptance_v3 as module
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return module


def _cells(n: int = 212, failed: int = 0) -> list[dict]:
    return [{"verdict": "FAIL" if i < failed else "PASS"} for i in range(n)]


def test_all_212_cells_passing_is_the_only_pass(v3) -> None:
    assert v3.run_verdict(_cells()) == "PASS"
    assert v3.exit_code("PASS") == 0


def test_a_missing_cell_fails_the_run(v3) -> None:
    assert v3.run_verdict(_cells(211)) == "FAIL"
    assert v3.run_verdict([]) == "FAIL"  # anti-vacuum: no cells is not "every cell passed"


def test_an_extra_cell_fails_the_run(v3) -> None:
    assert v3.run_verdict(_cells(213)) == "FAIL"


def test_a_failed_cell_fails_the_run(v3) -> None:
    assert v3.run_verdict(_cells(failed=1)) == "FAIL"
    assert v3.run_verdict(_cells(failed=212)) == "FAIL"


def test_any_non_pass_verdict_exits_non_zero(v3) -> None:
    for verdict in ("FAIL", "", None, "pass", "UNVERIFIED"):
        assert v3.exit_code(verdict) != 0, verdict
    for cells in (_cells(211), _cells(failed=1)):
        assert v3.exit_code(v3.run_verdict(cells)) != 0
