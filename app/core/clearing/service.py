import asyncio
import logging
import uuid
from decimal import Decimal
from typing import AbstractSet, Dict, List, Set

from sqlalchemy import bindparam, select, and_, func, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.models.audit_log import IntegrityAuditLog
from app.utils.error_codes import ErrorCode
from app.utils.exceptions import GeoException, TimeoutException
from app.utils.metrics import CLEARING_EVENTS_TOTAL
from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.core.invariants import InvariantChecker
from app.core.integrity import compute_integrity_checkpoint_for_equivalent

logger = logging.getLogger(__name__)

_CLEARING_REPLAY_NAMESPACE = uuid.UUID("7438b16f-c629-4aeb-8b97-4bf113704c93")


class ClearingCommittedAfterCancellation(asyncio.CancelledError):
    """Cancellation raised only after the clearing commit became durable."""

    def __init__(self, *, tx_id: str, cleared_amount: Decimal):
        super().__init__("Clearing committed while cancellation was pending")
        self.tx_id = tx_id
        self.cleared_amount = cleared_amount


class ClearingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _raise_unexpected_execution(self, exc: Exception) -> None:
        """Rollback and surface one sanitized unexpected clearing failure."""
        logger.exception("event=clearing.failed")
        try:
            CLEARING_EVENTS_TOTAL.labels(event="execute", result="error").inc()
        except Exception:
            pass
        try:
            await self.session.rollback()
        except Exception:
            logger.exception("event=clearing.rollback_failed")
        raise GeoException() from exc

    async def _rollback_skipped_execution(self) -> None:
        """End a service-owned clearing attempt before returning a skip result."""
        try:
            await self.session.rollback()
        except Exception as exc:
            logger.exception("event=clearing.skip_rollback_failed")
            raise GeoException() from exc

    @staticmethod
    async def _drain_task(task: asyncio.Task) -> asyncio.CancelledError | None:
        """Wait through repeated caller cancellation and return its first pulse."""

        caller_cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if caller_cancellation is None:
                    caller_cancellation = exc
            except Exception:
                # The task is terminal; surface its exact result below.
                pass
        task.result()
        return caller_cancellation

    @classmethod
    async def _rollback_before_interlock(cls, session: AsyncSession) -> None:
        rollback_task = asyncio.create_task(session.rollback())
        caller_cancellation = await cls._drain_task(rollback_task)
        if caller_cancellation is not None:
            raise caller_cancellation

    @classmethod
    async def _close_checked_out_connection(
        cls,
        connection: AsyncConnection,
    ) -> asyncio.CancelledError | None:
        """Return a pre-lock connection to its pool despite caller cancellation."""

        async def _cleanup() -> None:
            try:
                await connection.close()
            except BaseException:
                # No advisory lock exists at this stage, but a connection whose
                # close status is unknown must still not return to the pool.
                await connection.invalidate()
                await connection.close()

        return await cls._drain_task(asyncio.create_task(_cleanup()))

    @classmethod
    async def _release_interlock_session(
        cls,
        work_session: AsyncSession,
        connection: AsyncConnection,
        equivalent_id: uuid.UUID,
        *,
        lock_was_acquired: bool,
    ) -> asyncio.CancelledError | None:
        """Rollback work, release the session lock, then return the connection."""

        async def _cleanup() -> None:
            try:
                await work_session.rollback()
                unlocked = await PaymentEngine(
                    work_session
                ).release_session_equivalent_owner_lock(equivalent_id)
                if lock_was_acquired and not unlocked:
                    logger.warning(
                        "event=clearing.interlock_unlock_unconfirmed"
                    )
                    await connection.invalidate()
                    return
                remaining = await work_session.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE pid = pg_backend_pid() "
                        "AND locktype = 'advisory'"
                    )
                )
                if int(remaining or 0) != 0:
                    raise RuntimeError("Clearing connection retained advisory locks")
            except BaseException:
                # Never return a connection with uncertain session-lock state.
                logger.exception("event=clearing.interlock_cleanup_invalidated")
                await connection.invalidate()
            finally:
                try:
                    await work_session.close()
                finally:
                    await connection.close()

        return await cls._drain_task(asyncio.create_task(_cleanup()))

    @staticmethod
    def _execution_tx_id(debt_ids: List[uuid.UUID]) -> str:
        canonical_debt_set = ":".join(sorted({str(debt_id) for debt_id in debt_ids}))
        return str(uuid.uuid5(_CLEARING_REPLAY_NAMESPACE, canonical_debt_set))

    @staticmethod
    async def _read_committed_execution_amount(
        session: AsyncSession,
        tx_id: str,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        transaction = (
            await session.execute(
                select(Transaction).where(
                    Transaction.tx_id == tx_id,
                    Transaction.type == "CLEARING",
                )
            )
        ).scalar_one_or_none()
        if transaction is None or transaction.state != "COMMITTED":
            return None

        # 2026-08-22 / p010, found by external review of this batch.  The replay shortcut
        # returns BEFORE the locked re-read, and therefore before the perimeter check that
        # stands on those rows -- so without this a scoped caller replaying another run's
        # cycle would be handed the foreign amount as its own success.  The recorded
        # transaction carries the participants of every edge, so it can answer for itself.
        if allowed_participant_pids is not None:
            edges = (transaction.payload or {}).get("edges")
            if not isinstance(edges, list) or not edges:
                # A payload that cannot be checked is not a payload that passes.
                logger.error(
                    "event=clearing.replay_scope_unverifiable tx_id=%s", tx_id
                )
                raise GeoException()
            touched = {
                str(edge.get(role) or "")
                for edge in edges
                if isinstance(edge, dict)
                for role in ("debtor", "creditor")
            }
            if not touched or not touched <= set(allowed_participant_pids):
                logger.error(
                    "event=clearing.replay_escaped_scope tx_id=%s", tx_id
                )
                raise GeoException()
        try:
            amount = Decimal(str((transaction.payload or {})["amount"]))
        except Exception as exc:
            logger.error("event=clearing.replay_payload_invalid tx_id=%s", tx_id)
            raise GeoException() from exc
        if amount <= 0:
            logger.error("event=clearing.replay_amount_invalid tx_id=%s", tx_id)
            raise GeoException()
        return amount

    async def _committed_execution_amount(
        self,
        tx_id: str,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        return await self._read_committed_execution_amount(
            self.session, tx_id, allowed_participant_pids=allowed_participant_pids
        )

    @staticmethod
    def _postgres_error_codes(exc: BaseException) -> set[str]:
        pending: list[BaseException] = [exc]
        seen: set[int] = set()
        codes: set[str] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            for attr in ("sqlstate", "pgcode", "code"):
                value = getattr(current, attr, None)
                if value is not None:
                    codes.add(str(value).strip())
            for linked in (
                getattr(current, "orig", None),
                current.__cause__,
                current.__context__,
            ):
                if isinstance(linked, BaseException):
                    pending.append(linked)
        return codes

    @classmethod
    def _is_retryable_concurrency_error(cls, exc: BaseException) -> bool:
        return bool(cls._postgres_error_codes(exc) & {"40001", "40P01"})

    async def _reconcile_committed_execution(
        self,
        tx_id: str,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        """Resolve one ambiguous occurrence from a fresh transaction snapshot."""
        try:
            await self.session.rollback()
        except Exception:
            logger.exception("event=clearing.reconcile_rollback_failed tx_id=%s", tx_id)

        bind = getattr(self.session, "bind", None)
        if bind is None:
            raise GeoException()
        session_factory = async_sessionmaker(
            bind=bind,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with session_factory() as recovery_session:
                    amount = await self._read_committed_execution_amount(
                        recovery_session,
                        tx_id,
                        allowed_participant_pids=allowed_participant_pids,
                    )
            except Exception as exc:
                last_error = exc
            else:
                last_error = None
                if amount is not None:
                    return amount
            if attempt < 2:
                await asyncio.sleep(0.01 * (attempt + 1))
        if last_error is not None:
            raise last_error
        return None

    async def _commit_to_terminal(
        self,
    ) -> tuple[asyncio.CancelledError | None, BaseException | None]:
        """Drain the session commit and report caller cancellation separately."""
        commit_task = asyncio.create_task(self.session.commit())
        caller_cancellation: asyncio.CancelledError | None = None
        while not commit_task.done():
            try:
                await asyncio.shield(commit_task)
            except asyncio.CancelledError as exc:
                if caller_cancellation is None:
                    caller_cancellation = exc
            except Exception:
                # The task is terminal; surface its exact result below.
                pass
        commit_error: BaseException | None = None
        try:
            commit_task.result()
        except (Exception, asyncio.CancelledError) as exc:
            commit_error = exc
        return caller_cancellation, commit_error

    def _dialect_name(self) -> str | None:
        try:
            return self.session.get_bind().dialect.name
        except Exception:
            return None

    def _is_sqlite(self) -> bool:
        return self._dialect_name() == "sqlite"

    def _bind_uuid(self, uid: uuid.UUID) -> object:
        """Return UUID in a format supported by the current DBAPI for raw SQL binds."""
        if self._is_sqlite():
            # SQLAlchemy stores UUIDs in SQLite as CHAR(32) hex (no dashes).
            return uid.hex
        return uid

    def _bind_decimal(self, val: Decimal) -> object:
        """Return Decimal in a format supported by the current DBAPI for raw SQL binds."""
        if self._is_sqlite():
            return float(val)
        return val

    def _scope_predicate(self, columns: tuple[str, ...]) -> str:
        """SQL that confines a cycle to an allowlist of participants.

        2026-08-22 / p010 (`F-010-3`).  The predicate belongs in the WHERE clause, ahead of
        `ORDER BY ... LIMIT`, and NOT in a filter applied to the result of `find_cycles`.
        The detection queries rank every cycle of the equivalent and keep only the first 100
        (triangles) or 50 (quadrangles), so a post-filter can legitimately be handed a full
        page of another run's cycles, discard all of them, and leave the caller with a
        silent "no cycles" while its own cycle sat below the cut.  That is a false green of
        exactly the kind this wave exists to remove.

        Only the unique vertices need naming: the JOINs already tie the remaining ends to
        them.
        """
        return " ".join(
            f"AND {col} IN :allowed_participant_ids" for col in columns
        )

    def _scope_binds(self, allowed_participant_ids):
        """Bind material for `_scope_predicate`, or None when the scope is not applied."""
        if allowed_participant_ids is None:
            return None
        # Raw text() needs an expanding bind for IN, and SQLite stores UUIDs as bare hex.
        return [self._bind_uuid(pid) for pid in sorted(allowed_participant_ids)]

    async def _resolve_scope_ids(self, allowed_participant_pids):
        """Resolve a pid perimeter to participant ids once, or None when not applied."""
        if allowed_participant_pids is None:
            return None
        if not allowed_participant_pids:
            return set()
        return set(
            (
                await self.session.execute(
                    select(Participant.id).where(
                        Participant.pid.in_(sorted(allowed_participant_pids))
                    )
                )
            ).scalars().all()
        )

    def _sql_auto_clearing_ok(self, alias: str) -> str:
        """Dialect-aware SQL predicate: trustline policy permits auto-clearing.

        Must match `_policy_flag(..., default=True)` semantics as closely as possible:
        - NULL policy -> allow
        - missing key -> allow
        - explicit false-ish -> reject
        """
        dialect = self._dialect_name()
        if dialect == "sqlite":
            # json_extract returns 0/1 for JSON booleans; can also surface strings.
            return (
                "("
                f"{alias}.policy IS NULL OR "
                f"json_extract({alias}.policy, '$.auto_clearing') IS NULL OR "
                f"json_extract({alias}.policy, '$.auto_clearing') NOT IN (0, 'false', '0', 'no', 'off')"
                ")"
            )

        # Postgres (json/jsonb): policy->>'auto_clearing' yields text.
        return (
            "("
            f"{alias}.policy IS NULL OR "
            f"({alias}.policy->>'auto_clearing') IS NULL OR "
            f"lower({alias}.policy->>'auto_clearing') NOT IN ('false', '0', 'no', 'off')"
            ")"
        )

    @staticmethod
    def _deduplicate_cycles(cycles: List[List[Dict]]) -> List[List[Dict]]:
        """Stable dedupe by unordered set of debt ids.

        SQL cycle queries can emit the same logical cycle multiple times (different rotation).
        Keep first occurrence to preserve ordering heuristics (e.g. clear_amount DESC).
        """
        if not cycles:
            return []

        seen: set[tuple[str, ...]] = set()
        out: List[List[Dict]] = []
        for cycle in cycles:
            try:
                key = tuple(sorted(str(e.get("debt_id", "")) for e in cycle))
            except Exception:
                key = tuple()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(cycle)
        return out

    async def find_triangles_sql(
        self,
        equivalent_id: uuid.UUID,
        *,
        allowed_participant_ids: "set[uuid.UUID] | None" = None,
    ) -> List[List[Dict]]:
        """Find 3-node debt cycles using a SQL JOIN.

        `allowed_participant_ids` confines the cycle to one run's participants; None keeps
        the historic global behaviour, which the hub and the simulator tick still rely on.
        """

        dialect = self._dialect_name()

        least_expr = "LEAST(d1.amount, d2.amount, d3.amount)"
        if dialect == "sqlite":
            # SQLite supports scalar min(x, y, z) as a LEAST replacement.
            least_expr = "min(d1.amount, d2.amount, d3.amount)"

        # NOTE: When executing raw SQL (text()), sqlite3 DBAPI does not accept uuid.UUID/Decimal
        # as bound parameters. Normalize binds for SQLite only.
        equivalent_id_param = self._bind_uuid(equivalent_id)
        min_amount_param = self._bind_decimal(Decimal("0.01"))

        # a = d1.debtor, b = d1.creditor (= d2.debtor), c = d2.creditor (= d3.debtor);
        # d3.creditor is a by the JOIN, so three columns name every vertex.
        scope_binds = self._scope_binds(allowed_participant_ids)
        scope_sql = (
            ""
            if scope_binds is None
            else self._scope_predicate(("d1.debtor_id", "d1.creditor_id", "d2.creditor_id"))
        )

        query = text(
            f"""
            SELECT DISTINCT
                d1.id as debt1_id,
                d1.debtor_id as a,
                d1.creditor_id as b,
                d1.amount as amount1,
                d2.id as debt2_id,
                d2.creditor_id as c,
                d2.amount as amount2,
                d3.id as debt3_id,
                d3.amount as amount3,
                {least_expr} as clear_amount
            FROM debts d1
            JOIN debts d2 ON d1.creditor_id = d2.debtor_id
                         AND d1.equivalent_id = d2.equivalent_id
            JOIN debts d3 ON d2.creditor_id = d3.debtor_id
                         AND d3.creditor_id = d1.debtor_id
                         AND d2.equivalent_id = d3.equivalent_id
                        JOIN trust_lines t1 ON t1.from_participant_id = d1.creditor_id
                                                            AND t1.to_participant_id = d1.debtor_id
                                                            AND t1.equivalent_id = d1.equivalent_id
                                                            AND t1.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t1')}
                        JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id
                                                            AND t2.to_participant_id = d2.debtor_id
                                                            AND t2.equivalent_id = d2.equivalent_id
                                                            AND t2.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t2')}
                        JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id
                                                            AND t3.to_participant_id = d3.debtor_id
                                                            AND t3.equivalent_id = d3.equivalent_id
                                                            AND t3.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t3')}
            WHERE d1.equivalent_id = :equivalent_id
              AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0
              AND {least_expr} > :min_amount
              {scope_sql}
            ORDER BY clear_amount DESC
            LIMIT 100
            """
        )

        params = {
            "equivalent_id": equivalent_id_param,
            "min_amount": min_amount_param,
        }
        if scope_binds is not None:
            query = query.bindparams(bindparam("allowed_participant_ids", expanding=True))
            params["allowed_participant_ids"] = scope_binds

        result = await self.session.execute(query, params)

        cycles: List[List[Dict]] = []
        for row in result:
            cycles.append(
                [
                    {
                        "debt_id": str(row.debt1_id),
                        "debtor": str(row.a),
                        "creditor": str(row.b),
                        "amount": str(row.amount1),
                    },
                    {
                        "debt_id": str(row.debt2_id),
                        "debtor": str(row.b),
                        "creditor": str(row.c),
                        "amount": str(row.amount2),
                    },
                    {
                        "debt_id": str(row.debt3_id),
                        "debtor": str(row.c),
                        "creditor": str(row.a),
                        "amount": str(row.amount3),
                    },
                ]
            )

        return cycles

    async def find_quadrangles_sql(
        self,
        equivalent_id: uuid.UUID,
        *,
        allowed_participant_ids: "set[uuid.UUID] | None" = None,
    ) -> List[List[Dict]]:
        """Find 4-node debt cycles using a SQL JOIN."""

        dialect = self._dialect_name()

        least_expr = "LEAST(d1.amount, d2.amount, d3.amount, d4.amount)"
        if dialect == "sqlite":
            least_expr = "min(d1.amount, d2.amount, d3.amount, d4.amount)"

        equivalent_id_param = self._bind_uuid(equivalent_id)
        min_amount_param = self._bind_decimal(Decimal("0.01"))

        # a = d1.debtor, b = d1.creditor (= d2.debtor), c = d2.creditor (= d3.debtor),
        # d = d3.creditor (= d4.debtor); d4.creditor is a by the JOIN.
        scope_binds = self._scope_binds(allowed_participant_ids)
        scope_sql = (
            ""
            if scope_binds is None
            else self._scope_predicate(
                ("d1.debtor_id", "d1.creditor_id", "d2.creditor_id", "d3.creditor_id")
            )
        )

        query = text(
            f"""
            SELECT DISTINCT
                d1.id as debt1_id, d1.debtor_id as a, d1.creditor_id as b, d1.amount as amt1,
                d2.id as debt2_id, d2.creditor_id as c, d2.amount as amt2,
                d3.id as debt3_id, d3.creditor_id as d, d3.amount as amt3,
                d4.id as debt4_id, d4.amount as amt4,
                {least_expr} as clear_amount
            FROM debts d1
            JOIN debts d2 ON d1.creditor_id = d2.debtor_id AND d1.equivalent_id = d2.equivalent_id
            JOIN debts d3 ON d2.creditor_id = d3.debtor_id AND d2.equivalent_id = d3.equivalent_id
            JOIN debts d4 ON d3.creditor_id = d4.debtor_id AND d4.creditor_id = d1.debtor_id
                         AND d3.equivalent_id = d4.equivalent_id
                        JOIN trust_lines t1 ON t1.from_participant_id = d1.creditor_id
                                                            AND t1.to_participant_id = d1.debtor_id
                                                            AND t1.equivalent_id = d1.equivalent_id
                                                            AND t1.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t1')}
                        JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id
                                                            AND t2.to_participant_id = d2.debtor_id
                                                            AND t2.equivalent_id = d2.equivalent_id
                                                            AND t2.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t2')}
                        JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id
                                                            AND t3.to_participant_id = d3.debtor_id
                                                            AND t3.equivalent_id = d3.equivalent_id
                                                            AND t3.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t3')}
                        JOIN trust_lines t4 ON t4.from_participant_id = d4.creditor_id
                                                            AND t4.to_participant_id = d4.debtor_id
                                                            AND t4.equivalent_id = d4.equivalent_id
                                                            AND t4.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t4')}
            WHERE d1.equivalent_id = :equivalent_id
              AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0 AND d4.amount > 0
              AND d1.debtor_id != d2.creditor_id
              AND d1.debtor_id != d3.creditor_id
              AND d1.creditor_id != d3.creditor_id
              AND {least_expr} > :min_amount
              {scope_sql}
            ORDER BY clear_amount DESC
            LIMIT 50
            """
        )

        params = {
            "equivalent_id": equivalent_id_param,
            "min_amount": min_amount_param,
        }
        if scope_binds is not None:
            query = query.bindparams(bindparam("allowed_participant_ids", expanding=True))
            params["allowed_participant_ids"] = scope_binds

        result = await self.session.execute(query, params)

        cycles: List[List[Dict]] = []
        for row in result:
            cycles.append(
                [
                    {
                        "debt_id": str(row.debt1_id),
                        "debtor": str(row.a),
                        "creditor": str(row.b),
                        "amount": str(row.amt1),
                    },
                    {
                        "debt_id": str(row.debt2_id),
                        "debtor": str(row.b),
                        "creditor": str(row.c),
                        "amount": str(row.amt2),
                    },
                    {
                        "debt_id": str(row.debt3_id),
                        "debtor": str(row.c),
                        "creditor": str(row.d),
                        "amount": str(row.amt3),
                    },
                    {
                        "debt_id": str(row.debt4_id),
                        "debtor": str(row.d),
                        "creditor": str(row.a),
                        "amount": str(row.amt4),
                    },
                ]
            )

        return cycles

    async def _cycle_respects_auto_clearing(self, debts: List[Debt]) -> bool:
        """Return True if every cycle edge has consent for auto clearing.

        For each debt edge debtor->creditor, the controlling trustline is creditor->debtor
        (i.e. the creditor's line of trust/limit towards the debtor).
        """
        if not debts:
            return False

        equivalent_id = debts[0].equivalent_id
        required_pairs: set[tuple[uuid.UUID, uuid.UUID]] = {
            (d.creditor_id, d.debtor_id) for d in debts
        }

        from_ids = {p[0] for p in required_pairs}
        to_ids = {p[1] for p in required_pairs}

        trustlines = (
            (
                await self.session.execute(
                    select(TrustLine).where(
                        and_(
                            TrustLine.equivalent_id == equivalent_id,
                            TrustLine.status == "active",
                            TrustLine.from_participant_id.in_(list(from_ids)),
                            TrustLine.to_participant_id.in_(list(to_ids)),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        tl_by_pair: dict[tuple[uuid.UUID, uuid.UUID], TrustLine] = {
            (tl.from_participant_id, tl.to_participant_id): tl for tl in trustlines
        }

        for from_id, to_id in required_pairs:
            tl = tl_by_pair.get((from_id, to_id))
            if tl is None:
                return False
            if not self._policy_flag(tl.policy, "auto_clearing", default=True):
                return False

        return True

    @staticmethod
    def _policy_flag(policy: dict | None, key: str, *, default: bool) -> bool:
        """Parse a boolean flag from a policy JSON blob.

        SQLite JSON handling can surface values as strings in some flows.
        We treat common falsy string forms as False.
        """
        if policy is None:
            return default

        value = policy.get(key, default)
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"false", "0", "no", "off"}:
                return False
            if v in {"true", "1", "yes", "on"}:
                return True
            return default

        return bool(value)

    async def _filter_cycles_by_auto_clearing_policy_sql(
        self, cycles: List[List[Dict]], *, equivalent_id: uuid.UUID
    ) -> List[List[Dict]]:
        """Filter SQL-produced candidate cycles by auto-clearing consent.

        Important: SQL cycle detectors don't apply policy constraints. If we return
        cycles that will be skipped at execution time, the clearing loop may stop
        early and never try alternative depths (e.g., quadrangles).
        """

        if not cycles:
            return []

        debt_ids: set[uuid.UUID] = set()
        for cycle in cycles:
            for edge in cycle:
                try:
                    debt_ids.add(uuid.UUID(str(edge.get("debt_id"))))
                except Exception:
                    continue

        if not debt_ids:
            return []

        debts = (
            (
                await self.session.execute(
                    select(Debt).where(Debt.id.in_(list(debt_ids)))
                )
            )
            .scalars()
            .all()
        )
        debts_by_id: dict[uuid.UUID, Debt] = {d.id: d for d in debts}

        # Use the same policy evaluation path as execution time.
        # This is intentionally less optimized than the bulk trustline fetch, but
        # keeps behavior consistent and avoids subtle SQLite/JSON edge cases.
        filtered: List[List[Dict]] = []
        for cycle in cycles:
            cycle_debts: List[Debt] = []
            ok = True
            for edge in cycle:
                try:
                    debt_id = uuid.UUID(str(edge.get("debt_id")))
                except Exception:
                    ok = False
                    break
                debt = debts_by_id.get(debt_id)
                if debt is None:
                    ok = False
                    break
                cycle_debts.append(debt)

            if not ok or not cycle_debts:
                continue
            if cycle_debts[0].equivalent_id != equivalent_id:
                continue
            if await self._cycle_respects_auto_clearing(cycle_debts):
                filtered.append(cycle)

        return filtered

    async def _locked_pairs_for_equivalent(
        self, equivalent_id: uuid.UUID
    ) -> Set[frozenset[uuid.UUID]]:
        """Return participant pairs that must not be touched by clearing.

        For MVP safety we treat any active prepared payment flow `from->to` as a lock on the unordered
        participant pair {from, to}. Clearing must not modify debts between these participants.
        """
        stmt = select(PrepareLock).where(PrepareLock.expires_at > func.now())
        locks = (await self.session.execute(stmt)).scalars().all()

        locked: Set[frozenset[uuid.UUID]] = set()
        for lock in locks:
            for flow in (lock.effects or {}).get("flows", []):
                try:
                    eq_id = uuid.UUID(str(flow.get("equivalent")))
                    if eq_id != equivalent_id:
                        continue
                    from_id = uuid.UUID(str(flow.get("from")))
                    to_id = uuid.UUID(str(flow.get("to")))
                except Exception:
                    continue

                locked.add(frozenset({from_id, to_id}))

        return locked

    async def find_cycles(
        self,
        equivalent_code: str,
        max_depth: int = 6,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> List[List[Dict]]:
        """
        Find closed cycles of debts for a given equivalent.
        Returns list of cycles, where each cycle is a list of Debt objects (or dicts representing edges).

        `allowed_participant_pids` confines detection to one run's participants
        (2026-08-22 / p010, `F-010-3`).  Three states, and the difference between the last
        two is the whole point:

        * `None` — no perimeter is being applied.  The hub routes, the admin preview and the
          simulator tick all rely on this and pass nothing.
        * a non-empty set — only cycles whose every vertex is in the set.
        * an EMPTY set — nobody.  `_run_scoped_pids_or_none` returns exactly that when the
          perimeter cannot be established (`app/api/v1/simulator.py:641-651`), and reading it
          as "no restriction" would be a literal return of `F-009-1`.

        Algorithm:
        1. Load all debts for this equivalent into memory (Graph).
           For MVP (small scale), this is feasible. For production, we need more optimized graph DB or targeted search.
        2. Perform DFS/BFS to find cycles.
        """
        logger.info(
            "event=clearing.find_cycles equivalent=%s max_depth=%s",
            equivalent_code,
            max_depth,
        )
        try:
            CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="start").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=find_cycles.start",
                exc_info=True,
            )

        equivalent = (
            await self.session.execute(
                select(Equivalent).where(Equivalent.code == equivalent_code)
            )
        ).scalar_one_or_none()
        if not equivalent:
            try:
                CLEARING_EVENTS_TOTAL.labels(
                    event="find_cycles", result="not_found"
                ).inc()
            except Exception:
                logger.debug(
                    "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=find_cycles.not_found",
                    exc_info=True,
                )
            raise GeoException(f"Equivalent {equivalent_code} not found")

        allowed_ids: "set[uuid.UUID] | None" = None
        if allowed_participant_pids is not None:
            if not allowed_participant_pids:
                # An empty perimeter admits nobody, so there is nothing to look for.
                return []
            # Resolved once, here: the route loops over find_cycles up to a hundred times
            # (`app/api/v1/simulator.py:1771`), and the money code below works in UUIDs while
            # the perimeter arrives as pids.  A failure here must abort rather than fall into
            # the broad SQL fallback beneath, which would silently drop the perimeter.
            allowed_ids = set(
                (
                    await self.session.execute(
                        select(Participant.id).where(
                            Participant.pid.in_(sorted(allowed_participant_pids))
                        )
                    )
                ).scalars().all()
            )
            if not allowed_ids:
                return []

        # FIX-012: Prefer SQL JOIN based search for short cycles (3–4) when running with a real AsyncSession.
        use_sql = isinstance(self.session, AsyncSession)
        if use_sql and max_depth >= 3:
            locked_pairs = await self._locked_pairs_for_equivalent(equivalent.id)
            cycles: List[List[Dict]] = []
            try:
                cycles = await self.find_triangles_sql(
                    equivalent.id, allowed_participant_ids=allowed_ids
                )
                if cycles and locked_pairs:
                    filtered: List[List[Dict]] = []
                    for cycle in cycles:
                        skip = False
                        for edge in cycle:
                            try:
                                debtor_id = uuid.UUID(str(edge.get("debtor")))
                                creditor_id = uuid.UUID(str(edge.get("creditor")))
                            except Exception:
                                continue
                            if frozenset({debtor_id, creditor_id}) in locked_pairs:
                                skip = True
                                break
                        if not skip:
                            filtered.append(cycle)
                    cycles = filtered

                cycles = self._deduplicate_cycles(cycles)

                if cycles:
                    cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                        cycles, equivalent_id=equivalent.id
                    )

                # If triangles exist but are all filtered out (policy/locks), try quadrangles.
                if max_depth >= 4 and not cycles:
                    cycles = await self.find_quadrangles_sql(
                        equivalent.id, allowed_participant_ids=allowed_ids
                    )
                    if cycles and locked_pairs:
                        filtered = []
                        for cycle in cycles:
                            skip = False
                            for edge in cycle:
                                try:
                                    debtor_id = uuid.UUID(str(edge.get("debtor")))
                                    creditor_id = uuid.UUID(str(edge.get("creditor")))
                                except Exception:
                                    continue
                                if frozenset({debtor_id, creditor_id}) in locked_pairs:
                                    skip = True
                                    break
                            if not skip:
                                filtered.append(cycle)
                        cycles = filtered

                    cycles = self._deduplicate_cycles(cycles)

                    if cycles:
                        cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                            cycles, equivalent_id=equivalent.id
                        )
            except Exception:
                logger.warning(
                    "event=clearing.find_cycles_sql_failed equivalent=%s",
                    equivalent_code,
                    exc_info=True,
                )
                cycles = []

            if cycles:
                # Replace UUIDs with PIDs for consistency with existing output.
                participant_ids: Set[uuid.UUID] = set()
                for cycle in cycles:
                    for edge in cycle:
                        try:
                            participant_ids.add(uuid.UUID(str(edge["debtor"])))
                            participant_ids.add(uuid.UUID(str(edge["creditor"])))
                        except Exception:
                            pass

                pid_by_id: Dict[uuid.UUID, str] = {}
                if participant_ids:
                    participants = (
                        (
                            await self.session.execute(
                                select(Participant).where(
                                    Participant.id.in_(list(participant_ids))
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    pid_by_id = {p.id: p.pid for p in participants}

                for cycle in cycles:
                    for edge in cycle:
                        try:
                            debtor_uuid = uuid.UUID(str(edge["debtor"]))
                            creditor_uuid = uuid.UUID(str(edge["creditor"]))
                            edge["debtor"] = str(
                                pid_by_id.get(debtor_uuid, debtor_uuid)
                            )
                            edge["creditor"] = str(
                                pid_by_id.get(creditor_uuid, creditor_uuid)
                            )
                        except Exception:
                            pass

                return cycles

        # 1. Load Graph
        # Node: Participant ID
        # Edge: Debt (debtor -> creditor, amount)
        # The perimeter narrows the LOAD, not the result: with both ends of every edge
        # inside the allowlist, no cycle the DFS can build reaches outside it, so no output
        # filter is needed here (2026-08-22 / p010, `F-010-3`).
        conditions = [Debt.equivalent_id == equivalent.id, Debt.amount > 0]
        if allowed_ids is not None:
            conditions.append(Debt.debtor_id.in_(allowed_ids))
            conditions.append(Debt.creditor_id.in_(allowed_ids))
        stmt = select(Debt).where(and_(*conditions))
        all_debts = (await self.session.execute(stmt)).scalars().all()

        # Exclude edges that are involved in active prepared payments.
        locked_pairs = await self._locked_pairs_for_equivalent(equivalent.id)
        if locked_pairs:
            all_debts = [
                d
                for d in all_debts
                if frozenset({d.debtor_id, d.creditor_id}) not in locked_pairs
            ]

        adjacency: Dict[uuid.UUID, List[Debt]] = {}
        for d in all_debts:
            if d.debtor_id not in adjacency:
                adjacency[d.debtor_id] = []
            adjacency[d.debtor_id].append(d)

        # Build UUID -> PID mapping for participants in this graph.
        participant_ids: Set[uuid.UUID] = set()
        for d in all_debts:
            participant_ids.add(d.debtor_id)
            participant_ids.add(d.creditor_id)

        pid_by_id: Dict[uuid.UUID, str] = {}
        if participant_ids:
            participants = (
                (
                    await self.session.execute(
                        select(Participant).where(
                            Participant.id.in_(list(participant_ids))
                        )
                    )
                )
                .scalars()
                .all()
            )
            pid_by_id = {p.id: p.pid for p in participants}

        # 2. Find Cycles
        # We look for simple cycles.
        cycles = []

        # To avoid duplicates (e.g. A->B->C->A vs B->C->A->B), we can enforce ordering or use set of sets.
        # Simple DFS with path tracking.

        def dfs(
            start_node: uuid.UUID,
            current_node: uuid.UUID,
            path: List[Debt],
            visited_in_path: Set[uuid.UUID],
        ):
            # `max_depth` is the maximum number of edges in the resulting cycle.
            # If we already have `max_depth` edges in the path, we can't extend it.
            if len(path) >= max_depth:
                return

            if current_node not in adjacency:
                return

            for edge in adjacency[current_node]:
                neighbor = edge.creditor_id

                if neighbor == start_node:
                    # Cycle found!
                    cycles.append(path + [edge])
                    return

                if neighbor not in visited_in_path:
                    dfs(
                        start_node,
                        neighbor,
                        path + [edge],
                        visited_in_path | {neighbor},
                    )

        # Run DFS from each node.
        # Optimization: Remove nodes that cannot be part of a cycle (in-degree=0 or out-degree=0).
        # Optimization: Once a cycle is found, we might want to "consume" it?
        # But here we just LIST them.

        # We need to avoid finding same cycle multiple times starting from different nodes.
        # Canonization: Cycle is represented by min(node_id) as start?

        nodes = list(adjacency.keys())
        # Sort for determinism
        # nodes.sort()

        # We need a robust cycle finder.
        # NetworkX is good but adding dependency? Let's keep it simple custom DFS.
        # Since we want to find *any* cycle to clear, we don't need *all* cycles.

        unique_cycles_hashes = set()

        # Let's retry simple approach:
        # Iterate all nodes. If node not visited globally (optional optimization?), start DFS.
        # Actually finding ALL cycles in a graph is NP-hard (or exponential).
        # We usually want "Shortest Cycle" or "Any Cycle".

        # Let's implement finding ONE cycle per run? Or a few.
        # Clearing usually iterates: Find Cycle -> Clear -> Repeat.

        # Heuristic: Start from nodes with Debts.
        for start_node in nodes:
            # Limit search
            if len(cycles) > 50:
                break

            dfs(start_node, start_node, [], {start_node})

        # Filter duplicates
        final_cycles = []
        for cycle in cycles:
            # cycle is list of Debt objects
            # Signature: sorted list of debt IDs?
            ids = sorted([d.id for d in cycle])
            h = tuple(ids)
            if h not in unique_cycles_hashes:
                unique_cycles_hashes.add(h)

                # Format for output
                cycle_data = []
                for edge in cycle:
                    cycle_data.append(
                        {
                            "debt_id": str(edge.id),
                            "debtor": str(
                                pid_by_id.get(edge.debtor_id, edge.debtor_id)
                            ),
                            "creditor": str(
                                pid_by_id.get(edge.creditor_id, edge.creditor_id)
                            ),
                            "amount": str(edge.amount),
                        }
                    )
                final_cycles.append(cycle_data)

        # Prefer shorter cycles first for auto_clear().
        final_cycles.sort(key=len)

        logger.info(
            "event=clearing.find_cycles_done equivalent=%s cycles=%s",
            equivalent_code,
            len(final_cycles),
        )
        try:
            CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="success").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=find_cycles.success",
                exc_info=True,
            )
        if final_cycles:
            final_cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                final_cycles, equivalent_id=equivalent.id
            )
        return final_cycles

    async def execute_clearing(
        self,
        cycle: List[Dict],
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> bool:
        """Backward-compatible API: execute clearing and return success flag."""
        return (
            await self.execute_clearing_with_amount(
                cycle, allowed_participant_pids=allowed_participant_pids
            )
        ) is not None

    async def execute_clearing_with_amount(
        self,
        cycle: List[Dict],
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        """Execute one clearing attempt inside the shared payment owner domain.

        `allowed_participant_pids` is the run perimeter (2026-08-22 / p010, `F-010-3`).  It is
        carried through EVERY path into `_execute_clearing_with_amount` on purpose: a single
        forgotten transition would be a way around the guard, and the guard is the second
        line of defence — detection is the first, and a caller may hand us a cycle that
        detection never produced.
        """
        allowed_ids = await self._resolve_scope_ids(allowed_participant_pids)
        if allowed_participant_pids is not None and not allowed_ids:
            # An empty perimeter admits nobody; there is nothing this cycle can legally be.
            await self._raise_unexpected_execution(
                RuntimeError("Clearing cycle escaped participant scope: empty perimeter")
            )

        if self._dialect_name() not in {"postgresql", "postgres"} or not cycle:
            return await self._execute_clearing_with_amount(
                cycle,
                allowed_participant_ids=allowed_ids,
                allowed_participant_pids=allowed_participant_pids,
            )

        bind = getattr(self.session, "bind", None)
        if isinstance(bind, AsyncConnection):
            # PostgreSQL clearing owns a one-connection interlock boundary. An
            # externally owned connection cannot be returned before the pinned
            # work connection is acquired, so accepting it could exhaust even
            # a valid single-connection pool.
            await self._rollback_before_interlock(self.session)
            logger.error("event=clearing.external_connection_bind_unsupported")
            raise GeoException() from RuntimeError(
                "PostgreSQL clearing requires an engine-bound AsyncSession"
            )
        if not isinstance(bind, AsyncEngine):
            await self._raise_unexpected_execution(GeoException())
        lock_bind = bind

        try:
            debt_ids = [uuid.UUID(str(edge["debt_id"])) for edge in cycle]
        except Exception:
            return await self._execute_clearing_with_amount(
                cycle,
                allowed_participant_ids=allowed_ids,
                allowed_participant_pids=allowed_participant_pids,
            )

        execution_tx_id = self._execution_tx_id(debt_ids)
        try:
            preflight_debts = (
                (
                    await self.session.execute(
                        select(Debt).where(Debt.id.in_(debt_ids))
                    )
                )
                .scalars()
                .all()
            )
        except asyncio.CancelledError:
            await self._rollback_before_interlock(self.session)
            raise
        except Exception as exc:
            await self._raise_unexpected_execution(exc)

        if len(preflight_debts) != len(debt_ids):
            try:
                replay_amount = await self._reconcile_committed_execution(
                    execution_tx_id,
                    allowed_participant_pids=allowed_participant_pids,
                )
            except Exception as exc:
                await self._raise_unexpected_execution(exc)
            if replay_amount is not None:
                return replay_amount
            return await self._execute_clearing_with_amount(
                cycle,
                allowed_participant_ids=allowed_ids,
                allowed_participant_pids=allowed_participant_pids,
            )

        equivalent_ids = {debt.equivalent_id for debt in preflight_debts}
        if len(equivalent_ids) != 1:
            await self._raise_unexpected_execution(
                GeoException("Clearing cycle spans multiple equivalents")
            )
        equivalent_id = next(iter(equivalent_ids))

        try:
            caller_connection = await self.session.connection()
            isolation_level = await caller_connection.get_isolation_level()
        except asyncio.CancelledError:
            await self._rollback_before_interlock(self.session)
            raise
        except Exception as exc:
            await self._raise_unexpected_execution(exc)

        try:
            await self._rollback_before_interlock(self.session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._raise_unexpected_execution(exc)

        from app.config import settings

        connection_budget_s = max(
            0.001,
            min(
                float(settings.PAYMENT_TOTAL_TIMEOUT_SECONDS or 10),
                float(settings.COMMIT_TIMEOUT_SECONDS or 5),
            ),
        )
        connection: AsyncConnection | None = None
        try:
            connection = await asyncio.wait_for(
                lock_bind.connect(),
                timeout=connection_budget_s,
            )
            connection = await connection.execution_options(
                isolation_level=isolation_level,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutException("Clearing interlock timed out") from exc
        except asyncio.CancelledError as exc:
            if connection is not None:
                try:
                    await self._close_checked_out_connection(connection)
                except BaseException as cleanup_error:
                    exc.add_note(
                        "Clearing pre-lock connection cleanup failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            raise
        except Exception as exc:
            if connection is not None:
                await self._close_checked_out_connection(connection)
            await self._raise_unexpected_execution(exc)

        if connection is None:
            await self._raise_unexpected_execution(GeoException())

        work_session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
        )
        lock_was_acquired = False
        result: Decimal | None = None
        result_available = False
        primary_error: BaseException | None = None
        original_session = self.session
        try:
            try:
                await PaymentEngine(
                    work_session
                ).acquire_session_equivalent_owner_lock(equivalent_id)
                lock_was_acquired = True
            except Exception as exc:
                if "55P03" in self._postgres_error_codes(exc):
                    raise TimeoutException("Clearing interlock timed out") from exc
                logger.exception("event=clearing.interlock_acquire_failed")
                raise GeoException() from exc

            # The session lock survives this rollback, while the next statement
            # gets a snapshot newer than the payment holder we may have awaited.
            await self._rollback_before_interlock(work_session)
            self.session = work_session
            try:
                result = await self._execute_clearing_with_amount(
                    cycle,
                    interlocked_equivalent_id=equivalent_id,
                    allowed_participant_ids=allowed_ids,
                    allowed_participant_pids=allowed_participant_pids,
                )
                result_available = True
            finally:
                self.session = original_session
        except BaseException as exc:
            primary_error = exc

        try:
            cleanup_cancellation = await self._release_interlock_session(
                work_session,
                connection,
                equivalent_id,
                lock_was_acquired=lock_was_acquired,
            )
        except asyncio.CancelledError as exc:
            # The production helper drains cleanup and returns cancellation.
            # Keep the outer boundary correct if cancellation is delivered in
            # the final await after cleanup has already become terminal.
            cleanup_cancellation = exc
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(
                    "Clearing interlock cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
                raise primary_error
            raise

        if primary_error is not None:
            raise primary_error
        if cleanup_cancellation is not None:
            if result_available and result is not None:
                raise ClearingCommittedAfterCancellation(
                    tx_id=execution_tx_id,
                    cleared_amount=result,
                ) from cleanup_cancellation
            raise cleanup_cancellation
        return result

    async def _execute_clearing_with_amount(
        self,
        cycle: List[Dict],
        *,
        interlocked_equivalent_id: uuid.UUID | None = None,
        allowed_participant_ids: "set[uuid.UUID] | None" = None,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        """Execute clearing for a specific cycle and return the *actual* cleared amount.

        Returns:
        - Decimal amount on success (the min debt amount among the locked cycle edges at execution time)
        - None when the candidate is invalid or skipped by lock/policy checks.

        Unexpected execution failures are rolled back and surfaced through the
        application's sanitized internal-error path.
        """
        if not cycle:
            await self._rollback_skipped_execution()
            return None

        logger.info("event=clearing.execute cycle_len=%s", len(cycle))
        try:
            CLEARING_EVENTS_TOTAL.labels(event="execute", result="start").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.start",
                exc_info=True,
            )

        # Load debts for this cycle to avoid relying on debtor/creditor fields in the API output.
        try:
            debt_ids = [uuid.UUID(str(edge["debt_id"])) for edge in cycle]
        except Exception:
            try:
                CLEARING_EVENTS_TOTAL.labels(
                    event="execute", result="bad_request"
                ).inc()
            except Exception:
                logger.debug(
                    "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.bad_request",
                    exc_info=True,
                )
            await self._rollback_skipped_execution()
            return None

        execution_tx_id = self._execution_tx_id(debt_ids)
        try:
            replay_amount = await self._committed_execution_amount(
                execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )
        except Exception as exc:
            if self._is_retryable_concurrency_error(exc):
                try:
                    replay_amount = await self._reconcile_committed_execution(
                        execution_tx_id,
                        allowed_participant_pids=allowed_participant_pids,
                    )
                except Exception as reconciliation_error:
                    await self._raise_unexpected_execution(reconciliation_error)
                if replay_amount is not None:
                    return replay_amount
            await self._raise_unexpected_execution(exc)
        if replay_amount is not None:
            await self._rollback_skipped_execution()
            return replay_amount

        try:
            debts = (
                (
                    await self.session.execute(
                        select(Debt).where(Debt.id.in_(debt_ids)).with_for_update()
                    )
                )
                .scalars()
                .all()
            )
        except Exception as exc:
            if self._is_retryable_concurrency_error(exc):
                try:
                    replay_amount = await self._reconcile_committed_execution(
                        execution_tx_id,
                        allowed_participant_pids=allowed_participant_pids,
                    )
                except Exception as reconciliation_error:
                    await self._raise_unexpected_execution(reconciliation_error)
                if replay_amount is not None:
                    return replay_amount
            await self._raise_unexpected_execution(exc)

        if len(debts) != len(debt_ids):
            # A concurrent owner may have committed this exact occurrence while
            # we waited for its Debt rows. Resolve that durable result before skip.
            try:
                replay_amount = await self._committed_execution_amount(
                    execution_tx_id,
                    allowed_participant_pids=allowed_participant_pids,
                )
            except Exception as exc:
                await self._raise_unexpected_execution(exc)
            if replay_amount is not None:
                await self._rollback_skipped_execution()
                return replay_amount
            await self._rollback_skipped_execution()
            return None

        if interlocked_equivalent_id is not None and any(
            debt.equivalent_id != interlocked_equivalent_id for debt in debts
        ):
            await self._raise_unexpected_execution(
                GeoException("Clearing cycle identity changed after interlock")
            )

        # 2026-08-22 / p010 (`F-010-3`).  The authoritative perimeter check, and the only
        # one: it stands on the rows just re-read under FOR UPDATE, so it cannot be fooled
        # by a cycle that changed between detection and execution, and it runs before the
        # amount is computed and before any side effect.
        #
        # Not the preflight read: that snapshot is discarded when the original transaction
        # rolls back and a separate interlock connection opens a new one.  Not the later
        # `participant_ids` assembly either: by then several more queries have run.
        #
        # A violation is a fail-closed internal refusal, not a `None`.  `None` is the
        # caller's signal for "candidate skipped" and would let the request finish as a
        # successful zero result (`app/api/v1/simulator.py:1784-1785`), which is precisely
        # the silent outcome this finding is about.
        if allowed_participant_ids is not None:
            touched = {
                participant_id
                for debt in debts
                for participant_id in (debt.debtor_id, debt.creditor_id)
            }
            if not touched <= allowed_participant_ids:
                await self._raise_unexpected_execution(
                    GeoException("Clearing cycle escaped participant scope")
                )

        # 1. Determine clearing amount (min amount in cycle)
        clear_amount = min([d.amount for d in debts])

        if clear_amount <= 0:
            await self._rollback_skipped_execution()
            return None

        logger.info(
            "event=clearing.execute_ready cycle_len=%s amount=%s",
            len(cycle),
            clear_amount,
        )

        # Reject cycles that touch any edge reserved by active PrepareLocks.
        try:
            locked_pairs = await self._locked_pairs_for_equivalent(
                debts[0].equivalent_id
            )
        except Exception as exc:
            await self._raise_unexpected_execution(exc)
        if locked_pairs:
            for d in debts:
                pair = frozenset({d.debtor_id, d.creditor_id})
                if pair in locked_pairs:
                    logger.info("event=clearing.skip_locked cycle_len=%s", len(cycle))
                    try:
                        CLEARING_EVENTS_TOTAL.labels(
                            event="execute", result="skip_locked"
                        ).inc()
                    except Exception:
                        logger.debug(
                            "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.skip_locked",
                            exc_info=True,
                        )
                    await self._rollback_skipped_execution()
                    return None

        # FIX-017: enforce auto_clearing policy on every edge in the cycle.
        try:
            respects_auto_clearing = await self._cycle_respects_auto_clearing(debts)
        except Exception as exc:
            await self._raise_unexpected_execution(exc)
        if not respects_auto_clearing:
            logger.info("event=clearing.skip_policy cycle_len=%s", len(cycle))
            try:
                CLEARING_EVENTS_TOTAL.labels(
                    event="execute", result="skip_policy"
                ).inc()
            except Exception:
                logger.debug(
                    "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.skip_policy",
                    exc_info=True,
                )
            await self._rollback_skipped_execution()
            return None

        # FIX-011: capture net positions BEFORE clearing (clearing neutrality invariant).
        checker = InvariantChecker(self.session)
        participant_ids: Set[uuid.UUID] = set()
        for d in debts:
            participant_ids.add(d.debtor_id)
            participant_ids.add(d.creditor_id)

        # FIX-025: enrich CLEARING transaction payload for traceability.
        try:
            equivalent = (
                await self.session.execute(
                    select(Equivalent).where(Equivalent.id == debts[0].equivalent_id)
                )
            ).scalar_one_or_none()

            pid_by_id: Dict[uuid.UUID, str] = {}
            if participant_ids:
                participants = (
                    (
                        await self.session.execute(
                            select(Participant).where(
                                Participant.id.in_(list(participant_ids))
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                pid_by_id = {p.id: p.pid for p in participants}
        except Exception as exc:
            await self._raise_unexpected_execution(exc)

        debts_by_id: Dict[uuid.UUID, Debt] = {d.id: d for d in debts}
        edges_payload: List[Dict[str, str]] = []
        for edge in cycle:
            try:
                edge_debt_id = uuid.UUID(str(edge.get("debt_id")))
            except Exception:
                continue

            debt = debts_by_id.get(edge_debt_id)
            if debt is None:
                continue

            edges_payload.append(
                {
                    "debt_id": str(debt.id),
                    "debtor": str(pid_by_id.get(debt.debtor_id, debt.debtor_id)),
                    "creditor": str(pid_by_id.get(debt.creditor_id, debt.creditor_id)),
                    "amount": str(clear_amount),
                }
            )

        try:
            positions_before: Dict[uuid.UUID, Decimal] = {}
            for pid in participant_ids:
                positions_before[pid] = await checker._calculate_net_position(
                    pid, debts[0].equivalent_id
                )
        except Exception as exc:
            await self._raise_unexpected_execution(exc)

        checkpoint_before = None
        try:
            checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=debts[0].equivalent_id,
            )
        except Exception:
            logger.warning(
                "event=clearing.checkpoint_before_failed",
                exc_info=True,
            )
            checkpoint_before = None

        # 2. Create Transaction (CLEARING)
        # We need an initiator? System or one of participants.
        # Let's pick the first debtor.
        initiator_id = debts[0].debtor_id

        tx_uuid = uuid.UUID(execution_tx_id)
        tx_id_str = execution_tx_id

        new_tx = Transaction(
            id=tx_uuid,
            tx_id=tx_id_str,
            idempotency_key=f"clearing:{tx_id_str}",
            type="CLEARING",
            initiator_id=initiator_id,
            payload={
                # Backward-compatible fields.
                "cycle": [str(e["debt_id"]) for e in cycle],
                "amount": str(clear_amount),
                # Enriched fields for audit/debugging.
                "equivalent": str(
                    equivalent.code if equivalent else debts[0].equivalent_id
                ),
                "edges": edges_payload,
            },
            state="NEW",
        )
        self.session.add(new_tx)

        try:
            # 3. Apply changes (Decrease debts)
            # We must lock rows? Or just update.
            # Since we are in a transaction, we should select for update ideally.
            # For MVP, we just update.

            for debt in debts:
                if debt.amount < clear_amount:
                    raise GeoException(f"Debt {debt.id} amount changed during clearing")

                debt.amount -= clear_amount
                if debt.amount == 0:
                    await self.session.delete(debt)
                else:
                    self.session.add(debt)

            await self.session.flush()

            checkpoint_after = None
            try:
                checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
                    self.session,
                    equivalent_id=debts[0].equivalent_id,
                )
            except Exception:
                logger.warning(
                    "event=clearing.checkpoint_after_failed",
                    exc_info=True,
                )
                checkpoint_after = None

            try:
                before_sum = checkpoint_before.checksum if checkpoint_before else ""
                after_sum = (
                    checkpoint_after.checksum if checkpoint_after else before_sum
                )
                invariants_status = (
                    (checkpoint_after.invariants_status or {})
                    if checkpoint_after
                    else {}
                )
                passed = bool(invariants_status.get("passed", False))

                self.session.add(
                    IntegrityAuditLog(
                        operation_type="CLEARING",
                        tx_id=tx_id_str,
                        equivalent_code=str(
                            equivalent.code if equivalent else debts[0].equivalent_id
                        ),
                        state_checksum_before=before_sum,
                        state_checksum_after=after_sum,
                        affected_participants={
                            "participants": [
                                str(pid_by_id.get(p, p)) for p in participant_ids
                            ],
                            "edges": edges_payload,
                        },
                        invariants_checked=invariants_status.get("checks")
                        or invariants_status,
                        verification_passed=passed,
                        error_details=None if passed else invariants_status,
                    )
                )
            except Exception:
                # Best-effort; clearing must not fail due to audit logging.
                logger.warning(
                    "event=clearing.audit_build_failed",
                    exc_info=True,
                )

            # Verify neutrality AFTER applying changes (must be within the same DB transaction).
            await checker.verify_clearing_neutrality(
                list(participant_ids),
                debts[0].equivalent_id,
                positions_before,
            )

            # 4. Commit
            new_tx.state = "COMMITTED"
            self.session.add(new_tx)
            commit_cancellation, commit_error = await self._commit_to_terminal()
            if commit_error is not None:
                if (
                    commit_cancellation is None
                    and isinstance(commit_error, asyncio.CancelledError)
                ):
                    commit_cancellation = commit_error
                reconciliation_task = asyncio.create_task(
                    self._reconcile_committed_execution(
                        tx_id_str,
                        allowed_participant_pids=allowed_participant_pids,
                    )
                )
                reconciliation_cancellation = await self._drain_task(
                    reconciliation_task
                )
                reconciled_amount = reconciliation_task.result()
                if (
                    commit_cancellation is None
                    and reconciliation_cancellation is not None
                ):
                    commit_cancellation = reconciliation_cancellation
                if reconciled_amount is None:
                    if commit_cancellation is not None:
                        raise commit_cancellation
                    raise commit_error
                clear_amount = reconciled_amount

            # Debts changed: invalidate any TTL routing graph cache.
            try:
                eq_code = str(equivalent.code if equivalent else "")
                if eq_code:
                    PaymentRouter.invalidate_cache(eq_code)
            except Exception:
                pass

            logger.info("event=clearing.committed tx_id=%s", tx_id_str)
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="success").inc()
            except Exception:
                pass
            if commit_cancellation is not None:
                raise ClearingCommittedAfterCancellation(
                    tx_id=tx_id_str,
                    cleared_amount=clear_amount,
                ) from commit_cancellation
            return clear_amount

        except Exception as exc:
            if self._is_retryable_concurrency_error(exc):
                try:
                    replay_amount = await self._reconcile_committed_execution(
                        execution_tx_id,
                        allowed_participant_pids=allowed_participant_pids,
                    )
                except Exception as reconciliation_error:
                    await self._raise_unexpected_execution(reconciliation_error)
                if replay_amount is not None:
                    return replay_amount
            await self._raise_unexpected_execution(exc)

    async def auto_clear(self, equivalent_code: str, *, max_depth: int = 6) -> int:
        """
        Run clearing loop.
        Returns number of cleared cycles.
        """
        count = 0
        while True:
            try:
                cycles = await self.find_cycles(
                    equivalent_code,
                    max_depth=max_depth,
                )
            except GeoException as exc:
                if exc.code != ErrorCode.E010.value:
                    raise
                logger.exception(
                    "event=clearing.auto_clear_find_failed equivalent=%s "
                    "cleared_cycles=%s",
                    equivalent_code,
                    count,
                )
                raise GeoException(
                    details={
                        "cleared_cycles": count,
                        "partial": count > 0,
                    }
                ) from exc
            except Exception as exc:
                logger.exception(
                    "event=clearing.auto_clear_find_failed equivalent=%s "
                    "cleared_cycles=%s",
                    equivalent_code,
                    count,
                )
                raise GeoException(
                    details={
                        "cleared_cycles": count,
                        "partial": count > 0,
                    }
                ) from exc
            if not cycles:
                break

            # Try cycles until one succeeds. If all candidates fail (e.g. due to locks/concurrency), stop.
            executed = False
            for cycle in cycles:
                try:
                    success = await self.execute_clearing(cycle)
                except GeoException as exc:
                    if exc.code != ErrorCode.E010.value:
                        raise
                    logger.exception(
                        "event=clearing.auto_clear_execute_failed equivalent=%s "
                        "cleared_cycles=%s",
                        equivalent_code,
                        count,
                    )
                    raise GeoException(
                        details={
                            "cleared_cycles": count,
                            "partial": count > 0,
                        }
                    ) from exc
                except Exception as exc:
                    logger.exception(
                        "event=clearing.auto_clear_execute_failed equivalent=%s "
                        "cleared_cycles=%s",
                        equivalent_code,
                        count,
                    )
                    raise GeoException(
                        details={
                            "cleared_cycles": count,
                            "partial": count > 0,
                        }
                    ) from exc
                if success:
                    count += 1
                    executed = True
                    break

            if not executed:
                break

            if count > 100:  # Safety break
                break

        return count
