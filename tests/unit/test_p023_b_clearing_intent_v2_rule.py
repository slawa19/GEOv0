"""Programme 023, slice (b): the criterion (b) rule for CLEARING intent v2, and v1 left as it was.

Spec `specs/023-clearing-as-flow/spec.md`, decision 5 and Verification plan §2 ("Сверка v2"): a v2 occurrence
clears a DECLARED amount `c <= min` on one simple cycle, so the v1 rule `clear == min(pre)` cannot read it; v2
is a NEW entry of the `(kind, intent version)` dispatcher (`reconciliation.py` `_READABLE_ENVELOPES`,
`_RULES`, `_criterion_b`), and v1 keeps its rule for historical intents. The six v2 requirements, each with a
case that must produce a finding:

1. a positive integer amount in atoms, fixed in the occurrence descriptor (and the envelope's `tx_id` IS the
   occurrence id of that descriptor);
2. one equivalent, one connected simple directed cycle, unique debt ids, valid endpoints;
3. `p_e > 0` and `c <= p_e` on every edge;
4. descriptor amount == intent amount == `Transaction.payload` amount (and the payload carries the same
   descriptor);
5. exactly the declared change: `Δ_e = -c`, no extra edge, delete exactly at zero;
6. the recorded pre-state against the journal's first `amount_before`.

PURE: fake envelope rows and journal entries, no database - the rule is a function of the envelope, its
entries and the transaction payload. The expected deltas come from the frozen descriptor, never from the
entries under test. The end-to-end half (a real v2 execution recomputed PASSED, a coordinated corruption
FAILED) is `tests/integration/test_p023_b_occurrence_execution_postgres.py`.

RED ON A TREE WITHOUT SLICE (b): `slice_b_surface()` ends every v2 test on `TargetMismatch` (no v2 rule, no
descriptor); the v1 tests are green there and must stay green.

MUTATIONS (recorded in the spec Changelog): accept `c > p_e` in the v2 rule -
`test_v2_amount_above_an_edge_is_a_finding` red; drop the effect check - the two delete tests red; derive
the expected delta from the intent's `clear_amount` instead of the descriptor - the intent-amount test red.
"""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest

from app.core.ledger import reconciliation as rec
from tests.p023_support import slice_b_surface, target_xfail_023

EQ = uuid.UUID("00000000-0000-4000-8000-00000000e023")
PLAN = uuid.UUID("00000000-0000-4000-8000-0000000b1a17")
ATOM = 10**8


def _pid(k: int) -> uuid.UUID:
    return uuid.UUID(int=0x2302_0000 + k)


def _debt(k: int) -> uuid.UUID:
    return uuid.UUID(int=0x2302_DEB7_0000 + k)


def _text(atoms: int) -> str:
    return rec._money_text(atoms)


class _Stand:
    """One honest v2 occurrence on a ring `P0 -> P1 -> ... -> P0` with pre-amounts `pre` and amount `c`."""

    def __init__(self, pre: list[int], c: int, *, ordinal: int = 0) -> None:
        api = slice_b_surface()
        self.api = api
        n = len(pre)
        self.edges = [(_pid(k), _pid((k + 1) % n)) for k in range(n)]
        self.debt_ids = [_debt(k) for k in range(n)]
        self.pre = list(pre)
        self.c = c
        occurrence = api.ClearingOccurrence(
            plan_id=PLAN, equivalent_id=EQ, ordinal=ordinal, debt_ids=tuple(self.debt_ids), amount_atoms=c
        )
        self.occurrence = occurrence
        descriptor = occurrence.descriptor()
        self.intent = {
            "tx_id": occurrence.occurrence_id,
            "occurrence": copy.deepcopy(descriptor),
            "clear_amount": _text(c),
            "equivalent_id": str(EQ),
            "cycle": [
                {"debt_id": str(d), "amount": _text(p), "debtor_id": str(u), "creditor_id": str(v)}
                for d, p, (u, v) in zip(self.debt_ids, pre, self.edges, strict=True)
            ],
        }
        self.payload = {"amount": _text(c), "occurrence": copy.deepcopy(descriptor)}
        self.entries = [
            rec._Entry(ordinal=k, debtor_id=u, creditor_id=v, effect="D" if p == c else "U", amount_before=p, delta=-c)
            for k, (p, (u, v)) in enumerate(zip(pre, self.edges, strict=True))
        ]
        self.tx_id = occurrence.occurrence_id
        self.version = api.version

    def op(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.UUID(int=0x0B),
            kind="CLEARING",
            tx_id=self.tx_id,
            intent=self.intent,
            intent_encoding_version=self.version,
            tx_payload=self.payload,
        )

    def findings(self) -> list[dict]:
        op = self.op()
        found, coverage = rec._criterion_b(EQ, [op], {op.id: self.entries})
        assert coverage == (("full_recomputation", "CLEARING", 1),), coverage
        return found


