"""T1202 / slice B: the money modules and the two things they get wrong for not reading precision.

WHY THIS MODULE EXISTS.  `F-012-2` measures an absence - zero occurrences of `precision` in
`app/core/payments/`, `app/core/clearing/`, `app/core/trustlines/` and `app/core/balance/` - and
an absence is not a defect.  This module turns it into two observable ones, on configurations the
repository ships, and it is the replacement for `RT-012-3`, which the spec withdrew as artificial
on reasoning that `T1203` then showed to be true only for `precision <= 1`.

  * PART A - THE CLEARING THRESHOLD GIVES A WRONG ANSWER.  `app/core/clearing/service.py` binds
    `Decimal("0.01")` as `min_amount` at `:451` and `:557` and the SQL compares
    `AND {least_expr} > :min_amount` (`:498`, `:608`).  The comparison is strict and the shipped
    default `precision` is 2 (`seeds/equivalents.json`, `UAH` and `KWH`), so a triangle whose
    every leg is exactly `0.01` - the smallest amount that equivalent can express - is invisible
    to the SQL detector.  It is NOT invisible to the Python DFS underneath, which filters on
    `Debt.amount > 0` alone (`:1020`), so the two detectors disagree about what a real debt is,
    and `find_cycles` prefers the SQL one and returns early when it is non-empty (`:1011`).
    Consequence, and the reason this is `P2` and not `P3`: a graph holding one ordinary cycle and
    one at the boundary reports ONE of TWO, and `GET /api/v1/clearing/cycles` shows that verbatim.

  * PART B - THE BALANCE STRINGS ARE WRONG STRINGS.  `app/core/balance/service.py` renders every
    money field with bare `str(Decimal)` (`:198-205`, `:236`, `:254`) and consults no precision.
    Two separate failures fall out.  (1) `net_balance=str(total_credit - total_debt)` on two
    debts that cancel puts the literal `0E-8` on the wire - EXPONENTIAL MONEY, from the money
    core, on `GET /api/v1/balance`, on an entirely ordinary graph.  The spec's own invariant is
    "a money string never goes on the wire in exponent form", and `T1200` recorded that the
    reachable violation from `Numeric(20, 8)` is `E-`; this is it.  (2) Zero renders three
    different ways in one payload - `'0'` from the untouched `Decimal('0')` seed, `'0E-8'` from a
    subtraction, `'0.00000000'` from a sum - so the same money is three different strings.

WHAT WAS MEASURED BEFORE THE FIX (2026-08-24, PostgreSQL 16.9, `geov0_test_t1202`, migrated
schema `019_trust_lines_partial_unique_live`), against the production
`ClearingService.find_triangles_sql` and `BalanceService.get_summary`:

    UAH precision 2, every leg 0.01   -> find_triangles_sql: 0   find_cycles: 1 (the DFS)
    UAH precision 2, every leg 0.02   -> find_triangles_sql: 3   find_cycles: 1
    one 5.00 triangle + one 0.01      -> find_cycles: 1 of 2, and the 0.01 one is the lost one
    alice/bob owe each other 5.00 UAH -> net_balance == '0E-8'

WHAT THE FIX IS.  Part A is settled by MEASUREMENT and not by preference, and the measurement
said DROP the threshold rather than make it read `precision`; the reasoning is recorded on
`ClearingService.find_triangles_sql`.  In short: the threshold is not a dust filter, because the
DFS fallback already clears sub-quantum dust today; it is not a performance predicate, because
`LEAST(...)` over three joined tables can only ever be a post-join `Join Filter` and costs 0.5%
of buffers on a 400-participant / 4000-debt graph; and making it read `precision` would enact
"precision is the minimum money quantum" in the detector while the door does not enforce it -
the exact semantics `VERDICT-DOOR: C` deferred to a separate versioned decision.

PART B WAS RECORDED AS `xfail(strict=True)` AND IS NOW FIXED.  The tests were written to flip
loudly the moment the shared money-string renderer got a neutral home, and that is exactly what
happened: all six went `XPASS(strict)` in one run.  The renderer now lives in
`app/utils/money.py` - see its docstring for why the simulator package could not host it -
and the markers are gone.  The prose below is kept because the boundary it describes was real
and the reasoning is the record of how it was removed rather than stepped over.

WHAT THIS MODULE DELIBERATELY DOES NOT ASSERT.  Not that a sub-precision debt is rejected, not
that it is cleared away, and not that `0.005` is or is not money - that is the deferred decision.
It asserts only that a debt the door accepts and `Numeric(20, 8)` stores is visible to the
detector that exists to find it, and that a money value the system holds is not printed in
exponent form or at a scale the equivalent never asked for.

EVERY EQUIVALENT IS CREATED BY THE TEST.  `tests/conftest.py` builds the schema and nothing else
- it never reads `seeds/equivalents.json` - so `precision` here is set explicitly rather than
inherited from a fixture, and the parametrised cases would be meaningless otherwise.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.balance.service import BalanceService
from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

pytestmark = pytest.mark.postgres


# `LEAST(...) > 0.01` is the predicate under test, so the amounts below are chosen against the
# quantum of the equivalent they are stored under, never against the literal 0.01.
_OPEN_POLICY = {
    "auto_clearing": True,
    "can_be_intermediate": True,
    "max_hop_usage": None,
    "daily_limit": None,
    "blocked_participants": [],
}


def _quantum(precision: int) -> Decimal:
    """The smallest positive amount an equivalent of this precision can express.

    Written out arithmetically rather than taken from any function under test: this is the
    value the whole module is about, and computing it with the production helper would let a
    wrong helper agree with itself.
    """
    return Decimal(1).scaleb(-int(precision))


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
    """Create the equivalent this test needs, at the precision this test names.

    The codes used here are the shipped ones (`UAH` at 2, `HOUR` at 1) because the point is
    that these defects exist on the configuration in the box - but the row is created here and
    its `precision` is set here, never inherited: `tests/conftest.py` builds the schema and
    never reads `seeds/equivalents.json`, and a parametrised precision test that silently reused
    somebody else's `UAH` would be measuring that fixture instead of its own parameter.

    `equivalents.code` is unique, so any row another module committed and left behind would
    collide. Clearing it first inside this test's own transaction - which `db_session` rolls
    back - keeps the module independent of what ran before it without hiding the state: the
    delete is visible, scoped to one code, and undone at teardown.
    """
    await session.execute(delete(Equivalent).where(Equivalent.code == code))
    eq = Equivalent(
        id=uuid.uuid4(), code=code, symbol=code[:3], precision=precision, is_active=True
    )
    session.add(eq)
    await session.flush()
    return eq


async def _ring(
    session: AsyncSession, eq: Equivalent, names: list[str], amount: Decimal
) -> list[uuid.UUID]:
    """A closed ring n0 -> n1 -> ... -> n0, every edge consented to for auto clearing.

    Returns the debt ids, which is what the assertions identify a cycle by: the detectors are
    never asked to confirm their own output, only to name debts the fixture created.
    """
    people = [await _participant(session, n) for n in names]
    await session.flush()
    debt_ids: list[uuid.UUID] = []
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
        debt_ids.append(debt.id)
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
    return debt_ids


def _cycle_debt_id_sets(cycles) -> list[frozenset[str]]:
    return [frozenset(str(edge["debt_id"]) for edge in cycle) for cycle in cycles]


# --------------------------------------------------------------------------------------------
# PART A - the clearing threshold
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "precision, leg",
    [
        # The smallest amount each shipped precision can express.  `UAH`/`KWH` ship with 2 and
        # `HOUR` with 1, so the first row is the case that actually exists in the box.
        (2, Decimal("0.01")),
        (1, Decimal("0.1")),
        (0, Decimal("1")),
        # The smallest amount `Numeric(20, 8)` can hold at all.  The door admits it (scale 8 is
        # exactly `DEFAULT_MAX_AMOUNT_SCALE`) and storage keeps it exactly, so the detector is
        # the only stage that can lose it.
        (8, Decimal("0.00000001")),
    ],
)
async def test_a_triangle_at_the_smallest_expressible_amount_is_detected(
    db_session: AsyncSession, precision: int, leg: Decimal
) -> None:
    """The smallest real triangle there can be is still a triangle."""

    assert leg == _quantum(precision), "the parametrisation must use the equivalent's own quantum"

    eq = await _equivalent(db_session, "UAH", precision)
    debt_ids = await _ring(db_session, eq, ["a", "b", "c"], leg)
    expected = frozenset(str(d) for d in debt_ids)

    stored = (
        await db_session.execute(select(Debt.amount).where(Debt.id == debt_ids[0]))
    ).scalar_one()
    assert stored == leg, (
        f"precondition: Numeric(20, 8) must hold {leg} exactly, got {stored!r}. "
        "If this fails the test is measuring storage, not the detector."
    )

    service = ClearingService(db_session)
    found = _cycle_debt_id_sets(await service.find_triangles_sql(eq.id))

    assert expected in found, (
        f"a triangle whose every leg is {leg} - the smallest amount a precision-{precision} "
        f"equivalent can express, accepted by the door and stored exactly - is invisible to "
        f"find_triangles_sql. Found {len(found)} cycle(s): {found}. "
        "The strict `LEAST(...) > 0.01` threshold is the only stage that can drop it."
    )


async def test_a_quadrangle_at_the_smallest_expressible_amount_is_detected(
    db_session: AsyncSession,
) -> None:
    """The same hole, one edge wider: `find_quadrangles_sql` carries the identical predicate."""

    eq = await _equivalent(db_session, "UAH", 2)
    debt_ids = await _ring(db_session, eq, ["a", "b", "c", "d"], _quantum(2))
    expected = frozenset(str(d) for d in debt_ids)

    service = ClearingService(db_session)
    found = _cycle_debt_id_sets(await service.find_quadrangles_sql(eq.id))

    assert expected in found, (
        f"a four-node cycle at one quantum is invisible to find_quadrangles_sql. Found: {found}"
    )


async def test_the_sql_detector_and_the_dfs_fallback_agree_on_what_a_real_debt_is(
    db_session: AsyncSession,
) -> None:
    """Two detectors, one graph, one answer.

    `find_cycles` prefers the SQL detector and returns early when it is non-empty, falling
    through to the Python DFS only when SQL comes back empty.  That preference is sound only if
    the two admit the same debts.  Before the fix the DFS filtered on `Debt.amount > 0` and the
    SQL added `LEAST(...) > 0.01`, so on this graph the fallback found the cycle the fast path
    could not - the disagreement, observed without monkeypatching or disabling any guard.

    Audited, so that "they agree" is a claim and not a hope: both paths require `amount > 0`,
    both exclude pairs locked by a prepared payment, and both run every candidate through
    `_filter_cycles_by_auto_clearing_policy_sql` -> `_cycle_respects_auto_clearing`, which
    demands an active controlling trust line with `auto_clearing` on for every edge, exactly as
    the SQL JOINs do.  The threshold was the only admission rule that differed.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    debt_ids = await _ring(db_session, eq, ["a", "b", "c"], _quantum(2))
    expected = frozenset(str(d) for d in debt_ids)

    service = ClearingService(db_session)
    via_sql = _cycle_debt_id_sets(await service.find_triangles_sql(eq.id))
    via_find_cycles = _cycle_debt_id_sets(await service.find_cycles("UAH", max_depth=3))

    assert (expected in via_sql) == (expected in via_find_cycles), (
        "the SQL fast path and the detector `find_cycles` actually answers with disagree "
        f"about the same graph: find_triangles_sql -> {via_sql}, find_cycles -> "
        f"{via_find_cycles}. "
        "`find_cycles` prefers the SQL result whenever it is non-empty, so a disagreement is "
        "not academic: it decides what the API reports."
    )
    assert expected in via_find_cycles, "the cycle exists; both detectors must see it"


