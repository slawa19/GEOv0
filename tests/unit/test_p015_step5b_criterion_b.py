"""Programme 015, step 5b: criterion (b) per operation kind, and the payment envelope version 2.

WHAT IS UNDER TEST. `app/core/ledger/reconciliation.py`, criterion (b) - THE RECORDED CHANGE EQUALS THE
RECORDED INTENT - inside the 5a verifier, in the same outcome and fingerprint as criterion (a):

    CLEARING            full recomputation from the recorded cycle pre-amounts
    PAYMENT, intent v2  full recomputation from the recorded pre-state of both directions of every pair
    PAYMENT, intent v1  structural only - never reported as a full recomputation
    INJECT              the honest subset its intent supports
    SEED, TEST_FIXTURE  not examined (5a refuses them after the baseline)

and `app/core/payments/engine.py::_read_payment_prestate`, the one batched read after the owner lock that
writes that pre-state into the envelope.

THE CORRUPTIONS. "A wrong writer" is a real application writer bent by a wrapper or a listener, as in `C6`:
it journals faithfully what it did. "Around the application" is `exec_driver_sql`, and every corruption of
a RECORDED DELTA here is COORDINATED - the entry and the debt move together - so criterion (a) stays silent
(asserted) and only (b) can see it. A corruption of the INTENT re-digests it, as a coordinated rewrite
would; the verifier does not recompute the digest at all (review round D1), so the rule is what catches it.

TIER. SQLite, the default tier. Verdicts are read on a new session.

MUTATIONS. Each test names the mutation that must turn it red; the step 5b report records the runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.core.auth.canonical import canonical_json
from app.core.clearing.service import ClearingService
from app.core.ledger.reconciliation import (
    CRITERION_A,
    CRITERION_B,
    FAILED,
    PASSED,
)
from app.core.payments.engine import PaymentEngine
from app.db.journal_tables import (
    PAYMENT_INTENT_ENCODING_VERSION,
    debt_journal_entries,
    debt_operation_equivalents,
    debt_operations,
)
from app.db.models.debt import Debt
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import (
    ATOM,
    _audit,
    _collapse_the_route,
    _drop_triangle,
    _edges,
    _prepare_payment,
    _seed_triangle,
    _tx_state,
    _under_clear_by_one_atom,
)
from tests.unit.test_p015_step5a_reconciliation import (
    CHECKPOINT_CHECKS,
    _around_the_application,
    _baseline,
    _fixture_debts,
    _literal,
    _pay,
    _result_rows,
    _results,
    _run_once,
    _scheduled_run,
    _verify,
)


# ==============================================================================================
# Stand helpers
# ==============================================================================================


def _a_findings(outcome) -> list[dict]:
    return [f for f in outcome.findings if not str(f["kind"]).startswith("b_")]


def _b_findings(outcome) -> list[dict]:
    return [f for f in outcome.findings if str(f["kind"]).startswith("b_")]


def _coverage(outcome) -> dict:
    return outcome.detail()["criterion_b"]["coverage"]


def _decoded(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


async def _operation(factory, *, equivalent_id, kind: str) -> SimpleNamespace:
    """The ONE envelope of `kind` that named this equivalent."""

    ops, named = debt_operations.c, debt_operation_equivalents.c
    async with factory() as session:
        rows = (
            await session.execute(
                select(ops.id, ops.tx_id, ops.intent, ops.intent_digest, ops.intent_encoding_version)
                .join(debt_operation_equivalents, named.operation_id == ops.id)
                .where(named.equivalent_id == equivalent_id, ops.kind == kind)
            )
        ).all()
    assert len(rows) == 1, f"stand: expected one {kind} envelope in this equivalent, found {rows}"
    row = rows[0]
    return SimpleNamespace(
        id=row.id,
        tx_id=row.tx_id,
        intent=_decoded(row.intent),
        intent_digest=row.intent_digest,
        version=row.intent_encoding_version,
    )


async def _operation_entries(factory, operation_id) -> list[SimpleNamespace]:
    columns = debt_journal_entries.c
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    columns.id,
                    columns.flush_ordinal,
                    columns.equivalent_id,
                    columns.debtor_id,
                    columns.creditor_id,
                    columns.effect,
                    columns.amount_before,
                    columns.amount_after,
                    columns.delta,
                ).where(columns.operation_id == operation_id)
            )
        ).all()
    return [SimpleNamespace(**row._mapping) for row in rows]


async def _debt_id(factory, equivalent_id, debtor_id, creditor_id):
    async with factory() as session:
        return (
            await session.execute(
                select(Debt.id).where(
                    Debt.equivalent_id == equivalent_id,
                    Debt.debtor_id == debtor_id,
                    Debt.creditor_id == creditor_id,
                )
            )
        ).scalar_one()


def _text(amount: Decimal) -> str:
    return f"{Decimal(amount).quantize(ATOM):f}"


async def _rewrite_intent(factory, operation_id, intent: dict, *, version: int | None = None) -> None:
    """Replace a stored intent around the application, WITH a matching digest (a coordinated rewrite)."""

    canonical = canonical_json(intent)
    digest = hashlib.sha256(canonical).hexdigest()
    body = canonical.decode("utf-8")
    assert "'" not in body, "stand: the literal below does not escape quotes"
    version_sql = "" if version is None else f", intent_encoding_version = {int(version)}"
    await _around_the_application(
        factory,
        lambda d: (
            f"UPDATE debt_operations SET intent = '{body}', intent_digest = '{digest}'{version_sql} "
            f"WHERE id = '{_literal(d, operation_id)}'"
        ),
    )


async def _move_entry_and_debt(factory, entry, *, new_after: Decimal, debt_id) -> None:
    """Coordinated: an `U`/`I` entry ends at `new_after` instead, and the debt holds `new_after` too."""

    before = Decimal(0) if entry.amount_before is None else Decimal(entry.amount_before)
    delta = new_after - before
    await _around_the_application(
        factory,
        lambda d: (
            f"UPDATE debt_journal_entries SET amount_after = '{_text(new_after)}', delta = '{_text(delta)}' "
            f"WHERE id = '{_literal(d, entry.id)}'"
        ),
    )
    await _around_the_application(
        factory,
        lambda d: f"UPDATE debts SET amount = '{_text(new_after)}' WHERE id = '{_literal(d, debt_id)}'",
    )


async def _add_entry_and_debt(factory, operation_id, equivalent_id, debtor_id, creditor_id, amount: Decimal):
    """Coordinated: a new debt, and an `I` entry for it inside an existing operation."""

    entry_id, debt_id = uuid.uuid4(), uuid.uuid4()
    await _around_the_application(
        factory,
        lambda d: (
            "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) VALUES "
            f"('{_literal(d, debt_id)}', '{_literal(d, debtor_id)}', '{_literal(d, creditor_id)}', "
            f"'{_literal(d, equivalent_id)}', '{_text(amount)}', 0)"
        ),
    )
    await _around_the_application(
        factory,
        lambda d: (
            "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, equivalent_id, debtor_id, "
            "creditor_id, effect, amount_before, amount_after, delta) VALUES "
            f"('{_literal(d, entry_id)}', '{_literal(d, operation_id)}', 99, '{_literal(d, equivalent_id)}', "
            f"'{_literal(d, debtor_id)}', '{_literal(d, creditor_id)}', 'I', NULL, '{_text(amount)}', "
            f"'{_text(amount)}')"
        ),
    )


def _edge_of(entries, debtor_id, creditor_id):
    (entry,) = [e for e in entries if (e.debtor_id, e.creditor_id) == (debtor_id, creditor_id)]
    return entry


def _kinds(findings) -> set[tuple]:
    return {(f["kind"], f.get("debtor_id"), f.get("creditor_id")) for f in findings}


# ==============================================================================================
# PAYMENT, intent version 2: full recomputation
# ==============================================================================================


@pytest.mark.asyncio
async def test_step5b_an_honest_payment_records_both_directions_and_is_recomputed_in_full(db_session) -> None:
    """A payment over a MUTUAL pair (A owes B 10, B owes A 7), paying A -> B -> C 5: PASSED, fully.

    WHY THE MUTUAL PAIR. It is the case the flows alone cannot explain: the engine reduces B -> A first
    (7 -> 2), then nets the pair (A -> B 10 -> 8, B -> A gone). The recomputation must reproduce exactly
    that from the recorded pre-state.

    NON-VACUITY: the envelope is version 2 and records all FOUR directions of the two pairs, zeros
    included; the journal holds four entries over three flushes; coverage says one PAYMENT was fully
    recomputed.

    MUTATION: drop the netting step from `reconciliation._apply_payment_flow` - the recomputation leaves
    A -> B at 10 and this honest payment goes FAILED.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("a", "b", "100"), ("c", "b", "100")])
    try:
        await _fixture_debts(factory, triangle, [("a", "b", "10"), ("b", "a", "7")])
        await _baseline(factory, triangle.equivalent.id)

        tx_id = await _pay(factory, triangle, ["a", "b", "c"], "5")
        assert await _tx_state(factory, tx_id) == "COMMITTED"
        assert await _edges(factory, triangle) == {
            ("a", "b"): Decimal("8.00000000"),
            ("b", "c"): Decimal("5.00000000"),
        }, "stand: the payment did not net the mutual pair"

        envelope = await _operation(factory, equivalent_id=triangle.equivalent.id, kind="PAYMENT")
        assert envelope.version == PAYMENT_INTENT_ENCODING_VERSION == 2, envelope
        prestate = {
            (triangle.name(uuid.UUID(p["debtor"])), triangle.name(uuid.UUID(p["creditor"]))): p["amount"]
            for p in envelope.intent["prestate"]
        }
        assert prestate == {
            ("a", "b"): "10.00000000",
            ("b", "a"): "7.00000000",
            ("b", "c"): "0.00000000",
            ("c", "b"): "0.00000000",
        }, envelope.intent["prestate"]
        assert len(await _operation_entries(factory, envelope.id)) == 4

        outcome = await _verify(factory, triangle.equivalent.id)
        assert outcome.status == PASSED, f"an honest payment was not PASSED by criterion (b): {outcome}"
        assert _coverage(outcome) == {
            "full_recomputation": {"PAYMENT": 1},
            "not_examined": {"TEST_FIXTURE": 1},
        }, outcome.detail()
        assert outcome.detail()["criterion_b"]["limited"] == [], outcome.detail()
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind(db_session) -> None:
    """`C6` (i): A -> B -> C journalled faithfully as one A -> C. (a) has nothing to say; (b) FAILS.

    MUTATION: skip the per-edge delta comparison in `_compare_recomputation` - only the prestate
    finding on A -> C is left and the delta assertions go red.
    """
    from tests.conftest import TestingSessionLocal as factory
    import _pytest.monkeypatch

    patch = _pytest.monkeypatch.MonkeyPatch()
    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("c", "a", "100")]
    )
    try:
        await _baseline(factory, triangle.equivalent.id)
        tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
        _collapse_the_route(patch, triangle)
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)
        patch.undo()
        assert await _edges(factory, triangle) == {("a", "c"): Decimal("5.00000000")}
        assert await _audit(factory, tx_id) == [True], "stand: C6 is no longer a writer every barrier passes"

        outcome = await _verify(factory, triangle.equivalent.id)
        assert _a_findings(outcome) == [], f"criterion (a) was expected blind to a faithful wrong writer: {outcome}"
        assert outcome.status == FAILED, outcome
        a, b, c = (str(p.id) for p in (triangle.a, triangle.b, triangle.c))
        deltas = {
            (f["debtor_id"], f["creditor_id"]): (f["expected_delta"], f["recorded_delta"])
            for f in _b_findings(outcome)
            if f["kind"] == "b_delta_mismatch"
        }
        assert deltas == {
            (a, b): ("5.00000000", "0.00000000"),
            (b, c): ("5.00000000", "0.00000000"),
            (a, c): ("0.00000000", "5.00000000"),
        }, outcome.findings
        assert _kinds(_b_findings(outcome)) >= {("b_prestate_mismatch", a, c)}, outcome.findings
    finally:
        patch.undo()
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["recorded_delta", "intent_flow", "prestate"])
async def test_step5b_a_corrupted_payment_record_is_failed(db_session, corruption) -> None:
    """A -> B 5 over A owing B 10. Each record is corrupted coordinately; (a) silent, (b) FAILED.

    * `recorded_delta` - entry and debt both end at 16 instead of 15.
    * `intent_flow` - the flow says 4, re-digested.
    * `prestate` - the recorded A -> B pre-amount says 12, re-digested. The recomputed delta is unchanged
      (+5 either way), so ONLY the comparison with the journal's first `amount_before` can see it.

    MUTATIONS: (1) skip the delta comparison - `recorded_delta` and `intent_flow` red; (2) skip the
    prestate comparison - `prestate` red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    try:
        await _fixture_debts(factory, triangle, [("a", "b", "10")])
        await _baseline(factory, triangle.equivalent.id)
        await _pay(factory, triangle, ["a", "b"], "5")
        assert (await _verify(factory, triangle.equivalent.id)).status == PASSED
        envelope = await _operation(factory, equivalent_id=triangle.equivalent.id, kind="PAYMENT")
        a, b = str(triangle.a.id), str(triangle.b.id)

        if corruption == "recorded_delta":
            (entry,) = await _operation_entries(factory, envelope.id)
            await _move_entry_and_debt(
                factory,
                entry,
                new_after=Decimal("16"),
                debt_id=await _debt_id(factory, triangle.equivalent.id, triangle.a.id, triangle.b.id),
            )
            expected = {("b_delta_mismatch", a, b)}
        elif corruption == "intent_flow":
            intent = copy.deepcopy(envelope.intent)
            intent["locks"][0]["flows"][0]["amount"] = "4.00000000"
            await _rewrite_intent(factory, envelope.id, intent)
            expected = {("b_delta_mismatch", a, b)}
        else:
            intent = copy.deepcopy(envelope.intent)
            for item in intent["prestate"]:
                if (item["debtor"], item["creditor"]) == (a, b):
                    item["amount"] = "12.00000000"
            await _rewrite_intent(factory, envelope.id, intent)
            expected = {("b_prestate_mismatch", a, b)}

        outcome = await _verify(factory, triangle.equivalent.id)
        assert _a_findings(outcome) == [], f"stand: the corruption was not coordinated: {outcome}"
        assert outcome.status == FAILED, outcome
        assert _kinds(_b_findings(outcome)) == expected, outcome.findings
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_step5b_net_neutral_cycle_inflation_on_a_payment_is_failed(db_session) -> None:
    """T1508: after A -> B -> C 5, the cycle A -> B -> C -> A is inflated by 7 in debts AND journal.

    Every participant's net position is unchanged (asserted), the journal agrees with `debts` edge by
    edge, so criterion (a) is silent (asserted). The recomputation from the envelope says +5, +5, 0.

    MUTATION: compare per-PARTICIPANT net deltas instead of per-edge deltas in `_compare_recomputation`
    (the cheaper check `check_payment_delta` already makes) - this test goes green-for-the-wrong-reason,
    i.e. red here.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100")])
    try:
        await _baseline(factory, triangle.equivalent.id)
        await _pay(factory, triangle, ["a", "b", "c"], "5")
        envelope = await _operation(factory, equivalent_id=triangle.equivalent.id, kind="PAYMENT")
        entries = await _operation_entries(factory, envelope.id)
        eq = triangle.equivalent.id

        def _net(edges: dict) -> dict:
            net = {"a": Decimal(0), "b": Decimal(0), "c": Decimal(0)}
            for (debtor, creditor), amount in edges.items():
                net[debtor] -= amount
                net[creditor] += amount
            return net

        net_before = _net(await _edges(factory, triangle))
        for debtor, creditor in (("a", "b"), ("b", "c")):
            d, c = getattr(triangle, debtor).id, getattr(triangle, creditor).id
            await _move_entry_and_debt(
                factory, _edge_of(entries, d, c), new_after=Decimal("12"), debt_id=await _debt_id(factory, eq, d, c)
            )
        await _add_entry_and_debt(factory, envelope.id, eq, triangle.c.id, triangle.a.id, Decimal("7"))

        assert _net(await _edges(factory, triangle)) == net_before, "stand: the inflation is not net-neutral"
        outcome = await _verify(factory, eq)
        assert _a_findings(outcome) == [], f"stand: criterion (a) saw the coordinated inflation: {outcome}"
        assert outcome.status == FAILED, outcome
        a, b, c = (str(p.id) for p in (triangle.a, triangle.b, triangle.c))
        assert {k for k in _kinds(_b_findings(outcome)) if k[0] == "b_delta_mismatch"} == {
            ("b_delta_mismatch", a, b),
            ("b_delta_mismatch", b, c),
            ("b_delta_mismatch", c, a),
        }, outcome.findings
    finally:
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# PAYMENT, intent version 1: structural only
# ==============================================================================================