def _reasons(findings) -> set[str]:
    return {f.get("reason") for f in findings if f["kind"] == "b_intent_malformed"}


def _kinds(findings) -> set[str]:
    return {f["kind"] for f in findings}


# ------------------------------------------------------------------------------------------ the honest case


@target_xfail_023("(b)", "no CLEARING intent v2 rule: an honest partial occurrence is not recomputed")
def test_v2_an_honest_partial_occurrence_has_no_finding() -> None:
    stand = _Stand([5 * ATOM, 7 * ATOM, 9 * ATOM], 2 * ATOM)
    assert stand.findings() == []
    # Control: the rule is registered for version 2 at the full level, and v1 is still its own rule.
    assert rec._READABLE_ENVELOPES[("CLEARING", stand.version)] == rec.FULL_RECOMPUTATION
    assert stand.api.rule is not rec._RULES[("CLEARING", 1)]


@target_xfail_023("(b)", "no CLEARING intent v2 rule: an exact occurrence that deletes one edge")
def test_v2_an_occurrence_that_exhausts_one_edge_deletes_it_exactly_at_zero() -> None:
    stand = _Stand([2 * ATOM, 7 * ATOM, 9 * ATOM], 2 * ATOM)
    assert [e.effect for e in stand.entries] == ["D", "U", "U"]
    assert stand.findings() == []


# ------------------------------------------------------------------------- (1) the amount and the identity


@target_xfail_023("(b)", "no CLEARING intent v2 rule: the descriptor amount must be positive whole atoms")
@pytest.mark.parametrize("amount", ["0", "-5", "1.5", "abc", "", 7])
def test_v2_a_descriptor_amount_that_is_not_positive_atoms_is_malformed(amount) -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.intent["occurrence"]["amount_atoms"] = amount
    assert "clearing_v2_amount_is_not_positive_atoms" in _reasons(stand.findings())


@target_xfail_023("(b)", "no CLEARING intent v2 rule: the envelope tx_id must be the descriptor's occurrence id")
def test_v2_a_tx_id_that_is_not_the_occurrence_of_its_descriptor_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    # The descriptor says ordinal 1, the envelope (and the intent's tx_id) are ordinal 0's.
    stand.intent["occurrence"]["ordinal"] = 1
    stand.payload["occurrence"]["ordinal"] = 1
    assert "clearing_v2_tx_id_is_not_the_occurrence_of_the_descriptor" in _reasons(stand.findings())


# --------------------------------------------------------------------------------- (2) one simple cycle


@target_xfail_023("(b)", "no CLEARING intent v2 rule: a cycle that is not closed")
def test_v2_a_cycle_that_is_not_closed_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.intent["cycle"][2]["creditor_id"] = str(_pid(1))  # P2 -> P1 instead of P2 -> P0
    assert "clearing_v2_not_one_simple_directed_cycle" in _reasons(stand.findings())


