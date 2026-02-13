# Модель данных симулятора (Real Mode): events / snapshots / metrics

**Статус:** done (2026-01-28)

Этот документ фиксирует доменную модель данных симулятора так, чтобы:
- backend и simulator-ui не разъехались по полям,
- можно было писать contract-тесты,
- UI не вычислял метрики/узкие места из raw событий.

## Источники (source of truth)
- `docs/ru/simulator/frontend/docs/api.md` — контракт UI (snapshot/events + control-plane типы)
- `api/openapi.yaml` — формальный OpenAPI контракт

## 1) Идентификаторы и базовые поля

### 1.1 Run / Scenario
- `scenario_id: string` — сценарий
- `run_id: string` — прогон

### 1.2 Время
- `ts: string (ISO)` — время на стороне сервера (wall-clock)
- `sim_time_ms?: number` — виртуальное время симуляции (мс от старта прогона)

## 2) Data plane

### 2.1 Snapshot (graph)
- Тип: `SimulatorGraphSnapshot`
- Канал доставки: `GET /api/v1/simulator/graph/snapshot` (legacy) и `GET /api/v1/simulator/runs/{run_id}/graph/snapshot`
- Требование: поля `viz_*` (включая `viz_color_key`, `viz_shape_key`, `viz_size`) задаются backend (UI не вычисляет)

#### 2.1.1 Минимальный контракт Snapshot (MVP)
Контракт фиксируем по `api/openapi.yaml`:

- Snapshot-level (обязательное):
  - `equivalent: EquivalentCode`
  - `generated_at: ISO datetime`
  - `nodes: SimulatorGraphNode[]`
  - `links: SimulatorGraphLink[]`

- Node-level (обязательное):
  - `id: string`
  - остальные поля (включая `viz_*`) допускаются как `nullable`/optional и могут расширяться.

- Link-level (обязательное):
  - `source: string`
  - `target: string`

Пример минимального snapshot:
```json
{
  "equivalent": "UAH",
  "generated_at": "2026-01-28T12:00:00.000Z",
  "nodes": [{ "id": "p1" }, { "id": "h1" }],
  "links": [{ "source": "p1", "target": "h1" }]
}
```

### 2.2 Events stream
- Канал доставки: `GET /api/v1/simulator/events` (legacy) и `GET /api/v1/simulator/runs/{run_id}/events`
- Транспорт MVP: SSE

#### 2.2.1 Базовые поля события (все события)
- `event_id: string` — уникальный id события
- `ts: string (ISO)`
- `type: string` — строковый дискриминатор

Важно про `equivalent`:
- для доменных событий `tx.updated|clearing.done` в OpenAPI `equivalent` является **обязательным**
- для `run_status` `equivalent` не требуется

#### 2.2.2 Каталог событий (MVP: полный контракт)

MVP-контракт событий **точно равен** union `SimulatorEvent` в `api/openapi.yaml`.

1) `tx.updated`
- Назначение: короткоживущие визуальные подсветки (edges / node_badges), без обязанности показывать полное состояние.
- Обязательные поля (OpenAPI):
  - `event_id`, `ts`, `type="tx.updated"`, `equivalent`
- Опциональные поля (OpenAPI, can be omitted):
  - `ttl_ms`, `intensity_key`, `edges[]`, `node_badges[]`
  - `from`, `to`, `amount` — для backend-first подписей/FX транзакции (amount в major units, строка)
  - `node_patch[]`, `edge_patch[]` — если backend хочет сразу прислать обновление состояния (опционально)

2) `tx.failed`
- Назначение: нормализованное событие ошибки/отказа платежа (для статистики и UX без обращения к логам).
- Обязательные поля (OpenAPI):
  - `event_id`, `ts`, `type="tx.failed"`, `equivalent`, `error`
- Поле `error` (OpenAPI):
  - `error.code: string` (например `PAYMENT_TIMEOUT|PAYMENT_REJECTED|INTERNAL_ERROR`)
  - `error.message: string`
  - `error.at: ISO datetime`
  - дополнительные детали допускаются как `additionalProperties`
- Поля `from`/`to` допускаются как nullable.

3) `clearing.done`
- Назначение: завершение клиринга + (опционально) статистика и патчи для обновления графа без локальных расчётов.
- Обязательные поля (OpenAPI):
  - `event_id`, `ts`, `type="clearing.done"`, `equivalent`
 - Опциональные поля (OpenAPI, can be omitted):
  - `plan_id`
  - `cleared_cycles`, `cleared_amount` (amount в major units, строка)
  - `cycle_edges[]` — список затронутых рёбер (edge refs `{from,to}`) для FX-подсветки
  - `node_patch[]`, `edge_patch[]`

4) `run_status`
- Назначение: мониторинг/восстановление состояния; обязательное событие для UI вкладки Run.
- Эмитится:
  - при смене состояния (`start/pause/resume/stop/error`)
  - и периодически во время `running` (например раз в 1–2 секунды)
- Обязательные поля (OpenAPI):
  - `event_id`, `ts`, `type="run_status"`, `run_id`, `scenario_id`, `state`
- Опциональные поля (OpenAPI):
  - `sim_time_ms`, `intensity_percent`, `ops_sec`, `queue_depth`, `last_event_type`, `current_phase`, `last_error` и др.

Ошибки (текущая модель):
- отдельного `type="error"` события нет
- ошибки отдельных платежей выражаются через `tx.failed`
- фатальная ошибка прогона выражается через `run_status.state="error"` + `last_error`

