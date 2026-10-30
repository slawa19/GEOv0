"""054-A-1: `stop()` publishes the terminal `run_status(stopped)` only after the heartbeat is drained.

Defect (witness W-A1-1, 2026-10-30): `stop()` published `stopped` before it cancelled and awaited the heartbeat whose
tick was inside the money COMMIT. The committed payments' `tx.updated` got a larger event id than `stopped`, the live
HTTP SSE stream (`_run_events_stream`, which returns on `stopped`) never delivered them, and `stopped` carried
`committed_total` 0 although the money was durable.

Real path: `POST /simulator/runs` (real mode, the runtime's own heartbeat), real PostgreSQL (mode B), real
`POST .../stop`, real HTTP SSE `GET .../events` and the runtime's real dispatch. The only intervention is a barrier
that HOLDS the opening tick right after its REAL money commit landed, as a slow COMMIT acknowledgement would (no
exception is injected, no result is substituted). The control stops between ticks.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased

import app.core.simulator.money_replay as money_replay
from app.config import settings
from app.core.simulator.runtime import runtime
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.conftest import MODE_B, sessionmaker_of

pytestmark = MODE_B

SCENARIO = "riverside-town-50-realistic-v2"


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _create(client) -> str:
    r = await client.post(
        "/api/v1/simulator/runs",
        headers=_admin(),
        json={"scenario_id": SCENARIO, "mode": "real", "intensity_percent": 0},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["run_id"])


def _seq(event_id: str) -> int:
    return int(str(event_id).rsplit("_", 1)[-1])


def _parse_sse(body: str) -> list[dict]:
    out = []
    for block in body.split("\n\n"):
        data = [ln[len("data:"):].strip() for ln in block.splitlines() if ln.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def _is_stopped(e: dict) -> bool:
    return e.get("type") == "run_status" and e.get("state") == "stopped"


async def _debt(db_session, debtor: str, creditor: str, eq: str = "UAH") -> Decimal:
    a, b = aliased(Participant), aliased(Participant)
    async with sessionmaker_of(db_session)() as s:
        row = (
            await s.execute(
                select(Debt.amount)
                .join(a, a.id == Debt.debtor_id)
                .join(b, b.id == Debt.creditor_id)
                .join(Equivalent, Equivalent.id == Debt.equivalent_id)
                .where(a.pid == debtor, b.pid == creditor, Equivalent.code == eq)
            )
        ).scalar_one_or_none()
    return Decimal(row) if row is not None else Decimal("0")


@pytest.mark.parametrize("hold", [True, False], ids=["stop_during_commit", "control_stop_after_tick"])
@pytest.mark.asyncio
async def test_stopped_is_published_after_the_committed_tick_payments(client, db_session, monkeypatch, hold):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    inside, release = asyncio.Event(), asyncio.Event()
    original = money_replay.resolve_commit_under_cancellation
    calls = {"n": 0}

    async def held_resolution(*, commit, **kw):
        calls["n"] += 1
        if calls["n"] > 1:
            return await original(commit=commit, **kw)

        async def commit_then_hold():
            await commit()  # the REAL money commit of the opening tick
            inside.set()
            if hold:
                await release.wait()  # hold after the commit landed, as a slow COMMIT acknowledgement would

        return await original(commit=commit_then_hold, **kw)

    monkeypatch.setattr(money_replay, "resolve_commit_under_cancellation", held_resolution)
    run_id = await _create(client)
    run = runtime.get_run(run_id)
    stopped_published_while_held: list[dict] = []
    try:
        await asyncio.wait_for(inside.wait(), timeout=120)
        if not hold:  # control: the opening tick has published its payments before the stop
            async with asyncio.timeout(60):
                while not any(entry[3].get("type") == "tx.updated" for entry in run._event_buffer):
                    await asyncio.sleep(0.02)
        stream = asyncio.create_task(
            client.get(f"/api/v1/simulator/runs/{run_id}/events", headers=_admin(), params={"equivalent": "UAH"})
        )
        async with asyncio.timeout(30):
            while not run._subs:
                await asyncio.sleep(0.01)
        stop = asyncio.create_task(client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=_admin()))
        async with asyncio.timeout(30):
            while run.state != "stopped":
                await asyncio.sleep(0.01)
        if hold:
            # The state and the owner slot are released early; the terminal publication waits for the held tick.
            await asyncio.sleep(0.2)
            stopped_published_while_held = [entry[3] for entry in run._event_buffer if _is_stopped(entry[3])]
            assert not stream.done(), "the live stream returned while the committed tick was still held"
        release.set()
        streamed_resp = await asyncio.wait_for(stream, timeout=60)
        stop_resp = await asyncio.wait_for(stop, timeout=60)
        assert stop_resp.status_code == 200, stop_resp.text
    finally:
        release.set()
        try:
            await runtime.stop(run_id)
        except Exception:
            pass

    streamed = _parse_sse(streamed_resp.text)
    buffered = [entry[3] for entry in run._event_buffer]
    stopped = [e for e in buffered if _is_stopped(e)]
    assert stopped, "no run_status(stopped) was published"
    first_stopped = _seq(stopped[0]["event_id"])
    tx_buffered = [e for e in buffered if e.get("type") == "tx.updated"]
    tx_after_stopped = [e["event_id"] for e in tx_buffered if _seq(e["event_id"]) > first_stopped]
    tx_streamed = [e["event_id"] for e in streamed if e.get("type") == "tx.updated"]
    first = run._scenario_raw["events"][0]
    db_debt = await _debt(db_session, first["from"], first["to"])
    print(
        "054-A-1 hold=", hold,
        "streamed:", [e.get("type") + ("/" + str(e.get("state")) if e.get("type") == "run_status" else "") for e in streamed],
        "stopped:", stopped[0]["event_id"], "committed_total in stopped:", stopped[0].get("committed_total"),
        "run.committed_total:", run.committed_total, "tx.updated after stopped:", tx_after_stopped,
        "DB debt:", db_debt,
    )

    assert db_debt > 0
    assert streamed and _is_stopped(streamed[-1]), "the live stream must end on run_status(stopped)"
    assert not stopped_published_while_held, "stopped was published while the committed tick was still held"
    assert not tx_after_stopped, f"tx.updated published after stopped: {tx_after_stopped}"
    assert tx_buffered, "the committed tick published no tx.updated"
    assert run.committed_total > 0
    # `stopped` describes what is committed.
    assert stopped[0].get("committed_total") == run.committed_total
    if hold:
        # Every payment the held tick committed reached the live stream before `stopped`, and `stopped` counts them.
        assert tx_streamed == [e["event_id"] for e in tx_buffered]
        assert stopped[0].get("committed_total") == len(tx_buffered)
