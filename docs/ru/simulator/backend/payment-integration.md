# Интеграция симулятора с PaymentEngine / GEO Core API (MVP)

Дата: **2026-01-28**

Назначение: зафиксировать, **как именно** SimulationRunner создаёт реальные платежи/клиринг в текущем GEO backend, чтобы:
- выполнялись SB-09/SB-10 (реальные платежи, понятные ошибки),
- сохранялись guardrails (идемпотентность, лимиты параллельности, таймауты),
- UI получал события `SimulatorEvent` (через SSE) без угадываний.

Source of truth:
- Endpoints и схемы: `api/openapi.yaml` (Payments + Clearing)
- Реализация payments: `app/api/v1/payments.py`, `app/core/payments/service.py`, `app/core/payments/engine.py`
- Реализация clearing: `app/api/v1/clearing.py`, `app/core/clearing/service.py`
- Протокол realtime: `docs/ru/simulator/backend/ws-protocol.md`
- Типы событий симулятора: `docs/ru/simulator/backend/simulator-domain-model.md`

---

## 1) Какие реальные API используются (в рамках monorepo)

### 1.1 Payments
В текущем backend уже есть Payments API:
- `POST /api/v1/payments` — создать платеж
- `GET /api/v1/payments/{tx_id}` — получить результат
- `GET /api/v1/payments` — список платежей
- `GET /api/v1/payments/capacity` — capacity check
- `GET /api/v1/payments/max-flow` — max-flow диагностика

Ключевые свойства:
- Поддержан `Idempotency-Key` (заголовок).
- На уровне API есть distributed lock по ключу: `dlock:payment:{sender_id}:{equivalent}`.
- В `PaymentService.create_payment()` есть:
  - поиск маршрута (routing)
  - 2PC-like prepare/commit (через `PaymentEngine`)
  - явные таймауты (см. раздел 3)

### 1.2 Clearing
В текущем backend есть Clearing API:
- `GET /api/v1/clearing/cycles?equivalent=...` — найти циклы
- `POST /api/v1/clearing/auto?equivalent=...` — выполнить auto clearing

Ключевые свойства:
- Guardrail: `CLEARING_ENABLED` может выключать clearing.
- Есть distributed lock: `dlock:clearing:{equivalent}`.
- `ClearingService` избегает пар участников, затронутых активными prepared payment flows (см. `_locked_pairs_for_equivalent`).

---

## 2) Как runner вызывает «реальный» payment (варианты)

Требование SB-09: симулятор должен вызывать **реальную** платежную логику (routing + prepare/commit), а не мок.

### Вариант A (рекомендуемый для MVP): in-process вызов PaymentService
Runner вызывает `PaymentService.create_payment(...)` напрямую (внутри того же backend процесса), передавая `sender_id` и сформированный `PaymentCreateRequest`.

Плюсы:
- Максимальная производительность (без HTTP, без сериализации).
- Используется тот же код `PaymentService/PaymentEngine`, что и в API.
- Упрощает сбор метрик и контроль таймаутов.

Минусы / что надо явно решить в реализации:
- API требует `signature`, а runner должен её либо:
  1) генерировать и хранить приватные ключи виртуальных пользователей, либо
  2) иметь безопасный internal-only путь без подписи.

Рекомендация MVP:
- Добавить **внутренний метод** (не HTTP endpoint) для симулятора:
  - например `PaymentService.create_payment_internal(...)`, который:
    - не требует подписи,
    - но выполняет те же шаги routing→tx persist→prepare→commit,
    - и остаётся недоступным извне (только импорт внутри `app/core/simulator/*`).

Безопасность:
- internal метод должен быть доступен только в коде (не через FastAPI router).
- в production-сборках можно дополнительно защищать фичефлагом.

### Вариант B (строгая parity с клиентами): HTTP вызов собственного `/payments`
Runner действует как «виртуальный клиент»:
- для каждого simulated participant хранит приватный ключ,
- формирует canonical JSON payload и подпись,
- использует auth контекст (или отдельный тех-пользователь с правом действовать от имени PID).

Плюсы:
- Максимально близко к реальным клиентам.

Минусы:
- Существенно увеличивает объём работ (key management, auth), особенно для seed-сценариев.

---

## 3) Таймауты, ретраи, лимиты

### 3.1 Таймауты (есть в PaymentService)
В `PaymentService.create_payment()` используются настройки:
- `ROUTING_PATH_FINDING_TIMEOUT_MS` (по умолчанию 500ms)
- `PREPARE_TIMEOUT_SECONDS` (по умолчанию 3s)
- `COMMIT_TIMEOUT_SECONDS` (по умолчанию 5s)
- `PAYMENT_TOTAL_TIMEOUT_SECONDS` (по умолчанию 10s)

