"""Programme 015, step 5a: the baseline, criterion (a), the result model and the scheduled host.

WHAT IS UNDER TEST. `app/core/ledger/reconciliation.py` - criterion (a), DETECTION OF CHANGE MADE AROUND
THE APPLICATION: for every edge of an equivalent, `current debt - sum(journal delta) == baseline offset`,
plus the per-row `amount_after - amount_before == delta`, which PostgreSQL also holds as a constraint
(`chk_debt_journal_entries_delta_arithmetic`, migration 024) - see the arithmetic test for why the
verifier's own check is still measured.

THE REPRODUCTION THAT JUSTIFIES IT, asserted in the first test and measured before any of this existed
(2026-09-14, this module's first version on HEAD `053c58b`): a one-atom `UPDATE debts` issued through the
driver left the integrity checkpoint `healthy`, `passed: True`, `alerts: []`, with `trust_limits` and
`debt_symmetry` passing. Nothing in the application saw it.

WHAT IT DOES NOT SEE, and one test says so on purpose: a writer that journals a WRONG change faithfully.
`C6` is that writer; criterion (a) is PASSED on it, and refuting it is criterion (b), step 5b.

TIER. PostgreSQL. Money inside `|v| < 2^26`. Every verdict is read on a new session. The
changes "around the application" go through THE named corruption helper (`tests/ledger_corruption.py`,
spec 018 `FORK-4`): its own connection and transaction with `SET LOCAL session_replication_role =
replica`, i.e. a write with the journal's triggers off - which is exactly what an operator's SQL or a
partial restore is. Since 018 stage B1 the database REFUSES the same statement from the application's
side (`GE001`, `tests/integration/test_p018_a_write_without_context_is_refused_by_the_database.py`); that
refusal is the first barrier, and these tests keep the INDEPENDENT one - what criterion (a) and the hold
make of such a state once it exists (spec 018, Verification plan §2 and "Запрещено").

MUTATIONS. Each test names the mutation that must turn it red; the report of step 5a records the runs.

ONE TEST RUNS ON A DISPOSABLE CLONE WITHOUT THE ARITHMETIC CHECK (programme 017 stage 3, slice S3;
on a SQLite file of its own before). On the tier database `chk_debt_journal_entries_delta_arithmetic`
refuses the forged row before the verifier can see it, so the verifier's per-row check - an existing
rule of the money path - could be deleted with the tier staying green. The clone is where that rule is
still measured (spec 017, Changelog 2026-09-24: a rule a constraint makes unreachable stays measured).
The refusal to give a verdict without the SQLite transaction control left with SQLite: on PostgreSQL
there is no such control to be missing.

EVERY TEST RUNS ON A DISPOSABLE CLONE (018 B0b). The tests commit through sessions of their own, so each
runs on a clone of the migrated template (`tests/tier_on_a_clone.py`), and the clone's drop is the only
disposal of what it wrote - until B0b each test deleted its triangle by id, journal included. The test
above that needs the CHECK gone drops it from that same clone.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.ledger.book import Book, BookError, Refusal, operation_for
from app.core.ledger.reconciliation import (
    CRITERION_A,
    FAILED,
    PASSED,
    UNVERIFIABLE,
    BaselineAlreadyTaken,
    take_baseline,
    verify_journal_equals_change,
)
from app.db.journal_tables import debt_journal_entries
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.reconciliation_tables import (
    debt_reconciliation_baseline_offsets,
    debt_reconciliation_baselines,
    debt_reconciliation_results,
)
from tests.debt_setup import debt_fixture_setup
from tests.ledger_corruption import corrupt
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: F401 - autouse fixture
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import (
    _audit,
    _collapse_the_route,
    _edges,
    _prepare_payment,
    _seed_triangle,
    _tx_state,
)

#: The checkpoint keys that existed before step 5a. The reconciliation result must add none.
CHECKPOINT_CHECKS = {"zero_sum", "trust_limits", "debt_symmetry"}


# ==============================================================================================
# Stand helpers
# ==============================================================================================


#: The PostgreSQL CHECK that holds `delta = amount_after - amount_before` in the table itself.
_ARITHMETIC_CHECK = "chk_debt_journal_entries_delta_arithmetic"


@pytest_asyncio.fixture
async def clone_without_the_arithmetic_check(committed_database):
    """A sessionmaker over a DISPOSABLE clone (mode B) from which the delta-arithmetic CHECK is dropped.

    WHY: on PostgreSQL that CHECK refuses a journal row whose delta contradicts its own ends before
    the verifier's per-row comparison can see it - which is correct, and is asserted where it belongs
    (`tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`). But the per-row comparison is
    a rule of the verifier, and on the tier database it can never be reached, so its mutation would
    stay green. On a clone dropped when the test ends, removing the CHECK isolates the stage that owns
    the rule; nothing outside this test sees the altered schema. `DROP CONSTRAINT` without
    `IF EXISTS` is the non-vacuity: it fails unless the CHECK was there.
    """

    async with committed_database.engine.begin() as connection:
        await connection.exec_driver_sql(
            f"ALTER TABLE {debt_journal_entries.name} DROP CONSTRAINT {_ARITHMETIC_CHECK}"
        )
    yield committed_database.sessionmaker


def _literal(dialect: str, value: uuid.UUID) -> str:
    """A UUID as PostgreSQL stores it: the canonical form. (A 32-hex SQLite arm left with SQLite.)"""

    return str(value)


def _database_url(factory) -> str:
    """The URL of the database a sessionmaker is bound to, password included (for the helper)."""

    return factory.kw["bind"].url.render_as_string(hide_password=False)


async def _around_the_application(factory, build_statement) -> None:
    """Commit one statement WITH THE JOURNAL'S TRIGGERS OFF, through the named corruption helper.

    018 stage B1: the application's own connection can no longer write `debts` or the journal outside
    an operation (`GE001`, the guard triggers), so a change "around the application" is modelled the
    one way it can still arise - an operator or a restore with the triggers switched off
    (`tests/ledger_corruption.py`). `replica` also switches off foreign keys: a caller that needs one
    to bite uses `_driver_statement` instead.
    """

    await corrupt(_database_url(factory), [build_statement("postgresql")])


async def _driver_statement(factory, build_statement) -> None:
    """One statement through the driver with every trigger and foreign key ON, committed.

    For tables the journal's triggers do not guard (the reconciliation results), where the test is
    about a constraint that must bite.
    """

    async with factory() as session:
        connection = await session.connection()
        await connection.exec_driver_sql(build_statement(connection.dialect.name))
        await session.commit()


async def _fixture_debts(factory, triangle, edges: list[tuple[str, str, str]]) -> list[Debt]:
    debts = [
        Debt(
            id=uuid.uuid4(),
            debtor_id=getattr(triangle, debtor).id,
            creditor_id=getattr(triangle, creditor).id,
            equivalent_id=triangle.equivalent.id,
            amount=Decimal(amount),
            version=0,
        )
        for debtor, creditor, amount in edges
    ]
    async with factory() as session:
        async with debt_fixture_setup(session, label="step5a"):
            session.add_all(debts)
        await session.commit()
    return debts


async def _baseline(factory, equivalent_id):
    async with factory() as session:
        taken = await take_baseline(session, equivalent_id)
        await session.commit()
    return taken


async def _verify(factory, equivalent_id):
    async with factory() as session:
        return await verify_journal_equals_change(session, equivalent_id)


async def _pay(factory, triangle, path: list[str], amount: str) -> str:
    """One whole payment over the explicit route `path`, committed. Since 019 stage 4 (`T1906`) a payment
    is one transaction through `PaymentService`, so this is `_prepare_payment` itself (whose name is
    historical); until then it was a durable `PaymentEngine.prepare` followed by `PaymentEngine.commit`."""

    return await _prepare_payment(factory, triangle, path, Decimal(amount))


async def _entries(factory, equivalent_id) -> list[SimpleNamespace]:
    columns = debt_journal_entries.c
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    columns.id,
                    columns.debtor_id,
                    columns.creditor_id,
                    columns.effect,
                    columns.amount_before,
                    columns.amount_after,
                    columns.delta,
                ).where(columns.equivalent_id == equivalent_id)
            )
        ).all()
    return [SimpleNamespace(**row._mapping) for row in rows]


async def _offsets(factory, equivalent_id) -> list[tuple]:
    columns = debt_reconciliation_baseline_offsets.c
    async with factory() as session:
        return [
            tuple(row)
            for row in (
                await session.execute(
                    select(columns.debtor_id, columns.creditor_id, columns.offset_amount).where(
                        columns.equivalent_id == equivalent_id
                    )
                )
            ).all()
        ]


async def _results(factory, equivalent_id) -> list[tuple[str, dict]]:
    columns = debt_reconciliation_results.c
    async with factory() as session:
        rows = (
            await session.execute(
                select(columns.status, columns.detail).where(columns.equivalent_id == equivalent_id)
            )
        ).all()
    return [(status, json.loads(detail) if isinstance(detail, str) else detail) for status, detail in rows]


async def _scheduled_run(monkeypatch, factory) -> None:
    """The real scheduled host, `app.main._run_integrity_checkpoints_once`, on the test database."""

    import app.db.session as app_db_session
    import app.main as main_module

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    completed = await main_module._run_integrity_checkpoints_once(app, reason="periodic")
    assert completed is True, f"stand: the scheduled integrity run did not complete: {app.state}"


async def interleave_a_payment_between_the_verifiers_reads(factory, monkeypatch) -> dict:
    """Pause the scheduled verifier after its journal read, commit a real payment, resume.

    019 stage 4 (`T1906`): the WHOLE payment runs during the pause. Before, it was prepared before the
    pause and only committed during it; the prepare-before-pause was never load-bearing - what the
    premises below pin is that the payment's money COMMITTED while the verifier sat between its reads.

    The pause is an `asyncio.Event` barrier in a patched `_current_debts`, never a sleep. Returns the
    premises and the verdict for the calling test to assert: what the journal read saw, what a THIRD
    session saw while the verifier was paused, what the run counted and what it stored.
    """

    import asyncio

    from app.core.ledger import reconciliation

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)

    paused, resume = asyncio.Event(), asyncio.Event()
    seen: dict = {"triangle": triangle}
    original_sums = reconciliation._journal_sums
    original_debts = reconciliation._current_debts

    calls = {"sums": 0, "debts": 0}

    # ONLY THE FIRST CALL of each is the scheduled verifier's. Anything else reaching these helpers - a
    # mutation that calls the verifier from inside the payment, say - passes straight through instead of
    # meeting a barrier that is released only after that payment commits. Measured: without this the
    # stand deadlocked under such a mutation rather than going red.
    async def _sums(session, equivalent_id):
        calls["sums"] += 1
        result = await original_sums(session, equivalent_id)
        if calls["sums"] == 1:
            seen["journal_sum_ab"] = result[0].get((triangle.a.id, triangle.b.id))
        return result

    async def _debts(session, equivalent_id):
        calls["debts"] += 1
        if calls["debts"] == 1:
            paused.set()
            await asyncio.wait_for(resume.wait(), timeout=60)
        return await original_debts(session, equivalent_id)

    monkeypatch.setattr(reconciliation, "_journal_sums", _sums)
    monkeypatch.setattr(reconciliation, "_current_debts", _debts)

    task = asyncio.create_task(
        reconciliation.run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    )
    try:
        await asyncio.wait_for(paused.wait(), timeout=60)
        tx_id = await _prepare_payment(factory, triangle, ["a", "b"], Decimal("5"))
        seen["third_session_edges"] = await _edges(factory, triangle)
        seen["tx_state"] = await _tx_state(factory, tx_id)
    finally:
        resume.set()
    seen["counts"] = await asyncio.wait_for(task, timeout=120)
    seen["results"] = [status for status, _ in await _results(factory, triangle.equivalent.id)]
    return seen


def _assert_interleave(seen: dict) -> None:
    # PREMISES: the payment really committed while the verifier was paused, and the verifier really
    # read the journal before it (its sum for A -> B does not contain the payment's +5).
    assert seen["tx_state"] == "COMMITTED", seen
    assert seen["third_session_edges"] == {("a", "b"): Decimal("15.00000000")}, seen
    assert seen["journal_sum_ab"] == 10 * 10**8, seen
    assert seen["counts"]["error"] == 0, (
        f"the interleaved run ended in an ERROR, not a verdict: {seen['counts']}"
    )
    assert seen["results"] == [PASSED], (
        f"a payment committed between the verifier's reads produced {seen['results']} "
        f"({seen['counts']}): the four reads did not see one snapshot"
    )


def _one_edge(outcome, kind: str) -> dict:
    """The single CRITERION (a) finding. Since step 5b the same outcome also carries criterion (b)'s
    findings (kinds `b_*`): a journal entry of a payment that disappeared or was duplicated also makes its
    recorded change disagree with its recorded intent, and that is asserted where it applies."""

    criterion_a = [finding for finding in outcome.findings if not str(finding["kind"]).startswith("b_")]
    assert [finding["kind"] for finding in criterion_a] == [kind], outcome.findings
    return criterion_a[0]


# ==============================================================================================
# Criterion (a): the acceptance controls
# ==============================================================================================


@pytest.mark.asyncio
async def test_step5a_a_one_atom_change_around_the_application_is_failed(db_session) -> None:
    """A one-atom `UPDATE debts` through the driver: the checkpoint sees nothing, criterion (a) FAILS.

    THE REPRODUCTION is the first half and needs no reconciliation at all: after the change, the
    checkpoint is still `passed` - asserted, so this test would notice if something else started seeing
    it and the justification changed.

    MUTATION: read `debts.amount` at seven decimal places in `_current_debts` (quantize the value to
    `1E-7` before `_atoms`). The atom disappears into the rounding and the result goes PASSED. The other
    corruption tests move whole units and stay green.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    taken = await _baseline(factory, triangle.equivalent.id)
    assert (taken.offsets_recorded, taken.edges_seen) == (0, 1), taken

    control = await _verify(factory, triangle.equivalent.id)
    assert control.status == PASSED, f"stand: the untouched state is not PASSED: {control}"

    await _around_the_application(
        factory,
        lambda d: f"UPDATE debts SET amount = '10.00000001' WHERE id = '{_literal(d, debt.id)}'",
    )
    assert await _edges(factory, triangle) == {("a", "b"): Decimal("10.00000001")}

    # THE REPRODUCTION: nothing that existed before step 5a sees the change.
    async with factory() as session:
        checkpoint = await compute_integrity_checkpoint_for_equivalent(
            session, equivalent_id=triangle.equivalent.id
        )
    assert checkpoint.invariants_status["passed"] is True, checkpoint.invariants_status
    assert checkpoint.invariants_status["alerts"] == [], checkpoint.invariants_status

    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == FAILED, (
        f"one atom was added to a debt around the application and criterion (a) said "
        f"{outcome.status}: {outcome}"
    )
    finding = _one_edge(outcome, "edge_residual")
    assert finding["unexplained"] == "0.00000001", finding
    assert outcome.edges_checked == 1, outcome


