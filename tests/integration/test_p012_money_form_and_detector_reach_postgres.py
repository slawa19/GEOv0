"""012, second round: the money form that `F-012-11` left open, and the reach `T1202` assumed.

WHY THIS MODULE EXISTS, AND WHY IT IS NOT AN EXTENSION OF
`test_p012_t1202_money_modules_read_precision_postgres.py`.  That module is the record of the
first round and its prose is the argument for dropping the clearing threshold; both of its
findings were recorded as CLOSED.  External review measured that neither was, and in the second
case that closing it made a second defect worse.  These are the reproducers for what was left,
kept separate so the first round's evidence stays legible next to what it missed.

--------------------------------------------------------------------------------------------
PART 1 - `F-012-11` was closed on one route out of three.
--------------------------------------------------------------------------------------------

The finding says balance rendered "every money field" through `str(Decimal)` and that all of it
is fixed.  Measured on PostgreSQL 16.9 at `b801f77` with one stored debt of `0.00000001 UAH`:

    GET /balance                 net_balance = '0.00000001'    <- fixed
    GET /balance/debts           incoming    = [('P06b', '1E-8')]
    GET /api/v1/clearing/cycles  amounts     = ['1E-8', '1E-8', '1E-8']

`Numeric(20, 8)` returns `Decimal('1E-8')` for that value, so every producer that still used
bare `str()` printed the exponent.  Four of them did: `BalanceService.get_debts`, both SQL cycle
detectors, and the Python DFS - and, worse than a response, the CLEARING transaction PAYLOAD,
which is the column the `T1201` rollout condition says to audit for `scale >= 9`.

The persisted payload was the one place where "is this a storage-format change?" had to be
answered rather than assumed, and the answer is recorded on `_execute_clearing_with_amount`: it
is not, because the only reader parses it with `Decimal(...)`, which reads both forms to the
same value, and because the field is in no hash, no signature and no key.  This module holds
that answer to its consequence - `test_the_persisted_clearing_payload_...` below re-parses the
stored string and requires it to equal the amount that was actually applied to the debts.

--------------------------------------------------------------------------------------------
PART 2 - the early return, and the depth at which nobody looked.
--------------------------------------------------------------------------------------------

`T1202` recorded that the `min_amount` threshold was "the ONLY difference" between the SQL fast
path and the Python DFS, and that dropping it therefore "makes the early return sound".  The
first half is true about ADMISSION and false about REACH: `find_triangles_sql` joins three
`debts` rows and `find_quadrangles_sql` four, so the SQL side finds cycles of 3 and 4 edges and
nothing longer, while the DFS finds up to `max_depth` - whose value on the API is SIX
(`app/api/v1/clearing.py`, and this module reads it from the route rather than repeating it).

So `find_cycles` returning the SQL answer whenever it is non-empty was never sound at the depth
the API actually asks for, and dropping the threshold made it WORSE: the fast path is now
non-empty strictly more often, so it suppresses the fallback strictly more often.  Measured at
`b801f77` on one `UAH` graph holding a `0.01` triangle plus a disjoint 5-node cycle of `50`:

    max_depth=3 -> 1 cycle, lengths [3]      max_depth=5 -> 1 cycle, lengths [3]
    max_depth=4 -> 1 cycle, lengths [3]      max_depth=6 -> 1 cycle, lengths [3]

The 5-cycle is absent at every depth that asks for it.  This is the same "one of two" shape
`F-012-3` was rated `P2` for, pointing the other way, on a read endpoint.

WHY THE FIRST ROUND'S GUARD COULD NOT SEE IT.  All three of its `find_cycles` tests call
`max_depth=3` - the single depth at which the two detectors have equal reach, and therefore the
one depth at which the early return cannot be wrong.  The population was chosen where the two
agree.  Every reach assertion here is parametrised across the boundary instead, and the one
that matters most takes its depth from the route's own default so it cannot drift away from
what users get.

THE THRESHOLD IS NOT COMING BACK.  The measurements that removed it stand and were
independently re-verified; restoring it would trade this defect for the one `F-012-3` records.
The fix is to stop the early return from answering a question the SQL detectors cannot reach,
and to MERGE the two answers past that reach rather than pick one - neither detector is a
superset of the other (`LIMIT 100` and amount ordering on one side, a first-cycle-per-branch
cutoff and a cap of fifty on the other), so a union is the only combination whose result does
not depend on which one happened to be non-empty.

WHAT MERGING TURNED UP, AND WHERE ITS REPRODUCER LIVES.  The two detectors do not agree on
how a debt id LOOKS, and nothing had to notice while only one of them ever answered.  On SQLite
the ORM DFS emits `'333e9737-a7cc-4017-812d-fa3719bef0c9'` and `find_triangles_sql` emits
`'333e9737a7cc4017812dfa3719bef0c9'`, so a de-duplication keyed on the string reported every
cycle twice.  On PostgreSQL asyncpg returns `uuid.UUID` on both paths and the two agree, which
is why NOTHING IN THIS MODULE COULD HAVE CAUGHT IT: the reproducer is
`tests/unit/test_p1_clearing_run_perimeter.py::test_detection_layer_does_not_return_a_foreign_cycle`
on the default tier, which already asks at `max_depth=6` and counts cycles.  The fix is
`ClearingService._debt_id_key`, which keys on the value rather than the spelling.

REMOVED 2026-10-09 (035 A2b).  The detectors PART 2 is about - `find_cycles`, `find_triangles_sql`,
`find_quadrangles_sql`, `_debt_id_key`, the reach constant - are gone from `app/`; the text above is the
record of why the tests below exist, not a description of present code.  What was a property of the
detectors alone left with them (`test_a_long_cycle_appears_exactly_when_the_caller_asks_deep_enough`:
there is no depth to ask at).  What was a property of the clearing SURFACE was moved to the diagnostic's
producer in 035 A2a, and the last two pieces in A2b: the exact string of an ordinary debt
(`test_an_ordinary_debt_has_one_exact_string_on_the_diagnostic`) and "the executing route declares no
depth" (`test_the_executing_route_declares_no_depth`).

EVERY EQUIVALENT IS CREATED BY THE TEST.  `tests/conftest.py` builds the schema and never reads
`seeds/equivalents.json`, so `precision` here is set explicitly; a test that inherited someone
else's `UAH` would be measuring that fixture.
"""

