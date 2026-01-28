# Real-time протокол Simulator Backend (MVP: SSE + REST команды)

**Статус:** done (2026-01-28)

Несмотря на имя файла, для MVP мы фиксируем **SSE + REST команды**.
WebSocket — возможное улучшение позже (не блокирует MVP).

## 1) Выбранный транспорт (MVP)

### 1.1 Server → Client: SSE
- Endpoint: `GET /api/v1/simulator/runs/{run_id}/events?equivalent=...`
- Формат: `text/event-stream`, каждое сообщение содержит 1 JSON event.

Ожидаемые заголовки ответа (рекомендация):
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `Connection: keep-alive`

#### 1.1.1 SSE framing (MVP)
Для каждого события рекомендуется использовать стандартные поля SSE:

```
id: <event_id>
event: simulator.event
data: { ...json... }

```

Требования:
- `id` = `event_id` из payload.
- `data` содержит ровно 1 JSON-объект, совместимый с `SimulatorEvent` из `api/openapi.yaml`.
- `event` для MVP фиксируем как `simulator.event` (один event name для всех payload типов).
- Сервер должен периодически слать keep-alive комментарий (например раз в 10–20 секунд): `: keep-alive\n\n`.

Примечание про ограничения доставки (MVP):
- stream может присылать события с редукцией (например drop низкоприоритетных `tx.updated` при перегрузке), но **не должен** пропускать `run_status`.
- порядок событий best-effort; UI не должен предполагать строгий total order между разными эквивалентами.

### 1.2 Client → Server: REST команды
- `POST /api/v1/simulator/runs/{run_id}/pause`
- `POST /api/v1/simulator/runs/{run_id}/resume`
- `POST /api/v1/simulator/runs/{run_id}/stop`
- `POST /api/v1/simulator/runs/{run_id}/restart` (опционально)
- `POST /api/v1/simulator/runs/{run_id}/intensity` body: `{ "intensity_percent": 0..100 }`

Семантика команд (MVP):
- команды должны быть **идемпотентны** по состоянию:
  - `pause` на уже paused → вернуть 200 с текущим `RunStatus`
  - `resume` на running → 200
  - `stop` на stopped → 200
- при успешном применении команды backend возвращает `RunStatus` и **эмитит** `run_status` событие.

## 2) Reconnect policy
- UI при обрыве SSE переподключается с backoff.
- UI всегда может восстановить «истину» через `GET /api/v1/simulator/runs/{run_id}` и `GET .../graph/snapshot`.

Рекомендованный backoff (UI):
- 1s, 2s, 5s, 10s, 20s (с jitter), далее держать 20s.

MVP-гарантии (сознательно простые):
- stream не гарантирует полнофункциональный replay при реконнекте.
- Если клиент хочет "догнать" состояние после реконнекта — он делает `GET run_status` + `GET snapshot`.

Опционально (не блокирует MVP):
- поддержать `Last-Event-ID` для best-effort пропуска дубликатов.
  - UI при реконнекте отправляет заголовок `Last-Event-ID: <event_id>`.
  - Backend может попытаться реплеить из ring-buffer (если хранит), но в MVP это **не обязательно**.

Hardening (после MVP):
- Backend может завершать SSE stream после получения терминального `run_status.state` = `stopped` или `error`.
- Backend может включать строгий режим replay: если `Last-Event-ID` старее окна ring-buffer, вернуть 410 и UI делает full refresh (GET run_status + snapshots).

## 3) Системные сообщения

### 3.1 `run_status` (обязательное)
- Backend эмитит:
  - при смене состояния
  - периодически во время `running` (1–2 сек)

MVP правило: `run_status` — единственный обязательный "heartbeat" для состояния run.
Даже если доменных событий нет, UI должен получать `run_status` периодически.

Пример payload (минимум):
```json
{
  "event_id": "evt_status_0001",
  "ts": "2026-01-28T10:12:00Z",
  "type": "run_status",
  "run_id": "run_2026_01_28_001",
  "scenario_id": "greenfield-village-100",
  "state": "running",
  "sim_time_ms": 184000,
  "intensity_percent": 65,
  "ops_sec": 18.4,
  "queue_depth": 2
}
```

### 3.2 `tick` (debug-only)
- По умолчанию не эмитим.
- Допускается включение через debug-флаг (решение позже).

## 4) Доменные сообщения
- `tx.updated`
- `tx.failed`
- `clearing.plan`
- `clearing.done`

См. `docs/ru/simulator/frontend/docs/api.md` — там source of truth для полей `viz_*`.

## 5) Ошибки
- При ошибке выполнения backend:
  - переводит run в `error`
  - эмитит `run_status` с `last_error`

### 5.1 Таблица кодов ошибок (MVP)

`last_error.code` (строка):

| code | Когда | Что делает UI |
|---|---|---|
| `SCENARIO_INVALID` | сценарий не валиден/не загружается | показать ошибку в Run/Scenario, предложить выбрать другой сценарий |
| `RUN_NOT_FOUND` | run_id не существует/удалён | закрыть stream, показать "run not found" |
| `RUN_CONFLICT` | команда не применима из-за гонки/состояния | показать предупреждение, перезапросить `GET /runs/{run_id}` |
| `PAYMENT_TIMEOUT` | таймаут запроса в PaymentEngine/GEO API | показать деградацию/ошибку, run может продолжиться или упасть (зависит от политики runner) |
| `PAYMENT_REJECTED` | бизнес-ошибка платежа (нет маршрута/лимитов) | не фейлить UI, только счётчик ошибок/бэйдж |
| `INTERNAL_ERROR` | непредвиденная ошибка сервера | показать "internal error", предложить restart |

Примечание:
- В MVP допускается расширять список кодов без изменения `api_version`.

## 6) Версионирование
- Для control-plane ответов используем `api_version`.
- Для сообщений в stream (events) версионирование через backward-compatible добавление полей.

## 7) Таблица типов сообщений (MVP)

Источник правды: union `SimulatorEvent` в `api/openapi.yaml`.

| type | Канал | Назначение |
|---|---|---|
| `run_status` | SSE | статус прогона + heartbeat |
| `tx.updated` | SSE | визуальные подсветки транзакций |
| `clearing.plan` | SSE | план шагов клиринга |
| `clearing.done` | SSE | завершение клиринга |

Команды (REST): `pause`, `resume`, `stop`, `restart` (опц.), `intensity`.
