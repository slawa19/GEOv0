"""036 slice C (T3630): `community-story-10` is EXECUTED, not only validated - from its first episode to its last.

WHAT THE TEST DRIVES. The PRODUCT path of a run: `runtime.create_run` (mode `real`, intensity left to the scenario's
default), the product `_heartbeat_loop` (its `asyncio.sleep` is virtual, so no wall-clock time is spent; every tick is the
real `tick_real_mode` on PostgreSQL: seeding of the scenario into the database, the due `inject`s, the money phase, the
scripted clearing, the periodic clearing at its default cadence), and `runtime.resume` at every pause. Nothing is stubbed
but the clock and the storage side-writes of `install_tick_stand`. The scenario is not in the default allowlist (S9 adds it
with a launch flow that supplies its preconditions): the tests set the allowlist override, as an operator would.

WHAT IT ASSERTS, by the run's own report (`RunStatus.episode_progress`) AND by the ledger (`debts`, read from the database):

* the run pauses exactly after the episodes that ask for it (a LITERAL list here, not derived from the fixture), in order;
* every scripted payment is `done` with the payment as the scenario wrote it, and the debts after each pause are the ones
  the caption says (a direct purchase; a purchase through a middleman; debts piling up; the circle that closes; the same
  debts after the clearing); Taras is `active` until the freeze and `suspended` after it;
* the clearing is `done`, committed exactly ONE cycle, of the amount the circle allows (the smallest debt on it, 40.00) and
  over exactly the edges of the episode's `expected_cycle`, and carries no `reason` (the cycle announced was the one cleared);
* the one refusal of the story is `refused` with `ROUTING_NO_ROUTE` (the code of its `tx.failed`) and moves nothing; nothing
  is `incomplete` at the end;
* each episode's anchor is matched IN ORDER by an event of its own, consumed once, with the content the episode announces
  (the payment's own from/to/amount, the clearing's edges, the newcomer in `added_nodes`, Taras in `frozen_nodes`).

THE DEGRADED RUNS, pinned one case each, so that what the scenario document says about them is what happens:
the process flag for injects off (the default of the product); a SECOND run on the same database (the story is single-use per
database). Intensity 30, which the Simulator UI sends, is NOT pinned here: background payments are not deterministic; the
document says what it does.

WHY THE STORY IS BUILT THE WAY IT IS (and what the test therefore depends on). Declared in
`docs/ru/simulator/scenarios-and-engine.md`: the background intensity is 0; the PERIODIC clearing (every 25th tick, a
process setting) is not switched off by a scenario, so no cycle exists on the ticks 25, 50 and 75 - the circle is closed on
tick 38 and cleared on tick 43; the order inside a tick is by event TYPE (inject, payment, clearing), not by `time`; the
scripted clearing may end `incomplete` within its 250 ms budget and is then finished by the next tick - the test waits for
`done` and does not depend on how many ticks that took; `inject` needs the process flag, which the tests set (or unset).

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
from app.core.payments.router import PaymentRouter
from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_utils import run_to_status
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.integration.test_p021_trust_drift_is_audited_postgres import factory  # noqa: F401 - a fixture
from tests.simulator_tick_stand import install_tick_stand

SCENARIO_ID = "community-story-10"
STORY = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "simulator" / SCENARIO_ID / "scenario.json").read_text(encoding="utf-8"))
EVENTS = STORY["events"]
D = Decimal

#: The episodes after which the run pauses, written out: a fixture that loses a `pause_after` must turn this red.
PAUSING = [0, 1, 2, 5, 7, 8, 9, 10, 11]
PAYMENTS = [1, 2, 3, 4, 6, 7, 10]
REFUSED = 10
CLEARING = 8


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


async def _status_of(factory, pid: str) -> str | None:  # noqa: F811
    async with factory() as s:
        return (await s.execute(select(Participant.status).where(Participant.pid == pid))).scalar_one_or_none()


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
        self.taras_status: str | None = None
        self.dmytro_status: str | None = None
        self.listed = False


async def _run_story(factory, monkeypatch, *, inject: bool = True) -> _Story:  # noqa: F811
    """One run of the story to its end. Every patch is undone when it returns, so a second run on the same database is clean."""

    story = _Story()
    with monkeypatch.context() as mp:
        install_tick_stand(mp, factory)
        # The router keeps a graph per equivalent code for the life of the process; every test has a database of its own, with
        # participants of its own, so the graph of an earlier test would route nothing here (measured: ROUTING_NO_ROUTE on the
        # second run of the story in one session). `world()` of the other stands does the same.
        PaymentRouter.invalidate_cache("UAH")
        mp.setattr(runtime._real_runner, "_real_enable_inject", inject)  # the process flag the story's `inject`s need
        mp.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", SCENARIO_ID)  # the operator's override: not in the default list
        story.listed = SCENARIO_ID in [s.scenario_id for s in runtime.list_scenarios()]
        assert runtime._clearing_every_n_ticks == 25, runtime._clearing_every_n_ticks  # the cadence the story is built around

        real_sleep = asyncio.sleep

        async def virtual_sleep(_delay, *_a, **_kw):
            await real_sleep(0)

        class _VirtualAsyncio:
            sleep = staticmethod(virtual_sleep)

            def __getattr__(self, name):
                return getattr(asyncio, name)

        mp.setattr(runtime_impl, "asyncio", _VirtualAsyncio())

        original_publish = runtime._sse.publish_event

        def recording_publish(*, run_id, payload_factory):
            payload = original_publish(run_id=run_id, payload_factory=payload_factory)
            if payload is not None:
                story.events.append(payload)
            return payload

        mp.setattr(runtime._sse, "publish_event", recording_publish)

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
                        story.pauses.append({
                            "spent": sorted(now - previous), "tick": run.tick_index, "debts": await _debts(factory),
                            "taras": await _status_of(factory, "cs_taras"),
                        })
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
            story.taras_status = await _status_of(factory, "cs_taras")
            story.dmytro_status = await _status_of(factory, "cs_dmytro")
        finally:
            await runtime.stop(run_id)
    return story


def _expected_pauses() -> list[list[int]]:
    return [list(range(([-1, *PAUSING][k]) + 1, PAUSING[k] + 1)) for k in range(len(PAUSING))]


@pytest.mark.asyncio
async def test_the_story_pauses_where_it_asks_to_and_ends(factory, monkeypatch) -> None:  # noqa: F811
    s = await _run_story(factory, monkeypatch)

    assert s.listed  # the allowlist override makes the story selectable; the default list does not carry it
    assert s.intensity == 0, s.intensity  # the scenario's default, not a number the caller sent
    # a pause comes after the tick that spent an episode asking for it; the episodes without the request, spent on earlier
    # ticks, are reported by the pause that follows them
    assert [p["spent"] for p in s.pauses] == _expected_pauses(), [p["spent"] for p in s.pauses]
    assert s.last_tick >= EVENTS[-1]["time"] // 1000, s.last_tick  # the sim clock really ran to the last episode


@pytest.mark.asyncio
async def test_the_debts_after_every_pause_are_the_ones_the_caption_says_and_the_freeze_is_executed(factory, monkeypatch) -> None:  # noqa: F811
    s = await _run_story(factory, monkeypatch)

    seen = {p["spent"][-1]: p["debts"] for p in s.pauses}
    assert seen == {i: AFTER[i] for i in PAUSING}, {i: (seen.get(i), AFTER[i]) for i in PAUSING if seen.get(i) != AFTER[i]}
    assert s.final_debts == FINAL, s.final_debts
    # the freeze is a fact of the database, not only a report: Taras is active at every pause before episode 9 and suspended from it
    assert {p["spent"][-1]: p["taras"] for p in s.pauses} == {**{i: "active" for i in (0, 1, 2, 5, 7, 8)}, **{i: "suspended" for i in (9, 10, 11)}}
    assert (s.taras_status, s.dmytro_status) == ("suspended", "active")  # and the newcomer exists, active


@pytest.mark.asyncio
async def test_every_scripted_payment_is_done_the_clearing_cleared_exactly_its_cycle_and_the_refusal_is_a_refusal(factory, monkeypatch) -> None:  # noqa: F811
    s = await _run_story(factory, monkeypatch)

    assert [i for i, e in enumerate(EVENTS) if e["type"] == "payment"] == PAYMENTS  # the shape the assertions below are written for
    assert [i for i, e in enumerate(EVENTS) if e["type"] == "clearing"] == [CLEARING]

    for i in PAYMENTS:
        if i == REFUSED:
            continue
        got, e = s.progress.get(i), EVENTS[i]
        assert got is not None and got.status == "done" and got.kind == "payment" and got.reason is None, (i, got)
        assert (got.payment.from_, got.payment.to, got.payment.amount, got.payment.equivalent) == (e["from"], e["to"], e["amount"], e["equivalent"]), (i, got.payment)

    c = s.progress.get(CLEARING)
    expected_edges = _cycle_edges([p.removeprefix("cs_") for p in EVENTS[CLEARING]["expected_cycle"]])
    assert c is not None and c.status == "done" and c.kind == "clearing", c
    assert c.reason is None, c.reason  # the cycle the episode announces is the one that was cleared
    assert c.cleared_cycles == 1 and len(c.cycles) == 1, c
    assert c.cycles[0].cleared_amount == "40.00", c.cycles[0].cleared_amount  # the smallest debt on the circle
    assert {(e.from_.removeprefix("cs_"), e.to.removeprefix("cs_")) for e in c.cycles[0].edges} == expected_edges, c.cycles[0].edges

    r = s.progress.get(REFUSED)
    failed = [e for e in s.events if e.get("type") == "tx.failed"]
    assert len(failed) == 1 and r is not None and r.status == "refused", (r, failed)
    assert failed[0]["error"]["code"] == "ROUTING_NO_ROUTE" and r.reason == "ROUTING_NO_ROUTE", (r.reason, failed[0]["error"])  # said, not echoed

    assert sorted(s.progress) == sorted([*PAYMENTS, CLEARING]), sorted(s.progress)  # applied injects are not "progress"; notes are not tracked
    assert not [p for p in s.progress.values() if p.status == "incomplete"], s.progress
    assert s.final_state == "running"


def _anchor_matches(episode: dict, event: dict) -> bool:
    """Does `event` answer the anchor of `episode`, with the CONTENT the episode announces (not only the anchor's own fields)?"""

    anchor = episode["anchor"]
    if event.get("type") != anchor["event"]:
        return False
    if anchor["event"] == "tx.updated":
        mine = {k: episode[k] for k in ("from", "to", "amount", "equivalent")}
        return {k: anchor[k] for k in mine} == mine and all(str(event.get(k)) == v for k, v in mine.items())
    if anchor["event"] == "tx.failed":
        mine = {k: episode[k] for k in ("from", "to", "equivalent")}
        return {k: anchor[k] for k in mine} == mine and all(str(event.get(k)) == v for k, v in mine.items())
    if anchor["event"] == "clearing.done":
        edges = {(e["from"], e["to"]) for e in event.get("cycle_edges") or []}
        return _cycle_edges(episode["expected_cycle"]) <= edges
    if anchor["event"] == "topology.changed":
        effect, payload = episode["effects"][0], event.get("payload") or {}
        if effect["op"] == "add_participant":
            return effect["participant"]["id"] in [n["pid"] for n in payload.get("added_nodes") or []]
        if effect["op"] == "freeze_participant":
            return effect["participant_id"] in (payload.get("frozen_nodes") or [])
    return False


@pytest.mark.asyncio
async def test_the_anchors_of_the_episodes_arrive_in_order_as_events_of_their_own(factory, monkeypatch) -> None:  # noqa: F811
    """Each anchored episode consumes the FIRST not yet consumed event that answers it with its own content, and the
    episodes consume in order: an anchor that points at another episode's payment, or at the newcomer's event where the
    freeze's belongs, does not find its event."""

    s = await _run_story(factory, monkeypatch)

    cursor, consumed = 0, {}
    for i, e in enumerate(EVENTS):
        if "anchor" not in e:
            continue
        found = next((j for j in range(cursor, len(s.events)) if _anchor_matches(e, s.events[j])), None)
        assert found is not None, f"episode {i} ({e['type']}): its anchor {e['anchor']} found no event of its own after the previous episode's; types {[x.get('type') for x in s.events[cursor:]][:40]}"
        consumed[i], cursor = found, found + 1
    assert sorted(consumed) == [i for i, e in enumerate(EVENTS) if "anchor" in e]
    assert len(set(consumed.values())) == len(consumed)  # one event answers one episode
    assert sum(1 for ev in s.events if ev.get("type") == "tx.updated") == 6  # nothing but the story's six payments moved


# ---------------------------------------------------------------------------------------------------- degraded runs


@pytest.mark.asyncio
async def test_with_the_process_flag_for_injects_off_the_story_says_so_and_still_ends(factory, monkeypatch) -> None:  # noqa: F811
    """The product default (`SIMULATOR_REAL_ENABLE_INJECT=0`, and `run_full_stack.ps1` sets 0): the two injects are refused
    LOUDLY, the newcomer never exists, so his two payments and the circle never happen - and the clearing, a complete pass that
    cleared nothing, says that its announced cycle was not cleared. The story reaches its end; nothing is `incomplete`."""

    s = await _run_story(factory, monkeypatch, inject=False)

    report = {i: (p.kind, p.status, p.reason, p.cleared_cycles) for i, p in sorted(s.progress.items())}
    assert report == {
        1: ("payment", "done", None, None),
        2: ("payment", "done", None, None),
        3: ("payment", "done", None, None),
        4: ("payment", "done", None, None),
        5: ("inject", "refused", "inject_disabled_by_process", None),
        6: ("payment", "refused", "PAYMENT_REJECTED", None),
        7: ("payment", "refused", "PAYMENT_REJECTED", None),
        8: ("clearing", "done", "expected_cycle_not_cleared", 0),
        9: ("inject", "refused", "inject_disabled_by_process", None),
        10: ("payment", "refused", "ROUTING_NO_ROUTE", None),
    }, report
    assert [p["spent"][-1] for p in s.pauses] == PAUSING  # the pauses still come: the episodes are spent, said or not
    assert (s.taras_status, s.dmytro_status) == ("active", None)  # nobody was frozen, nobody arrived
    assert s.final_debts == {k: v for k, v in AFTER[5].items()}, s.final_debts  # the debts the first half of the story built
    assert not [p for p in s.progress.values() if p.status == "incomplete"]


@pytest.mark.asyncio
async def test_a_second_run_on_the_same_database_inherits_the_first_one(factory, monkeypatch) -> None:  # noqa: F811
    """The story is single-use per database: the debts, the frozen Taras and the newcomer stay in the ledger, the second run's
    perimeter does not include the newcomer, the injects are skipped without a word (an inject that was applied is not
    "progress", and neither is one that found nothing to do) and the debts grow."""

    first = await _run_story(factory, monkeypatch)
    second = await _run_story(factory, monkeypatch)

    assert first.final_debts == FINAL  # control: the first run was the good one
    report = {i: (p.status, p.reason) for i, p in sorted(second.progress.items())}
    assert report[2][0] == "refused", report  # Taras is suspended: the payment through him is refused
    assert report[6][0] == "refused" and report[7][0] == "refused", report  # Dmytro exists in the base but is outside this run's perimeter
    assert second.final_debts != FINAL and second.final_debts[("olena", "bakery")] == D("80"), second.final_debts  # the first purchase is paid again
    assert (second.taras_status, second.dmytro_status) == ("suspended", "active")