@pytest.mark.asyncio
async def test_step5a_an_application_payment_that_moves_debts_and_journal_is_passed(db_session) -> None:
    """NEGATIVE CONTROL. A real payment after the baseline changes three edges and stays PASSED.

    NON-VACUITY: the payment deletes one edge (a journal-only edge afterwards), creates two, and the
    verifier must have checked all three - `edges_checked == 3` - so PASSED is not a pass over nothing.

    MUTATION: invert the edge predicate (`if debt - journal == offset`). Every consistent edge becomes a
    finding and this test goes red, as do the tests that expect PASSED as their control step.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100")])
    await _fixture_debts(factory, triangle, [("b", "a", "3")])
    await _baseline(factory, triangle.equivalent.id)
    entries_before = len(await _entries(factory, triangle.equivalent.id))

    await _pay(factory, triangle, ["a", "b", "c"], "5")

    assert await _edges(factory, triangle) == {
        ("a", "b"): Decimal("2.00000000"),
        ("b", "c"): Decimal("5.00000000"),
    }, "stand: the payment did not produce the expected state"
    assert len(await _entries(factory, triangle.equivalent.id)) > entries_before

    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == PASSED, f"an honest payment was not PASSED: {outcome}"
    assert outcome.edges_checked == 3, outcome
    assert await _offsets(factory, triangle.equivalent.id) == [], (
        "a fully journalled equivalent recorded non-zero offsets; an absent row must mean zero"
    )


@pytest.mark.asyncio
async def test_step5a_without_a_baseline_the_result_is_unverifiable_never_passed(db_session) -> None:
    """No baseline: UNVERIFIABLE, even over a state carrying a change nothing explains.

    MUTATION: in `verify_journal_equals_change`, treat a missing baseline as an empty set of offsets
    instead of returning early. The atom below then becomes a FAILED edge and this test goes red; with
    no atom, the same mutation would have produced PASSED - the false green the rule exists to prevent.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _around_the_application(
        factory,
        lambda d: f"UPDATE debts SET amount = '10.00000001' WHERE id = '{_literal(d, debt.id)}'",
    )

    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == UNVERIFIABLE, outcome
    assert outcome.missing_evidence == ("baseline",), outcome
    assert outcome.findings == (), outcome
    assert outcome.entries_read == 1, "stand: the journal evidence existed and was read"


