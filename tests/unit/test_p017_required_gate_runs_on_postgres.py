"""T1701: the REQUIRED backend gate must declare PostgreSQL, in the workflow's own words.

WHAT THIS PROVES. `.github/workflows/quality.yml` declares a job that (a) runs on every pull
request - it carries no `if:` that narrows it to `schedule`/`workflow_dispatch` - (b) has a
`postgres:` service container, and (c) hands `scripts/verify_local.ps1` a PostgreSQL
`TEST_DATABASE_URL` for its backend tier. Until 2026-09-21 no required job had any of the three: PostgreSQL ran only on the
weekly schedule and on manual dispatch (`quality.yml:156-159` before this change), so a pull
request could be green while every advisory lock, `FOR UPDATE` and SERIALIZABLE retry in the money
path went unobserved (017 `spec.md`, Problem, defect 1).

WHAT THIS DOES NOT PROVE, and the boundary matters more than the check (AGENTS.md §11: a guard
checks FORM, not truth). Reading YAML cannot show that a single test ever reached PostgreSQL, that
the service came up, that the collected count was not zero, or that a marker expression did not
deselect everything (the marker trap, AGENTS.md §5). Those are established by the job's own log and
by the number of tests it reports collecting:

    gh run view <run-id> --log --job "Required backend gates (PostgreSQL)"

and locally, against a disposable database, by:

    .\\scripts\\verify_local.ps1 -TaskSlug <slug> -BackendOnly

WHERE IT LIVES, AND THE CORRECTED REASON (2026-09-22). 017 `spec.md:61` named
`tests/integration/test_p017_required_gate_runs_on_postgres.py`. THAT EXACT PATH is not available:
the repository's taxonomy requires every module whose FILENAME ENDS IN `_postgres.py` to carry
`pytest.mark.postgres` (`tests/unit/test_postgres_test_taxonomy.py:123-152`), and the marker would
take this guard out of the default tier - the one tier that must notice the required gate losing
PostgreSQL. Measured 2026-09-21: placed as the spec wrote it, the taxonomy guard failed with
"PostgreSQL suffix modules without marker". But the first version of this note went further and said
`tests/integration/` was therefore impossible, which the external review of `e2e1380..37fec08`
refuted: the taxonomy reads the SUFFIX, not every filename mentioning PostgreSQL, so
`tests/integration/test_p017_required_postgres_gate.py` would be unmarked and visible in the default
tier. The location stands on what the file IS - it reads one YAML file, opens no database and needs
no session - and not on there being no alternative.

AND WHAT IT READS GREW ON 2026-09-22. Until then every check here read the JOB and none read its
STEPS, so a schedule-only `if:` or a `continue-on-error: true` on the PostgreSQL steps left the job's
condition, service container, URL, marker and selectors all intact while a pull request ran only the
default tier. Step conditions and failure tolerance are now violations, with mutations 4-7 in
`test_the_guard_notices_a_gate_that_lost_postgres` as the counter-check.

AND WHAT IT LOOKS FOR CHANGED ON 2026-09-23 (017 stage 2c). Until then the job ran three sessions -
the concurrency matrix and the marker tier on PostgreSQL, the default tier on SQLite - and this guard
found the PostgreSQL ones by `-BackendMarker postgres` and checked the matrix by its three selectors
on the command line. The marker and the parameter are gone and the job runs ONE session of the whole
tier. So it now finds the backend-tier step (`-BackendOnly`), requires that it selects no files at all
(a `-BackendSelector` would narrow the whole tier to a subset while every other fact still looked
right - mutation 8), and checks the matrix where it can now be lost: its three tests must still exist
under their names and carry no `slow` marker, which is the one thing the tier's `not slow` excludes.
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github" / "workflows" / "quality.yml"

# The three concurrency selectors the scheduled `postgres` job used to own (quality.yml:206-210
# before 2026-09-21). They are named here one by one on purpose: "the matrix moved" is exactly the
# kind of claim that loses a member silently.
_CONCURRENCY_SELECTORS = (
    "tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py"
    "::test_concurrent_payments_shared_bottleneck_commit_once_postgres",
    "tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py"
    "::test_concurrent_payment_and_clearing_same_trustline_preserve_effects_postgres",
    "tests/integration/test_payment_idempotency_postgres.py"
    "::test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres",
)

_SCHEDULED_ONLY = re.compile(
    r"github\.event_name\s*==\s*'(?:schedule|workflow_dispatch)'"
)

_POSTGRES_TEST_DB = re.compile(
    r"postgresql\+asyncpg://[^\s]*/geov0_test_[A-Za-z0-9_]+"
)

_LIMITS = (
    "\n\nFORM ONLY. This reads .github/workflows/quality.yml and proves what the workflow "
    "DECLARES. It cannot show that any test executed against PostgreSQL, that the service "
    "container came up, or that the marker expression selected anything at all. Check the run "
    "itself - `gh run view <run-id> --log --job \"Required backend gates (PostgreSQL)\"` - and "
    "the collected-test count in it; locally, `scripts/verify_local.ps1 -TaskSlug <slug> -BackendOnly` "
    "against a disposable geov0_test_* database."
)


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _is_scheduled_only(job: dict[str, Any]) -> bool:
    condition = job.get("if")
    return bool(condition) and bool(_SCHEDULED_ONLY.search(str(condition)))


def _has_postgres_service(job: dict[str, Any]) -> bool:
    services = job.get("services") or {}
    return any(
        str((service or {}).get("image", "")).startswith("postgres:")
        for service in services.values()
    )


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in (job.get("steps") or []) if isinstance(step, dict)]


def _run_text(step: dict[str, Any]) -> str:
    return " ".join(str(step.get("run", "")).split())


def _effective_env(job: dict[str, Any], step: dict[str, Any]) -> dict[str, str]:
    env = dict(job.get("env") or {})
    env.update(step.get("env") or {})
    return {key: str(value) for key, value in env.items()}


def _verify_local_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in _steps(job) if "verify_local.ps1" in _run_text(step)]


def _tier_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps that run the backend tier: `verify_local.ps1 -BackendOnly`."""

    return [step for step in _verify_local_steps(job) if "-BackendOnly" in _run_text(step)]


