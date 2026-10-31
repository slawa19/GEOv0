"""MM2-SOL-3 (BACKLOG, MiniMax pilot M2, 2026-10-31): the replay of a recorded launch reads a scenario's equivalent code
by the runtime's own rule.

The runtime resolves a scripted payment's and a trust line's equivalent with `effective_equivalent` (`strip().upper()`,
falling back to the scenario default; `real_runner_impl.py`, `scripted_payments_due`) and records the resolved code in
the episode progress and on every SSE event. `scripts/measure_scenario_economy.py` compared the scenario's spelling
literally in three places - the nominal opening gross, the expected scripted payment, the intended (assigned) lines -
so a run the runtime executed as `UAH` from a scenario spelled `uah` or `" UAH "` was refused by the replay with
`Scripted progress payment/outcome mismatch` (witness W3, on 4197b3cd).

The recording below is what the runtime records for such a run: every code already `UAH`. Controls: `UAH` passes
before and after; a real mismatch (another equivalent, another amount) still fails loudly.
"""

from __future__ import annotations

import pytest

from scripts import measure_scenario_economy as economy

A, B, RUN = "mm_a", "mm_b", "r1"


def _scenario(line_eq: str, event: dict) -> dict:
    return {
        "scenario_id": "mm-m2-replay",
        "baseEquivalent": "UAH",
        "equivalents": ["UAH"],
        "participants": [{"id": A, "groupId": "g"}, {"id": B, "groupId": "g"}],
        "trustlines": [{"from": B, "to": A, "equivalent": line_eq, "limit": "100"}],  # B creditor: A pays B
        "events": [{"time": 1000, "type": "payment", "from": A, "to": B, "amount": "10.00", **event}],
    }


def _recording(*, scripted: bool = True) -> dict:
    """One tick as the runtime records it: a normal payment A -> B 5.00, then (`scripted`) the scripted one 10.00, all
    in `UAH`."""
    n_tx = 2 if scripted else 1
    zero = {"attempts_total": 0, "committed_total": 0, "rejected_total": 0, "errors_total": 0}
    done = {"attempts_total": n_tx, "committed_total": n_tx, "rejected_total": 0, "errors_total": 0}

    def tx(n: int, amount: str, used: str) -> dict:
        payload = {"event_id": f"evt_{RUN}_{n}", "type": "tx.updated", "equivalent": "UAH", "from": A, "to": B,
                   "amount": amount, "edge_patch": [{"source": B, "target": A, "used": used}]}
        return {"id": payload["event_id"], "type": "tx.updated", "payload": payload}

    txs = [tx(2, "5.00", "5.00"), *([tx(3, "10.00", "15.00")] if scripted else [])]
    status = {"event_id": f"evt_{RUN}_{2 + n_tx}", "type": "run_status", "run_id": RUN, "sim_time_ms": 1000, **done}
    progress = [{"index": 0, "epoch": 0, "kind": "payment", "status": "done", "attempts": 1, "equivalent": "UAH",
                 "payment": {"from": A, "to": B, "amount": "10.00", "equivalent": "UAH"}}] if scripted else []
    return {"launches": [{
        "run_id": RUN, "epoch": 0, "expected_ticks": 1, "clearing_every": 1000,
        "initial_snapshot": {"equivalent": "UAH",
                             "links": [{"source": B, "target": A, "used": "0", "trust_limit": "100"}]},
        "initial_status": {"run_id": RUN, "sim_time_ms": 0, "episode_progress": [], "intensity_percent": 100, **zero},
        "start_event_id": f"evt_{RUN}_1",
        "events": [*txs, {"id": status["event_id"], "type": "run_status", "payload": status}],
        "completed_ticks": [{"tick": 1, "event_id": status["event_id"],
                             "status": {"run_id": RUN, "sim_time_ms": 1000, "episode_progress": progress, **done}}],
    }]}


@pytest.mark.parametrize("spelling", ["UAH", "uah", " UAH ", " uAh"])
def test_replay_reads_the_equivalent_as_the_runtime_does(spelling) -> None:
    try:
        report = economy.measure_live(_scenario(spelling, {"equivalent": spelling}), _recording())
    except ValueError as exc:
        raise AssertionError(f"actual: spelling {spelling!r} - replay refused a run the runtime executed as UAH: "
                             f"{exc}") from exc
    launch = report["launches"][0]
    assert launch["scripted"]["successful"] == 1, launch["scripted"]
    assert launch["scripted"]["nominal_gross"] == "10.00", launch["scripted"]  # the opening gross counts the event
    # the normal payment runs over a line the scenario intends, whatever the line's spelling
    assert launch["normal"]["assigned"]["numerator"] == 1, launch["normal"]["assigned"]


@pytest.mark.parametrize("spelling", ["uah", " UAH "])
def test_the_intended_lines_read_the_line_equivalent_as_the_runtime_does(spelling) -> None:
    """The line's spelling alone (the event names none and falls back to `baseEquivalent`)."""
    report = economy.measure_live(_scenario(spelling, {}), _recording())
    assigned = report["launches"][0]["normal"]["assigned"]
    assert assigned["numerator"] == 1, (
        f"actual: line spelled {spelling!r} - {assigned['numerator']} of {assigned['denominator']} normal payments "
        f"counted as over an intended line; the runtime ran it as UAH"
    )


@pytest.mark.parametrize("spelling", ["uah", " UAH "])
def test_the_nominal_opening_gross_reads_the_event_equivalent_as_the_runtime_does(spelling) -> None:
    """The nominal sum alone: an event due after the recorded tick (time 2000, one tick recorded) is not reconciled yet,
    but it is in the opening window and counts toward its nominal gross."""
    scenario = _scenario("UAH", {"equivalent": spelling, "time": 2000})
    recording = _recording(scripted=False)  # the scripted payment is not attempted before its tick
    report = economy.measure_live(scenario, recording)
    assert report["launches"][0]["scripted"]["nominal_gross"] == "10.00", (
        f"actual: event spelled {spelling!r} - nominal opening gross {report['launches'][0]['scripted']['nominal_gross']}"
        f"; the runtime pays it as UAH"
    )


@pytest.mark.parametrize(
    "event",
    [
        pytest.param({"equivalent": "EUR"}, id="another-equivalent"),
        pytest.param({"equivalent": "uah", "amount": "11.00"}, id="another-amount"),
        pytest.param({"equivalent": "uah", "to": A, "from": B}, id="another-direction"),
    ],
)
def test_a_real_mismatch_still_fails_loudly(event) -> None:
    """Anti-vacuum: normalising the spelling must not make a different payment reconcile."""
    with pytest.raises(ValueError, match="Scripted progress payment/outcome mismatch"):
        economy.measure_live(_scenario("UAH", event), _recording())
