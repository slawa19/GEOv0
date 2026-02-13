# Test Plan — Simulator Backend + Simulator UI (Real Mode)

Дата: **2026-01-28**

Цель: описать **структуру тестов** (unit/integration/contract/e2e/perf), минимальные smoke-сценарии и правила запуска, чтобы выполнение критериев из `acceptance-criteria.md` было проверяемым и воспроизводимым.

Source of truth:
- `docs/ru/simulator/backend/acceptance-criteria.md`
- `api/openapi.yaml`
- `docs/ru/simulator/backend/ws-protocol.md`
- `docs/ru/simulator/frontend/docs/api.md`
- `fixtures/simulator/scenario.schema.json`
- `fixtures/simulator/*/scenario.json`

---

## 1) Область тестирования

Мы тестируем Real Mode по цепочке:
- Control plane (scenarios/runs): создание/управление прогоном.
- Stream (SSE): получение событий `SimulatorEvent`, обязательный `run_status`, keep-alive.
- Snapshots/metrics/bottlenecks/artifacts (если подключены UI): контракты + базовая функциональность.
- Интеграцию с PaymentEngine / GEO Core API (на уровне интеграционных тестов).

Мы **не** тестируем здесь визуальные эффекты/рендер-пиксели графа как продуктовый UX (это отдельные UI snapshot tests).

---

## 2) Среды и предусловия

### 2.1 Python/pytest
- Pytest конфигурация: `pytest.ini`.
- Базовые фикстуры клиента/БД: `tests/conftest.py`.

Важно про БД в тестах:
- По умолчанию `TEST_DATABASE_URL` указывает на SQLite (`.pytest_geov0.db`).
- Для **не-SQLite** тестовых БД потребуется `GEO_TEST_ALLOW_DB_RESET=1` (guardrail в `tests/conftest.py`).

### 2.2 UI e2e (Playwright)
- Simulator UI demo tests уже существуют в `simulator-ui/v2/e2e` и запускаются через `npm --prefix simulator-ui/v2 run test:e2e`.

Для Real Mode e2e предлагается добавить **отдельный набор тестов** (см. раздел 6) с возможностью `skip` по env-флагу.

---

## 3) Пирамида тестов (рекомендованная структура)

### 3.1 Unit (быстро, без внешних сервисов)
Цель: проверить детерминизм, бизнес-правила, сериализацию событий.

Покрывает:
- SB-08, SB-NF-04 (seed/PRNG/порядок обхода)
- частично SB-10 (маппинг ошибок в `tx.failed`)

Отдельный инвариант (важно для UI/визуализации):
- В Real Mode планировщик обязан выдавать действия с непрерывным `seq` внутри тика (`0..N-1`), иначе ordered-эмиттер может «залипнуть» и не выпускать `tx.updated` (возможен эффект: в БД есть COMMITTED платежи, но в SSE/`events.ndjson` нет доменных событий).

Расположение (фактически в репозитории сейчас):
- `tests/unit/test_simulator_real_planner_determinism.py`
- `tests/unit/test_simulator_sse_replay.py`
- `tests/unit/test_simulator_tx_failed_event_schema.py`
- `tests/unit/test_simulator_rejection_codes.py`
- `tests/unit/test_simulator_fixtures_clearing_plan_done_pair.py`
- `tests/unit/test_simulator_adaptive_clearing_policy.py` — unit tests for AdaptiveClearingPolicy (activation, hysteresis, backoff, guardrails, budget scaling, cold start, per-eq independence)
- `tests/unit/test_simulator_adaptive_clearing_effectiveness_synthetic.py` — deterministic effectiveness tests (reaction time, no jitter, backoff growth, budget clamping, warmup behaviour, budget scaling)

### 3.2 Contract (контракты схем/типов)
Цель: ловить дрейф контрактов без запуска сервисов.

Покрывает:
- SB-01 (валидаторы сценариев)
- соответствие примеров/полей в OpenAPI

Расположение: см. `tests/contract/` и `tests/unit/` (часть контрактов сейчас живёт как unit, например schema для `tx.failed`).

Минимум:
- позитивная проверка `fixtures/simulator/*/scenario.json` против `fixtures/simulator/scenario.schema.json`;
- негативные кейсы (обязательные поля/enum), если есть.

### 3.3 Integration (FastAPI + DB + (опц.) внешние сервисы)
Цель: проверить реальные endpoints и переходы состояний run.

