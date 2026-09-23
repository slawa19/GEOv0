from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import URL, make_url


@dataclass(frozen=True)
class RunWindow:
    started_at: datetime
    stopped_at: datetime


def _parse_dt(s: Any) -> datetime:
    """Accept both what a driver returns and what an API returns.

    PostgreSQL's `timestamptz` arrives from asyncpg as an aware `datetime`; the same instant coming
    back from the HTTP API is an ISO string. Programme 017 moved this analysis onto PostgreSQL, so
    both shapes reach here and coercing one into the other is the whole of this function.
    """

    if isinstance(s, datetime):
        return s
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


def _download(
    *,
    origin: str,
    url_path: str,
    headers: dict[str, str],
    out_path: Path,
    timeout_sec: int = 30,
) -> None:
    url = origin.rstrip("/") + "/" + url_path.lstrip("/")
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)

    with urllib.request.urlopen(req, timeout=max(1, int(timeout_sec))) as resp:
        out_path.write_bytes(resp.read())


#: Bounded, because an unbounded connect is how this script hangs after a successful run with the
#: artefacts already downloaded and nothing said about why.
_DB_CONNECT_TIMEOUT_SECONDS = 10.0
_DB_STATEMENT_TIMEOUT_SECONDS = 120.0


def _database_dsn(database_url: str) -> str:
    """A libpq DSN for asyncpg, from the same URL the application is configured with."""

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise SystemExit(
            f"This analysis reads PostgreSQL; --database-url names {url.get_backend_name()!r}. "
            f"Programme 017 removed the SQLite engine."
        )
    return URL.create(
        "postgresql",
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
    ).render_as_string(hide_password=False)


async def _connect(database_url: str) -> Any:
    """Open the analysis connection, telling a stopped cluster apart from a wrong credential."""

    import asyncpg

    try:
        return await asyncpg.connect(
            _database_dsn(database_url), timeout=_DB_CONNECT_TIMEOUT_SECONDS
        )
    except (asyncpg.InvalidPasswordError, asyncpg.InvalidCatalogNameError) as exc:
        raise SystemExit(f"The analysis database refused this connection: {exc}") from exc
    except (OSError, socket.gaierror, asyncio.TimeoutError, TimeoutError) as exc:
        raise SystemExit(
            f"PostgreSQL is not reachable for the post-run analysis ({type(exc).__name__}: {exc}). "
            f"Start the cluster - docs/ru/backend/postgres-local-portable.md section 3."
        ) from exc


async def _load_run_window(db: Any, run_id: str) -> RunWindow | None:
    row = await db.fetchrow(
        "SELECT started_at, stopped_at FROM simulator_runs WHERE run_id = $1",
        run_id,
        timeout=_DB_STATEMENT_TIMEOUT_SECONDS,
    )
    if not row or not row["started_at"] or not row["stopped_at"]:
        return None
    return RunWindow(
        started_at=_parse_dt(row["started_at"]), stopped_at=_parse_dt(row["stopped_at"])
    )


async def _collect_payment_amounts(db: Any, window: RunWindow, equivalent: str) -> list[float]:
    # Narrowed in SQL rather than in Python: on PostgreSQL the whole PAYMENT history of a seeded
    # community would otherwise cross the wire to be discarded here.
    rows = await db.fetch(
        "SELECT payload, created_at FROM transactions "
        "WHERE type = 'PAYMENT' AND created_at BETWEEN $1 AND $2",
        window.started_at,
        window.stopped_at,
        timeout=_DB_STATEMENT_TIMEOUT_SECONDS,
    )

    out: list[float] = []
    eq = str(equivalent).upper()

    for row in rows:
        payload = row["payload"]
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


