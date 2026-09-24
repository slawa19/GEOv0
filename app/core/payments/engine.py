import logging
import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, List, Tuple, Awaitable, Callable, TypeVar
from uuid import UUID

from sqlalchemy import select, and_, or_, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError

from app.db.models.prepare_lock import PrepareLock
from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.db.models.audit_log import IntegrityAuditLog
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import (
    GeoException,
    ConflictException,
    RetryablePaymentConflictException,
    RoutingException,
)
from app.utils.metrics import PAYMENT_EVENTS_TOTAL

from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.money_boundary import MoneyBoundary
from app.core.ledger.book import (
    DEBT_OPERATION_IDENTITY_CONSTRAINTS as _DEBT_OPERATION_IDENTITY_CONSTRAINTS,
    Book,
    PaymentFlow,
    operation_for,
)

logger = logging.getLogger(__name__)


#: Scale 8, the scale every money column in this repository carries.
_MONEY_QUANTUM = Decimal("1E-8")

#: `_DEBT_OPERATION_IDENTITY_CONSTRAINTS` (T1529) lives in `app/core/ledger/book.py` since 018 stage A
#: and is imported above under its old name: the envelope is the book's.


def _scale8_money(amount: Decimal) -> str:
    """A payment flow amount as the exact scale-8 string the operation intent records.

    `"8.00"` and `"8.00000000"` are the same money, and an intent recording whichever spelling a
    lock happened to hold would give two identical payments two different digests. The amounts
    reaching here were validated by `_parse_persisted_prepare_locks` and carry scale 8 or less, so
    `quantize` widens and never rounds; were one ever to carry more, the journal's own quantization
    predicate refuses the debt write that follows, so the operation fails loudly instead of
    recording a rounded intent for an exact payment.
    """

    return f"{amount.quantize(_MONEY_QUANTUM):f}"


_T = TypeVar("_T")


class _EquivalentOwnerPreflightChanged(Exception):
    """Persisted payment flows changed before the tx lock was acquired."""


# `_DELTA_DRIFT_TOLERANCE` (T1522), the owner/transaction lock namespaces and every lock primitive live
# in `app/core/money_boundary.py` since programme 019 stage 2 (`T1903`).


@dataclass(frozen=True)
class _PersistedPaymentFlow:
    equivalent_id: UUID
    from_id: UUID
    to_id: UUID
    amount: Decimal


@dataclass(frozen=True)
class _ValidatedPrepareLock:
    lock_id: UUID
    flows: tuple[_PersistedPaymentFlow, ...]


