"""047 `T4703`: the peak memory of one reconciliation does not grow with the history, and the verdict is unchanged.

WHAT IS UNDER TEST. `app/core/ledger/reconciliation.py::verify_journal_equals_change` READS the history through
server-side cursors (`_streamed`) instead of materialising it: until 047 `T4703` the journal and every envelope with
its decoded intent were held at once, ≈6.5 KB of peak per operation (044 stage 2), so one run's peak grew with the
history without bound. Nothing else may change: the verdict, the findings, the counts, the fingerprint.

THE HISTORIES. A run of real payments through `PaymentService` (alternately a -> b and b -> a, 1.00 each, so every
pair nets to zero), then MULTIPLIED in SQL: every envelope, its `transactions` row, its equivalent rows and its journal
entries are copied under new ids and a new `tx_id` (the intent's `tx_id` rewritten with it - criterion (b) checks it).
Every copy is the same shape as its original, so the two histories differ only in length; a copy replays its own
recorded pre-state, so the copied history is PASSED exactly when the original is (asserted, with the counts). The
copy runs through THE named corruption helper (`tests/ledger_corruption.py`), because the journal's guards refuse
any other write of it; it changes no debt.

THE ORACLE (equivalence). `_oracle` is the reading as it was on `main` `e902e332`, copied here verbatim and kept
only in this test: the journal and the envelopes materialised, the operations sorted by `id.bytes`, the met set of
the correspondence. It calls the module's own rules (`_RULES`, `_structure`), which the change did not touch, so the
comparison isolates the reading. Histories: multi-hop payments, both clearing versions, a TEST_FIXTURE write, a
baseline in the middle, two equivalents over the SAME participants and a third over others, and then one corruption
after another, compared at every step and at stream batches of 1, 2, 3 and 256 rows.

TIER. PostgreSQL, a disposable clone per test (`tier_on_a_clone`).
"""

from __future__ import annotations

import gc
import itertools
import json
import tracemalloc
import uuid
from collections import Counter
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import and_, or_, select, text

from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import (
    FAILED,
    HOLD_SET,
    PASSED,
    UNVERIFIABLE,
    open_verification_snapshot,
    run_scheduled_reconciliation,
    verify_journal_equals_change,
)
from app.db.models.equivalent import Equivalent
from app.db.models.trustline import TrustLine
from tests.ledger_corruption import corrupt
from tests.p023_support import TEST_PLAN_ID, historical_v1_clearing, occurrence_of
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import _seed_triangle, _Triangle
from tests.unit.test_p015_step5a_reconciliation import _baseline, _database_url, _fixture_debts, _pay

#: One payment's worth of copies: the base history is this many real payments, alternately a -> b and b -> a.
BASE_PAYMENTS = 50
SMALL_OPERATIONS = 1_000
LARGE_OPERATIONS = 10 * SMALL_OPERATIONS

#: The bound of the reproducer (spec 047, Verification plan 1 (в)): the 10x history's peak within 1.5x the small
#: one's. Both histories are longer than a stream batch (`_STREAM_BATCH_ROWS` = 256 rows), so a streamed reading holds
#: the same at most in both and the ratio is ≈1; 1.5 leaves room for allocator and dict-resize noise. Measured on
#: `main` `e902e332`, before the change, the ratio is ≈9 (the module's Changelog entry records the numbers).
PEAK_RATIO_BOUND = 1.5

ATOM = "0.00000001"


@pytest.fixture(autouse=True)
def _routing_budget_is_not_under_test():
    """The payments here build a history; the router's wall-clock budget is not under test (as in 015 step 5b)."""

    from app.config import settings

    original = settings.ROUTING_PATH_FINDING_TIMEOUT_MS
    settings.ROUTING_PATH_FINDING_TIMEOUT_MS = 5000
    try:
        yield
    finally:
        settings.ROUTING_PATH_FINDING_TIMEOUT_MS = original


# ==============================================================================================
# Stand: histories
# ==============================================================================================


async def _alternating_payments(factory, triangle, count: int) -> list[str]:
    return [
        await _pay(factory, triangle, ["a", "b"] if index % 2 == 0 else ["b", "a"], "1")
        for index in range(count)
    ]


async def _columns(factory, table: str) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema()"
                        " AND table_name = :table ORDER BY ordinal_position"
                    ),
                    {"table": table},
                )
            ).scalars()
        )


async def _column_type(factory, table: str, column: str) -> str:
    async with factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT data_type FROM information_schema.columns WHERE table_schema = current_schema()"
                    " AND table_name = :table AND column_name = :column"
                ),
                {"table": table, "column": column},
            )
        ).scalar_one()


