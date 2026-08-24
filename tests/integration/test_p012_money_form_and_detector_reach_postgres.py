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

EVERY EQUIVALENT IS CREATED BY THE TEST.  `tests/conftest.py` builds the schema and never reads
`seeds/equivalents.json`, so `precision` here is set explicitly; a test that inherited someone
else's `UAH` would be measuring that fixture.
"""

from __future__ import annotations

import inspect
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import clearing as clearing_route
from app.core.balance.service import BalanceService
from app.core.clearing.service import (
    _SQL_DETECTOR_MAX_CYCLE_LENGTH,
    ClearingService,
)
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine

pytestmark = pytest.mark.postgres


def _route_default(endpoint, name: str) -> int:
    """The default a route publishes, read from the route.

    Repeating `6` here would let the guard and the API drift apart silently, which is the
    precise mechanism that hid this defect: the first-round tests named a depth of their own
    and it was not the one users get.
    """

    marker = inspect.signature(endpoint).parameters[name].default
    return int(getattr(marker, "default", marker))


API_DEFAULT_MAX_DEPTH = _route_default(clearing_route.list_cycles, "max_depth")
API_AUTO_DEFAULT_MAX_DEPTH = _route_default(clearing_route.auto_clear, "max_depth")

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
@pytest.mark.parametrize(
    "ring_size, method",
    [(3, "find_triangles_sql"), (4, "find_quadrangles_sql")],
)
async def test_the_sql_detectors_render_money_plainly_and_at_the_declared_precision(
    db_session: AsyncSession,
    ring_size: int,
    method: str,
    precision: int,
    leg: Decimal,
    expected: str,
) -> None:
    """The two raw-SQL producers, addressed directly.

    DIRECTLY IS NOT PEDANTRY HERE.  Going through `find_cycles` at the API's default depth
    does NOT exercise these: past the SQL detectors' reach both detectors run and the union is
    de-duplicated by debt-id set, keeping the first occurrence, which is the DFS's rendition of
    the cycle.  Measured - reverting either SQL renderer to `str()` leaves every `find_cycles`
    test green.  So the SQL producers are asked here in their own right, and by name, which is
    also the only way to exercise the `precision` lookup they do when a caller passes none.
    """

    eq = await _equivalent(db_session, "UAH", precision)
    expected_cycle = await _ring(
        db_session, eq, [f"n{i}" for i in range(ring_size)], leg
    )

    cycles = await getattr(ClearingService(db_session), method)(eq.id)
    assert expected_cycle in _cycle_sets(cycles), (
        f"precondition: {method} must find the {ring_size}-ring it is being asked about. "
        f"Found: {_cycle_sets(cycles)}"
    )

    amounts = _all_amounts(cycles)
    offenders = [a for a in amounts if _is_exponential(a)]
    assert not offenders, (
        f"exponential money from {method}, which feeds GET /api/v1/clearing/cycles verbatim: "
        f"{offenders}"
    )
    assert set(amounts) == {expected}, (
        f"{method} must render {leg} under a precision-{precision} equivalent as {expected!r}: "
        f"got {sorted(set(amounts))}"
    )


@pytest.mark.parametrize(
    "ring_size, max_depth, producer",
    [
        (3, 3, "find_triangles_sql, through the early return"),
        (4, 4, "find_quadrangles_sql, through the early return"),
        (5, API_DEFAULT_MAX_DEPTH, "the Python DFS"),
    ],
)
async def test_no_routed_answer_puts_an_exponent_on_the_wire(
    db_session: AsyncSession, ring_size: int, max_depth: int, producer: str
) -> None:
    """The same three producers as `find_cycles` actually routes to them.

    A 3-ring at depth 3 and a 4-ring at depth 4 are inside the SQL detectors' reach, so the
    early return answers and the raw-SQL rendition is what reaches the caller; a 5-ring is
    past that reach and only the DFS can build it.  Parametrising by (ring length, depth) is
    therefore parametrising by producer without reaching into the service to force a path.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    expected = await _ring(
        db_session, eq, [f"n{i}" for i in range(ring_size)], _SMALLEST_STORABLE
    )

    cycles = await ClearingService(db_session).find_cycles("UAH", max_depth=max_depth)
    assert expected in _cycle_sets(cycles), (
        f"precondition: the {ring_size}-ring must be found at depth {max_depth}, so that "
        f"{producer} is the producer under test. Found: {_cycle_sets(cycles)}"
    )

    amounts = _all_amounts(cycles)
    offenders = [a for a in amounts if _is_exponential(a)]
    assert not offenders, (
        f"exponential money from {producer} on GET /api/v1/clearing/cycles: {offenders}. "
        f"All amounts: {amounts}"
    )
    assert all(Decimal(a) == _SMALLEST_STORABLE for a in amounts), (
        f"the value must survive the rendering: {amounts}"
    )


