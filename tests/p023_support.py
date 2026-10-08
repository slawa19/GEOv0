"""Programme 023: shared helpers of the planner's reproducers and correctness tests. NOT a test module.

* `target_xfail_023` - the expected-failure marker in the 019/020 shape (`tests/p019_support.py`):
  `xfail(raises=TargetMismatch, strict=True)`. A broken stand raises `AssertionError`, which the marker does
  not accept; a tree that already meets the target XPASSes and `strict=True` turns that into a failure, so
  the slice that delivers the target must take the marker off.
* `oracle_max_volume` - the SMALL EXHAUSTIVE ORACLE of Verification plan §5: the maximum of `Σ_e T_e` over
  every integer circulation `0 <= T <= L` of a small graph, found by enumerating every integer vector with
  balance pruning. It shares no code and no idea with the planner (no potentials, no shortest paths, no
  cycles); it is slow by design and only for graphs of a handful of vertices and small capacities.
* `edge_volume_on_debts` - `V_edge` measured the way the reproducers must measure it: the sum of positive debt
  amounts of one equivalent in the database, before minus after.
* `occurrence_of`, `historical_v1_clearing` (programme 025 `T2508.1`) - an execution test builds the plan
  occurrence it executes from what it DECLARES, and a historical v1 clearing is made from a committed occurrence
  the way the pre-023(b) writer left it, so no test needs the execution mode without an occurrence.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Hashable, Sequence

import pytest

from tests.p019_support import TargetMismatch, require_target  # noqa: F401 - re-exported for 023 tests


def slice_b_surface():
    """The slice (b) surface (spec decisions 5-6), or `TargetMismatch` naming what the tree lacks.

    The v2 tests read the new executor, descriptor and criterion (b) rule through here, so on a tree
    without slice (b) they end on the target - "no declared-amount occurrence path, no CLEARING intent v2
    rule" (the baseline executor takes neither an amount nor a plan, `service.py:1766`; a v2 envelope is
    `b_version_unsupported`) - and not on an `ImportError` the strict marker would not accept.
    """

    from types import SimpleNamespace

    from app.core.clearing import service
    from app.core.ledger import reconciliation
    from app.db import journal_tables

    missing = [
        name
        for owner, name in (
            (service, "ClearingOccurrence"),
            (service, "ClearingOccurrenceRefused"),
            (service.ClearingService, "execute_occurrence"),
            (journal_tables, "CLEARING_INTENT_ENCODING_VERSION"),
        )
        if not hasattr(owner, name)
    ]
    rule = getattr(reconciliation, "_RULES", {}).get(("CLEARING", 2))
    if rule is None:
        missing.append("criterion (b) rule for (CLEARING, intent v2)")
    if missing:
        raise TargetMismatch(
            "slice (b) is not delivered: no declared-amount plan occurrence and no CLEARING intent v2 rule "
            f"on this tree ({', '.join(missing)} absent)"
        )
    return SimpleNamespace(
        ClearingOccurrence=service.ClearingOccurrence,
        ClearingOccurrenceRefused=service.ClearingOccurrenceRefused,
        version=journal_tables.CLEARING_INTENT_ENCODING_VERSION,
        rule=rule,
    )


def slice_c_surface():
    """The slice (c) surface (spec decisions 7, 9, 10), or `TargetMismatch` naming what the tree lacks.

    The (c) tests read the runner, its committed-progress contract, the periodic isolation rule and the
    renewable lease through here, so on a tree without slice (c) they end on the target - "no common runner,
    no committed-progress handoff, no renewable lease" - and not on an `ImportError` the strict marker would
    not accept.
    """

    import importlib
    from types import SimpleNamespace

    from app.utils import distributed_lock

    missing: list[str] = []
    try:
        runner = importlib.import_module("app.core.clearing.runner")
    except ModuleNotFoundError as exc:
        if exc.name != "app.core.clearing.runner":
            raise
        runner = None
        missing.append("app.core.clearing.runner")
    names = (
        "run_clearing_pass",
        "run_awaited_clearing",
        "run_periodic_clearing_pass",
        "check_periodic_isolation",
        "ClearingPassResult",
        "CommittedOccurrence",
        "ClearingPassCancelled",
        "ClearingPassError",
        "ClearingPeriodicRefused",
        "InterruptReason",
    )
    if runner is not None:
        missing.extend(name for name in names if not hasattr(runner, name))
    for name in ("RenewableLease", "renewable_lease"):
        if not hasattr(distributed_lock, name):
            missing.append(f"distributed_lock.{name}")
    if missing:
        raise TargetMismatch(
            "slice (c) is not delivered: no common clearing runner, no committed-progress handoff, no periodic "
            f"isolation rule and no renewable lease on this tree ({', '.join(missing)} absent)"
        )
    return SimpleNamespace(
        runner=runner,
        RenewableLease=distributed_lock.RenewableLease,
        renewable_lease=distributed_lock.renewable_lease,
        **{name: getattr(runner, name) for name in names},
    )


def target_xfail_023(slice_: str, what: str):
    """The 023 marker: an expected `TargetMismatch`, strict, naming the slice whose switch removes it."""

    return pytest.mark.xfail(
        raises=TargetMismatch,
        strict=True,
        reason=f"023 target (maximum total eligible debt reduction on a snapshot), delivered by slice {slice_}: {what}",
    )


# ----------------------------------------------------------------------------------------------- oracle


def oracle_max_volume(
    edges: Sequence[tuple[Hashable, Hashable, Hashable, int]],
) -> tuple[int, dict[Hashable, int]]:
    """Maximum `Σ T_e` over integer `0 <= T_e <= L_e` with `B·T = 0`, by exhaustive enumeration.

    `edges` are `(edge_id, u, v, L)`; `T_e` flows `u -> v`. Returns `(best, T)` with `T` one maximiser.
    Pruning is only feasibility pruning: once the last edge incident to a vertex is assigned, the vertex's
    balance must be zero. Nothing about optimality is assumed, so the answer is the true maximum.
    """

    edges = list(edges)
    for _, u, v, cap in edges:
        assert isinstance(cap, int) and cap >= 0 and u != v
    last_touch: dict[Hashable, int] = {}
    for k, (_, u, v, _cap) in enumerate(edges):
        last_touch[u] = k
        last_touch[v] = k
    closes_at: dict[int, list[Hashable]] = {}
    for vertex, k in last_touch.items():
        closes_at.setdefault(k, []).append(vertex)
    # Remaining capacity reachable after position k (for an upper bound that only prunes, never decides).
    suffix = [0] * (len(edges) + 1)
    for k in range(len(edges) - 1, -1, -1):
        suffix[k] = suffix[k + 1] + edges[k][3]

    balance: dict[Hashable, int] = {x: 0 for x in last_touch}
    chosen = [0] * len(edges)
    best = [-1, None]

    def walk(k: int, volume: int) -> None:
        if volume + suffix[k] <= best[0]:
            return
        if k == len(edges):
            best[0] = volume
            best[1] = list(chosen)
            return
        _, u, v, cap = edges[k]
        for t in range(cap, -1, -1):
            balance[u] -= t
            balance[v] += t
            if all(balance[x] == 0 for x in closes_at.get(k, ())):
                chosen[k] = t
                walk(k + 1, volume + t)
            balance[u] += t
            balance[v] -= t
        chosen[k] = 0

    walk(0, 0)
    assert best[1] is not None, "the zero circulation is always feasible"
    return best[0], {edges[k][0]: best[1][k] for k in range(len(edges))}


# ---------------------------------------------------------------------------------------------- volume


async def positive_debt_total(session, equivalent_code: str) -> Decimal:
    """Σ of positive debt amounts of one equivalent, read from the database."""

    from sqlalchemy import func, select

    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent

    total = (
        await session.execute(
            select(func.coalesce(func.sum(Debt.amount), 0))
            .join(Equivalent, Equivalent.id == Debt.equivalent_id)
            .where(Equivalent.code == equivalent_code, Debt.amount > 0)
        )
    ).scalar_one()
    return Decimal(total)


async def remaining_debts(session, equivalent_code: str) -> list[tuple[str, str, str, Decimal]]:
    """Every positive debt left in one equivalent: (debt id, debtor pid, creditor pid, amount), sorted."""

    from sqlalchemy import select

    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant

    creditor = Participant.__table__.alias("creditor")
    rows = (
        await session.execute(
            select(Debt.id, Participant.pid, creditor.c.pid, Debt.amount)
            .join(Participant, Participant.id == Debt.debtor_id)
            .join(creditor, creditor.c.id == Debt.creditor_id)
            .join(Equivalent, Equivalent.id == Debt.equivalent_id)
            .where(Equivalent.code == equivalent_code, Debt.amount > 0)
        )
    ).all()
    return sorted((str(i), d, c, Decimal(a)) for i, d, c, a in rows)


# ------------------------------------------------------------------------------------------- slice (d)


#: The fields of `ClearingAutoResponse` fixed by spec decision R3 (2026-09-28): every one present, nullable ones
#: as an explicit `null`.
AUTO_RESPONSE_FIELDS = frozenset(
    {
        "equivalent",
        "cleared_cycles",
        "status",
        "reason",
        "v_edge",
        "v_cyc",
        "remaining_cycles",
        "remaining_v_edge",
        "committed",
        "error",
    }
)


async def auto_clear_http(client, headers, code: str, query: str = ""):
    """`POST /api/v1/clearing/auto` - the production entry of the manual pass. No depth: slice (d) removed it."""

    return await client.post(f"/api/v1/clearing/auto?equivalent={code}{query}", headers=headers)


def require_auto_progress(body) -> list:
    """The `committed` list of an `/auto` answer, or `TargetMismatch` when the answer does not report progress."""

    missing = sorted(AUTO_RESPONSE_FIELDS - set(body)) if isinstance(body, dict) else sorted(AUTO_RESPONSE_FIELDS)
    require_target(not missing, f"/clearing/auto does not report committed progress: fields {missing} absent ({body!r})")
    return body["committed"]


async def fresh_read(session, fn, *args):
    """Run `fn(session_of_the_same_database, *args)` on a NEW session: a snapshot after the request's commits."""

    from tests.conftest import sessionmaker_of

    async with sessionmaker_of(session)() as fresh:
        try:
            return await fn(fresh, *args)
        finally:
            await fresh.rollback()


