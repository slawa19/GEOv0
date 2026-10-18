"""The hub's maintenance loops - integrity checkpoints with debt reconciliation, and periodic clearing - and the
decision which of them this process starts. Moved from `app/main.py` unchanged (024 `T2414.2`); the supervisor
they run under is `app/utils/background_jobs.py`."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from app.config import settings
from app.utils.background_jobs import (
    _record_background_job_event,
    _start_supervised_background_task,
    background_job_states as _background_job_states,
)

logger = logging.getLogger(__name__)


def _emit_integrity_metric(result: str) -> None:
    try:
        from app.utils.metrics import RECOVERY_EVENTS_TOTAL

        RECOVERY_EVENTS_TOTAL.labels(
            event="integrity_checkpoints",
            result=result,
        ).inc()
    except Exception:
        logger.debug(
            "integrity.checkpoints_metric_failed result=%s",
            result,
            exc_info=True,
        )


async def _run_debt_reconciliation_once(session_factory, *, reason: str) -> tuple[bool, str | None]:
    """Programme 015 steps 5a and 5b: debt reconciliation, criteria (a) and (b) - detection of change made
    around the application, and of a recorded change that disagrees with its recorded intent.

    THE ONLY HOST in the application. Not `POST /integrity/verify`, which participants can call, and not the payment
    or clearing checkpoints, which run inside the money transaction. Outside it the operator runs the same entry point
    on demand (`scripts/reconcile_debts.py`, 047 `T4702`). It runs after the checkpoints - and since
    030 F-030-7 also after they failed - in fresh transactions of its own, and its result is its own row: it never enters a
    checkpoint's `checks`, `passed`, `status` or `alerts`, and never an audit row.

    Returns False on any ERROR - the run itself, an equivalent the verifier could not verify, or a FAILED
    whose hold reaction raised - and the caller then records the job as failed (024 `T2412.1`, R-024-4;
    until then the error was only logged and the job read `*_success`). An error still never becomes a
    result, and never turns the committed checkpoints into something else. A verdict (`FAILED`,
    `UNVERIFIABLE`) is not an error of the job. It reads every equivalent, active or not - the T1544
    operator stop is a refusal to MOVE money.

    030 `T3091`: also returns the equivalents whose verification erred, comma-joined - None when the run itself
    failed (or did not name them), so every equivalent's verdict is unknown.
    """

    from app.core.ledger.reconciliation import run_scheduled_reconciliation

    try:
        counts = await run_scheduled_reconciliation(session_factory)
    except Exception:  # noqa: BLE001 - an error is recorded as an error; no result is substituted
        logger.exception("integrity.debt_reconciliation_failed reason=%s", reason)
        _emit_integrity_metric(f"{reason}_debt_reconciliation_error")
        return False, None
    if counts["error"] or counts["hold_errors"]:
        logger.error(
            "integrity.debt_reconciliation_errors reason=%s errors=%d hold_errors=%d",
            reason,
            counts["error"],
            counts["hold_errors"],
        )
        _emit_integrity_metric(f"{reason}_debt_reconciliation_error")
        return False, ",".join(counts.get("errored_equivalents") or ()) or None
    return True, None


#: 047 fix-delta (2026-10-18, Codex §15 P1, class 1): the Redis lock's TTL when `INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS`
#: is unset. It was `max(30, interval)`; with the daily interval that is a day, and a process killed during its run left
#: the lock for a day, so its replacement skipped the start-up run. 300 s is the recovery budget the 300 s cadence had -
#: NOT a proven maximum duration of a run: a run longer than the TTL can still overlap one of another Redis-backed process
#: (the limitation stated at `record_outcome`). An explicit TTL stays the deployment's choice.
INTEGRITY_LOCK_TTL_DEFAULT_SECONDS = 300
#: 047 fix-delta: how long the loop waits before it retries a start-up run that found the lock held.
STARTUP_LOCK_RETRY_SECONDS = 30
#: ... and for how long past the lock's TTL it keeps retrying before it gives up into the ordinary interval.
STARTUP_LOCK_RETRY_MARGIN_SECONDS = 60


def _integrity_lock_ttl_seconds() -> int:
    ttl = int(settings.INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS)
    return ttl if ttl > 0 else INTEGRITY_LOCK_TTL_DEFAULT_SECONDS


async def _run_integrity_checkpoints_once(app: FastAPI, *, reason: str) -> bool | None:
    """One run of the checkpoints and the debt reconciliation under the distributed lock.

    True - the run completed cleanly; False - it ran and recorded a failure; None (047 fix-delta) - it did NOT run,
    because another holder has the lock, so the caller can tell a skipped run from one that happened."""

    from app.core.integrity import compute_and_store_integrity_checkpoints
    from app.db.session import AsyncSessionLocal
    from app.utils.distributed_lock import redis_distributed_lock
    from app.utils.exceptions import ConflictException

    lock_ttl_seconds = _integrity_lock_ttl_seconds()

    _emit_integrity_metric(f"{reason}_start")
    checkpoint_error: Exception | None = None
    try:
        async with redis_distributed_lock(
            getattr(app.state, "redis", None),
            "geo:integrity:checkpoints",
            ttl_seconds=lock_ttl_seconds,
            wait_timeout_seconds=0.0,
        ):
            try:  # 030 F-030-7: an error of the checkpoints does not cancel the reconciliation; both are recorded
                async with AsyncSessionLocal() as session:
                    await compute_and_store_integrity_checkpoints(session)
            except Exception as error:  # noqa: BLE001 - recorded below as the job's error, never as a success
                checkpoint_error = error
                logger.exception("integrity.checkpoints_failed reason=%s", reason)
                _emit_integrity_metric(f"{reason}_error")
            # After the checkpoints (committed or failed), and under the same distributed lock.
            reconciled, errored = await _run_debt_reconciliation_once(AsyncSessionLocal, reason=reason)
    except ConflictException:
        _emit_integrity_metric(f"{reason}_skipped_locked")
        # A skipped run proves nothing: a recorded failure (checkpoints or reconciliation) is kept, with its
        # event, until a run of this job completes cleanly (024 `T2412.1`, §15 fix-delta P2-1).
        if _background_job_states(app).get("integrity", {}).get("status") == "failed":
            return None
        _record_background_job_event(
            app,
            name="integrity",
            status="running",
            event=f"{reason}_skipped_locked",
        )
        return None
    except Exception as error:
        logger.exception("integrity.checkpoints_failed reason=%s", reason)
        _emit_integrity_metric(f"{reason}_error")
        _record_background_job_event(
            app,
            name="integrity",
            status="failed",
            event=f"{reason}_error",
            error=error,
        )
        return False

    if checkpoint_error is not None or not reconciled:
        failed = "checkpoints" if reconciled else "debt_reconciliation"
        failed = f"checkpoints_and_{failed}" if checkpoint_error and not reconciled else failed
        _record_background_job_event(
            app, name="integrity", status="failed", event=f"{reason}_{failed}_error", error=checkpoint_error
        )
        if errored:  # 030 T3091: the equivalents whose verdict is unknown; absent - the whole run's
            _background_job_states(app)["integrity"]["errored_equivalents"] = errored
        return False

    _emit_integrity_metric(f"{reason}_success")
    _record_background_job_event(
        app,
        name="integrity",
        status="running",
        event=f"{reason}_success",
    )
    return True


async def _stopped_within(stop_event: asyncio.Event, seconds: float) -> bool:
    """Wait up to `seconds` for the stop event: True if it was set, False if the time ran out."""

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _integrity_loop(app: FastAPI) -> None:
    """The start-up run, then one run every interval after the previous run completes.

    047 fix-delta (2026-10-18, Codex §15 P1): a start-up run that found the lock held - a run of another process, or
    the lock a killed process left until its TTL - is retried every `STARTUP_LOCK_RETRY_SECONDS` for up to the lock's
    TTL plus `STARTUP_LOCK_RETRY_MARGIN_SECONDS`, and only then does the loop give up into the ordinary interval, with
    a recorded event. Until 047 the interval was 300 s and the next periodic run was the retry; at a daily interval it
    is not. Periodic runs are not retried: a skipped periodic run means another holder is running one."""

    interval = int(
        settings.INTEGRITY_CHECKPOINT_INTERVAL_SECONDS or 300
    )
    stop = app.state._bg_stop_event
    ran = await _run_integrity_checkpoints_once(app, reason="startup")
    retries = -(-(_integrity_lock_ttl_seconds() + STARTUP_LOCK_RETRY_MARGIN_SECONDS) // STARTUP_LOCK_RETRY_SECONDS)
    while ran is None and retries > 0:
        if await _stopped_within(stop, STARTUP_LOCK_RETRY_SECONDS):
            return
        retries -= 1
        ran = await _run_integrity_checkpoints_once(app, reason="startup")
    if ran is None and _background_job_states(app).get("integrity", {}).get("status") != "failed":
        logger.error("integrity.startup_run_skipped_locked_gave_up interval_seconds=%d", interval)
        _record_background_job_event(app, name="integrity", status="running", event="startup_skipped_locked_gave_up")

    while not stop.is_set():
        if await _stopped_within(stop, interval):
            break
        await _run_integrity_checkpoints_once(app, reason="periodic")


async def _run_periodic_clearing_once(app: FastAPI) -> None:
    """Programme 023 (decisions 7, 9): one periodic clearing pass. A refusal or an error is recorded, never silent."""

    from app.core.clearing.runner import ClearingPeriodicRefused, run_periodic_clearing_pass
    from app.db.session import AsyncSessionLocal

    try:
        results = await run_periodic_clearing_pass(AsyncSessionLocal, getattr(app.state, "redis", None))
    except ClearingPeriodicRefused as refusal:
        logger.error("clearing.periodic_refused reason=%s", refusal.reason)
        _record_background_job_event(
            app, name="clearing", status="failed", event=f"refused_{refusal.reason}", error=refusal
        )
        return
    except Exception as error:
        logger.exception("clearing.periodic_failed")
        _record_background_job_event(app, name="clearing", status="failed", event="error", error=error)
        return
    from app.core.clearing.runner import InterruptReason

    failed = sorted(code for code, result in results.items() if result.reason == InterruptReason.ERROR)
    if failed:
        # Review P2-4: a pass stopped by an error (planner, stop/hold, perimeter, unknown commit) degrades health;
        # a budget, lease, re-plan or retry-budget interruption is ordinary and does not.
        logger.error("clearing.periodic_equivalents_failed count=%s", len(failed))
        _record_background_job_event(app, name="clearing", status="failed", event="pass_error")
        return
    interrupted = sorted(code for code, result in results.items() if result.status != "complete")
    _record_background_job_event(
        app,
        name="clearing",
        status="running",
        event="pass_interrupted" if interrupted else "pass_complete",
    )


async def _clearing_loop(app: FastAPI) -> None:
    interval = max(1, int(settings.CLEARING_PERIODIC_INTERVAL_SECONDS or 300))
    while not app.state._bg_stop_event.is_set():
        await _run_periodic_clearing_once(app)
        try:
            await asyncio.wait_for(app.state._bg_stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            continue


def debt_reconciliation_run_problem(app: FastAPI, equivalent_id) -> str | None:
    """030 F-030-8, `T3091`: why this process cannot vouch for the equivalent's stored verdict, or None.

    A failed run - any recorded failure except one of the checkpoints alone, narrowed to the equivalents it names
    when it names them. IMMEDIATE BUT PROCESS-LOCAL: a restart or another worker does not see it; the stored rows do,
    after the run allowance (`app/api/v1/integrity.py`, `_reconciliation_view`)."""

    state = _background_job_states(app).get("integrity", {})
    if state.get("status") != "failed" or str(state.get("event", "")).endswith("_checkpoints_error"):
        return None
    errored = state.get("errored_equivalents")
    if errored is not None and str(equivalent_id) not in errored.split(","):
        return None
    return "the last scheduled run ended in an error"


def _start_configured_background_tasks(app: FastAPI) -> None:
    # No payment recovery loop since programme 019, stage 4: the hub executes a payment as one
    # transaction and persists no intermediate state for it to finish (migration 030). The inert
    # `RECOVERY_*` settings were removed with the incidents screen (032 S5).
    if settings.INTEGRITY_CHECKPOINT_ENABLED:
        _start_supervised_background_task(
            app,
            name="integrity",
            coroutine_factory=lambda: _integrity_loop(app),
        )
    # Programme 023 (decision R1): the periodic clearing runner, started only where the deployment sets
    # `CLEARING_PERIODIC_ENABLED` - a separate hub; off by default and on simulator stands.
    if settings.CLEARING_PERIODIC_ENABLED:
        _start_supervised_background_task(
            app,
            name="clearing",
            coroutine_factory=lambda: _clearing_loop(app),
        )
