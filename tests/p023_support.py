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
