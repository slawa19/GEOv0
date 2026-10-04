"""028 E6, F-028-42 / F-028-43 (owner В-6): every refusal of `POST /payments` - before admission (the error
envelope) and after it (the stored `ABORTED` its replay answers) - carries `details.reason` from a closed set, `other`
beyond it; never the Redis `lock_key`, the delta check's `drifts` or a Python repr. ANTI-VACUUM: every reason of the
declared set is reached by a real request (`unverifiable_legacy_identity` in `test_p015_t1548_...`) and the reached
set must equal the declared one. Mode B: a payment runs on sessions of its own.
"""

from __future__ import annotations

import asyncio
import base64
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select, update

from app.api import deps
from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PAYMENT_REFUSAL_REASONS, PaymentService
from app.core.invariants import InvariantChecker
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.main import app
from app.utils.exceptions import IntegrityViolationException
from tests.conftest import MODE_B
from tests.integration.test_p015_t1523_replay_after_a_hold_or_an_abort import (  # noqa: I001
    ADMIN, _code, _create_equivalent, _payment_body, _trust)
from tests.integration.test_p028_e6_integrity_access import hold
from tests.integration.test_scenarios import _sign_payment_request, register_and_login

pytestmark = MODE_B

REACHED_ELSEWHERE = {"unverifiable_legacy_identity"}
FOREIGN = "0f0e0d0c-0b0a-4908-8706-050403020100"


async def _never(*_args, **_kwargs):
    return False


async def _post(client, sender, body):
    return await client.post("/api/v1/payments", json=body, headers=sender["headers"])


async def _stand(client):
    code = _code()
    await _create_equivalent(client, code)
    return (code, *[await register_and_login(client, p + code) for p in ("A_", "B_", "C_")])


@pytest.mark.asyncio
async def test_every_refusal_before_admission_names_its_reason(client, db_session) -> None:
    code, alice, bob, carol = await _stand(client)
    await _trust(client, bob, alice, code, "100.00")
    committed = _payment_body(alice, bob, code, "10.00")
    assert (await _post(client, alice, committed)).json()["status"] == "COMMITTED"

    bad_signature = _payment_body(alice, bob, code, "10.00")
    bad_signature["signature"] = base64.b64encode(b"\0" * 64).decode()
    reused = {**committed, "amount": "11.00", "signature": _sign_payment_request(
        signing_key=SigningKey(base64.b64decode(alice["priv"])), tx_id=committed["tx_id"], from_pid=alice["pid"],
        to_pid=bob["pid"], equivalent=code, amount="11.00")}
    cases = {
        "amount_not_positive": _payment_body(alice, bob, code, "0"),
        "other": _payment_body(alice, bob, code, "abc"),
        "self_payment": _payment_body(alice, alice, code, "1.00"),
        "equivalent_not_found": _payment_body(alice, bob, "Q" + code[1:], "1.00"),
        "invalid_signature": bad_signature,
        "recipient_not_found": {**_payment_body(alice, bob, code, "1.00"), "to": "nobody-" + code},
        "amount_precision_exceeded": _payment_body(alice, bob, code, "1.005"),
        "no_route": _payment_body(alice, carol, code, "1.00"),
        "insufficient_capacity": _payment_body(alice, bob, code, "150.00"),
        "tx_id_reused": reused,
    }
    seen: dict[str, dict] = {}
    for reason, body in cases.items():
        resp = await _post(client, alice, body)
        assert resp.status_code >= 400, (reason, resp.text)
        seen[reason] = resp.json()["error"]
    # "Busy": the per-participant Redis lock is held by another request.
    app.dependency_overrides[deps.get_redis_client] = lambda: SimpleNamespace(set=_never)
    try:
        seen["busy"] = (await _post(client, alice, _payment_body(alice, bob, code, "1.00"))).json()["error"]
    finally:
        app.dependency_overrides.pop(deps.get_redis_client, None)

    for name in ("equivalent_integrity_hold", "equivalent_inactive"):
        other = _code()
        await _create_equivalent(client, other)
        await _trust(client, bob, alice, other)
        if name == "equivalent_inactive":
            await client.patch(f"/api/v1/admin/equivalents/{other}", json={"is_active": False, "reason": "e6"},
                               headers=ADMIN)
        else:
            await hold(db_session, other)
        seen[name] = (await _post(client, alice, _payment_body(alice, bob, other, "1.00"))).json()["error"]

    wrong = {k: v for k, v in seen.items() if (v.get("details") or {}).get("reason") != k}
    assert not wrong, f"refusals without their reason: {wrong}"
    assert seen["no_route"]["details"]["max_available"] == "0.00", seen["no_route"]
    assert seen["insufficient_capacity"]["details"]["max_available"] == "90.00", seen["insufficient_capacity"]
    assert seen["busy"]["details"].get("retryable") is True and "lock_key" not in seen["busy"]["details"], seen
    assert seen["no_route"]["code"] == "E002" and seen["other"]["code"] == "E009", seen

    reached = set(seen) | REACHED_ELSEWHERE | {"timeout", "policy", "participant_suspended"}  # the admitted test
    assert reached == set(PAYMENT_REFUSAL_REASONS), (reached ^ set(PAYMENT_REFUSAL_REASONS))


