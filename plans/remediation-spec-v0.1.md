# GEO v0.1 — Спецификация доработок по результатам code review (90/90)

_Дата: 2026-01-04_
_Основано на: [plans/code-review-report-v0.1.md](plans/code-review-report-v0.1.md), [plans/phase1-patch-set.md](plans/phase1-patch-set.md)_

## 0) Контекст и цель документа

Этот документ задаёт **полную спецификацию доработок** по подтверждённым замечаниям из code review (матрица 90/90), а также правила закрытия пунктов со статусами **NOT CONFIRMED** и **SPEC MISMATCH**.

Ключевые принципы:
- **Протокол первичен**: форматы сообщений и инварианты берём из [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md).
- **MVP-границы первичны**: не добавляем межхабовость, споры, KYC и др. отложенные функции (см. [docs/ru/03-architecture.md](docs/ru/03-architecture.md) и [docs/ru/09-decisions-and-defaults.md](docs/ru/09-decisions-and-defaults.md)).
- **Уточнения и исправления не должны менять смысл модели**: TrustLines/долги/платежи/клиринг должны оставаться совместимыми с протоколом.

## 1) Источники истины и правила разрешения конфликтов

### 1.1. Приоритет документов

1. [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) — нормативная спецификация протокола (routing, 2PC, idempotency, multipath atomicity, clearing).
2. [api/openapi.yaml](api/openapi.yaml) — контракт REST API.
3. Реализация (код) — должна соответствовать (1) и (2).
4. [docs/ru/04-api-reference.md](docs/ru/04-api-reference.md) — справочник; при конфликте с (2) и (1) должен быть обновлён.

### 1.2. Как закрываем статусы из матрицы

- **CONFIRMED / PARTIAL** → обязателен фикс в коде (или фиксация в конфиге/миграции/тестах), критерии приёмки ниже.
- **NOT CONFIRMED** → код не меняем (кроме теста/дока, если требуется закрепить отсутствие проблемы). В PR явно фиксируем “закрыто как неактуально/не воспроизводится”.
- **SPEC MISMATCH** → выбираем источник истины (протокол и OpenAPI), затем:
  - либо правим код под контракт,
  - либо правим OpenAPI/документацию под реализованный (если это не ломает протокол),
  - либо вводим версионирование API (v1/v2) — **только если невозможно безболезненно**.

## 2) Гарантии соответствия целям проекта (не нарушаем “идею GEO”)

Доработки из этой спецификации:
- Укрепляют **2PC-модель**, идемпотентность и восстановление согласно разделу 6/9 протокола.
- Приводят routing и multipath к MVP-базлайну: **limited multipath (k-shortest paths, до 3 путей)**; full multipath остаётся **экспериментальным** и включается только через feature flag (см. [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) раздел 6.3.5 и [docs/ru/09-decisions-and-defaults.md](docs/ru/09-decisions-and-defaults.md)).
- Делают клиринг безопасным относительно платежных резервов и транзакционных конфликтов.
- Не добавляют новые продуктовые сущности (KYC, disputes, inter-hub), не расширяют UX.

## 3) Область работ (Scope)

### 3.1. Входит

- Платежи: состояние, prepare/commit/abort, TTL locks, idempotency, восстановление.
- Маршрутизация: формула available_credit, учёт резервов (pending locks), k-shortest paths, limited multipath split, конфиг лимитов.
- Клиринг: конфликт с prepare locks, безопасные транзакционные границы, политика auto_clearing.
- API контракт: приведение схем/пагинации/фильтров/авторизации к OpenAPI и протоколу.
- БД: ограничения/индексы, миграции, правила удаления (ON DELETE), уникальности, целостность.
- Эксплуатация: корректная конфигурация пула/изоляции, базовая наблюдаемость (логирование/метрики), без “золота”.

### 3.2. Не входит

