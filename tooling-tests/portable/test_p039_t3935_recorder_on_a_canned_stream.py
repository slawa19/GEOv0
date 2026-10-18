"""The recorder's own logic on a canned stream: what it refuses, and that it writes what came (039 `T3935`).

`scripts/record_workbench_playlists.py` drives a live run through a `Backend` and reads its events through an
`EventSource`. Here both are fakes: a list of event dicts stands for the SSE stream, so the recorder's refusals and its
output are checked without a network, a backend or a database.

What this checks: (a) a `tx.failed` in the stream refuses the recording; (b) a `tx.updated` that is not the planned
payment (sender, receiver, amount) refuses it; (c) a complete stream is written as it came - the four files, each playlist
next to its own start snapshot, the events in stream order, ids, timestamps and numbers untouched, LF, one trailing
newline; (d) a stream that ends before the expected events refuses it. Every refusal exits 1, names its reason and writes
nothing. What it does NOT check: the HTTP side (`HttpBackend`, `_HttpStream`) - that ran against a live backend on
2026-10-18 (spec 039 Changelog) - or the money of a recording (`simulator-ui/v2/src/composables/usePlaylistWorkbench.test.ts`).
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "record_workbench_playlists.py"
RUN = "run_canned_0001"


def _recorder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_workbench_playlists_canned", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(tag: str) -> dict[str, Any]:
    return {
        "equivalent": "UAH",
        "generated_at": f"2026-10-18T17:13:0{tag}.123456Z",
        "nodes": [{"id": f"{RUN}:p1", "name": "One", "net_balance_atoms": "0", "net_sign": 0, "net_balance": "0.00"}],
        "links": [{"source": f"{RUN}:p1", "target": f"{RUN}:p2", "id": None, "trust_limit": "1685.83", "used": "0.00"}],
    }


def _stream(rec: ModuleType) -> list[dict[str, Any]]:
    """What a run of the plan sends: a first status, one `tx.updated` per payment (a status between some), a clearing."""

    events: list[dict[str, Any]] = [{"event_id": f"evt_{RUN}_000001", "type": "run_status", "state": "paused"}]
    for n, (a, b, amount) in enumerate(rec.PLAN):
        if n % 7 == 3:
            events.append({"event_id": f"evt_{RUN}_s{n}", "type": "run_status", "state": "paused"})
        events.append({
            "event_id": f"evt_{RUN}_{n + 2:06d}", "ts": f"2026-10-18T17:13:{n:02d}.000001Z", "type": "tx.updated",
            "equivalent": "UAH", "from": f"{RUN}:p{a}", "to": f"{RUN}:p{b}", "amount": amount, "amount_flyout": True,
            "ttl_ms": 1200, "edges": [{"from": f"{RUN}:p{a}", "to": f"{RUN}:p{b}", "style": None}],
            "node_patch": [{"id": f"{RUN}:p{b}", "net_balance_atoms": amount.replace(".", ""), "net_sign": 1,
                            "net_balance": amount}],
            "edge_patch": [{"source": f"{RUN}:p{b}", "target": f"{RUN}:p{a}", "used": amount, "available": "1.00"}],
        })
    events.append({
        "event_id": f"evt_{RUN}_000099", "ts": "2026-10-18T17:14:00.000001Z", "type": "clearing.done",
        "equivalent": "UAH", "plan_id": "plan_interact_canned", "cleared_cycles": 1, "cleared_amount": "20.00",
        "cycle_edges": [{"from": f"{RUN}:p1", "to": f"{RUN}:p2"}],
    })
    return events


class _Source:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = list(events)
        self.closed = False

    def next(self) -> dict[str, Any] | None:
        return self._events.pop(0) if self._events else None

    def close(self) -> None:
        self.closed = True


class _Backend:
    """The backend of a run that answers every action, its events canned."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.source = _Source(events)
        self.snapshots = [_snapshot("1"), _snapshot("2"), _snapshot("3")]
        self.stopped = False

    def start_run(self) -> str:
        return RUN

    def pause(self, run_id: str) -> None:
        return None

    def seed(self, run_id: str) -> None:
        return None

    def snapshot(self, run_id: str) -> dict[str, Any]:
        return self.snapshots.pop(0)

    def pay(self, run_id: str, from_pid: str, to_pid: str, amount: str) -> dict[str, Any]:
        return {"routes": [{"hops": [{"from": from_pid, "to": to_pid, "amount": amount}]}]}

    def clear(self, run_id: str) -> dict[str, Any]:
        return {"cleared_cycles": 1, "total_cleared_amount": "20.00", "cycles": []}

    def stop(self, run_id: str) -> None:
        self.stopped = True

    def events(self, run_id: str) -> _Source:
        return self.source


