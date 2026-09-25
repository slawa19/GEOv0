"""The money boundary: the stop/hold guard, the payment delta check and THE ONE equivalent lock.

Programme 019 stage 2 (`T1903`, `FORK-2`) moved every lock primitive here from the payment engine without
a change of semantics. Stage 5 (`T1909`, decision `KEEP-EQUIVALENT-LOCK` of the fourth consultation,
2026-09-25) reduced them to ONE advisory identity per equivalent (`_EQUIVALENT_OWNER_LOCK_NAMESPACE`, key
`_equivalent_owner_lock_key`) in TWO MODES:

* **shared** - `acquire_shared_equivalent_locks` / `_acquire_shared_equivalent_locks_in_order`
  (`pg_advisory_xact_lock_shared`): a payment, a staged money phase of the tick, an inject. Shared holders
  do not wait for one another; SERIALIZABLE and the whole-transaction retry of each owner keep their
  concurrent debt writes correct (`T1908`: lost update, opposite directions and the bottleneck reach the
  serial result through 40001 and a retry; a concurrent insert of one new debt row is the `23505` the
  owners retry, `is_debt_pair_collision`);
* **exclusive** - `acquire_exclusive_equivalent_session_lock` (`pg_advisory_lock`, session level): the
  clearing only, on a PINNED connection, taken BEFORE its authoritative snapshot and held through its
  commit resolution and every permitted retry. It waits for the shared holders in flight and keeps new
  ones out, which is what gives the clearing liveness under continuous payment load (`T1908` (d): without
  it three of four predeclared batches missed the 90 % criterion).

There are no transaction or pair advisory locks and no reservations any more (`prepare_locks` is dropped by
migration `031`). Admin stop/hold and the equivalent `DELETE` take NO advisory lock: they `UPDATE`/`DELETE`
the equivalent row, which every money writer reads `FOR SHARE` through its commit
(`refuse_inactive_equivalents(row_lock=True)`); reconciliation reads at its chosen isolation and sets a hold
the same way.

THE ONE LOCK ORDER: the equivalent lock (a staged caller takes its COMPLETE set, sorted by key, before any
protected write) -> the equivalent row `FOR SHARE` -> the debt rows. The reverse order is a reachable
deadlock, and a deadlock retry does not replace it.

TWO LIFETIMES, deliberately different:

* **transaction-level** - the shared lock is released by the end of the transaction that took it,
  whatever ends it;
* **session-level** - the clearing's exclusive lock survives the rollback that gives it a fresh snapshot
  and every attempt's transaction on that connection, and lives until the explicit
  `release_exclusive_equivalent_session_lock`; a connection whose release is not confirmed is invalidated,
  never returned to the pool (`ClearingService._release_interlock_session`).

KEY SPACE. PostgreSQL's two-int key space with a stable domain tag as the first int. A one-argument
`pg_advisory_*` is a different key space and never the same lock.
"""

from __future__ import annotations

import hashlib
import logging
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import ConflictException, GeoException

logger = logging.getLogger(__name__)

# EXACT ZERO, and the constant is the subject of T1522 of programme 015.
#
# The payment delta barrier compared `abs(drift) > Decimal("0.00000001")` - one whole quantum of
# `Numeric(20, 8)`, strict. Storage is scale 8 and, since 012/T1201, the money door refuses any
# amount the column cannot hold unchanged, so every net position and every flow is a scale-8 value
# and every possible drift is a MULTIPLE of one quantum. There is no sub-quantum drift left for a
# tolerance to absorb - that was the 012-era phenomenon, when the door took scale 18 and the column
# rounded underneath it. What the old constant admitted was therefore the SMALLEST corruption that
# can exist: the ledger moving one atom more, or less, than the payment declared, unreported.
#
# Nor was it a concurrency mitigation, though it looks like one: both reads of the net positions are
# made in ONE SERIALIZABLE transaction, whose snapshot is fixed at its first statement, so no foreign
# write lands between them (until 019 stage 5 an exclusive advisory lock over the whole equivalent
# said the same; payments now hold it shared, and the snapshot is what holds). And a race would produce a drift the size of the
# other payment, not one atom - a mitigation shaped like this absorbs the smallest case and nothing
# larger, which is a threshold, not a safeguard.
#
# It is a module constant rather than a local so that it can be named in a test: the 012
# counter-check widens it deliberately, to reach the ledger state `F-012-1` is about now that this
# barrier catches the 1e-9 drift a widened door produces.
_DELTA_DRIFT_TOLERANCE = Decimal("0")