def _is_slow_marker(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "slow"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def concurrency_test_problems(source: str, test_name: str) -> list[str]:
    """Why the tier's `not slow` session would NOT collect `test_name` from this module's source.

    Empty means it would. A pure function of the source so the counter-check can plant the two ways
    to lose a test - renaming it and marking it `slow` - without touching a real module.
    """

    tree = ast.parse(source)
    problems: list[str] = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in statement.targets
        ):
            if any(_is_slow_marker(node) for node in ast.walk(statement.value)):
                problems.append("the module is marked slow")
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name
    ]
    if not functions:
        problems.append(f"no test named {test_name}")
    for function in functions:
        if any(_is_slow_marker(node) for d in function.decorator_list for node in ast.walk(d)):
            problems.append(f"{test_name} is marked slow")
    return problems


def required_postgres_backend_violations(workflow: dict[str, Any]) -> list[str]:
    """Return one message per broken requirement; empty means the gate has the declared shape.

    Factored out of the assertions so the counter-check below can feed it a deliberately broken
    workflow and see it complain (AGENTS.md §9, anti-vacuum: a rule that excludes or accepts
    anything must be shown to still notice the real case).
    """

    violations: list[str] = []

    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        violations.append(
            "quality.yml does not run on pull_request at all, so nothing in it is a required gate."
        )
        return violations

    jobs = workflow.get("jobs") or {}
    required_jobs = {
        job_id: job
        for job_id, job in jobs.items()
        if isinstance(job, dict) and not _is_scheduled_only(job)
    }
    postgres_jobs = {
        job_id: job
        for job_id, job in required_jobs.items()
        if _has_postgres_service(job)
    }

    if not postgres_jobs:
        violations.append(
            "No required job (one without a schedule/dispatch `if:`) declares a `postgres:` "
            "service container. PostgreSQL would run only on the weekly schedule again, which is "
            "the state 017 stage 1 removed."
        )

    tier_steps: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for job_id, job in postgres_jobs.items():
        for step in _tier_steps(job):
            tier_steps.append((job_id, job, step))

    if not tier_steps:
        violations.append(
            "No required PostgreSQL job runs the backend tier (verify_local.ps1 -BackendOnly). "
            "The service container would be up and nothing would use it."
        )

    for job_id, job, step in tier_steps:
        # A STEP CONDITION IS A WAY PAST THIS GUARD, NOT A GAP IN IT (2026-09-22, Codex external
        # review of `e2e1380..37fec08`). Everything below used to read the JOB - its `if:`, its
        # service, its environment - and nothing read the step. Hanging
        # `if: github.event_name == 'schedule'` on both PostgreSQL steps left every one of those
        # job-level facts intact while a pull request ran only the default tier; so did
        # `continue-on-error: true`, which keeps the step running and stops it failing the gate.
        # `AGENTS.md` §15: look for the way AROUND a guard, not for its absence.
        condition = step.get("if")
        if condition is not None:
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: carries `if: {condition}`. A required "
                "PostgreSQL session must be unconditional - a step condition narrows it while the "
                "job's own `if:`, its service container and its environment all still look right."
            )
        if step.get("continue-on-error") in (True, "true"):
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: carries `continue-on-error: true`, so "
                "a PostgreSQL failure would be reported and then ignored, and the required gate "
                "would be green with the money path unobserved."
            )
        if job.get("if") is not None:
            violations.append(
                f"Job '{job_id}' carries `if: {job.get('if')}`. It is not the schedule/dispatch "
                "condition this guard recognises, but any job condition can exclude pull requests; "
                "the required PostgreSQL job runs unconditionally or it is not required."
            )
        if job.get("continue-on-error") in (True, "true"):
            violations.append(
                f"Job '{job_id}' carries `continue-on-error: true`, so nothing in it can fail the "
                "required gate."
            )

        env = _effective_env(job, step)
        url = env.get("TEST_DATABASE_URL", "")
        if not _POSTGRES_TEST_DB.fullmatch(url):
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: TEST_DATABASE_URL is {url!r}; "
                "the backend tier needs a postgresql+asyncpg URL on a geov0_test_* database; "
                "tests/conftest.py refuses any other before collecting a test."
            )
        if env.get("GEO_TEST_ALLOW_DB_RESET") != "1":
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: GEO_TEST_ALLOW_DB_RESET is not '1', "
                "so scripts/validate_test_database_url.py refuses the PostgreSQL URL."
            )

        if "-BackendSelector" in _run_text(step):
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: passes -BackendSelector, so the required "
                "PostgreSQL session runs a subset of the tier. It must select no files - the whole tier "
                "is the gate, the concurrency matrix included."
            )

    return violations


