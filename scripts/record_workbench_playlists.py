"""Record the offline workbench's event playlists from a LIVE run (spec 039, task T3935).

The offline effects workbench of Simulator UI v2 (042 `T4205`) plays recorded server events through the live event
path. Until 2026-10-18 its playlists were hand-made (029 S4) and financially inconsistent; this script records them
from the product instead: a real-mode run of a shipped scenario on a running backend, driven by Interact actions with
a fixed plan, its SSE stream and its graph snapshots written as they came.

What it writes into ``--out`` (normally ``simulator-ui/v2/public/simulator-fixtures/v1/UAH/events``):

- ``demo-tx-snapshot.json`` - ``GET /simulator/runs/{id}/graph/snapshot`` after the run is seeded, before any payment;
- ``demo-tx.json`` - the ``tx.updated`` events of the plan's payments, in stream order;
- ``demo-clearing-snapshot.json`` - the snapshot after the last payment, before the clearing;
- ``demo-clearing.json`` - the ``clearing.done`` event(s) of one Interact clearing.

Each playlist starts from ITS OWN snapshot (the workbench plays it on that scene). Nothing recorded is changed: ids,
``event_id``, ``ts`` and every number are the server's. A recording is reproducible in its numbers (fixed scenario,
fixed plan, intensity 0, no automatic payments and no automatic clearing during the recording), not in its ids: the
run id (and so every participant identity ``<run_id>:p<N>``, 044 `T4405`) and the timestamps differ per recording.

Why a new script: ``scripts/diagnose_simulator_sse.py`` reads a run's stream but only counts it, and drives no payment;
nothing else records the stream with the snapshot it starts from.

What it does NOT do: check the money (that is ``simulator-ui/v2/src/composables/usePlaylistWorkbench.test.ts``, run
on the written files); start the backend; record the EUR or HOUR playlists (no shipped scenario carries those
equivalents). It refuses to write anything when a planned payment is refused, when a payment answers more than one
route (the plan relies on single-route payments), when an event of a type other than ``run_status``, ``tx.updated``
or ``clearing.done`` arrives (a ``tx.failed`` included), when a payment's stream answer is not exactly one ``tx.updated``
of its sender, receiver and amount, when the clearing's is not exactly one ``clearing.done``, or when an expected event
does not arrive in time.

Usage (PowerShell; the backend runs with ``SIMULATOR_ACTIONS_ENABLE=1`` and ``SIMULATOR_CLEARING_EVERY_N_TICKS``
large enough that no automatic clearing runs; the admin token is read from the environment, never from argv)::

    $env:GEO_ADMIN_TOKEN = '<the backend ADMIN_TOKEN>'
    .venv\\Scripts\\python.exe scripts\\record_workbench_playlists.py --base-url http://127.0.0.1:18391 `
        --out simulator-ui/v2/public/simulator-fixtures/v1/UAH/events
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

SCENARIO_ID = "greenfield-village-100-realistic-v2"
EQUIVALENT = "UAH"
_REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = _REPO_ROOT / "fixtures" / "simulator" / SCENARIO_ID / "scenario.json"

#: The payments, by the 1-based ORDINAL of the participant in the scenario (the run identity is ``<run_id>:p<N>``),
#: with the amount as a decimal string at the equivalent's precision. Built so that the recording shows direct and
#: 2-4 line routes and leaves debt cycles for the clearing, two of which share a line (1 -> 2):
#: 2 -> 1, 6 -> 2, 1 -> 6 (lines 1->2, 2->6, 6->1); 2 -> 1, 7 -> 2, 1 -> 7 (lines 1->2, 2->7, 7->1);
#: 36 -> 3, 11 -> 36, 3 -> 11 (lines 3->36, 36->11, 11->3). A payment X -> Y over one line uses the line Y -> X
#: (Y is the creditor), so every direct payment below names a line of the scenario (checked before the run starts).
PLAN: tuple[tuple[int, int, str], ...] = (
    (36, 3, "135.00"),
    (70, 17, "42.70"),
    (2, 1, "120.00"),
    (6, 2, "80.00"),
    (65, 14, "18.40"),
    (1, 6, "80.00"),
    (11, 36, "110.00"),
    (49, 46, "27.30"),
    (7, 2, "95.00"),
    (2, 1, "100.00"),
    (88, 19, "15.60"),
    (3, 11, "110.00"),
    (1, 7, "95.00"),
    (43, 56, "12.90"),
    (22, 14, "31.20"),
    (84, 11, "9.75"),
    (81, 43, "22.00"),
    (48, 14, "16.80"),
    (72, 27, "26.40"),
    (32, 35, "14.10"),
)

#: Direct payments of the plan (sender, receiver): each needs the scenario line receiver -> sender.
DIRECT = ((36, 3), (2, 1), (6, 2), (1, 6), (11, 36), (7, 2), (3, 11), (1, 7))

EFFECT_TYPES = ("tx.updated", "clearing.done")
EVENT_TIMEOUT_SEC = 30.0


class RecordingRefused(RuntimeError):
    """The recording cannot be trusted as it came; nothing is written."""


def check_plan(scenario: dict[str, Any]) -> list[str]:
    """Problems of the plan against the scenario: unknown ordinals, inactive parties, a direct payment without its line."""

    participants = scenario["participants"]
    ids = [p["id"] for p in participants]
    status = {i + 1: str(p.get("status") or "active") for i, p in enumerate(participants)}
    lines = {(t["from"], t["to"]) for t in scenario["trustlines"] if (t.get("equivalent") or EQUIVALENT) == EQUIVALENT}
    problems: list[str] = []
    for n, (a, b, amount) in enumerate(PLAN):
        for who in (a, b):
            if not 1 <= who <= len(ids):
                problems.append(f"PLAN[{n}]: participant ordinal {who} is not in the scenario")
            elif status[who] != "active":
                problems.append(f"PLAN[{n}]: participant {who} is {status[who]}, a payment would be refused")
        if len(amount.partition(".")[2]) != 2:
            problems.append(f"PLAN[{n}]: amount {amount!r} is not at precision 2")
    for a, b in DIRECT:
        if (a, b) not in {(x, y) for x, y, _ in PLAN}:
            problems.append(f"DIRECT {a}->{b} is not a payment of the plan")
        if 1 <= a <= len(ids) and 1 <= b <= len(ids) and (ids[b - 1], ids[a - 1]) not in lines:
            problems.append(f"DIRECT {a}->{b}: the scenario has no line {b}->{a}")
    return problems


class EventSource(Protocol):
    """The run's events in stream order. `next` returns None once the stream has ended (nothing more will come)."""

    def next(self) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


