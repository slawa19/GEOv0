"""Programme 019, `T1902` / `T1904`: how many times one successful `POST /payments` commits.

THE INSTRUMENT. `CommitRecorder` wraps `commit()` of every session the request opens - the request's
`get_db` session and, since stage 3, each session `PaymentService.pay` opens for an attempt through
`get_payment_session_factory` (`tests/integration/p019_stand.py`, `api`) - and after every REAL commit
reads, on a new session, what another transaction then sees of the payment.

BEFORE STAGE 3: three commits on the request's session, `NEW`, `PREPARED` (with its reservation),
`COMMITTED`.

TARGET (spec, Verification plan §1), PASSING SINCE STAGE 3 (`T1904`): one commit, after which the
payment is `COMMITTED`. Controls first: the recorder saw at least one commit (an instrument that saw
none measured nothing - the control that would have caught `pay()` moving to a session the recorder is
not on), and the payment committed with money moved.

WHAT THE INSTRUMENT DOES NOT SEE: a commit on a session opened outside both dependencies - a raw
engine connection, say. Nothing in the payment path does that today.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    CommitRecorder,
    api,
    build_api_world,
    debts,
    envelopes,
    factory,
    payment_body,
    with_session_hook,
)
from tests.p019_support import require_target


@pytest.mark.asyncio
async def test_a_successful_api_payment_commits_once(api, factory) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    body = payment_body(world, world.alice, world.bob, "10.00")
    recorder = CommitRecorder(factory, body["tx_id"])
    with with_session_hook(recorder):
        resp = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])

    assert resp.status_code == 200 and resp.json()["status"] == "COMMITTED", resp.text
    assert recorder.commits, "the recorder saw no commit at all: it is not on the payment's session"
    assert recorder.commits[-1] == "COMMITTED", recorder.commits
    assert await debts(factory, world) == {
        (world.alice["pid"], world.bob["pid"]): Decimal("10.00")
    }
    [(env_state, declared, entries)] = await envelopes(factory, body["tx_id"])
    assert env_state == "COMPLETED" and declared == entries > 0

    require_target(
        recorder.commits == ["COMMITTED"],
        f"the payment's session committed {len(recorder.commits)} times: {recorder.commits}",
    )