async def test_one_debt_has_one_string_whichever_detector_answered(
    db_session: AsyncSession,
) -> None:
    """The SAME debts, asked of both producers, must come back byte for byte alike.

    This is the "two scales for one debt" half of the finding, and it needs the same rows on
    both sides or it would be comparing fixtures rather than renderers.  So one triangle is
    built once and read twice: through `find_triangles_sql`, where asyncpg hands back the
    column's own scale, and through `find_cycles` at the route's default depth, where the ORM
    DFS produces the answer.  A reader who cannot compare two responses as strings cannot use
    them as identifiers, and one who parses them cannot tell which scale was meant.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangle = await _ring(db_session, eq, ["p1", "p2", "p3"], Decimal("0.01"))
    service = ClearingService(db_session)

    via_sql = await service.find_triangles_sql(eq.id)
    via_routed = await service.find_cycles("UAH", max_depth=API_DEFAULT_MAX_DEPTH)
    assert triangle in _cycle_sets(via_sql) and triangle in _cycle_sets(via_routed), (
        "precondition: both producers must return the one triangle this test built"
    )

    sql_forms = set(_all_amounts(via_sql))
    routed_forms = set(_all_amounts(via_routed))
    assert sql_forms == routed_forms == {"0.01"}, (
        f"one debt, two renderings: find_triangles_sql -> {sorted(sql_forms)}, "
        f"find_cycles -> {sorted(routed_forms)}. Which digits a caller sees must not depend "
        "on which detector answered."
    )


async def test_precision_widens_the_clearing_amount_but_never_narrows_the_value(
    db_session: AsyncSession,
) -> None:
    """The counter-check for the renderer's own parameter, on the clearing surface.

    Two claims at once, and they pull in opposite directions.  A precision-4 equivalent must
    show four digits for an ordinary `0.01` - proving the detectors read `precision` at all
    and that this test reacts to it.  And a precision-1 equivalent holding a real, stored
    `0.05` must still report `0.05`, not `0.0` - proving `precision` sets a MINIMUM number of
    digits and never a licence to round a debt away (`RT-012-2`, applied to clearing).
    """

    eq = await _equivalent(db_session, "UAH", 4)
    await _ring(db_session, eq, ["w1", "w2", "w3"], Decimal("0.01"))
    cycles = await ClearingService(db_session).find_cycles(
        "UAH", max_depth=API_DEFAULT_MAX_DEPTH
    )
    assert set(_all_amounts(cycles)) == {"0.0100"}, (
        f"a precision-4 equivalent must show four digits: {sorted(set(_all_amounts(cycles)))}"
    )

    hour = await _equivalent(db_session, "HOUR", 1)
    await _ring(db_session, hour, ["h1", "h2", "h3"], Decimal("0.05"))
    cycles = await ClearingService(db_session).find_cycles(
        "HOUR", max_depth=API_DEFAULT_MAX_DEPTH
    )
    amounts = set(_all_amounts(cycles))
    assert amounts == {"0.05"}, (
        f"a stored 0.05 under a precision-1 equivalent must not be rounded away by the "
        f"renderer: {sorted(amounts)}. The door accepts it and Numeric(20, 8) holds it."
    )


# --------------------------------------------------------------------------------------------
# PART 2 - the reach of the early return
# --------------------------------------------------------------------------------------------


def test_the_api_default_depth_is_past_what_the_sql_detectors_can_reach() -> None:
    """The premise every reach test below rests on, asserted instead of assumed.

    If someone lowers the route default to 4 or below, the SQL fast path can answer the whole
    question and the early return becomes sound again - at which point the tests below stop
    measuring anything and a reader must be told, not left with quietly vacuous assertions.
    """

    assert _SQL_DETECTOR_MAX_CYCLE_LENGTH == 4, (
        "find_triangles_sql joins three debts rows and find_quadrangles_sql four; if that "
        "changed, the constant and this module's reasoning must change together"
    )
    assert API_DEFAULT_MAX_DEPTH > _SQL_DETECTOR_MAX_CYCLE_LENGTH, (
        f"GET /api/v1/clearing/cycles defaults to max_depth={API_DEFAULT_MAX_DEPTH}, which no "
        f"longer exceeds the SQL detectors' reach of {_SQL_DETECTOR_MAX_CYCLE_LENGTH} edges. "
        "The reach tests below now prove nothing; re-read them before trusting them."
    )
    assert API_AUTO_DEFAULT_MAX_DEPTH == API_DEFAULT_MAX_DEPTH, (
        "the two clearing routes must not disagree about how deep clearing looks"
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
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangle = await _ring(db_session, eq, ["t1", "t2", "t3"], Decimal("0.01"))
    long_cycle = await _ring(
        db_session, eq, ["l1", "l2", "l3", "l4", "l5"], Decimal("50")
    )

    found = _cycle_sets(
        await ClearingService(db_session).find_cycles(
            "UAH", max_depth=API_DEFAULT_MAX_DEPTH
        )
    )

    assert triangle in found, "control: the short cycle must still be reported"
    assert long_cycle in found, (
        f"at the route's own default depth ({API_DEFAULT_MAX_DEPTH}) a 5-node cycle is missing "
        f"while a 3-node one is reported: {len(found)} cycle(s) for two that exist. The SQL "
        "fast path reaches 4 edges at most, and `find_cycles` returned its answer early "
        "because it was non-empty, so the DFS that would have found the long cycle never ran."
    )


@pytest.mark.parametrize("max_depth", [3, 4, 5, 6, 7])
async def test_a_long_cycle_appears_exactly_when_the_caller_asks_deep_enough(
    db_session: AsyncSession, max_depth: int
) -> None:
    """Across the boundary, not at one point on it.

    The first round's guard asked only at `max_depth=3`, the single depth where the two
    detectors have equal reach and the early return therefore cannot be wrong.  This walks
    both sides: below 5 the 5-cycle is genuinely out of scope and must be absent, from 5 up it
    was asked for and must be present - and the triangle must be reported at every depth,
    which is what stops "always run the DFS and drop the SQL result" from passing as a fix.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangle = await _ring(db_session, eq, ["t1", "t2", "t3"], Decimal("0.01"))
    long_cycle = await _ring(
        db_session, eq, ["l1", "l2", "l3", "l4", "l5"], Decimal("50")
    )

    found = _cycle_sets(
        await ClearingService(db_session).find_cycles("UAH", max_depth=max_depth)
    )

    assert triangle in found, (
        f"the 3-node cycle is within reach at every depth >= 3 and is missing at "
        f"{max_depth}: {found}"
    )
    if max_depth >= 5:
        assert long_cycle in found, (
            f"max_depth={max_depth} asks for cycles up to {max_depth} edges and the 5-node "
            f"one is missing: {found}"
        )
    else:
        assert long_cycle not in found, (
            f"max_depth={max_depth} must not return a 5-edge cycle: {found}"
        )


