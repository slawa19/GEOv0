"""Programme 024, stage 0 fix-delta D1: an artifact download never leaves the run's artifacts dir.

`ArtifactsManager.get_artifact_path` checked containment with `str(p).startswith(str(base))`, so a
SIBLING directory sharing the prefix (`.../artifacts_x/...`) passed. The route takes `name` as a
plain path segment, but `%5C` decodes to a backslash, which is a separator on Windows, and a
`..` inside the name resolves out of the directory. Containment is now `Path.is_relative_to`
over both resolved ends; a refusal stays a 404 (`NotFoundException`), as before.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord
from app.utils.exceptions import NotFoundException


def _manager(tmp_path: Path) -> tuple[ArtifactsManager, Path]:
    base = tmp_path / "runs" / "r1" / "artifacts"
    base.mkdir(parents=True)
    (base / "summary.json").write_text("{}", encoding="utf-8")
    sibling = tmp_path / "runs" / "r1" / "artifacts_x"
    sibling.mkdir()
    (sibling / "secret.json").write_text("{}", encoding="utf-8")
    run = RunRecord(run_id="r1", scenario_id="s", mode="fixtures", state="stopped")
    run.artifacts_dir = base
    manager = ArtifactsManager(
        lock=threading.RLock(),
        runs={"r1": run},
        local_state_dir=lambda: tmp_path,
        utc_now=lambda: datetime.now(timezone.utc),
        db_enabled=lambda: False,
        logger=logging.getLogger(__name__),
    )
    return manager, base


@pytest.mark.parametrize(
    "name",
    [
        "../artifacts_x/secret.json",
        "..\\artifacts_x\\secret.json",  # what `%5C` decodes to
        "../../r1/artifacts_x/secret.json",
        "..",
    ],
)
def test_a_name_leaving_the_directory_is_not_found(tmp_path: Path, name: str) -> None:
    manager, _base = _manager(tmp_path)

    with pytest.raises(NotFoundException):
        manager.get_artifact_path(run_id="r1", name=name)


def test_an_artifact_inside_the_directory_is_served_as_before(tmp_path: Path) -> None:
    # Counter-check (anti-vacuum): the rule must still serve the files it exists for.
    manager, base = _manager(tmp_path)

    assert manager.get_artifact_path(run_id="r1", name="summary.json") == (base / "summary.json").resolve()
