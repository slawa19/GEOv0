from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.payments.service import _classify_payment_db_error
from app.utils.exceptions import GeoException, RetryablePaymentConflictException


class _DriverError(Exception):
    pass


def _db_error(*, attribute: str, value: str) -> DBAPIError:
    original = _DriverError("database failure")
    setattr(original, attribute, value)
    return DBAPIError(
        statement="SELECT 1",
        params=None,
        orig=original,
        connection_invalidated=False,
    )


@pytest.mark.parametrize("attribute", ["sqlstate", "pgcode", "code"])
@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
def test_retryable_database_conflicts_use_existing_sanitized_contract(
    attribute: str, sqlstate: str
):
    classified = _classify_payment_db_error(
        _db_error(attribute=attribute, value=sqlstate)
    )

    assert isinstance(classified, RetryablePaymentConflictException)
    assert classified.status_code == 409
    assert classified.code == "E008"
    assert classified.details == {
        "retryable": True,
        "conflict_kind": "database_concurrency",
    }
    assert sqlstate not in str(classified.to_dict())


def test_retryable_sqlstate_is_found_through_exception_cause_chain():
    database_error = _db_error(attribute="sqlstate", value="40001")
    try:
        raise database_error
    except DBAPIError as cause:
        wrapper = RuntimeError("service wrapper")
        wrapper.__cause__ = cause

    assert isinstance(
        _classify_payment_db_error(wrapper),
        RetryablePaymentConflictException,
    )


@pytest.mark.parametrize("sqlstate", ["25P02", "23505", "08006"])
def test_nonretryable_database_errors_remain_internal(sqlstate: str):
    classified = _classify_payment_db_error(
        _db_error(attribute="sqlstate", value=sqlstate)
    )

    assert type(classified) is GeoException
    assert classified.status_code == 500
    assert classified.code == "E010"
    assert classified.details == {}