async def planned_cycles(session, equivalent_code: str, *, allowed_participant_pids=None) -> list[list[dict]]:
    """The cycles a clearing pass would plan on this session's snapshot, each a list of
    `{debt_id, debtor, creditor, amount}` in cycle order - the shape the retired `ClearingService.find_cycles` gave
    (035 A2), so a test that only needs "the clearable cycle of this stand" reads it the way it always did.

    What it is and what the detectors were not: the decomposition of the flow plan (`flow_planner.plan_for_equivalent`,
    in this process - a test graph is small), the same eligibility rule execution applies, no depth and no cap. It is
    not an enumeration of every cycle: two cycles sharing an edge may come back as one cycle, or as two with other
    amounts. `debtor`/`creditor` are pids; `amount` is the debt's amount on the snapshot at the equivalent's
    precision, as before. `allowed_participant_pids` is the planner's own perimeter (`None`: none).
    """

    from sqlalchemy import select

    from app.core.clearing.flow_planner import money_of, plan_for_equivalent
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.utils.money import to_money_str

    plan = await plan_for_equivalent(session, equivalent_code, allowed_participant_pids=allowed_participant_pids)
    precision = (
        await session.execute(select(Equivalent.precision).where(Equivalent.code == equivalent_code))
    ).scalar_one()
    vertices = {v for cycle in plan.cycles for e in cycle.edges for v in (e.debtor_id, e.creditor_id)}
    pid_of: dict = {}
    if vertices:
        rows = await session.execute(select(Participant.id, Participant.pid).where(Participant.id.in_(vertices)))
        pid_of = {row.id: row.pid for row in rows}
    return [
        [
            {
                "debt_id": str(e.debt_id),
                "debtor": pid_of[e.debtor_id],
                "creditor": pid_of[e.creditor_id],
                "amount": to_money_str(money_of(e.atoms), int(precision)),
            }
            for e in cycle.edges
        ]
        for cycle in plan.cycles
    ]