- Межхабовое взаимодействие (раздел 8 протокола) — **вне MVP**.
- Полноценный full multipath как “оптимальный max-flow” — только как эксперимент под флагом и в рамках лимитов.
- UI/админка/новые страницы.
- Redis/внешние блокировки — только если это уже предусмотрено стеком; на v0.1 все критические гарантии делаем на Postgres транзакциях.

## 4) Термины и инварианты (нормативные)

### 4.1. Формула доступного кредита

Для ребра (A→B, E):

$$available\_credit(A\to B,E)=limit(A\to B,E)-debt(B\to A,E)$$

См. [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) раздел 6.3.1.

### 4.2. Атомарность multipath

Инвариант: multi-path платёж либо коммитится по всем маршрутам, либо абортится по всем (см. 6.3.6 протокола). Частичный commit запрещён.

### 4.3. Идемпотентность

Любая операция с одинаковым `tx_id` должна давать одинаковый результат (см. раздел 9.1 протокола).

### 4.4. Таймауты и TTL

- PREPARE timeout, COMMIT timeout и общий timeout — см. [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) 6.9 и 9.2.
- Locks должны иметь TTL и очищаться восстановлением/джобой (9.3.2).

## 5) Работы по пакетам (соответствует Phase 1 Patch-Set)

Ниже — **спецификация**, а не “только список PR”. Каждый пакет содержит: назначение, изменения, критерии приёмки, риски.

### PR-1: 2PC Protocol Fix

**Покрывает (из Phase 1):** CORE-C1, CORE-C2, CORE-C4, CORE-H5, CORE-M1, CORE-M3.

**Нормативные требования:**
- 6.5–6.7: PREPARE/COMMIT/ABORT
- 9.1–9.3: idempotency + recovery

**Функциональные требования:**
1. `prepare()` **не должен** фиксировать транзакцию как “завершённую” автоматически; prepare — это резервирование.
2. `commit()` обязан проверять, что:
   - транзакция в состоянии, допускающем commit,
   - все required locks существуют,
   - `expires_at > now()` (TTL не истёк) для каждого lock.
3. `abort()` должен быть идемпотентным и очищать резервы.
4. Все переходы состояний должны быть строго определены (NEW → ROUTED → PREPARE_IN_PROGRESS → COMMITTED/ABORTED) и фиксироваться в БД.
5. Повтор `commit(tx_id)` после успешного commit возвращает тот же результат без повторного применения долгов.
6. Ошибки уровня deadlock/serialization должны иметь retry с ограничением попыток.
7. Идемпотентность инициирования платежа:
   - если используется `idempotency_key`, повторный запрос должен вернуть существующий `tx_id`/результат и не создавать дубликаты.

**Критерии приёмки:**
- `commit()` отклоняет просроченные locks.
- Повторный `commit` не меняет state и долги.
- После crash/restart есть механизм, который переводит подвисшие транзакции в ABORTED (при превышении timeout) и очищает locks (см. PR-13/Recovery).

### PR-2: Graph Router Enhancement

**Покрывает:** CORE-C3, CORE-H1, CORE-H2, CORE-H8.

**Нормативные требования:** 6.3.1–6.3.5.

**Функциональные требования:**
1. Реализовать **limited multipath** по протоколу:
   - поиск k путей (по умолчанию k=3, лимит конфигом)
   - ограничение `max_hops` (по умолчанию 6, лимит конфигом)
2. Формула capacity/available_credit должна соответствовать 6.3.1.
3. Routing обязан учитывать **pending reservations** (prepare locks) при расчёте доступной ёмкости.
4. Учитывать `can_be_intermediate` (policy) при выборе промежуточных узлов.
5. Политика таймаутов и budget: routing должен завершаться в `routing.path_finding_timeout_ms`.

**Критерии приёмки:**
- На одном и том же графе при наличии активных locks routing уменьшает доступные capacity.
- Возвращается 1..k маршрутов, суммарно покрывающих amount, либо корректный отказ `InsufficientCapacity`.