# PostgreSQL's two-int advisory-lock key space; the first int is a stable domain tag. The name keeps its
# historical "owner" (019 stage 5 retained this ONE identity; the transaction-lock domain 0x475458 and the
# one-BIGINT pair-lock space are gone).
_EQUIVALENT_OWNER_LOCK_NAMESPACE = 0x474551

#: `details.reason` of the isolation refusal (019 stage 5, `T1907`, `FORK-2`).
ISOLATION_NOT_SERIALIZABLE_REASON = "isolation_not_serializable"


class IsolationNotSerializable(GeoException):
    """A money writer was handed a transaction that does not run SERIALIZABLE; nothing was written.

    An internal error (`E010`, 500) and deliberately not a conflict: no retry of the same request on the
    same kind of session can succeed, and the caller that supplied the session is the defect.
    """

    def __init__(self, *, writer: str, isolation: str):
        super().__init__(
            details={
                "reason": ISOLATION_NOT_SERIALIZABLE_REASON,
                "writer": writer,
                "isolation": isolation,
            }
        )


class MoneyBoundary:
    """The equivalent lock, the stop/hold guard and the delta check over one session.

    One instance carries one advisory-lock deadline: the budget starts at the first lock this instance
    takes and is shared by every later lock it takes (`_set_local_advisory_lock_timeout`). A caller
    that wants a fresh budget per unit of work takes a fresh instance.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        from app.config import settings

        total_timeout_s = float(settings.PAYMENT_TOTAL_TIMEOUT_SECONDS or 10)
        commit_timeout_s = float(settings.COMMIT_TIMEOUT_SECONDS or 5)
        self._advisory_lock_budget_s = max(
            0.001,
            min(total_timeout_s, commit_timeout_s),
        )
        self._advisory_lock_timeout_enabled = True
        self._advisory_lock_deadline: float | None = None

    @staticmethod
    def _equivalent_owner_lock_key(equivalent_id: UUID) -> int:
        """Compute a stable signed INT key inside the equivalent-lock domain."""
        digest = hashlib.sha256(equivalent_id.bytes).digest()
        return int.from_bytes(digest[:4], byteorder="big", signed=True)

    async def _acquire_shared_equivalent_locks_in_order(
        self,
        equivalent_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
    ) -> None:
        """The equivalent lock in SHARED mode for the complete set, in one global (sorted-key) order."""
        keys = sorted(
            {
                self._equivalent_owner_lock_key(equivalent_id)
                for equivalent_id in equivalent_ids
            }
        )
        for key in keys:
            await self._set_local_advisory_lock_timeout()
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock_shared(:namespace, :key)"),
                {
                    "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                    "key": key,
                },
            )

    async def acquire_shared_equivalent_locks(
        self,
        equivalent_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
    ) -> None:
        """A caller-owned staged batch's (the tick's money phase, an inject) complete equivalent set, shared."""
        previous_lock_timeout = await self.session.scalar(text("SHOW lock_timeout"))
        acquired = False
        try:
            await self._acquire_shared_equivalent_locks_in_order(equivalent_ids)
            acquired = True
        finally:
            # Staged callers own a larger outer transaction. The lock deadline must
            # not become the timeout policy for later statements in that
            # transaction. If acquisition itself fails/cancels, the outer owner
            # rolls back the unusable UoW.
            if acquired:
                await self.session.execute(
                    text(
                        "SELECT set_config('lock_timeout', :lock_timeout, true)"
                    ),
                    {"lock_timeout": str(previous_lock_timeout)},
                )

    async def acquire_exclusive_equivalent_session_lock(
        self,
        equivalent_id: UUID,
    ) -> None:
        """The clearing's EXCLUSIVE equivalent lock, at SESSION level, beyond a transaction rollback.

        The clearing pins the physical connection, takes this lock (it waits for every shared holder in
        flight), rolls the acquisition transaction back to obtain a snapshot newer than whatever it waited
        for, and explicitly releases the lock before returning the connection.
        """
        await self._set_local_advisory_lock_timeout()
        await self.session.execute(
            text("SELECT pg_advisory_lock(:namespace, :key)"),
            {
                "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                "key": self._equivalent_owner_lock_key(equivalent_id),
            },
        )

    async def release_exclusive_equivalent_session_lock(
        self,
        equivalent_id: UUID,
    ) -> bool:
        """Release the clearing's session-level exclusive lock; True only when PostgreSQL confirms it."""
        return bool(
            await self.session.scalar(
                text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {
                    "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                    "key": self._equivalent_owner_lock_key(equivalent_id),
                },
            )
        )

    async def _set_local_advisory_lock_timeout(self) -> None:
        if not self._advisory_lock_timeout_enabled:
            return
        now = time.monotonic()
        if self._advisory_lock_deadline is None:
            self._advisory_lock_deadline = now + self._advisory_lock_budget_s
        timeout_ms = max(
            1,
            int((self._advisory_lock_deadline - now) * 1000),
        )
        await self.session.execute(
            text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'")
        )

    @staticmethod
    async def require_serializable(session: AsyncSession, *, writer: str) -> None:
        """Refuse, before the first write, a work transaction that does not run SERIALIZABLE.

        019 stage 5 (`T1907`, `FORK-2`): the invariants that stage 5 stops protecting with advisory
        locks - the capacity read of a payment, the one-direction-per-pair check of the inject, the
        clearing's re-read of its cycle, the floor of the trust decay - hold under concurrency only
        when EVERY writer taking part runs SERIALIZABLE; SSI in one participant does not make a mixed
        load serializable. The application's own engine is fenced to SERIALIZABLE (`app/config.py`,
        2026-09-25), so this guards a session HANDED IN by a caller at another level.

        It reads the ACTUAL level of the transaction the writer is about to write in (`SHOW
        transaction_isolation`, which opens that transaction if it has not begun - at the level it
        will run at), so it must be called on the session that writes, in the transaction that writes.
        It never commits, rolls back or re-levels the caller's transaction: a transaction that has
        already read cannot be upgraded honestly (`SET TRANSACTION` after a read is refused, and a
        rollback would discard the caller's work) - the only safe answer is to refuse.

        Blind spot, named: a writer that is not routed through a caller of this function is not
        covered by it; the callers are listed in `specs/019-payment-one-transaction/spec.md`
        (`T1907`, per-boundary table).
        """

        level = str(await session.scalar(text("SHOW transaction_isolation")) or "").strip().lower()
        if level != "serializable":
            logger.error(
                "event=money.isolation_refused writer=%s isolation=%s", writer, level
            )
            raise IsolationNotSerializable(writer=writer, isolation=level)

    #: `details.reason` of the operator-stop refusal (T1544). A state conflict, and deliberately NOT
    #: retryable: `details.retryable=true` belongs to the serialization-conflict variant of `E008`,
    #: and repeating a request against a deactivated equivalent cannot succeed.
    EQUIVALENT_INACTIVE_REASON = "equivalent_inactive"

    #: `details.reason` of the integrity-hold refusal (programme 015 step 5c, `T1546`). Same status,
    #: code and non-retryability as the operator stop, and a distinct reason: a hold is set by the
    #: scheduled reaction to a confirmed reconciliation `FAILED` and lifted only by an admin.
    EQUIVALENT_INTEGRITY_HOLD_REASON = "equivalent_integrity_hold"

    #: Every reason this boundary refuses with. The simulator classifies each as a REJECTION of the
    #: one operation, never as an error of the run, and matches on this set (step 5c brief).
    MONEY_STOP_REASONS = frozenset({EQUIVALENT_INACTIVE_REASON, EQUIVALENT_INTEGRITY_HOLD_REASON})

    @classmethod
    def inactive_equivalent_conflict(cls, codes: list[str]) -> ConflictException:
        return ConflictException(
            f"Equivalent {', '.join(codes)} is not active",
            details={"reason": cls.EQUIVALENT_INACTIVE_REASON, "equivalents": codes},
        )

    @classmethod
    def integrity_hold_conflict(cls, codes: list[str]) -> ConflictException:
        return ConflictException(
            f"Equivalent {', '.join(codes)} is under an integrity hold",
            details={"reason": cls.EQUIVALENT_INTEGRITY_HOLD_REASON, "equivalents": codes},
        )

    async def refuse_inactive_equivalents(
        self,
        equivalent_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
        *,
        row_lock: bool,
    ) -> None:
        """T1544: money does not move in an equivalent the operator has deactivated - and, since
        step 5c (`T1546`), in one under an integrity hold.

        ONE STATEMENT READS BOTH: `is_active` and `integrity_hold_result_id` live on the same row, so the
        hold inherits every guarantee described below unchanged. The scheduled reaction that sets a hold
        `UPDATE`s this row, exactly like the deactivating PATCH. ONE REASON PER REFUSAL: an equivalent
        that is both inactive and held is refused as `equivalent_inactive`. Neither refusal is retryable.

        The flag is read as columns, never through an `Equivalent` instance: the payment service
        loads one before routing, and its cached `is_active` can still say True.

        `row_lock=True` renders `FOR SHARE` on PostgreSQL, and IT is what binds a money writer to the
        stop: SERIALIZABLE takes its snapshot before any lock wait, and a plain read after the wait
        still returns the value from before the PATCH committed (measured 2026-09-13, `FOR KEY SHARE`
        equally stale). `FOR SHARE` instead waits for an uncommitted PATCH and then fails with 40001,
        which the owner's retry turns into a fresh snapshot that sees the stop; and a PATCH arriving
        after this read waits for the writer's commit. Since 019 stage 5 (`T1909`) the PATCH, the hold
        clear, the reaction and the equivalent `DELETE` take no advisory lock at all - this row lock is
        the whole protocol. Order: the equivalent advisory lock the writer holds (shared, or the
        clearing's exclusive) first, this row second, the debt rows after.

        Since 019 stage 5 (`T1907`, `FORK-7`) the clearing reads with `row_lock=True` too, in every
        attempt, and holds the row lock through its commit (`ClearingService._refuse_if_equivalent_inactive`).
        `row_lock=False` has no caller left in the application.
        """
        ids = sorted(set(equivalent_ids), key=str)
        if not ids:
            return
        stmt = select(
            Equivalent.code, Equivalent.is_active, Equivalent.integrity_hold_result_id
        ).where(Equivalent.id.in_(ids))
        if row_lock:
            stmt = stmt.with_for_update(read=True)
        rows = (await self.session.execute(stmt)).all()
        inactive = sorted(str(code) for code, is_active, _hold in rows if not is_active)
        if inactive:
            raise self.inactive_equivalent_conflict(inactive)
        held = sorted(str(code) for code, _is_active, hold in rows if hold is not None)
        if held:
            raise self.integrity_hold_conflict(held)

    async def _snapshot_net_positions(
        self,
        *,
        equivalent_id: UUID,
        participant_ids: set[UUID],
    ) -> dict[UUID, Decimal]:
        """Read net positions for participants (credits - debts) in an equivalent."""
        if not participant_ids:
            return {}

        credits_rows = (
            await self.session.execute(
                select(Debt.creditor_id, func.sum(Debt.amount).label("total"))
                .where(
                    Debt.equivalent_id == equivalent_id,
                    Debt.creditor_id.in_(participant_ids),
                )
                .group_by(Debt.creditor_id)
            )
        ).all()
        debts_rows = (
            await self.session.execute(
                select(Debt.debtor_id, func.sum(Debt.amount).label("total"))
                .where(
                    Debt.equivalent_id == equivalent_id,
                    Debt.debtor_id.in_(participant_ids),
                )
                .group_by(Debt.debtor_id)
            )
        ).all()

        credits = {pid: (total or Decimal("0")) for pid, total in credits_rows}
        debts = {pid: (total or Decimal("0")) for pid, total in debts_rows}

        out: dict[UUID, Decimal] = {}
        for pid in participant_ids:
            out[pid] = Decimal(str(credits.get(pid, Decimal("0")))) - Decimal(
                str(debts.get(pid, Decimal("0")))
            )
        return out

    async def check_payment_delta(
        self,
        *,
        equivalent_id: UUID,
        flows: list[tuple[UUID, UUID, Decimal]],
        net_positions_before: dict[UUID, Decimal],
    ) -> None:
        """Verify per-participant net position deltas match applied flows."""
        if not flows:
            return

        expected_delta: dict[UUID, Decimal] = {}
        for from_id, to_id, amount in flows:
            expected_delta[from_id] = expected_delta.get(from_id, Decimal("0")) - Decimal(
                str(amount)
            )
            expected_delta[to_id] = expected_delta.get(to_id, Decimal("0")) + Decimal(
                str(amount)
            )

        positions_after = await self._snapshot_net_positions(
            equivalent_id=equivalent_id,
            participant_ids=set(expected_delta.keys()),
        )

        tolerance = _DELTA_DRIFT_TOLERANCE
        drifts_raw: list[tuple[UUID, Decimal, Decimal, Decimal]] = []

        for pid, expected in expected_delta.items():
            before = net_positions_before.get(pid, Decimal("0"))
            after = positions_after.get(pid, Decimal("0"))
            actual = after - before
            drift = actual - expected
            if abs(drift) > tolerance:
                drifts_raw.append((pid, expected, actual, drift))

        if not drifts_raw:
            return

        # Best-effort enrichment: participant pids + equivalent code for downstream SSE.
        pid_rows = (
            await self.session.execute(
                select(Participant.id, Participant.pid).where(
                    Participant.id.in_([pid for pid, *_ in drifts_raw])
                )
            )
        ).all()
        uuid_to_pid = {row.id: str(row.pid) for row in pid_rows}
        eq_code = (
            await self.session.execute(
                select(Equivalent.code).where(Equivalent.id == equivalent_id)
            )
        ).scalar_one_or_none()
        eq_code_str = str(eq_code or equivalent_id)

        drifts_list: list[dict[str, Any]] = []
        for pid, expected, actual, drift in drifts_raw:
            participant_pid = uuid_to_pid.get(pid)
            drifts_list.append(
                {
                    "participant_id": str(participant_pid or pid),
                    "participant_uuid": str(pid),
                    "expected_delta": str(expected),
                    "actual_delta": str(actual),
                    "drift": str(drift),
                }
            )

        total_drift = sum(abs(d) for *_pid, _e, _a, d in drifts_raw) / Decimal("2")

        from app.utils.exceptions import IntegrityViolationException

        raise IntegrityViolationException(
            "Per-participant delta check failed",
            details={
                "invariant": "PAYMENT_DELTA_DRIFT",
                "source": "delta_check",
                "equivalent": eq_code_str,
                "equivalent_id": str(equivalent_id),
                "total_drift": str(total_drift),
                "drifts": drifts_list,
            },
        )