async def _multiply_payments(factory, equivalent_id: uuid.UUID, copies: range) -> None:
    """Copy every ORIGINAL payment envelope of the equivalent once per `k` in `copies`, with all it owns.

    New ids are `md5(<old id> # k)` and the new `tx_id` the same of the old one, so a copy is reproducible and never
    collides; the intent's `tx_id` is rewritten to it. Debts are not touched: every copy nets to zero as its original.
    """

    def new_uuid(expression: str) -> str:
        return f"md5(({expression})::text || '#' || k::text)::uuid"

    def new_tx_id(expression: str) -> str:
        return f"md5(({expression}) || '#' || k::text)::uuid::text"

    base = (
        "(SELECT o.* FROM debt_operations o WHERE o.kind = 'PAYMENT' AND position('#copy' IN o.identity) = 0"
        " AND EXISTS (SELECT 1 FROM debt_operation_equivalents e"
        f" WHERE e.operation_id = o.id AND e.equivalent_id = '{equivalent_id}'))"
    )
    series = f"generate_series({copies.start}, {copies.stop - 1}) AS k"
    intent_type = await _column_type(factory, "debt_operations", "intent")

    async def insert(table: str, alias: str, source: str, overrides: dict[str, str]) -> str:
        names = await _columns(factory, table)
        if "id" in names and "id" not in overrides:
            overrides = {**overrides, "id": new_uuid(f"{alias}.id")}
        expressions = [overrides.get(name, f'{alias}."{name}"') for name in names]
        quoted = ", ".join(f'"{name}"' for name in names)
        return f"INSERT INTO {table} ({quoted}) SELECT {', '.join(expressions)} FROM {source}"

    statements = [
        await insert(
            "transactions", "t", f"transactions t JOIN {base} o ON o.tx_id = t.tx_id CROSS JOIN {series}",
            {"tx_id": new_tx_id("t.tx_id"), "idempotency_key": "NULL"},
        ),
        await insert(
            "debt_operations", "o", f"{base} o CROSS JOIN {series}",
            {
                "identity": "o.identity || '#copy' || k::text",
                "tx_id": new_tx_id("o.tx_id"),
                "intent": f"jsonb_set(o.intent::jsonb, '{{tx_id}}', to_jsonb({new_tx_id('o.tx_id')}))::{intent_type}",
            },
        ),
        await insert(
            "debt_operation_equivalents", "e",
            f"debt_operation_equivalents e JOIN {base} o ON o.id = e.operation_id CROSS JOIN {series}",
            {"operation_id": new_uuid("o.id")},
        ),
        await insert(
            "debt_journal_entries", "j",
            f"debt_journal_entries j JOIN {base} o ON o.id = j.operation_id CROSS JOIN {series}",
            {"operation_id": new_uuid("o.id")},
        ),
    ]
    await corrupt(_database_url(factory), statements)


async def _history_of(factory, operations: int):
    """A triangle, its baseline, and `operations` payment envelopes: `BASE_PAYMENTS` real ones, the rest copies."""

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await _baseline(factory, triangle.equivalent.id)
    await _alternating_payments(factory, triangle, BASE_PAYMENTS)
    await _grow(factory, triangle, BASE_PAYMENTS, operations)
    return triangle


