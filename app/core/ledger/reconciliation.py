"""Debt reconciliation, criterion (a): DETECTION OF CHANGE MADE AROUND THE APPLICATION.

Programme 015, step 5a (`T1501`, `T1505` criterion (a), `T1508` corruption shapes for (a)). Decisions:
spec.md, "Ключевое ревью шага 5: `ON-TRACK`, критерий (а) строится как обнаружение", 2026-09-14.

WHAT IT CHECKS. For one equivalent, over the UNION of edges that have a current debt, journal entries,
or a baseline offset:

    current debt(edge) - sum of every recorded journal delta(edge) == baseline offset(edge)

plus, per journal row, `amount_after - amount_before == delta`. On PostgreSQL the row arithmetic is
already a CHECK constraint (`chk_debt_journal_entries_delta_arithmetic`, migration 024); on SQLite it is
not, because SQLite binds `Numeric` through float. The row check is done here on both dialects because
one code path is cheaper to keep right than two; on PostgreSQL it can only fire if the constraint is gone.

ALL ARITHMETIC IS IN INTEGER ATOMS (1e-8) IN PYTHON, never an SQL `SUM`: on SQLite an aggregate over a
`NUMERIC` column is a float sum, and a float sum is not money evidence.

ITS THREAT MODEL IS NARROW, AND THE WHOLE OF IT IS STATED. `docs/ru/02-protocol-spec.md` §11.2.1 names
this comparison as the real detector the withdrawn zero-sum check never was, and §11.6 asks for periodic
checks; this runs after commit, from the scheduled loop only, against independently stored tables.

    Detects: an operator's SQL to one side (`debts` or the journal), a partial restore, a future writer
    that is not instrumented, a journal entry that disappeared or was duplicated.
    Does NOT detect: a coordinated rewrite of debts, journal and baseline; a consistent full restore;
    code that disables this verifier; incorrect opening balances (the baseline adopts, it does not
    certify); and a writer that journals a WRONG change faithfully - that is criterion (b), step 5b,
    and the `C6` counterexamples are exactly that case.

It is DETECTION, not tamper protection, and nothing downstream may call it more. It reacts to nothing:
no hold, no notification, no repair (step 5c decides the reaction).

THE RESULT IS THREE-VALUED. `FAILED` - the evidence exists and an exact predicate is false.
`UNVERIFIABLE` - required evidence is absent (today: no baseline). `FAILED` dominates when at least one
conclusive contradiction exists. A database or query failure is an ERROR and propagates; it is never
turned into `UNVERIFIABLE`. The result is persisted as its own row (`debt_reconciliation_results`) and
never enters an integrity checkpoint or an audit row.

POST-BASELINE SEED AND TEST_FIXTURE WRITES ARE REFUSED by the journal at operation completion
(`app/core/ledger/journal.py`, `Reason.UNVERIFIABLE_WRITER_AFTER_BASELINE`), so they cannot produce a
`PASSED` over a change nothing can recompute.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

from sqlalchemy import insert, select, update

from app.db.journal_tables import debt_journal_entries
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import (
    debt_reconciliation_baseline_offsets,
    debt_reconciliation_baselines,
    debt_reconciliation_results,
)
from app.db.sqlite_transaction_control import sqlite_transaction_control_is_installed

__all__ = [
    "BaselineAlreadyTaken",
    "BaselineTaken",
    "CRITERION_A",
    "FAILED",
    "PASSED",
    "ReconciliationOutcome",
    "ReconciliationReadError",
    "ReconciliationSnapshotError",
    "UNVERIFIABLE",
    "record_outcome",
    "run_scheduled_reconciliation",
    "take_baseline",
    "verify_journal_equals_change",
]

logger = logging.getLogger(__name__)

PASSED = "PASSED"
FAILED = "FAILED"
UNVERIFIABLE = "UNVERIFIABLE"

#: Named for what it is, in the stored record as well as here.
CRITERION_A = "a:journal_equals_change:detection_around_the_application"

#: The stored record keeps the first findings and the total; a catastrophic mismatch must not turn
#: every scheduled run into a megabyte row.
MAX_STORED_FINDINGS = 50

_ATOM_EXPONENT = 8

Edge = tuple[uuid.UUID, uuid.UUID]


class ReconciliationReadError(RuntimeError):
    """A value read back could not be interpreted as money. An error, not `UNVERIFIABLE`."""


class BaselineAlreadyTaken(RuntimeError):
    """The equivalent already has its one baseline. Nothing here re-baselines."""


class ReconciliationSnapshotError(RuntimeError):
    """The verification reads cannot be placed in one snapshot. An error, never `UNVERIFIABLE`."""


def _atoms(value: Any, *, what: str) -> int:
    if value is None:
        return 0
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if not amount.is_finite():
        raise ReconciliationReadError(f"{what} read back as {amount!r}, which is not money")
    scaled = amount.scaleb(_ATOM_EXPONENT)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ReconciliationReadError(f"{what} {amount} is not a whole number of 1e-8 atoms")
    return int(integral)


def _money_text(atoms: int | None) -> str | None:
    if atoms is None:
        return None
    # Integer arithmetic, so no Decimal context and no exponent notation ("1E-8") can reach the record.
    units, fraction = divmod(abs(atoms), 10**_ATOM_EXPONENT)
    return f"{'-' if atoms < 0 else ''}{units}.{fraction:08d}"


def _edge_key(edge: Edge) -> tuple[bytes, bytes]:
    return (edge[0].bytes, edge[1].bytes)


@dataclass(frozen=True)
class ReconciliationOutcome:
    """One equivalent's criterion (a) verdict and the evidence behind it."""

    equivalent_id: uuid.UUID
    findings: tuple[dict[str, Any], ...]
    missing_evidence: tuple[str, ...]
    edges_checked: int
    entries_read: int

    @property
    def status(self) -> str:
        # FAILED dominates: a conclusive contradiction stays conclusive whatever else is missing.
        if self.findings:
            return FAILED
        if self.missing_evidence:
            return UNVERIFIABLE
        return PASSED

    def detail(self) -> dict[str, Any]:
        return {
            "criterion": CRITERION_A,
            "edges_checked": self.edges_checked,
            "entries_read": self.entries_read,
            "missing_evidence": list(self.missing_evidence),
            "findings_total": len(self.findings),
            "findings": list(self.findings[:MAX_STORED_FINDINGS]),
        }

    def fingerprint(self) -> str:
        """sha256 over the status, the missing evidence and the FAULT IDENTITY of every finding.

        Two observations with the same fingerprint are the same verdict; a different one is a transition
        and gets a row of its own (`record_outcome`). Every finding counts, sorted and not capped.

        IDENTITY, NOT AMOUNTS. An `edge_residual` is identified by its edge and its `unexplained`
        amount; an `entry_arithmetic` by the journal row, which is immutable. `current_debt` and
        `journal_delta_sum` are deliberately NOT hashed: a legitimate journalled payment on an edge that
        is already FAILED moves both and leaves the fault exactly as it was, and hashing them would store
        a new row for every such payment. So the latest row's `detail` shows the amounts AS FIRST
        OBSERVED, and its `last_checked_at` says the same fault is still present.
        """

        def _canonical(value: Any) -> str:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))

        return hashlib.sha256(
            _canonical(
                {
                    "status": self.status,
                    "missing_evidence": sorted(self.missing_evidence),
                    "findings": sorted(_canonical(_fault_identity(finding)) for finding in self.findings),
                }
            ).encode("utf-8")
        ).hexdigest()