### PR-3: Clearing Conflict Prevention

**Покрывает:** CORE-C5, CORE-H6.

**Нормативные требования:** раздел 7 + согласование с 6 (резервы) и 9 (восстановление).

**Функциональные требования:**
1. Клиринг не должен модифицировать долги, которые “задействованы” активными prepare locks (иначе нарушение атомарности и консистентности).
2. Для авто-клиринга нужно соблюдать `policy.auto_clearing` для всех участников цикла (7.4.1).
3. В случае конфликта (lock найден, или deadlock/serialization) — безопасный skip/retry по лимиту.

**Критерии приёмки:**
- Если существует prepare lock на edge/участника, клиринг для цикла не применяется.
- При конкурентном платеже клиринг либо откладывается, либо корректно откатывается.

### PR-4: PaymentResult Schema Alignment

**Покрывает:** API-C1, API-H4.

**Нормативные требования:** OpenAPI + протокольная модель `PAYMENT` (6.4).

**Функциональные требования:**
1. Привести `PaymentResult` (и ответы endpoints) к OpenAPI.
2. Поля должны включать минимум: `from`, `to`, `equivalent`, `amount/total_amount`, `routes`, `created_at`, `committed_at` (если committed), `status/state`, `tx_id`.
3. Подписи/сигнатуры: если OpenAPI/протокол требуют — вернуть/принимать; если это MVP-упрощение, документировать и синхронизировать OpenAPI.

**Критерии приёмки:**
- Генерация клиента по OpenAPI не ломается.
- Интеграционные тесты покрывают контракт ответа.

### PR-5: DB Constraints & Indices

**Покрывает:** DB-C1, DB-C3, DB-H2, DB-H3, DB-H6, DB-H9, DB-H10.

**Нормативные требования:** инварианты долгов/целостности из архитектуры (integrity checker) и необходимость предотвратить двунаправленные дубли.

**Функциональные требования:**
1. Долги: исключить одновременное существование A→B и B→A в одном эквиваленте как независимых записей, если модель предполагает единственную направленность (вариант реализации — канонизация пары или CHECK).
2. `participants.public_key` должен быть уникальным.
3. TrustLine policy должен включать `max_hop_usage` (если это часть протокола/архитектуры) и иметь индексы для типовых запросов.
4. Добавить/уточнить FK ON DELETE правила там, где это не ломает аудит.

**Критерии приёмки:**
- Миграция проходит на Postgres.
- Констрейнты реально предотвращают “битые” состояния.

### PR-6: Session & Pool Configuration

**Покрывает:** DB-C2, DB-H4, DB-H5.

**Функциональные требования:**
1. Настроить пул соединений (pool_size/max_overflow) через конфиг.
2. Включить `pool_pre_ping`.
3. Определить изоляцию для критических операций (минимум: отсутствие “грязных” чтений; для конкурирующих платежей/клиринга — предсказуемые блокировки).

**Критерии приёмки:**
- Под нагрузкой нет лавинообразного открытия соединений.
- Health-check БД не деградирует при “битых” соединениях.

### PR-7: API Payments Hardening

**Покрывает:** API-H3, API-H5, API-H6, API-H8, API-L1.

**Функциональные требования:**
1. Привести `list_payments` к контракту: фильтры/пагинация (page/per_page либо строго по OpenAPI).
2. Убрать `bare except`, заменить на конкретные исключения и валидируемые ошибки.
3. Добавить проверку прав доступа на чтение/листинг платежей.

**Критерии приёмки:**
- Нельзя получить чужой платёж без прав.
- Пагинация соответствует OpenAPI.

### PR-8: Clearing Schema Fix

**Покрывает:** API-H7.

**Функциональные требования:**
1. В API клиринга идентификаторы участников должны соответствовать протоколу (PID, строка), а не внутренним UUID (если OpenAPI/протокол задают PID).
2. Сервис клиринга возвращает PID в результатах поиска циклов.

