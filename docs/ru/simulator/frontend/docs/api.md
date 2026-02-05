# GEO Simulator API (MVP, черновик)

Этот документ — **источник правды** для контракта `/api/v1/simulator/*`.

Принципы (как в доработках графа Admin UI):
- Фронтенд **не вычисляет** семантику визуализации (цвет/размер/биннинг/приоритеты). Всё это приходит как `viz_*`.
- Клиент **не угадывает**, что подсветить: backend присылает готовые ключи и сценарии событий.
- Производительность важнее эффектов: по умолчанию мало текста, мало частиц, LOD по зуму/нагрузке.

---

## 1) Endpoints (MVP)

### Snapshot
- `GET /api/v1/simulator/graph/snapshot?equivalent=UAH`
  - Возвращает текущее состояние сети + готовые `viz_*` поля.

### Focus/Ego (опционально, но рекомендуется)
- `GET /api/v1/simulator/graph/ego?equivalent=UAH&pid=PID_...&depth=1`
  - Возвращает подграф вокруг узла (для режима Focus, чтобы не тянуть всё).

### Events
- `GET /api/v1/simulator/events?equivalent=UAH` (SSE)
  - Поток событий транзакций/клиринга, готовых к анимации.

Примечание про `equivalent`:
- Это строковый `EquivalentCode` (см. `api/openapi.yaml`), в примерах ниже используем `UAH`.
- В демо/seed данных также встречаются `HOUR` и `KWH` (см. `seeds/equivalents.json`).

Дополнение (Real Mode):
- backend также эмитит системное событие `run_status`:
  - при смене состояния (`start/pause/resume/stop/error`)
  - и периодически во время `running` (например раз в 1–2 секунды)
- событие `tick` не требуется для UI по умолчанию и допускается только как debug-режим (опционально)

Примечание: на раннем MVP можно сделать polling-режим:
- `GET /api/v1/simulator/events/poll?equivalent=UAH&after=evt_...`
  - Возвращает массив событий.

---

## 1.1) Endpoints (Real Mode control plane — расширение)

Этот раздел добавляет недостающие контракты, чтобы UI мог показывать вкладки:
`Scenario`, `Run`, `Metrics`, `Bottlenecks`, `Artifacts`.

Важно:
- Текущие `/graph/snapshot` и `/events` можно трактовать как **"active run"** (MVP),
  либо позже расширить до namespace с `run_id`.
- UI не должен вычислять метрики/узкие места из raw событий — backend отдаёт готовые серии/списки.

### Scenarios
- `GET /api/v1/simulator/scenarios`
  - Список доступных сценариев (presets).
- `POST /api/v1/simulator/scenarios`
  - Загрузка `scenario.json` (создаёт `scenario_id`).
- `GET /api/v1/simulator/scenarios/{scenario_id}` (опционально)
  - Полная информация по сценарию + summary.

### Runs
- `POST /api/v1/simulator/runs`
  - Старт прогона: `{ scenario_id, mode, intensity_percent } -> { run_id }`.

Примечание про `mode`:
- `real` — прогон с **DB enrichment** (балансы/долги/статусы для `viz_*`, если доступно).
- `fixtures` — **sandbox/topology-only**: backend строит снапшот из topology сценария без DB enrichment.
  Это удобно для быстрых smoke/e2e и отладки UI, но визуально может отличаться от «семантических» fixtures из Admin UI.
- `GET /api/v1/simulator/runs/{run_id}`
  - Текущее состояние прогона (для polling UI).
- `POST /api/v1/simulator/runs/{run_id}/pause`
- `POST /api/v1/simulator/runs/{run_id}/resume`
- `POST /api/v1/simulator/runs/{run_id}/stop`
- `POST /api/v1/simulator/runs/{run_id}/restart` (опционально)
- `POST /api/v1/simulator/runs/{run_id}/intensity`
  - Обновить интенсивность: `{ intensity_percent: 0..100 }`.

