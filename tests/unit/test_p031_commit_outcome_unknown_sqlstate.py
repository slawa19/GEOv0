"""031 (BACKLOG item 16): `40003 statement_completion_unknown` is NOT a server refusal of a COMMIT.

PostgreSQL's catalogue puts `40003` in class `40` (transaction rollback), but its meaning is "the outcome of the
statement is unknown". A COMMIT that failed with it may have landed, so treating it like `40001`/`40P01` ("the
server rolled the transaction back, nothing is stored") would terminalize or replay an attempt whose effects may
be persisted. The classifier is shared by the payment owner and the simulator money phase.

REACHABILITY, stated rather than implied: no source of `40003` after a COMMIT was found in the current stack
(asyncpg + PostgreSQL 16). This is a correctness-of-classification fix, so the test is on the classifier; there
is no stand that makes a real server emit the code.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.payments import service as payment_service
from app.core.simulator.money_replay import _commit_refused


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"fake driver error sqlstate={sqlstate}")
        self.sqlstate = sqlstate


def _commit_error(code: str) -> DBAPIError:
    return DBAPIError("COMMIT", None, _Orig(code))


@pytest.mark.parametrize("code", ["40001", "40P01", "40002", "23505", "23503", "P0001"])
def test_server_refusals_stay_refusals(code: str) -> None:
    # Counter-check (anti-vacuum): the exclusion is exactly one code, the rest of class 40 and the other
    # refusing classes are still refusals in both places.
    assert payment_service.commit_refused_by_server(code) is True
    assert _commit_refused(_commit_error(code)) is True


def test_statement_completion_unknown_is_not_a_refusal_for_payments() -> None:
    assert payment_service.commit_refused_by_server("40003") is False


def test_statement_completion_unknown_is_not_a_refusal_for_the_simulator_money_phase() -> None:
    assert _commit_refused(_commit_error("40003")) is False


@pytest.mark.parametrize("code", [None, "08006", "57014", "XX000"])
def test_unknown_outcomes_stay_unknown(code: str | None) -> None:
    assert payment_service.commit_refused_by_server(code) is False
