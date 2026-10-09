from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import simulator_state_dir
from app.schemas.simulator import SIMULATOR_API_VERSION, EpisodeProgress, RunStatus
from app.core.simulator.models import RunRecord
from app.core.simulator.scenario_equivalent import (
    effective_equivalent,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def repo_root() -> Path:
    # runtime_utils.py -> app/core/simulator/runtime_utils.py
    return Path(__file__).resolve().parents[3]


def local_state_dir() -> Path:
    """The simulator's runtime state directory: `SIMULATOR_STATE_DIR`, else the checkout's `.local-run/simulator`
    (ignored by .gitignore). 034 S1c: the override exists so that a session sharing the checkout - the test tier
    above all - does not write into the developer's directory (AGENTS.md §7, §12).

    The rule itself is `app.config.simulator_state_dir()`, and this only delegates to it: the scripts that need
    the path ask `app.config` directly, because importing this package starts the runtime (see there). The
    directory must be the simulator's own (see `Settings.SIMULATOR_STATE_DIR`)."""

    return simulator_state_dir()


FIXTURES_DIR = repo_root() / "fixtures" / "simulator"
SCENARIO_SCHEMA_PATH = FIXTURES_DIR / "scenario.schema.json"

# Real-mode guardrail defaults (PR-B hardening); `SIMULATOR_REAL_MAX_*` in `Settings` override them.
REAL_MAX_IN_FLIGHT_DEFAULT = 1
REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT = 3
REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT = 5
REAL_MAX_ERRORS_TOTAL_DEFAULT = 200


def new_run_id() -> str:
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{secrets.token_hex(4)}"


def edges_by_equivalent(raw: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    trustlines = raw.get("trustlines") or []
    out: dict[str, list[tuple[str, str]]] = {}
    for tl in trustlines:
        status = str(tl.get("status") or "active").strip().lower()
        if status != "active":
            continue
        eq = str(effective_equivalent(raw, tl) or "").strip().upper()
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


def episode_progress_of(run: RunRecord) -> list[EpisodeProgress] | None:
    """The run's `_real_story_progress` as typed entries ordered by the event index; None when nothing is tracked.

    A record this module cannot type is a bug of the writer, not a reason to fail the status read (pause, resume and stop
    answer with the status): it is skipped with a warning naming the event."""

    records = dict(run._real_story_progress)
    if not records:
        return None
    out: list[EpisodeProgress] = []
    for index in sorted(records):
        try:
            out.append(EpisodeProgress.model_validate({**records[index], "index": int(index)}))
        except ValueError:  # pydantic.ValidationError is a ValueError
            logging.getLogger(__name__).warning(
                "simulator.run_status.episode_progress_record_untyped run_id=%s event_index=%s", run.run_id, index, exc_info=True
            )
    return out or None


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
        stop_requested_at=getattr(run, "stop_requested_at", None),
        stop_source=getattr(run, "stop_source", None),
        stop_reason=getattr(run, "stop_reason", None),
        stop_client=getattr(run, "stop_client", None),
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
        episode_progress=episode_progress_of(run),
    )