Поведение:
- при `asyncio.TimeoutError` выполняется `engine.abort(..., reason="Payment timeout")` (shielded), затем кидается `TimeoutException`.

### 3.2 Идемпотентность
`Idempotency-Key`:
- поддержан и защищён fingerprint’ом запроса.
- если тот же ключ использован с другими параметрами → `ConflictException`.
- если тот же ключ уже «в процессе» → `ConflictException`.

Рекомендация для симулятора:
- генерировать `Idempotency-Key` детерминированно из:
  - `run_id`, `tick_index`, `from_pid`, `to_pid`, `equivalent`, `amount`, `action_seq`
- ключ должен быть:
  - достаточно коротким и безопасным по формату (см. `validate_idempotency_key`)
  - уникальным в пределах sender+equivalent

Пример (концептуально):
- `Idempotency-Key = "sim:" + sha256(<material>).hexdigest()[:32]`

### 3.3 Лимит параллельности
Чтобы не перегружать ядро:
- на run держать `max_in_flight` (см. `runner-algorithm.md` guardrails)
- дополнительно учитывать lock в API: платежи для одного sender+equivalent сериализуются через redis lock.

---

## 4) Маппинг ошибок payment → события симулятора

UI не читает внутренние состояния платежей; в симуляторе MVP ошибки отражаются через `run_status` (`state="error"`) и/или best-effort доменные события.

Текущая реализация:
- ошибки/отказы отдельных платежей эмитятся как `tx.failed` (с `error.code`/`error.message`)
- фатальные ошибки прогона по-прежнему отражаются через `run_status.state="error"` + `last_error`

### 4.1 Что считаем «ожидаемой бизнес-ошибкой» (PAYMENT_REJECTED)
Сюда относятся ситуации вида:
- `RoutingException` (нет маршрута / insufficient capacity)
- `BadRequestException` для параметров, которые runner сформировал неверно (это уже баг сценария/runner)

Рекомендация MVP:
- не переводить весь run в `error` на каждую бизнес-ошибку;
- учитывать в метриках и периодически отражать в `run_status` (например счётчик отказов).

`last_error.code` для UI (из `ws-protocol.md`):
- `PAYMENT_REJECTED`

### 4.2 Таймауты (PAYMENT_TIMEOUT)
- `TimeoutException` → `last_error.code=PAYMENT_TIMEOUT`.

Политика:
- порог: если за окно времени доля таймаутов выше X%, переводить run в `error`.

### 4.3 Внутренние/неожиданные ошибки (INTERNAL_ERROR)
- `GeoException`, `DBAPIError` и прочие непредвиденные → `last_error.code=INTERNAL_ERROR`.

---

## 5) Что эмитим в SSE (симулятор), когда платёж выполнен/не выполнен

Важно:
- `PaymentService` best-effort публикует `event_bus.publish(... event="payment.received" ...)`.
- Но контракт симулятора для UI — это `SimulatorEvent` union (`tx.updated`, `clearing.plan`, `clearing.done`, `run_status`).

MVP правило:
- симулятор **не обязан** проксировать `payment.received` как есть.
- симулятор должен преобразовать результаты действий (success/fail) в **визуальные события**:
  - `tx.updated` для подсветки рёбер/узлов
  - и обновления метрик

Рекомендация для `tx.updated`:
- при успехе:
  - `edges: [{ from: <sender_pid>, to: <receiver_pid>, style: { viz_width_key: "highlight", viz_alpha_key: "hi" } }]`
  - `ttl_ms`: 800..1500
- при отказе:
  - либо слабая подсветка (muted), либо только счётчик ошибок в HUD (через metrics/run_status)

---

## 6) Clearing в симуляторе

Если runner включает clearing (опционально на раннем MVP):
- инициировать `POST /api/v1/clearing/auto?equivalent=...` по политике (например раз в N тиков или при достижении порога debt).

Визуализация:
- до вызова или по результату:
  - сформировать `clearing.plan` (step list) для FX,
  - затем `clearing.done`.

Guardrails:
- учитывать, что `ClearingService` пропускает циклы, затрагивающие пары участников из активных payment prepare locks.

---

## 7) Definition of Done для B2

Документ B2 считается закрытым, если:
- перечислены конкретные payments/clearing endpoints и их свойства (idempotency/locks/timeouts);
- зафиксирован рекомендуемый способ вызова из runner (MVP вариант) и риски;
- описан маппинг классов ошибок → `last_error.code` из `ws-protocol.md`;
- описано, как платежные исходы преобразуются в `SimulatorEvent` (хотя бы на уровне `tx.updated`).