from __future__ import annotations

import inspect
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import clearing as clearing_route
from app.core.balance.service import BalanceService
from app.core.clearing.runner import planned_cycles_for_diagnostics
from app.core.clearing.service import ClearingService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup
from tests.p023_support import TEST_PLAN_ID, occurrence_of, planned_cycles

# Only `test_the_persisted_clearing_payload_is_plain_decimal_and_still_replays` commits (clearing refuses a
# connection-bound session), so only it runs on a disposable clone of the migrated template and leaves
# its rows to the clone's drop (018 B0b; see `tests/tier_on_a_clone.py`). The others stay mode A.
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: E402,F401 - opt-in fixture
from tests.debt_setup import transactions_of


# REMOVED 2026-10-09 (035 A2b): `API_DEFAULT_MAX_DEPTH` and `_route_default`, which read a default depth - first from
# the two clearing routes, then (035 A1) from `ClearingService.find_cycles`. Neither route has a depth and the
# detector is gone, so there is nothing to read. History of the agreement assertion (023 slice (d), decision 8): it
# required the two routes' defaults to be equal, then the ABSENCE of a depth on `POST /clearing/auto`; that last form
# is the live one and stays in `test_the_executing_route_declares_no_depth`.

_OPEN_POLICY = {
    "auto_clearing": True,
    "can_be_intermediate": True,
    "max_hop_usage": None,
    "daily_limit": None,
    "blocked_participants": [],
}

