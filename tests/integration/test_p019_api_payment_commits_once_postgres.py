"""Programme 019, `T1902`: how many times one successful `POST /payments` commits.

THE INSTRUMENT. `CommitRecorder` wraps `commit()` of the session the request handler received - the
service and `PaymentEngine` commit through that same object - and after every REAL commit reads, on a
new session, what another transaction then sees of the payment.

TODAY (pinned by the characterization in `test_p019_no_durable_intermediate_state_postgres.py`): three
commits, `NEW` (`app/core/payments/service.py:905`), `PREPARED` (`app/core/payments/engine.py:1032`),
`COMMITTED` (`engine.py:1699`).

TARGET (spec, Verification plan §1): one commit, after which the payment is `COMMITTED`. Controls
first: the recorder saw at least one commit (an instrument that saw none measured nothing), and the
payment committed with money moved. `xfail(strict)` until stage 3.

WHAT THE INSTRUMENT DOES NOT SEE: a commit made on a session the request did not receive through
`get_db`. If stage 3 moves `pay()` onto a session of its own, the "saw at least one commit" control
fails loudly rather than the target passing - adapt the recorder there, do not delete the control.
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
from tests.p019_support import require_target, target_xfail


@target_xfail("stage 3 (T1904)", "a successful API payment commits three times")
@pytest.mark.asyncio
async def test_a_successful_api_payment_commits_once(api, factory) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    body = payment_body(world, world.alice, world.bob, "10.00")
    recorder = CommitRecorder(factory, body["tx_id"])
    with with_session_hook(recorder):
        resp = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])

    assert resp.status_code == 200 and resp.json()["status"] == "COMMITTED", resp.text
    assert recorder.commits, "the recorder saw no commit at all: it is not on the payment's session"
    assert recorder.commits[-1] == ("COMMITTED", 0), recorder.commits
    assert await debts(factory, world) == {
        (world.alice["pid"], world.bob["pid"]): Decimal("10.00")
    }
    [(env_state, declared, entries)] = await envelopes(factory, body["tx_id"])
    assert env_state == "COMPLETED" and declared == entries > 0

    require_target(
        recorder.commits == [("COMMITTED", 0)],
        f"the payment's session committed {len(recorder.commits)} times: {recorder.commits}",
    )
