"""The money boundary: the stop/hold guard, the payment delta check and EVERY equivalent lock primitive.

Programme 019 stage 2 (`T1903`, third consultation `FORK-2`): moved here from
`app/core/payments/engine.py` WITHOUT a change of semantics, so that stage 4 can delete the engine without
silently dropping the coordination clearing, admin, the inject, the tick and reconciliation rely on. The
primitives live here until stage 5, which removes them only on the evidence of `T1907`/`T1908`.

THE ONE LOCK ORDER - payment, clearing, admin, inject and reconciliation alike:

1. **The equivalent owner set, complete and sorted** (`_acquire_equivalent_owner_locks` - the one
   function every entry below goes through), BEFORE any row of the equivalent or its debts is read
   under a lock (`FOR SHARE`/`FOR UPDATE` of the equivalent, debt rows). Admin already takes it that
   way: `PATCH`, the hold clear and the equivalent `DELETE` take the owner lock, then touch the row.
   The reverse order is a reachable deadlock, and a deadlock retry does not replace it.
2. Then, for a payment, the transaction lock (`_acquire_tx_advisory_lock`), then the pair locks in one
   global sorted order (`_acquire_segment_advisory_lock_keys`), then rows - with the stop/hold guard
   `refuse_inactive_equivalents(row_lock=True)` reading the equivalent `FOR SHARE` only after the owner
   lock (its docstring says why a plain read is not binding for a payment).

TWO LIFETIMES, deliberately different:

* **transaction-level** - owner (`pg_advisory_xact_lock(ns, key)`), transaction and pair locks are
  released by the end of the transaction that took them, whatever ends it;
* **session-level** - the clearing interlock takes the owner key with `pg_advisory_lock(ns, key)` on a
  PINNED connection (`acquire_session_equivalent_owner_lock`) and rolls back to get a fresh snapshot;
  the lock survives every transaction on that connection and lives until the explicit
  `release_session_equivalent_owner_lock`, which the clearing calls before returning the connection.

`acquire_staged_equivalent_owner_locks` is the entry for callers that own a larger outer transaction
(the tick, the inject, admin, reconciliation): the same sorted owner set, after which the transaction's
previous `lock_timeout` is restored so the owner-lock deadline does not become the timeout policy of the
caller's later statements.

KEY SPACES. Owner and transaction locks use PostgreSQL's two-int key space with a stable domain tag as
the first int; pair locks use the one-BIGINT key space. The two spaces never conflict with each other -
which is also why a one-argument `pg_advisory_*` next to a two-argument one is never the same lock.
"""

from __future__ import annotations

import hashlib
import time
from decimal import Decimal
from typing import Any, List, Tuple
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import ConflictException

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
# Nor was it a concurrency mitigation, though it looks like one: `commit` holds an advisory lock
# over the WHOLE equivalent for its unit of work, and this check is scoped to one equivalent, so no
# foreign write lands between the two snapshots. And a race would produce a drift the size of the
# other payment, not one atom - a mitigation shaped like this absorbs the smallest case and nothing
# larger, which is a threshold, not a safeguard.
#
# It is a module constant rather than a local so that it can be named in a test: the 012
# counter-check widens it deliberately, to reach the ledger state `F-012-1` is about now that this
# barrier catches the 1e-9 drift a widened door produces.
_DELTA_DRIFT_TOLERANCE = Decimal("0")

# PostgreSQL's two-int advisory-lock key space is disjoint from the one-BIGINT
# key space used by segment locks. The first int is a stable domain tag.
_TX_ADVISORY_LOCK_NAMESPACE = 0x475458
_EQUIVALENT_OWNER_LOCK_NAMESPACE = 0x474551


