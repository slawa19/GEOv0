"""Every replay path in the clearing service must carry the run perimeter.

Program 010, `F-010-3`.

The perimeter is a two-layer defence: detection never produces a foreign cycle, and execution
refuses one that arrives by any other route.  The execution layer has a third door - the
replay shortcut, which answers an already-committed execution from the recorded transaction
and returns before the locked re-read.  `_read_committed_execution_amount` skips its
ownership check when the perimeter is None, so a single forgotten `allowed_participant_pids=`
on any of those calls hands a scoped caller another run's durable result as its own.

This is not hypothetical and it is not somebody else's mistake: the first version of the fix
claimed in its own commit message to have threaded the perimeter through every transition,
and had missed three of them.  A prose claim about complete coverage is worth what this test
is worth.

Structural rather than behavioural on purpose.  Each of these paths needs a separate
PostgreSQL race to reach - a serialisable conflict at a precise moment - and building five
such stands to guard against a forgotten keyword is the wrong trade.  What can go wrong here
is exactly one thing, and it is visible in the source.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SERVICE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app"
    / "core"
    / "clearing"
    / "service.py"
)

# The replay resolvers: everything that can return a durable amount for a cycle without
# going through the locked re-read that the authoritative perimeter check stands on.
_REPLAY_CALLS = {
    "_reconcile_committed_execution",
    "_committed_execution_amount",
    "_read_committed_execution_amount",
}


def _calls_to_replay_resolvers():
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None)
        if name in _REPLAY_CALLS:
            yield name, node


def test_every_replay_resolver_call_passes_the_perimeter() -> None:
    missing = []
    total = 0
    for name, call in _calls_to_replay_resolvers():
        total += 1
        if not any(kw.arg == "allowed_participant_pids" for kw in call.keywords):
            missing.append(f"{name} at line {call.lineno}")

    assert total >= 5, (
        f"only {total} replay resolver calls found - the set of names this test guards is "
        "out of date with the code, which makes it worthless"
    )
    assert not missing, (
        "these replay paths return a durable amount without checking whose cycle it was, "
        "so a scoped caller can be handed another run's result as its own: "
        + ", ".join(missing)
    )


def test_the_resolver_refuses_rather_than_ignores_an_unverifiable_payload() -> None:
    """The check must not degrade to "no edges recorded, so nothing to object to"."""

    source = _SERVICE.read_text(encoding="utf-8")
    assert "clearing.replay_scope_unverifiable" in source, (
        "a recorded transaction whose edges cannot be read must be refused, not trusted"
    )