async def _grow(factory, triangle, have: int, want: int) -> None:
    assert want % BASE_PAYMENTS == 0 and want >= have, (have, want)
    if want > have:
        await _multiply_payments(
            factory, triangle.equivalent.id, range(have // BASE_PAYMENTS, want // BASE_PAYMENTS)
        )


def _assert_clean_history(outcome, operations: int) -> None:
    """The premise of every measurement: the history is the length claimed and PASSED - all of it was read."""

    assert outcome.status == PASSED, f"stand: the history is not PASSED: {outcome.findings[:5]}"
    assert outcome.entries_read == operations, outcome
    assert outcome.criterion_b_coverage == (("full_recomputation", "PAYMENT", operations),), outcome
    assert (outcome.transactions_read, outcome.transaction_pairs) == (operations, operations), outcome


async def _verify_in_a_snapshot(factory, equivalent_id):
    async with factory() as session:
        await open_verification_snapshot(session)
        outcome = await verify_journal_equals_change(session, equivalent_id)
        await session.rollback()
    return outcome


async def _peak_of_one_verification(factory, equivalent_id) -> tuple[int, object]:
    """The peak of Python allocations (`tracemalloc`) of ONE verification, above what was live when it started.

    A warm-up run first, so compiled statements and lazy imports are not the history; then the lower of two runs.
    """

    await _verify_in_a_snapshot(factory, equivalent_id)
    peaks, outcome = [], None
    for _ in range(2):
        gc.collect()
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            live_before = tracemalloc.get_traced_memory()[0]
            outcome = await _verify_in_a_snapshot(factory, equivalent_id)
            peaks.append(tracemalloc.get_traced_memory()[1] - live_before)
        finally:
            if not was_tracing:
                tracemalloc.stop()
    return min(peaks), outcome


# ==============================================================================================
# The reproducer: the peak does not grow with the history
# ==============================================================================================


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_the_peak_of_one_reconciliation_does_not_grow_with_the_history(db_session) -> None:
    """1 000 vs 10 000 payment envelopes of one shape: the larger history's peak within `PEAK_RATIO_BOUND` of the smaller.

    RED BEFORE (`main` `e902e332`, materialised reads): the peak grows with the history, ratio ≈9.
    MUTATION: `.all()` the envelopes in `_criterion_b` instead of streaming them - red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _history_of(factory, SMALL_OPERATIONS)
    small_peak, small = await _peak_of_one_verification(factory, triangle.equivalent.id)
    _assert_clean_history(small, SMALL_OPERATIONS)

    await _grow(factory, triangle, SMALL_OPERATIONS, LARGE_OPERATIONS)
    large_peak, large = await _peak_of_one_verification(factory, triangle.equivalent.id)
    _assert_clean_history(large, LARGE_OPERATIONS)

    assert small_peak > 0, "stand: the measurement saw no allocation at all"
    assert large_peak <= PEAK_RATIO_BOUND * small_peak, (
        f"the peak of one reconciliation grew with the history: {SMALL_OPERATIONS} envelopes "
        f"{small_peak / 2**20:.2f} MiB, {LARGE_OPERATIONS} envelopes {large_peak / 2**20:.2f} MiB "
        f"(ratio {large_peak / small_peak:.2f}, bound {PEAK_RATIO_BOUND})"
    )


# ==============================================================================================
# The verdict: a divergence at the end of a long history, and on a stream batch boundary
# ==============================================================================================


def _one_atom_on(entry_ids_sql: str) -> str:
    """Move the given journal rows by one atom WITHOUT breaking their own arithmetic (the CHECK still holds):
    an `I`/`U` row gains an atom after, a `D` row an atom before - so the edge sums and criterion (b) both see it."""

    return (
        "UPDATE debt_journal_entries SET"
        f" amount_after = CASE WHEN amount_after IS NULL THEN NULL ELSE amount_after + {ATOM} END,"
        f" amount_before = CASE WHEN amount_after IS NULL THEN amount_before + {ATOM} ELSE amount_before END,"
        f" delta = CASE WHEN amount_after IS NULL THEN delta - {ATOM} ELSE delta + {ATOM} END"
        f" WHERE id IN ({entry_ids_sql})"
    )


async def _operations_in_merge_order(factory, equivalent_id) -> list[uuid.UUID]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT operation_id FROM debt_journal_entries WHERE equivalent_id = :eq"
                        " ORDER BY operation_id"
                    ),
                    {"eq": equivalent_id},
                )
            ).scalars()
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_one_atom_divergence_at_the_end_of_a_long_history_is_failed_and_held(db_session) -> None:
    """10 000 envelopes: PASSED and not held; one atom on the LAST journal row of the stream - FAILED, held.

    The last row of the journal stream (ordered by operation id) belongs to the last envelope of the envelope stream:
    the divergence is where a reading that stopped early, or dropped its last batch, would never look.
    MUTATION: stop `_criterion_b`'s merge one envelope early, and skip the last batch of `_journal_sums` - each red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _history_of(factory, LARGE_OPERATIONS)
    equivalent_id = triangle.equivalent.id

    clean = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])
    assert (clean[PASSED], clean[FAILED], clean["error"]) == (1, 0, 0), clean
    _assert_clean_history(await _verify_in_a_snapshot(factory, equivalent_id), LARGE_OPERATIONS)

    last = (await _operations_in_merge_order(factory, equivalent_id))[-1]
    await corrupt(
        _database_url(factory),
        [_one_atom_on(f"SELECT id FROM debt_journal_entries WHERE operation_id = '{last}'")],
    )

    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])
    assert (counts[FAILED], counts["error"], counts[f"hold_{HOLD_SET}"]) == (1, 0, 1), counts
    async with factory() as session:
        held = (
            await session.execute(select(Equivalent.integrity_hold_result_id).where(Equivalent.id == equivalent_id))
        ).scalar_one()
    assert held is not None, "a confirmed FAILED did not hold the equivalent"

    outcome = await _verify_in_a_snapshot(factory, equivalent_id)
    kinds = Counter(finding["kind"] for finding in outcome.findings)
    assert kinds["edge_residual"] == 1 and kinds["b_delta_mismatch"] == 1, outcome.findings
    assert {f["operation_id"] for f in outcome.findings if f["kind"].startswith("b_")} == {str(last)}, outcome.findings
    assert outcome.entries_read == LARGE_OPERATIONS, outcome


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_divergences_on_a_stream_batch_boundary_are_failed_as_before(db_session) -> None:
    """1 000 envelopes, one journal row each: one atom on the envelopes at ranks 255 and 256 of the merge - the last
    row of the first batch of BOTH streams and the first row of the second (`_STREAM_BATCH_ROWS` = 256).

    Both are found by criterion (b), each on its own envelope, and the outcome equals the oracle's.
    MUTATION: drop the row that ends a batch in `_streamed` (yield `partition[:-1]`) - red.
    """
    from tests.conftest import TestingSessionLocal as factory

    assert reconciliation._STREAM_BATCH_ROWS == 256, "stand: the ranks below are the boundary of a 256-row batch"
    triangle = await _history_of(factory, SMALL_OPERATIONS)
    equivalent_id = triangle.equivalent.id
    order = await _operations_in_merge_order(factory, equivalent_id)
    assert len(order) == SMALL_OPERATIONS, len(order)
    boundary = order[255:257]
    await corrupt(
        _database_url(factory),
        [_one_atom_on(
            "SELECT id FROM debt_journal_entries WHERE operation_id IN ("
            + ", ".join(f"'{op}'" for op in boundary) + ")"
        )],
    )

    outcome = await _verify_in_a_snapshot(factory, equivalent_id)
    assert outcome.status == FAILED, outcome
    assert {f["operation_id"] for f in outcome.findings if f["kind"] == "b_delta_mismatch"} == {
        str(op) for op in boundary
    }, outcome.findings
    async with factory() as session:
        await open_verification_snapshot(session)
        expected = await _oracle(session, equivalent_id)
        await session.rollback()
    assert _shape(outcome) == _shape(expected)


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_payment_committed_while_the_streams_are_half_read_is_still_passed(db_session, monkeypatch) -> None:
    """The scheduled run is paused INSIDE criterion (b)'s merge - batches of one row, so most of both streams and every
    later read are still to come - and a real payment commits. The verdict is PASSED over the history before it.

    The streamed reads are statements of the run's one snapshot (`open_verification_snapshot`), as the materialised
    ones were. MUTATION: drop `open_verification_snapshot` from `run_scheduled_reconciliation` - the reads after the
    pause see the payment and the run says FAILED (a false FAILED, which step 5c would hold money on).
    """
    import asyncio

    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await _baseline(factory, triangle.equivalent.id)
    await _alternating_payments(factory, triangle, 9)
    monkeypatch.setattr(reconciliation, "_STREAM_BATCH_ROWS", 1)

    paused, resume = asyncio.Event(), asyncio.Event()
    original = reconciliation._streamed
    envelopes = {"yielded": 0}

    async def streamed(session, statement):
        # The barrier sits in the ENVELOPE stream, after its first row: the product's merge has examined nothing yet
        # past it, the journal stream has fetched one row, and every other read of the run is still to come.
        is_envelopes = "intent" in statement.selected_columns.keys()
        async with reconciliation.aclosing(original(session, statement)) as rows:
            async for row in rows:
                yield row
                if is_envelopes:
                    envelopes["yielded"] += 1
                    if envelopes["yielded"] == 1:
                        paused.set()
                        await asyncio.wait_for(resume.wait(), timeout=60)

    monkeypatch.setattr(reconciliation, "_streamed", streamed)
    task = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id]))
    try:
        await asyncio.wait_for(paused.wait(), timeout=60)
        tx_id = await _pay(factory, triangle, ["a", "b"], "1")
    finally:
        resume.set()
    counts = await asyncio.wait_for(task, timeout=120)

    async with factory() as session:
        state = (await session.execute(text("SELECT state FROM transactions WHERE tx_id = :t"), {"t": tx_id})).scalar_one()
        entries_now = (await session.execute(text(
            "SELECT count(*) FROM debt_journal_entries WHERE equivalent_id = :e"), {"e": triangle.equivalent.id})).scalar_one()
        stored = (await session.execute(text(
            "SELECT status, detail FROM debt_reconciliation_results WHERE equivalent_id = :e AND is_latest"),
            {"e": triangle.equivalent.id})).one()
    detail = stored.detail if isinstance(stored.detail, dict) else json.loads(stored.detail)
    # PREMISES: the payment committed during the pause, and the run read the history before it.
    assert state == "COMMITTED" and entries_now == 10, (state, entries_now)
    assert envelopes["yielded"] == 9 and detail["entries_read"] == 9, (envelopes, detail["entries_read"])
    assert (counts["error"], stored.status) == (0, PASSED), (counts, stored.status, detail["findings"])