#: The fields that identify a fault, per finding kind. An unknown kind is identified by all of its fields,
#: which errs towards storing a transition rather than hiding one.
_FAULT_IDENTITY_FIELDS = {
    "edge_residual": ("kind", "debtor_id", "creditor_id", "unexplained"),
    "entry_arithmetic": ("kind", "entry_id"),
}


def _fault_identity(finding: dict[str, Any]) -> dict[str, Any]:
    fields = _FAULT_IDENTITY_FIELDS.get(finding.get("kind"), tuple(sorted(finding)))
    return {field: finding.get(field) for field in fields}


async def _journal_sums(session: Any, equivalent_id: uuid.UUID) -> tuple[dict[Edge, int], list[dict], int]:
    """Per-edge sum of every recorded delta, the rows that contradict their own arithmetic, row count."""

    entries = debt_journal_entries.c
    rows = (
        await session.execute(
            select(
                entries.id,
                entries.debtor_id,
                entries.creditor_id,
                entries.amount_before,
                entries.amount_after,
                entries.delta,
            ).where(entries.equivalent_id == equivalent_id)
        )
    ).all()

    sums: dict[Edge, int] = {}
    contradictions: list[dict[str, Any]] = []
    for row in rows:
        before = None if row.amount_before is None else _atoms(row.amount_before, what="amount_before")
        after = None if row.amount_after is None else _atoms(row.amount_after, what="amount_after")
        delta = _atoms(row.delta, what="delta")
        if (after or 0) - (before or 0) != delta:
            contradictions.append(
                {
                    "kind": "entry_arithmetic",
                    "entry_id": str(row.id),
                    "debtor_id": str(row.debtor_id),
                    "creditor_id": str(row.creditor_id),
                    "amount_before": _money_text(before),
                    "amount_after": _money_text(after),
                    "delta": _money_text(delta),
                }
            )
        edge = (row.debtor_id, row.creditor_id)
        sums[edge] = sums.get(edge, 0) + delta
    return sums, contradictions, len(rows)


