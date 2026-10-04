"""R-021-6 (programme 021, stage 2, `T2103`): Interact actions and inject write trust lines through the service.

WHAT IS WRONG (on `3f23a39`). Five of the nine simulator writers of `trust_lines` still build or mutate the rows
themselves: inject `create_trustline` and the initial lines of `add_participant` (`inject_executor.py:703`,
`:848`) and the three Interact actions (`app/api/v1/simulator.py:1151`, `:1393`, `:1575`). None of them writes
an `IntegrityAuditLog` row or computes an integrity checkpoint (spec, Problem item 2), and a failure after the
first mutation of their transaction is not something the trust-line service ever sees.

THE TARGET (spec, "Решения" items 4, 5, 7 and 9; stage 2 row).
* every applied trust-line operation of an Interact action and of an inject event has its own audit row,
  labelled as belonging to the caller's transaction, and each actual caller transaction computes exactly ONE
  before/after checkpoint pair per touched equivalent (an action is one transaction; an inject event is one);
* the owner of the transaction rolls back on any failure: after a failure that follows a first mutation,
  neither the limits/statuses nor any audit row of the failed transaction survive a later commit;
* Interact create keeps its own existing-debt check (stronger than the service's create), and the responses,
  codes and stored policy of the actions do not change.

THE CONTROLS (green before and after): the action responses and codes, the stored rows (limit, status, policy),
the refusal of a create below an existing debt, the inject counters and note.

MODES. Interact: mode A (`client` over `db_session`; the handler's commit and rollback are savepoint operations,
and a later `db_session.commit()` shows what the session still carried). Inject: the owner's REAL unit of work
(`RealRunner._apply_due_scenario_events`: owner locks, envelope, staging, flush, commit) on a mode-B clone at
SERIALIZABLE, read back on fresh sessions.

The failure point is the trust-line service's own after-mutation checkpoint (a non-database exception): before
stage 2 these paths compute no checkpoint, which is part of what the target says. The public signature
refusals are existing green controls (`tests/unit/test_trustline_signatures.py`,
`tests/unit/test_p021_public_trust_line_operations_require_a_signature.py`), not red-first.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.ledger.reconciliation import take_baseline
from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import _Artifacts, _runner
from tests.integration.test_p018_mixed_inject_event_is_one_operation_postgres import (  # noqa: F401 - fixture
    factory,
)
from tests.p021_support import (
    TrustLineBatchPoints,
    is_transaction_scoped,
    require_target,
    trust_line_audit_rows,
)
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture of the inject tests

#: The model's default policy (`app/db/models/trustline.py`): what an Interact create stored before 021.
DEFAULT_POLICY = {
    "auto_clearing": True,
    "can_be_intermediate": True,
    "max_hop_usage": None,
    "daily_limit": None,
    "blocked_participants": [],
}

HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}



def _simulated_key(pid: str) -> str:
    return hashlib.sha256(pid.encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ Interact


@pytest.fixture
def interact(monkeypatch, db_session):
    """A run whose perimeter is two fresh participants in a fresh equivalent; actions enabled."""

    import app.api.v1.simulator as simulator_module

    n = uuid.uuid4().hex[:6].upper()
    w = SimpleNamespace(run_id=f"p021-i-{n}", a=f"P21I_A_{n}", b=f"P21I_B_{n}", eq=f"P21I{n}")
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda run_id: SimpleNamespace(run_id=str(run_id), state="running", owner_id="", _real_seeded=True,
                                       _real_seeding_lock=None),
    )
    run = RunRecord(run_id=w.run_id, scenario_id="p021-interact", mode="real", state="running")
    run._scenario_raw = {"participants": [{"id": w.a}, {"id": w.b}], "trustlines": []}
    monkeypatch.setitem(simulator_module.runtime._runs, w.run_id, run)
    return w


async def _seed_interact(session, w) -> SimpleNamespace:
    eq = Equivalent(code=w.eq, precision=2, is_active=True, metadata_={})
    a, b = (
        Participant(pid=pid, display_name=pid, public_key=_simulated_key(pid), type="person", status="active",
                    profile={})
        for pid in (w.a, w.b)
    )
    session.add_all([eq, a, b])
    await session.commit()
    # Plain ids: a handler that rolls back expires the loaded rows, and reading one then would do IO.
    return SimpleNamespace(eq_id=eq.id, a_id=a.id, b_id=b.id)


async def _action(client, w, name: str, **body):
    payload = {"from_pid": w.a, "to_pid": w.b, "equivalent": w.eq, **body}
    return await client.post(f"/api/v1/simulator/runs/{w.run_id}/actions/{name}", headers=HEADERS, json=payload)


async def _live_line(session, rows) -> TrustLine | None:
    return (
        await session.execute(
            select(TrustLine).where(
                TrustLine.from_participant_id == rows.a_id,
                TrustLine.to_participant_id == rows.b_id,
                TrustLine.equivalent_id == rows.eq_id,
            ).order_by(TrustLine.created_at.desc())
        )
    ).scalars().first()


def _one_row_per_action(rows, expected_ops: list[str], w) -> bool:
    return (
        [r.operation_type for r in rows] == expected_ops
        and all(is_transaction_scoped(r) for r in rows)
        and all((r.affected_participants or {}).get("from") == w.a for r in rows)
        and all((r.affected_participants or {}).get("to") == w.b for r in rows)
    )


@pytest.mark.asyncio
async def test_interact_actions_write_one_audit_row_and_one_checkpoint_pair_each(
    client, db_session, interact, monkeypatch
) -> None:
    w = interact
    rows = await _seed_interact(db_session, w)
    checkpoints = TrustLineBatchPoints(monkeypatch)
    per_action: list[int] = []

    r1 = await _action(client, w, "trustline-create", limit="100", client_action_id="c1")
    per_action.append(checkpoints.count)
    r2 = await _action(client, w, "trustline-update", new_limit="150", client_action_id="c2")
    per_action.append(checkpoints.count - sum(per_action))
    r3 = await _action(client, w, "trustline-close", client_action_id="c3")
    per_action.append(checkpoints.count - sum(per_action))

    # ── controls: the wire and the stored row are what they were before 021 ─────────────────────────────
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert (p1["ok"], p1["from_pid"], p1["to_pid"], p1["equivalent"], p1["limit"], p1["client_action_id"]) == (
        True, w.a, w.b, w.eq, "100", "c1"), p1
    assert r2.status_code == 200, r2.text
    p2 = r2.json()
    assert (p2["trustline_id"], p2["old_limit"], p2["new_limit"], p2["client_action_id"]) == (
        p1["trustline_id"], "100.00000000", "150", "c2"), p2
    assert r3.status_code == 200, r3.text
    # INTENTIONAL, 026 `T2603.2`: the close answer carries the line's state (no debt here: closed at once).
    closed = r3.json()
    assert closed.pop("close_requested_at") and closed == {
        "ok": True, "trustline_id": p1["trustline_id"], "status": "closed", "client_action_id": "c3"}, r3.json()
    line = await _live_line(db_session, rows)
    # INTENTIONAL, 026 `T2603.1` (owner В1): a close is the creditor's trust going to 0 - the closed row keeps limit 0
    # (was the last limit, 150) and the request time.
    assert (str(line.id), Decimal(str(line.limit)), line.status, dict(line.policy or {})) == (
        p1["trustline_id"], Decimal("0"), "closed", DEFAULT_POLICY)
    assert line.close_requested_at is not None

    audit = await trust_line_audit_rows(db_session, equivalent_codes=[w.eq])
    audit.sort(key=lambda r: ["TRUST_LINE_CREATE", "TRUST_LINE_UPDATE", "TRUST_LINE_CLOSE"].index(r.operation_type))
    require_target(
        _one_row_per_action(audit, ["TRUST_LINE_CREATE", "TRUST_LINE_UPDATE", "TRUST_LINE_CLOSE"], w)
        # 024 `T2413.2`: the rows record the operations; no check ran, so no checksums.
        and all((r.state_checksum_before, r.state_checksum_after, r.verification_passed) == ("", "", None)
                for r in audit)
        and per_action == [2, 2, 2],
        f"Interact: audit rows {[(r.operation_type, r.affected_participants) for r in audit]}, "
        f"checkpoints per action {per_action}",
    )


@pytest.mark.asyncio
async def test_interact_create_keeps_its_existing_debt_check(client, db_session, interact, monkeypatch) -> None:
    """Decision 5: Interact create refuses a limit below the debt that already exists - the service does not."""

    w = interact
    rows = await _seed_interact(db_session, w)
    async with debt_fixture_setup(db_session, label="p021-interact-debt"):
        db_session.add(Debt(debtor_id=rows.b_id, creditor_id=rows.a_id, equivalent_id=rows.eq_id,
                            amount=Decimal("50")))
    await db_session.commit()
    checkpoints = TrustLineBatchPoints(monkeypatch)

    below = await _action(client, w, "trustline-create", limit="40")

    # ── controls: the refusal is the action's own, before any write ─────────────────────────────────
    assert below.status_code == 409, below.text
    assert below.json()["code"] == "USED_EXCEEDS_NEW_LIMIT", below.json()
    assert await _live_line(db_session, rows) is None, "a line below the existing debt was created"
    assert await trust_line_audit_rows(db_session, equivalent_codes=[w.eq]) == []
    assert checkpoints.count == 0, "the refusal computed a checkpoint: it came after the first write"

    above = await _action(client, w, "trustline-create", limit="60")
    assert above.status_code == 200, above.text
    line = await _live_line(db_session, rows)
    assert line is not None and Decimal(str(line.limit)) == Decimal("60")

    audit = await trust_line_audit_rows(db_session, equivalent_codes=[w.eq])
    require_target(
        _one_row_per_action(audit, ["TRUST_LINE_CREATE"], w) and checkpoints.count == 2,
        f"Interact create above the debt: audit rows {len(audit)}, checkpoints {checkpoints.count}",
    )


@pytest.mark.parametrize("action", ["trustline-create", "trustline-update", "trustline-close"])
@pytest.mark.asyncio
async def test_a_failed_interact_action_is_rolled_back_by_its_handler(
    client, db_session, interact, monkeypatch, action
) -> None:
    w = interact
    rows = await _seed_interact(db_session, w)
    if action != "trustline-create":
        db_session.add(TrustLine(from_participant_id=rows.a_id, to_participant_id=rows.b_id,
                                 equivalent_id=rows.eq_id, limit=Decimal("100"), status="active",
                                 policy=dict(DEFAULT_POLICY)))
        await db_session.commit()
    body = {"trustline-create": {"limit": "70"}, "trustline-update": {"new_limit": "70"}, "trustline-close": {}}
    checkpoints = TrustLineBatchPoints(monkeypatch)
    checkpoints.fail_on_call = 2  # one equivalent: call 2 is the after-mutation checkpoint

    raised: BaseException | None = None
    response = None
    try:
        response = await _action(client, w, action, **body[action])
    except Exception as exc:  # the handler re-raises after its rollback; the ASGI client re-raises it here
        raised = exc

    await db_session.commit()  # a later commit on the request's session
    line = await _live_line(db_session, rows)
    state = None if line is None else (Decimal(str(line.limit)), line.status)
    audit = await trust_line_audit_rows(db_session, equivalent_codes=[w.eq])
    unchanged = None if action == "trustline-create" else (Decimal("100"), "active")

    require_target(
        checkpoints.count == 2
        and raised is not None and "forced trust-line batch failure" in str(raised)
        and state == unchanged
        and audit == [],
        f"{action}: checkpoint failure point reached: {checkpoints.count == 2} ({checkpoints.count} computations); "
        f"raised {raised!r}; response {getattr(response, 'status_code', None)}; line after {state} "
        f"(expected {unchanged}); {len(audit)} audit rows",
    )


# -------------------------------------------------------------------------------------------------- inject


async def _seed_inject(factory) -> SimpleNamespace:
    n = uuid.uuid4().hex[:6].upper()
    async with factory() as s:
        e1 = Equivalent(code=f"P21J{n}", precision=2, is_active=True, metadata_={})
        e2 = Equivalent(code=f"P21K{n}", precision=2, is_active=True, metadata_={})
        a, b = (
            Participant(pid=pid, display_name=pid, public_key=_simulated_key(pid), type="person",
                        status="active", profile={})
            for pid in (f"P21J_A_{n}", f"P21J_B_{n}")
        )
        s.add_all([e1, e2, a, b])
        await s.commit()
    for eq in (e1, e2):
        async with factory() as s:
            await take_baseline(s, eq.id)
            await s.commit()
    return SimpleNamespace(n=n, e1=e1, e2=e2, a=a, b=b, c=f"P21J_C_{n}")


def _inject_run(w, effects: list[dict[str, Any]]):
    scenario = {
        "equivalents": [w.e1.code, w.e2.code],
        "participants": [{"id": w.a.pid}, {"id": w.b.pid}],
        "trustlines": [],
        "behaviorProfiles": [],
        "events": [{"type": "inject", "time": 0, "effects": effects}],
    }
    run = RunRecord(run_id=f"p021-j-{w.n}", scenario_id="p021-inject", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1
    run.sim_time_ms = 1_000
    run.intensity_percent = 0
    run._real_seeded = True
    run._real_participants = [(w.a.id, w.a.pid), (w.b.id, w.b.pid)]
    run._real_equivalents = sorted([w.e1.code, w.e2.code])
    run._edges_by_equivalent = {}
    run._real_viz_by_eq = {}
    artifacts = _Artifacts()
    return run, scenario, artifacts, _runner(run, scenario, artifacts)


async def _inject_lines(factory, w) -> list[tuple]:
    async with factory() as s:
        pid_of = dict((await s.execute(select(Participant.id, Participant.pid))).all())
        code_of = {w.e1.id: w.e1.code, w.e2.id: w.e2.code}
        found = (
            await s.execute(select(TrustLine).where(TrustLine.equivalent_id.in_([w.e1.id, w.e2.id])))
        ).scalars().all()
        return sorted(
            (pid_of[t.from_participant_id], pid_of[t.to_participant_id], code_of[t.equivalent_id],
             Decimal(str(t.limit)), t.status, dict(t.policy or {}))
            for t in found
        )


async def _inject_audit(factory, w) -> list:
    async with factory() as s:
        return await trust_line_audit_rows(s, equivalent_codes=[w.e1.code, w.e2.code])


@pytest.mark.usefixtures("tier_on_a_clone")
@pytest.mark.asyncio
async def test_an_inject_event_writes_one_audit_row_per_line_and_one_checkpoint_pair_per_equivalent(
    factory, monkeypatch
) -> None:
    w = await _seed_inject(factory)
    effects = [
        {"op": "create_trustline", "from": w.a.pid, "to": w.b.pid, "equivalent": w.e1.code, "limit": "10"},
        {"op": "create_trustline", "from": w.a.pid, "to": w.b.pid, "equivalent": w.e2.code, "limit": "20"},
        {"op": "add_participant", "participant": {"id": w.c, "name": "C"},
         "initial_trustlines": [{"sponsor": w.a.pid, "equivalent": w.e1.code, "limit": "5"}]},
    ]
    run, scenario, artifacts, runner = _inject_run(w, effects)
    checkpoints = TrustLineBatchPoints(monkeypatch)

    async with factory() as session:
        await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)
        assert not session.in_transaction()

    # ── controls: the event applied exactly what it applied before 021 ──────────────────────────────
    assert run._real_fired_scenario_event_indexes == {0}
    notes = [p["scenario"] for p in artifacts.events if p.get("type") == "note"]
    assert [note["description"] for note in notes] == ["inject applied"], notes
    assert notes[0]["stats"] == {"applied": 3, "skipped": 0, "total_amount": {}}, notes[0]  # INTENTIONAL, 028 F-028-30: the total is per equivalent
    assert await _inject_lines(factory, w) == sorted([
        (w.a.pid, w.b.pid, w.e1.code, Decimal("10"), "active", DEFAULT_POLICY),
        (w.a.pid, w.b.pid, w.e2.code, Decimal("20"), "active", DEFAULT_POLICY),
        (w.a.pid, w.c, w.e1.code, Decimal("5"), "active", DEFAULT_POLICY),
    ])

    audit = await _inject_audit(factory, w)
    described = sorted(
        (r.equivalent_code, r.affected_participants.get("from"), r.affected_participants.get("to")) for r in audit
    )
    pairs = {}
    for r in audit:
        pairs.setdefault(r.equivalent_code, set()).add((r.state_checksum_before, r.state_checksum_after))
    require_target(
        described == sorted([(w.e1.code, w.a.pid, w.b.pid), (w.e2.code, w.a.pid, w.b.pid),
                             (w.e1.code, w.a.pid, w.c)])
        and all(r.operation_type == "TRUST_LINE_CREATE" and is_transaction_scoped(r) for r in audit)
        and all(len(v) == 1 for v in pairs.values())
        and checkpoints.count == 2 * 2,
        f"inject: audit rows {described}, checkpoints {checkpoints.count}",
    )


@pytest.mark.usefixtures("tier_on_a_clone")
@pytest.mark.parametrize(
    "fail_on_call",
    [
        # The before-checkpoint of the SECOND line (inside its effect handler, after the first line was
        # staged): the failure has to get past the handler's "skipped" to reach the owner.
        pytest.param(2, id="second_line_before_checkpoint"),
        # The first after-checkpoint (end of staging), after both lines were written and flushed.
        pytest.param(3, id="after_checkpoint"),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_inject_event_is_rolled_back_by_its_owner(factory, monkeypatch, fail_on_call) -> None:
    w = await _seed_inject(factory)
    effects = [
        {"op": "create_trustline", "from": w.a.pid, "to": w.b.pid, "equivalent": w.e1.code, "limit": "10"},
        {"op": "create_trustline", "from": w.a.pid, "to": w.b.pid, "equivalent": w.e2.code, "limit": "20"},
    ]
    run, scenario, _artifacts, runner = _inject_run(w, effects)
    checkpoints = TrustLineBatchPoints(monkeypatch)
    # Two equivalents: calls 1-2 are the before-checkpoints of the two lines, calls 3-4 the after-checkpoints.
    checkpoints.fail_on_call = fail_on_call

    raised: BaseException | None = None
    async with factory() as session:
        try:
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)
        except Exception as exc:
            raised = exc
        await session.commit()  # a later commit on the same session

    lines = await _inject_lines(factory, w)
    audit = await _inject_audit(factory, w)
    require_target(
        checkpoints.count == fail_on_call
        and raised is not None and "forced trust-line batch failure" in str(raised)
        and lines == []
        and audit == []
        and run._real_fired_scenario_event_indexes == set(),
        f"inject: checkpoint failure point reached: {checkpoints.count == fail_on_call} "
        f"({checkpoints.count} computations); "
        f"raised {raised!r}; lines after {lines}; {len(audit)} audit rows; "
        f"fired {sorted(run._real_fired_scenario_event_indexes)}",
    )
