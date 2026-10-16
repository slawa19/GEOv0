"""The whole backend tier refuses the flags that narrow it, and the five A10 cases hold by name (041 F-041-1).

THE RULE THIS FILE HOLDS (`tests/tier_count.py`, called from `tests/conftest.py` at the end of collection). A backend
session over the whole tier (no positional path narrower than `tests`) ends with a usage error (exit 4) naming the flag
when its options carry `--deselect`, `--ignore`, `--ignore-glob`, `-k`, `--lf`, `--sw`/`--sw-skip`, or a marker expression
other than `not slow` (the canonical profile) or none (`-IncludeExpensive`), an `-o` override of a discovery key
(`testpaths`, `norecursedirs`, `python_files`, `python_classes`, `python_functions`; added after the 041 review of
2026-10-16) or another `--rootdir` / `-c`. Until 2026-10-16 the same file held an exact
count of the selected cases (035 F-035-14); that count and its constant are gone by the owner's decision of 2026-10-16
(spec 041). Its two reproducers (a `--deselect`, an `--ignore` on the canonical run) stay below, now refused by the flag.

HOW THE CASES ASK. Each runs pytest in a subprocess, `--collect-only` unless said otherwise, against a PostgreSQL URL that
nothing listens on: collection opens no database, so the tooling tier keeps needing no database. The deselected and
ignored cases are taken from the baseline collection itself, so the file does not rot when a test module is renamed.

COUNTER-CHECKS (AGENTS.md section 9, anti-vacuum): every refusal has a run it must NOT refuse - the unmodified canonical
profile, the unmodified wide profile, the allowed reordering/stopping flags on the whole tier, and a path-selector run
carrying the refused flags, unrelated `-o` keys and this repository's own `--rootdir` / `-c`. Each refusal case asserts the flag's own name in the message, so a usage error of another
origin (an unknown option) cannot pass it.

WHAT THIS DOES NOT SEE. Which tests exist: a deleted test is not narrowing by a flag (accepted, spec 041). Narrowing
through committed collection configuration or conftest hooks is the same category as deleting a test and is not caught;
that and the options that end pytest before collection are named in `tests/tier_count.py`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]

_USAGE_ERROR = 4  # pytest.ExitCode.USAGE_ERROR
_NO_SERVER_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:1/geov0_test_p041_flag_probe"

# The canonical profile is exactly what `scripts/verify_local.ps1` passes by default: `-m "not slow"` and no
# positional selector. `-IncludeExpensive` drops the marker expression.
_CANONICAL = ("-m", "not slow")
_WIDE = ()

_SUMMARY = re.compile(r"(?P<selected>\d+)(?:/(?P<total>\d+))? tests? collected(?: \((?P<deselected>\d+) deselected\))?")


def _collect(
    *args: str,
    extra_pythonpath: Path | None = None,
    collect_only: bool = True,
    cache_dir: Path | None = None,
    addopts: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`cache_dir` keeps the cache provider (needed by `--lf` and `--sw`) in a private directory; otherwise it is off."""

    env = os.environ.copy()
    if extra_pythonpath is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(extra_pythonpath), env.get("PYTHONPATH")]))
    env["ENV"] = "test"
    env["TEST_DATABASE_URL"] = _NO_SERVER_URL
    env["GEO_TEST_ALLOW_DB_RESET"] = "1"
    env.pop("GEO_TEST_USE_MIGRATED_SCHEMA", None)
    env.pop("PYTEST_ADDOPTS", None)
    if addopts is not None:
        env["PYTEST_ADDOPTS"] = addopts
    cache = ["-o", f"cache_dir={cache_dir}"] if cache_dir is not None else ["-p", "no:cacheprovider"]
    return subprocess.run(
        [sys.executable, "-m", "pytest", *(["--collect-only"] if collect_only else ["-rs"]), "-q", *cache, *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _selected(result: subprocess.CompletedProcess[str]) -> int:
    match = _SUMMARY.search(result.stdout)
    assert match is not None, f"no collection summary in pytest output:\n{result.stdout[-1500:]}"
    return int(match["selected"])


def _nodeids(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line]


@pytest.fixture(scope="module")
def baseline() -> subprocess.CompletedProcess[str]:
    """The canonical profile, untouched: the tier as it is, which must collect and exit 0."""

    result = _collect(*_CANONICAL)
    assert result.returncode == 0, (
        f"the unmodified canonical profile did not collect (exit {result.returncode}):\n"
        f"{(result.stdout + result.stderr)[-1500:]}"
    )
    assert _selected(result) > 0, "the baseline collected nothing; the cases below would judge nothing"
    return result


def _a_unit_module(baseline: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    """One case of `tests/unit` and its module, taken from the baseline so that a rename cannot rot this file."""

    for nodeid in _nodeids(baseline):
        if nodeid.startswith("tests/unit/"):
            return nodeid, nodeid.split("::", 1)[0]
    raise AssertionError("the baseline holds no case under tests/unit/")


def _assert_refused(result: subprocess.CompletedProcess[str], flag: str) -> None:
    output = result.stdout + result.stderr
    assert result.returncode == _USAGE_ERROR, (
        f"the whole backend tier narrowed by {flag} exited {result.returncode}, not {_USAGE_ERROR}:\n{output[-1500:]}"
    )
    assert "backend tier refused" in output and "narrowed by" in output and flag in output, (
        f"exit {_USAGE_ERROR} but not the flag ban naming {flag}:\n{output[-1500:]}"
    )


def test_a_deselected_case_ends_the_canonical_backend_run(baseline: subprocess.CompletedProcess[str]) -> None:
    """Reproducer of 035 F-035-14 (red on 75dafc82): one `--deselect` on the whole tier refuses the run."""

    nodeid, _ = _a_unit_module(baseline)
    _assert_refused(_collect(*_CANONICAL, "--deselect", nodeid), "--deselect")


def test_a_module_ignored_on_the_canonical_run_ends_it(baseline: subprocess.CompletedProcess[str]) -> None:
    """Reproducer of 035 F-035-14 (red on 75dafc82): `--ignore` of a module on the whole tier refuses the run."""

    _, module = _a_unit_module(baseline)
    _assert_refused(_collect(*_CANONICAL, "--ignore", module), "--ignore")


@pytest.mark.parametrize(
    ("args", "flag"),
    [
        (("-m", "not slow", "--ignore-glob", "tests/unit/test_p0*.py"), "--ignore-glob"),
        (("-m", "not slow", "-k", "payment"), "-k"),
        (("-m", "(not slow)"), "-m '(not slow)'"),
        (("-m", "slow"), "-m 'slow'"),
        (("-m", "not slow and not a10_real_pool_timeout"), "-m 'not slow and not a10_real_pool_timeout'"),
        (("-m", "not slow", "-k", "payment", "--", "tests"), "-k"),  # `-- tests` is the whole tier too
        # 041 review 2026-10-16: discovery keys overridden with -o drop modules while the path stays `tests`
        (("-m", "not slow", "-o", "norecursedirs=unit"), "-o/--override-ini norecursedirs"),
        (("-m", "not slow", "--override-ini=python_files=test_p012_*.py"), "-o/--override-ini python_files"),
        (("-m", "not slow", "-o", "testpaths=tests/unit"), "-o/--override-ini testpaths"),
        (("-m", "not slow", "-o", "python_classes=Nothing"), "-o/--override-ini python_classes"),
        (("-m", "not slow", "-o", "python_functions=nothing_*"), "-o/--override-ini python_functions"),
        (("-m", "not slow", "--rootdir", "tests", "--", "tests"), "--rootdir 'tests'"),
    ],
    ids=[
        "--ignore-glob", "-k", "an equivalent marker spelling", "another marker expression", "a narrower expression",
        "-- tests with -k", "-o norecursedirs", "--override-ini python_files", "-o testpaths", "-o python_classes",
        "-o python_functions", "--rootdir",
    ],
)
def test_each_narrowing_option_is_refused_on_the_whole_tier(args: tuple[str, ...], flag: str) -> None:
    _assert_refused(_collect(*args), flag)


@pytest.mark.parametrize(("option", "flag"), [("--lf", "--lf/--last-failed"), ("--sw", "--sw/--stepwise"), ("--sw-skip", "--sw-skip")])
def test_the_cache_driven_narrowing_options_are_refused_on_the_whole_tier(tmp_path: Path, option: str, flag: str) -> None:
    """`--lf` and `--sw` need the cache provider, which the other cases switch off; here it writes into `tmp_path`."""

    _assert_refused(_collect(*_CANONICAL, option, cache_dir=tmp_path / "cache"), flag)


def test_a_narrowing_option_from_pytest_addopts_is_refused(baseline: subprocess.CompletedProcess[str]) -> None:
    """The ban reads the session's options, so the environment is no way around it."""

    nodeid, _ = _a_unit_module(baseline)
    _assert_refused(_collect(*_CANONICAL, addopts=f"--deselect {nodeid}"), "--deselect")


def test_a_discovery_override_from_pytest_addopts_is_refused() -> None:
    """Reproducer of the 041 review (green on 2f16bd05 through the canonical runner, tests/unit silently dropped)."""

    _assert_refused(_collect(*_CANONICAL, addopts="-o norecursedirs=unit"), "-o/--override-ini norecursedirs")


def test_another_configuration_file_is_refused(tmp_path: Path) -> None:
    """`-c` brings its own discovery keys; a copy of this repository's own file is enough to show the refusal."""

    other = tmp_path / "other.ini"
    other.write_text((_ROOT / "pytest.ini").read_text(encoding="utf-8"), encoding="utf-8")
    _assert_refused(_collect(*_CANONICAL, "-c", str(other), "--", "tests"), "-c/--config-file")


def test_the_wide_profile_with_a_deselect_is_refused(baseline: subprocess.CompletedProcess[str]) -> None:
    """`-IncludeExpensive` on the whole tier is the whole tier too; until 2026-10-16 it was not judged at all."""

    nodeid, _ = _a_unit_module(baseline)
    _assert_refused(_collect(*_WIDE, "--deselect", nodeid), "--deselect")


_COLLECTION_ERROR_PLUGIN = """
import pytest


class _CollectionError(pytest.File):
    def collect(self):
        raise ImportError("p035 simulated import error")


def pytest_collect_file(file_path, parent):
    if file_path.name == "{module_name}":
        return _CollectionError.from_parent(parent, path=file_path)
"""


def test_a_collection_error_stays_pytests_exit_2_and_is_not_reread_as_a_refusal(
    baseline: subprocess.CompletedProcess[str], tmp_path: Path
) -> None:
    """Reproducer (red on 7edf4cb8, review P2): pytest 7.4 calls `pytest_collection_finish` in a `finally`, BEFORE it
    aborts on a collection error. Expected: pytest's own exit 2 and the collection error's text, and no tier refusal
    (the `--deselect` here would be refused on its own; a collection error takes precedence).
    """

    nodeid, module = _a_unit_module(baseline)
    (tmp_path / "p035_collection_error_plugin.py").write_text(
        _COLLECTION_ERROR_PLUGIN.replace("{module_name}", Path(module).name), encoding="utf-8"
    )
    result = _collect(
        *_CANONICAL,
        "--deselect",
        module,
        "-p",
        "p035_collection_error_plugin",
        extra_pythonpath=tmp_path,
    )
    output = result.stdout + result.stderr
    assert "p035 simulated import error" in output, f"the collection error did not happen:\n{output[-1200:]}"
    assert result.returncode == 2, (
        f"a collection error ended the canonical backend run with exit {result.returncode}, not pytest's 2: "
        f"{[line for line in output.splitlines() if 'refused' in line][:1]}"
    )
    assert "backend tier refused" not in output, "the tier judged a collection that pytest was about to abort"


def test_the_unmodified_canonical_profile_is_not_refused(baseline: subprocess.CompletedProcess[str]) -> None:
    """Counter-check: the rule does not refuse the tier as it is (the `baseline` fixture asserts exit 0)."""

    assert _selected(baseline) > 0


def test_the_unmodified_wide_profile_is_not_refused() -> None:
    """Counter-check: `-IncludeExpensive` (no marker expression) on the whole tier, with no flag, collects with exit 0."""

    wide = _collect(*_WIDE)
    assert wide.returncode == 0, f"the wide profile was refused (exit {wide.returncode}):\n{wide.stdout[-800:]}"
    assert _selected(wide) > 0


@pytest.mark.parametrize("args", [("--ff",), ("--nf",), ("-x",), ("--maxfail", "3")], ids=["--ff", "--nf", "-x", "--maxfail"])
def test_the_reordering_and_stopping_options_are_allowed_on_the_whole_tier(tmp_path: Path, args: tuple[str, ...]) -> None:
    """Counter-check: the ban is a list, not a refusal of every option - these drop no case from a green run."""

    result = _collect(*_CANONICAL, *args, cache_dir=tmp_path / "cache")
    assert result.returncode == 0, f"{args} was refused (exit {result.returncode}):\n{(result.stdout + result.stderr)[-800:]}"
    assert _selected(result) > 0


@pytest.mark.parametrize(
    "args",
    [
        ("-o", "pythonpath=."),
        ("-o", "xfail_strict=true"),
        ("--rootdir", "."),
        ("-c", "pytest.ini"),
    ],
    ids=["-o pythonpath", "-o xfail_strict", "--rootdir of this repository", "-c of this repository's pytest.ini"],
)
def test_unrelated_overrides_and_this_repositorys_own_configuration_are_allowed(tmp_path: Path, args: tuple[str, ...]) -> None:
    """Counter-check: only discovery keys and another configuration are refused (`-o cache_dir=` is in every case here)."""

    result = _collect(*_CANONICAL, *args, cache_dir=tmp_path / "cache")
    assert result.returncode == 0, f"{args} was refused (exit {result.returncode}):\n{(result.stdout + result.stderr)[-800:]}"
    assert _selected(result) > 0


def test_a_selector_run_is_not_judged_even_with_the_refused_flags(baseline: subprocess.CompletedProcess[str]) -> None:
    """Counter-check: `-BackendSelector` narrows on purpose, and a flag inside it is the operator's business."""

    nodeid, module = _a_unit_module(baseline)
    for extra in ((), ("--deselect", nodeid), ("-k", "nothing_matches_this"), ("-m", "slow"), ("-o", "python_functions=test_*")):
        result = _collect(*_CANONICAL, *extra, "--", module)
        assert result.returncode in (0, 5), (
            f"a selector run with {extra} was refused (exit {result.returncode}):\n{result.stdout[-800:]}"
        )
        assert "backend tier refused" not in result.stdout + result.stderr


def _tier_count_module():
    path = _ROOT / "tests" / "tier_count.py"
    spec = importlib.util.spec_from_file_location("p041_tier_count_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_flag_verdict_in_both_outcomes() -> None:
    """Unit check of `narrowing_problem`: each listed option alone is refused by name; the profiles alone are not."""

    tier = _tier_count_module()

    def option(**given):
        defaults = dict(deselect=[], ignore=None, ignore_glob=None, keyword="", lf=False, stepwise=False, stepwise_skip=False, markexpr="not slow")
        return SimpleNamespace(**{**defaults, **given})

    def verdict(namespace):
        return tier.narrowing_problem(namespace, invocation_dir=_ROOT, root=_ROOT)

    assert verdict(option()) is None
    assert verdict(option(markexpr="")) is None  # -IncludeExpensive
    assert verdict(option(markexpr=" not  slow ")) is None  # whitespace is collapsed
    assert verdict(SimpleNamespace(markexpr="not slow")) is None  # an unloaded plugin's option is absent
    assert verdict(option(override_ini=["cache_dir=x", "pythonpath=.", "xfail_strict=true", "timeout=10"])) is None
    assert verdict(option(rootdir=str(_ROOT), inifilename="pytest.ini")) is None  # this repository's own
    for given, flag in (
        (dict(deselect=["tests/unit/test_x.py::t"]), "--deselect"),
        (dict(ignore=["tests/unit"]), "--ignore"),
        (dict(ignore_glob=["*x*"]), "--ignore-glob"),
        (dict(keyword="x"), "-k"),
        (dict(lf=True), "--lf"),
        (dict(stepwise=True), "--sw"),
        (dict(stepwise_skip=True), "--sw-skip"),
        (dict(markexpr="(not slow)"), "-m '(not slow)'"),
        (dict(markexpr="slow"), "-m 'slow'"),
        (dict(override_ini=["cache_dir=x", "norecursedirs=unit"]), "-o/--override-ini norecursedirs"),
        (dict(override_ini=["python_files = test_x*.py"]), "-o/--override-ini python_files"),
        (dict(override_ini=["testpaths=tests/unit"]), "-o/--override-ini testpaths"),
        (dict(override_ini=["python_classes=X"]), "-o/--override-ini python_classes"),
        (dict(override_ini=["python_functions=x"]), "-o/--override-ini python_functions"),
        (dict(override_ini=["collect_imported_tests=false"]), "-o/--override-ini collect_imported_tests"),
        (dict(rootdir="tests"), "--rootdir 'tests'"),
        (dict(inifilename="other.ini"), "-c/--config-file 'other.ini'"),
    ):
        problem = verdict(option(**given))
        assert problem is not None and flag in problem, (given, problem)


def test_only_a_run_over_the_whole_tier_is_judged() -> None:
    """The scope decision in both outcomes: a rule that judged nothing, or every selector, fails one row."""

    tier = _tier_count_module()

    def whole(*args):
        return tier.is_whole_tier(args=list(args), invocation_dir=_ROOT, root=_ROOT)

    assert whole()  # no argument: `testpaths = tests`
    assert whole("tests")
    assert whole("tests", "tests/")
    assert not whole("tests/unit")  # -BackendSelector
    assert not whole("tests/unit/test_admin_audit_log_list.py")
    assert not whole("tests/unit/test_admin_audit_log_list.py::test_x")
    assert not whole("tests", "tests/unit")
    assert not whole("docs")


# ------------------------------------------------------------------------------------------------ the A10 exception
# 2026-10-10 (Q-C): exactly five real-pool-timeout cases of 035 A10 run in the required tier without `slow`. The list is
# `A10_REAL_POOL_TIMEOUT_CASES` in `tests/tier_count.py`; `tests/conftest.py` judges it at collection. These cases hold the
# list in place: the verdict function on planted collections (both outcomes), and the REAL collection, once as it is and
# twice with a plugin that plants the two violations an edit could make (a listed case turned `slow`; a sixth case marked).

_A10_FILE = "tests/integration/test_p035_a10_pool_wait_is_inside_the_payment_deadline_postgres.py"


def test_the_a10_verdict_holds_for_the_list_and_names_every_way_it_can_break() -> None:
    tier = _tier_count_module()

    def verdict(items, *args):
        return tier.a10_exception_problem(items, args=list(args), invocation_dir=_ROOT, root=_ROOT)

    listed = sorted(tier.A10_REAL_POOL_TIMEOUT_CASES)
    assert len(listed) == 5 and all(node_id.startswith(_A10_FILE + "::") for node_id in listed)
    rest = [(f"tests/unit/test_x.py::t{i}", False, False) for i in range(3)]
    whole = [(node_id, True, False) for node_id in listed] + rest
    assert verdict(whole) is None  # no argument: the whole tier
    assert verdict(whole, "tests") is None and verdict(whole, _A10_FILE) is None
    # scope: a selector elsewhere obliges nothing, so the A10 module's absence is not a loss ...
    assert verdict(rest, "tests/unit/test_x.py") is None
    # ... but the whole tier (or the module, or its directory) without the five IS a loss (`--ignore` of the file)
    for scope in ((), ("tests",), ("tests/integration",), (_A10_FILE,)):
        assert "listed case is not collected" in (verdict(rest, *scope) or ""), scope
    # a node selector obliges only the members it covers
    one_function = _A10_FILE + "::test_no_connection_around_routing_is_the_timeout_refusal_and_leaves_nothing"
    routing = [n for n in listed if "around_routing" in n]
    assert len(routing) == 2
    assert verdict([(n, True, False) for n in routing], one_function) is None
    assert verdict([(routing[0], True, False)], one_function) is not None  # a covered member is still required
    assert verdict(rest, _A10_FILE + "::test_a_cancellation_while_waiting_for_the_pool_leaves_nothing") is None  # covers none
    assert verdict([(routing[0], True, False)], routing[0]) is None  # a full node id covers exactly itself
    # a listed case missing from a collection of the whole module (renamed, re-parametrized or deleted)
    assert listed[0] in (verdict(whole[1:]) or "")
    # reclassified: marker lost, or `slow` put back (which `-m 'not slow'` skips)
    lost = [(listed[0], False, False)] + [(n, True, False) for n in listed[1:]]
    assert "lost the a10_real_pool_timeout marker" in (verdict(lost) or "")
    slowed = [(listed[0], True, True)] + [(n, True, False) for n in listed[1:]]
    assert "carries `slow`" in (verdict(slowed) or "")
    # an addition: a sixth case with the marker, in this module or anywhere else, under any scope
    for extra in (_A10_FILE + "::test_something_else", "tests/unit/test_x.py::t0"):
        added = whole + [(extra, True, False)]
        for scope in ((), ("tests/unit/test_x.py",)):
            assert "without being on the closed list" in (verdict(added, *scope) or "")
            assert extra in (verdict(added, *scope) or "")


def test_the_a10_summary_line_is_printed_only_for_five_executed_and_passed_members() -> None:
    tier = _tier_count_module()
    listed = sorted(tier.A10_REAL_POOL_TIMEOUT_CASES)
    records = {n: {"setup": ("passed", 0.5), "call": ("passed", 2.0), "teardown": ("passed", 0.1)} for n in listed}
    assert tier.a10_summary_line(records) == (
        "a10 real-pool-timeout exception: 5 passed, setup+call+teardown 13.00s (admission budget 60s)"
    )
    partial = dict(records)
    del partial[listed[0]]
    assert tier.a10_summary_line(partial) is None  # not all five executed: nothing is claimed
    failed = {**records, listed[1]: {**records[listed[1]], "call": ("failed", 2.0)}}
    assert tier.a10_summary_line(failed) is None
    assert tier.a10_summary_line({}) is None


def test_the_real_collection_of_the_a10_module_selects_the_listed_five_without_slow() -> None:
    """The ids in the list are the ids pytest reports: marker selection finds exactly them, and `-m 'not slow'` keeps them."""

    listed = set(_tier_count_module().A10_REAL_POOL_TIMEOUT_CASES)
    by_marker = _collect("-m", "a10_real_pool_timeout", "--", _A10_FILE)
    assert by_marker.returncode == 0, (by_marker.stdout + by_marker.stderr)[-1500:]
    assert set(_nodeids(by_marker)) == listed
    canonical = _collect(*_CANONICAL, "--", _A10_FILE)
    assert canonical.returncode == 0, (canonical.stdout + canonical.stderr)[-1500:]
    assert listed <= set(_nodeids(canonical)), "a listed case is not selected by `-m 'not slow'`"


_A10_VIOLATION_PLUGIN = """
import pytest


def pytest_itemcollected(item):
    if item.nodeid == "{target}":
        item.add_marker({marker})
"""


@pytest.mark.parametrize(
    ("marker", "on_listed", "expected_text"),
    [
        ("pytest.mark.slow", True, "carries `slow`"),
        ("pytest.mark.a10_real_pool_timeout", False, "without being on the closed list"),
    ],
    ids=["a listed case turned slow", "a sixth case marked"],
)
def test_the_real_collection_ends_with_exit_4_when_the_a10_exception_is_broken(
    tmp_path: Path, marker: str, on_listed: bool, expected_text: str
) -> None:
    tier = _tier_count_module()
    target = sorted(tier.A10_REAL_POOL_TIMEOUT_CASES)[0]
    if not on_listed:
        baseline = _collect("--", _A10_FILE)
        target = next(node_id for node_id in _nodeids(baseline) if node_id not in tier.A10_REAL_POOL_TIMEOUT_CASES)
    (tmp_path / "p035_a10_violation_plugin.py").write_text(
        _A10_VIOLATION_PLUGIN.format(target=target.replace('"', '\\"'), marker=marker), encoding="utf-8"
    )
    result = _collect(*_CANONICAL, "-p", "p035_a10_violation_plugin", "--", _A10_FILE, extra_pythonpath=tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == _USAGE_ERROR, f"exit {result.returncode}, not {_USAGE_ERROR}:\n{output[-1500:]}"
    assert "the A10 exception" in output and expected_text in output, output[-1500:]


# --------------------------------------------------------------------- review of 5e9ba5fd: scope and execution (Q-C)
# Two P2 findings of the 2026-10-10 review: (1) a SINGLE A10 node was refused as "members missing"; (2) a listed member that
# is SKIPPED during execution left the run green. These cases are the reproducers (red on 5e9ba5fd) and stay as the guard.

_A10_NODE = _A10_FILE + "::"


@pytest.mark.parametrize(
    "selector",
    [
        _A10_NODE + "test_no_connection_around_routing_is_the_timeout_refusal_and_leaves_nothing",
        _A10_NODE + "test_a_cancellation_while_waiting_for_the_pool_leaves_nothing",
        _A10_NODE + "test_a_refusal_that_cannot_be_recorded_is_the_retryable_conflict_and_claims_nothing[the pool timeout-mode_b]",
    ],
    ids=["a function with two listed members", "a function with none", "one listed node by its full id"],
)
def test_a_single_a10_node_selector_is_not_judged_as_the_whole_module(selector: str) -> None:
    """A deliberate partial run (one function or one node) is complete for its scope: exit 0, the selection collected."""

    result = _collect("--", selector)
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"a single-node selector was refused (exit {result.returncode}): {output[-900:]}"
    assert _nodeids(result), "the selector collected nothing: the case would pass by judging an empty set"


def test_the_whole_tier_with_the_a10_file_ignored_is_refused_by_the_membership_guard_itself() -> None:
    """`--ignore` of the A10 file on the whole tier: the flag ban also refuses it, but the membership guard must name it."""

    result = _collect(*_CANONICAL, "--ignore", _A10_FILE)
    output = result.stdout + result.stderr
    assert result.returncode == _USAGE_ERROR, output[-900:]
    assert "the A10 exception" in output and "listed case is not collected" in output, output[-900:]


_A10_SKIP_PLUGIN = '''
import pytest

TARGET = {target!r}


def pytest_itemcollected(item):
    if item.nodeid == TARGET:
{collected}


# NORMAL priority on purpose: it runs after pytest's own tryfirst setup hook (which evaluates the skip and xfail marks) and
# before the runner's setup hook (which builds fixtures, among them the tier's session database). No database is reached.
def pytest_runtest_setup(item):
    if item.nodeid == TARGET:
{setup}
'''


def _run_with_planted_skip(
    tmp_path: Path, *, target: str, collected: str, setup: str, k: str, m: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute (not just collect) the A10 module narrowed by `-m`/`-k`, with a skip planted on `target` by a `-p` plugin.

    Only the planted case is selected, and it is skipped before any fixture is set up, so nothing here opens a database.
    """

    (tmp_path / "p035_a10_skip_plugin.py").write_text(
        _A10_SKIP_PLUGIN.format(
            target=target, collected="        " + collected, setup="        " + setup
        ),
        encoding="utf-8",
    )
    marker = ["-m", m] if m else []
    return _collect("-p", "p035_a10_skip_plugin", *marker, "-k", k, "--", _A10_FILE, extra_pythonpath=tmp_path, collect_only=False)


@pytest.mark.parametrize(
    ("collected", "setup"),
    [
        ("item.add_marker(pytest.mark.skip(reason='planted marker skip'))", "pass"),
        ("item.add_marker(pytest.mark.skipif(True, reason='planted skipif'))", "pass"),
        ("pass", "pytest.skip('planted runtime skip')"),
        ("pass", "pytest.xfail('planted runtime xfail')"),
        ("item.add_marker(pytest.mark.xfail(reason='planted xfail marker'))", "raise RuntimeError('planted failure')"),
    ],
    ids=[
        "pytest.mark.skip",
        "a true skipif",
        "pytest.skip() at runtime",
        "pytest.xfail() at runtime",
        "an xfail marker on a member that fails",
    ],
)
def test_a_listed_member_skipped_during_execution_is_refused(tmp_path: Path, collected: str, setup: str) -> None:
    """The membership stays right when a member is skipped; the run must still not be green."""

    target = _A10_NODE + "test_no_connection_for_the_attempt_is_the_timeout_refusal_and_leaves_nothing[the pool timeout-mode_b]"
    assert target in _tier_count_module().A10_REAL_POOL_TIMEOUT_CASES
    result = _run_with_planted_skip(
        tmp_path, target=target, collected=collected, setup=setup,
        k="test_no_connection_for_the_attempt", m="a10_real_pool_timeout",
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"a listed member was skipped and the run exited {result.returncode}:\n{output[-900:]}"
    assert "the A10 exception" in output and "skipped" in output, output[-900:]


def test_a_skipped_non_member_is_an_ordinary_skip(tmp_path: Path) -> None:
    """Counter-check: the refusal is about the five, not a blanket rule on skips."""

    other = _A10_NODE + "test_a_cancellation_while_waiting_for_the_pool_leaves_nothing[mode_b]"
    result = _run_with_planted_skip(
        tmp_path,
        target=other,
        collected="item.add_marker(pytest.mark.skip(reason='planted'))",
        setup="pass",
        k="test_a_cancellation_while_waiting",
    )
    assert result.returncode == 0 and "1 skipped" in result.stdout, (result.stdout + result.stderr)[-900:]


def test_an_xfailed_non_member_stays_an_expected_failure(tmp_path: Path) -> None:
    """Counter-check for the xfail cases above: the same planted xfail on a case outside the five is an ordinary xfail."""

    other = _A10_NODE + "test_a_cancellation_while_waiting_for_the_pool_leaves_nothing[mode_b]"
    result = _run_with_planted_skip(
        tmp_path,
        target=other,
        collected="item.add_marker(pytest.mark.xfail(reason='planted'))",
        setup="raise RuntimeError('planted failure')",
        k="test_a_cancellation_while_waiting",
    )
    assert result.returncode == 0 and "1 xfailed" in result.stdout, (result.stdout + result.stderr)[-900:]
