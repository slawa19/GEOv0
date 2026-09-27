"""Programme 021: the shared pieces of its reproducers. NOT a test module.

The expected-failure shape is 019's (`tests/p019_support.py`): a target test asserts its controls with plain
assertions first, and only the final comparison with the target raises `TargetMismatch`. The marker is
`xfail(raises=TargetMismatch, strict=True)`: a broken stand raises `AssertionError`, which the marker does
not accept, and a tree that already meets the target XPASSes, which `strict=True` turns into a failure - the
stage that delivers the target has to take the marker off.

`TrustLineCheckpoints` counts the integrity checkpoints the TRUST-LINE SERVICE computes. It wraps the name
the service module calls at call time and calls straight through, so what is measured is the real
computation; the counter replaces no domain behaviour. It is scoped to the service's binding on purpose:
the clearing and payment services compute checkpoints of their own through their own bindings, and a
tick-wide count would mix them in.
"""

from __future__ import annotations

import pytest

from tests.p019_support import TargetMismatch, require_target  # noqa: F401 - re-exported for 021 tests

#: What stage 1 writes into `affected_participants` of every trust-line audit row of an internal batch:
#: the checksums and check results on the row belong to the caller's whole transaction, not to the row.
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


class TrustLineCheckpoints:
    """Counts `compute_integrity_checkpoint_for_equivalent` calls made through the trust-line service."""

    def __init__(self, monkeypatch) -> None:
        import app.core.trustlines.service as service_module

        self.calls: list[object] = []
        self.fail_on_call: int | None = None
        original = service_module.compute_integrity_checkpoint_for_equivalent

        async def counting(session, *, equivalent_id):
            self.calls.append(equivalent_id)
            if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
                raise RuntimeError(f"p021 forced trust-line checkpoint failure on call {self.fail_on_call}")
            return await original(session, equivalent_id=equivalent_id)

        monkeypatch.setattr(service_module, "compute_integrity_checkpoint_for_equivalent", counting)

    @property
    def count(self) -> int:
        return len(self.calls)


async def trust_line_audit_rows(session, *, operation_type: str | None = None) -> list:
    from sqlalchemy import select

    from app.db.models.audit_log import IntegrityAuditLog

    stmt = select(IntegrityAuditLog).where(IntegrityAuditLog.operation_type.in_(TRUST_LINE_OPERATIONS))
    if operation_type is not None:
        stmt = stmt.where(IntegrityAuditLog.operation_type == operation_type)
    return list((await session.execute(stmt.order_by(IntegrityAuditLog.created_at))).scalars().all())


def is_transaction_scoped(row) -> bool:
    return (row.affected_participants or {}).get(CHECKPOINT_SCOPE_KEY) == CHECKPOINT_SCOPE_CALLER_TRANSACTION