### Live events (namespace by run)
- `GET /api/v1/simulator/runs/{run_id}/events?equivalent=UAH` (SSE)
  - То же, что `/api/v1/simulator/events`, но привязанное к конкретному run.

### Debug/demo actions (backend-driven)

Для отладки визуальных эффектов без offline demo backend может предоставлять actions endpoints,
которые **эмитят обычные доменные события** в SSE (никакой отдельной демо-семантики).

- `POST /api/v1/simulator/runs/{run_id}/actions/tx-once`
- `POST /api/v1/simulator/runs/{run_id}/actions/clearing-once`

Спецификация: [../backend/backend-driven-demo-mode-spec.md](../backend/backend-driven-demo-mode-spec.md)

### Snapshot (namespace by run)
- `GET /api/v1/simulator/runs/{run_id}/graph/snapshot?equivalent=UAH`
  - То же, что `/api/v1/simulator/graph/snapshot`, но привязанное к конкретному run.

### Metrics
- `GET /api/v1/simulator/runs/{run_id}/metrics?equivalent=UAH&from_ms=...&to_ms=...&step_ms=...`
  - Возвращает time-series (готовые для графиков).

### Bottlenecks
- `GET /api/v1/simulator/runs/{run_id}/bottlenecks?equivalent=UAH&limit=20&min_score=0.7`
  - Возвращает top bottlenecks + причины.

### Artifacts (опционально)
- `GET /api/v1/simulator/runs/{run_id}/artifacts`
  - Индекс артефактов (urls + метаданные).
- `GET /api/v1/simulator/runs/{run_id}/artifacts/{name}`
  - Скачать конкретный файл (`summary.json`, `events.ndjson`, `snapshots-0001.json`, ...).

---

## 2) Типы данных

### 2.1 GraphSnapshot

```ts
export type GraphSnapshot = {
  equivalent: string
  generated_at: string // ISO

  nodes: GraphNode[]
  links: GraphLink[]

  // Для HUD и стабильных легенд (опционально).
  palette?: Record<string, { color: string; label?: string }>

  // Лимиты — подсказка клиенту, как деградировать без лагов.
  limits?: {
    max_nodes?: number
    max_links?: number
    max_particles?: number
  }
}
```

### 2.2 GraphNode

```ts
export type GraphNode = {
  id: string
  name?: string
  type?: string // business | person | ...
  status?: string // active | suspended | left | deleted | ...

  // Для HUD (UI не обязан строить визуализацию по этим числам).
  links_count?: number
  net_balance?: string | null // signed, major units (например "-123.45"); UI отображает как есть
  net_balance_atoms?: string | null
  net_sign?: -1 | 0 | 1 | null

  // Визуальные поля (источник правды для рендера).
  viz_color_key?: string | null
  viz_shape_key?: string | null
  viz_size?: { w: number; h: number } | null // размер примитива (UI применяет как есть)
  viz_badge_key?: string | null
}
```

### 2.3 GraphLink

```ts
export type GraphLink = {
  id?: string
  source: string
  target: string

  trust_limit?: string | number
  used?: string | number
  available?: string | number
  status?: string

  // Визуальные ключи.
  viz_color_key?: string | null
  viz_width_key?: string | null
  viz_alpha_key?: string | null
}
```

---

## 3) Допустимые `viz_*_key` (MVP)

Важно: это именно **ключи**. Клиент маппит их на стили/паттерны и не вычисляет значения.

### 3.1 Узлы: `viz_color_key`
- `business`
- `person`
- `suspended` (желательно паттерн/штриховка вместо “толстой рамки”)
- `left`
- `deleted`
- `debt-0` … `debt-8` (биннинг делает backend)

### 3.2 Рёбра: `viz_width_key`
- `hairline` (фон)
- `thin`
- `mid`
- `thick`
- `highlight` (событие/фокус)

