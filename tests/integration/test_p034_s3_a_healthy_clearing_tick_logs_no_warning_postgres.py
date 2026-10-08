"""034 S3 (F-034-7): a clearing tick that went well writes nothing at WARNING.

AGENTS.md section 12: the simulator does not log in every iteration of a tick, and a WARNING is something an operator
reads. Until 034 S3 every clearing tick that cleared wrote seven WARNING lines - `tick_clearing_enter`,
`clearing_eq_enter`, `clearing_pass_done`, `clearing_eq_done`, `clearing_patch_start`, `clearing_patch_done`,
`tick_clearing_done` - so on a run with clearing a real warning (`tick_clearing_hard_timeout`, `clearing_failed`) sat
among lines that say "nothing is wrong". `LOG_LEVEL` is `INFO` by default (`app/config.py`, the one logging
configuration in `app/main.py`), so the same lines at INFO stay visible.

The stand is 023 (d)'s: the real `RealTick.maybe_run_clearing` -> the common runner -> PostgreSQL, one triangle.

Checks the LEVEL of what the tick's own logger wrote on that path, and that the progress lines are still written.
Does not check the wording of the lines, nor other loggers (the clearing runner's, the payment service's).
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest

from tests.integration.test_p023_d_tick_driver_through_runner_postgres import (  # noqa: F401 - `factory` is a fixture
    DRAIN_TICKS,
    T1,
    _runner_module,
    _stand,
    factory,
)

TICK_LOGGER = "p023d.tick"  # the logger the stand gives its runner

#: The seven lines that say "the clearing of this tick is going well". They are progress, never a warning.
PROGRESS_LINES = (
    "tick_clearing_enter",
    "clearing_eq_enter",
    "clearing_pass_done",
    "clearing_patch_start",
    "clearing_patch_done",
    "clearing_eq_done",
    "tick_clearing_done",
)


def _of_the_tick(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == TICK_LOGGER and r.levelno >= level]


async def _a_tick_that_cleared(stand, caplog) -> list[logging.LogRecord]:
    """Tick until one tick commits a clearing without the hard timeout cutting it; returns that tick's records."""
    runner = _runner_module()
    await asyncio.wrap_future(runner._default_planner_executor().submit(runner.plan_clearing, []))  # a warm planner
    for k in range(DRAIN_TICKS):
        stand.run.tick_index = 1 + k
        before = await stand.clearings()
        caplog.clear()
        await stand.tick()
        cut = any("tick_clearing_hard_timeout" in m for m in _of_the_tick(caplog, logging.WARNING))
        if await stand.clearings() > before and not cut:
            return list(caplog.records)
    pytest.fail(f"no tick of {DRAIN_TICKS} cleared without the hard timeout: the stand did not reach the healthy path")


@pytest.mark.asyncio
async def test_control_a_failed_clearing_is_a_warning_of_the_tick(factory, monkeypatch, caplog) -> None:  # noqa: F811
    """Anti-vacuum: the capture sees the tick's WARNINGs, and a clearing that failed is still one."""
    caplog.set_level(logging.DEBUG)
    stand = await _stand(factory, T1)

    async def broken(**_kwargs):
        raise RuntimeError("the clearing of this tick is broken")

    monkeypatch.setattr(stand.runner._tick, "_run_clearing", broken)
    await stand.tick()
    warnings = _of_the_tick(caplog, logging.WARNING)
    assert any("tick_clearing_failed" in m for m in warnings), warnings


@pytest.mark.asyncio
async def test_a_clearing_tick_that_cleared_writes_no_warning(factory, caplog) -> None:  # noqa: F811
    caplog.set_level(logging.DEBUG)
    stand = await _stand(factory, T1)
    await _a_tick_that_cleared(stand, caplog)

    # Controls: the cycle is really cleared and published, and the tick's progress lines are captured.
    assert await stand.total() == 0 and stand.done_events(), "the tick did not clear the triangle"
    progress = _of_the_tick(caplog, logging.DEBUG)
    for name in ("tick_clearing_enter", "clearing_pass_done", "tick_clearing_done"):
        assert any(f"simulator.real.{name} " in m for m in progress), f"the progress line {name} is gone: {progress}"

    # Only the PROGRESS lines are judged. A tick that cleared may still be slow, and `clearing_patch_slow` or a slow
    # commit is then a legitimate WARNING: rejecting every WARNING made this test fail on a loaded machine for
    # behaviour that is correct (section-15 review of 034 S3, 2026-10-09).
    warnings = [
        m for m in _of_the_tick(caplog, logging.WARNING) if any(re.search(rf"\b{name}\b", m) for name in PROGRESS_LINES)
    ]
    assert warnings == [], (
        f"a clearing tick that cleared its cycle wrote {len(warnings)} progress line(s) at WARNING or above, "
        f"expected 0: {warnings}")
