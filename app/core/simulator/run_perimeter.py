"""The run perimeter, as the tick money paths need it."""

from __future__ import annotations


def run_perimeter_pids(run) -> "set[str] | None":
    """The run's participants as pids, or None when the run has not been seeded yet.

    2026-08-22 / p010 (`F-010-4`).  The money paths of the tick were unbounded: clearing
    searched the whole equivalent and payments routed through it, so a run could reduce the
    obligations of another run's participants or consume their trust.  That is the same P1
    the Interact Mode routes had (`F-010-3`), on the path that runs by itself.

    Three states, and the difference between the last two is the whole point:

    * `None` — the participant list has not been LOADED yet, so no perimeter can be applied.
      The tick loads it before reaching any money path (`RealTick._open_money_phase` in
      `app/core/simulator/tick.py`, after seeding), so this state is not reachable from
      clearing or payments.
    * an EMPTY set — the list was loaded and the run has nobody in it.  That is a perimeter
      admitting nobody, NOT an absence of one.  Collapsing it into `None` would mean a run
      with an empty scenario clears and routes across the whole equivalent, which is the
      shape of `F-009-1` all over again.
    * a non-empty set — the run.
    """

    participants = getattr(run, "_real_participants", None)
    if participants is None:
        return None
    return {str(pid) for (_uuid, pid) in participants}
