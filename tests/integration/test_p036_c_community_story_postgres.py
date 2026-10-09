"""036 slice C (T3630): `community-story-10` is EXECUTED, not only validated - from its first episode to its last.

WHAT THE TEST DRIVES. The PRODUCT path of a run: `runtime.create_run` (mode `real`, intensity left to the scenario's
default), the product `_heartbeat_loop` (its `asyncio.sleep` is virtual, so no wall-clock time is spent; every tick is the
real `tick_real_mode` on PostgreSQL: seeding of the scenario into the database, the due `inject`s, the money phase, the
scripted clearing, the periodic clearing at its default cadence), and `runtime.resume` at every pause. Nothing is stubbed
but the clock and the storage side-writes of `install_tick_stand`.

WHAT IT ASSERTS, by the run's own report (`RunStatus.episode_progress`) AND by the ledger (`debts`, read from the database):

* the run pauses exactly after the episodes that ask for it, in order, and the story ends;
* every scripted payment is `done` with the payment as the scenario wrote it, and the debts after each pause are the ones
  the caption says (a direct purchase; a purchase through a middleman; debts piling up; the circle that closes; the same
  debts after the clearing);
* the clearing is `done`, committed exactly ONE cycle, of the amount the circle allows (the smallest debt on it, 40.00) and
  over exactly the edges of the episode's `expected_cycle`;
* the one refusal of the story (a payment to a participant nobody trusts) is `refused` with the code of its `tx.failed`
  and moves nothing; nothing is `incomplete` at the end;
* the final debts equal the expected ones; the anchors of the episodes arrive as real events.

WHY THE STORY IS BUILT THE WAY IT IS (and what the test therefore depends on). Declared in
`docs/ru/simulator/scenarios-and-engine.md`: the background intensity is 0; the PERIODIC clearing (every 25th tick, a
process setting) is not switched off by a scenario, so no cycle exists on the ticks 25, 50 and 75 - the circle is closed on
tick 38 and cleared on tick 43; the order inside a tick is by event TYPE (inject, payment, clearing), not by `time`; the
scripted clearing may end `incomplete` within its 250 ms budget and is then finished by the next tick - the test waits for
`done` and does not depend on how many ticks that took; `inject` needs the process flag, which the test sets.

NOT SEEN HERE. The Simulator UI (the story is never rendered), the demo snapshots, and a real clock.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased

import app.core.simulator.runtime_impl as runtime_impl
from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_utils import run_to_status
from app.core.payments.router import PaymentRouter
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.integration.test_p021_trust_drift_is_audited_postgres import factory  # noqa: F401 - a fixture
from tests.simulator_tick_stand import install_tick_stand

SCENARIO_ID = "community-story-10"
STORY = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "simulator" / SCENARIO_ID / "scenario.json").read_text(encoding="utf-8"))
EVENTS = STORY["events"]
PAUSING = [i for i, e in enumerate(EVENTS) if e.get("pause_after") is True]
D = Decimal


async def _debts(factory) -> dict[tuple[str, str], Decimal]:  # noqa: F811
    """{(debtor pid, creditor pid): amount} of the story's participants, in the story's equivalent, zero rows left out."""

    debtor, creditor = aliased(Participant), aliased(Participant)
    async with factory() as s:
        rows = (await s.execute(
            select(debtor.pid, creditor.pid, Debt.amount)
            .join(debtor, Debt.debtor_id == debtor.id).join(creditor, Debt.creditor_id == creditor.id)
            .join(Equivalent, Debt.equivalent_id == Equivalent.id).where(Equivalent.code == "UAH")
            .where(debtor.pid.like("cs\\_%", escape="\\"))
        )).all()
    return {(d.removeprefix("cs_"), c.removeprefix("cs_")): Decimal(str(a)) for d, c, a in rows if Decimal(str(a)) != 0}


def _cycle_edges(pids: list[str]) -> set[tuple[str, str]]:
    return {(pids[i], pids[(i + 1) % len(pids)]) for i in range(len(pids))}


# What the debts are after each pause (keyed by the index of the episode the run paused after): the captions' claims.
AFTER = {
    0: {},
    1: {("olena", "bakery"): D("40")},
    2: {("olena", "bakery"): D("40"), ("taras", "shop"): D("60"), ("shop", "farm"): D("60")},
    5: {("olena", "bakery"): D("40"), ("taras", "shop"): D("60"), ("shop", "farm"): D("60"), ("farm", "mill"): D("50"), ("bakery", "mill"): D("30")},
    7: {("olena", "bakery"): D("40"), ("taras", "shop"): D("60"), ("shop", "farm"): D("60"), ("farm", "mill"): D("50"), ("bakery", "mill"): D("30"),
        ("mill", "dmytro"): D("40"), ("dmytro", "shop"): D("40")},
}
AFTER[8] = {("olena", "bakery"): D("40"), ("taras", "shop"): D("60"), ("shop", "farm"): D("20"), ("farm", "mill"): D("10"), ("bakery", "mill"): D("30")}
AFTER[9] = AFTER[10] = AFTER[11] = AFTER[8]  # the freeze and the refusal move no debt
FINAL = AFTER[8]


class _Story:
    """The result of one run of the story: the pauses with the debts seen at each, the final report and the events."""

    def __init__(self) -> None:
        self.pauses: list[dict] = []
        self.events: list[dict] = []
        self.progress: dict = {}
        self.final_debts: dict = {}
        self.final_state = ""
        self.last_tick = 0
        self.intensity = None


async def _run_story(factory, monkeypatch) -> _Story:  # noqa: F811
    install_tick_stand(monkeypatch, factory)
    # The router keeps a graph per equivalent code for the life of the process; every test has a database of its own, with
    # participants of its own, so the graph of an earlier test would route nothing here (measured: ROUTING_NO_ROUTE on the
    # second run of the story in one session). `world()` of the other stands does the same.
    PaymentRouter.invalidate_cache("UAH")
    monkeypatch.setattr(runtime._real_runner, "_real_enable_inject", True)  # the process flag the story's `inject`s need
    assert runtime._clearing_every_n_ticks == 25, runtime._clearing_every_n_ticks  # the cadence the story is built around

    real_sleep = asyncio.sleep

    async def virtual_sleep(_delay, *_a, **_kw):
        await real_sleep(0)

    class _VirtualAsyncio:
        sleep = staticmethod(virtual_sleep)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    monkeypatch.setattr(runtime_impl, "asyncio", _VirtualAsyncio())

    story = _Story()
    original_publish = runtime._sse.publish_event

    def recording_publish(*, run_id, payload_factory):
        payload = original_publish(run_id=run_id, payload_factory=payload_factory)
        if payload is not None:
            story.events.append(payload)
        return payload

    monkeypatch.setattr(runtime._sse, "publish_event", recording_publish)

    run_id = await runtime.create_run(scenario_id=SCENARIO_ID, mode="real", intensity_percent=None)
    run = runtime.get_run(run_id)
    story.intensity = run.intensity_percent
    try:
        previous: set[int] = set()
        complete_at: int | None = None
        async with asyncio.timeout(240):
            while True:
                if run.state == "paused":
                    now = set(run._real_fired_scenario_event_indexes)
                    story.pauses.append({"spent": sorted(now - previous), "tick": run.tick_index, "debts": await _debts(factory)})
                    previous = now
                    await runtime.resume(run_id)
                elif run.state in ("error", "stopping", "stopped"):
                    raise AssertionError(f"the run left the story: {run.state} {run.last_error}")
                elif len(run._real_fired_scenario_event_indexes) == len(EVENTS):
                    # The last episode is spent inside a tick and the pause it asks for comes at the end of that same tick:
                    # the story is over once a LATER tick has started.
                    complete_at = run.tick_index if complete_at is None else complete_at
                    if run.tick_index > complete_at:
                        break
                await real_sleep(0.02)
        story.progress = {p.index: p for p in (run_to_status(run).episode_progress or [])}
        story.final_debts = await _debts(factory)
        story.final_state = run.state
        story.last_tick = run.tick_index
    finally:
        await runtime.stop(run_id)
    return story


@pytest.mark.asyncio
async def test_the_story_pauses_where_it_asks_to_and_ends(factory, monkeypatch) -> None:  # noqa: F811
    s = await _run_story(factory, monkeypatch)

    assert s.intensity == 0, s.intensity  # the scenario's default, not a number the caller sent
    # a pause comes after the tick that spent an episode asking for it; the episodes without the request, spent on earlier ticks, are
    # reported by the pause that follows them
    expected = [list(range(([-1, *PAUSING][k]) + 1, PAUSING[k] + 1)) for k in range(len(PAUSING))]
    assert [p["spent"] for p in s.pauses] == expected, [p["spent"] for p in s.pauses]
    assert s.last_tick >= EVENTS[-1]["time"] // 1000, s.last_tick  # the sim clock really ran to the last episode


@pytest.mark.asyncio
async def test_the_debts_after_every_pause_are_the_ones_the_caption_says(factory, monkeypatch) -> None:  # noqa: F811
    s = await _run_story(factory, monkeypatch)

    seen = {p["spent"][-1]: p["debts"] for p in s.pauses}
    assert seen == {i: AFTER[i] for i in PAUSING}, {i: (seen.get(i), AFTER[i]) for i in PAUSING if seen.get(i) != AFTER[i]}
    assert s.final_debts == FINAL, s.final_debts


@pytest.mark.asyncio
async def test_every_scripted_payment_is_done_the_clearing_cleared_exactly_its_cycle_and_the_refusal_is_a_refusal(factory, monkeypatch) -> None:  # noqa: F811
    s = await _run_story(factory, monkeypatch)

    payments = [i for i, e in enumerate(EVENTS) if e["type"] == "payment"]
    clearing = [i for i, e in enumerate(EVENTS) if e["type"] == "clearing"]
    refused = 10
    assert EVENTS[refused]["type"] == "payment" and clearing == [8]  # the shape the assertions below are written for

    for i in payments:
        got = s.progress.get(i)
        if i == refused:
            continue
        e = EVENTS[i]
        assert got is not None and got.status == "done" and got.kind == "payment", (i, got)
        assert (got.payment.from_, got.payment.to, got.payment.amount, got.payment.equivalent) == (e["from"], e["to"], e["amount"], e["equivalent"]), (i, got.payment)

    c = s.progress.get(8)
    expected_edges = _cycle_edges([p.removeprefix("cs_") for p in EVENTS[8]["expected_cycle"]])
    assert c is not None and c.status == "done" and c.kind == "clearing", c
    assert c.cleared_cycles == 1 and len(c.cycles) == 1, c
    assert c.cycles[0].cleared_amount == "40.00", c.cycles[0].cleared_amount  # the smallest debt on the circle
    assert {(e.from_.removeprefix("cs_"), e.to.removeprefix("cs_")) for e in c.cycles[0].edges} == expected_edges, c.cycles[0].edges

    r = s.progress.get(refused)
    failed = [e for e in s.events if e.get("type") == "tx.failed"]
    assert len(failed) == 1 and r is not None and r.status == "refused", (r, failed)
    assert r.reason == failed[0]["error"]["code"], (r.reason, failed[0]["error"])  # the report says what the core said

    assert sorted(s.progress) == sorted([*payments, 8]), sorted(s.progress)  # injects that were applied are not "progress"; notes are not tracked
    assert not [p for p in s.progress.values() if p.status == "incomplete"], s.progress
    assert s.final_state == "running"


@pytest.mark.asyncio
async def test_the_anchors_of_the_episodes_arrive_as_real_events(factory, monkeypatch) -> None:  # noqa: F811
    """Each episode's anchor is matched by an event the run really published, in the fields the schema says it matches on."""

    s = await _run_story(factory, monkeypatch)

    def matches(anchor: dict, event: dict) -> bool:
        if event.get("type") != anchor["event"]:
            return False
        if anchor["event"] == "tx.updated":
            return all(str(event.get(k)) == str(anchor[k]) for k in ("from", "to", "amount", "equivalent"))
        if anchor["event"] == "tx.failed":
            return all(str(event.get(k)) == str(anchor[k]) for k in ("from", "to", "equivalent"))
        return True

    missing = [i for i, e in enumerate(EVENTS) if "anchor" in e and not any(matches(e["anchor"], ev) for ev in s.events)]
    assert missing == [], f"episodes whose anchor never arrived: {missing}; event types {sorted({ev.get('type') for ev in s.events})}"
    assert sum(1 for ev in s.events if ev.get("type") == "tx.updated") == 6  # nothing but the story's six payments moved
