"""One hop of a payment - capacity and policy: ONE owner for the router, the core and `/balance`.

Protocol §6.3.1; owner decision 2026-09-29 (024, П1, revised to the original GEO after the §15 review).
A hop payer -> payee exists only while the pair has an ACTIVE line in either direction; its capacity is
`limit(payee -> payer) - debt[payer -> payee] + debt[payee -> payer]` (a non-active payee line is 0).
Its policy is the conjunction of every active line of the pair: `can_be_intermediate = false` or
`max_hop_usage = 0` forbids the line's OWNER to mediate over this pair, entering or leaving; every
line's `blocked_participants` applies to the whole route. Clearing is a different operation.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Optional


def pair_capacity(
    *, line_limit: Decimal | None, payer_owes: Decimal, payee_owes: Decimal, pair_has_active_line: bool
) -> Decimal:
    """`line_limit` is the payee's active line to the payer (None when there is none)."""

    if not pair_has_active_line:
        return Decimal("0")
    return (line_limit if line_limit is not None else Decimal("0")) - payer_owes + payee_owes


def pair_rules(lines: Iterable[tuple[str, dict | None]]) -> tuple[frozenset[str], frozenset[str]]:
    """(owners forbidding mediation, blocked pids) of one pair's active lines `(owner_pid, policy)`."""

    forbid: set[str] = set()
    blocked: set[str] = set()
    for owner_pid, policy in lines:
        policy = policy if isinstance(policy, dict) else {}
        forbids = not bool(policy.get("can_be_intermediate", True))
        try:  # owner decision B (024 T2415.3): forbids EXACTLY at zero - "0.0", "0e0", 0 yes, 0.5 no
            forbids = forbids or Decimal(str(policy.get("max_hop_usage", 1))) == 0
        except (InvalidOperation, ValueError, TypeError):  # unparsable (None, "abc", NaN signal): permits, as before
            pass
        if forbids:
            forbid.add(owner_pid)
        listed = policy.get("blocked_participants")
        blocked.update(x for x in (listed if isinstance(listed, list) else []) if isinstance(x, str) and x)
    return frozenset(forbid), frozenset(blocked)


def route_breaks_policy(
    path: list[str], rules: Callable[[str, str], tuple[frozenset, frozenset]], *, payee: Optional[str] = None
) -> Optional[str]:
    """Why `path` (possibly a prefix ending before `payee`) breaks a policy, or None."""

    payee = path[-1] if payee is None else payee
    inner = {n for n in path[1:] if n != payee}
    blocked: set[str] = set()
    for u, v in zip(path, path[1:]):
        forbid, b = rules(u, v)
        if forbid & inner & {u, v}:
            return f"{sorted(forbid & inner & {u, v})} may not mediate over {u}->{v}"
        blocked |= b
    return f"{sorted(blocked & inner)} blocked by a line of the route" if blocked & inner else None
