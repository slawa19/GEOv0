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
