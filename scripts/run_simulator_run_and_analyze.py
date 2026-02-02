from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunWindow:
    started_at: datetime
    stopped_at: datetime


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _http_json(*, base_url: str, method: str, path: str, headers: dict[str, str], body: Any | None = None) -> Any:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {detail}") from e


def _download(*, origin: str, url_path: str, headers: dict[str, str], out_path: Path) -> None:
    url = origin.rstrip("/") + "/" + url_path.lstrip("/")
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)

    with urllib.request.urlopen(req, timeout=30) as resp:
        out_path.write_bytes(resp.read())


def _load_run_window(db: sqlite3.Connection, run_id: str) -> RunWindow | None:
    row = db.execute(
        "SELECT started_at, stopped_at FROM simulator_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return RunWindow(started_at=_parse_dt(row[0]), stopped_at=_parse_dt(row[1]))


def _collect_payment_amounts(db: sqlite3.Connection, window: RunWindow, equivalent: str) -> list[float]:
    rows = db.execute(
        "SELECT payload, created_at FROM transactions WHERE type = 'PAYMENT'"
    ).fetchall()

    out: list[float] = []
    eq = str(equivalent).upper()

    for payload_raw, created_at_raw in rows:
        try:
            created_at = _parse_dt(created_at_raw)
        except Exception:
            continue
        if created_at < window.started_at or created_at > window.stopped_at:
            continue

        payload = payload_raw
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if str(payload.get("equivalent", "")).upper() != eq:
            continue
        try:
            out.append(float(str(payload.get("amount"))))
        except Exception:
            continue

    return out


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        raise ValueError("empty")
    p = max(0.0, min(1.0, float(p)))
    i = int(round((len(sorted_values) - 1) * p))
    return sorted_values[max(0, min(len(sorted_values) - 1, i))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="http://127.0.0.1:18000", help="Backend origin (no /api/v1)")
    ap.add_argument("--base-url", default="http://127.0.0.1:18000/api/v1", help="API base URL")
    ap.add_argument("--admin-token", default="dev-admin-token-change-me")
    ap.add_argument("--scenario-id", default="greenfield-village-100-realistic-v2")
    ap.add_argument("--mode", default="real", choices=["fixtures", "real"])
    ap.add_argument("--intensity", type=int, default=80)
    ap.add_argument("--run-seconds", type=int, default=20)
    ap.add_argument("--equivalent", default="UAH")
    ap.add_argument("--min-amount", type=float, default=50.0)
    ap.add_argument("--max-amount", type=float, default=1500.0)
    ap.add_argument("--out-dir", default=str(Path(".local-run") / "analysis"))
    args = ap.parse_args()

    headers = {
        "X-Admin-Token": args.admin_token,
        "Content-Type": "application/json",
    }

    scenarios = _http_json(base_url=args.base_url, method="GET", path="/simulator/scenarios", headers=headers)
    scenario_ids = {item.get("scenario_id") for item in scenarios.get("items", [])}
    if args.scenario_id not in scenario_ids:
        raise SystemExit(f"Scenario not found: {args.scenario_id}")

    run = _http_json(
        base_url=args.base_url,
        method="POST",
        path="/simulator/runs",
        headers=headers,
        body={
            "scenario_id": args.scenario_id,
            "mode": args.mode,
            "intensity_percent": int(args.intensity),
        },
    )
    run_id = run["run_id"]
    print(f"run_id={run_id}")

    end_at = time.time() + max(1, int(args.run_seconds))
    while time.time() < end_at:
        st = _http_json(base_url=args.base_url, method="GET", path=f"/simulator/runs/{run_id}", headers=headers)
        print(
            f"state={st.get('state')} sim_time_ms={st.get('sim_time_ms')} tick={st.get('tick_index')} ops_sec={st.get('ops_sec')}"
        )
        time.sleep(2)

    _http_json(base_url=args.base_url, method="POST", path=f"/simulator/runs/{run_id}/stop", headers=headers)

    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        st = _http_json(base_url=args.base_url, method="GET", path=f"/simulator/runs/{run_id}", headers=headers)
        last = st
        if st.get("state") in ("stopped", "error"):
            break
        time.sleep(1)

    print(f"final_state={last.get('state')} sim_time_ms={last.get('sim_time_ms')} tick={last.get('tick_index')}")

    idx = _http_json(base_url=args.base_url, method="GET", path=f"/simulator/runs/{run_id}/artifacts", headers=headers)
    items = {it["name"]: it for it in idx.get("items", [])}
    print("artifacts=", ",".join(sorted(items.keys())))

    out_base = Path(args.out_dir)
    out_dir = out_base / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in ("last_tick.json", "status.json", "summary.json", "events.ndjson"):
        it = items.get(name)
        if not it:
            continue
        _download(origin=args.origin, url_path=it["url"], headers={"X-Admin-Token": args.admin_token}, out_path=out_dir / name)

    print(f"downloaded_dir={out_dir}")

    db = sqlite3.connect("file:geov0.db?mode=ro", uri=True)
    window = _load_run_window(db, run_id)

    if not window:
        print("db_window=missing")
        return 0

    amounts = _collect_payment_amounts(db, window, equivalent=args.equivalent)
    db.close()

    if not amounts:
        print("payments.count=0")
        return 0

    amounts.sort()
    print(f"payments.count={len(amounts)}")
    print(f"payments.min={amounts[0]} payments.max={amounts[-1]}")
    print(
        "payments.p50/p90/p99="
        f"{_percentile(amounts, 0.50)}/{_percentile(amounts, 0.90)}/{_percentile(amounts, 0.99)}"
    )

    lo = float(args.min_amount)
    hi = float(args.max_amount)
    below = sum(1 for x in amounts if x < lo - 1e-9)
    above = sum(1 for x in amounts if x > hi + 1e-9)
    print(f"payments.target_range=[{lo},{hi}]")
    print(f"payments.below_min={below} payments.above_max={above}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
