"""T1210 finding 6: one net, two encodings, and the fact they were allowed to disagree about.

WHAT THIS MODULE IS ABOUT, AND HOW IT DIFFERS FROM `T1207`.

`test_p012_t1207_one_money_form_across_producers.py` pins that the producers agree WITH EACH
OTHER: the snapshot and the SSE node patch must print the same `net_balance` bytes.  They did,
and they still do - and that was the hole.  Every producer can agree with every other producer
and all of them be wrong together, because `net_balance` is not the only encoding of a
participant's net on the wire.  The same payload carries `net_balance_atoms`, `net_sign`, and
- derived from those - `viz_color_key` and `viz_size`.  Nothing anywhere related the two.

WHO READS WHICH, measured in `simulator-ui/v2/src`:

  * `net_balance`  -> `NodeCardOverlay.netText` (`NodeCardOverlay.vue:90-92`), which PREFERS it
    and only falls back to the atoms when it is absent.  It is the number a human reads.
  * `net_balance_atoms` + `net_sign` -> the same `netText` fallback branch
    (`NodeCardOverlay.vue:94-108`, `atomsToMoney(signed, precision)`), and the transport
    plumbing that copies them onto the node (`useSimulatorApp.ts:908-909`,
    `demo/patches.ts:46-48`, `normalizeSimulatorEvent.ts:78-88`).
  * `viz_color_key` -> the node's fill (`NodeCardOverlay.vue:69-72`, and the renderer's
    mapping).  On the backend it is `debt-<bin>` when and only when the ATOMS are negative
    (`viz_rules.node_color_key`, `viz_patch_helper._node_color_key`).
  * `viz_size` -> the node's drawn width/height, from the percentile rank of `abs(atoms)`
    among all nodes (`viz_rules.node_size_wh`, `viz_patch_helper._node_size`).

So `net_balance` answers *how much*, and the atoms answer *how much, at the coarsest
resolution the equivalent declares*, because a drawing layer wants integers it can bucket and
rank.  Those are different questions, and a difference of RESOLUTION between the answers is
legitimate: at `precision: 1` a net of `0.14` is `"0.14"` on the card and one atom in the
ranking, and neither is lying.

THE DEFECT WAS A DIFFERENCE OF FACT, NOT OF RESOLUTION.  `to_money_str` (`T1207`) stopped
treating `precision` as the ledger's quantum, because it is a display parameter and
`Numeric(20, 8)` stores finer (`RT-012-2`).  `net_decimal_to_atoms` was never given the same
treatment, and ROUND_HALF_UP maps the entire open interval `(-q/2, q/2)` onto zero.  Result,
reproduced and pinned below: a stored net of `0.04` at `precision: 1` shipped
`net_balance: "0.04"` next to `net_sign: 0`, `net_balance_atoms: "0"` and the neutral
`person` colour.  The card read `0.04` on a node the graph painted as owing nothing - the
`RT-012-2` erasure, alive in the atoms channel after the string channel was fixed.

THE CONTRACT PINNED HERE.  With `m = Decimal(net_balance)`, `a` the signed atoms and
`q = 10**-precision`:

  1. `m` is the money that is stored           - a rendering may add digits, never change the
                                                 number.  This also re-guards `RT-012-2`.
  2. `sign(m) == net_sign`                     - no rounding may invent or erase a direction.
  3. `m == 0` if and only if `a == 0`          - whether a balance exists is a fact, not a
                                                 resolution.
  4. `abs(a*q - m) < q`                        - the atoms are `m` at resolution `q` and may
                                                 differ from it by less than one quantum:
                                                 that bounds the legitimate difference so it
                                                 cannot grow into drift.
  5. the colour is a debt colour iff `net_sign == -1` - the channel a human actually sees must
                                                 not contradict the other two.
  6. when `m` IS exactly representable at the precision, `a*q == m` exactly - everything that
                                                 was already right is pinned to strict
                                                 equality, not to a tolerance (section 3).

Properties 2, 3 and 5 are the ones that were false.  Property 4 was TRUE even at the moment of
the defect (`abs(0*q - 0.04) = 0.04 < 0.1`), and is here precisely so that nobody "fixes" this
by widening the atoms and calling a two-quantum drift acceptable.

MUTATIONS THIS CATCHES:

  * delete the `if atoms == 0 and net != 0` branch of `net_decimal_to_atoms`, i.e. restore
    plain ROUND_HALF_UP: the sub-quantum rows go red on properties 2, 3 and 5, on both
    producers.
  * apply that branch unconditionally, or swap ROUND_HALF_UP for ROUND_UP or ROUND_DOWN: the
    differential in section 3 goes red, because values that were already right moved.
  * re-quantise `net_balance` to `precision` in either producer - the "make the string agree
    with the atoms" fix: property 1 goes red, which is `RT-012-2` refusing to be re-opened.

Default tier, deliberately: nothing here depends on how the database rounds on write - both
producers are handed a `Decimal` they already hold - so it must be visible to the tier that
runs on every change.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulator.models import RunRecord
from app.core.simulator.net_balance_utils import net_decimal_to_atoms, to_money_str
from app.core.simulator.snapshot_builder import SnapshotBuilder
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

# `Equivalent.precision` is declared `ge=0, le=18`; the same span `T1207` used.
PRECISIONS = [0, 1, 2, 4, 8, 18]

# `Debt.amount` is `Numeric(20, 8)`.  A probe finer than this is not a test of the code, it is
# a test of what the column silently did to it on write.
STORAGE_QUANTUM = Decimal("0.00000001")

# The four rows of the finding, verbatim, as `(precision, stored net)`, so that the exact
# population the reviewer measured is inside the guard.  What each row turned out to be, once
# driven through the real producers rather than through the two helpers in isolation:
#   row 1  the defect - `"0.04"` next to zero atoms, zero sign and the neutral colour;
#   rows 2-3  differences of RESOLUTION, which the contract accepts and pins;
#   row 4  not a `precision: 0` case at all - both producers coerce a declared 0 to 2, so this
#          ships as `"0.60"` with 60 atoms, in exact agreement.  See `_effective_precision`.
FINDING_TABLE = [
    pytest.param(1, "0.04", id="prec1-0.04-was-erased-to-zero-atoms"),
    pytest.param(1, "0.05", id="prec1-0.05-the-RT-012-2-value"),
    pytest.param(1, "0.14", id="prec1-0.14-resolution-difference-only"),
    pytest.param(0, "0.6", id="prec0-0.6-precision-zero-is-coerced-to-two"),
]

_WHY = (
    "WHY THIS MATTERS: `net_balance` and (`net_balance_atoms`, `net_sign`) are two encodings "
    "of ONE participant's net, shipped side by side in one payload, and the second drives the "
    "colour and the size of the node a human looks at.  When they disagree about the SIGN, or "
    "about whether there is a balance at all, the graph says a participant owes nothing while "
    "the card on that same participant shows a debt.  Do not satisfy this assertion by "
    "quantising `net_balance` back to `precision`: that is `RT-012-2`, and it does not even "
    "produce agreement, because the string rounds DOWN and the atoms round HALF_UP."
)


def _utc_now() -> datetime:
    return datetime(2026, 8, 24, tzinfo=timezone.utc)


def _effective_precision(declared: int) -> int:
    """The precision the producers ACTUALLY use for an equivalent that declares `declared`.

    A SEPARATE DEFECT, FOUND BY THIS TEST'S POPULATION AND DELIBERATELY NOT FIXED HERE.  Both
    producers resolve the equivalent's precision as `int(getattr(eq, "precision", 2) or 2)`
    (`snapshot_builder.py:173`, `viz_patch_helper.py:54`).  `Equivalent.precision` is declared
    `ge=0`, and `0` is a legitimate value - a whole-units equivalent - but `0 or 2` is `2`, so
    a declared `precision: 0` is silently replaced by an invented `2`.  Measured: a stored net
    of `0.6` under a `precision: 0` equivalent ships as `net_balance: "0.60"` with `60` atoms,
    not as `"0.6"` with `1`.

    Not fixed by this task, for a measured reason rather than a scoping one: the same
    expression lives in `edge_patch_builder.py:71`, `inject_executor.py:266,271` and
    `real_clearing_engine.py:86`, which this task does not own, and repairing it in these two
    files alone would make `snapshot_builder` and `edge_patch_builder` disagree about `used`
    and `available` at precision 0 - breaking `T1207`'s cross-producer agreement, which is
    parametrised over exactly that precision.  It is reported instead.

    So the contract below is checked against the precision the code really used.  What the two
    producers must do IDENTICALLY is asserted regardless, which is the part that protects the
    encoding while the coercion is still there.
    """

    return int(declared) or 2


async def _fixture(session: AsyncSession, *, precision: int, amount: str):
    """One debt of `amount`, so the creditor's net is `+amount` and the debtor's `-amount`.

    Both signs of one magnitude come out of a single fixture, which is the point: the defect
    is sign-shaped, and a population carrying only positives could not see it.

    `amount="0"` creates no debt row at all: `chk_debt_amount_positive` forbids a zero debt, so
    a participant's zero net is the absence of debts, not a debt of zero.
    """

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce).upper()[:16], precision=precision, is_active=True)
    creditor = Participant(
        pid=f"CR{nonce}",
        display_name="creditor",
        public_key=f"pk-cr-{nonce}",
        type="person",
        status="active",
        profile={},
    )
    debtor = Participant(
        pid=f"DB{nonce}",
        display_name="debtor",
        public_key=f"pk-db-{nonce}",
        type="person",
        status="active",
        profile={},
    )
    session.add_all([eq, creditor, debtor])
    await session.commit()

    if Decimal(amount) != 0:
        session.add(
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal(amount),
            )
        )
        await session.commit()
    return eq, creditor, debtor


def _run_for(eq, creditor, debtor) -> RunRecord:
    run = RunRecord(
        run_id=f"t1210-{uuid.uuid4().hex[:8]}",
        scenario_id=f"scn-{uuid.uuid4().hex[:8]}",
        mode="real",
        state="running",
    )
    run._real_participants = [(creditor.id, creditor.pid), (debtor.id, debtor.pid)]
    run._scenario_raw = {
        "participants": [
            {"id": creditor.pid, "name": "creditor", "type": "person"},
            {"id": debtor.pid, "name": "debtor", "type": "person"},
        ],
        "trustlines": [],
    }
    return run


async def _both_producers(session: AsyncSession, eq, creditor, debtor):
    """The two real producers of the node encoding, driven rather than imitated.

    `snapshot_builder.py:274-278` (the REST snapshot) and `viz_patch_helper.py:288-296` (the
    SSE `node_patch`).  Returns `{pid: (snapshot_node_fields, node_patch)}`.

    `viz_size` is deliberately NOT compared between the two: it is a percentile of `abs(atoms)`
    within whatever population the producer was handed, and the two are handed different
    populations by construction.  Comparing it would pin an accident.
    """

    run = _run_for(eq, creditor, debtor)
    builder = SnapshotBuilder(
        lock=threading.RLock(),
        runs={run.run_id: run},
        scenarios={},
        utc_now=_utc_now,
        db_enabled=lambda: True,
    )
    snap = await builder.build_graph_snapshot(
        run_id=run.run_id, equivalent=eq.code, session=session
    )
    snap_by_pid = {n.id: n for n in snap.nodes}

    helper = await VizPatchHelper.create(session, equivalent_code=eq.code)
    patches = await helper.compute_node_patches(
        session,
        pids=[creditor.pid, debtor.pid],
        pid_to_participant={creditor.pid: creditor, debtor.pid: debtor},
    )
    patch_by_pid = {p["id"]: p for p in patches}

    out: dict[str, tuple[dict, dict]] = {}
    for pid in (creditor.pid, debtor.pid):
        node = snap_by_pid[pid]
        out[pid] = (
            {
                "net_balance": node.net_balance,
                "net_balance_atoms": node.net_balance_atoms,
                "net_sign": node.net_sign,
                "viz_color_key": node.viz_color_key,
                "viz_size": node.viz_size,
            },
            patch_by_pid[pid],
        )
    return out


def _check_one_payload(
    payload: dict, *, producer: str, precision: int, expected: Decimal
) -> None:
    """Properties 1-5 of the contract, on one participant's fields in one payload.

    `precision` here is the EFFECTIVE precision - see `_effective_precision`.
    """

    q = Decimal(1).scaleb(-precision)
    where = f"{producer}, precision {precision}, stored net {expected}"

    money_text = payload["net_balance"]
    assert money_text is not None, f"{where}: no `net_balance` was emitted at all"
    m = Decimal(money_text)
    sign_of_money = 0 if m == 0 else (1 if m > 0 else -1)

    net_sign = int(payload["net_sign"])
    magnitude = int(payload["net_balance_atoms"])
    assert magnitude >= 0, (
        f"{where}: `net_balance_atoms` is a MAGNITUDE and the sign travels in `net_sign` "
        f"(`fixtures.ts:124-131` rejects a signed one), but got "
        f"{payload['net_balance_atoms']!r}"
    )
    atoms = magnitude * (1 if net_sign >= 0 else -1)

    # 1. The string is the money that is stored.  `RT-012-2` lives here.
    assert m == expected, (
        f"{where}: `net_balance` is {money_text!r}, which is not the stored number.  A "
        f"rendering may add digits; it may not change the value.  If this failed because the "
        f"string was re-quantised to `precision` to make it match the atoms, that is exactly "
        f"the `RT-012-2` defect being re-opened.\n{_WHY}"
    )
    assert "e" not in money_text.lower(), (
        f"{where}: exponential money on the wire: {money_text!r}"
    )

    # 2. Same direction.
    assert net_sign == sign_of_money, (
        f"{where}: `net_balance` is {money_text!r} (sign {sign_of_money}) but `net_sign` is "
        f"{net_sign}.  Two encodings of one number, disagreeing about which way it points."
        f"\n{_WHY}"
    )

    # 3. Whether there is a balance at all is a fact, not a resolution.
    assert (m == 0) == (atoms == 0), (
        f"{where}: `net_balance` is {money_text!r} but the signed atoms are {atoms}.  One of "
        f"these says there is a balance and the other says there is none.  `precision` is a "
        f"DISPLAY parameter - `Numeric(20, 8)` stored this value faithfully - so a value "
        f"below the display quantum is money that is hard to show, not money that is absent."
        f"\n{_WHY}"
    )

    # 4. And the difference between them is bounded by the resolution they differ in.
    assert abs(Decimal(atoms) * q - m) < q, (
        f"{where}: the atoms reconstruct to {Decimal(atoms) * q} against a `net_balance` of "
        f"{money_text!r}: more than one quantum ({q}) apart.  The atoms are allowed to be "
        f"coarser than the string; they are not allowed to be a different number.\n{_WHY}"
    )

    # 5. The channel a human actually sees agrees with the other two.
    #
    # `viz_rules.node_color_key` lets a non-active STATUS override the balance colour by
    # design, so the property is stated only where the balance is what decides.  Every
    # participant this module creates is `active`, so the branch below is the live one; the
    # guard exists so that widening the population to a suspended debtor later reports "not
    # applicable" instead of a false failure.
    colour = str(payload["viz_color_key"] or "")
    if colour in {"suspended", "left", "deleted"}:
        return
    assert colour.startswith("debt-") == (net_sign == -1), (
        f"{where}: `net_sign` is {net_sign} and `net_balance` is {money_text!r}, but the node "
        f"is painted {colour!r}.  `viz_color_key` is derived from the atoms, and this is where "
        f"the disagreement becomes visible to a human: a participant who owes money drawn in "
        f"the neutral colour, or a participant who owes nothing drawn as a debtor.\n{_WHY}"
    )


def _check_both(both: dict, *, precision: int, amount: Decimal) -> None:
    """`precision` is the DECLARED `Equivalent.precision`; the contract is checked against the
    effective one, for the reason recorded in `_effective_precision`."""

    effective = _effective_precision(precision)
    creditor_pid, debtor_pid = list(both.keys())
    expected_by_pid = {creditor_pid: amount, debtor_pid: -amount}

    for pid, (snapshot_node, node_patch) in both.items():
        _check_one_payload(
            snapshot_node,
            producer="snapshot_builder.build_graph_snapshot",
            precision=effective,
            expected=expected_by_pid[pid],
        )
        _check_one_payload(
            node_patch,
            producer="viz_patch_helper.compute_node_patches",
            precision=effective,
            expected=expected_by_pid[pid],
        )
        # `T1207`'s property, extended from the string to the whole encoding: the two
        # producers must ship all three fields byte for byte.
        for key in ("net_balance", "net_balance_atoms", "net_sign"):
            assert snapshot_node[key] == node_patch[key], (
                f"the snapshot and the node patch disagree about {key} for {pid} at "
                f"precision {precision} on a stored net of {expected_by_pid[pid]}: "
                f"{snapshot_node[key]!r} vs {node_patch[key]!r}.\n{_WHY}"
            )


# ---------------------------------------------------------------------------
# 1. The finding's own table, through the real producers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("precision", "amount"), FINDING_TABLE)
async def test_the_two_encodings_of_one_net_agree_on_the_findings_own_table(
    db_session: AsyncSession, precision: int, amount: str
) -> None:
    eq, creditor, debtor = await _fixture(db_session, precision=precision, amount=amount)
    both = await _both_producers(db_session, eq, creditor, debtor)
    _check_both(both, precision=precision, amount=Decimal(amount))

    if precision == 0:
        # WHAT THIS ROW ACTUALLY MEASURES, and it is not what the finding's table predicts.
        # The table gives `net_balance: "0.6"` with `1` atom for a `precision: 0` equivalent.
        # Driving the real producers gives `"0.60"` with `60`, because both of them resolve
        # the precision as `... or 2` and a declared `0` is falsy.  The table's row is a
        # measurement of `to_money_str`/`net_decimal_to_atoms` in isolation, not of the
        # producers.  Pinned here so the divergence is a recorded fact rather than a surprise;
        # see `_effective_precision` for why it is reported and not repaired by this task.
        creditor_pid = list(both.keys())[0]
        snapshot_node = both[creditor_pid][0]
        assert snapshot_node["net_balance"] == "0.60", (
            f"a `precision: 0` equivalent is expected to be silently rendered at precision 2 "
            f"by the shipped producers, but `net_balance` came out "
            f"{snapshot_node['net_balance']!r}.  If this is now {'0.6'!r}, the `or 2` "
            f"coercion has been fixed - remove `_effective_precision` and this assertion, and "
            f"check that `edge_patch_builder.py:71` was fixed with it."
        )


# ---------------------------------------------------------------------------
# 2. The same contract over a population generated from each precision's own quantum
# ---------------------------------------------------------------------------


def _probes_for(precision: int) -> list[Decimal]:
    """Values chosen relative to the quantum rather than hand-written per precision.

    The population straddles `q/2` deliberately, because that is where ROUND_HALF_UP fell off
    a cliff, and it also carries values that ARE representable, so the same test exercises the
    case where nothing may move.  Anything finer than the storage column is dropped: it would
    be a test of what `Numeric(20, 8)` did on write, not of this code.
    """

    q = Decimal(1).scaleb(-precision)
    raw = [
        q * Decimal("0.4"),  # strictly inside half a quantum - the class that was erased
        q * Decimal("0.5"),  # the ROUND_HALF_UP boundary itself
        q * Decimal("0.6"),
        q * Decimal("1.4"),
        q,  # exactly representable
        q * 3,
        Decimal("0.05"),  # the `RT-012-2` value, at every precision
        Decimal("12.34"),
    ]
    out: list[Decimal] = []
    seen: set[str] = set()
    for value in raw:
        value = Decimal(format(value, "f"))
        if value <= 0 or value >= Decimal("1000000000000"):
            continue
        if value != value.quantize(STORAGE_QUANTUM):
            continue  # not storable in Numeric(20, 8): out of scope, by measurement
        key = format(value, "f")
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", PRECISIONS)
async def test_the_two_encodings_agree_at_every_precision_across_the_quantum_boundary(
    db_session: AsyncSession, precision: int
) -> None:
    # Generated from the precision the producers really use, so that the population actually
    # straddles the quantum they round at rather than the one the equivalent declares.
    probes = _probes_for(_effective_precision(precision))
    assert probes, f"precision {precision} generated no storable probes"

    for amount in probes:
        eq, creditor, debtor = await _fixture(
            db_session, precision=precision, amount=format(amount, "f")
        )
        both = await _both_producers(db_session, eq, creditor, debtor)
        _check_both(both, precision=precision, amount=amount)


@pytest.mark.asyncio
async def test_a_zero_net_still_ships_as_zero_in_both_encodings(
    db_session: AsyncSession,
) -> None:
    """The other direction of property 3: nothing may be invented out of nothing.

    A participant with no balance must not acquire a sign, an atom or a debt colour - the
    failure mode a careless "never round to zero" would produce, and the reason the guard in
    `net_decimal_to_atoms` is conditioned on `net != 0` rather than on the atoms alone.
    """

    eq, creditor, debtor = await _fixture(db_session, precision=1, amount="0")
    both = await _both_producers(db_session, eq, creditor, debtor)

    for pid, payloads in both.items():
        for producer, payload in (
            ("snapshot_builder.build_graph_snapshot", payloads[0]),
            ("viz_patch_helper.compute_node_patches", payloads[1]),
        ):
            assert Decimal(payload["net_balance"]) == 0, f"{producer} / {pid}: {payload!r}"
            assert int(payload["net_balance_atoms"]) == 0, f"{producer} / {pid}: {payload!r}"
            assert int(payload["net_sign"]) == 0, f"{producer} / {pid}: {payload!r}"
            assert not str(payload["viz_color_key"] or "").startswith("debt-"), (
                f"{producer} / {pid}: a participant with no balance was painted "
                f"{payload['viz_color_key']!r}"
            )


# ---------------------------------------------------------------------------
# 3. Nothing already-correct moved - the differential, the way `T1207` did it
# ---------------------------------------------------------------------------


def _old_net_decimal_to_atoms(net: Decimal, precision: int) -> int:
    """Verbatim the body `net_balance_utils.net_decimal_to_atoms` had before this change."""

    p = int(precision)
    if p < 0:
        p = 0
    scale10 = Decimal(10) ** p
    return int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))


def _representable_at(precision: int) -> list[Decimal]:
    quantum = Decimal(1).scaleb(-precision)
    out: list[Decimal] = []
    for n in (0, 1, 2, 5, 7, 9, 10, 11, 25, 99, 100, 101, 999, 1000, 12345, 99999999, 10**12 - 1):
        for sign in (1, -1):
            value = Decimal(n) * quantum * sign
            out.append(value)
            out.append(value.quantize(quantum))
            out.append(Decimal(format(value, "f")))
    for spelling in ("0", "1", "10", "100.00", "0.10", "10.25", "1000000", "0.05"):
        candidate = Decimal(spelling)
        if candidate == candidate.quantize(quantum):
            out.append(candidate)
    return out


@pytest.mark.parametrize("precision", list(range(0, 19)))
def test_a_value_representable_at_its_precision_keeps_exactly_the_atoms_it_had(
    precision: int,
) -> None:
    """A differential, not a table of expected numbers - the counter-check `T1207` used.

    Asserted against the OLD expression rather than against hand-written integers, so it
    cannot be satisfied by the author and the code merely agreeing with each other.
    """

    values = _representable_at(precision)
    assert values
    mismatches = [
        (
            format(v, "f"),
            _old_net_decimal_to_atoms(v, precision),
            net_decimal_to_atoms(v, precision=precision),
        )
        for v in values
        if _old_net_decimal_to_atoms(v, precision) != net_decimal_to_atoms(v, precision=precision)
    ]
    assert not mismatches, (
        f"at precision {precision}, {len(mismatches)} of {len(values)} values that ARE exactly "
        f"representable at that precision now convert to different atoms than before.  The "
        f"change was licensed only for values the precision cannot express; anything already "
        f"expressible must be identical.  First few (value, old, new): {mismatches[:5]!r}"
    )


@pytest.mark.parametrize("precision", list(range(0, 19)))
def test_every_net_whose_atoms_moved_was_one_the_precision_could_not_express(
    precision: int,
) -> None:
    """The bounding half: the change is not merely small, it is confined by a quantifier.

    `T1203` recorded a downgrade in this programme that was right about its example and wrong
    about its quantifier, so this is stated as quantifiers over a population rather than as an
    example.
    """

    q = Decimal(1).scaleb(-precision)
    probes = [
        Decimal(s)
        for s in (
            "0", "0.04", "-0.04", "0.05", "-0.05", "0.14", "0.6", "0.4", "1E-8", "-1E-8",
            "0.00000001", "0.123456789", "99.999999999", "0.1", "1.5", "10.25", "100.00",
        )
    ] + [q * Decimal("0.4"), q * Decimal("-0.4"), q / 2, -q / 2, q, -q]

    moved = 0
    for v in probes:
        old = _old_net_decimal_to_atoms(v, precision)
        new = net_decimal_to_atoms(v, precision=precision)
        if old == new:
            continue
        moved += 1
        assert old == 0 and abs(new) == 1, (
            f"precision {precision}: the atoms for {format(v, 'f')} moved from {old} to "
            f"{new}.  The only licensed movement is a non-zero net that used to become zero "
            f"atoms becoming one atom in its own direction."
        )
        assert v != 0 and abs(v) < q / 2, (
            f"precision {precision}: the atoms for {format(v, 'f')} moved, but that value is "
            f"not strictly inside half a quantum of zero, so the old result was not an "
            f"erasure and there was nothing here to fix."
        )
        assert (new > 0) == (v > 0), (
            f"precision {precision}: {format(v, 'f')} became {new} atoms - the wrong direction"
        )
        # And the string half must not have moved with it.
        assert Decimal(to_money_str(v, precision)) == v, (
            f"precision {precision}: `to_money_str` no longer round-trips "
            f"{format(v, 'f')}; the subject of this change is the atoms, and the string is "
            f"not licensed to move with them"
        )

    assert moved or precision >= 8, (
        f"precision {precision}: no probe in the population exercised the erasure this change "
        f"exists to stop, so a green result here would mean nothing"
    )
