r"""T1525 detector: SQLite SAVEPOINTs that run with no database transaction open.

WHY THIS IS IN THE REPOSITORY. This plugin produced the `113 -> 0` measurement that T1525 rests on
(`specs/015-financial-core-verification/t1525-measurements.md`). It lived in a scratch directory, so
the headline number could not be re-derived from a clone - evidence nobody else can reproduce is not
evidence. It is a measurement instrument, not part of the application and not part of any tier: it
is loaded explicitly with `-p`, and loading nothing changes no test.

WHAT IT MEASURES. On SQLite connections only, per test node id:

* ``savepoints_total``       - every SAVEPOINT statement;
* ``independent_savepoints`` - SAVEPOINT executed while ``sqlite3.Connection.in_transaction`` is
  False, i.e. the savepoint is STARTING the database transaction rather than nesting inside one.
  This is the T1525 defect: such a savepoint's RELEASE commits, and the root rollback that follows
  has nothing to undo;
* ``durable_releases``       - a RELEASE of such a savepoint after which ``in_transaction`` is False
  again, verified AFTER execution: the release really committed;
* ``released_then_rollback`` - a root ROLLBACK on the same SQLAlchemy Connection after one or more
  durable releases in the same root transaction. This is the shape that loses money;
* ``sites``                  - the ``app/`` and ``tests/`` frames that opened independent
  savepoints, so a finding points at code rather than at a count.

WHAT IT DOES NOT SEE, and these are limits of the number, not caveats about the code: a rollback
that bypasses SQLAlchemy's ``Connection`` (a raw DBAPI rollback, or a pool reset of a connection
whose SQLAlchemy transaction had already ended); SAVEPOINTs on non-SQLite backends; and work done by
background tasks, which is attributed to whatever test happens to be running when the statement
executes. ``released_then_rollback`` counts a close-with-open-transaction as a rollback, because
SQLAlchemy dispatches the same event. It reports a SHAPE, not proven harm.

It also perturbs what it measures: per-statement tracing widens the window between a read and the
write that follows it, so a race that is rare without the plugin can become visible under it. That
is useful (it is how the enrichment flake of part 4 was found) but it means timing results taken
under the plugin are not comparable with results taken without it.

USAGE

Run any pytest selection with the plugin loaded and point ``T1525_DETECTOR_OUT`` at a JSON file.
``scripts`` is importable from the repository root, so no PYTHONPATH is needed::

    $env:T1525_DETECTOR_OUT = ".local-run/test-runs/t1525-detector/detector.json"
    $env:TEST_DATABASE_URL  = "sqlite+aiosqlite:///./.local-run/test-runs/t1525-detector/test.db"
    $env:GEO_TEST_ARTIFACT_ROOT = ".local-run/test-runs/t1525-detector/artifacts"
    .\.venv\Scripts\python.exe -m pytest -p scripts.t1525_savepoint_detector `
      --basetemp .local-run/test-runs/t1525-detector/pytest `
      -o cache_dir=.local-run/test-runs/t1525-detector/cache `
      -q -m "not slow and not postgres"

Without ``T1525_DETECTOR_OUT`` the plugin still counts but writes nothing. The JSON holds
``totals``, ``nodes_with_independent_savepoints``, ``nodes_with_released_then_rollback`` and a
per-node breakdown. A clean tree is ``independent_savepoints: 0`` with a NON-ZERO
``savepoints_total`` and ``sqlite_statements`` - a run that observed nothing would otherwise read
the same as a run that observed nothing wrong.

Note that the test database must sit at ``.local-run/test-runs/<slug>/test.db``: the conftest URL
guard rejects a deeper path.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import weakref
from collections import Counter, defaultdict

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

_current = {"nodeid": "<outside-test>", "phase": "session"}
_stats: dict[str, Counter] = defaultdict(Counter)
_sites: dict[str, Counter] = defaultdict(Counter)
_rollback_sites: dict[str, Counter] = defaultdict(Counter)
_rollback_callers: dict[str, Counter] = defaultdict(Counter)
_totals: Counter = Counter()


class _ConnState:
    __slots__ = ("independent", "durable", "pending_release", "durable_sites")

    def __init__(self) -> None:
        self.independent: dict[str, str] = {}
        self.durable: list[str] = []
        self.durable_sites: list[str] = []
        self.pending_release: str | None = None


_state: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _st(conn) -> _ConnState:
    st = _state.get(conn)
    if st is None:
        st = _ConnState()
        _state[conn] = st
    return st


def _raw_sqlite(conn):
    """The underlying `sqlite3.Connection`, through aiosqlite's adapter when there is one."""
    try:
        dbapi = conn.connection.dbapi_connection
    except Exception:
        return None
    if isinstance(dbapi, sqlite3.Connection):
        return dbapi
    inner = getattr(dbapi, "_connection", None)
    raw = getattr(inner, "_conn", None)
    if isinstance(raw, sqlite3.Connection):
        return raw
    return None


_REPO_MARKERS = (os.sep + "app" + os.sep, os.sep + "tests" + os.sep)


