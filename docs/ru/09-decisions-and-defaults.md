# GEO v0.1 — ключевые решения и дефолты MVP

**Версия:** 0.1  
**Дата:** Декабрь 2025

Краткая сводка ключевых архитектурных решений и рекомендуемых дефолтов для MVP.

---

## 1. Ключевые решения MVP

### 1.1. Архитектура и узлы

| Решение | Выбор для MVP |
|---------|---------------|
| **Модель узлов** | Hub-центричная (участник = аккаунт в hub) |
| **Подпись операций** | Обязательная на стороне клиента (Ed25519) |
| **Координация 2PC** | Сервер hub (состояние + транзакционные блокировки в БД) |
| **Primary клиент** | PWA (Web Client) |
| **Мобильный/десктоп** | Flutter — отложено до v1.0+ |

### 1.2. Клиринг

| Решение | Выбор для MVP |
|---------|---------------|
| **Режим** | Автоматический (по расписанию + по триггерам) |
| **Согласие участников** | По умолчанию `auto_clearing: true` в TrustLine policy |
| **Длина циклов (триггерный)** | 3–4 узла |
| **Длина циклов (периодический)** | 5–6 узлов (опционально) |

Дополнение (MVP):
- **Нулевые долги не храним.** В таблице `Debt` допустимы только `amount > 0`; если в результате платежа или клиринга сумма долга становится равной 0, запись удаляется.
- **Трассируемость клиринга.** В `Transaction.payload` для `type=CLEARING` сохраняется `equivalent` и список рёбер цикла (`debtor`, `creditor`, `amount`), чтобы клиринг можно было воспроизвести и аудитировать.

### 1.3. Маршрутизация (Routing)

| Решение | Выбор для MVP |
|---------|---------------|
| **Базовый режим** | Limited multipath (k-shortest paths) |
| **Максимум путей** | 3 |
| **Максимум хопов** | 6 |
| **Full multipath** | Выключен по умолчанию (feature flag для бенчмарков) |

### 1.4. Эквиваленты

| Решение | Выбор для MVP |
|---------|---------------|
| **Кто создаёт** | Только админ/оператор сообщества |
| **Стартовый набор** | UAH, HOUR, kWh |

### 1.5. Верификация

| Решение | Выбор для MVP |
|---------|---------------|
| **KYC** | Не внедряем в MVP |
| **Verification levels** | 0–3 (лимиты и права по уровням) |
| **Модерация** | Ручная (админ/оператор) |

### 1.6. Операторские полномочия

| Решение | Выбор для MVP |
|---------|---------------|
| **Действия** | freeze/unfreeze, ban/unban, расследование, компенсирующие операции |
| **Аудит** | Обязательный audit-log для всех действий |
| **Роли** | admin, operator, auditor |

### 1.7. Машина состояний транзакций (internal)

В БД `Transaction.state` хранит внутренние состояния для операционной надёжности (recovery, идемпотентность, учёт 2PC/локов). Эти состояния **не равны** публичному статусу результата платежа.

**Допустимые internal-состояния (ограничение в БД):** `NEW`, `ROUTED`, `PREPARE_IN_PROGRESS`, `PREPARED`, `COMMITTED`, `ABORTED`, `PROPOSED`, `WAITING`, `REJECTED`.

#### 1.7.1. Таблица: `Transaction.type` → states → финальные → internal-only

Важно: в текущей реализации **не все** значения `Transaction.type`, присутствующие в CHECK constraints, реально используются как записи в таблице `transactions`. Часть типов зарезервирована под будущее расширение.

| `Transaction.type` | Ожидаемые `state` (MVP сейчас) | Финальные `state` | Internal-only | Комментарий |
|---|---|---|---|---|
| `PAYMENT` | `NEW → PREPARED → COMMITTED` или `NEW → PREPARED → ABORTED` | `COMMITTED`, `ABORTED` (зарезервировано: `REJECTED`) | `NEW`, `PREPARED` (зарезервировано: `ROUTED`, `PREPARE_IN_PROGRESS`, `PROPOSED`, `WAITING`) | Payment engine фактически выставляет `NEW`, затем `PREPARED`, далее `COMMITTED`/`ABORTED`. Остальные состояния присутствуют для операционной надёжности и будущих расширений, но в MVP не используются как переходы. |
| `CLEARING` | `NEW → COMMITTED` | `COMMITTED` | `NEW` | Clearing выполняется в рамках одной DB-транзакции. В случае ошибки — rollback; запись `Transaction` может не сохраниться, поэтому `ABORTED` для clearing сейчас скорее «концептуально возможен», чем наблюдаем в БД. |
| `TRUST_LINE_CREATE` | n/a (не создаётся `Transaction`) | n/a | n/a | TrustLine create/update/close в MVP пишут audit (`IntegrityAuditLog`), но не создают запись в `transactions`. Если позже потребуется унификация аудита через `Transaction`, ожидаемый поток будет single-phase: `NEW → COMMITTED/ABORTED`. |
| `TRUST_LINE_UPDATE` | n/a (не создаётся `Transaction`) | n/a | n/a | См. `TRUST_LINE_CREATE`. |
| `TRUST_LINE_CLOSE` | n/a (не создаётся `Transaction`) | n/a | n/a | См. `TRUST_LINE_CREATE`. |
| `COMPENSATION` | n/a (зарезервировано) | n/a | n/a | Зарезервировано под операторские компенсирующие операции. При реализации, вероятнее всего, будет single-phase `NEW → COMMITTED/ABORTED`. |
| `COMMODITY_REDEMPTION` | n/a (зарезервировано) | n/a | n/a | Зарезервировано под операции погашения товарных эквивалентов. При реализации, вероятнее всего, будет single-phase `NEW → COMMITTED/ABORTED`. |

