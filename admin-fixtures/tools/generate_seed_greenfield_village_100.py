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
import uuid


BASE_DIR = Path(__file__).resolve().parents[1]
V1_DIR = BASE_DIR / "v1"
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
    butcher = pid("Village Butcher Shop")
    coffee = pid("Morning Coffee Corner")
    canteen = pid("Family Canteen")
    school_cafe = pid("School Cafeteria")
    pharmacy = pid("Pharmacy & Health Kiosk")
    storefront = pid("Cooperative Storefront")
    it_support = pid("Mason Wright (IT Support)")
    translator = pid("Sophia Kim (Translator)")
    courier = pid("Jack Russell (Courier)")

    # A few named producers for more realistic supplier graphs.
    grain_farmer = pid("Anton Petrenko (Grain Farmer)")
    flour_mill = pid("Kateryna Marchenko (Flour Milling)")
    eggs_poultry = pid("Iryna Bondar (Eggs & Poultry)")
    honey_apiary = pid("Dmytro Kovalenko (Honey & Apiary)")
    goat_cheese = pid("Serhii Melnyk (Goat Cheese)")
    meat_processing = pid("Oleh Savchenko (Meat Processing)")
    smoked_fish = pid("Andrii Boiko (Fishing & Smoked Fish)")
    veg_farmer = pid("Maria Shevchenko (Vegetable Farmer)")
    berry_farm = pid("Oksana Hrytsenko (Berry Farm)")

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

    # Producers connect to procurement + a few buyers.
    # Direction: producer -> buyer (seller extends credit / payment deferral).
    for i, pr in enumerate(producers):
        add(pr, procurement, "UAH", weight=4, n=n); n += 1

        # Some producers also sell directly at the market hall.
        if i % 3 == 0:
            add(pr, market, "UAH", weight=2, n=n); n += 1

        # A smaller subset sells to grocery directly.
        if i % 7 == 0:
            add(pr, grocery, "UAH", weight=2, n=n); n += 1

        # Occasionally procurement pre-finances producers (advance payments).
        if i % 3 == 0:
            add(procurement, pr, "UAH", weight=2, n=n); n += 1

    # Retail buys from producers.
    # Direction: producer -> retail (seller credit). Occasionally the reverse exists (advance payment).
    for j, shop in enumerate(retail):
        pr = producers[(j * 3) % len(producers)]
        add(pr, shop, "UAH", weight=3, n=n); n += 1
        if j % 4 == 0:
            add(shop, pr, "UAH", weight=1, n=n); n += 1

    # Anchor suppliers (few suppliers, many buyers)
    # Bakery suppliers: limited set + warehouse.
    for supplier in [grain_farmer, flour_mill, eggs_poultry, honey_apiary, warehouse]:
        add(supplier, bakery, "UAH", weight=4, n=n); n += 1

    # Dairy suppliers: limited set + warehouse.
    for supplier in [goat_cheese, grain_farmer, berry_farm, warehouse]:
        add(supplier, dairy, "UAH", weight=3, n=n); n += 1

    # Grocery and butcher: suppliers are few but stable.
    for supplier in [warehouse, procurement, bakery, dairy, veg_farmer]:
        add(supplier, grocery, "UAH", weight=3, n=n); n += 1
    for supplier in [meat_processing, eggs_poultry, smoked_fish, warehouse]:
        add(supplier, butcher, "UAH", weight=3, n=n); n += 1

    # Canteen / school cafeteria: buy from a few food suppliers.
    for supplier in [bakery, dairy, grocery]:
        add(supplier, canteen, "UAH", weight=2, n=n); n += 1
        add(supplier, school_cafe, "UAH", weight=2, n=n); n += 1

    # Co-op storefront sources inventory from the co-op/warehouse.
    for supplier in [coop, warehouse]:
        add(supplier, storefront, "UAH", weight=3, n=n); n += 1

    # Households spend weekly.
    # Direction: seller -> household (buy now / pay later).
    # Make grocery/market dominant (many buyers), and distribute the second line across other shops.
    secondary_shops = [canteen, bakery, dairy, coffee, pharmacy, butcher, storefront, school_cafe]
    for k, hh in enumerate(households):
        # Everyone uses grocery; most also use the market.
        add(grocery, hh, "UAH", weight=2, n=n); n += 1
        if k % 2 == 0:
            add(market, hh, "UAH", weight=1, n=n); n += 1

        shop = secondary_shops[k % len(secondary_shops)]
        add(shop, hh, "UAH", weight=1, n=n); n += 1

        # HOUR economy: services often work now and settle later.
        svc = services[k % len(services)]
        add(svc, hh, "HOUR", weight=2, n=n); n += 1
        if k % 5 == 0:
            add(hh, svc, "HOUR", weight=1, n=n); n += 1

    # Micro-work (odd jobs) creates realistic cycles: household -> producer (UAH)
    # Direction: worker -> buyer of work (work now / pay later).
    micro_workers = households[22:]
    for i, hw in enumerate(micro_workers):
        add(hw, producers[(i * 2) % len(producers)], "UAH", weight=1, n=n); n += 1

    # Social layer: person-to-person branching (small limits)
    # Goal: avoid the "chain of residents" effect; add realistic neighborhood microcredit + prepayment links.
    # Keep these small and often non-intermediate to avoid distorting the main liquidity picture.

    def add_social(from_pid: str, to_pid: str, eq: str, weight: int, n_local: int) -> None:
        # Same as add(), but with a stricter policy for intermediate routing.
        key = (eq, from_pid, to_pid)
        if key in seen or from_pid == to_pid:
            return
        seen.add(key)

        precision = 2
        limit = _limit_for(eq, weight)
        used = _used_for(limit, n_local)
        if used > limit:
            used = limit
        available = limit - used

        status = "active"
        p_from = next(p for p in participants if p.pid == from_pid)
        if p_from.status == "frozen" and (n_local % 3 != 0):
            status = "frozen"

        created_at = _iso(BASE_TS - timedelta(days=(n_local % 90), minutes=n_local * 11))

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
                "policy": {"auto_clearing": (n_local % 2 == 0), "can_be_intermediate": False},
            }
        )

    # Household ↔ household neighbor microcredit (UAH) + a small HOUR mutual-help link.
    for i, hh in enumerate(households):
        hh1 = households[(i + 1) % len(households)]
        hh2 = households[(i + 4) % len(households)]
        add_social(hh, hh1, "UAH", weight=1, n_local=n); n += 1
        add_social(hh, hh2, "UAH", weight=1, n_local=n); n += 1

        hh3 = households[(i + 2) % len(households)]
        add_social(hh, hh3, "HOUR", weight=1, n_local=n); n += 1

    # Prepayment / CSA-style support: a subset of households prepays producers (household -> producer, UAH).
    # This adds realistic branching and cycles without making households major creditors.
    for i, hh in enumerate(households[::5]):
        pr = producers[(i * 3) % len(producers)]
        add_social(hh, pr, "UAH", weight=1, n_local=n); n += 1

    # Some producers occasionally do direct-sale tabs to households (producer -> household, UAH).
    for i, hh in enumerate(households[::4]):
        pr = producers[(i * 5) % len(producers)]
        add(pr, hh, "UAH", weight=1, n=n); n += 1

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

    # Agents connect to co-op (governance) with HOUR/UAH
    for a, ag in enumerate(agents):
        add(ag, coop, "HOUR", weight=2, n=n); n += 1
        add(coop, ag, "HOUR", weight=2, n=n); n += 1
        add(ag, coop, "UAH", weight=1, n=n); n += 1

    # Ensure a few explicit 3-node cycles (UAH) aligned with the economic model:
    # supplier(producer) -> buyer(business) -> household -> supplier(producer)
    cycle_nodes = [
        (flour_mill, bakery, micro_workers[0]),
        (meat_processing, butcher, micro_workers[4] if len(micro_workers) > 4 else micro_workers[0]),
        (veg_farmer, market, micro_workers[8] if len(micro_workers) > 8 else micro_workers[0]),
    ]
    for pr, shop, hw in cycle_nodes:
        add(pr, shop, "UAH", weight=2, n=n); n += 1
        add(shop, hw, "UAH", weight=2, n=n); n += 1
        add(hw, pr, "UAH", weight=2, n=n); n += 1

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


