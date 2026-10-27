"""Generate the realistic-v2 simulator scenarios from the community descriptions.

A simulator scenario is *a community description plus the behaviour the scenario
adds on top of it*:

* **Roster** — participants, groups, status, type and descriptive role come from
  ``seeds/communities/<id>/community.json``. Its trustlines are not copied.
* **Economy** — fixed role flows, counterpart assignments, inverse trustlines and
  opening debt rings are derived here. No second roster or assignments metadata.
* **Behaviour** — profiles, recipient weights, amount models, flow chains,
  warm-up, trust drift and seasonal stress — belongs to the scenario and lives
  in this file.
* **Identity in a database** — a scenario's participant id is
  ``<scenario_id>:<community pid>`` (``scenario_participant_id``). The community
  ``pid`` is the description's own label and repeats between communities
  (``PID_U0001_9e3779b1`` is a different person in each), while a real-mode run
  stores its participants under the scenario id, and the seeder adopts a stored
  simulated participant by id alone. Two scenarios run in one database must
  therefore not share an id (D1, 2026-10-10: they did, and the second one ran
  on the first one's people, lines and debts).

The generator is deterministic: same description in, same bytes out. It writes
nothing that is not derived from the description or from the behaviour block
below, and it validates the result against
``fixtures/simulator/scenario.schema.json``.

Run:
  ./.venv/Scripts/python.exe scripts/generate_simulator_seed_scenarios.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_DOWN
import json
import os
import subprocess
import time
import uuid
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT / "seeds" / "communities") not in sys.path:
    sys.path.insert(0, str(ROOT / "seeds" / "communities"))

from community_schema import load_community  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" rather than the platform default: `.gitattributes` keeps every
    # `.json` at LF in the worktree, so a CRLF write would make the scenario read
    # as modified after every run on Windows, and the output would stop being
    # byte-identical across platforms.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _validate_scenario_shape(scenario_path: Path) -> None:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError

    schema = _read_json(ROOT / "fixtures" / "simulator" / "scenario.schema.json")
    try:
        Draft202012Validator(schema).validate(_read_json(scenario_path))
    except ValidationError as exc:
        location = "/".join(str(p) for p in exc.absolute_path)
        raise RuntimeError(
            f"Invalid scenario {scenario_path} at {location}: {exc.message}"
        ) from exc


#: ``participants.pid`` is ``String(64)`` (``app/db/models/participant.py``). The
#: scenario schema allows an id of 200 characters; the database does not.
MAX_PARTICIPANT_ID_LENGTH = 64


def scenario_participant_id(scenario_id: str, community_pid: str) -> str:
    """The id a scenario gives a participant of its community. The one owner of the rule.

    ``community_pid`` is kept whole and opaque: its ``_xxxxxxxx`` tail is a
    historical label, not a checksum of anything, and the simulator's pseudo key
    hashes the whole resulting id.
    """
    pid = f"{scenario_id}:{community_pid}"
    if len(pid) > MAX_PARTICIPANT_ID_LENGTH:
        raise RuntimeError(
            f"{scenario_id}: participant id '{pid}' is {len(pid)} characters; "
            f"participants.pid holds {MAX_PARTICIPANT_ID_LENGTH}"
        )
    return pid


# Fixed before planner probes (050 T5002). Values are amount-flow shares.
ROLE_FLOW = {
    "households": {"retail": ".7", "services": ".1", "anchors": ".2"},
    "retail": {"producers": ".7", "agents": ".2", "services": ".1"},
    "producers": {"households": ".7", "services": ".3"},
    "anchors": {"households": ".7", "producers": ".3"},
    "services": {"households": ".7", "retail": ".3"},
    "agents": {"producers": "1"},
}
ROLE_P50 = {
    "households": 100,
    "retail": 200,
    "producers": 150,
    "anchors": 200,
    "services": 120,
    "agents": 150,
}
BALANCE_SEEDS = (42, 1729, 20261027)
BALANCE_TICKS = 2000


def role_turnover() -> dict[str, Decimal]:
    """Stationary AMOUNT shares, independently of participant counts and seeds."""
    values = {r: Decimal(1) / len(ROLE_FLOW) for r in ROLE_FLOW}
    for _ in range(500):
        nxt = {r: Decimal(0) for r in ROLE_FLOW}
        for sender, targets in ROLE_FLOW.items():
            for receiver, share in targets.items():
                nxt[receiver] += values[sender] * Decimal(share)
        if max(abs(nxt[r] - values[r]) for r in values) < Decimal("1e-24"):
            return nxt
        values = nxt
    raise RuntimeError("Role circulation did not converge")


def amount_model(role: str) -> dict[str, int]:
    scale = ROLE_P50[role]
    return {"p50": scale, "p90": 2 * scale, "min": scale // 2, "max": 3 * scale}


def approximate_amount_mean(role: str) -> Decimal:
    """Clipped lognormal mean of pick_amount, within .005 UAH after cent rounding.

    The sampler uses sigma=log(p90/p50)/1.281551565545 and clamps to min/max,
    then ROUND_DOWN to .01. Continuous expectation minus .005 is the midpoint
    of the enclosing cent-rounding interval, NOT an exact discrete mean.
    No configured global cap is used by this generated model (runtime opt-in
    caps and changing debt headroom require their own observation).
    """
    import math
    from statistics import NormalDist

    model = amount_model(role)
    sigma = math.log(model["p90"] / model["p50"]) / 1.281551565545
    lo, hi = model["min"] / model["p50"], model["max"] / model["p50"]
    a, b = math.log(lo) / sigma, math.log(hi) / sigma
    cdf = NormalDist().cdf
    factor = (
        lo * cdf(a)
        + math.exp(sigma * sigma / 2) * (cdf(b - sigma) - cdf(a - sigma))
        + hi * (1 - cdf(b))
    )
    return Decimal(str(factor)) * model["p50"] - Decimal(".005")


def economy_model(participants: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure deterministic counterpart assignment; no persisted second roster.

    This bounded model requires every role, enough payroll recipients, at least
    two shops/anchors/services/agents, and enough shop supplier slots. It refuses
    infeasible input rather than finding a convenient random seed or optimizer.
    """
    by_role = {r: [] for r in ROLE_FLOW}
    for p in participants:
        role = p["groupId"]
        if role not in by_role:
            raise RuntimeError(f"No realistic-v2 behaviour profile for group '{role}'")
        if p.get("status", "active") == "active":
            by_role[role].append(p["id"])
    for role, pids in by_role.items():
        pids.sort()
        minimum = 2 if role in {"retail", "anchors", "services", "agents"} else 1
        if len(pids) < minimum:
            raise RuntimeError(
                f"Infeasible {role}: needs at least {minimum} active participants"
            )
    H, R, P, A, S, G = (
        by_role[r]
        for r in ("households", "retail", "producers", "anchors", "services", "agents")
    )
    if len(H) < len(P) + len(A):
        raise RuntimeError(
            "Infeasible households payroll: fewer households than producers + anchors"
        )
    supplier_count = min(4, len(P))
    if len(P) > supplier_count * len(R):
        raise RuntimeError(
            "Infeasible producers: retail supplier slots cannot cover every employer"
        )
    role_by_pid = {pid: r for r, pids in by_role.items() for pid in pids}
    turnover = role_turnover()
    income = {pid: Decimal(0) for pid in role_by_pid}
    pairs: set[tuple[str, str]] = set()
    outgoing: dict[str, dict[str, list[str]]] = {pid: {} for pid in role_by_pid}

    def add(sender, target):
        if sender == target or (target, sender) in pairs:
            raise RuntimeError(
                f"Infeasible {role_by_pid[sender]} {sender}: reciprocal counterpart {target}"
            )
        pairs.add((sender, target))
        outgoing[sender].setdefault(role_by_pid[target], [])
        if target not in outgoing[sender][role_by_pid[target]]:
            outgoing[sender][role_by_pid[target]].append(target)

    def eligible(sender, targets):
        return [
            v
            for v in targets
            if v != sender and (v, sender) not in pairs and (sender, v) not in pairs
        ]

    def assign(sender_role, target_role, count):
        # Near-regular assignment balances nominal incoming AMOUNTS, not ids.
        for sender in by_role[sender_role]:
            targets = by_role[target_role]
            selected = outgoing[sender].get(target_role, [])
            while len(selected) < count:
                pool = eligible(sender, targets)
                if not pool:
                    raise RuntimeError(
                        f"Infeasible {sender_role} {sender}: no {target_role} counterpart"
                    )
                target = min(pool, key=lambda v: (income[v], v))
                add(sender, target)
                selected = outgoing[sender][target_role]
                income[target] += (
                    turnover[sender_role]
                    * Decimal(ROLE_FLOW[sender_role][target_role])
                    / len(by_role[sender_role])
                    / count
                )

    # Exactly one employer per household, and every employer has payroll.
    employers = P + A
    employer_by_household = {h: employers[i % len(employers)] for i, h in enumerate(H)}
    for employer in employers:
        staff = [h for h in H if employer_by_household[h] == employer]
        for h in staff:
            add(employer, h)
            income[h] += (
                turnover[role_by_pid[employer]]
                * Decimal(".7")
                / len(by_role[role_by_pid[employer]])
                / len(staff)
            )

    # Cover each producer before filling each shop's 2..4 supplier slots.
    for producer in P:
        shop = min(R, key=lambda v: (len(outgoing[v].get("producers", [])), v))
        add(shop, producer)
    for shop in R:
        while len(outgoing[shop].get("producers", [])) < supplier_count:
            pool = eligible(shop, P)
            producer = min(pool, key=lambda v: (sum((r, v) in pairs for r in R), v))
            add(shop, producer)
        for producer in outgoing[shop]["producers"]:
            income[producer] += (
                turnover["retail"] * Decimal(".7") / len(R) / supplier_count
            )

    # A producer employer supplies at least one of its household's two shops.
    shop_load = Counter()
    for h in H:
        employer = employer_by_household[h]
        primary = [r for r in R if (r, employer) in pairs] if employer in P else R
        first = min(primary, key=lambda r: (shop_load[r], r))
        second = min((r for r in R if r != first), key=lambda r: (shop_load[r], r))
        for shop in (first, second):
            add(h, shop)
            shop_load[shop] += 1
            income[shop] += turnover["households"] * Decimal(".7") / len(H) / 2

    # Rent avoids an anchor's own employee; procurement closes H -> A -> P -> H.
    rent_load = Counter()
    for h in H:
        anchors = [a for a in A if a != employer_by_household[h]]
        anchor = min(anchors, key=lambda a: (rent_load[a], a))
        add(h, anchor)
        rent_load[anchor] += 1
        income[anchor] += turnover["households"] * Decimal(".2") / len(H)
        employer = employer_by_household[h]
        if employer in P:
            add(anchor, employer)
    for anchor in A:
        if not outgoing[anchor].get("producers"):
            add(anchor, min(P, key=lambda p: (income[p], p)))
        for producer in outgoing[anchor]["producers"]:
            income[producer] += (
                turnover["anchors"]
                * Decimal(".3")
                / len(A)
                / len(outgoing[anchor]["producers"])
            )

    # Service payroll fills the household deficit left by its single employer.
    k = (2 * len(H) + len(S) - 1) // len(S)
    if k >= len(H):
        raise RuntimeError("Infeasible services: distinct household payroll cannot fit")
    service_total = turnover["services"] * Decimal(".7")
    deficits = {h: turnover["households"] / len(H) - income[h] for h in H}
    if min(deficits.values()) <= 0:
        raise RuntimeError(
            "Infeasible households: employer payroll leaves no positive service-payroll deficit"
        )
    quota_raw = {h: deficits[h] / sum(deficits.values()) * len(S) * k for h in H}
    quotas = {h: int(quota_raw[h]) for h in H}
    remainder = len(S) * k - sum(quotas.values())
    for h in sorted(H, key=lambda h: (-(quota_raw[h] - quotas[h]), h))[:remainder]:
        quotas[h] += 1
    if max(quotas.values()) >= len(S):
        raise RuntimeError(
            "Infeasible services: household payroll leaves no distinct service provider"
        )
    # Largest remaining degree first is the bipartite degree-sequence construction.
    remaining = dict(quotas)
    for service in S:
        for h in sorted(H, key=lambda h: (-remaining[h], h))[:k]:
            if remaining[h] <= 0:
                raise RuntimeError(
                    f"Infeasible services {service}: payroll degree sequence"
                )
            add(service, h)
            remaining[h] -= 1
            income[h] += service_total / len(S) / k
    if any(remaining.values()):
        raise RuntimeError("Infeasible services: incomplete household payroll")

    assign("services", "retail", min(2, len(R)))
    assign("retail", "agents", min(2, len(G)))
    assign("agents", "producers", min(4, len(P)))
    assign("producers", "services", min(2, len(S)))
    assign("retail", "services", 1)
    assign("households", "services", 1)
    for sender, targets in outgoing.items():
        missing = set(ROLE_FLOW[role_by_pid[sender]]) - targets.keys()
        if missing:
            raise RuntimeError(
                f"Infeasible {role_by_pid[sender]} {sender}: missing counterpart roles {sorted(missing)}"
            )
    return {
        "assigned_pairs": sorted(pairs),
        "by_role": by_role,
        "role_by_pid": role_by_pid,
        "outgoing": outgoing,
        "role_turnover": turnover,
    }