async def assert_named_cycles_are_in_the_snapshot(session, equivalent_code: str, cycles) -> None:
    """CONTROL of a stand: each named cycle really is a clearable alternative on the planner's snapshot - read from
    the OBSERVED snapshot edges, without asking the optimizer to choose it (035 A2a, decision D2, 2026-10-08).

    It replaces `found == {cycle, cycle}` over the retired detectors, which listed every alternative; the plan's
    decomposition does not (it holds the optimum only), so the control is built on the snapshot instead and made
    to say as much as the old one did about each cycle. `cycles` are sequences of `tests.p020_support.Edge` in
    cycle order. For each of them:

    1. at least three distinct debt ids, every one an edge of this equivalent's snapshot
       (`flow_planner.load_snapshot`: positive debt, live consenting line, active participants);
    2. the observed debtor and creditor of each edge are the fixture's;
    3. the observed debtors are distinct and each creditor is the next edge's debtor, the last closing on the first;
    4. the observed amounts are the stand's declared ones, and the smallest is positive and a whole number of the
       equivalent's steps.

    What it does not establish: that the named cycles are the only cycles of the stand, and that execution would
    admit them at this moment (a stopped or held equivalent, a concurrent writer).
    """

    from decimal import Decimal

    from sqlalchemy import select

    from app.core.clearing.flow_planner import atoms_of, load_snapshot
    from app.db.models.equivalent import Equivalent
    from tests.p020_support import participant_uuid

    observed = {edge.debt_id: edge for edge in await load_snapshot(session, equivalent_code)}
    # The step of THE EQUIVALENT WHOSE SNAPSHOT IS READ (review of `4b833af5`: an argument defaulting to 2 refused
    # a correct one-atom triangle under precision 8 and passed debts of 0.01 under precision 0).
    precision = (
        await session.execute(select(Equivalent.precision).where(Equivalent.code == equivalent_code))
    ).scalar_one()
    step_atoms = 10 ** (8 - int(precision))
    for cycle in cycles:
        cycle = list(cycle)
        ids = [edge.debt_id for edge in cycle]
        assert len(set(ids)) == len(ids) >= 3, f"control: a cycle needs three or more distinct debts, got {ids}"
        missing = [str(i) for i in ids if i not in observed]
        assert not missing, f"control: debts {missing} are not edges of the snapshot of {equivalent_code}"
        seen = [observed[i] for i in ids]
        for named, edge in zip(cycle, seen):
            assert (edge.debtor_id, edge.creditor_id) == (
                participant_uuid(named.debtor), participant_uuid(named.creditor)
            ), f"control: debt {named.debt_id} is {edge.debtor_id} -> {edge.creditor_id}, not {named.debtor} -> {named.creditor}"
        debtors = [edge.debtor_id for edge in seen]
        assert len(set(debtors)) == len(debtors), f"control: a debtor repeats in {[e.debtor for e in cycle]}"
        for edge, following in zip(seen, seen[1:] + seen[:1]):
            assert edge.creditor_id == following.debtor_id, (
                f"control: the cycle is not closed - debt {edge.debt_id} ends at {edge.creditor_id}, "
                f"debt {following.debt_id} starts at {following.debtor_id}"
            )
        amounts = [edge.atoms for edge in seen]
        declared = [atoms_of(Decimal(named.amount)) for named in cycle]
        assert amounts == declared, f"control: the snapshot holds {amounts} atoms, the stand declares {declared}"
        assert min(amounts) > 0 and min(amounts) % step_atoms == 0, (
            f"control: the smallest debt of the cycle ({min(amounts)} atoms) is not a positive multiple of the step"
        )


