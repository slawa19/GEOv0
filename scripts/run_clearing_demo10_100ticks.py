from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _parse_dt(s: str) -> datetime:
    # e.g. 2026-02-15T12:56:06.124805Z
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _http_json(
    *,
    base_url: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: Any | None = None,
    timeout_sec: int = 30,
) -> Any:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=max(1, int(timeout_sec))) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {detail}") from e


def _try_parse_error_detail(msg: str) -> dict[str, Any] | None:
    # RuntimeError text is: "HTTP <code> <method> <path>: <detail>"
    try:
        detail = str(msg).split(":", 1)[1].strip()
    except Exception:
        return None
    try:
        v = json.loads(detail)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _download(
    *,
    origin: str,
    url_path: str,
    headers: dict[str, str],
    timeout_sec: int = 30,
) -> bytes:
    url = origin.rstrip("/") + "/" + url_path.lstrip("/")
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)

    with urllib.request.urlopen(req, timeout=max(1, int(timeout_sec))) as resp:
        return resp.read()


@dataclass(frozen=True)
class ClearingStats:
    done_events: int
    total_cleared_cycles: int
    total_cleared_amount: float
    done_ticks: list[int]


def _analyze_events_ndjson(raw: bytes, *, started_at: datetime) -> dict[str, Any]:
    tx_updated = 0
    clearing_done = 0
    cleared_cycles_total = 0
    cleared_amount_total = 0.0
    done_ticks: list[int] = []
    done_ticks_interact: list[int] = []
    done_ticks_auto: list[int] = []
    clearing_done_interact = 0
    clearing_done_auto = 0

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue

        t = str(evt.get("type") or "")
        if t == "tx.updated":
            tx_updated += 1
        elif t == "clearing.done":
            clearing_done += 1
            plan_id = str(evt.get("plan_id") or "")
            is_interact = plan_id.startswith("plan_interact_")
            if is_interact:
                clearing_done_interact += 1
            else:
                clearing_done_auto += 1
            try:
                cleared_cycles_total += int(evt.get("cleared_cycles") or 0)
            except Exception:
                pass
            try:
                cleared_amount_total += float(str(evt.get("cleared_amount") or 0.0))
            except Exception:
                pass
            try:
                ts = _parse_dt(str(evt.get("ts")))
                dt = (ts - started_at).total_seconds()
                done_ticks.append(max(0, int(round(dt))))
                if is_interact:
                    done_ticks_interact.append(max(0, int(round(dt))))
                else:
                    done_ticks_auto.append(max(0, int(round(dt))))
            except Exception:
                pass

    done_ticks_sorted = sorted(done_ticks)
    gaps = [b - a for a, b in zip(done_ticks_sorted, done_ticks_sorted[1:])]

    done_ticks_interact_sorted = sorted(done_ticks_interact)
    gaps_interact = [
        b - a
        for a, b in zip(done_ticks_interact_sorted, done_ticks_interact_sorted[1:])
    ]
    done_ticks_auto_sorted = sorted(done_ticks_auto)
    gaps_auto = [b - a for a, b in zip(done_ticks_auto_sorted, done_ticks_auto_sorted[1:])]

    return {
        "events": {
            "tx.updated": tx_updated,
            "clearing.done": clearing_done,
        },
        "clearing": {
            "cleared_cycles_total": cleared_cycles_total,
            "cleared_amount_total": round(cleared_amount_total, 6),
            "done_ticks": done_ticks_sorted,
            "done_tick_gaps": gaps,
            "done": {
                "interact": {
                    "count": clearing_done_interact,
                    "done_ticks": done_ticks_interact_sorted,
                    "done_tick_gaps": gaps_interact,
                },
                "auto": {
                    "count": clearing_done_auto,
                    "done_ticks": done_ticks_auto_sorted,
                    "done_tick_gaps": gaps_auto,
                },
            },
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="http://127.0.0.1:18000", help="Backend origin (no /api/v1)")
    ap.add_argument("--base-url", default="http://127.0.0.1:18000/api/v1", help="API base URL")
    ap.add_argument("--admin-token", default="dev-admin-token-change-me")
    ap.add_argument("--scenario-id", default="clearing-demo-10")
    ap.add_argument("--equivalent", default="UAH")
    ap.add_argument("--run-seconds", type=int, default=110, help="~100 ticks = 100s")
    ap.add_argument("--inject-payment-every-sec", type=int, default=2)
    ap.add_argument("--payment-amount", default="120")
    ap.add_argument("--clearing-every-sec", type=int, default=10)
    ap.add_argument("--clearing-max-depth", type=int, default=6)
    ap.add_argument("--timeout-sec", type=int, default=30)
    args = ap.parse_args()

    headers = {
        "X-Admin-Token": args.admin_token,
        "Content-Type": "application/json",
    }

    integrity_before = _http_json(
        base_url=args.base_url,
        method="POST",
        path="/integrity/verify",
        headers=headers,
        body={"equivalent": args.equivalent},
        timeout_sec=max(args.timeout_sec, 60),
    )

    # Start run (manual scenario => intensity=0)
    run = _http_json(
        base_url=args.base_url,
        method="POST",
        path="/simulator/runs",
        headers=headers,
        body={
            "scenario_id": args.scenario_id,
            "mode": "real",
            "intensity_percent": 0,
        },
        timeout_sec=args.timeout_sec,
    )
    run_id = str(run["run_id"])
    print(f"run_id={run_id}")

    # Resolve participant PIDs via interact endpoint.
    plist = _http_json(
        base_url=args.base_url,
        method="GET",
        path=f"/simulator/runs/{run_id}/actions/participants-list",
        headers=headers,
        timeout_sec=args.timeout_sec,
    )
    items = list(plist.get("items") or [])

    def _pid_by_name_contains(substr: str) -> str | None:
        s = substr.lower().strip()
        for it in items:
            name = str(it.get("name") or "").lower()
            pid = str(it.get("pid") or "").strip()
            if s and s in name and pid:
                return pid
        return None

    # Try to resolve canonical demo actors by name (preferred).
    pid_shop = _pid_by_name_contains("магаз")
    pid_farmer = _pid_by_name_contains("фер")
    pid_bakery = _pid_by_name_contains("пек")
    pid_workshop = _pid_by_name_contains("май")
    pid_alice = _pid_by_name_contains("али")
    pid_bob = _pid_by_name_contains("боб")
    pid_olena = _pid_by_name_contains("олен")
    pid_dmytro = _pid_by_name_contains("дмит")

    # Build trustline topology for routable payment generation (fallback).
    # Payment S->R requires a trustline path from R to S (creditor->debtor direction).
    tlist = _http_json(
        base_url=args.base_url,
        method="GET",
        path=f"/simulator/runs/{run_id}/actions/trustlines-list?equivalent={args.equivalent}",
        headers=headers,
        timeout_sec=args.timeout_sec,
    )
    trust_items = list(tlist.get("items") or [])
    adj: dict[str, list[str]] = {}
    for it in trust_items:
        fp = str(it.get("from_pid") or "").strip()
        tp = str(it.get("to_pid") or "").strip()
        if fp and tp and fp != tp:
            adj.setdefault(fp, []).append(tp)

    all_pids = [str(it.get("pid") or "").strip() for it in items if str(it.get("pid") or "").strip()]

    def _reachable(src: str, dst: str, *, max_hops: int = 6) -> bool:
        if not src or not dst:
            return False
        if src == dst:
            return True
        q: list[tuple[str, int]] = [(src, 0)]
        seen = {src}
        while q:
            cur, d = q.pop(0)
            if d >= max_hops:
                continue
            for nxt in adj.get(cur, []):
                if nxt == dst:
                    return True
                if nxt in seen:
                    continue
                seen.add(nxt)
                q.append((nxt, d + 1))
        return False

    routable_pairs: list[tuple[str, str]] = []
    for sender in all_pids:
        for receiver in all_pids:
            if sender == receiver:
                continue
            # receiver -> sender must be reachable
            if _reachable(receiver, sender, max_hops=6):
                routable_pairs.append((sender, receiver))

    if not routable_pairs:
        raise SystemExit("No routable payment pairs found for scenario topology")

    rng = random.Random(int.from_bytes(run_id.encode("utf-8"), "little") % (2**32))
    rng.shuffle(routable_pairs)

    # Prefer a deterministic triangle-program that should create true 3-cycles in the debt graph.
    # Each step is a real payment. After 3 steps, debts form a triangle:
    #   alice -> shop, shop -> farmer, farmer -> alice
    # and similarly for other participants.
    scripted_steps: list[tuple[str, str]] = []
    if all([pid_shop, pid_farmer, pid_bakery, pid_workshop, pid_alice, pid_bob, pid_olena, pid_dmytro]):
        scripted_steps = [
            # Triangle 1: alice -> shop -> farmer -> alice
            (pid_alice, pid_shop),
            (pid_shop, pid_farmer),
            (pid_farmer, pid_alice),

            # Triangle 2: bob -> shop -> bakery -> bob
            (pid_bob, pid_shop),
            (pid_shop, pid_bakery),
            (pid_bakery, pid_bob),

            # Triangle 3: olena -> shop -> workshop -> olena
            (pid_olena, pid_shop),
            (pid_shop, pid_workshop),
            (pid_workshop, pid_olena),

            # Triangle 4: dmytro -> shop -> workshop -> dmytro
            (pid_dmytro, pid_shop),
            (pid_shop, pid_workshop),
            (pid_workshop, pid_dmytro),
        ]

    started_at = datetime.now().astimezone()

    end_at = time.time() + max(1, int(args.run_seconds))
    next_payment_at = time.time() + max(1, int(args.inject_payment_every_sec))
    next_clearing_at = time.time() + max(1, int(args.clearing_every_sec))

    payment_ok = 0
    payment_fail = 0
    payment_fail_by_code: dict[str, int] = {}
    clearing_calls = 0
    clearing_cycles_reported = 0

    i = 0
    while time.time() < end_at:
        now = time.time()

        if now >= next_payment_at:
            if scripted_steps:
                a, b = scripted_steps[i % len(scripted_steps)]
            else:
                a, b = routable_pairs[i % len(routable_pairs)]
            i += 1
            try:
                _http_json(
                    base_url=args.base_url,
                    method="POST",
                    path=f"/simulator/runs/{run_id}/actions/payment-real",
                    headers=headers,
                    body={
                        "equivalent": args.equivalent,
                        "from_pid": a,
                        "to_pid": b,
                        "amount": str(args.payment_amount),
                    },
                    timeout_sec=args.timeout_sec,
                )
                payment_ok += 1
            except Exception as e:
                payment_fail += 1
                detail = _try_parse_error_detail(str(e)) or {}
                code = str(detail.get("code") or "UNKNOWN")
                payment_fail_by_code[code] = int(payment_fail_by_code.get(code, 0)) + 1
            next_payment_at = now + max(1, int(args.inject_payment_every_sec))

        if now >= next_clearing_at:
            try:
                resp = _http_json(
                    base_url=args.base_url,
                    method="POST",
                    path=f"/simulator/runs/{run_id}/actions/clearing-real",
                    headers=headers,
                    body={
                        "equivalent": args.equivalent,
                        "max_depth": int(args.clearing_max_depth),
                    },
                    timeout_sec=max(args.timeout_sec, 60),
                )
                clearing_calls += 1
                try:
                    clearing_cycles_reported += int(resp.get("cleared_cycles") or 0)
                except Exception:
                    pass
            except Exception:
                clearing_calls += 1
            next_clearing_at = now + max(1, int(args.clearing_every_sec))

        time.sleep(0.25)

    # Stop run
    _http_json(
        base_url=args.base_url,
        method="POST",
        path=f"/simulator/runs/{run_id}/stop?source=cli&reason=demo10_100ticks",
        headers=headers,
        timeout_sec=max(args.timeout_sec, 60),
    )

    # Download artifacts and analyze events
    idx = _http_json(
        base_url=args.base_url,
        method="GET",
        path=f"/simulator/runs/{run_id}/artifacts",
        headers=headers,
        timeout_sec=args.timeout_sec,
    )
    items_by_name = {it["name"]: it for it in (idx.get("items") or []) if isinstance(it, dict) and it.get("name")}

    events_raw = b""
    if "events.ndjson" in items_by_name:
        events_raw = _download(
            origin=args.origin,
            url_path=items_by_name["events.ndjson"]["url"],
            headers={"X-Admin-Token": args.admin_token},
            timeout_sec=args.timeout_sec,
        )

    # Integrity verify (authoritative invariants check)
    integrity_after = _http_json(
        base_url=args.base_url,
        method="POST",
        path="/integrity/verify",
        headers=headers,
        body={"equivalent": args.equivalent},
        timeout_sec=max(args.timeout_sec, 60),
    )

    analyzed = _analyze_events_ndjson(events_raw, started_at=started_at)

    out = {
        "run_id": run_id,
        "scenario_id": args.scenario_id,
        "equivalent": args.equivalent,
        "run_seconds": int(args.run_seconds),
        "actions": {
            "payment_real_ok": payment_ok,
            "payment_real_fail": payment_fail,
            "payment_real_fail_by_code": payment_fail_by_code,
            "clearing_real_calls": clearing_calls,
            "clearing_real_cycles_reported": clearing_cycles_reported,
        },
        "artifact_events_analysis": analyzed,
        "integrity_verify": {
            "before": integrity_before,
            "after": integrity_after,
        },
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