def behavior_profiles(model):
    degree = Counter(a for a, _ in model["assigned_pairs"])
    weights = {
        pid: model["role_turnover"][role]
        / len(model["by_role"][role])
        / approximate_amount_mean(role)
        / degree[pid]
        for pid, role in model["role_by_pid"].items()
    }
    scale = Decimal(".8") / max(weights.values())
    rates = {pid: float(scale * weight) for pid, weight in weights.items()}
    if sum(rates.values()) / len(rates) < 0.05:
        raise RuntimeError(
            "Model mean(tx_rate) is below .05; sampling budget is not supported"
        )
    profiles = {}
    profile_by_pid = {}
    for pid, role in sorted(model["role_by_pid"].items()):
        ident = f"{role}_degree_{degree[pid]}"
        profile_by_pid[pid] = ident
        q = ROLE_FLOW[role]
        primary = next(iter(q))
        fallback = {
            target: float(
                (Decimal(share) - (Decimal(".7") if target == primary else 0))
                / Decimal(".3")
            )
            for target, share in q.items()
        }
        profiles[ident] = {
            "id": ident,
            "props": {
                "tx_rate": rates[pid],
                "equivalent_weights": {"UAH": 1},
                "recipient_group_weights": {r: w for r, w in fallback.items() if w > 0},
                "amount_model": {"UAH": amount_model(role)},
                "flow_chains": [[role, primary]],
            },
        }
    return [profiles[k] for k in sorted(profiles)], profile_by_pid