def slow_plan(delay_seconds: float, edges):
    """Planner-process entry for the cold-spawn acceptance (spec (d), P2-2): sleep, then the real planner.

    Module-level so the `spawn` worker can import it by name; the planner itself is the unchanged
    `flow_planner.plan_clearing`.
    """

    import time

    from app.core.clearing.flow_planner import plan_clearing

    time.sleep(delay_seconds)
    return plan_clearing(edges)


# --------------------------------------------------------------------------------------- 025 `T2508.1`


#: Atoms per unit of money: a descriptor's amount is a whole number of 1e-8.
ATOMS_PER_UNIT = 10**8

#: The plan identity of the occurrences an execution test builds by hand (programme 025 `T2508.1`). Any UUID
#: would do; a fixed one keeps a test's occurrence ids reproducible. A REPLAY is the same descriptor again; a
#: LATER execution of the same rows is another occurrence - `LATER_PLAN_ID`, or another ordinal of the plan.
TEST_PLAN_ID = uuid.UUID("0a025081-0000-4000-8000-000000000001")
LATER_PLAN_ID = uuid.UUID("0a025081-0000-4000-8000-000000000002")


def occurrence_of(debt_ids, *, equivalent_id, amount, plan_id: uuid.UUID, ordinal: int):
    """A `ClearingOccurrence` from what the test DECLARES - never from what the executor computes.

    `debt_ids` is the cycle in its order (debtor -> creditor of one edge is the debtor of the next): UUIDs, their
    strings, or the `{"debt_id": ...}` edges of a cycle list. `amount` is the declared `c` as a decimal string or
    `Decimal`; one that is not a whole number of atoms is a test bug and is refused here, not rounded. Every
    field is required: an execution test states its plan identity and ordinal as it states its amount.
    """

    from app.core.clearing.service import ClearingOccurrence

    ids = tuple(uuid.UUID(str(item["debt_id"] if isinstance(item, dict) else item)) for item in debt_ids)
    atoms = Decimal(str(amount)) * ATOMS_PER_UNIT
    if atoms != atoms.to_integral_value():
        raise ValueError(f"the declared amount {amount!r} is not a whole number of atoms")
    return ClearingOccurrence(
        plan_id=plan_id,
        equivalent_id=uuid.UUID(str(equivalent_id)),
        ordinal=ordinal,
        debt_ids=ids,
        amount_atoms=int(atoms),
    )


