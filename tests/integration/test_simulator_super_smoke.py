from __future__ import annotations

import asyncio
import json
import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
from httpx import AsyncClient


class _SuperSimFailure(AssertionError):
    pass


def _utc_now_s() -> str:
    # Include microseconds to avoid collisions when multiple dumps are written quickly.
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def _edge_key(a: str, b: str) -> str:
    return f"{a}→{b}"


def _ensure_str(v: Any) -> str:
    if isinstance(v, str):
        return v
    raise _SuperSimFailure(f"Expected str, got {type(v).__name__}: {v!r}")


def _ensure_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    raise _SuperSimFailure(f"Expected list, got {type(v).__name__}: {v!r}")


def _ensure_dict(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    raise _SuperSimFailure(f"Expected dict, got {type(v).__name__}: {v!r}")


async def _best_effort_stop(client: AsyncClient, *, run_id: str, auth_headers: dict[str, str]) -> None:
    try:
        await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
    except Exception:
        return


async def _get_snapshot_with_links(
    client: AsyncClient, *, run_id: str, eq: str, auth_headers: dict[str, str]
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    r = await client.get(
        f"/api/v1/simulator/runs/{run_id}/graph/snapshot",
        headers=auth_headers,
        params={"equivalent": eq},
    )
    if r.status_code != 200:
        raise _SuperSimFailure(f"Snapshot failed: {r.status_code} {r.text}")

    snap = _ensure_dict(r.json())
    links_any = _ensure_list(snap.get("links"))
    links: list[dict[str, Any]] = [_ensure_dict(x) for x in links_any]

    keys: set[str] = set()
    for l in links:
        src = l.get("source")
        tgt = l.get("target")
        if isinstance(src, str) and isinstance(tgt, str):
            keys.add(_edge_key(src, tgt))

    if not keys:
        raise _SuperSimFailure("Snapshot has no links; cannot validate tx/clearing edges")

    return snap, links, keys


async def _read_sse_events(
    client: AsyncClient,
    *,
    run_id: str,
    eq: str,
    auth_headers: dict[str, str],
    stop_after_types_param: Optional[str] = None,
    stop_when: Optional[Callable[[dict[str, Any], set[str]], bool]] = None,
    last_event_id: Optional[str] = None,
    timeout_sec: float = 10.0,
) -> list[dict[str, Any]]:
    url = f"/api/v1/simulator/runs/{run_id}/events"

    headers = dict(auth_headers)
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id

    events: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    params: dict[str, Any] = {"equivalent": eq}
    if stop_after_types_param is not None:
        params["stop_after_types"] = stop_after_types_param

    try:
        async with asyncio.timeout(timeout_sec):
            async with client.stream(
                "GET",
                url,
                headers=headers,
                params=params,
            ) as r:
                if r.status_code != 200:
                    raise _SuperSimFailure(f"SSE stream failed: {r.status_code} {await r.aread()}")

                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line.removeprefix("data: "))
                    if isinstance(payload, dict):
                        events.append(payload)
                        t = payload.get("type")
                        if isinstance(t, str) and t:
                            seen_types.add(t)
                        if stop_when is not None and stop_when(payload, seen_types):
                            try:
                                await r.aclose()
                            except Exception:
                                pass
                            break
    except TimeoutError:
        types = [str(e.get("type") or "") for e in events]
        raise _SuperSimFailure(
            f"SSE timeout after {timeout_sec}s; got {len(events)} events; types={types}"
        )

    if not events:
        raise _SuperSimFailure("SSE produced zero events")

    return events


@dataclass
class _RunCapture:
    run_id: str
    mode: str
    eq: str
    scenario_id: str
    intensity_percent: int
    snapshot_edge_keys: set[str] = field(default_factory=set)
    actions: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    run_status: Optional[dict[str, Any]] = None
    artifacts_index: Optional[dict[str, Any]] = None


class _DumpCollector:
    def __init__(self, *, test_name: str) -> None:
        self.test_name = test_name
        self._t0 = datetime.now(timezone.utc)
        self.started_at_utc = self._t0.isoformat()
        self.runs: list[_RunCapture] = []
        self.extra: list[dict[str, Any]] = []
        self.dump_dir = Path("test-results") / "super-simulator"

    def add_run(self, run: _RunCapture) -> None:
        self.runs.append(run)

    def add_extra(self, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            self.extra.append(payload)

    async def collect_postmortem(self, client: AsyncClient, *, auth_headers: dict[str, str]) -> None:
        for run in self.runs:
            try:
                st = await client.get(f"/api/v1/simulator/runs/{run.run_id}", headers=auth_headers)
                if st.status_code == 200:
                    run.run_status = st.json()
            except Exception:
                pass

            try:
                idx = await client.get(
                    f"/api/v1/simulator/runs/{run.run_id}/artifacts",
                    headers=auth_headers,
                )
                if idx.status_code == 200:
                    run.artifacts_index = idx.json()

                    # If events.ndjson exists, store it next to the JSON dump.
                    names = set()
                    items = run.artifacts_index.get("items") if isinstance(run.artifacts_index, dict) else None
                    if isinstance(items, list):
                        for it in items:
                            if isinstance(it, dict) and isinstance(it.get("name"), str):
                                names.add(it["name"])

                    if "events.ndjson" in names:
                        try:
                            nd = await client.get(
                                f"/api/v1/simulator/runs/{run.run_id}/artifacts/events.ndjson",
                                headers=auth_headers,
                            )
                            if nd.status_code == 200:
                                self.dump_dir.mkdir(parents=True, exist_ok=True)
                                p = self.dump_dir / f"{self.test_name}_{_utc_now_s()}_{run.run_id}_events.ndjson"
                                p.write_bytes(nd.content)
                        except Exception:
                            pass
            except Exception:
                pass

    def write_dump(self, *, reason: str, exc: BaseException | None = None) -> Path:
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        p = self.dump_dir / f"{self.test_name}_{_utc_now_s()}.json"

        t1 = datetime.now(timezone.utc)
        dur = (t1 - self._t0).total_seconds()

        payload = {
            "test": self.test_name,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": t1.isoformat(),
            "duration_sec": dur,
            "reason": reason,
            "exception": repr(exc) if exc else None,
            "extra": self.extra,
            "runs": [
                {
                    "run_id": r.run_id,
                    "mode": r.mode,
                    "equivalent": r.eq,
                    "scenario_id": r.scenario_id,
                    "intensity_percent": r.intensity_percent,
                    "snapshot_edge_keys_count": len(r.snapshot_edge_keys or []),
                    "actions": r.actions,
                    "events": r.events,
                    "run_status": r.run_status,
                    "artifacts_index": r.artifacts_index,
                }
                for r in self.runs
            ],
        }

        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


def _validate_common_event_shape(evt: dict[str, Any], *, eq: str) -> None:
    if not isinstance(evt.get("event_id"), str) or not evt["event_id"]:
        raise _SuperSimFailure(f"Event missing event_id: {evt}")
    if not isinstance(evt.get("ts"), str) or not evt["ts"]:
        raise _SuperSimFailure(f"Event missing ts: {evt}")
    # Basic ISO-ish check for diagnostics readability.
    if "T" not in str(evt.get("ts") or ""):
        raise _SuperSimFailure(f"Event ts is not ISO-like: {evt}")
    if not isinstance(evt.get("type"), str) or not evt["type"]:
        raise _SuperSimFailure(f"Event missing type: {evt}")
    if evt.get("type") != "run_status":
        if evt.get("equivalent") != eq:
            raise _SuperSimFailure(f"Event has wrong equivalent (expected={eq}): {evt}")


def _validate_tx_failed(evt: dict[str, Any]) -> None:
    err = evt.get("error")
    if not isinstance(err, dict):
        raise _SuperSimFailure(f"tx.failed missing error object: {evt}")
    code = err.get("code")
    msg = err.get("message")
    if not isinstance(code, str) or not code:
        raise _SuperSimFailure(f"tx.failed missing error.code: {evt}")
    if not isinstance(msg, str) or not msg:
        raise _SuperSimFailure(f"tx.failed missing error.message: {evt}")

    # Guardrail: INTERNAL_ERROR must never be a "normal" outcome of a debug tx.
    if code == "INTERNAL_ERROR":
        raise _SuperSimFailure(f"Unexpected INTERNAL_ERROR in tx.failed: {evt}")


def _validate_edge_refs_are_in_snapshot(
    edges: list[Any], *, snapshot_edge_keys: set[str], context: str
) -> None:
    for e in edges:
        ed = _ensure_dict(e)
        a = ed.get("from")
        b = ed.get("to")
        if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
            raise _SuperSimFailure(f"{context}: bad edge ref: {e!r}")
        k = _edge_key(a, b)
        if k not in snapshot_edge_keys:
            raise _SuperSimFailure(
                f"{context}: edge {k} not present in snapshot links (count={len(snapshot_edge_keys)})"
            )


def _validate_tx_updated(evt: dict[str, Any], *, snapshot_edge_keys: set[str], require_patches: bool) -> None:
    edges = evt.get("edges")
    if edges is None:
        raise _SuperSimFailure(f"tx.updated missing edges: {evt}")
    edges_l = _ensure_list(edges)
    if not edges_l:
        raise _SuperSimFailure(f"tx.updated has empty edges: {evt}")

    # Ensure edge ref shape too (from/to are non-empty strings).
    for e in edges_l:
        ed = _ensure_dict(e)
        if not isinstance(ed.get("from"), str) or not str(ed.get("from") or "").strip():
            raise _SuperSimFailure(f"tx.updated edge missing from: {evt}")
        if not isinstance(ed.get("to"), str) or not str(ed.get("to") or "").strip():
            raise _SuperSimFailure(f"tx.updated edge missing to: {evt}")

    _validate_edge_refs_are_in_snapshot(edges_l, snapshot_edge_keys=snapshot_edge_keys, context="tx.updated")

    if require_patches:
        node_patch = evt.get("node_patch")
        edge_patch = evt.get("edge_patch")
        if not isinstance(node_patch, list) or not node_patch:
            raise _SuperSimFailure(f"tx.updated missing node_patch in real-mode: {evt}")
        if not isinstance(edge_patch, list) or not edge_patch:
            raise _SuperSimFailure(f"tx.updated missing edge_patch in real-mode: {evt}")

        # Minimal contract checks (mirrors existing real SSE smoke).
        np0 = _ensure_dict(node_patch[0])
        if not isinstance(np0.get("id"), str):
            raise _SuperSimFailure(f"tx.updated node_patch[0] missing id: {evt}")
        if not isinstance(np0.get("net_balance_atoms"), str):
            raise _SuperSimFailure(f"tx.updated node_patch[0] missing net_balance_atoms: {evt}")
        if str(np0.get("net_balance_atoms")).startswith("-"):
            raise _SuperSimFailure(f"tx.updated net_balance_atoms must be magnitude-only: {evt}")
        if np0.get("net_sign") not in (-1, 0, 1):
            raise _SuperSimFailure(f"tx.updated node_patch[0] missing/invalid net_sign: {evt}")


def _validate_clearing_done(evt: dict[str, Any], *, snapshot_edge_keys: set[str]) -> None:
    plan_id = evt.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise _SuperSimFailure(f"clearing.done missing plan_id: {evt}")

    cleared_amount = str(evt.get("cleared_amount") or "").strip()
    if not cleared_amount or cleared_amount in {"0", "0.0", "0.00"}:
        raise _SuperSimFailure(f"clearing.done has empty/zero cleared_amount: {evt}")

    cycle_edges = evt.get("cycle_edges")
    if not isinstance(cycle_edges, list) or not cycle_edges:
        raise _SuperSimFailure(f"clearing.done missing cycle_edges: {evt}")

    _validate_edge_refs_are_in_snapshot(
        cycle_edges,
        snapshot_edge_keys=snapshot_edge_keys,
        context="clearing.done",
    )


def _validate_run_status(evt: dict[str, Any], *, run_id: str, scenario_id: str) -> None:
    if evt.get("type") != "run_status":
        raise _SuperSimFailure(f"Expected run_status, got: {evt}")
    if evt.get("run_id") != run_id:
        raise _SuperSimFailure(f"run_status has wrong run_id: {evt}")
    if evt.get("scenario_id") != scenario_id:
        raise _SuperSimFailure(f"run_status has wrong scenario_id: {evt}")
    state = str(evt.get("state") or "")
    if state not in {"running", "idle", "stopped", "error"}:
        raise _SuperSimFailure(f"run_status has unexpected state: {evt}")
    if not isinstance(evt.get("sim_time_ms"), int):
        raise _SuperSimFailure(f"run_status missing/invalid sim_time_ms: {evt}")
    if not isinstance(evt.get("intensity_percent"), int):
        raise _SuperSimFailure(f"run_status missing/invalid intensity_percent: {evt}")


def _print_summary(msg: str) -> None:
    # Intentional stdout summary for CI readability.
    print(msg)


@pytest.mark.asyncio
async def test_super_smoke_part1_fixtures_http_visual_contract(
    client: AsyncClient,
    auth_headers,
    monkeypatch,
) -> None:
    """Part 1: fixtures-mode HTTP + SSE + visual contract."""

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")

    dump = _DumpCollector(test_name="test_super_smoke_part1_fixtures_http_visual_contract")
    scenario_id = "greenfield-village-100-realistic-v2"
    eq = "UAH"
    intensity_percent = 0

    async with asyncio.timeout(30.0):
        resp = await client.post(
            "/api/v1/simulator/runs",
            headers=auth_headers,
            json={
                "scenario_id": scenario_id,
                "mode": "fixtures",
                "intensity_percent": intensity_percent,
            },
        )
        if resp.status_code != 200:
            raise _SuperSimFailure(f"create_run failed: {resp.status_code} {resp.text}")
        run_id = str(resp.json()["run_id"])

        cap = _RunCapture(
            run_id=run_id,
            mode="fixtures",
            eq=eq,
            scenario_id=scenario_id,
            intensity_percent=intensity_percent,
        )
        dump.add_run(cap)

        try:
            snap, links, edge_keys = await _get_snapshot_with_links(
                client,
                run_id=cap.run_id,
                eq=eq,
                auth_headers=auth_headers,
            )
            cap.snapshot_edge_keys = edge_keys

            # Pick one deterministic edge for tx-once.
            l0 = _ensure_dict(links[0])
            src = _ensure_str(l0.get("source"))
            tgt = _ensure_str(l0.get("target"))
            dump.add_extra({"phase": "part1.snapshot", "snapshot": snap})

            tx1 = await client.post(
                f"/api/v1/simulator/runs/{cap.run_id}/actions/tx-once",
                headers=auth_headers,
                json={
                    "equivalent": eq,
                    "from": src,
                    "to": tgt,
                    "amount": "1.00",
                    "ttl_ms": 1200,
                    "seed": {"case": "super-smoke", "n": 1},
                    "client_action_id": "super-smoke-tx-1",
                },
            )
            cap.actions.append({"type": "tx-once", "status": tx1.status_code, "body": tx1.json()})
            if tx1.status_code != 200:
                raise _SuperSimFailure(f"tx-once failed: {tx1.status_code} {tx1.text}")

            tx2 = await client.post(
                f"/api/v1/simulator/runs/{cap.run_id}/actions/tx-once",
                headers=auth_headers,
                json={
                    "equivalent": eq,
                    "from": src,
                    "to": tgt,
                    "amount": "2.00",
                    "ttl_ms": 1200,
                    "seed": {"case": "super-smoke", "n": 2},
                    "client_action_id": "super-smoke-tx-2",
                },
            )
            cap.actions.append({"type": "tx-once", "status": tx2.status_code, "body": tx2.json()})
            if tx2.status_code != 200:
                raise _SuperSimFailure(f"tx-once(2) failed: {tx2.status_code} {tx2.text}")

            clr = await client.post(
                f"/api/v1/simulator/runs/{cap.run_id}/actions/clearing-once",
                headers=auth_headers,
                json={
                    "equivalent": eq,
                    "seed": {"case": "super-smoke"},
                    "client_action_id": "super-smoke-clr",
                },
            )
            cap.actions.append({"type": "clearing-once", "status": clr.status_code, "body": clr.json()})
            if clr.status_code != 200:
                raise _SuperSimFailure(f"clearing-once failed: {clr.status_code} {clr.text}")

            def _stop_when(evt: dict[str, Any], seen: set[str]) -> bool:
                return "clearing.done" in seen

            cap.events = await _read_sse_events(
                client,
                run_id=cap.run_id,
                eq=eq,
                auth_headers=auth_headers,
                last_event_id=f"evt_{cap.run_id}_000000",
                stop_after_types_param="clearing.done",
                stop_when=_stop_when,
                timeout_sec=12.0,
            )

            seen_run_status = 0
            seen_updated = 0
            seen_failed = 0
            seen_done = 0

            for evt in cap.events:
                _validate_common_event_shape(evt, eq=eq)
                t = evt.get("type")
                if t == "run_status":
                    seen_run_status += 1
                    _validate_run_status(evt, run_id=cap.run_id, scenario_id=scenario_id)
                elif t == "tx.updated":
                    seen_updated += 1
                    _validate_tx_updated(evt, snapshot_edge_keys=cap.snapshot_edge_keys, require_patches=False)
                elif t == "tx.failed":
                    seen_failed += 1
                    _validate_tx_failed(evt)
                elif t == "clearing.done":
                    seen_done += 1
                    _validate_clearing_done(evt, snapshot_edge_keys=cap.snapshot_edge_keys)

            if seen_run_status < 1:
                raise _SuperSimFailure(f"Expected run_status in SSE. Types: {[e.get('type') for e in cap.events]}")
            if seen_done < 1:
                raise _SuperSimFailure(f"Expected clearing.done. Types: {[e.get('type') for e in cap.events]}")
            if (seen_updated + seen_failed) < 1:
                raise _SuperSimFailure(
                    f"Expected tx.updated or tx.failed, saw updated={seen_updated} failed={seen_failed}."
                )

            st = await client.get(f"/api/v1/simulator/runs/{cap.run_id}", headers=auth_headers)
            if st.status_code == 200:
                cap.run_status = st.json()
                if str(cap.run_status.get("state") or "") == "error":
                    raise _SuperSimFailure(f"Run ended in error state: {cap.run_status}")

            _print_summary(
                f"Part1 OK: run_status={seen_run_status} tx.updated={seen_updated} tx.failed={seen_failed} clearing.done={seen_done}"
            )
        except Exception as e:
            await dump.collect_postmortem(client, auth_headers=auth_headers)
            p = dump.write_dump(reason="part1 failure", exc=e)
            raise _SuperSimFailure(f"Part1 failed; postmortem written to: {p.as_posix()}") from e
        finally:
            await _best_effort_stop(client, run_id=cap.run_id, auth_headers=auth_headers)

    if str(os.environ.get("GEO_TEST_DUMP_SUPER_SIM", "") or "").strip() in {"1", "true", "TRUE", "yes"}:
        await dump.collect_postmortem(client, auth_headers=auth_headers)
        dump.write_dump(reason="part1 success")


@pytest.mark.asyncio
async def test_super_smoke_part2_real_logic_deterministic(
    db_session,
) -> None:
    """Part 2: deterministic real-mode logic smoke (nested tx + RealClearingEngine).

    Does not depend on HTTP layer.
    """

    dump = _DumpCollector(test_name="test_super_smoke_part2_real_logic_deterministic")

    async with asyncio.timeout(30.0):
        try:
            from dataclasses import asdict
            from decimal import Decimal
            import hashlib
            import threading

            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

            from app.core.payments.service import PaymentService
            from app.core.simulator.edge_patch_builder import EdgePatchBuilder
            from app.core.simulator.models import RunRecord, TrustDriftResult
            from app.core.simulator.real_clearing_engine import RealClearingEngine
            from app.db.models.debt import Debt
            from app.db.models.equivalent import Equivalent
            from app.db.models.participant import Participant
            from app.db.models.trustline import TrustLine

            def _pubkey(seed: str) -> str:
                return hashlib.sha256(seed.encode("utf-8")).hexdigest()

            eq = Equivalent(code="UAH", is_active=True, precision=2, metadata_={})
            db_session.add(eq)

            a = Participant(pid="P_A", display_name="A", public_key=_pubkey("A"), type="person", status="active", profile={})
            b = Participant(pid="P_B", display_name="B", public_key=_pubkey("B"), type="person", status="active", profile={})
            c = Participant(pid="P_C", display_name="C", public_key=_pubkey("C"), type="person", status="active", profile={})
            db_session.add_all([a, b, c])
            await db_session.flush()

            tl_pairs = [(a, b), (b, c), (c, a), (b, a), (c, b), (a, c)]
            for src, dst in tl_pairs:
                db_session.add(
                    TrustLine(
                        from_participant_id=src.id,
                        to_participant_id=dst.id,
                        equivalent_id=eq.id,
                        limit=Decimal("1000"),
                        status="active",
                        policy={
                            "auto_clearing": True,
                            "can_be_intermediate": True,
                            "max_hop_usage": None,
                            "daily_limit": None,
                            "blocked_participants": [],
                        },
                    )
                )

            db_session.add_all(
                [
                    Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
                    Debt(debtor_id=c.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
                    Debt(debtor_id=a.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
                ]
            )
            await db_session.commit()

            # Nested payment (regression guard for transaction context misuse).
            svc = PaymentService(db_session)
            async with db_session.begin_nested():
                await svc.create_payment_internal(
                    a.id,
                    to_pid=b.pid,
                    equivalent="UAH",
                    amount="1.00",
                    idempotency_key="super-smoke-nested-payment",
                    commit=False,
                )
            await db_session.commit()

            class _CaptureSse:
                def __init__(self) -> None:
                    self.events: list[dict[str, Any]] = []

                def next_event_id(self, run: RunRecord) -> str:  # type: ignore[override]
                    run._event_seq += 1
                    return f"evt_{run.run_id}_{run._event_seq:06d}"

                def broadcast(self, run_id: str, payload: dict[str, Any]) -> None:  # type: ignore[override]
                    if isinstance(payload, dict):
                        self.events.append(payload)

            run = RunRecord(run_id="run_super_logic", scenario_id="super", mode="real", state="running")
            run._real_participants = [(a.id, a.pid), (b.id, b.pid), (c.id, c.pid)]
            run._real_equivalents = ["UAH"]
            run._edges_by_equivalent = {"UAH": [(p1.pid, p2.pid) for (p1, p2) in tl_pairs]}

            sse = _CaptureSse()
            clearing = RealClearingEngine(
                lock=threading.Lock(),
                sse=sse,  # type: ignore[arg-type]
                utc_now=lambda: datetime.now(timezone.utc),
                logger=__import__("logging").getLogger("tests.super_smoke"),
                edge_patch_builder=EdgePatchBuilder(logger=__import__("logging").getLogger("tests.super_smoke")),
                clearing_max_depth_limit=6,
                clearing_max_fx_edges_limit=50,
                real_clearing_time_budget_ms=2_000,
            )

            async def _no_trust_growth(*args, **kwargs) -> TrustDriftResult:
                return TrustDriftResult(updated_count=0)

            async def _no_edge_patch(*args, **kwargs) -> list[dict[str, Any]]:
                return []

            def _no_topology_patch(*args, **kwargs) -> None:
                return None

            # Use an explicit sessionmaker bound to the same test DB.
            bind = getattr(db_session, "bind", None) or db_session.get_bind()
            async_session_local = async_sessionmaker(
                bind=bind,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                join_transaction_mode="create_savepoint",
            )

            await clearing.tick_real_mode_clearing(
                db_session,
                run_id=run.run_id,
                run=run,
                equivalents=["UAH"],
                apply_trust_growth=_no_trust_growth,
                build_edge_patch_for_equivalent=_no_edge_patch,
                broadcast_topology_edge_patch=_no_topology_patch,
                async_session_local=async_session_local,
            )

            dump.add_extra({"phase": "part2.all_sse_events", "events": sse.events})

            done_evts = [e for e in sse.events if isinstance(e, dict) and e.get("type") == "clearing.done"]
            if not done_evts:
                raise _SuperSimFailure("Expected at least one real-mode clearing.done event")

            done0 = _ensure_dict(done_evts[0])

            cleared_amount = str(done0.get("cleared_amount") or "").strip()
            if not cleared_amount or cleared_amount in {"0", "0.0", "0.00"}:
                raise _SuperSimFailure(f"clearing.done has empty/zero cleared_amount: {done0}")

            cleared_cycles = done0.get("cleared_cycles")
            if not isinstance(cleared_cycles, int) or cleared_cycles < 1:
                raise _SuperSimFailure(f"clearing.done cleared_cycles must be >= 1: {done0}")

            cycle_edges = done0.get("cycle_edges")
            if not isinstance(cycle_edges, list) or not cycle_edges:
                raise _SuperSimFailure(f"clearing.done missing cycle_edges: {done0}")

            topo_keys = {_edge_key(x, y) for x, y in (run._edges_by_equivalent or {}).get("UAH", [])}
            _validate_edge_refs_are_in_snapshot(cycle_edges, snapshot_edge_keys=topo_keys, context="real clearing.done")

            node_patch = done0.get("node_patch")
            edge_patch = done0.get("edge_patch")
            if not isinstance(node_patch, list) or not node_patch:
                raise _SuperSimFailure(f"clearing.done missing node_patch: {done0}")
            if not isinstance(edge_patch, list) or not edge_patch:
                raise _SuperSimFailure(f"clearing.done missing edge_patch: {done0}")

            np0 = _ensure_dict(node_patch[0])
            if not isinstance(np0.get("id"), str) or not np0.get("id"):
                raise _SuperSimFailure(f"clearing.done node_patch[0] missing id: {done0}")
            if not isinstance(np0.get("net_balance_atoms"), str):
                raise _SuperSimFailure(f"clearing.done node_patch[0] missing net_balance_atoms: {done0}")
            if str(np0.get("net_balance_atoms")).startswith("-"):
                raise _SuperSimFailure(f"clearing.done net_balance_atoms must be magnitude-only: {done0}")
            if np0.get("net_sign") not in (-1, 0, 1):
                raise _SuperSimFailure(f"clearing.done node_patch[0] invalid net_sign: {done0}")
            if not isinstance(np0.get("net_balance"), str) or not str(np0.get("net_balance") or "").strip():
                raise _SuperSimFailure(f"clearing.done node_patch[0] missing net_balance: {done0}")
            if not isinstance(np0.get("viz_color_key"), str) or not np0.get("viz_color_key"):
                raise _SuperSimFailure(f"clearing.done node_patch[0] missing viz_color_key: {done0}")
            vs = np0.get("viz_size")
            if not isinstance(vs, dict) or not ("w" in vs and "h" in vs):
                raise _SuperSimFailure(f"clearing.done node_patch[0] missing viz_size: {done0}")

            ep0 = _ensure_dict(edge_patch[0])
            for k in ("source", "target", "viz_alpha_key", "viz_width_key"):
                if not isinstance(ep0.get(k), str) or not str(ep0.get(k) or "").strip():
                    raise _SuperSimFailure(f"clearing.done edge_patch[0] missing {k}: {done0}")

            _print_summary(
                f"Part2 OK: cleared_amount={cleared_amount} cleared_cycles={cleared_cycles} cycle_edges={len(cycle_edges)} node_patch={len(node_patch)} edge_patch={len(edge_patch)}"
            )
        except Exception as e:
            dump.add_extra({"phase": "part2.exception", "exception": repr(e), "traceback": traceback.format_exc()})
            p = dump.write_dump(reason="part2 failure", exc=e)
            raise _SuperSimFailure(f"Part2 failed; postmortem written to: {p.as_posix()}") from e

    if str(os.environ.get("GEO_TEST_DUMP_SUPER_SIM", "") or "").strip() in {"1", "true", "TRUE", "yes"}:
        dump.write_dump(reason="part2 success")


@pytest.mark.asyncio
async def test_super_smoke_part3_real_mode_http_startup(
    client: AsyncClient,
    auth_headers,
) -> None:
    """Part 3: real-mode HTTP startup smoke.

    Ensures real-mode run can be created and emits run_status + at least one tx.*.
    """

    dump = _DumpCollector(test_name="test_super_smoke_part3_real_mode_http_startup")
    scenario_id = "greenfield-village-100-realistic-v2"
    eq = "UAH"
    intensity_percent = 50

    async with asyncio.timeout(30.0):
        resp = await client.post(
            "/api/v1/simulator/runs",
            headers=auth_headers,
            json={
                "scenario_id": scenario_id,
                "mode": "real",
                "intensity_percent": intensity_percent,
            },
        )
        if resp.status_code != 200:
            raise _SuperSimFailure(f"create_run failed: {resp.status_code} {resp.text}")
        run_id = str(resp.json()["run_id"])

        cap = _RunCapture(
            run_id=run_id,
            mode="real",
            eq=eq,
            scenario_id=scenario_id,
            intensity_percent=intensity_percent,
        )
        dump.add_run(cap)

        try:
            def _stop_when(evt: dict[str, Any], seen: set[str]) -> bool:
                # Stop once we have run_status and any tx.* event.
                if "run_status" not in seen:
                    return False
                if "tx.updated" in seen or "tx.failed" in seen:
                    return True
                return False

            cap.events = await _read_sse_events(
                client,
                run_id=cap.run_id,
                eq=eq,
                auth_headers=auth_headers,
                last_event_id=f"evt_{cap.run_id}_000000",
                stop_after_types_param=None,
                stop_when=_stop_when,
                timeout_sec=12.0,
            )

            seen_run_status = 0
            seen_tx = 0

            for evt in cap.events:
                _validate_common_event_shape(evt, eq=eq)
                t = evt.get("type")
                if t == "run_status":
                    seen_run_status += 1
                    _validate_run_status(evt, run_id=cap.run_id, scenario_id=scenario_id)
                elif t == "tx.updated":
                    seen_tx += 1
                elif t == "tx.failed":
                    seen_tx += 1
                    _validate_tx_failed(evt)

            if seen_run_status < 1:
                raise _SuperSimFailure(f"No run_status in real-mode SSE. Types: {[e.get('type') for e in cap.events]}")
            if seen_tx < 1:
                raise _SuperSimFailure(f"No tx.* in real-mode SSE. Types: {[e.get('type') for e in cap.events]}")

            st = await client.get(f"/api/v1/simulator/runs/{cap.run_id}", headers=auth_headers)
            if st.status_code == 200:
                cap.run_status = st.json()
                if str(cap.run_status.get("state") or "") == "error":
                    raise _SuperSimFailure(f"Run ended in error state: {cap.run_status}")

            _print_summary(f"Part3 OK: run_status={seen_run_status} tx_events={seen_tx}")
        except Exception as e:
            await dump.collect_postmortem(client, auth_headers=auth_headers)
            p = dump.write_dump(reason="part3 failure", exc=e)
            raise _SuperSimFailure(f"Part3 failed; postmortem written to: {p.as_posix()}") from e
        finally:
            await _best_effort_stop(client, run_id=cap.run_id, auth_headers=auth_headers)

    if str(os.environ.get("GEO_TEST_DUMP_SUPER_SIM", "") or "").strip() in {"1", "true", "TRUE", "yes"}:
        await dump.collect_postmortem(client, auth_headers=auth_headers)
        dump.write_dump(reason="part3 success")