# The value that makes the exponent appear.  It is the smallest amount `Numeric(20, 8)` can
# hold, the door admits it (scale 8 is exactly `DEFAULT_MAX_AMOUNT_SCALE`), and `str()` of what
# the column returns for it is the literal `'1E-8'`.
_SMALLEST_STORABLE = Decimal("0.00000001")


def _is_exponential(text: str) -> bool:
    return "e" in text.lower()


async def _participant(session: AsyncSession, name: str) -> Participant:
    p = Participant(
        id=uuid.uuid4(),
        pid=f"geo:{name}:{uuid.uuid4().hex[:12]}",
        display_name=name,
        type="person",
        public_key=uuid.uuid4().hex * 2,
        status="active",
    )
    session.add(p)
    return p


async def _equivalent(session: AsyncSession, code: str, precision: int) -> Equivalent:
    """Create this test's own equivalent at the precision this test names.

    `equivalents.code` is unique, so a row another module committed and left behind would
    collide; clearing it first inside this test's own transaction - which `db_session` rolls
    back - keeps the module independent of run order without hiding the state.
    """

    await session.execute(delete(Equivalent).where(Equivalent.code == code))
    eq = Equivalent(
        id=uuid.uuid4(), code=code, symbol=code[:3], precision=precision, is_active=True
    )
    session.add(eq)
    await session.flush()
    return eq


async def _ring(
    session: AsyncSession,
    eq: Equivalent,
    names: list[str],
    amount: Decimal,
) -> frozenset[str]:
    """A closed ring n0 -> n1 -> ... -> n0, every edge consented to for auto clearing.

    Returns the debt ids as strings, which is how the assertions identify a cycle: the
    detectors are never asked to confirm their own output, only to name debts the fixture made.
    """

    people = [await _participant(session, n) for n in names]
    await session.flush()
    debt_ids: list[str] = []
    for i, debtor in enumerate(people):
        creditor = people[(i + 1) % len(people)]
        async with debt_fixture_setup(session, label="setup"):
            debt = Debt(
                id=uuid.uuid4(),
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=amount,
            )
            session.add(debt)
        debt_ids.append(str(debt.id))
        # The controlling line for a debt debtor->creditor is creditor->debtor.
        session.add(
            TrustLine(
                id=uuid.uuid4(),
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=eq.id,
                limit=Decimal("1000000"),
                policy=dict(_OPEN_POLICY),
                status="active",
            )
        )
    await session.flush()
    return frozenset(debt_ids)


def _cycle_sets(cycles) -> list[frozenset[str]]:
    return [frozenset(str(edge["debt_id"]) for edge in cycle) for cycle in cycles]


def _all_amounts(cycles) -> list[str]:
    return [str(edge["amount"]) for cycle in cycles for edge in cycle]


async def _diagnostic_cycles(session: AsyncSession, code: str) -> list[list[dict]]:
    """What `GET /api/v1/clearing/cycles` answers with since 035 A1: the PRODUCT's producer and renderer
    (`runner.planned_cycles_for_diagnostics` - the flow plan on the snapshot, computed in the diagnostic planner
    process, each edge carrying the debt's amount through `to_money_str`). 035 A2a (decision D3, 2026-10-08) moved
    the money-form and "one component does not hide another" assertions of this module here from the retired
    detectors: programme 012's clearing surface is this renderer now.

    The stand is committed first (in mode A that releases a savepoint inside the test's rolled-back transaction):
    the producer ends its read transaction before it plans, which would discard a stand that was only flushed.
    """

    await session.commit()
    return await planned_cycles_for_diagnostics(session, code)


