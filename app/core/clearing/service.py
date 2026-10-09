import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import AbstractSet, Dict, List, Set

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.models.audit_log import IntegrityAuditLog
from app.utils.exceptions import ConflictException, GeoException, TimeoutException
from app.utils.metrics import CLEARING_EVENTS_TOTAL
from app.utils.money import to_money_str
from app.utils.validation import money_step
from app.core.money_boundary import IsolationNotReadCommitted, MoneyBoundary
from app.core.invariants import InvariantChecker
from app.core.ledger.book import Book, ClearingReduction, operation_for
from app.db.journal_tables import CLEARING_INTENT_ENCODING_VERSION
from app.db.sqlstate import ROLLED_BACK_SQLSTATES, chain_codes

logger = logging.getLogger(__name__)

# Trust-line statuses whose consent clearing reads. `frozen` was admitted by T1551 (2026-09-13) and no longer
# exists (028 `F-028-29`, owner В-2: a line is `active` or `closed`; the freeze lives on the participant, and an
# occurrence through a suspended one is skipped at execution, `F-028-28`). `closed` stays excluded. ONE tuple for
# the planner's snapshot (`flow_planner.load_snapshot`) and for `_cycle_respects_auto_clearing`, so planning and
# execution cannot disagree. (Until 035 A2b the SQL cycle detectors read it too; they are removed.)
_CLEARABLE_TRUSTLINE_STATUSES = ("active",)


class ClearingCommittedAfterCancellation(asyncio.CancelledError):
    """Cancellation raised only after the clearing commit became durable."""

    def __init__(self, *, tx_id: str, cleared_amount: Decimal):
        super().__init__("Clearing committed while cancellation was pending")
        self.tx_id = tx_id
        self.cleared_amount = cleared_amount


#: Programme 023 slice (b), decision 6: the namespace of a v2 plan occurrence's identity. Its own and versioned,
#: so a v2 id is never a v1 set-hash (the v1 namespace left the application with the v1 writer, 024 `T2417`;
#: historical v1 rows are read, never written). `app/core/ledger/reconciliation.py` holds an independent copy and
#: derives the id itself.
_CLEARING_OCCURRENCE_V2_NAMESPACE = uuid.UUID("5f3c2e7a-0d6b-4a53-9e8f-023b00000002")


#: `details.reason` of the executor's refusal of an amount that is not a multiple of the equivalent's step (030
#: `F-030-1`): a database holding debts finer than the step, to be reseeded. Not a money stop (`MONEY_STOP_REASONS`).
OCCURRENCE_AMOUNT_NOT_IN_STEP = "occurrence_amount_not_in_step"


class ClearingOccurrenceRefused(ConflictException):
    """A v2 plan occurrence that cannot run as declared, or whose id is already committed with another descriptor.

    Also the refusal of an execution that carries no occurrence at all (`occurrence_missing`, 024 `T2417`): the
    shared boundary runs only inside `ClearingService.execute_occurrence`.

    Programme 023 slice (b), decision 6: the same occurrence id with a different intent is a refusal, not a
    replay. Raised with nothing changed (the attempt is rolled back first), and never retried - a descriptor
    does not become right by trying again. `details.reason` names which check refused it.
    """

    def __init__(self, reason: str):
        super().__init__(
            f"Clearing occurrence refused: {reason}",
            details={"retryable": False, "reason": reason, "operation": "clearing"},
        )
        self.reason = reason


@dataclass(frozen=True)
class ClearingOccurrence:
    """The immutable descriptor of one cycle of a clearing plan (programme 023 slice (b), decisions 5-6).

    `debt_ids` is the cycle in its order (debtor -> creditor of one edge is the debtor of the next), unique,
    three or more (the planner never emits a 2-cycle: the book nets opposing debt). `amount_atoms` is the
    declared `c` in atoms of 1e-8 - an int, never a float - fixed before any mutation. The occurrence id is
    `uuid5(v2 namespace, "plan:equivalent:ordinal")`: two plans that clear equal amounts on the same surviving
    debts are two occurrences; a replay of one occurrence is recognised by its id and CHECKED against the
    committed descriptor. Construction validates every field and raises `ValueError` before any work.
    """

    plan_id: uuid.UUID
    equivalent_id: uuid.UUID
    ordinal: int
    debt_ids: tuple[uuid.UUID, ...]
    amount_atoms: int

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, uuid.UUID) or not isinstance(self.equivalent_id, uuid.UUID):
            raise ValueError("a clearing occurrence needs a plan UUID and an equivalent UUID")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError(f"a cycle ordinal is a non-negative int, got {self.ordinal!r}")
        if (
            not isinstance(self.debt_ids, tuple)
            or len(self.debt_ids) < 3
            or not all(isinstance(debt_id, uuid.UUID) for debt_id in self.debt_ids)
            or len(set(self.debt_ids)) != len(self.debt_ids)
        ):
            raise ValueError("a cycle is a tuple of three or more unique debt UUIDs")
        if isinstance(self.amount_atoms, bool) or not isinstance(self.amount_atoms, int) or self.amount_atoms <= 0:
            raise ValueError(f"the declared amount is a positive int of atoms, got {self.amount_atoms!r}")

    @property
    def occurrence_id(self) -> str:
        return str(
            uuid.uuid5(
                _CLEARING_OCCURRENCE_V2_NAMESPACE, f"{self.plan_id}:{self.equivalent_id}:{self.ordinal}"
            )
        )

    @property
    def amount(self) -> Decimal:
        """`c` as money: exact, scale 8."""

        return Decimal(self.amount_atoms).scaleb(-8)

    def descriptor(self) -> dict:
        """The JSON form recorded in the intent and in `Transaction.payload` (the amount as a digit string)."""

        return {
            "version": 2,
            "plan_id": str(self.plan_id),
            "equivalent_id": str(self.equivalent_id),
            "ordinal": self.ordinal,
            "debt_ids": [str(debt_id) for debt_id in self.debt_ids],
            "amount_atoms": str(self.amount_atoms),
        }


