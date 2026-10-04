"""R-021-2 (programme 021, stage 1, `T2102`): the scenario seeder writes trust lines through the service.

WHAT IS WRONG (on `87c7200`). `RealScenarioSeeder.seed_scenario_into_db` builds `TrustLine(...)` rows itself
(`real_scenario_seeder.py:276-285`): no `IntegrityAuditLog` row and no integrity checkpoint, so a line the
simulator seeded does not exist in the audit trail at all (spec, Problem item 2).

THE TARGET (spec, "Решения" items 6 and 9). Seeding goes through a narrow internal operation of
`TrustLineService` that imports a scenario's initial status: one `TRUST_LINE_CREATE` row per applied line,
labelled as belonging to the caller's transaction, and exactly one before/after checkpoint pair per touched
equivalent for the whole seeding transaction.

THE CHARACTERIZATION (green before and after, the half that must NOT move): the initial statuses
`active`/`closed` (028 `F-028-29`: `frozen` is refused, below) and the fallback of anything else to `active`, the policy default, the skips (no
equivalent, unknown participant, negative, non-numeric and too-large limits - 028 E4: a limit finer than 1E-8 is refused by name instead, a live line already there) and
repeated seeding - including the one surprising property found while pinning it: a `closed` scenario line is
NOT found by the live-line lookup, so every repeated seeding imports it once more as a new closed row.

Mode A (`db_session`): everything is checked inside one transaction the fixture rolls back.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.simulator.real_scenario_seeder import RealScenarioSeeder, ScenarioTrustLineRefused
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.p021_support import (
    TrustLineBatchPoints,
    is_transaction_scoped,
    require_target,
    trust_line_audit_rows,
)

DEFAULT_POLICY = {
    "auto_clearing": True,
    "can_be_intermediate": True,
    "max_hop_usage": None,
    "daily_limit": None,
    "blocked_participants": [],
}


def _world() -> dict:
    n = uuid.uuid4().hex[:6].upper()
    pid = {r: f"P21S_{r}_{n}" for r in ("A", "B", "C", "D")}
    e1, e2 = f"P21A{n}", f"P21B{n}"
    scenario = {
        "equivalents": [e1, e2],
        "participants": [{"id": pid[r], "name": r} for r in ("A", "B", "C", "D")],
        "trustlines": [
            # applied
            {"from": pid["A"], "to": pid["B"], "equivalent": e1, "limit": "100", "status": "active",
             "policy": {"auto_clearing": False}},
            {"from": pid["A"], "to": pid["C"], "equivalent": e1, "limit": "50.5", "status": "active"},  # 028: was frozen
            {"from": pid["B"], "to": pid["C"], "equivalent": e1, "limit": "10", "status": "closed",
             "policy": "not-a-dict"},
            {"from": pid["B"], "to": pid["D"], "equivalent": e1, "limit": "7", "status": "weird"},
            {"from": pid["D"], "to": pid["B"], "equivalent": e2, "limit": "20"},
            # skipped
            {"from": pid["C"], "to": pid["D"], "equivalent": e1, "limit": "-1"},
            {"from": pid["C"], "to": pid["A"], "equivalent": e1, "limit": "abc"},
            {"from": pid["D"], "to": pid["A"], "equivalent": e1, "limit": "1000000000000"},  # too large to store (028 E4: 1E-9 now refuses)
            {"from": pid["A"], "to": f"P21S_GHOST_{n}", "equivalent": e1, "limit": "5"},
            {"from": pid["A"], "to": pid["D"], "equivalent": "", "limit": "5"},
            {"from": pid["C"], "to": pid["B"], "equivalent": e1, "limit": "99"},  # a live line exists
        ],
    }
    return {"pid": pid, "e1": e1, "e2": e2, "scenario": scenario}


async def _preexisting_live_line(session, w) -> uuid.UUID:
    """C -> B in e1, live, written before the seed - the seed must skip its own C -> B line."""

    eq = Equivalent(code=w["e1"], is_active=True, metadata_={})
    people = [
        Participant(pid=w["pid"][r], display_name=r, public_key=hashlib.sha256(w["pid"][r].encode()).hexdigest(),
                    type="person", status="active", profile={})
        for r in ("B", "C")
    ]
    session.add_all([eq, *people])
    await session.flush()
    line = TrustLine(from_participant_id=people[1].id, to_participant_id=people[0].id, equivalent_id=eq.id,
                     limit=Decimal("33"), status="active", policy={"auto_clearing": True})
    session.add(line)
    await session.commit()
    return line.id


async def _lines(session, w) -> list[tuple[str, str, str, Decimal, str, dict]]:
    by_id = {p.id: p.pid for p in (await session.execute(select(Participant))).scalars().all()}
    eqs = {e.id: e.code for e in (await session.execute(select(Equivalent))).scalars().all()}
    rows = (
        await session.execute(select(TrustLine).where(TrustLine.equivalent_id.in_(
            [i for i, c in eqs.items() if c in (w["e1"], w["e2"])]
        )))
    ).scalars().all()
    return sorted(
        (by_id[r.from_participant_id], by_id[r.to_participant_id], eqs[r.equivalent_id], Decimal(str(r.limit)),
         str(r.status), dict(r.policy or {}))
        for r in rows
    )


async def _seed(session, w) -> None:
    await RealScenarioSeeder().seed_scenario_into_db(session=session, scenario=w["scenario"])
    await session.commit()


def _expected_first_seed(w) -> list:
    p, e1, e2 = w["pid"], w["e1"], w["e2"]
    return sorted([
        (p["A"], p["B"], e1, Decimal("100"), "active", {"auto_clearing": False}),
        (p["A"], p["C"], e1, Decimal("50.5"), "active", DEFAULT_POLICY),
        (p["B"], p["C"], e1, Decimal("10"), "closed", DEFAULT_POLICY),
        (p["B"], p["D"], e1, Decimal("7"), "active", DEFAULT_POLICY),
        (p["C"], p["B"], e1, Decimal("33"), "active", {"auto_clearing": True}),  # pre-existing, untouched
        (p["D"], p["B"], e2, Decimal("20"), "active", DEFAULT_POLICY),
    ])


@pytest.mark.asyncio
async def test_seed_keeps_statuses_policy_defaults_and_skips(db_session) -> None:
    """Characterization: what the seeder writes, and what it does not."""

    w = _world()
    await _preexisting_live_line(db_session, w)
    await _seed(db_session, w)

    assert await _lines(db_session, w) == _expected_first_seed(w)


@pytest.mark.asyncio
async def test_repeated_seeding_imports_only_the_closed_line_again(db_session) -> None:
    """Characterization: live lines are skipped on the second run; a closed one is imported once more."""

    w = _world()
    await _preexisting_live_line(db_session, w)
    await _seed(db_session, w)
    await _seed(db_session, w)

    p, e1 = w["pid"], w["e1"]
    expected = sorted(_expected_first_seed(w) + [(p["B"], p["C"], e1, Decimal("10"), "closed", DEFAULT_POLICY)])
    assert await _lines(db_session, w) == expected


@pytest.mark.asyncio
async def test_every_seeded_line_has_a_transaction_scoped_create_row(db_session, monkeypatch) -> None:
    w = _world()
    await _preexisting_live_line(db_session, w)
    checkpoints = TrustLineBatchPoints(monkeypatch)

    await _seed(db_session, w)
    first_seed_checkpoints = checkpoints.count
    first_rows = await trust_line_audit_rows(db_session, equivalent_codes=[w["e1"], w["e2"]], operation_type="TRUST_LINE_CREATE")

    await _seed(db_session, w)
    second_seed_checkpoints = checkpoints.count - first_seed_checkpoints
    all_rows = await trust_line_audit_rows(db_session, equivalent_codes=[w["e1"], w["e2"]], operation_type="TRUST_LINE_CREATE")
    # By id, not by position: in mode A every row shares one `created_at` (`now()` of the outer transaction).
    first_ids = {r.id for r in first_rows}
    second_rows = [r for r in all_rows if r.id not in first_ids]

    # ── controls: both seedings ran and applied what the characterization says ─────────────────
    lines = await _lines(db_session, w)
    assert len(lines) == len(_expected_first_seed(w)) + 1, lines

    p, e1, e2 = w["pid"], w["e1"], w["e2"]
    applied_first = sorted([
        (p["A"], p["B"], e1, "active"), (p["A"], p["C"], e1, "active"), (p["B"], p["C"], e1, "closed"),
        (p["B"], p["D"], e1, "active"), (p["D"], p["B"], e2, "active"),
    ])

    def described(rows) -> list:
        return sorted(
            (r.affected_participants.get("from"), r.affected_participants.get("to"), r.equivalent_code,
             r.affected_participants.get("initial_status"))
            for r in rows
        )

    def shares_one_pair_per_equivalent(rows) -> bool:
        pairs: dict[str, set] = {}
        for r in rows:
            pairs.setdefault(r.equivalent_code, set()).add((r.state_checksum_before, r.state_checksum_after))
        # 024 `T2413.2`: no checkpoint in the transaction - every row of the batch carries the empty pair.
        return bool(pairs) and all(v == {("", "")} for v in pairs.values())

    require_target(
        described(first_rows) == applied_first
        and all(is_transaction_scoped(r) for r in first_rows)
        and shares_one_pair_per_equivalent(first_rows)
        and first_seed_checkpoints == 2 * 2
        and described(second_rows) == [(p["B"], p["C"], e1, "closed")]
        and all(is_transaction_scoped(r) for r in second_rows)
        and second_seed_checkpoints == 2 * 1,
        f"first seed: {len(first_rows)} TRUST_LINE_CREATE rows {described(first_rows)}, "
        f"{first_seed_checkpoints} checkpoints; second seed: {len(second_rows)} rows, "
        f"{second_seed_checkpoints} checkpoints",
    )


@pytest.mark.asyncio
async def test_a_frozen_scenario_line_refuses_the_seed(db_session) -> None:
    """028 `F-028-29` (owner В-2): `frozen` is no line status - the seed stops naming the line, nothing is written."""
    w = _world()
    w["scenario"]["trustlines"] = [{"from": w["pid"]["A"], "to": w["pid"]["B"], "equivalent": w["e1"], "limit": "5",
                                    "status": "frozen"}]
    with pytest.raises(ScenarioTrustLineRefused) as refused:
        await _seed(db_session, w)
    assert refused.value.details["reason"] == "trust_line_status_frozen", refused.value.details
    await db_session.rollback()
    assert await _lines(db_session, w) == []
