"""MiniMax pilot M2 (BACKLOG 2026-10-31): three ways a scenario's trust line entered the registry and then vanished at
seeding, or was stored as a different number - each is now refused where the scenario enters, and by name in the seeder.

* MM2-MM-A - a line whose `from`/`to` is not a participant of the scenario passed the upload (`story_errors` checked the
  story's participants, not the lines' ends); the seeder skipped it without a word (`real_scenario_seeder.py`, `if p_from
  is None or p_to is None: continue`) and the planner still planned payments to the "ghost" (witness: 500 of 1000).
  An end introduced only by an `add_participant` inject is not enough for a `trustlines[]` line: the seeder runs BEFORE
  the first inject (`tick.py`, seeding, then the due events) and resolves ends among `participants[]` only, so such a
  line could never be created - the inject's own lines are `initial_trustlines` / `create_trustline`.
* MM2-SOL-2 - a negative limit (`"-1"`) passed the registry (only finiteness was judged) and the seeder skipped the
  line; so did an unparsable one (`"abc"`).
* MM2-MM-B (section 8) - a JSON-number limit passed the schema and the registry and reached the seeder through a binary
  float (`Decimal(str(float))`): `123456789012.12345678` at precision 8 was stored as `...12346000`. A scenario limit is
  a decimal string; a JSON number is refused when the scenario enters the registry (upload, stored upload, shipped
  fixture - all go through `scenario_to_record`), and the seeder refuses a non-string by name.

NOT CHANGED HERE: a limit too large for `Numeric(20, 8)` is still skipped by the seeder (`money_storability_violation`,
012) - see `tests/integration/test_p035_money_storability_does_not_depend_on_the_decimal_context.py`.

Anti-vacuum: `"0"` and `"100"` (and a fraction) upload and seed with their exact values; every shipped fixture still
enters the registry and has a servable story.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder, ScenarioTrustLineRefused
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry
from app.core.simulator.scenario_story import story_errors
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures" / "simulator"
SCHEMA = FIXTURES / "scenario.schema.json"
ORIGIN = {"Origin": "http://localhost:5176"}
UPLOAD_HEADERS = {**ORIGIN, "X-Admin-Token": settings.ADMIN_TOKEN}
PREFIX = "mm-m2-"


@pytest.fixture
def registry(monkeypatch, tmp_path: Path):
    reg = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=runtime._scenarios,
        fixtures_dir=tmp_path / "fixtures",
        schema_path=SCHEMA,
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", reg)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "all")
    try:
        yield reg
    finally:
        for key in [k for k in runtime._scenarios if k.startswith(PREFIX)]:
            runtime._scenarios.pop(key, None)


def _scenario(name: str, line: dict, *, events: list | None = None) -> dict:
    out = {
        "schema_version": "scenario/1",
        "scenario_id": PREFIX + name,
        "equivalents": ["UAH"],
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}],
        "trustlines": [{"from": "A", "to": "B", "equivalent": "UAH", "limit": "10"}, {"equivalent": "UAH", **line}],
    }
    if events is not None:
        out["events"] = events
    return out


async def _upload_text(client, body: str):
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.status_code == 200, ensured.text
    return await client.post(
        "/api/v1/simulator/scenarios", headers={**UPLOAD_HEADERS, "Content-Type": "application/json"}, content=body
    )


async def _upload(client, scenario: dict):
    return await _upload_text(client, json.dumps({"scenario": scenario}))


def _refused_paths(response) -> list[str]:
    assert response.status_code == 400, f"{response.status_code}: {response.text[:400]}"
    details = response.json()["error"]["details"]
    assert details["simulator_error"] == "SCENARIO_INVALID", details
    return [e["path"] for e in details["errors"]]


# ------------------------------------------------------------------------------------------------ the upload (HTTP)

INJECT_ADDS_C = [{"time": 1000, "type": "inject", "effects": [
    {"op": "add_participant", "participant": {"id": "C", "type": "person"},
     "initial_trustlines": [{"sponsor": "A", "equivalent": "UAH", "limit": "5"}]}]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("line,events,path", [
    ({"from": "GHOST", "to": "A", "limit": "100"}, None, "trustlines/1/from"),
    ({"from": "A", "to": "GHOST", "limit": "100"}, None, "trustlines/1/to"),
    # introduced by an inject only: the seeder runs before the first inject and never resolves it
    ({"from": "A", "to": "C", "limit": "100"}, INJECT_ADDS_C, "trustlines/1/to"),
], ids=["ghost creditor", "ghost debtor", "inject-only debtor"])
async def test_mm_a_a_line_to_an_undeclared_participant_is_refused_at_upload(client, registry, line, events, path):
    name = "ghost-" + path.rsplit("/", 1)[-1] + ("-inject" if events else "")
    response = await _upload(client, _scenario(name, line, events=events))
    assert _refused_paths(response) == [path]
    assert PREFIX + name not in runtime._scenarios


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["-1", "-0.01", "abc", "1e3", " 100", "NaN"])
async def test_sol2_a_limit_that_is_not_a_non_negative_decimal_string_is_refused_at_upload(client, registry, limit):
    name = f"bad-str-{abs(hash(limit)) % 10**6}"
    response = await _upload(client, _scenario(name, {"from": "B", "to": "A", "limit": limit}))
    assert _refused_paths(response) == ["trustlines/1/limit"]
    assert PREFIX + name not in runtime._scenarios


@pytest.mark.asyncio
@pytest.mark.parametrize("literal", ["7", "7.5", "123456789012.12345678", "100000000000000.01", "0"])
async def test_mm_b_a_json_number_limit_is_refused_at_upload(client, registry, literal):
    """The body carries a bare JSON number literal - what a client sends, and what `json` makes a float or an int of."""

    name = "number-" + literal.replace(".", "-")
    scenario = _scenario(name, {"from": "B", "to": "A", "limit": "__LIMIT__"})
    body = json.dumps({"scenario": scenario}).replace('"__LIMIT__"', literal)
    response = await _upload_text(client, body)
    assert _refused_paths(response) == ["trustlines/1/limit"]
    assert PREFIX + name not in runtime._scenarios


@pytest.mark.asyncio
@pytest.mark.parametrize("effect,path", [
    ({"op": "add_participant", "participant": {"id": "C", "type": "person"},
      "initial_trustlines": [{"sponsor": "A", "equivalent": "UAH", "limit": 5}]}, "events/0/effects/0"),
    ({"op": "create_trustline", "from": "B", "to": "A", "equivalent": "UAH", "limit": 5.5}, "events/0/effects/0"),
], ids=["initial_trustlines", "create_trustline"])
async def test_mm_b_an_inject_limit_that_is_a_json_number_is_refused_at_upload(client, registry, effect, path):
    name = "inject-number-" + effect["op"]
    scenario = _scenario(name, {"from": "B", "to": "A", "limit": "1"},
                         events=[{"time": 1000, "type": "inject", "effects": [effect]}])
    response = await _upload(client, scenario)
    assert _refused_paths(response) == [path]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["0", "100", "100.50", "000.10"])
async def test_control_a_decimal_string_limit_between_participants_uploads(client, registry, limit):
    name = "ok-" + limit.replace(".", "-")
    response = await _upload(client, _scenario(name, {"from": "B", "to": "A", "limit": limit}))
    assert response.status_code == 200, response.text
    preview = await client.get(f"/api/v1/simulator/scenarios/{PREFIX}{name}/graph/preview", params={"equivalent": "UAH"})
    assert preview.status_code == 200, preview.text
    assert sorted(Decimal(link["trust_limit"]) for link in preview.json()["links"]) == sorted([Decimal("10"), Decimal(limit)])


@pytest.mark.asyncio
async def test_control_an_inject_with_string_limits_and_its_own_lines_uploads(client, registry):
    effects = INJECT_ADDS_C[0]["effects"] + [{"op": "create_trustline", "from": "B", "to": "A", "equivalent": "UAH",
                                              "limit": "3.00"}]
    scenario = _scenario("ok-inject", {"from": "B", "to": "A", "limit": "1"},
                         events=[{"time": 1000, "type": "inject", "effects": effects}])
    response = await _upload(client, scenario)
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------------------------- stored and shipped scenarios


@pytest.mark.parametrize("store", ["uploaded", "fixture"])
def test_mm_b_a_stored_scenario_with_a_number_limit_is_not_loaded(registry, store) -> None:
    root = registry._local_state_dir / "scenarios" if store == "uploaded" else registry._fixtures_dir
    # The registry alone judges a stored or shipped scenario (no schema at load): a number, a sign, an exponent, a space
    # and a non-finite value are each refused by it (review F4, 2026-10-31), the plain string loads.
    cases = [7, 7.5, "-1", "1e3", " 100", "NaN", "8"]
    for i, limit in enumerate(cases):
        name = f"disk-{store}-{i}"
        directory = root / (PREFIX + name)
        directory.mkdir(parents=True)
        text = json.dumps(_scenario(name, {"from": "B", "to": "A", "limit": limit}))
        (directory / "scenario.json").write_text(text, encoding="utf-8")

    registry.load_uploaded_scenarios() if store == "uploaded" else registry.load_fixture_scenarios()

    loaded = sorted(k for k in runtime._scenarios if k.startswith(PREFIX + f"disk-{store}-"))
    assert loaded == [PREFIX + f"disk-{store}-{len(cases) - 1}"], loaded  # the string control loads


@pytest.mark.asyncio
async def test_mm_a_a_stored_scenario_with_a_ghost_line_is_refused_by_the_detail_and_the_run(client, registry, monkeypatch):
    """A stored or shipped scenario does not pass `story_errors` at load (`scenario_to_record`); the same rule refuses it
    where it is used - the detail read and the run creation (`build_story`, 409 `E008`)."""

    from app.core.simulator.scenario_registry import scenario_to_record

    name = PREFIX + "stored-ghost"
    record = scenario_to_record(_scenario("stored-ghost", {"from": "GHOST", "to": "A", "limit": "5"}),
                                source_path=None, created_at=None)
    monkeypatch.setitem(runtime._scenarios, name, record)
    client.cookies.clear()
    assert (await client.post("/api/v1/simulator/session/ensure")).status_code == 200

    detail = await client.get(f"/api/v1/simulator/scenarios/{name}")
    assert detail.status_code == 409, detail.text
    assert [e["path"] for e in detail.json()["error"]["details"]["errors"]] == ["trustlines/1/from"]

    from app.core.simulator.scenario_story import ScenarioStoryInvalid

    with pytest.raises(ScenarioStoryInvalid) as refused:
        await runtime.create_run(scenario_id=name, mode="real", owner_id="mm-m2", owner_kind="admin")
    assert [e["path"] for e in refused.value.errors] == ["trustlines/1/from"]


def test_every_shipped_fixture_enters_the_registry_with_a_servable_story(monkeypatch, tmp_path) -> None:
    """Anti-vacuum for the refusals above: the rules refuse no shipped scenario (all limits are decimal strings, every
    line's ends are participants)."""

    scenarios: dict = {}
    reg = ScenarioRegistry(lock=threading.RLock(), scenarios=scenarios, fixtures_dir=FIXTURES, schema_path=SCHEMA,
                           local_state_dir=tmp_path / "state", utc_now=lambda: datetime.now(timezone.utc),
                           logger=logging.getLogger(__name__))
    reg.load_fixture_scenarios()
    shipped = sorted(p.parent.name for p in FIXTURES.glob("*/scenario.json") if not p.parent.name.startswith("_"))
    by_dir = {rec.source_path.parent.name: rec for rec in scenarios.values()}
    assert sorted(by_dir) == shipped and len(shipped) == 6, (sorted(by_dir), shipped)
    for name, rec in by_dir.items():
        assert story_errors(rec.raw) == [], name
        assert all(isinstance(t["limit"], str) for t in rec.raw["trustlines"]), name


# --------------------------------------------------------------------------------------------- the seeder (second line)


def _ids() -> tuple[str, str, str]:
    n = uuid.uuid4().hex[:6].upper()
    return f"MM2A_{n}", f"MM2B_{n}", f"MM2{n}"


def _seed_scenario(a: str, b: str, eq: str, line: dict) -> dict:
    return {"equivalents": [eq], "participants": [{"id": a}, {"id": b}],
            "trustlines": [{"from": a, "to": b, "equivalent": eq, "limit": "100"}, {"equivalent": eq, **line}]}


async def _lines(db_session, eq: str) -> list[Decimal]:
    eq_id = (await db_session.execute(select(Equivalent.id).where(Equivalent.code == eq))).scalar_one_or_none()
    if eq_id is None:
        return []
    rows = (await db_session.execute(select(TrustLine.limit).where(TrustLine.equivalent_id == eq_id))).scalars()
    return sorted(Decimal(str(x)) for x in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["from", "to"])
async def test_mm_a_the_seeder_refuses_a_line_to_an_undeclared_participant_by_name(db_session, end) -> None:
    a, b, eq = _ids()
    ghost = f"MM2G_{eq}"
    line = {"from": b, "to": a, "limit": "100", end: ghost}
    with pytest.raises(ScenarioTrustLineRefused) as caught:
        await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=_seed_scenario(a, b, eq, line))
    assert caught.value.details["reason"] == "trust_line_end_not_a_participant", caught.value.details
    assert caught.value.details["line"] == f"{line['from']}->{line['to']} {eq}"
    assert caught.value.permanent
    ghost_rows = (await db_session.execute(select(Participant.id).where(Participant.pid == ghost))).all()
    assert ghost_rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,reason", [
    ("-1", "limit_negative"),
    ("-0.01", "limit_negative"),
    ("abc", "limit_not_a_decimal_string"),
    (7, "limit_not_a_decimal_string"),
    (123456789012.12345678, "limit_not_a_decimal_string"),
    (100000000000000.01, "limit_not_a_decimal_string"),
    (None, "limit_not_a_decimal_string"),
], ids=["-1", "-0.01", "abc", "int 7", "float 12+8 digits", "float overflow", "None"])
async def test_sol2_mm_b_the_seeder_refuses_a_bad_limit_by_name(db_session, limit, reason) -> None:
    a, b, eq = _ids()
    with pytest.raises(ScenarioTrustLineRefused) as caught:
        await RealScenarioSeeder().seed_scenario_into_db(
            session=db_session, scenario=_seed_scenario(a, b, eq, {"from": b, "to": a, "limit": limit}))
    assert caught.value.details["reason"] == reason, caught.value.details
    assert caught.value.details["line"] == f"{b}->{a} {eq}"
    assert caught.value.permanent


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["0", "100", "0.50"])
async def test_control_the_seeder_seeds_a_decimal_string_limit_exactly(db_session, limit) -> None:
    a, b, eq = _ids()
    await RealScenarioSeeder().seed_scenario_into_db(
        session=db_session, scenario=_seed_scenario(a, b, eq, {"from": b, "to": a, "limit": limit}))
    await db_session.flush()
    assert await _lines(db_session, eq) == sorted([Decimal("100"), Decimal(limit)])