@pytest.mark.asyncio
async def test_an_admitted_refusal_keeps_its_reason_on_the_replay(client, db_session, monkeypatch) -> None:
    """F-028-43: the same `tx_id` after a definitive refusal answers the stored ABORTED with the same error."""

    code, alice, bob, carol = await _stand(client)
    await _trust(client, carol, alice, code)
    await _trust(client, bob, carol, code)
    await db_session.execute(update(TrustLine).where(TrustLine.from_participant_id == select(Participant.id).where(
        Participant.pid == carol["pid"]).scalar_subquery()).values(policy={"can_be_intermediate": False}))
    await db_session.commit()

    # A stale route through carol, who may not mediate: the core's own policy check refuses it after admission.
    monkeypatch.setattr(PaymentRouter, "find_flow_routes",
                        lambda self, a, b, amount, **_kw: [([a, carol["pid"], b], Decimal(amount))])
    policy = _payment_body(alice, bob, code, "5.00")
    first = await _post(client, alice, policy)
    assert first.status_code == 400 and first.json()["error"]["details"]["reason"] == "policy", first.text
    replay = await _post(client, alice, policy)
    assert replay.status_code == 200 and replay.json()["status"] == "ABORTED", replay.text
    assert replay.json()["error"]["details"]["reason"] == "policy", replay.text
    assert replay.json()["error"]["code"] == first.json()["error"]["code"], (first.text, replay.text)

    # The delta check's drifts name other participants: they stay in the log, never in the answer.
    async def drifted(*_a, **_kw):
        raise IntegrityViolationException("Per-participant delta check failed", details={
            "invariant": "PAYMENT_DELTA_DRIFT", "drifts": [{"participant_id": "someone", "drift": "1"}]})

    monkeypatch.undo()
    await _trust(client, bob, alice, code)
    monkeypatch.setattr(MoneyBoundary, "check_payment_delta", drifted)
    drift = await _post(client, alice, _payment_body(alice, bob, code, "1.00"))
    assert "drifts" not in drift.text and drift.json()["error"]["details"]["reason"] == "other", drift.text

    # §15 review `T2899.4` #1: an invariant on an intermediate pair names its debtor, creditor and sums
    # (`violations`, `app/core/invariants.py`) - the admin's row keeps them, the payer's answers do not.
    async def over_limit(*_a, **_kw):
        raise IntegrityViolationException("Trust limit exceeded", details={"invariant": "TRUST_LIMIT_VIOLATION",
                                          "violations": [{"debtor_id": FOREIGN, "debt_amount": "77.70"}]})

    monkeypatch.setattr(InvariantChecker, "check_debt_growth", over_limit)
    leaky = _payment_body(alice, bob, code, "1.00")
    for answer in (await _post(client, alice, leaky), await _post(client, alice, leaky)):  # POST, stored ABORTED
        assert FOREIGN not in answer.text and "77.70" not in answer.text, answer.text
    assert FOREIGN in str((await db_session.execute(select(Transaction.error).where(
        Transaction.tx_id == leaky["tx_id"]))).scalar_one()), "the stored row lost its diagnostics"

    # A route naming a participant that does not exist: an internal error, answered without its repr.
    monkeypatch.setattr(PaymentRouter, "find_flow_routes",
                        lambda self, a, b, amount, **_kw: [([a, "ghost", b], Decimal(amount))])
    ghosted = _payment_body(alice, bob, code, "1.00")
    ghost = await _post(client, alice, ghosted)
    assert ghost.status_code == 500 and "ghost" not in ghost.text and "{" not in ghost.json()["error"]["message"]
    # #2: a stored E010 row with a diagnostic text is read back with the code's meaning only, the row kept intact.
    diagnostic = {"code": "E010", "message": "Participants not found: {'ghost'}", "details": {}}
    await db_session.execute(update(Transaction).where(Transaction.tx_id == ghosted["tx_id"]).values(error=diagnostic))
    await db_session.commit()
    reads = [await _post(client, alice, ghosted), await client.get(f"/api/v1/payments/{ghosted['tx_id']}",
             headers=alice["headers"]), await client.get("/api/v1/payments", headers=alice["headers"])]
    assert all("Participants not found" not in r.text and r.status_code == 200 for r in reads), [r.text for r in reads]
    monkeypatch.undo()

    # The timeout of an admitted payment, and its replay.
    original = PaymentService._bind_payment

    async def slowly(self, *args, **kwargs):
        await asyncio.sleep(0.2)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_bind_payment", slowly)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    late = _payment_body(alice, bob, code, "1.00")
    timed_out = await _post(client, alice, late)
    assert timed_out.status_code == 504 and timed_out.json()["error"]["details"]["reason"] == "timeout", timed_out.text
    monkeypatch.undo()
    again = await _post(client, alice, late)
    assert again.json()["status"] == "ABORTED" and again.json()["error"]["details"]["reason"] == "timeout", again.text

    # A frozen payee leaves the router's graph (that answer is "no route"); a route handed over before the freeze
    # meets the core's own participant check after admission.
    resp = await client.post(f"/api/v1/admin/participants/{bob['pid']}/freeze", json={"reason": "e6"}, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    monkeypatch.setattr(PaymentRouter, "find_flow_routes", lambda self, a, b, amount, **_kw: [([a, b], Decimal(amount))])
    frozen = await _post(client, alice, _payment_body(alice, bob, code, "1.00"))
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["error"]["details"]["reason"] == "participant_suspended", frozen.text

