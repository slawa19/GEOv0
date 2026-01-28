# Simulator UI (RU) — Real Mode screens & controls (addendum)

Этот документ фиксирует **где именно** в уже существующем Simulator UI (v2) находятся (или должны появиться) экраны/контролы, и какие данные/эндпоинты они требуют.

Статус: draft (SoT для "панелей управления" real mode).

## 0. Контекст: что уже есть в Simulator UI v2

Текущая архитектура v2 — это **один экран-карта** (canvas) + два слоя HUD:
- **Top HUD** (верхняя строка): глобальные селекторы/статусы (сейчас: `EQ`, `Layout`, `Scene`, counts).
- **Bottom HUD** (нижняя строка): минимальные кнопки демо/quality/labels.

Важно: мы **не вводим отдельный роутинг** для «экранов» Scenario/Run/Metrics — это будут **оверлеи/панели (tabs)** поверх карты.
Так мы сохраняем UX «игровой тактической карты»: карта всегда видна, панели — вспомогательные.

## 1. Информационная архитектура (IA)

### 1.1. Основной экран
- Всегда отображается: **граф сети** (узлы/рёбра) + FX overlay.

### 1.2. Control Panel (правый сайдбар с вкладками)
Добавляем единую панель управления (drawer/panel) с вкладками:
1) **Scenario**
2) **Run**
3) **Metrics**
4) **Network / Bottlenecks**
5) **Artifacts** (опционально)

Открытие панели:
- кнопка/пилюля в **Top HUD**: `Panel` или `Scenario`.

## 2. Экран/вкладка “Scenario”

### 2.1. Что делает пользователь
- Выбирает пресет сценария (из backend) **или** загружает `scenario.json`.
- Видит краткое резюме сценария.

### 2.2. UI элементы (минимум)
- `Preset selector` (список сценариев)
- `Upload scenario.json` (file input)
- `Summary card`:
  - `participants_count`
  - `trustlines_count`
  - `equivalents[]`
  - `clusters_count` (если есть)
  - `hubs_count` (если есть)

### 2.3. Источник данных
- См. расширения контракта в `docs/ru/simulator/frontend/docs/api.md`:
  - `GET /api/v1/simulator/scenarios`
  - `GET /api/v1/simulator/scenarios/{scenario_id}` (опционально)
  - `POST /api/v1/simulator/scenarios` (загрузка JSON)

### 2.4. Принцип семантики
- `clusters`/`hubs` должны приходить **как готовая семантика** (backend/tooling), а не вычисляться UI.
- Простые counts UI может посчитать, но для согласованности лучше получать `summary`.

## 3. Экран/вкладка “Run”

### 3.1. Что делает пользователь
- Стартует прогон по выбранному сценарию.
- Пауза/резюм/стоп/рестарт.
- Меняет интенсивность.

### 3.2. UI элементы (минимум)
- Кнопки: `Start`, `Pause/Resume`, `Stop`, `Restart`.
- Слайдер: `Intensity 0..100%`.
- Блок статуса:
  - `run_state` (idle/running/paused/stopping/stopped/error)
  - `sim_time` (tick или seconds)
  - `ops_sec`
  - `queue_depth` (если есть)
  - `errors_last_1m` (или `error_rate`)
  - `current_phase` / `last_event_type`

### 3.3. Где расположить
- Основные кнопки Run допустимо вынести в **Bottom HUD** (заменив demo-кнопки в real-mode),
  а подробный статус и интенсивность — оставить во вкладке Run.

### 3.4. Источник данных
- Команды управления run: REST или WS (см. backend roadmap A3/A4).
- Статус: либо отдельный endpoint polling, либо события `run.status` в stream.

## 4. Экран/вкладка “Metrics”

### 4.1. Что делает пользователь
- Смотрит time-series по прогону.

### 4.2. Графики (MVP 4–5)
- `success_rate`
- `avg_route_length`
- `total_debt` (или `total_used`)
- `clearing_volume`
- `top_bottlenecks_score` (или количество bottlenecks)

### 4.3. Источник данных
- UI **не вычисляет** эти метрики из raw событий.
- Метрики приходят готовыми сериями из backend:
  - `GET /api/v1/simulator/runs/{run_id}/metrics?...`

## 5. Экран/вкладка “Network / Bottlenecks”

### 5.1. Что делает пользователь
- Видит список Top bottlenecks.
- Кликает по bottleneck → UI подсвечивает ребро/узлы и фокусирует камеру.

### 5.2. UI элементы (минимум)
- `Top bottlenecks list` (N=10..50)
  - `edge (from,to)` или `node`
  - `score`
  - `reason_code`
  - `human_label` (опц.)
  - `suggested_action` (опц.)
- `Filter`: equivalent / reason.

### 5.3. Источник данных
- Список bottlenecks должен приходить из backend:
  - `GET /api/v1/simulator/runs/{run_id}/bottlenecks?equivalent=...`

## 6. Экран/вкладка “Artifacts” (опционально)

### 6.1. Важное ограничение (Web)
В браузере нельзя надёжно реализовать «Открыть папку run».
Поэтому MVP варианты:
- `Download artifacts` (zip или отдельные файлы)
- `Copy run_id` / `Copy artifact URL`
- (если это локальный dev инструмент) показывать `artifact_path` как текст.

### 6.2. UI элементы (минимум)
- `Artifacts index`:
  - `summary.json`
  - `events.ndjson`
  - `snapshots/*.json` (или чанки)
- Кнопки: `Download summary`, `Download events`, `Download bundle`.

### 6.3. Источник данных
- `GET /api/v1/simulator/runs/{run_id}/artifacts`
- `GET /api/v1/simulator/runs/{run_id}/artifacts/{name}`

## 7. Что нужно, чтобы не было пробелов

Чтобы эти экраны были однозначно реализуемы, нужно зафиксировать:
- Контракт управления run (REST vs WS, message types) — backend docs A3/A4.
- Типы `ScenarioSummary`, `RunStatus`, `MetricSeries`, `BottleneckItem`, `ArtifactIndex` — frontend `docs/api.md`.
- Политики детерминизма/test-mode (чтобы UI тестировался) — остаётся в существующих SoT спеках.