### PR-9: TrustLine Service Hardening

**Покрывает:** CORE-H7, API-M3, API-M6.

**Функциональные требования:**
1. При закрытии линии доверия проверять отсутствие долга в обоих направлениях в рамках принятой модели долгов.
2. В API trustlines: default direction = `all` (если это соответствует документации) и валидация `limit`.

### PR-10: Rate Limiting & Auth

**Покрывает:** API-H9, CORE-M7, API-L3.

**Функциональные требования:**
1. Rate limiting на чувствительные endpoints (auth/payment) с конфигом.
2. Token blacklist/инвалидация — только если предусмотрено архитектурой auth.
3. Логирование попыток авторизации без утечки секретов.

### PR-11: Multi-path Payment Support

**Покрывает:** CORE-H3, CORE-H4.

**Нормативные требования:** 6.3.4–6.3.6.

**Функциональные требования:**
1. Реализовать split суммы по маршрутам (limited multipath).
2. Координация multipath должна быть атомарной (prepare_all → commit_all или abort_all).
3. Корректно вести `reserved_usage`/учёт резервов.

### PR-12: DB Schema Enhancements

**Покрывает:** DB-M5, DB-H13, DB-H7, DB-M4, DB-M7, DB-M9.

**Функциональные требования:**
1. `idempotency_key` (у транзакций или платежей) + уникальный индекс.
2. Частичный индекс для активных транзакций, если это ускоряет recovery и выборки.
3. Эквиваленты: `symbol`/валидаторы code.
4. Prepare locks: типизация `lock_type` (enum) если есть разные виды резервов.
5. Config: версия/ревизия для воспроизводимости.

### PR-13: Observability & Quality

**Покрывает:** CORE-L1..CORE-L6, CORE-M6.

**Функциональные требования:**
1. Структурированные логи на ключевых операциях (payment prepare/commit/abort, routing, clearing).
2. Минимальные метрики (duration, counts, failure reasons) — строго те, что помогают подтвердить SLA/таймауты из протокола.
3. Убрать “магические числа” в пользу конфигурации.
4. Recovery-процедуры:
   - при старте: найти зависшие транзакции и завершить по правилам таймаутов (9.3.1)
   - периодически: чистить устаревшие locks (9.3.2)

## 6) Трассировка “90/90 → work packages”

Источник списка пунктов — матрица в [plans/code-review-report-v0.1.md](plans/code-review-report-v0.1.md).

Правило: каждый пункт должен быть отнесён к одному из:
- PR-1..PR-13 (фикс в коде/миграциях/тестах)
- DOC-1 (исправление документации/OpenAPI)
- CLOSE-1 (закрыть как NOT CONFIRMED с тестом-ограждением)

Дополнительно:
- DEFER-1 (осознанно отложить за рамки MVP, но зафиксировать причины и условия возврата)

### 6.1. DOC-1: Синхронизация документации

**Задачи:**
- Если [docs/ru/04-api-reference.md](docs/ru/04-api-reference.md) описывает envelope `{success,data}` и это расходится с реальным API/OpenAPI — привести к одному стандарту.
- Зафиксировать, что именно является “каноническим”: OpenAPI.

**Критерии приёмки:**
- Документация не противоречит OpenAPI.

### 6.2. CLOSE-1: Закрытие NOT CONFIRMED

**Задачи:**
- На каждый NOT CONFIRMED пункт добавить короткое обоснование в PR description (или отдельный раздел отчёта) и при необходимости тест/линтер/типовую проверку.

### 6.3. Матрица трассировки 90/90

Ниже — формальная трассировка каждого ID из отчёта к действию.

Обозначения disposition:
- **IMPLEMENT**: исправляется в указанном PR.
- **DOC**: исправляется документацией/OpenAPI (DOC-1).
- **CLOSE**: закрывается как NOT CONFIRMED (CLOSE-1).
- **DEFER**: отложено за рамки MVP (DEFER-1) — не противоречит протоколу, но не требуется для v0.1.

