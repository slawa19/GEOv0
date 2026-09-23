"""The stage-2 catalogue recorder: every test's outcome on disk AS IT HAPPENS, and resumable (T1702).

WHY IT EXISTS. Programme 017 stage 2 had to turn the per-file inventory's predictions into a
measurement: run the whole default tier on PostgreSQL and record, for every test that does not pass,
what happened. Two facts of this machine make pytest's own reporting insufficient for that:

* a hung PostgreSQL test waits forever, so the tier needs a per-test timeout (`pytest-timeout`,
  `pytest.ini`);
* on Windows pytest-timeout has no `signal` method, only `thread`, and the `thread` method ENDS THE
  PROCESS with `os._exit` after dumping the stacks. Junit XML, `-r` summaries and `--durations` die
  with it.

So this plugin appends one JSON line per event to `GEO_CATALOGUE_JSONL`, closed after every write, and
on the next start it deselects everything the file already has an answer for - including the test
that was started and never finished, which is recorded as `hung`. Re-running the same command until
the file carries a `sessionfinish` line gives the whole tier, one timeout at a time.

It is a MEASUREMENT INSTRUMENT, loaded only with `-p tests.stage2_catalogue_recorder`; no gate loads
it. WHAT IT DOES NOT SEE: a resumed run starts a new process and a fresh tier schema, so a failure
caused by residue from a test that ran before the kill is not reproduced after it; and the session
aggregate in `tests/contract/test_p011_responses_conform_to_the_canon.py` sees only the bodies of its
own process. Both are named in `specs/017-postgres-only-engine/stage2-catalogue.md`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

_ENV = "GEO_CATALOGUE_JSONL"

#: The process that owns the file. Several tests of the tier start a CHILD pytest (the runtime
#: conformance module re-runs itself, the marker guards run a subprocess), and the child inherits
#: both the environment and `PYTEST_ADDOPTS`. Measured on the first catalogue run, 2026-09-23: the
#: child wrote its own `sessionfinish` into the parent's file and marked the parent's running test
#: `hung`. The first process to load the plugin claims the file; every descendant sees the claim in
#: its inherited environment and records nothing.
_OWNER_ENV = "GEO_CATALOGUE_OWNER_PID"


def _path() -> Path | None:
    if os.environ.get(_OWNER_ENV) != str(os.getpid()):
        return None
    value = os.environ.get(_ENV, "").strip()
    return Path(value) if value else None


def _write(record: dict) -> None:
    path = _path()
    if path is None:
        return
    record.setdefault("t", time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read() -> list[dict]:
    path = _path()
    if path is None or not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def pytest_configure(config: pytest.Config) -> None:
    if _OWNER_ENV in os.environ:
        return  # a child of the recording process: inert, see `_OWNER_ENV`
    os.environ[_OWNER_ENV] = str(os.getpid())
    if _path() is None:
        raise pytest.UsageError(
            f"tests.stage2_catalogue_recorder was loaded without {_ENV}: nothing would be recorded."
        )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items) -> None:
    if _path() is None:
        return
    records = _read()
    started = {r["nodeid"] for r in records if r.get("kind") == "start"}
    finished = {r["nodeid"] for r in records if r.get("kind") == "finish"}
    already_hung = {r["nodeid"] for r in records if r.get("kind") == "hung"}
    for nodeid in sorted(started - finished - already_hung):
        # Started in an earlier process and never finished: that process was ended by the timeout.
        _write({"kind": "hung", "nodeid": nodeid})
        already_hung.add(nodeid)
    answered = finished | already_hung
    keep, drop = [], []
    for item in items:
        (drop if item.nodeid in answered else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
    _write(
        {
            "kind": "collected",
            "selected_now": len(keep),
            "already_answered": len(drop),
        }
    )


def pytest_collectreport(report) -> None:
    if report.failed:
        _write(
            {
                "kind": "collect-error",
                "nodeid": report.nodeid,
                "longrepr": str(report.longrepr),
            }
        )


def pytest_runtest_logstart(nodeid, location) -> None:
    _write({"kind": "start", "nodeid": nodeid})


def pytest_runtest_logreport(report) -> None:
    record = {
        "kind": "report",
        "nodeid": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "duration": round(report.duration, 3),
    }
    if hasattr(report, "wasxfail"):
        record["wasxfail"] = str(report.wasxfail)
    if report.outcome != "passed":
        record["longrepr"] = str(report.longrepr)
        # An HTTP 500 carries its cause only in the captured log, not in the assertion's traceback.
        record["sections"] = {
            name: content[-12000:] for name, content in report.sections if content
        }
    _write(record)


def pytest_runtest_logfinish(nodeid, location) -> None:
    _write({"kind": "finish", "nodeid": nodeid})


def pytest_sessionfinish(session, exitstatus) -> None:
    _write({"kind": "sessionfinish", "exitstatus": int(exitstatus)})