**PAYMENT (2PC-подобный поток, реализация MVP):**

| Состояние | Смысл (MVP) |
|----------|-------------|
| `NEW` | Запись транзакции создана, маршрутизация рассчитана и сохранена в `payload` |
| `PREPARED` | Сегментные блокировки созданы успешно (фаза 1) |
| `COMMITTED` | Долги обновлены и блокировки удалены (фаза 2) |
| `ABORTED` | Терминальный сбой; блокировки удалены (best-effort) и сохранён `error` |

Примечания:
- `ROUTED` и `PREPARE_IN_PROGRESS` зарезервированы как internal-состояния, но в текущем MVP payment engine не выставляются.
- `REJECTED` зарезервирован как терминальное состояние для будущих «явных отказов»; в MVP считается терминальным.

**CLEARING (MVP):**

| Состояние | Смысл (MVP) |
|----------|-------------|
| `NEW` | Создана транзакция клиринга |
| `COMMITTED` | Клиринг успешно применён |
| `ABORTED` | Терминальный сбой |

**Recovery (MVP):** транзакции, застрявшие в «активных» internal-состояниях дольше заданного времени, переводятся в `ABORTED`, а связанные блокировки очищаются.

**Публичный статус платежа:** `PaymentResult.status` — это финальный результат и возвращает только `COMMITTED` или `ABORTED`.

### 1.8. Каноничный контракт результата платежа (single source of truth)

Решение для MVP: форма результата платежа и маршрутов — **строго типизированный контракт**, который задаётся и валидируется в одном месте.

**Source of truth (схемы):**

- Pydantic-модели: `app/schemas/payment.py` (`PaymentResult`, `PaymentRoute`).
- (Для внешнего API) OpenAPI: `api/openapi.yaml`.

**Инварианты контракта:**

- `PaymentResult.routes: Optional[List[PaymentRoute]]`.
- `PaymentRoute` имеет ровно поля `{ path: List[str], amount: str }` (лишние поля запрещены).

**Где контракт должен обеспечиваться:**

- Единственная точка приведения/валидации «сырого payload из БД» → типизированная модель: `PaymentService._tx_to_payment_result()`.

**Правило для consumer-кода (симулятор/раннер/внутренние сервисы):**

- Consumer-код использует `res.routes` напрямую и **не делает** защитный парсинг через `getattr`, `dict.get(...)` и т.п.
- Если `Transaction.payload.routes` имеет неверную форму, это считается багом/коррупцией данных и должно проявляться как явная ошибка (fail-fast) на границе `PaymentService`.

### 1.9. Simulator UI: устойчивость производительности (software-only / low FPS)

Дополнение (Январь 2026): UI симулятора должен оставаться работоспособным даже если браузер внезапно перешёл в software-only режим (например, `Microsoft Basic Render Driver`) или baseline FPS крайне низкий сразу после открытия.

Решение:
- Вводим эвристику определения software-only (по WebGL renderer string, если доступно).
- В software-only режиме автоматически выбираем безопасное качество рендера (`quality=low`), если пользователь не успел вручную переключить качество вскоре после старта.
- Отключаем дорогие CSS-эффекты (например, `backdrop-filter`) при software-only независимо от выбранного качества.
- Для сохранения визуального glow без `shadowBlur` используем pre-baked glow sprites как fallback в software-only режиме.

Каноничное описание политики и точек реализации: [simulator/frontend/docs/performance-and-quality-policy.md](simulator/frontend/docs/performance-and-quality-policy.md).

### 1.10. Simulator (MVP): сценарии и движок запуска

Решение: симулятор использует **входные сценарии (`scenario.json`)** и общий движок запуска (runtime + runner). Сценарий — это **описание мира (участники/trustlines/эквиваленты)**, а не скрипт с заранее заданным результатом.

