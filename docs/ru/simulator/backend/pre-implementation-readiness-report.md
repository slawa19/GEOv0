# Pre-implementation readiness report (Real Mode Simulator)

Дата: **2026-01-28**

Цель: подтвердить, что перед началом реализации **Real Mode Simulator (backend + simulator-ui)** документация и артефакты **полны, согласованы и проверяемы**.

---

## Source of truth (что главнее при расхождениях)

1) **OpenAPI контракт**: `api/openapi.yaml`
2) **SSE/REST протокол (семантика событий)**: `docs/ru/simulator/backend/ws-protocol.md`
3) **Frontend expectations (как UI использует API)**: `docs/ru/simulator/frontend/docs/api.md`
4) **JSON Schema сценария**: `fixtures/simulator/scenario.schema.json`
5) **Семантика/алгоритм/приёмка**: `runner-algorithm.md`, `acceptance-criteria.md`, `test-plan.md`

---

## Выполненные проверки (фактические)

### 1) Валидность seed scenarios

- Проверено: все файлы `fixtures/simulator/*/scenario.json` проходят валидацию по `fixtures/simulator/scenario.schema.json`.
- Исправлено: дефект схемы для `trustline.limit` (пересечение `oneOf` для `integer` и `number` → заменено на `anyOf`), из‑за которого валидные целые значения ломали валидацию.

### 2) Согласованность RunState

- Приведено к единому enum `RunState` из OpenAPI: `idle | running | paused | stopping | stopped | error`.
- Убраны/заменены упоминания состояний, которых нет в OpenAPI (`completed`, `starting`).

### 3) Док-ссылки и пути

- Исправлены ссылки на несуществующие/неверные пути:
  - `generate_simulator_scenario.py` → `scripts/generate_simulator_seed_scenarios.py`
  - `realApi.ts` → `admin-ui/src/api/realApi.ts`
  - уточнены пути к `admin-fixtures/v1/datasets/*.json`
  - унифицированы ссылки на `scripts/run_local.ps1`

### 4) “Equivalent” в примерах

- В примерах документации по умолчанию используем `UAH` для читабельности.
- При этом `HOUR` и `KWH` являются валидными кодами и встречаются в seed данных (см. `seeds/equivalents.json`).

---

## Appendix: OpenAPI ↔ Protocol ↔ Frontend contract (сверка)

### A) Endpoints

| Capability | OpenAPI | Protocol | Frontend doc | Notes |
|---|---|---|---|---|
| List scenarios | `GET /api/v1/simulator/scenarios` | n/a | `docs/.../api.md` §1.1 Scenarios | Control-plane |
| Upload scenario | `POST /api/v1/simulator/scenarios` | n/a | `docs/.../api.md` §1.1 Scenarios | Body: `ScenarioUploadRequest` (`{scenario: {...}}`) |
| Get scenario summary | `GET /api/v1/simulator/scenarios/{scenario_id}` | n/a | `docs/.../api.md` (опц.) | |
| Start run | `POST /api/v1/simulator/runs` | n/a | `docs/.../api.md` §1.1 Runs | Req: `{scenario_id, mode, intensity_percent}` |
| Get run status | `GET /api/v1/simulator/runs/{run_id}` | Reconnect policy | `docs/.../api.md` §1.1 Runs | Returns `RunStatus` (`state` = `RunState`) |
| Pause/Resume/Stop/Restart | `POST /api/v1/simulator/runs/{run_id}/*` | REST commands | `docs/.../api.md` §1.1 Runs | Idempotent semantics закреплены в `ws-protocol.md` |
| Set intensity | `POST /api/v1/simulator/runs/{run_id}/intensity` | REST command | `docs/.../api.md` §1.1 Runs | Body: `{intensity_percent: 0..100}` |
| Events stream (SSE) | `GET /api/v1/simulator/runs/{run_id}/events?equivalent=...` | §1.1 SSE | `docs/.../api.md` §1.1 Live events | `equivalent` обязателен; `run_status` не должен пропускаться |
| Graph snapshot by run | `GET /api/v1/simulator/runs/{run_id}/graph/snapshot?equivalent=...` | Reconnect policy | `docs/.../api.md` §1.1 Snapshot | `equivalent` обязателен |
| Metrics time-series | `GET /api/v1/simulator/runs/{run_id}/metrics?...` | n/a | `docs/.../api.md` §Metrics | `from_ms`, `to_ms`, `step_ms` — required |
| Bottlenecks | `GET /api/v1/simulator/runs/{run_id}/bottlenecks?...` | n/a | `docs/.../api.md` §Bottlenecks | Optional `min_score` (0..1) + `limit` |
| Artifacts index/download | `GET /api/v1/simulator/runs/{run_id}/artifacts*` | n/a | `docs/.../api.md` §Artifacts | Download: `application/octet-stream` |