@pytest.mark.asyncio
async def test_step5a_a_journal_row_contradicting_its_own_arithmetic_is_failed_and_dominates(
    clone_without_the_arithmetic_check,
) -> None:
    """A row saying `-> 11` with `delta 10`, and no baseline: FAILED, with the baseline still missing.

    Two properties in one stand, because only the row arithmetic is conclusive without a baseline:
    the verifier's per-row check, and `FAILED` dominating `UNVERIFIABLE`.

    MUTATIONS: (1) remove the per-row comparison in `_journal_sums` - no finding, UNVERIFIABLE, red;
    (2) test `missing_evidence` before `findings` in `ReconciliationOutcome.status` - UNVERIFIABLE, red.

    ON A CLONE WITHOUT THE CHECK: with the schema intact the forged row is refused by
    `chk_debt_journal_entries_delta_arithmetic` (migration 024) and the per-row check "can only fire
    if the constraint is gone" (`app/core/ledger/reconciliation.py`, module docstring). The clone is
    where it is gone (see `clone_without_the_arithmetic_check`); until 017 stage 3 this ran on SQLite.

    BOTH EXCEPTIONS AT ONCE (spec 018 §2, manifest `T1808` 7.C item 3): since stage B1 the guard trigger
    on `debt_journal_entries` refuses the forging UPDATE on the clone too, so the forgery goes through
    the corruption helper (`replica`) on the CHECK-less clone. That the ordinary schema refuses such a
    row by its CHECK is `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`.
    """
    factory = clone_without_the_arithmetic_check

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    (entry,) = await _entries(factory, triangle.equivalent.id)
    await _around_the_application(
        factory,
        lambda d: (
            f"UPDATE debt_journal_entries SET amount_after = '11' "
            f"WHERE id = '{_literal(d, entry.id)}'"
        ),
    )

    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == FAILED, outcome
    assert outcome.missing_evidence == ("baseline",), outcome
    finding = _one_edge(outcome, "entry_arithmetic")
    assert (finding["amount_after"], finding["delta"]) == ("11.00000000", "10.00000000"), finding


