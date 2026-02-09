#!/usr/bin/env python3
"""scripts/generate_scenario_events.py

Этот файл исторически был утилитой, которая *патчит* (перезаписывает поле
``events``) у двух fixture-сценариев.

Цель доработки: сделать скрипт **воспроизводимым генератором events с реальными
PID** (без фейковых PID по умолчанию) и при этом **не сломать** текущие
потребители.

Режимы работы
-------------

1) Legacy (backward-compatible): без аргументов — как раньше.

   - перезаписывает ``events`` в двух фиксированных файлах сценариев
   - использует детерминированные *фейковые* PID для wave-participants

   Пример:
       python scripts/generate_scenario_events.py

2) Генератор JSONL/NDJSON событий (новый режим):

   - читает входной scenario JSON
   - генерирует поток событий (по умолчанию: ``tx.updated``) **только на базе
     существующих PID** из ``scenario["participants"]``
   - пишет NDJSON/JSONL (или JSON массив)
   - полностью воспроизводим при одинаковых входах + ``--seed`` + ``--start_ts``

   Примеры:
       python scripts/generate_scenario_events.py generate \
         --scenario fixtures/simulator/riverside-town-50-realistic-v2/scenario.json \
         --out artifacts/events.ndjson \
         --seed 1 \
         --count 200 \
         --equivalent UAH \
         --start_ts 2026-01-01T00:00:00Z

       python scripts/generate_scenario_events.py generate \
         --scenario fixtures/simulator/riverside-town-50-realistic-v2/scenario.json \
         --out artifacts/events.json \
         --format json \
         --seed 1 \
         --duration 30000 \
         --interval_ms 250

Формат времени
-------------

Для generated events поле ``ts`` пишется в ISO-8601 в UTC с суффиксом ``Z``
(например ``2026-01-01T00:00:00.250Z``). ``--start_ts`` принимает:

- ISO8601: ``2026-01-01T00:00:00Z`` / ``2026-01-01T00:00:00.123Z``
- Unix epoch в миллисекундах: ``1735689600000``

"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal

ROOT = Path(__file__).resolve().parent.parent


class ScenarioEventsGeneratorError(RuntimeError):
    pass


def _die(msg: str) -> "NoReturn":
    raise ScenarioEventsGeneratorError(msg)


def _stable_sorted_strs(values: Iterable[str]) -> list[str]:
    return sorted((str(v) for v in values), key=lambda s: (s.lower(), s))


def _as_path(p: str) -> Path:
    return Path(p).expanduser()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _die(f"Scenario file not found: {path}")
    except json.JSONDecodeError as e:
        _die(f"Invalid JSON in {path}: {e}")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _iso_z_from_ms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    # Всегда пишем миллисекунды, чтобы было проще сравнивать/диффать.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_start_ts_to_ms(raw: str) -> int:
    raw = str(raw).strip()
    if raw == "":
        _die("--start_ts must be non-empty")

    # 1) epoch ms
    if raw.isdigit():
        try:
            ms = int(raw)
        except ValueError:
            _die(f"Invalid --start_ts (epoch ms): {raw}")
        if ms < 0:
            _die(f"--start_ts must be >= 0 (epoch ms), got: {ms}")
        return ms

    # 2) ISO8601 Z
    # Поддерживаем только UTC, чтобы избежать сюрпризов с TZ.
    if raw.endswith("Z"):
        iso = raw[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(iso, fmt).replace(tzinfo=UTC)
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass
        _die(
            "Invalid --start_ts ISO8601Z. Expected like 2026-01-01T00:00:00Z or 2026-01-01T00:00:00.123Z"
        )

    _die(
        "Invalid --start_ts. Use ISO8601Z (e.g. 2026-01-01T00:00:00Z) or epoch milliseconds (e.g. 1735689600000)"
    )


def _parse_duration_ms(raw: str | int) -> int:
    """Парсер для --duration.

    Backward/simple: принимаем либо целое (ms), либо строку вида "30s", "2m",
    "1500ms".
    """

    if isinstance(raw, int):
        ms = raw
    else:
        s = str(raw).strip().lower()
        if s.isdigit():
            ms = int(s)
        elif s.endswith("ms") and s[:-2].isdigit():
            ms = int(s[:-2])
        elif s.endswith("s") and s[:-1].isdigit():
            ms = int(s[:-1]) * 1000
        elif s.endswith("m") and s[:-1].isdigit():
            ms = int(s[:-1]) * 60 * 1000
        else:
            _die("Invalid --duration. Use ms integer or suffix: 1500ms, 30s, 2m")

    if ms <= 0:
        _die(f"--duration must be > 0, got: {ms}")
    return ms


@dataclass(frozen=True)
class ScenarioInfo:
    scenario_id: str | None
    equivalents: list[str]
    participant_ids: list[str]


def _read_scenario_info(scenario_path: Path) -> ScenarioInfo:
    data = _load_json(scenario_path)
    if not isinstance(data, dict):
        _die(f"Scenario must be a JSON object: {scenario_path}")

    scenario_id = data.get("scenario_id")
    if scenario_id is not None and not isinstance(scenario_id, str):
        _die(f"scenario_id must be a string when present: {scenario_path}")

    equivalents_raw = data.get("equivalents")
    equivalents: list[str] = []
    if equivalents_raw is None:
        equivalents = []
    elif isinstance(equivalents_raw, list) and all(isinstance(x, str) for x in equivalents_raw):
        equivalents = _stable_sorted_strs(equivalents_raw)
    else:
        _die(f"equivalents must be an array of strings: {scenario_path}")

    participants_raw = data.get("participants")
    if not isinstance(participants_raw, list):
        _die(f"participants must be an array: {scenario_path}")

    pids: list[str] = []
    for i, p in enumerate(participants_raw):
        if not isinstance(p, dict):
            _die(f"participants[{i}] must be an object: {scenario_path}")
        pid = p.get("id")
        if not isinstance(pid, str) or not pid.strip():
            _die(f"participants[{i}].id must be a non-empty string: {scenario_path}")
        pids.append(pid)

    pids = _stable_sorted_strs(pids)
    if len(pids) < 2:
        _die(f"Scenario must contain at least 2 participants to generate tx events, got {len(pids)}: {scenario_path}")

    if len(set(pids)) != len(pids):
        # Стабильная ошибка (сортировали выше): показываем дубликаты.
        seen: set[str] = set()
        dups: list[str] = []
        for pid in pids:
            if pid in seen:
                dups.append(pid)
            seen.add(pid)
        _die(f"Scenario has duplicate participant ids: {sorted(set(dups))}")

    return ScenarioInfo(scenario_id=scenario_id, equivalents=equivalents, participant_ids=pids)


def _new_fake_pid(index: int) -> str:
    """Детерминированный *фейковый* PID для legacy patch режима.

    Важно: этот PID не обязан быть "реальным" с точки зрения протокола.
    Для *генератора* NDJSON событий мы такие PID по умолчанию НЕ используем.
    """

    raw = uuid.uuid5(uuid.NAMESPACE_DNS, f"wave-participant-{index:04d}")
    short = raw.hex[:8]
    return f"PID_W{index:04d}_{short}"


def _build_riverside_legacy_timeline_events(*, allow_fake_pids: bool) -> list[dict[str, Any]]:
    """Legacy timeline events для riverside-town-50-realistic-v2.

    По умолчанию (в legacy режиме) allow_fake_pids=True для backward-compat.
    """

    if not allow_fake_pids:
        _die(
            "Legacy patch timeline uses add_participant + synthetic PIDs. "
            "Re-run with --allow_fake_pids (or use 'generate' mode which uses only real scenario participants)."
        )

    new_pid = _new_fake_pid

    # ─────────────────────────────────────────────────────────────────────────
    # Riverside Town 50 — events
    # ─────────────────────────────────────────────────────────────────────────
    return [
    # ── Phase 0: launch note ──
    {
        "time": 0,
        "type": "note",
        "description": "Network launched: 50 participants, UAH equivalent",
    },
    # ── Phase 1: market day burst (tick ~30 s) ──
    {
        "time": 30000,
        "type": "stress",
        "description": "Weekend fish market — households rush to buy fresh catch",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "group:households", "value": 2.5},
            {"op": "mult", "field": "tx_rate", "scope": "group:retail", "value": 1.8},
        ],
        "metadata": {"duration_ms": 8000},
    },
    # ── Phase 2: Monday morning lull ──
    {
        "time": 45000,
        "type": "stress",
        "description": "Monday morning — slow start to the week",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.3},
        ],
        "metadata": {"duration_ms": 10000},
    },
    # ── Phase 3: Wave 2 onboarding — 3 new households ──
    {
        "time": 60000,
        "type": "inject",
        "description": "Wave 2: Three new families join (recommended by neighbors)",
        "effects": [
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(51),
                    "name": "The Moroz Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    # Sponsor: Fresh Catch Fish Shop (top hub, 17 tl)
                    {"sponsor": "PID_U0016_e3779b10", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "500"},
                    # Sponsor: The Rybchenko Family (neighbor, 12 tl)
                    {"sponsor": "PID_U0034_035e2982", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "400"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(52),
                    "name": "The Stepanenko Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0019_be1e0823", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "350"},
                    {"sponsor": "PID_U0039_1a7389f7", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "300"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(53),
                    "name": "The Koval Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0016_e3779b10", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "600"},
                    {"sponsor": "PID_U0001_9e3779b1", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "800"},
                ],
            },
        ],
    },
    # ── Phase 4: new families earn trust from shops ──
    {
        "time": 90000,
        "type": "inject",
        "description": "New families earn trust: shops open lines to them",
        "effects": [
            {"op": "create_trustline", "from": "PID_U0019_be1e0823", "to": new_pid(51), "equivalent": "UAH", "limit": "450"},
            {"op": "create_trustline", "from": "PID_U0018_1fe68e72", "to": new_pid(52), "equivalent": "UAH", "limit": "350"},
            {"op": "create_trustline", "from": "PID_U0002_3c6ef362", "to": new_pid(53), "equivalent": "UAH", "limit": "700"},
        ],
    },
    # ── Phase 5: seasonal surge — fishing season peak ──
    {
        "time": 120000,
        "type": "stress",
        "description": "Peak fishing season — producers and retail booming",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "group:producers", "value": 2.0},
            {"op": "mult", "field": "tx_rate", "scope": "group:retail", "value": 1.5},
            {"op": "mult", "field": "tx_rate", "scope": "group:households", "value": 1.3},
        ],
        "metadata": {"duration_ms": 15000},
    },
    # ── Phase 6: Wave 3 — new service provider ──
    {
        "time": 150000,
        "type": "inject",
        "description": "Wave 3: New boat mechanic joins (referred by Marina & Boat Services)",
        "effects": [
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(54),
                    "name": "Taras Motorny (Boat Mechanic Jr)",
                    "type": "person",
                    "status": "active",
                    "groupId": "services",
                    "behaviorProfileId": "service",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0005_17156075", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1200"},
                    {"sponsor": "PID_U0024_d5336898", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "600"},
                ],
            },
        ],
    },
    # ── Phase 7: seasonal slowdown ──
    {
        "time": 200000,
        "type": "stress",
        "description": "Winter approaches — tourism drops, activity slows",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.5},
        ],
        "metadata": {"duration_ms": 20000},
    },
    # ── Phase 8: one family leaves ──
    {
        "time": 250000,
        "type": "inject",
        "description": "The Stavkovi family moves to the city",
        "effects": [
            {
                "op": "freeze_participant",
                "participant_id": "PID_U0037_de049695",
                "freeze_trustlines": True,
            },
        ],
    },
    # ── Phase 9: spring revival ──
    {
        "time": 300000,
        "type": "stress",
        "description": "Spring festival — community celebration, big spending day",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "all", "value": 3.0},
        ],
        "metadata": {"duration_ms": 5000},
    },
    ]


def _build_greenfield_legacy_timeline_events(*, allow_fake_pids: bool) -> list[dict[str, Any]]:
    """Legacy timeline events для greenfield-village-100-realistic-v2."""

    if not allow_fake_pids:
        _die(
            "Legacy patch timeline uses add_participant + synthetic PIDs. "
            "Re-run with --allow_fake_pids (or use 'generate' mode which uses only real scenario participants)."
        )

    new_pid = _new_fake_pid

    # ─────────────────────────────────────────────────────────────────────────
    # Greenfield Village 100 — events
    # ─────────────────────────────────────────────────────────────────────────
    return [
    # ── Phase 0: launch ──
    {
        "time": 0,
        "type": "note",
        "description": "Network launched: 100 participants, UAH equivalent",
    },
    # ── Phase 1: first market day ──
    {
        "time": 25000,
        "type": "stress",
        "description": "Farmers' market day — households buy from producers/retail",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "group:households", "value": 2.0},
            {"op": "mult", "field": "tx_rate", "scope": "group:retail", "value": 1.5},
            {"op": "mult", "field": "tx_rate", "scope": "group:producers", "value": 1.3},
        ],
        "metadata": {"duration_ms": 8000},
    },
    # ── Phase 2: midweek lull ──
    {
        "time": 40000,
        "type": "stress",
        "description": "Midweek — only essential purchases",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.4},
        ],
        "metadata": {"duration_ms": 8000},
    },
    # ── Phase 3: Wave 2 — 5 new households ──
    {
        "time": 55000,
        "type": "inject",
        "description": "Wave 2: Five new families join the village network",
        "effects": [
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(101),
                    "name": "The Brown Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0036_3fcd1ce4", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "500"},
                    {"sponsor": "PID_U0061_b337ff2d", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "300"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(102),
                    "name": "The Wilson Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0036_3fcd1ce4", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "400"},
                    {"sponsor": "PID_U0001_9e3779b1", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "500"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(103),
                    "name": "The Murphy Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0039_1a7389f7", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "350"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(104),
                    "name": "The O'Brien Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0037_de049695", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "300"},
                    {"sponsor": "PID_U0004_78dde6c4", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "600"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(105),
                    "name": "The Kelly Family (Household)",
                    "type": "person",
                    "status": "active",
                    "groupId": "households",
                    "behaviorProfileId": "household",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0006_b54cda26", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "450"},
                    {"sponsor": "PID_U0040_b8ab03a8", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "250"},
                ],
            },
        ],
    },
    # ── Phase 4: new families earn trust ──
    {
        "time": 80000,
        "type": "inject",
        "description": "New families earn trust from local shops and co-op",
        "effects": [
            {"op": "create_trustline", "from": "PID_U0036_3fcd1ce4", "to": new_pid(101), "equivalent": "UAH", "limit": "600"},
            {"op": "create_trustline", "from": "PID_U0037_de049695", "to": new_pid(102), "equivalent": "UAH", "limit": "500"},
            {"op": "create_trustline", "from": "PID_U0001_9e3779b1", "to": new_pid(103), "equivalent": "UAH", "limit": "400"},
            {"op": "create_trustline", "from": "PID_U0039_1a7389f7", "to": new_pid(104), "equivalent": "UAH", "limit": "350"},
            {"op": "create_trustline", "from": "PID_U0036_3fcd1ce4", "to": new_pid(105), "equivalent": "UAH", "limit": "450"},
        ],
    },
    # ── Phase 5: harvest season surge ──
    {
        "time": 100000,
        "type": "stress",
        "description": "Harvest season — producers flood market, retail busy",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "group:producers", "value": 2.5},
            {"op": "mult", "field": "tx_rate", "scope": "group:retail", "value": 2.0},
            {"op": "mult", "field": "tx_rate", "scope": "group:households", "value": 1.5},
        ],
        "metadata": {"duration_ms": 15000},
    },
    # ── Phase 6: Wave 3 — 3 new producers ──
    {
        "time": 130000,
        "type": "inject",
        "description": "Wave 3: Three new producers join (farmers who saw the co-op works)",
        "effects": [
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(106),
                    "name": "Oksana Vynohrad (Grape Farmer)",
                    "type": "person",
                    "status": "active",
                    "groupId": "producers",
                    "behaviorProfileId": "producer",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0001_9e3779b1", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "2000"},
                    {"sponsor": "PID_U0003_daa66d13", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1500"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(107),
                    "name": "Petro Yabluko (Apple Orchard)",
                    "type": "person",
                    "status": "active",
                    "groupId": "producers",
                    "behaviorProfileId": "producer",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0001_9e3779b1", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1800"},
                    {"sponsor": "PID_U0002_3c6ef362", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1000"},
                ],
            },
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(108),
                    "name": "Halyna Kvitka (Flower & Herb Garden)",
                    "type": "person",
                    "status": "active",
                    "groupId": "producers",
                    "behaviorProfileId": "producer",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0004_78dde6c4", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1200"},
                ],
            },
        ],
    },
    # ── Phase 7: new service provider ──
    {
        "time": 160000,
        "type": "inject",
        "description": "Wave 4: New veterinarian joins (needed for livestock farmers)",
        "effects": [
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(109),
                    "name": "Dr. Yaroslav Veter (Veterinarian)",
                    "type": "person",
                    "status": "active",
                    "groupId": "services",
                    "behaviorProfileId": "service",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0001_9e3779b1", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "2500"},
                    {"sponsor": "PID_U0007_538453d7", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1500"},
                    {"sponsor": "PID_U0015_4540215f", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "1000"},
                ],
            },
        ],
    },
    # ── Phase 8: winter preparation ──
    {
        "time": 200000,
        "type": "stress",
        "description": "Winter preparation — stocking up, preserving, repairing",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "group:services", "value": 2.0},
            {"op": "mult", "field": "tx_rate", "scope": "group:producers", "value": 0.5},
        ],
        "metadata": {"duration_ms": 15000},
    },
    # ── Phase 9: one household leaves, one business closes ──
    {
        "time": 250000,
        "type": "inject",
        "description": "The Walker family moves out; HomeGoods Mini-Mart closes",
        "effects": [
            {"op": "freeze_participant", "participant_id": "PID_U0081_0f8d8101", "freeze_trustlines": True},
            {"op": "freeze_participant", "participant_id": "PID_U0041_56e27d59", "freeze_trustlines": True},
        ],
    },
    # ── Phase 10: Wave 5 — new retail replaces closed one ──
    {
        "time": 280000,
        "type": "inject",
        "description": "Wave 5: New mini-market opens (replacing HomeGoods)",
        "effects": [
            {
                "op": "add_participant",
                "participant": {
                    "id": new_pid(110),
                    "name": "Village Essentials Shop",
                    "type": "business",
                    "status": "active",
                    "groupId": "retail",
                    "behaviorProfileId": "retail",
                },
                "initial_trustlines": [
                    {"sponsor": "PID_U0001_9e3779b1", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "5000"},
                    {"sponsor": "PID_U0003_daa66d13", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "3000"},
                    {"sponsor": "PID_U0036_3fcd1ce4", "direction": "sponsor_credits_new", "equivalent": "UAH", "limit": "2000"},
                ],
            },
        ],
    },
    # ── Phase 11: spring festival ──
    {
        "time": 320000,
        "type": "stress",
        "description": "Spring planting festival — community celebration and seed exchange",
        "effects": [
            {"op": "mult", "field": "tx_rate", "scope": "all", "value": 2.5},
        ],
        "metadata": {"duration_ms": 6000},
    },
    ]


def patch_scenario(path: Path, events: list[dict[str, Any]]) -> None:
    data = _load_json(path)
    if not isinstance(data, dict):
        _die(f"Scenario must be a JSON object: {path}")
    data["events"] = events
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  ✅ {path.relative_to(ROOT)}  events={len(events)}")


def _legacy_patch_fixtures(*, allow_fake_pids: bool = True) -> None:
    """Оставляем прежнее поведение: патчим два фиксированных scenario.json."""

    print("Generating scenario events...\n")
    riverside_events = _build_riverside_legacy_timeline_events(allow_fake_pids=allow_fake_pids)
    greenfield_events = _build_greenfield_legacy_timeline_events(allow_fake_pids=allow_fake_pids)

    patch_scenario(
        ROOT / "fixtures" / "simulator" / "riverside-town-50-realistic-v2" / "scenario.json",
        riverside_events,
    )
    patch_scenario(
        ROOT / "fixtures" / "simulator" / "greenfield-village-100-realistic-v2" / "scenario.json",
        greenfield_events,
    )
    print("\nDone. New event types used:")
    all_types = set()
    all_ops = set()
    for evts in [riverside_events, greenfield_events]:
        for e in evts:
            all_types.add(e["type"])
            for eff in e.get("effects", []):
                all_ops.add(eff.get("op", ""))
    print(f"  Event types: {sorted(all_types)}")
    print(f"  Effect ops:  {sorted(all_ops - {''})}")
    print("\n⚠️  Ops 'add_participant', 'create_trustline', 'freeze_participant'")
    print("   are NOT yet supported by runtime. See plans/phase3-network-growth-spec.md")


def _deterministic_evt_id(*, seed: int, index: int, scenario_id: str | None) -> str:
    # uuid5 -> стабильный, компактный, не зависит от runtime.
    base = f"scenario={scenario_id or 'unknown'}|seed={seed}|i={index}"
    u = uuid.uuid5(uuid.NAMESPACE_URL, base)
    return "evt_" + u.hex[:16]


def _pick_two_distinct(rng: random.Random, items: list[str]) -> tuple[str, str]:
    if len(items) < 2:
        _die("Need at least 2 participants")
    a = rng.randrange(len(items))
    b = rng.randrange(len(items) - 1)
    if b >= a:
        b += 1
    return items[a], items[b]


def _generate_tx_updated_events(
    *,
    scenario: ScenarioInfo,
    equivalent: str,
    seed: int,
    start_ts_ms: int,
    count: int,
    interval_ms: int,
    amount_min: int,
    amount_max: int,
) -> list[dict[str, Any]]:
    if count <= 0:
        _die(f"--count must be > 0, got: {count}")
    if interval_ms <= 0:
        _die(f"--interval_ms must be > 0, got: {interval_ms}")
    if amount_min <= 0 or amount_max <= 0 or amount_min > amount_max:
        _die(f"Invalid amount range: --amount_min={amount_min}, --amount_max={amount_max}")

    rng = random.Random(seed)
    pids = scenario.participant_ids
    out: list[dict[str, Any]] = []
    for i in range(count):
        frm, to = _pick_two_distinct(rng, pids)
        amount_int = rng.randint(amount_min, amount_max)
        ts_ms = start_ts_ms + i * interval_ms
        evt = {
            "type": "tx.updated",
            "event_id": _deterministic_evt_id(seed=seed, index=i, scenario_id=scenario.scenario_id),
            "ts": _iso_z_from_ms(ts_ms),
            "equivalent": equivalent,
            "from": frm,
            "to": to,
            "amount": str(amount_int),
            "edges": [],
        }
        out.append(evt)
    return out


def _format_events(
    events: list[dict[str, Any]],
    *,
    fmt: Literal["ndjson", "json"],
    pretty: bool,
) -> str:
    if fmt == "ndjson":
        # sort_keys=True важен для детерминированных диффов.
        return "".join(
            json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for e in events
        )

    if fmt == "json":
        if pretty:
            return json.dumps(events, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        return json.dumps(events, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"

    _die(f"Unsupported format: {fmt}")


def _cmd_generate(args: argparse.Namespace) -> None:
    scenario_path = _as_path(args.scenario)
    out_path = _as_path(args.out)
    scenario = _read_scenario_info(scenario_path)

    eq = args.equivalent
    if eq is None:
        if scenario.equivalents:
            eq = scenario.equivalents[0]
        else:
            _die("Scenario has no equivalents; pass --equivalent")
    if not isinstance(eq, str) or not eq:
        _die("--equivalent must be a non-empty string")
    if scenario.equivalents and eq not in scenario.equivalents:
        _die(f"--equivalent={eq} not present in scenario.equivalents={scenario.equivalents}")

    seed = int(args.seed)
    interval_ms = int(args.interval_ms)
    start_ts_ms = _parse_start_ts_to_ms(args.start_ts)

    # count/duration
    if args.count is not None and args.duration is not None:
        _die("Use either --count or --duration (mutually exclusive)")
    if args.count is None and args.duration is None:
        _die("Missing required parameter: --count or --duration")

    if args.count is not None:
        count = int(args.count)
    else:
        duration_ms = _parse_duration_ms(args.duration)
        count = duration_ms // interval_ms
        if count <= 0:
            _die(
                f"Duration too small for given interval: duration_ms={duration_ms}, interval_ms={interval_ms}. "
                "Increase --duration or decrease --interval_ms"
            )

    events = _generate_tx_updated_events(
        scenario=scenario,
        equivalent=eq,
        seed=seed,
        start_ts_ms=start_ts_ms,
        count=count,
        interval_ms=interval_ms,
        amount_min=int(args.amount_min),
        amount_max=int(args.amount_max),
    )

    fmt: Literal["ndjson", "json"] = args.format
    text = _format_events(events, fmt=fmt, pretty=bool(args.pretty))
    _write_text(out_path, text)

    rel_out = out_path
    try:
        rel_out = out_path.resolve().relative_to(ROOT.resolve())
    except Exception:
        pass

    print(
        "Generated events:\n"
        f"  scenario:   {scenario_path}\n"
        f"  out:        {rel_out}\n"
        f"  format:     {fmt}\n"
        f"  seed:       {seed}\n"
        f"  equivalent: {eq}\n"
        f"  start_ts:   {_iso_z_from_ms(start_ts_ms)}\n"
        f"  interval:   {interval_ms} ms\n"
        f"  count:      {len(events)}\n"
    )


def _cmd_patch_fixtures(args: argparse.Namespace) -> None:
    allow_fake_pids = not bool(getattr(args, "no_fake_pids", False))
    _legacy_patch_fixtures(allow_fake_pids=allow_fake_pids)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate_scenario_events.py",
        description="Scenario timeline patcher (legacy) and reproducible NDJSON/JSON events generator.",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # --- generate ---
    g = sub.add_parser(
        "generate",
        help="Generate reproducible NDJSON/JSON events from a scenario (real PIDs only).",
    )
    g.add_argument("--scenario", required=True, help="Input scenario JSON file")
    g.add_argument("--out", required=True, help="Output events file (NDJSON/JSON)")
    g.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    g.add_argument(
        "--start_ts",
        default="2026-01-01T00:00:00Z",
        help="Start timestamp: ISO8601Z or epoch ms (default: 2026-01-01T00:00:00Z)",
    )
    g.add_argument(
        "--equivalent",
        default=None,
        help="Equivalent code (default: first from scenario.equivalents)",
    )
    g.add_argument("--count", type=int, default=None, help="Number of events")
    g.add_argument(
        "--duration",
        default=None,
        help="Total duration window. Examples: 30000 (ms), 30s, 2m. Requires --interval_ms.",
    )
    g.add_argument(
        "--interval_ms",
        type=int,
        default=250,
        help="Interval between events (ms). Used with --count and --duration (default: 250)",
    )
    g.add_argument("--amount_min", type=int, default=1, help="Min amount (int, default: 1)")
    g.add_argument("--amount_max", type=int, default=1000, help="Max amount (int, default: 1000)")
    g.add_argument(
        "--format",
        choices=["ndjson", "json"],
        default="ndjson",
        help="Output format (default: ndjson)",
    )
    g.add_argument("--pretty", action="store_true", help="Pretty-print JSON (only for --format json)")
    g.set_defaults(func=_cmd_generate)

    # --- patch-fixtures ---
    f = sub.add_parser(
        "patch-fixtures",
        help="Legacy mode: patch two fixture scenario.json files in-place.",
    )
    f.add_argument(
        "--no_fake_pids",
        action="store_true",
        help="Disallow synthetic PIDs in legacy onboarding waves (will fail because legacy timeline uses add_participant).",
    )
    f.set_defaults(func=_cmd_patch_fixtures)

    return p


def main() -> None:
    # Backward compatible default: без аргументов выполняем legacy patch.
    if len(sys.argv) == 1:
        _legacy_patch_fixtures(allow_fake_pids=True)
        return

    parser = _build_arg_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except ScenarioEventsGeneratorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
