from __future__ import annotations

import bisect
from typing import Iterable


def quantile(values_sorted: list[float], p: float) -> float:
    if not values_sorted:
        raise ValueError("values_sorted must be non-empty")
    if p <= 0:
        return values_sorted[0]
    if p >= 1:
        return values_sorted[-1]
    n = len(values_sorted)
    i = int(p * (n - 1))
    return values_sorted[i]


def link_width_key(limit: float | None, *, q33: float | None, q66: float | None) -> str:
    if limit is None or q33 is None or q66 is None:
        return "hairline"
    if limit <= q33:
        return "thin"
    if limit <= q66:
        return "mid"
    return "thick"


def link_alpha_key(status: str | None, used: float | None, limit: float | None) -> str:
    if status and status != "active":
        return "muted"
    if used is None or limit is None or limit <= 0:
        return "bg"
    r = abs(used) / limit
    if r >= 0.75:
        return "hi"
    if r >= 0.40:
        return "active"
    if r >= 0.15:
        return "muted"
    return "bg"


def net_sign_from_atoms(atoms: int) -> int:
    if atoms < 0:
        return -1
    if atoms > 0:
        return 1
    return 0


def node_shape_key(type_key: str | None) -> str:
    return "rounded-rect" if (type_key or "").strip().lower() == "business" else "circle"


def percentile_rank(sorted_mags: list[int], mag: int) -> float:
    n = len(sorted_mags)
    if n <= 1:
        return 0.0
    i = bisect.bisect_right(sorted_mags, mag) - 1
    i = max(0, min(i, n - 1))
    return i / (n - 1)


def scale_from_pct(pct: float, *, max_scale: float = 1.90, gamma: float = 0.75) -> float:
    if pct <= 0:
        return 1.0
    if pct >= 1:
        return max_scale
    return 1.0 + (max_scale - 1.0) * (pct**gamma)


def debt_bin(debt_mags_sorted: list[int], mag: int, *, bins: int = 9) -> int:
    n = len(debt_mags_sorted)
    if n <= 1:
        return 0
    i = bisect.bisect_right(debt_mags_sorted, mag) - 1
    i = max(0, min(i, n - 1))
    pct = i / (n - 1)
    b = int(round(pct * (bins - 1)))
    return max(0, min(b, bins - 1))


def node_color_key(*, atoms: int, status_key: str | None, type_key: str | None, debt_mags_sorted: list[int]) -> str | None:
    sk = (status_key or "").strip().lower()
    tk = (type_key or "").strip().lower()

    if sk in {"suspended", "frozen"}:
        return "suspended"
    if sk == "left":
        return "left"
    if sk in {"deleted", "banned"}:
        return "deleted"

    if atoms < 0:
        b = debt_bin(debt_mags_sorted, abs(atoms))
        return f"debt-{b}"

    return "business" if tk == "business" else "person"


def node_size_wh(*, atoms_abs: int, mags_sorted: list[int], type_key: str | None) -> tuple[int, int]:
    pct = percentile_rank(mags_sorted, atoms_abs) if mags_sorted else 0.0
    s = scale_from_pct(pct)

    tk = (type_key or "").strip().lower()
    if tk == "business":
        w0, h0 = 26, 22
    else:
        w0, h0 = 16, 16

    return int(round(w0 * s)), int(round(h0 * s))


def collect_magnitudes(atoms_by_id: Iterable[int]) -> tuple[list[int], list[int]]:
    mags: list[int] = []
    debt_mags: list[int] = []
    for atoms in atoms_by_id:
        mag = abs(int(atoms))
        mags.append(mag)
        if atoms < 0:
            debt_mags.append(mag)
    return sorted(mags), sorted(debt_mags)
