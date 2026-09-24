"""Debt reconciliation: criterion (a) DETECTION OF CHANGE AROUND THE APPLICATION, and criterion (b)
THE RECORDED CHANGE EQUALS THE RECORDED INTENT, per operation kind.

Programme 015, step 5a (`T1501`, `T1505` criterion (a), `T1508` corruption shapes for (a)) and step 5b
(`T1505` criterion (b), `T1508` shapes for (b)). Decisions: spec.md, "Ключевое ревью шага 5: `ON-TRACK`,
критерий (а) строится как обнаружение", 2026-09-14.

CRITERION (a). For one equivalent, over the UNION of edges that have a current debt, journal entries,
or a baseline offset:

    current debt(edge) - sum of every recorded journal delta(edge) == baseline offset(edge)

plus, per journal row, `amount_after - amount_before == delta`. On PostgreSQL the row arithmetic is
already a CHECK constraint (`chk_debt_journal_entries_delta_arithmetic`, migration 024); on SQLite it is
not, because SQLite binds `Numeric` through float. The row check is done here on both dialects because
one code path is cheaper to keep right than two; on PostgreSQL it can only fire if the constraint is gone.

CRITERION (b), per operation that touched or named the equivalent, from what the ENVELOPE recorded:

    CLEARING  (intent v1)  FULL RECOMPUTATION. Every cycle edge drops by the cycle minimum; the recorded
                           `clear_amount` must be that minimum; the cycle must be closed; the journal's
                           per-edge delta sum and each edge's first `amount_before` must equal what the
                           recorded pre-amounts imply.
    PAYMENT   (intent v2)  FULL RECOMPUTATION. The flows are replayed in their recorded order over the
                           recorded pre-state of BOTH directions of every flow pair, with the payment rule
                           re-implemented here in integer atoms (never imported from the engine); the
                           netted per-edge deltas and first `amount_before` must match the journal.
    PAYMENT   (intent v1)  STRUCTURAL ONLY. The envelope carries no pre-state, so nothing is replayed:
                           the intent must parse and every journalled edge must be a direction of a flow
                           pair. It is recorded as `structural_only` and is never a full recomputation.
    INJECT    (intent v1)  AN HONEST SUBSET. The intent is the raw scenario event; the scenario's
                           default equivalent, the pid map, the trust line's limit and state and the
                           event's `max_total_amount` are NOT recorded, so which edge and how much each
                           effect wrote cannot be recomputed. What CAN be: every entry is an increase,
                           every delta is whole cents (the writer rounds every amount down to 0.01), no
                           more edges than `inject_debt` effects, and no more debt in total than the
                           rounded-down positive amounts the intent names.
    SEED, TEST_FIXTURE     NOT EXAMINED. Refused after the baseline (5a), adopted by it before.

and for every examined kind: the envelope's version is one a rule above reads. The stored intent digest
is NOT recomputed: every ledger-relevant change of an intent is refuted by its kind's rule. Findings of (b) are immutable journal and intent facts, so they are stable fault
identities; they enter the same outcome and the same fingerprint as (a).

ALL ARITHMETIC IS IN INTEGER ATOMS (1e-8) IN PYTHON, never an SQL `SUM`: on SQLite an aggregate over a
`NUMERIC` column is a float sum, and a float sum is not money evidence.

THE THREAT MODEL IS NARROW, AND THE WHOLE OF IT IS STATED. `docs/ru/02-protocol-spec.md` §11.2.1 names
the comparison of `debts` with the history that produced them as the real detector, and §11.6 asks for
periodic checks; this runs after commit, from the scheduled loop only, against independently stored
tables.

    (a) detects: an operator's SQL to one side (`debts` or the journal), a partial restore, a future
        writer that is not instrumented, a journal entry that disappeared or was duplicated.
    (b) detects: a writer that journals a WRONG change faithfully (the `C6` counterexamples), and a
        coordinated rewrite of debts and journal that leaves the recorded intent behind - including
        net-neutral cycle inflation - for the kinds with full recomputation; for INJECT only what its
        subset names; for v1 payments only an edge outside the flow pairs.
    Neither detects: a coordinated rewrite of debts, journal AND intent; an intent rewrite that changes
        nothing a rule reads (a lock id, say); a consistent full
        restore; code that disables this verifier; incorrect opening balances (the baseline adopts, it
        does not certify); a v1 payment or an inject that wrote a wrong amount on a named edge.

It is DETECTION, not tamper protection, and nothing downstream may call it more.

THE REACTION (step 5c, `T1516`/`T1546`, `react_to_failed`): a scheduled `FAILED`, confirmed by a full
re-run under the equivalent's owner lock, sets the equivalent's integrity hold, which stops money at the
T1544 boundary. Nothing else: no notification subsystem, no checkpoint search, no report, no repair, and
never an automatic clear - an admin clears it after a later `PASSED`.

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
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from sqlalchemy import insert, or_, select, update

from app.db.journal_tables import (
    debt_journal_entries,
    debt_operation_equivalents,
    debt_operations,
)
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import (
    debt_reconciliation_baseline_offsets,
    debt_reconciliation_baselines,
    debt_reconciliation_results,
)

__all__ = [
    "BaselineAlreadyTaken",
    "BaselineTaken",
    "CRITERION_A",
    "CRITERION_B",
    "FAILED",
    "FULL_RECOMPUTATION",
    "HOLD_ALREADY_HELD",
    "HOLD_EQUIVALENT_GONE",
    "HOLD_METRIC_EVENT",
    "HOLD_NOT_CONFIRMED",
    "HOLD_SET",
    "HoldDecision",
    "NOT_EXAMINED",
    "PASSED",
    "ReconciliationOutcome",
    "ReconciliationReadError",
    "STRUCTURAL_ONLY",
    "SUBSET",
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
CRITERION_B = "b:recorded_change_equals_recorded_intent:per_operation_kind"

#: How much of criterion (b) an operation received. Written into the result so that a PASSED over an
#: operation that could only be checked in part never reads as a full recomputation.
FULL_RECOMPUTATION = "full_recomputation"
STRUCTURAL_ONLY = "structural_only"
SUBSET = "subset"
NOT_EXAMINED = "not_examined"

#: The levels that LIMIT what a PASSED claims. They enter the fingerprint; `full_recomputation` and
#: `not_examined` do not (see `ReconciliationOutcome.fingerprint`).
_LIMITED_LEVELS = frozenset({STRUCTURAL_ONLY, SUBSET})

#: `(kind, intent_encoding_version)` -> the rule that reads that envelope. Anything else is a finding.
_READABLE_ENVELOPES = {
    ("CLEARING", 1): FULL_RECOMPUTATION,
    ("PAYMENT", 2): FULL_RECOMPUTATION,
    ("PAYMENT", 1): STRUCTURAL_ONLY,
    ("INJECT", 1): SUBSET,
}

#: Written only before a baseline (5a refuses them after one) and adopted by it; nothing recomputes them.
_NOT_EXAMINED_KINDS = frozenset({"SEED", "TEST_FIXTURE"})

#: The stored record keeps the first findings and the total; a catastrophic mismatch must not turn
#: every scheduled run into a megabyte row.
MAX_STORED_FINDINGS = 50

_ATOM_EXPONENT = 8

#: One cent in atoms: `inject_debt` rounds every amount down to 0.01 before it writes.
_CENT_ATOMS = 10**6

Edge = tuple[uuid.UUID, uuid.UUID]


class ReconciliationReadError(RuntimeError):
    """A value read back could not be interpreted as money. An error, not `UNVERIFIABLE`."""


class BaselineAlreadyTaken(RuntimeError):
    """The equivalent already has its one baseline. Nothing here re-baselines."""


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
    """One equivalent's verdict over criteria (a) and (b), and the evidence behind it."""

    equivalent_id: uuid.UUID
    findings: tuple[dict[str, Any], ...]
    missing_evidence: tuple[str, ...]
    edges_checked: int
    entries_read: int
    #: `(level, operation kind, count)` for criterion (b), sorted. Empty when no operation was read.
    criterion_b_coverage: tuple[tuple[str, str, int], ...] = ()

    @property
    def status(self) -> str:
        # FAILED dominates: a conclusive contradiction stays conclusive whatever else is missing.
        if self.findings:
            return FAILED
        if self.missing_evidence:
            return UNVERIFIABLE
        return PASSED

    def _criterion_b_limited(self) -> list[str]:
        return sorted(
            f"{level}:{kind}"
            for level, kind, count in self.criterion_b_coverage
            if count and level in _LIMITED_LEVELS
        )

    def detail(self) -> dict[str, Any]:
        coverage: dict[str, dict[str, int]] = {}
        for level, kind, count in self.criterion_b_coverage:
            coverage.setdefault(level, {})[kind] = count
        return {
            "criterion": CRITERION_A,
            "edges_checked": self.edges_checked,
            "entries_read": self.entries_read,
            "missing_evidence": list(self.missing_evidence),
            "findings_total": len(self.findings),
            "findings": list(self.findings[:MAX_STORED_FINDINGS]),
            "criterion_b": {
                "criterion": CRITERION_B,
                "operations_examined": sum(
                    count for level, _kind, count in self.criterion_b_coverage if level != NOT_EXAMINED
                ),
                "coverage": coverage,
                # What a PASSED here does NOT claim: operations checked only structurally or in part.
                "limited": self._criterion_b_limited(),
            },
        }

    def fingerprint(self) -> str:
        """sha256 over the status, the missing evidence, the FAULT IDENTITY of every finding, and the
        LIMITS of criterion (b).

        Two observations with the same fingerprint are the same verdict; a different one is a transition
        and gets a row of its own (`record_outcome`). Every finding counts, sorted and not capped.

        IDENTITY, NOT AMOUNTS. An `edge_residual` is identified by its edge and its `unexplained`
        amount; an `entry_arithmetic` by the journal row, which is immutable. `current_debt` and
        `journal_delta_sum` are deliberately NOT hashed: a legitimate journalled payment on an edge that
        is already FAILED moves both and leaves the fault exactly as it was, and hashing them would store
        a new row for every such payment. So the latest row's `detail` shows the amounts AS FIRST
        OBSERVED, and its `last_checked_at` says the same fault is still present. Every criterion (b)
        finding is built from the envelope and its entries, which are immutable, so all of its fields
        are identity.

        THE LIMITS OF (b) ARE HASHED AND THE COUNTS ARE NOT (step 5b). The row's `detail` is the verdict
        as first observed, so a PASSED stored before the first v1 payment or inject would otherwise keep
        saying nothing about them for as long as the status stays PASSED. `limited` - the levels
        `structural_only` and `subset`, per kind, that occur at all - changes only when such a kind first
        appears, which bounds the transitions; operation counts change with every payment and are not
        hashed.
        """

        def _canonical(value: Any) -> str:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))

        return hashlib.sha256(
            _canonical(
                {
                    "status": self.status,
                    "missing_evidence": sorted(self.missing_evidence),
                    "findings": sorted(_canonical(_fault_identity(finding)) for finding in self.findings),
                    "criterion_b_limited": self._criterion_b_limited(),
                }
            ).encode("utf-8")
        ).hexdigest()


