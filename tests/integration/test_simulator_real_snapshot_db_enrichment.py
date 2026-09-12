import asyncio
from decimal import Decimal, ROUND_DOWN

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.conftest import TestingSessionLocal


@pytest_asyncio.fixture
async def stopped_runs(client, auth_headers):
    """Stop whatever run this test started, pass or fail.

    T1525 (2026-09-12): when the test failed early it left its run ticking into the NEXT test, which
    then reported `Event loop is closed` from an aiosqlite thread and a never-awaited
    `AsyncSession.close`. A run this test starts is this test's to stop; the HTTP stop is the real
    path, and the runtime call behind it covers the case where the client is already gone.
    """
    started: list[str] = []
    try:
        yield started
    finally:
        from app.core.simulator.runtime import runtime

        for run_id in started:
            try:
                await client.post(
                    f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers
                )
            except Exception:
                pass
            try:
                await runtime.stop(run_id)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_real_mode_graph_snapshot_enriches_used_and_net_sign(
    client, auth_headers, db_session, stopped_runs
):
    # Start a real-mode run from fixture scenario.
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": "greenfield-village-100-realistic-v2", "mode": "real", "intensity_percent": 0},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    stopped_runs.append(run_id)

    # Seeding occurs on the first real tick (heartbeat loop), on the runner's OWN session. Poll for
    # it to land - and end this session's read transaction before every poll.
    #
    # T1525 (2026-09-12): SQLite reads now take a real snapshot, so a poll loop inside one
    # transaction is blind by construction - the snapshot is fixed at the first SELECT and the
    # runner's later commit can never appear in it. This is not a SQLite quirk to work around: any
    # backend at a repeatable-read or stricter isolation behaves the same. Waiting for another
    # transaction's write means re-reading in a NEW transaction.
    eq = None
    for _ in range(15):
        await db_session.rollback()
        eq = (
            await db_session.execute(select(Equivalent).where(Equivalent.code == "UAH"))
        ).scalar_one_or_none()
        if eq is not None:
            break
        await asyncio.sleep(0.2)
    assert eq is not None

    tl = (
        await db_session.execute(select(TrustLine).where(TrustLine.equivalent_id == eq.id))
    ).scalars().first()
    assert tl is not None

    creditor = await db_session.get(Participant, tl.from_participant_id)
    debtor = await db_session.get(Participant, tl.to_participant_id)
    assert creditor is not None
    assert debtor is not None

    # Captured while these instances are live: the rollback below expires every ORM object in the
    # session, and in async SQLAlchemy a refresh triggered by plain attribute access raises
    # MissingGreenlet. Everything this test needs after the write is a plain value from here on.
    eq_id = eq.id
    tl_limit = tl.limit
    creditor_id, creditor_pid = creditor.id, creditor.pid
    debtor_id, debtor_pid = debtor.id, debtor.pid

    amount = Decimal("12.34")

    # T1525: this setup mutation runs in its OWN short session whose FIRST statement is a write, and
    # which commits immediately. THAT is the property which makes it safe, and it is the whole
    # reason the session is separate: a transaction that opens with a write takes the write lock up
    # front, so it holds no read snapshot that a concurrent commit could make stale. If the
    # simulator heartbeat is mid-commit on its own connection, this writer WAITS for the lock -
    # which `busy_timeout` does cure - instead of being refused outright for a stale snapshot, which
    # no amount of waiting can cure.
    #
    # Do not "simplify" this back onto `db_session`. That session has been reading since the poll
    # above, so a write through it would be read -> write -> commit inside one transaction, which is
    # exactly the shape that fails under a concurrent writer (measured: it failed once in four full
    # tier runs even with the write moved to the last possible moment).
    async with TestingSessionLocal() as setup:
        await setup.execute(
            delete(Debt).where(
                Debt.equivalent_id == eq_id,
                Debt.creditor_id == creditor_id,
                Debt.debtor_id == debtor_id,
            )
        )
        setup.add(
            Debt(
                equivalent_id=eq_id,
                creditor_id=creditor_id,
                debtor_id=debtor_id,
                amount=amount,
            )
        )
        await setup.commit()

    # The snapshot request below reads through THIS session - the `client` fixture overrides
    # `get_db` with it - so its transaction must not predate the write above, or the API would
    # serve a snapshot in which the debt does not exist yet.
    await db_session.rollback()

    snap = await client.get(
        f"/api/v1/simulator/runs/{run_id}/graph/snapshot",
        headers=auth_headers,
        params={"equivalent": "UAH"},
    )
    assert snap.status_code == 200, snap.text
    data = snap.json()

    nodes = {n["id"]: n for n in (data.get("nodes") or [])}
    links = data.get("links") or []

    link = next(
        l
        for l in links
        if l.get("source") == creditor_pid and l.get("target") == debtor_pid
    )

    assert link.get("used") == "12.34"

    expected_available = (tl_limit - amount).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if expected_available < 0:
        expected_available = Decimal("0.00")
    assert link.get("available") == format(expected_available, "f")

    # Net sign is derived from debts: creditor positive, debtor negative.
    assert nodes[creditor_pid].get("net_sign") == 1
    assert nodes[debtor_pid].get("net_sign") == -1

    # UI node appearance is driven by viz_color_key (see simulator-ui/v2/src/vizMapping.ts).
    # For debtors (negative net), backend should assign a debt bin key.
    assert isinstance(nodes[debtor_pid].get("viz_color_key"), str)
    assert str(nodes[debtor_pid].get("viz_color_key")).startswith("debt-")

    # For creditors/neutral, backend should keep person/business semantics.
    assert nodes[creditor_pid].get("viz_color_key") in ("person", "business")

    # Node sizing must be present (fixtures semantics).
    assert nodes[creditor_pid].get("viz_size") is not None
    assert nodes[debtor_pid].get("viz_size") is not None
    assert nodes[creditor_pid]["viz_size"]["w"] > 0
    assert nodes[creditor_pid]["viz_size"]["h"] > 0

    # Link viz keys should be computed like fixtures.
    assert link.get("viz_width_key") in ("hairline", "thin", "mid", "thick")
    assert link.get("viz_alpha_key") in ("bg", "muted", "active", "hi")