class RetryableClearingConflictException(ConflictException):
    """The clearing's retry budget was spent on transaction-level conflicts; nothing was committed.

    019 stage 5 (`T1907`, `FORK-4`): the typed retryable refusal of an exhausted clearing - the same public
    shape as a payment's (`409/E008`, `details.retryable`), so a caller can tell "try again" from an
    internal error. No occurrence, envelope, journal entry or audit row of the clearing exists.
    """

    def __init__(self, message: str | None = None):
        super().__init__(
            message or "Clearing conflicted with concurrent money writers; retry",
            details={
                "retryable": True,
                "conflict_kind": "database_concurrency",
                "operation": "clearing",
            },
        )


class _ClearingAttemptConflict(Exception):
    """One attempt met a transaction-level conflict and no committed occurrence answers it.

    Internal to `ClearingService`: raised only after the attempt's transaction has been rolled back (by
    `_reconcile_committed_execution` or `_rollback_skipped_execution`), caught only by the retry owner
    `_run_attempts`; it never leaves `execute_clearing_with_amount`.
    """

    def __init__(self, cause: BaseException):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause


class ClearingService:
    """Clearing detection and execution of one plan occurrence, on the caller's `AsyncSession`.

    WHO OWNS THE TRANSACTION (032 `P-2`) differs by method, not by class:

    * Nothing here only reads any more: the cycle detectors were removed 2026-10-09 (035 A2b), and what
      a pass would close is read by the planner (`flow_planner`), not by this class.
    * `execute_occurrence` (and `execute_clearing_with_amount`, which only it reaches) OWNS the
      session's transaction and ends it on every path: success commits it
      (`_commit_to_terminal`); a skip, a replay of an occurrence that is already committed, a refusal
      and every failure roll it back (`_rollback_skipped_execution`, `_raise_unexpected_execution`,
      the commit resolver). A retryable conflict runs the next attempt on the same session after
      `expunge_all`, each attempt its own transaction (`_run_attempts`). Anything the caller had
      pending on that session is committed or rolled back with it, so hand it a session that carries
      no other work.
    """

    #: The v2 occurrence being executed (`execute_occurrence`); None outside it, and then the boundary refuses
    #: (024 `T2417`: execution without an occurrence is removed). Instance state on
    #: purpose: every replay resolver reads it, so no path of the
    #: boundary can drop the descriptor the way a forgotten keyword would.
    _occurrence: ClearingOccurrence | None = None
    #: The cycle's lines this attempt locked; its consent is read from them only (027 stage 2, §15 P1).
    _locked_lines: list | None = None

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _raise_unexpected_execution(self, exc: Exception) -> None:
        """Rollback and surface one sanitized unexpected clearing failure.

        A `ClearingOccurrenceRefused` is not unexpected: it is rolled back the same way and raised AS IS, so the
        caller of a v2 occurrence sees the refusal and its reason rather than `E010`.
        """
        if isinstance(exc, ClearingOccurrenceRefused):
            logger.warning("event=clearing.occurrence_refused reason=%s", exc.reason)
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="refused").inc()
            except Exception:
                pass
            try:
                await self.session.rollback()
            except Exception:
                logger.exception("event=clearing.rollback_failed")
            raise exc
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

    async def _refuse_if_equivalent_inactive(self, equivalent_ids: Set[uuid.UUID]) -> None:
        """T1544: clearing does not run in an equivalent the operator has deactivated (and, T1546, not
        in one under an integrity hold).

        A refusal, not a skip: `None` would let the caller finish with a successful zero result and
        hide the stop. It ends the attempt first, like a skip, and surfaces `409/E008`.

        THE READ IS `FOR SHARE` (019 stage 5, `T1907`, `FORK-7`), in every attempt, and the row lock is
        held through the clearing's commit. That is what makes the cutoff observable: a deactivating
        `PATCH` or the reaction setting a hold `UPDATE`s this row, so it waits for a clearing that has
        already read `active`; and a clearing that reads while such an update is in flight waits for it
        and, under READ COMMITTED (027 stage 2), reads the stop and refuses. Order: the cycle's pair lines
        -> this row -> the debt rows, the same as the payment's.
        """
        try:
            await MoneyBoundary(self.session).refuse_inactive_equivalents(equivalent_ids)
        except ConflictException as refusal:
            # Step 5c: the same helper also refuses an integrity hold; the reason names which.
            logger.info("event=clearing.refused_%s", (refusal.details or {}).get("reason"))
            await self._rollback_skipped_execution()
            raise
        except Exception as exc:
            if self._is_retryable_concurrency_error(exc):
                # A stop or hold committed after this attempt's snapshot: the next attempt reads it.
                await self._rollback_skipped_execution()
                raise _ClearingAttemptConflict(exc) from exc
            await self._raise_unexpected_execution(exc)

    async def _end_attempt_on_error(
        self,
        exc: Exception,
        execution_tx_id: str,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal:
        """THE ONE CLASSIFIER of a failure inside a clearing attempt (019 stage 5, `T1909` step 3).

        A transaction-level conflict (40001/40P01, `_is_retryable_concurrency_error`) anywhere in the
        attempt ends it the same way: a committed occurrence of this execution, if the resolver finds
        one, is the answer; otherwise the attempt is rolled back (by the resolver) and
        `_ClearingAttemptConflict` carries the ORIGINAL error, with its SQLSTATE, to the retry owner
        `_run_attempts`. Anything else is the sanitized unexpected failure (`E010`). Until `T1909` only
        four call sites converted conflicts, and the policy, metadata and net-position reads went
        straight to `E010` (5a review, P2). An unresolved COMMIT error is a conflict only when PostgreSQL
        reported one (a rollback it performed), so an unknown commit is never retried here.
        """
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
            raise _ClearingAttemptConflict(exc) from exc
        await self._raise_unexpected_execution(exc)
        raise AssertionError("unreachable")  # pragma: no cover - the call above always raises

    @staticmethod
    async def _drain_task(
        task: asyncio.Task, *, surface_result: bool = True
    ) -> asyncio.CancelledError | None:
        """Wait through repeated caller cancellation and return its first pulse.

        `surface_result=False` (020 stage 1, the commit-resolution block only): return the pulse
        WITHOUT reading the task's result, so a task that failed cannot take the caller's
        cancellation down with its error; the caller reads `task.result()` itself.
        """

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
        if surface_result:
            task.result()
        return caller_cancellation

    @staticmethod
    async def _read_committed_execution_amount(
        session: AsyncSession,
        tx_id: str,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
        occurrence: ClearingOccurrence,
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
        # 023 slice (b), decision 6: a v2 id is a replay only of the SAME intent. The committed
        # descriptor is compared whole, and the committed amount must be the one it declares.
        if (transaction.payload or {}).get("occurrence") != occurrence.descriptor():
            raise ClearingOccurrenceRefused("occurrence_descriptor_mismatch")
        if amount != occurrence.amount:
            logger.error("event=clearing.replay_amount_differs_from_descriptor tx_id=%s", tx_id)
            raise ClearingOccurrenceRefused("occurrence_amount_mismatch")
        return amount

    async def _committed_execution_amount(
        self,
        tx_id: str,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        return await self._read_committed_execution_amount(
            self.session,
            tx_id,
            allowed_participant_pids=allowed_participant_pids,
            occurrence=self._occurrence,
        )

    @staticmethod
    def _postgres_error_codes(exc: BaseException) -> set[str]:
        """Codes carried by THIS failure: `orig` / `__cause__` only, never `__context__` (`app/db/sqlstate.py`).

        The consumer is `_is_retryable_concurrency_error`; it receives the `DBAPIError` itself. That a real `55P03` and a real `40001` are still found through
        the deliberate walk is measured by
        `tests/integration/test_p015_t1525_classification_reads_deliberate_wrapping_only_postgres.py`.
        """

        return chain_codes(exc)

    @classmethod
    def _is_retryable_concurrency_error(cls, exc: BaseException) -> bool:
        # Retryable is what the SERVER ended by rolling the transaction back (`ROLLED_BACK_SQLSTATES`,
        # `app/db/sqlstate.py`: 40001, 40P01), found on the deliberate chain only (`_postgres_error_codes`).
        # Nothing else is retried here.
        # REMOVED 2026-10-09 (035 A7): a twenty-line note on why SQLite's busy-snapshot refusal was not in this
        # set and what each caller then answered. SQLite left the application in programme 017, so the note
        # described no reachable case; its text is in `git log -S "SQLITE_BUSY_SNAPSHOT" -- <this file>`.
        return bool(cls._postgres_error_codes(exc) & ROLLED_BACK_SQLSTATES)

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
                        occurrence=self._occurrence,
                    )
            except ClearingOccurrenceRefused:
                # A durable occurrence with another descriptor is a fact, not a transient read failure.
                raise
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

    # REMOVED 2026-10-09 (035 A2b): the cycle detectors that predate the flow planner - `find_cycles` (SQL fast path
    # plus the DFS), `find_triangles_sql`, `find_quadrangles_sql` - and the helpers only they used (`_bind_uuid`,
    # `_scope_predicate`, `_scope_binds`, `_sql_auto_clearing_ok`, `_debt_id_key`, `_cycle_order_key`,
    # `_deduplicate_cycles`, `_equivalent_precision`, `_filter_cycles_by_auto_clearing_policy_sql`). Nothing executed
    # from them since programme 023; `GET /clearing/cycles` left them in 035 A1 and the seed and the tests in A2a.
    # What a clearing pass or the diagnostic may consider is the planner's snapshot (`flow_planner.load_snapshot`);
    # their history is `git log -S "find_triangles_sql" -- app/core/clearing/service.py`.

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
        if self._locked_lines is not None:  # inside an attempt: ONLY the locked rows (027 stage 2, §15 P1)
            tl_by_pair = {(r.from_participant_id, r.to_participant_id): r for r in self._locked_lines
                          if r.equivalent_id == equivalent_id and r.status in _CLEARABLE_TRUSTLINE_STATUSES}
            return all(pair in tl_by_pair and self._policy_flag(tl_by_pair[pair].policy, "auto_clearing", default=True)
                       for pair in required_pairs)

        trustlines = (
            (
                await self.session.execute(
                    select(TrustLine).where(
                        and_(
                            TrustLine.equivalent_id == equivalent_id,
                            TrustLine.status.in_(_CLEARABLE_TRUSTLINE_STATUSES),
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

        A flag may arrive as a string; common falsy string forms are treated as False.
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

    async def _run_attempts(self, cycle: List[Dict], **attempt_kwargs) -> Decimal | None:
        """THE RETRY OWNER of one clearing execution (019 stage 5, `T1907`, `FORK-4`).

        Runs `_execute_clearing_with_amount` and, when an attempt meets a transaction-level conflict
        (40001/40P01) that no committed occurrence answers, runs the WHOLE execution again in a new
        transaction: the attempt has already been rolled back, its ORM state is dropped here
        (`expunge_all`), and the next attempt re-reads the committed-occurrence row, the cycle's lines and
        rows `FOR UPDATE`, the stop/hold and the amounts - never a savepoint rollback: a deadlock aborts the
        whole transaction. Since 027 stage 2 there is no equivalent lock and no pinned connection: the attempts
        run on the caller's session, each its own transaction.

        THE BUDGET is the one a payment has (no new setting): at most `COMMIT_RETRY_ATTEMPTS` attempts,
        with the same exponential backoff and jitter (`COMMIT_RETRY_BASE_DELAY_MS`,
        `COMMIT_RETRY_MAX_DELAY_MS`), and no new attempt that would start after
        `PAYMENT_TOTAL_TIMEOUT_SECONDS` from the first. Exhausted: `RetryableClearingConflictException`
        (`409/E008`, retryable) - no occurrence and no partial effect, since every attempt was rolled
        back. Outcomes that are NOT conflicts keep their paths unchanged: a committed occurrence found
        by the resolver is returned as the success it is, an unresolved commit error stays an error
        (never retried - a retry is safe only after a rollback PostgreSQL reported, which is what 40001
        and 40P01 are), and `ClearingCommittedAfterCancellation` carries the committed amount out.
        """

        from app.config import settings

        attempts = max(1, int(settings.COMMIT_RETRY_ATTEMPTS or 1))
        base_seconds = max(0.0, settings.COMMIT_RETRY_BASE_DELAY_MS / 1000.0)
        cap_seconds = max(base_seconds, settings.COMMIT_RETRY_MAX_DELAY_MS / 1000.0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (settings.PAYMENT_TOTAL_TIMEOUT_SECONDS or 10)
        attempt_no = 0
        while True:
            attempt_no += 1
            try:
                return await self._execute_clearing_with_amount(cycle, **attempt_kwargs)
            except _ClearingAttemptConflict as conflict:
                cause = conflict.cause
            self.session.expunge_all()
            codes = sorted(self._postgres_error_codes(cause) & ROLLED_BACK_SQLSTATES)
            delay = min(cap_seconds, base_seconds * (2 ** (attempt_no - 1)))
            delay *= 1.0 + 0.25 * random.random()
            if attempt_no >= attempts or loop.time() + delay >= deadline:
                logger.warning(
                    "event=clearing.retry_exhausted attempts=%s pgcode=%s", attempt_no, codes
                )
                try:
                    CLEARING_EVENTS_TOTAL.labels(event="execute", result="conflict").inc()
                except Exception:
                    pass
                raise RetryableClearingConflictException() from cause
            logger.warning(
                "event=clearing.attempt_retry attempt=%s/%s delay_s=%.3f pgcode=%s",
                attempt_no,
                attempts,
                delay,
                codes,
            )
            await asyncio.sleep(delay)

    async def execute_occurrence(
        self,
        occurrence: ClearingOccurrence,
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        """Execute one plan occurrence with its DECLARED amount (programme 023 slice (b), decisions 5-6).

        The shared boundary `execute_clearing_with_amount`, not a copy of it: the line locks of the
        cycle's pairs (027 stage 2), the retry owner `_run_attempts`, the stop/hold read `FOR SHARE`, the authoritative perimeter and
        consent checks on the rows locked `FOR UPDATE`, the commit resolver and `ClearingCommittedAfterCancellation`
        all run unchanged. What the occurrence changes: the tx id is its occurrence id; a committed occurrence
        with that id is a replay only if its recorded descriptor is this one (else `ClearingOccurrenceRefused`);
        the rows must be the descriptor's cycle in its equivalent (else refused); every edge must still hold at
        least `c` (else a stale plan: `None`, nothing changed); each edge is reduced by exactly `c` through
        `Book` (deleted at zero), under a CLEARING intent v2 envelope that criterion (b) recomputes.

        Returns `c` (or the durable amount of a verified replay), or `None` for a skip. Since slice (d) the only
        production caller is the clearing runner (`app/core/clearing/runner.py`).

        TRANSACTION: it commits the passed session when the occurrence lands and rolls it back on a skip, a
        replay, a refusal or a failure - the caller never inherits an open transaction from it, and never
        commits or rolls back around it (see the class docstring).
        """
        if not isinstance(occurrence, ClearingOccurrence):
            raise TypeError("execute_occurrence takes a ClearingOccurrence")
        if self._occurrence is not None:
            raise RuntimeError("a clearing occurrence is already executing on this service")
        self._occurrence = occurrence
        try:
            return await self.execute_clearing_with_amount(
                [{"debt_id": str(debt_id)} for debt_id in occurrence.debt_ids],
                allowed_participant_pids=allowed_participant_pids,
            )
        finally:
            self._occurrence = None

    async def execute_clearing_with_amount(
        self,
        cycle: List[Dict],
        *,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        """Execute one clearing with its retry owner (027 stage 2, `T2704`); COMMITS or ROLLS BACK the passed session.

        The session is the caller's object but not the caller's transaction: this method ends it on every
        path (success commits, a skip, a replay, a refusal and a failure roll back), and a retryable conflict
        runs the next attempt on it as a new transaction. It is not a step inside a larger transaction.

        No equivalent lock and no pinned connection any more (019 `T1909`'s exclusive session lock and its
        interlock are gone): each attempt locks the lines of the cycle's pairs, reads the stop/hold, locks the
        cycle's debt rows - in that order - and re-reads everything it decides on after those locks.

        `allowed_participant_pids` is the run perimeter (2026-08-22 / p010, `F-010-3`).  It is
        carried through EVERY path into `_execute_clearing_with_amount` on purpose: a single
        forgotten transition would be a way around the guard, and the guard is the second
        line of defence — detection is the first, and a caller may hand us a cycle that
        detection never produced.

        It runs only inside `execute_occurrence` (024 `T2417`): without an occurrence it refuses with
        `ClearingOccurrenceRefused("occurrence_missing")` before its first query - execution without an
        occurrence (v1: the locked cycle minimum under a set-hash id) is removed.
        """
        occurrence = self._occurrence
        if occurrence is None:
            await self._raise_unexpected_execution(ClearingOccurrenceRefused("occurrence_missing"))
        allowed_ids = await self._resolve_scope_ids(allowed_participant_pids)
        if allowed_participant_pids is not None and not allowed_ids:
            # An empty perimeter admits nobody; there is nothing this cycle can legally be.
            await self._raise_unexpected_execution(
                RuntimeError("Clearing cycle escaped participant scope: empty perimeter")
            )
        return await self._run_attempts(
            cycle,
            allowed_participant_ids=allowed_ids,
            allowed_participant_pids=allowed_participant_pids,
        )

    async def _execute_clearing_with_amount(
        self,
        cycle: List[Dict],
        *,
        allowed_participant_ids: "set[uuid.UUID] | None" = None,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> Decimal | None:
        """Execute clearing for a specific cycle and return the *actual* cleared amount.

        Returns:
        - Decimal amount on success (the occurrence's declared amount, or a verified replay's durable one)
        - None when the occurrence is skipped (its rows are gone, a stale plan, or withheld consent).

        Unexpected execution failures are rolled back and surfaced through the
        application's sanitized internal-error path.
        """
        occurrence = self._occurrence
        logger.info("event=clearing.execute cycle_len=%s", len(cycle))
        try:
            CLEARING_EVENTS_TOTAL.labels(event="execute", result="start").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.start",
                exc_info=True,
            )

        debt_ids = list(occurrence.debt_ids)
        execution_tx_id = occurrence.occurrence_id
        self._locked_lines = None
        try:
            replay_amount = await self._committed_execution_amount(
                execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )
        except Exception as exc:
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )
        if replay_amount is not None:
            await self._rollback_skipped_execution()
            return replay_amount

        # 027 stage 2 (`T2704`): the re-read of the cycle below holds against concurrent money writers only at
        # READ COMMITTED behind the line locks. Checked on THIS attempt's transaction, before its first lock and
        # its first write; the clearing ends the attempt, as it ends every attempt.
        try:
            await MoneyBoundary.require_read_committed(self.session, writer="clearing")
        except IsolationNotReadCommitted:
            await self._rollback_skipped_execution()
            raise
        except Exception as exc:
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )

        # THE LOCK ORDER (027 stage 2): every non-closed line of every pair of the cycle `FOR UPDATE` (one
        # statement, `trust_lines.id` order) BEFORE any amount of those pairs is read - the edges' identity
        # columns read first are immutable; then the stop/hold `FOR SHARE`; then the cycle's debt rows
        # `FOR UPDATE` (their `version` is checked by `Book`). The lines a requested close settles
        # (`book.py`, `_settle_requested_closes`) are among those held.
        try:
            edges = (
                await self.session.execute(
                    select(Debt.equivalent_id, Debt.debtor_id, Debt.creditor_id).where(Debt.id.in_(debt_ids))
                )
            ).all()
            # 028 `F-028-28` (owner В-1): the cycle's participants `FOR SHARE` before its lines (the one order), the
            # status read by that statement; an occurrence through a suspended participant is skipped, by reason.
            statuses = await MoneyBoundary(self.session).lock_participants(
                {p for row in edges for p in (row.debtor_id, row.creditor_id)}, timeout_ms=MoneyBoundary.lock_budget_ms())
            if suspended := sorted(pid for status, pid in statuses.values() if status != "active"):
                logger.info("event=clearing.skip_participant_suspended cycle_len=%s participants=%s",
                            len(cycle), suspended)
                try:
                    CLEARING_EVENTS_TOTAL.labels(event="execute", result="skip_participant_suspended").inc()
                except Exception:
                    pass
                await self._rollback_skipped_execution()
                return None
            self._locked_lines = await MoneyBoundary(self.session).lock_pair_lines(
                edges, timeout_ms=MoneyBoundary.lock_budget_ms())
        except Exception as exc:
            if "55P03" in self._postgres_error_codes(exc):  # the bounded wait (019's interlock budget, restored)
                await self._rollback_skipped_execution()
                raise TimeoutException("Clearing lock timed out") from exc
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )

        # T1544, the binding read, `FOR SHARE` through the commit (see the method). After the
        # committed-execution shortcut above (an already-durable clearing stays reported), before
        # the Debt rows are locked and before any new execution work.
        await self._refuse_if_equivalent_inactive({row.equivalent_id for row in edges} or {occurrence.equivalent_id})

        # 030 `F-030-1` (owner В-4 of 028, В2 of 030): `c` is a multiple of the equivalent's step, the step read
        # under the row lock just taken - the one the stop and the hold were read under, held to commit, so a PATCH
        # of the precision either waits for this clearing or committed before it. Refused, never rounded: debts
        # finer than the step mean a database to reseed (runbook of S1), and this refusal is what notices it.
        try:
            step_of = await MoneyBoundary(self.session).share_equivalent_step(occurrence.equivalent_id)
        except Exception as exc:
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )
        if step_of is None or occurrence.amount % money_step(step_of[1]) != 0:
            await self._raise_unexpected_execution(ClearingOccurrenceRefused(OCCURRENCE_AMOUNT_NOT_IN_STEP))

        try:
            debts = (
                (
                    await self.session.execute(
                        select(Debt).where(Debt.id.in_(debt_ids)).order_by(Debt.id).with_for_update()
                    )
                )
                .scalars()
                .all()
            )
        except Exception as exc:
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )

        if len(debts) != len(debt_ids):
            # A concurrent owner may have committed this exact occurrence while
            # we waited for its Debt rows. Resolve that durable result before skip.
            try:
                replay_amount = await self._committed_execution_amount(
                    execution_tx_id,
                    allowed_participant_pids=allowed_participant_pids,
                )
            except Exception as exc:
                return await self._end_attempt_on_error(
                    exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
                )
            if replay_amount is not None:
                await self._rollback_skipped_execution()
                return replay_amount
            await self._rollback_skipped_execution()
            return None

        # 2026-08-22 / p010 (`F-010-3`).  The authoritative perimeter check, and the only
        # one: it stands on the rows just re-read under FOR UPDATE, so it cannot be fooled
        # by a cycle that changed between detection and execution, and it runs before the
        # amount is computed and before any side effect.
        #
        # Not the edges' identity read before the locks.  Not the later
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

        # 023 slice (b): the DECLARED amount of a plan occurrence, checked against the rows just locked.
        # The descriptor must describe them - its equivalent, its cycle in its order - or it is refused
        # (a wrong descriptor is not a stale plan); an edge now below `c` is a stale plan, skipped whole.
        # `c` is positive by construction (`ClearingOccurrence.__post_init__`).
        debts_by_declared_id = {debt.id: debt for debt in debts}
        ordered = [debts_by_declared_id[debt_id] for debt_id in occurrence.debt_ids]
        if any(debt.equivalent_id != occurrence.equivalent_id for debt in ordered):
            await self._raise_unexpected_execution(
                ClearingOccurrenceRefused("occurrence_equivalent_differs_from_the_rows")
            )
        debtors = [debt.debtor_id for debt in ordered]
        if len(set(debtors)) != len(debtors) or any(
            ordered[k].creditor_id != ordered[(k + 1) % len(ordered)].debtor_id for k in range(len(ordered))
        ):
            await self._raise_unexpected_execution(
                ClearingOccurrenceRefused("occurrence_is_not_one_simple_directed_cycle")
            )
        debts = ordered
        clear_amount = occurrence.amount
        if any(debt.amount < clear_amount for debt in debts):
            logger.info("event=clearing.skip_stale_plan cycle_len=%s", len(debts))
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="skip_stale").inc()
            except Exception:
                pass
            await self._rollback_skipped_execution()
            return None

        logger.info(
            "event=clearing.execute_ready cycle_len=%s amount=%s",
            len(cycle),
            clear_amount,
        )

        # FIX-017: enforce auto_clearing policy on every edge in the cycle.
        try:
            respects_auto_clearing = await self._cycle_respects_auto_clearing(debts)
        except Exception as exc:
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )
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
        # 027 stage 2: neutrality over the cycle's own pairs - its locked rows - never every debt of a
        # participant, which a neighbour's commit on another pair changes under READ COMMITTED.
        cycle_pairs = {p for d in debts for p in ((d.debtor_id, d.creditor_id), (d.creditor_id, d.debtor_id))}

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
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )

        # 012, second round.  THE PAYLOAD AMOUNT IS A REPRESENTATION, NOT A STORAGE FORMAT, and
        # that was checked before it was changed rather than assumed.  `transactions.payload` is
        # re-parsed on replay by `_read_committed_execution_amount` (`:203`) as
        # `Decimal(str(payload["amount"]))`, and `Decimal("1E-8") == Decimal("0.00000001")` is
        # the same value of the same type - the round trip is exact either way.  Nothing else in
        # the repository reads this field: it is in no hash (`compute_integrity_checkpoint_for_
        # equivalent` digests `debts` and `trust_lines` rows, never a payload), in no signature
        # (CLEARING rows are not signed), and in no key (`idempotency_key` is
        # `clearing:{tx_id}`, and `tx_id` is the occurrence id - a uuid5 of plan, equivalent and ordinal), and the
        # only other CLEARING-payload readers at the time - the admin participant activity (removed by 032 S5) and
        # the admin graph's transactions collection - read `equivalent` and `edges[].debtor/creditor`.
        # `to_money_str` never drops a digit the value carries, so the amount a replay hands
        # back still equals the amount that was applied to the debts.  Rows written before this
        # change keep their old string and parse identically, so no backfill and no migration.
        #
        # The reason to change it at all is the T1201 rollout condition: the audit that has to
        # run over `transactions.payload->>'amount'` looking for `scale >= 9`.  Measured here:
        # a `cast(... as numeric)` audit reads `'1E-8'` correctly (scale 8), but any audit that
        # counts digits in the TEXT - the obvious way to write it, and the only way that also
        # catches a value `numeric` cannot hold - sees no fraction digits at all in `1E-8` and
        # silently passes the row. An exponential literal in the audited column is a trap laid
        # for the audit, whichever way it is eventually written.
        payload_precision = 2
        if equivalent is not None:
            try:
                payload_precision = int(equivalent.precision)
            except (TypeError, ValueError):
                payload_precision = 2
        clear_amount_str = to_money_str(clear_amount, payload_precision)

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
                    "amount": clear_amount_str,
                }
            )

        try:
            positions_before: Dict[uuid.UUID, Decimal] = {}
            for pid in participant_ids:
                positions_before[pid] = await checker._calculate_net_position(
                    pid, debts[0].equivalent_id, pairs=cycle_pairs
                )
        except Exception as exc:
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )

        # 2. Create Transaction (CLEARING). No initiator (028 F-028-45, owner В-8): the hub closes the cycle, nobody
        # asks for it; who took part is `edges`.
        tx_uuid = uuid.UUID(execution_tx_id)
        tx_id_str = execution_tx_id

        tx_payload = {
            # Backward-compatible fields.
            "cycle": [str(e["debt_id"]) for e in cycle],
            "amount": clear_amount_str,
            # Enriched fields for audit/debugging.
            "equivalent": str(
                equivalent.code if equivalent else debts[0].equivalent_id
            ),
            "edges": edges_payload,
        }
        intent_cycle = [
            {
                "debt_id": str(debt.id),
                "amount": f"{debt.amount.quantize(Decimal('1E-8')):f}",
                "debtor_id": str(debt.debtor_id),
                "creditor_id": str(debt.creditor_id),
            }
            for debt in debts
        ]
        intent = {
            "tx_id": tx_id_str,
            "clear_amount": f"{clear_amount.quantize(Decimal('1E-8')):f}",
            "equivalent_id": str(debts[0].equivalent_id),
            "cycle": intent_cycle,
        }
        # 023 slice (b), decision 5 (4): ONE descriptor in the intent and the transaction row; the
        # verifier (`reconciliation._clearing_v2`) and the replay check read it from both.
        tx_payload["occurrence"] = occurrence.descriptor()
        intent["occurrence"] = occurrence.descriptor()

        new_tx = Transaction(
            id=tx_uuid,
            tx_id=tx_id_str,
            idempotency_key=f"clearing:{tx_id_str}",
            type="CLEARING",
            initiator_id=None,
            payload=tx_payload,
            state="NEW",
        )
        self.session.add(new_tx)

        try:
            # 3. Apply changes (Decrease debts). The rows are already held, in the one order (027 stage 2, 028
            # `F-028-28`): the cycle's participants `FOR SHARE`, every non-closed line of its pairs `FOR UPDATE`, the
            # cycle's debt rows `FOR UPDATE` (their `version` is checked by `Book`). READ COMMITTED reads the
            # committed state behind those locks, so nothing here is a plain update of rows another writer may change.

            # THE OPERATION ENVELOPE (programme 015, phase B step 4). The clearing's declared
            # intent, opened after the cycle has been read FOR UPDATE and before a single edge is
            # reduced, and closed before `_commit_to_terminal` makes any of it durable.
            #
            # THE INTENT IS THE PRE-AMOUNTS, and it has to be: after the clearing runs they are
            # gone - every edge is reduced by `clear_amount` and the minimum edge is deleted
            # outright - so an intent built from anything later could only ever be compared against
            # the answer. They come from the locked read at `:1754`, not from candidate detection:
            # the cycle is unchanged whenever nothing else is writing, so an intent built from the
            # detection amounts would look right in every single-threaded test and describe a state
            # this clearing did not act on the first time it raced.
            async with Book.operation(
                self.session,
                operation_for(
                    "CLEARING",
                    tx_id_str,
                    tx_id=tx_id_str,
                    intent=intent,
                    scope_equivalent_ids={debts[0].equivalent_id},
                    intent_equivalent_ids={debts[0].equivalent_id},
                    intent_encoding_version=CLEARING_INTENT_ENCODING_VERSION,
                ),
            ) as posting:
                for debt in debts:
                    if debt.amount < clear_amount:
                        raise GeoException(f"Debt {debt.id} amount changed during clearing")

                    # Decrease, and delete at zero: the book's CLEARING semantics (018 stage A).
                    await posting.apply(ClearingReduction(debt=debt, amount=clear_amount))

                await self.session.flush()

                # The operation's audit record (024 `T2413.2`): no full-equivalent check runs in this
                # transaction, and the row says so - `verification_passed = null`, empty checksums,
                # `invariants_checked = {}`. What guards the clearing is the neutrality check below and
                # the `amount < clear_amount` refusal above, which raise.
                try:
                    self.session.add(
                        IntegrityAuditLog(
                            operation_type="CLEARING",
                            tx_id=tx_id_str,
                            equivalent_code=str(
                                equivalent.code if equivalent else debts[0].equivalent_id
                            ),
                            state_checksum_before="",
                            state_checksum_after="",
                            affected_participants={
                                "participants": [
                                    str(pid_by_id.get(p, p)) for p in participant_ids
                                ],
                                "edges": edges_payload,
                            },
                            invariants_checked={},
                            verification_passed=None,
                            error_details=None,
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
                    pairs=cycle_pairs,
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
                # Both resolutions are drained with `surface_result=False`: a pulse the caller
                # sent while one ran is kept even if that resolution then fails, and is raised
                # below (or carried by `ClearingCommittedAfterCancellation`) - never retried.
                reconciliation_cancellation = await self._drain_task(
                    reconciliation_task, surface_result=False
                )
                try:
                    reconciled_amount = reconciliation_task.result()
                except Exception:
                    # 020 stage 1: a resolver error is never classified as the attempt's
                    # conflict - its 40001/40P01 is not the COMMIT's. One more complete
                    # resolution, so a durable occurrence is still reported as the success it
                    # is; if that one finds nothing or fails too, the COMMIT's own error keeps
                    # precedence below: an unknown commit ends unretried (`E010`), and only a
                    # rollback PostgreSQL reported on COMMIT reaches the retry owner.
                    logger.warning(
                        "event=clearing.commit_resolution_failed tx_id=%s resolution=1",
                        tx_id_str,
                        exc_info=True,
                    )
                    second_resolution = asyncio.create_task(
                        self._reconcile_committed_execution(
                            tx_id_str,
                            allowed_participant_pids=allowed_participant_pids,
                        )
                    )
                    second_cancellation = await self._drain_task(
                        second_resolution, surface_result=False
                    )
                    if reconciliation_cancellation is None:
                        reconciliation_cancellation = second_cancellation
                    try:
                        reconciled_amount = second_resolution.result()
                    except Exception:
                        logger.warning(
                            "event=clearing.commit_resolution_failed tx_id=%s resolution=2",
                            tx_id_str,
                            exc_info=True,
                        )
                        reconciled_amount = None
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

            # 027 stage 1: a money commit leaves the route cache in place (it ages out by its TTL; a payment's final
            # check re-reads every pair) - only topology edits drop it.

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
            return await self._end_attempt_on_error(
                exc, execution_tx_id, allowed_participant_pids=allowed_participant_pids
            )