#### API Layer (23/23)

| ID | Статус (верификация) | Disposition | Package |
|---|---|---|---|
| API-C1 | CONFIRMED | IMPLEMENT | PR-4 |
| API-H1 | SPEC MISMATCH | DOC | DOC-1 |
| API-H2 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| API-H3 | CONFIRMED | IMPLEMENT | PR-7 |
| API-H4 | CONFIRMED | IMPLEMENT | PR-4 |
| API-H5 | CONFIRMED | IMPLEMENT | PR-7 |
| API-H6 | CONFIRMED | IMPLEMENT | PR-7 |
| API-H7 | CONFIRMED | IMPLEMENT | PR-8 |
| API-H8 | CONFIRMED | IMPLEMENT | PR-7 |
| API-H9 | CONFIRMED | IMPLEMENT | PR-10 |
| API-M1 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| API-M2 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| API-M3 | CONFIRMED | IMPLEMENT | PR-9 |
| API-M4 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| API-M5 | PARTIAL | IMPLEMENT | PR-7 |
| API-M6 | PARTIAL | IMPLEMENT | PR-9 |
| API-M7 | CONFIRMED | IMPLEMENT | PR-5 |
| API-M8 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| API-L1 | CONFIRMED | IMPLEMENT | PR-7 |
| API-L2 | CONFIRMED | IMPLEMENT | PR-13 |
| API-L3 | CONFIRMED | IMPLEMENT | PR-10 |
| API-L4 | CONFIRMED | IMPLEMENT | PR-13 |
| API-L5 | PARTIAL | DEFER | DEFER-1 |

#### Core Business Logic (26/26)

| ID | Статус (верификация) | Disposition | Package |
|---|---|---|---|
| CORE-C1 | PARTIAL | IMPLEMENT | PR-1 |
| CORE-C2 | CONFIRMED | IMPLEMENT | PR-1 |
| CORE-C3 | PARTIAL | IMPLEMENT | PR-2 |
| CORE-C4 | CONFIRMED | IMPLEMENT | PR-1 |
| CORE-C5 | CONFIRMED | IMPLEMENT | PR-3 |
| CORE-H1 | CONFIRMED | IMPLEMENT | PR-2 |
| CORE-H2 | CONFIRMED | IMPLEMENT | PR-2 |
| CORE-H3 | CONFIRMED | IMPLEMENT | PR-11 |
| CORE-H4 | CONFIRMED | IMPLEMENT | PR-11 |
| CORE-H5 | PARTIAL | IMPLEMENT | PR-1 |
| CORE-H6 | PARTIAL | IMPLEMENT | PR-3 |
| CORE-H7 | CONFIRMED | IMPLEMENT | PR-9 |
| CORE-H8 | CONFIRMED | IMPLEMENT | PR-2 |
| CORE-M1 | CONFIRMED | IMPLEMENT | PR-1 |
| CORE-M2 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| CORE-M3 | CONFIRMED | IMPLEMENT | PR-1 |
| CORE-M4 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| CORE-M5 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| CORE-M6 | CONFIRMED | IMPLEMENT | PR-13 |
| CORE-M7 | CONFIRMED | IMPLEMENT | PR-10 |
| CORE-L1 | CONFIRMED | IMPLEMENT | PR-13 |
| CORE-L2 | CONFIRMED | IMPLEMENT | PR-13 |
| CORE-L3 | CONFIRMED | IMPLEMENT | PR-13 |
| CORE-L4 | CONFIRMED | IMPLEMENT | PR-13 |
| CORE-L5 | CONFIRMED | IMPLEMENT | PR-13 |
| CORE-L6 | CONFIRMED | IMPLEMENT | PR-13 |

#### Data Layer (41/41)