async def _current_debts(session: Any, equivalent_id: uuid.UUID) -> dict[Edge, int]:
    rows = (
        await session.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                Debt.equivalent_id == equivalent_id
            )
        )
    ).all()
    debts: dict[Edge, int] = {}
    for debtor_id, creditor_id, amount in rows:
        edge = (debtor_id, creditor_id)
        # Accumulated rather than assigned, so a duplicated edge row could never hide one of its halves.
        debts[edge] = debts.get(edge, 0) + _atoms(amount, what="debts.amount")
    return debts


async def _has_baseline(session: Any, equivalent_id: uuid.UUID) -> bool:
    found = (
        await session.execute(
            select(debt_reconciliation_baselines.c.equivalent_id).where(
                debt_reconciliation_baselines.c.equivalent_id == equivalent_id
            )
        )
    ).first()
    return found is not None


async def verify_journal_equals_change(
    session: Any, equivalent_id: uuid.UUID
) -> ReconciliationOutcome:
    """Criterion (a) for one equivalent: four reads, no writes, arithmetic in atoms."""

    sums, findings, entries_read = await _journal_sums(session, equivalent_id)

    if not await _has_baseline(session, equivalent_id):
        # Without a baseline the edge predicate has nothing to be compared against. A row that
        # contradicts its own arithmetic is still conclusive, and still makes the result FAILED.
        return ReconciliationOutcome(
            equivalent_id=equivalent_id,
            findings=tuple(findings),
            missing_evidence=("baseline",),
            edges_checked=0,
            entries_read=entries_read,
        )

    debts = await _current_debts(session, equivalent_id)
    offset_columns = debt_reconciliation_baseline_offsets.c
    offsets: dict[Edge, int] = {
        (debtor_id, creditor_id): _atoms(amount, what="offset_amount")
        for debtor_id, creditor_id, amount in (
            await session.execute(
                select(
                    offset_columns.debtor_id,
                    offset_columns.creditor_id,
                    offset_columns.offset_amount,
                ).where(offset_columns.equivalent_id == equivalent_id)
            )
        ).all()
    }

    edges = set(debts) | set(sums) | set(offsets)
    for edge in sorted(edges, key=_edge_key):
        debt = debts.get(edge, 0)
        journal = sums.get(edge, 0)
        offset = offsets.get(edge, 0)
        if debt - journal != offset:
            findings.append(
                {
                    "kind": "edge_residual",
                    "debtor_id": str(edge[0]),
                    "creditor_id": str(edge[1]),
                    "current_debt": _money_text(debt),
                    "journal_delta_sum": _money_text(journal),
                    "baseline_offset": _money_text(offset),
                    "unexplained": _money_text(debt - journal - offset),
                }
            )

    return ReconciliationOutcome(
        equivalent_id=equivalent_id,
        findings=tuple(findings),
        missing_evidence=(),
        edges_checked=len(edges),
        entries_read=entries_read,
    )


