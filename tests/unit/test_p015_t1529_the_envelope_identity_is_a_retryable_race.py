"""Programme 015, T1529: a duplicate operation envelope is a race, not a logic error.

WHAT IS MEASURED HERE. The classifier `PaymentEngine._is_retryable_db_error` is a pure function of
an exception, an operation name and the engine's backend, so its whole truth table is measurable
without a database. What is NOT measurable here is that PostgreSQL actually produces this exception
on the concurrent-commit schedule - that is
`tests/integration/test_payment_commit_advisory_locks_postgres.py`, which runs the real race at
SERIALIZABLE, and without it every assertion below would be a statement about an error nobody has
seen.

WHY THE PREDICATE IS PINNED THIS TIGHTLY. A `23505` means "somebody already wrote this". Retrying
one is only ever safe when the second attempt can READ what that somebody wrote and decide from it,
which `_run_uow_with_retry` arranges by rolling back first and refusing to retry when the rollback
fails. Every row of the table below is a way for that reading not to happen: a different table, a
different constraint, a caller-owned transaction whose snapshot survives the rollback.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import DBAPIError

from app.core.payments.engine import (
    _DEBT_OPERATION_IDENTITY_CONSTRAINTS,
    PaymentEngine,
)
from app.db.journal_tables import debt_operations

ENVELOPE_INSERT = (
    "INSERT INTO debt_operations (id, kind, identity, tx_id, intent, intent_digest, "
    "schema_version, money_encoding_version, intent_encoding_version, opened_at, state) "
    "VALUES ($1::UUID, $2::VARCHAR, $3::VARCHAR, $4::VARCHAR, $5::JSON, $6::VARCHAR, "
    "$7::INTEGER, $8::INTEGER, $9::INTEGER, $10::TIMESTAMP WITH TIME ZONE, $11::VARCHAR)"
)


class _FakeSession:
    """Just enough session for the retry wrapper: it counts its own rollbacks."""

    def __init__(self) -> None:
        self.rollback_calls = 0

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def execute(self, *args, **kwargs):  # pragma: no cover - never reached here
        raise AssertionError("this unit test issues no SQL")


def _unique_violation(statement: str, constraint: str) -> DBAPIError:
    """A `DBAPIError` shaped exactly as asyncpg's wrapper delivers one.

    The SQLSTATE and the constraint name are the two fields the classifier reads, and asyncpg
    carries both on the driver exception - see the gate log quoted in T1529.
    """

    orig = type(
        "FakeUniqueViolationError",
        (Exception,),
        {"sqlstate": "23505", "constraint_name": constraint},
    )("duplicate key value violates unique constraint")
    return DBAPIError(
        statement=statement,
        params=None,
        orig=orig,
        connection_invalidated=False,
    )


def _engine(monkeypatch, session=None) -> PaymentEngine:
    engine = PaymentEngine(session or _FakeSession())  # type: ignore[arg-type]
    monkeypatch.setattr(engine, "_is_postgres", lambda: True)
    return engine


@pytest.mark.parametrize("constraint", sorted(_DEBT_OPERATION_IDENTITY_CONSTRAINTS))
def test_t1529_an_envelope_identity_collision_during_commit_is_retryable(
    monkeypatch, constraint: str
) -> None:
    """The finding itself: this exact error used to be classified "fail closed".

    MUTATION that must redden it: drop the `is_envelope_insert` branch from
    `_is_retryable_db_error` - which is the code as it stood at `7ba41d4`, where the PostgreSQL
    gate met this error and the commit raised `IntegrityError` instead of returning idempotently.
    """

    engine = _engine(monkeypatch)
    error = _unique_violation(ENVELOPE_INSERT, constraint)

    assert engine._is_retryable_db_error(error, op="commit") is True, (
        f"a concurrent duplicate commit collided on {constraint} and the engine refuses to "
        f"re-read: the payment raises instead of being idempotent"
    )


def test_t1529_the_identity_set_is_the_envelope_identity_the_schema_declares() -> None:
    """ANTI-DRIFT, and the reason the constant is a literal rather than a comprehension.

    Reading the names off `debt_operations.constraints` would make the retry predicate widen by
    itself the day somebody adds a unique constraint to that table - a policy change nobody
    decided. So the names are written out, and this is what stops them drifting: renaming one, or
    adding a third, reddens here and forces the decision to be made on purpose.

    MUTATION that must redden it: rename either constraint in `app/db/journal_tables.py`, or add a
    `UniqueConstraint` to `debt_operations`, without touching
    `_DEBT_OPERATION_IDENTITY_CONSTRAINTS`.
    """

    declared = {
        constraint.name
        for constraint in debt_operations.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert declared == set(_DEBT_OPERATION_IDENTITY_CONSTRAINTS), (
        f"the retry predicate names {sorted(_DEBT_OPERATION_IDENTITY_CONSTRAINTS)} while "
        f"`debt_operations` declares {sorted(declared)}. Decide which of them means \"this "
        f"operation was already opened\" - a retry predicate pointing at a constraint that no "
        f"longer exists is a guard that passes vacuously."
    )


@pytest.mark.parametrize(
    ("statement", "constraint", "op", "why"),
    [
        pytest.param(
            ENVELOPE_INSERT,
            "pk_debt_operations",
            "commit",
            "a primary-key collision on a freshly generated uuid4 is not a race, it is a defect",
            id="other_constraint_on_the_same_table",
        ),
        pytest.param(
            "INSERT INTO debt_operations_archive (id, kind, identity) VALUES ($1, $2, $3)",
            "uq_debt_operations_kind_identity",
            "commit",
            "a table whose name merely BEGINS with the envelope's must not match. No such table "
            "exists today - which is the point of testing the rule rather than the corpus: the "
            "word boundary is what stops one from joining this predicate by being named well",
            id="table_name_extends_the_envelope_table",
        ),
        pytest.param(
            ENVELOPE_INSERT,
            "uq_debt_operations_kind_identity",
            "commit_nocommit",
            "the caller owns the transaction: a savepoint rollback keeps the stale snapshot, so "
            "the second attempt would collide again - the outer owner must restart",
            id="caller_owned_unit_of_work",
        ),
        pytest.param(
            ENVELOPE_INSERT,
            "uq_debt_operations_kind_identity",
            "prepare",
            "prepare opens no envelope; a duplicate there is a different question",
            id="wrong_operation",
        ),
        pytest.param(
            ENVELOPE_INSERT,
            "uq_debt_operations_kind_identity",
            "abort",
            "abort opens no envelope either",
            id="abort",
        ),
    ],
)
def test_t1529_everything_else_still_fails_closed(
    monkeypatch, statement: str, constraint: str, op: str, why: str
) -> None:
    """ANTI-VACUUM for the widening above: it must admit the race and nothing else.

    MUTATION that must redden it: key the new branch on the SQLSTATE alone (drop the statement and
    constraint tests), or drop the `op == "commit"` test, or match the table name by
    `startswith("INSERT INTO DEBT_OPERATIONS")` without requiring a following space or `(`.
    """

    engine = _engine(monkeypatch)

    assert (
        engine._is_retryable_db_error(_unique_violation(statement, constraint), op=op) is False
    ), why


@pytest.mark.asyncio
async def test_t1529_the_retry_rolls_back_once_and_re_runs_the_unit_of_work(monkeypatch) -> None:
    """The half that makes the retry SAFE rather than merely permitted.

    A rollback before the second attempt is what gives it a fresh snapshot to read the other
    writer's outcome from; without it the retry would re-run on a transaction PostgreSQL has
    already aborted. This measures that the wrapper actually takes it for this error.

    MUTATION that must redden it: remove the `await self.session.rollback()` call from the
    non-savepoint branch of `_run_uow_with_retry`, or make the new classifier branch return False.
    """

    session = _FakeSession()
    engine = _engine(monkeypatch, session)
    engine._retry_attempts = 3
    engine._retry_base_delay_s = 0.0
    engine._retry_max_delay_s = 0.0

    attempts: list[int] = []

    async def _uow() -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise _unique_violation(ENVELOPE_INSERT, "uq_debt_operations_kind_identity")
        return "already committed"

    result = await engine._run_uow_with_retry(op="commit", fn=_uow)

    assert result == "already committed"
    assert attempts == [1, 2], attempts
    assert session.rollback_calls == 1, (
        "the second attempt must run on a rolled-back transaction; without that rollback it reads "
        "nothing and PostgreSQL refuses every further statement"
    )


@pytest.mark.asyncio
async def test_t1529_a_bounded_budget_keeps_a_permanent_duplicate_from_looping(monkeypatch) -> None:
    """The refusal case the widening must not swallow.

    An envelope row becomes visible only in the same database transaction that sets
    `transactions.state = 'COMMITTED'`, so a permanently colliding identity on a transaction that
    never commits is unreachable by construction. "Unreachable by construction" is an argument, and
    arguments are what this programme keeps finding wrong, so the behaviour if it happened anyway
    is measured instead of asserted away: the attempt budget runs out and the ORIGINAL 23505 is
    re-raised.

    MUTATION that must redden it: `continue` unconditionally instead of honouring
    `attempt >= self._retry_attempts`.
    """

    session = _FakeSession()
    engine = _engine(monkeypatch, session)
    engine._retry_attempts = 3
    engine._retry_base_delay_s = 0.0
    engine._retry_max_delay_s = 0.0

    attempts: list[int] = []

    async def _always_duplicate() -> str:
        attempts.append(len(attempts) + 1)
        raise _unique_violation(ENVELOPE_INSERT, "uq_debt_operations_kind_identity")

    with pytest.raises(DBAPIError) as raised:
        await engine._run_uow_with_retry(op="commit", fn=_always_duplicate)

    assert getattr(raised.value.orig, "sqlstate", None) == "23505"
    assert len(attempts) == 3, attempts
    assert session.rollback_calls == 2, session.rollback_calls
