"""Offline planned-flow measurements and replay of recorded real-mode launches.

CLI: explicitly set ENV=test (compatible ENVIRONMENT alias), DEBUG=false, DATABASE_URL to an explicit task PostgreSQL URL and SIMULATOR_STATE_DIR
under .local-run/test-runs/<task>/ before running. No database connection is made
by this script; importing the real planner constructs the application's runtime
and engine. The state override protects the developer runtime from import cleanup.
JSON goes to stdout (or --output); a concise human table goes to stderr.
Replay: scenario.json --replay capture.json [--output report.json]; no runtime
imports or environment overrides are needed for a recorded launch.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import functools
import hashlib
import importlib.util
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


@functools.cache
def _replay_equivalent():
    """The runtime's own `effective_equivalent` (`app/core/simulator/scenario_equivalent.py`) for the replay.

    MM2-SOL-3: the runtime resolves a scenario's equivalent with it (`strip().upper()`, falling back to the scenario
    default) and records the resolved code, so the replay reads the scenario by the same rule. The module is loaded by
    its file: importing it through `app.core.simulator` would construct the simulator runtime, and the replay needs no
    runtime imports or environment (module docstring). The module imports nothing from `app`.
    """
    path = ROOT / "app" / "core" / "simulator" / "scenario_equivalent.py"
    spec = importlib.util.spec_from_file_location("_measure_replay_scenario_equivalent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.effective_equivalent


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


def _live_money(raw):
    if raw is None or isinstance(raw, (bool, float)):
        raise ValueError("Missing or inexact money measurement")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal money measurement") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("Money must be finite and nonnegative")
    return value


def _live_seq(event_id, run_id):
    prefix = f"evt_{run_id}_"
    if not isinstance(event_id, str) or not event_id.startswith(prefix):
        raise ValueError("Event ID belongs to another run")
    tail = event_id[len(prefix) :]
    if not tail.isdigit():
        raise ValueError("Invalid event sequence")
    return int(tail)


def _live_normal(transactions, roles, intended):
    successful = [p for p in transactions if p["type"] == "tx.updated"]
    n = len(transactions)

    def role_counts(items, endpoint):
        return {
            role: _stat(sum(roles[p[endpoint]] == role for p in items), n)
            for role in sorted(set(roles.values()))
        }

    def assigned(items):
        return sum((p["equivalent"], p["from"], p["to"]) in intended for p in items)

    return {
        "attempts": n,
        "successful": len(successful),
        "failed": n - len(successful),
        "successful_gross": format(
            sum((_live_money(p["amount"]) for p in successful), Decimal(0)), "f"
        ),
        "rejection": _stat(n - len(successful), n),
        "assigned": _stat(assigned(successful), n),
        "role_send": role_counts(successful, "from"),
        "role_receive": role_counts(successful, "to"),
        "attempt_assigned": _stat(assigned(transactions), n),
        "attempt_role_send": role_counts(transactions, "from"),
        "attempt_role_receive": role_counts(transactions, "to"),
        "errors": {
            code: sum(p.get("error", {}).get("code") == code for p in transactions)
            for code in sorted(
                {p["error"]["code"] for p in transactions if p["type"] == "tx.failed"}
            )
        },
    }


def _measure_live_launch(scenario, launch, roles, intended):
    run_id, epoch = launch["run_id"], launch["epoch"]
    expected, cadence = launch["expected_ticks"], launch["clearing_every"]
    if type(epoch) is not int or epoch < 0 or type(expected) is not int or expected < 1:
        raise ValueError("Invalid launch epoch or expected tick coverage")
    if type(cadence) is not int or cadence < 1:
        raise ValueError("Invalid clearing cadence")
    snapshot = launch["initial_snapshot"]
    eq = snapshot["equivalent"]
    debt, limits = {}, {}
    for edge in snapshot["links"]:
        key = edge["source"], edge["target"]
        if key in debt:
            raise ValueError("Duplicate snapshot edge")
        debt[key] = _live_money(edge.get("used"))
        limits[key] = _live_money(edge.get("trust_limit"))
    initial = launch["initial_status"]
    if initial.get("run_id") != run_id or initial.get("sim_time_ms") != 0:
        raise ValueError("Initial status must describe this launch before tick 1")
    if initial.get("episode_progress"):
        raise ValueError("Initial launch progress is not empty")
    prior_status = initial
    counters = ("attempts_total", "committed_total", "rejected_total", "errors_total")

    def counter(status, name):
        value = status.get(name)
        if type(value) is not int or value < 0:
            raise ValueError(f"Missing runtime counter {name}")
        return value

    for name in counters:
        counter(initial, name)

    seq = _live_seq(launch["start_event_id"], run_id)
    events, seen = [], {}
    for envelope in launch["events"]:
        event_id, kind, payload = envelope["id"], envelope["type"], envelope["payload"]
        if payload.get("event_id") != event_id or payload.get("type") != kind:
            raise ValueError("Conflicting SSE envelope")
        if event_id in seen:
            if envelope != seen[event_id]:
                raise ValueError("Conflicting duplicate event ID")
            continue
        seen[event_id] = envelope
        number = _live_seq(event_id, run_id)
        if number != seq + 1:
            raise ValueError("SSE event gap or out-of-order sequence")
        seq = number
        events.append(envelope)
    completions = launch["completed_ticks"]
    if [c["tick"] for c in completions] != list(range(1, expected + 1)):
        raise ValueError("Missing completed tick coverage")
    by_id = {e["id"]: i for i, e in enumerate(events)}
    previous_end, progress = -1, {}
    normal, scripted = [], []
    clearings, stalls = [], []
    max_ratio, max_sample, observed_tick = Decimal(0), None, 0

    def observe():
        nonlocal max_ratio, max_sample
        for key, amount in debt.items():
            limit = limits[key]
            if limit > 0:
                ratio = amount / limit
                if max_sample is None or ratio > max_ratio:
                    max_ratio = ratio
                    max_sample = {
                        "tick": observed_tick,
                        "source": key[0],
                        "target": key[1],
                        "used": format(amount, "f"),
                        "trust_limit": format(limit, "f"),
                        "ratio": format(ratio, "f"),
                    }

    observe()

    def patch(edges, *, required=False):
        if required and not edges:
            raise ValueError("Missing required money edge patch")
        for edge in edges or []:
            key = edge["source"], edge["target"]
            if key not in debt:
                raise ValueError("Money patch for unknown edge")
            if "used" in edge:
                debt[key] = _live_money(edge["used"])
            elif required:
                raise ValueError("Missing absolute used in money patch")
            if "trust_limit" in edge:
                limits[key] = _live_money(edge["trust_limit"])
        observe()

    payments = {
        i: e
        for i, e in enumerate(scenario.get("events", []))
        if e.get("type") == "payment"
    }
    nominal = sum(
        (
            _live_money(e["amount"])
            for e in payments.values()
            if _replay_equivalent()(scenario, e) == eq
            and type(e.get("time")) is int
            and e["time"] <= 20000
        ),
        Decimal(0),
    )
    for completion in completions:
        tick, event_id, status = (
            completion["tick"],
            completion["event_id"],
            completion["status"],
        )
        observed_tick = tick
        if event_id not in by_id or by_id[event_id] <= previous_end:
            raise ValueError("Missing or reused post-tick SSE completion")
        end = by_id[event_id]
        completed = events[end]
        payload = completed["payload"]
        if (
            completed["type"] != "run_status"
            or payload.get("sim_time_ms") != tick * 1000
            or payload.get("run_id") != run_id
        ):
            raise ValueError("Completion must link this tick's post-tick run_status")
        if status.get("run_id") != run_id or status.get("sim_time_ms") != tick * 1000:
            raise ValueError("GET status does not match completed tick")
        if any(counter(status, name) != counter(payload, name) for name in counters):
            raise ValueError("GET/SSE status counters disagree")
        if (status.get("consec_all_rejected_ticks") or 0) != (
            payload.get("consec_all_rejected_ticks") or 0
        ):
            raise ValueError("GET/SSE stall evidence disagrees")
        if (status.get("consec_all_rejected_ticks") or 0) >= 3:
            stalls.append(tick)
        transactions, cleared, cycle_amount = [], Decimal(0), Decimal(0)
        for event in events[previous_end + 1 : end + 1]:
            kind, p = event["type"], event["payload"]
            if kind == "run_status":
                continue
            if p.get("equivalent") != eq:
                raise ValueError(
                    "Recording must contain all events of one measured equivalent"
                )
            if kind in ("tx.updated", "tx.failed"):
                if p.get("from") not in roles or p.get("to") not in roles:
                    raise ValueError("Unknown transaction endpoint")
                if kind == "tx.updated":
                    _live_money(p.get("amount"))
                    patch(p.get("edge_patch"), required=True)
                elif not p.get("error", {}).get("code"):
                    raise ValueError("Missing failed payment outcome")
                transactions.append(p)
            elif kind == "clearing.done":
                if tick % cadence:
                    raise ValueError(
                        "Unscheduled clearing cannot be attributed to scheduled interval"
                    )
                before = sum(debt.values(), Decimal(0))
                patch(p.get("edge_patch"), required=True)
                reduction = before - sum(debt.values(), Decimal(0))
                if reduction < 0:
                    raise ValueError("Clearing increased edge debt")
                cleared += reduction
                if p.get("cleared_amount") is None:
                    cycle_amount = None
                elif cycle_amount is not None:
                    cycle_amount += _live_money(p["cleared_amount"])
            elif kind == "topology.changed":
                topology = p.get("payload", {})
                for edge in topology.get("added_edges", []):
                    if edge["equivalent_code"] != eq:
                        raise ValueError("Mixed equivalent topology")
                    key = edge["from_pid"], edge["to_pid"]
                    if key in debt:
                        raise ValueError("Duplicate added edge")
                    debt[key], limits[key] = Decimal(0), _live_money(edge.get("limit"))
                patch(topology.get("edge_patch"))
                for edge in topology.get("removed_edges", []):
                    key = edge["from_pid"], edge["to_pid"]
                    if key not in debt or debt[key] != 0:
                        raise ValueError("Removed edge has unavailable or nonzero debt")
                    del debt[key], limits[key]
            else:
                raise ValueError(f"Unmeasured event type {kind}")
        success = sum(p["type"] == "tx.updated" for p in transactions)
        delta = {
            name: counter(status, name) - counter(prior_status, name)
            for name in counters
        }
        if (
            delta["attempts_total"] != len(transactions)
            or delta["committed_total"] != success
            or delta["rejected_total"] + delta["errors_total"]
            != len(transactions) - success
            or any(n < 0 for n in delta.values())
        ):
            raise ValueError(
                "Runtime counters do not reconcile with terminal SSE outcomes"
            )
        current = {}
        for record in status.get("episode_progress") or []:
            index = record["index"]
            if record.get("epoch") != epoch or index in current:
                raise ValueError("Mixed launch epoch or duplicate episode progress")
            story = scenario.get("events", [])
            if (
                type(index) is not int
                or not 0 <= index < len(story)
                or story[index].get("type") != record.get("kind")
            ):
                raise ValueError(
                    "Episode progress does not belong to bound scenario event"
                )
            current[index] = record
        matched = set()
        for index, event in payments.items():
            due = event.get("time")
            if type(due) is not int:
                raise ValueError("Unmeasurable scripted payment time")
            old, record = progress.get(index), current.get(index)
            if due > tick * 1000:
                if record is not None:
                    raise ValueError("Scripted progress before due tick")
                continue
            if record is None:
                raise ValueError("Missing due scripted episode progress")
            if old and old["status"] in ("done", "refused"):
                if old != record:
                    raise ValueError("Terminal scripted progress changed")
                continue
            attempts = record.get("attempts")
            if (
                type(attempts) is not int
                or attempts != (old.get("attempts", 0) if old else 0) + 1
            ):
                raise ValueError("Scripted attempt history unavailable")
            expected_payment = {
                "from": event["from"],
                "to": event["to"],
                "amount": event["amount"],
                "equivalent": _replay_equivalent()(scenario, event),
            }
            actual_payment = record.get("payment")
            if (
                record.get("kind") != "payment"
                or actual_payment != expected_payment
                or record.get("status") not in ("done", "refused", "incomplete")
            ):
                raise ValueError("Scripted progress payment/outcome mismatch")
            kind = "tx.updated" if record["status"] == "done" else "tx.failed"
            candidates = [
                i
                for i, p in enumerate(transactions)
                if i not in matched
                and p["type"] == kind
                and all(
                    p.get(k) == expected_payment[k]
                    for k in ("equivalent", "from", "to")
                )
                and (
                    _live_money(p.get("amount")) == _live_money(event["amount"])
                    if kind == "tx.updated"
                    else p.get("error", {}).get("code") == record.get("reason")
                )
            ]
            if not candidates:
                raise ValueError("Scripted outcome multiset does not reconcile")
            chosen = candidates[
                -1
            ]  # normals precede scripts; identical outcomes are metrically interchangeable.
            matched.add(chosen)
            scripted.append(
                {
                    "type": kind,
                    "amount": event["amount"],
                    "tick": tick,
                    "status": record["status"],
                    "reason": record.get("reason"),
                }
            )
        if any(index not in current for index in progress):
            raise ValueError("Episode progress disappeared")
        normal.extend(
            {**p, "tick": tick} for i, p in enumerate(transactions) if i not in matched
        )
        if tick % cadence == 0:
            turnover = sum(
                (
                    _live_money(p["amount"])
                    for p in normal
                    if tick - cadence < p["tick"] <= tick and p["type"] == "tx.updated"
                ),
                Decimal(0),
            )
            uses_opening = tick == cadence and nominal > 0
            denominator = nominal if uses_opening else turnover
            if denominator <= 0:
                raise ValueError("Missing or zero scheduled clearing denominator")
            clearings.append(
                {
                    "tick": tick,
                    "cleared_edge_debt": format(cleared, "f"),
                    "cleared_cycle_amount": (
                        format(cycle_amount, "f") if cycle_amount is not None else None
                    ),
                    "normal_successful_turnover": format(turnover, "f"),
                    "denominator": format(denominator, "f"),
                    "denominator_basis": (
                        "nominal_opening_gross"
                        if uses_opening
                        else "normal_successful_turnover"
                    ),
                    "share": _ratio(cleared, denominator),
                }
            )
        previous_end, prior_status, progress = end, status, current
    for event in events[previous_end + 1 :]:
        payload = event["payload"]
        if (
            event["type"] != "run_status"
            or payload.get("run_id") != run_id
            or payload.get("sim_time_ms") != expected * 1000
            or any(
                counter(payload, name) != counter(prior_status, name)
                for name in counters
            )
        ):
            raise ValueError("Uncompleted trailing SSE events")
    if not normal and initial.get("intensity_percent") != 0:
        raise ValueError("Missing or zero normal attempt denominator")
    return {
        "epoch": epoch,
        "equivalent": eq,
        "completed_ticks": expected,
        "normal": _live_normal(normal, roles, intended),
        "windows": {
            name: (
                _live_normal(
                    [p for p in normal if lo <= p["tick"] <= hi], roles, intended
                )
                if expected >= lo
                else None
            )
            for name, lo, hi in (("opening", 1, 20), ("steady", 21, expected))
        },
        "scripted": {
            "successful": sum(p["type"] == "tx.updated" for p in scripted),
            "failed": sum(p["type"] == "tx.failed" for p in scripted),
            "refused": sum(p["status"] == "refused" for p in scripted),
            "incomplete": sum(p["status"] == "incomplete" for p in scripted),
            "successful_gross": format(
                sum(
                    (
                        _live_money(p["amount"])
                        for p in scripted
                        if p["type"] == "tx.updated"
                    ),
                    Decimal(0),
                ),
                "f",
            ),
            "refused_gross": format(
                sum(
                    (
                        _live_money(p["amount"])
                        for p in scripted
                        if p["status"] == "refused"
                    ),
                    Decimal(0),
                ),
                "f",
            ),
            "incomplete_gross": format(
                sum(
                    (
                        _live_money(p["amount"])
                        for p in scripted
                        if p["status"] == "incomplete"
                    ),
                    Decimal(0),
                ),
                "f",
            ),
            "errors": {
                reason: sum(p["reason"] == reason for p in scripted)
                for reason in sorted({p["reason"] for p in scripted if p["reason"]})
            },
            "nominal_gross": format(nominal, "f"),
        },
        "clearings": clearings,
        "max_debt_limit": format(max_ratio, "f") if max_sample else None,
        "max_debt_limit_sample": max_sample,
        "stall_ticks": stalls,
        "final_debt_by_edge": [
            {"source": a, "target": b, "used": format(amount, "f")}
            for (a, b), amount in sorted(debt.items())
        ],
    }


def measure_live(bound_scenario, recording):
    """Measure complete recorded tick prefixes, without planner or runtime imports.

    Each launch supplies epoch/run_id, initial_snapshot and initial_status at
    sim_time=0, start_event_id cursor, ordered SSE envelopes {id,type,payload},
    completed_ticks {tick,event_id,status} (post-tick SSE plus matching GET
    RunStatus), expected_ticks and clearing_every. All SSE IDs must be contiguous;
    exact replay duplicates are ignored. Restart launches have separate snapshots,
    progress and epochs. Capture parameters may be retained as extra metadata.

    This parses evidence, not acceptance thresholds. Missing required evidence
    raises ValueError; future windows are None, observed scheduled empty clearing
    is zero. Tempo-zero launches expose undefined normal shares as None.
    The first clearing uses positive nominal opening gross when present;
    otherwise its denominator is successful normal turnover, as for later windows.
    Failed SSE has no amount: endpoint/currency/error counts are reconciled;
    scripted attempted gross comes from progress and normal failed gross is not
    invented. Identical failed endpoint/outcome records are interchangeable.
    max_debt_limit_sample is observed on initial snapshot and ordered money/limit
    patches, not a claim of continuous or unreported database state.
    """
    try:
        roles = {
            str(p["id"]): str(p.get("groupId") or "(ungrouped)")
            for p in bound_scenario["participants"]
        }
        intended = {
            (
                _replay_equivalent()(bound_scenario, line),
                str(line["to"]),
                str(line["from"]),
            )
            for line in bound_scenario["trustlines"]
            if str(line.get("status", "active")).lower() == "active"
            and _live_money(line["limit"]) > 0
        }
        launches = recording["launches"]
        if not launches or len({(x["run_id"], x["epoch"]) for x in launches}) != len(
            launches
        ):
            raise ValueError("Missing or duplicate launch epoch")
        return {
            "scenario_id": bound_scenario.get("scenario_id"),
            "launches": [
                _measure_live_launch(bound_scenario, launch, roles, intended)
                for launch in launches
            ],
        }
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(f"Missing or malformed live recording: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument(
        "--replay", type=Path, help="measure a live recording instead of offline plans"
    )
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
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
        result = (
            measure_live(scenario, json.loads(args.replay.read_text(encoding="utf-8")))
            if args.replay
            else measure(
                scenario,
                ticks=args.ticks,
                intensity=args.intensity,
                seed=args.seed,
                actions_per_tick=args.actions_per_tick,
                amount_cap=args.amount_cap,
                precision_by_eq=precision,
                default_precision=args.precision,
                max_hops=args.max_hops,
            )
        )
        payload = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        if args.replay:
            return 0
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
