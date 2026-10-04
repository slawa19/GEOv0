"""The money boundary: the stop/hold guard, the payment delta check and THE LINE LOCKS of the money writers.

027 stage 2 (`T2704`, 2026-10-03) replaced 019's protocol (`FORK-2` SERIALIZABLE for every money writer, `T1909`
`KEEP-EQUIVALENT-LOCK` - one advisory lock per equivalent, shared for payments, exclusive for the clearing on a
pinned connection) with READ COMMITTED and row locks: "lock what you check, read after the lock". History - the
019 and 024 specs, which keep their decisions as written.

* **Lines of pairs** (`lock_pair_lines`): every money writer locks `FOR UPDATE` every non-closed (`active` or
  `frozen`) line of EVERY pair whose debt it reads or writes, both directions, in ONE global order (`ORDER BY
  trust_lines.id`) over its whole set, and only then reads a debt, a limit, a policy or a close request of those
  pairs. A debt of a pair exists only beside a live line of that pair (spec 027, "Долг на паре без незакрытой
  линии"), so two writers of one pair always meet on a line row, and disjoint pairs never meet at all. A staged
  phase that does not know its routes in advance locks the lines among its run's participants
  (`lock_lines_among`) before its first payment. Decrease-only writers (the clearing) also lock their debt rows
  `FOR UPDATE`, after the lines.
* **The equivalent row** `FOR SHARE` (`refuse_inactive_equivalents`) after the lines and before the debts: the
  operator stop, the integrity hold and the equivalent `DELETE` update it, and the reconciliation baseline takes it
  `FOR NO KEY UPDATE`, so each waits for the writers in flight and keeps new ones out.
* **The isolation** of a writer's transaction is READ COMMITTED (`require_read_committed`): every statement after a
  lock wait reads what the lock holder committed. REPEATABLE READ and SERIALIZABLE take their snapshot BEFORE the
  wait, and a snapshot older than the lock reads a debt the holder has since changed.

There are no advisory locks, no reservations (`prepare_locks` is dropped by migration `031`) and no pinned clearing
connection. The ONE ORDER: lines (sorted by id) -> the equivalent row -> the debt rows. A writer that adds a line
lock later in its transaction (a stale route, an inject effect on a pair it did not name) may meet a deadlock
(`40P01`); its owner retries the whole transaction.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
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
# Nor was it a concurrency mitigation, though it looks like one: since 027 stage 2 both reads of the net
# positions cover only the operation's pairs, whose lines this transaction holds `FOR UPDATE`, so no foreign
# write lands between them (until then one SERIALIZABLE snapshot said the same). And a race would produce a
# drift the size of the other payment, not one atom - a mitigation shaped like this absorbs the smallest case and nothing
# larger, which is a threshold, not a safeguard.
#
# It is a module constant rather than a local so that it can be named in a test: the 012
# counter-check widens it deliberately, to reach the ledger state `F-012-1` is about now that this
# barrier catches the 1e-9 drift a widened door produces.
_DELTA_DRIFT_TOLERANCE = Decimal("0")

#: `details.reason` of the isolation refusal (027 stage 2, `T2704`; until then 019 `T1907`'s
#: `isolation_not_serializable`).
ISOLATION_NOT_READ_COMMITTED_REASON = "isolation_not_read_committed"


class IsolationNotReadCommitted(GeoException):
    """A money writer was handed a transaction that does not run READ COMMITTED; nothing was written.

    An internal error (`E010`, 500) and deliberately not a conflict: no retry of the same request on the
    same kind of session can succeed, and the caller that supplied the session is the defect.
    """

    def __init__(self, *, writer: str, isolation: str):
        super().__init__(
            details={
                "reason": ISOLATION_NOT_READ_COMMITTED_REASON,
                "writer": writer,
                "isolation": isolation,
            }
        )


class MoneyBoundary:
    """The line locks, the isolation check, the stop/hold guard and the delta check over one session."""

    def __init__(self, session: AsyncSession):
        self.session = session

    #: The columns a writer decides from, in the order `PaymentService._segment` unpacks them.
    _LINE = (TrustLine.from_participant_id, TrustLine.limit, TrustLine.policy, TrustLine.close_requested_at,
             TrustLine.id, TrustLine.status, TrustLine.to_participant_id, TrustLine.equivalent_id)

    @staticmethod
    def lock_budget_ms() -> int:
        """The bound of a staged, inject or clearing lock wait (as 019's advisory budget): min(total, commit)."""
        from app.config import settings

        return max(1, int(min(settings.PAYMENT_TOTAL_TIMEOUT_SECONDS or 10, settings.COMMIT_TIMEOUT_SECONDS or 5) * 1000))

    async def _locking(self, stmt, timeout_ms: int | None) -> list:
        """Run a locking statement, its wait bounded by `timeout_ms` (`55P03` past it); the caller's own
        `lock_timeout` is restored afterwards."""
        if timeout_ms is None:
            return list((await self.session.execute(stmt)).all())
        previous = await self.session.scalar(text("SHOW lock_timeout"))
        set_timeout = text("SELECT set_config('lock_timeout', :t, true)")
        await self.session.execute(set_timeout, {"t": f"{max(1, timeout_ms)}ms"})
        rows = list((await self.session.execute(stmt)).all())
        await self.session.execute(set_timeout, {"t": str(previous)})
        return rows

    async def lock_pair_lines(self, pairs: Iterable[tuple[UUID, UUID, UUID]], *, timeout_ms: int | None = None) -> list:
        """Lock every non-closed line of each pair `(equivalent_id, a, b)`, both directions, `FOR UPDATE`, in
        `trust_lines.id` order, in ONE statement - before any debt of these pairs is read (027 stage 2) - and
        RETURN the locked rows (`_LINE`). A writer decides ONLY from them: a line not among them does not exist
        for its transaction (§15 review of stage 2, P1 - a line created after the lock was read unlocked).

        PostgreSQL locks the rows of `ORDER BY ... FOR UPDATE` in the sorted order, so two writers taking
        overlapping sets this way cannot deadlock each other. Under READ COMMITTED a row that another writer
        closed while this one waited is re-checked and left out (`status != 'closed'`)."""

        keys = sorted({(e, x, y) for e, a, b in pairs for x, y in ((a, b), (b, a))}, key=str)
        if not keys:
            return []
        return await self._locking(
            select(*self._LINE)
            .where(tuple_(TrustLine.equivalent_id, TrustLine.from_participant_id,
                          TrustLine.to_participant_id).in_(keys), TrustLine.status != "closed")
            .order_by(TrustLine.id)
            .with_for_update(),
            timeout_ms,
        )

    async def lock_lines_among(
        self, equivalent_ids: Iterable[UUID], participant_ids: Iterable[UUID], *, timeout_ms: int | None = None
    ) -> None:
        """A staged money phase's COMPLETE set, taken before its first payment (027 stage 2): every non-closed line
        between two of `participant_ids` (the run's perimeter, which confines its routes) in `equivalent_ids`,
        `FOR UPDATE`, in `trust_lines.id` order. A line created after it (none in a money phase) is locked by the
        payment that routes over it, later and out of order - at worst a deadlock the phase's owner replays."""

        equivalents, participants = sorted(set(equivalent_ids), key=str), sorted(set(participant_ids), key=str)
        if equivalents and participants:
            await self._locking(
                select(TrustLine.id)
                .where(TrustLine.equivalent_id.in_(equivalents), TrustLine.from_participant_id.in_(participants),
                       TrustLine.to_participant_id.in_(participants), TrustLine.status != "closed")
                .order_by(TrustLine.id)
                .with_for_update(),
                timeout_ms,
            )

    @staticmethod
    async def require_read_committed(session: AsyncSession, *, writer: str) -> None:
        """Refuse, before the first write, a work transaction that does not run READ COMMITTED (027 stage 2).

        The line locks hold only if every statement after a lock wait reads what the holder committed. A
        REPEATABLE READ or SERIALIZABLE transaction took its snapshot before the wait: it reads the debt as it was
        before the holder's commit (both directions of a fresh pair end up written, `T2703` stand 1), and SSI
        does not see a READ COMMITTED partner. The application engine is fenced to READ COMMITTED (`app/config.py`),
        so this guards a session HANDED IN by a caller at another level.

        It reads the ACTUAL level (`SHOW transaction_isolation`, which opens the transaction at the level it will
        run at) on the session that writes, and never commits, rolls back or re-levels it: the only safe answer
        is to refuse. Blind spot, named: a writer not routed through a caller of this function is not covered
        (callers: spec 027, "Писатели").
        """

        level = str(await session.scalar(text("SHOW transaction_isolation")) or "").strip().lower()
        if level != "read committed":
            logger.error(
                "event=money.isolation_refused writer=%s isolation=%s", writer, level
            )
            raise IsolationNotReadCommitted(writer=writer, isolation=level)

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
    ) -> None:
        """T1544: money does not move in an equivalent the operator has deactivated - and, since
        step 5c (`T1546`), in one under an integrity hold.

        ONE STATEMENT READS BOTH: `is_active` and `integrity_hold_result_id` live on the same row, so the
        hold inherits every guarantee described below unchanged. The scheduled reaction that sets a hold
        `UPDATE`s this row, exactly like the deactivating PATCH. ONE REASON PER REFUSAL: an equivalent
        that is both inactive and held is refused as `equivalent_inactive`. Neither refusal is retryable.

        The flag is read as columns, never through an `Equivalent` instance: the payment service
        loads one before routing, and its cached `is_active` can still say True.

        The read is always `FOR SHARE`, and IT is what binds a money writer to the stop: it waits for an
        uncommitted PATCH (READ COMMITTED then reads the committed row, 027 stage 2; under 019's SERIALIZABLE it
        failed with 40001 and the retry read it), and a PATCH arriving after this read waits for the writer's
        commit. The PATCH, the hold clear, the reaction, the baseline and the equivalent `DELETE` take no other
        lock - this row lock is the whole protocol. A row that is GONE (deleted while this read waited) is refused
        as inactive. Order: the writer's line locks first, this row second, the debt rows after.

        Since 019 stage 5 (`T1907`, `FORK-7`) the clearing reads it too, in every attempt, and holds the
        row lock through its commit (`ClearingService._refuse_if_equivalent_inactive`). A `row_lock=False`
        switch - a plain read, fail-open against the stop - stood here with no caller until 2026-09-28
        and was removed by 024 `T2411`.
        """
        ids = sorted(set(equivalent_ids), key=str)
        if not ids:
            return
        stmt = select(
            Equivalent.code, Equivalent.is_active, Equivalent.integrity_hold_result_id, Equivalent.id
        ).where(Equivalent.id.in_(ids)).with_for_update(read=True)
        rows = (await self.session.execute(stmt)).all()
        gone = sorted(str(i) for i in set(ids) - {row.id for row in rows})
        inactive = sorted(str(code) for code, is_active, _hold, _id in rows if not is_active) + gone
        if inactive:
            raise self.inactive_equivalent_conflict(inactive)
        held = sorted(str(code) for code, _is_active, hold, _id in rows if hold is not None)
        if held:
            raise self.integrity_hold_conflict(held)

    async def share_equivalent_step(self, equivalent_id: UUID) -> tuple[str, int] | None:
        """`(code, precision)` of the equivalent row, read `FOR SHARE` - the accounting step a writer checks (028
        `F-028-25`). The admin PATCH takes the row `FOR NO KEY UPDATE` before it lowers the precision, so a writer
        holding this lock commits before the PATCH reads its usage, and one arriving after it reads the new step.
        Its place in the one order: after the writer's line locks, before its debt rows. None: the row is gone."""

        row = (await self.session.execute(
            select(Equivalent.code, Equivalent.precision).where(Equivalent.id == equivalent_id).with_for_update(read=True)
        )).one_or_none()
        return (str(row[0]), int(row[1])) if row is not None else None

    async def _snapshot_net_positions(
        self,
        *,
        equivalent_id: UUID,
        participant_ids: set[UUID],
        pairs: "Iterable[tuple[UUID, UUID]] | None" = None,
    ) -> dict[UUID, Decimal]:
        """Net positions (credits - debts) of participants in an equivalent - over the directed debts `pairs`
        (`(debtor, creditor)`) only, when given: the operation's own rows, whose lines its transaction holds (027
        stage 2). `None` reads every debt of the participants: correct only with no concurrent writer, since under
        READ COMMITTED a neighbour's commit on another pair of a shared participant lands between two reads."""
        if not participant_ids:
            return {}
        scope = [] if pairs is None else [or_(false(), *(and_(Debt.debtor_id == d, Debt.creditor_id == c) for d, c in pairs))]

        credits_rows = (
            await self.session.execute(
                select(Debt.creditor_id, func.sum(Debt.amount).label("total"))
                .where(
                    Debt.equivalent_id == equivalent_id,
                    Debt.creditor_id.in_(participant_ids),
                    *scope,
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
                    *scope,
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
        pairs: "Iterable[tuple[UUID, UUID]] | None" = None,
    ) -> None:
        """Verify per-participant net position deltas match applied flows - over `pairs`, the operation's directed
        debts, as `net_positions_before` was read (`_snapshot_net_positions`)."""
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
            pairs=pairs,
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