def build_debts_from_trustlines(trustlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive debt edges from trustline `used`.

    Semantics: trustline from->to means creditor->debtor, therefore used represents a debt:
      debtor = to
      creditor = from
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for t in trustlines:
        eq = str(t.get("equivalent") or "")
        debtor = str(t.get("to") or "")
        creditor = str(t.get("from") or "")
        used_raw = str(t.get("used") or "0")
        try:
            used = Decimal(used_raw)
        except Exception:
            continue
        if used <= 0:
            continue

        key = (eq, debtor, creditor)
        if key in seen:
            continue
        seen.add(key)
        out.append({"equivalent": eq, "debtor": debtor, "creditor": creditor, "amount": used_raw})
    return out


def build_clearing_cycles_from_debts(
    debts: list[dict[str, Any]],
    *,
    max_cycles_per_equivalent: int = 8,
) -> dict[str, Any]:
    """Find a few short (3-edge) debt cycles for UI prototyping.

    Output shape mirrors the API response family:
      { equivalents: { CODE: { cycles: [ [edge, edge, edge], ... ] } } }
    """
    # Group debts by equivalent.
    by_eq: dict[str, list[dict[str, Any]]] = {}
    for d in debts:
        eq = str(d.get("equivalent") or "")
        if not eq:
            continue
        by_eq.setdefault(eq, []).append(d)

    result: dict[str, Any] = {"equivalents": {}}
    for eq in sorted(by_eq.keys()):
        edges = by_eq[eq]

        # adjacency[debtor] = list of (creditor, amount_str)
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for e in edges:
            debtor = str(e.get("debtor") or "")
            creditor = str(e.get("creditor") or "")
            amount = str(e.get("amount") or "0")
            if not debtor or not creditor or debtor == creditor:
                continue
            adjacency.setdefault(debtor, []).append((creditor, amount))

        for k in list(adjacency.keys()):
            adjacency[k] = sorted(adjacency[k], key=lambda x: (x[0], x[1]))

        # Find unique triangles A->B, B->C, C->A.
        cycles: list[list[dict[str, Any]]] = []
        seen_cycles: set[tuple[str, str, str]] = set()

        nodes = sorted(adjacency.keys())
        for a in nodes:
            if len(cycles) >= max_cycles_per_equivalent:
                break
            for b, ab_amt in adjacency.get(a, []):
                if b == a:
                    continue
                for c, bc_amt in adjacency.get(b, []):
                    if c in (a, b):
                        continue
                    # Check c -> a
                    ca_edges = adjacency.get(c, [])
                    ca_amt = None
                    for nxt, amt in ca_edges:
                        if nxt == a:
                            ca_amt = amt
                            break
                    if ca_amt is None:
                        continue

                    # Canonicalize by sorting node ids (triangle as a set with stable rotation).
                    tri = tuple(sorted([a, b, c]))
                    if tri in seen_cycles:
                        continue
                    seen_cycles.add(tri)

                    cycles.append(
                        [
                            {"debtor": a, "creditor": b, "equivalent": eq, "amount": ab_amt},
                            {"debtor": b, "creditor": c, "equivalent": eq, "amount": bc_amt},
                            {"debtor": c, "creditor": a, "equivalent": eq, "amount": ca_amt},
                        ]
                    )
                    if len(cycles) >= max_cycles_per_equivalent:
                        break
                if len(cycles) >= max_cycles_per_equivalent:
                    break

        result["equivalents"][eq] = {"cycles": cycles}

    return result


def build_meta(*, participants: list[Participant], trustlines: list[dict[str, Any]], incidents: dict[str, Any], debts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": "v1",
        "generated_at": _iso(BASE_TS),
        "counts": {
            "participants": len(participants),
            "equivalents": len(EQUIVALENTS),
            "trustlines": len(trustlines),
            "incidents": len(list(incidents.get("items") or [])),
            "debts": len(debts),
        },
        "notes": [
            "TrustLine direction is creditor -> debtor (from -> to).",
            "Debt direction is debtor -> creditor (derived from trustline.used).",
            f"Timestamps are fixed relative to {_iso(BASE_TS)}.",
        ],
    }


def _tx_uuid(name: str) -> uuid.UUID:
    # Deterministic UUIDs for reproducible fixtures.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"geov0-admin-fixtures:{name}")


def build_transactions(
    *,
    participants: list[Participant],
    trustlines: list[dict[str, Any]],
    clearing_cycles: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate Transaction-like fixtures close to the backend Transaction model.

    Backend reference: app/db/models/transaction.py
    - tx_id is a UUID string
    - type: PAYMENT / CLEARING / TRUST_LINE_* ...
    - state: NEW / ... / COMMITTED / ABORTED
    - payload contains payment/clearing details

    In fixtures we reference participants by pid (not UUID id).
    """

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

    # Payments: a spread over 0..95 days so Activity windows have signal.
    payment_count = 180
    for i in range(payment_count):
        sender = pick_pid(i, 3)
        receiver = pick_other_pid(sender, i)
        eq = eqs[i % len(eqs)]
        amount = _q(Decimal(5 + (i % 37) * 3), 2)

        created_at = BASE_TS - timedelta(days=(i % 96), hours=(i % 24), minutes=(i * 13) % 60)
        committed = (i % 11) != 0  # some aborted
        updated_at = created_at + timedelta(minutes=5 if committed else 2)

        tx_uuid = _tx_uuid(f"payment:{i}")
        tx_id = str(tx_uuid)
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

    # Clearings: take a subset of available cycles (if any), and emit committed tx.
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

    clearing_target = 24
    clearing_idx = 0
    for eq, cycles in cycles_by_eq.items():
        for cycle in cycles:
            if clearing_idx >= clearing_target:
                break
            if not isinstance(cycle, list) or not cycle:
                continue

            # Derive participants from edges.
            initiator = str(cycle[0].get("debtor") or pick_pid(clearing_idx, 9))
            clear_amount = str(cycle[0].get("amount") or "1.00")

            created_at = BASE_TS - timedelta(days=(clearing_idx % 60), hours=(clearing_idx % 12), minutes=(clearing_idx * 19) % 60)
            updated_at = created_at + timedelta(minutes=3)

            tx_uuid = _tx_uuid(f"clearing:{clearing_idx}")
            tx_id = str(tx_uuid)

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

    # Trustline ops: include a handful to mirror backend transaction types.
    tl_ops = 60
    for i in range(tl_ops):
        t = trustlines[i % len(trustlines)] if trustlines else None
        if not t:
            break
        created_at = BASE_TS - timedelta(days=(i % 90), minutes=(i * 23) % 60)
        updated_at = created_at + timedelta(minutes=1)

        op_type = "TRUST_LINE_CREATE" if i % 3 == 0 else ("TRUST_LINE_UPDATE" if i % 3 == 1 else "TRUST_LINE_CLOSE")
        initiator = str(t.get("from") or pick_pid(i, 5))

        tx_uuid = _tx_uuid(f"trustline-op:{i}")
        tx_id = str(tx_uuid)

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


def main() -> None:
    participants = build_participants()
    trustlines = build_trustlines(participants)
    incidents = build_incidents(participants)
    debts = build_debts_from_trustlines(trustlines)
    clearing_cycles = build_clearing_cycles_from_debts(debts)
    transactions = build_transactions(participants=participants, trustlines=trustlines, clearing_cycles=clearing_cycles)

    _write_json(DATASETS_DIR / "equivalents.json", EQUIVALENTS)
    _write_json(DATASETS_DIR / "participants.json", [p.__dict__ for p in participants])
    _write_json(DATASETS_DIR / "trustlines.json", trustlines)
    _write_json(DATASETS_DIR / "incidents.json", incidents)
    _write_json(DATASETS_DIR / "debts.json", debts)
    _write_json(DATASETS_DIR / "clearing-cycles.json", clearing_cycles)
    _write_json(DATASETS_DIR / "transactions.json", transactions)
    _write_json(V1_DIR / "_meta.json", build_meta(participants=participants, trustlines=trustlines, incidents=incidents, debts=debts))

    print(f"Wrote participants: {len(participants)}")
    print(f"Wrote trustlines: {len(trustlines)}")
    print(f"Wrote debts: {len(debts)}")
    print(f"Wrote transactions: {len(transactions)}")


if __name__ == "__main__":
    main()
