# GEO Simulator API (MVP, черновик)

Этот документ — **источник правды** для контракта `/api/v1/simulator/*`.

Принципы (как в доработках графа Admin UI):
- Фронтенд **не вычисляет** семантику визуализации (цвет/размер/биннинг/приоритеты). Всё это приходит как `viz_*`.
- Клиент **не угадывает**, что подсветить: backend присылает готовые ключи и сценарии событий.
- Производительность важнее эффектов: по умолчанию мало текста, мало частиц, LOD по зуму/нагрузке.

---

## 1) Endpoints (MVP)

### Snapshot
- `GET /api/v1/simulator/graph/snapshot?equivalent=HOUR`
  - Возвращает текущее состояние сети + готовые `viz_*` поля.

### Focus/Ego (опционально, но рекомендуется)
- `GET /api/v1/simulator/graph/ego?equivalent=HOUR&pid=PID_...&depth=1`
  - Возвращает подграф вокруг узла (для режима Focus, чтобы не тянуть всё).

### Events
- `GET /api/v1/simulator/events?equivalent=HOUR` (SSE)
  - Поток событий транзакций/клиринга, готовых к анимации.

Примечание: на раннем MVP можно сделать polling-режим:
- `GET /api/v1/simulator/events/poll?equivalent=HOUR&after=evt_...`
  - Возвращает массив событий.

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
  net_balance_atoms?: string | null
  net_sign?: -1 | 0 | 1 | null

  // Визуальные поля (источник правды для рендера).
  viz_color_key?: string | null
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
  "equivalent": "HOUR",
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

### 4.3 `clearing.plan` (сценарий клиринга)

Примечание по визуалу:
- `steps` — это сценарные «подсказки» для FX overlay.
- Конкретная презентация (например, beam-искра золотого цвета + локальные вспышки узлов) — задача UI.
- В demo-fast-mock v2 клиринг реализован как последовательность `particles_edges` + node glows; full-screen `flash` не является обязательным элементом.

```json
{
  "event_id": "evt_0001",
  "ts": "2026-01-22T12:00:00Z",
  "type": "clearing.plan",
  "equivalent": "HOUR",
  "plan_id": "clr_2026_01_22_0001",
  "steps": [
    { "at_ms": 0,   "highlight_edges": [{"from":"user_2","to":"user_5"}], "intensity_key": "hi" },
    { "at_ms": 180, "particles_edges": [{"from":"user_5","to":"user_9"}], "intensity_key": "mid" },
    { "at_ms": 420, "flash": {"kind":"clearing"} }
  ]
}
```

### 4.4 `clearing.done`
- Снять эффекты, затем либо:
  - запросить новый snapshot, либо
  - получить патчи.

---

## 5) Патчи (опционально, для FPS)

Если snapshot большой, обновление после клиринга можно делать патчами:
- `node_patch`: `{ id, net_balance_atoms, net_sign, viz_color_key, viz_size }`
- `edge_patch`: `{ source, target, used/available, viz_*_key }`

Примечание: `viz_size` в `node_patch` — это тот же размер примитива (w/h). Если баланс/класс изменился,
backend может одновременно обновить и `viz_color_key`, и `viz_size`, а клиент просто применит их (без локальных расчётов).

---

## 6) Минимальные правила производительности для клиента

- В `Overview` не рисовать постоянные частицы: частицы только по `tx.*` и шагам `clearing.plan`.
- Если одновременно слишком много событий: оставить top-N по `intensity_key`.
- Подписи — только выбранный узел + соседи (глобальные подписи — отдельная настройка).