# ==============================================================================================
# Equivalence: the streamed reading gives the materialised reading's outcome on every history
# ==============================================================================================


async def _oracle(session, equivalent_id: uuid.UUID):
    """`verify_journal_equals_change` as it READ on `main` `e902e332` - materialised - with the module's own rules.

    Kept only here, as the reference: `_journal_sums` with its grouping by operation, `_operations` `.all()`,
    `_criterion_b` sorting by `id.bytes`, `_correspondence` with its met set. Copied, not imported, because the
    module no longer has them.
    """

    r = reconciliation
    entries = r.debt_journal_entries.c
    rows = (
        await session.execute(
            select(
                entries.id, entries.operation_id, entries.ordinal, entries.debtor_id, entries.creditor_id,
                entries.effect, entries.amount_before, entries.amount_after, entries.delta,
            ).where(entries.equivalent_id == equivalent_id)
        )
    ).all()
    sums: dict = {}
    findings: list = []
    by_operation: dict = {}
    for row in rows:
        before = None if row.amount_before is None else r._atoms(row.amount_before, what="amount_before")
        after = None if row.amount_after is None else r._atoms(row.amount_after, what="amount_after")
        delta = r._atoms(row.delta, what="delta")
        if (after or 0) - (before or 0) != delta:
            findings.append({
                "kind": "entry_arithmetic", "entry_id": str(row.id), "debtor_id": str(row.debtor_id),
                "creditor_id": str(row.creditor_id), "amount_before": r._money_text(before),
                "amount_after": r._money_text(after), "delta": r._money_text(delta),
            })
        edge = (row.debtor_id, row.creditor_id)
        sums[edge] = sums.get(edge, 0) + delta
        by_operation.setdefault(row.operation_id, []).append(r._Entry(
            ordinal=int(row.ordinal), debtor_id=row.debtor_id, creditor_id=row.creditor_id, effect=str(row.effect),
            amount_before=before, delta=delta,
        ))
    entries_read = len(rows)

    ops = r.debt_operations.c
    transactions = r.Transaction.__table__
    operations = list((await session.execute(
        select(ops.id, ops.kind, ops.tx_id, ops.intent, ops.intent_digest, ops.intent_encoding_version,
               transactions.c.payload.label("tx_payload"))
        .select_from(r.debt_operations.outerjoin(transactions, and_(
            transactions.c.tx_id == ops.tx_id, ops.kind == "CLEARING", ops.intent_encoding_version == 2)))
        .where(r._here(equivalent_id))
    )).all())

    coverage: Counter = Counter()
    for op in sorted(operations, key=lambda row: row.id.bytes):
        kind = str(op.kind)
        if kind in r._NOT_EXAMINED_KINDS:
            coverage[(r.NOT_EXAMINED, kind)] += 1
            continue
        version = int(op.intent_encoding_version)
        level = r._READABLE_ENVELOPES.get((kind, version))
        if level is None:
            findings.append(r._finding("b_version_unsupported", op, intent_encoding_version=version))
            continue
        coverage[(level, kind)] += 1
        intent = op.intent
        if not isinstance(intent, dict):
            findings.append(r._finding("b_intent_malformed", op, reason="intent_is_not_an_object"))
            continue
        try:
            findings.extend(r._RULES[(kind, version)](op, intent, by_operation.get(op.id, []), equivalent_id))
        except r._Malformed as malformed:
            findings.append(r._finding("b_intent_malformed", op, reason=malformed.reason))
    coverage_tuple = tuple(sorted((level, kind, count) for (level, kind), count in coverage.items()))

    def _refute(rule, tx_id, op_id, tx_type=None, tx_state=None):
        findings.append({"kind": "tx_operation_correspondence", "rule": rule, "tx_id": tx_id, "tx_type": tx_type,
                         "tx_state": tx_state, "operation_id": None if op_id is None else str(op_id)})

    tx, here = transactions.c, r._here(equivalent_id)
    names_it = tx.payload["equivalent"].as_string()
    tx_rows = (await session.execute(
        select(tx.tx_id, tx.type, tx.state, ops.id.label("op_id"), ops.kind.label("op_kind"), here.label("op_here"))
        .select_from(transactions.outerjoin(r.debt_operations, ops.tx_id == tx.tx_id))
        .where(or_(names_it == str(equivalent_id),
                     names_it == select(Equivalent.code).where(Equivalent.id == equivalent_id).scalar_subquery(),
                     here))
    )).all()
    pairs, met = 0, set()
    for row in tx_rows:
        met.add(row.op_id)
        committed = row.type in r._TX_KINDS and row.state == "COMMITTED"
        if committed and row.op_kind == row.type and row.op_here:
            pairs += 1
        elif committed:
            _refute("committed_transaction_without_its_operation", row.tx_id, row.op_id, row.type, row.state)
        elif row.op_id is not None:
            _refute("operation_without_its_committed_transaction", row.tx_id, row.op_id, row.type, row.state)
        elif not (row.type == "PAYMENT" and row.state == "ABORTED"):
            _refute("transaction_in_a_shape_no_writer_leaves", row.tx_id, None, row.type, row.state)
    for op in operations:
        if op.kind in r._TX_KINDS and op.id not in met:
            _refute("operation_without_its_committed_transaction", op.tx_id, op.id)
    findings.extend(await r._structure(session, equivalent_id))
    seen = {"transactions_read": len(tx_rows), "transaction_pairs": pairs}

    if not await r._has_baseline(session, equivalent_id):
        return r.ReconciliationOutcome(
            equivalent_id=equivalent_id, findings=tuple(findings), missing_evidence=("baseline",), edges_checked=0,
            entries_read=entries_read, criterion_b_coverage=coverage_tuple, **seen,
        )
    debts = await r._current_debts(session, equivalent_id)
    offset_columns = r.debt_reconciliation_baseline_offsets.c
    offsets = {
        (debtor_id, creditor_id): r._atoms(amount, what="offset_amount")
        for debtor_id, creditor_id, amount in (await session.execute(
            select(offset_columns.debtor_id, offset_columns.creditor_id, offset_columns.offset_amount)
            .where(offset_columns.equivalent_id == equivalent_id)
        )).all()
    }
    edges = set(debts) | set(sums) | set(offsets)
    for edge in sorted(edges, key=r._edge_key):
        debt, journal, offset = debts.get(edge, 0), sums.get(edge, 0), offsets.get(edge, 0)
        if debt - journal != offset:
            findings.append({
                "kind": "edge_residual", "debtor_id": str(edge[0]), "creditor_id": str(edge[1]),
                "current_debt": r._money_text(debt), "journal_delta_sum": r._money_text(journal),
                "baseline_offset": r._money_text(offset), "unexplained": r._money_text(debt - journal - offset),
            })
    return r.ReconciliationOutcome(
        equivalent_id=equivalent_id, findings=tuple(findings), missing_evidence=(), edges_checked=len(edges),
        entries_read=entries_read, criterion_b_coverage=coverage_tuple, **seen,
    )