class MoneyBoundary:
    """The lock primitives, the stop/hold guard and the delta check over one session.

    One instance carries one advisory-lock deadline: the budget starts at the first lock this instance
    takes and is shared by every later lock it takes (`_set_local_advisory_lock_timeout`). A caller
    that wants a fresh budget per unit of work takes a fresh instance. (Until programme 019, stage 4
    the payment engine was a `MoneyBoundary`; the engine is gone, the primitives stay here until
    stage 5.)
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
    def _segment_lock_key(
        *, equivalent_id: UUID, from_participant_id: UUID, to_participant_id: UUID
    ) -> int:
        """Compute a stable BIGINT advisory lock key for a reciprocal pair.

        Lock identity is intentionally unordered because both payment directions
        mutate the same reciprocal Debt resource. Persisted flow direction remains
        unchanged. The first 8 SHA-256 bytes form a signed Postgres BIGINT.
        """
        participant_a, participant_b = sorted(
            (from_participant_id.bytes, to_participant_id.bytes)
        )
        digest = hashlib.sha256(
            equivalent_id.bytes + participant_a + participant_b
        ).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    @staticmethod
    def _tx_lock_key(tx_id: str) -> int:
        """Compute a stable signed INT key inside the transaction-lock domain."""
        digest = hashlib.sha256(str(tx_id).encode("utf-8")).digest()
        return int.from_bytes(digest[:4], byteorder="big", signed=True)

    @staticmethod
    def _equivalent_owner_lock_key(equivalent_id: UUID) -> int:
        """Compute a stable signed INT key inside the equivalent-owner domain."""
        digest = hashlib.sha256(equivalent_id.bytes).digest()
        return int.from_bytes(digest[:4], byteorder="big", signed=True)

    async def _acquire_equivalent_owner_locks(
        self,
        equivalent_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
    ) -> None:
        """Acquire the complete equivalent owner set in one global order."""
        keys = sorted(
            {
                self._equivalent_owner_lock_key(equivalent_id)
                for equivalent_id in equivalent_ids
            }
        )
        for key in keys:
            await self._set_local_advisory_lock_timeout()
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
                {
                    "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                    "key": key,
                },
            )

    async def acquire_staged_equivalent_owner_locks(
        self,
        equivalent_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
    ) -> None:
        """Acquire a caller-owned staged batch's complete equivalent set."""
        previous_lock_timeout = await self.session.scalar(text("SHOW lock_timeout"))
        acquired = False
        try:
            await self._acquire_equivalent_owner_locks(equivalent_ids)
            acquired = True
        finally:
            # Staged callers own a larger outer transaction. The payment owner-lock
            # deadline must not become the timeout policy for later clearing,
            # drift or persistence statements in that transaction. If acquisition
            # itself fails/cancels, the outer owner rolls back the unusable UoW.
            if acquired:
                await self.session.execute(
                    text(
                        "SELECT set_config('lock_timeout', :lock_timeout, true)"
                    ),
                    {"lock_timeout": str(previous_lock_timeout)},
                )

    async def acquire_session_equivalent_owner_lock(
        self,
        equivalent_id: UUID,
    ) -> None:
        """Acquire the shared owner identity beyond a transaction rollback.

        Clearing pins the physical connection, rolls back the acquisition
        transaction to obtain a fresh SERIALIZABLE snapshot, and explicitly
        releases this session-level lock before returning the connection.
        """
        await self._set_local_advisory_lock_timeout()
        await self.session.execute(
            text("SELECT pg_advisory_lock(:namespace, :key)"),
            {
                "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                "key": self._equivalent_owner_lock_key(equivalent_id),
            },
        )

    async def release_session_equivalent_owner_lock(
        self,
        equivalent_id: UUID,
    ) -> bool:
        """Release one session-level owner lock from a pinned connection."""
        return bool(
            await self.session.scalar(
                text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {
                    "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                    "key": self._equivalent_owner_lock_key(equivalent_id),
                },
            )
        )

    async def _acquire_tx_advisory_lock(self, tx_id: str) -> None:
        """Serialize all state transitions for one tx before authoritative reads."""
        await self._set_local_advisory_lock_timeout()
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
            {
                "namespace": _TX_ADVISORY_LOCK_NAMESPACE,
                "key": self._tx_lock_key(tx_id),
            },
        )

    async def _acquire_segment_advisory_locks(
        self,
        *,
        equivalent_id: UUID,
        routes: List[Tuple[List[str], Decimal]],
        participant_map: dict[str, UUID],
    ) -> None:
        keys: set[int] = set()
        for path, _route_amount in routes:
            for i in range(len(path) - 1):
                sender_id = participant_map[path[i]]
                receiver_id = participant_map[path[i + 1]]
                keys.add(
                    self._segment_lock_key(
                        equivalent_id=equivalent_id,
                        from_participant_id=sender_id,
                        to_participant_id=receiver_id,
                    )
                )

        await self._acquire_segment_advisory_lock_keys(keys)

    async def _acquire_segment_advisory_lock_keys(
        self,
        keys: set[int] | list[int] | tuple[int, ...],
    ) -> None:
        """Acquire unique segment keys in one global deadlock-safe order."""
        for key in sorted(set(keys)):
            await self._set_local_advisory_lock_timeout()
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": key},
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
        hold inherits every guarantee described below unchanged - the same `FOR SHARE`, the same owner
        lock order, the same fresh post-lock snapshot in clearing. The scheduled reaction that sets a
        hold holds the owner lock through its commit, exactly like the deactivating PATCH. ONE REASON
        PER REFUSAL: an equivalent that is both inactive and held is refused as `equivalent_inactive`.
        Neither refusal is retryable.

        The flag is read as columns, never through an `Equivalent` instance: the payment service
        loads one before routing, and its cached `is_active` can still say True.

        `row_lock=True` is for the payment COMMIT and renders `FOR SHARE` on PostgreSQL. It is what
        binds the payment/PATCH race, and the advisory owner lock does not: the application runs at
        SERIALIZABLE, a commit takes its snapshot before it waits on that lock, and a plain read
        after the wait still returns the value from before the PATCH committed (measured
        2026-09-13, `FOR KEY SHARE` equally stale). `FOR SHARE` instead waits for an uncommitted
        PATCH and then fails with 40001, which the unit-of-work retry turns into a fresh snapshot
        that sees the stop; and a PATCH arriving after this read waits for the payment's commit.
        The caller must already hold the equivalent owner lock - owner lock first, row lock second,
        the same order as the PATCH, or the two can deadlock.

        `row_lock=False` is for clearing, whose read happens in a snapshot taken AFTER its owner
        lock, and for the PATCH lock that makes that sufficient see `admin_update_equivalent`.
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