class Backend(Protocol):
    """What the recording asks of the backend. `HttpBackend` is the real one; the tooling tests feed a canned stream."""

    def start_run(self) -> str: ...

    def pause(self, run_id: str) -> None: ...

    def seed(self, run_id: str) -> None: ...

    def snapshot(self, run_id: str) -> dict[str, Any]: ...

    def pay(self, run_id: str, from_pid: str, to_pid: str, amount: str) -> dict[str, Any]: ...

    def clear(self, run_id: str) -> dict[str, Any]: ...

    def stop(self, run_id: str) -> None: ...

    def events(self, run_id: str) -> EventSource: ...


def take_until(source: EventSource, wanted: str) -> list[dict[str, Any]]:
    """Every event up to and including the first of type `wanted`. Refuses an event of another type than `run_status`,
    `tx.updated` or `clearing.done` (a `tx.failed` included) and a stream that ends first."""

    seen: list[dict[str, Any]] = []
    while True:
        event = source.next()
        if event is None:
            raise RecordingRefused(f"the stream ended before an expected {wanted!r} event")
        kind = str(event.get("type"))
        if kind not in ("run_status", *EFFECT_TYPES):
            raise RecordingRefused(f"an unexpected event type {kind!r}: {json.dumps(event)[:400]}")
        seen.append(event)
        if kind == wanted:
            return seen


