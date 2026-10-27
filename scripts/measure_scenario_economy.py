"""Deterministic, offline planned-flow and static-policy measurements.

CLI: explicitly set ENV=test (compatible ENVIRONMENT alias), DEBUG=false, DATABASE_URL to an explicit task PostgreSQL URL and SIMULATOR_STATE_DIR
under .local-run/test-runs/<task>/ before running. No database connection is made
by this script; importing the real planner constructs the application's runtime
and engine. The state override protects the developer runtime from import cleanup.
JSON goes to stdout (or --output); a concise human table goes to stderr.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _dependencies():
    # Do not import simulator until its package singleton has an isolated home.
    from sqlalchemy.engine import make_url

    try:
        url = make_url(os.environ.get("DATABASE_URL", ""))
        valid = bool(url.database)
    except Exception:
        valid = False
    if not valid:
        raise ValueError(
            "Set DATABASE_URL explicitly to a task postgresql+asyncpg URL; offline imports construct an engine but do not connect."
        )
    state = os.environ.get("SIMULATOR_STATE_DIR", "").strip()
    state_path = (ROOT / state).resolve() if state else None
    parts = tuple(part.lower() for part in state_path.parts) if state_path else ()
    if not any(
        parts[i : i + 2] == (".local-run", "test-runs") and len(parts) > i + 2
        for i in range(len(parts))
    ):
        raise ValueError(
            "Set SIMULATOR_STATE_DIR under .local-run/test-runs/<task>/ before importing the planner; default runtime state is unsafe."
        )
    if (
        os.environ.get("ENV", os.environ.get("ENVIRONMENT", "")).strip().lower()
        != "test"
    ):
        raise ValueError(
            "Set ENV=test explicitly for offline measurement (and remove any conflicting ENVIRONMENT alias)."
        )
    from app.core.payments.capacity import pair_rules, route_breaks_policy

    # Settings owns the supported-driver refusal; do not duplicate its rule here.
    from app.core.simulator.real_payment_planner import RealPaymentPlanner
    from app.core.simulator.scenario_equivalent import effective_equivalent

    return RealPaymentPlanner, pair_rules, route_breaks_policy, effective_equivalent


def _ratio(numerator, denominator):
    return (
        format(Decimal(numerator) / Decimal(denominator), "f") if denominator else None
    )


def _stat(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "share": _ratio(numerator, denominator),
    }


class StaticPolicyGraph:
    """Zero-debt positive-limit edges, debtor -> creditor; no dynamic capacity claim."""

    def __init__(self, scenario, *, max_hops=6):
        if not isinstance(max_hops, int) or not 1 <= max_hops <= 64:
            raise ValueError("max_hops must be an integer in 1..64")
        _, pair_rules, self.route_breaks_policy, equivalent = _dependencies()
        self.max_hops = max_hops
        self.participants = {
            str(p.get("id") or p.get("participant_id")): p
            for p in scenario.get("participants", [])
            if p.get("id") or p.get("participant_id")
        }
        self.active = {
            pid
            for pid, p in self.participants.items()
            if str(p.get("status") or "active").strip().lower() == "active"
        }
        self.equivalents = {
            str(eq).strip().upper()
            for eq in scenario.get("equivalents", [])
            if str(eq).strip()
        }
        self.edges = defaultdict(lambda: defaultdict(set))
        lines = defaultdict(dict)
        self.exclusions = defaultdict(int)
        for line in scenario.get("trustlines", []):
            eq = equivalent(scenario, line)
            if eq:
                self.equivalents.add(eq)
            a, b = str(line.get("from") or ""), str(line.get("to") or "")
            if str(line.get("status") or "active").strip().lower() != "active":
                self.exclusions["nonactive_lines"] += 1
                continue
            if a == b:
                self.exclusions["self_lines"] += 1
                continue
            if a not in self.active or b not in self.active:
                self.exclusions["unknown_or_nonactive_endpoint_lines"] += 1
                continue
            if not eq:
                self.exclusions["missing_equivalent_lines"] += 1
                continue
            if (a, b) in lines[eq]:
                raise ValueError(
                    "Duplicate active trustline for an equivalent and ordered pair"
                )
            limit = Decimal(str(line.get("limit")))
            if not limit.is_finite() or limit < 0:
                raise ValueError("Trustline limits must be finite and nonnegative")
            lines[eq][a, b] = line
            if limit > 0:
                self.edges[eq][b].add(a)
            else:
                self.exclusions["zero_limit_edges"] += 1
        self.rules = {}
        for eq, by_pair in lines.items():
            for a, b in by_pair:
                pair = [by_pair[x, y] for x, y in ((a, b), (b, a)) if (x, y) in by_pair]
                self.rules[eq, b, a] = self.rules[eq, a, b] = pair_rules(
                    (str(tl["from"]), tl.get("policy")) for tl in pair
                )
        self._reachable = {}

    def _rules(self, eq):
        return lambda a, b: self.rules[eq, a, b]

    def reachable(self, eq, sender, receiver):
        key = eq, sender, receiver
        if key in self._reachable:
            return self._reachable[key]
        if (
            sender == receiver
            or sender not in self.active
            or receiver not in self.active
        ):
            return False
        stack = [[sender]]
        while stack:
            path = stack.pop()
            if len(path) - 1 >= self.max_hops:
                continue
            for neighbor in sorted(self.edges[eq].get(path[-1], ()), reverse=True):
                if neighbor in path:
                    continue
                extension = path + [neighbor]
                if self.route_breaks_policy(extension, self._rules(eq), payee=receiver):
                    continue
                if neighbor == receiver:
                    self._reachable[key] = True
                    return True
                stack.append(extension)
        self._reachable[key] = False
        return False

    def cycles(self, eq, length, *, transit_circuit=False):
        # Rotation is counted once; reverse orientation is a different directed cycle.
        found = []
        for start in sorted(self.active):
            stack = [[start]]
            while stack:
                path = stack.pop()
                if len(path) == length:
                    if start not in self.edges[eq].get(path[-1], ()):
                        continue
                    # Primary economic rings consist of individual direct payments.
                    # A separate transit diagnostic requires every vertex to mediate.
                    if all(
                        self.route_breaks_policy(
                            (
                                path[i:] + path[:i] + [path[i]]
                                if transit_circuit
                                else [path[i], path[(i + 1) % length]]
                            ),
                            self._rules(eq),
                        )
                        is None
                        for i in range(length)
                    ):
                        found.append(path)
                    continue
                for neighbor in sorted(self.edges[eq].get(path[-1], ()), reverse=True):
                    if neighbor > start and neighbor not in path:
                        stack.append(path + [neighbor])
        return found


def _balance(incoming, outgoing):
    turnover = incoming + outgoing
    return {
        "in": str(incoming),
        "out": str(outgoing),
        "signed_in_minus_out": str(incoming - outgoing),
        "turnover_in_plus_out": str(turnover),
        "relative_signed": _ratio(incoming - outgoing, turnover),
        "relative_absolute": _ratio(abs(incoming - outgoing), turnover),
    }


def measure(
    scenario,
    *,
    ticks=2000,
    intensity=5,
    seed=42,
    actions_per_tick=20,
    amount_cap=None,
    precision_by_eq=None,
    default_precision=2,
    max_hops=6,
):
    """Measure unchanged real plans, with debt_snapshot=None at every tick."""
    if not isinstance(ticks, int) or ticks < 1:
        raise ValueError("ticks must be a positive integer")
    if not isinstance(intensity, int) or not 0 <= intensity <= 100:
        raise ValueError("intensity must be an integer in 0..100")
    if not isinstance(actions_per_tick, int) or actions_per_tick < 1:
        raise ValueError("actions_per_tick must be positive")
    precision_by_eq = {str(eq).upper(): p for eq, p in (precision_by_eq or {}).items()}
    if any(
        not isinstance(p, int) or not 0 <= p <= 8
        for p in [default_precision, *precision_by_eq.values()]
    ):
        raise ValueError("equivalent precision must be an integer in 0..8")
    cap = Decimal(str(amount_cap)) if amount_cap is not None else None
    if cap is not None and (not cap.is_finite() or cap <= 0):
        raise ValueError("amount_cap must be finite and positive")
    Planner, _, _, _ = _dependencies()
    graph = StaticPolicyGraph(scenario, max_hops=max_hops)
    precisions = {
        eq: precision_by_eq.get(eq, default_precision)
        for eq in sorted(graph.equivalents)
    }
    precisions.update(precision_by_eq)
    planner = Planner(
        actions_per_tick_max=actions_per_tick,
        amount_cap_limit=cap,
        logger=logging.getLogger(__name__),
        action_factory=lambda seq, eq, sender, receiver, amount: (
            eq,
            sender,
            receiver,
            amount,
        ),
    )
    role = {
        pid: str(p.get("groupId") or "(ungrouped)")
        for pid, p in graph.participants.items()
    }
    counts, amounts = defaultdict(int), defaultdict(lambda: Decimal(0))
    pair_counts = defaultdict(int)
    inflow, outflow = defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0))
    total = unreachable = direct = nonactive = 0
    per_eq = defaultdict(lambda: {"planned": 0, "unreachable": 0, "direct": 0})
    run = SimpleNamespace(seed=seed, intensity_percent=intensity)
    for tick in range(1, ticks + 1):
        run.tick_index, run.sim_time_ms = tick, 1000 * tick
        for eq, sender, receiver, raw_amount in planner.plan_payments(
            run, scenario, debt_snapshot=None, precision_by_eq=precisions
        ):
            amount = Decimal(raw_amount)
            total += 1
            pair_counts[eq, sender, receiver] += 1
            failed = not graph.reachable(eq, sender, receiver)
            is_direct = receiver in graph.edges[eq].get(sender, ())
            unreachable += failed
            direct += is_direct
            nonactive += sender not in graph.active or receiver not in graph.active
            per_eq[eq]["planned"] += 1
            per_eq[eq]["unreachable"] += failed
            per_eq[eq]["direct"] += is_direct
            key = eq, role.get(sender, "(unknown)"), role.get(receiver, "(unknown)")
            counts[key] += 1
            amounts[key] += amount
            outflow[eq, sender] += amount
            inflow[eq, receiver] += amount
    result_eq = {}
    for eq in sorted(graph.equivalents | set(per_eq)):
        pairs = [
            (a, b) for a in sorted(graph.active) for b in sorted(graph.active) if a != b
        ]
        reachable = sum(graph.reachable(eq, a, b) for a, b in pairs)
        cycles = {str(length): graph.cycles(eq, length) for length in (3, 4)}
        cycle_nodes = {
            pid for paths in cycles.values() for path in paths for pid in path
        }
        pids = sorted(
            graph.participants.keys()
            | {pid for e, pid in inflow if e == eq}
            | {pid for e, pid in outflow if e == eq}
        )
        roles = sorted(
            set(role.values())
            | {r for e, r, _ in counts if e == eq}
            | {r for e, _, r in counts if e == eq}
        )
        total_out = sum((outflow[eq, pid] for pid in pids), Decimal(0))
        circulation = sum(
            (min(inflow[eq, pid], outflow[eq, pid]) for pid in pids), Decimal(0)
        )
        stats = per_eq[eq]
        result_eq[eq] = {
            "planned_actions": stats["planned"],
            "planned_pair_counts": [
                {"sender": sender, "receiver": receiver, "count": count}
                for (pair_eq, sender, receiver), count in sorted(pair_counts.items())
                if pair_eq == eq
            ],
            "planned_static_unreachable": _stat(stats["unreachable"], stats["planned"]),
            "direct_planned_payment_share": _stat(stats["direct"], stats["planned"]),
            "eligible_ordered_pair_coverage": _stat(reachable, len(pairs)),
            "role_flow_counts": {
                a: {b: counts[eq, a, b] for b in roles} for a in roles
            },
            "role_flow_amounts": {
                a: {b: str(amounts[eq, a, b]) for b in roles} for a in roles
            },
            "participant_balances": {
                pid: _balance(inflow[eq, pid], outflow[eq, pid]) for pid in pids
            },
            "role_balances": {
                r: _balance(
                    sum(
                        (
                            inflow[eq, pid]
                            for pid in pids
                            if role.get(pid, "(unknown)") == r
                        ),
                        Decimal(0),
                    ),
                    sum(
                        (
                            outflow[eq, pid]
                            for pid in pids
                            if role.get(pid, "(unknown)") == r
                        ),
                        Decimal(0),
                    ),
                )
                for r in roles
            },
            "circulation_upper_bound": {
                "numerator_amount": str(circulation),
                "denominator_out_amount": str(total_out),
                "share": _ratio(circulation, total_out),
            },
            "structural_cycles": {
                length: {"count": len(paths), "paths": paths}
                for length, paths in cycles.items()
            },
            "transit_policy_circuits": {
                str(length): {"count": len(paths), "paths": paths}
                for length in (3, 4)
                for paths in [graph.cycles(eq, length, transit_circuit=True)]
            },
            "role_cycle_participation": {
                r: _stat(
                    sum(pid in cycle_nodes for pid in graph.active if role[pid] == r),
                    sum(role[pid] == r for pid in graph.active),
                )
                for r in roles
            },
        }
    return {
        "scenario_id": scenario.get("scenario_id"),
        "scenario_sha256": hashlib.sha256(
            json.dumps(
                scenario, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest(),
        "parameters": {
            "ticks": ticks,
            "intensity_percent": intensity,
            "seed": seed,
            "actions_per_tick_max": actions_per_tick,
            "amount_cap": str(cap) if cap is not None else None,
            "precision_by_eq": precisions,
            "default_precision": default_precision,
            "router_max_hops": max_hops,
        },
        "model": {
            "debt_snapshot": None,
            "debt_evolution": False,
            "tick_range": "1..N",
            "sim_time_ms": "1000*tick_index",
            "applies": [
                "actual plan_payments",
                "stress tx_rate multipliers",
                "warmup and fractional action budget",
            ],
            "does_not_execute": [
                "scripted payment/inject",
                "clearing",
                "trust drift",
                "dynamic capacity",
                "reservations",
            ],
            "static_graph": "active participants and active positive-limit trustlines; debtor->creditor; zero-debt topology only",
            "policy": "pair_rules over both active lines, owner_pid=trustline.from; route_breaks_policy inner-node semantics",
            "planned_domain": "actual action weighted; no frozen/nonactive plans removed; historical baseline, not independent acceptance evidence after T5003",
            "coverage_domain": "all ordered distinct active participant pairs per equivalent, including isolated participants, independent of planner sampling",
            "cycle_domain": "primary structural_cycles: directed simple direct-hop debt rings length 3/4; once per rotation; reverse distinct; each individual hop is policy legal with no intermediate; role participation uses these rings; no debt/capacity simulation or clearing guarantee",
            "transit_circuit_domain": "separate diagnostic transit_policy_circuits: every rotation policy-valid so every vertex may mediate; not an economic ring acceptance criterion or a clearing bound",
            "direct_share": "actual plans whose debtor->creditor edge exists in static graph; assigned counterpart metadata unavailable",
            "balance_denominator": "in+out per participant/role, signed in-out; amounts never combined across equivalents",
            "roles": "groupId; missing=(ungrouped); metadata.role descriptive only",
            "import_side_effect": "real simulator package constructs runtime/engine; explicit ENV=test, task DATABASE_URL and isolated state required; measurement does not connect",
            "profiles_present": bool(scenario.get("behaviorProfiles")),
            "settings_present": isinstance(scenario.get("settings"), dict),
        },
        "population": {
            "participants": len(graph.participants),
            "active": len(graph.active),
            "excluded_nonactive": sorted(set(graph.participants) - graph.active),
            "self_pairs_excluded_per_equivalent": len(graph.active),
            "line_exclusions": dict(sorted(graph.exclusions.items())),
            "descriptive_roles": {
                pid: p.get("metadata", {}).get("role")
                for pid, p in sorted(graph.participants.items())
            },
        },
        "planned_actions": total,
        "planned_nonactive_endpoint_actions": nonactive,
        "planned_static_unreachable": _stat(unreachable, total),
        "direct_planned_payment_share": _stat(direct, total),
        "equivalents": result_eq,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--ticks", type=int, default=2000)
    parser.add_argument("--intensity", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--actions-per-tick", type=int, default=20)
    parser.add_argument("--amount-cap", default=None)
    parser.add_argument(
        "--precision", type=int, default=2, help="default equivalent precision (0..8)"
    )
    parser.add_argument(
        "--eq-precision", action="append", default=[], metavar="CODE=DIGITS"
    )
    parser.add_argument("--max-hops", type=int, default=6)
    parser.add_argument("--output", type=Path, help="JSON file instead of stdout")
    args = parser.parse_args(argv)
    try:
        precision = {}
        for raw in args.eq_precision:
            code, digits = raw.split("=", 1)
            if not code.strip():
                raise ValueError("empty equivalent in --eq-precision")
            precision[code.strip().upper()] = int(digits)
        result = measure(
            json.loads(args.scenario.read_text(encoding="utf-8")),
            ticks=args.ticks,
            intensity=args.intensity,
            seed=args.seed,
            actions_per_tick=args.actions_per_tick,
            amount_cap=args.amount_cap,
            precision_by_eq=precision,
            default_precision=args.precision,
            max_hops=args.max_hops,
        )
        payload = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        print(
            "EQ    plans  unreachable/plans  reachable/eligible  direct/plans  cycles3/4  circulation",
            file=sys.stderr,
        )
        for eq, metrics in result["equivalents"].items():

            def fraction(name):
                s = metrics[name]
                return f"{s['numerator']}/{s['denominator']}"

            print(
                f"{eq:<6} {metrics['planned_actions']:>5} {fraction('planned_static_unreachable'):>18} {fraction('eligible_ordered_pair_coverage'):>19} {fraction('direct_planned_payment_share'):>13} {metrics['structural_cycles']['3']['count']}/{metrics['structural_cycles']['4']['count']} {metrics['circulation_upper_bound']['share']}",
                file=sys.stderr,
            )
    except (ValueError, OSError, ArithmeticError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
