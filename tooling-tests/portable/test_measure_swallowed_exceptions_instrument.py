"""The instrument of program 010 must not over-report.

`T1004` compares a "before" and an "after" measurement, so the script that produces those
numbers is load-bearing evidence, not a convenience.  Three defects in it were already found
by external review when the program was written; this pins the fourth, found while
re-verifying the spec against HEAD.

`direct_txn_calls` promises "rollback()/commit() in the IMMEDIATE body of the try, without
nested try and handlers".  Its `continue` on an `ast.Try` node skips only that node - the
generator has already pushed its children onto the stack, so the nested try's body AND its
handlers are still walked.  A `rollback()` that sits inside a nested try with its own
re-raising handler was therefore attributed to the outer swallowing one, which inflates the
`rollback_swallow` count.

The shape is not hypothetical: `app/core/simulator/storage.py` grew exactly this structure
when program 009 gave `write_tick_bottlenecks` its savepoint.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "specs"
    / "010-money-core-fail-closed"
    / "measure_swallowed_exceptions.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("measure_swallowed_exceptions", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_NESTED = '''
async def write(session):
    try:
        session.add_all([])
        try:
            await session.rollback()
        except Exception:
            raise
    except Exception:
        pass
'''

_DIRECT = '''
async def write(session):
    try:
        await session.rollback()
    except Exception:
        pass
'''


@pytest.mark.parametrize(
    ("source", "expected", "why"),
    [
        (_DIRECT, 1, "a rollback in the immediate body of a swallowing try is the subject"),
        (
            _NESTED,
            0,
            "a rollback inside a nested try whose own handler re-raises is not swallowed by "
            "the outer one, and counting it inflates the measurement the program reports",
        ),
    ],
    ids=["direct", "nested"],
)
def test_only_immediate_transaction_calls_are_counted(source: str, expected: int, why: str) -> None:
    module = _load()
    tree = ast.parse(source)
    tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    outer = tries[0]

    calls = module.direct_txn_calls(outer)

    assert len(calls) == expected, f"{why}; got {[c.lineno for c in calls]}"


def test_the_docstring_and_the_code_agree() -> None:
    """The promise in the docstring is the contract this test holds it to."""

    module = _load()
    assert "без вложенных try" in (module.direct_txn_calls.__doc__ or "")