def payment_event(effects: list[dict[str, Any]], n: int, sender: str, receiver: str, amount: str) -> dict[str, Any]:
    """The one `tx.updated` of planned payment `n`: exactly one effect event, of its sender, receiver and amount."""

    event = effects[-1] if effects else {}
    if len(effects) != 1 or (event.get("from"), event.get("to"), event.get("amount")) != (sender, receiver, amount):
        raise RecordingRefused(f"payment PLAN[{n}] {sender}->{receiver} {amount}: the stream answered {json.dumps(effects)[:400]}")
    return event


def record(backend: Backend) -> tuple[str, dict[str, Any]]:
    """Drives one run by `PLAN` and returns its id and what was recorded; raises `RecordingRefused` on any doubt."""

    out: dict[str, Any] = {}
    run_id = backend.start_run()
    source: EventSource | None = None
    try:
        backend.pause(run_id)
        backend.seed(run_id)  # Interact's lazy seeding: the run's participants and lines are written here
        source = backend.events(run_id)
        take_until(source, "run_status")  # the stream answers its first status on connect: from here nothing is missed

        def pid(ordinal: int) -> str:
            return f"{run_id}:p{ordinal}"

        def effects_until(wanted: str) -> list[dict[str, Any]]:
            assert source is not None
            return [e for e in take_until(source, wanted) if e["type"] != "run_status"]

        out["demo-tx-snapshot"] = backend.snapshot(run_id)
        payments: list[dict[str, Any]] = []
        for n, (a, b, amount) in enumerate(PLAN):
            answer = backend.pay(run_id, pid(a), pid(b), amount)
            if len(answer.get("routes") or []) != 1:
                raise RecordingRefused(f"payment PLAN[{n}] answered {len(answer.get('routes') or [])} routes")
            payments.append(payment_event(effects_until("tx.updated"), n, pid(a), pid(b), amount))
        out["demo-tx"] = payments

        out["demo-clearing-snapshot"] = backend.snapshot(run_id)
        cleared = backend.clear(run_id)
        if int(cleared.get("cleared_cycles") or 0) < 1:
            raise RecordingRefused(f"the clearing cleared nothing: {json.dumps(cleared)[:400]}")
        clearing = effects_until("clearing.done")
        if len(clearing) != 1:
            raise RecordingRefused(f"the clearing: the stream answered {json.dumps(clearing)[:400]}")
        out["demo-clearing"] = clearing
        out["clearing-answer"] = cleared
        out["after-clearing-snapshot"] = backend.snapshot(run_id)
    finally:
        if source is not None:
            source.close()
        backend.stop(run_id)
    return run_id, out


#: The files of a recording, in the folder of the playlists: each playlist next to its own start snapshot.
RECORDED_FILES = ("demo-tx-snapshot", "demo-tx", "demo-clearing-snapshot", "demo-clearing")


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_recording(out_dir: Path, out: dict[str, Any], evidence: Path | None = None) -> None:
    """Writes the recording as it came: the server's dicts, untouched, as JSON."""

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in RECORDED_FILES:
        _write(out_dir / f"{name}.json", out[name])
    if evidence is not None:
        evidence.mkdir(parents=True, exist_ok=True)
        _write(evidence / "clearing-answer.json", out["clearing-answer"])
        _write(evidence / "after-clearing-snapshot.json", out["after-clearing-snapshot"])


