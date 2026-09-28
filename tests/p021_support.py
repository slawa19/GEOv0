"""Programme 021: the shared pieces of its reproducers. NOT a test module.

The expected-failure shape is 019's (`tests/p019_support.py`): a target test asserts its controls with plain
assertions first, and only the final comparison with the target raises `TargetMismatch`. The marker is
`xfail(raises=TargetMismatch, strict=True)`: a broken stand raises `AssertionError`, which the marker does
not accept, and a tree that already meets the target XPASSes, which `strict=True` turns into a failure - the
stage that delivers the target has to take the marker off.

`TrustLineBatchPoints` counts the per-equivalent points of the trust-line batch: the FIRST touch of an equivalent
and the staging of its audit rows in `finish()`. Until 024 `T2413.2` the batch computed a full-equivalent
integrity checkpoint at exactly these two points (before and after), and the 021 tests counted and failed
those computations; the checkpoints are gone, the points - and what a failure at one must leave behind - are
not. In the 021 test modules "checkpoint" in a name or message means such a point. It wraps the batch's own
methods and calls straight through; it replaces no domain behaviour.
"""

from __future__ import annotations

import pytest

from tests.p019_support import TargetMismatch, require_target  # noqa: F401 - re-exported for 021 tests

#: What stage 1 writes into `affected_participants` of every trust-line audit row of an internal batch:
#: the row belongs to the caller's whole transaction (since 024 `T2413.2` it carries no checksums).
CHECKPOINT_SCOPE_KEY = "checkpoint_scope"
CHECKPOINT_SCOPE_CALLER_TRANSACTION = "caller_transaction"

TRUST_LINE_OPERATIONS = ("TRUST_LINE_CREATE", "TRUST_LINE_UPDATE", "TRUST_LINE_CLOSE")


def target_xfail_021(stage: str, what: str):
    """The 021 marker: an expected `TargetMismatch`, strict, naming the task that removes it."""

    return pytest.mark.xfail(
        raises=TargetMismatch,
        strict=True,
        reason=f"021 target, delivered by {stage}: {what}",
    )


class TrustLineBatchPoints:
    """Counts (and can fail) the trust-line batch's per-equivalent points: first touch, audit staging."""

    def __init__(self, monkeypatch) -> None:
        from app.core.trustlines.service import TrustLineWriteBatch

        self.calls: list[object] = []
        self.fail_on_call: int | None = None
        touch, stage = TrustLineWriteBatch._touch, TrustLineWriteBatch._stage_audit_rows

        def point(equivalent_id) -> None:
            self.calls.append(equivalent_id)
            if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
                raise RuntimeError(f"p021 forced trust-line batch failure on call {self.fail_on_call}")

        async def counting_touch(batch, equivalent_id, equivalent_code):
            if equivalent_id not in batch._codes:
                point(equivalent_id)
            return await touch(batch, equivalent_id, equivalent_code)

        async def counting_stage(batch, equivalent_id):
            point(equivalent_id)
            return await stage(batch, equivalent_id)

        monkeypatch.setattr(TrustLineWriteBatch, "_touch", counting_touch)
        monkeypatch.setattr(TrustLineWriteBatch, "_stage_audit_rows", counting_stage)

    @property
    def count(self) -> int:
        return len(self.calls)


async def trust_line_audit_rows(session, *, equivalent_codes, operation_type: str | None = None) -> list:
    """Trust-line audit rows of THIS test's equivalents only. Required filter: the mode-A tier database is shared
    across the session and may hold rows other tests committed, so an unfiltered count measures them too."""

    from sqlalchemy import select

    from app.db.models.audit_log import IntegrityAuditLog

    stmt = select(IntegrityAuditLog).where(
        IntegrityAuditLog.operation_type.in_(TRUST_LINE_OPERATIONS),
        IntegrityAuditLog.equivalent_code.in_(list(equivalent_codes)),
    )
    if operation_type is not None:
        stmt = stmt.where(IntegrityAuditLog.operation_type == operation_type)
    return list((await session.execute(stmt.order_by(IntegrityAuditLog.created_at))).scalars().all())


def is_transaction_scoped(row) -> bool:
    return (row.affected_participants or {}).get(CHECKPOINT_SCOPE_KEY) == CHECKPOINT_SCOPE_CALLER_TRANSACTION