async def test_the_long_cycle_is_reported_whether_or_not_a_short_one_exists(
    db_session: AsyncSession,
) -> None:
    """The defect stated as the property it violates.

    The answer to "what cycles are there" must not depend on whether some OTHER, unrelated
    cycle happened to make one detector non-empty.  So the same 5-node cycle is asked for
    twice - once alone, once with a disjoint triangle beside it - and it must be reported both
    times.  This is the assertion that fails for any fix that merely reorders the detectors.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    long_cycle = await _ring(
        db_session, eq, ["l1", "l2", "l3", "l4", "l5"], Decimal("50")
    )
    service = ClearingService(db_session)

    alone = _cycle_sets(
        await service.find_cycles("UAH", max_depth=API_DEFAULT_MAX_DEPTH)
    )
    assert long_cycle in alone, f"control: with nothing else present it is found: {alone}"

    await _ring(db_session, eq, ["t1", "t2", "t3"], Decimal("0.01"))
    beside_a_triangle = _cycle_sets(
        await service.find_cycles("UAH", max_depth=API_DEFAULT_MAX_DEPTH)
    )
    assert long_cycle in beside_a_triangle, (
        "adding an unrelated triangle removed the 5-node cycle from the answer: "
        f"{alone} -> {beside_a_triangle}. Which cycles exist cannot depend on which detector "
        "was non-empty."
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
    """

    eq = await _equivalent(db_session, "UAH", 2)
    triangles = [
        await _ring(db_session, eq, [f"m{k}_{i}" for i in range(3)], Decimal("5"))
        for k in range(25)
    ]

    found = _cycle_sets(
        await ClearingService(db_session).find_cycles(
            "UAH", max_depth=API_DEFAULT_MAX_DEPTH
        )
    )

    missing = [i for i, t in enumerate(triangles) if t not in found]
    assert not missing, (
        f"{len(missing)} of {len(triangles)} triangles are missing at "
        f"max_depth={API_DEFAULT_MAX_DEPTH}: indices {missing}. Past the SQL detectors' reach "
        "both detectors must contribute; the DFS alone stops at fifty raw cycles and the SQL "
        "answer is what carries the rest."
    )