Покрывает:
- SB-02..SB-07
- частично SB-09/SB-10 (если поднимаем тестовую среду PaymentEngine)

Расположение (фактически в репозитории сейчас):
- `tests/integration/test_simulator_sse_smoke.py`
- `tests/integration/test_simulator_sse_real_smoke.py`
- `tests/integration/test_simulator_sse_strict_replay_410.py`
- `tests/integration/test_simulator_sse_tx_failed_timeout.py`
- `tests/integration/test_simulator_sse_fixtures_clearing_animation_pair.py`
- `tests/integration/test_simulator_artifacts_events_ndjson.py`
- `tests/integration/test_simulator_real_snapshot_db_enrichment.py`
- `tests/integration/test_simulator_adaptive_clearing_integration.py` — spy-based integration tests for adaptive clearing coordinator (adaptive activation, static unchanged, cooldown)
- `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py` — A/B benchmark: static vs adaptive, non-flaky invariants (errors, committed_rate, no_capacity_rate). Marked `@pytest.mark.slow`.

Примечание по SSE тестам:
- для MVP достаточно проверить, что stream отдаёт:
  - корректный `Content-Type: text/event-stream`
  - минимум 1 `run_status` в течение 2 секунд после старта
  - keep-alive комментарии (best-effort)
  - корректное завершение/закрытие при `stop`.

Важно: под pytest in-process ASGI transport SSE stream **намеренно завершается рано** ("первый кадр"), чтобы тесты не зависали.
См. `ws-protocol.md` (раздел про pytest). Для проверки keep-alive/долгого стрима используйте реальный HTTP клиент против запущенного backend.

### 3.4 E2E (UI + backend)
Цель: подтвердить, что simulator-ui в `apiMode=real` способен управлять run и визуализировать поток (без падений).

Покрывает:
- SUI-01..SUI-06
- INT-01..INT-03

### 3.5 Performance/Load/Soak
Цель: SB-NF-01..03.

Расположение (предложение):
```
tests/performance/simulator/
  test_tick_latency.py
  test_sse_load.py
  test_soak_memory.py
```

---

## 4) Матрица покрытия (критерии → тесты)

Минимальная целевая матрица:
- SB-01 → contract + unit
- SB-02/03 → integration
- SB-04/05/06 → integration + e2e
- SB-07 → integration (SSE) + e2e
- SB-08 → unit
- SB-09/10 → integration
- SB-NF-01..03 → performance/load/soak
- SB-NF-04 → unit
- SUI-* → e2e (Playwright) + code review (для запрета локального вычисления `viz_*`)
- INT-* → e2e

---

## 5) Smoke сценарии (backend-level)

### 5.1 Smoke: старт → run_status → события

Цель: быстро проверить «живость» системы без UI.

Псевдосценарий (REST+SSE):
1) `POST /api/v1/simulator/scenarios` с телом `fixtures/simulator/greenfield-village-100/scenario.json` → получить `scenario_id`.
2) `POST /api/v1/simulator/runs` → получить `run_id`.
3) Подключиться к `GET /api/v1/simulator/runs/{run_id}/events?equivalent=UAH`.
4) В течение 2 секунд получить минимум 1 событие `type=run_status` со `state=running`.
5) В течение 30 секунд получить минимум 1 доменное событие из набора (в зависимости от MVP runner): `tx.updated` или `clearing.plan`.

Критерии успеха:
- stream не падает, события парсятся как JSON (в строке `data:`)
- `run_status` приходит периодически (1–2 сек), см. `ws-protocol.md`.

### 5.2 Smoke: pause/resume/stop

1) `POST /.../pause` → `run_status.state=paused` (и/или ответ команды содержит state).
2) Во время `paused` **не приходят доменные события** (кроме `run_status` и keep-alive).
3) `POST /.../resume` → `run_status.state=running`.
4) `POST /.../stop` → `run_status.state=stopped` (или `error` при ошибке), stream завершается.

### 5.3 Smoke: realistic-v2 суммы + артефакты (task-driven)

Цель: подтвердить, что realistic-v2 действительно даёт «сотни UAH», артефакты формируются, а потолок суммы соблюдается.

Рекомендуемый запуск (VS Code Tasks):
1) Запустить/перезапустить стек с реалистичным капом:
  - `Full Stack: restart (cap=500)`
  - (опционально, с очисткой DB) `Full Stack: start with DB reset (greenfield, cap=500)`