async def _downgrade_to_v1(factory, envelope) -> None:
    """The envelope as the pre-5b writer left it: the same intent without `prestate`, version 1."""

    intent = {key: value for key, value in envelope.intent.items() if key != "prestate"}
    assert set(intent) == {"tx_id", "locks"}, intent
    await _rewrite_intent(factory, envelope.id, intent, version=1)


@pytest.mark.asyncio
async def test_step5b_a_v1_payment_is_structural_only_and_never_a_full_recomputation(db_session) -> None:
    """A v1 payment: PASSED as STRUCTURE, recorded as `structural_only`, and its limit is real.

    NEGATIVE HALF, stated rather than hidden: a coordinated corruption of the v1 payment's delta on its own
    flow edge stays PASSED - nothing recomputes a v1 payment, and the result says so in `limited`.

    MUTATIONS: (1) read `("PAYMENT", 1)` as `full_recomputation` in `_READABLE_ENVELOPES` - red on the
    coverage; (2) route v1 through `_payment_v2` - a malformed-prestate finding, red on the status.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    try:
        await _fixture_debts(factory, triangle, [("a", "b", "10")])
        await _baseline(factory, triangle.equivalent.id)
        await _pay(factory, triangle, ["a", "b"], "5")
        v2 = await _verify(factory, triangle.equivalent.id)
        assert v2.status == PASSED and _coverage(v2)["full_recomputation"] == {"PAYMENT": 1}, v2

        envelope = await _operation(factory, equivalent_id=triangle.equivalent.id, kind="PAYMENT")
        await _downgrade_to_v1(factory, envelope)
        assert (await _operation(factory, equivalent_id=triangle.equivalent.id, kind="PAYMENT")).version == 1

        v1 = await _verify(factory, triangle.equivalent.id)
        assert v1.status == PASSED, v1
        assert _coverage(v1) == {"structural_only": {"PAYMENT": 1}, "not_examined": {"TEST_FIXTURE": 1}}, (
            f"a v1 payment was reported as something other than structural only: {v1.detail()}"
        )
        assert v1.detail()["criterion_b"]["limited"] == ["structural_only:PAYMENT"], v1.detail()
        assert v1.fingerprint() != v2.fingerprint(), "the limit of (b) did not enter the fingerprint"

        (entry,) = await _operation_entries(factory, envelope.id)
        await _move_entry_and_debt(
            factory,
            entry,
            new_after=Decimal("16"),
            debt_id=await _debt_id(factory, triangle.equivalent.id, triangle.a.id, triangle.b.id),
        )
        limit = await _verify(factory, triangle.equivalent.id)
        assert limit.status == PASSED and limit.findings == (), (
            f"the structural check claimed more than structure: {limit}"
        )
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_step5b_a_v1_payment_with_an_edge_outside_its_flow_pairs_is_failed(db_session) -> None:
    """The structure a v1 payment CAN be held to: every journalled edge is a direction of a flow pair.

    MUTATION: return no findings from `_payment_v1` - red.
    """
    from tests.conftest import TestingSessionLocal as factory
    import _pytest.monkeypatch

    patch = _pytest.monkeypatch.MonkeyPatch()
    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("c", "a", "100")]
    )
    try:
        tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
        _collapse_the_route(patch, triangle)
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)
        patch.undo()
        await _downgrade_to_v1(factory, await _operation(factory, equivalent_id=triangle.equivalent.id, kind="PAYMENT"))

        outcome = await _verify(factory, triangle.equivalent.id)
        assert outcome.status == FAILED, outcome
        assert outcome.missing_evidence == ("baseline",), "stand: (b) must be conclusive without a baseline"
        assert _kinds(_b_findings(outcome)) == {
            ("b_payment_v1_structure", str(triangle.a.id), str(triangle.c.id))
        }, outcome.findings
    finally:
        patch.undo()
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# CLEARING: full recomputation
# ==============================================================================================


_CYCLE = [("a", "b", "10"), ("b", "c", "20"), ("c", "a", "30")]


async def _clear_cycle(factory, debts) -> Decimal:
    async with factory() as session:
        cleared = await ClearingService(session).execute_clearing_with_amount(
            [{"debt_id": str(debt.id)} for debt in debts]
        )
    return cleared


async def _cycle_triangle(factory):
    return await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100")])


@pytest.mark.asyncio
async def test_step5b_an_honest_clearing_is_recomputed_in_full_and_passed(db_session) -> None:
    """10/20/30 cleared by 10: A -> B deleted, B -> C 10, C -> A 20. PASSED, fully recomputed.

    MUTATION: recompute with `clear_amount` from the intent MINUS one atom instead of the cycle minimum -
    red (and the C6 (ii) test below turns green-for-the-wrong-reason).
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _cycle_triangle(factory)
    try:
        debts = await _fixture_debts(factory, triangle, _CYCLE)
        await _baseline(factory, triangle.equivalent.id)
        assert await _clear_cycle(factory, debts) == Decimal("10")
        assert await _edges(factory, triangle) == {
            ("b", "c"): Decimal("10.00000000"),
            ("c", "a"): Decimal("20.00000000"),
        }
        outcome = await _verify(factory, triangle.equivalent.id)
        assert outcome.status == PASSED, outcome
        assert _coverage(outcome) == {"full_recomputation": {"CLEARING": 1}, "not_examined": {"TEST_FIXTURE": 1}}
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_step5b_the_c6_under_clearing_is_failed_by_b_while_a_stays_blind(db_session) -> None:
    """`C6` (ii): one atom left on every edge of a 10/10/10 cycle, journalled faithfully.

    MUTATION: skip the delta comparison in `_compare_recomputation` - red.
    """
    from tests.conftest import TestingSessionLocal as factory
    import _pytest.monkeypatch

    patch = _pytest.monkeypatch.MonkeyPatch()
    triangle = await _cycle_triangle(factory)
    remove_listener = None
    try:
        debts = await _fixture_debts(factory, triangle, [("a", "b", "10"), ("b", "c", "10"), ("c", "a", "10")])
        await _baseline(factory, triangle.equivalent.id)
        armed, remove_listener = _under_clear_by_one_atom(patch)
        async with factory() as session:
            await ClearingService(session).execute_clearing_with_amount([{"debt_id": str(d.id)} for d in debts])
        remove_listener()
        remove_listener = None
        patch.undo()
        assert armed["hits"] == 3 and len(await _edges(factory, triangle)) == 3, "stand: the skim did not fire"

        outcome = await _verify(factory, triangle.equivalent.id)
        assert _a_findings(outcome) == [], outcome
        assert outcome.status == FAILED, outcome
        mismatches = [f for f in _b_findings(outcome) if f["kind"] == "b_delta_mismatch"]
        assert len(mismatches) == 3, outcome.findings
        assert {(f["expected_delta"], f["recorded_delta"]) for f in mismatches} == {
            ("-10.00000000", "-9.99999999")
        }, mismatches
    finally:
        if remove_listener is not None:
            remove_listener()
        patch.undo()
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption", ["recorded_delta", "clear_amount", "prestate", "cycle_not_closed", "cycle_inflation"]
)
async def test_step5b_a_corrupted_clearing_record_is_failed(db_session, corruption) -> None:
    """The 10/20/30 clearing, each record corrupted coordinately; (a) silent, (b) FAILED for its reason.

    * `recorded_delta` - B -> C ends at 11 in entry and debt.
    * `clear_amount` - the intent says 9, re-digested; the rule is the cycle minimum.
    * `prestate` - C -> A's recorded pre-amount says 31, re-digested; the minimum is still 10, so only the
      journal's first `amount_before` disagrees.
    * `cycle_not_closed` - C -> A is recorded as C -> B, re-digested.
    * `cycle_inflation` (T1508) - every edge holds 7 more in debts and journal; net positions unchanged.

    MUTATIONS: (1) no delta comparison - `recorded_delta`, `cycle_inflation` red; (2) no minimum check -
    `clear_amount` red; (3) no prestate comparison - `prestate` red; (4) no closure check -
    `cycle_not_closed` red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _cycle_triangle(factory)
    try:
        debts = await _fixture_debts(factory, triangle, _CYCLE)
        await _baseline(factory, triangle.equivalent.id)
        await _clear_cycle(factory, debts)
        assert (await _verify(factory, triangle.equivalent.id)).status == PASSED
        eq = triangle.equivalent.id
        envelope = await _operation(factory, equivalent_id=eq, kind="CLEARING")
        entries = await _operation_entries(factory, envelope.id)
        a, b, c = triangle.a.id, triangle.b.id, triangle.c.id

        if corruption == "recorded_delta":
            await _move_entry_and_debt(
                factory, _edge_of(entries, b, c), new_after=Decimal("11"), debt_id=await _debt_id(factory, eq, b, c)
            )
            expected = {("b_delta_mismatch", str(b), str(c))}
            reasons = set()
        elif corruption == "clear_amount":
            intent = copy.deepcopy(envelope.intent)
            intent["clear_amount"] = "9.00000000"
            await _rewrite_intent(factory, envelope.id, intent)
            expected = {("b_intent_malformed", None, None)}
            reasons = {"clear_amount_is_not_the_cycle_minimum"}
        elif corruption == "prestate":
            intent = copy.deepcopy(envelope.intent)
            for item in intent["cycle"]:
                if (item["debtor_id"], item["creditor_id"]) == (str(c), str(a)):
                    item["amount"] = "31.00000000"
            await _rewrite_intent(factory, envelope.id, intent)
            expected = {("b_prestate_mismatch", str(c), str(a))}
            reasons = set()
        elif corruption == "cycle_not_closed":
            intent = copy.deepcopy(envelope.intent)
            for item in intent["cycle"]:
                if (item["debtor_id"], item["creditor_id"]) == (str(c), str(a)):
                    item["creditor_id"] = str(b)
            await _rewrite_intent(factory, envelope.id, intent)
            expected = None
            reasons = {"clearing_cycle_is_not_closed"}
        else:
            # A -> B was deleted by the clearing: its `D` entry becomes an `U` ending at 7, and the debt
            # comes back holding 7. The other two end 7 higher.
            deleted = _edge_of(entries, a, b)
            debt_id = uuid.uuid4()
            await _around_the_application(
                factory,
                lambda d: (
                    "UPDATE debt_journal_entries SET effect = 'U', amount_after = '7.00000000', "
                    f"delta = '-3.00000000' WHERE id = '{_literal(d, deleted.id)}'"
                ),
            )
            await _around_the_application(
                factory,
                lambda d: (
                    "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) VALUES "
                    f"('{_literal(d, debt_id)}', '{_literal(d, a)}', '{_literal(d, b)}', '{_literal(d, eq)}', "
                    "'7.00000000', 0)"
                ),
            )
            for debtor, creditor, after in ((b, c, "17"), (c, a, "27")):
                await _move_entry_and_debt(
                    factory,
                    _edge_of(entries, debtor, creditor),
                    new_after=Decimal(after),
                    debt_id=await _debt_id(factory, eq, debtor, creditor),
                )
            expected = {
                ("b_delta_mismatch", str(a), str(b)),
                ("b_delta_mismatch", str(b), str(c)),
                ("b_delta_mismatch", str(c), str(a)),
            }
            reasons = set()

        outcome = await _verify(factory, eq)
        assert _a_findings(outcome) == [], f"stand: the corruption was not coordinated: {outcome}"
        assert outcome.status == FAILED, outcome
        found_reasons = {f.get("reason") for f in _b_findings(outcome) if f["kind"] == "b_intent_malformed"}
        assert reasons <= found_reasons, outcome.findings
        if expected is not None:
            assert _kinds(_b_findings(outcome)) == expected, outcome.findings
        if corruption == "clear_amount":
            assert found_reasons == reasons, outcome.findings
    finally:
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# INJECT: the honest subset
# ==============================================================================================


async def _inject_world(db_session, *, inject_amount: str = "1.00"):
    from tests.unit.test_p015_t1514_simulator_must_not_requantise_stored_money import _scenario, _seed
    from tests.unit.test_scenario_inject_topology import _make_run, _make_runner

    eq, creditor, debtor = await _seed(db_session, existing_amount=Decimal("5.00000000"), limit=Decimal("100.00"))
    await db_session.commit()
    run = _make_run(
        participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid)],
        equivalents=[str(eq.code)],
    )
    scenario = _scenario(eq, creditor, debtor, inject_amount=inject_amount, limit="100.00")
    runner, _artifacts = _make_runner(inject_enabled=True)
    return SimpleNamespace(eq=eq, creditor=creditor, debtor=debtor, run=run, scenario=scenario, runner=runner)


async def _apply_inject(db_session, world) -> None:
    await world.runner._apply_due_scenario_events(
        db_session, run_id="r-step5b", run=world.run, scenario=world.scenario
    )


@pytest.mark.asyncio
async def test_step5b_an_honest_inject_is_checked_as_its_subset_and_passed(db_session) -> None:
    """A real inject of 1.00 onto a debt of 5.00: PASSED, recorded as `subset`, never as full.

    MUTATION: record INJECT as `full_recomputation` in `_READABLE_ENVELOPES` - red on the coverage.
    """
    from tests.conftest import TestingSessionLocal as factory

    world = await _inject_world(db_session)
    await _baseline(factory, world.eq.id)
    await _apply_inject(db_session, world)
    envelope = await _operation(factory, equivalent_id=world.eq.id, kind="INJECT")
    assert envelope.version == 1, envelope
    (entry,) = await _operation_entries(factory, envelope.id)
    assert (entry.effect, Decimal(entry.delta)) == ("U", Decimal("1.00000000")), "stand: the inject did not apply"

    outcome = await _verify(factory, world.eq.id)
    assert outcome.status == PASSED, outcome
    assert _coverage(outcome) == {"subset": {"INJECT": 1}, "not_examined": {"TEST_FIXTURE": 1}}, outcome.detail()
    assert outcome.detail()["criterion_b"]["limited"] == ["subset:INJECT"], outcome.detail()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["atom_writer", "intent_amount", "split_edge", "decrease"])
async def test_step5b_an_inject_outside_its_subset_is_failed(db_session, corruption) -> None:
    """Each rule of the INJECT subset, reddened alone.

    * `atom_writer` - a listener takes one atom off while the inject stages: `delta_is_not_whole_cents`
      alone (one atom MORE would also exceed the named total and trip a second rule).
    * `intent_amount` - the effect says 0.50, re-digested: `more_debt_than_the_intent_names`.
    * `split_edge` - coordinated: 0.50 on the named edge and 0.50 on the reverse edge:
      `more_edges_than_inject_debt_effects`.
    * `decrease` - coordinated: the entry and the debt go 5 -> 4: `entry_is_not_an_increase`.

    MUTATIONS: remove each rule from `_inject_subset` - its own case goes red.
    """
    from tests.conftest import TestingSessionLocal as factory
    import _pytest.monkeypatch

    patch = _pytest.monkeypatch.MonkeyPatch()
    world = await _inject_world(db_session)
    await _baseline(factory, world.eq.id)
    listener = None
    try:
        if corruption == "atom_writer":
            armed = {"on": False}
            original = world.runner._inject_executor.stage_inject_event

            def _skim(_target, value, _old, _initiator):
                return value - ATOM if armed["on"] else value

            async def _wrapper(*args, **kwargs):
                armed["on"] = True
                try:
                    return await original(*args, **kwargs)
                finally:
                    armed["on"] = False

            listener = _skim
            event.listen(Debt.amount, "set", _skim, retval=True)
            patch.setattr(world.runner._inject_executor, "stage_inject_event", _wrapper)
        await _apply_inject(db_session, world)
        if listener is not None:
            event.remove(Debt.amount, "set", listener)
            listener = None
        patch.undo()

        envelope = await _operation(factory, equivalent_id=world.eq.id, kind="INJECT")
        (entry,) = await _operation_entries(factory, envelope.id)
        debtor, creditor = world.debtor.id, world.creditor.id
        debt_id = await _debt_id(factory, world.eq.id, debtor, creditor)
        if corruption == "atom_writer":
            assert Decimal(entry.delta) == Decimal("0.99999999"), "stand: the listener did not fire"
            rule = "delta_is_not_whole_cents"
        elif corruption == "intent_amount":
            intent = copy.deepcopy(envelope.intent)
            intent["effects"][0]["amount"] = "0.50"
            await _rewrite_intent(factory, envelope.id, intent)
            rule = "more_debt_than_the_intent_names"
        elif corruption == "split_edge":
            await _move_entry_and_debt(factory, entry, new_after=Decimal("5.50"), debt_id=debt_id)
            await _add_entry_and_debt(factory, envelope.id, world.eq.id, creditor, debtor, Decimal("0.50"))
            rule = "more_edges_than_inject_debt_effects"
        else:
            await _move_entry_and_debt(factory, entry, new_after=Decimal("4"), debt_id=debt_id)
            rule = "entry_is_not_an_increase"

        outcome = await _verify(factory, world.eq.id)
        assert _a_findings(outcome) == [], f"stand: the corruption was not coordinated: {outcome}"
        assert outcome.status == FAILED, outcome
        assert {f["rule"] for f in _b_findings(outcome)} == {rule}, outcome.findings
    finally:
        if listener is not None:
            event.remove(Debt.amount, "set", listener)
        patch.undo()


# ==============================================================================================
# Shared structure: digest, version, and the result
# ==============================================================================================


@pytest.mark.asyncio
async def test_step5b_the_version_split_and_the_check_on_the_metadata_path(db_session) -> None:
    """Only PAYMENT writes intent version 2; the CHECK admits 2 and refuses 3 on the create_all schema.

    Also: the schema and money versions did not move - a 2 there is refused.

    MUTATION: leave `chk_debt_operations_intent_version` at `IN (1)` in `journal_tables.py` - the payment
    commit itself is refused and this test goes red before its CHECK half.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100"), ("a", "b", "100")]
    )
    try:
        await _clear_cycle(factory, await _fixture_debts(factory, triangle, _CYCLE))
        await _pay(factory, triangle, ["b", "a"], "1")
        ops = debt_operations.c
        async with factory() as session:
            versions = {
                (kind, schema, money, intent)
                for kind, schema, money, intent in (
                    await session.execute(
                        select(ops.kind, ops.schema_version, ops.money_encoding_version, ops.intent_encoding_version)
                        .join(debt_operation_equivalents, debt_operation_equivalents.c.operation_id == ops.id)
                        .where(debt_operation_equivalents.c.equivalent_id == triangle.equivalent.id)
                    )
                ).all()
            }
        assert versions == {("PAYMENT", 1, 1, 2), ("TEST_FIXTURE", 1, 1, 1), ("CLEARING", 1, 1, 1)}, versions

        async def _insert(column: str, value: int) -> str | None:
            operation_id = uuid.uuid4()
            values = {"schema_version": 1, "money_encoding_version": 1, "intent_encoding_version": 1, column: value}
            try:
                await _around_the_application(
                    factory,
                    lambda d: (
                        "INSERT INTO debt_operations (id, kind, identity, tx_id, intent, intent_digest, "
                        "schema_version, money_encoding_version, intent_encoding_version, opened_at, state) "
                        f"VALUES ('{_literal(d, operation_id)}', 'TEST_FIXTURE', 'step5b-check-{operation_id}', NULL, "
                        f"'{{}}', '{'0' * 64}', {values['schema_version']}, {values['money_encoding_version']}, "
                        f"{values['intent_encoding_version']}, '2026-09-14T00:00:00+00:00', 'OPEN')"
                    ),
                )
            except IntegrityError as exc:
                return str(exc.orig)
            await _around_the_application(
                factory, lambda d: f"DELETE FROM debt_operations WHERE id = '{_literal(d, operation_id)}'"
            )
            return None

        assert await _insert("intent_encoding_version", 2) is None, "intent version 2 was refused"
        refused = await _insert("intent_encoding_version", 3)
        assert refused is not None and "chk_debt_operations_intent_version" in refused, refused
        for column in ("schema_version", "money_encoding_version"):
            refused = await _insert(column, 2)
            assert refused is not None and "CHECK" in refused.upper(), (column, refused)
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_step5b_a_b_finding_is_stored_in_the_same_row_and_fingerprint_and_never_in_a_checkpoint(
    db_session, monkeypatch
) -> None:
    """The scheduled host on `C6` (i): one FAILED row carrying both criteria, the checkpoint untouched; a
    later honest payment keeps the same fingerprint (a (b) finding is an immutable fault identity).

    MUTATION: hash `operations_examined` into `ReconciliationOutcome.fingerprint` - the honest payment
    inserts a second row, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("c", "a", "100")]
    )
    try:
        await _baseline(factory, triangle.equivalent.id)
        tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
        _collapse_the_route(monkeypatch, triangle)
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)
        monkeypatch.undo()

        await _scheduled_run(monkeypatch, factory)
        results = await _results(factory, triangle.equivalent.id)
        assert [status for status, _ in results] == [FAILED], results
        detail = results[0][1]
        assert detail["criterion"] == CRITERION_A and detail["criterion_b"]["criterion"] == CRITERION_B, detail
        assert {f["kind"] for f in detail["findings"]} >= {"b_delta_mismatch"}, detail

        async with factory() as session:
            (status,) = (
                await session.execute(
                    select(IntegrityCheckpoint.invariants_status).where(
                        IntegrityCheckpoint.equivalent_id == triangle.equivalent.id
                    )
                )
            ).scalars().all()
        assert set(status["checks"]) == CHECKPOINT_CHECKS and status["passed"] is True, status
        assert "reconcil" not in json.dumps(status).lower() and await _audit(factory, tx_id) == [True]

        (first,) = await _result_rows(factory, triangle.equivalent.id)
        await _pay(factory, triangle, ["a", "b"], "1")
        counts = await _run_once(factory, triangle.equivalent.id)
        assert (counts[FAILED], counts["rows_inserted"], counts["rows_unchanged"]) == (1, 0, 1), counts
        (row,) = await _result_rows(factory, triangle.equivalent.id)
        assert row.fingerprint == first.fingerprint, "an honest payment changed the (b) fault identity"
    finally:
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# The pre-state read: placement and cost on the commit path
# ==============================================================================================


@pytest.mark.asyncio
async def test_step5b_the_prestate_is_one_read_after_the_operator_stop_and_before_the_envelope(db_session) -> None:
    """ANCHORED, not merely present. On a two-hop payment (two pairs, four directions), the statements
    sent between the operator-stop read of `equivalents` and `INSERT INTO debt_operations` are EXACTLY one,
    and it reads `debts`. The TTL read of `prepare_locks` precedes the stop, as T1544 fixed it.

    MUTATIONS: (1) move `_read_payment_prestate` above the TTL branch - nothing between the anchors, red;
    (2) read each direction with its own SELECT - four statements, red.
    """
    from tests.conftest import TestingSessionLocal as factory
    from tests.conftest import engine

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100")])
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(" ".join(str(statement).split()).upper())

    try:
        tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
        event.listen(engine.sync_engine, "before_cursor_execute", _record)
        try:
            async with factory() as session:
                await PaymentEngine(session).commit(tx_id)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _record)
        assert await _tx_state(factory, tx_id) == "COMMITTED"

        stops = [i for i, s in enumerate(statements) if s.startswith("SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE")]
        envelopes = [i for i, s in enumerate(statements) if s.startswith("INSERT INTO DEBT_OPERATIONS")]
        # The TTL branch's own read: the only statement comparing `expires_at` with the database clock.
        ttl = [i for i, s in enumerate(statements) if "PREPARE_LOCKS.EXPIRES_AT <=" in s]
        assert len(stops) == 1 and len(envelopes) == 1 and len(ttl) == 1, (
            f"premise: the anchors were not each seen once: stop={stops} envelope={envelopes} ttl={ttl}\n"
            + "\n".join(statements)
        )
        assert ttl[0] < stops[0] < envelopes[0], (ttl, stops, envelopes)
        between = statements[stops[0] + 1 : envelopes[0]]
        assert len(between) == 1 and " FROM DEBTS " in f"{between[0]} " and between[0].startswith("SELECT"), (
            f"expected exactly one batched read of debts between the operator stop and the envelope: {between}"
        )
    finally:
        await _drop_triangle(factory, triangle)
