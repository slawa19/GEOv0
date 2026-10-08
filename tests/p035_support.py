"""Programme 035: planner-process entries for the A1 tests. NOT a test module.

Module-level and importing only the planner, so a `spawn` worker can import them by name without importing the
test tier (`tests/p023_support.py::slow_plan` is the same idea).
"""

from __future__ import annotations

from dataclasses import replace


def slow_rotated_plan(delay_seconds: float, edges):
    """Sleep, then the real planner, with every cycle ROTATED by one edge.

    `flow_planner` returns each cycle in its canonical rotation (smallest debt UUID first), in whatever process
    it runs. A rotated cycle is therefore a plan that can only have come back from the worker: a caller that
    recomputes the plan itself - on the event loop or anywhere else - answers with the canonical rotation.
    """

    import time

    from app.core.clearing.flow_planner import plan_clearing

    time.sleep(delay_seconds)
    plan = plan_clearing(edges)
    return replace(plan, cycles=tuple(replace(c, edges=c.edges[1:] + c.edges[:1]) for c in plan.cycles))