2) Запустить smoke-run + анализ:
  - `Simulator: run realistic-v2 smoke + analyze`

Что делает smoke-run:
- создаёт run через control plane (`/simulator/runs`), ждёт `run_seconds`, затем `stop`;
- скачивает артефакты через API в `.local-run/analysis/<run_id>/`;
- читает `geov0.db` и печатает распределение `PAYMENT.amount` за окно run (started_at..stopped_at).

Критерии успеха (минимум):
- в выводе есть `payments.over_3 > 0` при `SIMULATOR_REAL_AMOUNT_CAP>=500` (не «залипло» на legacy 1–3);
- `payments.over_500 == 0` (потолок соблюдается);
- `artifacts` содержит как минимум `events.ndjson`, `summary.json`, `status.json`, `last_tick.json`.

Где смотреть артефакты:
- скачанные для анализа: `.local-run/analysis/<run_id>/events.ndjson` и соседние файлы;
- оригинальные runtime-артефакты: `.local-run/simulator/runs/<run_id>/artifacts/`.

---

## 6) Smoke сценарии (UI-level, Playwright)

### 6.1 E2E: real-mode control + stream

Gherkin (целевое поведение):
```gherkin
Feature: Simulator UI Real Mode

  Scenario: UI запускает run и получает события
    Given Backend доступен на http://127.0.0.1:18000
    And UI открыта в режиме apiMode=real
    And Выбран сценарий greenfield-village-100
    When Пользователь нажимает Start
    Then UI получает run_status: running в течение 2 секунд
    And UI получает хотя бы 1 доменное событие в течение 30 секунд

  Scenario: Pause/Resume работает без дрейфа
    Given Запущен run
    When Пользователь нажимает Pause
    Then UI показывает статус paused
    And доменные события прекращаются (кроме run_status)
    When Пользователь нажимает Resume
    Then UI показывает статус running

  Scenario: Stop завершает прогон
    Given Запущен run
    When Пользователь нажимает Stop
    Then UI показывает stopped и прекращает сессию
```

Реализация (предложение):
- добавить файл `simulator-ui/v2/e2e/real-mode.spec.ts`.
- по умолчанию **skip**, пока не будет реализован real mode:
  - `test.skip(process.env.GEO_E2E_REAL_MODE !== '1', '...')`.
- запускать при поднятом backend (`scripts/run_local.ps1 start`) и доступном порту UI.

---

## 7) Правила запуска и флаги

### 7.1 Pytest
- Базовый запуск: `pytest`.
- Для тестов, требующих внешние сервисы, использовать маркер `@pytest.mark.e2e` (см. `pytest.ini`).

Окружение (важно):
- `TEST_DATABASE_URL=...` (SQLite по умолчанию)
- `GEO_TEST_ALLOW_DB_RESET=1` (только для dedicated test DB, если не SQLite)

### 7.2 Playwright (simulator-ui)
- Unit: `npm --prefix simulator-ui/v2 run test:unit`
- E2E fixtures scenes: `npm --prefix simulator-ui/v2 run test:e2e`
- E2E real mode (когда появится): `GEO_E2E_REAL_MODE=1 npm --prefix simulator-ui/v2 run test:e2e`

Рекомендация по быстрым прогонам:
- Для «быстрых проверок» UI (smoke/e2e без зависимости от БД и данных) используйте **sandbox/topology-only**:
  - API: `mode=fixtures` (в UI может отображаться как `sandbox`)
  - ожидания: топология/рендер/стрим/контроль run; без «семантической» раскраски по долгам/балансам.
- Для проверки DB enrichment и бизнес-семантики визуализации используйте `mode=real`.

### 7.3 Real Mode env knobs (важно для воспроизводимости)

- `SIMULATOR_REAL_AMOUNT_CAP` — верхняя граница суммы в real mode (default `3.00` для совместимости). Для realistic-v2 используйте `>=500`.
- `SIMULATOR_REAL_ENABLE_INJECT` — включает warmup/inject (по умолчанию `0`/выключено, включать только явно).

Примечание: в репозитории есть задачи VS Code, которые выставляют эти env на время запуска стека.

---

## 8) Definition of Done для C2

Документ считается готовым, если:
- описана пирамида тестов и структура директорий;
- есть минимум 1 backend smoke сценарий и 1 UI e2e сценарий (описательно), привязанные к `ws-protocol.md` и `acceptance-criteria.md`;
- описаны ключевые env-флаги/guardrails для БД.
