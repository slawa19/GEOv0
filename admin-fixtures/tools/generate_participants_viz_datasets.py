#!/usr/bin/env python
"""Generate per-equivalent participant net-viz datasets for Admin UI mock mode.

Why:
- Admin UI mock mode reads static JSON fixtures from admin-ui/public/admin-fixtures/...
- Net-viz (node color/size) must be computed on backend.
- For mock mode, we emulate "backend computed" by precomputing viz fields offline
  into deterministic fixture files:

  - datasets/participants.viz-<EQ>.json

These are loaded by mockApi when graphSnapshot({equivalent}) is called.

Input datasets (canonical):
- datasets/participants.json
- datasets/equivalents.json
- datasets/debts.json (optional; if missing, nets are 0)

Output datasets:
- datasets/participants.viz-<EQ>.json for each equivalent code.

No external dependencies.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    # Strip UTF-8 BOM if present.
    if text and text[0] == "\ufeff":
        text = text[1:]
    return json.loads(text)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _percentile_rank(sorted_mags: list[int], mag: int) -> float:
    n = len(sorted_mags)
    if n <= 1:
        return 0.0

    # Use bisect-right so equal magnitudes share percentile towards the right.
    import bisect

    i = bisect.bisect_right(sorted_mags, mag) - 1
    i = max(0, min(i, n - 1))
    return i / (n - 1)


def _scale_from_pct(pct: float, max_scale: float = 1.90, gamma: float = 0.75) -> float:
    if pct <= 0:
        return 1.0
    if pct >= 1:
        return max_scale
    return 1.0 + (max_scale - 1.0) * (pct**gamma)


DEBT_BINS = 9


def _bin_from_pct(pct: float, bins: int = DEBT_BINS) -> int:
    if bins <= 1:
        return 0
    if pct <= 0:
        return 0
    if pct >= 1:
        return bins - 1
    b = int(round(pct * (bins - 1)))
    return max(0, min(b, bins - 1))


def _to_atoms(amount: Decimal, precision: int) -> int:
    scale10 = Decimal(10) ** int(precision)
    return int((amount * scale10).to_integral_value(rounding=ROUND_HALF_UP))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate participants.viz-<EQ>.json datasets")
    p.add_argument(
        "--v1",
        default=str(Path(__file__).resolve().parents[1] / "v1"),
        help="Path to fixtures v1 dir (default: admin-fixtures/v1)",
    )

    args = p.parse_args(argv)

    v1_dir = Path(args.v1).resolve()
    datasets_dir = v1_dir / "datasets"

    participants_path = datasets_dir / "participants.json"
    equivalents_path = datasets_dir / "equivalents.json"
    debts_path = datasets_dir / "debts.json"

    participants = _read_json(participants_path)
    equivalents = _read_json(equivalents_path)
    debts = _read_json(debts_path) if debts_path.exists() else []

    if not isinstance(participants, list):
        raise RuntimeError("participants.json must be a JSON array")
    if not isinstance(equivalents, list):
        raise RuntimeError("equivalents.json must be a JSON array")
    if debts is not None and not isinstance(debts, list):
        raise RuntimeError("debts.json must be a JSON array")

    precision_by_eq: dict[str, int] = {}
    for e in equivalents:
        if not isinstance(e, dict):
            continue
        code = str(e.get("code") or "").strip().upper()
        if not code:
            continue
        try:
            prec = int(e.get("precision") or 0)
        except Exception:
            prec = 0
        precision_by_eq[code] = prec

    eq_codes = sorted(precision_by_eq.keys())

    pid_set = {str(p.get("pid") or "") for p in participants if isinstance(p, dict)}

    # Build net atoms per (eq, pid) using debts: net = credits - debts.
    net_atoms_by_eq_pid: dict[str, dict[str, int]] = {code: {pid: 0 for pid in pid_set if pid} for code in eq_codes}

    for d in debts or []:
        if not isinstance(d, dict):
            continue
        eq = str(d.get("equivalent") or "").strip().upper()
        if not eq or eq not in net_atoms_by_eq_pid:
            continue
        debtor = str(d.get("debtor") or "").strip()
        creditor = str(d.get("creditor") or "").strip()
        if debtor not in pid_set or creditor not in pid_set:
            continue
        try:
            amt = Decimal(str(d.get("amount") or "0").strip() or "0")
        except Exception:
            continue
        if amt <= 0:
            continue

        atoms = _to_atoms(amt, precision_by_eq.get(eq, 0))
        net_atoms_by_eq_pid[eq][creditor] = net_atoms_by_eq_pid[eq].get(creditor, 0) + atoms
        net_atoms_by_eq_pid[eq][debtor] = net_atoms_by_eq_pid[eq].get(debtor, 0) - atoms

    for eq in eq_codes:
        net_by_pid = net_atoms_by_eq_pid.get(eq, {})
        mags = [abs(int(net_by_pid.get(str(p.get("pid") or ""), 0))) for p in participants if isinstance(p, dict)]
        mags_sorted = sorted(mags)

        debt_mags = [
            abs(int(net_by_pid.get(str(p.get("pid") or ""), 0)))
            for p in participants
            if isinstance(p, dict) and int(net_by_pid.get(str(p.get("pid") or ""), 0)) < 0
        ]
        debt_mags_sorted = sorted(debt_mags)

        out: list[dict[str, Any]] = []
        for rec in participants:
            if not isinstance(rec, dict):
                continue
            pid = str(rec.get("pid") or "").strip()
            atoms = int(net_by_pid.get(pid, 0))
            net_sign = -1 if atoms < 0 else (1 if atoms > 0 else 0)

            status_key = str(rec.get("status") or "").strip().lower()
            type_key = str(rec.get("type") or "").strip().lower()

            if status_key in {"suspended", "frozen"}:
                viz_color_key = "suspended"
            elif status_key == "left":
                viz_color_key = "left"
            elif status_key in {"deleted", "banned"}:
                viz_color_key = "deleted"
            else:
                if net_sign == -1:
                    debt_pct = _percentile_rank(debt_mags_sorted, abs(atoms))
                    viz_color_key = f"debt-{_bin_from_pct(debt_pct)}"
                else:
                    viz_color_key = "business" if type_key == "business" else "person"

            pct = _percentile_rank(mags_sorted, abs(atoms))
            s = _scale_from_pct(pct)

            if type_key == "business":
                w0, h0 = 26, 22
            else:
                w0, h0 = 16, 16

            out.append(
                {
                    "pid": pid,
                    "display_name": rec.get("display_name"),
                    "type": rec.get("type"),
                    "status": rec.get("status"),
                    "net_balance_atoms": str(atoms),
                    "net_sign": net_sign,
                    "viz_color_key": viz_color_key,
                    "viz_size": {"w": int(round(w0 * s)), "h": int(round(h0 * s))},
                }
            )

        out_path = datasets_dir / f"participants.viz-{eq}.json"
        _write_json(out_path, out)
        print(f"Wrote: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
