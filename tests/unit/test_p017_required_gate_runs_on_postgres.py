"""T1701: the REQUIRED backend gate must declare PostgreSQL, in the workflow's own words.

WHAT THIS PROVES. `.github/workflows/quality.yml` declares a job that (a) runs on every pull
request - it carries no `if:` that narrows it to `schedule`/`workflow_dispatch` - (b) has a
`postgres:` service container, and (c) hands `scripts/verify_local.ps1` a PostgreSQL
`TEST_DATABASE_URL` for the PostgreSQL-marker sessions that used to live in the scheduled
`postgres` job. Until 2026-09-21 no required job had any of the three: PostgreSQL ran only on the
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

    $env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_<slug>"
    $env:GEO_TEST_ALLOW_DB_RESET = "1"
    .\\scripts\\verify_local.ps1 -TaskSlug <slug> -BackendOnly -BackendMarker postgres `
      -BackendSelector tests/integration

WHERE IT LIVES, AND WHY NOT WHERE THE SPEC SAID. 017 `spec.md:61` names
`tests/integration/test_p017_required_gate_runs_on_postgres.py`. That path is not available: this
repository's own taxonomy requires every `tests/integration/*_postgres.py` module to carry
`pytest.mark.postgres` (`tests/unit/test_postgres_test_taxonomy.py:123-152`), and the marker would
take this guard out of the default tier - the one tier that must notice the required gate losing
PostgreSQL. Measured 2026-09-21: placed as the spec wrote it, the taxonomy guard failed with
"PostgreSQL suffix modules without marker". The file reads one YAML file and needs no database, so
it belongs here beside the other workflow-form guards.
"""

from __future__ import annotations

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

_MARKER_TIER_SELECTOR = "-BackendSelector tests/integration"

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
    "the collected-test count in it; locally, run the tier against a disposable geov0_test_* "
    "database with -BackendMarker postgres."
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

    marker_steps: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for job_id, job in postgres_jobs.items():
        for step in _verify_local_steps(job):
            if "-BackendMarker postgres" in _run_text(step):
                marker_steps.append((job_id, job, step))

    if not marker_steps:
        violations.append(
            "No required PostgreSQL job runs verify_local.ps1 with -BackendMarker postgres. "
            "Without that marker the postgres-marked tests are deselected by the default "
            "`not slow and not postgres` expression and the job passes having collected none of "
            "them (AGENTS.md §5)."
        )

    for job_id, job, step in marker_steps:
        env = _effective_env(job, step)
        url = env.get("TEST_DATABASE_URL", "")
        if not _POSTGRES_TEST_DB.fullmatch(url):
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: TEST_DATABASE_URL is {url!r}; a "
                "PostgreSQL marker session needs a postgresql+asyncpg URL on a geov0_test_* "
                "database, otherwise the tier fails closed in collection instead of running."
            )
        if env.get("GEO_TEST_ALLOW_DB_RESET") != "1":
            violations.append(
                f"Job '{job_id}', step {step.get('name')!r}: GEO_TEST_ALLOW_DB_RESET is not '1', "
                "so scripts/validate_test_database_url.py refuses the PostgreSQL URL."
            )

    marker_runs = [_run_text(step) for _, _, step in marker_steps]
    if not any(_MARKER_TIER_SELECTOR in run for run in marker_runs):
        violations.append(
            "The PostgreSQL marker tier (-BackendMarker postgres -BackendSelector "
            "tests/integration) is not in a required job."
        )
    for selector in _CONCURRENCY_SELECTORS:
        if not any(selector in run for run in marker_runs):
            violations.append(
                f"Concurrency selector not present in any required PostgreSQL job: {selector}"
            )

    scheduled_marker_tiers = sorted(
        job_id
        for job_id, job in jobs.items()
        if isinstance(job, dict)
        and _is_scheduled_only(job)
        and any(
            "-BackendMarker postgres" in _run_text(step)
            and _MARKER_TIER_SELECTOR in _run_text(step)
            for step in _verify_local_steps(job)
        )
    )
    if scheduled_marker_tiers:
        violations.append(
            "The PostgreSQL marker tier is back in a schedule/dispatch-only job "
            f"({', '.join(scheduled_marker_tiers)}): a pull request would not run it."
        )

    return violations


def test_the_required_backend_gate_declares_postgres_and_its_url() -> None:
    violations = required_postgres_backend_violations(_load_workflow())

    assert not violations, "\n".join(violations) + _LIMITS


def test_the_guard_notices_a_gate_that_lost_postgres() -> None:
    """Counter-check: the three ways this gate has actually regressed must each be caught."""

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
        if "-BackendMarker postgres" in _run_text(step):
            step.setdefault("env", {})["TEST_DATABASE_URL"] = (
                "sqlite+aiosqlite:///./.local-run/test-runs/ci/test.db"
            )
    assert required_postgres_backend_violations(sqlite_url)


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
        marker_steps = [
            step
            for step in _verify_local_steps(workflow["jobs"][job_id])
            if "-BackendMarker postgres" in _run_text(step)
        ]
        assert marker_steps, (
            f"Job '{job_id}' has a PostgreSQL service but runs no -BackendMarker postgres "
            "session, so its URL mutation would flip nothing." + _LIMITS
        )
