import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import AbstractSet, Dict, List, Set

from sqlalchemy import bindparam, select, and_, text
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

# The longest cycle the SQL fast path can express, in edges.  `find_triangles_sql` joins three
# `debts` rows and `find_quadrangles_sql` four; there is no five-table variant, so five is
# beyond their reach by construction rather than by configuration.  `find_cycles` uses this to
# decide whether its early return can answer the question it was asked - see the comment there.
_SQL_DETECTOR_MAX_CYCLE_LENGTH = 4

# Trust-line statuses whose consent clearing reads. `frozen` was admitted by T1551 (2026-09-13) and no longer
# exists (028 `F-028-29`, owner В-2: a line is `active` or `closed`; the freeze lives on the participant, and an
# occurrence through a suspended one is skipped at execution, `F-028-28`). `closed` stays excluded. ONE tuple for
# the SQL detectors and for `_cycle_respects_auto_clearing`, so discovery and execution cannot disagree.
_CLEARABLE_TRUSTLINE_STATUSES = ("active",)
_SQL_CLEARABLE_TRUSTLINE_STATUSES = (
    "(" + ", ".join(f"'{status}'" for status in _CLEARABLE_TRUSTLINE_STATUSES) + ")"
)


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
        # PostgreSQL SQLSTATEs only, and that is a decision rather than an omission (T1525,
        # 2026-09-12). Since SQLite transactions take a real read snapshot, a clearing execution
        # that reads and then writes can be refused with SQLITE_BUSY_SNAPSHOT, which this predicate
        # does NOT call retryable: that execution ends there.
        #
        # ACCEPTED, BUT ONLY THE SIMULATOR GETS THE "LATER TICK" - corrected 2026-09-12, this
        # comment used to justify the decision with "the simulator attempts it again on a later
        # tick" full stop, which is true of one caller and false of the other:
        #   * simulator: a postponed clearing rather than lost money, because there IS a next tick.
        #     The cost is that the failure feeds `run.errors_total`, which can stop a run once
        #     `SIMULATOR_REAL_MAX_ERRORS_TOTAL` is reached.
        #   * HTTP `POST /api/v1/clearing/auto`: there is NO next tick. The busy becomes E010 and
        #     the caller gets HTTP 500. Cycles already cleared in that call stay committed, so the
        #     remainder is refused rather than lost, but the caller is told "internal error" for
        #     what is a transient lock conflict.
        # Neither loses money - unlike the inject, whose owner would mark the
        # event fired and drop it (`real_runner_impl._is_transient_inject_db_error`). It appeared in
        # no measurement of T1525: two 180 s multi-session simulator runs, four full default tiers
        # and five multi-session modules ten times each, all with zero busy errors from clearing.
        # HISTORY: the SQLite half of this note no longer applies - SQLite, its busy predicate and
        # `app/db/sqlite_transaction_control.py` left the application in programme 017 stage 3.
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

    def _bind_uuid(self, uid: uuid.UUID) -> object:
        """Return UUID in a format supported by the current DBAPI for raw SQL binds."""
        return uid

    # `_bind_decimal` USED TO LIVE HERE AND IS GONE (2026-08-24 / p012 `F-012-3`, `T1202`).
    # Its only job was to hand the removed `Decimal("0.01")` clearing threshold to the SQLite
    # DBAPI as a `float`, and it was the only place any of the four money modules converted a
    # money value to binary floating point.  With the threshold dropped it had no callers.

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
        # Raw text() needs an expanding bind for IN.
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
        # Postgres (json/jsonb): policy->>'auto_clearing' yields text.
        return (
            "("
            f"{alias}.policy IS NULL OR "
            f"({alias}.policy->>'auto_clearing') IS NULL OR "
            f"lower({alias}.policy->>'auto_clearing') NOT IN ('false', '0', 'no', 'off')"
            ")"
        )

    @staticmethod
    def _debt_id_key(raw: object) -> str:
        """One spelling for one debt id, whatever produced the string.

        012, second round.  The raw-SQL detectors and the ORM DFS do not agree on how a debt id
        LOOKS, and until `find_cycles` merged their answers nothing had to notice.  Measured on
        SQLite, one debt, one graph: the DFS says `'333e9737-a7cc-4017-812d-fa3719bef0c9'` and
        `find_triangles_sql` says `'333e9737a7cc4017812dfa3719bef0c9'` - the driver hands raw
        SQL the stored 32-hex form while the ORM's `Uuid` type reconstructs a `uuid.UUID`.  On
        PostgreSQL asyncpg returns `uuid.UUID` on both paths and the two agree, which is exactly
        how a de-duplication keyed on the spelling passes the Postgres tier and reports every
        cycle twice on the default one.  So the key is the VALUE, not the text.
        """

        text = str(raw or "")
        try:
            return str(uuid.UUID(text))
        except (ValueError, AttributeError, TypeError):
            return text

    @classmethod
    def _cycle_order_key(cls, cycle: List[Dict]) -> tuple:
        """Shorter first; within a length, largest executable amount first (T1211).

        The executable amount of a cycle is its smallest edge - the `LEAST(...)` the SQL
        detectors deliberately ORDER BY DESC, because `auto_clear` executes the first cycle
        that succeeds and ordering therefore IS behavior (for two cycles sharing an edge it
        decides which debts remain).  The debt-id set as the last component makes
        equal-amount ties deterministic across tiers and detectors, instead of leaving them
        to discovery or `ORDER BY` residue.  Used on the merged answer AND on the fast-path
        early return, so a caller sees one ordering rule regardless of which path answered.

        HISTORICAL since programme 023 slice (d), 2026-09-28: `auto_clear` is gone and no executor consumes this
        order - execution goes through the flow plan (`app/core/clearing/runner.py`). `find_cycles` is the
        diagnostic of `GET /clearing/cycles` and `/admin/clearing/cycles` only; the order is its answer's.
        """

        def _executable_amount() -> Decimal:
            try:
                return min(Decimal(str(edge.get("amount", "0"))) for edge in cycle)
            except (InvalidOperation, ValueError, TypeError):
                return Decimal(0)

        return (
            len(cycle),
            -_executable_amount(),
            tuple(sorted(cls._debt_id_key(e.get("debt_id", "")) for e in cycle)),
        )

    @classmethod
    def _deduplicate_cycles(cls, cycles: List[List[Dict]]) -> List[List[Dict]]:
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
                key = tuple(sorted(cls._debt_id_key(e.get("debt_id", "")) for e in cycle))
            except Exception:
                key = tuple()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(cycle)
        return out

    async def _equivalent_precision(self, equivalent_id: uuid.UUID) -> int:
        """The digits this equivalent declares, for rendering only.

        012, second round.  The three detectors and the CLEARING payload all printed amounts
        with bare `str(Decimal)`, which puts `1E-8` on the wire for a value `Numeric(20, 8)`
        holds exactly.  `to_money_str` needs the equivalent's `precision`, and the SQL
        detectors are addressed by `equivalent_id` alone, so this reads it when the caller has
        not already got the row.  `find_cycles` and `_execute_clearing_with_amount` both do,
        and pass it, so the extra query is only for a direct caller (tests, and any future
        one).  Rendering is the ONLY use: no comparison, no admission rule and no stored value
        depends on it, so a wrong or missing `precision` can widen or narrow the digits shown
        and can never hide a cycle -- which is the distinction `VERDICT-DOOR: C` deferred.
        """

        try:
            precision = (
                await self.session.execute(
                    select(Equivalent.precision).where(Equivalent.id == equivalent_id)
                )
            ).scalar_one_or_none()
        except Exception:
            return 2
        try:
            return int(precision)
        except (TypeError, ValueError):
            return 2

    async def find_triangles_sql(
        self,
        equivalent_id: uuid.UUID,
        *,
        allowed_participant_ids: "set[uuid.UUID] | None" = None,
        precision: int | None = None,
    ) -> List[List[Dict]]:
        """Find 3-node debt cycles using a SQL JOIN.

        `allowed_participant_ids` confines the cycle to one run's participants; None keeps
        the historic global behaviour, which the hub and the simulator tick still rely on.

        `precision` is a RENDERING parameter and nothing else - it decides how many digits the
        returned `amount` strings carry, never which rows come back.  Omitted, it is read from
        the equivalent.

        THE `min_amount` THRESHOLD IS GONE, AND THE REASON IS MEASURED (2026-08-24 / p012,
        `F-012-3`, `T1202`).  This query used to carry `AND LEAST(...) > :min_amount` with
        `min_amount` bound to a hardcoded `Decimal("0.01")`.  The comparison is strict and the
        shipped default `precision` is 2, so a triangle whose every leg is exactly `0.01` - the
        smallest amount that equivalent can express - was invisible here.  Measured on
        PostgreSQL 16.9: `UAH` at precision 2 with every leg `0.01` found 0 triangles, `0.02`
        found 3.  `find_cycles` returns early when this query is non-empty, so a graph holding
        one ordinary cycle and one at the boundary reported one of two, verbatim through
        `GET /api/v1/clearing/cycles`.

        THREE ALTERNATIVES WERE WEIGHED AND THE THRESHOLD WAS DROPPED RATHER THAN TAUGHT TO
        READ `precision`:

        * IT WAS NOT PROTECTING ANYTHING FROM DUST.  The Python DFS underneath filters on
          `Debt.amount > 0` alone, and `find_cycles` falls through to it whenever this query
          comes back empty.  Measured: a triangle of three `0.005` debts - below the precision-2
          quantum, and storable today - is found and cleared by that fallback right now.  So the
          threshold never suppressed a single sub-quantum cycle system-wide.  All it did was
          make the fast path and the fallback disagree about what a real debt is.

        * IT WAS NOT PAYING FOR ITSELF IN THE PLAN, and this was the open question the survey
          admitted it had not measured.  `LEAST(d1.amount, d2.amount, d3.amount)` spans three
          joined tables, so no index can serve it and PostgreSQL can only apply it as a post-join
          `Join Filter`, after the expensive expansion that dominates the query.  It is not a
          performance predicate, and keeping it "for the plan" would have been a claim the numbers
          do not support.

          THE CONCLUSION HELD ACROSS TWO REVIEW ROUNDS; THE NUMBERS UNDER IT DID NOT, and are
          corrected here rather than left to rot.  What stood here was "61898 buffers against
          62204 - 0.5% - and 77.8 ms against 81.0 ms median".  The first round found the figure was
          a RETELLING: no artefact, no generator, nothing in the tree to re-run.  The second found
          the anti-vacuum was luck - the population did not guarantee the predicate anything to
          discard, so the comparison could have been printed over a predicate that filtered
          nothing.  The timing half was worse than stale: "inside the run-to-run spread of both"
          was two runs described as a property, and over five runs the medians of ONE variant span
          87.6 to 238.6 ms while the buffer counts repeat to the digit.

          Live numbers, on a population with planted boundary triangles and a script that exits
          non-zero if the predicate discarded nothing: 62116 shared buffers with the predicate
          against 62638 without, a delta of 522 - 0.833% of the statement.  Regenerate with
          `scripts/measure_clearing_min_amount_plan.py`; the run is in
          `specs/012-money-precision-and-representation/evidence/`.

        * TEACHING IT `precision` WOULD HAVE DECIDED SOMETHING THIS PROGRAMME DEFERRED.  A
          threshold of `>= 10**-precision` reads "an amount below one quantum is not money" -
          which is exactly the semantics `VERDICT-DOOR: C` deferred to a separate versioned
          decision with a data audit, because `precision` is admin-editable and the door
          deliberately still accepts `0.05` for a precision-1 `HOUR`.  Enacting it here, in the
          detector only, would have created debts that are storable, payable and permanently
          unclearable - and it would still have left the fast path and the fallback disagreeing.
          CORRECTION 2026-10-04 (028 `F-028-23`, `F-028-26`, owner В-4): HOUR has precision 2, and the
          doors now refuse amounts finer than the step; this detector stays step-blind on purpose.

        `d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0` already says "a real debt", it is
        per-table so the planner can push it down, and it is the same rule the DFS applies.
        """

        if precision is None:
            precision = await self._equivalent_precision(equivalent_id)

        least_expr = "LEAST(d1.amount, d2.amount, d3.amount)"

        equivalent_id_param = self._bind_uuid(equivalent_id)

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
                                                            AND t1.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t1')}
                        JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id
                                                            AND t2.to_participant_id = d2.debtor_id
                                                            AND t2.equivalent_id = d2.equivalent_id
                                                            AND t2.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t2')}
                        JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id
                                                            AND t3.to_participant_id = d3.debtor_id
                                                            AND t3.equivalent_id = d3.equivalent_id
                                                            AND t3.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t3')}
            WHERE d1.equivalent_id = :equivalent_id
              AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0
              {scope_sql}
            ORDER BY clear_amount DESC
            LIMIT 100
            """
        )

        params = {
            "equivalent_id": equivalent_id_param,
        }
        if scope_binds is not None:
            query = query.bindparams(bindparam("allowed_participant_ids", expanding=True))
            params["allowed_participant_ids"] = scope_binds

        result = await self.session.execute(query, params)

        cycles: List[List[Dict]] = []
        for row in result:
            # `to_money_str`, not `str(Decimal)`: asyncpg hands back `Numeric(20, 8)` at the
            # column's scale, so a stored `0.00000001` is `Decimal('1E-8')` and `str()` puts
            # that exponent literal straight into `GET /api/v1/clearing/cycles`.  It also ends
            # the two-scales-for-one-debt effect this raw-SQL path had against the ORM DFS
            # below: the driver decides the scale of the value it returns, and the renderer
            # takes that decision back.
            #
            # `_debt_id_key` for `debt_id`, not bare `str()`, for the same one-form reason:
            # on SQLite this raw-SQL path sees the stored 32-hex spelling while the DFS emits
            # the hyphenated one, so a merged answer could mix two spellings of the same kind
            # of id in one payload (T1210-bis).  The dedup key already normalized this way;
            # the RENDITION now matches the key.  (Both detectors, same rule - quadrangles
            # below inherit this comment.)
            cycles.append(
                [
                    {
                        "debt_id": self._debt_id_key(row.debt1_id),
                        "debtor": str(row.a),
                        "creditor": str(row.b),
                        "amount": to_money_str(row.amount1, precision),
                    },
                    {
                        "debt_id": self._debt_id_key(row.debt2_id),
                        "debtor": str(row.b),
                        "creditor": str(row.c),
                        "amount": to_money_str(row.amount2, precision),
                    },
                    {
                        "debt_id": self._debt_id_key(row.debt3_id),
                        "debtor": str(row.c),
                        "creditor": str(row.a),
                        "amount": to_money_str(row.amount3, precision),
                    },
                ]
            )

        return cycles

    async def find_quadrangles_sql(
        self,
        equivalent_id: uuid.UUID,
        *,
        allowed_participant_ids: "set[uuid.UUID] | None" = None,
        precision: int | None = None,
    ) -> List[List[Dict]]:
        """Find 4-node debt cycles using a SQL JOIN.

        The `min_amount` threshold is gone here for the same measured reasons as in
        `find_triangles_sql`, which carries the full argument; this query held the identical
        `AND LEAST(...) > :min_amount` and hid four-node cycles at the boundary the same way.

        `precision`, likewise, only decides how many digits the returned `amount` strings
        carry; it selects no rows.
        """

        if precision is None:
            precision = await self._equivalent_precision(equivalent_id)

        least_expr = "LEAST(d1.amount, d2.amount, d3.amount, d4.amount)"

        equivalent_id_param = self._bind_uuid(equivalent_id)

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
                                                            AND t1.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t1')}
                        JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id
                                                            AND t2.to_participant_id = d2.debtor_id
                                                            AND t2.equivalent_id = d2.equivalent_id
                                                            AND t2.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t2')}
                        JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id
                                                            AND t3.to_participant_id = d3.debtor_id
                                                            AND t3.equivalent_id = d3.equivalent_id
                                                            AND t3.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t3')}
                        JOIN trust_lines t4 ON t4.from_participant_id = d4.creditor_id
                                                            AND t4.to_participant_id = d4.debtor_id
                                                            AND t4.equivalent_id = d4.equivalent_id
                                                            AND t4.status IN {_SQL_CLEARABLE_TRUSTLINE_STATUSES}
                                                            AND {self._sql_auto_clearing_ok('t4')}
            WHERE d1.equivalent_id = :equivalent_id
              AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0 AND d4.amount > 0
              AND d1.debtor_id != d2.creditor_id
              AND d1.debtor_id != d3.creditor_id
              AND d1.creditor_id != d3.creditor_id
              {scope_sql}
            ORDER BY clear_amount DESC
            LIMIT 50
            """
        )

        params = {
            "equivalent_id": equivalent_id_param,
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
                        "debt_id": self._debt_id_key(row.debt1_id),
                        "debtor": str(row.a),
                        "creditor": str(row.b),
                        "amount": to_money_str(row.amt1, precision),
                    },
                    {
                        "debt_id": self._debt_id_key(row.debt2_id),
                        "debtor": str(row.b),
                        "creditor": str(row.c),
                        "amount": to_money_str(row.amt2, precision),
                    },
                    {
                        "debt_id": self._debt_id_key(row.debt3_id),
                        "debtor": str(row.c),
                        "creditor": str(row.d),
                        "amount": to_money_str(row.amt3, precision),
                    },
                    {
                        "debt_id": self._debt_id_key(row.debt4_id),
                        "debtor": str(row.d),
                        "creditor": str(row.a),
                        "amount": to_money_str(row.amt4, precision),
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
        # keeps behavior consistent with execution.
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
        #
        # THE EARLY RETURN BELOW IS CONDITIONAL, AND THE CONDITION IS THE SQL DETECTORS' REACH
        # (012, second round).  These two queries find cycles of exactly 3 and exactly 4 edges.
        # The DFS underneath finds 3..`max_depth`, and `max_depth` defaults to SIX on the API
        # (`app/api/v1/clearing.py:20,34`).  So "return the SQL answer whenever it is non-empty"
        # is not a shortcut, it is a different question answered: with a triangle anywhere in the
        # graph, every 5- and 6-edge cycle disappears from `GET /api/v1/clearing/cycles`.
        # Measured on a `UAH` graph holding one `0.01` triangle and one disjoint 5-node cycle of
        # `50`, at every depth 3..6: 1 cycle, lengths [3].  Removing the `min_amount` threshold
        # made this STRICTLY WORSE rather than closing it - the fast path is now non-empty more
        # often, so it suppresses the fallback more often.
        #
        # `auto_clear` was never the victim: it loops until `find_cycles` comes back empty, so
        # it reaches the long cycle on a later pass.  The READ endpoint answers once.
        #
        # So the SQL result is returned early only when nothing longer was asked for.  Past that
        # depth both detectors run and their answers are merged (see the end of this method):
        # neither is a superset of the other - the SQL side is capped at `LIMIT 100` and ordered
        # by amount, the DFS stops descending a branch at its first cycle and at 50 overall - so
        # a union is the only combination whose answer does not depend on which one happened to
        # be non-empty.
        sql_cycles: List[List[Dict]] = []
        use_sql = isinstance(self.session, AsyncSession)
        if use_sql and max_depth >= 3:
            cycles: List[List[Dict]] = []
            try:
                cycles = await self.find_triangles_sql(
                    equivalent.id,
                    allowed_participant_ids=allowed_ids,
                    precision=equivalent.precision,
                )

                # BOTH lengths, unconditionally, whenever the depth asks for both (T1210-bis
                # finding A).  The previous gate ran quadrangles only when the filtered
                # triangles came back EMPTY - "if triangles exist but are all filtered out,
                # try quadrangles" - which re-created, one step down, exactly the shape the
                # merge below exists to kill: at max_depth=4 (a legal API input, ge=3) a
                # single triangle hid every quadrangle from a "complete" early return, and at
                # 5-6 it starved the SQL side down to triangles, leaving quadrangles to the
                # DFS's 50-raw-cycle cap alone.  A union's answer must not depend on which
                # detector happened to be non-empty - including the union of these two.
                if max_depth >= 4:
                    cycles = cycles + await self.find_quadrangles_sql(
                        equivalent.id,
                        allowed_participant_ids=allowed_ids,
                        precision=equivalent.precision,
                    )

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

                sql_cycles = cycles

                # `_SQL_DETECTOR_MAX_CYCLE_LENGTH` edges is everything the two queries above can
                # express.  Ask for no more than that and the SQL answer is complete for the
                # question, so returning it here costs nothing and skips loading the graph.  Ask
                # for more and it is not, so fall through: the DFS runs and the two answers are
                # merged below.
                if max_depth <= _SQL_DETECTOR_MAX_CYCLE_LENGTH:
                    # Same ordering rule as the merged answer below (1b's review of
                    # a9d742e): the raw `ORDER BY ... DESC` has no secondary key, so
                    # equal-amount ties were tier-dependent residue on this path.
                    sql_cycles.sort(key=self._cycle_order_key)
                    return sql_cycles

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
                            # Same renderer as the two SQL detectors above.  `Debt.amount` is a
                            # `Numeric(20, 8)`, so `str()` here printed `1E-8` for a value the
                            # ledger holds exactly - and printed the SAME debt at a different
                            # scale than the raw-SQL path did, which made one payload's digits
                            # depend on which detector answered.
                            "amount": to_money_str(edge.amount, equivalent.precision),
                        }
                    )
                final_cycles.append(cycle_data)

        if final_cycles:
            final_cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                final_cycles, equivalent_id=equivalent.id
            )

        # MERGE, not pick.  Reached only when `max_depth` exceeds the SQL detectors' reach, so
        # `sql_cycles` is a partial answer by construction and the DFS one is partial too (it
        # abandons a branch at its first cycle and stops at fifty).  Both sides have already
        # been through `_filter_cycles_by_auto_clearing_policy_sql` (the reservation filter left with
        # `prepare_locks`, 019 stage 5), so the
        # union needs no further admission check - only de-duplication, which is by debt-id set
        # and therefore blind to which detector produced the edge, and to the order the edges
        # come in.  `sql_cycles` is empty whenever the SQL path found nothing or raised -
        # though the raised case is a graceful fallback only on SQLite: on PostgreSQL a
        # failed raw query aborts the transaction, so the DFS's own queries then fail too
        # and `find_cycles` errors out anyway (T1210-bis; pre-existing, recorded not fixed).
        #
        # `_deduplicate_cycles` keeps the FIRST occurrence, so for a cycle both detectors found
        # the DFS rendition is the one that survives.  That is immaterial only because the two
        # now render money identically - which is the other half of this change, and the reason
        # the SQL renderers have reproducers that address them directly rather than through
        # here (`test_p012_money_form_and_detector_reach_postgres.py`).
        if sql_cycles:
            final_cycles = self._deduplicate_cycles(final_cycles + sql_cycles)

        # (The executor named below is gone since programme 023 slice (d); the order now shapes the diagnostic answer
        # only - see `_cycle_order_key`.)
        # Shorter cycles first for auto_clear(); WITHIN a length, largest clearable amount
        # first (T1211, external review).  The SQL detectors deliberately ORDER BY
        # `LEAST(...) DESC` - the executable amount of a cycle is its smallest edge - and
        # `auto_clear` executes the first cycle that succeeds, so ordering IS behavior: the
        # first edition of this merge sorted by length alone, which let DFS discovery order
        # replace that heuristic among same-length cycles, and for two cycles SHARING an edge
        # the executed-first cycle decides which debts remain.  The reviewer reproduced a
        # different final ledger from the order alone.  Sorting the union restores the
        # recorded heuristic for every cycle regardless of which detector found it (the DFS
        # side never had it - it was simply never merged in front of SQL results before).
        final_cycles.sort(key=self._cycle_order_key)

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
        return final_cycles

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
        """Execute one clearing with its retry owner, on the caller's session (027 stage 2, `T2704`).

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
        # only other CLEARING-payload readers - `app/core/admin/metrics.py:723` and
        # `app/api/v1/admin.py:203,1035` - read `equivalent` and `edges[].debtor/creditor`.
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
            # 3. Apply changes (Decrease debts)
            # We must lock rows? Or just update.
            # Since we are in a transaction, we should select for update ideally.
            # For MVP, we just update.

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