#: The fields that identify a fault, per finding kind. An unknown kind is identified by all of its fields,
#: which errs towards storing a transition rather than hiding one.
_FAULT_IDENTITY_FIELDS = {
    "edge_residual": ("kind", "debtor_id", "creditor_id", "unexplained"),
    "entry_arithmetic": ("kind", "entry_id"),
    "b_intent_malformed": ("kind", "operation_id", "reason"),
    "b_version_unsupported": ("kind", "operation_id", "intent_encoding_version"),
    "b_delta_mismatch": ("kind", "operation_id", "debtor_id", "creditor_id", "expected_delta", "recorded_delta"),
    "b_prestate_mismatch": (
        "kind",
        "operation_id",
        "debtor_id",
        "creditor_id",
        "recorded_prestate",
        "journal_amount_before",
    ),
    "b_payment_v1_structure": ("kind", "operation_id", "rule", "debtor_id", "creditor_id"),
    "b_inject_subset": ("kind", "operation_id", "rule", "debtor_id", "creditor_id"),
}


def _fault_identity(finding: dict[str, Any]) -> dict[str, Any]:
    fields = _FAULT_IDENTITY_FIELDS.get(finding.get("kind"), tuple(sorted(finding)))
    return {field: finding.get(field) for field in fields}


@dataclass(frozen=True)
class _Entry:
    """One journal row as criterion (b) reads it: atoms, and `amount_before` as recorded (None for I)."""

    flush_ordinal: int
    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    effect: str
    amount_before: int | None
    delta: int


