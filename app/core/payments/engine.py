import logging
import asyncio
import hashlib
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Tuple, Awaitable, Callable, TypeVar
from uuid import UUID

from sqlalchemy import select, and_, delete, update, func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError

from app.db.models.prepare_lock import PrepareLock
from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.db.models.audit_log import IntegrityAuditLog
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import GeoException, ConflictException, RoutingException
from app.utils.metrics import PAYMENT_EVENTS_TOTAL

from app.core.integrity import compute_integrity_checkpoint_for_equivalent

logger = logging.getLogger(__name__)


_T = TypeVar("_T")


class PaymentEngine:
    def __init__(self, session: AsyncSession):
        self.session = session
        from app.config import settings

        self.lock_ttl_seconds = settings.PREPARE_LOCK_TTL_SECONDS

        # Retry policy for SERIALIZABLE conflicts/deadlocks.
        # IMPORTANT: retry must repeat the *entire* unit-of-work (reads/checks/writes + commit),
        # not only `session.commit()`, because a rollback discards all changes.
        self._retry_attempts = settings.COMMIT_RETRY_ATTEMPTS
        self._retry_base_delay_s = settings.COMMIT_RETRY_BASE_DELAY_MS / 1000.0
        self._retry_max_delay_s = settings.COMMIT_RETRY_MAX_DELAY_MS / 1000.0

    def _is_postgres(self) -> bool:
        bind = getattr(self.session, "bind", None)
        dialect = getattr(getattr(bind, "dialect", None), "name", None)
        return dialect in {"postgresql", "postgres"}

    @staticmethod
    def _segment_lock_key(
        *, equivalent_id: UUID, from_participant_id: UUID, to_participant_id: UUID
    ) -> int:
        """Compute a stable BIGINT advisory lock key for a segment.

        Key material: equivalent UUID + from UUID + to UUID (bytes), hashed via SHA-256.
        Uses first 8 bytes as signed big-endian int (Postgres BIGINT).
        """
        digest = hashlib.sha256(
            equivalent_id.bytes + from_participant_id.bytes + to_participant_id.bytes
        ).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    async def _acquire_segment_advisory_locks(
        self,
        *,
        equivalent_id: UUID,
        routes: List[Tuple[List[str], Decimal]],
        participant_map: dict[str, UUID],
    ) -> None:
        if not self._is_postgres():
            return

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

        for key in sorted(keys):
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": key},
            )

    def _is_retryable_db_error(self, exc: BaseException) -> bool:
        if not isinstance(exc, DBAPIError):
            return False

        if not self._is_postgres():
            return False

        orig = getattr(exc, "orig", None)
        # asyncpg uses `sqlstate`, psycopg2 uses `pgcode`.
        sqlstate = (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or getattr(orig, "code", None)
        )
        # 40P01: deadlock_detected, 40001: serialization_failure
        return sqlstate in {"40P01", "40001"}

    def _get_pgcode(self, exc: BaseException) -> str | None:
        if not isinstance(exc, DBAPIError):
            return None
        orig = getattr(exc, "orig", None)
        return (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or getattr(orig, "code", None)
        )

    async def _run_uow_with_retry(
        self,
        *,
        op: str,
        fn: Callable[[], Awaitable[_T]],
        use_savepoint: bool = False,
    ) -> _T:
        """Retry wrapper for SERIALIZABLE/deadlock errors.

        Policy:
        - Catch DBAPIError with Postgres sqlstate 40001/40P01.
        - Rollback.
        - Exponential backoff with jitter, bounded.
        - Re-run the whole unit-of-work `fn()`.
        """

        attempt = 0
        while True:
            try:
                if use_savepoint:
                    async with self.session.begin_nested():
                        return await fn()
                return await fn()
            except DBAPIError as exc:
                attempt += 1

                # Only rollback when we are actually going to retry.
                # For non-retryable DBAPIError (or on non-Postgres backends), rolling back
                # inside a surrounding transaction context manager can close that context
                # and cause follow-up errors like:
                # "Can't operate on closed transaction inside context manager".
                is_retryable = self._is_retryable_db_error(exc)
                if attempt >= self._retry_attempts or not is_retryable:
                    raise

                if not use_savepoint:
                    try:
                        await self.session.rollback()
                    except Exception:
                        pass

                base = max(0.0, self._retry_base_delay_s)
                cap = max(base, self._retry_max_delay_s)
                delay = min(cap, base * (2 ** (attempt - 1)))
                # Small jitter (0..25%) to avoid thundering herd.
                delay = delay * (1.0 + 0.25 * random.random())

                logger.warning(
                    "event=payment.uow_retry op=%s attempt=%s/%s delay_s=%.3f pgcode=%s",
                    op,
                    attempt,
                    self._retry_attempts,
                    delay,
                    self._get_pgcode(exc),
                )
                await asyncio.sleep(delay)

    async def _get_tx(self, tx_id: str) -> Transaction | None:
        return (
            await self.session.execute(
                select(Transaction).where(Transaction.tx_id == tx_id)
            )
        ).scalar_one_or_none()

    async def prepare(
        self,
        tx_id: str,
        path: List[str],
        amount: Decimal,
        equivalent_id: UUID,
        *,
        commit: bool = True,
    ):
        """
        Phase 1: Prepare
        Create locks on all segments of the path.
        Checks capacity and ensures no double spending (via locks).

        path: List of PIDs, e.g., ['A', 'B', 'C']
        """
        async def _uow() -> bool:
            logger.info(
                "event=payment.prepare tx_id=%s path=%s amount=%s", tx_id, path, amount
            )
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="start").inc()
            except Exception:
                pass

            tx = await self._get_tx(tx_id)
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")

            if tx.state == "COMMITTED":
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="prepare", result="already_committed"
                    ).inc()
                except Exception:
                    pass
                return True
            if tx.state in {"ABORTED", "REJECTED"}:
                raise ConflictException(f"Transaction {tx_id} is {tx.state}")

            # 1. Resolve PIDs to UUIDs
            pids = set(path)
            stmt = select(Participant).where(Participant.pid.in_(pids))
            result = await self.session.execute(stmt)
            participants = {p.pid: p for p in result.scalars().all()}

            if len(participants) != len(pids):
                missing = pids - set(participants.keys())
                raise GeoException(f"Participants not found: {missing}")

            participant_map = {pid: p.id for pid, p in participants.items()}

            # 2. Iterate through path segments
            locks_to_create = []

            # We need to lock resources. In MVP, we use PrepareLock table.
            # Idempotency: if locks exist and tx is already prepared, treat prepare as no-op.
            stmt = select(PrepareLock).where(PrepareLock.tx_id == tx_id)
            result = await self.session.execute(stmt)
            existing_locks = result.scalars().all()
            if existing_locks:
                if tx.state == "PREPARED":
                    try:
                        PAYMENT_EVENTS_TOTAL.labels(
                            event="prepare", result="already_prepared"
                        ).inc()
                    except Exception:
                        pass
                    return True
                raise ConflictException(
                    f"Transaction {tx_id} already has locks but state={tx.state}"
                )

            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.lock_ttl_seconds
            )

            for i in range(len(path) - 1):
                sender_pid = path[i]
                receiver_pid = path[i + 1]

                sender_id = participant_map[sender_pid]
                receiver_id = participant_map[receiver_pid]

                # For payment flow Sender -> Receiver, capacity is controlled by TrustLine (Receiver -> Sender)
                tl_stmt = select(TrustLine).where(
                    and_(
                        TrustLine.from_participant_id == receiver_id,
                        TrustLine.to_participant_id == sender_id,
                        TrustLine.equivalent_id == equivalent_id,
                        TrustLine.status == "active",
                    )
                )
                tl_res = await self.session.execute(tl_stmt)
                trustline = tl_res.scalar_one_or_none()

                limit = trustline.limit if trustline else Decimal("0")

                # Fetch Debts
                debt_r_s_stmt = select(Debt).where(
                    and_(
                        Debt.debtor_id == receiver_id,
                        Debt.creditor_id == sender_id,
                        Debt.equivalent_id == equivalent_id,
                    )
                )
                debt_r_s = (
                    await self.session.execute(debt_r_s_stmt)
                ).scalar_one_or_none()
                amount_r_owes_s = debt_r_s.amount if debt_r_s else Decimal("0")

                debt_s_r_stmt = select(Debt).where(
                    and_(
                        Debt.debtor_id == sender_id,
                        Debt.creditor_id == receiver_id,
                        Debt.equivalent_id == equivalent_id,
                    )
                )
                debt_s_r = (
                    await self.session.execute(debt_s_r_stmt)
                ).scalar_one_or_none()
                amount_s_owes_r = debt_s_r.amount if debt_s_r else Decimal("0")

                # Reserved usage from other prepared transactions on this edge.
                locks_query = select(PrepareLock).where(
                    and_(
                        PrepareLock.participant_id == sender_id,
                        PrepareLock.expires_at > func.now(),
                    )
                )
                relevant_locks = (
                    await self.session.execute(locks_query)
                ).scalars().all()

                reserved_usage = Decimal("0")
                for lock in relevant_locks:
                    if lock.tx_id == tx_id:
                        continue
                    for flow in (lock.effects or {}).get("flows", []):
                        try:
                            if UUID(flow["equivalent"]) != equivalent_id:
                                continue
                            f_s = UUID(flow["from"])
                            f_r = UUID(flow["to"])
                            f_a = Decimal(str(flow["amount"]))
                        except Exception:
                            continue
                        if f_s == sender_id and f_r == receiver_id:
                            reserved_usage += f_a

                available_capacity = limit - amount_s_owes_r + amount_r_owes_s

                if available_capacity < (amount + reserved_usage):
                    raise RoutingException(
                        f"Insufficient capacity between {sender_pid} and {receiver_pid}. "
                        f"Available: {available_capacity}, Needed: {amount}, Reserved: {reserved_usage}",
                        insufficient_capacity=True,
                        details={
                            "available": str(available_capacity),
                            "needed": str(amount),
                            "reserved": str(reserved_usage),
                            "from": sender_pid,
                            "to": receiver_pid,
                        },
                    )

                lock = PrepareLock(
                    tx_id=tx_id,
                    participant_id=sender_id,
                    effects={
                        "flows": [
                            {
                                "from": str(sender_id),
                                "to": str(receiver_id),
                                "amount": str(amount),
                                "equivalent": str(equivalent_id),
                            }
                        ]
                    },
                    expires_at=expires_at,
                )
                locks_to_create.append(lock)

            # 4. Save all locks
            self.session.add_all(locks_to_create)
            await self.session.execute(
                update(Transaction)
                .where(Transaction.tx_id == tx_id)
                .values(state="PREPARED", updated_at=func.now())
            )

            if commit:
                await self.session.commit()
            else:
                await self.session.flush()

            logger.info("event=payment.prepared tx_id=%s", tx_id)
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="success").inc()
            except Exception:
                pass
            return True

        if not commit:
            return await self._run_uow_with_retry(op="prepare_nocommit", fn=_uow, use_savepoint=True)
        return await self._run_uow_with_retry(op="prepare", fn=_uow)

    async def prepare_routes(
        self,
        tx_id: str,
        routes: List[Tuple[List[str], Decimal]],
        equivalent_id: UUID,
        *,
        commit: bool = True,
    ):
        """Prepare multiple routes for a single payment transaction.

        Creates segment locks for each route with the per-route amount.
        """
        async def _uow() -> bool:
            logger.info(
                "event=payment.prepare_multipath tx_id=%s routes=%s",
                tx_id,
                len(routes),
            )
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="start").inc()
            except Exception:
                pass

            tx = await self._get_tx(tx_id)
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")

            if tx.state == "COMMITTED":
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="prepare", result="already_committed"
                    ).inc()
                except Exception:
                    pass
                return True
            if tx.state in {"ABORTED", "REJECTED"}:
                raise ConflictException(f"Transaction {tx_id} is {tx.state}")

            # Idempotency: if locks exist and tx is already prepared, treat prepare as no-op.
            existing_locks = (
                (
                    await self.session.execute(
                        select(PrepareLock).where(PrepareLock.tx_id == tx_id)
                    )
                )
                .scalars()
                .all()
            )
            if existing_locks:
                if tx.state == "PREPARED":
                    try:
                        PAYMENT_EVENTS_TOTAL.labels(
                            event="prepare", result="already_prepared"
                        ).inc()
                    except Exception:
                        pass
                    return True
                raise ConflictException(
                    f"Transaction {tx_id} already has locks but state={tx.state}"
                )

            # Resolve PIDs to UUIDs for all routes.
            pids: set[str] = set()
            for path, route_amount in routes:
                if route_amount <= 0:
                    raise GeoException("Route amount must be positive")
                if len(path) < 2:
                    raise GeoException("Route path must include at least 2 participants")
                pids.update(path)

            stmt = select(Participant).where(Participant.pid.in_(pids))
            result = await self.session.execute(stmt)
            participants = {p.pid: p for p in result.scalars().all()}

            if len(participants) != len(pids):
                missing = pids - set(participants.keys())
                raise GeoException(f"Participants not found: {missing}")

            participant_map = {pid: p.id for pid, p in participants.items()}

            # FIX-016: serialize prepare on segments to prevent oversubscription races.
            # Advisory locks are Postgres-only; other backends run best-effort.
            await self._acquire_segment_advisory_locks(
                equivalent_id=equivalent_id,
                routes=routes,
                participant_map=participant_map,
            )

            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.lock_ttl_seconds
            )

            # PrepareLock has UNIQUE(tx_id, participant_id), so we must aggregate multiple segment flows
            # per participant into a single lock.
            flows_by_participant: dict[UUID, list[dict]] = {}

            # Track reservations created in this prepare call to avoid overcommitting shared edges.
            local_reserved: dict[tuple[UUID, UUID, UUID], Decimal] = {}

            for path, route_amount in routes:
                for i in range(len(path) - 1):
                    sender_pid = path[i]
                    receiver_pid = path[i + 1]
                    sender_id = participant_map[sender_pid]
                    receiver_id = participant_map[receiver_pid]

                    # TrustLine enabling S -> R is TL R -> S.
                    trustline = (
                        await self.session.execute(
                            select(TrustLine).where(
                                and_(
                                    TrustLine.from_participant_id == receiver_id,
                                    TrustLine.to_participant_id == sender_id,
                                    TrustLine.equivalent_id == equivalent_id,
                                    TrustLine.status == "active",
                                )
                            )
                        )
                    ).scalar_one_or_none()

                    limit = trustline.limit if trustline else Decimal("0")

                    # Debts
                    debt_r_s = (
                        await self.session.execute(
                            select(Debt).where(
                                and_(
                                    Debt.debtor_id == receiver_id,
                                    Debt.creditor_id == sender_id,
                                    Debt.equivalent_id == equivalent_id,
                                )
                            )
                        )
                    ).scalar_one_or_none()
                    amount_r_owes_s = debt_r_s.amount if debt_r_s else Decimal("0")

                    debt_s_r = (
                        await self.session.execute(
                            select(Debt).where(
                                and_(
                                    Debt.debtor_id == sender_id,
                                    Debt.creditor_id == receiver_id,
                                    Debt.equivalent_id == equivalent_id,
                                )
                            )
                        )
                    ).scalar_one_or_none()
                    amount_s_owes_r = debt_s_r.amount if debt_s_r else Decimal("0")

                    # Reserved usage from other prepared transactions on this edge (same equivalent).
                    locks_query = select(PrepareLock).where(
                        and_(
                            PrepareLock.participant_id == sender_id,
                            PrepareLock.expires_at > func.now(),
                        )
                    )
                    relevant_locks = (
                        (await self.session.execute(locks_query)).scalars().all()
                    )
                    reserved_usage = Decimal("0")
                    for lock in relevant_locks:
                        if lock.tx_id == tx_id:
                            continue
                        for flow in (lock.effects or {}).get("flows", []):
                            try:
                                if UUID(flow["equivalent"]) != equivalent_id:
                                    continue
                                f_s = UUID(flow["from"])
                                f_r = UUID(flow["to"])
                                f_a = Decimal(str(flow["amount"]))
                            except Exception:
                                continue
                            if f_s == sender_id and f_r == receiver_id:
                                reserved_usage += f_a

                    local_key = (sender_id, receiver_id, equivalent_id)
                    reserved_usage += local_reserved.get(local_key, Decimal("0"))

                    available_capacity = limit - amount_s_owes_r + amount_r_owes_s
                    if available_capacity < (route_amount + reserved_usage):
                        raise RoutingException(
                            f"Insufficient capacity between {sender_pid} and {receiver_pid}. "
                            f"Available: {available_capacity}, Needed: {route_amount}, Reserved: {reserved_usage}",
                            insufficient_capacity=True,
                            details={
                                "available": str(available_capacity),
                                "needed": str(route_amount),
                                "reserved": str(reserved_usage),
                                "from": sender_pid,
                                "to": receiver_pid,
                            },
                        )

                    flow = {
                        "from": str(sender_id),
                        "to": str(receiver_id),
                        "amount": str(route_amount),
                        "equivalent": str(equivalent_id),
                    }
                    flows_by_participant.setdefault(sender_id, []).append(flow)
                    local_reserved[local_key] = (
                        local_reserved.get(local_key, Decimal("0")) + route_amount
                    )

            locks_to_create = [
                PrepareLock(
                    tx_id=tx_id,
                    participant_id=participant_id,
                    effects={"flows": flows},
                    expires_at=expires_at,
                )
                for participant_id, flows in flows_by_participant.items()
            ]
            self.session.add_all(locks_to_create)
            await self.session.execute(
                update(Transaction)
                .where(Transaction.tx_id == tx_id)
                .values(state="PREPARED", updated_at=func.now())
            )
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            logger.info("event=payment.prepared tx_id=%s multipath=true", tx_id)
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="success").inc()
            except Exception:
                pass
            return True

        if not commit:
            return await self._run_uow_with_retry(op="prepare_routes_nocommit", fn=_uow, use_savepoint=True)
        return await self._run_uow_with_retry(op="prepare_routes", fn=_uow)

    async def commit(self, tx_id: str, *, commit: bool = True):
        """
        Phase 2: Commit
        Apply changes to Debt/TrustLine based on locks.
        Remove locks.
        Update Transaction to COMMITTED.
        """
        async def _uow() -> bool:
            logger.info("event=payment.commit tx_id=%s", tx_id)
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="commit", result="start").inc()
            except Exception:
                pass

            tx = await self._get_tx(tx_id)
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")

            if tx.state == "COMMITTED":
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="commit", result="already_committed"
                    ).inc()
                except Exception:
                    pass
                return True
            if tx.state in {"ABORTED", "REJECTED"}:
                raise ConflictException(f"Transaction {tx_id} is {tx.state}")
            if tx.state != "PREPARED":
                raise ConflictException(
                    f"Transaction {tx_id} is not prepared (state={tx.state})"
                )

            # 1. Load Locks
            stmt = select(PrepareLock).where(PrepareLock.tx_id == tx_id)
            result = await self.session.execute(stmt)
            locks = result.scalars().all()

            if not locks:
                raise GeoException(f"No locks found for transaction {tx_id}")

            # FIX-014: capture integrity checksums before applying flows.
            checkpoints_before: dict[UUID, object] = {}
            try:
                affected_eq_ids = {
                    UUID(flow["equivalent"])
                    for lock in locks
                    for flow in (lock.effects or {}).get("flows", [])
                    if isinstance(flow, dict) and "equivalent" in flow
                }
                for eq_id in affected_eq_ids:
                    try:
                        checkpoints_before[eq_id] = (
                            await compute_integrity_checkpoint_for_equivalent(
                                self.session,
                                equivalent_id=eq_id,
                            )
                        )
                    except Exception:
                        continue
            except Exception:
                checkpoints_before = {}

            # 1a. TTL validation: any expired lock aborts the transaction.
            expired_lock = (
                await self.session.execute(
                    select(PrepareLock.id)
                    .where(
                        and_(
                            PrepareLock.tx_id == tx_id,
                            PrepareLock.expires_at <= func.now(),
                        )
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if expired_lock:
                await self.abort(tx_id, reason="Prepare locks expired before commit", commit=commit)
                raise ConflictException(f"Transaction {tx_id} expired before commit")

            # 2. Process each lock (segment)
            flows_by_equivalent: dict[UUID, list[tuple[UUID, UUID, Decimal]]] = {}
            affected_pids_by_equivalent: dict[UUID, set[UUID]] = {}
            flows_parsed_by_lock: list[list[tuple[UUID, UUID, Decimal, UUID]]] = []

            for lock in locks:
                parsed: list[tuple[UUID, UUID, Decimal, UUID]] = []
                raw_effects = lock.effects or {}
                raw_flows = raw_effects.get("flows", []) if isinstance(raw_effects, dict) else []
                if isinstance(raw_flows, list):
                    for flow in raw_flows:
                        if not isinstance(flow, dict):
                            continue
                        try:
                            from_id = UUID(flow["from"])
                            to_id = UUID(flow["to"])
                            amount = Decimal(flow["amount"])
                            equivalent_id = UUID(flow["equivalent"])
                        except Exception:
                            continue

                        parsed.append((from_id, to_id, amount, equivalent_id))
                        flows_by_equivalent.setdefault(equivalent_id, []).append(
                            (from_id, to_id, amount)
                        )
                        affected = affected_pids_by_equivalent.setdefault(equivalent_id, set())
                        affected.add(from_id)
                        affected.add(to_id)

                flows_parsed_by_lock.append(parsed)

            net_positions_before_by_equivalent: dict[UUID, dict[UUID, Decimal]] = {}
            for eq_id, pids in affected_pids_by_equivalent.items():
                net_positions_before_by_equivalent[eq_id] = await self._snapshot_net_positions(
                    equivalent_id=eq_id,
                    participant_ids=pids,
                )

            affected_pairs_by_equivalent: dict[UUID, set[tuple[UUID, UUID]]] = {}
            for parsed in flows_parsed_by_lock:
                for from_id, to_id, amount, equivalent_id in parsed:
                    pairs = affected_pairs_by_equivalent.setdefault(equivalent_id, set())
                    pairs.add((from_id, to_id))
                    pairs.add((to_id, from_id))

                    await self._apply_flow(from_id, to_id, amount, equivalent_id)

                await self.session.flush()

            # 2a. Invariants: trust limits + zero-sum smoke-check
            from app.core.invariants import InvariantChecker
            from app.utils.exceptions import IntegrityViolationException

            checker = InvariantChecker(self.session)
            try:
                for eq_id, pairs in affected_pairs_by_equivalent.items():
                    await checker.check_trust_limits(
                        equivalent_id=eq_id, participant_pairs=list(pairs)
                    )
                    await checker.check_zero_sum(equivalent_id=eq_id)
                    await checker.check_debt_symmetry(
                        equivalent_id=eq_id, participant_pairs=list(pairs)
                    )
                    await self.check_payment_delta(
                        equivalent_id=eq_id,
                        flows=flows_by_equivalent.get(eq_id, []),
                        net_positions_before=net_positions_before_by_equivalent.get(eq_id, {}),
                    )
            except IntegrityViolationException as exc:
                # IMPORTANT:
                # PaymentService may call engine methods with commit=False inside a
                # surrounding (nested) transaction (e.g. simulator real-mode tick).
                # Calling session.rollback() inside that context can close the
                # transaction while the context manager is still active, leading to:
                # "Can't operate on closed transaction inside context manager".
                if commit:
                    await self.session.rollback()
                await self.abort(
                    tx_id,
                    reason=f"Invariant violation: {exc.code}",
                    error_code=getattr(exc, "code", None),
                    details=getattr(exc, "details", None),
                    commit=commit,
                )
                raise

            # FIX-014: write integrity audit trail per equivalent (best-effort).
            try:
                payload = tx.payload or {}
                participant_pids: set[str] = set()
                for key in ("from", "to"):
                    value = payload.get(key)
                    if isinstance(value, str) and value:
                        participant_pids.add(value)

                routes = payload.get("routes")
                if isinstance(routes, list):
                    for route in routes:
                        if not isinstance(route, dict):
                            continue
                        path = route.get("path")
                        if isinstance(path, list):
                            for pid in path:
                                if isinstance(pid, str) and pid:
                                    participant_pids.add(pid)

                for eq_id in affected_pairs_by_equivalent.keys():
                    try:
                        eq_code = (
                            await self.session.execute(
                                select(Equivalent.code).where(Equivalent.id == eq_id)
                            )
                        ).scalar_one_or_none()
                        eq_code_str = str(eq_code or eq_id)

                        cp_before = checkpoints_before.get(eq_id)
                        before_sum = getattr(cp_before, "checksum", "") or ""

                        cp_after = await compute_integrity_checkpoint_for_equivalent(
                            self.session,
                            equivalent_id=eq_id,
                        )
                        after_sum = (
                            getattr(cp_after, "checksum", before_sum) or before_sum
                        )
                        invariants_status = (
                            getattr(cp_after, "invariants_status", {}) or {}
                        )
                        passed = bool(invariants_status.get("passed", True))

                        self.session.add(
                            IntegrityAuditLog(
                                operation_type="PAYMENT",
                                tx_id=tx_id,
                                equivalent_code=eq_code_str,
                                state_checksum_before=before_sum,
                                state_checksum_after=after_sum,
                                affected_participants={
                                    "participants": sorted(participant_pids)
                                },
                                invariants_checked=invariants_status.get("checks")
                                or invariants_status,
                                verification_passed=passed,
                                error_details=None if passed else invariants_status,
                            )
                        )
                    except Exception:
                        continue
            except Exception:
                pass

            # 3. Delete Locks
            delete_stmt = delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
            await self.session.execute(delete_stmt)

            # 4. Update Transaction
            update_stmt = (
                update(Transaction)
                .where(Transaction.tx_id == tx_id)
                .values(state="COMMITTED", updated_at=func.now())
            )
            await self.session.execute(update_stmt)

            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            logger.info("event=payment.committed tx_id=%s", tx_id)
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="commit", result="success").inc()
            except Exception:
                pass
            return True

        if not commit:
            return await self._run_uow_with_retry(op="commit_nocommit", fn=_uow, use_savepoint=True)
        return await self._run_uow_with_retry(op="commit", fn=_uow)

    async def _apply_flow(
        self, from_id: UUID, to_id: UUID, amount: Decimal, equivalent_id: UUID
    ):
        """
        Apply flow of `amount` from `from_id` to `to_id`.
        Logic:
          1. If receiver owes sender: reduce that debt.
          2. If remaining amount > 0: increase sender's debt to receiver.
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with self.session.begin_nested():
                    remaining_amount = amount

                    # 1. Check if Receiver owes Sender (Debt: debtor=to, creditor=from)
                    debt_r_s = await self._get_debt(to_id, from_id, equivalent_id)

                    if debt_r_s and debt_r_s.amount > 0:
                        reduction = min(remaining_amount, debt_r_s.amount)
                        debt_r_s.amount -= reduction
                        remaining_amount -= reduction

                        if debt_r_s.amount == 0:
                            await self.session.delete(debt_r_s)
                        else:
                            self.session.add(debt_r_s)

                    if remaining_amount > 0:
                        # 2. Increase Sender's debt to Receiver (Debt: debtor=from, creditor=to)
                        debt_s_r = await self._get_debt(from_id, to_id, equivalent_id)
                        if not debt_s_r:
                            # Create new debt record
                            debt_s_r = Debt(
                                debtor_id=from_id,
                                creditor_id=to_id,
                                equivalent_id=equivalent_id,
                                amount=Decimal("0"),
                            )

                        debt_s_r.amount += remaining_amount
                        self.session.add(debt_s_r)

                    # NOTE: app sessions may run with autoflush=False. Ensure the DB view is
                    # consistent before the symmetry-netting queries below.
                    await self.session.flush()

                    # Enforce debt symmetry by netting mutual debts, if any.
                    debt_forward = await self._get_debt(from_id, to_id, equivalent_id)
                    debt_reverse = await self._get_debt(to_id, from_id, equivalent_id)
                    if (
                        debt_forward
                        and debt_reverse
                        and debt_forward.amount > 0
                        and debt_reverse.amount > 0
                    ):
                        net = min(debt_forward.amount, debt_reverse.amount)
                        debt_forward.amount -= net
                        debt_reverse.amount -= net

                        if debt_forward.amount == 0:
                            await self.session.delete(debt_forward)
                        else:
                            self.session.add(debt_forward)

                        if debt_reverse.amount == 0:
                            await self.session.delete(debt_reverse)
                        else:
                            self.session.add(debt_reverse)

                        # Flush netting effects immediately so later flows / invariant checks
                        # don't observe a transient mutual-debt state.
                        await self.session.flush()

                return
            except StaleDataError:
                if attempt >= max_retries - 1:
                    raise
                logger.warning(
                    "event=apply_flow.stale_data retry=%s/%s from=%s to=%s",
                    attempt + 1,
                    max_retries,
                    str(from_id),
                    str(to_id),
                )
                try:
                    self.session.expire_all()
                except Exception:
                    pass

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

        tolerance = Decimal("0.00000001")
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

    async def _get_debt(
        self, debtor_id: UUID, creditor_id: UUID, equivalent_id: UUID
    ) -> Debt | None:
        stmt = select(Debt).where(
            and_(
                Debt.debtor_id == debtor_id,
                Debt.creditor_id == creditor_id,
                Debt.equivalent_id == equivalent_id,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def abort(
        self,
        tx_id: str,
        reason: str = "Aborted",
        *,
        commit: bool = True,
        error_code: ErrorCode | str | None = None,
        details: dict[str, Any] | None = None,
    ):
        """
        Abort transaction: Delete locks, set state to ABORTED.
        """

        def _normalize_code(value: ErrorCode | str | None) -> ErrorCode:
            if value is None:
                return ErrorCode.E010
            if isinstance(value, ErrorCode):
                return value
            try:
                return ErrorCode(str(value))
            except Exception:
                return ErrorCode.E010

        async def _uow() -> bool:
            logger.info("event=payment.abort tx_id=%s reason=%s", tx_id, reason)
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="abort", result="start").inc()
            except Exception:
                pass

            tx = await self._get_tx(tx_id)

            existing_error: dict[str, Any] = (tx.error if tx and isinstance(tx.error, dict) else {}) or {}
            normalized_code = _normalize_code(error_code or existing_error.get("code"))
            normalized_details: dict[str, Any] = (
                details
                if details is not None
                else (existing_error.get("details") if isinstance(existing_error.get("details"), dict) else {})
            ) or {}
            # Always persist a stable error schema for aborted transactions.
            error_payload: dict[str, Any] = {
                "code": normalized_code.value,
                "message": str(existing_error.get("message") or reason or ERROR_MESSAGES[normalized_code]),
                "details": normalized_details,
            }

            if tx and tx.state == "COMMITTED":
                # Safety guard: never transition a committed transaction back to ABORTED.
                # This can happen in the service layer under timeout uncertainty (commit may
                # have finished, but the caller timed out).
                delete_stmt = delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
                await self.session.execute(delete_stmt)
                if commit:
                    await self.session.commit()
                else:
                    await self.session.flush()
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="abort", result="already_committed"
                    ).inc()
                except Exception:
                    pass
                return True
            if tx and tx.state == "ABORTED":
                # Idempotency: ensure locks are gone as well.
                delete_stmt = delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
                await self.session.execute(delete_stmt)

                # Ensure error code is present even on idempotent aborts.
                await self.session.execute(
                    update(Transaction)
                    .where(Transaction.tx_id == tx_id)
                    .values(error=error_payload)
                )
                if commit:
                    await self.session.commit()
                else:
                    await self.session.flush()
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="abort", result="already_aborted"
                    ).inc()
                except Exception:
                    pass
                return True

            # Delete locks
            delete_stmt = delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
            await self.session.execute(delete_stmt)

            # Update Transaction
            update_stmt = (
                update(Transaction)
                .where(Transaction.tx_id == tx_id)
                .values(state="ABORTED", error=error_payload, updated_at=func.now())
            )
            await self.session.execute(update_stmt)

            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="abort", result="success").inc()
            except Exception:
                pass
            return True

        if not commit:
            return await self._run_uow_with_retry(op="abort_nocommit", fn=_uow, use_savepoint=True)
        return await self._run_uow_with_retry(op="abort", fn=_uow)