### 3.3 Рёбра: `viz_alpha_key`
- `bg`
- `muted`
- `active`
- `hi`

### 3.4 Узлы: `viz_shape_key`
- `circle`
- `rounded-rect`

---

## 4) Events (готовые к анимации)

### 4.1 Общие требования
- Любое событие должно иметь `event_id` и `ts` (идемпотентность/упорядочивание).
- Клиент применяет событие без перерасчёта графа:
  - меняет overlays (подсветка/частицы/бейдж),
  - вызывает `refresh()`,
  - НЕ пересобирает `graphData`.

### 4.2 `tx.updated` (пример)

```json
{
  "event_id": "evt_tx_0007",
  "ts": "2026-01-22T12:00:01Z",
  "type": "tx.updated",
  "equivalent": "UAH",
  "from": "user_1",
  "to": "user_2",
  "amount": "150.00",
  "ttl_ms": 1200,
  "intensity_key": "mid",
  "edges": [
    { "from": "user_1", "to": "user_2", "style": { "viz_width_key": "highlight", "viz_alpha_key": "hi" } }
  ],
  "node_badges": [
    { "id": "user_1", "viz_badge_key": "tx" },
    { "id": "user_2", "viz_badge_key": "tx" }
  ]
}
```

Примечания (backend-first):
- `from → to` — направление транзакции.
- `amount` — **в major units** (строка, без локальных конвертаций на UI); знак выводимого дельта-лейбла на узлах задаётся UI как `-amount` у `from` и `+amount` у `to`.

### 4.3 `tx.failed` (диагностика ошибок/отказов)

Назначение:
- UI показывает понятную причину (и при желании может подсветить ребро `from -> to`).
- `tx.failed` не заменяет `run_status.last_error`, а дополняет его деталями по конкретной транзакции.

```json
{
  "event_id": "evt_tx_0008",
  "ts": "2026-01-22T12:00:02Z",
  "type": "tx.failed",
  "equivalent": "UAH",
  "from": "user_1",
  "to": "user_2",
  "error": { "code": "PAYMENT_TIMEOUT", "message": "PAYMENT_TIMEOUT", "at": "2026-01-22T12:00:02Z" }
}
```

### 4.4 `clearing.plan` (сценарий клиринга)

Примечание по визуалу:
- `steps` — это сценарные «подсказки» для FX overlay.
- Конкретная презентация (например, beam-искра золотого цвета + локальные вспышки узлов) — задача UI.
- В demo-fast-mock v2 клиринг реализован как последовательность `particles_edges` + node glows; full-screen `flash` не является обязательным элементом.

```json
{
  "event_id": "evt_0001",
  "ts": "2026-01-22T12:00:00Z",
  "type": "clearing.plan",
  "equivalent": "UAH",
  "plan_id": "clr_2026_01_22_0001",
  "steps": [
    { "at_ms": 0,   "highlight_edges": [{"from":"user_2","to":"user_5"}], "intensity_key": "hi" },
    { "at_ms": 180, "particles_edges": [{"from":"user_5","to":"user_9"}], "intensity_key": "mid" },
    { "at_ms": 420, "flash": {"kind":"clearing"} }
  ]
}
```

### 4.5 `clearing.done`
- Снять эффекты, затем либо:
  - запросить новый snapshot, либо
  - получить патчи.

Рекомендуемый контракт (backend-first):
```json
{
  "event_id": "evt_0002",
  "ts": "2026-01-22T12:00:01Z",
  "type": "clearing.done",
  "equivalent": "UAH",
  "plan_id": "clr_2026_01_22_0001",
  "cleared_cycles": 2,
  "cleared_amount": "10.00",
  "node_patch": [{ "id": "user_1", "net_balance": "-12.50", "net_balance_atoms": "1250", "net_sign": -1 }],
  "edge_patch": [{ "source": "user_1", "target": "user_2", "used": "1.00", "available": "9.00" }]
}
```

