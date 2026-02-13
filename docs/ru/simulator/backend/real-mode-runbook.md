# Real Mode Runbook (Monorepo): как поднять окружение симулятора

Дата: 2026-01-28

Цель этого runbook — чтобы новый разработчик мог **быстро поднять backend + UI + зависимости** и начать проверять контракты Real Mode (scenarios/runs/events/snapshots/metrics).

Важно: в репозитории уже есть удобные скрипты запуска для Windows. Для «настоящей» семантики конкурентности/блокировок (payments/2PC) нужен Postgres (Docker Compose или локальный Postgres), а не SQLite.

---

## 0) Что именно мы поднимаем

Минимальный набор для разработки:

- Backend (FastAPI, GEO Hub)
- Admin UI (Vue/Vite) — для админских экранов
- Simulator UI (Vite, `simulator-ui/v2`) — прототип интерфейса симулятора

Опционально:

- Postgres + Redis (через Docker Compose) — чтобы тестировать real‑semantics (таймауты, локи, конкурентность)

---

## 1) Быстрый старт (Windows, PowerShell): Backend + Admin UI

Рекомендованный путь: репо‑раннер скрипт.

Команда из корня репозитория:

- `./scripts/run_local.ps1 start`

Что делает `scripts/run_local.ps1`:

- Стартует backend на `http://127.0.0.1:18000` (по умолчанию)
- Стартует Admin UI на `http://localhost:5173/` (по умолчанию)
- Пишет `admin-ui/.env.local` так, чтобы UI работал в **real mode**:
  - `VITE_API_MODE=real`
  - `VITE_API_BASE_URL=http://127.0.0.1:<backendPort>`
- Держит состояние в `.local-run/` (PID, логи)

Полезные команды:

- `./scripts/run_local.ps1 status`
- `./scripts/run_local.ps1 stop`
- `./scripts/run_local.ps1 restart-backend -ReloadBackend` (hot reload)

Проверка, что backend жив:

- `GET http://127.0.0.1:18000/api/v1/health`
- Swagger: `http://127.0.0.1:18000/docs`

См. также (ops-навигация):

- `observability.md` — `/metrics`, request_id, метрики runner vs Prometheus
- `run-storage.md` — где хранится run state/metrics/bottlenecks и как устроены artifacts (export)

---

## 2) Данные для локальной разработки (SQLite) — «fixtures mode»

`scripts/run_local.ps1` по умолчанию использует локальную SQLite базу `geov0.db`.

Если базы нет — она создаётся и засеивается.

Чтобы **пересоздать** SQLite DB и наполнить данными из канонических admin‑fixtures:

- Greenfield (100):
  - `./scripts/run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100 -RegenerateFixtures`
- Riverside (50):
  - `./scripts/run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity riverside-town-50 -RegenerateFixtures`

Быстрая проверка целостности SQLite:

- `./scripts/run_local.ps1 check-db`

Примечание:

- Если скрипт предупреждает про «tiny test seed» — пересоздайте базу с Greenfield/Riverside командами выше.

---

## 3) Запуск Simulator UI (Windows)

Simulator UI v2 — отдельный Vite‑прототип.

Из корня репозитория:

- `./scripts/run_simulator_ui.ps1`

По умолчанию:

- порт: 5176
- host: 127.0.0.1

Скрипт аккуратно обходит «зарезервированные» Windows порты (excludedportrange) и подбирает альтернативный, если надо.

---

## 4) Real Mode семантика: Postgres + Redis через Docker Compose

SQLite удобен для UI/демо, но **не подходит** для проверки реальных свойств (изоляция транзакций, advisory locks, конкурентность prepare/commit).

Для более «реального» окружения поднимайте сервисы из `docker-compose.yml`:

- Postgres 16
- Redis 7
- app (FastAPI)

Базовый запуск:

- `docker compose up -d --build`

Dev‑override (uvicorn `--reload`, volumes, debug):

- `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`

Порты по умолчанию:

- API: `http://localhost:8000`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

Можно переопределять host‑порты переменными окружения:

- `GEO_API_PORT` (по умолчанию 8000)
- `GEO_DB_PORT` (по умолчанию 5432)
- `GEO_REDIS_PORT` (по умолчанию 6379)

Проверки:

- `http://localhost:8000/health` (или `http://localhost:8000/api/v1/health`) и `http://localhost:8000/docs`
- `http://localhost:8000/metrics` (если `METRICS_ENABLED=true`)

Если нужна подробная инструкция для WSL2 без Docker Desktop — см. `docs/ru/runbook-dev-wsl2-docker-no-desktop.md`.

---

## 5) Как понять, что Real Mode симулятора работает

### 5.1 Базовые признаки «ок»

- Backend отвечает на `GET /api/v1/health`
- Swagger UI открывается (`/docs`)
- Admin UI в real mode (в `admin-ui/.env.local` выставлены `VITE_API_MODE=real` и `VITE_API_BASE_URL=...`)

### 5.2 Симуляторный control plane (когда endpoints реализованы)

Ориентир по контракту — `api/openapi.yaml` и документы протокола.

Ключевые группы:

