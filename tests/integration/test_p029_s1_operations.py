"""029 S1: checkpoint retention (F-029-2, № 87), logging at start (F-029-3, № 60), `/admin/config` (F-029-4, № 61).

The logging test reads a child process, because pytest hangs its own handlers on the root logger; it shows that
`app.main` configures logging, not what a deployment's `--log-config` then does to it.
"""

import os
import re
import subprocess
import sys
from datetime import timedelta

import pytest
from sqlalchemy import func, select

import app.core.integrity as integrity
from app.config import settings
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
# LOG_LEVEL and the integrity job's switch and period are taken once at start; the other three have no reader.
NOT_RUNTIME = ("LOG_LEVEL", "RECOVERY_ENABLED", "RECOVERY_INTERVAL_SECONDS", "PAYMENT_TX_STUCK_TIMEOUT_SECONDS")
NOT_RUNTIME += ("INTEGRITY_CHECKPOINT_ENABLED", "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS")


async def _rows_per_equivalent(db_session) -> dict:
    counted = select(IntegrityCheckpoint.equivalent_id, func.count()).group_by(IntegrityCheckpoint.equivalent_id)
    return dict((await db_session.execute(counted)).all())


@pytest.mark.parametrize("ttl_seconds", [0, -3600])  # -3600: even a term that would cover the new rows keeps them
async def test_checkpoints_past_the_term_are_deleted_and_the_latest_of_each_equivalent_stays(
    db_session, monkeypatch, ttl_seconds
) -> None:
    db_session.add_all([Equivalent(code=code, description=code, precision=2) for code in ("P29A", "P29B")])
    await db_session.commit()
    assert await integrity.compute_and_store_integrity_checkpoints(db_session) >= 2
    assert await integrity.compute_and_store_integrity_checkpoints(db_session) >= 2
    kept = await _rows_per_equivalent(db_session)
    assert set(kept.values()) == {2}, kept  # control: within the real term nothing is deleted
    monkeypatch.setattr(integrity, "INTEGRITY_CHECKPOINT_TTL", timedelta(seconds=ttl_seconds), raising=False)
    written = await integrity.compute_and_store_integrity_checkpoints(db_session)
    after = await _rows_per_equivalent(db_session)
    assert len(after) == written == len(kept) and set(after.values()) == {1}, after


@pytest.mark.parametrize(("level", "info_is_written"), [("INFO", True), ("WARNING", False)])
def test_the_application_configures_logging_at_start_from_log_level(level, info_is_written) -> None:
    probe = (
        "import logging, app.main\n"
        "log = logging.getLogger('app.api.v1.auth')\n"
        "log.info('p029 info probe'); log.warning('p029 warning probe')\n"
        "print(logging.getLevelName(logging.getLogger().level))\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", probe], env={**os.environ, "LOG_LEVEL": level}, capture_output=True, text=True, timeout=120
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == [level], (child.stdout, child.stderr)
    stamp = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} "
    assert re.search(stamp + r"WARNING app\.api\.v1\.auth p029 warning probe$", child.stderr, re.M), child.stderr
    assert bool(re.search(stamp + r"INFO app\.api\.v1\.auth p029 info probe$", child.stderr, re.M)) is info_is_written


async def test_admin_config_refuses_the_keys_nothing_reads_after_start(client, monkeypatch) -> None:
    for key in NOT_RUNTIME:
        before = getattr(settings, key)
        value = "DEBUG" if key == "LOG_LEVEL" else (not before if isinstance(before, bool) else before + 1)
        response = await client.patch("/api/v1/admin/config", headers=ADMIN, json={"updates": {key: value}})
        assert response.status_code == 400, (key, response.text)
        assert response.json()["error"]["message"] == f"Config key not mutable: {key}"
        assert getattr(settings, key) == before
    listed = (await client.get("/api/v1/admin/config", headers=ADMIN)).json()["items"]
    assert {item["key"] for item in listed if not item["mutable"]} == set(NOT_RUNTIME)
    assert sum(item["mutable"] for item in listed) == 6
    # Positive control: a live key is still changed, and the change is in force.
    monkeypatch.setattr(settings, "CLEARING_ENABLED", True)
    response = await client.patch("/api/v1/admin/config", headers=ADMIN, json={"updates": {"CLEARING_ENABLED": False}})
    assert response.status_code == 200 and response.json()["updated"] == ["CLEARING_ENABLED"], response.text
    flags = await client.get("/api/v1/admin/feature-flags", headers=ADMIN)
    assert flags.json()["clearing_enabled"] is False