@pytest.mark.asyncio
async def test_step5a_a_missing_delta_on_an_edge_the_application_removed_is_failed(db_session) -> None:
    """T1508 shape: the `D` entry of a deleted edge disappears. The edge has no debt left - only history.

    WHY THE EDGE IS DELETED: it is then present in the journal ONLY, which is what the union over debts,
    journal AND offsets is for.

    MUTATION: build the edge set from debts and offsets only (drop `set(sums)` from the union). The
    journal-only edge is never compared and this test goes red; the other tests break a live edge.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("a", "b", "100")])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    await _pay(factory, triangle, ["b", "a"], "10")
    assert await _edges(factory, triangle) == {}, "stand: the payment did not remove the edge"
    assert (await _verify(factory, triangle.equivalent.id)).status == PASSED

    deletions = [e for e in await _entries(factory, triangle.equivalent.id) if e.effect == "D"]
    assert len(deletions) == 1, deletions
    await _around_the_application(
        factory,
        lambda d: f"DELETE FROM debt_journal_entries WHERE id = '{_literal(d, deletions[0].id)}'",
    )

    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == FAILED, outcome
    finding = _one_edge(outcome, "edge_residual")
    assert (finding["current_debt"], finding["unexplained"]) == ("0.00000000", "-10.00000000"), finding


@pytest.mark.asyncio
async def test_step5a_a_duplicated_delta_is_failed(db_session) -> None:
    """T1508 shape: a journal entry is copied - same edge, same amounts, a different ordinal.

    MUTATION: in `_journal_sums`, count an entry only once per `(edge, amount_before, amount_after,
    delta)`. The copy is then invisible and this test goes red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    await _pay(factory, triangle, ["a", "b"], "5")
    assert await _edges(factory, triangle) == {("a", "b"): Decimal("15.00000000")}
    assert (await _verify(factory, triangle.equivalent.id)).status == PASSED

    updates = [e for e in await _entries(factory, triangle.equivalent.id) if e.effect == "U"]
    assert len(updates) == 1, updates
    copy_id = uuid.uuid4()
    await _around_the_application(
        factory,
        lambda d: (
            "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, "
            "debtor_id, creditor_id, effect, amount_before, amount_after, delta, recorded_at) "
            f"SELECT '{_literal(d, copy_id)}', operation_id, "
            "nextval('debt_journal_entries_ordinal_seq'), equivalent_id, "
            "debtor_id, creditor_id, effect, amount_before, amount_after, delta, recorded_at "
            f"FROM debt_journal_entries WHERE id = '{_literal(d, updates[0].id)}'"
        ),
    )

    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == FAILED, outcome
    assert _one_edge(outcome, "edge_residual")["unexplained"] == "-5.00000000", outcome