5) `audit.drift`
- Назначение: сигнал о нарушении целостности данных (Lost Update / Data Corruption), обнаруженном Post-Tick Audit.
- Обязательные поля:
  - `event_id`, `ts`, `type="audit.drift"`, `equivalent`
  - `severity`: "warning" | "critical"
  - `total_drift`: строка (сумма модулей расхождений)
- Опциональные поля:
  - `drifts[]`: список деталей по участникам (`participant_id`, `expected_delta`, `actual_delta`, `drift`)
  - `source`: "post_tick_audit" | "delta_check"

#### 2.2.3 Системные события
- `run_status` — обязательное для MVP событие статуса:
  - эмитить при смене состояния (`start/pause/resume/stop/error`)
  - и периодически во время `running` (например раз в 1–2 секунды)

`tick`:
- не требуется для UI по умолчанию
- допускается только как debug-режим (опционально)

## 3) Control plane (управление)

### 3.1 RunStatus
- Канал: `GET /api/v1/simulator/runs/{run_id}` и также как `run_status` event
- Назначение: UI вкладки Run + мониторинг

### 3.2 Metrics
- Канал: `GET /api/v1/simulator/runs/{run_id}/metrics`
- Формат: готовые time-series для графиков

#### 3.2.1 Набор метрик MVP (канонические ключи)
Ключи должны совпадать с `MetricSeriesKey` в `api/openapi.yaml`.

- `success_rate` — доля успешных платежей за интервал, диапазон $[0,1]$.
  - Определение: `successful_payments / attempted_payments` в пределах одного временного бакета.
  - Если `attempted_payments = 0`, допускается `value = null` (предпочтительно) или `0` (если UI проще так обрабатывать).

- `avg_route_length` — средняя длина маршрута (hop count) **для успешных платежей** за интервал.
  - Единицы: hops (целое или float).

- `total_debt` — суммарный объём непогашенных обязательств в сети на момент бакета.
  - Единицы: сумма в выбранном эквиваленте (например UAH).
  - Определение (MVP): сумма всех текущих долгов по всем отношениям/рёбрам (не «net»).

- `clearing_volume` — объём клиринга за интервал.
  - Единицы: сумма в выбранном эквиваленте.

- `bottlenecks_score` — агрегированный индикатор «насколько сеть упёрлась».
  - Диапазон: $[0,1]$.
  - Определение (MVP): нормализованная функция от top-N bottlenecks (например max(score) или average(score)).

### 3.3 Bottlenecks
- Канал: `GET /api/v1/simulator/runs/{run_id}/bottlenecks`
- Возврат: top-N + (опционально) фильтр `min_score`

### 3.4 Artifacts (export)
- Канал: `GET /api/v1/simulator/runs/{run_id}/artifacts`
- Важно: UI в браузере скачивает/копирует ссылки, но не «открывает папку».

## 4) Версионирование
- `api_version` — только для control-plane ответов (fail-fast совместимость UI)
- `schema_version` — для входного `scenario.json`

## 5) TODO (для закрытия документа)
Открытые вопросы (не блокируют MVP):
- Решить, хотим ли дополнительно стандартизировать причины отказов платежей как отдельные поля (пока они живут в `last_error`).
- Решить, нужен ли debug `tick` в OpenAPI (сейчас допускается только как неформальный debug).

## Приложение A: JSON примеры событий (MVP)

### A.1 `run_status`
```json
{
  "event_id": "evt_0001",
  "ts": "2026-01-28T12:00:00.000Z",
  "type": "run_status",
  "run_id": "run_123",
  "scenario_id": "minimal",
  "state": "running",
  "sim_time_ms": 2000,
  "intensity_percent": 50,
  "ops_sec": 120.0
}
```

### A.2 `tx.updated`
```json
{
  "event_id": "evt_0100",
  "ts": "2026-01-28T12:00:02.000Z",
  "type": "tx.updated",
  "equivalent": "UAH",
  "ttl_ms": 1500,
  "edges": [
    { "from": "p1", "to": "h1", "style": { "viz_width_key": "pulse" } }
  ],
  "node_badges": [
    { "id": "h1", "viz_badge_key": "busy" }
  ]
}
```

### A.3 `clearing.done`
```json
{
  "event_id": "evt_0250",
  "ts": "2026-01-28T12:00:05.800Z",
  "type": "clearing.done",
  "equivalent": "UAH",
  "plan_id": "plan_001",
  "cleared_cycles": 2,
  "cleared_amount": "10.00",
  "cycle_edges": [{ "from": "p1", "to": "h1" }, { "from": "h1", "to": "p2" }],
  "node_patch": [{ "id": "p1", "net_balance": "-12.50", "net_balance_atoms": "1250", "net_sign": -1 }],
  "edge_patch": [{ "source": "p1", "target": "h1", "used": "1.00", "available": "9.00" }]
}
```

### A.4 `tx.failed`
```json
{
  "event_id": "evt_0150",
  "ts": "2026-01-28T12:00:03.000Z",
  "type": "tx.failed",
  "equivalent": "UAH",
  "from": "p1",
  "to": "h1",
  "error": {
    "code": "PAYMENT_REJECTED",
    "message": "insufficient limit",
    "at": "2026-01-28T12:00:03.000Z",
    "details": {"status_code": 409}
  }
}
```

### A.5 `audit.drift`
```json
{
  "event_id": "evt_audit_drift_001",
  "ts": "2026-02-13T10:00:00.000Z",
  "type": "audit.drift",
  "source": "post_tick_audit",
  "severity": "critical",
  "equivalent": "UAH",
  "total_drift": "30.00",
  "drifts": [
    {"participant_id": "p1", "drift": "30.00"},
    {"participant_id": "p2", "drift": "-30.00"}
  ]
}
```