def _tick_from_status(st: dict[str, Any]) -> tuple[int | None, str]:
    """Best-effort tick extraction.

    RunStatus currently doesn't expose tick_index, so we fall back to
    deriving tick from sim_time_ms (1 tick = 1000ms in simulator MVP).
    """

    tick = st.get("tick_index")
    if isinstance(tick, int):
        return tick, "api"
    tick = st.get("tick")
    if isinstance(tick, int):
        return tick, "api"

    sim_time_ms = st.get("sim_time_ms")
    if isinstance(sim_time_ms, int):
        return max(0, int(sim_time_ms) // 1000), "derived"
    try:
        if sim_time_ms is not None:
            return max(0, int(float(str(sim_time_ms))) // 1000), "derived"
    except Exception:
        pass
    return None, "missing"


def _status_from_summary(summary: dict[str, Any]) -> dict[str, Any] | None:
    st = summary.get("status")
    return st if isinstance(st, dict) else None


def _fmt_rate(n: int | None, d: int | None) -> str:
    if not isinstance(n, int) or not isinstance(d, int) or d <= 0:
        return "n/a"
    return f"{(n / d) * 100:.1f}%"


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
    ap.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help=(
            "PostgreSQL URL used for the post-run payment analysis; defaults to DATABASE_URL, "
            "which is what the launcher exports"
        ),
    )
    ap.add_argument("--timeout-sec", type=int, default=30, help="HTTP client timeout (seconds)")
    args = ap.parse_args()

    headers = {
        "X-Admin-Token": args.admin_token,
        "Content-Type": "application/json",
    }

    scenarios = _http_json(
        base_url=args.base_url,
        method="GET",
        path="/simulator/scenarios",
        headers=headers,
        timeout_sec=args.timeout_sec,
    )
    scenario_ids = {item.get("scenario_id") for item in scenarios.get("items", [])}
    if args.scenario_id not in scenario_ids:
        raise SystemExit(f"Scenario not found: {args.scenario_id}")

    # NOTE: RuntimeError from _http_json includes raw JSON detail in the message.
    # We keep this script dependency-free (no requests) and parse it best-effort.
    try:
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
            timeout_sec=args.timeout_sec,
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
                        base_url=args.base_url,
                        method="GET",
                        path="/simulator/runs/active",
                        headers=headers,
                        timeout_sec=args.timeout_sec,
                    )
                    active_run_id = active.get("run_id") if isinstance(active, dict) else None
                except Exception:
                    active_run_id = None

                print("cannot start run: too many active simulator runs (E008)")
                if active_run_id:
                    print(f"active_run_id={active_run_id}")
                    print(
                        "stop it explicitly via: POST /api/v1/simulator/runs/<run_id>/stop?source=cli&reason=free_slot"
                    )
                else:
                    print("could not fetch active run id via GET /api/v1/simulator/runs/active")
                return 2
        raise

    run_id = run["run_id"]
    print(f"run_id={run_id}")

    end_at = time.time() + max(1, int(args.run_seconds))
    while time.time() < end_at:
        st = _http_json(
            base_url=args.base_url,
            method="GET",
            path=f"/simulator/runs/{run_id}",
            headers=headers,
            timeout_sec=args.timeout_sec,
        )
        tick, tick_src = _tick_from_status(st)
        tick_s = str(tick) if tick is not None else "None"
        if tick_src == "derived":
            tick_s = tick_s + "~"
        print(
            f"state={st.get('state')} sim_time_ms={st.get('sim_time_ms')} tick={tick_s} ops_sec={st.get('ops_sec')}"
        )
        time.sleep(2)

    print(f"stopping: reason=run_seconds_elapsed seconds={int(args.run_seconds)}")
    _http_json(
        base_url=args.base_url,
        method="POST",
        path=f"/simulator/runs/{run_id}/stop?source=cli&reason=run_seconds_elapsed",
        headers=headers,
        timeout_sec=max(args.timeout_sec, 60),
    )

    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        st = _http_json(
            base_url=args.base_url,
            method="GET",
            path=f"/simulator/runs/{run_id}",
            headers=headers,
            timeout_sec=args.timeout_sec,
        )
        last = st
        if st.get("state") in ("stopped", "error"):
            break
        time.sleep(1)

    tick, tick_src = _tick_from_status(last or {})
    tick_s = str(tick) if tick is not None else "None"
    if tick_src == "derived":
        tick_s = tick_s + "~"
    print(f"final_state={last.get('state')} sim_time_ms={last.get('sim_time_ms')} tick={tick_s}")

    idx = _http_json(
        base_url=args.base_url,
        method="GET",
        path=f"/simulator/runs/{run_id}/artifacts",
        headers=headers,
        timeout_sec=args.timeout_sec,
    )
    items = {it["name"]: it for it in idx.get("items", [])}
    print("artifacts=", ",".join(sorted(items.keys())))

    out_base = Path(args.out_dir)
    out_dir = out_base / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in ("last_tick.json", "status.json", "summary.json", "events.ndjson"):
        it = items.get(name)
        if not it:
            continue
        _download(
            origin=args.origin,
            url_path=it["url"],
            headers={"X-Admin-Token": args.admin_token},
            out_path=out_dir / name,
            timeout_sec=args.timeout_sec,
        )

    print(f"downloaded_dir={out_dir}")

    summary_path = out_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            st = _status_from_summary(summary) or {}
            attempts = st.get("attempts_total")
            committed = st.get("committed_total")
            rejected = st.get("rejected_total")
            timeouts = st.get("timeouts_total")
            errors = st.get("errors_total")
            sim_time_ms = st.get("sim_time_ms")
            ops_sec = st.get("ops_sec")

            print(
                "run.counters="
                f"attempts={attempts} committed={committed} rejected={rejected} "
                f"timeouts={timeouts} errors={errors} committed_rate={_fmt_rate(committed, attempts)}"
            )
            print(f"run.sim_time_ms={sim_time_ms} run.ops_sec={ops_sec}")
        except Exception:
            print("run.counters=unavailable (failed to parse summary.json)")

    if not str(args.database_url).strip():
        raise SystemExit(
            "No database URL for the post-run analysis: pass --database-url or set DATABASE_URL "
            "(the launcher exports it)."
        )

    async def _read() -> tuple[RunWindow | None, list[float]]:
        db = await _connect(args.database_url)
        try:
            window = await _load_run_window(db, run_id)
            if not window:
                return None, []
            return window, await _collect_payment_amounts(
                db, window, equivalent=args.equivalent
            )
        finally:
            await db.close()

    window, amounts = asyncio.run(_read())

    if not window:
        print("db_window=missing")
        return 0

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
    import sys

    if sys.platform == "win32":
        # asyncpg needs the selector loop on Windows; the rest of this repository's entrypoints
        # (`scripts/seed_db.py`) set the same policy for the same reason.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(main())
