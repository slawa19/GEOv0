"""Programme 019 stage 5, `T1909` precondition 1: a concurrent insert of the SAME new debt row is a retry.

027 STAGE 2 (`T2704`): the six schedules are REMOVED - their competitor inserts the row without the pair's line
locks, which no application writer may do any more, and two protocol writers of one pair queue instead of
colliding (spec 027, Changelog `T2704`, §11 evidence). The classifier check below stays. The text is history.

THE FINDING (`T1908`, 2026-09-25). Without the pair locks two writers can both read "no debt X->Y" and
both insert it; the loser's insert meets the winner's committed row on `uq_debts_debtor_creditor_equivalent`
and PostgreSQL answers `23505`, not `40001`. The classifiers retried only 40001/40P01, so the loser ended as
an internal error (`E010`) - and, after admission, as a stored definitive `ABORTED` - for what is a
transient conflict a fresh snapshot cures (the fresh attempt reads the row and updates it).

THE DECISION (fourth consultation, precondition 1): retry `23505` ONLY on that constraint, read from the
structured constraint name through the deliberate exception chain, in all three owners - the API payment
(`PaymentService.pay`), the simulator money phase (`money_replay`, through the staged payment), the inject
(`_apply_inject_unit_of_work`). Every other `23505` stays what it was; the `tx_id` identity resolver stays
separate.

THE SCHEDULE, real and independent of the advisory locks: the writer under test is PARKED after it has read
the pair (its SERIALIZABLE snapshot is taken) and before it writes; a competitor transaction then BLIND-inserts
the same debt row - no read first, so SSI has no read-write cycle to report and the insert is left to the
unique index - and commits; the writer is released and inserts. The competitor declares its write
(`debt_fixture_setup`), as every writer of `debts` must.

CONTROL BEFORE TARGET (`tests/p019_support.py`): every test first asserts, with plain assertions, that the
competitor committed while the writer was parked and that the writer really met `23505` on exactly that
constraint (recorded where the payment names its refusal, or where the inject classifies its error); only then
is the outcome compared with the target, and a mismatch is `TargetMismatch`.
"""

from __future__ import annotations

from decimal import Decimal


from app.core.payments import service as payment_service_module
from app.core.simulator import real_runner_impl
from app.utils.exceptions import RetryablePaymentConflictException
from tests.p019_support import require_target

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

DEBT_PAIR = "uq_debts_debtor_creditor_equivalent"
COMPETITOR = Decimal("5.00")


# ── the API payment (`pay()` owns the retry) ────────────────────────────────────────────────────


# ── the simulator money phase (the staged payment propagates, the phase replays) ─────────────────


# ── the inject (one transient retry, then the event stays pending) ──────────────────────────────


# ── the counter-check: another 23505 is NOT retried (anti-vacuum, AGENTS.md §9) ─────────────────


def test_only_the_debt_pair_constraint_is_a_retryable_collision() -> None:
    """Built on the real driver's error type: the same SQLSTATE on any other constraint stays terminal."""

    from asyncpg.exceptions import UniqueViolationError
    from sqlalchemy.exc import IntegrityError

    def integrity(constraint: str | None) -> IntegrityError:
        driver = UniqueViolationError("duplicate key value violates unique constraint")
        driver.sqlstate = "23505"
        driver.constraint_name = constraint
        return IntegrityError("INSERT ...", {}, driver)

    classify = payment_service_module._classify_payment_db_error
    for other in ("transactions_tx_id_key", "uq_debt_operations_tx_id", "uq_prepare_locks_tx_participant", None):
        assert not isinstance(classify(integrity(other)), RetryablePaymentConflictException), other
        assert not real_runner_impl._is_transient_inject_db_error(integrity(other)), other
    require_target(
        isinstance(classify(integrity(DEBT_PAIR)), RetryablePaymentConflictException)
        and real_runner_impl._is_transient_inject_db_error(integrity(DEBT_PAIR)),
        "the debt-pair collision is not classified as a retryable conflict",
    )
