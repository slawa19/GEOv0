"""Shared deterministic helpers for Admin UI seed generators.

This module is intentionally dependency-free and is imported by the seed scripts
in this folder via sys.path insertion (the parent folder name contains a hyphen
so it can't be imported as a normal Python package).

Keep functions small and stable: seeds should remain deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any


def iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def q(value: Decimal, precision: int) -> str:
    quant = Decimal("1") if precision == 0 else Decimal("1").scaleb(-precision)
    return str(value.quantize(quant, rounding=ROUND_DOWN))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pid(idx: int) -> str:
    # Keep the familiar PID_U0001_xxxxxxxx format.
    h = (idx * 2654435761) % (2**32)
    return f"PID_U{idx:04d}_{h:08x}"


@dataclass(frozen=True)
class Participant:
    pid: str
    display_name: str
    type: str
    status: str


def build_debts_from_trustlines(trustlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive debt edges from trustline `used`.

    Semantics reminder:
      trustline from->to means creditor->debtor, therefore used represents a debt:
        debtor = to
        creditor = from

    Output shape matches Admin UI fixtures: {equivalent, debtor, creditor, amount}.
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
    max_cycles_per_equivalent: int = 6,
) -> dict[str, Any]:
    """Find a few short (3-edge) debt cycles for UI prototyping."""

    by_eq: dict[str, list[dict[str, Any]]] = {}
    for d in debts:
        eq = str(d.get("equivalent") or "")
        if not eq:
            continue
        by_eq.setdefault(eq, []).append(d)

    result: dict[str, Any] = {"equivalents": {}}

    for eq in sorted(by_eq.keys()):
        edges = by_eq[eq]
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

                    ca_amt = None
                    for nxt, amt in adjacency.get(c, []):
                        if nxt == a:
                            ca_amt = amt
                            break
                    if ca_amt is None:
                        continue

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


def build_meta(
    *,
    base_ts: datetime,
    equivalents: list[dict[str, Any]],
    participants: list[Any],
    trustlines: list[dict[str, Any]],
    incidents: dict[str, Any],
    debts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": "v1",
        "generated_at": iso(base_ts),
        "counts": {
            "participants": len(participants),
            "equivalents": len(equivalents),
            "trustlines": len(trustlines),
            "incidents": len(list(incidents.get("items") or [])),
            "debts": len(debts),
        },
        "notes": [
            "TrustLine direction is creditor -> debtor (from -> to).",
            "Debt direction is debtor -> creditor (derived from trustline.used).",
            f"Timestamps are fixed relative to {iso(base_ts)}.",
        ],
    }