@pytest.mark.asyncio
async def test_step5a_a_payment_committed_between_the_verifiers_reads_is_still_passed(
    db_session, monkeypatch
) -> None:
    """The scheduled verifier reads journal, baseline, debts and offsets without the owner lock.

    Its verdict is only sound if the four reads see ONE snapshot. Forced here: the verifier is paused
    after the journal read, a real payment commits in the same equivalent from another session, and the
    verifier resumes. A false FAILED is the worst outcome in this programme - step 5c holds money on it.

    MUTATION: commit the session between `_journal_sums` and `_current_debts` - red.
    """
    from tests.conftest import TestingSessionLocal as factory

    seen = await interleave_a_payment_between_the_verifiers_reads(factory, monkeypatch)
    _assert_interleave(seen)


# ==============================================================================================
# Stored results: transitions, not observations
# ==============================================================================================


async def _run_once(factory, equivalent_id) -> dict:
    from app.core.ledger.reconciliation import run_scheduled_reconciliation

    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])
    assert counts["error"] == 0, counts
    return counts


async def _result_rows(factory, equivalent_id) -> list[SimpleNamespace]:
    columns = debt_reconciliation_results.c
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    columns.status,
                    columns.fingerprint,
                    columns.checked_at,
                    columns.last_checked_at,
                    columns.is_latest,
                    columns.detail,
                ).where(columns.equivalent_id == equivalent_id)
            )
        ).all()
    return [SimpleNamespace(**row._mapping) for row in rows]


def _latest(rows: list[SimpleNamespace]) -> SimpleNamespace:
    latest = [row for row in rows if row.is_latest]
    assert len(latest) == 1, f"expected exactly one latest row: {rows}"
    return latest[0]


@pytest.mark.asyncio
async def test_step5a_an_unchanged_verdict_keeps_one_row_and_advances_last_checked_at(db_session) -> None:
    """Three identical scheduled runs: one row, `last_checked_at` advancing, `checked_at` fixed.

    MUTATION: make `record_outcome` insert even when the fingerprint matches - three rows, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)

    assert (await _run_once(factory, triangle.equivalent.id))["rows_inserted"] == 1
    (first,) = await _result_rows(factory, triangle.equivalent.id)
    seen = [first.last_checked_at]
    for _ in range(2):
        counts = await _run_once(factory, triangle.equivalent.id)
        assert (counts["rows_inserted"], counts["rows_unchanged"]) == (0, 1), counts
        rows = await _result_rows(factory, triangle.equivalent.id)
        assert len(rows) == 1, rows
        seen.append(rows[0].last_checked_at)

    (row,) = await _result_rows(factory, triangle.equivalent.id)
    assert (row.status, row.is_latest) == (PASSED, True), row
    assert row.checked_at == first.checked_at, "the first observation time moved"
    assert seen[0] < seen[1] < seen[2], f"last_checked_at did not advance: {seen}"


@pytest.mark.asyncio
async def test_step5a_a_failed_then_passed_transition_inserts_and_keeps_the_failed_evidence(
    db_session,
) -> None:
    """FAILED, then the change is undone around the application: a PASSED row is inserted beside it.

    MUTATION: leave the old row's `is_latest` set when inserting - the partial unique index refuses the
    second latest row, the run errors, red (as does the findings-change test).
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)

    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '10.00000001' WHERE id = '{_literal(d, debt.id)}'"
    )
    await _run_once(factory, triangle.equivalent.id)
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '10' WHERE id = '{_literal(d, debt.id)}'"
    )
    assert (await _run_once(factory, triangle.equivalent.id))["rows_inserted"] == 1
    assert (await _run_once(factory, triangle.equivalent.id))["rows_unchanged"] == 1

    rows = await _result_rows(factory, triangle.equivalent.id)
    assert sorted(row.status for row in rows) == [FAILED, PASSED], rows
    assert _latest(rows).status == PASSED, rows
    (failed,) = [row for row in rows if row.status == FAILED]
    detail = json.loads(failed.detail) if isinstance(failed.detail, str) else failed.detail
    assert detail["findings"][0]["unexplained"] == "0.00000001", detail


