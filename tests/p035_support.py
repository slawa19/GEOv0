"""Programme 035: planner-process entries for the A1 tests. NOT a test module.

Module-level and importing only the planner, so a `spawn` worker can import them by name without importing the
test tier (`tests/p023_support.py::slow_plan` is the same idea).
"""

from __future__ import annotations

from dataclasses import replace

#: How long a held plan waits for its test before it gives up: a stand that forgot to release (or to terminate) the
#: worker must not leave a process spinning for the rest of the session.
_HELD_PLAN_GIVES_UP_AFTER_SECONDS = 120.0


def _rotated(plan):
    return replace(plan, cycles=tuple(replace(c, edges=c.edges[1:] + c.edges[:1]) for c in plan.cycles))


def slow_rotated_plan(delay_seconds: float, edges):
    """Sleep, then the real planner, with every cycle ROTATED by one edge.

    `flow_planner` returns each cycle in its canonical rotation (smallest debt UUID first), in whatever process
    it runs. A rotated cycle is therefore a plan that can only have come back from the worker: a caller that
    recomputes the plan itself - on the event loop or anywhere else - answers with the canonical rotation.
    """

    import time

    from app.core.clearing.flow_planner import plan_clearing

    time.sleep(delay_seconds)
    return _rotated(plan_clearing(edges))


def held_rotated_plan(started_path: str, release_path: str, edges):
    """A plan that stays IN FLIGHT until the test lets it go - a barrier, not a duration.

    The worker creates `started_path` when the plan has begun in it, then waits for `release_path` to exist, and
    only then runs the real planner (rotated, as above). Two files, because a `spawn` worker shares nothing else
    with the test that a pool's `submit` can carry. The test asserts its outcome while the barrier is closed and
    opens it afterwards (or terminates the worker), so no assertion depends on how fast either side runs.
    """

    import os
    import time

    from app.core.clearing.flow_planner import plan_clearing

    with open(started_path, "w"):
        pass
    gives_up_at = time.monotonic() + _HELD_PLAN_GIVES_UP_AFTER_SECONDS
    while not os.path.exists(release_path):
        if time.monotonic() > gives_up_at:
            raise TimeoutError("p035: the test never released the held plan")
        time.sleep(0.01)
    return _rotated(plan_clearing(edges))
