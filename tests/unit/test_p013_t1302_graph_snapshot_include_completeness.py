"""013 / ``RT-013-1`` - an empty optional collection means two different things on the wire.

WHAT WAS MEASURED, AND WHY IT IS A DEFECT.  ``GET /api/v1/admin/graph/snapshot`` carries three
optional collections - ``transactions``, ``incidents``, ``audit_log`` - and each is returned as an
empty list in two entirely different situations:

  * the client did not ask for it (no ``include`` parameter), and
  * the client asked for it and the answer is genuinely zero.

Nothing in the response distinguishes them (``app/api/v1/admin.py`` around the ``include_set``
block, and ``app/schemas/graph.py::AdminGraphSnapshotResponse``, where all three are
``list[Any] = Field(default_factory=list)``).  The canon says so in as many words - "Each is
present and empty unless named in the ``include`` CSV" (``api/openapi.yaml``, the
``AdminGraphSnapshotResponse`` declaration) - which documents the ambiguity rather than removing
it.

THE CONSUMER TREATS IT AS ZERO.  ``admin-ui/src/composables/useGraphAnalytics.ts`` derives
``hasTransactions`` from the array's length and counts committed payments by iterating it, and the
admin client never sends ``include`` at all (``admin-ui/src/api/realApi.ts``, the graph-snapshot
call).  So the activity panel reports "no payments" for a period it was never told about.  That is
the 013 class exactly: a screen showing a value where there is no value.

A THIRD SITUATION EXISTS AND IS ALSO INVISIBLE: the fetch helpers take a ``limit`` (default 50,
``ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS``), so a full list can mean "these are all of them" or
"these are the first 50 of an unknown number".  A count computed over a truncated list is wrong in
a way nothing on the wire admits.

WHY THIS TEST IS SHAPED AS A DIFFERENCE, NOT AS AN ASSERTION ABOUT ONE RESPONSE.  Judging a single
response cannot show the defect - both readings produce byte-identical bodies, which is the whole
finding.  The tests below therefore compare the NOT-ASKED response against the ASKED-AND-EMPTY
response and require that something in them differs.  A fix that merely renames a field, or adds
metadata only to the non-empty case, does not satisfy that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_committed_payment(db_session, *, pid: str, tx_id: str) -> None:
    """One committed payment, i.e. exactly what the activity panel exists to count."""

    actor = Participant(
        pid=pid,
        display_name=pid,
        public_key=uuid.uuid4().hex * 2,
        type="person",
        status="active",
    )
    db_session.add(actor)
    await db_session.flush()

    db_session.add(
        Transaction(
            tx_id=tx_id,
            type="PAYMENT",
            initiator_id=actor.id,
            payload={"equivalent": "UAH", "amount": "10.00"},
            state="COMMITTED",
            created_at=_utc_now() - timedelta(minutes=5),
            updated_at=_utc_now() - timedelta(minutes=4),
        )
    )
    await db_session.flush()
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_snapshot_says_whether_the_optional_collections_were_asked_for(client, db_session):
    """The two empty answers must not be the same answer.

    SITUATION: one committed payment exists.  The client asks for the snapshot WITHOUT `include`.
    HONEST WIRE: `transactions` is empty AND the response says it was not requested, so a consumer
        knows it may not conclude anything about payments.
    WHAT IT DOES: an empty list, indistinguishable from "asked, and there are none".
    """

    await _seed_committed_payment(db_session, pid="p013a", tx_id="TX_P013_A")

    not_asked = await client.get("/api/v1/admin/graph/snapshot", headers=_admin_headers())
    assert not_asked.status_code == 200, not_asked.text
    body = not_asked.json()

    assert body["transactions"] == [], (
        "precondition: without `include` the route returns an empty list - if this ever changes, "
        "this test is measuring something else"
    )
    assert "included" in body, (
        "the response does not say which optional collections it carries. `transactions: []` here "
        "means 'you did not ask', while the very same body means 'there are none' when the client "
        "did ask - and a consumer counting payments cannot tell the two apart. "
        "app/schemas/graph.py::AdminGraphSnapshotResponse"
    )
    assert "transactions" not in body["included"], (
        "the response claims to carry transactions although `include` was not sent"
    )


@pytest.mark.asyncio
async def test_asked_and_empty_is_distinguishable_from_not_asked(client, db_session):
    """The difference is the point, so the test is a comparison of two bodies.

    SITUATION: no transactions exist at all.  One request omits `include`, the other asks for
        transactions explicitly.  Both return an empty list.
    HONEST WIRE: the two bodies differ, because one of them was told nothing and the other was told
        'none'.
    """

    not_asked = await client.get("/api/v1/admin/graph/snapshot", headers=_admin_headers())
    asked = await client.get(
        "/api/v1/admin/graph/snapshot?include=transactions", headers=_admin_headers()
    )
    assert not_asked.status_code == 200 and asked.status_code == 200

    a, b = not_asked.json(), asked.json()
    assert a["transactions"] == [] and b["transactions"] == [], "precondition: both are empty"

    assert a.get("included") != b.get("included"), (
        "'not asked' and 'asked, and there are none' produce the same response. A consumer that "
        "reports 'no payments' is guessing, and it guesses the same way in both cases"
    )
    assert "transactions" in (b.get("included") or []), (
        "the response does not admit that transactions were requested and answered"
    )


@pytest.mark.asyncio
async def test_a_truncated_collection_says_that_it_was_truncated(client, db_session, monkeypatch):
    """A count over a truncated list is wrong, and nothing on the wire admits the truncation.

    SITUATION: the include limit is lowered to 1 and two payments exist, so the answer is cut.
    HONEST WIRE: the response says this collection is incomplete.
    WHAT IT DOES: returns one row, looking exactly like a complete answer of one.
    """

    monkeypatch.setattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS", 1, raising=False)
    await _seed_committed_payment(db_session, pid="p013b", tx_id="TX_P013_B")
    await _seed_committed_payment(db_session, pid="p013c", tx_id="TX_P013_C")

    r = await client.get(
        "/api/v1/admin/graph/snapshot?include=transactions", headers=_admin_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert len(body["transactions"]) == 1, (
        "precondition: the limit really did cut the list, otherwise this test proves nothing"
    )
    assert "transactions" in (body.get("truncated") or []), (
        "the collection was cut at the include limit and the response does not say so. Any count "
        "computed from it is a lower bound presented as a total"
    )


@pytest.mark.asyncio
async def test_a_payment_row_says_who_it_was_between(client, db_session):
    """`initiator_pid` answers a different question than the consumer is asking.

    SITUATION: a committed payment from `p013d` to `p013e`, requested with `include=transactions`.
    HONEST WIRE: the row names both parties, so a screen can say whether a given participant was
        involved.
    WHAT THE FIRST EDITION DID: published `initiator_pid` alone.  A consumer counting "payments
        this participant was party to" then reports zero for everyone who was PAID rather than
        paying - a false zero one level below the false zero this programme is about.

    The full internal payload stays internal: only the two pids cross the wire.
    """

    actor = Participant(
        pid="p013d",
        display_name="p013d",
        public_key=uuid.uuid4().hex * 2,
        type="person",
        status="active",
    )
    db_session.add(actor)
    await db_session.flush()
    db_session.add(
        Transaction(
            tx_id="TX_P013_PAY",
            type="PAYMENT",
            initiator_id=actor.id,
            payload={
                "from": "p013d",
                "to": "p013e",
                "amount": "10.00",
                "equivalent": "UAH",
                "routes": [{"secret": "must not reach the wire"}],
            },
            state="COMMITTED",
            created_at=_utc_now() - timedelta(minutes=3),
            updated_at=_utc_now() - timedelta(minutes=2),
        )
    )
    await db_session.flush()
    await db_session.commit()

    r = await client.get(
        "/api/v1/admin/graph/snapshot?include=transactions", headers=_admin_headers()
    )
    assert r.status_code == 200, r.text
    row = next(t for t in r.json()["transactions"] if t["tx_id"] == "TX_P013_PAY")

    assert row.get("from") == "p013d" and row.get("to") == "p013e", (
        "the payment does not say who it was between, so only its initiator can be told they were "
        "involved and the counterparty is counted as absent"
    )
    assert "payload" not in row and "routes" not in row, (
        "the internal, versionless payload is being published; the projection exists precisely so "
        "that it is not"
    )


@pytest.mark.asyncio
async def test_a_clearing_row_says_whose_debts_it_moved(client, db_session):
    """CLEARING is attributed by its edges, and they are cut down to the two pids.

    SITUATION: a committed clearing over one edge, `p013g` owing `p013h`.
    HONEST WIRE: `edges: [{debtor, creditor}]` - enough to place a participant, and nothing more.
    WHAT THE FIRST EDITION DID: published nothing about the cycle at all.
    """

    actor = Participant(
        pid="p013f",
        display_name="p013f",
        public_key=uuid.uuid4().hex * 2,
        type="person",
        status="active",
    )
    db_session.add(actor)
    await db_session.flush()
    db_session.add(
        Transaction(
            tx_id="TX_P013_CLR",
            type="CLEARING",
            initiator_id=actor.id,
            payload={
                "equivalent": "UAH",
                "amount": "5.00",
                "cycle": ["d1"],
                "edges": [
                    {
                        "debt_id": "d1",
                        "debtor": "p013g",
                        "creditor": "p013h",
                        "amount": "5.00",
                    }
                ],
            },
            state="COMMITTED",
            created_at=_utc_now() - timedelta(minutes=3),
            updated_at=_utc_now() - timedelta(minutes=2),
        )
    )
    await db_session.flush()
    await db_session.commit()

    r = await client.get(
        "/api/v1/admin/graph/snapshot?include=transactions", headers=_admin_headers()
    )
    assert r.status_code == 200, r.text
    row = next(t for t in r.json()["transactions"] if t["tx_id"] == "TX_P013_CLR")

    assert row.get("edges") == [{"debtor": "p013g", "creditor": "p013h"}], (
        "the clearing does not say whose debts it moved, so nobody but the initiator can be "
        "attributed to it - and the edge carries only the two pids, because amounts and debt ids "
        "belong to the audit surface rather than to a graph read"
    )