@pytest.mark.asyncio
async def test_step5a_a_payment_on_an_already_failed_edge_keeps_the_fault_identity(db_session, monkeypatch) -> None:
    """The fingerprint is the FAULT, not the amounts around it.

    A one-unit change around the application makes A -> B FAILED. A legitimate journalled payment on
    that same edge then moves `current_debt` and `journal_delta_sum` and leaves `unexplained` at 1:
    one row, the same fingerprint, `last_checked_at` advanced, `detail` still showing the first
    observation. A DIFFERENT unexplained amount on the same edge is a new fault and inserts.

    STEP 5c CHANGED THE STAND, NOT THE CLAIM (2026-09-14). A scheduled FAILED now holds the equivalent,
    and a held equivalent refuses exactly the payment this test needs. The fault identity still matters
    wherever such a payment is reachable - a hold whose transaction failed to commit, or a hold released
    around the application - so ONLY the reaction is stubbed out here; nothing the fingerprint reads is.
    The hold itself is tested in `tests/unit/test_p015_step5c_reaction_and_hold.py`.

    MUTATION: put `current_debt` back into the `edge_residual` identity - the payment inserts, red.
    """
    from app.core.ledger import reconciliation
    from tests.conftest import TestingSessionLocal as factory

    async def _no_reaction(session_factory, equivalent_id):
        return reconciliation.HoldDecision(reconciliation.HOLD_NOT_CONFIRMED)

    monkeypatch.setattr(reconciliation, "react_to_failed", _no_reaction)

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '11' WHERE id = '{_literal(d, debt.id)}'"
    )
    assert (await _run_once(factory, triangle.equivalent.id))[FAILED] == 1
    (first,) = await _result_rows(factory, triangle.equivalent.id)

    await _pay(factory, triangle, ["a", "b"], "5")
    assert await _edges(factory, triangle) == {("a", "b"): Decimal("16.00000000")}, "stand: no payment"
    counts = await _run_once(factory, triangle.equivalent.id)
    assert (counts[FAILED], counts["rows_inserted"], counts["rows_unchanged"]) == (1, 0, 1), counts

    (row,) = await _result_rows(factory, triangle.equivalent.id)
    assert row.fingerprint == first.fingerprint, "the fault identity changed with a legitimate payment"
    assert row.last_checked_at > first.last_checked_at, row
    detail = json.loads(row.detail) if isinstance(row.detail, str) else row.detail
    assert (detail["findings"][0]["current_debt"], detail["findings"][0]["unexplained"]) == (
        "11.00000000",
        "1.00000000",
    ), "detail no longer shows the amounts as first observed"

    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '18' WHERE id = '{_literal(d, debt.id)}'"
    )
    assert (await _run_once(factory, triangle.equivalent.id))["rows_inserted"] == 1
    rows = await _result_rows(factory, triangle.equivalent.id)
    assert len(rows) == 2 and rows[0].fingerprint != rows[1].fingerprint, rows


@pytest.mark.asyncio
async def test_step5a_different_findings_under_the_same_status_insert(db_session) -> None:
    """FAILED on A -> B, then FAILED on B -> C instead: two FAILED rows, not one refreshed.

    MUTATION: leave the findings out of the fingerprint - the second FAILED looks identical, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[])
    ab, bc = await _fixture_debts(factory, triangle, [("a", "b", "10"), ("b", "c", "4")])
    await _baseline(factory, triangle.equivalent.id)

    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '11' WHERE id = '{_literal(d, ab.id)}'"
    )
    await _run_once(factory, triangle.equivalent.id)
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '10' WHERE id = '{_literal(d, ab.id)}'"
    )
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '5' WHERE id = '{_literal(d, bc.id)}'"
    )
    counts = await _run_once(factory, triangle.equivalent.id)
    assert (counts[FAILED], counts["rows_inserted"]) == (1, 1), counts

    rows = await _result_rows(factory, triangle.equivalent.id)
    assert [row.status for row in rows] == [FAILED, FAILED], rows
    assert rows[0].fingerprint != rows[1].fingerprint, rows
    latest = _latest(rows)
    detail = json.loads(latest.detail) if isinstance(latest.detail, str) else latest.detail
    assert detail["findings"][0]["debtor_id"] == str(triangle.b.id), detail


# ==============================================================================================
# The baseline
# ==============================================================================================


@pytest.mark.asyncio
async def test_step5a_the_baseline_adopts_a_debt_the_journal_cannot_explain_and_later_change_is_checked(
    db_session,
) -> None:
    """A debt written around the journal BEFORE the baseline is adopted as an offset, not certified.

    That is the upgrade case: debts older than migration 022 have no entries. The offset row exists for
    exactly that edge; the journalled edge beside it has no row (an absent row is zero). A later
    payment on the adopted edge is PASSED.

    MUTATIONS: (1) compare `debt - journal == -offset` in the verifier - red at the first PASSED;
    (2) record zero offsets as rows in `take_baseline` - the CHECK refuses the zero row, the baseline
    raises, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    adopted_id = uuid.uuid4()
    await _around_the_application(
        factory,
        lambda d: (
            "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
            f"VALUES ('{_literal(d, adopted_id)}', '{_literal(d, triangle.a.id)}', "
            f"'{_literal(d, triangle.b.id)}', '{_literal(d, triangle.equivalent.id)}', '7', 0)"
        ),
    )
    await _fixture_debts(factory, triangle, [("b", "c", "4")])

    taken = await _baseline(factory, triangle.equivalent.id)
    assert (taken.offsets_recorded, taken.edges_seen) == (1, 2), taken
    assert await _offsets(factory, triangle.equivalent.id) == [
        (triangle.a.id, triangle.b.id, Decimal("7.00000000"))
    ]
    assert (await _verify(factory, triangle.equivalent.id)).status == PASSED

    await _pay(factory, triangle, ["a", "b"], "1")
    assert (await _edges(factory, triangle))[("a", "b")] == Decimal("8.00000000")
    outcome = await _verify(factory, triangle.equivalent.id)
    assert outcome.status == PASSED, outcome
    assert outcome.edges_checked == 2, outcome


