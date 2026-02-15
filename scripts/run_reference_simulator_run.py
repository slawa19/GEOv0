from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class IntegrityResult:
    status: str
    details: dict[str, Any]


def _summarize_trust_limits_violations(integrity_details: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort summary of TRUST_LIMIT_VIOLATION details from /integrity/verify response."""

    if not isinstance(integrity_details, dict):
        return None
    eqs = integrity_details.get("equivalents")
    if not isinstance(eqs, dict) or not eqs:
        return None

    # We usually verify a single equivalent; pick the first entry if multiple.
    _eq_code, eq_payload = next(iter(eqs.items()))
    if not isinstance(eq_payload, dict):
        return None
    inv = eq_payload.get("invariants")
    if not isinstance(inv, dict):
        return None

    tl = inv.get("trust_limits")
    if not isinstance(tl, dict):
        return None

    details = tl.get("details")
    if not isinstance(details, dict):
        return {"violations": int(tl.get("violations") or 0)}

    violations = details.get("violations")
    if not isinstance(violations, list):
        return {"violations": int(tl.get("violations") or 0)}

    def _to_float(v: Any) -> float:
        try:
            return float(v)
        except Exception:
            try:
                return float(str(v))
            except Exception:
                return 0.0

    enriched: list[dict[str, Any]] = []
    for item in violations:
        if not isinstance(item, dict):
            continue
        v_amt = _to_float(item.get("violation_amount"))
        enriched.append({**item, "_violation_amount_num": v_amt})

    enriched.sort(key=lambda x: x.get("_violation_amount_num", 0.0), reverse=True)
    top = []
    for item in enriched[:3]:
        top.append(
            {
                "debtor_id": item.get("debtor_id"),
                "creditor_id": item.get("creditor_id"),
                "debt_amount": item.get("debt_amount"),
                "trust_limit": item.get("trust_limit"),
                "violation_amount": item.get("violation_amount"),
            }
        )

    max_amt = enriched[0].get("_violation_amount_num") if enriched else 0.0
    return {
        "violations": len(enriched),
        "max_violation_amount": max_amt,
        "top": top,
    }


def _parse_dt(s: str) -> datetime:
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


def _try_parse_error_detail(detail: str) -> dict[str, Any] | None:
    try:
        v = json.loads(str(detail or ""))
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _download(*, origin: str, url_path: str, headers: dict[str, str], out_path: Path, timeout_sec: int) -> None:
    url = origin.rstrip("/") + "/" + url_path.lstrip("/")
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)

    with urllib.request.urlopen(req, timeout=max(1, int(timeout_sec))) as resp:
        out_path.write_bytes(resp.read())


def _tick_from_status(st: dict[str, Any], *, tick_ms_base: int) -> tuple[int | None, str]:
    tick = st.get("tick_index")
    if isinstance(tick, int):
        return tick, "api"
    tick = st.get("tick")
    if isinstance(tick, int):
        return tick, "api"

    sim_time_ms = st.get("sim_time_ms")
    if isinstance(sim_time_ms, int):
        return max(0, int(sim_time_ms) // max(1, int(tick_ms_base))), "derived"
    try:
        if sim_time_ms is not None:
            return max(0, int(float(str(sim_time_ms))) // max(1, int(tick_ms_base))), "derived"
    except Exception:
        pass
    return None, "missing"


def _iter_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


def _event_type(evt: dict[str, Any]) -> str:
    # We have seen both {"type": "tx.updated"} and envelope-like shapes.
    t = evt.get("type")
    if isinstance(t, str):
        return t
    t = evt.get("event")
    if isinstance(t, str):
        return t
    return ""


def _integrity_verify(
    *,
    base_url: str,
    headers: dict[str, str],
    equivalent: str,
    timeout_sec: int,
) -> IntegrityResult:
    res = _http_json(
        base_url=base_url,
        method="POST",
        path="/integrity/verify",
        headers=headers,
        body={"equivalent": str(equivalent)},
        timeout_sec=timeout_sec,
    )
    if isinstance(res, dict):
        return IntegrityResult(status=str(res.get("status", "unknown")), details=res)
    return IntegrityResult(status="unknown", details={"raw": res})


def _analyze_events(events_path: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}

    clearing_done = 0
    cleared_cycles_total = 0
    cleared_amount_total = 0.0

    tx_updated = 0
    tx_failed = 0
    tx_failed_by_code: dict[str, int] = {}

    for evt in _iter_events(events_path):
        t = _event_type(evt)
        if t:
            counts[t] = counts.get(t, 0) + 1

        if t == "tx.updated":
            tx_updated += 1
        elif t == "tx.failed":
            tx_failed += 1
            err = evt.get("error")
            if isinstance(err, dict):
                code = err.get("code")
                if isinstance(code, str) and code:
                    tx_failed_by_code[code] = tx_failed_by_code.get(code, 0) + 1

        if t == "clearing.done":
            clearing_done += 1
            try:
                cleared_cycles_total += int(evt.get("cleared_cycles") or 0)
            except Exception:
                pass
            try:
                # cleared_amount may be null
                v = evt.get("cleared_amount")
                if v is not None:
                    cleared_amount_total += float(v)
            except Exception:
                pass

    return {
        "counts": counts,
        "tx_updated": tx_updated,
        "tx_failed": tx_failed,
        "tx_failed_by_code": dict(sorted(tx_failed_by_code.items(), key=lambda kv: (-kv[1], kv[0]))),
        "clearing_done": clearing_done,
        "cleared_cycles_total": cleared_cycles_total,
        "cleared_amount_total": cleared_amount_total,
    }


def _load_summary_counters(summary_path: Path) -> dict[str, Any] | None:
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(summary, dict):
        return None
    st = summary.get("status")
    if not isinstance(st, dict):
        return None
    keys = (
        "attempts_total",
        "committed_total",
        "rejected_total",
        "timeouts_total",
        "errors_total",
        "sim_time_ms",
        "ops_sec",
        "last_event_type",
    )
    return {k: st.get(k) for k in keys}


def _ensure_scenario_exists(*, base_url: str, headers: dict[str, str], scenario_id: str, timeout_sec: int) -> None:
    scenarios = _http_json(
        base_url=base_url,
        method="GET",
        path="/simulator/scenarios",
        headers=headers,
        timeout_sec=timeout_sec,
    )
    scenario_ids = {item.get("scenario_id") for item in scenarios.get("items", [])}
    if scenario_id not in scenario_ids:
        raise SystemExit(f"Scenario not found: {scenario_id}")


def _start_run(
    *,
    base_url: str,
    headers: dict[str, str],
    scenario_id: str,
    mode: str,
    intensity: int,
    timeout_sec: int,
) -> str:
    try:
        run = _http_json(
            base_url=base_url,
            method="POST",
            path="/simulator/runs",
            headers=headers,
            body={
                "scenario_id": scenario_id,
                "mode": mode,
                "intensity_percent": int(intensity),
            },
            timeout_sec=timeout_sec,
        )
    except RuntimeError as e:
        msg = str(e)
        prefix = "HTTP 409 POST /simulator/runs:"
        if msg.startswith(prefix):
            detail_raw = msg[len(prefix) :].strip()
            detail = _try_parse_error_detail(detail_raw) or {}
            if str(detail.get("code")) == "E008":
                active_run_id = None
                try:
                    active = _http_json(
                        base_url=base_url,
                        method="GET",
                        path="/simulator/runs/active",
                        headers=headers,
                        timeout_sec=timeout_sec,
                    )
                    active_run_id = active.get("run_id") if isinstance(active, dict) else None
                except Exception:
                    active_run_id = None

                hint = f" (active_run_id={active_run_id})" if active_run_id else ""
                raise RuntimeError(
                    "cannot start run: too many active simulator runs (E008)" + hint
                ) from e
        raise

    run_id = str(run["run_id"])
    return run_id


def _stop_run(*, base_url: str, headers: dict[str, str], run_id: str, reason: str, timeout_sec: int) -> None:
    _http_json(
        base_url=base_url,
        method="POST",
        path=f"/simulator/runs/{run_id}/stop?source=cli&reason={urllib.parse.quote(reason)}",
        headers=headers,
        timeout_sec=max(int(timeout_sec), 60),
    )


def _wait_for_stop(*, base_url: str, headers: dict[str, str], run_id: str, timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + 60
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        st = _http_json(
            base_url=base_url,
            method="GET",
            path=f"/simulator/runs/{run_id}",
            headers=headers,
            timeout_sec=timeout_sec,
        )
        last = st if isinstance(st, dict) else last
        if isinstance(st, dict) and st.get("state") in ("stopped", "error"):
            break
        time.sleep(1)
    return last or {}


def _download_artifacts(
    *,
    base_url: str,
    origin: str,
    admin_token: str,
    headers: dict[str, str],
    run_id: str,
    out_dir: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    idx = _http_json(
        base_url=base_url,
        method="GET",
        path=f"/simulator/runs/{run_id}/artifacts",
        headers=headers,
        timeout_sec=timeout_sec,
    )
    items = {it["name"]: it for it in idx.get("items", [])}

    out_dir.mkdir(parents=True, exist_ok=True)

    for name in ("last_tick.json", "status.json", "summary.json", "events.ndjson"):
        it = items.get(name)
        if not it:
            continue
        _download(
            origin=origin,
            url_path=it["url"],
            headers={"X-Admin-Token": admin_token},
            out_path=out_dir / name,
            timeout_sec=timeout_sec,
        )

    return {
        "artifact_names": sorted(items.keys()),
        "downloaded": [p.name for p in out_dir.glob("*") if p.is_file()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="http://127.0.0.1:18000", help="Backend origin (no /api/v1)")
    ap.add_argument("--base-url", default="http://127.0.0.1:18000/api/v1", help="API base URL")
    ap.add_argument("--admin-token", default="dev-admin-token-change-me")

    ap.add_argument("--scenario-id", required=True)
    ap.add_argument("--mode", default="real", choices=["fixtures", "real"])
    ap.add_argument("--intensity", type=int, default=80)

    ap.add_argument("--target-ticks", type=int, default=300)
    ap.add_argument(
        "--tick-ms-base",
        type=int,
        default=1000,
        help="Only used if API status lacks tick_index/tick; tick ~= sim_time_ms / tick_ms_base",
    )

    ap.add_argument("--equivalent", default="UAH")
    ap.add_argument("--out-dir", default=str(Path(".local-run") / "analysis" / "reference-runs"))
    ap.add_argument("--timeout-sec", type=int, default=30)
    ap.add_argument("--poll-sec", type=float, default=2.0)
    ap.add_argument("--max-wall-sec", type=int, default=1800, help="Safety stop if target ticks never reached")

    args = ap.parse_args()

    headers = {
        "X-Admin-Token": args.admin_token,
        "Content-Type": "application/json",
    }

    _ensure_scenario_exists(
        base_url=args.base_url,
        headers=headers,
        scenario_id=args.scenario_id,
        timeout_sec=args.timeout_sec,
    )

    integrity_before = _integrity_verify(
        base_url=args.base_url,
        headers=headers,
        equivalent=args.equivalent,
        timeout_sec=args.timeout_sec,
    )

    run_id = _start_run(
        base_url=args.base_url,
        headers=headers,
        scenario_id=args.scenario_id,
        mode=args.mode,
        intensity=args.intensity,
        timeout_sec=args.timeout_sec,
    )
    print(f"run_id={run_id}")

    started = time.time()
    last_tick = None

    while True:
        st = _http_json(
            base_url=args.base_url,
            method="GET",
            path=f"/simulator/runs/{run_id}",
            headers=headers,
            timeout_sec=args.timeout_sec,
        )
        st = st if isinstance(st, dict) else {}

        tick, tick_src = _tick_from_status(st, tick_ms_base=int(args.tick_ms_base))
        last_tick = tick
        tick_s = str(tick) if tick is not None else "None"
        if tick_src == "derived":
            tick_s = tick_s + "~"

        print(
            f"state={st.get('state')} sim_time_ms={st.get('sim_time_ms')} tick={tick_s} ops_sec={st.get('ops_sec')}"
        )

        if isinstance(tick, int) and tick >= int(args.target_ticks):
            break
        if time.time() - started > int(args.max_wall_sec):
            break
        time.sleep(max(0.2, float(args.poll_sec)))

    reason = "target_ticks_reached" if isinstance(last_tick, int) and last_tick >= int(args.target_ticks) else "max_wall_sec_elapsed"
    print(f"stopping: reason={reason} target_ticks={int(args.target_ticks)}")
    _stop_run(
        base_url=args.base_url,
        headers=headers,
        run_id=run_id,
        reason=reason,
        timeout_sec=args.timeout_sec,
    )

    final_status = _wait_for_stop(
        base_url=args.base_url,
        headers=headers,
        run_id=run_id,
        timeout_sec=args.timeout_sec,
    )

    out_base = Path(args.out_dir)
    out_dir = out_base / run_id

    artifacts_info = _download_artifacts(
        base_url=args.base_url,
        origin=args.origin,
        admin_token=args.admin_token,
        headers=headers,
        run_id=run_id,
        out_dir=out_dir,
        timeout_sec=args.timeout_sec,
    )

    integrity_after = _integrity_verify(
        base_url=args.base_url,
        headers=headers,
        equivalent=args.equivalent,
        timeout_sec=args.timeout_sec,
    )

    trust_limits_before = _summarize_trust_limits_violations(integrity_before.details)
    trust_limits_after = _summarize_trust_limits_violations(integrity_after.details)

    events_analysis = _analyze_events(out_dir / "events.ndjson")
    summary_counters = _load_summary_counters(out_dir / "summary.json")

    report = {
        "scenario_id": args.scenario_id,
        "run_id": run_id,
        "mode": args.mode,
        "intensity": int(args.intensity),
        "target_ticks": int(args.target_ticks),
        "tick_ms_base": int(args.tick_ms_base),
        "final_status": final_status,
        "integrity_before": {"status": integrity_before.status, "details": integrity_before.details},
        "integrity_after": {"status": integrity_after.status, "details": integrity_after.details},
        "trust_limits_before": trust_limits_before,
        "trust_limits_after": trust_limits_after,
        "artifacts": artifacts_info,
        "summary_counters": summary_counters,
        "events": events_analysis,
        "report_generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    (out_dir / "reference_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        "summary="
        + json.dumps(
            {
                "scenario_id": args.scenario_id,
                "run_id": run_id,
                "final_state": final_status.get("state"),
                "tick": final_status.get("tick") or final_status.get("tick_index") or "?",
                "integrity_before": integrity_before.status,
                "integrity_after": integrity_after.status,
                "trust_limits_before": trust_limits_before,
                "trust_limits_after": trust_limits_after,
                "attempts_total": (summary_counters or {}).get("attempts_total"),
                "committed_total": (summary_counters or {}).get("committed_total"),
                "rejected_total": (summary_counters or {}).get("rejected_total"),
                "tx_updated": events_analysis.get("tx_updated"),
                "tx_failed": events_analysis.get("tx_failed"),
                "tx_failed_by_code": events_analysis.get("tx_failed_by_code"),
                "clearing_done": events_analysis.get("clearing_done"),
                "cleared_cycles_total": events_analysis.get("cleared_cycles_total"),
                "cleared_amount_total": events_analysis.get("cleared_amount_total"),
            },
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
