"""RT-011-5: the tx.updated emitter must not write keys its own model does not declare.

Program 011, finding `F-011-5`.

`SseEventEmitter` states a strict serialization policy in its own docstring
(`app/core/simulator/sse_broadcast.py`): "always `model_dump(mode="json", by_alias=True)`".
Before `T1104` the tx.updated builder broke that policy in its last three lines: it dumped
the model and then appended `edge_patch` / `node_patch` straight into the resulting dict.
`SimulatorTxUpdatedEvent` did not declare either field, so the event on the wire carried
keys the model had never heard of.

**The reproducer had to be reformulated, and the reason matters.** The spec asked
RT-011-5 to show that "a client generated from the application's schema does not know these
fields".  That is not provable by fixing the model: `SimulatorTxUpdatedEvent` is never used
as a `response_model` anywhere - the SSE routes return `StreamingResponse` - so the event
does not appear in the generated schema *at all*, with or without the two fields.  Verified
on 2026-08-23: `app.openapi()["components"]["schemas"]` contains neither
`SimulatorTxUpdatedEvent` nor `SimulatorGraphNodePatch`, while `api/openapi.yaml` declares
both.  A test written to the spec's wording would have been red for a reason `T1104` cannot
fix, which is the same defect that already forced `RT-011-4` and the first `RT-011-5` to be
rewritten.

So the reproducer asserts the half of the finding that `T1104` actually closes: no key
reaches the wire that the model does not declare.

The second test is the counter-check the fix is only safe because of: declaring the fields
must not change the bytes consumers already read.  A naive `Optional[list] = None` would add
`"edge_patch": null` to every tx.updated event, which `## Non-goals` forbids.
"""

from __future__ import annotations

import logging

from app.core.simulator.sse_broadcast import SseEventEmitter
from app.schemas.simulator import SimulatorTxUpdatedEvent

_EDGE_PATCH = [{"source": "alice", "target": "bob", "used": "5.00"}]
_NODE_PATCH = [{"id": "alice", "net_balance": "-5.00"}]


class _CapturingTransport:
    """Minimal SseBroadcast stand-in: runs the real payload factory, keeps the result."""

    def __init__(self) -> None:
        self.payload: dict | None = None

    def publish_event(self, *, run_id, payload_factory):
        self.payload = payload_factory("evt-1")
        return self.payload


def _emit(edge_patch=None, node_patch=None) -> dict:
    """Drive the production emitter, not a copy of it, and return the wire payload."""

    transport = _CapturingTransport()
    emitter = SseEventEmitter(
        sse=transport,
        utc_now=lambda: "2026-08-23T00:00:00Z",
        logger=logging.getLogger("test.p011"),
    )
    emitter.emit_tx_updated(
        run_id="run-1",
        run=object(),
        equivalent="USD",
        from_pid="alice",
        to_pid="bob",
        amount="5.00",
        amount_flyout=False,
        ttl_ms=1000,
        edges=[{"from": "alice", "to": "bob"}],
        node_badges=[],
        edge_patch=edge_patch,
        node_patch=node_patch,
    )
    assert transport.payload is not None, "emitter swallowed the event"
    return transport.payload


def test_tx_updated_declares_every_key_it_emits() -> None:
    """No key on the wire may be absent from the model's declared fields."""

    declared = set(SimulatorTxUpdatedEvent.model_fields)
    # `from_` is declared but serialized under its alias.
    declared.discard("from_")
    declared.add("from")

    emitted = set(_emit(_EDGE_PATCH, _NODE_PATCH))

    assert emitted <= declared, (
        "tx.updated emits keys its model does not declare: "
        f"{sorted(emitted - declared)}"
    )
    # Guard the guard: the two fields the finding is about must really be exercised here,
    # so this stays red for the right reason rather than passing on an empty payload.
    assert {"edge_patch", "node_patch"} <= emitted


def test_declaring_the_patches_does_not_change_the_wire() -> None:
    """Counter-check: the keys stay absent when there is no patch, as before `T1104`."""

    without = _emit(None, None)
    assert "edge_patch" not in without
    assert "node_patch" not in without

    # An empty list was omitted before the change and must stay omitted.
    empty = _emit([], [])
    assert "edge_patch" not in empty
    assert "node_patch" not in empty

    # When present, the payload passes through untouched - no re-typing, no added nulls.
    with_patches = _emit(_EDGE_PATCH, _NODE_PATCH)
    assert with_patches["edge_patch"] == _EDGE_PATCH
    assert with_patches["node_patch"] == _NODE_PATCH