@pytest.mark.asyncio
async def test_step5a_an_equivalent_has_exactly_one_baseline(db_session) -> None:
    """Nothing re-baselines: a second attempt is refused and the first header is unchanged."""
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[])
    await _baseline(factory, triangle.equivalent.id)
    with pytest.raises(BaselineAlreadyTaken):
        await _baseline(factory, triangle.equivalent.id)
    async with factory() as session:
        headers = (
            await session.execute(
                select(func.count()).select_from(debt_reconciliation_baselines).where(
                    debt_reconciliation_baselines.c.equivalent_id == triangle.equivalent.id
                )
            )
        ).scalar_one()
    assert headers == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["TEST_FIXTURE", "SEED"])
async def test_step5a_a_seed_or_fixture_write_after_the_baseline_is_refused(db_session, kind) -> None:
    """After the baseline, a SEED or TEST_FIXTURE write to that equivalent is refused, not recorded.

    ANTI-VACUUM: the identical write to an equivalent WITHOUT a baseline still commits, so the refusal
    is about the baseline and not about the write.

    MUTATION: remove the `_PRE_BASELINE_ONLY_KINDS` block from `book._complete`. The late debt
    commits and this test goes red on the refusal and on the stored state.
    """
    from tests.conftest import TestingSessionLocal as factory

    baselined = await _seed_triangle(factory, trustlines=[])
    open_book = await _seed_triangle(factory, trustlines=[])
    await _fixture_debts(factory, baselined, [("a", "b", "10")])
    await _baseline(factory, baselined.equivalent.id)
    before = await _edges(factory, baselined)
    entries_before = len(await _entries(factory, baselined.equivalent.id))

    async def _write(triangle) -> None:
        late = Debt(
            id=uuid.uuid4(),
            debtor_id=triangle.b.id,
            creditor_id=triangle.c.id,
            equivalent_id=triangle.equivalent.id,
            amount=Decimal("4"),
            version=0,
        )
        async with factory() as session:
            if kind == "TEST_FIXTURE":
                async with debt_fixture_setup(session, label="after-baseline"):
                    session.add(late)
            else:
                async with Book.operation(
                    session,
                    operation_for(
                        "SEED",
                        f"step5a-late-seed/{uuid.uuid4()}",
                        {"probe": "after-baseline"},
                        scope_equivalent_ids=None,
                    ),
                ):
                    session.add(late)
            await session.commit()

    with pytest.raises(BookError) as refused:
        await _write(baselined)
    assert refused.value.reason == Refusal.UNVERIFIABLE_WRITER_AFTER_BASELINE, refused.value
    assert await _edges(factory, baselined) == before, "the refused write is durable"
    assert len(await _entries(factory, baselined.equivalent.id)) == entries_before
    assert (await _verify(factory, baselined.equivalent.id)).status == PASSED

    await _write(open_book)
    assert await _edges(factory, open_book) == {("b", "c"): Decimal("4.00000000")}, (
        "stand: the same write to an equivalent without a baseline did not commit"
    )


# ==============================================================================================
# The host and the separation of the result
# ==============================================================================================


