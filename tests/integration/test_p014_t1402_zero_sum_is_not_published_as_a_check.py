"""T1402: zero-sum is no longer published as a passed check, and the wire says so.

WHY THIS MODULE EXISTS. `app/core/invariants.py` `check_zero_sum` sums the same `Debt` rows twice -
grouped by creditor and grouped by debtor - and returns the difference, so it telescopes to exactly
zero for any set of rows. It cannot fail on data corruption. Measured on PostgreSQL 2026-09-11
through the canonical gate: a debt inflated by one storage quantum, and a three-edge cycle inflated
uniformly by 7, both leave it PASSED - and in the second case the participant net positions are
bit-identical too.

Publishing `passed: true` for that was a claim about integrity the call could not support, and
programme 014 owns withdrawing it. Building a real check is programme 015 and is NOT done here: the
gap is stated, not filled.

WHAT THESE TESTS ARE, AND ARE NOT. They assert the WITHDRAWAL - that no verdict is published, that
the aggregate is scoped to the checks that ran, and that the gap is visible. They assert nothing
about corruption detection, because there is none to assert. Their counter-proof is the reverse of
the usual one: restoring the old publication (`InvariantResult(passed=True, value="0")` at the two
endpoints, `{"passed": True}` in the checkpoint) must redden them.
"""

from __future__ import annotations

import ast
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.schemas.integrity import InvariantWithdrawn
from tests.integration.test_scenarios import register_and_login

_ROOT = Path(__file__).resolve().parents[2]
_WITHDRAWN = {"status": "not_verified", "reason": "check_withdrawn"}


async def _seed(db_session) -> Equivalent:
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("W" + nonce[:15]).upper(),
        symbol="W",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    a = Participant(pid="WA" + nonce, display_name="WA", public_key="pkWA-" + nonce)
    b = Participant(pid="WB" + nonce, display_name="WB", public_key="pkWB-" + nonce)
    db_session.add_all([eq, a, b])
    await db_session.flush()
    db_session.add(
        Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10"))
    )
    await db_session.commit()
    return eq


@pytest.mark.asyncio
async def test_status_endpoint_publishes_no_zero_sum_verdict(
    client: AsyncClient, db_session
) -> None:
    eq = await _seed(db_session)
    user = await register_and_login(client, "WithdrawStatus")

    resp = await client.get("/api/v1/integrity/status", headers=user["headers"])
    assert resp.status_code == 200
    payload = resp.json()

    entry = payload["equivalents"][eq.code]
    assert entry["invariants"]["zero_sum"] == _WITHDRAWN
    assert "passed" not in entry["invariants"]["zero_sum"]
    assert entry["unverified"] == ["zero_sum"]

    # The other two still carry verdicts: the withdrawal is one check, not a retreat from all of
    # them. This debt has no trustline, so trust_limits genuinely fails here.
    assert entry["invariants"]["trust_limits"]["passed"] is False
    assert entry["invariants"]["debt_symmetry"]["passed"] is True

    # An unverified check can never be a detected failure.
    assert not any("Zero-sum" in a for a in payload["alerts"])


@pytest.mark.asyncio
async def test_verify_endpoint_publishes_no_zero_sum_verdict(
    client: AsyncClient, db_session
) -> None:
    eq = await _seed(db_session)
    user = await register_and_login(client, "WithdrawVerify")

    resp = await client.post("/api/v1/integrity/verify", json={}, headers=user["headers"])
    assert resp.status_code == 200
    payload = resp.json()

    entry = payload["equivalents"][eq.code]
    assert entry["invariants"]["zero_sum"] == _WITHDRAWN
    assert entry["unverified"] == ["zero_sum"]
    assert not any("Zero-sum" in a for a in payload["alerts"])


@pytest.mark.asyncio
async def test_checkpoint_records_the_withdrawal_not_a_pass(db_session) -> None:
    eq = await _seed(db_session)
    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)

    checks = cp.invariants_status["checks"]
    assert checks["zero_sum"] == _WITHDRAWN
    assert cp.invariants_status["unverified"] == ["zero_sum"]

    # The aggregate is computed from the checks that RAN. trust_limits fails on this fixture, so
    # the status must be critical - and it must be critical because of trust_limits, never
    # because zero_sum went missing.
    assert cp.invariants_status["status"] == "critical"
    assert cp.invariants_status["alerts"] == ["trust_limits"]


def test_the_withdrawn_shape_cannot_carry_a_verdict() -> None:
    """`passed` is FORBIDDEN on the withdrawn variant, not merely absent.

    An optional boolean would let a later writer reintroduce the claim with nothing noticing,
    which is the defect being removed rather than a smaller version of it.
    """
    assert InvariantWithdrawn().model_dump() == _WITHDRAWN

    with pytest.raises(ValidationError):
        InvariantWithdrawn(passed=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        InvariantWithdrawn(status="verified")  # type: ignore[arg-type]


def test_no_production_path_calls_check_zero_sum() -> None:
    """The call is gone from every producer, including the payment path.

    008 required the invocation removed from the payment path specifically: it scanned the whole
    equivalent on every payment and could not abort one, because it could not fail. This is an AST
    walk rather than a grep so a call written across two lines still counts, and it names the
    files it searched so an empty search cannot pass for a clean one.
    """
    searched: list[Path] = sorted((_ROOT / "app").rglob("*.py"))
    assert len(searched) > 100, len(searched)

    offenders: list[str] = []
    for path in searched:
        if path.name == "invariants.py" and path.parent.name == "core":
            continue  # the definition itself
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - would be a broken tree, not this test's subject
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "check_zero_sum":
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")

    assert offenders == [], offenders
