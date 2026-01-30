from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

from app.core.simulator.models import ScenarioRecord
from app.utils.exceptions import BadRequestException


_VALIDATORS_BY_SCHEMA_PATH: dict[str, Draft202012Validator] = {}


def get_scenario_validator(*, schema_path: Path) -> Optional[Draft202012Validator]:
    """Returns cached JSONSchema validator if schema exists."""

    try:
        key = str(schema_path.resolve())
    except Exception:
        key = str(schema_path)

    cached = _VALIDATORS_BY_SCHEMA_PATH.get(key)
    if cached is not None:
        return cached

    if not schema_path.exists():
        return None

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    _VALIDATORS_BY_SCHEMA_PATH[key] = validator
    return validator


def validate_scenario_or_400(*, raw: dict[str, Any], schema_path: Path) -> None:
    validator = get_scenario_validator(schema_path=schema_path)
    if validator is None:
        return

    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
    if not errors:
        return

    def _err(e):
        return {
            "path": "/".join(str(p) for p in e.path),
            "message": e.message,
        }

    raise BadRequestException(
        "Scenario invalid",
        details={
            "simulator_error": "SCENARIO_INVALID",
            "errors": [_err(e) for e in errors[:50]],
        },
    )


def scenario_to_record(
    raw: dict[str, Any],
    *,
    source_path: Optional[Path],
    created_at: Optional[datetime],
) -> ScenarioRecord:
    scenario_id = str(raw.get("scenario_id") or raw.get("id") or "").strip()
    if not scenario_id:
        scenario_id = source_path.parent.name if source_path is not None else "unknown"

    participants = raw.get("participants") or []
    trustlines = raw.get("trustlines") or []
    equivalents = raw.get("equivalents") or []

    name = raw.get("name")
    return ScenarioRecord(
        scenario_id=scenario_id,
        name=str(name) if name is not None else None,
        created_at=created_at,
        participants_count=int(len(participants)),
        trustlines_count=int(len(trustlines)),
        equivalents=[str(x) for x in equivalents],
        raw=raw,
        source_path=source_path,
    )


class ScenarioRegistry:
    def __init__(
        self,
        *,
        lock: Any,
        scenarios: dict[str, ScenarioRecord],
        fixtures_dir: Path,
        schema_path: Path,
        local_state_dir: Path,
        utc_now: Any,
        logger: logging.Logger,
    ) -> None:
        self._lock = lock
        self._scenarios = scenarios
        self._fixtures_dir = fixtures_dir
        self._schema_path = schema_path
        self._local_state_dir = local_state_dir
        self._utc_now = utc_now
        self._logger = logger

    def load_all(self) -> None:
        self.load_fixture_scenarios()
        self.load_uploaded_scenarios()

    def save_uploaded_scenario(self, scenario: dict[str, Any]) -> ScenarioRecord:
        validate_scenario_or_400(raw=scenario, schema_path=self._schema_path)

        scenario_id = str(scenario.get("scenario_id") or "").strip()
        if not scenario_id:
            raise BadRequestException("Scenario must contain scenario_id")

        base = self._local_state_dir / "scenarios" / scenario_id
        base.mkdir(parents=True, exist_ok=True)
        path = base / "scenario.json"

        if path.exists():
            raise BadRequestException(
                f"Scenario {scenario_id} already exists",
                details={"scenario_id": scenario_id},
            )

        path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
        rec = scenario_to_record(scenario, source_path=path, created_at=self._utc_now())
        with self._lock:
            self._scenarios[scenario_id] = rec
        return rec

    def load_fixture_scenarios(self) -> None:
        if not self._fixtures_dir.exists():
            self._logger.warning("simulator.fixtures_missing path=%s", str(self._fixtures_dir))
            return

        for child in sorted(self._fixtures_dir.iterdir()):
            if not child.is_dir():
                continue
            scenario_path = child / "scenario.json"
            if not scenario_path.exists():
                continue
            try:
                raw = json.loads(scenario_path.read_text(encoding="utf-8"))
                rec = scenario_to_record(raw, source_path=scenario_path, created_at=None)
                self._scenarios[rec.scenario_id] = rec
            except Exception:
                self._logger.exception("simulator.fixture_scenario_load_failed path=%s", str(scenario_path))
                continue

    def load_uploaded_scenarios(self) -> None:
        base = self._local_state_dir / "scenarios"
        if not base.exists():
            return

        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            scenario_path = child / "scenario.json"
            if not scenario_path.exists():
                continue
            try:
                raw = json.loads(scenario_path.read_text(encoding="utf-8"))
                rec = scenario_to_record(raw, source_path=scenario_path, created_at=None)
                self._scenarios[rec.scenario_id] = rec
            except Exception:
                continue