# --------------------------------------------------------------------------------------------
# PART 1 - the money form on the routes `F-012-11` did not reach
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "precision, stored, expected",
    [
        # The exponent case: `str(Decimal('1E-8'))` is what the route printed.
        (2, _SMALLEST_STORABLE, "0.00000001"),
        # The precision case.  Four digits is a value the storage scale of 8 does not coincide
        # with, so a renderer that consulted no `precision` would print `'7.00000000'`.  The
        # expected string is built by hand and not by any renderer under test.
        (4, Decimal("7"), "7.0000"),
        # THE CONTROL, and it carries no expectation of change: precision 8 is the one value
        # that coincides with `Numeric(20, 8)`, so the old precision-blind rendering already
        # agreed with it. It must be green before and after, which is what shows the other two
        # rows fail on `precision` rather than on something the fixture does.
        (8, Decimal("7"), "7.00000000"),
    ],
)
@pytest.mark.parametrize("direction", ["outgoing", "incoming"])
async def test_get_debts_renders_plain_decimals_at_the_declared_precision(
    db_session: AsyncSession,
    direction: str,
    precision: int,
    stored: Decimal,
    expected: str,
) -> None:
    """`GET /balance/debts` - the route sixty lines below the one that was fixed.

    Both directions, because they are two separate `str(d.amount)` sites and a fix to one
    would otherwise pass for a fix to both.
    """

    eq = await _equivalent(db_session, "UAH", precision)
    me = await _participant(db_session, "me")
    peer = await _participant(db_session, "peer")
    await db_session.flush()
    debtor, creditor = (me, peer) if direction == "outgoing" else (peer, me)
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(
                id=uuid.uuid4(),
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=stored,
            )
        )
    await db_session.flush()

    details = await BalanceService(db_session).get_debts(me.id, "UAH", direction)
    rows = getattr(details, direction)
    assert len(rows) == 1, f"precondition: the fixture must produce one {direction} debt"
    amount = rows[0].amount

    assert not _is_exponential(amount), (
        f"exponential money on GET /balance/debts ({direction}): {amount!r}. "
        "`Numeric(20, 8)` returns 0.00000001 as Decimal('1E-8') and `str()` keeps the "
        "exponent. This is the same defect `F-012-11` records as closed, on the next route."
    )
    assert Decimal(amount) == stored, (
        f"the value must survive the rendering: {amount!r} is not {stored}"
    )
    assert amount == expected, (
        f"a precision-{precision} equivalent must render {stored} as {expected!r} on "
        f"GET /balance/debts ({direction}), got {amount!r}. `get_summary` on the neighbouring "
        "route has done this since the first round; this route consulted no precision at all."
    )


@pytest.mark.parametrize(
    "precision, leg, expected",
    [
        # The value that produces the exponent: `str(Decimal('1E-8'))`.
        (2, _SMALLEST_STORABLE, "0.00000001"),
        # A precision the storage scale does not coincide with, so a renderer that ignored
        # `precision` and printed at scale 8 would say `'0.01000000'` and be caught.
        (4, Decimal("0.01"), "0.0100"),
    ],
)
@pytest.mark.parametrize("ring_size", [3, 4])
async def test_the_clearing_diagnostic_renders_money_plainly_and_at_the_declared_precision(
    db_session: AsyncSession,
    ring_size: int,
    precision: int,
    leg: Decimal,
    expected: str,
) -> None:
    """The clearing surface's renderer, on rings of three and of four.

    MOVED 2026-10-08 (035 A2a, D3). This was `test_the_sql_detectors_render_money_plainly_and_at_the_declared_
    precision`, which addressed the two raw-SQL producers by name (`find_triangles_sql`, `find_quadrangles_sql`)
    because each had a renderer of its own. `GET /clearing/cycles` has one producer now and it is asked here; the
    precondition and both assertions - no exponent, the exact string at the declared precision - are unchanged.
    The two ring sizes stay as two stands (they were the two producers).
    """

    eq = await _equivalent(db_session, "UAH", precision)
    expected_cycle = await _ring(
        db_session, eq, [f"n{i}" for i in range(ring_size)], leg
    )

    cycles = await _diagnostic_cycles(db_session, "UAH")
    assert expected_cycle in _cycle_sets(cycles), (
        f"precondition: the diagnostic must offer the {ring_size}-ring it is being asked about. "
        f"Found: {_cycle_sets(cycles)}"
    )

    amounts = _all_amounts(cycles)
    offenders = [a for a in amounts if _is_exponential(a)]
    assert not offenders, (
        f"exponential money on GET /api/v1/clearing/cycles: {offenders}"
    )
    assert set(amounts) == {expected}, (
        f"the diagnostic must render {leg} under a precision-{precision} equivalent as {expected!r}: "
        f"got {sorted(set(amounts))}"
    )


