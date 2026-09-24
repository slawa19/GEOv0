"""Every `async with debt_fixture_setup(...)` block in this repository, checked.

WHY THE GUARD NEEDS A CARRIER (programme 015, phase B step 4, design v2 §7, `C21`). Slice B built
`tests/debt_setup.fixture_block_violations` and `C21` proves it recognises a violation when it is
handed one. Neither runs it over the tree. A rule with no repo-wide carrier is a rule that holds
for the two strings its own test passes it, and slice B could not add this file without disturbing
the exact-count evidence that its migration changed nothing - so it recorded the gap and left it
here.

WHAT THE RULE IS. The body of a fixture block may contain only model construction,
`session.add`/`add_all`/`delete`, assignment to a local or to an attribute of a local, and
`await session.flush()`. Anything else - a loop, a branch, a call into application code, an
assignment to a subscript - is a violation.

WHY IT MATTERS, and it is not tidiness. `debt_fixture_setup` opens a `TEST_FIXTURE` operation, and
every debt written inside it is journalled as that operation's own effect. Application code running
inside such a block - `_apply_flow`, `stage_inject_event`, a helper that reaches either - opens no
operation of its own, so the journal has nothing to refuse and silently records a payment's
movements as fixture setup. The runtime half of `C21` (the journal's refusal to nest operations)
catches only code that DOES open its own operation, which is the opposite case. Neither half covers
the other's blind spot, and this is the only one that can see this one.

WHAT THIS GUARD DOES NOT SEE, so its silence is not mistaken for more than it is:

* it reads text. A fixture block that calls a local helper which in turn drives a writer is
  invisible here - the call to the helper is itself reported only if the helper's name does not look
  like a constructor, which is a heuristic and is documented as one in `tests/debt_setup.py`;
* it finds blocks by the NAME `debt_fixture_setup`, at the call site. A block entered through an
  alias, a variable, or `contextlib.AsyncExitStack` is not matched;
* it says nothing about what the block's rows MEAN. A perfectly legal fixture block can still set up
  a world no test needs.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from tests.debt_setup import fixture_block_violations

_ROOT = Path(__file__).resolve().parents[2]
_TESTS = _ROOT / "tests"


def _sources() -> list[Path]:
    return sorted(path for path in _TESTS.rglob("*.py") if "__pycache__" not in path.parts)


def test_every_fixture_block_in_the_tree_contains_only_fixture_setup() -> None:
    """The rule, over `tests/**`. This is the carrier design v2 §7 asks for."""

    findings: list[str] = []
    scanned = 0
    for path in _sources():
        scanned += 1
        relative = path.relative_to(_ROOT).as_posix()
        findings.extend(
            str(violation)
            for violation in fixture_block_violations(
                path.read_text(encoding="utf-8"), path=relative
            )
        )

    # ANTI-VACUUM: the scan really read the tree. Without this a broken glob, a renamed directory
    # or an `rglob` that returned nothing would report a clean repository.
    assert scanned > 100, (
        f"this guard scanned {scanned} file(s) under {_TESTS}, which cannot be the whole test "
        f"suite; it measured almost nothing and its silence means nothing"
    )

    assert not findings, (
        "these `async with debt_fixture_setup(...)` blocks contain something that is not fixture "
        "setup:\n  " + "\n  ".join(findings) + "\n\n"
        "A fixture block declares that the debts written inside it are setup, and the journal "
        "records them under `TEST_FIXTURE`. Application code running inside one opens no operation "
        "of its own, so its movements are journalled as the fixture's and nothing at runtime can "
        "tell the difference. Move the statement out of the block: build values before it, and "
        "drive writers after it."
    )


def test_the_tree_really_uses_the_context_this_guard_is_about() -> None:
    """ANTI-VACUUM for the guard's SUBJECT, not just for its scan.

    The assertion above is satisfied by a tree with no fixture blocks at all, which is exactly what
    a rename or a mass revert would produce - and it would read as "every block is clean". The test
    suite writes debts in well over a hundred places and every one of them must be declared, so a
    count that collapses is a defect and not a cleanup.
    """

    blocks = 0
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncWith, ast.With)):
                continue
            for item in node.items:
                expression = item.context_expr
                if not isinstance(expression, ast.Call):
                    continue
                function = expression.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else getattr(function, "attr", None)
                )
                if name == "debt_fixture_setup":
                    blocks += 1

    assert blocks >= 100, (
        f"only {blocks} `async with debt_fixture_setup(...)` block(s) remain under tests/. Slice B "
        f"migrated over a hundred debt-setup sites onto that context and the journal refuses a "
        f"Debt written outside one, so a count this low means either the migration was reverted - "
        f"in which case the default tier is about to go red - or the context was renamed and the "
        f"guard above is now checking nothing."
    )


# ==============================================================================================
# C21 - the guard itself, on three hand-written blocks. Moved here VERBATIM from
# `tests/unit/test_p015_b4_write_guard.py` when 018 stage B1 deleted that module (manifest `T1808`
# §5, row `:1403`): it depends only on `tests.debt_setup.fixture_block_violations`.
# ==============================================================================================


_FIXTURE_BLOCK_CALLING_APPLICATION_CODE = textwrap.dedent(
    """
    async def test_something(db_session):
        async with debt_fixture_setup(session, label="seed") as op:
            session.add(Debt(debtor_id=a, creditor_id=b, equivalent_id=e, amount=amount))
            await PaymentEngine(session).commit(tx_id)
            await session.flush()
    """
)

_FIXTURE_BLOCK_HIDING_THE_CALL_IN_AN_EXPRESSION = textwrap.dedent(
    """
    async def test_something(db_session):
        async with debt_fixture_setup(session, label="seed") as op:
            debt.amount = (await PaymentEngine(session).quote(tx_id)).amount
    """
)

_FIXTURE_BLOCK_THAT_IS_ALLOWED = textwrap.dedent(
    """
    async def test_something(db_session):
        async with debt_fixture_setup(session, label="seed") as op:
            debt = Debt(debtor_id=a, creditor_id=b, equivalent_id=e, amount=amount)
            session.add(debt)
            debt.amount = amount
            await session.flush()
    """
)


@pytest.mark.parametrize(
    "source,expected_rejected",
    [
        (_FIXTURE_BLOCK_CALLING_APPLICATION_CODE, True),
        (_FIXTURE_BLOCK_HIDING_THE_CALL_IN_AN_EXPRESSION, True),
        (_FIXTURE_BLOCK_THAT_IS_ALLOWED, False),
    ],
    ids=["statement call", "call hidden in an expression", "allowed block"],
)
def test_c21_the_fixture_block_guard_rejects_application_calls(source, expected_rejected) -> None:
    """C21. The AST guard over `async with debt_fixture_setup` blocks.

    WHY A STATIC GUARD AND NOT ONLY A RUNTIME ONE. The test-local operation kind exists so tests can
    put debts in place without pretending to be a payment. Its contract is that the block contains
    only model construction, `session.add/add_all/delete`, attribute assignment and `flush` - if
    application code runs inside it, the fixture's envelope claims authorship of effects the
    application produced. The runtime nesting refusal (`Book`, contract item 2) catches only
    application code that opens an operation OF ITS OWN; `_apply_flow` under a fixture context is
    caught by nothing else (design v2 §7).

    THE SECOND CASE is binding condition 7: the whitelist must be recursive over EXPRESSIONS. A
    statement-level whitelist that allows "attribute assignment on a local" does not by itself
    forbid an application call on the right-hand side.

    MUTATION: make the whitelist statement-level only; the second case must go green when it should
    be red.
    """
    violations = fixture_block_violations(source)
    if expected_rejected:
        assert violations, (
            f"the guard accepted a fixture block that calls application code:\n{source}"
        )
    else:
        assert not violations, (
            f"the guard rejected a fixture block that only builds and flushes models: {violations}"
            f"\n{source}"
        )