async def record_outcome(session: Any, outcome: ReconciliationOutcome) -> str:
    """Persist a TRANSITION, not an observation. Does not commit. Returns `inserted` or `unchanged`.

    The latest row is read and written in the caller's one transaction: an identical fingerprint only
    advances its `last_checked_at`; anything else clears its `is_latest` and inserts the new verdict.

    CONCURRENCY, AND THE ONE CASE LEFT OPEN ON PURPOSE (coordinator decision, 2026-09-14). A run that
    read an older snapshot could, in principle, publish its verdict after a newer run published one.
    That is not prevented here, and these are the measured reasons:

    * Within one process the runs are strictly sequential: `_integrity_loop` (`app/main.py:270`) awaits
      the startup run (`:274`) and then each periodic run (`:281`) before scheduling the next, and it is
      started once (`:314`). A task that dies is recorded (`_on_background_task_done`, `:62`), not
      restarted concurrently.
    * No launcher, compose file or Dockerfile starts more than one worker (no `--workers`,
      `WEB_CONCURRENCY` or gunicorn anywhere in them). Overlap needs two or more processes on one
      database WITHOUT Redis, which is not a configured deployment. With Redis the integrity loop's
      distributed lock serialises the runs; without it that lock is a no-op
      (`app/utils/distributed_lock.py`).
    * The damage is bounded: a stale verdict is replaced on the next cycle (300 s,
      `INTEGRITY_CHECKPOINT_INTERVAL_SECONDS`), because the persisting state recomputes a different
      fingerprint from the stale one and is stored as a transition; and step 5c re-verifies under the
      owner lock before it holds anything.

    The partial unique index on the latest marker keeps at most one latest row per equivalent; it does
    not order publications. No ordering token and no publication lock are built.
    """

    results = debt_reconciliation_results.c
    now = datetime.now(timezone.utc)
    fingerprint = outcome.fingerprint()
    latest = (
        await session.execute(
            select(results.id, results.fingerprint).where(
                results.equivalent_id == outcome.equivalent_id,
                results.is_latest.is_(True),
            )
        )
    ).first()

    if latest is not None and latest.fingerprint == fingerprint:
        await session.execute(
            update(debt_reconciliation_results)
            .where(results.id == latest.id)
            .values(last_checked_at=now)
        )
        return "unchanged"

    if latest is not None:
        await session.execute(
            update(debt_reconciliation_results)
            .where(results.id == latest.id)
            .values(is_latest=False)
        )
    await session.execute(
        insert(debt_reconciliation_results).values(
            id=uuid.uuid4(),
            equivalent_id=outcome.equivalent_id,
            status=outcome.status,
            fingerprint=fingerprint,
            detail=outcome.detail(),
            checked_at=now,
            last_checked_at=now,
            is_latest=True,
        )
    )
    return "inserted"


async def open_verification_snapshot(session: Any) -> None:
    """Begin ONE read transaction for all of the verifier's reads, or refuse.

    The scheduled verifier reads journal, baseline, debts and offsets WITHOUT the owner lock, so its
    verdict is sound only if the four reads see one snapshot. A false FAILED is the worst outcome in this
    programme: step 5c will hold money on it.

    MEASURED 2026-09-14 by forced interleaving (a real payment committed between `_journal_sums` and
    `_current_debts`, `test_step5a_*_a_payment_committed_between_the_verifiers_reads_*`), before this
    function existed:

    * PostgreSQL, test engine at READ COMMITTED: a FALSE FAILED. Each statement took a new snapshot. The
      application engine's SERIALIZABLE would have hidden it, but that is configuration
      (`DB_POSTGRES_ISOLATION_LEVEL`), not something the verifier may lean on. So the verifier asks for
      REPEATABLE READ READ ONLY itself: one snapshot from its first read, and no write inside it.
    * SQLite: the four reads DID share one snapshot (the transaction control's deferred `BEGIN` holds a
      WAL read snapshot) and the verdict computed was PASSED - but the RESULT WRITE in the same
      transaction failed with SQLITE_BUSY_SNAPSHOT ("database is locked"): a read transaction cannot
      become a write transaction after someone else committed. So the caller ends this transaction
      before it writes anything. An SQLite engine without the transaction control has no read
      transaction at all, and is refused.
    """

    bind = session.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        await session.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
    elif dialect == "sqlite":
        if not sqlite_transaction_control_is_installed(bind):
            raise ReconciliationSnapshotError(
                "this SQLite engine has no explicit transaction control (T1525), so the verifier's reads "
                "would not share a snapshot; refusing to produce a verdict"
            )
        await session.connection()
    else:
        raise ReconciliationSnapshotError(f"no snapshot recipe for dialect {dialect!r}")


@dataclass(frozen=True)
class BaselineTaken:
    equivalent_id: uuid.UUID
    offsets_recorded: int
    edges_seen: int
    entries_read: int
    entry_arithmetic_contradictions: int


