"""035 A8 (F-035-5): the payment READ side and the replay of a stored payment answer over HTTP as before the move.

WHAT THIS IS. The read side of `app/core/payments/service.py` (`get_payment_for_participant`, `list_payments`,
`_tx_to_payment_result`) moved to `app/core/payments/read.py` with no change of behaviour. This is the fixed list of
requests the move is checked against: every branch of `GET /payments/{tx_id}` (the sender's own payment, the
receiver's, a stranger's, an absent id, a row that is not a payment, COMMITTED, a stored refusal), `GET /payments`
with each filter, and a second `POST /payments` with a `tx_id` already answered (the same request for a COMMITTED
row, for an ABORTED row and for a refusal that stored nothing; another request; another sender).

THE STAND'S ROWS ARE WRITTEN BY THE APPLICATION. Two payments commit. One is refused AFTER admission and stored
`ABORTED`: it times out while binding (the technique of `test_p015_t1523_replay_after_a_hold_or_an_abort.py` - a
slow `_bind_payment` under a 10 ms `PREPARE_TIMEOUT_SECONDS`, both lifted before the replay). Two are refused BEFORE
admission - more than the line carries, and no route - and store nothing: their `tx_id` reads as absent.

HOW THE MOVE WAS CHECKED WITH IT. The test writes every answer - status code and body text - to
`<GEO_TEST_ARTIFACT_ROOT>/p035_a8_read_side.json` when that variable is set (the canonical runner sets it). The file
of a run on the base commit and the file of a run on the moved code were compared byte for byte. Three kinds of
value differ between two runs by construction and are replaced by a role name before writing: participant ids
(derived from a fresh key pair), transaction ids (a fresh UUID), and timestamps. Everything else is the answer's
own bytes.

WHAT THE ASSERTIONS HERE HOLD ON THEIR OWN (without the comparison of two files): the status code of every request
and the fields that tell the branches apart. They are not a byte pin: a deliberate change of a wire shape changes
the file, not this test.

NOT REACHABLE, so not in the list: a stored PAYMENT row that is neither COMMITTED nor ABORTED ("in progress").
Migration 030 leaves none (see `PaymentService._resolve_existing_payment`), and a payment in flight has no row.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.utils.exceptions import TimeoutException
from tests.conftest import MODE_B
from tests.p023_support import auto_clear_http
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_REQUEST_ID = re.compile(r'("request_id"\s*:\s*")[^"]*(")')


class _Recorder:
    """Every answer of the stand, in order, with the run's own identifiers replaced by role names."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.answers: list[dict] = []

    def name(self, value: str, role: str) -> None:
        self.names[value] = role

    def record(self, label: str, response) -> dict | None:
        text = response.text
        # Longest first, so one identifier that contains another is replaced whole.
        for value in sorted(self.names, key=len, reverse=True):
            text = text.replace(value, f"<{self.names[value]}>")
        text = _TIMESTAMP.sub("<timestamp>", text)
        text = _REQUEST_ID.sub(r"\1<request_id>\2", text)
        self.answers.append({"request": label, "status": response.status_code, "body": text})
        try:
            return response.json()
        except ValueError:
            return None

    @property
    def last(self) -> dict:
        return self.answers[-1]

    def write(self) -> None:
        root = os.environ.get("GEO_TEST_ARTIFACT_ROOT")
        if not root:
            return
        target = Path(root) / "p035_a8_read_side.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.answers, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")


def _payment(sender: dict, to_pid: str, tx_id: str, amount: str, equivalent: str = "USD") -> dict:
    return {
        "tx_id": tx_id,
        "to": to_pid,
        "equivalent": equivalent,
        "amount": amount,
        "signature": _sign_payment_request(
            signing_key=SigningKey(base64.b64decode(sender["priv"])),
            tx_id=tx_id,
            from_pid=sender["pid"],
            to_pid=to_pid,
            equivalent=equivalent,
            amount=amount,
        ),
    }