@pytest.mark.parametrize("ring_size", [3, 4, 5])
async def test_no_routed_answer_puts_an_exponent_on_the_wire(
    db_session: AsyncSession, ring_size: int
) -> None:
    """A ring of the smallest storable debt, three, four and five edges long, on the diagnostic's wire.

    MOVED 2026-10-08 (035 A2a, D3): the three ring lengths were the three producers `find_cycles` routed to (the
    two SQL detectors through the early return, and the DFS); the diagnostic has one producer now and the three
    lengths stay as three stands. The precondition and both assertions are unchanged.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    expected = await _ring(
        db_session, eq, [f"n{i}" for i in range(ring_size)], _SMALLEST_STORABLE
    )

    cycles = await _diagnostic_cycles(db_session, "UAH")
    assert expected in _cycle_sets(cycles), (
        f"precondition: the {ring_size}-ring must be offered. Found: {_cycle_sets(cycles)}"
    )

    amounts = _all_amounts(cycles)
    offenders = [a for a in amounts if _is_exponential(a)]
    assert not offenders, (
        f"exponential money on GET /api/v1/clearing/cycles: {offenders}. "
        f"All amounts: {amounts}"
    )
    assert all(Decimal(a) == _SMALLEST_STORABLE for a in amounts), (
        f"the value must survive the rendering: {amounts}"
    )


async def test_an_ordinary_debt_has_one_exact_string_on_the_diagnostic(
    db_session: AsyncSession,
) -> None:
    """An ordinary `0.01` under a precision-2 equivalent is the string `'0.01'`, on every edge.

    MOVED 2026-10-09 (035 A2b). This was `test_one_debt_has_one_string_whichever_detector_answered`: one triangle
    read twice - through `find_triangles_sql`, where asyncpg hands back the column's own scale (`'0.01000000'`
    before the fix), and through `find_cycles`, where the ORM DFS answered - and both had to say exactly
    `{"0.01"}`. The two producers are removed; "the two agree" has nothing left to compare. The exact form they had
    to agree ON is a property of the clearing surface and stays, asked of the diagnostic's one producer: a reader
    who compares responses as strings must get the declared precision and not the storage scale. No other test in
    this module pins the precision-2 form of an ordinary amount (the parametrised one above pins `1E-8` and a
    precision of 4).
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangle = await _ring(db_session, eq, ["p1", "p2", "p3"], Decimal("0.01"))

    cycles = await _diagnostic_cycles(db_session, "UAH")
    assert _cycle_sets(cycles) == [triangle], (
        f"precondition: the diagnostic must offer the one triangle this test built, once: {_cycle_sets(cycles)}"
    )

    forms = set(_all_amounts(cycles))
    assert forms == {"0.01"}, (
        f"one stored 0.01 under a precision-2 equivalent, rendered as {sorted(forms)} on "
        "GET /api/v1/clearing/cycles: the wire carries the declared precision, not the column's scale."
    )


