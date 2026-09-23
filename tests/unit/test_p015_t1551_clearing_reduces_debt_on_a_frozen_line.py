"""T1551: clearing may reduce debt on a frozen trust line, given that line's `auto_clearing` consent.

THE DEFECT. Clearing excluded frozen trust lines in three places in `app/core/clearing/service.py`:
the triangle query and the quadrangle query joined every controlling line with `status = 'active'`,
and the consent check `_cycle_respects_auto_clearing` read only `active` lines - so a frozen line
had no consent to find, both when candidates were filtered and when execution re-checked them.

THE PROTOCOL does not exclude them. `docs/ru/02-protocol-spec.md` §7.2 searches cycles over `debts`
alone, with no trust-line table and no status, and §7.4 makes clearing depend only on
`policy.auto_clearing`. Clearing subtracts one amount around a cycle, so it preserves every net
position and creates no new exposure. The consequence of the exclusion: a line over its limit - the
state §11.5.2 freezes a line FOR - could never be reduced by a cycle.

THE DECISION, not reopened here: Codex review `CLEARING-FROZEN: ALLOW-REDUCTION`, 2026-09-13 - admit
`active` and `frozen` consistently in discovery, in policy evaluation and in execution-time
revalidation, while still requiring consent. `closed` stays excluded, as it was.

WHAT A RESIDUAL VIOLATION DOES AFTER CLEARING - established before the fix, because it could have
defeated it. `_execute_clearing_with_amount` computes a post-checkpoint and records its result on the
`IntegrityAuditLog` row as `verification_passed`; the only post-state check that RAISES is clearing
neutrality. So a partial reduction of an over-limit line commits, and the line is still over its
limit afterwards. The over-limit test below asserts that remaining breach explicitly, so it cannot
pass on a fixture that was never over the limit.

WHY THE DETECTORS ARE ADDRESSED DIRECTLY. `find_cycles` falls through to the Python DFS when the
triangle query comes back empty, and the DFS has no status predicate of its own - it relies on the
consent filter. A triangle query that still excluded frozen lines would therefore be hidden behind
`find_cycles(max_depth=3)`, so the two SQL detectors are called by name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import or_, select

from app.core.clearing.service import ClearingService
from app.core.invariants import InvariantChecker
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException
from tests.debt_setup import debt_fixture_setup
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner
from tests.conftest import MODE_B

_CONSENT = {"auto_clearing": True}
_REFUSAL = {"auto_clearing": False}


@dataclass(frozen=True)
class _Ring:
    eq_id: uuid.UUID
    eq_code: str
    participant_ids: list[uuid.UUID]
    participant_pids: list[str]
    # debt_ids[i] is the debt p[i] -> p[i+1]; the last one, p[-1] -> p[0], is the subject.
    debt_ids: list[uuid.UUID]


async def _ring(
    db_session,
    *,
    amounts: list[str],
    subject_status: str,
    subject_limit: str = "1000",
    subject_policy: dict | None = None,
) -> _Ring:
    """A debt ring p0 -> p1 -> ... -> p0 in which p[i] owes p[i+1] `amounts[i]`.

    The last debt, p[-1] -> p[0], is the SUBJECT: its controlling line (creditor p0 trusts debtor
    p[-1]) takes `subject_status`, `subject_limit` and `subject_policy`. Every other controlling
    line is active, has consent and a limit of 1000, so it is never the reason for an outcome.
    """
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("FZ" + nonce[:14]).upper(),
        symbol="FZ",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    participants = [
        Participant(
            pid=f"R{i}{nonce}",
            display_name=f"R{i}",
            public_key=f"pk_r{i}_{nonce}",
            type="person",
            status="active",
            profile={},
        )
        for i in range(len(amounts))
    ]
    db_session.add_all([eq, *participants])
    await db_session.flush()

    n = len(participants)
    debts = [
        Debt(
            debtor_id=participants[i].id,
            creditor_id=participants[(i + 1) % n].id,
            equivalent_id=eq.id,
            amount=Decimal(amount),
        )
        for i, amount in enumerate(amounts)
    ]
    async with debt_fixture_setup(db_session, label="ring"):
        db_session.add_all(debts)

    for i, debt in enumerate(debts):
        is_subject = i == n - 1
        db_session.add(
            TrustLine(
                from_participant_id=debt.creditor_id,
                to_participant_id=debt.debtor_id,
                equivalent_id=eq.id,
                limit=Decimal(subject_limit if is_subject else "1000"),
                policy=(subject_policy or _CONSENT) if is_subject else _CONSENT,
                status=subject_status if is_subject else "active",
            )
        )
    ring = _Ring(
        eq_id=eq.id,
        eq_code=eq.code,
        participant_ids=[p.id for p in participants],
        participant_pids=[p.pid for p in participants],
        debt_ids=[d.id for d in debts],
    )
    await db_session.commit()
    return ring


async def _debt_amount(db_session, debt_id: uuid.UUID) -> Decimal | None:
    value = (
        await db_session.execute(select(Debt.amount).where(Debt.id == debt_id))
    ).scalar_one_or_none()
    return None if value is None else Decimal(str(value))


def _debt_id_sets(cycles) -> set[frozenset[str]]:
    return {
        frozenset(ClearingService._debt_id_key(edge["debt_id"]) for edge in cycle)
        for cycle in cycles
    }


# --- discovery -------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "frozen"])
async def test_the_triangle_query_returns_a_cycle_through_a_frozen_line(db_session, status):
    ring = await _ring(db_session, amounts=["10", "10", "10"], subject_status=status)

    cycles = await ClearingService(db_session).find_triangles_sql(ring.eq_id)

    assert frozenset(str(i) for i in ring.debt_ids) in _debt_id_sets(cycles)


@pytest.mark.asyncio
async def test_the_quadrangle_query_returns_a_cycle_through_a_frozen_line(db_session):
    ring = await _ring(db_session, amounts=["10", "10", "10", "10"], subject_status="frozen")

    cycles = await ClearingService(db_session).find_quadrangles_sql(ring.eq_id)

    assert frozenset(str(i) for i in ring.debt_ids) in _debt_id_sets(cycles)


# --- the reduction ---------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "frozen"])
async def test_clearing_reduces_the_over_limit_debt_on_a_frozen_line(db_session, status):
    # The subject line allows 100 and carries 150 - the breach §11.5.2 freezes a line for. The
    # cycle carries 30, so clearing can reduce the breach but not cure it. `active` is the control:
    # it behaved this way before the fix, and must still.
    ring = await _ring(
        db_session,
        amounts=["30", "30", "150"],
        subject_status=status,
        subject_limit="100",
    )
    checker = InvariantChecker(db_session)
    positions_before = {
        pid: await checker._calculate_net_position(pid, ring.eq_id)
        for pid in ring.participant_ids
    }

    cleared = await ClearingService(db_session).auto_clear(ring.eq_code, max_depth=3)

    assert cleared == 1
    positions_after = {
        pid: await checker._calculate_net_position(pid, ring.eq_id)
        for pid in ring.participant_ids
    }
    assert positions_after == positions_before

    first, second, subject = ring.debt_ids
    assert await _debt_amount(db_session, subject) == Decimal("120")
    assert await _debt_amount(db_session, first) is None
    assert await _debt_amount(db_session, second) is None

    # Still over the limit after the reduction: this is the case the task exists for, and the
    # clearing above committed through it rather than being refused by its own verification.
    with pytest.raises(IntegrityViolationException) as exc_info:
        await checker.check_trust_limits(equivalent_id=ring.eq_id)
    (violation,) = exc_info.value.details["violations"]
    assert Decimal(violation["violation_amount"]) == Decimal("20")


# --- control: consent is still required -------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_a_frozen_line_without_consent_is_still_not_cleared(db_session):
    ring = await _ring(
        db_session,
        amounts=["10", "10", "10"],
        subject_status="frozen",
        subject_policy=_REFUSAL,
    )
    service = ClearingService(db_session)

    assert await service.find_cycles(ring.eq_code, max_depth=3) == []
    # Execution re-checks consent on its own; a caller may hand it a cycle detection never produced.
    cycle = [{"debt_id": str(debt_id)} for debt_id in ring.debt_ids]
    assert await service.execute_clearing_with_amount(cycle) is None
    assert [await _debt_amount(db_session, i) for i in ring.debt_ids] == [Decimal("10")] * 3


# --- the application path: the simulator's freeze inject --------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_a_cycle_through_lines_frozen_by_the_simulator_inject_is_cleared(db_session):
    # The only assignment of `frozen` in the application is the simulator's `freeze_participant`
    # inject (`app/core/simulator/inject_executor.py`). It freezes every active line incident to the
    # participant; before T1551 that stranded any cycle through the participant for good.
    ring = await _ring(db_session, amounts=["10", "10", "10"], subject_status="active")
    frozen_id, frozen_pid = ring.participant_ids[0], ring.participant_pids[0]
    pids = ring.participant_pids
    run = _make_run(
        participants=list(zip(ring.participant_ids, pids)),
        equivalents=[ring.eq_code],
        edges_by_equivalent={
            ring.eq_code: [(pids[(i + 1) % 3], pids[i]) for i in range(3)]
        },
    )
    scenario = {
        "participants": [{"id": pid, "status": "active"} for pid in pids],
        "trustlines": [
            {"from": pids[(i + 1) % 3], "to": pids[i], "status": "active"} for i in range(3)
        ],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "freeze_participant",
                        "participant_id": frozen_pid,
                        "freeze_trustlines": True,
                    }
                ],
            }
        ],
    }
    runner, _artifacts = _make_runner()

    await runner._apply_due_scenario_events(
        db_session, run_id="r-t1551", run=run, scenario=scenario
    )

    incident_statuses = (
        await db_session.execute(
            select(TrustLine.status).where(
                TrustLine.equivalent_id == ring.eq_id,
                or_(
                    TrustLine.from_participant_id == frozen_id,
                    TrustLine.to_participant_id == frozen_id,
                ),
            )
        )
    ).scalars().all()
    assert sorted(incident_statuses) == ["frozen", "frozen"]

    cleared = await ClearingService(db_session).auto_clear(ring.eq_code, max_depth=3)

    assert cleared == 1
    assert [await _debt_amount(db_session, i) for i in ring.debt_ids] == [None, None, None]