async def _journal_sums(
    session: Any, equivalent_id: uuid.UUID
) -> tuple[dict[Edge, int], list[dict], int, dict[uuid.UUID, list[_Entry]]]:
    """Per-edge sum of every recorded delta, the rows that contradict their own arithmetic, row count,
    and the same rows grouped by operation for criterion (b). ONE read serves both criteria."""

    entries = debt_journal_entries.c
    rows = (
        await session.execute(
            select(
                entries.id,
                entries.operation_id,
                entries.flush_ordinal,
                entries.debtor_id,
                entries.creditor_id,
                entries.effect,
                entries.amount_before,
                entries.amount_after,
                entries.delta,
            ).where(entries.equivalent_id == equivalent_id)
        )
    ).all()

    sums: dict[Edge, int] = {}
    contradictions: list[dict[str, Any]] = []
    by_operation: dict[uuid.UUID, list[_Entry]] = {}
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
        by_operation.setdefault(row.operation_id, []).append(
            _Entry(
                flush_ordinal=int(row.flush_ordinal),
                debtor_id=row.debtor_id,
                creditor_id=row.creditor_id,
                effect=str(row.effect),
                amount_before=before,
                delta=delta,
            )
        )
    return sums, contradictions, len(rows), by_operation


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


# ==================================================================================================
# Criterion (b)
# ==================================================================================================


async def _operations(session: Any, equivalent_id: uuid.UUID) -> list[Any]:
    """Every envelope that NAMED this equivalent or has an entry in it, in one read.

    Named: `debt_operation_equivalents`, written at completion for the intent's equivalents and the
    touched ones - so an operation whose entries in this equivalent went missing is still found and
    recomputed against nothing. Has an entry: the journal's own foreign key.
    """

    ops = debt_operations.c
    named = select(debt_operation_equivalents.c.operation_id).where(
        debt_operation_equivalents.c.equivalent_id == equivalent_id
    )
    touched = select(debt_journal_entries.c.operation_id).where(
        debt_journal_entries.c.equivalent_id == equivalent_id
    )
    return list(
        (
            await session.execute(
                select(
                    ops.id,
                    ops.kind,
                    ops.tx_id,
                    ops.intent,
                    ops.intent_digest,
                    ops.intent_encoding_version,
                ).where(or_(ops.id.in_(named), ops.id.in_(touched)))
            )
        ).all()
    )


def _intent_atoms(value: Any) -> int | None:
    """An amount written in an intent, as atoms - or None when it is not exactly representable money."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    scaled = amount.scaleb(_ATOM_EXPONENT)
    integral = scaled.to_integral_value()
    if scaled != integral:
        return None
    return int(integral)


def _intent_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class _Malformed(Exception):
    """The intent cannot be read by its kind's rule. Carried as a finding, never raised out."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _finding(kind: str, op: Any, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "operation_id": str(op.id), "operation_kind": op.kind, **fields}


