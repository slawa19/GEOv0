"""Programme 019 stage 3, `FORK-1`: the book's debt-version conflict is a retryable conflict - and only it.

`app/core/ledger/book.py::_apply_payment_flow` no longer retries a `StaleDataError` from the same
snapshot; it raises `DebtVersionConflict`, and the two owners of retries classify THAT type as a
retryable conflict: `PaymentService.pay` through `_classify_payment_db_error`, the simulator's money
phase through `money_conflict_name`.

ANTI-VACUUM (AGENTS.md §9). Both predicates now admit one more type, so each owes the counter-check
that nothing else rides in with it: a bare `StaleDataError` (the same ORM class, raised by anything
but a payment flow), the ORM's other errors and an `IntegrityError` are NOT retryable. A predicate that
widened to `StaleDataError` would pass the positive half of this module and fail the negative half.

The behaviour through a real stale version - the book raises once, does not retry, and `pay()` retries
the whole attempt on a fresh session - is `tests/unit/test_apply_flow_retry_on_stale.py` and
`tests/integration/test_p019_pay_retries_a_debt_version_conflict_postgres.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm.exc import NoResultFound, StaleDataError

from app.core.ledger.book import DebtVersionConflict
from app.core.payments.service import _classify_payment_db_error
from app.core.simulator.money_replay import money_conflict_name
from app.utils.exceptions import GeoException, RetryablePaymentConflictException


def _conflict() -> DebtVersionConflict:
    try:
        try:
            raise StaleDataError("UPDATE statement on table 'debts' expected to update 1 row(s)")
        except StaleDataError as stale:
            raise DebtVersionConflict("a debt of the pair changed") from stale
    except DebtVersionConflict as conflict:
        return conflict


def _not_retryable() -> list[BaseException]:
    return [
        StaleDataError("a stale row that is not a payment flow's"),
        InvalidRequestError("an ORM misuse"),
        NoResultFound("no row"),
        IntegrityError("INSERT INTO debts", {}, Exception("23505 not a declared race")),
        RuntimeError("anything else"),
    ]


def test_the_conflict_is_still_the_original_orm_failure() -> None:
    conflict = _conflict()
    assert isinstance(conflict, StaleDataError)
    assert isinstance(conflict.__cause__, StaleDataError)


def test_the_payment_classifier_retries_the_book_conflict() -> None:
    assert isinstance(_classify_payment_db_error(_conflict()), RetryablePaymentConflictException)
    # Found through deliberate wrapping too, the way the service re-raises a classified failure.
    try:
        try:
            raise _conflict()
        except DebtVersionConflict as inner:
            raise GeoException() from inner
    except GeoException as wrapped:
        assert isinstance(_classify_payment_db_error(wrapped), RetryablePaymentConflictException)


@pytest.mark.parametrize("error", _not_retryable(), ids=lambda e: type(e).__name__)
def test_the_payment_classifier_retries_nothing_else(error: BaseException) -> None:
    classified = _classify_payment_db_error(error)
    assert not isinstance(classified, RetryablePaymentConflictException), (error, classified)


def test_the_money_phase_classifier_retries_the_book_conflict() -> None:
    assert money_conflict_name(_conflict()) == "DEBT_VERSION_CONFLICT"


@pytest.mark.parametrize("error", _not_retryable(), ids=lambda e: type(e).__name__)
def test_the_money_phase_classifier_retries_nothing_else(error: BaseException) -> None:
    assert money_conflict_name(error) is None, error
