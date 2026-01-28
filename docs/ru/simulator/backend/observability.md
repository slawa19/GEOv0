# Наблюдаемость (Observability) для Real Mode симулятора

Дата: 2026-01-28

Назначение: заранее зафиксировать **минимально достаточный** набор метрик/логов, чтобы симулятор был управляемым в разработке и в нагрузочных прогонах.

Важно: здесь два разных «класса метрик»:

1) **Операционные метрики сервиса** (Prometheus `/metrics`): HTTP latency, кол-во платежей, recovery, и т.п.
2) **Метрики прогона симулятора** (API control plane `GET /api/v1/simulator/runs/{run_id}/metrics`): time‑series для UI (charts) и анализа конкретного прогона.

---

## 1) Что уже есть в репозитории (факты)

### 1.1 Prometheus endpoint `/metrics`

В backend есть Prometheus endpoint:

- `GET /metrics` (выдаёт `text/plain; version=0.0.4` и т.п.)

Включается/выключается настройкой:

- `METRICS_ENABLED` (см. `app/config.py`)

Базовые метрики определены в `app/utils/metrics.py`:

- `geo_http_requests_total{method,path,status}`
- `geo_http_request_duration_seconds{method,path}`
- `geo_payment_events_total{event,result}`
- `geo_clearing_events_total{event,result}`
- `geo_recovery_events_total{event,result}`
- `geo_routing_failures_total{reason}`

HTTP метрики собираются middleware в `app/main.py`.

### 1.2 Корреляция запросов (request_id)

В middleware (`app/main.py`) реализован request id:

- Принимается входной `X-Request-ID` (если есть), иначе генерируется.
- Возвращается клиенту в `X-Request-ID`.

Есть утилита для измерения длительности операций в логах:

- `app/utils/observability.py::log_duration(logger, operation, **fields)`

Она добавляет `request_id` (best‑effort) и логирует строку в стабильном `key=value` стиле.

---

## 2) Принципы (чтобы не «сломать» мониторинг)

### 2.1 Кардинальность меток (важно)

В Prometheus нельзя бездумно добавлять labels:

- НЕЛЬЗЯ: `run_id`, `pid`, `participant_id`, `tx_id`, `scenario_id` как label — это взорвёт кардинальность.
- МОЖНО: ограниченные множества (enum): `state`, `result`, `type`, `equivalent` (если equivalents немного и фиксированы), `http_status`, `op`.

**Правило:** всё, что может быть «почти уникальным», идёт в лог/DB, но не в Prometheus labels.

### 2.2 Разделение сигналов

- Prometheus `/metrics` — про «здоровье сервиса» и технику.
- `GET /simulator/.../metrics` — про конкретный run (UI, анализ сценария, отчёты).

### 2.3 Best-effort

Метрики/логирование не должны ломать бизнес‑логику.

В текущем коде это уже соблюдается (часто стоит `try/except Exception: pass`).

---

## 3) MVP: какие метрики должны быть у симулятора

Ниже — список метрик (и точек измерения), которые нужны именно для Real Mode runner.

### 3.1 Метрики качества симуляции (для UI / run metrics endpoint)

Эти метрики должны агрегироваться и отдаваться через:

- `GET /api/v1/simulator/runs/{run_id}/metrics?from_ms&to_ms&step_ms` (см. `api/openapi.yaml`)

Минимальный набор (привязка к требованиям из `GEO-community-simulator-application.md` и acceptance criteria):

- Success rate платежей: доля `tx.completed` / (completed+failed)
- Avg/median route length (hop count)
- Throughput: payments/sec (и clearing/sec при включённом clearing)
- Clearing volume (сумма/кол-во клиринговых операций)
- Bottlenecks score time‑series (или count of bottlenecks above threshold)

Точки измерения:

- в момент завершения платежа (успех/ошибка, route len)
- в момент выполнения clearing
- периодически (каждые `step_ms`) — сэмплирование агрегатов

### 3.2 Технические метрики runner (для Prometheus)

Эти метрики нужны, чтобы понимать, «почему всё тормозит/падает».

Рекомендуемые имена (конвенция): `geo_simulator_*`.

1) Tick‑loop

- `geo_simulator_tick_duration_seconds` (Histogram)
  - labels: `phase` (например: `plan|execute|emit|persist`)
- `geo_simulator_tick_lag_seconds` (Gauge)
  - рассинхрон: `(wall_clock_now - expected_tick_time)`

2) Нагрузка и управление

- `geo_simulator_actions_total{action,result}` (Counter)
  - action: `payment|clearing|noop|pause|resume|stop`
  - result: `success|error|skipped|timeout`
- `geo_simulator_inflight_actions` (Gauge)
- `geo_simulator_action_duration_seconds{action}` (Histogram)

3) Стрим событий (SSE)

- `geo_simulator_sse_clients` (Gauge)
- `geo_simulator_sse_events_sent_total{type}` (Counter)
  - type: ограниченное enum (например `run_status|tx_completed|tx_failed|error|snapshot`)
- `geo_simulator_sse_bytes_sent_total` (Counter)
- `geo_simulator_sse_disconnects_total{reason}` (Counter)
  - reason: `client|timeout|server_error`

4) Буферы/очереди (важно для best‑effort stream)

- `geo_simulator_event_buffer_size` (Gauge)
- `geo_simulator_event_dropped_total{reason}` (Counter)
  - reason: `ring_full|queue_full|backpressure`

Точки измерения:

- Runner loop (tick начало/конец)
- Планировщик действий (enqueue/dequeue)
- SSE handler (connect/disconnect/write)
- Слой «ring buffer» (дропы)

---

## 4) Логи: что логировать и как

Требования к логам для runner:

- Стабильный формат: `key=value` (как уже делается местами в payments/clearing)
- Всегда включать:
  - `request_id` (для HTTP команд)
  - `run_id` и `scenario_id` (но как поля в сообщении, не как Prometheus label)
  - `state` переходы (`created → running → paused → stopped/error`)
- Логировать «переходы и итоги», а не каждый tick (иначе шум)

Рекомендуемые события логирования:

- `sim.run.start` / `sim.run.stop` / `sim.run.pause` / `sim.run.resume`
- `sim.tick.slow` если tick duration > порога
- `sim.action.failed` с кодом ошибки и кратким контекстом
- `sim.sse.client_connected` / `sim.sse.client_disconnected`

---

## 5) Локальная проверка наблюдаемости

### 5.1 Проверить Prometheus метрики

- `GET http://127.0.0.1:18000/metrics` (если backend запущен через `scripts/run_local.ps1`)

Ожидаемо увидеть строки с префиксом `geo_`.

### 5.2 Проверить request id

Сделайте любой запрос, передав `X-Request-ID`, и проверьте, что он возвращается в ответе.

---

## 6) Как это связано с UI метриками симулятора

UI‑графики (Real Mode вкладка Metrics) должны потреблять **не** `/metrics`, а:

- `GET /api/v1/simulator/runs/{run_id}/metrics` (run‑scoped time series)

А `/metrics` остаётся для DevOps/диагностики.

---

## 7) Checklist для реализации (когда дойдём до кода)

- Добавить `geo_simulator_*` метрики в `app/utils/metrics.py` (без high‑cardinality labels)
- Инструментировать runner loop (tick duration/lag)
- Инструментировать SSE handler (clients, events sent, disconnects)
- Ввести пороги «slow tick» и логировать только превышения
- Привязать `run_status` heartbeat к измерениям (например, last_emit_ts, emit failures)