def _apply_payment_flow(state: dict[Edge, int], sender: uuid.UUID, receiver: uuid.UUID, amount: int) -> None:
    """The documented payment rule, in integer atoms, written here and NOT imported from the engine.

    Reduce the receiver's debt to the sender first, put what remains on the sender's debt to the
    receiver, then net a mutual pair. Importing `PaymentEngine._apply_flow` would check a wrong writer
    against itself.
    """

    remaining = amount
    reverse = state.get((receiver, sender), 0)
    if reverse > 0:
        reduction = min(remaining, reverse)
        state[(receiver, sender)] = reverse - reduction
        remaining -= reduction
    if remaining > 0:
        state[(sender, receiver)] = state.get((sender, receiver), 0) + remaining
    forward = state.get((sender, receiver), 0)
    reverse = state.get((receiver, sender), 0)
    if forward > 0 and reverse > 0:
        net = min(forward, reverse)
        state[(sender, receiver)] = forward - net
        state[(receiver, sender)] = reverse - net


def _journal_by_edge(entries: list[_Entry]) -> tuple[dict[Edge, int], dict[Edge, int]]:
    """Per edge: the operation's delta sum, and `amount_before` of its FIRST entry (an insert's is 0)."""

    sums: dict[Edge, int] = {}
    first: dict[Edge, tuple[int, int]] = {}
    for entry in entries:
        edge = (entry.debtor_id, entry.creditor_id)
        sums[edge] = sums.get(edge, 0) + entry.delta
        if edge not in first or entry.flush_ordinal < first[edge][0]:
            first[edge] = (entry.flush_ordinal, entry.amount_before or 0)
    return sums, {edge: before for edge, (_ordinal, before) in first.items()}


def _compare_recomputation(
    op: Any, entries: list[_Entry], expected: dict[Edge, int], prestate: dict[Edge, int]
) -> list[dict[str, Any]]:
    """The journal of ONE operation against its recomputation: delta sums, and where each edge started."""

    recorded, first_before = _journal_by_edge(entries)
    findings: list[dict[str, Any]] = []
    for edge in sorted(set(expected) | set(recorded), key=_edge_key):
        want, have = expected.get(edge, 0), recorded.get(edge, 0)
        if want != have:
            findings.append(
                _finding(
                    "b_delta_mismatch",
                    op,
                    debtor_id=str(edge[0]),
                    creditor_id=str(edge[1]),
                    expected_delta=_money_text(want),
                    recorded_delta=_money_text(have),
                )
            )
    for edge in sorted(first_before, key=_edge_key):
        if prestate.get(edge) != first_before[edge]:
            findings.append(
                _finding(
                    "b_prestate_mismatch",
                    op,
                    debtor_id=str(edge[0]),
                    creditor_id=str(edge[1]),
                    recorded_prestate=_money_text(prestate.get(edge)),
                    journal_amount_before=_money_text(first_before[edge]),
                )
            )
    return findings


def _require_tx_id(op: Any, intent: dict[str, Any]) -> None:
    if intent.get("tx_id") != op.tx_id:
        raise _Malformed("tx_id_differs_from_the_envelope")


def _clearing(op: Any, intent: dict[str, Any], entries: list[_Entry], equivalent_id: uuid.UUID) -> list:
    _require_tx_id(op, intent)
    cycle = intent.get("cycle")
    clear = _intent_atoms(intent.get("clear_amount"))
    cycle_equivalent = _intent_uuid(intent.get("equivalent_id"))
    if not isinstance(cycle, list) or len(cycle) < 2 or clear is None or clear <= 0 or cycle_equivalent is None:
        raise _Malformed("clearing_intent_shape")
    pre: dict[Edge, int] = {}
    for item in cycle:
        if not isinstance(item, dict):
            raise _Malformed("clearing_intent_shape")
        debtor, creditor = _intent_uuid(item.get("debtor_id")), _intent_uuid(item.get("creditor_id"))
        amount = _intent_atoms(item.get("amount"))
        if debtor is None or creditor is None or debtor == creditor or amount is None or amount <= 0:
            raise _Malformed("clearing_intent_shape")
        if (debtor, creditor) in pre:
            raise _Malformed("clearing_cycle_repeats_an_edge")
        pre[(debtor, creditor)] = amount

    findings: list[dict[str, Any]] = []
    # A cycle: every participant owes on exactly as many of its edges as it is owed on. Otherwise
    # reducing every edge by one amount moves net positions, which a clearing may never do.
    if Counter(debtor for debtor, _ in pre) != Counter(creditor for _, creditor in pre):
        findings.append(_finding("b_intent_malformed", op, reason="clearing_cycle_is_not_closed"))
    # The documented rule is the cycle minimum; the writer's own number is checked, not trusted.
    minimum = min(pre.values())
    if clear != minimum:
        findings.append(_finding("b_intent_malformed", op, reason="clear_amount_is_not_the_cycle_minimum"))

    here = cycle_equivalent == equivalent_id
    expected = {edge: -minimum for edge in pre} if here else {}
    return findings + _compare_recomputation(op, entries, expected, pre if here else {})


