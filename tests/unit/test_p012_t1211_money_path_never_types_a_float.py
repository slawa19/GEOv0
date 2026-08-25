"""012 - the guard the Verification plan promised and the wave never built.

``## Verification plan`` item 2 lists, among the invariants that must survive, "no money path
accepts a ``float``", with the counter-check stated outright: *a ``float`` annotation on a money
parameter must be caught by a GUARD, not by review*.  The wave removed the one money-to-float
conversion it found (``_bind_decimal``, which existed only to hand ``Decimal("0.01")`` to the
SQLite driver as ``float(val)``) BY HAND, and never built the guard.  Found while auditing the
plan against reality before closing, and built here rather than recorded as unmet: a promise of a
guard is worth nothing until the guard exists, and this one is cheap.

WHY A FLOAT MATTERS HERE.  A binary double holds about 15-16 significant decimal digits; the
ledger column is ``Numeric(20, 8)``.  So the moment an amount passes through a ``float``, two
amounts the ledger stores separately can become one value, and nothing downstream can tell them
apart again.  ``AGENTS.md`` section 8 states it as an invariant; this module is the part that
notices when it stops being true.

WHAT IS ALLOWED, AND WHY IT IS NOT A HOLE.  Floats are legitimate on these modules for TIME -
timeouts and deadlines are wall-clock seconds, not ledger values, and ``asyncio`` takes them as
floats.  So time is admitted by an explicit allowlist, and the allowlist is asserted to be
EXHAUSTED: an entry that stops matching anything fails the test rather than sitting there
silently widening the guard.  That failure mode - an allowlist checked by key rather than by what
actually arrived - is one this repository has already shipped once.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The money path.  `payments`, `clearing`, `trustlines` and `balance` are the four modules 012
#: names as the money core; `money.py` renders amounts and `validation.py` is the door.
MONEY_MODULES = [
    "app/core/payments",
    "app/core/clearing",
    "app/core/trustlines",
    "app/core/balance",
    "app/utils/money.py",
    "app/utils/validation.py",
]

#: Time is not money.  Each entry must match at least one real site (asserted below), so this
#: list cannot rot into a blanket permission.
ALLOWED_TIME_TOKENS = ("timeout", "deadline", "seconds")


def _iter_money_files():
    for entry in MONEY_MODULES:
        path = REPO_ROOT / entry
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        else:
            yield path


def _is_time(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ALLOWED_TIME_TOKENS)


def _float_sites():
    """Every place the money path types or constructs a `float`, with why it was allowed."""

    typed, constructed, declared = [], [], []
    for file in _iter_money_files():
        relative = file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
                    if arg.annotation and "float" in ast.unparse(arg.annotation):
                        typed.append((relative, node.lineno, f"{node.name}({arg.arg})", arg.arg))
                if node.returns and "float" in ast.unparse(node.returns):
                    typed.append((relative, node.lineno, f"{node.name} -> ", node.name))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                constructed.append((relative, node.lineno, ast.unparse(node), ast.unparse(node)))
            # Annotated assignments - module attributes, instance attributes, dataclass and class
            # fields.  Added after internal review of the first version pointed out that the set of
            # places a `float` can be TYPED is wider than the set this guard sampled: it read
            # function signatures only, so a money field typed `float` on a class inside these six
            # modules would have gone unseen.  That is the wave's own lesson - the sample was drawn
            # from implementations, not values - applied to the guard itself.
            if isinstance(node, ast.AnnAssign) and node.annotation and "float" in ast.unparse(node.annotation):
                declared.append((relative, node.lineno, ast.unparse(node.target), ast.unparse(node.annotation)))
    return typed, constructed, declared


TYPED, CONSTRUCTED, DECLARED = _float_sites()

#: Annotated `float` attributes that are clocks rather than ledger values, each named with its
#: reason.  A token-matching allowlist is too weak here: two of these three are caches whose float
#: is a timestamp, and their NAMES say "cache", not "time", so any rule loose enough to admit them
#: by name would also admit a cache of amounts.  So each site is acknowledged individually - and
#: the list is asserted EXHAUSTED, so an entry that stops matching fails rather than lingering.
KNOWN_NON_MONEY_FLOAT_ATTRIBUTES = {
    ("app/core/payments/engine.py", "self._advisory_lock_deadline"):
        "an advisory-lock deadline in monotonic seconds",
    ("app/core/payments/router.py", "_graph_cache"):
        "the cache tuple's first slot is a monotonic timestamp; the amounts in it are Decimal",
    ("app/core/balance/service.py", "_summary_cache"):
        "same shape: a monotonic timestamp beside a summary whose money fields are strings",
}


def test_the_scan_actually_reached_the_money_path() -> None:
    """Without this, a moved module or a bad path makes every assertion below vacuous."""

    files = list(_iter_money_files())
    assert len(files) >= 10, (
        f"Only {len(files)} files scanned under {MONEY_MODULES}. The money path did not move on "
        f"its own - fix the list before trusting a green run here."
    )
    assert any(f.name == "money.py" for f in files)
    assert any(f.name == "validation.py" for f in files)


@pytest.mark.parametrize(
    "site", TYPED, ids=[f"{s[0]}:{s[1]}:{s[2]}" for s in TYPED] or None
)
def test_no_money_value_is_typed_as_a_float(site) -> None:
    relative, line, where, subject = site
    assert _is_time(subject), (
        f"{relative}:{line} types `{where}` as a float. A binary double holds ~15-16 significant "
        f"decimal digits and the ledger column is Numeric(20, 8), so an amount that passes "
        f"through here can come out as a different amount, and nothing downstream can tell. If "
        f"this really is a clock value and not a ledger value, name it so - the allowlist admits "
        f"{ALLOWED_TIME_TOKENS}."
    )


@pytest.mark.parametrize(
    "site", CONSTRUCTED, ids=[f"{s[0]}:{s[1]}" for s in CONSTRUCTED] or None
)
def test_no_money_value_is_converted_to_a_float(site) -> None:
    relative, line, where, subject = site
    assert _is_time(subject), (
        f"{relative}:{line} constructs a float: `{where}`. The wave removed exactly one of these "
        f"(`_bind_decimal`, which handed Decimal(\"0.01\") to the SQLite driver as float(val)) and "
        f"it was enough to make the fast path and the fallback disagree. Convert through `str()` "
        f"and `Decimal`, or, if this is a clock value, name it so."
    )


@pytest.mark.parametrize(
    "site", DECLARED, ids=[f"{s[0]}:{s[1]}:{s[2]}" for s in DECLARED] or None
)
def test_no_money_attribute_is_declared_as_a_float(site) -> None:
    relative, line, target, annotation = site
    reason = KNOWN_NON_MONEY_FLOAT_ATTRIBUTES.get((relative, target))
    assert reason is not None, (
        f"{relative}:{line} declares `{target}: {annotation}`. If this holds a ledger value, a "
        f"binary double cannot: the column is Numeric(20, 8) and a double carries ~15-16 "
        f"significant decimal digits. If it is a clock or a cache timestamp, add it to "
        f"KNOWN_NON_MONEY_FLOAT_ATTRIBUTES with the reason - the point is that somebody decided, "
        f"not that the name looked harmless."
    )


def test_every_acknowledged_float_attribute_still_exists() -> None:
    """An acknowledgement that matches nothing is a permission waiting to be reused."""

    present = {(site[0], site[2]) for site in DECLARED}
    stale = sorted(set(KNOWN_NON_MONEY_FLOAT_ATTRIBUTES) - present)
    assert not stale, (
        f"These acknowledged float attributes no longer exist: {stale}. Remove them - an entry "
        f"kept past its site will one day be read as covering a different site with the same name."
    )


def test_every_allowlisted_reason_is_actually_used() -> None:
    """An allowlist entry that matches nothing is a blanket permission waiting to be used.

    The failure mode is not hypothetical here: this repository has shipped an allowlist checked by
    key rather than by what arrived. So each token must earn its place on every run.
    """

    subjects = [site[3] for site in TYPED] + [site[3] for site in CONSTRUCTED]
    unused = [
        token for token in ALLOWED_TIME_TOKENS
        if not any(token in subject.lower() for subject in subjects)
    ]
    assert not unused, (
        f"These allowlist tokens no longer match any site: {unused}. Remove them - a token that "
        f"admits nothing today will admit the wrong thing tomorrow."
    )
