# Real Mode Simulator — Debug Harness / Scenario Runner (спецификация разработки)

Дата: **2026-01-29**

## 0. Контекст и проблема

В real mode (симулятор гоняет операции через реальный backend/payment stack) мы наблюдаем большое число событий типа:

- `payment rejected`
- `fail internal error`

Это мешает понять:

1) **нормально ли** сценарий «должен» давать много reject (например, из-за лимитов),
2) или это **регрессия**/ошибка в интеграции (например, баг в платежах, таймаутах, идемпотентности),
3) как быстро локализовать первопричину, не превращая каждую отладку в ручной “тык в UI”.

Нужно сделать такой инструмент, который:

- воспроизводимо запускает сценарии;
- собирает наблюдаемость (события + метрики + ошибки);
- считает “успешность” прогона и даёт диагноз;
- позволяет **автоматически** повторять цикл: run → analyze → patch → re-run.

## 1. Цели

- Иметь **CLI runner** (одна команда), который может:
  - загрузить/выбрать сценарий,
  - стартовать run (real/fixtures),
  - подключиться к SSE,
  - дождаться критериев остановки,
  - собрать артефакты,
  - сделать summary/diagnostics.

- Иметь **авто-анализ** (без UI):
  - success rate;
  - распределение error codes;
  - классификация reject vs internal;
  - топ проблемных рёбер/узлов (если возможно);
  - детект типовых аномалий.

- Иметь «органичное включение» отладочного режима:
  - по флагу CLI,
  - по env vars,
  - через pytest smoke/regression.

## 2. Non-goals (не делаем в этой фазе)

- Полный replay/перемотка прогона.
- ML/ИИ интерпретация (можно позже).
- Построение графиков (UI уже есть; тут именно debug/CI инструмент).

## 3. Термины

- **Run** — запуск симуляции по `scenario_id` (имеет `run_id`).
- **SSE stream** — `GET /api/v1/simulator/runs/{run_id}/events?equivalent=...`.
- **Artifact pack** — директория с результатами: события, summary, статусы.
- **Success** — “tx.updated” без ошибки/отказа.
- **Reject** — контролируемый отказ (нет маршрута / лимит / правила).
- **Internal error** — 5xx/исключение/неожиданная ошибка (должно быть ≈ 0).

## 4. Источники данных (SoT)

- OpenAPI: `api/openapi.yaml`
- SSE семантика: `docs/ru/simulator/backend/ws-protocol.md`
- Frontend expectations: `docs/ru/simulator/frontend/docs/api.md`
- Сценарии/схема: `fixtures/simulator/*/scenario.json`, `fixtures/simulator/scenario.schema.json`

## 5. Предлагаемое решение

### 5.1. Новый CLI инструмент: `scripts/sim_debug_run.py`

**Назначение:** запустить run и собрать/проанализировать результаты.

**Требования к реализации:**

- Python 3.11+ (в проекте уже Python; избегаем Node для CLI).
- HTTP клиент: `httpx`.
- SSE: поддержка `text/event-stream` чтения построчно, с обработкой `id:` и `data:`.
- Авторизация: через `X-Admin-Token` (как в UI).

**Пример команд:**

- Запуск прогона и анализ:
  - `python scripts/sim_debug_run.py --api http://127.0.0.1:18000 --token dev-admin-token-change-me --scenario greenfield-village-100 --mode real --equivalent UAH --intensity 50 --duration-sec 30`

- Быстрый smoke (для CI/локально):
  - `python scripts/sim_debug_run.py --scenario minimal --mode fixtures --duration-sec 5 --expect-internal-errors 0`

**Остановка прогона (stop criteria):**

- по времени (`--duration-sec`),
- по числу событий (`--max-events`),
- по достижению min количества доменных событий (`--min-tx-events`),
- early abort, если internal errors > `--max-internal-errors`.

### 5.2. Формат артефактов

Путь (локально):

- Встроенные артефакты runtime (уже есть): `.local-run/simulator/runs/<run_id>/artifacts/`
  - включает `events.ndjson`, `status.json`, `last_tick.json` и др.
- Артефакты debug harness (если добавим отдельный CLI-слой): `.local-run/simulator/debug-runs/<run_id>/`

Содержимое:

- `params.json` — параметры запуска (scenario_id, mode, eq, intensity, api_base, started_at)
- `events.ndjson` — все SSE события (как пришли) + добавленные поля `received_at`, `seq`
- `summary.json` — агрегированные метрики и verdict
- `errors.ndjson` — нормализованные ошибки (если есть)

Опционально:

- `snapshot.json` — `GET /graph/snapshot` на старте (для стабильного baseline)
- `run_status.json` — финальный `GET /runs/{run_id}`

### 5.3. Авто‑анализ (verdict)

`summary.json` должен содержать:

- `counts`:
  - `run_status` events count
  - `tx_updated` count
  - `tx_failed` count (если есть)
  - `clearing_plan`, `clearing_done` counts