def _payment_flows(intent: dict[str, Any]) -> list[tuple[uuid.UUID, uuid.UUID, int, uuid.UUID]]:
    locks = intent.get("locks")
    if not isinstance(locks, list) or not locks:
        raise _Malformed("payment_intent_shape")
    flows = []
    for lock in locks:
        raw = lock.get("flows") if isinstance(lock, dict) else None
        if not isinstance(raw, list) or not raw:
            raise _Malformed("payment_intent_shape")
        for flow in raw:
            if not isinstance(flow, dict):
                raise _Malformed("payment_intent_shape")
            sender, receiver = _intent_uuid(flow.get("from")), _intent_uuid(flow.get("to"))
            amount, flow_equivalent = _intent_atoms(flow.get("amount")), _intent_uuid(flow.get("equivalent"))
            if (
                sender is None
                or receiver is None
                or sender == receiver
                or amount is None
                or amount <= 0
                or flow_equivalent is None
            ):
                raise _Malformed("payment_intent_shape")
            flows.append((sender, receiver, amount, flow_equivalent))
    return flows


def _payment_v2(op: Any, intent: dict[str, Any], entries: list[_Entry], equivalent_id: uuid.UUID) -> list:
    _require_tx_id(op, intent)
    flows = [(s, r, a) for s, r, a, e in _payment_flows(intent) if e == equivalent_id]
    raw_prestate = intent.get("prestate")
    if not isinstance(raw_prestate, list):
        raise _Malformed("payment_prestate_shape")
    prestate: dict[Edge, int] = {}
    for item in raw_prestate:
        if not isinstance(item, dict):
            raise _Malformed("payment_prestate_shape")
        item_equivalent = _intent_uuid(item.get("equivalent"))
        debtor, creditor = _intent_uuid(item.get("debtor")), _intent_uuid(item.get("creditor"))
        amount = _intent_atoms(item.get("amount"))
        if item_equivalent is None or debtor is None or creditor is None or amount is None or amount < 0:
            raise _Malformed("payment_prestate_shape")
        if item_equivalent != equivalent_id:
            continue
        if (debtor, creditor) in prestate:
            raise _Malformed("payment_prestate_repeats_an_edge")
        prestate[(debtor, creditor)] = amount

    # BOTH directions of every flow pair, and nothing else: the rule reads both, so a missing direction
    # is not a zero this verifier may assume.
    pairs = {edge for s, r, _a in flows for edge in ((s, r), (r, s))}
    if set(prestate) != pairs:
        raise _Malformed("payment_prestate_is_not_both_directions_of_the_flow_pairs")

    state = dict(prestate)
    for sender, receiver, amount in flows:
        _apply_payment_flow(state, sender, receiver, amount)
    expected = {edge: state[edge] - prestate[edge] for edge in state if state[edge] != prestate[edge]}
    return _compare_recomputation(op, entries, expected, prestate)


def _payment_v1(op: Any, intent: dict[str, Any], entries: list[_Entry], equivalent_id: uuid.UUID) -> list:
    _require_tx_id(op, intent)
    pairs = {
        edge
        for s, r, _a, e in _payment_flows(intent)
        if e == equivalent_id
        for edge in ((s, r), (r, s))
    }
    return [
        _finding(
            "b_payment_v1_structure",
            op,
            rule="entry_outside_the_flow_pairs",
            debtor_id=str(edge[0]),
            creditor_id=str(edge[1]),
        )
        for edge in sorted({(e.debtor_id, e.creditor_id) for e in entries} - pairs, key=_edge_key)
    ]