def _canonical(finding: dict) -> str:
    return json.dumps(finding, sort_keys=True, default=str)


def _shape(outcome) -> dict:
    """Everything an outcome says: the verdict, the counts, the fingerprint, the stored detail, every finding.

    Findings are compared as a multiset and, where the reading defines their order (criterion (b) by envelope id,
    the edge residuals by edge), in order too; the entry-arithmetic and correspondence rows came, and come, in the
    database's order of an unordered read, and the stored detail keeps the first `MAX_STORED_FINDINGS` of them.
    """

    detail = outcome.detail()
    detail.pop("findings")
    return {
        "status": outcome.status,
        "missing_evidence": outcome.missing_evidence,
        "edges_checked": outcome.edges_checked,
        "entries_read": outcome.entries_read,
        "coverage": outcome.criterion_b_coverage,
        "transactions": (outcome.transactions_read, outcome.transaction_pairs),
        "fingerprint": outcome.fingerprint(),
        "detail": detail,
        "findings": sorted(_canonical(finding) for finding in outcome.findings),
        "ordered_b": [_canonical(f) for f in outcome.findings if str(f["kind"]).startswith("b_")],
        "ordered_residuals": [_canonical(f) for f in outcome.findings if f["kind"] == "edge_residual"],
    }


async def _compare_with_the_oracle(factory, monkeypatch, equivalent_ids, step: str) -> list[dict]:
    shapes = []
    for equivalent_id in equivalent_ids:
        async with factory() as session:
            await open_verification_snapshot(session)
            expected = _shape(await _oracle(session, equivalent_id))
            await session.rollback()
        for batch in (1, 2, 3, 256):
            monkeypatch.setattr(reconciliation, "_STREAM_BATCH_ROWS", batch)
            actual = _shape(await _verify_in_a_snapshot(factory, equivalent_id))
            assert actual == expected, f"{step}: equivalent {equivalent_id}, batch {batch} rows"
        shapes.append(expected)
    return shapes


