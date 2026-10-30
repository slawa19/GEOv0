"""054-E-14: every `tx.updated` a real tick publishes is valid against the canonical event schema.

WHAT WAS WRONG (054 slice E review, reproduced as witness W14 on `66f29397`). Each edge of a
`tx.updated` carries `style: null` - `SimulatorTxUpdatedEventEdge.style` defaults to `None`
(`app/schemas/simulator.py`) and no writer sets it - while the canon declared the property as a
bare `$ref: SimulatorEventEdgeStyle`, which admits an object and nothing else. So the canon
rejected every `tx.updated` the simulator has ever sent. Consumers tolerate the null
(`simulator-ui/v2` `normalizeSimulatorEvent.ts`, and `test_p034_s1b...` pins it on the wire), so
the canon was the side that was wrong: it now admits `null` through a sanctioned
`oneOf [$ref, {nullable, enum: [null]}]` branch. The serialization is untouched.

Real DB, real tick, real `SseEventEmitter` (`model_dump(mode="json", by_alias=True)`); only the
transport is captured (`_Sse.broadcast`), i.e. exactly the dict that becomes the `data:` line.
Setup as in `test_p034_s1b_payment_edge_patch_names_the_line_postgres.py`.

WHAT THIS DOES NOT SEE: the HTTP framing of the stream, and events other than those one tick of
a one-line scenario emits. That a wrong style object is still rejected is held without a DB in
`tests/contract/test_p054_E_14_edge_style_admits_null.py`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.contract import openapi_response_conformance as oc
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _Sse,
    _forget_the_route_cache,
    _install,
    _record_plans,
    _run_record,
    _runner,
    _scenario,
    _seed,
    factory,
)
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

_CANON = oc.load_canon()

_SCHEMA_BY_TYPE = {
    "tx.updated": "SimulatorTxUpdatedEvent",
    "tx.failed": "SimulatorTxFailedEvent",
    "clearing.done": "SimulatorClearingDoneEvent",
    "topology.changed": "SimulatorTopologyChangedEvent",
    "run_status": "SimulatorRunStatusEvent",
}


@pytest.mark.asyncio
async def test_a_real_tick_publishes_events_the_canon_accepts(factory, monkeypatch) -> None:  # noqa: F811
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p054-e14-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)
    finally:
        _forget_the_route_cache(world)

    # Controls: the tick carried a payment, and the event under test really has the null style.
    assert len(plans) == 1, plans
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    assert updated, [e.get("type") for e in sse.events]
    edges = updated[0].get("edges") or []
    assert edges and all("style" in e and e["style"] is None for e in edges), edges

    errors = []
    for event in sse.events:
        schema = _SCHEMA_BY_TYPE.get(event.get("type"), "SimulatorEvent")
        for node, message, value in oc.validate_body(_CANON, f"/components/schemas/{schema}", event):
            errors.append((event.get("type"), node, message[:160], value))
    assert not errors, errors
