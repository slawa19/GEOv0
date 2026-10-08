"""The backend tier counts what it collects (035 F-035-14, slice C1).

WHY. The tooling tier refuses a count that differs from `EXPECTED_CASES` (`tooling-tests/conftest.py`). The backend
tier had no such count: a deleted module, a `--ignore` or a `--deselect` on the canonical run ended green. Measured
2026-10-08 on `75dafc82`, direct pytest: one `--deselect` of one case, 3149 selected / 16 deselected, exit 0, and
nothing in the session noticed. This module is the whole mechanism: one constant, one comparison, one profile test;
`tests/conftest.py` calls it from `pytest_collection_finish`.

THE RULE. On the CANONICAL PROFILE the number of selected cases must equal `EXPECTED_SELECTED_ITEMS`, in EITHER
direction (a lost case and an unrecorded new case fail alike, AGENTS.md section 6). A mismatch ends the session with
exit 4 (`pytest.ExitCode.USAGE_ERROR`) at the end of collection, before any test body runs, so it also holds under
`--collect-only`.

WHAT THE CANONICAL PROFILE IS, decided from the session's own options and never from an environment variable that
can be forgotten:

* the marker expression is exactly `not slow` - what `scripts/verify_local.ps1 -BackendOnly` passes by default;
* the positional arguments, if any, name the whole `tests` directory and nothing narrower (no argument at all means
  `testpaths = tests`). Any narrower path, `path::node` or `-- <paths>` is `-BackendSelector`: a deliberate narrowing
  that is NOT counted.

Everything else that reduces or reorders the selection is NOT an exemption, so the rule is not vacuous:
`--deselect`, `--ignore`, `--ignore-glob`, `-k`, `--lf`, `--sw`. Run them with a path selector, or accept the refusal.

NOT COUNTED BY THIS RULE: a run with a selector (`-BackendSelector`), and a run with no marker expression
(`-IncludeExpensive`, the wider profile: nothing is excluded, the 15 `slow` cases come back). The wide profile has no
constant of its own because the rule asks for one number; it is the canonical number plus the `slow` cases.

PLATFORM. `skipped` cases are COLLECTED cases, so the number is the same on every platform as long as collection does
not depend on the platform. Checked 2026-10-08 by reading (grep over `tests/`): no `collect_ignore`, no
`pytest_ignore_collect`, no `allow_module_level` skip, no platform branch in a parametrize list, no `importorskip`
that removes a module; the only platform conditions are the three `skipif` listed below, which skip a collected case.
What this does NOT prove: it is a reading, not a Linux run - the first `required-backend` run after this change is the
proof (AGENTS.md section 16, item 6). Parametrize lists built from the working tree would also move the count on a
developer machine with an untracked file; the tier has none today (the file scans in `tests/unit` assert inside the
test, they do not parametrize).

CHANGING THE NUMBER. Add, move or delete a test - then change `EXPECTED_SELECTED_ITEMS` in the same commit with a dated
line below saying why. That is the only way to make the session green again:

* 2026-10-08, 035 slice C1 (F-035-14): first value, 3150 (3165 with the 15 `slow` cases). Measured with
  `python -m pytest --collect-only -q -m "not slow"` on `75dafc82`; on Windows the full run was
  `3144 passed, 2 skipped, 3 xfailed, 16 deselected` with one case deselected, which is 3149 + 1.

SKIPPED CASES OF THE CANONICAL RUN, NAMED (`-rs` shows them; compare the number in the CI log with this list):

* Windows (`os.name == "nt"`): 2 - `tests/unit/test_deployment_config.py::test_entrypoint_preserves_custom_command_after_migrations`
  (no POSIX shell) and `tests/unit/test_settings_guardrails.py` case under `skipif(os.name == "nt")` at line 260
  (Windows environment keys are case-insensitive). Measured 2026-10-08: `SKIPPED [1] ...test_deployment_config.py:131`,
  `SKIPPED [1] ...test_settings_guardrails.py:260`.
* Linux (`os.name == "posix"`): 1 - the case under `skipif(os.name != "nt")` in
  `tests/unit/test_p024_scenario_id_is_a_safe_path_segment.py` at line 188 (Windows device names). INFERRED from the
  conditions, not measured on Linux; the CI log of `required-backend` is the measurement.
* Neither platform on the full tier: `tests/contract/test_p011_responses_conform_to_the_canon.py` skips the aggregate only
  when the session was vacuous (`_vacuity_skip_reason`) or `GEO_CONFORMANCE_NO_SUBPROCESS` is set - a full run is neither.
  Expected strict `xfail` cases: 3 on the Windows run (`xfailed`), not named here.

`tooling-tests/portable/test_p035_c_backend_tier_counts_what_it_collects.py` holds that each named skip is still
declared where this list says it is.

WHAT THIS DOES NOT SEE. WHICH tests are present: a case replaced by another under the same number passes. Whether the
number was RIGHT when recorded: it is a measurement of the tier at the commit that changed it. A reconfiguration of the
runner itself (`-o addopts=...`, `-p no:...`, `PYTEST_ADDOPTS` that ends pytest before the end of collection): the
runner is not defended against reconfiguring itself (the same limit as `tooling-tests/conftest.py`). A collection that
ERRORS (import failure): pytest stops before this hook with its own exit code 2, which is loud on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: THE EXPECTED NUMBER OF SELECTED CASES OF THE CANONICAL PROFILE (parametrised cases count one each).
#: Moves only by the dated lines in the module docstring.
EXPECTED_SELECTED_ITEMS = 3150

#: The marker expression `scripts/verify_local.ps1` passes without `-IncludeExpensive`.
CANONICAL_MARKEXPR = "not slow"

#: The directory whose collection is the whole tier (`pytest.ini` `testpaths`).
TIER_DIRECTORY = "tests"


def is_canonical_profile(
    *,
    markexpr: str | None,
    args: Sequence[str],
    invocation_dir: Path,
    root: Path,
) -> bool:
    """True when the session collects the whole tier under the canonical marker expression.

    `args` are the session's positional arguments after pytest has substituted `testpaths` for an empty list.
    A `path::node` argument is a selector; so is every path other than the tier directory itself.
    """

    if str(markexpr or "").strip() != CANONICAL_MARKEXPR:
        return False
    if not args:
        return True
    tier = (root / TIER_DIRECTORY).resolve()
    for argument in args:
        if "::" in argument:
            return False
        if (invocation_dir / argument).resolve() != tier:
            return False
    return True


def count_problem(*, selected: int, expected: int = EXPECTED_SELECTED_ITEMS) -> str | None:
    """None when the count matches; otherwise the refusal text, for either direction."""

    if selected == expected:
        return None
    direction = (
        "FEWER than recorded: a test was lost - look for a deleted or renamed module, a --deselect, a --ignore, "
        "a -k, or a marker that now excludes it."
        if selected < expected
        else "MORE than recorded: a test was added - record the new number."
    )
    return (
        f"the canonical backend profile (whole tier, -m 'not slow', no path selector) selected {selected} case(s), "
        f"expected exactly {expected} (EXPECTED_SELECTED_ITEMS in tests/tier_count.py): {direction} "
        "If the change is intended, set the constant in the same commit and add a dated line to the docstring of "
        "tests/tier_count.py saying why. To run a subset on purpose, give a path selector "
        "(scripts/verify_local.ps1 -BackendSelector <paths>) or run without -m (-IncludeExpensive); neither is counted."
    )
