#!/usr/bin/env python
"""Generate a more realistic Greenfield (100) seed v2 for Admin UI + DB seeding.

Goals vs v1:
- Keep persons (households/services/producers) from becoming routing intermediates.
- Make auto-clearing the default so obligations are reduced quickly.
- Reduce initial UAH debts for persons (used ratios), while allowing larger business balances.

Writes fixture pack compatible with admin-fixtures/tools/generate_fixtures.py.

Usage:
  ./.venv/Scripts/python.exe admin-fixtures/tools/generate_seed_greenfield_village_100_v2.py --out-v1 admin-fixtures/v1

No external deps.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from datetime import timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
import sys

# Import shared seed helpers from this folder.
TOOLS_DIR = Path(__file__).resolve().parent

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from seedlib import build_clearing_cycles_from_debts, build_debts_from_trustlines, build_meta, write_json as _write_json

from adminlib import write_common_admin_datasets


BASE_DIR = Path(__file__).resolve().parents[1]
V1_DIR = BASE_DIR / "v1"
BASE_TS = __import__("datetime").datetime(2026, 2, 2, 0, 0, 0, tzinfo=timezone.utc)


EQUIVALENTS = [
    {"code": "UAH", "precision": 2, "description": "Ukrainian Hryvnia", "is_active": True},
    {"code": "EUR", "precision": 2, "description": "Euro", "is_active": True},
    {"code": "HOUR", "precision": 2, "description": "Community time credit (hours)", "is_active": True},
]


def _u01(key: str) -> float:
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def _load_base_module():
    path = TOOLS_DIR / "generate_seed_greenfield_village_100.py"
    spec = importlib.util.spec_from_file_location("geo_seed_greenfield_village_100_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load seed module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pid_index(pid: str) -> int | None:
    import re

    m = re.match(r"^PID_U(\d{4})_", str(pid))
    if not m:
        return None
    return int(m.group(1))


def _is_household_pid(pid: str, *, pid_to_name: dict[str, str]) -> bool:
    name = str(pid_to_name.get(pid, ""))
    return "(Household)" in name


def _is_business_pid(pid: str, *, pid_to_type: dict[str, str]) -> bool:
    return str(pid_to_type.get(pid, "")).lower() == "business"


def _quantize_money(x: Decimal) -> str:
    return format(x.quantize(Decimal("0.01"), rounding=ROUND_DOWN), "f")


def _transform_trustlines(
    trustlines: list[dict[str, Any]],
    *,
    pid_to_type: dict[str, str],
    pid_to_name: dict[str, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    person_pids = {pid for pid, t in pid_to_type.items() if str(t).lower() == "person"}
    business_pids = {pid for pid, t in pid_to_type.items() if str(t).lower() == "business"}
    household_pids = {pid for pid in person_pids if _is_household_pid(pid, pid_to_name=pid_to_name)}

    incoming_by_person: dict[str, list[int]] = {}
    outgoing_by_person: dict[str, list[int]] = {}
    for i, t in enumerate(trustlines):
        eq = str(t.get("equivalent") or "").strip().upper() or "UAH"
        if eq != "UAH":
            continue
        fp = str(t.get("from"))
        tp = str(t.get("to"))
        if tp in person_pids:
            incoming_by_person.setdefault(tp, []).append(i)
        if fp in person_pids:
            outgoing_by_person.setdefault(fp, []).append(i)

    primary_tl_index_by_person: dict[str, int] = {}
    target_balance_by_person: dict[str, Decimal] = {}
    target_limit_by_person: dict[str, Decimal] = {}

    def _pick_primary(pid: str, candidates: list[int]) -> int | None:
        if not candidates:
            return None
        best_i = None
        best_k = None
        for idx in candidates:
            t = trustlines[idx]
            fp = str(t.get("from"))
            tp = str(t.get("to"))
            k = _u01(f"primary|{pid}|{idx}|{fp}|{tp}")
            if best_k is None or k < best_k:
                best_k = k
                best_i = idx
        return best_i

    for pid in sorted(person_pids):
        bal = Decimal("500") + (Decimal("2500") * Decimal(str(_u01(f"bal|{pid}"))))
        bal = bal.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        lim = (bal * Decimal("1.25")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        lim = min(Decimal("4000"), max(Decimal("800"), lim))
        target_balance_by_person[pid] = bal
        target_limit_by_person[pid] = lim

        if pid in household_pids:
            cand = [i for i in incoming_by_person.get(pid, []) if str(trustlines[i].get("from")) in business_pids]
            cand = cand or incoming_by_person.get(pid, [])
        else:
            cand = [i for i in outgoing_by_person.get(pid, []) if str(trustlines[i].get("to")) in business_pids]
            cand = cand or outgoing_by_person.get(pid, []) or incoming_by_person.get(pid, [])

        picked = _pick_primary(pid, cand)
        if picked is not None:
            primary_tl_index_by_person[pid] = picked

    for i, t in enumerate(trustlines):
        eq = str(t.get("equivalent") or "").strip().upper() or "UAH"
        fp = str(t.get("from"))
        tp = str(t.get("to"))

        from_is_business = _is_business_pid(fp, pid_to_type=pid_to_type)
        to_is_business = _is_business_pid(tp, pid_to_type=pid_to_type)

        can_be_intermediate = bool(from_is_business and to_is_business)
        auto_clearing = True

        policy = dict((t.get("policy") or {}) if isinstance(t.get("policy"), dict) else {})
        policy["can_be_intermediate"] = can_be_intermediate
        policy["auto_clearing"] = auto_clearing

        limit = Decimal(str(t.get("limit") or "0"))
        used = Decimal(str(t.get("used") or "0"))

        if eq == "UAH":
            k = _u01(f"v2|{eq}|{fp}|{tp}")

            person_primary_pid: str | None = None
            if fp in person_pids and primary_tl_index_by_person.get(fp) == i:
                person_primary_pid = fp
            elif tp in person_pids and primary_tl_index_by_person.get(tp) == i:
                person_primary_pid = tp

            if person_primary_pid is not None:
                used = target_balance_by_person[person_primary_pid]
                limit = max(target_limit_by_person[person_primary_pid], used + Decimal("50"))
            elif (fp in person_pids) and (tp in person_pids):
                lim_min = Decimal("150")
                lim_max = Decimal("900")
                limit = (lim_min + (lim_max - lim_min) * Decimal(str(k))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
                used = (Decimal("10") + Decimal("90") * Decimal(str(_u01(f"used|p2p|{fp}|{tp}")))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            elif (fp in person_pids) or (tp in person_pids):
                lim_min = Decimal("400")
                lim_max = Decimal("3000")
                limit = (lim_min + (lim_max - lim_min) * Decimal(str(k))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
                used = (Decimal("0") + Decimal("120") * Decimal(str(_u01(f"used|small|{fp}|{tp}")))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            else:
                lim_min = Decimal("1500")
                lim_max = Decimal("12000")
                limit = (lim_min + (lim_max - lim_min) * Decimal(str(k))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
                used_ratio = Decimal("0.25") + Decimal("0.45") * Decimal(str(_u01(f"used|b2b|{fp}|{tp}")))
                used = (limit * used_ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

            if used < 0:
                used = Decimal("0")
            if used > limit:
                used = limit

        available = (limit - used).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        tt = dict(t)
        tt["limit"] = _quantize_money(limit)
        tt["used"] = _quantize_money(used)
        tt["available"] = _quantize_money(available)
        tt["policy"] = policy
        out.append(tt)

    return out


def _transform_transactions(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for tx in transactions:
        if str(tx.get("type") or "").upper() != "PAYMENT":
            out.append(tx)
            continue

        payload = tx.get("payload") if isinstance(tx.get("payload"), dict) else None
        if not payload or str(payload.get("equivalent") or "").upper() != "UAH":
            out.append(tx)
            continue

        key = (
            str(tx.get("tx_id") or "")
            or str(tx.get("id") or "")
            or str(tx.get("idempotency_key") or "")
            or str(((payload.get("idempotency") or {}) if isinstance(payload.get("idempotency"), dict) else {}).get("key") or "")
        )
        k = _u01(f"txamt|{key}")
        amount = (Decimal("50") + Decimal("1450") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        payload2 = dict(payload)
        payload2["amount"] = _quantize_money(amount)
        routes = payload2.get("routes")
        if isinstance(routes, list):
            new_routes = []
            for r in routes:
                if not isinstance(r, dict):
                    new_routes.append(r)
                    continue
                rr = dict(r)
                rr["amount"] = _quantize_money(amount)
                new_routes.append(rr)
            payload2["routes"] = new_routes

        tx2 = dict(tx)
        tx2["payload"] = payload2
        out.append(tx2)

    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Greenfield seed fixtures (v2)")
    p.add_argument(
        "--out-v1",
        type=str,
        default=str(V1_DIR),
        help="Output directory for fixture pack root (v1)",
    )
    return p.parse_args(argv)


def build_participants():
    base = _load_base_module()
    return base.build_participants()


def build_trustlines(participants) -> list[dict[str, Any]]:
    base = _load_base_module()
    pid_to_type = {p.pid: p.type for p in participants}
    pid_to_name = {p.pid: p.display_name for p in participants}
    trustlines_v1 = base.build_trustlines(participants)
    return _transform_trustlines(trustlines_v1, pid_to_type=pid_to_type, pid_to_name=pid_to_name)


def build_incidents(participants):
    base = _load_base_module()
    return base.build_incidents(participants)


def build_transactions(*, participants, trustlines, clearing_cycles):
    base = _load_base_module()
    txs = base.build_transactions(participants=participants, trustlines=trustlines, clearing_cycles=clearing_cycles)
    return _transform_transactions(txs)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    out_v1 = Path(args.out_v1)
    datasets_dir = out_v1 / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    participants = build_participants()
    trustlines = build_trustlines(participants)
    incidents = build_incidents(participants)
    debts = build_debts_from_trustlines(trustlines)
    clearing_cycles = build_clearing_cycles_from_debts(debts, max_cycles_per_equivalent=8)
    transactions = build_transactions(participants=participants, trustlines=trustlines, clearing_cycles=clearing_cycles)

    _write_json(datasets_dir / "equivalents.json", EQUIVALENTS)
    _write_json(datasets_dir / "participants.json", [p.__dict__ for p in participants])
    _write_json(datasets_dir / "trustlines.json", trustlines)
    _write_json(datasets_dir / "incidents.json", incidents)
    _write_json(datasets_dir / "debts.json", debts)
    _write_json(datasets_dir / "clearing-cycles.json", clearing_cycles)
    _write_json(datasets_dir / "transactions.json", transactions)

    write_common_admin_datasets(datasets_dir=datasets_dir, participants=participants, base_ts=BASE_TS)

    _write_json(
        out_v1 / "_meta.json",
        build_meta(
            seed_id="greenfield-village-100-v2",
            generator="generate_seed_greenfield_village_100_v2.py",
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
    print(f"Wrote debts: {len(debts)}")
    print(f"Wrote transactions: {len(transactions)}")


if __name__ == "__main__":
    main()
