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
"""

from __future__ import annotations

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


def slow_plan(delay_seconds: float, edges):
    """Planner-process entry for the cold-spawn acceptance (spec (d), P2-2): sleep, then the real planner.

    Module-level so the `spawn` worker can import it by name; the planner itself is the unchanged
    `flow_planner.plan_clearing`.
    """

    import time

    from app.core.clearing.flow_planner import plan_clearing

    time.sleep(delay_seconds)
    return plan_clearing(edges)
