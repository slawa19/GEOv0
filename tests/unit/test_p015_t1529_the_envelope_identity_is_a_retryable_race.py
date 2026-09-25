"""Programme 015, T1529, after programme 019 stage 4: a `23505` is never retried by its SQLSTATE.

HISTORY. T1529 found that a concurrent duplicate COMMIT of one payment collided on the operation
envelope's identity (`uq_debt_operations_kind_identity` / `uq_debt_operations_tx_id`) and the engine
raised instead of re-reading; the engine's classifier `PaymentEngine._is_retryable_db_error` was widened
to retry exactly that `23505` in its commit phase. Programme 019 removed the phase and then the engine
(stage 4, `T1906`): a payment is one transaction whose FIRST write is the `transactions` row, so two
requests of one `tx_id` collide on `transactions_tx_id_key` before either can open an envelope, and that
collision goes to the EXACT identity resolver (spec, "Идентичность `tx_id`";
`tests/integration/test_p019_staged_tx_id_race_is_a_declared_conflict_postgres.py`,
`tests/integration/test_p015_t1523_in_progress_and_insert_race_postgres.py`). The envelope race is
unreachable on the payment path (manifest `t1901`, 5.1, rows of this file and of
`test_payment_commit_advisory_locks_postgres.py`).

WHAT IS MEASURED HERE, without a database - the truth table of the two surviving owners of retries,
`PaymentService.pay()`'s `_classify_payment_db_error` and the money phase's `money_conflict_name`, plus
the resolver's own recognizer `_is_tx_id_collision`:

* a `23505` on ANY constraint - the envelope identity, the envelope primary key, a look-alike table - is
  NOT a retryable conflict by SQLSTATE (spec §2 counter-check: "`23505` на неименованном ограничении
  остаётся неповторяемым"; spec §4 caps the predicate, it does not oblige a widening);
* THE ONE EXCEPTION, decided 2026-09-25 (019 stage 5, `T1909`, precondition 1 of the fourth
  consultation): a `23505` that is an `IntegrityError` naming `uq_debts_debtor_creditor_equivalent` -
  two writers inserted the same new debt row once the pair locks are gone - IS retryable at both owners,
  by its structured constraint name; the same code on a plain `DBAPIError` or on any other constraint is
  not (reproducers: `tests/integration/test_p019_debt_pair_insert_race_is_retried_postgres.py`);
* the resolver recognizes `transactions_tx_id_key` and nothing else, so the one `23505` the payment
  path does handle cannot be reached through a different constraint;
* positive controls: `40001` and `40P01` ARE retryable at both owners - the table is not vacuously
  "nothing is retryable".

The anti-drift test of the identity set is unchanged: the set is the envelope's declared identity and is
the input of the rows above.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.ledger.book import (
    DEBT_OPERATION_IDENTITY_CONSTRAINTS as _DEBT_OPERATION_IDENTITY_CONSTRAINTS,
)
from app.core.payments.service import (
    TX_ID_UNIQUE_CONSTRAINT,
    _classify_payment_db_error,
    _is_tx_id_collision,
)
from app.core.simulator.money_replay import money_conflict_name
from app.db.journal_tables import debt_operations
from app.utils.exceptions import RetryablePaymentConflictException

ENVELOPE_INSERT = (
    "INSERT INTO debt_operations (id, kind, identity, tx_id, intent, intent_digest, "
    "schema_version, money_encoding_version, intent_encoding_version, opened_at, state) "
    "VALUES ($1::UUID, $2::VARCHAR, $3::VARCHAR, $4::VARCHAR, $5::JSON, $6::VARCHAR, "
    "$7::INTEGER, $8::INTEGER, $9::INTEGER, $10::TIMESTAMP WITH TIME ZONE, $11::VARCHAR)"
)
TRANSACTION_INSERT = "INSERT INTO transactions (id, tx_id, type, initiator_id, payload, state) VALUES ($1, $2, $3, $4, $5, $6)"
DEBT_INSERT = "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) VALUES ($1, $2, $3, $4, $5, $6)"


def _driver_error(sqlstate: str, constraint: str | None = None):
    """A driver exception shaped as asyncpg delivers one: the SQLSTATE and the constraint name."""

    return type(
        "FakeDriverError", (Exception,), {"sqlstate": sqlstate, "constraint_name": constraint}
    )("driver error")


def _wrapped(statement: str, sqlstate: str, constraint: str | None = None, *, cls=DBAPIError):
    return cls(statement=statement, params=None, orig=_driver_error(sqlstate, constraint))


def test_t1529_the_identity_set_is_the_envelope_identity_the_schema_declares() -> None:
    """ANTI-DRIFT, and the reason the constant is a literal rather than a comprehension.

    MUTATION that must redden it: rename either constraint in `app/db/journal_tables.py`, or add a
    `UniqueConstraint` to `debt_operations`, without touching `DEBT_OPERATION_IDENTITY_CONSTRAINTS`.
    """

    declared = {
        constraint.name
        for constraint in debt_operations.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert declared == set(_DEBT_OPERATION_IDENTITY_CONSTRAINTS), (
        f"the envelope identity names {sorted(_DEBT_OPERATION_IDENTITY_CONSTRAINTS)} while "
        f"`debt_operations` declares {sorted(declared)}. Decide which of them means \"this "
        f"operation was already opened\"."
    )


_UNIQUE_VIOLATIONS = [
    *[
        pytest.param(ENVELOPE_INSERT, name, id=f"envelope_identity:{name}")
        for name in sorted(_DEBT_OPERATION_IDENTITY_CONSTRAINTS)
    ],
    pytest.param(ENVELOPE_INSERT, "pk_debt_operations", id="envelope_primary_key"),
    pytest.param(
        "INSERT INTO debt_operations_archive (id, kind, identity) VALUES ($1, $2, $3)",
        "uq_debt_operations_kind_identity",
        id="table_name_extends_the_envelope_table",
    ),
    pytest.param(TRANSACTION_INSERT, TX_ID_UNIQUE_CONSTRAINT, id="transaction_identity"),
]


@pytest.mark.parametrize(("statement", "constraint"), _UNIQUE_VIOLATIONS)
def test_t1529_no_unique_violation_is_a_retryable_conflict_by_its_sqlstate(statement: str, constraint: str) -> None:
    """ANTI-VACUUM for the owners' predicates: a `23505` is not cured by a blind re-run.

    MUTATION that must redden it: add "23505" to `_RETRYABLE_PAYMENT_SQLSTATES` (service) or to
    `_TRANSIENT_SQLSTATES` (money replay).
    """

    for cls in (DBAPIError, IntegrityError):
        error = _wrapped(statement, "23505", constraint, cls=cls)
        assert not isinstance(_classify_payment_db_error(error), RetryablePaymentConflictException), (
            f"pay() would re-run a 23505 on {constraint} as a transient conflict"
        )
        assert money_conflict_name(error) is None, (
            f"the money phase would replay a 23505 on {constraint} as a transient conflict"
        )


def test_t1909_the_debt_pair_collision_alone_is_retryable_and_only_as_an_integrity_error() -> None:
    """019 stage 5, `T1909` precondition 1: the one `23505` both owners retry, read by its constraint name.

    MUTATION that must redden it: drop `is_debt_pair_collision` from `_classify_payment_db_error` or
    from `money_conflict_name`; or key it on the SQLSTATE alone (the `DBAPIError` half then passes).
    """

    collision = _wrapped(DEBT_INSERT, "23505", "uq_debts_debtor_creditor_equivalent", cls=IntegrityError)
    assert isinstance(_classify_payment_db_error(collision), RetryablePaymentConflictException)
    assert money_conflict_name(collision) == "23505"
    # Not the identity resolver's: the tx_id collision stays a separate path.
    assert _is_tx_id_collision(collision) is False
    # The same code and name on a wrapper that is not an integrity error is not the collision.
    plain = _wrapped(DEBT_INSERT, "23505", "uq_debts_debtor_creditor_equivalent", cls=DBAPIError)
    assert not isinstance(_classify_payment_db_error(plain), RetryablePaymentConflictException)
    assert money_conflict_name(plain) is None


@pytest.mark.parametrize(("statement", "constraint"), _UNIQUE_VIOLATIONS)
def test_t1529_only_the_transaction_identity_reaches_the_identity_resolver(statement: str, constraint: str) -> None:
    """The one `23505` the payment path handles is `transactions_tx_id_key`, through the exact resolver.

    MUTATION that must redden it: key `_is_tx_id_collision` on the SQLSTATE alone.
    """

    error = _wrapped(statement, "23505", constraint, cls=IntegrityError)
    assert _is_tx_id_collision(error) is (constraint == TX_ID_UNIQUE_CONSTRAINT), constraint


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
def test_t1529_the_positive_controls_are_retryable_at_both_owners(sqlstate: str) -> None:
    error = _wrapped(ENVELOPE_INSERT, sqlstate)
    assert isinstance(_classify_payment_db_error(error), RetryablePaymentConflictException)
    assert money_conflict_name(error) == sqlstate
    assert _is_tx_id_collision(_wrapped(TRANSACTION_INSERT, sqlstate, TX_ID_UNIQUE_CONSTRAINT, cls=IntegrityError)) is False