- `rates`:
  - `success_rate = tx_updated / (tx_updated + tx_failed)`
  - `internal_error_rate` (см. классификацию)
- `errors`:
  - `by_code` (top-N)
  - `by_http_status` (если из REST)
- `verdict`:
  - `PASS | WARN | FAIL`
  - `reasons[]`

**Классификация ошибок (первичная):**

- `REJECT`:
  - `NO_ROUTE`
  - `INSUFFICIENT_LIMIT`
  - `TIMEOUT_2PC` (если протокол так классифицирует)
  - прочие ожидаемые доменные отказы

- `INTERNAL`:
  - `INTERNAL_ERROR`
  - любые 5xx
  - исключения/traceback маркеры
  - “unknown code” (пока не классифицировано)

**Пороговые правила (по умолчанию, настраиваемые флагами):**

- `INTERNAL` должно быть `0` для PASS (или <=1 для WARN в flaky среде)
- `success_rate` для `greenfield-village-100` в fixtures‑mode должно быть >= 0.95
- `success_rate` для real‑mode может быть ниже, но FAIL если < 0.70 (консервативно)

Пороги должны быть **per scenario** (см. 5.4).

### 5.4. Baselines и регрессии

Добавить файл:

- `fixtures/simulator/baselines.json`

Структура:

- `scenario_id` → expected thresholds:
  - `min_success_rate`
  - `max_internal_errors`
  - `max_reject_rate` (опц.)
  - `notes` (человеческое объяснение)

Это позволит:

- стабилизировать “что считается нормой” для конкретного сценария,
- ловить регрессии в CI.

### 5.5. Органичное включение “при необходимости”

Чтобы инструмент был удобно использовать и человеком, и агентом (Copilot):

1) **Скрипт** всегда доступен локально: `python scripts/sim_debug_run.py ...`.
2) **PowerShell wrapper** (опционально): `scripts/run_local.ps1 -Action sim-debug ...`.
3) **Pytest integration**:
   - `tests/integration/test_sim_debug_minimal.py` — запускает короткий run в fixtures-mode и валидирует PASS.
   - отдельный `-m realmode` маркер для тяжелых real‑mode прогонов.

**Как агент будет использовать (операционный протокол):**

- Если в задаче появляется “много rejected/internal error”, агент:
  1) запускает `sim_debug_run.py` на minimal (fixtures), чтобы исключить базовые поломки;
  2) запускает на целевом сценарии (real) короткий run;
  3) анализирует `summary.json` и топ ошибок;
  4) вносит точечный фикс (обычно в раннере/PaymentService интеграции/идемпотентности);
  5) повторяет прогон, пока verdict не PASS/WARN по baseline.

## 6. Изменения в backend (для лучшей диагностики)

### 6.1. Нормализованный `tx.failed`

Событие `tx.failed` уже является частью OpenAPI union и используется для нормализованных ошибок/отказов платежей.
Проверки совместимости/качества payload:

- `error.code` (стабильный enum/строка)
- `error.message` (человекочитаемо)
- `from`, `to`, `amount`, `equivalent` (когда применимо)

Примечание:
- `amount` может отсутствовать (nullable/опционально) в зависимости от точки отказа; для диагностики это допустимо.

### 6.2. Correlation IDs

- добавлять `request_id` / `tx_id` в события, где возможно.
- писать `request_id` в backend лог (чтобы сопоставлять события и исключения).

## 7. Acceptance criteria

- CLI способен:
  - получить сценарии,
  - стартовать run,
  - подключиться к SSE,
  - собрать `events.ndjson` и `summary.json`.

- Для `fixtures/minimal` verdict = PASS.
- Для `fixtures/greenfield-village-100` verdict = PASS с success_rate >= baseline.
- Для real-mode прогонов verdict не FAIL из-за отсутствия данных (run_status heartbeat обязателен).

## 8. План реализации (итерации)

1) `scripts/sim_debug_run.py` (SSE client + artifacts + basic summary)
2) Baselines (`fixtures/simulator/baselines.json`) + пороги
3) Pytest smoke (fixtures minimal)
4) Улучшения классификации ошибок + correlation

---

## Appendix A: Рекомендуемые поля summary.json (пример)

```json
{
  "run_id": "run_...",
  "scenario_id": "greenfield-village-100",
  "mode": "real",
  "equivalent": "UAH",
  "intensity_percent": 50,
  "started_at": "2026-01-29T09:40:00Z",
  "finished_at": "2026-01-29T09:40:30Z",
  "counts": {
    "run_status": 31,
    "tx_updated": 120,
    "tx_failed": 15,
    "clearing_plan": 0,
    "clearing_done": 0
  },
  "rates": {
    "success_rate": 0.888,
    "reject_rate": 0.100,
    "internal_error_rate": 0.012
  },
  "errors": {
    "reject_by_code": {"NO_ROUTE": 8, "INSUFFICIENT_LIMIT": 7},
    "internal_by_code": {"INTERNAL_ERROR": 2}
  },
  "verdict": "WARN",
  "reasons": ["internal errors > 0"]
}
```