def opening_events(model, limits):
    graph = {
        pid: sorted({b for a, b in model["assigned_pairs"] if a == pid})
        for pid in model["role_by_pid"]
    }
    cycles = set()
    for start in sorted(graph):
        stack = [[start]]
        while stack:
            path = stack.pop()
            if len(path) >= 3 and start in graph[path[-1]]:
                cycles.add(tuple(path))
            if len(path) < 4:
                stack.extend(
                    path + [n] for n in graph[path[-1]] if n > start and n not in path
                )
    events, used, covered = [], set(), set()
    while cycles:
        cycle = min(
            cycles,
            key=lambda c: (
                -len(set(c) - covered),
                -len({model["role_by_pid"][p] for p in c}),
                c,
            ),
        )
        cycles.remove(cycle)
        edges = list(zip(cycle, cycle[1:] + cycle[:1]))
        if any(e in used for e in edges) or len(events) + len(edges) > 100:
            continue
        amount = min(limits[e] / 4 for e in edges).quantize(
            Decimal(".01"), rounding=ROUND_DOWN
        )
        if amount <= 0:
            raise RuntimeError("Opening ring has no positive monetary step")
        for sender, receiver in edges:
            events.append(
                {
                    "time": 1000 * (len(events) // 5 + 1),
                    "type": "payment",
                    "from": sender,
                    "to": receiver,
                    "equivalent": "UAH",
                    "amount": format(amount, "f"),
                }
            )
        used.update(edges)
        covered.update(cycle)
    if not events:
        raise RuntimeError("Model has no direct three/four participant opening ring")
    return events


def validate_economy_reports(scenario, reports):
    """Pool actual amounts first; no averaging ratios or excluding inconvenient plans."""
    active = {
        p["id"]: p["groupId"]
        for p in scenario["participants"]
        if p.get("status", "active") == "active"
    }
    totals = {pid: [Decimal(0), Decimal(0)] for pid in active}
    for report in reports:
        balances = report["equivalents"]["UAH"]["participant_balances"]
        for pid in totals:
            for i, field in enumerate(("in", "out")):
                totals[pid][i] += Decimal(balances.get(pid, {}).get(field, "0"))
    roles = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for pid, (incoming, outgoing) in totals.items():
        role = active[pid]
        if not incoming or not outgoing:
            raise RuntimeError(
                f"Unrepresented flow {role} {pid}: in={incoming}, out={outgoing}"
            )
        actual = abs(incoming - outgoing) / (incoming + outgoing)
        if actual > Decimal(".25"):
            raise RuntimeError(
                f"Unbalanced {role} {pid}: actual={actual}, threshold=.25, in={incoming}, out={outgoing}"
            )
        roles[role][0] += incoming
        roles[role][1] += outgoing
    for role, (incoming, outgoing) in roles.items():
        actual = abs(incoming - outgoing) / (incoming + outgoing)
        if actual > Decimal(".10"):
            raise RuntimeError(
                f"Unbalanced role {role}: actual={actual}, threshold=.10"
            )
    return {
        "participant_max": str(max(abs(a - b) / (a + b) for a, b in totals.values())),
        "role_max": str(max(abs(a - b) / (a + b) for a, b in roles.values())),
    }


def _make_seasonal_stress_events() -> list[dict[str, Any]]:
    """Seasonal stress events for realistic-v2 scenarios.

    These model periodic demand fluctuations: weekend markets, quiet periods,
    harvest festivals, and winter lulls.
    """
    return [
        {
            "time": 120000,
            "type": "stress",
            "label": "weekend_market",
            "description": "Weekend market day — increased consumer activity",
            "effects": [
                {
                    "op": "mult",
                    "field": "tx_rate",
                    "scope": "group:households",
                    "value": 1.5,
                },
                {
                    "op": "mult",
                    "field": "tx_rate",
                    "scope": "group:retail",
                    "value": 1.3,
                },
            ],
            "metadata": {"duration_ms": 30000},
        },
        {
            "time": 180000,
            "type": "stress",
            "label": "quiet_period",
            "description": "Midweek quiet period — reduced economic activity",
            "effects": [
                {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.7},
            ],
            "metadata": {"duration_ms": 20000},
        },
        {
            "time": 240000,
            "type": "stress",
            "label": "harvest_festival",
            "description": "Harvest festival — peak seasonal demand, producers and retail booming",
            "effects": [
                {
                    "op": "mult",
                    "field": "tx_rate",
                    "scope": "group:producers",
                    "value": 2.0,
                },
                {
                    "op": "mult",
                    "field": "tx_rate",
                    "scope": "group:retail",
                    "value": 1.8,
                },
                {
                    "op": "mult",
                    "field": "tx_rate",
                    "scope": "group:households",
                    "value": 1.5,
                },
            ],
            "metadata": {"duration_ms": 40000},
        },
        {
            "time": 300000,
            "type": "stress",
            "label": "winter_lull",
            "description": "Winter lull — minimal economic activity, people stay home",
            "effects": [
                {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.5},
            ],
            "metadata": {"duration_ms": 15000},
        },
    ]


def _make_settings_realistic_v2() -> dict[str, Any]:
    return {
        "warmup": {"ticks": 50, "floor": 0.1},
        "trust_drift": {
            "enabled": True,
            "growth_rate": 0.05,
            "decay_rate": 0.02,
            "max_growth": 2.0,
            "min_limit_ratio": 0.3,
            "overload_threshold": 0.8,
        },
        "flow": {
            "enabled": True,
            "default_affinity": 0.7,
            "reciprocity_bonus": 0.15,
        },
    }


# Each entry is "this community, run with this behaviour". The community_id is
# the only structural input; everything else on the line is the scenario's own.
SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "greenfield-village-100-realistic-v2",
        "community_id": "greenfield-village-100",
        # Realistic-v2 is a single-equivalent profile: the HOUR and EUR lines of
        # the description are left out of this scenario, not deleted from it.
        "equivalents": ["UAH"],
    },
    {
        "scenario_id": "riverside-town-50-realistic-v2",
        "community_id": "riverside-town-50",
        "equivalents": ["UAH"],
    },
]