# --------------------------------------------------------------------------------------------
# PART 3 - the persisted payload
# --------------------------------------------------------------------------------------------


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
    here against the amount `execute_clearing_with_amount` actually applied, not against a
    literal, so the two cannot be wrong together.
    """

    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8].upper()
    code = f"PZ{nonce}"
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    debt_ids = [uuid.uuid4() for _ in range(3)]

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(id=equivalent_id, code=code, symbol="PZ", precision=2)
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
            cycles = await service.find_cycles(code, max_depth=API_DEFAULT_MAX_DEPTH)
            assert cycles, "precondition: the triangle must be detected before it is cleared"
            applied = await service.execute_clearing_with_amount(cycles[0])

        assert applied == _SMALLEST_STORABLE, (
            f"precondition: the whole debt must have cleared, got {applied!r}"
        )

        async with TestingSessionLocal() as verify:
            tx = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(participant_ids),
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
            f"the payload must replay to the amount that was actually applied ({applied}): "
            f"{stored}"
        )
    finally:
        primary_error = sys.exc_info()[1]
        try:
            async with TestingSessionLocal() as cleanup:
                await cleanup.execute(
                    delete(IntegrityAuditLog).where(
                        IntegrityAuditLog.equivalent_code == code
                    )
                )
                await cleanup.execute(
                    delete(Transaction).where(
                        Transaction.initiator_id.in_(participant_ids)
                    )
                )
                await cleanup.execute(
                    delete(Debt).where(Debt.equivalent_id == equivalent_id)
                )
                await cleanup.execute(
                    delete(TrustLine).where(TrustLine.equivalent_id == equivalent_id)
                )
                await cleanup.execute(
                    delete(Participant).where(Participant.id.in_(participant_ids))
                )
                await cleanup.execute(
                    delete(Equivalent).where(Equivalent.id == equivalent_id)
                )
                await cleanup.commit()
        except Exception:
            if primary_error is None:
                raise