Правило: если есть `node_patch/edge_patch`, UI **не вычисляет** изменения балансов/лимитов, а просто применяет патчи.

---

## 5) Патчи (опционально, для FPS)

Если snapshot большой, обновление после клиринга можно делать патчами:
- `node_patch`: `{ id, net_balance, net_balance_atoms, net_sign, viz_color_key, viz_shape_key, viz_size }`
- `edge_patch`: `{ source, target, used/available, viz_*_key }`

Примечание: `viz_size` в `node_patch` — это тот же размер примитива (w/h). Если баланс/класс изменился,
backend может одновременно обновить и `viz_color_key`, и `viz_size`, а клиент просто применит их (без локальных расчётов).

---

## 6) Минимальные правила производительности для клиента

- В `Overview` не рисовать постоянные частицы: частицы только по `tx.*` и шагам `clearing.plan`.
- Если одновременно слишком много событий: оставить top-N по `intensity_key`.
- Подписи — только выбранный узел + соседи (глобальные подписи — отдельная настройка).

---

## 7) Real Mode: строгие типы для control plane

Цель этого раздела — чтобы backend и UI **не могли разъехаться по полям**.

Правила:
- Все ответы control-plane должны содержать `api_version` (строка), чтобы UI мог fail-fast при несовместимости.
- Новые поля добавлять можно (backward-compatible). Переименования/удаления — только через новый major `api_version`.
- `scenario.json` как входной формат должен иметь собственную `schema_version` (не путать с `api_version`).

### 7.1 ScenarioSummary

```ts
export type ScenarioSummary = {
  api_version: string // например: "simulator-api/1"

  scenario_id: string
  name?: string
  created_at?: string // ISO

  participants_count: number
  trustlines_count: number
  equivalents: string[]

  clusters_count?: number
  hubs_count?: number

  // Backend/tooling может приложить готовые подсказки для UI.
  tags?: string[]
}
```

Пример:

```json
{
  "api_version": "simulator-api/1",
  "scenario_id": "greenfield-village-100",
  "name": "Greenfield Village (100)",
  "created_at": "2026-01-28T10:05:00Z",
  "participants_count": 100,
  "trustlines_count": 520,
  "equivalents": ["UAH", "KWH"],
  "clusters_count": 7,
  "hubs_count": 6,
  "tags": ["preset", "demo"]
}
```

### 7.2 Scenarios list

```ts
export type ScenariosListResponse = {
  api_version: string
  items: ScenarioSummary[]
}
```

### 7.3 RunStatus

```ts
export type RunState = 'idle' | 'running' | 'paused' | 'stopping' | 'stopped' | 'error'

export type RunStatus = {
  api_version: string

  run_id: string
  scenario_id: string
  mode: 'fixtures' | 'real'

  state: RunState
  started_at?: string // ISO
  stopped_at?: string // ISO

  // UI-статус (то, что ты хотел видеть на вкладке Run)
  sim_time_ms?: number // виртуальное время симуляции
  intensity_percent?: number // 0..100
  ops_sec?: number
  queue_depth?: number

  // Ошибки
  errors_total?: number
  errors_last_1m?: number
  last_error?: { code: string; message: string; at: string } | null

  // Для "какая фаза/событие сейчас"
  last_event_type?: string | null
  current_phase?: string | null
}
```

Пример:

```json
{
  "api_version": "simulator-api/1",
  "run_id": "run_2026_01_28_001",
  "scenario_id": "greenfield-village-100",
  "mode": "real",
  "state": "running",
  "started_at": "2026-01-28T10:10:00Z",
  "sim_time_ms": 184000,
  "intensity_percent": 65,
  "ops_sec": 18.4,
  "queue_depth": 2,
  "errors_total": 3,
  "errors_last_1m": 1,
  "last_error": { "code": "PAYMENT_TIMEOUT", "message": "prepare timeout", "at": "2026-01-28T10:12:57Z" },
  "last_event_type": "tx.updated",
  "current_phase": "payments"
}
```