- Scenarios: `GET/POST /api/v1/simulator/scenarios`
- Runs: `POST /api/v1/simulator/runs` и `GET /api/v1/simulator/runs/{run_id}`
- Stream: `GET /api/v1/simulator/runs/{run_id}/events` (SSE)
- Snapshots/Metrics/Bottlenecks/Artifacts: соответствующие endpoints `.../snapshot`, `.../metrics`, `.../bottlenecks`, `.../artifacts`

Смысловая проверка для UI:

- В stream регулярно приходит `run_status` (heartbeat во время `running`)
- UI не вычисляет смысл `viz_*` — backend присылает готовые `viz_*` поля

---

## 6) Troubleshooting

### 6.1 Порты заняты

- Для Backend/Admin UI используйте `-AutoPorts`:
  - `./scripts/run_local.ps1 start -AutoPorts`
- Simulator UI сам подберёт порт, если 5176 занят или «excluded».

### 6.2 Нет `.venv` или Python зависимостей

`scripts/run_local.ps1` ожидает Python в `.venv`.

- Создать venv: `py -m venv .venv`
- Активировать: `./.venv/Scripts/Activate.ps1`
- Установить deps: `pip install -r requirements.txt -r requirements-dev.txt`

### 6.3 Admin UI неожиданно в mock mode

Проверьте `admin-ui/.env.local`:

- Должно быть `VITE_API_MODE=real`
- Должно быть `VITE_API_BASE_URL=http://127.0.0.1:<порт backend>`

Проще всего: запускать через `./scripts/run_local.ps1 start`.

### 6.4 Docker окружение не стартует

- `docker compose ps`
- `docker compose logs -f app`
- Проверьте конфликты портов и healthcheck’и db/redis.

### 6.5 Симулятор: полезные env-параметры

SSE replay buffer (best-effort):
- `SIMULATOR_EVENT_BUFFER_SIZE` (по умолчанию 2000)
- `SIMULATOR_EVENT_BUFFER_TTL_SEC` (по умолчанию 600)
- `SIMULATOR_SSE_STRICT_REPLAY=1` — возвращать `HTTP 410`, если `Last-Event-ID` слишком старый

Real Mode guardrails:
- `SIMULATOR_REAL_MAX_IN_FLIGHT` (по умолчанию 1)
- `SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK` (по умолчанию 5)
- `SIMULATOR_REAL_MAX_ERRORS_TOTAL` (по умолчанию 200)
- `SIMULATOR_CLEARING_MAX_DEPTH` (по умолчанию 6)

Clearing policy:
- `SIMULATOR_CLEARING_POLICY=static|adaptive` (по умолчанию `static`)
  - `static` использует `SIMULATOR_CLEARING_EVERY_N_TICKS`
  - `adaptive` динамически подстраивает периодичность (см. `adaptive-clearing-policy.md`)

Adaptive clearing knobs (используются только при `SIMULATOR_CLEARING_POLICY=adaptive`):
- `SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS` (default: 30) — длина rolling window (в тиках) для расчёта `no_capacity_rate`.
- `SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH` (default: 0.60) — порог `no_capacity_rate` для включения клиринга.
- `SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW` (default: 0.30) — порог для выключения (hysteresis).
- `SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS` (default: 5) — минимальный cooldown между запусками клиринга для одного equivalent.
- `SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS` (default: 60) — потолок exponential backoff при zero yield.
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN` (default: 3) / `MAX` (default: 6) — диапазон `max_depth` per-eq.
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN` (default: 50) / `MAX` (default: 250) — диапазон `time_budget_ms` per-eq.
- `SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD` (default: 0, disabled) — если in-flight > threshold, клиринг откладывается.
- `SIMULATOR_CLEARING_ADAPTIVE_QUEUE_DEPTH_THRESHOLD` (default: 0, disabled) — если queue_depth > threshold, клиринг откладывается.

Cold-start: на первых `WINDOW_TICKS` тиках данные неполные. Если `warmup_fallback_cadence > 0` (по умолчанию = `CLEARING_EVERY_N_TICKS`), clearing запускается периодически с минимальным бюджетом. После заполнения окна policy переключается на полную адаптивную логику.

Real Mode: тюнинг дисковой нагрузки (артефакты) для dev/UI:
- `SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS` — как часто обновлять `last_tick.json` (по умолчанию `500`).
  - `<=0` отключает запись `last_tick.json` на каждом тике.
- `SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS` — как часто синхронизировать индекс артефактов в БД (по умолчанию `5000`).
  - `<=0` отключает `sync_artifacts` (уменьшает IO, но список артефактов может обновляться реже/только при finalize).

Где искать артефакты прогона локально:
- `.local-run/simulator/runs/<run_id>/artifacts/` (включая `events.ndjson`)

---

## 7) Примечание про UX артефактов (важно для Real Mode)

В браузере нельзя надёжно «открыть папку на диске».

Поэтому для артефактов прогона нужен один из вариантов:

- отдавать `download_url` / zip‑архивы на скачивание
- или показывать `artifact_path` как текст (копируемая строка), без попытки открыть папку
