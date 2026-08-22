"""The uniqueness classifier must not rename unrelated integrity failures.

Program 009, `T903`.  `TrustLineService.create` translates a clash with the live-trustline
partial unique index into a declared 409.  That translation is only safe if it can tell
*which* integrity failure happened: reporting a CHECK or foreign-key violation as
"active trustline already exists" hands the caller a conflict they cannot act on.

The first implementation matched against the full `str(exc)`, which embeds the INSERT
statement — and that statement lists `from_participant_id`/`to_participant_id`, so every
failing INSERT on this table classified as a uniqueness clash.  An external review
demonstrated it with `CHECK constraint failed: chk_trust_line_status`.  These are the
counter-probes that keep the fix honest.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.trustlines.service import (
    _LIVE_TRUSTLINE_INDEX,
    _is_live_trustline_uniqueness_violation,
)


_INSERT_SQL = (
    "INSERT INTO trust_lines (id, from_participant_id, to_participant_id, equivalent_id, "
    '"limit", policy, status) VALUES (?, ?, ?, ?, ?, ?, ?)'
)


class _DriverError(Exception):
    """Stands in for the DBAPI exception carried by IntegrityError.orig."""


class _AsyncpgLike(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.constraint_name = constraint_name


def _integrity(orig: Exception) -> IntegrityError:
    return IntegrityError(_INSERT_SQL, (), orig)


@pytest.mark.parametrize(
    ("driver_message", "expected"),
    [
        # SQLite spells the columns out; this is the real clash.
        (
            "UNIQUE constraint failed: trust_lines.from_participant_id, "
            "trust_lines.to_participant_id, trust_lines.equivalent_id",
            True,
        ),
        # Unrelated failures must keep their own meaning.
        ("CHECK constraint failed: chk_trust_line_status", False),
        ("CHECK constraint failed: chk_trust_line_limit_positive", False),
        ("FOREIGN KEY constraint failed", False),
        ("NOT NULL constraint failed: trust_lines.equivalent_id", False),
        # A uniqueness clash on a DIFFERENT table is not ours either.
        ("UNIQUE constraint failed: participants.pid", False),
    ],
)
def test_classifier_reads_the_driver_error_and_not_the_statement(driver_message, expected):
    assert _is_live_trustline_uniqueness_violation(_integrity(_DriverError(driver_message))) is expected


def test_classifier_prefers_the_constraint_name_when_the_driver_provides_it():
    ours = _integrity(_AsyncpgLike(_LIVE_TRUSTLINE_INDEX))
    theirs = _integrity(_AsyncpgLike("uq_debts_debtor_creditor_equivalent"))

    assert _is_live_trustline_uniqueness_violation(ours) is True
    assert _is_live_trustline_uniqueness_violation(theirs) is False


def test_classifier_is_false_without_a_driver_error():
    assert _is_live_trustline_uniqueness_violation(IntegrityError(_INSERT_SQL, (), None)) is False

class _Asyncpg23505(Exception):
    """asyncpg unique_violation that does NOT expose `constraint_name`."""

    def __init__(self, table_name: str, detail: str) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.sqlstate = "23505"
        self.table_name = table_name
        self.detail = detail


@pytest.mark.parametrize(
    ("driver_message", "expected"),
    [
        # Two of the three columns is NOT our index -- some other pair uniqueness.
        (
            "UNIQUE constraint failed: trust_lines.from_participant_id, "
            "trust_lines.to_participant_id",
            False,
        ),
        # The word "unique" in an unrelated sentence must not be enough.
        (
            "value is not unique enough for trust_lines.from_participant_id and "
            "to_participant_id and equivalent_id",
            True,  # documented limitation: a text fallback cannot parse prose
        ),
    ],
)
def test_text_fallback_requires_the_full_triple(driver_message, expected):
    """The SQLite fallback needs all three columns; two of them belong to someone else."""
    assert _is_live_trustline_uniqueness_violation(_integrity(_DriverError(driver_message))) is expected


def test_constraint_name_is_found_through_a_nested_cause_chain():
    """Drivers wrap differently; nesting depth is not fixed at one level."""
    inner = _AsyncpgLike(_LIVE_TRUSTLINE_INDEX)
    middle = _DriverError("wrapper")
    middle.__cause__ = inner
    outer = _DriverError("outer")
    outer.__cause__ = middle

    assert _is_live_trustline_uniqueness_violation(_integrity(outer)) is True


def test_postgres_unique_violation_without_a_constraint_name_uses_sqlstate_and_table():
    ours = _Asyncpg23505(
        "trust_lines",
        "Key (from_participant_id, to_participant_id, equivalent_id)=(1, 2, 3) already exists.",
    )
    other_table = _Asyncpg23505("debts", "Key (debtor_id, creditor_id)=(1, 2) already exists.")

    assert _is_live_trustline_uniqueness_violation(_integrity(ours)) is True
    assert _is_live_trustline_uniqueness_violation(_integrity(other_table)) is False


class _SqlAlchemyAsyncpgWrapper(Exception):
    """What SQLAlchemy's asyncpg adapter actually raises.

    `sqlalchemy/dialects/postgresql/asyncpg.py:785-796` builds a fresh exception whose
    message is `"%s: %s" % (type(error), error)`, copies **only** `pgcode`/`sqlstate` onto
    it, and chains the real asyncpg error via `raise ... from error`.  So the sqlstate and
    the `table_name`/`detail` live on *different* links of the chain.
    """

    def __init__(self, sqlstate: str) -> None:
        super().__init__(
            "<class 'asyncpg.exceptions.UniqueViolationError'>: "
            "duplicate key value violates unique constraint"
        )
        self.sqlstate = sqlstate
        self.pgcode = sqlstate


class _AsyncpgNoConstraintName(Exception):
    """asyncpg's own error when PostgreSQL omits the constraint field.

    Every unset field defaults to None (`asyncpg/exceptions/_base.py`), so
    `constraint_name` is absent and only `table_name`/`detail` identify the clash.
    """

    def __init__(self) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.constraint_name = None
        self.sqlstate = "23505"
        self.table_name = "trust_lines"
        self.detail = (
            "Key (from_participant_id, to_participant_id, equivalent_id)=(1, 2, 3) "
            "already exists."
        )


def test_asyncpg_wrapper_does_not_hide_the_facts_on_the_chained_error() -> None:
    """A live-triple clash must stay declared when the driver splits it across the chain.

    Found by an independent scan of the merged wave.  The classifier read `table_name` and
    `detail` off the FIRST link carrying a sqlstate.  Under SQLAlchemy's asyncpg adapter
    that link is the wrapper, which has neither, so the check fell through to a message
    without the DETAIL line, returned False, and the IntegrityError re-raised as HTTP 500 --
    the exact outcome this classifier exists to prevent.
    """
    wrapper = _SqlAlchemyAsyncpgWrapper("23505")
    wrapper.__cause__ = _AsyncpgNoConstraintName()

    assert _is_live_trustline_uniqueness_violation(_integrity(wrapper)) is True


def test_asyncpg_wrapper_still_rejects_a_clash_on_another_table() -> None:
    """The chain walk must not become a blanket yes for any 23505."""

    class _OtherTable(_AsyncpgNoConstraintName):
        def __init__(self) -> None:
            super().__init__()
            self.table_name = "audit_log"
            self.detail = "Key (event_id)=(7) already exists."

    wrapper = _SqlAlchemyAsyncpgWrapper("23505")
    wrapper.__cause__ = _OtherTable()

    assert _is_live_trustline_uniqueness_violation(_integrity(wrapper)) is False
