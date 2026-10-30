"""054-E-14: the canon admits `tx.updated.edges[].style: null` and still rejects a wrong style.

The real-tick half lives in `tests/integration/test_p054_E_14_tick_tx_updated_conforms_postgres.py`.
This half needs no DB: it validates hand-built events with the same `OAS30Validator` engine the
response-conformance harness uses (`tests/contract/openapi_response_conformance.py`), so that
admitting `null` cannot have been bought by admitting everything.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract import openapi_response_conformance as oc

_CANON = oc.load_canon()
_POINTER = "/components/schemas/SimulatorTxUpdatedEvent"


def _event(style: Any) -> dict[str, Any]:
    return {
        "event_id": "evt_1",
        "ts": "2026-10-30T00:00:00Z",
        "type": "tx.updated",
        "equivalent": "UAH",
        "edges": [{"from": "PID_A", "to": "PID_B", "style": style}],
    }


@pytest.mark.parametrize(
    "style",
    [None, {}, {"viz_width_key": "thin", "viz_alpha_key": None}],
    ids=["null", "empty-object", "object"],
)
def test_the_canon_accepts_a_null_or_a_well_formed_style(style: Any) -> None:
    assert oc.validate_body(_CANON, _POINTER, _event(style)) == []


@pytest.mark.parametrize(
    "style",
    ["thin", 5, [], {"viz_width_key": 5}],
    ids=["string", "number", "array", "wrong-member-type"],
)
def test_the_canon_still_rejects_a_malformed_style(style: Any) -> None:
    errors = oc.validate_body(_CANON, _POINTER, _event(style))
    assert errors, f"style={style!r} validated, so the null branch admits too much"
    assert all(node.startswith("$.edges[].style") for node, _message, _value in errors), errors