class _HttpStream:
    """The run's SSE stream, read on a thread into a queue of parsed events; None in the queue marks its end."""

    def __init__(self, base_url: str, headers: dict[str, str], run_id: str) -> None:
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, args=(base_url, headers, run_id), daemon=True)
        self._thread.start()

    def _read(self, base_url: str, headers: dict[str, str], run_id: str) -> None:
        try:
            with httpx.Client(base_url=base_url, headers=headers, timeout=httpx.Timeout(30.0, read=None)) as client:
                url = f"/api/v1/simulator/runs/{run_id}/events"
                with client.stream("GET", url, params={"equivalent": EQUIVALENT}) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if self._stop.is_set():
                            return
                        if line.startswith("data: "):
                            self.events.put(json.loads(line.removeprefix("data: ")))
        except BaseException as exc:  # noqa: BLE001 - handed to the main thread, which refuses the recording
            self.error = exc
        finally:
            self.events.put(None)

    def next(self) -> dict[str, Any] | None:
        deadline = time.monotonic() + EVENT_TIMEOUT_SEC
        while True:
            if self.error is not None:
                raise RecordingRefused(f"the SSE stream failed: {self.error!r}")
            left = deadline - time.monotonic()
            if left <= 0:
                raise RecordingRefused("an expected event did not arrive in time")
            try:
                return self.events.get(timeout=min(left, 0.5))
            except queue.Empty:
                continue

    def close(self) -> None:
        self._stop.set()


class HttpBackend:
    """The running backend, by its HTTP API and its admin token."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url
        self._headers = {"X-Admin-Token": token}
        self._client = httpx.Client(base_url=base_url, headers=self._headers, timeout=60.0)
        self._api = "/api/v1/simulator"

    @staticmethod
    def _json(response: httpx.Response, what: str) -> Any:
        if response.status_code != 200:
            raise RecordingRefused(f"{what}: HTTP {response.status_code} {response.text[:400]}")
        return response.json()

    def start_run(self) -> str:
        body = {"scenario_id": SCENARIO_ID, "intensity_percent": 0}
        return str(self._json(self._client.post(f"{self._api}/runs", json=body), "start the run")["run_id"])

    def pause(self, run_id: str) -> None:
        self._json(self._client.post(f"{self._api}/runs/{run_id}/pause"), "pause the run")

    def seed(self, run_id: str) -> None:
        url = f"{self._api}/runs/{run_id}/actions/trustlines-list"
        self._json(self._client.get(url, params={"equivalent": EQUIVALENT}), "seed the run")

    def snapshot(self, run_id: str) -> dict[str, Any]:
        url = f"{self._api}/runs/{run_id}/graph/snapshot"
        return self._json(self._client.get(url, params={"equivalent": EQUIVALENT}), "the snapshot")

    def pay(self, run_id: str, from_pid: str, to_pid: str, amount: str) -> dict[str, Any]:
        body = {"from_pid": from_pid, "to_pid": to_pid, "equivalent": EQUIVALENT, "amount": amount}
        url = f"{self._api}/runs/{run_id}/actions/payment-real"
        return self._json(self._client.post(url, json=body), f"payment {from_pid}->{to_pid} {amount}")

    def clear(self, run_id: str) -> dict[str, Any]:
        url = f"{self._api}/runs/{run_id}/actions/clearing-real"
        return self._json(self._client.post(url, json={"equivalent": EQUIVALENT}), "the clearing")

    def stop(self, run_id: str) -> None:
        self._client.post(f"{self._api}/runs/{run_id}/stop")
        self._client.close()

    def events(self, run_id: str) -> EventSource:
        return _HttpStream(self._base_url, self._headers, run_id)


def main(argv: list[str] | None = None, backend: Backend | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--out", required=True, type=Path, help="the folder of the UAH playlists")
    parser.add_argument("--evidence", type=Path, default=None,
                        help="optional folder for what is not committed: the clearing answer, the snapshot after it")
    args = parser.parse_args(argv)

    if backend is None:
        token = os.environ.get("GEO_ADMIN_TOKEN", "").strip()
        if not token:
            print("GEO_ADMIN_TOKEN is not set (the backend's ADMIN_TOKEN)", file=sys.stderr)
            return 2
        backend = HttpBackend(args.base_url, token)
    problems = check_plan(json.loads(SCENARIO_PATH.read_text(encoding="utf-8")))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    try:
        run_id, out = record(backend)
    except RecordingRefused as exc:
        print(f"recording refused, nothing written: {exc}", file=sys.stderr)
        return 1

    write_recording(args.out, out, args.evidence)
    print(f"recorded run {run_id}: {len(out['demo-tx'])} payment events, {len(out['demo-clearing'])} clearing events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())