async def test_precision_widens_the_clearing_amount_but_never_narrows_the_value(
    db_session: AsyncSession,
) -> None:
    """The counter-check for the renderer's own parameter, on the clearing surface.

    Two claims at once, and they pull in opposite directions.  A precision-4 equivalent must
    show four digits for an ordinary `0.01` - proving the renderer reads `precision` at all
    and that this test reacts to it.  And a precision-1 equivalent holding a real, stored
    `0.05` must still report `0.05`, not `0.0` - proving `precision` sets a MINIMUM number of
    digits and never a licence to round a debt away (`RT-012-2`, applied to clearing).

    MOVED 2026-10-08 (035 A2a, D3) from the retired detectors to the diagnostic's own producer; both exact
    strings are unchanged. The diagnostic renders the DEBT on the snapshot, so execution's step rule (a 0.05 under
    precision 1 is not a whole step) does not apply to what is shown.
    """

    eq = await _equivalent(db_session, "UAH", 4)
    await _ring(db_session, eq, ["w1", "w2", "w3"], Decimal("0.01"))
    cycles = await _diagnostic_cycles(db_session, "UAH")
    assert set(_all_amounts(cycles)) == {"0.0100"}, (
        f"a precision-4 equivalent must show four digits: {sorted(set(_all_amounts(cycles)))}"
    )

    hour = await _equivalent(db_session, "HOUR", 1)
    await _ring(db_session, hour, ["h1", "h2", "h3"], Decimal("0.05"))
    cycles = await _diagnostic_cycles(db_session, "HOUR")
    amounts = set(_all_amounts(cycles))
    assert amounts == {"0.05"}, (
        f"a stored 0.05 under a precision-1 equivalent must not be rounded away by the "
        f"renderer: {sorted(amounts)}. The door accepts it and Numeric(20, 8) holds it."
    )


# --------------------------------------------------------------------------------------------
# PART 2 - the reach of the early return
# --------------------------------------------------------------------------------------------


def test_the_executing_route_declares_no_depth() -> None:
    """`POST /clearing/auto` has no `max_depth` (programme 023, decision 8).

    NARROWED 2026-10-09 (035 A2b). This was `test_the_api_default_depth_is_past_what_the_sql_detectors_can_reach`,
    with two more assertions - the SQL detectors reach four edges, and the default depth exceeds that - which were
    the premise of the detector reach tests and left with the detectors. This one is about a live route and stays.
    """

    # 023 (d): the executing route has no depth to disagree about; a `max_depth` parameter there would be the
    # accepted-and-ignored (or MTCS-limiting) state decision 8 forbids.
    assert "max_depth" not in inspect.signature(clearing_route.auto_clear).parameters, (
        "POST /clearing/auto must not declare an execution depth (programme 023, decision 8)"
    )


async def test_at_the_api_default_depth_a_triangle_does_not_hide_a_long_cycle(
    db_session: AsyncSession,
) -> None:
    """The user-visible defect, at the depth users actually get.

    The graph is the smallest one that separates the two detectors: a `0.01` triangle, which
    only the SQL path returns, and a DISJOINT 5-node cycle of `50`, which only the DFS can
    reach.  They share no participant and no debt, so neither can be an artefact of the other.
    Before the fix `find_cycles` returned the SQL answer because it was non-empty and the
    5-cycle never appeared - at ANY depth, including this one.

    MOVED 2026-10-08 (035 A2a, D3): the property - another component must not hide this eligible one - is asked
    of the diagnostic's producer (the flow plan), which has no depth and no early return. Both assertions are
    unchanged; the name keeps its history.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangle = await _ring(db_session, eq, ["t1", "t2", "t3"], Decimal("0.01"))
    long_cycle = await _ring(
        db_session, eq, ["l1", "l2", "l3", "l4", "l5"], Decimal("50")
    )

    found = _cycle_sets(await _diagnostic_cycles(db_session, "UAH"))

    assert triangle in found, "control: the short cycle must still be reported"
    assert long_cycle in found, (
        f"a 5-node cycle is missing while a 3-node one is reported: {len(found)} cycle(s) for two that exist"
    )


async def test_the_long_cycle_is_reported_whether_or_not_a_short_one_exists(
    db_session: AsyncSession,
) -> None:
    """The defect stated as the property it violates.

    The answer to "what cycles are there" must not depend on whether some OTHER, unrelated
    cycle happened to make one detector non-empty.  So the same 5-node cycle is asked for
    twice - once alone, once with a disjoint triangle beside it - and it must be reported both
    times.  This is the assertion that fails for any fix that merely reorders the detectors.

    MOVED 2026-10-08 (035 A2a, D3) to the diagnostic's producer; both assertions are unchanged.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    long_cycle = await _ring(
        db_session, eq, ["l1", "l2", "l3", "l4", "l5"], Decimal("50")
    )
    eq_id = eq.id  # the producer ends its read transaction, which expires `eq`

    alone = _cycle_sets(await _diagnostic_cycles(db_session, "UAH"))
    assert long_cycle in alone, f"control: with nothing else present it is found: {alone}"

    eq = await db_session.get(Equivalent, eq_id)
    await _ring(db_session, eq, ["t1", "t2", "t3"], Decimal("0.01"))
    beside_a_triangle = _cycle_sets(await _diagnostic_cycles(db_session, "UAH"))
    assert long_cycle in beside_a_triangle, (
        "adding an unrelated triangle removed the 5-node cycle from the answer: "
        f"{alone} -> {beside_a_triangle}. Which cycles are offered cannot depend on an unrelated component."
    )