def _inject_subset(op: Any, intent: dict[str, Any], entries: list[_Entry], _equivalent_id: uuid.UUID) -> list:
    effects = intent.get("effects")
    if not isinstance(effects, list):
        raise _Malformed("inject_intent_shape")
    inject_debt_effects = 0
    named_atoms = 0
    for effect in effects:
        if not isinstance(effect, dict) or str(effect.get("op") or "").strip() != "inject_debt":
            continue
        inject_debt_effects += 1
        try:
            amount = Decimal(str(effect.get("amount"))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        except (InvalidOperation, ValueError):
            continue
        if amount > 0:
            named_atoms += int(amount.scaleb(_ATOM_EXPONENT))

    def _rule(rule: str, edge: Edge | None = None) -> dict[str, Any]:
        return _finding(
            "b_inject_subset",
            op,
            rule=rule,
            debtor_id=None if edge is None else str(edge[0]),
            creditor_id=None if edge is None else str(edge[1]),
        )

    findings: list[dict[str, Any]] = []
    ordered = sorted(entries, key=lambda e: (e.flush_ordinal, e.debtor_id.bytes, e.creditor_id.bytes))
    for entry in ordered:
        edge = (entry.debtor_id, entry.creditor_id)
        if entry.effect not in ("I", "U") or entry.delta <= 0:
            findings.append(_rule("entry_is_not_an_increase", edge))
        if entry.delta % _CENT_ATOMS != 0:
            findings.append(_rule("delta_is_not_whole_cents", edge))
    if len({(e.debtor_id, e.creditor_id) for e in entries}) > inject_debt_effects:
        findings.append(_rule("more_edges_than_inject_debt_effects"))
    if sum(e.delta for e in entries) > named_atoms:
        findings.append(_rule("more_debt_than_the_intent_names"))
    return findings


_RULES = {
    ("CLEARING", 1): _clearing,
    ("PAYMENT", 2): _payment_v2,
    ("PAYMENT", 1): _payment_v1,
    ("INJECT", 1): _inject_subset,
}


def _criterion_b(
    equivalent_id: uuid.UUID,
    operations: list[Any],
    entries_by_operation: dict[uuid.UUID, list[_Entry]],
) -> tuple[list[dict[str, Any]], tuple[tuple[str, str, int], ...]]:
    findings: list[dict[str, Any]] = []
    coverage: Counter[tuple[str, str]] = Counter()
    for op in sorted(operations, key=lambda row: row.id.bytes):
        kind = str(op.kind)
        if kind in _NOT_EXAMINED_KINDS:
            coverage[(NOT_EXAMINED, kind)] += 1
            continue
        version = int(op.intent_encoding_version)
        level = _READABLE_ENVELOPES.get((kind, version))
        if level is None:
            findings.append(_finding("b_version_unsupported", op, intent_encoding_version=version))
            continue
        coverage[(level, kind)] += 1

        intent = op.intent
        if not isinstance(intent, dict):
            findings.append(_finding("b_intent_malformed", op, reason="intent_is_not_an_object"))
            continue
        # NO DIGEST RECOMPUTATION HERE, by coordinator decision (step 5b review round, D1). The stored
        # digest and the writer are unchanged. WHAT THAT GIVES UP, exactly: for CLEARING and PAYMENT v2 an
        # intent rewrite is still caught, because their rules read every ledger-relevant field of the
        # intent and recompute against the journal; what is lost there is only a rewrite of a field no
        # rule reads (a lock id). For PAYMENT v1 (structural only) and INJECT (subset), an independent
        # rewrite of the intent is NOT detected - their rules cannot recompute what the intent says. That
        # is accepted under the threat model: this is detection around the application, and a rewrite
        # of the intent that also moved the ledger remains a coordinated rewrite, outside it.
        try:
            findings.extend(_RULES[(kind, version)](op, intent, entries_by_operation.get(op.id, []), equivalent_id))
        except _Malformed as malformed:
            findings.append(_finding("b_intent_malformed", op, reason=malformed.reason))
    return findings, tuple(sorted((level, kind, count) for (level, kind), count in coverage.items()))


async def verify_journal_equals_change(
    session: Any, equivalent_id: uuid.UUID
) -> ReconciliationOutcome:
    """Criteria (a) and (b) for one equivalent: five reads, no writes, arithmetic in atoms."""

    sums, findings, entries_read, entries_by_operation = await _journal_sums(session, equivalent_id)

    # Criterion (b) needs no baseline: a recorded change that contradicts its recorded intent is
    # conclusive on its own, like a row that contradicts its own arithmetic.
    b_findings, coverage = _criterion_b(
        equivalent_id, await _operations(session, equivalent_id), entries_by_operation
    )
    findings.extend(b_findings)

    if not await _has_baseline(session, equivalent_id):
        # Without a baseline the edge predicate has nothing to be compared against. A row that
        # contradicts its own arithmetic is still conclusive, and still makes the result FAILED.
        return ReconciliationOutcome(
            equivalent_id=equivalent_id,
            findings=tuple(findings),
            missing_evidence=("baseline",),
            edges_checked=0,
            entries_read=entries_read,
            criterion_b_coverage=coverage,
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
        criterion_b_coverage=coverage,
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
    * The damage is bounded, and the bound is the interval PLUS a run's duration, not the interval:
      `_integrity_loop` waits `INTEGRITY_CHECKPOINT_INTERVAL_SECONDS` (300 s by default) after the
      previous run COMPLETES, and the next run then takes as long as the checkpoints and this verifier
      over every equivalent take. A stale verdict is replaced when that next run publishes, because the
      persisting state recomputes a different fingerprint from the stale one and is stored as a
      transition; and step 5c re-verifies under the owner lock before it holds anything.
    * THE REDIS LOCK IS NOT RENEWED. `redis_distributed_lock` sets its key once with a TTL
      (`INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS`, by default `max(30, interval)`, `app/main.py`) and never
      extends it, so a run longer than the TTL could overlap a second Redis-backed process. Acceptable
      for the single-worker v0.1 deployment; a renewable lease is registered as a follow-up and is not
      built here.

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

    The scheduled verifier reads journal, envelopes, baseline, debts and offsets WITHOUT the owner lock,
    so its verdict is sound only if the five reads see one snapshot. A false FAILED is the worst outcome in this
    programme: step 5c will hold money on it.

    MEASURED 2026-09-14 by forced interleaving (a real payment committed between `_journal_sums` and
    `_current_debts`, `test_step5a_*_a_payment_committed_between_the_verifiers_reads_*`), before this
    function existed:

    * PostgreSQL, test engine at READ COMMITTED: a FALSE FAILED. Each statement took a new snapshot. The
      application engine's SERIALIZABLE would have hidden it, but that is configuration
      (`DB_POSTGRES_ISOLATION_LEVEL`), not something the verifier may lean on. So the verifier asks for
      REPEATABLE READ READ ONLY itself: one snapshot from its first read, and no write inside it.
    * SQLite: the (then four) reads DID share one snapshot (the transaction control's deferred `BEGIN` holds a
      WAL read snapshot) and the verdict computed was PASSED - but the RESULT WRITE in the same
      transaction failed with SQLITE_BUSY_SNAPSHOT ("database is locked"): a read transaction cannot
      become a write transaction after someone else committed. So the caller ends this transaction
      before it writes anything. An SQLite engine without the transaction control had no read
      transaction at all, and was refused. (History: SQLite left in programme 017, stage 3, and with
      it this function's dialect dispatch; the PostgreSQL recipe below is the one that was here.)
    """

    await session.connection(
        execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
    )


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

    sums, contradictions, entries_read, _by_operation = await _journal_sums(session, equivalent_id)
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


# ==================================================================================================
# Step 5c: the reaction to a confirmed FAILED (`T1516`) - the integrity hold (`T1546`)
# ==================================================================================================

#: What one reaction did. `held` is the only decision that changed state.
HOLD_SET = "set"
HOLD_ALREADY_HELD = "already_held"
HOLD_NOT_CONFIRMED = "not_confirmed"
HOLD_EQUIVALENT_GONE = "equivalent_gone"

#: `RECOVERY_EVENTS_TOTAL{event=...}` of the hold. An existing counter, not a new metric.
HOLD_METRIC_EVENT = "debt_reconciliation_integrity_hold"


@dataclass(frozen=True)
class HoldDecision:
    decision: str
    result_id: uuid.UUID | None = None
    outcome: ReconciliationOutcome | None = None


async def _open_reaction_transaction(session: Any) -> None:
    """Begin the reaction's ONE transaction: every verifier read in one snapshot, and the writes in it.

    Unlike `open_verification_snapshot` this transaction writes (the evidence and the hold), so it is
    not read-only. PostgreSQL: REPEATABLE READ asked for here rather than leaned on from configuration,
    for the reason recorded on `open_verification_snapshot`; a concurrent update of the equivalent row
    then fails this transaction with 40001, which is an error of this reaction and is retried by the next
    scheduled run. (Until programme 017 stage 3 a SQLite arm used the transaction control's deferred
    `BEGIN` and anything else was refused; that dispatch left with SQLite.)
    """

    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})


async def _set_integrity_hold(session: Any, equivalent_id: uuid.UUID, result_id: uuid.UUID) -> None:
    """The hold itself: one UPDATE of the equivalent row, only while it is not held already."""

    await session.execute(
        update(Equivalent)
        .where(Equivalent.id == equivalent_id, Equivalent.integrity_hold_result_id.is_(None))
        .values(integrity_hold_result_id=result_id)
    )


async def _confirm_and_hold(session: Any, equivalent_id: uuid.UUID) -> HoldDecision:
    """Inside the reaction's transaction, under the owner lock: re-verify, and hold only on FAILED."""

    row = (
        await session.execute(
            select(Equivalent.integrity_hold_result_id).where(Equivalent.id == equivalent_id)
        )
    ).first()
    if row is None:
        return HoldDecision(HOLD_EQUIVALENT_GONE)
    if row[0] is not None:
        # IDEMPOTENT: an equivalent already held is not re-verified, re-pointed or re-announced.
        return HoldDecision(HOLD_ALREADY_HELD, result_id=row[0])

    # THE RE-RUN IS THE CONFIRMATION, not a formality: the verdict that triggered this reaction was
    # computed without the owner lock and published in another transaction, and `record_outcome` leaves
    # a stale publication open on the grounds that this re-run happens. Full criteria (a) and (b).
    outcome = await verify_journal_equals_change(session, equivalent_id)
    if outcome.status != FAILED:
        return HoldDecision(HOLD_NOT_CONFIRMED, outcome=outcome)

    await record_outcome(session, outcome)
    results = debt_reconciliation_results.c
    result_id = (
        await session.execute(
            select(results.id).where(
                results.equivalent_id == equivalent_id, results.is_latest.is_(True)
            )
        )
    ).scalar_one()
    await _set_integrity_hold(session, equivalent_id, result_id)
    return HoldDecision(HOLD_SET, result_id=result_id, outcome=outcome)


def _announce_hold(equivalent_id: uuid.UUID, decision: HoldDecision) -> None:
    """The structured log and the metric. Called ONLY after the hold's transaction has committed."""

    outcome = decision.outcome
    logger.error(
        "debt_reconciliation.integrity_hold_set equivalent_id=%s result_id=%s findings_total=%d",
        equivalent_id,
        decision.result_id,
        len(outcome.findings) if outcome is not None else 0,
    )
    try:
        from app.utils.metrics import RECOVERY_EVENTS_TOTAL

        RECOVERY_EVENTS_TOTAL.labels(event=HOLD_METRIC_EVENT, result=HOLD_SET).inc()
    except Exception:  # noqa: BLE001 - a metric must not turn a committed hold into an error
        logger.debug("debt_reconciliation.integrity_hold_metric_failed", exc_info=True)


async def react_to_failed(session_factory: Callable[[], Any], equivalent_id: uuid.UUID) -> HoldDecision:
    """React to ONE scheduled `FAILED`: confirm it under the owner lock and hold the equivalent.

    Decisions: step 5c brief and spec.md "Ключевое ревью шага 5", reaction paragraph. The order is the
    point, and each step is load-bearing:

    1. A FRESH TRANSACTION FOR THIS EQUIVALENT ALONE, so a hold that fails to commit cannot roll back a
       neighbour's.
    2. THE OWNER LOCK BEFORE THE AUTHORITATIVE SNAPSHOT. It is taken on a session of its own and held
       until the work transaction has committed, and the work transaction's first statement comes after
       it is granted. Under SERIALIZABLE or REPEATABLE READ a snapshot is taken at a transaction's first
       statement - before any wait on a lock in that transaction (T1544, measured) - so a lock taken in
       the work transaction itself would verify a state from before whatever it waited for.
    3. THE FULL VERIFIER RE-RUN inside it (`_confirm_and_hold`).
    4. IF STILL FAILED: the evidence (`record_outcome`) and the hold, in that one transaction; COMMIT.
    5. ONLY THEN the structured log and the metric. Emitted before the commit they would report a hold
       that rolled back.

    WHAT THE LOCK BINDS, on PostgreSQL (on SQLite the owner lock is a no-op and the refusal carries no
    race guarantee, as for T1544): a payment commit reads the hold with `FOR SHARE` after its own owner
    lock, so it either commits before this hold or meets 40001 and refuses on the retry; a clearing
    reads it in its fresh post-lock snapshot, so it either commits before this hold or refuses.

    Any exception propagates to the caller, which records it as an error of this reaction; nothing is
    announced.

    KNOWN EDGE, recorded and not built (step 5c review): a cancellation during the work `commit()` can
    leave its outcome ambiguous - the hold may be durable while the log and metric are never emitted, and
    later runs then return `already_held` without announcing it; no money moves and no false hold results.
    """

    from app.core.payments.engine import PaymentEngine

    async with session_factory() as lock_session:
        try:
            await PaymentEngine(lock_session).acquire_staged_equivalent_owner_locks([equivalent_id])
            async with session_factory() as work:
                await _open_reaction_transaction(work)
                decision = await _confirm_and_hold(work, equivalent_id)
                if decision.decision == HOLD_SET:
                    await work.commit()
                else:
                    await work.rollback()
        finally:
            # Releases the transaction-scoped owner lock - after the work transaction has ended.
            await lock_session.rollback()

    if decision.decision == HOLD_SET:
        _announce_hold(equivalent_id, decision)
    return decision


async def run_scheduled_reconciliation(
    session_factory: Callable[[], Any],
    *,
    equivalent_ids: Iterable[uuid.UUID] | None = None,
) -> dict[str, int]:
    """Verify every equivalent in its own fresh session and transaction, persisting each result.

    Called only from the scheduled integrity loop, after the checkpoints have committed
    (`app/main.py`). Inactive equivalents are verified too: reading is not moving money, and a held
    equivalent keeps being verified - a later `PASSED` is what permits an admin to clear it.

    A failure while verifying ONE equivalent is logged as an error and leaves NO result row for it -
    an error is not `UNVERIFIABLE` and nothing is substituted for it - and the next equivalent is still
    verified, because they share no state. Listing the equivalents is not caught: without the list
    there is nothing to verify, and the caller logs it.

    STEP 5c: a `FAILED` - and only a `FAILED`; never `UNVERIFIABLE`, never an error - is followed by
    `react_to_failed` for that equivalent, before the next equivalent is verified. A reaction that fails
    is counted in `hold_errors` and logged; it does not change the recorded verdict and does not stop
    the loop. The next scheduled run reacts again.
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
        f"hold_{HOLD_SET}": 0,
        f"hold_{HOLD_ALREADY_HELD}": 0,
        f"hold_{HOLD_NOT_CONFIRMED}": 0,
        f"hold_{HOLD_EQUIVALENT_GONE}": 0,
        "hold_errors": 0,
    }
    for equivalent_id in list(equivalent_ids):
        try:
            # ONE snapshot for the five verification reads, ended before anything is written.
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

        if outcome.status != FAILED:
            continue
        try:
            decision = await react_to_failed(session_factory, equivalent_id)
        except Exception:  # noqa: BLE001 - classified: an error of this reaction only; the loop goes on
            counts["hold_errors"] += 1
            logger.exception("debt_reconciliation.integrity_hold_error equivalent_id=%s", equivalent_id)
            continue
        counts[f"hold_{decision.decision}"] += 1

    logger.info(
        "debt_reconciliation.completed passed=%d failed=%d unverifiable=%d errors=%d "
        "rows_inserted=%d rows_unchanged=%d holds_set=%d hold_errors=%d",
        counts[PASSED],
        counts[FAILED],
        counts[UNVERIFIABLE],
        counts["error"],
        counts["rows_inserted"],
        counts["rows_unchanged"],
        counts[f"hold_{HOLD_SET}"],
        counts["hold_errors"],
    )
    return counts
