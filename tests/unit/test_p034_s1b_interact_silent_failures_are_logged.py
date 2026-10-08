"""034 S1b, F-034-9: a failure of the Interact routes' best-effort in-memory work is logged, not swallowed.

WHAT WAS WRONG (on `0f248b9c`). After an Interact action has committed, `app/api/v1/simulator.py` brings the run's
in-memory topology in line with it (`_mutate_runtime_trustline_topology_best_effort`) and computes the visual patches
of the event (`_compute_viz_patches_best_effort`). Both are best-effort and both ended in a bare `except Exception`
with no log: when they failed, the run's `_scenario_raw` / `_edges_by_equivalent` silently stopped matching the
database, or the event went out without its patch, and nothing anywhere said that it had happened (AGENTS.md §9, §12).

These tests make each of the three swallowing handlers fire and ask for a WARNING that carries the exception. They
do not judge the wording - only that the failure can be found - and they keep the contract of the helpers: neither
raises, and the patches of a failed computation are absent, not invented.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import app.api.v1.simulator as simulator_module
from app.core.simulator.models import RunRecord


def _failures(caplog) -> list[logging.LogRecord]:
    """WARNING-or-worse records of the simulator routes' logger that carry an exception."""

    return [r for r in caplog.records
            if r.name == simulator_module.logger.name and r.levelno >= logging.WARNING and r.exc_info]


def test_a_failed_sync_of_the_runtime_topology_is_logged(monkeypatch, caplog) -> None:
    run = RunRecord(run_id="r-034-9", scenario_id="s", mode="real", state="running")
    run._scenario_raw = {"participants": [], "trustlines": []}
    run._edges_by_equivalent = {"UAH": 5}  # not a list of pairs: the edge-cache update raises
    monkeypatch.setitem(simulator_module.runtime._runs, run.run_id, run)

    with caplog.at_level(logging.WARNING, logger=simulator_module.logger.name):
        simulator_module._mutate_runtime_trustline_topology_best_effort(
            run_id=run.run_id, op="create", equivalent="UAH", from_pid="alice", to_pid="bob", limit="10")

    # Controls: the helper did not raise, and it failed where the stand made it fail - AFTER the scenario was
    # updated, so the run's two in-memory pictures of the topology now disagree.
    assert [(t["from"], t["to"]) for t in run._scenario_raw["trustlines"]] == [("alice", "bob")]
    assert run._edges_by_equivalent == {"UAH": 5}

    failures = _failures(caplog)
    assert len(failures) == 1 and run.run_id in failures[0].getMessage(), (
        f"the run's in-memory topology could not be brought in line with a committed Interact action "
        f"(scenario updated, edge cache not); warnings carrying the exception: "
        f"{[r.getMessage() for r in failures]}. Expected one, naming the run"
    )


class _Helper:
    """A viz helper whose quantile refresh fails; nothing else of it is reached in these tests."""

    async def maybe_refresh_quantiles(self, *_args, **_kwargs) -> None:
        raise RuntimeError("p034: the quantiles could not be read")


class _Session:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("p034: the patch query failed")


@pytest.mark.asyncio
async def test_a_failed_patch_computation_is_logged_twice_over(caplog) -> None:
    """Both handlers of `_compute_viz_patches_best_effort` on one call: the optional quantile refresh fails and is
    passed over, then the first patch query fails and the whole computation is given up."""

    run = SimpleNamespace(run_id="r-034-9", tick_index=1, _real_viz_by_eq={"UAH": _Helper()}, _real_participants=[])

    with caplog.at_level(logging.WARNING, logger=simulator_module.logger.name):
        patches = await simulator_module._compute_viz_patches_best_effort(
            session=_Session(), run=run, equivalent_code="UAH", edges_pairs=[("alice", "bob")])

    assert patches == (None, None)  # control: best-effort - no raise, and no patch invented
    messages = [r.getMessage() for r in _failures(caplog)]
    assert len(messages) == 2 and all("UAH" in m for m in messages), (
        f"the quantile refresh and then the patch query of an Interact event failed; warnings carrying the "
        f"exception: {messages}. Expected two, each naming the equivalent"
    )