async def _trust(client: AsyncClient, creditor: dict, debtor: dict, limit: str) -> None:
    response = await client.post(
        "/api/v1/trustlines",
        json={
            "to": debtor["pid"],
            "equivalent": "USD",
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=SigningKey(base64.b64decode(creditor["priv"])),
                to_pid=debtor["pid"],
                equivalent="USD",
                limit=limit,
            ),
        },
        headers=creditor["headers"],
    )
    assert response.status_code == 201, response.text


@MODE_B
@pytest.mark.asyncio
async def test_the_read_side_and_the_replay_answer_every_branch(client: AsyncClient, db_session, monkeypatch) -> None:
    db_session.add(Equivalent(code="USD", description="USD", precision=2))
    await db_session.commit()

    seen = _Recorder()
    alice = await register_and_login(client, "A8 Alice")
    bob = await register_and_login(client, "A8 Bob")
    carol = await register_and_login(client, "A8 Carol")
    dave = await register_and_login(client, "A8 Dave")
    for person, role in ((alice, "alice"), (bob, "bob"), (carol, "carol"), (dave, "dave")):
        seen.name(person["pid"], role)

    # Alice can pay Bob up to 100; Bob can pay Carol up to 100 (so Alice reaches Carol through Bob); nobody
    # can pay Dave at all.
    await _trust(client, bob, alice, "100.00")
    await _trust(client, carol, bob, "100.00")

    paid, relayed, timed_out, too_much, no_route, absent = (str(uuid.uuid4()) for _ in range(6))
    for tx_id, role in (
        (paid, "tx-paid"), (relayed, "tx-relayed"), (timed_out, "tx-timed-out"), (too_much, "tx-too-much"),
        (no_route, "tx-no-route"), (absent, "tx-absent"),
    ):
        seen.name(tx_id, role)

    async def post(label: str, sender: dict, body: dict):
        return seen.record(label, await client.post("/api/v1/payments", json=body, headers=sender["headers"]))

    async def get(label: str, reader: dict, path: str):
        return seen.record(label, await client.get(f"/api/v1/payments{path}", headers=reader["headers"]))

    # --- the rows: two committed payments, one stored refusal, two refusals that store nothing ---------------
    first = await post("POST paid, first time", alice, _payment(alice, bob["pid"], paid, "10.00"))
    assert seen.last["status"] == 200 and first["status"] == "COMMITTED", seen.last

    two_hops = await post("POST relayed (two hops), first time", alice, _payment(alice, carol["pid"], relayed, "5.00"))
    assert seen.last["status"] == 200 and two_hops["status"] == "COMMITTED", seen.last
    assert [route["path"] for route in two_hops["routes"]] == [[alice["pid"], bob["pid"], carol["pid"]]], two_hops

    original_bind = PaymentService._bind_payment
    original_timeout = settings.PREPARE_TIMEOUT_SECONDS
    slow = {"on": True}

    async def bind_slowly(self, *args, **kwargs):
        if slow["on"]:
            await asyncio.sleep(0.2)
        return await original_bind(self, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_bind_payment", bind_slowly)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    await post("POST timed-out, first time", alice, _payment(alice, bob["pid"], timed_out, "20.00"))
    assert seen.last["status"] == 504, seen.last
    slow["on"] = False
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", original_timeout, raising=False)

    await post("POST too-much, first time", alice, _payment(alice, bob["pid"], too_much, "500.00"))
    assert seen.last["status"] == 400 and "insufficient_capacity" in seen.last["body"], seen.last
    too_much_first = seen.last["body"]
    await post("POST no-route, first time", alice, _payment(alice, dave["pid"], no_route, "1.00"))
    assert seen.last["status"] == 400 and "no_route" in seen.last["body"], seen.last

    states = dict(
        (
            await db_session.execute(
                select(Transaction.tx_id, Transaction.state).where(
                    Transaction.tx_id.in_([paid, relayed, timed_out, too_much, no_route])
                )
            )
        ).all()
    )
    assert states == {paid: "COMMITTED", relayed: "COMMITTED", timed_out: "ABORTED"}, (
        f"premise: two committed payments, one stored refusal, and no row for a refusal before admission: {states}"
    )

    # --- a second POST with a tx_id already answered ---------------------------------------------------------
    replay = await post("POST paid, the same request again", alice, _payment(alice, bob["pid"], paid, "10.00"))
    assert seen.last["status"] == 200 and replay == first, (first, replay)

    stored_refusal = await post(
        "POST timed-out, the same request again", alice, _payment(alice, bob["pid"], timed_out, "20.00")
    )
    assert seen.last["status"] == 200 and stored_refusal["status"] == "ABORTED", seen.last
    assert stored_refusal["error"]["code"] == "E007" and stored_refusal["committed_at"] is None, stored_refusal

    await post("POST too-much, the same request again", alice, _payment(alice, bob["pid"], too_much, "500.00"))
    assert seen.last["status"] == 400 and seen.last["body"] == too_much_first, seen.last

    await post("POST paid, another amount under the same tx_id", alice, _payment(alice, bob["pid"], paid, "11.00"))
    assert seen.last["status"] == 409 and "tx_id_reused" in seen.last["body"], seen.last
    await post("POST paid, another sender under the same tx_id", bob, _payment(bob, carol["pid"], paid, "10.00"))
    assert seen.last["status"] == 409 and "tx_id_reused" in seen.last["body"], seen.last
    await post("POST timed-out, another amount under the same tx_id", alice,
               _payment(alice, bob["pid"], timed_out, "21.00"))
    assert seen.last["status"] == 409 and "tx_id_reused" in seen.last["body"], seen.last

    # --- GET /payments/{tx_id} -------------------------------------------------------------------------------
    own = await get("GET paid, by the sender", alice, f"/{paid}")
    assert seen.last["status"] == 200 and own == first, (own, first)
    assert own["from"] == alice["pid"] and own["to"] == bob["pid"] and own["amount"] == "10.00", own
    assert own["committed_at"] and own["routes"] and own["error"] is None, own
    assert await get("GET paid, by the receiver", bob, f"/{paid}") == own
    await get("GET paid, by a stranger", carol, f"/{paid}")
    assert seen.last["status"] == 404, seen.last

    assert await get("GET relayed, by the sender", alice, f"/{relayed}") == two_hops
    assert await get("GET relayed, by the receiver", carol, f"/{relayed}") == two_hops
    await get("GET relayed, by the intermediate participant", bob, f"/{relayed}")
    assert seen.last["status"] == 404, "the participant a payment passes through is neither its sender nor receiver"

    assert await get("GET timed-out, by the sender", alice, f"/{timed_out}") == stored_refusal
    assert await get("GET timed-out, by the receiver", bob, f"/{timed_out}") == stored_refusal
    await get("GET timed-out, by a stranger", carol, f"/{timed_out}")
    assert seen.last["status"] == 404, seen.last

    for label, tx_id in (("too-much (refused before admission)", too_much), ("no-route (refused before admission)",
                         no_route), ("an absent tx_id", absent), ("a tx_id that is not a UUID", "not-a-transaction")):
        await get(f"GET {label}", alice, f"/{tx_id}")
        assert seen.last["status"] == 404, seen.last

    # --- GET /payments ---------------------------------------------------------------------------------------
    def ids(listing: dict) -> list[str]:
        return [item["tx_id"] for item in listing["items"]]

    everything = ids(await get("LIST alice, no filter", alice, ""))
    assert everything == [timed_out, relayed, paid], f"newest first: {everything}"
    assert ids(await get("LIST alice, sent", alice, "?direction=sent")) == everything
    assert ids(await get("LIST alice, received", alice, "?direction=received")) == []
    assert ids(await get("LIST alice, all", alice, "?direction=all")) == everything
    assert ids(await get("LIST bob, received", bob, "?direction=received")) == [timed_out, paid]
    assert ids(await get("LIST bob, sent", bob, "?direction=sent")) == []
    assert ids(await get("LIST bob, no filter", bob, "")) == [timed_out, paid]
    assert ids(await get("LIST carol, no filter", carol, "")) == [relayed]
    assert ids(await get("LIST dave, no filter", dave, "")) == []
    assert ids(await get("LIST alice, committed", alice, "?status=COMMITTED")) == [relayed, paid]
    assert ids(await get("LIST alice, aborted", alice, "?status=ABORTED")) == [timed_out]
    assert ids(await get("LIST bob, received and aborted", bob, "?direction=received&status=ABORTED")) == [timed_out]
    assert ids(await get("LIST alice, USD", alice, "?equivalent=USD")) == everything
    assert ids(await get("LIST alice, another equivalent", alice, "?equivalent=EUR")) == []
    assert ids(await get("LIST alice, page 1 of 2 per page", alice, "?per_page=2&page=1")) == everything[:2]
    assert ids(await get("LIST alice, page 2 of 2 per page", alice, "?per_page=2&page=2")) == everything[2:]
    assert ids(await get("LIST alice, a page past the end", alice, "?per_page=2&page=3")) == []
    assert ids(await get("LIST alice, from a future date", alice, "?from_date=2999-01-01T00:00:00Z")) == []
    assert ids(await get("LIST alice, up to a past date (no zone)", alice, "?to_date=2000-01-01T00:00:00")) == []
    assert ids(await get("LIST alice, between a past date (no zone) and a future one (another zone)", alice,
                         "?from_date=2000-01-01T00:00:00&to_date=2999-01-01T05:00:00%2B05:00")) == everything
    for label, query in (("an unknown direction", "?direction=sideways"), ("an unknown status", "?status=PREPARED"),
                         ("page 0", "?page=0"), ("201 per page", "?per_page=201")):
        await get(f"LIST with {label}", alice, query)
        assert seen.last["status"] in (400, 422), seen.last

    # --- a stored transaction that is not a payment ---------------------------------------------------------
    # After the listings, because it takes one more payment to make: Carol pays Alice, which closes the cycle
    # alice -> bob -> carol -> alice, and the manual clearing pass stores a CLEARING row.
    await _trust(client, alice, carol, "100.00")
    closing = str(uuid.uuid4())
    seen.name(closing, "tx-closing")
    await post("POST closing (carol pays alice)", carol, _payment(carol, alice["pid"], closing, "3.00"))
    assert seen.last["status"] == 200, seen.last
    cleared = await auto_clear_http(client, alice["headers"], "USD")
    assert cleared.status_code == 200, cleared.text
    clearing_rows = (
        await db_session.execute(select(Transaction.tx_id).where(Transaction.type == "CLEARING"))
    ).scalars().all()
    assert len(clearing_rows) == 1, f"premise: the pass stored one transaction that is not a payment: {clearing_rows}"
    seen.name(str(clearing_rows[0]), "tx-clearing")
    for reader, role in ((alice, "a participant of the cycle"), (dave, "a stranger")):
        await get(f"GET a transaction that is not a payment, by {role}", reader, f"/{clearing_rows[0]}")
        assert seen.last["status"] == 404, seen.last
    assert ids(await get("LIST alice, after the clearing", alice, "")) == [closing] + everything, (
        "a CLEARING row must not appear among payments"
    )

    seen.write()

    # --- the stored refusal, as the simulator's owner classifies it (the public name of 035 A8 (a)) -----------
    # After the file is written: on the base commit this import is the only thing that fails.
    from app.core.payments.service import public_error_of_stored

    row = (await db_session.execute(select(Transaction).where(Transaction.tx_id == timed_out))).scalar_one()
    error = public_error_of_stored(PaymentService._tx_to_payment_result(row))
    assert type(error) is TimeoutException and error.message == "Payment timeout", error