def test_the_required_backend_gate_declares_postgres_and_its_url() -> None:
    violations = required_postgres_backend_violations(_load_workflow())

    assert not violations, "\n".join(violations) + _LIMITS


def test_the_guard_notices_a_gate_that_lost_postgres() -> None:
    """Counter-check: every way this gate can lose PostgreSQL must be caught.

    Mutations 1-3 are the ways it has actually regressed. Mutations 4-6 are the ways AROUND it that
    the external review of `e2e1380..37fec08` proposed and the guard did not see: the job kept its
    service, its URL, its marker and its selectors, and a pull request still ran only the default
    tier. They are witnesses, not history - but a guard that misses them is not evidence about the
    gate, only about the last shape someone happened to break.
    """

    workflow = _load_workflow()
    assert not required_postgres_backend_violations(workflow), (
        "Precondition for the counter-check: the committed workflow must be clean."
    )

    def _required_postgres_job_id(candidate: dict[str, Any]) -> str:
        return next(
            job_id
            for job_id, job in candidate["jobs"].items()
            if isinstance(job, dict)
            and not _is_scheduled_only(job)
            and _has_postgres_service(job)
        )

    # 1. The service container disappears.
    without_service = copy.deepcopy(workflow)
    del without_service["jobs"][_required_postgres_job_id(without_service)]["services"]
    assert required_postgres_backend_violations(without_service)

    # 2. The job goes back to schedule/dispatch only - exactly the pre-017 state.
    scheduled_again = copy.deepcopy(workflow)
    scheduled_again["jobs"][_required_postgres_job_id(scheduled_again)]["if"] = (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    )
    assert required_postgres_backend_violations(scheduled_again)

    # 3. The URL slides back to SQLite while everything else keeps its shape.
    sqlite_url = copy.deepcopy(workflow)
    job = sqlite_url["jobs"][_required_postgres_job_id(sqlite_url)]
    for step in _verify_local_steps(job):
        if "-BackendOnly" in _run_text(step):
            step.setdefault("env", {})["TEST_DATABASE_URL"] = (
                "sqlite+aiosqlite:///./.local-run/test-runs/ci/test.db"
            )
    assert required_postgres_backend_violations(sqlite_url)

    # 4. The PostgreSQL STEPS become schedule-only while the job stays unconditional.
    scheduled_steps = copy.deepcopy(workflow)
    job = scheduled_steps["jobs"][_required_postgres_job_id(scheduled_steps)]
    for step in _verify_local_steps(job):
        if "-BackendOnly" in _run_text(step):
            step["if"] = "github.event_name == 'schedule'"
    assert required_postgres_backend_violations(scheduled_steps)

    # 5. The PostgreSQL steps keep running and stop mattering.
    tolerated = copy.deepcopy(workflow)
    job = tolerated["jobs"][_required_postgres_job_id(tolerated)]
    for step in _verify_local_steps(job):
        if "-BackendOnly" in _run_text(step):
            step["continue-on-error"] = True
    assert required_postgres_backend_violations(tolerated)

    # 6. A job condition that is not the schedule/dispatch one this guard recognises, and still
    #    takes PostgreSQL off every pull request.
    not_on_pull_request = copy.deepcopy(workflow)
    not_on_pull_request["jobs"][_required_postgres_job_id(not_on_pull_request)]["if"] = (
        "github.event_name != 'pull_request'"
    )
    assert required_postgres_backend_violations(not_on_pull_request)

    # 7. The whole job is allowed to fail.
    tolerant_job = copy.deepcopy(workflow)
    tolerant_job["jobs"][_required_postgres_job_id(tolerant_job)]["continue-on-error"] = True
    assert required_postgres_backend_violations(tolerant_job)

    # 8. The session keeps its service, URL and step, and quietly narrows to part of the tier.
    narrowed = copy.deepcopy(workflow)
    job = narrowed["jobs"][_required_postgres_job_id(narrowed)]
    for step in _verify_local_steps(job):
        if "-BackendOnly" in _run_text(step):
            step["run"] = str(step["run"]) + " -BackendSelector tests/unit"
    assert required_postgres_backend_violations(narrowed)