async def _second_equivalent_over(factory, triangle) -> _Triangle:
    """Another equivalent over the SAME three participants, with lines both ways: what must never be mixed."""

    tag = uuid.uuid4().hex[:8].upper()
    async with factory() as session:
        equivalent = Equivalent(code=f"M7{tag}", precision=2, is_active=True, metadata_={})
        session.add(equivalent)
        await session.flush()
        for creditor, debtor in itertools.permutations("abc", 2):
            session.add(TrustLine(
                from_participant_id=getattr(triangle, creditor).id, to_participant_id=getattr(triangle, debtor).id,
                equivalent_id=equivalent.id, limit=Decimal("1000"), status="active",
            ))
        await session.commit()
    return _Triangle(equivalent, {"a": triangle.a, "b": triangle.b, "c": triangle.c})


async def _debt_ids(factory, triangle, edges: list[tuple[str, str]]) -> list[SimpleNamespace]:
    from app.db.models.debt import Debt

    found = []
    async with factory() as session:
        for debtor, creditor in edges:
            debt = (await session.execute(select(Debt.id, Debt.equivalent_id).where(
                Debt.debtor_id == getattr(triangle, debtor).id, Debt.creditor_id == getattr(triangle, creditor).id,
                Debt.equivalent_id == triangle.equivalent.id,
            ))).one()
            found.append(SimpleNamespace(id=debt.id, equivalent_id=debt.equivalent_id))
    return found


