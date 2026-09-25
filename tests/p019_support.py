"""The one expected-failure shape of programme 019 (spec, Verification plan §1). NOT a test module.

WHY A DEDICATED EXCEPTION. A target test of 019 first asserts, with ordinary assertions, that its
mechanism was reached and that the operation completed: the barrier was hit, the observer's snapshot
is fresh, the conflict really was the SQLSTATE it claims, the payment ended. Only AFTER all of that
does it compare the observed outcome with the target, and a mismatch there - and only there - is
raised as `TargetMismatch`. The marker is `xfail(raises=TargetMismatch, strict=True)`:

* a broken stand raises `AssertionError` (or anything else), which `raises=` does NOT accept, so the
  test goes red instead of being filed as an expected failure. `raises=AssertionError` would accept a
  broken barrier as the expected mismatch, which is exactly the false green the spec forbids;
* a tree that already behaves as the target passes the comparison, and `strict=True` turns that
  XPASS into a failure - the stage that fixes the behaviour must take the marker off, it cannot be
  forgotten.

`TargetMismatch` deliberately does not inherit from `AssertionError`, so no plain `assert` anywhere can
ever be mistaken for it.
"""

from __future__ import annotations

import pytest


class TargetMismatch(Exception):
    """The observed outcome differs from the 019 target. Raised only after every control passed."""


def target_xfail(stage: str, what: str):
    """The 019 marker: an expected `TargetMismatch`, strict, naming the stage that removes it."""

    return pytest.mark.xfail(
        raises=TargetMismatch,
        strict=True,
        reason=f"019 target, fixed by {stage}: {what}",
    )


def require_target(condition: bool, message: str) -> None:
    """The final comparison of a target test. Call it LAST, after every control assertion."""

    if not condition:
        raise TargetMismatch(message)


def allow_below_serializable_for_a_diagnostic(monkeypatch) -> list[str]:
    """A NAMED DIAGNOSTIC CONTROL below the supported isolation (019 stage 5, `T1907`, item 7).

    Since `T1907` every money writer refuses a transaction that does not run SERIALIZABLE
    (`MoneyBoundary.require_serializable`). A few stands run a writer at READ COMMITTED ON PURPOSE - to
    reach a path that exists only there (a `StaleDataError` instead of 40001, a re-check refusal after the
    row insert) or to show that a verifier DOES detect the disagreement such a level allows (the positive
    control of criterion (b)). They switch the check off for their own test only, through this function,
    so every such stand is findable by one name and none of them is evidence that the application runs
    below SERIALIZABLE; the production refusal is `test_p019_money_writers_refuse_non_serializable_postgres.py`.
    Returns the writers whose check was skipped, so a stand can show it was on its path.
    """

    from app.core.money_boundary import MoneyBoundary

    skipped: list[str] = []

    async def skip(session, *, writer: str) -> None:
        skipped.append(writer)

    monkeypatch.setattr(MoneyBoundary, "require_serializable", staticmethod(skip))
    return skipped
