#!/usr/bin/env python
"""Generate a realistic 100-participant seed for the Admin UI graph.

Writes canonical fixtures:
  admin-fixtures/v1/datasets/participants.json
  admin-fixtures/v1/datasets/equivalents.json (keeps UAH/EUR/HOUR)
  admin-fixtures/v1/datasets/trustlines.json
  admin-fixtures/v1/datasets/incidents.json

Run:
  python admin-fixtures/tools/generate_seed_greenfield_village_100.py

No external deps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATASETS_DIR = BASE_DIR / "v1" / "datasets"
BASE_TS = datetime(2026, 1, 11, 0, 0, 0, tzinfo=timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _q(value: Decimal, precision: int) -> str:
    quant = Decimal("1") if precision == 0 else Decimal("1").scaleb(-precision)
    return str(value.quantize(quant, rounding=ROUND_DOWN))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pid(idx: int) -> str:
    # Keep the familiar PID_U0001_xxxxxxxx format.
    h = (idx * 2654435761) % (2**32)
    return f"PID_U{idx:04d}_{h:08x}"


@dataclass(frozen=True)
class Participant:
    pid: str
    display_name: str
    type: str  # person | business
    status: str  # active | frozen


EQUIVALENTS = [
    {"code": "UAH", "precision": 2, "description": "Ukrainian Hryvnia", "is_active": True},
    {"code": "EUR", "precision": 2, "description": "Euro", "is_active": True},
    {"code": "HOUR", "precision": 2, "description": "Community time credit (hours)", "is_active": True},
]


def build_participants() -> list[Participant]:
    # Ordering matches the seed doc numbering (1..100).
    entries: list[tuple[str, str]] = []

    # 1) Anchors & infrastructure (10, business)
    entries += [
        ("GreenField Cooperative (Co-op)", "business"),
        ("GreenField Warehouse", "business"),
        ("GreenField Procurement Desk", "business"),
        ("Riverside Market Hall", "business"),
        ("Village Transport Co-op", "business"),
        ("SunnyHarvest Bakery", "business"),
        ("MeadowDairy Collective", "business"),
        ("Hilltop Greenhouses", "business"),
        ("Oak & Iron Workshop", "business"),
        ("Community Energy & Water Team", "business"),
    ]

    # 2) Producers & crafts (25, person)
    entries += [
        ("Anton Petrenko (Grain Farmer)", "person"),
        ("Maria Shevchenko (Vegetable Farmer)", "person"),
        ("Oksana Hrytsenko (Berry Farm)", "person"),
        ("Dmytro Kovalenko (Honey & Apiary)", "person"),
        ("Iryna Bondar (Eggs & Poultry)", "person"),
        ("Serhii Melnyk (Goat Cheese)", "person"),
        ("Yulia Tkachenko (Herbal Tea)", "person"),
        ("Viktor Koval (Sunflower Oil Press)", "person"),
        ("Kateryna Marchenko (Flour Milling)", "person"),
        ("Oleh Savchenko (Meat Processing)", "person"),
        ("Nina Romanenko (Canning & Pickles)", "person"),
        ("Taras Klymenko (Carpentry)", "person"),
        ("Alina Moroz (Sewing & Alterations)", "person"),
        ("Bohdan Kravets (Shoemaker)", "person"),
        ("Svitlana Poliakova (Pottery)", "person"),
        ("Denys Lysenko (Welding)", "person"),
        ("Hanna Sydorenko (Tailor Workshop)", "person"),
        ("Roman Zadorozhnyi (Firewood & Timber)", "person"),
        ("Pavlo Rudenko (Farm Equipment Rental)", "person"),
        ("Olena Fedorova (Seedlings & Plants)", "person"),
        ("Maksym Horbach (Compost & Soil)", "person"),
        ("Tetiana Kovalchuk (Bread Supplies)", "person"),
        ("Andrii Boiko (Fishing & Smoked Fish)", "person"),
        ("Larysa Ivashchenko (Handmade Soap)", "person"),
        ("Ihor Chernenko (Metal Parts)", "person"),
    ]

    # 3) Retail & food (10, business)
    entries += [
        ("Riverside Grocery", "business"),
        ("Village Butcher Shop", "business"),
        ("Morning Coffee Corner", "business"),
        ("Family Canteen", "business"),
        ("Farmers’ Corner Shop", "business"),
        ("HomeGoods Mini-Mart", "business"),
        ("Pharmacy & Health Kiosk", "business"),
        ("School Cafeteria", "business"),
        ("Weekend Street Food Stall", "business"),
        ("Cooperative Storefront", "business"),
    ]

    # 4) Services (15, person)
    entries += [
        ("Alex Turner (General Builder)", "person"),
        ("Ethan Brooks (Electrician)", "person"),
        ("Liam Carter (Plumber)", "person"),
        ("Noah Bennett (Mechanic)", "person"),
        ("Chloe Evans (Bicycle Repair)", "person"),
        ("Grace Collins (Accountant)", "person"),
        ("Ava Reed (Legal Advisor)", "person"),
        ("Mason Wright (IT Support)", "person"),
        ("Ella Morgan (Nurse)", "person"),
        ("Lucas Hayes (Tutor)", "person"),
        ("Mia Foster (Childcare)", "person"),
        ("Jack Russell (Courier)", "person"),
        ("Amelia Scott (Hairdresser)", "person"),
        ("Henry Ward (Photographer)", "person"),
        ("Sophia Kim (Translator)", "person"),
    ]

    # 5) Households (35, person)
    entries += [
        ("The Adams Family (Household)", "person"),
        ("The Baker Family (Household)", "person"),
        ("The Carter Family (Household)", "person"),
        ("The Davis Family (Household)", "person"),
        ("The Edwards Family (Household)", "person"),
        ("The Foster Family (Household)", "person"),
        ("The Garcia Family (Household)", "person"),
        ("The Harris Family (Household)", "person"),
        ("The Ivanov Family (Household)", "person"),
        ("The Johnson Family (Household)", "person"),
        ("The King Family (Household)", "person"),
        ("The Lewis Family (Household)", "person"),
        ("The Miller Family (Household)", "person"),
        ("The Nelson Family (Household)", "person"),
        ("The Owens Family (Household)", "person"),
        ("The Parker Family (Household)", "person"),
        ("The Quinn Family (Household)", "person"),
        ("The Robinson Family (Household)", "person"),
        ("The Smith Family (Household)", "person"),
        ("The Taylor Family (Household)", "person"),
        ("The Walker Family (Household)", "person"),
        ("The Young Family (Household)", "person"),
        ("Oliver Price (Odd Jobs)", "person"),
        ("Emily Price (Home Baking)", "person"),
        ("Daniel Hughes (Garden Help)", "person"),
        ("Lily Hughes (Babysitting)", "person"),
        ("Benjamin Lee (Repairs)", "person"),
        ("Isabella Lee (Sewing Help)", "person"),
        ("James Hill (Driving Help)", "person"),
        ("Charlotte Hill (Cooking Help)", "person"),
        ("William Green (Harvest Help)", "person"),
        ("Harper Green (Crafts)", "person"),
        ("Michael Wood (Handyman)", "person"),
        ("Evelyn Wood (Care Visits)", "person"),
        ("Samuel Clark (Errands)", "person"),
    ]

    # 6) Launch agents (5, person)
    entries += [
        ("Olivia Bennett (Community Coordinator)", "person"),
        ("Daniel Stone (Trustline Officer)", "person"),
        ("Emma Wright (Dispute Mediator)", "person"),
        ("Ryan Cooper (Operations Lead)", "person"),
        ("Sophia Turner (Tech Steward)", "person"),
    ]

    if len(entries) != 100:
        raise RuntimeError(f"Expected 100 participants, got {len(entries)}")

    # Small variety for UI states.
    frozen_indices = {8, 42, 55, 77, 99}  # 1-based indices

    participants: list[Participant] = []
    for idx, (name, ptype) in enumerate(entries, start=1):
        status = "frozen" if idx in frozen_indices else "active"
        participants.append(Participant(pid=_pid(idx), display_name=name, type=ptype, status=status))
    return participants


def _limit_for(eq: str, weight: int) -> Decimal:
    # Weight roughly corresponds to relationship strength.
    if eq == "HOUR":
        base = Decimal("3")
        step = Decimal("1")
        return base + step * Decimal(weight)
    # UAH/EUR
    base = Decimal("250")
    step = Decimal("150")
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

    # Key nodes
    coop = pid("GreenField Cooperative (Co-op)")
    warehouse = pid("GreenField Warehouse")
    procurement = pid("GreenField Procurement Desk")
    market = pid("Riverside Market Hall")
    transport = pid("Village Transport Co-op")
    bakery = pid("SunnyHarvest Bakery")
    dairy = pid("MeadowDairy Collective")
    grocery = pid("Riverside Grocery")
    canteen = pid("Family Canteen")
    it_support = pid("Mason Wright (IT Support)")
    translator = pid("Sophia Kim (Translator)")
    courier = pid("Jack Russell (Courier)")

    # Role slices by index in this seed
    producers = [p.pid for p in participants[10:35]]
    retail = [p.pid for p in participants[35:45]]
    services = [p.pid for p in participants[45:60]]
    households = [p.pid for p in participants[60:95]]
    agents = [p.pid for p in participants[95:100]]

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(from_pid: str, to_pid: str, eq: str, weight: int, n: int) -> None:
        key = (eq, from_pid, to_pid)
        if key in seen or from_pid == to_pid:
            return
        seen.add(key)

        precision = 2
        limit = _limit_for(eq, weight)
        used = _used_for(limit, n)
        if used > limit:
            used = limit
        available = limit - used

        status = "active"
        # If a participant is frozen, freeze most of its outgoing links.
        p_from = next(p for p in participants if p.pid == from_pid)
        if p_from.status == "frozen" and (n % 3 != 0):
            status = "frozen"

        created_at = _iso(BASE_TS - timedelta(days=(n % 90), minutes=n * 11))

        out.append(
            {
                "equivalent": eq,
                "from": from_pid,
                "to": to_pid,
                "from_display_name": pid_to_name.get(from_pid),
                "to_display_name": pid_to_name.get(to_pid),
                "limit": _q(limit, precision),
                "used": _q(used, precision),
                "available": _q(available, precision),
                "status": status,
                "created_at": created_at,
                "policy": {"auto_clearing": (n % 2 == 0), "can_be_intermediate": (n % 5 != 0)},
            }
        )

    n = 1

    # --- Hub cluster: co-op as the main connector
    for node in [warehouse, procurement, market, transport, bakery, dairy] + retail:
        add(coop, node, "UAH", weight=5, n=n); n += 1
        add(node, coop, "UAH", weight=4, n=n); n += 1

    # Producers connect to procurement + bakery + 1 retail outlet
    for i, pr in enumerate(producers):
        add(pr, procurement, "UAH", weight=4, n=n); n += 1
        add(procurement, pr, "UAH", weight=3, n=n); n += 1
        add(pr, bakery if i % 2 == 0 else grocery, "UAH", weight=2, n=n); n += 1

        # Light EUR connections for a subset (export-like)
        if i % 7 == 0:
            add(pr, coop, "EUR", weight=2, n=n); n += 1
            add(coop, pr, "EUR", weight=1, n=n); n += 1

    # Retail buys from producers (create cycles)
    for j, shop in enumerate(retail):
        pr = producers[(j * 3) % len(producers)]
        add(shop, pr, "UAH", weight=3, n=n); n += 1
        add(pr, shop, "UAH", weight=2, n=n); n += 1

    # Households spend weekly at grocery/canteen/market/coffee
    household_shops = [grocery, canteen, market] + retail[:2]
    for k, hh in enumerate(households):
        shop_a = household_shops[k % len(household_shops)]
        shop_b = household_shops[(k + 2) % len(household_shops)]
        add(hh, shop_a, "UAH", weight=2, n=n); n += 1
        add(hh, shop_b, "UAH", weight=1, n=n); n += 1

        # Some households offer HOUR to services (childcare, repairs)
        svc = services[k % len(services)]
        add(hh, svc, "HOUR", weight=1, n=n); n += 1
        add(svc, hh, "HOUR", weight=2, n=n); n += 1

    # Services link to anchors (maintenance contracts)
    for s, svc in enumerate(services):
        anchor = [coop, warehouse, bakery, dairy, transport][s % 5]
        add(svc, anchor, "UAH", weight=2, n=n); n += 1
        add(anchor, svc, "UAH", weight=2, n=n); n += 1

        # EUR for IT/translator as bridge
        if svc in (it_support, translator):
            add(svc, coop, "EUR", weight=3, n=n); n += 1
            add(coop, svc, "EUR", weight=2, n=n); n += 1

    # Transport/courier as bridges across clusters
    for i, node in enumerate([transport, courier]):
        add(node, market, "UAH", weight=3, n=n); n += 1
        add(node, grocery, "UAH", weight=3, n=n); n += 1
        add(node, bakery, "UAH", weight=2, n=n); n += 1

    # Agents connect to co-op (governance) with HOUR/UAH
    for a, ag in enumerate(agents):
        add(ag, coop, "HOUR", weight=2, n=n); n += 1
        add(coop, ag, "HOUR", weight=2, n=n); n += 1
        add(ag, coop, "UAH", weight=1, n=n); n += 1

    # Ensure a few explicit 3–6 node cycles (UAH)
    cycle_nodes = [
        (producers[0], bakery, services[0], producers[0]),
        (producers[3], grocery, services[3], producers[3]),
        (producers[7], canteen, services[7], producers[7]),
    ]
    for a, b, c, a2 in cycle_nodes:
        add(a, b, "UAH", weight=2, n=n); n += 1
        add(b, c, "UAH", weight=2, n=n); n += 1
        add(c, a2, "UAH", weight=2, n=n); n += 1

    return out


def build_incidents(participants: list[Participant]) -> dict[str, Any]:
    # Keep the same structure the admin-ui already supports: { items: [...] }
    pids = [p.pid for p in participants]
    return {
        "items": [
            {
                "tx_id": "TX_STUCK_SEED_0001",
                "state": "PREPARE_IN_PROGRESS",
                "initiator_pid": pids[0],
                "equivalent": "UAH",
                "age_seconds": 5400,
                "created_at": "2026-01-10T21:10:00Z",
                "sla_seconds": 1800,
            },
            {
                "tx_id": "TX_STUCK_SEED_0002",
                "state": "COMMIT_IN_PROGRESS",
                "initiator_pid": pids[5],
                "equivalent": "HOUR",
                "age_seconds": 2100,
                "created_at": "2026-01-10T23:30:00Z",
                "sla_seconds": 1800,
            },
            {
                "tx_id": "TX_STUCK_SEED_0003",
                "state": "PREPARE_IN_PROGRESS",
                "initiator_pid": pids[52],
                "equivalent": "EUR",
                "age_seconds": 4100,
                "created_at": "2026-01-10T20:55:00Z",
                "sla_seconds": 2400,
            },
        ]
    }


def main() -> None:
    participants = build_participants()
    trustlines = build_trustlines(participants)

    _write_json(DATASETS_DIR / "equivalents.json", EQUIVALENTS)
    _write_json(DATASETS_DIR / "participants.json", [p.__dict__ for p in participants])
    _write_json(DATASETS_DIR / "trustlines.json", trustlines)
    _write_json(DATASETS_DIR / "incidents.json", build_incidents(participants))

    print(f"Wrote participants: {len(participants)}")
    print(f"Wrote trustlines: {len(trustlines)}")


if __name__ == "__main__":
    main()