async def _clear(factory, debts, *, amount: str, ordinal: int):
    from app.core.clearing.service import ClearingService

    occurrence = occurrence_of(
        [debt.id for debt in debts], equivalent_id=debts[0].equivalent_id, amount=amount,
        plan_id=TEST_PLAN_ID, ordinal=ordinal,
    )
    async with factory() as session:
        cleared = await ClearingService(session).execute_occurrence(occurrence)
    assert cleared == Decimal(amount), cleared
    return occurrence


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_the_streamed_verifier_gives_the_materialised_verifiers_outcome(db_session, monkeypatch) -> None:
    """Three equivalents, clean and then corrupted step by step: at every step and every batch size, the outcome of
    `verify_journal_equals_change` equals the oracle's - verdict, counts, coverage, fingerprint, detail, findings.

    The stand asserts it covered what it claims: an empty history, UNVERIFIABLE, PASSED and FAILED; full
    recomputation of PAYMENT and both CLEARING versions, structural only, not examined; and every finding kind the
    corruptions name.
    """
    from tests.conftest import TestingSessionLocal as factory

    url = _database_url(factory)
    one = await _seed_triangle(factory, trustlines=[(x, y, "1000") for x, y in itertools.permutations("abc", 2)])
    empty = await _seed_triangle(factory, trustlines=[])
    same_people = await _second_equivalent_over(factory, one)
    cycle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100")])
    ids = [one.equivalent.id, same_people.equivalent.id, cycle.equivalent.id, empty.equivalent.id]
    covered: dict[str, set] = {"status": set(), "kinds": set(), "levels": set()}

    async def step(name: str) -> None:
        for shape in await _compare_with_the_oracle(factory, monkeypatch, ids, name):
            covered["status"].add(shape["status"])
            covered["kinds"] |= {json.loads(f)["kind"] for f in shape["findings"]}
            covered["levels"] |= {(level, kind) for level, kind, _count in shape["coverage"]}

    await step("before any baseline")  # UNVERIFIABLE everywhere; the empty history among them

    # ONE: a fixture write and two payments before the baseline, more after it - the baseline in the middle.
    await _fixture_debts(factory, one, [("a", "c", "5")])
    await _pay(factory, one, ["a", "b"], "3")
    multi_hop = await _pay(factory, one, ["a", "b", "c"], "2")
    for equivalent_id in ids:
        await _baseline(factory, equivalent_id)
    await _pay(factory, one, ["c", "b", "a"], "4")
    await _pay(factory, one, ["b", "c"], "1.5")
    reversed_multi_hop = await _pay(factory, one, ["c", "b", "a"], "0.75")
    await _pay(factory, one, ["c", "a"], "0.01")
    # SAME PEOPLE, another equivalent: payments over the same edges, which must not meet ONE's.
    await _pay(factory, same_people, ["a", "b", "c"], "7")
    await _pay(factory, same_people, ["c", "a"], "2")
    # CYCLE: a v2 occurrence, then a second one turned into the record a v1 clearing left.
    await _pay(factory, cycle, ["a", "b"], "10")
    await _pay(factory, cycle, ["b", "c"], "20")
    await _pay(factory, cycle, ["c", "a"], "30")
    await _clear(factory, await _debt_ids(factory, cycle, [("a", "b"), ("b", "c"), ("c", "a")]), amount="10", ordinal=0)
    await _pay(factory, cycle, ["a", "b"], "5")
    v1 = await _clear(factory, await _debt_ids(factory, cycle, [("a", "b"), ("b", "c"), ("c", "a")]), amount="5", ordinal=1)
    await historical_v1_clearing(factory, v1)
    await step("clean, after the baseline")

    def op_of(tx_id: str) -> str:
        return f"(SELECT id FROM debt_operations WHERE tx_id = '{tx_id}')"

    one_eq, cycle_eq = one.equivalent.id, cycle.equivalent.id
    corruptions = [
        ("one atom on a debt", [
            f"UPDATE debts SET amount = amount + {ATOM} WHERE equivalent_id = '{one_eq}'"
            f" AND debtor_id = '{one.a.id}' AND creditor_id = '{one.b.id}'"]),
        ("a journal row duplicated", [
            "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, debtor_id, creditor_id,"
            " effect, amount_before, amount_after, delta) SELECT gen_random_uuid(), operation_id, ordinal + 1000,"
            " equivalent_id, debtor_id, creditor_id, effect, amount_before, amount_after, delta"
            f" FROM debt_journal_entries WHERE operation_id = {op_of(multi_hop)} ORDER BY ordinal LIMIT 1"]),
        ("a journal row deleted", [
            f"DELETE FROM debt_journal_entries WHERE id = (SELECT id FROM debt_journal_entries"
            f" WHERE operation_id = {op_of(reversed_multi_hop)} ORDER BY ordinal DESC LIMIT 1)"]),
        ("an intent rewritten", [
            f"UPDATE debt_operations SET intent = jsonb_set(intent::jsonb, '{{locks,0,flows,0,amount}}',"
            f" '\"9.00000000\"')::json WHERE id = (SELECT operation_id FROM debt_journal_entries"
            f" WHERE equivalent_id = '{one_eq}' AND effect = 'U' ORDER BY operation_id LIMIT 1)"]),
        ("an intent that is not an object", [
            f"UPDATE debt_operations SET intent = '[]'::json WHERE id = {op_of(multi_hop)}"]),
        ("a payment read as version 1", [
            f"UPDATE debt_operations SET intent_encoding_version = 1 WHERE id = {op_of(reversed_multi_hop)}"]),
        ("a transaction row deleted", [
            "DELETE FROM transactions WHERE tx_id = (SELECT o.tx_id FROM debt_operations o"
            " JOIN debt_operation_equivalents e ON e.operation_id = o.id"
            f" WHERE e.equivalent_id = '{one_eq}' AND o.kind = 'PAYMENT' AND o.tx_id NOT IN"
            f" ('{multi_hop}', '{reversed_multi_hop}') ORDER BY o.id DESC LIMIT 1)"]),
        ("a committed transaction without an operation", [
            "INSERT INTO transactions (id, tx_id, type, initiator_id, payload, signatures, state)"
            f" VALUES (gen_random_uuid(), 'p047-stray-{uuid.uuid4().hex[:8]}', 'PAYMENT', '{one.a.id}',"
            f" json_build_object('equivalent', '{one.equivalent.code}'), '[]'::json, 'COMMITTED')"]),
        ("an operation deleted, its entries left", [
            f"DELETE FROM debt_operation_equivalents WHERE operation_id = (SELECT id FROM debt_operations"
            f" WHERE kind = 'PAYMENT' AND tx_id IN (SELECT tx_id FROM transactions"
            f" WHERE payload->>'equivalent' = '{same_people.equivalent.code}') ORDER BY id LIMIT 1)",
            f"DELETE FROM debt_operations WHERE id = (SELECT o.id FROM debt_operations o WHERE o.kind = 'PAYMENT'"
            f" AND o.tx_id IN (SELECT tx_id FROM transactions"
            f" WHERE payload->>'equivalent' = '{same_people.equivalent.code}') ORDER BY o.id LIMIT 1)"]),
        ("a counter-debt", [
            "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version)"
            " SELECT gen_random_uuid(), d.creditor_id, d.debtor_id, d.equivalent_id, 1, 0 FROM debts d"
            f" WHERE d.equivalent_id = '{one_eq}' AND NOT EXISTS (SELECT 1 FROM debts r"
            " WHERE r.debtor_id = d.creditor_id AND r.creditor_id = d.debtor_id AND r.equivalent_id = d.equivalent_id)"
            " ORDER BY d.id LIMIT 1"]),
        ("a debt without a live line", [
            "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) VALUES"
            f" (gen_random_uuid(), '{cycle.b.id}', '{cycle.a.id}', '{cycle_eq}', 1, 0)"]),
        ("an envelope relabelled INJECT, which no rule reads", [
            "UPDATE debt_operations SET kind = 'INJECT', tx_id = NULL WHERE id = (SELECT o.id FROM debt_operations o"
            " JOIN debt_operation_equivalents e ON e.operation_id = o.id"
            f" WHERE e.equivalent_id = '{same_people.equivalent.id}' AND o.kind = 'PAYMENT' ORDER BY o.id DESC LIMIT 1)"]),
        ("a clearing payload that differs from its descriptor", [
            "UPDATE transactions SET payload = jsonb_set(payload::jsonb, '{amount}', '\"9.00\"')::json"
            f" WHERE type = 'CLEARING' AND tx_id IN (SELECT tx_id FROM debt_operations o"
            f" JOIN debt_operation_equivalents e ON e.operation_id = o.id"
            f" WHERE e.equivalent_id = '{cycle_eq}' AND o.intent_encoding_version = 2)"]),
    ]
    for name, statements in corruptions:
        await corrupt(url, statements)
        await step(name)

    # A row contradicting its own arithmetic needs the CHECK gone, on this clone only (as in 015 step 5a).
    async with factory() as session:
        await session.execute(text(
            "ALTER TABLE debt_journal_entries DROP CONSTRAINT chk_debt_journal_entries_delta_arithmetic"))
        await session.commit()
    await corrupt(url, [
        f"UPDATE debt_journal_entries SET delta = delta + {ATOM} WHERE id = (SELECT id FROM debt_journal_entries"
        f" WHERE equivalent_id = '{cycle_eq}' ORDER BY operation_id, ordinal LIMIT 1)"])
    await step("a row contradicting its own arithmetic")

    assert covered["status"] == {PASSED, FAILED, UNVERIFIABLE}, covered
    assert {
        ("full_recomputation", "PAYMENT"), ("full_recomputation", "CLEARING"),
        ("structural_only", "PAYMENT"), ("not_examined", "TEST_FIXTURE"),
    } <= covered["levels"], covered
    assert {
        "edge_residual", "entry_arithmetic", "b_intent_malformed", "b_delta_mismatch", "b_version_unsupported",
        "tx_operation_correspondence", "debt_counter_debt", "debt_without_a_live_line",
    } <= covered["kinds"], covered