def test_the_counter_check_is_not_vacuous() -> None:
    """The mutations above must be reachable: the committed workflow must have what they break."""

    workflow = _load_workflow()
    postgres_job_ids = [
        job_id
        for job_id, job in workflow["jobs"].items()
        if isinstance(job, dict)
        and not _is_scheduled_only(job)
        and _has_postgres_service(job)
    ]

    assert postgres_job_ids, (
        "There is no required job with a PostgreSQL service to mutate, so the counter-check above "
        "would have proved nothing." + _LIMITS
    )
    for job_id in postgres_job_ids:
        marker_steps = _tier_steps(workflow["jobs"][job_id])
        assert marker_steps, (
            f"Job '{job_id}' has a PostgreSQL service but runs no backend tier "
            "(verify_local.ps1 -BackendOnly), so its URL mutation would flip nothing." + _LIMITS
        )
        # Mutations 4-7 only prove something if the committed workflow does NOT already carry the
        # thing they add. An `if:` or a `continue-on-error:` already present would make those four
        # assertions pass by doing nothing.
        assert "if" not in workflow["jobs"][job_id], (
            f"Job '{job_id}' already carries a condition, so mutation 6 would flip nothing."
        )
        assert "continue-on-error" not in workflow["jobs"][job_id], (
            f"Job '{job_id}' already tolerates failure, so mutation 7 would flip nothing."
        )
        for step in marker_steps:
            assert "-BackendSelector" not in _run_text(step), (
                f"Job '{job_id}', step {step.get('name')!r} already selects files, so mutation 8 "
                "would flip nothing."
            )
            assert "if" not in step and "continue-on-error" not in step, (
                f"Job '{job_id}', step {step.get('name')!r} already carries a condition or "
                "failure tolerance, so mutations 4 and 5 would flip nothing."
            )


def test_the_concurrency_matrix_is_still_collected_by_the_tier() -> None:
    """The three matrix tests exist under their names and nothing marks them `slow`.

    FORM, not truth: this reads the modules. That the tier actually collected and passed them is in
    the job's log (`gh run view <run-id> --log --job "Required backend gates (PostgreSQL)"`), where the
    whole-tier session reports them among its passed tests.
    """

    problems = []
    for selector in _CONCURRENCY_SELECTORS:
        path, _, name = selector.partition("::")
        source = (_ROOT / path).read_text(encoding="utf-8")
        problems.extend(f"{selector}: {p}" for p in concurrency_test_problems(source, name))
    assert not problems, "\n".join(problems) + _LIMITS


def test_the_matrix_check_notices_a_renamed_or_slowed_test() -> None:
    """Counter-check for the one above: the two ways the tier's `not slow` session loses a test."""

    healthy = "import pytest\n\nasync def test_x():\n    pass\n"
    assert concurrency_test_problems(healthy, "test_x") == []
    assert concurrency_test_problems(healthy, "test_y")
    assert concurrency_test_problems(
        "import pytest\n\n@pytest.mark.slow\nasync def test_x():\n    pass\n", "test_x"
    )
    assert concurrency_test_problems(
        "import pytest\npytestmark = [pytest.mark.slow]\n\nasync def test_x():\n    pass\n", "test_x"
    )