class PaymentEngine(MoneyBoundary):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        from app.config import settings

        self.lock_ttl_seconds = settings.PREPARE_LOCK_TTL_SECONDS

        # Retry policy for SERIALIZABLE conflicts/deadlocks.
        # IMPORTANT: retry must repeat the *entire* unit-of-work (reads/checks/writes + commit),
        # not only `session.commit()`, because a rollback discards all changes.
        self._retry_attempts = settings.COMMIT_RETRY_ATTEMPTS
        self._retry_base_delay_s = settings.COMMIT_RETRY_BASE_DELAY_MS / 1000.0
        self._retry_max_delay_s = settings.COMMIT_RETRY_MAX_DELAY_MS / 1000.0
        # The advisory-lock budget, its switch and its deadline are `MoneyBoundary`'s
        # (`app/core/money_boundary.py`, programme 019 stage 2).

    @classmethod
    def _parse_persisted_prepare_locks(
        cls,
        locks: list[PrepareLock],
    ) -> tuple[_ValidatedPrepareLock, ...]:
        """Strictly validate the persisted effects consumed by commit/abort."""
        validated: list[_ValidatedPrepareLock] = []
        for lock in locks:
            raw_effects = lock.effects
            if not isinstance(raw_effects, dict):
                raise GeoException()
            raw_flows = raw_effects.get("flows")
            if not isinstance(raw_flows, list) or not raw_flows:
                raise GeoException()

            flows: list[_PersistedPaymentFlow] = []
            for flow in raw_flows:
                if not isinstance(flow, dict):
                    raise GeoException()
                try:
                    equivalent_id = UUID(str(flow["equivalent"]))
                    from_id = UUID(str(flow["from"]))
                    to_id = UUID(str(flow["to"]))
                    amount = Decimal(str(flow["amount"]))
                    if not amount.is_finite() or amount <= 0:
                        raise ValueError("invalid persisted payment amount")
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    AttributeError,
                    InvalidOperation,
                ) as exc:
                    raise GeoException() from exc
                flows.append(
                    _PersistedPaymentFlow(
                        equivalent_id=equivalent_id,
                        from_id=from_id,
                        to_id=to_id,
                        amount=amount,
                    )
                )
            validated.append(
                _ValidatedPrepareLock(lock_id=lock.id, flows=tuple(flows))
            )

        if not validated or not any(item.flows for item in validated):
            raise GeoException()
        return tuple(sorted(validated, key=lambda item: str(item.lock_id)))

    @classmethod
    def _segment_lock_keys_from_validated_flows(
        cls,
        validated_locks: tuple[_ValidatedPrepareLock, ...],
    ) -> set[int]:
        return {
            cls._segment_lock_key(
                equivalent_id=flow.equivalent_id,
                from_participant_id=flow.from_id,
                to_participant_id=flow.to_id,
            )
            for lock in validated_locks
            for flow in lock.flows
        }

    @staticmethod
    def _equivalent_ids_from_validated_locks(
        validated_locks: tuple[_ValidatedPrepareLock, ...],
    ) -> set[UUID]:
        return {
            flow.equivalent_id
            for lock in validated_locks
            for flow in lock.flows
        }

    async def _read_payment_prestate(
        self,
        validated_locks: tuple[_ValidatedPrepareLock, ...],
    ) -> list[dict[str, str]]:
        """Step 5b: the amounts on BOTH directions of every flow pair, before any flow ran, in ONE read.

        WHAT IT IS FOR. `_apply_flow` first reduces the receiver's debt to the sender and nets a mutual
        pair, so what a flow does to `debts` depends on both directions of its pair - and neither is in
        the flows. Recorded in the payment envelope (intent encoding version 2), they let the scheduled
        verifier recompute the netted deltas from the envelope alone
        (`app/core/ledger/reconciliation.py`, criterion (b)).

        WHERE IT RUNS (the commit calls it once, and the placement is tested by statement order):
        after the owner, transaction and segment locks, the TTL branch and the operator-stop
        `FOR SHARE`, and immediately before the envelope that records it. The owner lock keeps every
        other application writer of these debts - payment, clearing, inject - out until this
        transaction ends, and the read shares this transaction with `_apply_flow`. It is INSIDE the
        retried unit of work, so a retry reads again instead of reusing a pre-state from a snapshot
        that lost.

        COST: one SELECT per commit, whatever the number of flows, pairs and equivalents.

        Every direction is written, zero included: an absent entry and a zero amount must not be two
        spellings of one fact a reader has to agree on.
        """

        edges = sorted(
            {
                (flow.equivalent_id, debtor_id, creditor_id)
                for lock in validated_locks
                for flow in lock.flows
                for debtor_id, creditor_id in (
                    (flow.from_id, flow.to_id),
                    (flow.to_id, flow.from_id),
                )
            },
            key=lambda edge: (str(edge[0]), str(edge[1]), str(edge[2])),
        )
        if not edges:
            return []
        rows = (
            await self.session.execute(
                select(
                    Debt.equivalent_id, Debt.debtor_id, Debt.creditor_id, Debt.amount
                ).where(
                    or_(
                        *(
                            and_(
                                Debt.equivalent_id == equivalent_id,
                                Debt.debtor_id == debtor_id,
                                Debt.creditor_id == creditor_id,
                            )
                            for equivalent_id, debtor_id, creditor_id in edges
                        )
                    )
                )
            )
        ).all()
        held = {
            (equivalent_id, debtor_id, creditor_id): Decimal(str(amount))
            for equivalent_id, debtor_id, creditor_id, amount in rows
        }
        return [
            {
                "equivalent": str(equivalent_id),
                "debtor": str(debtor_id),
                "creditor": str(creditor_id),
                "amount": _scale8_money(
                    held.get((equivalent_id, debtor_id, creditor_id), Decimal("0"))
                ),
            }
            for equivalent_id, debtor_id, creditor_id in edges
        ]

    async def _load_prepare_locks(self, tx_id: str) -> list[PrepareLock]:
        return (
            (
                await self.session.execute(
                    select(PrepareLock)
                    .where(PrepareLock.tx_id == tx_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )

    async def _preacquire_equivalent_owner_locks_for_tx(
        self,
        tx_id: str,
        *,
        allow_malformed: bool,
    ) -> tuple[tuple[_ValidatedPrepareLock, ...], bool] | None:
        """Read lock metadata before tx lock, then acquire its full owner set.

        PostgreSQL callers re-read under the tx lock and reject any changed
        metadata rather than acquiring a newly appeared owner key out of order.
        """
        locks = await self._load_prepare_locks(tx_id)
        if not locks:
            return (), False
        try:
            validated_locks = self._parse_persisted_prepare_locks(locks)
        except GeoException:
            if allow_malformed:
                return (), True
            raise
        await self._acquire_equivalent_owner_locks(
            self._equivalent_ids_from_validated_locks(validated_locks)
        )
        return validated_locks, False

    def _is_retryable_db_error(self, exc: BaseException, *, op: str) -> bool:
        if not isinstance(exc, DBAPIError):
            return False

        orig = getattr(exc, "orig", None)
        # asyncpg uses `sqlstate`, psycopg2 uses `pgcode`.
        sqlstate = (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or getattr(orig, "code", None)
        )
        # 40P01: deadlock_detected, 40001: serialization_failure. PostgreSQL may
        # alternatively surface an invisible concurrent insert as 23505. Retry only the two exact
        # identity constraints below; every other unique violation must fail closed.
        statement = str(getattr(exc, "statement", None) or "").lstrip().upper()
        is_debt_insert = self._statement_inserts_into(statement, "debts")
        # T1529, measured 2026-09-13. THE SAME RACE ONE TABLE UP. `debt_operation` INSERTs and
        # flushes the operation envelope as the unit of work's FIRST write (programme 015, phase B
        # step 4 slice C), and `_uow`'s idempotency check above it reads `transactions` from the
        # unit of work's own snapshot. The application runs PostgreSQL at SERIALIZABLE
        # (`DB_POSTGRES_ISOLATION_LEVEL`, `app/db/session.py`), so a second commit of the same
        # tx_id that was parked on the segment advisory lock resumes on the snapshot it took
        # BEFORE the holder committed: it still sees `PREPARED`, walks past the `COMMITTED`
        # short-circuit and meets the holder's committed envelope row on the envelope's identity.
        #
        # WHY THE RETRY IS SAFE HERE, and it is the same reason as for the Debt business key above
        # rather than a new one: `_run_uow_with_retry` rolls back first, refuses to retry at all if
        # that rollback fails, and the next attempt therefore takes a FRESH snapshot and re-reads
        # the transaction. It does not assume the outcome - it measures it: `COMMITTED` returns
        # idempotently, `ABORTED`/`REJECTED` raises `ConflictException`, and a `PREPARED` row whose
        # envelope somehow exists cannot loop, because the attempt budget is bounded and the
        # original 23505 is then re-raised. An envelope row only becomes visible in the same
        # database transaction that sets `transactions.state = 'COMMITTED'`, so that last case is
        # not reachable THROUGH THE CODE PATHS THAT EXIST TODAY - and that is a statement about
        # `_uow`, not about the schema, which does not couple the two at all (corrected 2026-09-13
        # after external review; the first wording here said "by construction", which claimed the
        # schema's guarantee for a property only this function's callers provide). A completed
        # envelope over a still-`PREPARED` transaction is mechanically possible, so it must fail
        # closed rather than be asserted away here - and the bounded budget is what delivers that.
        #
        # BOTH identity constraints, not only the one the failing gate happened to report: a
        # PAYMENT envelope's `identity` IS its `tx_id`, so a duplicate row violates
        # `uq_debt_operations_kind_identity` and `uq_debt_operations_tx_id` at the same time and
        # PostgreSQL reports whichever index it checked first. Keying on one of them would make
        # this predicate depend on index creation order.
        is_envelope_insert = self._statement_inserts_into(statement, "debt_operations")
        constraint_name = self._get_db_constraint_name(exc)
        return sqlstate in {"40P01", "40001"} or (
            sqlstate == "23505"
            and op == "commit"
            and (
                (is_debt_insert and constraint_name == "uq_debts_debtor_creditor_equivalent")
                or (
                    is_envelope_insert
                    and constraint_name in _DEBT_OPERATION_IDENTITY_CONSTRAINTS
                )
            )
        )

    @staticmethod
    def _statement_inserts_into(upper_statement: str, table: str) -> bool:
        """Does this already-upper-cased statement insert into exactly `table`?

        The table name must be followed by a space or `(` and nothing else, so a table named by
        EXTENDING one of these - `debt_operations_archive`, say - cannot join a retry predicate by
        being named well. No such neighbour exists today (`debt_operation_equivalents` is not a
        prefix match: it is `operation_`, singular), which is why the boundary is in the rule and
        the case is in the test rather than in the corpus.
        """

        head = f"INSERT INTO {table.upper()}"
        return upper_statement == head or upper_statement.startswith((head + " ", head + "("))

    @staticmethod
    def _get_db_constraint_name(exc: BaseException) -> str | None:
        """Read a constraint name across asyncpg and psycopg DBAPI wrappers."""
        current = getattr(exc, "orig", None)
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            direct = getattr(current, "constraint_name", None)
            if direct:
                return str(direct)
            diag = getattr(current, "diag", None)
            diagnostic = getattr(diag, "constraint_name", None)
            if diagnostic:
                return str(diagnostic)
            current = getattr(current, "__cause__", None) or getattr(
                current, "__context__", None
            )
        return None

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
        - Catch Postgres 40001/40P01 and the two exact 23505 races after an invisible concurrent
          insert: the Debt business key, and the operation envelope's identity (T1529).
        - Rollback.
        - Exponential backoff with jitter, bounded.
        - Re-run the whole unit-of-work `fn()`.
        """

        previous_timeout_enabled = self._advisory_lock_timeout_enabled
        previous_deadline = self._advisory_lock_deadline
        self._advisory_lock_timeout_enabled = (
            previous_timeout_enabled and not use_savepoint
        )
        if self._advisory_lock_timeout_enabled:
            candidate_deadline = time.monotonic() + self._advisory_lock_budget_s
            self._advisory_lock_deadline = (
                min(previous_deadline, candidate_deadline)
                if previous_deadline is not None
                else candidate_deadline
            )
        else:
            # SET LOCAL would leak into the caller-owned outer transaction after
            # a successful savepoint. The outer service timeout owns this path.
            self._advisory_lock_deadline = None

        attempt = 0
        try:
            while True:
                try:
                    if use_savepoint:
                        async with self.session.begin_nested():
                            return await fn()
                    return await fn()
                except _EquivalentOwnerPreflightChanged as exc:
                    # The safe global order forbids acquiring a newly appeared
                    # equivalent owner after the tx key. A caller-owned outer UoW
                    # must restart from a fresh snapshot; an engine-owned UoW can
                    # rollback and repeat the complete owner -> tx -> pair sequence.
                    attempt += 1
                    if use_savepoint or attempt >= self._retry_attempts:
                        raise RetryablePaymentConflictException(
                            "Payment owner set changed concurrently; retry the transaction"
                        ) from exc
                    await self.session.rollback()
                    logger.warning(
                        "event=payment.owner_preflight_retry op=%s attempt=%s/%s",
                        op,
                        attempt,
                        self._retry_attempts,
                    )
                    continue
                except DBAPIError as exc:
                    pgcode = self._get_pgcode(exc)
                    if use_savepoint and pgcode in {"40P01", "40001"}:
                        # A transaction-level owner lock or SERIALIZABLE snapshot
                        # survives savepoint rollback. Retrying here recreates the
                        # same conflict; the outer owner must restart its whole UoW.
                        raise
                    attempt += 1

                    # A bounded advisory-lock wait is an operational timeout,
                    # not a generic database failure. Callers already own the
                    # rollback/abort policy for asyncio timeouts.
                    if pgcode == "55P03":
                        raise asyncio.TimeoutError(
                            "Payment advisory lock timed out"
                        ) from exc

                    # Only rollback when we are actually going to retry.
                    # For non-retryable DBAPIError (or on non-Postgres backends), rolling back
                    # inside a surrounding transaction context manager can close that context
                    # and cause follow-up errors like:
                    # "Can't operate on closed transaction inside context manager".
                    is_retryable = self._is_retryable_db_error(exc, op=op)
                    if attempt >= self._retry_attempts or not is_retryable:
                        raise

                    if not use_savepoint:
                        # T1525: THE ROLLBACK IS WHAT MAKES THE RETRY SAFE, so a rollback that
                        # fails must stop the retry instead of being swallowed (it was, until
                        # 2026-09-12). A SQLite busy does not imply the transaction rolled back -
                        # a busy raised by `commit()` with a statement still in progress leaves it
                        # OPEN, with this attempt's own rows visible inside it. Re-running `fn()`
                        # on a session whose rollback failed would therefore build the second
                        # attempt on top of the first attempt's uncommitted writes. Surface the
                        # original database error, with the rollback failure as its cause.
                        try:
                            await self.session.rollback()
                        except Exception as rollback_error:
                            logger.error(
                                "event=payment.uow_retry_rollback_failed op=%s attempt=%s "
                                "error_type=%s rollback_error_type=%s",
                                op,
                                attempt,
                                type(exc).__name__,
                                type(rollback_error).__name__,
                            )
                            raise exc from rollback_error

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
                        pgcode,
                    )
                    await asyncio.sleep(delay)
        finally:
            self._advisory_lock_timeout_enabled = previous_timeout_enabled
            self._advisory_lock_deadline = previous_deadline

    async def _get_tx(self, tx_id: str) -> Transaction | None:
        return (
            await self.session.execute(
                select(Transaction)
                .where(Transaction.tx_id == tx_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _get_segment_capacity_and_reserved_usage(
        self,
        *,
        tx_id: str,
        sender_id: UUID,
        receiver_id: UUID,
        equivalent_id: UUID,
    ) -> tuple[Decimal, Decimal]:
        """Return current capacity and persisted reservations for one flow edge."""
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

        relevant_locks = (
            (
                await self.session.execute(
                    select(PrepareLock).where(
                        and_(
                            PrepareLock.participant_id == sender_id,
                            PrepareLock.expires_at > func.now(),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        reserved_usage = Decimal("0")
        for lock in relevant_locks:
            if lock.tx_id == tx_id:
                continue
            for flow in (lock.effects or {}).get("flows", []):
                try:
                    if UUID(flow["equivalent"]) != equivalent_id:
                        continue
                    flow_sender_id = UUID(flow["from"])
                    flow_receiver_id = UUID(flow["to"])
                    flow_amount = Decimal(str(flow["amount"]))
                except Exception:
                    continue
                if flow_sender_id == sender_id and flow_receiver_id == receiver_id:
                    reserved_usage += flow_amount

        available_capacity = limit - amount_s_owes_r + amount_r_owes_s
        return available_capacity, reserved_usage

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

            await self._acquire_equivalent_owner_locks([equivalent_id])
            await self._acquire_tx_advisory_lock(tx_id)
            tx = await self._get_tx(tx_id)
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")

            if tx.state == "COMMITTED":
                if commit:
                    await self.session.commit()
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
            stmt = (
                select(PrepareLock)
                .where(PrepareLock.tx_id == tx_id)
                .execution_options(populate_existing=True)
            )
            result = await self.session.execute(stmt)
            existing_locks = result.scalars().all()
            if existing_locks:
                if tx.state == "PREPARED":
                    if commit:
                        await self.session.commit()
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

            await self._acquire_segment_advisory_locks(
                equivalent_id=equivalent_id,
                routes=[(path, amount)],
                participant_map=participant_map,
            )

            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.lock_ttl_seconds
            )

            for i in range(len(path) - 1):
                sender_pid = path[i]
                receiver_pid = path[i + 1]

                sender_id = participant_map[sender_pid]
                receiver_id = participant_map[receiver_pid]

                available_capacity, reserved_usage = (
                    await self._get_segment_capacity_and_reserved_usage(
                        tx_id=tx_id,
                        sender_id=sender_id,
                        receiver_id=receiver_id,
                        equivalent_id=equivalent_id,
                    )
                )

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
            if commit:
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

            await self._acquire_equivalent_owner_locks([equivalent_id])
            await self._acquire_tx_advisory_lock(tx_id)
            tx = await self._get_tx(tx_id)
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")

            if tx.state == "COMMITTED":
                if commit:
                    await self.session.commit()
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
                        select(PrepareLock)
                        .where(PrepareLock.tx_id == tx_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            if existing_locks:
                if tx.state == "PREPARED":
                    if commit:
                        await self.session.commit()
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

                    available_capacity, reserved_usage = (
                        await self._get_segment_capacity_and_reserved_usage(
                            tx_id=tx_id,
                            sender_id=sender_id,
                            receiver_id=receiver_id,
                            equivalent_id=equivalent_id,
                        )
                    )

                    local_key = (sender_id, receiver_id, equivalent_id)
                    reserved_usage += local_reserved.get(local_key, Decimal("0"))

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
            if commit:
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

            owner_preflight = await self._preacquire_equivalent_owner_locks_for_tx(
                tx_id,
                allow_malformed=False,
            )
            await self._acquire_tx_advisory_lock(tx_id)
            tx = await self._get_tx(tx_id)
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")

            if tx.state == "COMMITTED":
                if commit:
                    await self.session.commit()
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
            locks = await self._load_prepare_locks(tx_id)

            if not locks:
                # A concurrent commit may have removed the locks after our initial
                # transaction-state read. Refresh before preserving the no-lock error.
                tx_latest = (
                    await self.session.execute(
                        select(Transaction)
                        .where(Transaction.tx_id == tx_id)
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if tx_latest is not None and tx_latest.state == "COMMITTED":
                    if commit:
                        await self.session.commit()
                    try:
                        PAYMENT_EVENTS_TOTAL.labels(
                            event="commit", result="already_committed"
                        ).inc()
                    except Exception:
                        pass
                    return True
                if tx_latest is not None and tx_latest.state in {"ABORTED", "REJECTED"}:
                    raise ConflictException(
                        f"Transaction {tx_id} is {tx_latest.state}"
                    )
                if tx_latest is not None and tx_latest.state != "PREPARED":
                    raise ConflictException(
                        f"Transaction {tx_id} is not prepared (state={tx_latest.state})"
                    )
                raise GeoException(f"No locks found for transaction {tx_id}")

            # Prepare and commit must serialize on the same globally ordered segment
            # keys. Otherwise commit can update Debt and delete PrepareLocks between a
            # concurrent prepare's debt reads and reservation read under READ COMMITTED.
            validated_locks = self._parse_persisted_prepare_locks(locks)
            if owner_preflight is not None:
                preliminary_validated_locks, preliminary_malformed = owner_preflight
                if preliminary_malformed or (
                    validated_locks != preliminary_validated_locks
                ):
                    # Never acquire a newly appeared equivalent owner after the tx key.
                    raise _EquivalentOwnerPreflightChanged()
            commit_segment_keys = self._segment_lock_keys_from_validated_flows(
                validated_locks
            )
            await self._acquire_segment_advisory_lock_keys(commit_segment_keys)

            # The advisory wait may have allowed another same-tx commit/abort to finish.
            # Refresh both state and locks before applying any persisted effects.
            tx = (
                await self.session.execute(
                    select(Transaction)
                    .where(Transaction.tx_id == tx_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if not tx:
                raise GeoException(f"Transaction {tx_id} not found")
            if tx.state == "COMMITTED":
                if commit:
                    # Release advisory keys acquired by this idempotent waiter.
                    await self.session.commit()
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

            locks = await self._load_prepare_locks(tx_id)
            if not locks:
                raise GeoException(f"No locks found for transaction {tx_id}")
            refreshed_validated_locks = self._parse_persisted_prepare_locks(locks)
            refreshed_segment_keys = self._segment_lock_keys_from_validated_flows(
                refreshed_validated_locks
            )
            if (
                refreshed_validated_locks != validated_locks
                or refreshed_segment_keys != commit_segment_keys
            ):
                # Never acquire newly appeared keys out of the established global order.
                raise GeoException()
            validated_locks = refreshed_validated_locks

            # Optimistic-lock retries in _apply_flow may expire the identity map.
            # Preserve the audit input as plain data before those retries so a
            # successful payment cannot silently lose its integrity audit row to
            # an implicit async ORM refresh (MissingGreenlet).
            raw_tx_payload = tx.payload
            tx_payload = dict(raw_tx_payload) if isinstance(raw_tx_payload, dict) else {}

            # FIX-014: capture integrity checksums before applying flows.
            checkpoints_before: dict[UUID, object] = {}
            try:
                affected_eq_ids = {
                    flow.equivalent_id
                    for lock in validated_locks
                    for flow in lock.flows
                }
                for eq_id in affected_eq_ids:
                    try:
                        checkpoints_before[eq_id] = (
                            await compute_integrity_checkpoint_for_equivalent(
                                self.session,
                                equivalent_id=eq_id,
                            )
                        )
                    except DBAPIError:
                        # PostgreSQL aborts the whole transaction after a database
                        # error. Let the UoW retry classifier see the original
                        # SQLSTATE instead of continuing into a misleading 25P02.
                        raise
                    except Exception as exc:
                        logger.warning(
                            "event=payment.audit_checkpoint_before_failed "
                            "tx_id=%s error_type=%s",
                            tx_id,
                            type(exc).__name__,
                        )
                        continue
            except DBAPIError:
                raise
            except Exception as exc:
                logger.warning(
                    "event=payment.audit_checkpoint_before_failed "
                    "tx_id=%s error_type=%s",
                    tx_id,
                    type(exc).__name__,
                )
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
                await self.abort(
                    tx_id,
                    reason="Prepare locks expired before commit",
                    commit=commit,
                    _tx_lock_already_held=True,
                    _equivalent_owner_locks_already_held=True,
                )
                raise ConflictException(f"Transaction {tx_id} expired before commit")

            # T1544: the operator's equivalent-level stop, IMMEDIATELY BEFORE THE ENVELOPE - after
            # every lock this commit takes, every idempotent short-circuit, the audit checkpoint reads
            # and the TTL branch above. Below the TTL branch on purpose: an expired payment is aborted
            # as expired, whatever state its equivalent is in, and the equivalent row is not held
            # through checkpoint work that does not need it. `FOR SHARE` holds through this
            # transaction's commit (or through the caller's outer commit when `commit=False`), so a
            # deactivating PATCH either waits for this payment or makes this read fail with 40001 and
            # the retry refuse. Why a plain read is not enough is in `refuse_inactive_equivalents`.
            try:
                await self.refuse_inactive_equivalents(
                    self._equivalent_ids_from_validated_locks(validated_locks),
                    row_lock=True,
                )
            except ConflictException as refusal:
                # Same shape as the expired-lock branch above: nothing has been written yet, so the
                # payment is terminalised under the locks already held and the refusal surfaces.
                await self.abort(
                    tx_id,
                    reason=refusal.message,
                    error_code=refusal.code,
                    details=refusal.details,
                    commit=commit,
                    _tx_lock_already_held=True,
                    _equivalent_owner_locks_already_held=True,
                )
                raise

            # STEP 5b: the pre-state of BOTH directions of every flow pair, in one read, here - after
            # every lock this commit takes and the operator stop above, inside this retryable unit of
            # work - and recorded in the envelope below. Why here and nothing earlier or later:
            # `_read_payment_prestate`.
            payment_prestate = await self._read_payment_prestate(validated_locks)

            # THE OPERATION ENVELOPE (programme 015, phase B step 4). Everything from here to the
            # deletion of the prepare locks is one declared debt operation: what this payment said
            # it was about to do, recorded before it does it.
            #
            # WHERE IT OPENS. After the TTL branch above, because an expired payment aborts and
            # writes no debts at all, and an envelope for it would record an intent nothing ever
            # carried out.
            #
            # WHERE IT CLOSES, and this is load-bearing rather than tidy: BEFORE
            # `delete(PrepareLock)` below. `Book.operation` INSERTs and flushes the envelope at
            # open, so the row is already on this connection when the locks are deleted. The
            # prepare locks are the only authoritative statement of what this payment was allowed
            # to do; once they are gone, an envelope not yet written could never be reconstructed,
            # and a crash between the two statements would leave a payment whose authority is
            # deleted and whose journal never began.
            #
            # THE INTENT is the validated flows per lock - the rows `_parse_persisted_prepare_locks`
            # validated, as exact scale-8 strings - plus the tx id. Not the outcome: an intent read
            # back from the result cannot disagree with it, and being able to disagree is the
            # entire reason it is recorded.
            _intent_equivalent_ids = self._equivalent_ids_from_validated_locks(
                validated_locks
            )
            async with Book.operation(
                self.session,
                operation_for(
                    "PAYMENT",
                    tx_id,
                    tx_id=tx_id,
                    intent={
                        "tx_id": tx_id,
                        "locks": [
                            {
                                "lock_id": str(lock.lock_id),
                                "flows": [
                                    {
                                        "from": str(flow.from_id),
                                        "to": str(flow.to_id),
                                        "amount": _scale8_money(flow.amount),
                                        "equivalent": str(flow.equivalent_id),
                                    }
                                    for flow in lock.flows
                                ],
                            }
                            for lock in validated_locks
                        ],
                        # Intent encoding version 2 (step 5b): both directions of every flow pair, as
                        # they stood before the first flow ran. Without it the netted deltas cannot be
                        # recomputed from the envelope - `_apply_flow` reads the reverse debt.
                        "prestate": payment_prestate,
                    },
                    scope_equivalent_ids=_intent_equivalent_ids,
                    intent_equivalent_ids=_intent_equivalent_ids,
                ),
            ):
                # 2. Process each lock (segment)
                flows_by_equivalent: dict[UUID, list[tuple[UUID, UUID, Decimal]]] = {}
                affected_pids_by_equivalent: dict[UUID, set[UUID]] = {}
                flows_parsed_by_lock: list[list[tuple[UUID, UUID, Decimal, UUID]]] = []

                for lock in validated_locks:
                    parsed = [
                        (flow.from_id, flow.to_id, flow.amount, flow.equivalent_id)
                        for flow in lock.flows
                    ]
                    for from_id, to_id, amount, equivalent_id in parsed:
                        flows_by_equivalent.setdefault(equivalent_id, []).append(
                            (from_id, to_id, amount)
                        )
                        affected = affected_pids_by_equivalent.setdefault(
                            equivalent_id, set()
                        )
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

                # 2a. Invariants: trust limits + debt symmetry.
                #
                # The zero-sum call that stood here was removed by T1402 of programme 014. It scanned
                # the whole equivalent on every payment and could not fail: `_compute_imbalance` sums
                # the same `Debt` rows grouped by creditor and by debtor and returns the difference,
                # which telescopes to zero for any row set. Aborting a payment on it was therefore
                # impossible, and 008 required the call removed from this path
                # (`008/tasks.md:305,311-321`). Nothing replaces it here: the replacement invariant is
                # programme 015, and this path must not pretend to a check it is not making.
                from app.core.invariants import InvariantChecker
                from app.utils.exceptions import IntegrityViolationException

                checker = InvariantChecker(self.session)
                try:
                    for eq_id, pairs in affected_pairs_by_equivalent.items():
                        await checker.check_trust_limits(
                            equivalent_id=eq_id, participant_pairs=list(pairs)
                        )
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
                        _tx_lock_already_held=not commit,
                        _equivalent_owner_locks_already_held=not commit,
                    )
                    raise

                # FIX-014: write integrity audit trail per equivalent (best-effort).
                try:
                    payload = tx_payload
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
                            passed = bool(invariants_status.get("passed", False))

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
                        except DBAPIError:
                            # A swallowed DB error poisons the live transaction. The
                            # retry wrapper must receive the original SQLSTATE.
                            raise
                        except Exception as exc:
                            logger.warning(
                                "event=payment.audit_log_failed tx_id=%s error_type=%s",
                                tx_id,
                                type(exc).__name__,
                            )
                            continue
                except DBAPIError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "event=payment.audit_log_failed tx_id=%s error_type=%s",
                        tx_id,
                        type(exc).__name__,
                    )

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
            if commit:
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
        """Apply flow of `amount` from `from_id` to `to_id` - through the book.

        The algebra (reduce the receiver's debt to the sender, grow the sender's debt, net a mutual
        pair, delete a zero) and its `StaleDataError` retry loop live in
        `app/core/ledger/book.py` since programme 018 stage A, the single writer of `debts`. This
        method stays as the forwarding point on purpose: tests perturb the payment path by patching
        it, and they must keep executing their perturbation (018 `T1802`).
        """
        await Book.current(self.session).apply(
            PaymentFlow(from_id=from_id, to_id=to_id, amount=amount, equivalent_id=equivalent_id)
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
        return_outcome: bool = False,
        error_code: ErrorCode | str | None = None,
        details: dict[str, Any] | None = None,
        _tx_lock_already_held: bool = False,
        _equivalent_owner_locks_already_held: bool = False,
    ):
        """
        Abort transaction: Delete locks, set state to ABORTED.

        By default the return value remains the legacy ``True``. Staged owners may
        request the lock-protected terminal outcome so they can publish effects
        only after their outer transaction becomes durable.
        """

        attempt_index = 0

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
            nonlocal attempt_index
            logger.info("event=payment.abort tx_id=%s reason=%s", tx_id, reason)
            try:
                PAYMENT_EVENTS_TOTAL.labels(event="abort", result="start").inc()
            except Exception:
                pass

            reuse_outer_owner_locks = _equivalent_owner_locks_already_held and (
                not commit or attempt_index == 0
            )
            reuse_outer_tx_lock = _tx_lock_already_held and (
                not commit or attempt_index == 0
            )
            attempt_index += 1
            owner_preflight = None
            if not reuse_outer_owner_locks:
                owner_preflight = (
                    await self._preacquire_equivalent_owner_locks_for_tx(
                        tx_id,
                        allow_malformed=True,
                    )
                )
            if not reuse_outer_tx_lock:
                await self._acquire_tx_advisory_lock(tx_id)
            tx = await self._get_tx(tx_id)
            if tx and tx.state == "COMMITTED":
                await self.session.execute(
                    delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
                )
                if commit:
                    # Preserve the public commit=True transaction boundary.
                    await self.session.commit()
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="abort", result="already_committed"
                    ).inc()
                except Exception:
                    pass
                return "already_committed" if return_outcome else True

            initial_locks = await self._load_prepare_locks(tx_id)
            initial_validated_locks: tuple[_ValidatedPrepareLock, ...] = ()
            initial_segment_keys: set[int] = set()
            initial_locks_malformed = False
            terminal_aborted = bool(tx and tx.state == "ABORTED")
            if initial_locks:
                try:
                    initial_validated_locks = self._parse_persisted_prepare_locks(
                        initial_locks
                    )
                except GeoException:
                    # Abort never applies persisted effects. Legacy/malformed locks are
                    # recoverable under the tx-scoped lock without segment keys.
                    initial_locks_malformed = True
                else:
                    initial_segment_keys = self._segment_lock_keys_from_validated_flows(
                        initial_validated_locks
                    )
                    if owner_preflight is not None and not terminal_aborted:
                        preliminary_validated_locks, preliminary_malformed = (
                            owner_preflight
                        )
                        if preliminary_malformed or (
                            initial_validated_locks != preliminary_validated_locks
                        ):
                            raise _EquivalentOwnerPreflightChanged()
                    await self._acquire_segment_advisory_lock_keys(initial_segment_keys)
            elif (
                owner_preflight is not None
                and not terminal_aborted
                and owner_preflight != ((), False)
            ):
                raise _EquivalentOwnerPreflightChanged()

            if (
                initial_locks_malformed
                and owner_preflight is not None
                and not terminal_aborted
                and owner_preflight != ((), True)
            ):
                raise _EquivalentOwnerPreflightChanged()

            # A competing commit/abort may have completed while we waited. Terminal
            # state wins; active state requires the exact same authoritative lock flows.
            tx = (
                await self.session.execute(
                    select(Transaction)
                    .where(Transaction.tx_id == tx_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            refreshed_locks = await self._load_prepare_locks(tx_id)

            if tx and tx.state == "COMMITTED":
                await self.session.execute(
                    delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
                )
                if commit:
                    # Release advisory keys acquired before the terminal re-check.
                    await self.session.commit()
                try:
                    PAYMENT_EVENTS_TOTAL.labels(
                        event="abort", result="already_committed"
                    ).inc()
                except Exception:
                    pass
                return "already_committed" if return_outcome else True

            existing_error: dict[str, Any] = (
                tx.error if tx and isinstance(tx.error, dict) else {}
            ) or {}
            normalized_code = _normalize_code(error_code or existing_error.get("code"))
            normalized_details: dict[str, Any] = (
                details
                if details is not None
                else (
                    existing_error.get("details")
                    if isinstance(existing_error.get("details"), dict)
                    else {}
                )
            ) or {}
            error_payload: dict[str, Any] = {
                "code": normalized_code.value,
                "message": str(
                    existing_error.get("message")
                    or reason
                    or ERROR_MESSAGES[normalized_code]
                ),
                "details": normalized_details,
            }

            if tx and tx.state == "ABORTED":
                # Idempotent terminal state wins over the pre-wait lock snapshot.
                await self.session.execute(
                    delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
                )
                await self.session.execute(
                    update(Transaction)
                    .where(Transaction.tx_id == tx_id)
                    .values(error=error_payload)
                )
                if commit:
                    await self.session.commit()
                else:
                    await self.session.flush()
                if commit:
                    try:
                        PAYMENT_EVENTS_TOTAL.labels(
                            event="abort", result="already_aborted"
                        ).inc()
                    except Exception:
                        pass
                return "already_aborted" if return_outcome else True

            refreshed_validated_locks: tuple[_ValidatedPrepareLock, ...] = ()
            refreshed_segment_keys: set[int] = set()
            refreshed_locks_malformed = False
            if refreshed_locks:
                try:
                    refreshed_validated_locks = self._parse_persisted_prepare_locks(
                        refreshed_locks
                    )
                except GeoException:
                    refreshed_locks_malformed = True
                else:
                    refreshed_segment_keys = self._segment_lock_keys_from_validated_flows(
                        refreshed_validated_locks
                    )
            if initial_locks_malformed != refreshed_locks_malformed:
                raise GeoException()
            if not initial_locks_malformed and (
                refreshed_validated_locks != initial_validated_locks
                or refreshed_segment_keys != initial_segment_keys
            ):
                # Do not acquire newly appeared keys outside the established order.
                raise GeoException()

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
            if commit:
                try:
                    PAYMENT_EVENTS_TOTAL.labels(event="abort", result="success").inc()
                except Exception:
                    pass
            return "success" if return_outcome else True

        if not commit:
            return await self._run_uow_with_retry(op="abort_nocommit", fn=_uow, use_savepoint=True)
        return await self._run_uow_with_retry(op="abort", fn=_uow)