def _scenario_from_community(
    *,
    scenario_id: str,
    community: dict[str, Any],
    equivalents: list[str],
) -> dict[str, Any]:
    active_codes = {eq["code"] for eq in community["equivalents"] if eq["is_active"]}
    unknown = sorted(set(equivalents) - active_codes)
    if unknown:
        raise RuntimeError(
            f"{scenario_id}: equivalents {unknown} are not active in community "
            f"'{community['community_id']}' (active: {sorted(active_codes)})"
        )

    if equivalents != ["UAH"]:
        raise RuntimeError(
            f"{scenario_id}: no trustline survived the UAH-only model selection {equivalents}"
        )
    scenario_participants = []
    for p in community["participants"]:
        scenario_participants.append(
            {
                "id": scenario_participant_id(scenario_id, p["pid"]),
                "name": p["name"],
                "type": p["type"],
                "status": p["status"],
                "groupId": p["group"],
                "metadata": {"role": p["role"]} if "role" in p else {},
            }
        )
    scenario_participants.sort(key=lambda p: p["id"])
    model = economy_model(scenario_participants)
    profiles, profile_by_pid = behavior_profiles(model)
    for p in scenario_participants:
        if p["id"] in profile_by_pid:
            p["behaviorProfileId"] = profile_by_pid[p["id"]]
    by_pid = {p["id"]: p for p in scenario_participants}
    limits = {
        (a, b): Decimal(6 * amount_model(model["role_by_pid"][a])["p90"])
        for a, b in model["assigned_pairs"]
    }
    scenario_trustlines = [
        {
            "from": b,
            "to": a,
            "equivalent": "UAH",
            "limit": str(limits[a, b]),
            "policy": {
                "auto_clearing": True,
                "can_be_intermediate": by_pid[b]["type"] == "business",
            },
        }
        for a, b in model["assigned_pairs"]
    ]
    scenario_trustlines.sort(key=lambda t: (t["equivalent"], t["from"], t["to"]))

    # Every declared group, in the order the description declares them. The
    # validator already refuses a group with no participants, so there is
    # nothing here to filter out — and a filter that never filters would only
    # look like a check.
    groups = [{"id": g["id"], "label": g["label"]} for g in community["groups"]]

    return {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "name": scenario_id,
        "equivalents": list(equivalents),
        "participants": scenario_participants,
        "groups": groups,
        "behaviorProfiles": profiles,
        "trustlines": scenario_trustlines,
        "events": opening_events(model, limits) + _make_seasonal_stress_events(),
        "settings": _make_settings_realistic_v2(),
    }


