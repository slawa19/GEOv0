"""The one writer of `audit_log` rows from HTTP handlers, and the one "action + audit + commit/rollback" frame.

032 A-8. Until this module the admin router carried six copies of the same frame - stage the mutation, add the
audit row, commit, roll back on any failure - and `auth.py` built its own `AuditLog` by hand. The row is built in
one place now, so the request id, client address and user agent are read the same way for every action.

THE FRAME IS THE ATOMICITY CONTRACT, not a convenience: the action and its audit row commit together or not at all
(`tests/integration/test_admin_mutation_audit_atomicity.py`). And every failure inside it - a refusal raised under
a row lock included - rolls back HERE, so the lock ends with the refusal and not when the request's session is torn
down after the response was sent (032 A-7: one rule instead of a rollback in some branches and not in others).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_log import AuditLog
from app.utils.request_id import new_request_id, request_id_var, validate_request_id


def add_audit_entry(
    db: AsyncSession,
    *,
    request: Request,
    action: str,
    actor_role: str | None = "admin",
    object_type: str | None = None,
    object_id: str | None = None,
    reason: str | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> AuditLog:
    """Stage one audit row in the caller's transaction; the caller commits."""

    rid = validate_request_id(request_id_var.get())
    if rid is None:
        rid = validate_request_id(request.headers.get("X-Request-ID")) or new_request_id()
    entry = AuditLog(
        actor_id=None,
        actor_role=actor_role,
        action=action,
        object_type=object_type,
        object_id=object_id,
        reason=reason,
        before_state=before_state,
        after_state=after_state,
        request_id=rid,
        ip_address=(request.client.host if request.client else None) or None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(entry)
    return entry


@dataclass
class AuditRecord:
    """What the action reports for its audit row; filled inside `audited(...)`, written at its successful end."""

    object_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None


@asynccontextmanager
async def audited(
    db: AsyncSession,
    *,
    request: Request,
    action: str,
    object_type: str | None,
    object_id: str | None = None,
    reason: str | None = None,
) -> AsyncIterator[AuditRecord]:
    """Run an operator action, then its audit row and the commit; on ANY failure roll back and re-raise.

    The body performs the action (row locks, checks, mutation) and fills the yielded record. The audit row is
    added only if the body completed, so a refusal leaves no row, and the commit carries both or neither.
    `BaseException` on purpose: a cancelled request must not leave the transaction and its locks open either.
    """

    record = AuditRecord(object_id=object_id)
    try:
        yield record
        add_audit_entry(
            db,
            request=request,
            action=action,
            object_type=object_type,
            object_id=record.object_id,
            reason=reason,
            before_state=record.before_state,
            after_state=record.after_state,
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