### 7.4 MetricSeries

Принцип: UI рисует графики, но **не вычисляет** метрики из событий.

```ts
export type MetricPoint = { t_ms: number; v: number }

export type MetricSeries = {
  key:
    | 'success_rate'
    | 'avg_route_length'
    | 'total_debt'
    | 'clearing_volume'
    | 'bottlenecks_score'

  unit?: '%' | 'count' | 'amount'
  points: MetricPoint[]
}

export type MetricsResponse = {
  api_version: string
  run_id: string
  equivalent: string
  from_ms: number
  to_ms: number
  step_ms: number
  series: MetricSeries[]
}
```

Пример:

```json
{
  "api_version": "simulator-api/1",
  "run_id": "run_2026_01_28_001",
  "equivalent": "UAH",
  "from_ms": 0,
  "to_ms": 600000,
  "step_ms": 10000,
  "series": [
    { "key": "success_rate", "unit": "%", "points": [{"t_ms":0,"v":92.0},{"t_ms":10000,"v":93.1}] },
    { "key": "avg_route_length", "unit": "count", "points": [{"t_ms":0,"v":2.4},{"t_ms":10000,"v":2.6}] }
  ]
}
```

### 7.5 BottleneckItem

```ts
export type BottleneckTarget =
  | { kind: 'edge'; from: string; to: string }
  | { kind: 'node'; id: string }

export type BottleneckItem = {
  target: BottleneckTarget
  score: number
  reason_code:
    | 'LOW_AVAILABLE'
    | 'HIGH_USED'
    | 'FREQUENT_ABORTS'
    | 'TOO_MANY_TIMEOUTS'
    | 'ROUTING_TOO_DEEP'
    | 'CLEARING_PRESSURE'

  // Опциональные подсказки для UI
  label?: string
  suggested_action?: string
}

export type BottlenecksResponse = {
  api_version: string
  run_id: string
  equivalent: string
  items: BottleneckItem[]
}
```

Пример:

```json
{
  "api_version": "simulator-api/1",
  "run_id": "run_2026_01_28_001",
  "equivalent": "UAH",
  "items": [
    {
      "target": { "kind": "edge", "from": "shop_12", "to": "household_44" },
      "score": 0.91,
      "reason_code": "LOW_AVAILABLE",
      "label": "No capacity",
      "suggested_action": "Increase trust limit or reroute"
    }
  ]
}
```

### 7.6 ArtifactIndex

Важное: UI в браузере **не открывает папки**. Поэтому артефакты — это ссылки для скачивания/просмотра.

```ts
export type ArtifactItem = {
  name: string // "summary.json" | "events.ndjson" | "snapshots-0001.json" | ...
  content_type?: string
  size_bytes?: number
  sha256?: string
  url: string
}

export type ArtifactIndex = {
  api_version: string
  run_id: string

  // Для dev-режима можно отдать путь, но UI использует это только как "copy".
  artifact_path?: string

  items: ArtifactItem[]

  // Опционально: одна ссылка на zip bundle.
  bundle_url?: string
}
```

Пример:

```json
{
  "api_version": "simulator-api/1",
  "run_id": "run_2026_01_28_001",
  "artifact_path": "C:/geo/runs/run_2026_01_28_001",
  "items": [
    { "name": "summary.json", "content_type": "application/json", "size_bytes": 18234, "url": "/api/v1/simulator/runs/run_2026_01_28_001/artifacts/summary.json" },
    { "name": "events.ndjson", "content_type": "application/x-ndjson", "size_bytes": 934455, "url": "/api/v1/simulator/runs/run_2026_01_28_001/artifacts/events.ndjson" }
  ],
  "bundle_url": "/api/v1/simulator/runs/run_2026_01_28_001/artifacts/bundle.zip"
}
```
