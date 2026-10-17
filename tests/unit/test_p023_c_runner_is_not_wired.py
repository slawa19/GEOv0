"""Programme 023, slice (c), narrowed in slice (d) and in 041: the periodic loop is off by default and its failures are loud.

HISTORY: the module name is historical. Until slice (d) it also asserted that no production entrypoint used the
runner; the atomic switch inverted that check into `test_p023_d_product_callers_go_through_the_runner.py`. The AST
walker that remained, with its anti-vacuum probe, guarded nothing after that and was deleted 2026-10-16 (041
`T4104`); what is left is behaviour, not form.

What it checks: with default settings `_start_configured_background_tasks` starts no `clearing` task and the
setting's default is False (anti-vacuum: with `CLEARING_PERIODIC_ENABLED` set the loop IS started); a refusal of
the isolation rule is recorded as a failed job (health `degraded`), not swallowed; an equivalent pass that stopped
on an error degrades health, while ordinary interruptions keep it `ok`.

Имя историческое, сканер проводки удалён 041 T4104.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.p023_support import require_target


def _started_names(monkeypatch, *, periodic: bool | None) -> list[str]:
    import app.core.maintenance_jobs as main
    from app.config import settings

    if periodic is not None:
        monkeypatch.setattr(settings, "CLEARING_PERIODIC_ENABLED", periodic)
    started: list[str] = []
    monkeypatch.setattr(
        main, "_start_supervised_background_task", lambda app, *, name, coroutine_factory: started.append(name)
    )
    main._start_configured_background_tasks(SimpleNamespace())
    return started


def test_the_periodic_loop_is_off_by_default_and_starts_only_when_configured(monkeypatch) -> None:
    from app.config import Settings

    assert Settings.model_fields["CLEARING_PERIODIC_ENABLED"].default is False
    assert "clearing" not in _started_names(monkeypatch, periodic=None)
    # Anti-vacuum: the same probe sees the start when the setting asks for it.
    assert "clearing" in _started_names(monkeypatch, periodic=True)


@pytest.mark.asyncio
async def test_an_isolation_refusal_is_recorded_as_a_failed_job_not_swallowed(monkeypatch) -> None:
    import app.core.clearing.runner as runner
    import app.core.maintenance_jobs as main
    from app.utils.background_jobs import background_health_status

    async def refuse(_factory, _redis):
        raise runner.ClearingPeriodicRefused("simulator_real_runs_in_database")

    monkeypatch.setattr(runner, "run_periodic_clearing_pass", refuse)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    await main._run_periodic_clearing_once(app)
    assert app.state.background_jobs["clearing"]["status"] == "failed"
    assert app.state.background_jobs["clearing"]["event"] == "refused_simulator_real_runs_in_database"
    assert background_health_status(app) == "degraded"


# ------------------------------------------------------------------------------ fix-delta (review P2-4), red-first


def _result(runner, reason):
    return runner.ClearingPassResult(
        equivalent="PQH",
        status="interrupted",
        reason=reason,
        committed=(),
        remaining_cycles=None,
        remaining_v_edge_atoms=None,
        plans=0,
        distributed_exclusive=False,
    )


async def _health_after(monkeypatch, results) -> str:
    import app.core.clearing.runner as runner
    import app.core.maintenance_jobs as main
    from app.utils.background_jobs import background_health_status

    async def periodic(_factory, _redis):
        return results

    monkeypatch.setattr(runner, "run_periodic_clearing_pass", periodic)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    await main._run_periodic_clearing_once(app)
    return background_health_status(app)


@pytest.mark.asyncio
async def test_an_equivalent_pass_error_degrades_health(monkeypatch) -> None:
    import app.core.clearing.runner as runner

    status = await _health_after(monkeypatch, {"PQH": _result(runner, runner.InterruptReason.ERROR)})
    require_target(status == "degraded", f"a pass that stopped on an error left health {status!r}")


@pytest.mark.parametrize("reason", ["LEASE_LOST", "BUDGET_EXHAUSTED", "REPLAN_LIMIT", "OPERATIONAL_LIMIT"])
@pytest.mark.asyncio
async def test_counter_check_ordinary_interruptions_keep_health_ok(monkeypatch, reason) -> None:
    import app.core.clearing.runner as runner

    status = await _health_after(monkeypatch, {"PQH": _result(runner, getattr(runner.InterruptReason, reason))})
    assert status == "ok", (reason, status)
