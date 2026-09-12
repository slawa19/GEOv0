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
import json
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

from tests.debt_setup import debt_fixture_setup

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
    async with debt_fixture_setup(db_session, label="setup"):
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


# ---------------------------------------------------------------------------
# Both of the following exist because the external review of this slice found them missing.
# The first fix closed one of six sources and one of two schema shapes; these hold the other
# halves to the code rather than to a commit message.
# ---------------------------------------------------------------------------


def test_no_checked_in_fixture_publishes_a_zero_sum_verdict() -> None:
    """Every `integrity-status.json` in the tree, not just the one the mock happens to read.

    The first edition of this slice edited `admin-ui/public/admin-fixtures/...`, which is a
    DERIVED copy: `npm run dev` and `npm run build` both run `sync:fixtures`, which force-copies
    over it from `admin-fixtures/v1`. Reproduced - one `npm run sync:fixtures` put the green
    `passed: true` straight back, which would have reddened the fixture contract test on the next
    build. Five sources were behind it: the canonical dataset, two scenario packs and two
    generators. This walks the tree so a sixth cannot appear quietly.
    """
    found: list[str] = []
    offenders: list[str] = []
    for path in _ROOT.rglob("integrity-status.json"):
        rel = path.relative_to(_ROOT)
        if any(part in {"node_modules", "dist", ".local-run", ".git"} for part in rel.parts):
            continue
        found.append(str(rel))
        payload = json.loads(path.read_text(encoding="utf-8"))
        for code, entry in (payload.get("equivalents") or {}).items():
            zero_sum = (entry.get("invariants") or {}).get("zero_sum")
            if zero_sum != _WITHDRAWN:
                offenders.append(f"{rel}::{code} -> {zero_sum}")

    assert found, "no fixture was scanned - an empty search is not a clean one"
    assert offenders == [], offenders


def test_a_checkpoint_stored_before_the_withdrawal_still_conforms_to_the_canon() -> None:
    """`GET /integrity/checksum/{eq}` hands back a STORED `invariants_status` verbatim.

    Rows written before T1402 carry `zero_sum: {"passed": true}` and can carry
    `alerts: ["zero_sum"]`. They are evidence of what the system claimed at the time and are not
    rewritten. The first edition of the canon admitted only the new shape, which declared a
    reachable response impossible - the mirror of the defect programme 011 exists to fix, and
    exactly the half of the finding that the audit-log schema had already been given.
    """
    from openapi_schema_validator import OAS30Validator

    from tests.contract.openapi_response_conformance import load_canon, registry_for

    canon = load_canon()
    validator = OAS30Validator(
        {"$ref": "urn:canon#/components/schemas/IntegrityInvariantsStatus"},
        registry=registry_for(canon),
    )

    legacy = {
        "computed_at": "2026-08-01T00:00:00+00:00",
        "debts_count": 2,
        "trustlines_count": 1,
        "debts_non_negative": True,
        "debts_negative_count": 0,
        "status": "critical",
        "checks": {
            "zero_sum": {"passed": False, "details": {
                "invariant": "ZERO_SUM_VIOLATION",
                "violations": {"11111111-1111-1111-1111-111111111111": "0.00000001"},
            }},
            "trust_limits": {"passed": True, "violations": 0},
            "debt_symmetry": {"passed": True, "violations": 0},
        },
        "alerts": ["zero_sum"],
        "passed": False,
    }
    assert list(validator.iter_errors(legacy)) == [
    ], [e.message for e in validator.iter_errors(legacy)]

    legacy_pass = json.loads(json.dumps(legacy))
    legacy_pass["checks"]["zero_sum"] = {"passed": True}
    legacy_pass["alerts"] = []
    legacy_pass["status"] = "healthy"
    legacy_pass["passed"] = True
    assert list(validator.iter_errors(legacy_pass)) == [
    ], [e.message for e in validator.iter_errors(legacy_pass)]

    current = json.loads(json.dumps(legacy_pass))
    current["checks"]["zero_sum"] = dict(_WITHDRAWN)
    current["unverified"] = ["zero_sum"]
    assert list(validator.iter_errors(current)) == [
    ], [e.message for e in validator.iter_errors(current)]

    # And the thing the withdrawal must never allow: a verdict wearing the withdrawal's clothes.
    fused = json.loads(json.dumps(current))
    fused["checks"]["zero_sum"] = {**_WITHDRAWN, "passed": True}
    assert list(validator.iter_errors(fused)), "a fused entry must not validate"