@target_xfail_023("(b)", "no CLEARING intent v2 rule: a closed walk through one vertex twice is not simple")
def test_v2_a_closed_walk_through_a_vertex_twice_is_a_finding() -> None:
    # Two triangles sharing P0: P0->P1->P2->P0->P3->P4->P0. Closed and balanced, but not ONE simple cycle.
    stand = _Stand([5 * ATOM] * 6, 2 * ATOM)
    walk = [0, 1, 2, 0, 3, 4]
    for k, item in enumerate(stand.intent["cycle"]):
        item["debtor_id"] = str(_pid(walk[k]))
        item["creditor_id"] = str(_pid(walk[(k + 1) % 6]))
    stand.entries = [
        rec._Entry(k, _pid(walk[k]), _pid(walk[(k + 1) % 6]), "U", 5 * ATOM, -2 * ATOM) for k in range(6)
    ]
    assert "clearing_v2_not_one_simple_directed_cycle" in _reasons(stand.findings())


@target_xfail_023("(b)", "no CLEARING intent v2 rule: the recorded cycle must be the descriptor's debts")
def test_v2_a_cycle_whose_debts_differ_from_the_descriptor_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.intent["cycle"][1]["debt_id"] = str(_debt(99))
    assert "clearing_v2_cycle_differs_from_the_descriptor" in _reasons(stand.findings())


@target_xfail_023("(b)", "no CLEARING intent v2 rule: repeated debt ids in the descriptor")
def test_v2_repeated_debt_ids_are_malformed() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    ids = stand.intent["occurrence"]["debt_ids"]
    ids[2] = ids[0]
    assert "clearing_v2_debt_ids_repeat" in _reasons(stand.findings())


@target_xfail_023("(b)", "no CLEARING intent v2 rule: one equivalent")
def test_v2_an_equivalent_that_differs_from_the_descriptor_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.intent["equivalent_id"] = str(uuid.UUID(int=0xE0E0))
    assert "clearing_v2_equivalent_differs_from_the_descriptor" in _reasons(stand.findings())


# ------------------------------------------------------------------------------------- (3) c <= p_e


@target_xfail_023("(b)", "no CLEARING intent v2 rule: over-clearing c > p_e")
def test_v2_amount_above_an_edge_is_a_finding() -> None:
    """Over-clearing: `c` = 6 on an edge that held 5. Recorded faithfully (the entry says -6), so only (3) can
    say it. MUTATION: accept `c > p_e` in the rule - red."""
    stand = _Stand([5 * ATOM, 9 * ATOM, 9 * ATOM], 6 * ATOM)
    stand.entries[0] = rec._Entry(0, *stand.edges[0], "U", 5 * ATOM, -6 * ATOM)
    assert "clearing_v2_amount_exceeds_an_edge" in _reasons(stand.findings())


# ------------------------------------------------------------------------------ (4) the three amounts


@target_xfail_023("(b)", "no CLEARING intent v2 rule: intent amount vs descriptor amount")
def test_v2_an_intent_amount_that_differs_from_the_descriptor_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.intent["clear_amount"] = _text(3 * ATOM)
    found = stand.findings()
    assert "clearing_v2_intent_amount_differs_from_the_descriptor" in _reasons(found)
    # The expected change is the DESCRIPTOR's (-2 per edge), which the journal matches: no delta finding.
    assert "b_delta_mismatch" not in _kinds(found), found


@target_xfail_023("(b)", "no CLEARING intent v2 rule: payload amount vs descriptor amount")
def test_v2_a_payload_amount_that_differs_from_the_descriptor_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.payload["amount"] = "2.01"
    assert "clearing_v2_payload_amount_differs_from_the_descriptor" in _reasons(stand.findings())