async def test_past_the_sql_reach_the_two_answers_are_merged_and_not_swapped(
    db_session: AsyncSession,
) -> None:
    """The other half of the fix, and the reason it is a union rather than a switch.

    "Past four edges, skip the SQL and let the DFS answer" would satisfy every other test in
    this file and would be a REGRESSION, because the DFS is not a superset of the SQL detector
    either: it stops collecting at fifty raw cycles (`if len(cycles) > 50: break`), and it
    finds the same cycle once per start node, so fifty raw cycles is roughly seventeen distinct
    ones.  Measured on this fixture at `max_depth=6`: the DFS alone reports 17 of the 25
    triangles; the SQL detector returns all 25 (its own cap is `LIMIT 100`, and 25 triangles
    are 75 rows).  Only the union reports all of them.

    Twenty-five is chosen against both caps: comfortably past the DFS's, comfortably inside the
    SQL's, so the assertion is about the merge and not about either limit.

    MOVED 2026-10-08 (035 A2a, D3): the caps and the merge above are the detectors' and leave with them; what
    stays is that every one of 25 independent eligible cycles is offered, asked of the diagnostic's producer
    (which has no cap). The assertion is unchanged; the name keeps its history.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangles = [
        await _ring(db_session, eq, [f"m{k}_{i}" for i in range(3)], Decimal("5"))
        for k in range(25)
    ]

    found = _cycle_sets(await _diagnostic_cycles(db_session, "UAH"))

    missing = [i for i, t in enumerate(triangles) if t not in found]
    assert not missing, (
        f"{len(missing)} of {len(triangles)} triangles are missing from the diagnostic: indices {missing}"
    )


# --------------------------------------------------------------------------------------------
# PART 3 - the persisted payload
# --------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("tier_on_a_clone")
async def test_the_persisted_clearing_payload_is_plain_decimal_and_still_replays(
    db_session: AsyncSession,
) -> None:
    """`transactions.payload` is the column the `T1201` rollout audit has to read.

    Two things are asserted together on purpose, because either alone would be a trap.

    THE FORM: no amount stored by a CLEARING row - `payload.amount`, every
    `payload.edges[].amount`, and the same edges copied into `integrity_audit_log` - may be in
    exponent notation.  A `cast(... as numeric)` audit happens to read `'1E-8'` correctly, but
    an audit that counts fraction digits in the TEXT sees none at all in it, and that is the
    obvious way to write one.

    THE VALUE: the payload is re-parsed on replay (`_read_committed_execution_amount`), so a
    changed string form is only safe if it parses back to the same `Decimal`.  It is compared
    here against the value `execute_occurrence` returned. Under v2 that is the occurrence's DECLARED amount (one
    atom, the whole debt), so this is a round-trip check of the stored string form, not an independent measure of
    what was applied; what was applied is the declared amount the precondition above asserts.
    """

    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8].upper()
    code = f"PZ{nonce}"
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    debt_ids = [uuid.uuid4() for _ in range(3)]

    async with TestingSessionLocal() as setup:
        setup.add(
            # Precision 8: one atom is then one whole step. At precision 2 the executor refuses it since 030 S2
            # (`F-030-1`, `occurrence_amount_not_in_step`) - the stored form of 1E-8 is still the question here.
            Equivalent(id=equivalent_id, code=code, symbol="PZ", precision=8)
        )
        setup.add_all(
            [
                Participant(
                    id=pid,
                    pid=f"geo:{label}:{nonce}",
                    display_name=label,
                    public_key=uuid.uuid4().hex * 2,
                    type="person",
                    status="active",
                )
                for pid, label in zip(participant_ids, ("A", "B", "C"), strict=True)
            ]
        )
        ring = [
            (debt_ids[0], participant_ids[0], participant_ids[1]),
            (debt_ids[1], participant_ids[1], participant_ids[2]),
            (debt_ids[2], participant_ids[2], participant_ids[0]),
        ]
        setup.add_all(
            [
                TrustLine(
                    from_participant_id=creditor,
                    to_participant_id=debtor,
                    equivalent_id=equivalent_id,
                    limit=Decimal("1000000"),
                    policy=dict(_OPEN_POLICY),
                    status="active",
                )
                for _, debtor, creditor in ring
            ]
        )
        async with debt_fixture_setup(setup, label="setup"):
            setup.add_all(
                [
                    Debt(
                        id=debt_id,
                        debtor_id=debtor,
                        creditor_id=creditor,
                        equivalent_id=equivalent_id,
                        amount=_SMALLEST_STORABLE,
                    )
                    for debt_id, debtor, creditor in ring
                ]
            )
        await setup.commit()

    async with TestingSessionLocal() as worker:
        service = ClearingService(worker)
        cycles = await planned_cycles(worker, code)  # 035 A2a: the planner, not the retired detectors
        assert cycles, "precondition: the triangle must be offered before it is cleared"
        # 025 `T2508.1`: the plan occurrence of the ring, declared (one atom, the whole debt), not the detected list.
        applied = await service.execute_occurrence(
            occurrence_of(
                debt_ids, equivalent_id=equivalent_id, amount=_SMALLEST_STORABLE, plan_id=TEST_PLAN_ID, ordinal=0
            )
        )

    assert applied == _SMALLEST_STORABLE, (
        f"precondition: the whole debt must have cleared, got {applied!r}"
    )

    async with TestingSessionLocal() as verify:
        tx = (
            await verify.scalars(
                select(Transaction).where(
                    Transaction.type == "CLEARING",
                    transactions_of(participant_ids),
                )
            )
        ).one()
        audits = (
            await verify.scalars(
                select(IntegrityAuditLog).where(
                    IntegrityAuditLog.equivalent_code == code
                )
            )
        ).all()

    payload = tx.payload or {}
    stored = [str(payload.get("amount"))] + [
        str(edge.get("amount")) for edge in (payload.get("edges") or [])
    ]
    for audit in audits:
        stored += [
            str(edge.get("amount"))
            for edge in ((audit.affected_participants or {}).get("edges") or [])
        ]

    assert len(stored) >= 4, f"precondition: the payload must carry amounts: {payload}"
    offenders = [a for a in stored if _is_exponential(a)]
    assert not offenders, (
        f"exponential money PERSISTED in transactions.payload / integrity_audit_log: "
        f"{offenders}. This is the column the T1201 rollout condition audits for "
        "scale >= 9, and a text-shaped audit reads no fraction digits at all in '1E-8'."
    )
    assert {Decimal(a) for a in stored} == {applied}, (
        f"the payload must round-trip to the amount `execute_occurrence` returned ({applied}): "
        f"{stored}"
    )
