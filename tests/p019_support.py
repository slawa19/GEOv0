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
