"""030 S3a: the reconciliation's reaction on states no supported writer leaves (F-030-20, decision B) and on a
money INJECT envelope (T3000 decision DELETE), plus the anti-vacuum of the transaction correspondence (F-030-5).

Every corrupted state is written AROUND the application by THE named corruption helper (`tests/ledger_corruption.py`)
and COORDINATED, so criterion (a) stays silent: a structural debt arrives with the baseline offset that adopts it (the
equivalent's baseline is taken at creation, 030 F-030-9), a money INJECT with its envelope and journal entry. The hold is the existing confirm-then-hold path (`run_scheduled_reconciliation` -> `react_to_failed`); the
proof that money stops is a payment on a pair the corruption does not touch, refused for the hold by name.
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from app.core.ledger import reconciliation as rec
from app.core.ledger.reconciliation import FAILED, PASSED, run_scheduled_reconciliation
from app.core.money_boundary import MoneyBoundary
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from tests.integration.p019_stand import api, build_api_world, factory, payment_body  # noqa: F401 - fixtures
from tests.integration.test_scenarios import _sign_trustline_create_request
from tests.ledger_corruption import corrupt

HOLD = MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON


async def _line(api, world, truster: dict, to: dict, limit: str = "100.00") -> None:  # noqa: F811
    key = SigningKey(base64.b64decode(truster["priv"]))
    signature = _sign_trustline_create_request(signing_key=key, to_pid=to["pid"], equivalent=world.code, limit=limit)
    body = {"to": to["pid"], "equivalent": world.code, "limit": limit, "signature": signature}
    resp = await api.post("/api/v1/trustlines", json=body, headers=truster["headers"])
    assert resp.status_code == 201, resp.text


async def _pay(api, world, sender: dict, receiver: dict, amount: str):  # noqa: F811
    return await api.post("/api/v1/payments", json=payment_body(world, sender, receiver, amount), headers=sender["headers"])


def _committed(resp) -> bool:
    return resp.status_code == 200 and resp.json().get("status") == "COMMITTED"


async def _around(factory, statements: list[str]) -> None:  # noqa: F811
    await corrupt(factory.kw["bind"].url.render_as_string(hide_password=False), statements)


async def _verify(factory, equivalent_id):  # noqa: F811
    async with factory() as s:
        await rec.open_verification_snapshot(s)
        outcome = await rec.verify_journal_equals_change(s, equivalent_id)
        await s.rollback()
    return outcome


async def _hold(factory, equivalent_id):  # noqa: F811
    async with factory() as s:
        return (await s.execute(select(Equivalent.integrity_hold_result_id).where(Equivalent.id == equivalent_id))).scalar()


async def _two_worlds(api, factory):  # noqa: F811
    """E with Alice owing Bob 10.00, and a neighbour F over the same people; Carol trusts Alice in both."""

    e = await build_api_world(api, factory)
    f = await build_api_world(api, factory, people=e)
    assert _committed(await _pay(api, e, e.alice, e.bob, "10.00"))
    return e, f


def _debt_sql(world, debtor: dict, creditor: dict, amount: str) -> list[str]:
    """A debt and the baseline offset adopting it: the state an import left before 030 S2, invisible to (a)."""

    edge = f"'{world.equivalent_id}', '{world.ids[debtor['pid']]}', '{world.ids[creditor['pid']]}'"
    return [f"INSERT INTO debts (id, equivalent_id, debtor_id, creditor_id, amount, version) "
            f"VALUES ('{uuid.uuid4()}', {edge}, {amount}, 0)",
            f"INSERT INTO debt_reconciliation_baseline_offsets (equivalent_id, debtor_id, creditor_id, offset_amount) "
            f"VALUES ({edge}, {amount})"]


async def _scheduled_hold_stops_only_e(api, factory, e, f) -> None:  # noqa: F811
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[e.equivalent_id, f.equivalent_id])
    assert (counts[FAILED], counts[PASSED], counts["error"], counts["hold_set"]) == (1, 1, 0, 1), counts
    assert await _hold(factory, e.equivalent_id) is not None and await _hold(factory, f.equivalent_id) is None
    refused = await _pay(api, e, e.alice, e.carol, "1.00")
    assert not _committed(refused) and HOLD in refused.text, refused.text
    assert _committed(await _pay(api, f, f.alice, f.carol, "1.00")), "the neighbour equivalent stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["counter_debt", "debt_without_a_live_line"])
async def test_s3a_a_structural_state_is_failed_confirmed_and_holds_only_its_equivalent(api, factory, shape) -> None:  # noqa: F811
    e, f = await _two_worlds(api, factory)
    if shape == "counter_debt":  # Bob owes Alice too, on a live line Alice -> Bob: only the symmetry is broken
        await _line(api, e, e.alice, e.bob)
        await _around(factory, _debt_sql(e, e.bob, e.alice, "1.00"))
        kind = "debt_counter_debt"
    else:  # Carol owes Bob; Bob never trusted Carol
        await _around(factory, _debt_sql(e, e.carol, e.bob, "1.00"))
        kind = "debt_without_a_live_line"

    outcome = await _verify(factory, e.equivalent_id)
    assert outcome.status == FAILED, outcome
    assert {x["kind"] for x in outcome.findings} == {kind}, outcome.findings
    await _scheduled_hold_stops_only_e(api, factory, e, f)


@pytest.mark.asyncio
async def test_s3a_a_debt_above_a_lowered_limit_is_allowed_and_holds_nothing(api, factory) -> None:  # noqa: F811
    e, f = await _two_worlds(api, factory)
    await _around(factory, [f"UPDATE trust_lines SET \"limit\" = 5 WHERE equivalent_id = '{e.equivalent_id}' "
                            f"AND to_participant_id = '{e.ids[e.alice['pid']]}' "
                            f"AND from_participant_id = '{e.ids[e.bob['pid']]}'"])
    assert (await _verify(factory, e.equivalent_id)).status == PASSED
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[e.equivalent_id])
    assert (counts[PASSED], counts["hold_set"]) == (1, 0), counts
    assert _committed(await _pay(api, e, e.alice, e.carol, "1.00"))


@pytest.mark.asyncio
async def test_s3a_an_error_of_the_structural_check_never_holds(api, factory, monkeypatch) -> None:  # noqa: F811
    """A counter-debt that WOULD hold, and the check itself breaks: an error, no result row, no hold, money moves."""

    e, f = await _two_worlds(api, factory)
    await _line(api, e, e.alice, e.bob)
    await _around(factory, _debt_sql(e, e.bob, e.alice, "1.00"))

    async def _broken(self, **_kw):
        raise RuntimeError("the structural check itself failed")

    monkeypatch.setattr(rec.InvariantChecker, "check_debt_symmetry", _broken)
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[e.equivalent_id])
    assert (counts["error"], counts[FAILED], counts["hold_set"]) == (1, 0, 0), counts
    assert await _hold(factory, e.equivalent_id) is None
    monkeypatch.undo()  # the payment path runs the same checker
    assert _committed(await _pay(api, e, e.alice, e.carol, "1.00"))


def _inject_envelope_sql(world, *, with_money: bool) -> list[str]:
    op, digest = uuid.uuid4(), "0" * 64
    intent = ('{"run_id": "historical", "event_index": 0, "effects": [{"op": "inject_debt", '
              f'"equivalent": "{world.code}", "amount": "1.00"}}]}}')
    statements = [
        "INSERT INTO debt_operations (id, kind, identity, tx_id, intent, intent_digest, schema_version, "
        "money_encoding_version, intent_encoding_version, state, completed_at, effect_count, effect_digest) "
        f"VALUES ('{op}', 'INJECT', 'historical:{op}', NULL, '{intent}', '{digest}', 2, 1, 1, 'COMPLETED', now(), "
        f"{1 if with_money else 0}, '{digest}')"
    ]
    if with_money:  # coordinated: envelope, membership, entry and debt move together, so (a) stays silent
        a, b = world.ids[world.alice["pid"]], world.ids[world.bob["pid"]]
        statements += [
            f"INSERT INTO debt_operation_equivalents VALUES ('{op}', '{world.equivalent_id}', true, true, 1, '{digest}')",
            "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, debtor_id, creditor_id, effect, "
            f"amount_before, amount_after, delta) VALUES ('{uuid.uuid4()}', '{op}', 1, '{world.equivalent_id}', "
            f"'{a}', '{b}', 'U', 10, 11, 1)",
            f"UPDATE debts SET amount = 11 WHERE equivalent_id = '{world.equivalent_id}' AND debtor_id = '{a}'",
        ]
    return statements


@pytest.mark.asyncio
async def test_s3a_a_historical_money_inject_is_failed_and_holds(api, factory) -> None:  # noqa: F811
    e, f = await _two_worlds(api, factory)
    await _around(factory, _inject_envelope_sql(e, with_money=True))

    outcome = await _verify(factory, e.equivalent_id)
    assert outcome.status == FAILED, outcome
    assert [(x["kind"], x["operation_kind"]) for x in outcome.findings] == [("b_version_unsupported", "INJECT")]
    await _scheduled_hold_stops_only_e(api, factory, e, f)


@pytest.mark.asyncio
async def test_s3a_control_a_money_less_inject_envelope_is_not_read(api, factory) -> None:  # noqa: F811
    e, _f = await _two_worlds(api, factory)
    await _around(factory, _inject_envelope_sql(e, with_money=False))
    assert (await _verify(factory, e.equivalent_id)).status == PASSED


@pytest.mark.asyncio
async def test_s3a_anti_vacuum_payments_a_clearing_and_a_stored_refusal_pass_with_every_pair_seen(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """F-030-5 anti-vacuum: three payments, a clearing occurrence of their cycle and one stored ABORTED refusal -
    PASSED, four transaction-operation pairs seen and the refusal read without an operation."""

    from app.core.clearing.service import ClearingService
    from app.core.payments.router import PaymentRouter
    from tests.p023_support import TEST_PLAN_ID, occurrence_of

    e = await build_api_world(api, factory)
    await _line(api, e, e.carol, e.bob)
    await _line(api, e, e.alice, e.carol)
    for sender, receiver in ((e.alice, e.bob), (e.bob, e.carol), (e.carol, e.alice)):
        assert _committed(await _pay(api, e, sender, receiver, "10.00"))
    ids = [e.ids[p["pid"]] for p in (e.alice, e.bob, e.carol)]
    async with factory() as s:
        debt_of = {(r.debtor_id, r.creditor_id): r.id for r in (await s.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.id).where(Debt.equivalent_id == e.equivalent_id))).all()}
    cycle = [debt_of[(ids[k], ids[(k + 1) % 3])] for k in range(3)]
    occurrence = occurrence_of(cycle, equivalent_id=e.equivalent_id, amount="10", plan_id=TEST_PLAN_ID, ordinal=0)
    async with factory() as s:
        await ClearingService(s).execute_occurrence(occurrence)
    a, b = e.alice["pid"], e.bob["pid"]
    monkeypatch.setattr(PaymentRouter, "find_flow_routes", lambda *_a, **_k: [([a, b], Decimal("4.00")), ([a, b], Decimal("3.00"))])
    refused = await _pay(api, e, e.alice, e.bob, "10.00")  # short of the request (R-4): refused after admission
    monkeypatch.undo()
    assert not _committed(refused) and refused.status_code != 200, refused.text

    outcome = await _verify(factory, e.equivalent_id)
    assert outcome.status == PASSED, outcome
    assert (outcome.transaction_pairs, outcome.transactions_read) == (4, 5), outcome.detail()
