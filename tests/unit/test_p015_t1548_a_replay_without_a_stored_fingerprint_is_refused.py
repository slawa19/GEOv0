"""T1548: a `tx_id` replay whose stored PAYMENT row carries no fingerprint is refused, not guessed.

WHAT WAS WRONG. `PaymentService._resolve_existing_payment` is the single idempotency policy for
both the lookup and the insert-race row. It compared fingerprints only `if existing_fp is not
None`, so a stored PAYMENT row with no fingerprint - written before fingerprints existed, or by
any path that records none - skipped the comparison, fell through to `idempotent_hit`, and the
caller was handed the stored result as if it were the same request. Equality of the canonical
payload was GUESSED: nothing in the row said what request produced it.

WHAT IT DOES NOW. Such a replay answers `409` (`E008`) with `details.reason =
"unverifiable_legacy_identity"` and `details.retryable = False`. It writes no money, compares no
payload and does not reach the perimeter check or the in-progress branch. The stored result
remains readable through `GET /payments/{tx_id}`, so nothing the caller could see is lost.

THE ANTI-VACUUM CASES ARE THE POINT OF THIS FILE. A refusal is a rule that throws work away
(`AGENTS.md` §9), so each case below that refuses is paired with one that must NOT refuse: a
fingerprinted replay of the same request still returns the stored result, and a fingerprinted
replay of a different request still gets the old "different request" 409. Without those two, the
whole of idempotency could be broken and every assertion here would still be green.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.payments.service import (
    UNVERIFIABLE_LEGACY_IDENTITY_REASON,
    PaymentService,
)
from app.core.payments.router import PaymentRouter
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.error_codes import ErrorCode
from app.utils.exceptions import ConflictException

_EQ = "T1548"


async def _seed(db_session):
    """sender -> receiver, one direct edge with capacity for the payments below."""

    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)

    people: dict[str, Participant] = {}
    for pid in ("t1548s", "t1548r"):
        p = Participant(
            id=uuid.uuid4(),
            pid=pid,
            display_name=pid.upper(),
            public_key=(pid * 32)[:64],
            type="person",
            status="active",
            profile={},
        )
        people[pid] = p
        db_session.add(p)
    await db_session.commit()

    # TrustLine(from=Y, to=X) == graph edge X -> Y: the receiver trusts the sender, so the
    # sender may pay the receiver.
    db_session.add(
        TrustLine(
            from_participant_id=people["t1548r"].id,
            to_participant_id=people["t1548s"].id,
            equivalent_id=eq.id,
            limit=Decimal("1000"),
            status="active",
        )
    )
    await db_session.commit()
    PaymentRouter.invalidate_cache()
    return eq, people


async def _money_snapshot(db_session) -> dict[str, object]:
    """Everything a refusal is forbidden to move: debts, the journal, the transaction rows."""

    debts = sorted(
        (str(d.debtor_id), str(d.creditor_id), str(d.equivalent_id), str(d.amount))
        for d in (await db_session.execute(select(Debt))).scalars().all()
    )
    rows = (
        await db_session.execute(
            select(Transaction.tx_id, Transaction.state, Transaction.payload)
        )
    ).all()
    return {
        "debts": debts,
        "transactions": sorted(
            (str(tx_id), str(state), repr(payload)) for tx_id, state, payload in rows
        ),
        "operations": int(
            await db_session.scalar(select(func.count()).select_from(debt_operations))
        ),
        "journal_entries": int(
            await db_session.scalar(
                select(func.count()).select_from(debt_journal_entries)
            )
        ),
    }


def _legacy_row(people, *, tx_id: str, idempotency, state: str = "COMMITTED") -> Transaction:
    """A stored PAYMENT row shaped exactly like a real one except for its identity block."""

    return Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        idempotency_key=None,
        type="PAYMENT",
        initiator_id=people["t1548s"].id,
        payload={
            "from": people["t1548s"].pid,
            "to": people["t1548r"].pid,
            "amount": "50",
            "equivalent": _EQ,
            "routes": [
                {"path": [people["t1548s"].pid, people["t1548r"].pid], "amount": "50"}
            ],
            "idempotency": idempotency,
        },
        state=state,
    )


async def _replay(db_session, people, *, tx_id: str, amount: str = "50"):
    return await PaymentService(db_session).create_payment_internal(
        people["t1548s"].id,
        to_pid=people["t1548r"].pid,
        equivalent=_EQ,
        amount=amount,
        idempotency_key=tx_id,
        commit=True,
    )


# ------------------------------------------------------------------------------------------
# The refusal
# ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idempotency",
    [
        pytest.param(None, id="no_idempotency_block"),
        pytest.param({}, id="empty_idempotency_block"),
        pytest.param({"key": "t1548"}, id="key_but_no_fingerprint"),
        pytest.param({"key": "t1548", "fingerprint": None}, id="null_fingerprint"),
        pytest.param({"key": "t1548", "fingerprint": ""}, id="empty_fingerprint"),
        pytest.param("legacy-string", id="idempotency_is_not_an_object"),
    ],
)
async def test_a_replay_of_a_row_without_a_usable_fingerprint_is_refused(
    db_session, idempotency
):
    """Every shape that leaves nothing to compare is refused the same way.

    The last two cases are the ones the previous code could not survive at all: `""` compared
    unequal and produced the WRONG reason ("different request"), and a non-object `idempotency`
    raised `AttributeError` out of the service - a 500 for a stored row, not a 409.
    """

    _eq, people = await _seed(db_session)
    tx_id = "t1548-legacy-" + uuid.uuid4().hex[:8]
    db_session.add(_legacy_row(people, tx_id=tx_id, idempotency=idempotency))
    await db_session.commit()

    before = await _money_snapshot(db_session)

    with pytest.raises(ConflictException) as excinfo:
        await _replay(db_session, people, tx_id=tx_id)

    exc = excinfo.value
    assert exc.status_code == 409, exc.status_code
    assert exc.code == ErrorCode.E008, exc.code
    assert (exc.details or {}).get("reason") == UNVERIFIABLE_LEGACY_IDENTITY_REASON, (
        f"the refusal must name an unverifiable legacy identity, got {exc.details!r}"
    )
    assert (exc.details or {}).get("retryable") is False, (
        f"repeating the request cannot make the stored row grow a fingerprint: {exc.details!r}"
    )

    assert await _money_snapshot(db_session) == before, (
        "the refusal moved debts, wrote the journal or touched a transaction row"
    )


@pytest.mark.asyncio
async def test_the_refusal_does_not_depend_on_the_stored_state(db_session):
    """It is decided before the in-progress branch, so every stored state answers the same.

    Ordering matters for a reason the caller can feel: `PREPARE_IN_PROGRESS` used to answer
    "Payment with same tx_id is in progress", which invites a retry, for a row that is not
    known to be this request at all.
    """

    _eq, people = await _seed(db_session)
    for state in ("NEW", "ROUTED", "PREPARE_IN_PROGRESS", "PREPARED", "COMMITTED", "ABORTED"):
        tx_id = f"t1548-state-{state.lower()}-" + uuid.uuid4().hex[:8]
        db_session.add(_legacy_row(people, tx_id=tx_id, idempotency=None, state=state))
        await db_session.commit()

        with pytest.raises(ConflictException) as excinfo:
            await _replay(db_session, people, tx_id=tx_id)
        assert (excinfo.value.details or {}).get(
            "reason"
        ) == UNVERIFIABLE_LEGACY_IDENTITY_REASON, f"state={state} {excinfo.value.details!r}"


@pytest.mark.asyncio
async def test_the_refusal_is_decided_before_the_perimeter(db_session):
    """A scoped caller gets the identity answer, not a routing one.

    The perimeter check asks WHOSE route the stored row holds. That question presupposes the
    row is this request; when it is not known to be, the perimeter's routing refusal would
    report the wrong cause (and a retryable-looking one) for an identity failure.
    """

    _eq, people = await _seed(db_session)
    tx_id = "t1548-scoped-" + uuid.uuid4().hex[:8]
    db_session.add(_legacy_row(people, tx_id=tx_id, idempotency=None))
    await db_session.commit()

    with pytest.raises(ConflictException) as excinfo:
        await PaymentService(db_session).create_payment_internal(
            people["t1548s"].id,
            to_pid=people["t1548r"].pid,
            equivalent=_EQ,
            amount="50",
            idempotency_key=tx_id,
            commit=True,
            allowed_participant_pids={people["t1548s"].pid, people["t1548r"].pid},
        )
    assert (excinfo.value.details or {}).get(
        "reason"
    ) == UNVERIFIABLE_LEGACY_IDENTITY_REASON, excinfo.value.details


@pytest.mark.asyncio
async def test_the_stored_result_is_still_readable_after_the_refusal(db_session):
    """The refusal closes the replay, not the record: GET still answers with what was stored."""

    _eq, people = await _seed(db_session)
    tx_id = "t1548-readable-" + uuid.uuid4().hex[:8]
    db_session.add(_legacy_row(people, tx_id=tx_id, idempotency=None))
    await db_session.commit()

    with pytest.raises(ConflictException):
        await _replay(db_session, people, tx_id=tx_id)

    stored = await PaymentService(db_session).get_payment_for_participant(
        tx_id,
        requester_participant_id=people["t1548s"].id,
        requester_pid=people["t1548s"].pid,
    )
    assert str(stored.tx_id) == tx_id
    assert str(stored.status) == "COMMITTED"


@pytest.mark.asyncio
async def test_the_insert_race_row_is_refused_by_the_same_policy(db_session):
    """The OTHER entrance to the policy: the row that appears between the lookup and the insert.

    `_create_payment_impl` reaches `_resolve_existing_payment` twice - once from the lookup that
    precedes routing, and once from the `IntegrityError` handler when `UNIQUE(tx_id)` refuses its
    own INSERT. One function, so one policy; this exercises the second entrance rather than
    asserting that in prose.

    HOW THE RACE IS BUILT, and what that costs. The conflicting row is written and committed from
    inside `PaymentRouter.build_graph`, which runs AFTER the lookup and BEFORE the insert. That
    placement is the anti-vacuum: routing is only reached when the lookup returned nothing, so
    `build_graph` having run is proof that the lookup missed and that the refusal below came from
    the `IntegrityError` handler. What it does not reproduce is a second connection - the writer
    here is the same session. Two genuinely concurrent connections were tried first and could not
    reach this branch at all on the SQLite tier (the reader's snapshot turns the insert into
    SQLITE_BUSY, which is classified as a retryable conflict long before `UNIQUE` is consulted).
    """

    _eq, people = await _seed(db_session)
    tx_id = "t1548-race-" + uuid.uuid4().hex[:8]

    assert (
        await db_session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.tx_id == tx_id)
        )
    ) == 0, "the stand needs the lookup to miss"

    service = PaymentService(db_session)
    original_build_graph = service.router.build_graph
    ran_after_the_lookup = {"yes": False}

    async def _build_graph_then_lose_the_race(*args, **kwargs):
        result = await original_build_graph(*args, **kwargs)
        if not ran_after_the_lookup["yes"]:
            ran_after_the_lookup["yes"] = True
            db_session.add(_legacy_row(people, tx_id=tx_id, idempotency=None))
            await db_session.commit()
        return result

    service.router.build_graph = _build_graph_then_lose_the_race  # type: ignore[method-assign]

    with pytest.raises(ConflictException) as excinfo:
        await service.create_payment_internal(
            people["t1548s"].id,
            to_pid=people["t1548r"].pid,
            equivalent=_EQ,
            amount="50",
            idempotency_key=tx_id,
            commit=True,
        )

    assert ran_after_the_lookup["yes"], (
        "routing never ran, so the lookup - not the insert race - answered this"
    )
    assert (excinfo.value.details or {}).get(
        "reason"
    ) == UNVERIFIABLE_LEGACY_IDENTITY_REASON, excinfo.value.details
    assert (excinfo.value.details or {}).get("retryable") is False, excinfo.value.details

    rows = (
        await db_session.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalars().all()
    assert len(rows) == 1 and rows[0].payload.get("idempotency") is None, (
        "the losing insert must leave nothing behind"
    )


# ------------------------------------------------------------------------------------------
# Anti-vacuum: what must still NOT be refused
# ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fingerprinted_replay_of_the_same_request_is_still_an_idempotent_hit(
    db_session,
):
    """The counter-check for the rule above: real idempotency is untouched.

    The row here is written by the application itself, so its fingerprint is the real one - no
    test computes a fingerprint, which is what would make this case agree by construction.
    """

    _eq, people = await _seed(db_session)
    tx_id = "t1548-real-" + uuid.uuid4().hex[:8]

    first = await _replay(db_session, people, tx_id=tx_id)
    after_first = await _money_snapshot(db_session)

    second = await _replay(db_session, people, tx_id=tx_id)

    assert str(second.tx_id) == str(first.tx_id)
    assert str(second.status) == str(first.status)
    assert await _money_snapshot(db_session) == after_first, (
        "the idempotent replay moved money a second time"
    )


@pytest.mark.asyncio
async def test_a_fingerprinted_replay_of_a_different_request_is_still_the_old_conflict(
    db_session,
):
    """The other counter-check: the "different request" 409 keeps its own reason.

    Its taxonomy is owned by another programme, so the new refusal must not swallow it.
    """

    _eq, people = await _seed(db_session)
    tx_id = "t1548-different-" + uuid.uuid4().hex[:8]
    await _replay(db_session, people, tx_id=tx_id, amount="50")

    with pytest.raises(ConflictException) as excinfo:
        await _replay(db_session, people, tx_id=tx_id, amount="17")

    assert (excinfo.value.details or {}).get("reason") != UNVERIFIABLE_LEGACY_IDENTITY_REASON, (
        "a row that HAS a fingerprint has a verifiable identity; it simply differs"
    )
    assert "different request" in str(excinfo.value.message)


@pytest.mark.asyncio
async def test_a_row_of_another_type_or_another_sender_keeps_its_own_answer(db_session):
    """The two checks that run BEFORE the new one still decide first.

    Their message is deliberately the bare "tx_id already used": it tells a caller nothing about
    a transaction that is not theirs. The new reason must not leak into those answers.
    """

    _eq, people = await _seed(db_session)

    foreign = Participant(
        id=uuid.uuid4(),
        pid="t1548o",
        display_name="OTHER",
        public_key=("t1548o-other" * 32)[:64],
        type="person",
        status="active",
        profile={},
    )
    db_session.add(foreign)
    await db_session.commit()

    other_sender = _legacy_row(
        people, tx_id="t1548-foreign-" + uuid.uuid4().hex[:8], idempotency=None
    )
    other_sender.initiator_id = foreign.id
    db_session.add(other_sender)
    await db_session.commit()

    with pytest.raises(ConflictException) as excinfo:
        await _replay(db_session, people, tx_id=str(other_sender.tx_id))
    assert (excinfo.value.details or {}).get("reason") is None, excinfo.value.details

    other_type = _legacy_row(
        people, tx_id="t1548-clearing-" + uuid.uuid4().hex[:8], idempotency=None
    )
    other_type.type = "CLEARING"
    db_session.add(other_type)
    await db_session.commit()

    with pytest.raises(ConflictException) as excinfo:
        await _replay(db_session, people, tx_id=str(other_type.tx_id))
    assert (excinfo.value.details or {}).get("reason") is None, excinfo.value.details
