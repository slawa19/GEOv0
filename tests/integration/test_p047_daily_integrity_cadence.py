"""047 `T4701` (owner decision «B», 2026-10-18): the integrity loop runs at start and then once a day, and a stored
reconciliation result of a daily cadence does not read stale between two runs.

The interval is read where it was before (`app/core/maintenance_jobs.py` `_integrity_loop`) and the freshness policy
follows it (`result_freshness_threshold`, 2 x interval + 5 min, 030 F-030-8). What changed is the default. Before it a
result older than 15 minutes read `warning`; with a daily run and a 15-minute policy every equivalent would read
`warning` for almost the whole day, so the policy must follow the interval, not stay at 300 s.

RED BEFORE (on `b103fee9`, default 300 s): the result one interval old reads `warning`, and the precondition on the
declared default fails. The existing freshness test (`test_p030_s1_detection_postgres.py::
test_f030_8_an_errored_or_stale_reconciliation_is_a_warning_and_holds_nothing`) ages its result by
`result_freshness_threshold() + 60 s`, so it assumed no interval and needed no change.

WHAT THIS DOES NOT SEE: the loop's waiting itself; the start-up run, its retry on a held lock and the interval wait
are held by `tests/unit/test_p047_integrity_loop_startup_retry.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert, select

from app.config import Settings, settings
from app.core.ledger.reconciliation import PASSED, RESULT_RUN_ALLOWANCE, result_freshness_threshold
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import debt_reconciliation_results

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
DAY = timedelta(seconds=86400)


async def _passed_aged(db_session, code: str, age: timedelta) -> None:
    db_session.add(Equivalent(code=code, description=code, precision=2))
    await db_session.commit()
    eq_id = (await db_session.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
    at = datetime.now(timezone.utc) - age
    await db_session.execute(insert(debt_reconciliation_results).values(
        id=uuid.uuid4(), equivalent_id=eq_id, status=PASSED, fingerprint="7" * 64, detail={}, checked_at=at,
        last_checked_at=at, is_latest=True))
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_daily_result_reads_healthy_until_two_intervals_and_a_run_and_stale_after(client, db_session) -> None:
    """A PASSED one interval and ten minutes old - the age just before the next daily run - is `healthy`; one minute
    inside the policy is `healthy`, one minute past it is `warning` with the reason. The two bounds pin the policy
    at 2 x 86400 s + 5 min within a minute. MUTATION: the policy back to a fixed 300 s interval - the first two read
    `warning`, red; drop the freshness branch of `_reconciliation_view` - the third reads `healthy`, red."""

    declared = Settings.model_fields["INTEGRITY_CHECKPOINT_INTERVAL_SECONDS"].default
    assert declared == 86400 and settings.INTEGRITY_CHECKPOINT_INTERVAL_SECONDS == declared, (
        f"stand: the default is {declared} and this process runs with "
        f"{settings.INTEGRITY_CHECKPOINT_INTERVAL_SECONDS}; unset INTEGRITY_CHECKPOINT_INTERVAL_SECONDS for the tier")
    policy = 2 * DAY + RESULT_RUN_ALLOWANCE

    minute = timedelta(minutes=1)
    await _passed_aged(db_session, "P47D", DAY + 10 * minute)
    await _passed_aged(db_session, "P47E", policy - minute)
    await _passed_aged(db_session, "P47S", policy + minute)

    response = await client.get("/api/v1/integrity/summary", headers=ADMIN)
    assert response.status_code == 200, response.text
    statuses = {row["equivalent"]: row["status"] for row in response.json()["equivalents"]}
    assert {code: statuses.get(code) for code in ("P47D", "P47E", "P47S")} == {
        "P47D": "healthy", "P47E": "healthy", "P47S": "warning"}, statuses

    response = await client.get("/api/v1/integrity/status", headers=ADMIN)
    assert response.status_code == 200, response.text
    stale = [alert for alert in response.json()["alerts"] if "older than the freshness policy" in alert]
    assert [code for code in ("P47D", "P47E", "P47S") if any(code in alert for alert in stale)] == ["P47S"], stale
    assert result_freshness_threshold() == policy