Каноничные входные артефакты и доки:
- RU обзор (для пользователей + для технарей): [simulator/scenarios-and-engine.md](simulator/scenarios-and-engine.md)
- Индекс документации симулятора: [simulator/README.md](simulator/README.md)
- JSON Schema сценария: [fixtures/simulator/scenario.schema.json](fixtures/simulator/scenario.schema.json)
- Примеры сценариев: [fixtures/simulator/](fixtures/simulator/)

Дефолты runtime (env‑override допускается):
- `SIMULATOR_TICK_MS_BASE=1000`
- `SIMULATOR_ACTIONS_PER_TICK_MAX=20`
- `SIMULATOR_CLEARING_EVERY_N_TICKS=25` (для real mode)

Расширение real mode (behavior model):
- Спецификация: [simulator/backend/behavior-model-spec.md](simulator/backend/behavior-model-spec.md)
- Новый env (backward‑compatible): `SIMULATOR_REAL_AMOUNT_CAP=3.00`

Real mode: артефакты (dev perf)
- `SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS=500` — частота записи `last_tick.json`.
- `SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS=5000` — частота `sync_artifacts` (индексация артефактов в БД).

Рекомендация для realistic-v2 (чтобы выйти на целевые суммы 100–500 UAH):
- запускать с `SIMULATOR_REAL_AMOUNT_CAP>=500`.

### 1.11. Simulator UI: demo/debug режим только через backend

Решение: убрать offline demo pipeline (плейлисты JSON + плеер на фронте) и перейти к **backend-driven demo/debug**.

- UI получает события только через backend SSE (единый контракт/пайплайн визуализации).
- Демо-кнопки (Single TX / Run Clearing) реализуются как backend actions, которые эмитят те же `tx.updated` / `clearing.*`.
- Спецификация: [simulator/backend/backend-driven-demo-mode-spec.md](simulator/backend/backend-driven-demo-mode-spec.md)

---

## 2. Дефолты и лимиты

### 2.1. Таймауты 2PC

| Параметр | Дефолт | Диапазон |
|----------|--------|----------|
| `protocol.transaction_timeout_seconds` | 10 | 5–30 |
| `protocol.prepare_timeout_seconds` | 3 | 1–10 |
| `protocol.commit_timeout_seconds` | 5 | 2–15 |
| `protocol.lock_ttl_seconds` | 60 | 30–300 |

### 2.2. Маршрутизация

| Параметр | Дефолт | Диапазон |
|----------|--------|----------|
| `routing.max_path_length` | 6 | 3–10 |
| `routing.max_paths_per_payment` | 3 | 1–10 |
| `routing.path_finding_timeout_ms` | 500 | 100–2000 |
| `routing.multipath_mode` | `limited` | limited, full |

### 2.3. Клиринг

| Параметр | Дефолт | Диапазон |
|----------|--------|----------|
| `clearing.enabled` | true | true/false |
| `clearing.trigger_cycles_max_length` | 4 | 3–6 |
| `clearing.periodic_cycles_max_length` | 6 | 4–8 |
| `clearing.min_clearing_amount` | 1.00 | 0.01–100 |
| `clearing.max_cycles_per_run` | 100 | 10–1000 |
| `clearing.trigger_interval_seconds` | 0 (сразу) | 0–60 |
| `clearing.periodic_interval_minutes` | 60 | 5–1440 |

### 2.4. Лимиты

| Параметр | Дефолт | Диапазон |
|----------|--------|----------|
| `limits.default_trust_line_limit` | 1000.00 | 0–∞ |
| `limits.max_trust_line_limit` | 100000.00 | 1000–∞ |
| `limits.max_payment_amount` | 50000.00 | 100–∞ |
| `limits.daily_payment_limit` | 100000.00 | 1000–∞ |

### 2.5. Feature Flags

| Параметр | Дефолт | Описание |
|----------|--------|----------|
| `feature_flags.multipath_enabled` | true | Limited multipath включён |
| `feature_flags.full_multipath_enabled` | false | Full mode для бенчмарков |
| `feature_flags.inter_hub_enabled` | false | Межхабовое взаимодействие |

---

## 3. Пилотный размер (проектирование)

| Метрика | Целевое значение |
|---------|------------------|
| **Участники** | 50–200 |
| **Транзакции/день** | до 1000 |
| **Пиковая нагрузка** | 5–10 tx/sec |
| **Эквивалентов** | 1–3 |

---

## Связанные документы

- [config-reference.md](config-reference.md) — полный реестр параметров с описанием
- [02-protocol-spec.md](02-protocol-spec.md) — спецификация протокола
- [03-architecture.md](03-architecture.md) — архитектура системы
- [admin/specs/archive/admin-console-minimal-spec.md](admin/specs/archive/admin-console-minimal-spec.md) — спецификация админки