@pytest.mark.asyncio
async def test_step5a_the_scheduled_result_is_its_own_row_and_never_enters_a_checkpoint_or_audit(
    db_session, monkeypatch
) -> None:
    """The scheduled host persists FAILED as its own row; the checkpoint and audit rows stay untouched.

    ALSO: the equivalent is DEACTIVATED first. The T1544 operator stop refuses to move money; it must
    not stop the verifier from reading.

    MUTATIONS: (1) add `checks["debt_reconciliation"] = {"passed": False}` and an alert to
    `compute_integrity_checkpoint_for_equivalent` - red on the checkpoint assertions; (2) remove the
    `_run_debt_reconciliation_once` call from `app/main.py` - no result row, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    # A whole unit, not an atom: the atom's precision has its own control above, and this test is
    # about where the result goes, so a precision mutation must not redden it.
    await _around_the_application(
        factory,
        lambda d: f"UPDATE debts SET amount = '11' WHERE id = '{_literal(d, debt.id)}'",
    )
    async with factory() as session:
        await session.execute(
            update(Equivalent).where(Equivalent.id == triangle.equivalent.id).values(is_active=False)
        )
        await session.commit()
        audit_before = (
            await session.execute(select(func.count()).select_from(IntegrityAuditLog))
        ).scalar_one()

    # The participant-reachable checkpoint computation writes no result.
    async with factory() as session:
        await compute_integrity_checkpoint_for_equivalent(session, equivalent_id=triangle.equivalent.id)
    assert await _results(factory, triangle.equivalent.id) == []

    await _scheduled_run(monkeypatch, factory)

    results = await _results(factory, triangle.equivalent.id)
    assert [status for status, _ in results] == [FAILED], results
    assert results[0][1]["criterion"] == CRITERION_A, results
    assert results[0][1]["findings"][0]["kind"] == "edge_residual", results

    async with factory() as session:
        checkpoints = (
            await session.execute(
                select(IntegrityCheckpoint.invariants_status).where(
                    IntegrityCheckpoint.equivalent_id == triangle.equivalent.id
                )
            )
        ).scalars().all()
        audit_after = (
            await session.execute(select(func.count()).select_from(IntegrityAuditLog))
        ).scalar_one()
    assert len(checkpoints) == 1, checkpoints
    status = checkpoints[0]
    assert set(status["checks"]) == CHECKPOINT_CHECKS, status["checks"]
    assert status["passed"] is True and status["status"] == "healthy", status
    assert status["alerts"] == [], status
    assert "reconcil" not in json.dumps(status).lower(), status
    assert audit_after == audit_before, "the scheduled reconciliation wrote an audit row"


@pytest.mark.asyncio
async def test_step5a_c6_still_commits_verified_and_criterion_a_is_blind_to_it_until_the_book_moves(
    db_session, monkeypatch
) -> None:
    """`C6` (i) with a baseline: COMMITTED, `audit == [True]`, then the scheduled result.

    WHAT THE RESULT IS: criterion (a) has NO finding on the C6 state. C6's writer journals its wrong edge
    faithfully, so `debt - sum(delta)` still equals the offset - criterion (a) cannot refute a faithful
    wrong writer. Since step 5b the same row carries criterion (b), which does refute it, so the stored
    status is FAILED with (b) findings only: this is the "(a) passes, (b) fails" counterprobe the spec
    keeps. A one-atom change around the application on the same C6 state then adds (a)'s residual as a
    new transition.

    MUTATION: remove the `_run_debt_reconciliation_once` call from `app/main.py` - no result rows, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("c", "a", "100")]
    )
    await _baseline(factory, triangle.equivalent.id)
    # 019 stage 4: the wrong writer is armed BEFORE the payment (it is one transaction now), on the book
    # seam `book._apply_payment_flow`; the explicit route A -> B -> C is the router's result.
    _collapse_the_route(monkeypatch, triangle)
    tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))

    assert await _edges(factory, triangle) == {("a", "c"): Decimal("5.00000000")}
    assert await _tx_state(factory, tx_id) == "COMMITTED"
    assert await _audit(factory, tx_id) == [True]

    await _scheduled_run(monkeypatch, factory)
    results = await _results(factory, triangle.equivalent.id)
    # STEP 5b: the same row now carries criterion (b), and (b) refutes C6. What this test keeps is the
    # "(a) passes, (b) fails" counterprobe - no criterion (a) finding, and (b) findings present.
    assert [s for s, _ in results] == [FAILED], results
    kinds = {f["kind"] for f in results[0][1]["findings"]}
    assert kinds and all(kind.startswith("b_") for kind in kinds), (
        "criterion (a) on the C6 state was expected silent - the wrong edge is journalled faithfully - "
        f"and criterion (b) to refute it: {kinds}"
    )

    async with factory() as session:
        debt_id = (
            await session.execute(
                select(Debt.id).where(
                    Debt.equivalent_id == triangle.equivalent.id,
                    Debt.debtor_id == triangle.a.id,
                    Debt.creditor_id == triangle.c.id,
                )
            )
        ).scalar_one()
    await _around_the_application(
        factory,
        lambda d: f"UPDATE debts SET amount = '6' WHERE id = '{_literal(d, debt_id)}'",
    )
    await _scheduled_run(monkeypatch, factory)
    rows = await _results(factory, triangle.equivalent.id)
    # Two FAILED rows since step 5b: the first carries only (b), the transition adds (a)'s residual.
    assert sorted(s for s, _ in rows) == [FAILED, FAILED], rows
    assert any(f["kind"] == "edge_residual" for _, detail in rows for f in detail["findings"]), rows
    assert await _audit(factory, tx_id) == [True], "the scheduled run touched the payment's audit row"
