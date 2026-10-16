"""The whole backend tier is run unnarrowed, and the five A10 cases are checked by name (041 F-041-1, 2026-10-16).

THERE IS NO COUNT TO MAINTAIN. Until 2026-10-16 this module held `EXPECTED_SELECTED_ITEMS`, an exact two-sided count of
the selected cases (035 F-035-14, introduced in `7edf4cb8`), and a dated journal of every edit of it. Every added or
removed test, and every change of a parametrize source, had to move the constant in the same commit. The owner's decision
of 2026-10-16 (programme 041, `specs/041-verification-diet/spec.md`, F-041-1) replaced the count with a ban on the flags
that narrow the whole tier; the constant and its journal are in git history (`git log -S EXPECTED_SELECTED_ITEMS --
tests/tier_count.py`). Do not add a number, a lower bound or a journal back: adding or deleting a test needs no edit here.

WHAT IS CHECKED (`tests/conftest.py`, `pytest_collection_finish`, exit 4 = `pytest.ExitCode.USAGE_ERROR` at the end of
collection, before any test body runs, so it also holds under `--collect-only`):

1. THE WHOLE TIER IS NOT NARROWED BY A FLAG. A session whose positional arguments name the whole `tests` directory (or
   none: `testpaths = tests`; `-BackendSelector tests` and `-- tests` are the whole tier too) is refused when the session's
   own options carry any of:
   * `--deselect`, `--ignore`, `--ignore-glob`, `-k` - each removes cases from the run by name or pattern;
   * `--lf` / `--last-failed` - runs only the previous failures;
   * `--sw` / `--stepwise`, `--sw-skip` - stops at a failure and on the next run skips what passed before;
   * a marker expression other than `not slow` (the canonical profile, `scripts/verify_local.ps1 -BackendOnly`) or none
     (`-IncludeExpensive`, the wide profile). Spellings are compared after runs of whitespace are collapsed; an
     equivalent spelled differently, such as `(not slow)`, is REFUSED (before 2026-10-16 it silently turned the count off).
   The options are read from `config.option`, so the same flag given in `PYTEST_ADDOPTS` or in an `addopts` line of a
   configuration file is seen too. Deliberately ALLOWED on the whole tier: `--ff` / `--nf` (they reorder, every case still
   runs), `-x` / `--maxfail` (they stop a run that is already red), `--collect-only` (runs nothing and claims nothing; the
   checks of this module are asked with it).
   To run a subset on purpose, give a path narrower than `tests` (`scripts/verify_local.ps1 -BackendSelector <paths>`):
   such a run is a deliberate narrowing and no flag is refused in it.

2. THE A10 EXCEPTION, BY NAME (unchanged by 041; decision of 2026-10-10, AGENTS.md section 11). Exactly five cases of
   `tests/integration/test_p035_a10_pool_wait_is_inside_the_payment_deadline_postgres.py` wait for the product engine's
   REAL pool timeout (1 s) or the refusal recording's 500 ms grace, and they run in the required tier without `slow`: they
   verify the translation of that timeout and the bound of the refusal's recording, which nothing else in the required
   gate sees (no scheduled job selects them; `-IncludeExpensive` is used only by the simulator super-smoke). Their
   configured waits total 5.5 s. The list is CLOSED and owned here: `A10_REAL_POOL_TIMEOUT_CASES`. They carry the
   registered marker `a10_real_pool_timeout` and no `slow`. `tests/conftest.py` evaluates `a10_exception_problem` on the
   collection BEFORE any deselection, on every run, and ends the session with exit 4 when a listed case the REQUESTED SCOPE
   obliges is missing, is not marked, carries `slow` (so the canonical `-m "not slow"` would skip it), or when any other
   case carries the marker. The scope is read from the positional arguments: the whole tier or the A10 module obliges all
   five, a node selector `file.py::test_name` only the members it covers. At EXECUTION a listed member that is skipped
   (marker, skipif, runtime skip, xfail) is turned into a failure, and when all five ran and passed the summary prints one
   line with their setup+call+teardown seconds (printing only, no time limit). Widening the list needs a separately
   recorded decision, not an edit of this constant alone.

A COLLECTION ERROR IS NOT THIS MODULE'S TO JUDGE. pytest 7.4 calls `pytest_collection_finish` even when a module failed to
import, and aborts only afterwards (exit 2). The hook returns without judging when the session recorded a collection error
or a stop request; pytest's own failure handling and error text stand.

WHAT THIS DOES NOT SEE (accepted by the owner's decision of 2026-10-16, spec 041):
* WHICH tests exist. A test or module deleted from the tree, or replaced by a weaker one, leaves the tier unnarrowed and
  passes: the composition of the tier is not checked (only the five A10 cases are checked by name).
* Narrowing that is not a flag: a conftest hook that drops items (`pytest_collection_modifyitems`, `pytest_deselected`),
  `collect_ignore` / `pytest_ignore_collect` in a conftest, a module-level skip (reported as skipped, not hidden).
* A run that never loads this conftest (`--noconftest`) or never reaches the end of collection (`--cache-show`,
  `--version`, `--help`, `--fixtures`, `--markers`: pytest answers and exits 0 without running a case), including when
  such an option arrives through `PYTEST_ADDOPTS`. The runner is not defended against reconfiguring itself;
  `scripts/verify_local.ps1` passes none of them.
* A union of narrower paths that covers the whole tier (`-- tests/unit tests/integration tests/contract`) is a path
  selector, so flags are allowed in it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: The registered marker (`pytest.ini`) of the five A10 cases that run in the required tier WITHOUT `slow`.
A10_MARKER = "a10_real_pool_timeout"

_A10_MODULE = "tests/integration/test_p035_a10_pool_wait_is_inside_the_payment_deadline_postgres.py"

#: THE CLOSED LIST of the A10 exception: exact node ids as pytest reports them (the `mode_b` suffix is the fixture mode).
A10_REAL_POOL_TIMEOUT_CASES = frozenset(
    f"{_A10_MODULE}::{case}"
    for case in (
        "test_no_connection_for_the_attempt_is_the_timeout_refusal_and_leaves_nothing[the pool timeout-mode_b]",
        "test_no_connection_around_routing_is_the_timeout_refusal_and_leaves_nothing[the route reader's checkout-mode_b]",
        "test_no_connection_around_routing_is_the_timeout_refusal_and_leaves_nothing[the re-acquisition after routing-mode_b]",
        "test_a_refusal_that_cannot_be_recorded_is_the_retryable_conflict_and_claims_nothing[the recording's budget-mode_b]",
        "test_a_refusal_that_cannot_be_recorded_is_the_retryable_conflict_and_claims_nothing[the pool timeout-mode_b]",
    )
)


#: The marker expression `scripts/verify_local.ps1` passes without `-IncludeExpensive`.
CANONICAL_MARKEXPR = "not slow"

#: The marker expressions a whole-tier run may carry: the canonical profile and the wide one (`-IncludeExpensive`, none).
WHOLE_TIER_MARKEXPRS = frozenset({CANONICAL_MARKEXPR, ""})

#: The directory whose collection is the whole tier (`pytest.ini` `testpaths`).
TIER_DIRECTORY = "tests"

#: The narrowing options refused on the whole tier: `config.option` attribute -> the flag as a person types it.
NARROWING_OPTIONS = (
    ("deselect", "--deselect"),
    ("ignore", "--ignore"),
    ("ignore_glob", "--ignore-glob"),
    ("keyword", "-k"),
    ("lf", "--lf/--last-failed"),
    ("stepwise", "--sw/--stepwise"),
    ("stepwise_skip", "--sw-skip"),
)


def is_whole_tier(*, args: Sequence[str], invocation_dir: Path, root: Path) -> bool:
    """True when the session collects the whole tier: no positional argument, or only the tier directory itself.

    `args` are the session's positional arguments after pytest has substituted `testpaths` for an empty list.
    A `path::node` argument is a selector; so is every path other than the tier directory itself.
    """

    if not args:
        return True
    tier = (root / TIER_DIRECTORY).resolve()
    for argument in args:
        if "::" in argument:
            return False
        if (invocation_dir / argument).resolve() != tier:
            return False
    return True


def narrowing_problem(option: object) -> str | None:
    """None when a whole-tier session carries no narrowing option; otherwise the refusal text naming each one.

    `option` is `config.option` (any object with those attributes; a missing attribute - a plugin that is not loaded,
    such as the cache provider under `-p no:cacheprovider` - counts as not given).
    """

    found = [flag for attribute, flag in NARROWING_OPTIONS if getattr(option, attribute, None)]
    markexpr = " ".join(str(getattr(option, "markexpr", "") or "").split())
    if markexpr not in WHOLE_TIER_MARKEXPRS:
        found.append(f"-m {markexpr!r}")
    if not found:
        return None
    return (
        f"the whole backend tier (no path narrower than `{TIER_DIRECTORY}`) was narrowed by {', '.join(found)}. "
        "Checked: on the whole tier the options --deselect, --ignore, --ignore-glob, -k, --lf, --sw/--sw-skip and any "
        f"marker expression other than {CANONICAL_MARKEXPR!r} (or none, -IncludeExpensive) are refused, wherever they come "
        "from (command line, PYTEST_ADDOPTS, addopts). Not checked: which tests exist - a deleted test is not seen. "
        "To run a subset on purpose, give a path selector: scripts/verify_local.ps1 -BackendOnly -BackendSelector <paths> "
        "(any of these flags is allowed there); the required gate is scripts/verify_local.ps1 -BackendOnly without them "
        "(041 F-041-1, tests/tier_count.py)."
    )


def _a10_required_members(args: Sequence[str], invocation_dir: Path, root: Path) -> frozenset[str]:
    """The listed members the REQUESTED SCOPE obliges this run to collect.

    Package selectors (`--pyargs tests.integration`) are NOT supported: such an argument is read as a literal path, matches
    nothing and obliges no member (the canonical runner and CI never use it).

    All five when the whole tier or the A10 module (or a directory holding it) is requested - an empty argument list is
    `testpaths = tests`. For a node selector inside the module (`file.py::test_name`, or a full node id) only the members
    that selector covers. None when every argument points elsewhere. A scope that includes the module but then loses its
    items (`--ignore` of the file on the whole tier) is therefore a loss, not "an unrelated selector".
    """

    if not args:
        return A10_REAL_POOL_TIMEOUT_CASES
    module = (root / _A10_MODULE).resolve()
    required: set[str] = set()
    for argument in args:
        path_part, _, node_part = argument.partition("::")
        target = (invocation_dir / path_part).resolve()
        if node_part:
            if target == module:
                prefix = f"{_A10_MODULE}::{node_part}"
                required |= {
                    member
                    for member in A10_REAL_POOL_TIMEOUT_CASES
                    if member == prefix or member.startswith((prefix + "[", prefix + "::"))
                }
        elif target == module or module.is_relative_to(target):
            return A10_REAL_POOL_TIMEOUT_CASES
    return frozenset(required)


def a10_exception_problem(
    items: Sequence[tuple[str, bool, bool]], *, args: Sequence[str], invocation_dir: Path, root: Path
) -> str | None:
    """None when the A10 exception holds in this collection; otherwise the refusal text.

    `items` is every collected case BEFORE any deselection, as `(node id, carries a10_real_pool_timeout, carries
    slow)`. A case carrying the marker that is not on the list is an ADDITION, checked on every run. The listed cases
    the requested scope obliges (`_a10_required_members`) must be present, marked and not `slow` (a missing, unmarked or
    `slow` member is a loss, a reclassification or a skip by `-m "not slow"`). That the five really RUN and pass is
    enforced at execution (`a10_skip_refusal` below), because a collection cannot know it.
    """

    by_id = {node_id: (marked, slow) for node_id, marked, slow in items}
    problems: list[str] = []
    added = sorted(node_id for node_id, (marked, _) in by_id.items() if marked and node_id not in A10_REAL_POOL_TIMEOUT_CASES)
    if added:
        problems.append(f"{len(added)} case(s) carry {A10_MARKER} without being on the closed list: {added}")
    for node_id in sorted(_a10_required_members(args, invocation_dir, root)):
        if node_id not in by_id:
            problems.append(f"listed case is not collected (missing, renamed or re-parametrized): {node_id}")
            continue
        marked, slow = by_id[node_id]
        if not marked:
            problems.append(f"listed case lost the {A10_MARKER} marker (reclassified): {node_id}")
        if slow:
            problems.append(f"listed case carries `slow`, which `-m 'not slow'` skips: {node_id}")
    if not problems:
        return None
    return (
        "the A10 exception (exactly five real-pool-timeout cases run in the required tier without `slow`; list: "
        "A10_REAL_POOL_TIMEOUT_CASES in tests/tier_count.py) does not hold: " + "; ".join(problems) + ". Widening, "
        "narrowing or renaming the list needs a separately recorded decision (AGENTS.md section 11)."
    )


def a10_skip_refusal(node_id: str, reason: object) -> str:
    """The failure text for a listed member that was skipped (or expected to fail) instead of passing."""

    return (
        f"the A10 exception: the listed case {node_id} was skipped or expected to fail ({reason}), not executed. The five real-pool-timeout "
        "cases are mandatory in the required tier; a skip, a true skipif, an xfail or a runtime pytest.skip() takes one "
        "out of the gate while the membership stays right (AGENTS.md section 11)."
    )


#: The admission budget (seconds) the five cases were accepted with: setup + call + teardown, all five, fixtures included.
A10_ADMISSION_BUDGET_SECONDS = 60


def a10_summary_line(records: dict[str, dict[str, tuple[str, float]]]) -> str | None:
    """One line for the terminal summary when all five members EXECUTED (every phase) and passed; otherwise None.

    Printing only: no time limit is enforced (a limit on a real-service test in the required tier is a source of flakes);
    an excess is read from this line by a person. `records` maps node id -> phase -> (outcome, seconds).
    """

    total = 0.0
    for node_id in A10_REAL_POOL_TIMEOUT_CASES:
        phases = records.get(node_id, {})
        if set(phases) != {"setup", "call", "teardown"} or any(outcome != "passed" for outcome, _ in phases.values()):
            return None
        total += sum(seconds for _, seconds in phases.values())
    return (
        f"a10 real-pool-timeout exception: {len(A10_REAL_POOL_TIMEOUT_CASES)} passed, setup+call+teardown "
        f"{total:.2f}s (admission budget {A10_ADMISSION_BUDGET_SECONDS}s)"
    )