@target_xfail_023("(b)", "no CLEARING intent v2 rule: payload descriptor vs intent descriptor")
@pytest.mark.parametrize("payload", ["other_descriptor", "no_descriptor", "no_payload"])
def test_v2_a_payload_without_the_same_descriptor_is_a_finding(payload) -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    if payload == "other_descriptor":
        stand.payload["occurrence"]["plan_id"] = str(uuid.UUID(int=0xB0B))
    elif payload == "no_descriptor":
        del stand.payload["occurrence"]
    else:
        stand.payload = None
    assert "clearing_v2_payload_descriptor_differs" in _reasons(stand.findings())


# ----------------------------------------------------------------------------- (5) the declared change


@target_xfail_023("(b)", "no CLEARING intent v2 rule: an extra affected edge")
def test_v2_an_extra_affected_edge_is_a_delta_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.entries.append(rec._Entry(9, _pid(7), _pid(8), "U", 4 * ATOM, -1 * ATOM))
    mismatches = [f for f in stand.findings() if f["kind"] == "b_delta_mismatch"]
    assert [(f["debtor_id"], f["creditor_id"], f["expected_delta"]) for f in mismatches] == [
        (str(_pid(7)), str(_pid(8)), "0.00000000")
    ], mismatches


@target_xfail_023("(b)", "no CLEARING intent v2 rule: a delta other than -c")
def test_v2_a_delta_other_than_minus_c_is_a_delta_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.entries[1] = rec._Entry(1, *stand.edges[1], "U", 5 * ATOM, -2 * ATOM + 1)
    mismatches = [f for f in stand.findings() if f["kind"] == "b_delta_mismatch"]
    assert [(f["expected_delta"], f["recorded_delta"]) for f in mismatches] == [("-2.00000000", "-1.99999999")]


@target_xfail_023("(b)", "no CLEARING intent v2 rule: a delete above zero")
def test_v2_a_delete_above_zero_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    # Coordinated: a `D` of the whole 5 is -5, not -2; the effect rule names it, the delta rule too.
    stand.entries[0] = rec._Entry(0, *stand.edges[0], "D", 5 * ATOM, -5 * ATOM)
    found = stand.findings()
    assert ("b_clearing_v2_effect", "deleted_above_zero") in {(f["kind"], f.get("rule")) for f in found}, found