def _site() -> str:
    """Nearest repo frames, walking into the suspended parent greenlet for async callers."""
    frames = []
    try:
        frame = sys._getframe(2)
        while frame is not None:
            frames.append(frame)
            frame = frame.f_back
        try:
            import greenlet

            current = greenlet.getcurrent()
            parent = current.parent
            depth = 0
            while parent is not None and depth < 3:
                parent_frame = parent.gr_frame
                while parent_frame is not None:
                    frames.append(parent_frame)
                    parent_frame = parent_frame.f_back
                parent = parent.parent
                depth += 1
        except Exception:
            pass
    except Exception:
        return "<unknown>"

    picked = []
    for frame in frames:
        filename = frame.f_code.co_filename
        if any(marker in filename for marker in _REPO_MARKERS) and "site-packages" not in filename:
            relative = filename.split(os.sep + "GEOv0" + os.sep, 1)[-1].replace(os.sep, "/")
            picked.append(f"{relative}:{frame.f_lineno}:{frame.f_code.co_name}")
            if len(picked) >= 6:
                break
    return " <- ".join(picked) if picked else "<no repo frame>"


def _name_after(tokens: list[str], keyword_index: int) -> str | None:
    rest = tokens[keyword_index + 1 :]
    if rest and rest[0].upper() == "SAVEPOINT":
        rest = rest[1:]
    return rest[0].strip('"`[]') if rest else None


@event.listens_for(Engine, "begin")
def _on_begin(conn):
    if conn.dialect.name != "sqlite":
        return
    _state[conn] = _ConnState()


@event.listens_for(Engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):
    if conn.dialect.name != "sqlite":
        return
    node = _current["nodeid"]
    _totals["sqlite_statements"] += 1
    head = statement.lstrip()[:120]
    upper = head.upper()
    if upper.startswith("SAVEPOINT"):
        raw = _raw_sqlite(conn)
        tokens = head.split()
        name = tokens[1] if len(tokens) > 1 else "?"
        _stats[node]["savepoints_total"] += 1
        _totals["savepoints_total"] += 1
        if raw is None:
            _stats[node]["savepoints_unreadable_state"] += 1
            _totals["savepoints_unreadable_state"] += 1
            return
        if not raw.in_transaction:
            site = _site()
            _stats[node]["independent_savepoints"] += 1
            _totals["independent_savepoints"] += 1
            _sites[node][site] += 1
            _st(conn).independent[name] = site
    elif upper.startswith("RELEASE"):
        st = _state.get(conn)
        if st is None:
            return
        name = _name_after(head.split(), 0)
        if name in st.independent:
            st.pending_release = name


@event.listens_for(Engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):
    if conn.dialect.name != "sqlite":
        return
    st = _state.get(conn)
    if st is None or st.pending_release is None:
        return
    name = st.pending_release
    st.pending_release = None
    raw = _raw_sqlite(conn)
    node = _current["nodeid"]
    site = st.independent.pop(name, "?")
    if raw is not None and not raw.in_transaction:
        # The RELEASE ended the transaction: it committed. That is the durable half of the defect.
        _stats[node]["durable_releases"] += 1
        _totals["durable_releases"] += 1
        st.durable.append(name)
        st.durable_sites.append(site)
    else:
        _stats[node]["independent_release_left_transaction_open"] += 1
        _totals["independent_release_left_transaction_open"] += 1


@event.listens_for(Engine, "commit")
def _on_commit(conn):
    if conn.dialect.name != "sqlite":
        return
    _state[conn] = _ConnState()


@event.listens_for(Engine, "rollback")
def _on_rollback(conn):
    if conn.dialect.name != "sqlite":
        return
    st = _state.get(conn)
    if st is not None and st.durable:
        node = _current["nodeid"]
        _stats[node]["released_then_rollback"] += 1
        _stats[node]["released_then_rollback_savepoints"] += len(st.durable)
        _totals["released_then_rollback"] += 1
        for site in st.durable_sites:
            _rollback_sites[node][site] += 1
        _rollback_callers[node][_site()] += 1
    _state[conn] = _ConnState()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _current["nodeid"] = item.nodeid
    _totals["tests_seen"] += 1
    yield
    _current["nodeid"] = "<between-tests>"


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("T1525_DETECTOR_OUT")
    if not out:
        return
    nodes = {}
    for node, counter in _stats.items():
        nodes[node] = {
            **dict(counter),
            "independent_sites": dict(_sites.get(node, {})),
            "released_then_rollback_sites": dict(_rollback_sites.get(node, {})),
            "rollback_callers": dict(_rollback_callers.get(node, {})),
        }
    payload = {
        "exitstatus": int(exitstatus),
        "totals": dict(_totals),
        "nodes_with_independent_savepoints": sorted(
            node for node, counter in _stats.items() if counter.get("independent_savepoints")
        ),
        "nodes_with_released_then_rollback": sorted(
            node for node, counter in _stats.items() if counter.get("released_then_rollback")
        ),
        "nodes": nodes,
    }
    directory = os.path.dirname(os.path.abspath(out))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