async def test_a_graph_with_an_ordinary_and_a_boundary_cycle_reports_both(
    db_session: AsyncSession,
) -> None:
    """The user-visible shape of the defect: one of two.

    With an ordinary cycle present the SQL detector returns non-empty, `find_cycles` returns
    early, and the boundary cycle is never looked for by the fallback that would have found it.
    `GET /api/v1/clearing/cycles` renders the result verbatim.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    ordinary = frozenset(
        str(d) for d in await _ring(db_session, eq, ["o1", "o2", "o3"], Decimal("5.00"))
    )
    boundary = frozenset(
        str(d) for d in await _ring(db_session, eq, ["b1", "b2", "b3"], _quantum(2))
    )

    service = ClearingService(db_session)
    found = _cycle_debt_id_sets(await service.find_cycles("UAH", max_depth=3))

    assert ordinary in found, "control: the ordinary cycle must be reported"
    assert boundary in found, (
        "the boundary cycle is missing while the ordinary one is reported: "
        f"{len(found)} cycle(s) for two that exist. This is the reported-one-of-two effect - "
        "the SQL detector drops the boundary cycle, `find_cycles` returns early because the "
        "SQL result is non-empty, and the DFS that would have found it never runs."
    )


async def test_the_detector_does_not_privately_decide_that_sub_quantum_debt_is_not_debt(
    db_session: AsyncSession,
) -> None:
    """The test that separates "drop the threshold" from "make it read `precision`".

    Every other test in Part A is satisfied by either design: a threshold of
    `>= 10**-precision` would also admit a triangle whose legs are exactly one quantum. This
    one is not. `0.005` under a precision-2 equivalent is finer than the quantum, and it is
    nonetheless a debt the system really holds: the door admits it (scale 3 is well inside
    `DEFAULT_MAX_AMOUNT_SCALE`), `Numeric(20, 8)` stores it exactly, `chk_debt_amount_positive`
    is satisfied, and - measured - the Python DFS in `find_cycles` finds and clears it today.

    So the threshold never suppressed sub-quantum dust system-wide; it only made the SQL fast
    path disagree with the fallback about what a debt is. A precision-driven threshold would
    keep that disagreement AND would make the detector, alone, enact "precision is the minimum
    money quantum" - the semantics `VERDICT-DOOR: C` deferred to a separate versioned decision
    with a data audit, on the reasoning that `precision` is admin-editable.

    This assertion does NOT claim `0.005` ought to be money. It claims the detector must not be
    the stage that privately decides it is not while the door, the storage and the fallback all
    say it is.
    """

    eq = await _equivalent(db_session, "UAH", 2)
    sub_quantum = Decimal("0.005")
    assert sub_quantum < _quantum(2), "the fixture must be finer than the declared quantum"

    debt_ids = await _ring(db_session, eq, ["s1", "s2", "s3"], sub_quantum)
    expected = frozenset(str(d) for d in debt_ids)

    stored = (
        await db_session.execute(select(Debt.amount).where(Debt.id == debt_ids[0]))
    ).scalar_one()
    assert stored == sub_quantum, (
        f"precondition: storage must hold {sub_quantum} exactly, got {stored!r}"
    )

    service = ClearingService(db_session)
    via_sql = _cycle_debt_id_sets(await service.find_triangles_sql(eq.id))
    via_find_cycles = _cycle_debt_id_sets(await service.find_cycles("UAH", max_depth=3))

    assert expected in via_find_cycles, (
        "control: the DFS fallback finds this cycle today, which is the whole point - the "
        "threshold was never protecting the system from sub-quantum cycles"
    )
    assert expected in via_sql, (
        f"the SQL detector drops a cycle the fallback finds and clears: find_triangles_sql -> "
        f"{via_sql}. Whatever the right answer about sub-quantum money is, it cannot be "
        "decided here, by one of two detectors, while the other one clears it."
    )


# --------------------------------------------------------------------------------------------
# PART B - the balance strings.  WAS `xfail(strict=True)`; FIXED once the renderer had a home.
# The boundary below was real, and it is recorded because it is why these were red at all.
#
# These four tests are red on the current code and they measure real, user-visible defects on
# `GET /api/v1/balance`; they are kept red-and-declared rather than deleted because a
# reproducer is the evidence and deleting it would lose the finding.
#
# WHY T1202 DID NOT FIX THEM.  Fixing any of them means rendering a money value against its
# equivalent's precision, and that needs the money-string renderer.  The repository has exactly
# one - `to_money_str`, built by `T1207` and defended by a cross-producer agreement test - and
# it lives in `app/core/simulator/net_balance_utils.py`.  Three ways to reach it were tried and
# all three leave slice B:
#
#   * IMPORT IT.  Measured, not assumed: `app/core/simulator/__init__.py` imports `.runtime`
#     eagerly, which pulls in `runtime_impl`, `artifacts`, `storage` and `app.db.session`, so
#     `from app.core.simulator.net_balance_utils import to_money_str` drags the whole simulator
#     runtime into the balance request path. The module itself is a leaf, its package is not.
#   * MOVE IT to a neutral home. That is an edit inside `app/core/simulator/`, which this task
#     is explicitly forbidden to touch.
#   * COPY IT into `app/core/balance/` (and again into `app/core/clearing/`). That is the fifth
#     and sixth copy of a formatter whose duplication `T1207` existed to end, and `## Optimal`
#     names making it COMMON - not duplicating it - as the work.
#
# Slice B grants "reading `Equivalent.precision` and nothing else" in four modules. It grants no
# home for a shared renderer, and this programme's own record says the honest move when the
# boundary makes a task unexecutable is to write it down. So it is written down here: this
# cluster belongs to `F-012-9`'s class (the form of a money string), not to the comparison half
# of `F-012-2`, and `T1207` set the precedent by reporting the eighteen money-string producers
# it found in `app/api/v1/simulator.py` instead of reaching outside its own surface for them.
#
# `strict=True` on purpose: whoever gives the renderer a neutral home and wires it in will see
# these turn from xpass into a hard failure telling them to drop the marker.
# --------------------------------------------------------------------------------------------

_PART_B_BOUNDARY = (
    "T1202 / slice B: needs the shared money-string renderer, which lives behind the "
    "simulator package's eager `__init__`; giving it a neutral home is outside 'reading "
    "Equivalent.precision and nothing else'. Reported, not widened."
)


@pytest_asyncio.fixture
async def cancelling_pair(db_session: AsyncSession):
    """Alice and Bob owe each other the same ordinary amount, so every total is exact.

    Nothing here is unusual: two debts, one equivalent, the shipped default precision.
    """
    eq = await _equivalent(db_session, "UAH", 2)
    alice = await _participant(db_session, "alice")
    bob = await _participant(db_session, "bob")
    await db_session.flush()
    for debtor, creditor in ((alice, bob), (bob, alice)):
        db_session.add(
            Debt(
                id=uuid.uuid4(),
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal("5.00"),
            )
        )
    # Only Bob extends trust, so Alice has spend capacity and no receive capacity. That is an
    # ordinary asymmetric relationship, and it is what makes `available_to_receive` keep the
    # untouched `Decimal('0')` seed while `net_balance` comes out of a subtraction - the two
    # zeros whose rendering the payload then disagrees about.
    db_session.add(
        TrustLine(
            id=uuid.uuid4(),
            from_participant_id=bob.id,
            to_participant_id=alice.id,
            equivalent_id=eq.id,
            limit=Decimal("100"),
            policy=dict(_OPEN_POLICY),
            status="active",
        )
    )
    await db_session.flush()
    return {"eq": eq, "alice": alice, "bob": bob}


_MONEY_FIELDS = (
    "total_debt",
    "total_credit",
    "net_balance",
    "available_to_spend",
    "available_to_receive",
)


async def test_no_balance_field_is_ever_in_exponent_notation(
    db_session: AsyncSession, cancelling_pair
) -> None:
    """`net_balance` on two debts that cancel is the literal string `0E-8` today."""

    summary = await BalanceService(db_session).get_summary(cancelling_pair["alice"].id)
    (entry,) = summary.equivalents

    offenders = {
        field: getattr(entry, field)
        for field in _MONEY_FIELDS
        if "e" in getattr(entry, field).lower()
    }
    assert not offenders, (
        f"exponent notation on the wire from the money core: {offenders}. "
        "`Numeric(20, 8)` gives every stored value exponent -8, and a subtraction that lands "
        "on zero keeps it, so `str(Decimal)` prints `0E-8`. This is `GET /api/v1/balance`."
    )


async def test_one_balance_payload_renders_zero_exactly_one_way(
    db_session: AsyncSession, cancelling_pair
) -> None:
    """The same money must be the same string.

    In one response today: `total_credit` is `'5.00000000'`, `net_balance` is `'0E-8'`, and
    `available_to_receive` - which is also zero - is `'0'`.
    """

    summary = await BalanceService(db_session).get_summary(cancelling_pair["alice"].id)
    (entry,) = summary.equivalents

    zeros = {
        field: getattr(entry, field)
        for field in _MONEY_FIELDS
        if Decimal(getattr(entry, field)) == 0
    }
    assert len(zeros) >= 2, f"precondition: this fixture must produce several zeros, got {zeros}"
    assert len(set(zeros.values())) == 1, (
        f"one payload renders zero in more than one form: {zeros}. A reader cannot compare "
        "these as strings, and a reader that parses them cannot tell which scale is meant."
    )


@pytest.mark.parametrize(
    "precision",
    [
        pytest.param(p)
        for p in (0, 1, 2, 4)
    ]
    # Precision 8 is the CONTROL and carries no marker: it is the one value that coincides with
    # the scale of `Numeric(20, 8)`, so today's precision-blind rendering already agrees with it.
    # It must be green before and after, and it is what proves the other four are failing on
    # precision rather than on something the fixture does.
    + [8],
)
async def test_balance_renders_the_number_of_digits_the_equivalent_declares(
    db_session: AsyncSession, precision: int
) -> None:
    """The screen shows as many digits as the equivalent declares - the programme's `Intended`.

    The expected string is built here from `precision` by string arithmetic, not by calling any
    renderer: a test that asked the production formatter what to expect would agree with a
    broken formatter. The parametrisation is itself the counter-check - substituting `precision`
    must change the output, and a test that does not react to it is indistinguishable from its
    own absence.
    """

    eq = await _equivalent(db_session, "UAH", precision)
    alice = await _participant(db_session, "alice")
    bob = await _participant(db_session, "bob")
    await db_session.flush()
    db_session.add(
        Debt(
            id=uuid.uuid4(),
            debtor_id=alice.id,
            creditor_id=bob.id,
            equivalent_id=eq.id,
            amount=Decimal("7"),
        )
    )
    await db_session.flush()

    summary = await BalanceService(db_session).get_summary(alice.id)
    (entry,) = summary.equivalents

    expected = "7" if precision == 0 else "7." + "0" * precision
    assert entry.total_debt == expected, (
        f"a precision-{precision} equivalent must render 7 as {expected!r}, got "
        f"{entry.total_debt!r}. Today every value is printed at the storage scale of "
        "`Numeric(20, 8)` regardless of what the equivalent declares."
    )
    assert entry.net_balance == ("-" + expected), (
        f"the sign must not change the form: expected {'-' + expected!r}, got "
        f"{entry.net_balance!r}"
    )


async def test_precision_never_erases_an_amount_the_ledger_actually_holds(
    db_session: AsyncSession,
) -> None:
    """Reading `precision` must not become a licence to round money away.

    `HOUR` ships with `precision: 1` and the door accepts `0.05` for it (scale 2 is far below
    `DEFAULT_MAX_AMOUNT_SCALE`), so `Numeric(20, 8)` holds a real, committed `0.05 HOUR`. A
    renderer that quantised down to the declared precision would report `0.0` for it - the debt
    would exist and the balance would say it does not. This is the `RT-012-2` failure applied to
    the balance surface, and it is why the fix sets a MINIMUM number of digits rather than a
    fixed one.
    """

    eq = await _equivalent(db_session, "HOUR", 1)
    alice = await _participant(db_session, "alice")
    bob = await _participant(db_session, "bob")
    await db_session.flush()
    db_session.add(
        Debt(
            id=uuid.uuid4(),
            debtor_id=alice.id,
            creditor_id=bob.id,
            equivalent_id=eq.id,
            amount=Decimal("0.05"),
        )
    )
    await db_session.flush()

    summary = await BalanceService(db_session).get_summary(alice.id)
    (entry,) = summary.equivalents

    assert Decimal(entry.total_debt) == Decimal("0.05"), (
        f"a stored 0.05 HOUR must not be rounded away by the renderer: got "
        f"{entry.total_debt!r} for a precision-1 equivalent."
    )
    debts = await BalanceService(db_session).get_debts(alice.id, "HOUR", "outgoing")
    assert Decimal(debts.outgoing[0].amount) == Decimal("0.05"), (
        f"the per-debt listing must not round it away either: {debts.outgoing[0].amount!r}"
    )
