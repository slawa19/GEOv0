#!/usr/bin/env python
"""Generate a more realistic Riverside Town (50) seed v2 for Admin UI + DB seeding.

Goals vs v1:
- Persons should not start with huge UAH debts (cap-ish via used ratio).
- Business↔business can have higher limits; person-involved links are capped.
- Fast clearing: set trustline.policy.auto_clearing = true.
- Routing intermediates: only allow business↔business.

This script is deterministic and dependency-free.

Usage:
  ./.venv/Scripts/python.exe admin-fixtures/tools/generate_seed_riverside_town_50_v2.py --out-v1 admin-fixtures/v1
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from seedlib import build_clearing_cycles_from_debts, build_debts_from_trustlines, build_meta, write_json as _write_json
from adminlib import write_common_admin_datasets


BASE_DIR = Path(__file__).resolve().parents[1]
V1_DIR = BASE_DIR / "v1"
BASE_TS = datetime(2026, 2, 2, 0, 0, 0, tzinfo=timezone.utc)


EQUIVALENTS = [
    {"code": "UAH", "precision": 2, "description": "Ukrainian Hryvnia", "is_active": True},
    {"code": "EUR", "precision": 2, "description": "Euro", "is_active": True},
    {"code": "HOUR", "precision": 2, "description": "Community time credit (hours)", "is_active": True},
]


def _u01(key: str) -> float:
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def _load_base_module():
    path = TOOLS_DIR / "generate_seed_riverside_town_50.py"
    spec = importlib.util.spec_from_file_location("geo_seed_riverside_town_50_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load seed module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _is_business_pid(pid: str, *, pid_to_type: dict[str, str]) -> bool:
    return str(pid_to_type.get(pid, "")).lower() == "business"


def _is_household_pid(pid: str, *, pid_to_name: dict[str, str]) -> bool:
    return "(Household)" in str(pid_to_name.get(pid, ""))


def _quantize_money(x: Decimal) -> str:
    return format(x.quantize(Decimal("0.01"), rounding=ROUND_DOWN), "f")


def _transform_trustlines(
    trustlines: list[dict[str, Any]],
    *,
    pid_to_type: dict[str, str],
    pid_to_name: dict[str, str],
) -> list[dict[str, Any]]:
    # We want person balances to look realistic (UAH-only):
    # - each person has a single primary UAH "balance" (credit or debt) in ~500..3000
    # - other person-involved links have small used values (so totals don't explode)
    # - trustline limits are sized to comfortably contain that used
    # - do NOT modify non-UAH trustlines
    out: list[dict[str, Any]] = []

    person_pids = {pid for pid, t in pid_to_type.items() if str(t).lower() == "person"}
    business_pids = {pid for pid, t in pid_to_type.items() if str(t).lower() == "business"}
    household_pids = {pid for pid in person_pids if _is_household_pid(pid, pid_to_name=pid_to_name)}

    # Pre-index UAH trustlines per person to pick a deterministic primary edge.
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
        if eq != "UAH":
            out.append(t)
            continue

        fp = str(t.get("from"))
        tp = str(t.get("to"))

        from_is_business = _is_business_pid(fp, pid_to_type=pid_to_type)

        # Routing intermediates: allow business creditors to be intermediates.
        can_be_intermediate = bool(from_is_business)
        auto_clearing = True

        policy = dict((t.get("policy") or {}) if isinstance(t.get("policy"), dict) else {})
        policy["can_be_intermediate"] = can_be_intermediate
        policy["auto_clearing"] = auto_clearing

        limit = Decimal(str(t.get("limit") or "0"))
        used = Decimal(str(t.get("used") or "0"))

        k = _u01(f"v2|{eq}|{fp}|{tp}")

        # Primary per-person balance enforcement.
        person_primary_pid: str | None = None
        if fp in person_pids and primary_tl_index_by_person.get(fp) == i:
            person_primary_pid = fp
        elif tp in person_pids and primary_tl_index_by_person.get(tp) == i:
            person_primary_pid = tp

        if person_primary_pid is not None:
            used = target_balance_by_person[person_primary_pid]
            limit = max(target_limit_by_person[person_primary_pid], used + Decimal("50"))
        elif (fp in person_pids) and (tp in person_pids):
            # Person-to-person: small microcredit.
            lim_min = Decimal("150")
            lim_max = Decimal("900")
            limit = (lim_min + (lim_max - lim_min) * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            used = (Decimal("10") + Decimal("90") * Decimal(str(_u01(f"used|p2p|{fp}|{tp}")))).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
        elif (fp in person_pids) or (tp in person_pids):
            # Person involved but not the primary balance edge: keep small so total stays ~500..3000.
            lim_min = Decimal("400")
            lim_max = Decimal("3000")
            limit = (lim_min + (lim_max - lim_min) * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            used = (Decimal("0") + Decimal("120") * Decimal(str(_u01(f"used|small|{fp}|{tp}")))).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
        else:
            # Business↔business: allow higher limits / used.
            lim_min = Decimal("1500")
            lim_max = Decimal("12000")
            limit = (lim_min + (lim_max - lim_min) * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            used_ratio = Decimal("0.25") + Decimal("0.45") * Decimal(str(_u01(f"used|b2b|{fp}|{tp}")))
            used = (limit * used_ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        # Always keep used within [0, limit].
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

def _add_extra_uah_service_links(
    trustlines: list[dict[str, Any]],
    *,
    pid_to_type: dict[str, str],
    pid_to_name: dict[str, str],
    max_service_to_household: int = 60,
    max_service_to_business: int = 30,
) -> list[dict[str, Any]]:
    """Add extra UAH-only service links for realistic-v2 connectivity.

    Realistic-v2 scenarios are UAH-only. To keep enough household/service connectivity
    (and thus clearing potential), we add a limited number of small UAH trustlines:
      - service(person, non-household) -> household(person)
      - service(person, non-household) -> business

    Intentionally does NOT look at or modify HOUR/EUR trustlines.
    """

    out = list(trustlines)
    seen: set[tuple[str, str, str]] = set()
    for t in out:
        eq = str(t.get("equivalent") or "").strip().upper() or "UAH"
        seen.add((eq, str(t.get("from")), str(t.get("to"))))

    import re

    def _pid_index(pid: str) -> int | None:
        m = re.match(r"^PID_U(\d{4})_", str(pid))
        if not m:
            return None
        return int(m.group(1))

    person_pids = {pid for pid, t in pid_to_type.items() if str(t).lower() == "person"}
    business_pids_all = sorted([pid for pid, t in pid_to_type.items() if str(t).lower() == "business"])

    # Align with realistic-v2 grouping (scripts/generate_simulator_seed_scenarios.py)
    service_pids = sorted(
        [pid for pid in person_pids if (idx := _pid_index(pid)) is not None and 24 <= idx <= 33]
    )
    household_pids = sorted(
        [pid for pid in person_pids if (idx := _pid_index(pid)) is not None and 34 <= idx <= 48]
    )
    producer_pids = sorted(
        [pid for pid in person_pids if (idx := _pid_index(pid)) is not None and 6 <= idx <= 15]
    )
    agent_pids = sorted([pid for pid in person_pids if (idx := _pid_index(pid)) is not None and 49 <= idx <= 50])
    retail_business_pids = sorted(
        [pid for pid in business_pids_all if (idx := _pid_index(pid)) is not None and 16 <= idx <= 23]
    )
    anchor_business_pids = sorted([pid for pid in business_pids_all if (idx := _pid_index(pid)) is not None and 1 <= idx <= 5])
    business_targets = retail_business_pids or business_pids_all
    business_sources = [*retail_business_pids, *anchor_business_pids] or business_pids_all

    # Give the routing graph a business-intermediate backbone:
    # business -> (service/producer/agent) links have can_be_intermediate=True.
    person_targets = [*service_pids, *producer_pids, *agent_pids]

    if not household_pids or not service_pids:
        return out

    def _pick_from(lst: list[str], *, key: str) -> str:
        idx = int(_u01(key) * len(lst))
        if idx >= len(lst):
            idx = len(lst) - 1
        return lst[idx]

    dt = __import__("datetime")

    def _add_uah(src: str, dst: str, *, limit: Decimal, used: Decimal) -> None:
        key = ("UAH", src, dst)
        if key in seen:
            return
        seen.add(key)

        lim = max(Decimal("0"), limit).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        u = max(Decimal("0"), min(lim, used)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        avail = (lim - u).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        created_at = (BASE_TS - dt.timedelta(seconds=int(_u01(f"uah_svc_created|{src}|{dst}") * 86400))).isoformat()
        created_at = created_at.replace("+00:00", "Z")

        out.append(
            {
                "equivalent": "UAH",
                "from": src,
                "to": dst,
                "from_display_name": str(pid_to_name.get(src, "")),
                "to_display_name": str(pid_to_name.get(dst, "")),
                "limit": _quantize_money(lim),
                "used": _quantize_money(u),
                "available": _quantize_money(avail),
                "status": "active",
                "created_at": created_at,
                "policy": {
                    "auto_clearing": True,
                    "can_be_intermediate": bool(_is_business_pid(src, pid_to_type=pid_to_type)),
                },
            }
        )

    svc_hh_candidates: list[tuple[float, str, str]] = []
    svc_biz_candidates: list[tuple[float, str, str]] = []
    aux_hh_candidates: list[tuple[float, str, str]] = []
    aux_svc_candidates: list[tuple[float, str, str]] = []
    aux_biz_candidates: list[tuple[float, str, str]] = []
    biz_person_candidates: list[tuple[float, str, str]] = []

    for src in service_pids:
        n_hh = 2 + int(_u01(f"uah_svc_n_hh|{src}") * 2)
        picked_hh: set[str] = set()
        for j in range(n_hh):
            dst = _pick_from(household_pids, key=f"uah_svc_pick_hh|{src}|{j}")
            if dst in picked_hh:
                continue
            picked_hh.add(dst)
            score = _u01(f"uah_svc_score|hh|{src}|{dst}")
            svc_hh_candidates.append((score, src, dst))

        if business_targets:
            dst_b = _pick_from(business_targets, key=f"uah_svc_pick_biz|{src}")
            score_b = _u01(f"uah_svc_score|biz|{src}|{dst_b}")
            svc_biz_candidates.append((score_b, src, dst_b))

    # Producers/agents (persons) can be key intermediates in UAH-only routing.
    # Add a small number of extra links from them to households/services/retail,
    # without changing HOUR/EUR equivalents.
    aux_sources = [*producer_pids, *agent_pids]
    for src in aux_sources:
        # 1-2 household links per aux source
        n_hh = 1 + int(_u01(f"uah_aux_n_hh|{src}") * 2)
        picked_hh: set[str] = set()
        for j in range(n_hh):
            dst = _pick_from(household_pids, key=f"uah_aux_pick_hh|{src}|{j}")
            if dst in picked_hh:
                continue
            picked_hh.add(dst)
            score = _u01(f"uah_aux_score|hh|{src}|{dst}")
            aux_hh_candidates.append((score, src, dst))

        # One service link per aux source
        dst_s = _pick_from(service_pids, key=f"uah_aux_pick_svc|{src}")
        score_s = _u01(f"uah_aux_score|svc|{src}|{dst_s}")
        aux_svc_candidates.append((score_s, src, dst_s))

        # Optional retail link
        if business_targets:
            dst_b = _pick_from(business_targets, key=f"uah_aux_pick_biz|{src}")
            score_b = _u01(f"uah_aux_score|biz|{src}|{dst_b}")
            aux_biz_candidates.append((score_b, src, dst_b))

    if business_sources and person_targets:
        for src in business_sources:
            n_p = 1 + int(_u01(f"uah_biz_n_person|{src}") * 2)
            picked_p: set[str] = set()
            for j in range(n_p):
                dst = _pick_from(person_targets, key=f"uah_biz_pick_person|{src}|{j}")
                if dst in picked_p:
                    continue
                picked_p.add(dst)
                score = _u01(f"uah_biz_score|person|{src}|{dst}")
                biz_person_candidates.append((score, src, dst))

    for _, src, dst in sorted(svc_hh_candidates, key=lambda x: (x[0], x[1], x[2]))[:max_service_to_household]:
        k = _u01(f"uah_svc_lim|hh|{src}|{dst}")
        limit = (Decimal("700") + Decimal("1800") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        used = (Decimal("0") + Decimal("200") * Decimal(str(_u01(f"uah_svc_used|hh|{src}|{dst}")))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        _add_uah(src, dst, limit=limit, used=used)

    for _, src, dst in sorted(svc_biz_candidates, key=lambda x: (x[0], x[1], x[2]))[:max_service_to_business]:
        k = _u01(f"uah_svc_lim|biz|{src}|{dst}")
        limit = (Decimal("1000") + Decimal("3500") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        used = (Decimal("0") + Decimal("300") * Decimal(str(_u01(f"uah_svc_used|biz|{src}|{dst}")))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        _add_uah(src, dst, limit=limit, used=used)

    for _, src, dst in sorted(aux_hh_candidates, key=lambda x: (x[0], x[1], x[2]))[:40]:
        k = _u01(f"uah_aux_lim|hh|{src}|{dst}")
        limit = (Decimal("250") + Decimal("900") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        used = (Decimal("0") + Decimal("80") * Decimal(str(_u01(f"uah_aux_used|hh|{src}|{dst}")))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        _add_uah(src, dst, limit=limit, used=used)

    for _, src, dst in sorted(aux_svc_candidates, key=lambda x: (x[0], x[1], x[2]))[:25]:
        k = _u01(f"uah_aux_lim|svc|{src}|{dst}")
        limit = (Decimal("300") + Decimal("1200") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        used = (Decimal("0") + Decimal("120") * Decimal(str(_u01(f"uah_aux_used|svc|{src}|{dst}")))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        _add_uah(src, dst, limit=limit, used=used)

    for _, src, dst in sorted(aux_biz_candidates, key=lambda x: (x[0], x[1], x[2]))[:25]:
        k = _u01(f"uah_aux_lim|biz|{src}|{dst}")
        limit = (Decimal("400") + Decimal("1600") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        used = (Decimal("0") + Decimal("150") * Decimal(str(_u01(f"uah_aux_used|biz|{src}|{dst}")))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        _add_uah(src, dst, limit=limit, used=used)

    for _, src, dst in sorted(biz_person_candidates, key=lambda x: (x[0], x[1], x[2]))[:60]:
        k = _u01(f"uah_biz_lim|person|{src}|{dst}")
        limit = (Decimal("900") + Decimal("4200") * Decimal(str(k))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        used = (Decimal("0") + Decimal("250") * Decimal(str(_u01(f"uah_biz_used|person|{src}|{dst}")))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        _add_uah(src, dst, limit=limit, used=used)

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
    p = argparse.ArgumentParser(description="Generate Riverside Town seed fixtures (v2)")
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
    trustlines_v2 = _transform_trustlines(trustlines_v1, pid_to_type=pid_to_type, pid_to_name=pid_to_name)
    trustlines_v2 = _add_extra_uah_service_links(trustlines_v2, pid_to_type=pid_to_type, pid_to_name=pid_to_name)
    return trustlines_v2


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
            seed_id="riverside-town-50-v2",
            generator="generate_seed_riverside_town_50_v2.py",
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
