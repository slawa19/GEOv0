"""Audit-only observer: no collection, fixture, timeout, result or app-policy changes."""
from __future__ import annotations
import collections
import functools
import json
import os
from pathlib import Path
import time
import pytest

_OUT = None
_NODE = None
_PHASE = None
_SPANS = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])

def _emit(kind, **fields):
    if _OUT is not None:
        _OUT.write(json.dumps(dict(kind=kind, monotonic=time.perf_counter(), **fields), ensure_ascii=True) + "\n")

def pytest_configure(config):
    global _OUT
    path = Path(os.environ["GEO_BACKEND_PROFILE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    _OUT = path.open("w", encoding="utf-8", buffering=65536)
    _emit("session", pid=os.getpid(), python=__import__("sys").version, instrumentation="phase-fixture-span-v1")

def _wrap_async(owner, name, label, state=False):
    original = getattr(owner, name)
    @functools.wraps(original)
    async def observed(*args, **kwargs):
        started, cpu = time.perf_counter(), time.process_time()
        suffix = ""
        if state:
            try:
                runtime, run_id = args[0], args[1]
                run = runtime._runs.get(run_id)
                suffix = ":" + str(getattr(run, "state", "missing"))
            except Exception:
                suffix = ":unreadable"
        try:
            return await original(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            stats = _SPANS[label + suffix]
            stats[0] += 1
            stats[1] += elapsed
            stats[2] += time.process_time() - cpu
            stats[3] = max(stats[3], elapsed)
    setattr(owner, name, observed)

def pytest_collection_finish(session):
    from app.core.simulator import runtime_impl, storage
    from app.core.simulator.artifacts import ArtifactsManager
    _wrap_async(runtime_impl._SimulatorRuntimeBase, "stop", "runtime.stop", state=True)
    _wrap_async(storage, "upsert_run", "storage.upsert_run")
    _wrap_async(ArtifactsManager, "finalize_run_artifacts", "artifacts.finalize")
    _wrap_async(ArtifactsManager, "stop_events_writer", "artifacts.stop_writer")
    for item in session.items:
        _emit("item", nodeid=item.nodeid, fixtures=item.fixturenames)
    _OUT.flush()

def _registry():
    try:
        from app.core.simulator.runtime import runtime
        with runtime._lock:
            return dict(collections.Counter(str(run.state) for run in runtime._runs.values()))
    except Exception:
        return None

def _phase_start(item, phase):
    global _NODE, _PHASE
    _NODE, _PHASE = item.nodeid, phase
    _SPANS.clear()
    _emit("phase_start", nodeid=_NODE, phase=phase, registry=_registry())
    _OUT.flush()
    return time.perf_counter(), time.process_time()

def _phase_end(started):
    global _PHASE
    _emit("phase_end", nodeid=_NODE, phase=_PHASE, wall=time.perf_counter()-started[0], cpu=time.process_time()-started[1], spans=dict(_SPANS), registry=_registry())
    _OUT.flush()
    _PHASE = None

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    started = _phase_start(item, "setup")
    yield
    _phase_end(started)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    started = _phase_start(item, "call")
    yield
    _phase_end(started)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    started = _phase_start(item, "teardown")
    yield
    _phase_end(started)

@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    started, cpu = time.perf_counter(), time.process_time()
    yield
    _emit("fixture_setup", nodeid=_NODE, phase=_PHASE, name=fixturedef.argname, scope=fixturedef.scope, wall=time.perf_counter()-started, cpu=time.process_time()-cpu)

def pytest_runtest_logreport(report):
    _emit("report", nodeid=report.nodeid, phase=report.when, duration=report.duration, outcome=report.outcome)

def pytest_sessionfinish(session, exitstatus):
    _emit("sessionfinish", exitstatus=int(exitstatus))
    _OUT.flush()
    _OUT.close()
