from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

from app.core.simulator.models import ScenarioRecord
from app.core.simulator.scenario_equivalent import (
    effective_equivalent,
    scenario_default_equivalent,
)
from app.utils.exceptions import BadRequestException
from app.utils.validation import validate_equivalent_code


_VALIDATORS_BY_SCHEMA_PATH: dict[str, Draft202012Validator] = {}

# An uploaded scenario_id becomes a directory name under `<local_state_dir>/scenarios/`
# (programme 024, F-024-4a). One rule of form: ASCII letters, digits, `-`, `_`, `.`; starts with a
# letter or digit, does not end with `.` (Windows drops a trailing dot); at most 128 characters.
# `fixtures/simulator/scenario.schema.json` carries the same pattern for `scenario_id`, but the
# schema validator is optional, so the code checks the form itself.
SCENARIO_ID_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?$"
_SCENARIO_ID_RE = re.compile(SCENARIO_ID_PATTERN)


def is_safe_scenario_id(value: object) -> bool:
    return isinstance(value, str) and _SCENARIO_ID_RE.fullmatch(value) is not None


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


def _scenario_equivalent_sources(raw: dict[str, Any]) -> Iterator[tuple[str, str]]:
    for index, value in enumerate(raw.get("equivalents") or []):
        code = str(value).strip().upper()
        if code:
            yield f"equivalents/{index}", code

    default_code = scenario_default_equivalent(raw)
    if default_code:
        default_path = "baseEquivalent" if raw.get("baseEquivalent") else "equivalent"
        yield default_path, default_code

    for index, trustline in enumerate(raw.get("trustlines") or []):
        if not isinstance(trustline, dict):
            continue
        code = str(trustline.get("equivalent") or "").strip().upper()
        if code:
            yield f"trustlines/{index}/equivalent", code

    for event_index, event in enumerate(raw.get("events") or []):
        if not isinstance(event, dict):
            continue
        for effect_index, effect in enumerate(event.get("effects") or []):
            if not isinstance(effect, dict):
                continue
            code = str(effect.get("equivalent") or "").strip().upper()
            if code:
                yield f"events/{event_index}/effects/{effect_index}/equivalent", code
            for trustline_index, trustline in enumerate(
                effect.get("initial_trustlines") or []
            ):
                if not isinstance(trustline, dict):
                    continue
                code = str(trustline.get("equivalent") or "").strip().upper()
                if code:
                    yield (
                        f"events/{event_index}/effects/{effect_index}/"
                        f"initial_trustlines/{trustline_index}/equivalent",
                        code,
                    )


def _validate_scenario_equivalent_codes(raw: dict[str, Any]) -> None:
    errors = []
    for path, code in _scenario_equivalent_sources(raw):
        try:
            validate_equivalent_code(code)
        except BadRequestException:
            errors.append(
                {
                    "path": path,
                    "message": f"Noncanonical equivalent code: {code}",
                }
            )
            if len(errors) == 50:
                break

    if errors:
        raise BadRequestException(
            "Scenario invalid",
            details={
                "simulator_error": "SCENARIO_INVALID",
                "errors": errors,
            },
        )


def scenario_to_record(
    raw: dict[str, Any],
    *,
    source_path: Optional[Path],
    created_at: Optional[datetime],
) -> ScenarioRecord:
    _validate_scenario_equivalent_codes(raw)

    scenario_id = str(raw.get("scenario_id") or raw.get("id") or "").strip()
    if not scenario_id:
        scenario_id = source_path.parent.name if source_path is not None else "unknown"

    participants = raw.get("participants") or []
    trustlines = raw.get("trustlines") or []
    equivalents_raw = raw.get("equivalents")
    eq_set: set[str] = set(
        str(x).strip().upper() for x in (equivalents_raw or [])
    )
    eq_set.discard("")

    default_eq = scenario_default_equivalent(raw)
    if default_eq:
        eq_set.add(default_eq)

    for tl in (trustlines or []):
        eq = effective_equivalent(raw, tl)
        if eq:
            eq_set.add(str(eq).strip().upper())

    equivalents = sorted(eq_set)

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

        raw_id = scenario.get("scenario_id")
        if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
            raise BadRequestException("Scenario must contain scenario_id")
        if not is_safe_scenario_id(raw_id):
            raise BadRequestException(
                "scenario_id must be 1-128 ASCII letters, digits, '-', '_' or '.', "
                "starting with a letter or digit and not ending with '.'",
                details={"scenario_id": raw_id},
            )
        scenario_id: str = raw_id

        root = (self._local_state_dir / "scenarios").resolve()
        base = (root / scenario_id).resolve()
        if base.parent != root:
            raise BadRequestException(
                "scenario_id does not name a directory inside the scenarios store",
                details={"scenario_id": scenario_id},
            )
        path = base / "scenario.json"

        with self._lock:
            registered = scenario_id in self._scenarios
        if registered or path.exists():
            raise BadRequestException(
                f"Scenario {scenario_id} already exists",
                details={"scenario_id": scenario_id},
            )

        rec = scenario_to_record(
            scenario,
            source_path=path,
            created_at=self._utc_now(),
        )
        base.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
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
            # Guardrail: ignore archived / private dirs (e.g. `_archive/`).
            # Runtime only treats top-level `*/scenario.json` as loadable fixtures.
            if child.name.startswith("_"):
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
            except Exception:
                self._logger.exception("simulator.uploaded_scenario_load_failed path=%s", str(scenario_path))
                continue
            # An upload may neither carry an unsafe id nor replace a bundled preset
            # (programme 024, F-024-4a); a file written before that rule is skipped, not loaded.
            if not is_safe_scenario_id(rec.scenario_id) or rec.scenario_id != child.name:
                self._logger.warning(
                    "simulator.uploaded_scenario_skipped reason=id_mismatch dir=%s", child.name
                )
                continue
            if rec.scenario_id in self._scenarios:
                self._logger.warning(
                    "simulator.uploaded_scenario_skipped reason=shadows_registered id=%s",
                    rec.scenario_id,
                )
                continue
            self._scenarios[rec.scenario_id] = rec