@target_xfail_023("(b)", "no CLEARING intent v2 rule: zero left standing instead of deleted")
def test_v2_an_edge_left_at_zero_is_a_finding() -> None:
    stand = _Stand([2 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.entries[0] = rec._Entry(0, *stand.edges[0], "U", 2 * ATOM, -2 * ATOM)
    found = stand.findings()
    assert ("b_clearing_v2_effect", "not_deleted_at_zero") in {(f["kind"], f.get("rule")) for f in found}, found
    assert "b_delta_mismatch" not in _kinds(found), "the delta is right; only the effect is wrong"


# ------------------------------------------------------------------------------------ (6) the pre-state


@target_xfail_023("(b)", "no CLEARING intent v2 rule: recorded pre-state vs journal")
def test_v2_a_prestate_the_journal_does_not_start_from_is_a_finding() -> None:
    stand = _Stand([5 * ATOM, 5 * ATOM, 5 * ATOM], 2 * ATOM)
    stand.intent["cycle"][2]["amount"] = _text(6 * ATOM)
    assert "b_prestate_mismatch" in _kinds(stand.findings())


# ------------------------------------------------------------------- writer and verifier agree on the id


@target_xfail_023("(b)", "no plan occurrence identity")
def test_v2_the_occurrence_id_is_plan_equivalent_and_ordinal_and_nothing_else() -> None:
    api = slice_b_surface()
    ids = tuple(_debt(k) for k in range(3))
    base = api.ClearingOccurrence(plan_id=PLAN, equivalent_id=EQ, ordinal=0, debt_ids=ids, amount_atoms=2 * ATOM)
    other_plan = api.ClearingOccurrence(
        plan_id=uuid.UUID(int=0xB0B), equivalent_id=EQ, ordinal=0, debt_ids=ids, amount_atoms=2 * ATOM
    )
    other_ordinal = api.ClearingOccurrence(plan_id=PLAN, equivalent_id=EQ, ordinal=1, debt_ids=ids, amount_atoms=2 * ATOM)
    same_slot = api.ClearingOccurrence(plan_id=PLAN, equivalent_id=EQ, ordinal=0, debt_ids=ids, amount_atoms=3 * ATOM)
    # Two partial occurrences of ONE debt set in two plans: two identities (the R-023-4a defect, fixed for v2).
    assert base.occurrence_id != other_plan.occurrence_id
    assert base.occurrence_id != other_ordinal.occurrence_id
    # The slot is the identity; the rest of the descriptor is what a replay checks against.
    assert base.occurrence_id == same_slot.occurrence_id and base.descriptor() != same_slot.descriptor()
    # v1's set-hash is not v2's namespace.
    from app.core.clearing.service import ClearingService

    assert base.occurrence_id != ClearingService._execution_tx_id(list(ids))
    # The verifier derives the same id from the descriptor with its own copy of the rule.
    stand = _Stand([5 * ATOM] * 3, 2 * ATOM)
    assert stand.findings() == []


@target_xfail_023("(b)", "no plan occurrence descriptor")
@pytest.mark.parametrize(
    "bad",
    [
        {"ordinal": -1},
        {"ordinal": True},
        {"amount_atoms": 0},
        {"amount_atoms": 2.0},
        {"debt_ids": (_debt(0), _debt(1))},
        {"debt_ids": (_debt(0), _debt(1), _debt(0))},
        {"plan_id": "not-a-uuid"},
    ],
)
def test_v2_a_descriptor_is_validated_before_any_work(bad) -> None:
    api = slice_b_surface()
    fields = dict(plan_id=PLAN, equivalent_id=EQ, ordinal=0, debt_ids=tuple(_debt(k) for k in range(3)), amount_atoms=1)
    fields.update(bad)
    with pytest.raises(ValueError):
        api.ClearingOccurrence(**fields)


# ------------------------------------------------------------------------------ v1 is left exactly as it was


def _v1_op(clear: int, pre: list[int]) -> tuple[SimpleNamespace, list]:
    n = len(pre)
    edges = [(_pid(k), _pid((k + 1) % n)) for k in range(n)]
    intent = {
        "tx_id": "v1-tx",
        "clear_amount": _text(clear),
        "equivalent_id": str(EQ),
        "cycle": [
            {"debt_id": str(_debt(k)), "amount": _text(p), "debtor_id": str(u), "creditor_id": str(v)}
            for k, (p, (u, v)) in enumerate(zip(pre, edges, strict=True))
        ],
    }
    minimum = min(pre)
    entries = [
        rec._Entry(k, u, v, "D" if p == minimum else "U", p, -minimum) for k, (p, (u, v)) in enumerate(zip(pre, edges))
    ]
    op = SimpleNamespace(
        id=uuid.UUID(int=0x01), kind="CLEARING", tx_id="v1-tx", intent=intent, intent_encoding_version=1, tx_payload=None
    )
    return op, entries


def test_v1_a_historical_clearing_is_still_read_by_the_v1_rule() -> None:
    op, entries = _v1_op(5 * ATOM, [5 * ATOM, 7 * ATOM, 9 * ATOM])
    found, coverage = rec._criterion_b(EQ, [op], {op.id: entries})
    assert found == [] and coverage == (("full_recomputation", "CLEARING", 1),)
    assert rec._RULES[("CLEARING", 1)] is rec._clearing


def test_v1_still_refuses_a_partial_amount_for_a_v1_intent() -> None:
    """v1 keeps `clear == min(pre)`: a partial amount in a v1 envelope is a finding, not a v2 occurrence."""
    op, entries = _v1_op(2 * ATOM, [5 * ATOM, 7 * ATOM, 9 * ATOM])
    found, _ = rec._criterion_b(EQ, [op], {op.id: entries})
    assert "clear_amount_is_not_the_cycle_minimum" in _reasons(found), found
