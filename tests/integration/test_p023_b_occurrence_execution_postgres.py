"""Programme 023, slice (b): a plan occurrence executed with a DECLARED amount through the 019 money boundary.

Spec `specs/023-clearing-as-flow/spec.md`, decisions 5-6 and Verification plan §1 (R-023-4b) and §2
("Идентичность", "Нейтральность и 019"). The v2 entry `ClearingService.execute_occurrence` takes an immutable
descriptor - plan UUID, equivalent, cycle ordinal, debt ids in cycle order, integer amount in atoms - and
reduces each declared edge by exactly `c` through `Book`, inside the SAME boundary as v1: the exclusive
equivalent session lock, the retry owner `_run_attempts`, the stop/hold read `FOR SHARE`, the commit
resolver and `ClearingCommittedAfterCancellation`. Nothing in production calls it yet (slice (d) switches the
callers; `tests/unit/test_p023_b_occurrence_is_not_wired.py`).

* R-023-4b (the v2 regression): two plans commit two EQUAL partial amounts on the SAME surviving debt ids -
  two occurrences, two identities, both effects (`p_e - 2c`), and criterion (b) recomputes both PASSED.
  Separate controls: a replay of one occurrence returns its durable amount with no second effect; the same
  occurrence id with a changed descriptor is REFUSED, not replayed.
* The paths of the boundary, walked for v2: success (partial and exact-at-zero), stale plan (skip), a real
  40001 retried on a fresh snapshot, the retry budget exhausted, an unknown commit that did and did not land,
  a commit that became durable while the caller was cancelled, the operator stop, the run perimeter (fresh
  and on replay), consent withdrawn, and a descriptor that does not match the locked rows.

Every test runs on a disposable clone (`tier_sessions_on_a_clone`). RED ON A TREE WITHOUT SLICE (b): the
surface lookup (`tests/p023_support.py::slice_b_surface`) ends each v2 test on `TargetMismatch`.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clearing.service import ClearingOccurrenceRefused, ClearingService
from app.core.ledger.reconciliation import FAILED, PASSED
from app.db.journal_tables import debt_operations
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import ConflictException, GeoException
from tests.integration.test_clearing_commit_replay_postgres import (
    _conflicting_clearing_service,
    _seed_conflict_cycle,
)
from tests.p019_support import require_target
from tests.p023_support import historical_v1_clearing, occurrence_of, slice_b_surface
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: F401 - autouse: every test on a clone
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import _edges, _seed_triangle
from tests.unit.test_p015_step5a_reconciliation import (
    _around_the_application,
    _baseline,
    _fixture_debts,
    _literal,
    _verify,
)

ATOM = 10**8
PLAN_A = uuid.UUID("0a023b00-0000-4000-8000-00000000000a")
PLAN_B = uuid.UUID("0a023b00-0000-4000-8000-00000000000b")


def _factory():
    from tests.conftest import TestingSessionLocal

    return TestingSessionLocal


async def _triangle(pre=("5", "5", "5")):
    """a -> b, b -> c, c -> a holding `pre`; trust lines creditor -> debtor; consent by default (no policy)."""

    factory = _factory()
    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100")])
    debts = await _fixture_debts(factory, triangle, [("a", "b", pre[0]), ("b", "c", pre[1]), ("c", "a", pre[2])])
    return triangle, debts


def _occurrence(api, triangle, debts, *, plan=PLAN_A, ordinal=0, units: str = "2", order=(0, 1, 2), equivalent_id=None):
    # The construction lives in `tests/p023_support.py::occurrence_of` (025 `T2508.1`); `api` stays in the signature
    # because every caller has already looked the slice (b) surface up through it.
    return occurrence_of(
        [debts[k].id for k in order],
        equivalent_id=equivalent_id or triangle.equivalent.id,
        amount=units,
        plan_id=plan,
        ordinal=ordinal,
    )


async def _execute(occurrence, **kwargs):
    async with _factory()() as session:
        return await ClearingService(session).execute_occurrence(occurrence, **kwargs)


async def _clearings() -> list[tuple[str, str, dict]]:
    async with _factory()() as session:
        rows = (
            await session.execute(
                select(Transaction.tx_id, Transaction.state, Transaction.payload).where(Transaction.type == "CLEARING")
            )
        ).all()
    return sorted((tx_id, state, payload) for tx_id, state, payload in rows)


async def _envelopes() -> list[tuple[str, int]]:
    async with _factory()() as session:
        rows = (
            await session.execute(
                select(debt_operations.c.tx_id, debt_operations.c.intent_encoding_version).where(
                    debt_operations.c.kind == "CLEARING"
                )
            )
        ).all()
    return sorted((tx_id, int(version)) for tx_id, version in rows)


# ------------------------------------------------------------------------------------------------ R-023-4b


@pytest.mark.asyncio
async def test_r023_4b_two_plans_commit_two_equal_partials_on_the_same_surviving_debts() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    await _baseline(_factory(), triangle.equivalent.id)
    first = _occurrence(api, triangle, debts, plan=PLAN_A)
    second = _occurrence(api, triangle, debts, plan=PLAN_B)
    # Controls: one debt set, one amount, one ordinal - only the plan differs.
    assert first.debt_ids == second.debt_ids and first.amount_atoms == second.amount_atoms

    assert await _execute(first) == Decimal("2")
    assert await _execute(second) == Decimal("2")

    clearings = await _clearings()
    assert [(tx_id, state) for tx_id, state, _ in clearings] == sorted(
        [(first.occurrence_id, "COMMITTED"), (second.occurrence_id, "COMMITTED")]
    ), clearings
    assert first.occurrence_id != second.occurrence_id
    # Both effects: every row survived both occurrences and lost 2c.
    assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("1"), ("b", "c"): Decimal("1"), ("c", "a"): Decimal("1")}
    assert await _envelopes() == sorted([(first.occurrence_id, api.version), (second.occurrence_id, api.version)])
    outcome = await _verify(_factory(), triangle.equivalent.id)
    assert outcome.status == PASSED, outcome
    assert outcome.detail()["criterion_b"]["coverage"]["full_recomputation"] == {"CLEARING": 2}


@pytest.mark.asyncio
async def test_r023_4b_control_a_replay_returns_the_durable_amount_without_a_second_effect() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    occurrence = _occurrence(api, triangle, debts)
    assert await _execute(occurrence) == Decimal("2")
    # The same occurrence again - a retry or a resolved unknown commit re-entering with its id.
    replayed = await _execute(api.ClearingOccurrence(**_fields(occurrence)))
    assert replayed == Decimal("2")
    assert len(await _clearings()) == 1
    assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("3"), ("b", "c"): Decimal("3"), ("c", "a"): Decimal("3")}


def _fields(occurrence) -> dict:
    return dict(
        plan_id=occurrence.plan_id,
        equivalent_id=occurrence.equivalent_id,
        ordinal=occurrence.ordinal,
        debt_ids=occurrence.debt_ids,
        amount_atoms=occurrence.amount_atoms,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["amount", "debt_order"])
async def test_r023_4b_control_a_changed_descriptor_for_the_same_occurrence_is_refused(change) -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    occurrence = _occurrence(api, triangle, debts)
    assert await _execute(occurrence) == Decimal("2")
    changed = (
        _occurrence(api, triangle, debts, units="1")
        if change == "amount"
        else _occurrence(api, triangle, debts, order=(1, 2, 0))  # the same cycle, rotated: another descriptor
    )
    # Control: the slot - and therefore the id - is the same; only the descriptor differs.
    assert changed.occurrence_id == occurrence.occurrence_id and changed.descriptor() != occurrence.descriptor()
    with pytest.raises(api.ClearingOccurrenceRefused) as refused:
        await _execute(changed)
    assert refused.value.details.get("reason") == "occurrence_descriptor_mismatch", refused.value.details
    assert len(await _clearings()) == 1
    assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("3"), ("b", "c"): Decimal("3"), ("c", "a"): Decimal("3")}


# ----------------------------------------------------------------------------------------- success paths


@pytest.mark.asyncio
async def test_v2_an_exact_occurrence_deletes_at_zero_and_is_recomputed_passed() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle(("2", "5", "7"))
    await _baseline(_factory(), triangle.equivalent.id)
    assert await _execute(_occurrence(api, triangle, debts, units="2")) == Decimal("2")
    assert await _edges(_factory(), triangle) == {("b", "c"): Decimal("3"), ("c", "a"): Decimal("5")}
    assert (await _verify(_factory(), triangle.equivalent.id)).status == PASSED


@pytest.mark.asyncio
async def test_v2_writes_intent_version_two_and_the_payload_descriptor_and_a_historical_v1_record_still_reconciles() -> None:
    """The v2 record next to a HISTORICAL v1 one on the same rows, and both read back.

    025 `T2508.1` (spec `T2500`, P2-4): the v1 clearing is history, not a fresh execution without an occurrence. A
    later occurrence clears the locked minimum (3) - what the v1 writer cleared - and its record is FORGED into the
    one the v1 writer left (`historical_v1_clearing`). What is tested in the APPLICATION: v2 writes intent version
    2 and the descriptor in the payload (the `api.version` pair and the `occurrence` payload key of the v2 tx), and
    the verifier reads the v1-encoded record next to it (criterion (b) PASSED, `full_recomputation` `{"CLEARING":
    2}`). The two assertions on the v1 side - `(v1_tx, 1)` in the envelopes and no `occurrence` key in its payload -
    only confirm the forged record (the stand), not that any application code path writes v1: that execution
    WITHOUT an occurrence is the mode which 024 `T2417` removes.
    """
    api = slice_b_surface()
    triangle, debts = await _triangle(("5", "5", "5"))
    await _baseline(_factory(), triangle.equivalent.id)
    occurrence = _occurrence(api, triangle, debts)
    assert await _execute(occurrence) == Decimal("2")
    later = _occurrence(api, triangle, debts, plan=PLAN_B, units="3")
    assert await _execute(later) == Decimal("3")
    v1_tx = await historical_v1_clearing(_factory(), later)
    assert await _envelopes() == sorted([(occurrence.occurrence_id, api.version), (v1_tx, 1)])
    payloads = {tx_id: payload for tx_id, _state, payload in await _clearings()}
    assert payloads[occurrence.occurrence_id]["occurrence"] == occurrence.descriptor()
    assert Decimal(payloads[occurrence.occurrence_id]["amount"]) == Decimal("2")
    assert "occurrence" not in payloads[v1_tx]
    assert await _edges(_factory(), triangle) == {}
    outcome = await _verify(_factory(), triangle.equivalent.id)
    assert outcome.status == PASSED, outcome
    assert outcome.detail()["criterion_b"]["coverage"]["full_recomputation"] == {"CLEARING": 2}, outcome.detail()


@pytest.mark.asyncio
async def test_v2_a_coordinated_under_clear_of_a_v2_occurrence_is_failed_by_b() -> None:
    """The v2 record corrupted coordinately (entry and debt move together, so criterion (a) is silent): one edge
    reduced by 1 instead of 2. The expected delta comes from the frozen descriptor, so (b) says it."""
    api = slice_b_surface()
    triangle, debts = await _triangle(("5", "5", "5"))
    await _baseline(_factory(), triangle.equivalent.id)
    occurrence = _occurrence(api, triangle, debts)
    assert await _execute(occurrence) == Decimal("2")
    await _around_the_application(
        _factory(),
        lambda d: (
            "UPDATE debt_journal_entries SET amount_after = '4.00000000', delta = '-1.00000000' "
            f"WHERE effect = 'U' AND debtor_id = '{_literal(d, triangle.a.id)}' "
            f"AND creditor_id = '{_literal(d, triangle.b.id)}'"
        ),
    )
    await _around_the_application(
        _factory(), lambda d: f"UPDATE debts SET amount = '4.00000000' WHERE id = '{_literal(d, debts[0].id)}'"
    )
    outcome = await _verify(_factory(), triangle.equivalent.id)
    assert outcome.status == FAILED, outcome
    assert [f["kind"] for f in outcome.findings] == ["b_delta_mismatch"], outcome.findings


# ------------------------------------------------------------------------------------------- skip paths


@pytest.mark.asyncio
async def test_v2_a_stale_plan_whose_edge_fell_below_c_is_skipped_without_effect() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle(("5", "5", "5"))
    assert await _execute(_occurrence(api, triangle, debts, plan=PLAN_A, units="3")) == Decimal("3")
    # Plan B was made on the old snapshot (5 each) and declares 4: every edge now holds 2.
    assert await _execute(_occurrence(api, triangle, debts, plan=PLAN_B, units="4")) is None
    assert len(await _clearings()) == 1
    assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("2"), ("b", "c"): Decimal("2"), ("c", "a"): Decimal("2")}


@pytest.mark.asyncio
async def test_v2_withdrawn_consent_is_a_skip() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    async with _factory()() as session:
        await session.execute(
            update(TrustLine)
            .where(TrustLine.from_participant_id == triangle.b.id, TrustLine.to_participant_id == triangle.a.id)
            .values(policy={"auto_clearing": False})
        )
        await session.commit()
    assert await _execute(_occurrence(api, triangle, debts)) is None
    assert await _clearings() == []


# ---------------------------------------------------------------------------------------- refusal paths


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["equivalent", "not_a_directed_cycle"])
async def test_v2_a_descriptor_that_does_not_match_the_locked_rows_is_refused(wrong) -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    occurrence = (
        _occurrence(api, triangle, debts, equivalent_id=uuid.UUID(int=0xE0E0))
        if wrong == "equivalent"
        else _occurrence(api, triangle, debts, order=(0, 2, 1))  # a->b, c->a, b->c: not a walk in this order
    )
    with pytest.raises(api.ClearingOccurrenceRefused):
        await _execute(occurrence)
    assert await _clearings() == []
    assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("5"), ("b", "c"): Decimal("5"), ("c", "a"): Decimal("5")}


@pytest.mark.asyncio
async def test_the_boundary_refuses_an_execution_without_an_occurrence_before_any_money_moves() -> None:
    """024 `T2417`: the shared boundary runs only inside `execute_occurrence`; called bare it refuses, nothing moves."""

    triangle, debts = await _triangle()
    try:
        async with _factory()() as session:
            outcome = await ClearingService(session).execute_clearing_with_amount([{"debt_id": str(d.id)} for d in debts])
    except ClearingOccurrenceRefused as refusal:
        outcome = refusal.reason
    edges, clearings, envelopes = await _edges(_factory(), triangle), await _clearings(), await _envelopes()
    require_target(
        outcome == "occurrence_missing" and not clearings and not envelopes
        and edges == {("a", "b"): Decimal("5"), ("b", "c"): Decimal("5"), ("c", "a"): Decimal("5")},
        f"a call without an occurrence returned {outcome!r}; clearings={len(clearings)} edges={edges}",
    )


@pytest.mark.asyncio
async def test_v2_the_operator_stop_refuses_the_occurrence() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    async with _factory()() as session:
        await session.execute(update(Equivalent).where(Equivalent.id == triangle.equivalent.id).values(is_active=False))
        await session.commit()
    with pytest.raises(ConflictException) as refused:
        await _execute(_occurrence(api, triangle, debts))
    assert not isinstance(refused.value, api.ClearingOccurrenceRefused), "the stop, not a descriptor refusal"
    assert await _clearings() == []


@pytest.mark.asyncio
async def test_v2_the_run_perimeter_holds_fresh_and_on_replay() -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    occurrence = _occurrence(api, triangle, debts)
    outside = {triangle.a.pid, triangle.b.pid}  # c is outside
    with pytest.raises(GeoException):
        await _execute(occurrence, allowed_participant_pids=outside)
    assert await _clearings() == []
    assert await _execute(occurrence, allowed_participant_pids={p.pid for p in (triangle.a, triangle.b, triangle.c)}) == Decimal("2")
    with pytest.raises(GeoException):
        await _execute(api.ClearingOccurrence(**_fields(occurrence)), allowed_participant_pids=outside)
    assert len(await _clearings()) == 1


# ------------------------------------------------------------------------- the 019 retry owner, for v2


async def _run_owner_v2(service_cls, occurrence):
    from tests.conftest import TestingSessionLocal

    owner_session = TestingSessionLocal()
    try:
        await owner_session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
        try:
            return await asyncio.wait_for(service_cls(owner_session).execute_occurrence(occurrence), 30)
        except Exception as exc:  # noqa: BLE001 - compared by the caller
            return exc
    finally:
        await owner_session.rollback()
        await owner_session.close()


@pytest.mark.asyncio
async def test_v2_a_real_serialization_conflict_is_retried_and_clears_the_declared_amount() -> None:
    api = slice_b_surface()
    equivalent_id, _code, _participants, debt_ids = await _seed_conflict_cycle("VB")
    occurrence = api.ClearingOccurrence(
        plan_id=PLAN_A, equivalent_id=equivalent_id, ordinal=0, debt_ids=tuple(debt_ids), amount_atoms=10 * ATOM
    )
    observed: list[str] = []
    service_cls = _conflicting_clearing_service(debt_ids[0], [Decimal("101.00")], observed)
    outcome = await _run_owner_v2(service_cls, occurrence)
    assert observed == ["40001"], observed  # control: the conflict was PostgreSQL's own
    assert outcome == Decimal("10"), outcome
    assert service_cls.attempts == 2
    clearings = await _clearings()
    assert [(tx_id, state) for tx_id, state, _ in clearings] == [(occurrence.occurrence_id, "COMMITTED")]
    async with _factory()() as session:
        rows = dict((await session.execute(text("SELECT id, amount FROM debts WHERE equivalent_id = :e"), {"e": equivalent_id})).all())
    # The declared 10, not the minimum 30, on the re-read amounts (101 by the concurrent writer).
    assert rows == {debt_ids[0]: Decimal("91.00000000"), debt_ids[1]: Decimal("20.00000000"), debt_ids[2]: Decimal("30.00000000")}


@pytest.mark.asyncio
async def test_v2_an_exhausted_retry_budget_is_the_typed_retryable_refusal(monkeypatch) -> None:
    from app.config import settings
    from app.core.clearing.service import RetryableClearingConflictException

    api = slice_b_surface()
    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)
    equivalent_id, _code, _participants, debt_ids = await _seed_conflict_cycle("VX")
    occurrence = api.ClearingOccurrence(
        plan_id=PLAN_A, equivalent_id=equivalent_id, ordinal=0, debt_ids=tuple(debt_ids), amount_atoms=10 * ATOM
    )
    observed: list[str] = []
    writes = [Decimal("101.00"), Decimal("102.00"), Decimal("103.00"), Decimal("104.00")]
    service_cls = _conflicting_clearing_service(debt_ids[0], writes, observed)
    outcome = await _run_owner_v2(service_cls, occurrence)
    assert observed == ["40001"] * 3, observed
    assert isinstance(outcome, RetryableClearingConflictException), outcome
    assert await _clearings() == [] and await _envelopes() == []


# ------------------------------------------------------------------ unknown commit and cancellation, v2


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [True, False])
async def test_v2_an_unknown_commit_is_resolved_and_never_retried(monkeypatch, landed) -> None:
    api = slice_b_surface()
    triangle, debts = await _triangle()
    occurrence = _occurrence(api, triangle, debts)
    real_commit = AsyncSession.commit
    commits = {"armed": True, "seen": 0}

    async def _commit_then_lose_the_ack(session):
        if commits["armed"] and session.in_transaction():
            commits["armed"] = False
            commits["seen"] += 1
            if landed:
                await real_commit(session)
            else:
                await session.rollback()
            raise ConnectionError("commit acknowledgement lost")
        return await real_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", _commit_then_lose_the_ack)
    if landed:
        assert await _execute(occurrence) == Decimal("2")
        assert [(t, s) for t, s, _ in await _clearings()] == [(occurrence.occurrence_id, "COMMITTED")]
        assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("3"), ("b", "c"): Decimal("3"), ("c", "a"): Decimal("3")}
    else:
        # Not committed and not a rollback PostgreSQL reported: the sanitized error, never a retry.
        with pytest.raises(GeoException):
            await _execute(occurrence)
        assert await _clearings() == []
    assert commits["seen"] == 1


@pytest.mark.asyncio
async def test_v2_committed_after_cancellation_carries_the_occurrence_id_and_declared_amount(monkeypatch) -> None:
    from app.core.clearing.service import ClearingCommittedAfterCancellation

    api = slice_b_surface()
    triangle, debts = await _triangle()
    occurrence = _occurrence(api, triangle, debts)
    real_commit = AsyncSession.commit
    committed, release = asyncio.Event(), asyncio.Event()
    state = {"armed": True}

    async def _commit_then_hold_the_ack(session):
        await real_commit(session)
        if state["armed"] and not committed.is_set():
            state["armed"] = False
            committed.set()
            await release.wait()

    monkeypatch.setattr(AsyncSession, "commit", _commit_then_hold_the_ack)
    task = asyncio.create_task(_execute(occurrence))
    await asyncio.wait_for(committed.wait(), timeout=10)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(ClearingCommittedAfterCancellation) as carried:
        await asyncio.wait_for(task, timeout=10)
    assert carried.value.tx_id == occurrence.occurrence_id
    assert carried.value.cleared_amount == Decimal("2")
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    # The durable occurrence answers its own replay; no second effect.
    assert await _execute(api.ClearingOccurrence(**_fields(occurrence))) == Decimal("2")
    assert len(await _clearings()) == 1
    assert await _edges(_factory(), triangle) == {("a", "b"): Decimal("3"), ("b", "c"): Decimal("3"), ("c", "a"): Decimal("3")}