#: The v1 execution namespace: a v1 clearing's tx id is the uuid5 of its sorted debt-id set. COPIED from
#: `app/core/clearing/service.py` (`_CLEARING_REPLAY_NAMESPACE`, at `6e25aaa` line 35), which programme 024 `T2417`
#: removed with the execution without an occurrence; this copy is now the only one, so historical rows stay buildable.
_V1_CLEARING_NAMESPACE = uuid.UUID("7438b16f-c629-4aeb-8b97-4bf113704c93")


def v1_clearing_tx_id(debt_ids) -> str:
    """The tx id a v1 (set-hash) clearing of these debts carried: order-insensitive by construction."""

    ids = {str(item["debt_id"] if isinstance(item, dict) else item) for item in debt_ids}
    return str(uuid.uuid5(_V1_CLEARING_NAMESPACE, ":".join(sorted(ids))))


async def historical_v1_clearing(factory, occurrence) -> str:
    """Turn the COMMITTED clearing of `occurrence` into the record a v1 clearing left; return its v1 tx id.

    Programme 025 `T2508.1` (spec `T2500`, P2-4): the tests that READ historical v1 clearings - criterion (b)'s
    `("CLEARING", 1)` rule - keep that reading without keeping the production writer of v1 alive for a fixture.
    A v1 clearing cleared the cycle MINIMUM, so the occurrence must have declared it (refused otherwise); its
    money, journal entries and recorded pre-amounts are then what the v1 writer wrote (the two paths share one
    writer), and only the identity differs. This rewrites the identity the way the v1 writer spelled it: the
    envelope's `tx_id`, `identity` and intent (the same intent without the `occurrence` descriptor, re-digested,
    `intent_encoding_version = 1`), the transaction row's `tx_id`, `idempotency_key` and payload (no descriptor),
    and the audit row's `tx_id`.

    WHAT THIS DOES NOT REBUILD (the record is the occurrence's, relabelled - not a v1 execution replayed):
    `transactions.id` stays the occurrence uuid while `transactions.tx_id` becomes the v1 set-hash (v1 wrote
    `id = uuid(tx_id)`, `app/core/clearing/service.py` ~:2214-2262); the intent's `cycle`, the journal entries'
    order and `initiator_id` follow the DECLARED occurrence order, whereas v1 followed the order its
    `SELECT ... FOR UPDATE` returned the rows (~:2067-2079 only reorders in the occurrence branch). A reader that
    depended on any of these would see a record no v1 writer produced; criterion (b) reads none of them.

    It writes with the journal's triggers off through THE named corruption helper (`tests/ledger_corruption.py`),
    so it runs only on a disposable clone (`tier_on_a_clone` / `tier_sessions_on_a_clone`) and needs that
    helper's privilege; anything else is the helper's refusal, not a skip.
    """

    import hashlib
    import json

    from sqlalchemy import select

    from app.core.auth.canonical import canonical_json
    from app.db.journal_tables import debt_operations
    from app.db.models.transaction import Transaction
    from tests.ledger_corruption import corrupt

    old = occurrence.occurrence_id
    new = v1_clearing_tx_id(occurrence.debt_ids)
    ops = debt_operations.c
    async with factory() as session:
        envelope = (
            await session.execute(
                select(ops.id, ops.intent, ops.state).where(ops.kind == "CLEARING", ops.tx_id == old)
            )
        ).one()
        transaction = (
            await session.execute(select(Transaction.state, Transaction.payload).where(Transaction.tx_id == old))
        ).one()
        await session.rollback()
    assert (envelope.state, transaction.state) == ("COMPLETED", "COMMITTED"), (
        f"stand: occurrence {old} is not a committed clearing ({envelope.state}, {transaction.state})"
    )
    intent = json.loads(envelope.intent) if isinstance(envelope.intent, (str, bytes)) else dict(envelope.intent)
    assert intent.pop("occurrence") == occurrence.descriptor(), "stand: the envelope is not this occurrence's"
    minimum = min(Decimal(edge["amount"]) for edge in intent["cycle"])
    assert Decimal(intent["clear_amount"]) == minimum, (
        f"stand: a v1 clearing cleared the cycle minimum {minimum}; the occurrence declared {intent['clear_amount']}"
    )
    intent["tx_id"] = new
    canonical = canonical_json(intent)
    payload = {key: value for key, value in dict(transaction.payload).items() if key != "occurrence"}
    body, payload_body = canonical.decode("utf-8"), json.dumps(payload, sort_keys=True)
    assert "'" not in body + payload_body, "stand: the literals below do not escape quotes"
    await corrupt(
        factory.kw["bind"].url.render_as_string(hide_password=False),
        [
            f"UPDATE transactions SET tx_id = '{new}', idempotency_key = 'clearing:{new}', "
            f"payload = '{payload_body}' WHERE tx_id = '{old}'",
            f"UPDATE debt_operations SET tx_id = '{new}', identity = '{new}', intent = '{body}', "
            f"intent_digest = '{hashlib.sha256(canonical).hexdigest()}', intent_encoding_version = 1 "
            f"WHERE id = '{envelope.id}'",
            f"UPDATE integrity_audit_log SET tx_id = '{new}' WHERE tx_id = '{old}'",
        ],
    )
    return new
