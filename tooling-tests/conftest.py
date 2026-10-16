"""The tooling tier: tests of this repository's own tools that need no database (025, T2502.2).

WHAT LIVES HERE. Checks of the CI workflow's form, of document and repository guards, of seed
descriptions and recipes without a database, of the fixture generators, and of the PowerShell
launchers. Until 2026-10-03 they ran inside the backend tier, whose `tests/conftest.py` refuses to
start without a PostgreSQL `TEST_DATABASE_URL` and builds the application at import - so a check of
a YAML file could not run anywhere a database was not. Two partitions, one per runner:

* `portable/`   - plain Python; a blocking step of the `static-diagnostics` job (ubuntu);
* `powershell/` - the launcher checks; a blocking step of the `required-ui` job (Windows).

Both run through `scripts/verify_local.ps1 -ToolingOnly -ToolingPartition <portable|powershell|all>`,
locally and on CI alike. Backend discovery is not touched: `pytest.ini` still collects `tests` only.

THERE IS NO COUNT TO MAINTAIN. Until 2026-10-16 this file held `EXPECTED_CASES`, an exact two-sided
count of the selected cases per partition, and a dated journal of every edit of it. The owner's
decision of 2026-10-16 (programme 041, `specs/041-verification-diet/spec.md`, F-041-13, the same
mode as F-041-1 for the backend tier) replaced it with the refusals below; the constant and its
journal are in git history (`git log -S EXPECTED_CASES -- tooling-tests/conftest.py`). Do not add a
number, a lower bound or a journal back: adding or deleting a tooling test needs no edit here.

WHAT THIS FILE ENFORCES, IN THE SAME PYTEST SESSION THAT RUNS THE TESTS (anti-vacuum, AGENTS.md §9).
A tier that moved out of the gate is exactly the shape of a false green - a step nobody runs, or one
that runs and collects nothing, still exits 0 somewhere. So the session refuses (exit 4) when:

* it is started without `--tooling-partition` (the runner always passes it);
* it is narrowed: by an option (`-m`, `-k`, `--deselect`, `--ignore`, `--ignore-glob`, `--lf`,
  `--sw`/`--sw-skip`, read from the session's options, so `PYTEST_ADDOPTS` and `addopts` are seen),
  by a positional path other than the requested partition directories (or `tooling-tests` for
  `all`), or by anything that reports deselected cases (a plugin's `pytest_deselected` included);
* an option collects without executing the test bodies (`--collect-only` and the like);
* a case is collected outside the requested partitions, or a requested partition collects nothing;
* the `powershell` partition is requested on a host without both PowerShells the launcher checks
  run under (see `_missing_powershell_hosts`);

and turns a passing run red (exit 1) when a case was skipped, xfailed or xpassed, or when fewer
cases reported a result than were selected.

It also removes the database variables from the environment before any test module is imported:
a tooling test that reaches for a database gets no URL, so "needs no database" is a property of the
tier and not only of the tests that are in it today.

WHAT IT DOES NOT SEE. Which tests exist: a tooling test deleted or replaced by a weaker one passes
(accepted by the owner's decision of 2026-10-16, spec 041). Narrowing that is neither an option nor
a reported deselection: a `collect_ignore` or `pytest_ignore_collect` in a conftest, or a plugin that
drops items without reporting them. Whether CI runs the steps: that is asserted by
`tooling_ci_binding_violations` below, from both partitions, so removing either step is noticed by the
other one. A debug run of one file is possible with `--noconftest`; it is a debug path and checks
none of the above.

WHAT THIS DOES NOT DEFEND AGAINST (narrowed 2026-10-03 after the §15 fix-delta round, AGENTS.md §19.4).
The guarantee is the FORM of the CI steps and the selection/outcome inside a session that really runs.
It does not defend against deliberate reconfiguration of the mechanism that runs it:

* `pytest.ini` `addopts` (for example `--help` or `--version`: pytest exits before any check here,
  and the runner accepts exit 0);
* the workflow's job `needs:` (a dependency on a job skipped on pull requests, such as
  `container-smoke`, skips the tooling job), step `shell:` (a custom shell such as `echo {0}`
  succeeds without running the step), `defaults`, `strategy` and `timeout-minutes`;
* `PYTEST_PLUGINS`, and arbitrary plugin code in the same process.

WHY NOT BUILT. Such an edit changes the verification mechanism itself, which is a §15 external-review
trigger: it is caught by review, not by this file, and chasing it here is the loop §19.5 names (four
findings in a row about the binding mechanism). The cheapest general fix, if ever authorised - a
completion marker the enforcing session writes at `sessionfinish` and the runner requires - is in
`specs/BACKLOG.md` ("Пределы тира инструментов").
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent

#: The partitions, each a directory under `tooling-tests/` and a blocking CI step (`tooling_ci_binding_violations`).
_PARTITIONS = ("portable", "powershell")

_DATABASE_VARIABLES = ("DATABASE_URL", "TEST_DATABASE_URL", "GEO_TEST_ALLOW_DB_RESET")

_STATE = pytest.StashKey[dict[str, Any]]()

#: pytest options that end the session without running a test body.
_NO_RUN_OPTIONS = (
    ("collectonly", "--collect-only"),
    ("setuponly", "--setup-only"),
    ("setupplan", "--setup-plan"),
    ("showfixtures", "--fixtures"),
    ("show_fixtures_per_test", "--fixtures-per-test"),
    ("markers", "--markers"),
)

#: pytest options that narrow the selection: `config.option` attribute -> the flag as a person types it.
_NARROWING_OPTIONS = (
    ("markexpr", "-m"),
    ("keyword", "-k"),
    ("deselect", "--deselect"),
    ("ignore", "--ignore"),
    ("ignore_glob", "--ignore-glob"),
    ("lf", "--lf/--last-failed"),
    ("stepwise", "--sw/--stepwise"),
    ("stepwise_skip", "--sw-skip"),
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--tooling-partition",
        choices=(*_PARTITIONS, "all"),
        default=None,
        help="Tooling tier partition to run (scripts/verify_local.ps1 -ToolingOnly).",
    )


def _missing_powershell_hosts() -> list[str]:
    """The two hosts the launcher checks are run under, and which of them this machine lacks.

    `powershell/test_run_full_stack_database_url_redaction.py` runs most of its cases once per host
    it finds; a machine with one host would silently run fewer of them. Requiring both keeps every
    case in the run and names the real reason when a host is missing.
    """

    missing: list[str] = []
    if shutil.which("pwsh") is None:
        missing.append("pwsh (PowerShell 7) on PATH")
    system_root = os.environ.get("SystemRoot")
    windows = (
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if system_root
        else None
    )
    if windows is None or not windows.is_file():
        missing.append(r"Windows PowerShell 5.1 (%SystemRoot%\System32\WindowsPowerShell\v1.0)")
    return missing


def pytest_configure(config: pytest.Config) -> None:
    # Here and not at import, so `tests/unit/test_tooling_tier_is_bound.py` can load this module for
    # `tooling_ci_binding_violations` without stripping the backend tier's database environment.
    for name in _DATABASE_VARIABLES:
        os.environ.pop(name, None)
    value = config.getoption("--tooling-partition")
    if value is None:
        raise pytest.UsageError(
            "The tooling tier runs only with --tooling-partition portable|powershell|all, which "
            "`scripts/verify_local.ps1 -ToolingOnly` passes; the partition is what the selection "
            "is checked against. A one-file debug run is `--noconftest` and checks nothing."
        )
    requested = _PARTITIONS if value == "all" else (value,)
    # §15 2026-10-03 (P1): `--collect-only` matched the count and ran nothing, and the runner said
    # passed. Every option that collects without executing the test bodies is refused.
    no_run = [flag for option, flag in _NO_RUN_OPTIONS if getattr(config.option, option, False)]
    if no_run:
        raise pytest.UsageError(
            f"the tooling tier executes every case; {', '.join(no_run)} would collect cases and run "
            "none of them (check PYTEST_ADDOPTS)"
        )
    # 041 F-041-13 (2026-10-16): with no count left, a narrowing option or a narrower path is refused
    # by name. A missing attribute (a plugin that is not loaded) counts as not given.
    narrowing = [flag for option, flag in _NARROWING_OPTIONS if getattr(config.option, option, None)]
    narrowing += _narrowing_paths(config, requested, whole=value == "all")
    if narrowing:
        raise pytest.UsageError(
            f"the tooling tier runs whole partitions; it was narrowed by {', '.join(narrowing)} "
            "(check PYTEST_ADDOPTS). Not checked: which tests exist. Run the tier with "
            "`scripts/verify_local.ps1 -ToolingOnly -ToolingPartition <portable|powershell|all>`; "
            "a debug run of a subset is `--noconftest` and checks nothing."
        )
    outside = sorted(
        str(path.relative_to(_HERE))
        for path in _HERE.rglob("test_*.py")
        if path.relative_to(_HERE).parts[0] not in _PARTITIONS
    )
    if outside:
        raise pytest.UsageError(
            f"test modules outside portable/ and powershell/ belong to no partition and would run "
            f"nowhere: {outside}. Move each into the partition whose CI step runs it."
        )
    if "powershell" in requested:
        missing = _missing_powershell_hosts()
        if missing:
            raise pytest.UsageError(
                "The powershell partition needs both PowerShell hosts the launcher checks run under; "
                f"missing: {', '.join(missing)}. It runs in the Windows `required-ui` job."
            )
    config.stash[_STATE] = {"requested": requested, "deselected": 0}


def _narrowing_paths(config: pytest.Config, requested: tuple[str, ...], *, whole: bool) -> list[str]:
    """Positional arguments other than the requested partition directories (or `tooling-tests` for `all`).

    A partition the arguments do not reach is caught after collection as "collects nothing".
    """

    allowed = {(_HERE / partition).resolve() for partition in requested}
    if whole:
        allowed.add(_HERE)
    narrower: list[str] = []
    for argument in config.args:
        if "::" in argument or (config.invocation_params.dir / argument).resolve() not in allowed:
            narrower.append(f"the path {argument!r}")
    return narrower


def pytest_deselected(items: list[pytest.Item]) -> None:
    if items:
        config = items[0].config
        if _STATE in config.stash:
            config.stash[_STATE]["deselected"] += len(items)


def _partition_of(item: pytest.Item) -> str | None:
    try:
        relative = Path(str(item.path)).resolve().relative_to(_HERE)
    except ValueError:
        return None
    head = relative.parts[0] if len(relative.parts) > 1 else None
    return head if head in _PARTITIONS else None


def pytest_collection_finish(session: pytest.Session) -> None:
    state = session.config.stash[_STATE]
    requested = state["requested"]
    counts = {partition: 0 for partition in _PARTITIONS}
    strays: list[str] = []
    for item in session.items:
        partition = _partition_of(item)
        if partition is None:
            strays.append(item.nodeid)
        else:
            counts[partition] += 1

    problems: list[str] = []
    if state["deselected"]:
        problems.append(
            f"{state['deselected']} case(s) were deselected; the tooling tier runs whole partitions, "
            "so no -m, -k, --deselect, --lf or deselecting plugin"
        )
    if strays:
        problems.append(f"cases outside the requested partitions: {strays[:5]}")
    for partition in _PARTITIONS:
        if partition in requested and counts[partition] == 0:
            problems.append(f"partition {partition!r} collected nothing, which is a gate over nothing")
        if partition not in requested and counts[partition]:
            problems.append(f"partition {partition!r} was not requested and selected {counts[partition]} case(s)")
    if problems:
        pytest.exit(
            "tooling tier refused: " + "; ".join(problems),
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    state["selected"] = sum(counts.values())


#: Outcome per case of this session: the call's, unless setup or teardown did not pass. One session
#: per process, so a module-level record is enough.
_OUTCOMES: dict[str, str] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    outcome = "xfail" if hasattr(report, "wasxfail") else report.outcome
    if report.when == "call" or outcome != "passed":
        if _OUTCOMES.get(report.nodeid) in (None, "passed"):
            _OUTCOMES[report.nodeid] = outcome


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash.get(_STATE, None)
    if state is None or "selected" not in state:
        return
    reports = _OUTCOMES
    unexpected = sorted(
        f"{nodeid} ({outcome})" for nodeid, outcome in reports.items() if outcome in ("skipped", "xfail")
    )
    problems: list[str] = []
    if unexpected:
        problems.append(f"skipped or xfail cases are not allowed in the tooling tier: {unexpected}")
    if len(reports) != state["selected"]:
        problems.append(f"{state['selected']} case(s) selected, {len(reports)} reported a result")
    if problems and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if problems:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.ensure_newline()
            for problem in problems:
                reporter.write_line(f"tooling tier: {problem}", red=True, bold=True)


def tooling_ci_binding_violations(workflow: dict[str, Any], runner: str) -> list[str]:
    """Whether CI runs each partition as a blocking step, and the runner as a checked session.

    Each partition must be exactly one `verify_local.ps1 -ToolingOnly -ToolingPartition <name>` step
    of its job, and neither the job nor the step may carry `if:` or `continue-on-error`: the Black
    step's `continue-on-error` in the same job is a step attribute and must stay off this one. The
    runner's tooling session must run under `$runTooling` as an `Invoke-RequiredStep` (a diagnostic
    step turns a failure into a warning and exit 0) and pass `--tooling-partition`, and must not
    pass `--noconftest`, which would run the tests without any check of this file.

    Form only: whether the steps really ran is in the job log (`gh run view <id> --log`).

    WHAT THIS DOES NOT DEFEND AGAINST (narrowed 2026-10-03, AGENTS.md §19.4): deliberate
    reconfiguration. Job `needs:` (a dependency on a job skipped on pull requests, e.g.
    `container-smoke`, skips this one), step `shell:` (`echo {0}` succeeds without running), and
    `defaults`, `strategy`, `timeout-minutes`, `PYTEST_PLUGINS`, `pytest.ini` `addopts` (`--help`,
    `--version`) are not examined. Such edits change the verification mechanism and are themselves
    §15 review triggers; see the module docstring and `specs/BACKLOG.md`.
    """

    owners = {"portable": "static-diagnostics", "powershell": "required-ui"}
    violations: list[str] = []
    jobs = workflow.get("jobs", {})
    for partition, job_id in owners.items():
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            violations.append(f"job {job_id!r} is missing; it runs the {partition} partition")
            continue
        # §15 2026-10-03 (P1): `if: false` passed an allowlist shared with continue-on-error. Any
        # `if` - false, always(), a condition - is a violation; only its absence is accepted.
        if "if" in job:
            violations.append(f"job {job_id!r} carries if: {job['if']!r}")
        if job.get("continue-on-error") not in (None, False, "false"):
            violations.append(f"job {job_id!r} carries continue-on-error")
        steps = [
            step
            for step in job.get("steps", [])
            if isinstance(step, dict)
            and "verify_local.ps1" in str(step.get("run", ""))
            and re.search(rf"-ToolingPartition\s+{partition}\b", str(step.get("run", "")))
            and "-ToolingOnly" in str(step.get("run", ""))
        ]
        if len(steps) != 1:
            violations.append(
                f"job {job_id!r} has {len(steps)} step(s) running the {partition} partition, not 1"
            )
            continue
        step = steps[0]
        if "if" in step:
            violations.append(f"the {partition} step carries if: {step['if']!r}")
        for scope, holder in (("workflow", workflow), (f"job {job_id!r}", job), ("step", step)):
            if "PYTEST_ADDOPTS" in (holder.get("env") or {}):
                violations.append(f"{scope} env sets PYTEST_ADDOPTS for the {partition} partition")
        if step.get("continue-on-error") not in (None, False, "false"):
            violations.append(f"the {partition} step carries continue-on-error")
    block = re.search(r"\n {8}if \(\$runTooling\) \{\r?\n(.*?)\n {8}\}\r?\n", runner, re.DOTALL)
    if "$runTooling = $ToolingOnly -or" not in runner or block is None or (
        "'--tooling-partition'" not in block.group(1)
        or "Invoke-RequiredStep" not in block.group(1)
        or "Invoke-DiagnosticStep" in block.group(1)
    ):
        violations.append("verify_local.ps1 does not run the checked session (--tooling-partition)")
    if "--noconftest" in runner:
        violations.append("scripts/verify_local.ps1 passes --noconftest, which drops every check of the session")
    return violations


@pytest.fixture
def tooling_ci_binding() -> Any:
    """`tooling_ci_binding_violations`, for the binding checks in both partitions."""

    return tooling_ci_binding_violations