| ID | Статус (верификация) | Disposition | Package |
|---|---|---|---|
| DB-C1 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-C2 | PARTIAL | IMPLEMENT | PR-6 |
| DB-C3 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-H1 | PARTIAL | IMPLEMENT | PR-12 |
| DB-H2 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-H3 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-H4 | CONFIRMED | IMPLEMENT | PR-6 |
| DB-H5 | CONFIRMED | IMPLEMENT | PR-6 |
| DB-H6 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-H7 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-H8 | CONFIRMED | DOC | DOC-1 |
| DB-H9 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-H10 | CONFIRMED | IMPLEMENT | PR-5 |
| DB-H11 | CONFIRMED | DEFER | DEFER-1 |
| DB-H12 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| DB-H13 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M1 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| DB-M2 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| DB-M3 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| DB-M4 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M5 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M6 | CONFIRMED | DEFER | DEFER-1 |
| DB-M7 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M8 | CONFIRMED | IMPLEMENT | PR-13 |
| DB-M9 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M10 | CONFIRMED | IMPLEMENT | PR-13 |
| DB-M11 | PARTIAL | IMPLEMENT | PR-12 |
| DB-M12 | PARTIAL | IMPLEMENT | PR-13 |
| DB-M13 | CONFIRMED | IMPLEMENT | PR-13 |
| DB-M14 | CONFIRMED | DEFER | DEFER-1 |
| DB-M15 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M16 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-M17 | CONFIRMED | DEFER | DEFER-1 |
| DB-M18 | NOT CONFIRMED | CLOSE | CLOSE-1 |
| DB-L1 | PARTIAL | DEFER | DEFER-1 |
| DB-L2 | CONFIRMED | IMPLEMENT | PR-12 |
| DB-L3 | CONFIRMED | DOC | DOC-1 |
| DB-L4 | CONFIRMED | IMPLEMENT | PR-13 |
| DB-L5 | CONFIRMED | DEFER | DEFER-1 |
| DB-L6 | CONFIRMED | DEFER | DEFER-1 |
| DB-L7 | CONFIRMED | DEFER | DEFER-1 |

## 7) Backward compatibility и миграции

- Изменения БД идут через alembic миграции.
- Для API: если меняются response shapes — либо сохраняем старые поля как deprecated (если уже были), либо поднимаем minor-изменение в рамках v1 только если клиенты ещё не существуют; иначе — обсуждаем v2.

## 8) Тестирование и критерии качества

Минимальный набор тестов (MVP):
- Интеграционные: платеж (single-path), платеж (multipath 2–3 пути), abort на таймауте, commit повторно (idempotent), клиринг при отсутствии locks, клиринг при наличии locks (skip).
- Юнит: формула capacity, split_payment, проверка can_be_intermediate.
- Контрактные: ответы API соответствуют OpenAPI (pydantic schema / snapshot).

## 9) Риски и меры

- **Конкурентные дедлоки**: решаем retry + упорядочивание блокировок + индексы.
- **Ломающий API контракт**: решаем синхронизацией OpenAPI/схем и аккуратной миграцией.
- **Рост сложности routing**: ограничиваем budget/таймауты, строго limited multipath по дефолту.

## 10) Открытые решения (требуют явного выбора)

1. Что является “внешним ID” участника в API: PID (строка) или UUID.
   - По протоколу: PID.
   - Если внутренние UUID уже используются, требуется маппинг/dual-field стратегия.
2. Envelope формата ответов: plain JSON vs `{success,data}`.
   - Рекомендация: OpenAPI как истина; привести docs и код к нему.

---

## Приложение A: Ссылки на протокольные требования

- Платежи: [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) раздел 6 (routing, prepare/commit/abort, multipath atomicity)
- Клиринг: [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) раздел 7
- Восстановление/идемпотентность: [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) раздел 9
- MVP дефолты: [docs/ru/09-decisions-and-defaults.md](docs/ru/09-decisions-and-defaults.md)
