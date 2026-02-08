from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.schemas.simulator import SIMULATOR_API_VERSION, RunStatus
from app.core.simulator.models import RunRecord


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def repo_root() -> Path:
    # runtime_utils.py -> app/core/simulator/runtime_utils.py
    return Path(__file__).resolve().parents[3]


def local_state_dir() -> Path:
    # Ignored by .gitignore
    return repo_root() / ".local-run" / "simulator"


FIXTURES_DIR = repo_root() / "fixtures" / "simulator"
SCENARIO_SCHEMA_PATH = FIXTURES_DIR / "scenario.schema.json"

# Runner constants (MVP, see docs/ru/simulator/backend/runner-algorithm.md)
TICK_MS_BASE = 1000
ACTIONS_PER_TICK_MAX = 20
CLEARING_EVERY_N_TICKS = 25

# Real-mode guardrails (PR-B hardening). Values can be overridden via env vars.
REAL_MAX_IN_FLIGHT_DEFAULT = 1
REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT = 3
REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT = 5
REAL_MAX_ERRORS_TOTAL_DEFAULT = 200


def safe_int_env(name: str, default: int) -> int:
    """Parse int env var with a defensive fallback.

    Behavior is intentionally lenient and matches the prior local helpers:
    - missing / empty value -> default
    - any parsing error -> default
    """

    try:
        return int(os.getenv(name, str(default)) or str(default))
    except Exception:
        return int(default)


def new_run_id() -> str:
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{secrets.token_hex(4)}"


def edges_by_equivalent(raw: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    trustlines = raw.get("trustlines") or []
    out: dict[str, list[tuple[str, str]]] = {}
    for tl in trustlines:
        eq = str(tl.get("equivalent") or "").strip()
        if not eq:
            continue
        src = str(tl.get("from") or "").strip()
        dst = str(tl.get("to") or "").strip()
        if not src or not dst:
            continue
        out.setdefault(eq, []).append((src, dst))
    return out


def dict_to_last_error(raw: Optional[dict[str, Any]]):
    if not raw:
        return None
    # Expecting {code,message,at}
    if "at" not in raw:
        raw = dict(raw)
        raw["at"] = utc_now()
    return raw


def run_to_status(run: RunRecord) -> RunStatus:
    cutoff = time.time() - 60.0
    # Best-effort: timestamps are pruned on write; we only count here.
    errors_last_1m = sum(1 for ts in run._error_timestamps if ts >= cutoff)
    consec_stall = int(run._real_consec_all_rejected_ticks or 0)
    return RunStatus(
        api_version=SIMULATOR_API_VERSION,
        run_id=run.run_id,
        scenario_id=run.scenario_id,
        mode=run.mode,
        state=run.state,
        started_at=run.started_at,
        stopped_at=run.stopped_at,
        sim_time_ms=run.sim_time_ms,
        intensity_percent=run.intensity_percent,
        ops_sec=run.ops_sec,
        queue_depth=run.queue_depth,
        errors_total=run.errors_total,
        committed_total=run.committed_total,
        rejected_total=run.rejected_total,
        attempts_total=run.attempts_total,
        timeouts_total=run.timeouts_total,
        errors_last_1m=int(errors_last_1m),
        consec_all_rejected_ticks=(consec_stall if consec_stall > 0 else None),
        last_error=dict_to_last_error(run.last_error),
        last_event_type=run.last_event_type,
        current_phase=run.current_phase,
    )