def _validate_economy_child(path: Path) -> None:
    # Importing the planner constructs a runtime singleton. Keep that side effect
    # out of portable callers, whose tier deliberately removes database variables.
    task = "p050_t5002_generator_" + uuid.uuid4().hex
    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    env.update(
        ENV="test",
        DEBUG="false",
        DATABASE_URL=f"postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_{task}",
        SIMULATOR_STATE_DIR=str(ROOT / ".local-run" / "test-runs" / task / "state"),
    )
    try:
        child = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--validate-economy",
                str(path.resolve()),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Economy validation timed out for {path}") from exc
    if child.returncode:
        raise RuntimeError(
            f"Economy validation refused {path}:\n{child.stdout}{child.stderr}"
        )
    print(child.stdout, end="", file=sys.stderr)


def generate(spec: dict[str, Any], *, out_root: Path | None = None) -> Path:
    community = load_community(
        spec["community_id"], root=ROOT / "seeds" / "communities"
    )
    scenario = _scenario_from_community(
        scenario_id=spec["scenario_id"],
        community=community,
        equivalents=spec["equivalents"],
    )
    base = out_root if out_root is not None else ROOT / "fixtures" / "simulator"
    out_path = base / spec["scenario_id"] / "scenario.json"
    # Publish only after both checks; a refusal leaves any previous output intact.
    candidate = (
        ROOT
        / ".local-run"
        / "test-runs"
        / ("p050_t5002_generator_" + uuid.uuid4().hex)
        / "candidate.json"
    )
    _write_json(candidate, scenario)
    try:
        _validate_scenario_shape(candidate)
        _validate_economy_child(candidate)
        staged = out_path.with_name(".scenario-" + uuid.uuid4().hex + ".json")
        try:
            _write_json(staged, scenario)
            staged.replace(out_path)
        finally:
            staged.unlink(missing_ok=True)
    finally:
        candidate.unlink(missing_ok=True)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-root",
        type=Path,
        help="Output root; shape and economy validation are always required",
    )
    parser.add_argument("--validate-economy", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.validate_economy:
        from measure_scenario_economy import measure

        scenario = _read_json(args.validate_economy)
        reports, durations = [], []
        evidence = Path(os.environ["SIMULATOR_STATE_DIR"]).parent
        for seed in BALANCE_SEEDS:
            started = time.perf_counter()
            report = measure(scenario, ticks=BALANCE_TICKS, intensity=100, seed=seed)
            durations.append(time.perf_counter() - started)
            reports.append(report)
            _write_json(evidence / f"balance-{seed}.json", report)
        result = validate_economy_reports(scenario, reports)
        print(
            json.dumps(
                {
                    "scenario_id": scenario["scenario_id"],
                    "pooled_balance": result,
                    "plans_by_seed": [r["planned_actions"] for r in reports],
                    "seconds_by_seed": durations,
                    "evidence_root": str(evidence),
                    "approximate_amount_means": {
                        r: str(approximate_amount_mean(r)) for r in ROLE_FLOW
                    },
                    "amount_mean_absolute_error_bound_uah": ".005",
                }
            )
        )
        return 0
    for spec in SCENARIOS:
        print("Wrote", generate(spec, out_root=args.out_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
