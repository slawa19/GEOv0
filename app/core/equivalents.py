"""The protocol of an equivalent's lifecycle: create, stop and step, lift an integrity hold, delete (032 A-6).

Until 032 the hold was SET by the core (`app/core/ledger/reconciliation.py`) and LIFTED by an HTTP handler, and the
operator's stop, the change of the accounting step and the delete lived in handlers as well. They are moved here
MECHANICALLY: every row lock below is the one the handler took, of the same kind and in the same order (spec 032,
Verification plan: "перенос не меняет ни одного `with_for_update`"). Each function works in the caller's transaction
and never commits; the caller audits and commits, and on any failure rolls back (`app/api/audit.py`).

THE LOCK KINDS, as PostgreSQL names them (the SQLAlchemy spelling hides it):

* stop / step (`update_equivalent`) - `FOR NO KEY UPDATE` (`with_for_update(key_share=True)`);
* lift a hold (`clear_integrity_hold`) - `FOR UPDATE` on the equivalent, `FOR SHARE` on its latest result;
* delete (`delete_equivalent`) - `FOR UPDATE`.

Every money writer holds its equivalents' rows `FOR SHARE` through its commit (`MoneyBoundary`), so each of these
waits for a writer in flight and keeps a later one out until the caller commits. No advisory lock since 019
stage 5 (`T1909`): the row lock is the whole protocol.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.models.trustline import TrustLine
from app.db.reconciliation_tables import debt_reconciliation_baselines
from app.db.sqlstate import deliberate_chain, sqlstate
from app.utils.exceptions import BadRequestException, ConflictException, NotFoundException
from app.utils.validation import MONEY_MAX_SCALE, validate_equivalent_code, validate_equivalent_precision

#: The uniqueness of `equivalents.code` (migration 001, `unique=True` on the column).
CODE_UNIQUE_CONSTRAINT = "equivalents_code_key"


def canonical_code(code: str) -> str:
    """An equivalent code as an operator typed it into a path, in the one stored form (032 A-11).

    Codes are stored upper case (`^[A-Z0-9_]{1,16}$`, CHECK `chk_equivalents_code_format`), so `uah` names `UAH` in
    every path of the admin router, not only in some. A code that cannot exist is a 400, not a lookup."""

    normalized = str(code or "").strip().upper()
    validate_equivalent_code(normalized)
    return normalized


def _is_code_collision(exc: IntegrityError) -> bool:
    if sqlstate(exc) != "23505":
        return False
    for node in deliberate_chain(exc):
        name = getattr(node, "constraint_name", None) or getattr(getattr(node, "diag", None), "constraint_name", None)
        if name:
            return str(name) == CODE_UNIQUE_CONSTRAINT
    return False


async def create_equivalent(session: AsyncSession, **fields) -> Equivalent:
    """THE creation of an equivalent (030 F-030-9, F-030-19): the row and its reconciliation baseline, in the caller's
    transaction; the caller commits. A new equivalent has no debt and no journal entry, so the baseline adopts nothing
    and every later change is checkable. Only NEW equivalents: an existing one is never baselined here (024 `T2412`).
    Callers: `POST /admin/equivalents` (and through it `scripts/seed_recipe.py`) and the simulator's scenario seeder.

    An existing code is a 409 `code_exists` (032 A-2), decided by the unique constraint and not by a read before the
    insert, which a concurrent creation would pass too. The session is unusable after it; the caller rolls back."""

    from app.core.ledger.reconciliation import take_baseline

    equivalent = Equivalent(**fields)
    session.add(equivalent)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_code_collision(exc):
            raise ConflictException(
                f"Equivalent {equivalent.code} already exists",
                details={"reason": "code_exists", "code": equivalent.code},
            ) from exc
        raise
    await take_baseline(session, equivalent.id)
    return equivalent


def _editable_state(eq: Equivalent) -> dict[str, Any]:
    return {
        "symbol": eq.symbol,
        "description": eq.description,
        "precision": eq.precision,
        "metadata": eq.metadata_,
        "is_active": eq.is_active,
    }


async def update_equivalent(
    session: AsyncSession,
    code: str,
    *,
    symbol: str | None = None,
    description: str | None = None,
    precision: int | None = None,
    metadata: dict | None = None,
    is_active: bool | None = None,
) -> tuple[Equivalent, dict[str, Any], dict[str, Any]]:
    """The operator's stop (`is_active`) and change of step (`precision`), plus the descriptive fields.

    Returns the row and its editable state before and after. A field left `None` is not changed."""

    # The operator stop is a money boundary: the equivalent row `FOR NO KEY UPDATE` before any decision - it waits for
    # every money writer holding it `FOR SHARE` and keeps new ones out until the caller commits (027 stage 2; 019
    # `T1907` checked for SERIALIZABLE here instead). `FOR NO KEY UPDATE` and not `FOR UPDATE`: it conflicts with
    # `FOR SHARE` all the same, and leaves `FOR KEY SHARE` - the foreign-key checks of rows naming this equivalent - free.
    eq = (
        await session.execute(select(Equivalent).where(Equivalent.code == code).with_for_update(key_share=True))
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {code} not found")

    try:
        validate_equivalent_code(eq.code)
    except BadRequestException:
        raise ConflictException(
            "Legacy equivalent code requires manual cleanup",
            details={
                "code": eq.code,
                "reason": "noncanonical_code",
                "repair": "manual_cleanup",
            },
        )

    try:
        validate_equivalent_precision(eq.precision)
    except BadRequestException:
        if precision is None:
            raise ConflictException(
                "Legacy equivalent precision must be repaired by this PATCH",
                details={
                    "code": eq.code,
                    "reason": "noncanonical_precision",
                    "repair": "patch_precision",
                },
            )

    # T1544: the operator's stop has an observable cutoff through the ROW: every money writer - payment,
    # tick, inject and, since 019 stage 5 (`T1907`), the clearing in every attempt - reads this row
    # `FOR SHARE` and holds it through its commit (`MoneyBoundary.refuse_inactive_equivalents`), so the
    # `UPDATE` below waits for a writer that has already read `active`, and a writer that reads after it
    # committed meets 40001 and refuses on its retry. Two outcomes only: the writer commits before this
    # stop is committed, or the stop commits first and the writer refuses.

    # 028 `F-028-25` (owner В-4): precision is the accounting step, so lowering it under stored data would make
    # those amounts finer than the step. Read AFTER the row lock above: every writer that checks the step holds
    # this row `FOR SHARE` to its commit (`MoneyBoundary.share_equivalent_step`), so its row is visible here. A
    # step finer than the storage scale is the scale (`noncanonical_precision` repair stays possible).
    if precision is not None and precision < min(int(eq.precision), MONEY_MAX_SCALE):
        from app.db.journal_tables import debt_journal_entries

        used = await session.scalar(select(
            select(TrustLine.id).where(TrustLine.equivalent_id == eq.id).exists()
            | select(Debt.id).where(Debt.equivalent_id == eq.id).exists()
            | select(debt_journal_entries.c.id).where(debt_journal_entries.c.equivalent_id == eq.id).exists()))
        if used:
            raise ConflictException(
                f"Equivalent {eq.code} holds lines, debts or journal entries; its precision cannot be lowered",
                details={"code": eq.code, "reason": "precision_in_use", "precision": eq.precision},
            )

    before = _editable_state(eq)
    if symbol is not None:
        eq.symbol = symbol
    if description is not None:
        eq.description = description
    if precision is not None:
        eq.precision = precision
    if metadata is not None:
        eq.metadata_ = metadata
    if is_active is not None:
        eq.is_active = is_active

    await session.flush()
    await session.refresh(eq)
    return eq, before, _editable_state(eq)


async def clear_integrity_hold(session: AsyncSession, code: str) -> tuple[Equivalent, uuid.UUID, uuid.UUID]:
    """Lift an integrity hold, explicitly (015 step 5c, `T1546`); returns the row, the hold's result and the result
    it is cleared on.

    PREDICATE, NO CLOCKS: the equivalent is held AND its latest reconciliation result is `PASSED` AND that
    result is not the one the hold points at AND a verification here, under the row lock, is `PASSED` too.
    Result transitions alone do not give the causal order: two overlapping verifier runs can publish an older
    PASSED after a newer hold (030 F-030-10, `tests/integration/test_p030_s1_stale_passed_hold_clear_postgres.py`).

    The two predicate reads take row locks - `FOR UPDATE` on the equivalent (the stop takes `FOR NO KEY UPDATE`;
    both conflict with a money writer's `FOR SHARE`), `FOR SHARE` on the latest result - held through the caller's
    commit, so a scheduled reaction confirming a new FAILED is serialised with this clear; at READ COMMITTED
    (027 stage 2) a read after the wait sees the committed row. The equivalent is read ONCE, by the locking
    statement (032 A-11): before 032 an unlocked read by code came first and the lock re-read the hold by id."""

    from app.core.ledger.reconciliation import PASSED, verify_journal_equals_change
    from app.db.reconciliation_tables import debt_reconciliation_results

    eq = (
        await session.execute(
            select(Equivalent).where(Equivalent.code == code).with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {code} not found")
    hold_result_id = eq.integrity_hold_result_id
    if hold_result_id is None:
        raise ConflictException(
            f"Equivalent {eq.code} is not under an integrity hold",
            details={"reason": "no_integrity_hold"},
        )

    results = debt_reconciliation_results.c
    latest = (
        await session.execute(
            select(results.id, results.status)
            .where(results.equivalent_id == eq.id, results.is_latest.is_(True))
            .with_for_update(read=True)
        )
    ).first()
    # 030 F-030-10: publication order is open (`record_outcome`), so a stored PASSED may predate the hold. The
    # equivalent is re-verified here, after the row lock every money writer of it waits on.
    recheck = None if latest is None or latest.status != PASSED else await verify_journal_equals_change(session, eq.id)
    if recheck is None or recheck.status != PASSED or latest.id == hold_result_id:
        raise ConflictException(
            f"Equivalent {eq.code} can be cleared only after a later PASSED reconciliation result",
            details={
                "reason": "no_later_passed_reconciliation_result",
                "latest_status": None if latest is None else str(latest.status),
                "recheck_status": None if recheck is None else recheck.status,
            },
        )

    await session.execute(
        sql_update(Equivalent)
        .where(
            Equivalent.id == eq.id,
            Equivalent.integrity_hold_result_id == hold_result_id,
        )
        .values(integrity_hold_result_id=None)
    )
    await session.refresh(eq)
    return eq, hold_result_id, latest.id


async def equivalent_usage_counts(session: AsyncSession, *, equivalent_id) -> dict[str, int]:
    trustlines = (
        await session.execute(select(func.count()).select_from(TrustLine).where(TrustLine.equivalent_id == equivalent_id))
    ).scalar_one()
    debts = (
        await session.execute(select(func.count()).select_from(Debt).where(Debt.equivalent_id == equivalent_id))
    ).scalar_one()
    integrity_checkpoints = (
        await session.execute(
            select(func.count())
            .select_from(IntegrityCheckpoint)
            .where(IntegrityCheckpoint.equivalent_id == equivalent_id)
        )
    ).scalar_one()

    return {
        "trustlines": int(trustlines or 0),
        "debts": int(debts or 0),
        "integrity_checkpoints": int(integrity_checkpoints or 0),
    }


async def delete_equivalent(session: AsyncSession, code: str) -> dict[str, Any]:
    """Delete a stopped, unused equivalent with its baseline header; returns its state before."""

    # The row `FOR UPDATE` before the usage counts below (027 stage 2): it waits for every money writer holding it
    # `FOR SHARE`, and the counts are read after them.
    eq = (
        await session.execute(select(Equivalent).where(Equivalent.code == code).with_for_update())
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {code} not found")

    # T1524: the RESTRICT foreign key is the guarantee that no debt outlives its equivalent. No advisory lock
    # narrows the window: the row lock above waits for every money writer holding it `FOR SHARE`, and a writer
    # that reads it afterwards finds it gone and refuses (`refuse_inactive_equivalents`). (A deletable
    # equivalent is already inactive, so writers refuse it anyway.)

    if eq.is_active:
        raise ConflictException("Deactivate equivalent before delete")

    counts = await equivalent_usage_counts(session, equivalent_id=eq.id)
    # 024 `T2412.3`: integrity checkpoints are reports ABOUT the equivalent (`ON DELETE CASCADE`), not use of
    # it; counted as use, every equivalent was undeletable after the first integrity run. They stay in the
    # counts reported, not in the refusal.
    if counts["trustlines"] > 0 or counts["debts"] > 0:
        raise ConflictException("Equivalent is in use", details=counts)

    before = {"code": eq.code, **_editable_state(eq)}

    try:
        # 024 `T2412.3`: the baseline header goes with the equivalent - it is `RESTRICT`, and since `T2412.2`
        # every created equivalent has one. Only the HEADER: a baseline that recorded offsets adopted real
        # debts, its offsets keep `RESTRICT` on it, and this statement then fails into the 409 below. Journal
        # entries keep refusing the equivalent's own delete the same way.
        await session.execute(
            sql_delete(debt_reconciliation_baselines).where(
                debt_reconciliation_baselines.c.equivalent_id == eq.id
            )
        )
        await session.delete(eq)
        await session.flush()
    except IntegrityError as exc:
        # T1524: `debts.equivalent_id` is RESTRICT. If a debt exists that the count above did not see - it
        # appeared after the count, or a writer did not hold this row `FOR SHARE` - the database refuses the
        # delete instead of cascading the obligation away. Reported as the same 409 an equivalent in use already
        # gets, because that is exactly what it is. The session is unusable after it; the caller rolls back.
        raise ConflictException(
            "Equivalent is in use",
            details={"reason": "referenced_by_existing_rows"},
        ) from exc
    return before
