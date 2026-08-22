"""The run perimeter, as the tick money paths need it."""

from __future__ import annotations


def run_perimeter_pids(run) -> "set[str] | None":
    """The run's participants as pids, or None when the run has not been seeded yet.

    2026-08-22 / p010 (`F-010-4`).  The money paths of the tick were unbounded: clearing
    searched the whole equivalent and payments routed through it, so a run could reduce the
    obligations of another run's participants or consume their trust.  That is the same P1
    the Interact Mode routes had (`F-010-3`), on the path that runs by itself.

    `None` means the perimeter is not being applied, and it is returned only before seeding,
    where the run has no participants to speak of and its money paths have nothing to do
    either.  An EMPTY set would mean nobody, which is not the same thing.
    """

    participants = getattr(run, "_real_participants", None)
    if not participants:
        return None
    return {str(pid) for (_uuid, pid) in participants}