async def take_baseline(session: Any, equivalent_id: uuid.UUID) -> BaselineTaken:
    """Take THE baseline of one equivalent inside the caller's transaction, under its owner lock.

    The caller commits; one equivalent is one transaction. Run it after seeding and before ordinary
    money operations, or - on an upgrade - as an explicit cutover on a quiet system
    (`scripts/take_reconciliation_baseline.py`). It adopts whatever the journal does not explain and
    certifies none of it.

    THE OWNER LOCK serialises this with payments and clearing on PostgreSQL (a no-op on SQLite, as for
    every other holder of it). Under `SERIALIZABLE` the snapshot is taken at the lock statement, before
    any wait, so a money operation that commits while this waits is invisible here - and that is still
    consistent, because such an operation changed `debts` and wrote the matching entries in the same
    commit: its edges keep `debt - sum(delta)` unchanged.
    """

    from app.core.payments.engine import PaymentEngine

    await PaymentEngine(session).acquire_staged_equivalent_owner_locks([equivalent_id])

    if await _has_baseline(session, equivalent_id):
        raise BaselineAlreadyTaken(
            f"equivalent {equivalent_id} already has a reconciliation baseline; there is exactly one "
            f"per equivalent and nothing re-baselines"
        )

    sums, contradictions, entries_read = await _journal_sums(session, equivalent_id)
    debts = await _current_debts(session, equivalent_id)
    edges = set(debts) | set(sums)
    offsets = {
        edge: debts.get(edge, 0) - sums.get(edge, 0)
        for edge in sorted(edges, key=_edge_key)
        if debts.get(edge, 0) - sums.get(edge, 0) != 0
    }

    await session.execute(
        insert(debt_reconciliation_baselines).values(
            equivalent_id=equivalent_id, taken_at=datetime.now(timezone.utc)
        )
    )
    if offsets:
        await session.execute(
            insert(debt_reconciliation_baseline_offsets),
            [
                {
                    "equivalent_id": equivalent_id,
                    "debtor_id": debtor_id,
                    "creditor_id": creditor_id,
                    "offset_amount": Decimal(atoms).scaleb(-_ATOM_EXPONENT),
                }
                for (debtor_id, creditor_id), atoms in offsets.items()
            ],
        )

    return BaselineTaken(
        equivalent_id=equivalent_id,
        offsets_recorded=len(offsets),
        edges_seen=len(edges),
        entries_read=entries_read,
        entry_arithmetic_contradictions=len(contradictions),
    )


async def run_scheduled_reconciliation(
    session_factory: Callable[[], Any],
    *,
    equivalent_ids: Iterable[uuid.UUID] | None = None,
) -> dict[str, int]:
    """Verify every equivalent in its own fresh session and transaction, persisting each result.

    Called only from the scheduled integrity loop, after the checkpoints have committed
    (`app/main.py`). Inactive equivalents are verified too: reading is not moving money.

    A failure while verifying ONE equivalent is logged as an error and leaves NO result row for it -
    an error is not `UNVERIFIABLE` and nothing is substituted for it - and the next equivalent is still
    verified, because they share no state. Listing the equivalents is not caught: without the list
    there is nothing to verify, and the caller logs it.
    """

    if equivalent_ids is None:
        async with session_factory() as session:
            equivalent_ids = (
                await session.execute(select(Equivalent.id).order_by(Equivalent.code))
            ).scalars().all()

    counts = {
        PASSED: 0,
        FAILED: 0,
        UNVERIFIABLE: 0,
        "error": 0,
        "rows_inserted": 0,
        "rows_unchanged": 0,
    }
    for equivalent_id in list(equivalent_ids):
        try:
            # ONE snapshot for the four verification reads, ended before anything is written.
            async with session_factory() as session:
                await open_verification_snapshot(session)
                outcome = await verify_journal_equals_change(session, equivalent_id)
                await session.rollback()
            # The latest-row read and the write, in one transaction of their own.
            async with session_factory() as session:
                stored = await record_outcome(session, outcome)
                await session.commit()
        except Exception:  # noqa: BLE001 - classified: an error, recorded as one, no result substituted
            counts["error"] += 1
            logger.exception("debt_reconciliation.error equivalent_id=%s", equivalent_id)
            continue
        counts[outcome.status] += 1
        counts[f"rows_{stored}"] += 1

    logger.info(
        "debt_reconciliation.completed passed=%d failed=%d unverifiable=%d errors=%d "
        "rows_inserted=%d rows_unchanged=%d",
        counts[PASSED],
        counts[FAILED],
        counts[UNVERIFIABLE],
        counts["error"],
        counts["rows_inserted"],
        counts["rows_unchanged"],
    )
    return counts
