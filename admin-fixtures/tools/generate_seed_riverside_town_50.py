#!/usr/bin/env python
"""Generate a compact 50-participant "Riverside Town" seed for the Admin UI graph.

Writes canonical fixtures:
  admin-fixtures/v1/datasets/participants.json
  admin-fixtures/v1/datasets/equivalents.json (keeps UAH/EUR/HOUR)
  admin-fixtures/v1/datasets/trustlines.json
  admin-fixtures/v1/datasets/incidents.json

Run:
  python admin-fixtures/tools/generate_seed_riverside_town_50.py

No external deps.

Semantics reminder:
  Trustline from → to means: from is creditor, to is debtor.

Design goals:
- Smaller network that is still "alive": person nodes have branched trustlines.
- Realistic roles: fishermen/service providers have multiple clients; households have
  small social microcredit + a few fish-share prepayment links.
- Keep EUR minimal.
- Deterministic output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
import uuid
import sys
import argparse

# Import shared seed helpers from this folder.
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from seedlib import (
    Participant,
    build_clearing_cycles_from_debts,
    build_debts_from_trustlines,
    build_meta,
    iso as _iso,
    pid as _pid,
    q as _q,
    write_json as _write_json,
)

from adminlib import write_common_admin_datasets


BASE_DIR = Path(__file__).resolve().parents[1]
V1_DIR = BASE_DIR / "v1"
DATASETS_DIR = BASE_DIR / "v1" / "datasets"
BASE_TS = datetime(2026, 1, 12, 0, 0, 0, tzinfo=timezone.utc)


EQUIVALENTS = [
    {"code": "UAH", "precision": 2, "description": "Ukrainian Hryvnia", "is_active": True},
    {"code": "EUR", "precision": 2, "description": "Euro", "is_active": True},
    {"code": "HOUR", "precision": 2, "description": "Community time credit (hours)", "is_active": True},
]


def build_participants() -> list[Participant]:
    # Ordering matches the seed doc numbering (1..50).
    entries: list[tuple[str, str]] = []

    # 1) Anchors & infrastructure (5, business)
    entries += [
        ("Riverside Fishing Co-operative", "business"),
        ("Fish Market & Cold Storage", "business"),
        ("River Port Warehouses", "business"),
        ("Town Community Center", "business"),
        ("Marina & Boat Services", "business"),
    ]

    # 2) Producers & fishing (10, person)
    entries += [
        ("Ivan Kozak (Fisherman)", "person"),
        ("Petro Rybka (Fisherman)", "person"),
        ("Nadia Morska (Smoking House)", "person"),
        ("Vasyl Stavok (Fish Farming)", "person"),
        ("Olha Vodiana (Water Plants)", "person"),
        ("Mykola Liman (Boat Building)", "person"),
        ("Hanna Berehova (Nets & Repair)", "person"),
        ("Serhii Richkovyi (River Guide)", "person"),
        ("Liudmyla Ozerna (Mussels)", "person"),
        ("Andriy Dnipro (Transport Boats)", "person"),
    ]

    # 3) Retail & food (8, business)
    entries += [
        ("Fresh Catch Fish Shop", "business"),
        ("River View Restaurant", "business"),
        ("Marina Café", "business"),
        ("Town Grocery", "business"),
        ("Fisherman's Supply Store", "business"),
        ("Riverside Pharmacy", "business"),
        ("Weekend Fish Market Stall", "business"),
        ("Cooperative Fish Counter", "business"),
    ]

    # 4) Services (10, person)
    entries += [
        ("Viktor Veslo (Boat Mechanic)", "person"),
        ("Natalia Seti (Net Repair)", "person"),
        ("Roman Motornyi (Engine Service)", "person"),
        ("Olena Kuhnia (Catering)", "person"),
        ("Dmytro Kholodny (Ice Supply)", "person"),
        ("Yana Rakhivna (Accountant)", "person"),
        ("Ihor Pravo (Legal Advisor)", "person"),
        ("Svitlana Medyk (Nurse)", "person"),
        ("Maksym Electro (Electrician)", "person"),
        ("Anna Turystka (Tourism Guide)", "person"),
    ]

    # 5) Households (15, person)
    entries += [
        ("The Rybchenko Family (Household)", "person"),
        ("The Moriak Family (Household)", "person"),
        ("The Berehovy Family (Household)", "person"),
        ("The Stavkovi Family (Household)", "person"),
        ("The Vodiani Family (Household)", "person"),
        ("The Richkovi Family (Household)", "person"),
        ("The Ozerni Family (Household)", "person"),
        ("The Limany Family (Household)", "person"),
        ("The Dnipro Family (Household)", "person"),
        ("The Prystan Family (Household)", "person"),
        ("The Zatoka Family (Household)", "person"),
        ("The Plyazh Family (Household)", "person"),
        ("The Khvylia Family (Household)", "person"),
        ("The Pryliv Family (Household)", "person"),
        ("The Ruslo Family (Household)", "person"),
    ]

    # 6) Launch agents (2, person)
    entries += [
        ("Marina Koordinator (Launch Agent)", "person"),
        ("Bohdan Dovira (Trustline Officer)", "person"),
    ]

    if len(entries) != 50:
        raise RuntimeError(f"Expected 50 participants, got {len(entries)}")

    frozen_indices = {21, 33}  # 1-based indices

    out: list[Participant] = []
    for idx, (name, ptype) in enumerate(entries, start=1):
        status = "frozen" if idx in frozen_indices else "active"
        out.append(Participant(pid=_pid(idx), display_name=name, type=ptype, status=status))
    return out


def _limit_for(eq: str, weight: int) -> Decimal:
    if eq == "HOUR":
        base = Decimal("3")
        step = Decimal("2")
        return base + step * Decimal(weight)

    if eq == "EUR":
        base = Decimal("80")
        step = Decimal("60")
        return base + step * Decimal(weight)

    # UAH
    base = Decimal("180")
    step = Decimal("140")
    return base + step * Decimal(weight)


def _used_for(limit: Decimal, n: int) -> Decimal:
    # Deterministic used ratio: 0..0.85
    ratio = Decimal((n % 17) + 1) / Decimal(20)
    ratio = min(ratio, Decimal("0.85"))
    return (limit * ratio).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN)


def build_trustlines(participants: list[Participant]) -> list[dict[str, Any]]:
    pid_to_name = {p.pid: p.display_name for p in participants}
    pid_by_name = {p.display_name: p.pid for p in participants}

    def pid(name: str) -> str:
        return pid_by_name[name]

    def add(
        out: list[dict[str, Any]],
        *,
        eq: str,
        from_pid: str,
        to_pid: str,
        weight: int,
        status: str = "active",
        auto_clearing: bool = False,
        can_be_intermediate: bool = True,
        ts_shift_min: int = 0,
        used_ratio: Decimal | None = None,
        force_bottleneck: bool = False,
    ) -> None:
        if from_pid == to_pid:
            return
        limit = _limit_for(eq, weight)

        if used_ratio is not None and limit != 0 and status == "active":
            used = (limit * used_ratio).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN)
        else:
            used = _used_for(limit, len(out) + 1)

        # Deterministic "bottleneck" cases so Dashboard shows examples under default threshold (0.10).
        # Only apply to active trustlines with a positive limit.
        if status == "active" and limit != 0 and (force_bottleneck or ((len(out) + 1) % 29 == 0)):
            used = (limit * Decimal("0.93")).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN)

        available = (limit - used).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN)
        created_at = _iso(BASE_TS - timedelta(minutes=5 * (len(out) + 1 + ts_shift_min)))
        out.append(
            {
                "equivalent": eq,
                "from": from_pid,
                "to": to_pid,
                "from_display_name": pid_to_name[from_pid],
                "to_display_name": pid_to_name[to_pid],
                "limit": _q(limit, 2),
                "used": _q(used, 2),
                "available": _q(available, 2),
                "status": status,
                "created_at": created_at,
                "policy": {"auto_clearing": bool(auto_clearing), "can_be_intermediate": bool(can_be_intermediate)},
            }
        )

    # Key nodes
    coop = pid("Riverside Fishing Co-operative")
    market = pid("Fish Market & Cold Storage")
    port = pid("River Port Warehouses")
    center = pid("Town Community Center")
    marina = pid("Marina & Boat Services")

    smoke = pid("Nadia Morska (Smoking House)")

    fish_shop = pid("Fresh Catch Fish Shop")
    restaurant = pid("River View Restaurant")
    cafe = pid("Marina Café")
    grocery = pid("Town Grocery")
    supply_store = pid("Fisherman's Supply Store")
    pharmacy = pid("Riverside Pharmacy")
    stall = pid("Weekend Fish Market Stall")
    coop_counter = pid("Cooperative Fish Counter")

    fisherman_ivan = pid("Ivan Kozak (Fisherman)")
    fisherman_petro = pid("Petro Rybka (Fisherman)")
    fisherman_vasyl = pid("Vasyl Stavok (Fish Farming)")

    boat_building = pid("Mykola Liman (Boat Building)")
    nets_repair_prod = pid("Hanna Berehova (Nets & Repair)")
    transport_boats = pid("Andriy Dnipro (Transport Boats)")

    mechanic = pid("Viktor Veslo (Boat Mechanic)")
    net_repair = pid("Natalia Seti (Net Repair)")
    engine = pid("Roman Motornyi (Engine Service)")
    catering = pid("Olena Kuhnia (Catering)")
    ice_supply = pid("Dmytro Kholodny (Ice Supply)")
    accountant = pid("Yana Rakhivna (Accountant)")
    legal = pid("Ihor Pravo (Legal Advisor)")
    nurse = pid("Svitlana Medyk (Nurse)")
    electrician = pid("Maksym Electro (Electrician)")
    tourism = pid("Anna Turystka (Tourism Guide)")

    agent1 = pid("Marina Koordinator (Launch Agent)")
    agent2 = pid("Bohdan Dovira (Trustline Officer)")

    households = [
        pid("The Rybchenko Family (Household)"),
        pid("The Moriak Family (Household)"),
        pid("The Berehovy Family (Household)"),
        pid("The Stavkovi Family (Household)"),
        pid("The Vodiani Family (Household)"),
        pid("The Richkovi Family (Household)"),
        pid("The Ozerni Family (Household)"),
        pid("The Limany Family (Household)"),
        pid("The Dnipro Family (Household)"),
        pid("The Prystan Family (Household)"),
        pid("The Zatoka Family (Household)"),
        pid("The Plyazh Family (Household)"),
        pid("The Khvylia Family (Household)"),
        pid("The Pryliv Family (Household)"),
        pid("The Ruslo Family (Household)"),
    ]

    tls: list[dict[str, Any]] = []

    # 1) Hub liquidity mesh (UAH)
    hub_pairs = [
        (coop, market, 6),
        (coop, port, 6),
        (market, port, 5),
        (coop, marina, 4),
        (port, marina, 3),
    ]
    for a, b, w in hub_pairs:
        add(tls, eq="UAH", from_pid=a, to_pid=b, weight=w, auto_clearing=True)
        add(tls, eq="UAH", from_pid=b, to_pid=a, weight=max(2, w - 1), auto_clearing=True)

    # 2) Producer → buyer supply lines (UAH)
    fishermen = [fisherman_ivan, fisherman_petro, fisherman_vasyl]

    # Fishermen sell to market and smokehouse on credit.
    for f in fishermen:
        add(tls, eq="UAH", from_pid=f, to_pid=market, weight=4)
        add(tls, eq="UAH", from_pid=f, to_pid=smoke, weight=3)

    # Additional producer roles feeding the system.
    add(tls, eq="UAH", from_pid=boat_building, to_pid=marina, weight=3)
    add(tls, eq="UAH", from_pid=boat_building, to_pid=fisherman_ivan, weight=2)
    add(tls, eq="UAH", from_pid=boat_building, to_pid=fisherman_petro, weight=2)

    add(tls, eq="UAH", from_pid=nets_repair_prod, to_pid=coop_counter, weight=2)
    add(tls, eq="UAH", from_pid=nets_repair_prod, to_pid=fisherman_vasyl, weight=2)

    add(tls, eq="UAH", from_pid=transport_boats, to_pid=port, weight=3)
    add(tls, eq="UAH", from_pid=transport_boats, to_pid=coop, weight=2)

    # Smokehouse sells onward.
    add(tls, eq="UAH", from_pid=smoke, to_pid=market, weight=4)
    add(tls, eq="UAH", from_pid=smoke, to_pid=fish_shop, weight=3)
    add(tls, eq="UAH", from_pid=smoke, to_pid=cafe, weight=2)
    add(tls, eq="UAH", from_pid=smoke, to_pid=coop, weight=2)

    # 3) Market → retail distribution (UAH)
    for buyer, w in [
        (fish_shop, 4),
        (restaurant, 3),
        (cafe, 2),
        (stall, 2),
        (coop_counter, 3),
    ]:
        add(tls, eq="UAH", from_pid=market, to_pid=buyer, weight=w)

    # Retail receives additional supply from hubs.
    add(tls, eq="UAH", from_pid=coop, to_pid=grocery, weight=3)
    add(tls, eq="UAH", from_pid=port, to_pid=supply_store, weight=3)
    add(tls, eq="UAH", from_pid=coop, to_pid=pharmacy, weight=2)

    # 4) Retail → household tabs (UAH)
    def pick_households(step: int, count: int) -> list[str]:
        # Deterministic selection without randomness.
        return [households[(i * step) % len(households)] for i in range(count)]

    for h in households:
        add(tls, eq="UAH", from_pid=fish_shop, to_pid=h, weight=2)

    for h in pick_households(step=2, count=12):
        add(tls, eq="UAH", from_pid=grocery, to_pid=h, weight=2)

    for h in pick_households(step=3, count=10):
        add(tls, eq="UAH", from_pid=pharmacy, to_pid=h, weight=2)

    for h in pick_households(step=4, count=8):
        add(tls, eq="UAH", from_pid=cafe, to_pid=h, weight=1)

    for h in pick_households(step=5, count=5):
        add(tls, eq="UAH", from_pid=restaurant, to_pid=h, weight=1)

    # 5) HOUR services: service → client (branched)
    for f in [fisherman_ivan, fisherman_petro, fisherman_vasyl, boat_building]:
        add(tls, eq="HOUR", from_pid=mechanic, to_pid=f, weight=6)
        add(tls, eq="HOUR", from_pid=net_repair, to_pid=f, weight=4)
        add(tls, eq="HOUR", from_pid=engine, to_pid=f, weight=5)

    # Nurse covers all households, but keep limits small.
    for h in households:
        add(tls, eq="HOUR", from_pid=nurse, to_pid=h, weight=3)

    # Electrician supports marina + some households.
    add(tls, eq="HOUR", from_pid=electrician, to_pid=marina, weight=4)
    for h in pick_households(step=2, count=6):
        add(tls, eq="HOUR", from_pid=electrician, to_pid=h, weight=2)

    # Catering: event tabs for some households.
    for h in pick_households(step=7, count=8):
        add(tls, eq="HOUR", from_pid=catering, to_pid=h, weight=3)

    # Ice supply supports market and a couple of fishing boats.
    add(tls, eq="HOUR", from_pid=ice_supply, to_pid=market, weight=3)
    add(tls, eq="HOUR", from_pid=ice_supply, to_pid=fisherman_ivan, weight=2)
    add(tls, eq="HOUR", from_pid=ice_supply, to_pid=fisherman_petro, weight=2)

    # Admin/professional services.
    add(tls, eq="HOUR", from_pid=accountant, to_pid=coop, weight=3)
    add(tls, eq="HOUR", from_pid=accountant, to_pid=restaurant, weight=2)
    add(tls, eq="HOUR", from_pid=legal, to_pid=coop, weight=2)
    add(tls, eq="HOUR", from_pid=legal, to_pid=port, weight=2)

    # 6) Social layer: household microcredit + fish-share prepayment
    # Households lend small UAH to neighbors (microcredit).
    for i, h in enumerate(households):
        h1 = households[(i + 1) % len(households)]
        h2 = households[(i + 4) % len(households)]
        add(tls, eq="UAH", from_pid=h, to_pid=h1, weight=1, can_be_intermediate=False)
        add(tls, eq="UAH", from_pid=h, to_pid=h2, weight=1, can_be_intermediate=False)

        # Mutual-help in hours (a single small HOUR line).
        h3 = households[(i + 2) % len(households)]
        add(tls, eq="HOUR", from_pid=h, to_pid=h3, weight=1, can_be_intermediate=False)

    # Fish-share: a subset of households prepays fishermen (household → fisherman).
    fish_share_households = pick_households(step=3, count=7)
    for idx, h in enumerate(fish_share_households):
        target = fishermen[idx % len(fishermen)]
        add(tls, eq="UAH", from_pid=h, to_pid=target, weight=2, can_be_intermediate=False)

    # Fishermen also do some direct-sale tabs to households.
    direct_sale = pick_households(step=4, count=9)
    for idx, h in enumerate(direct_sale):
        seller = fishermen[idx % len(fishermen)]
        add(tls, eq="UAH", from_pid=seller, to_pid=h, weight=1)

    # 7) Community center as an HOUR hub
    for h in pick_households(step=2, count=10):
        add(tls, eq="HOUR", from_pid=center, to_pid=h, weight=2)

    # Volunteer hours: households → community center (small, non-intermediate)
    for h in pick_households(step=5, count=7):
        add(tls, eq="HOUR", from_pid=h, to_pid=center, weight=1, can_be_intermediate=False)

    # 8) Launch agents
    for h in households:
        add(tls, eq="HOUR", from_pid=agent1, to_pid=h, weight=1, can_be_intermediate=False)

    for s in [mechanic, nurse, electrician, net_repair]:
        add(tls, eq="HOUR", from_pid=agent2, to_pid=s, weight=2, can_be_intermediate=False)

    # 9) Minimal EUR (tourism)
    add(tls, eq="EUR", from_pid=coop, to_pid=tourism, weight=2, can_be_intermediate=False)
    add(tls, eq="EUR", from_pid=tourism, to_pid=coop, weight=1, can_be_intermediate=False)

    # De-duplicate by (eq, from, to) keeping first occurrence.
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for t in tls:
        k = (t["equivalent"], t["from"], t["to"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)

    return deduped


def build_incidents(participants: list[Participant]) -> dict[str, Any]:
    pid_by_name = {p.display_name: p.pid for p in participants}

    coop = pid_by_name["Riverside Fishing Co-operative"]
    market = pid_by_name["Fish Market & Cold Storage"]
    tourism = pid_by_name["Anna Turystka (Tourism Guide)"]

    return {
        "items": [
            {
                "tx_id": "TX_STUCK_RIVER_0001",
                "state": "PREPARE_IN_PROGRESS",
                "initiator_pid": coop,
                "equivalent": "UAH",
                "age_seconds": 4200,
                "created_at": _iso(BASE_TS - timedelta(hours=3, minutes=20)),
                "sla_seconds": 1800,
            },
            {
                "tx_id": "TX_STUCK_RIVER_0002",
                "state": "COMMIT_IN_PROGRESS",
                "initiator_pid": market,
                "equivalent": "HOUR",
                "age_seconds": 2100,
                "created_at": _iso(BASE_TS - timedelta(hours=1, minutes=5)),
                "sla_seconds": 1800,
            },
            {
                "tx_id": "TX_STUCK_RIVER_0003",
                "state": "PREPARE_IN_PROGRESS",
                "initiator_pid": tourism,
                "equivalent": "EUR",
                "age_seconds": 3800,
                "created_at": _iso(BASE_TS - timedelta(hours=4, minutes=10)),
                "sla_seconds": 2400,
            },
        ]
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Riverside seed fixtures")
    p.add_argument(
        "--out-v1",
        type=str,
        default=str(V1_DIR),
        help="Output directory for fixture pack root (v1)",
    )
    return p.parse_args(argv)


def _tx_uuid(name: str) -> uuid.UUID:
    # Deterministic UUIDs for reproducible fixtures.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"geov0-admin-fixtures:{name}")


def build_transactions(
    *,
    participants: list[Participant],
    trustlines: list[dict[str, Any]],
    clearing_cycles: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate Transaction-like fixtures close to the backend Transaction model."""

    pids = [p.pid for p in participants]
    if not pids:
        return []

    def pick_pid(i: int, salt: int = 0) -> str:
        return pids[(i + salt) % len(pids)]

    def pick_other_pid(a: str, i: int) -> str:
        b = pick_pid(i, 17)
        if b == a:
            b = pick_pid(i, 29)
        return b

    eqs = [e["code"] for e in EQUIVALENTS]

    out: list[dict[str, Any]] = []

    # Payments: spread over time so Activity windows have signal.
    payment_count = 120
    for i in range(payment_count):
        sender = pick_pid(i, 3)
        receiver = pick_other_pid(sender, i)
        eq = eqs[i % len(eqs)]
        amount = _q(Decimal(5 + (i % 37) * 3), 2)

        created_at = BASE_TS - timedelta(days=(i % 96), hours=(i % 24), minutes=(i * 13) % 60)
        committed = (i % 11) != 0
        updated_at = created_at + timedelta(minutes=5 if committed else 2)

        tx_id = str(_tx_uuid(f"payment:{i}"))
        state = "COMMITTED" if committed else "ABORTED"

        out.append(
            {
                "id": tx_id,
                "tx_id": tx_id,
                "idempotency_key": f"idem_payment_{i:04d}" if (i % 5 == 0) else None,
                "type": "PAYMENT",
                "initiator_pid": sender,
                "payload": {
                    "from": sender,
                    "to": receiver,
                    "amount": str(amount),
                    "equivalent": eq,
                    "routes": [
                        {
                            "path": [sender, receiver],
                            "amount": str(amount),
                        }
                    ],
                    "idempotency": {
                        "key": f"idem_payment_{i:04d}",
                        "fingerprint": f"fp_payment_{i:04d}",
                    }
                    if (i % 5 == 0)
                    else None,
                },
                "signatures": [],
                "state": state,
                "error": {"code": "ABORTED", "message": "Simulated abort"} if state == "ABORTED" else None,
                "created_at": _iso(created_at),
                "updated_at": _iso(updated_at),
            }
        )

    # Clearings: take a subset of available cycles.
    cycles_by_eq: dict[str, list[list[dict[str, Any]]]] = {}
    try:
        for eq, entry in (clearing_cycles.get("equivalents") or {}).items():
            if not entry or not isinstance(entry, dict):
                continue
            cycles = entry.get("cycles")
            if isinstance(cycles, list):
                cycles_by_eq[str(eq)] = cycles
    except Exception:
        cycles_by_eq = {}

    clearing_target = 18
    clearing_idx = 0
    for eq, cycles in cycles_by_eq.items():
        for cycle in cycles:
            if clearing_idx >= clearing_target:
                break
            if not isinstance(cycle, list) or not cycle:
                continue

            initiator = str(cycle[0].get("debtor") or pick_pid(clearing_idx, 9))
            clear_amount = str(cycle[0].get("amount") or "1.00")

            created_at = BASE_TS - timedelta(days=(clearing_idx % 60), hours=(clearing_idx % 12), minutes=(clearing_idx * 19) % 60)
            updated_at = created_at + timedelta(minutes=3)

            tx_id = str(_tx_uuid(f"clearing:{clearing_idx}"))

            edges_payload: list[dict[str, Any]] = []
            for e in cycle:
                if not e or not isinstance(e, dict):
                    continue
                edges_payload.append(
                    {
                        "debtor": str(e.get("debtor") or ""),
                        "creditor": str(e.get("creditor") or ""),
                        "amount": str(e.get("amount") or ""),
                    }
                )

            out.append(
                {
                    "id": tx_id,
                    "tx_id": tx_id,
                    "idempotency_key": None,
                    "type": "CLEARING",
                    "initiator_pid": initiator,
                    "payload": {
                        "cycle": [f"debt_{clearing_idx:04d}_{j:02d}" for j in range(len(edges_payload))],
                        "amount": clear_amount,
                        "equivalent": eq,
                        "edges": edges_payload,
                    },
                    "signatures": [],
                    "state": "COMMITTED",
                    "error": None,
                    "created_at": _iso(created_at),
                    "updated_at": _iso(updated_at),
                }
            )
            clearing_idx += 1

        if clearing_idx >= clearing_target:
            break

    # Trustline ops.
    tl_ops = 40
    for i in range(tl_ops):
        t = trustlines[i % len(trustlines)] if trustlines else None
        if not t:
            break

        created_at = BASE_TS - timedelta(days=(i % 90), minutes=(i * 23) % 60)
        updated_at = created_at + timedelta(minutes=1)

        op_type = "TRUST_LINE_CREATE" if i % 3 == 0 else ("TRUST_LINE_UPDATE" if i % 3 == 1 else "TRUST_LINE_CLOSE")
        initiator = str(t.get("from") or pick_pid(i, 5))

        tx_id = str(_tx_uuid(f"trustline-op:{i}"))

        out.append(
            {
                "id": tx_id,
                "tx_id": tx_id,
                "idempotency_key": None,
                "type": op_type,
                "initiator_pid": initiator,
                "payload": {
                    "equivalent": str(t.get("equivalent") or ""),
                    "from": str(t.get("from") or ""),
                    "to": str(t.get("to") or ""),
                    "limit": str(t.get("limit") or ""),
                },
                "signatures": [],
                "state": "COMMITTED",
                "error": None,
                "created_at": _iso(created_at),
                "updated_at": _iso(updated_at),
            }
        )

    return out


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    out_v1 = Path(args.out_v1)
    datasets_dir = out_v1 / "datasets"

    participants = build_participants()
    trustlines = build_trustlines(participants)
    incidents = build_incidents(participants)
    debts = build_debts_from_trustlines(trustlines)
    clearing_cycles = build_clearing_cycles_from_debts(debts)
    transactions = build_transactions(participants=participants, trustlines=trustlines, clearing_cycles=clearing_cycles)

    _write_json(datasets_dir / "participants.json", [p.__dict__ for p in participants])
    _write_json(datasets_dir / "equivalents.json", EQUIVALENTS)
    _write_json(datasets_dir / "trustlines.json", trustlines)
    _write_json(datasets_dir / "incidents.json", incidents)
    _write_json(datasets_dir / "debts.json", debts)
    _write_json(datasets_dir / "clearing-cycles.json", clearing_cycles)
    _write_json(datasets_dir / "transactions.json", transactions)

    write_common_admin_datasets(datasets_dir=datasets_dir, participants=participants, base_ts=BASE_TS)
    _write_json(
        out_v1 / "_meta.json",
        build_meta(
            seed_id="riverside-town-50",
            generator="generate_seed_riverside_town_50.py",
            base_ts=BASE_TS,
            equivalents=EQUIVALENTS,
            participants=participants,
            trustlines=trustlines,
            incidents=incidents,
            debts=debts,
        ),
    )

    print(f"Wrote participants: {len(participants)}")
    print(f"Wrote trustlines: {len(trustlines)}")
    print("Wrote equivalents: 3")
    print(f"Wrote incidents: {len(incidents.get('items', []))}")
    print(f"Wrote debts: {len(debts)}")
    print(f"Wrote transactions: {len(transactions)}")


if __name__ == "__main__":
    main()