def _refused(rec: ModuleType, events: list[dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    backend = _Backend(events)
    out = tmp_path / "events"
    assert rec.main(["--out", str(out)], backend=backend) == 1
    assert not out.exists(), "a refused recording wrote files"
    assert backend.stopped and backend.source.closed, "a refused recording left the run or its stream open"
    return capsys.readouterr().err


def test_a_tx_failed_in_the_stream_refuses_the_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec = _recorder()
    events = _stream(rec)
    third = [i for i, e in enumerate(events) if e.get("type") == "tx.updated"][2]
    events[third] = {**events[third], "type": "tx.failed"}
    err = _refused(rec, events, tmp_path, capsys)
    assert "recording refused, nothing written: an unexpected event type 'tx.failed'" in err


def test_a_tx_updated_that_is_not_the_planned_payment_refuses_the_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec = _recorder()
    events = _stream(rec)
    sixth = [i for i, e in enumerate(events) if e.get("type") == "tx.updated"][5]
    events[sixth] = {**events[sixth], "amount": "0.01"}
    err = _refused(rec, events, tmp_path, capsys)
    a, b, amount = rec.PLAN[5]
    assert f"payment PLAN[5] {RUN}:p{a}->{RUN}:p{b} {amount}: the stream answered" in err


def test_a_stream_that_ends_early_refuses_the_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec = _recorder()
    events = _stream(rec)
    last_payment = max(i for i, e in enumerate(events) if e.get("type") == "tx.updated")
    err = _refused(rec, events[:last_payment], tmp_path, capsys)
    assert "the stream ended before an expected 'tx.updated' event" in err
    # ... and one that ends after the payments, before the clearing's event.
    err = _refused(rec, events[:-1], tmp_path, capsys)
    assert "the stream ended before an expected 'clearing.done' event" in err


def test_a_complete_stream_is_written_as_it_came(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec = _recorder()
    events = _stream(rec)
    backend = _Backend(events)
    snapshots = copy.deepcopy(backend.snapshots)
    out = tmp_path / "events"
    evidence = tmp_path / "evidence"
    assert rec.main(["--out", str(out), "--evidence", str(evidence)], backend=backend) == 0
    assert "recorded run run_canned_0001: 20 payment events, 1 clearing events" in capsys.readouterr().out

    # The four files and nothing else; each playlist next to its own start snapshot.
    assert sorted(p.name for p in out.iterdir()) == [
        "demo-clearing-snapshot.json", "demo-clearing.json", "demo-tx-snapshot.json", "demo-tx.json"]
    for playlist in ("demo-tx", "demo-clearing"):
        assert (out / f"{playlist}-snapshot.json").is_file()
    raw = {p.stem: p.read_bytes() for p in out.iterdir()}
    for name, data in raw.items():
        assert b"\r" not in data and data.endswith((b"]\n", b"}\n")) and not data.endswith(b"\n\n"), f"{name}: LF, one trailing newline"
    written = {name: json.loads(data) for name, data in raw.items()}

    # As it came: the payments are the stream's tx.updated in order (statuses dropped), the clearing its one event,
    # the snapshots the backend's - ids, event ids, timestamps and numbers (decimal strings) untouched.
    assert written["demo-tx"] == [e for e in events if e["type"] == "tx.updated"]
    assert written["demo-clearing"] == [events[-1]]
    assert [written["demo-tx-snapshot"], written["demo-clearing-snapshot"]] == snapshots[:2]
    assert [e["amount"] for e in written["demo-tx"]] == [amount for _, _, amount in rec.PLAN]
    assert all(isinstance(e["amount"], str) for e in written["demo-tx"])
    assert written["demo-tx"][0]["from"] == f"{RUN}:p{rec.PLAN[0][0]}"
    assert json.loads((evidence / "after-clearing-snapshot.json").read_text(encoding="utf-8")) == snapshots[2]
    assert backend.stopped and backend.source.closed
