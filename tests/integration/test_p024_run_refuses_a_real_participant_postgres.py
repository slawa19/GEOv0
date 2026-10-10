"""Programme 024, stage 0, F-024-4b (SIM-02): a run never acts in the name of a real participant.

A run acts for its participants WITHOUT signatures - the seeder's trustlines, the tick's and
Interact Mode's `create_payment_internal`. The seeder used to skip a participant whose pid already
existed and so made it a member of the run; an inject resolved sponsors, trustline ends and freeze
targets from the global table the same way. The rule (one owner,
`app/core/simulator/real_scenario_seeder.py::simulated_public_key`): a run may take over an existing
participant only if the simulator itself would have created it - its `public_key` is the pseudo key
`sha256(pid)`, left over from an earlier run of the same scenario. Anything else is refused
fail-closed: the tick fails the run with `SIMULATOR_PID_TAKEN` and the pid; the Interact Mode seeding
path answers 409 with the code, the pid and the request id.

Stand: the real `RealRunnerImpl.tick_real_mode` over a mode-B clone (`committed_database`), as in
`test_p015_t1544_operator_stop_through_the_tick_sqlite.py`; the HTTP case uses the `client` fixture.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, or_, select

from app.config import settings
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.simulator_tick_stand import RecordingSse
from tests.simulator_tick_stand import install_tick_stand
from tests.simulator_tick_stand import pooled_sessionmaker_over

PID_TAKEN = "SIMULATOR_PID_TAKEN"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def factory(committed_database):
    async with pooled_sessionmaker_over(committed_database.url) as session_factory:
        yield session_factory


class _Artifacts:
    def write_real_tick_artifact(self, *a, **kw) -> None:
        return None

    def enqueue_event_artifact(self, _run_id: str, _payload: dict[str, Any]) -> None:
        return None


def _runner(run: RunRecord, scenario: dict) -> RealRunnerImpl:
    runner = RealRunnerImpl(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=RecordingSse(),
        artifacts=_Artifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=1,
        clearing_every_n_ticks=10_000,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("tests.p024.perimeter"),
    )
    runner._real_enable_inject = True
    return runner


def _fresh_run(run_id: str, scenario_id: str, scenario: dict) -> RunRecord:
    """A run that has NOT been seeded yet, as `start_run` leaves it."""
    run = RunRecord(run_id=run_id, scenario_id=scenario_id, mode="real", state="running")
    run.seed = 7
    run.tick_index = 0
    run.sim_time_ms = 1_000
    run.intensity_percent = 0
    run._scenario_raw = scenario
    run._real_viz_by_eq = {}
    run._edges_by_equivalent = {}
    return run


async def _tick(runner: RealRunnerImpl, run: RunRecord) -> None:
    run.tick_index += 1
    run.sim_time_ms += 1_000
    await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=60)


def _real_row() -> Participant:
    """A participant as registration makes one: a real Ed25519 key and PID = base58(sha256(key))."""
    public_key, _private = generate_keypair()
    return Participant(
        pid=get_pid_from_public_key(public_key),
        display_name="real",
        public_key=public_key,
        type="person",
        status="active",
        profile={},
    )


async def _real_participant(factory) -> Participant:
    row = _real_row()
    async with factory() as s:
        s.add(row)
        await s.commit()
    return row


def _scenario(eq_code: str, pids: list[str], trustlines: list[tuple[str, str]], events=None) -> dict:
    return {
        "equivalents": [eq_code],
        "participants": [{"id": pid, "type": "person"} for pid in pids],
        "trustlines": [
            {"from": a, "to": b, "equivalent": eq_code, "limit": "100.00"} for a, b in trustlines
        ],
        "behaviorProfiles": [],
        "events": events or [],
    }


async def _trustlines_touching(factory, pid: str) -> int:
    async with factory() as s:
        pid_id = await s.scalar(select(Participant.id).where(Participant.pid == pid))
        return int(
            await s.scalar(
                select(func.count())
                .select_from(TrustLine)
                .where(
                    or_(
                        TrustLine.from_participant_id == pid_id,
                        TrustLine.to_participant_id == pid_id,
                    )
                )
            )
        )


async def _participant_exists(factory, pid: str) -> bool:
    async with factory() as s:
        return (await s.scalar(select(Participant.id).where(Participant.pid == pid))) is not None


async def _equivalent_exists(factory, code: str) -> bool:
    async with factory() as s:
        return (await s.scalar(select(Equivalent.id).where(Equivalent.code == code))) is not None


def _assert_refused(run: RunRecord, pid: str) -> None:
    assert run.state == "error", (run.state, run.last_error)
    assert run.last_error is not None
    assert run.last_error["code"] == PID_TAKEN, run.last_error
    assert pid in run.last_error["message"], run.last_error


def _tag() -> str:
    return uuid.uuid4().hex[:8].upper()


@pytest.mark.asyncio
async def test_seeding_a_scenario_that_names_a_real_participant_fails_the_run(factory, monkeypatch) -> None:
    real = await _real_participant(factory)
    n = _tag()
    eq_code = f"P24S{n}"
    newcomer = f"p024_new_{n}"
    scenario = _scenario(eq_code, [real.pid, newcomer], [(real.pid, newcomer), (newcomer, real.pid)])
    run = _fresh_run(f"p024-seed-{n}", f"p024-seed-{n}", scenario)
    runner = _runner(run, scenario)
    install_tick_stand(monkeypatch, factory)

    await _tick(runner, run)

    _assert_refused(run, real.pid)
    # Refused before anything was staged: nothing of the scenario landed.
    assert not await _participant_exists(factory, newcomer)
    assert not await _equivalent_exists(factory, eq_code)
    assert await _trustlines_touching(factory, real.pid) == 0
    assert run._real_participants is None


@pytest.mark.asyncio
async def test_a_second_run_of_the_same_scenario_adopts_its_simulated_participants(
    factory, monkeypatch
) -> None:
    # Counter-check (anti-vacuum): the rule must keep "run the scenario again" working.
    n = _tag()
    eq_code = f"P24A{n}"
    a, b = f"p024_a_{n}", f"p024_b_{n}"
    scenario = _scenario(eq_code, [a, b], [(a, b)])
    install_tick_stand(monkeypatch, factory)

    first = _fresh_run(f"p024-first-{n}", f"p024-again-{n}", scenario)
    await _tick(_runner(first, scenario), first)
    assert first.state == "running", first.last_error
    assert first._real_seeded is True
    assert await _participant_exists(factory, a) and await _participant_exists(factory, b)

    second = _fresh_run(f"p024-second-{n}", f"p024-again-{n}", scenario)
    await _tick(_runner(second, scenario), second)

    assert second.state == "running", second.last_error
    assert second.last_error is None
    assert second._real_seeded is True
    assert sorted(pid for _id, pid in second._real_participants) == sorted([a, b])
    assert await _trustlines_touching(factory, a) == 1


@pytest.mark.asyncio
async def test_a_scenario_line_to_a_participant_another_scenario_froze_fails_the_run_on_its_first_tick(
    factory, monkeypatch
) -> None:
    """The sibling refusal of the seeding, on the same path (D1, 2026-10-10): `SCENARIO_TRUSTLINE_REFUSED`.

    The adopted participant is simulated, so the perimeter above lets it in; it is the trust-line service that refuses
    the new line to its suspended end. The same scenario over the same database is refused on every tick, so the run
    stops on the FIRST one with the seeder's code, line and reason - not after three with
    `REAL_MODE_TICK_FAILED_REPEATED`. `_runner` allows three consecutive failures, so one tick ending in `error` is
    the fail-fast and not the budget."""
    n = _tag()
    eq_code = f"P24F{n}"
    a, frozen, c = f"p024_a_{n}", f"p024_frozen_{n}", f"p024_c_{n}"
    first_scenario = _scenario(eq_code, [a, frozen], [(a, frozen)])
    first_scenario["participants"][1]["status"] = "frozen"
    second_scenario = _scenario(eq_code, [c, frozen], [(c, frozen)])
    install_tick_stand(monkeypatch, factory)

    first = _fresh_run(f"p024-froze-{n}", f"p024-froze-{n}", first_scenario)
    await _tick(_runner(first, first_scenario), first)
    assert first.state == "running", first.last_error

    second = _fresh_run(f"p024-refused-{n}", f"p024-refused-{n}", second_scenario)
    await _tick(_runner(second, second_scenario), second)

    assert second.state == "error", (second.state, second.last_error)
    assert second.last_error["code"] == "SCENARIO_TRUSTLINE_REFUSED", second.last_error
    assert f"{c}->{frozen} {eq_code}" in second.last_error["message"], second.last_error
    assert "(reason: participant_suspended)" in second.last_error["message"], second.last_error
    assert set(second.last_error) == {"code", "message", "at"}  # the existing shape of `last_error`
    # One error - the stop itself (`fail_run`) - and no failed tick counted towards the three.
    assert (second.errors_total, second._real_consec_tick_failures) == (1, 0)
    assert second._real_seeded is False
    # Rolled back whole: nothing of the refused scenario stays.
    assert not await _participant_exists(factory, c)
    assert await _trustlines_touching(factory, frozen) == 1


def _seeded_run_with_event(monkeypatch, factory, n: str, effect: dict) -> tuple[RunRecord, RealRunnerImpl]:
    """A fresh run of two simulated participants whose first tick seeds them and fires `effect`."""
    eq_code = f"P24I{n}"
    a, b = f"p024_a_{n}", f"p024_b_{n}"
    events = [{"time": 0, "type": "inject", "effects": [effect]}]
    scenario = _scenario(eq_code, [a, b], [(a, b)], events=events)
    run = _fresh_run(f"p024-inject-{n}", f"p024-inject-{n}", scenario)
    runner = _runner(run, scenario)
    install_tick_stand(monkeypatch, factory)
    return run, runner


@pytest.mark.asyncio
async def test_an_inject_adding_a_participant_with_a_real_pid_fails_the_run(factory, monkeypatch) -> None:
    real = await _real_participant(factory)
    n = _tag()
    effect = {
        "op": "add_participant",
        "participant": {"id": real.pid, "type": "person"},
        "initial_trustlines": [{"sponsor": f"p024_a_{n}", "equivalent": f"P24I{n}", "limit": "10"}],
    }
    run, runner = _seeded_run_with_event(monkeypatch, factory, n, effect)

    await _tick(runner, run)

    _assert_refused(run, real.pid)
    assert await _trustlines_touching(factory, real.pid) == 0


@pytest.mark.asyncio
async def test_an_inject_trustline_to_a_real_participant_fails_the_run(factory, monkeypatch) -> None:
    # Sibling entry: `create_trustline` resolved an end outside the run from the global table.
    real = await _real_participant(factory)
    n = _tag()
    effect = {
        "op": "create_trustline",
        "from": f"p024_a_{n}",
        "to": real.pid,
        "equivalent": f"P24I{n}",
        "limit": "10",
    }
    run, runner = _seeded_run_with_event(monkeypatch, factory, n, effect)

    await _tick(runner, run)

    _assert_refused(run, real.pid)
    assert await _trustlines_touching(factory, real.pid) == 0


@pytest.mark.asyncio
async def test_an_inject_sponsored_by_a_real_participant_fails_the_run(factory, monkeypatch) -> None:
    # Sibling entry: an `add_participant` sponsor outside the run was looked up globally.
    real = await _real_participant(factory)
    n = _tag()
    newcomer = f"p024_new_{n}"
    effect = {
        "op": "add_participant",
        "participant": {"id": newcomer, "type": "person"},
        "initial_trustlines": [{"sponsor": real.pid, "equivalent": f"P24I{n}", "limit": "10"}],
    }
    run, runner = _seeded_run_with_event(monkeypatch, factory, n, effect)

    await _tick(runner, run)

    _assert_refused(run, real.pid)
    assert await _trustlines_touching(factory, real.pid) == 0
    assert not await _participant_exists(factory, newcomer)


@pytest.mark.asyncio
async def test_an_inject_freezing_a_real_participant_fails_the_run(factory, monkeypatch) -> None:
    # Sibling entry: `freeze_participant` suspended any participant found by pid.
    real = await _real_participant(factory)
    n = _tag()
    effect = {"op": "freeze_participant", "participant_id": real.pid}
    run, runner = _seeded_run_with_event(monkeypatch, factory, n, effect)

    await _tick(runner, run)

    _assert_refused(run, real.pid)
    async with factory() as s:
        status = await s.scalar(select(Participant.status).where(Participant.pid == real.pid))
    assert status == "active"


@pytest.mark.asyncio
async def test_interact_seeding_of_a_real_participant_is_409_with_code_pid_and_request_id(
    client, db_session, monkeypatch
) -> None:
    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    real = _real_row()
    db_session.add(real)
    await db_session.commit()

    n = _tag()
    eq_code = f"P24H{n}"
    newcomer = f"p024_new_{n}"
    scenario = _scenario(eq_code, [real.pid, newcomer], [(real.pid, newcomer)])
    registered = RunRecord(run_id="p024-http", scenario_id="p024-http", mode="real", state="paused")
    registered._scenario_raw = scenario
    monkeypatch.setitem(simulator_module.runtime._runs, "p024-http", registered)
    run = SimpleNamespace(
        run_id="p024-http",
        scenario_id="p024-http",
        mode="real",
        state="paused",
        owner_id="",
        _scenario_raw=scenario,
        _real_seeded=False,
        _real_seeding_lock=None,
    )
    monkeypatch.setattr(simulator_module.runtime, "get_run", lambda run_id: run)

    response = await client.post(
        "/api/v1/simulator/runs/p024-http/actions/trustline-create",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN, "X-Request-ID": f"p024-{n}"},
        json={
            "from_pid": real.pid,
            "to_pid": newcomer,
            "equivalent": eq_code,
            "limit": "10",
            "client_action_id": f"p024-{n}",
        },
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == PID_TAKEN
    assert body["details"]["pid"] == real.pid
    assert body["details"]["request_id"] == f"p024-{n}"
    assert run._real_seeded is False
    found = await db_session.scalar(select(Participant.id).where(Participant.pid == newcomer))
    assert found is None


def _canon_409_schema_ref(document: dict, path: str) -> str | None:
    """The schema `$ref` the document declares for `GET <path>` 409, following a response `$ref`."""
    operation = (document.get("paths") or {}).get(path, {}).get("get") or {}
    response = (operation.get("responses") or {}).get("409")
    if response is None:
        return None
    if "$ref" in response:
        name = response["$ref"].rsplit("/", 1)[-1]
        response = document["components"]["responses"][name]
    return response["content"]["application/json"]["schema"].get("$ref")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "canon_path"),
    [
        ("actions/trustlines-list", "/simulator/runs/{run_id}/actions/trustlines-list"),
        ("payment-targets", "/simulator/runs/{run_id}/payment-targets"),
    ],
)
async def test_read_actions_that_seed_answer_the_declared_409(
    client, db_session, monkeypatch, route: str, canon_path: str
) -> None:
    """Fix-delta F1: the two read actions reach the seeding refusal too, and must declare it."""
    from pathlib import Path

    import yaml

    import app.api.v1.simulator as simulator_module
    from app.main import app as fastapi_app

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    real = _real_row()
    db_session.add(real)
    await db_session.commit()
    n = _tag()
    eq_code = f"P24R{n}"
    scenario = _scenario(eq_code, [real.pid, f"p024_new_{n}"], [(real.pid, f"p024_new_{n}")])
    run = SimpleNamespace(
        run_id="p024-read", scenario_id="p024-read", mode="real", state="paused", owner_id="",
        _scenario_raw=scenario, _real_seeded=False, _real_seeding_lock=None,
    )
    monkeypatch.setattr(simulator_module.runtime, "get_run", lambda run_id: run)

    response = await client.get(
        f"/api/v1/simulator/runs/p024-read/{route}",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        params={"equivalent": eq_code, "from_pid": real.pid},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == PID_TAKEN
    canon = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "api" / "openapi.yaml").read_text(encoding="utf-8")
    )
    assert _canon_409_schema_ref(canon, canon_path) == "#/components/schemas/SimulatorActionError"
    generated = fastapi_app.openapi()
    assert _canon_409_schema_ref(generated, "/api/v1" + canon_path) == (
        "#/components/schemas/SimulatorActionError"
    )
