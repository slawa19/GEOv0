"""T1543: a legally frozen trust line is compared with its stored limit, not with zero.

028 `F-028-29` (owner В-2, 2026-10-04): the line status `frozen` is gone (migration 035), and with it the four
tests of a frozen line. What remains is the rule's other half: `active` against its stored limit; `closed` or no
live line against zero. The text below is the T1543 history.

THE DEFECT. `check_trust_limits` outer-joined a debt only to an `active` trust line and substituted
a limit of zero when none matched, so a debt on a FROZEN line counted as exceeding a limit of zero.
Three observable consequences followed, and each has a test below that was red before the fix:

* the checkpoint of any equivalent holding such a line was `critical`;
* every payment in that equivalent wrote an audit row with `verification_passed=false` (since 024
  `T2413.2` a payment's audit row runs no check, so its test was removed with that contract);
* a payment that partly repaid the debt on a frozen line was ABORTED as a trust-limit violation,
  because the commit-time check saw the remaining debt against a limit of zero.

THE RULE, decided by the Codex plan review of 2026-09-13 against `docs/ru/02-protocol-spec.md`
§3.3 (`∀ (from, to, equivalent): debt[to→from] ≤ limit`, with no `active` condition, for statuses
`active | frozen | closed`) and §11.5.2 (whose first reaction to a violation is to freeze the line):

* `active` and `frozen` - the debt is compared with the stored limit;
* `closed`, or no live line at all - the permitted debt is zero.

Freezing means the line offers no NEW routing capacity; that is routing's job
(`app/core/payments/router.py`, `PaymentService._bind_payment`'s segment capacity), not the
invariant's. The controls below keep the rule from sliding into its two wrong neighbours: excluding
frozen lines from the check would hide the very breach §11.5.2 froze the line for, and counting a
closed line at its stored limit would let history authorise debt.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.invariants import InvariantChecker
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException
from tests.debt_setup import debt_fixture_setup


def _equivalent(nonce: str) -> Equivalent:
    return Equivalent(
        code=("Q" + nonce[:15]).upper(),
        symbol="Q",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )


def _participant(label: str, nonce: str) -> Participant:
    return Participant(
        pid=label + nonce,
        display_name=label,
        public_key=f"pk{label}-{nonce}",
        type="person",
        status="active",
        profile={},
    )


async def _line_with_debt(
    db_session,
    *,
    status: str | None,
    limit: str,
    debt: str,
) -> tuple[Equivalent, Participant, Participant]:
    """Creditor trusts debtor on a line of `status` (None: no line at all); debtor owes `debt`."""
    nonce = uuid.uuid4().hex[:10]
    eq = _equivalent(nonce)
    creditor = _participant("C", nonce)
    debtor = _participant("D", nonce)
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()

    if status is not None:
        db_session.add(
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=eq.id,
                limit=Decimal(limit),
                status=status,
            )
        )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal(debt),
            )
        )
    await db_session.commit()
    return eq, creditor, debtor


# --- the defect ------------------------------------------------------------------------------


# --- controls: the rule must not slide into its wrong neighbours ----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None], ids=["no-line"])
async def test_a_debt_without_a_live_line_is_a_violation_against_zero(db_session, status):
    # No live line: the permitted debt is zero, so a debt of 42 is a violation against 0.
    # The closed-line case was removed 2026-09-13 after review: no application path holds debt on a
    # closed line (closing refuses non-zero debt), and a closed line produces the same outer-join
    # miss as a missing one, so it exercised no separate branch of production code.
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status=status, limit="100", debt="42"
    )

    with pytest.raises(IntegrityViolationException) as exc_info:
        await InvariantChecker(db_session).check_trust_limits(equivalent_id=eq.id)

    (violation,) = exc_info.value.details["violations"]
    assert Decimal(violation["trust_limit"]) == Decimal("0")
    assert Decimal(violation["debt_amount"]) == Decimal("42")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("debt", "violates"), [("100", False), ("100.01", True)], ids=["at-limit", "over-limit"]
)
async def test_an_active_line_is_compared_with_its_stored_limit(db_session, debt, violates):
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status="active", limit="100", debt=debt
    )
    checker = InvariantChecker(db_session)

    if not violates:
        assert await checker.check_trust_limits(equivalent_id=eq.id) == []
        return

    # INTENTIONAL, 026 `T2601`: over the stored limit is reported as allowed excess, not raised.
    (entry,) = await checker.check_trust_limits(equivalent_id=eq.id)
    assert Decimal(entry["trust_limit"]) == Decimal("100")
    assert Decimal(entry["excess"]) == Decimal("0.01")