Примечание про legacy endpoints:
- OpenAPI также содержит legacy пути без `run_id`: `/api/v1/simulator/events`, `/api/v1/simulator/graph/snapshot`, `/api/v1/simulator/graph/ego`, `/api/v1/simulator/events/poll`.
- В документации UI они могут трактоваться как режим **"active run"** (MVP), но целевой Real Mode — это namespace по `run_id`.

### B) Event union / обязательные поля

| Event `type` | In `SimulatorEvent` (OpenAPI) | Required fields (OpenAPI) | Mentioned in protocol | Mentioned in frontend doc |
|---|---|---|---|---|
| `tx.updated` | ✅ | `event_id, ts, type, equivalent` | ✅ | ✅ |
| `clearing.plan` | ✅ | `event_id, ts, type, equivalent, plan_id, steps` | ✅ | ✅ |
| `clearing.done` | ✅ | `event_id, ts, type, equivalent` | ✅ | ✅ |
| `run_status` | ✅ | `event_id, ts, type, run_id, scenario_id, state` | ✅ (heartbeat) | ✅ (heartbeat) |

Критичный инвариант:
- `run_status` обязателен как heartbeat во время `running` (и как событие смены состояния).

---

## Что теперь можно начинать реализовывать без риска «дрейфа»

- Backend endpoints и схемы ответов/событий строго по `api/openapi.yaml`.
- Клиентскую реализацию (Admin UI) по `admin-ui/src/api/realApi.ts` + UI contracts.
- Runner/интеграцию по `runner-algorithm.md` и `payment-integration.md`.
- Генерацию/конвертацию сценариев по `fixtures-mapping.md` и `scripts/generate_simulator_seed_scenarios.py`.

---

## Рекомендуемые “первые проверки” перед первым PR с реализацией

1) Запуск локально:
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_local.ps1 -Action start`
2) Smoke по контрактам:
   - убедиться, что `/api/v1/docs` поднимается и содержит simulator endpoints
   - убедиться, что SSE endpoint отдаёт `run_status` как heartbeat во время `running`
3) Fixtures consistency:
   - `npm --prefix admin-ui run sync:fixtures`
   - `npm --prefix admin-ui run validate:fixtures`

---

## Остаточные риски (не блокеры)

- Дальнейшая детализация payload’ов и error taxonomy в процессе реализации может потребовать точечных уточнений в `ws-protocol.md` и `api-examples.md`.
- Нагрузочные/ретеншн параметры (TTL, размер ring buffer) уточняются по мере появления реальных потоков событий.

---

## Примечания по текущей реализации (обновлено: 2026-01-28)

Этот документ остаётся актуальным как «контрактный чек‑лист». Для удобства разработки фиксируем несколько практических уточнений, которые появились в ходе реализации MVP:

- **Tick-model в Real mode**: `sim_time_ms = tick_index * 1000`, а `intensity_percent` влияет на **budget действий на тик**, а не на скорость sim-time (см. `runner-algorithm.md`).
- **In-process SSE в тестах**: httpx ASGI transport может буферизовать stream, поэтому под pytest допускается **finite SSE** (минимум: `run_status` + 1 событие). Это не меняет внешний контракт; это только test-stability режим.
- **Real mode smoke**: есть отдельный integration smoke для real-mode SSE (помогает ловить регрессии в раннере и in-process payments).
- **Artifacts (локальный MVP)**: создаются минимальные артефакты (`events.ndjson`, `status.json`, обновляемый `last_tick.json`) в `.local-run/simulator/runs/<run_id>/artifacts` (best-effort).

---

## PR gate checklist (минимум перед каждым PR по Simulator)

Backend:
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe -m pytest -q tests/contract/test_openapi_contract.py`
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe -m pytest -q tests/integration/test_simulator_sse_smoke.py`
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe -m pytest -q` (полный прогон)

Admin UI fixtures (если менялись fixtures/генераторы):
- `npm --prefix admin-ui run sync:fixtures`
- `npm --prefix admin-ui run validate:fixtures`
