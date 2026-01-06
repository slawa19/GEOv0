# GEO v0.1 — Проверка соответствия кода идее/протоколу/спецификации

_Дата: 2026-01-06_

## 0) Что проверено и по каким источникам

**Источники истины (как в remediation-spec):**
1) [docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md) — нормативный протокол
2) [api/openapi.yaml](api/openapi.yaml) — контракт REST API
3) Реализация (код)
4) [docs/en/04-api-reference.md](docs/en/04-api-reference.md) / README — справочно, при конфликте должны быть синхронизированы

**Код, который просмотрен точечно по критическим потокам:**
- API: [app/api/v1/auth.py](app/api/v1/auth.py), [app/api/v1/participants.py](app/api/v1/participants.py), [app/api/v1/trustlines.py](app/api/v1/trustlines.py), [app/api/v1/payments.py](app/api/v1/payments.py), [app/api/v1/clearing.py](app/api/v1/clearing.py), [app/api/v1/balance.py](app/api/v1/balance.py)
- Core: [app/core/auth/crypto.py](app/core/auth/crypto.py), [app/core/auth/service.py](app/core/auth/service.py), [app/core/participants/service.py](app/core/participants/service.py), [app/core/trustlines/service.py](app/core/trustlines/service.py), [app/core/payments/service.py](app/core/payments/service.py), [app/core/payments/router.py](app/core/payments/router.py), [app/core/payments/engine.py](app/core/payments/engine.py), [app/core/clearing/service.py](app/core/clearing/service.py), [app/core/recovery.py](app/core/recovery.py), [app/core/integrity.py](app/core/integrity.py)
- DB модели: [app/db/models/transaction.py](app/db/models/transaction.py), [app/db/models/prepare_lock.py](app/db/models/prepare_lock.py), [app/db/models/debt.py](app/db/models/debt.py), [app/db/models/trustline.py](app/db/models/trustline.py)

---

## 1) Короткое резюме

- **Общий каркас MVP соответствует идее GEO**: доверительные линии → долговые рёбра → платежи по графу → клиринг циклов.
- **Сильные места**: есть 2PC-подобная модель (prepare locks + commit), учёт reserved capacity в роутинге, фоновые recovery/cleanup, базовые метрики и rate limiting.
- **Критические несоответствия протоколу**: PID (алгоритм), формат подписи/канонизация, политика auto_clearing, а также конкуррентность/резервы на промежуточных рёбрах (риск превышения лимитов при параллельных транзакциях).
- **Документация (EN API reference, README) не синхронизирована** с OpenAPI/кодом по ряду ключевых моментов.

### 1.1. Проверка релевантности замечаний (по факту кода)

Ниже — валидация типовых утверждений из внешних “аудит-сводок”, чтобы в план доработок попадало только подтверждённое.

**Подтверждено / актуально:**
- **Race/oversubscription при конкурентных платежах**: `PrepareLock` учитывается, но нет строгой сериализации конкурентных `prepare()` на одном сегменте — риск превышения лимита остаётся (см. P0-3).
- **Клиринг не проверяет `policy.auto_clearing`**: согласие на auto-clearing не валидируется перед исполнением цикла (см. P0-4).
- **Подписи отсутствуют для TrustLines/Clearing**: mutating-операции линий доверия и клиринга авторизуются через JWT, но не требуют клиентской Ed25519-подписи как в протоколе (см. P1-5).
- **Integrity: отсутствует zero-sum проверка**: в integrity checkpoint сейчас нет sanity-check уровня эквивалента вида $\sum credits = \sum debts$ (или $\sum net\_balance = 0$), что подтверждается текущим содержимым invariants_status.

**Не подтверждено (в текущем коде уже сделано иначе):**
- **"Router/Engine не учитывают активные prepare_locks"** — не подтверждено: роутер строит `reserved_map` из активных `PrepareLock` и вычитает из capacity; engine на prepare также учитывает `reserved_usage` по активным locks.
- **"Clearing не блокируется при активных payment locks"** — не подтверждено: clearing исключает рёбра/пары участников, затронутые активными `PrepareLock` (best-effort защита).
- **"Нет валидации типов токена (access/refresh)"** — не подтверждено: `decode_token()` требует claim `type` и по умолчанию ожидает `access`.
- **"Нужно настроить connection pooling"** — уже сделано: пул SQLAlchemy конфигурируется через settings (pool_pre_ping/pool_size/max_overflow и т.д.).

**Частично / требует аккуратной формулировки:**
- **"commit инициируется преждевременно"**: в MVP `prepare→commit` происходит в рамках одного API-вызова. Это упрощение относительно сетевого 2PC между узлами, но в текущей hub-модели не является само по себе нарушением атомарности. Реальная проблема — конкурирующий `prepare()` без сериализации на сегментах (P0-3).

### 1.2. Сверка с предоставленным отчётом "Анализ кода GEO v0.1 на соответствие спецификации" (2026-01-06)

Ниже — что в вашем отчёте **подтверждается кодом**, а что требует корректировки формулировок (чтобы список доработок был точным).

**Подтверждено / релевантно:**
- **`/auth/refresh` отсутствует в коде**: в [app/api/v1/auth.py](app/api/v1/auth.py) есть только `POST /challenge` и `POST /login`. При этом refresh-токены **генерируются** (см. [app/core/auth/service.py](app/core/auth/service.py) + [app/utils/security.py](app/utils/security.py)), то есть функциональность “refresh token как сущность” есть, но публичного endpoint нет.
- **Integrity инварианты реализованы минимально**: в [app/core/integrity.py](app/core/integrity.py) `invariants_status` пока не включает zero-sum и прочие протокольные проверки.
- **Zero-sum отсутствует** — подтверждено (см. выше).
- **Equivalent.code валидируется только на upper-case**: в [app/db/models/equivalent.py](app/db/models/equivalent.py) нет ограничения на алфавит `A-Z0-9_`.
- **Состояния `Transaction` шире, чем базовая протокольная PAYMENT-стейтмашина**: дополнительные состояния в модели требуют документирования (и/или уточнения что это кросс-типовая модель для разных tx).
- **`metadata` для Equivalent без структурной валидации** — сейчас принимается как JSON.

**Опровергнуто / нужно исправить в отчёте:**
- **"Нет rate limiting на API"** — не подтверждено: глобальная зависимость `Depends(deps.rate_limit)` подключена на [app/api/router.py](app/api/router.py), реализация есть в [app/api/deps.py](app/api/deps.py) (in-memory или Redis, если включён).
- **"Периодические проверки целостности не реализованы"** — не подтверждено: в [app/main.py](app/main.py) есть фоновой `_integrity_loop()` с интервалом `INTEGRITY_CHECKPOINT_INTERVAL_SECONDS` (по умолчанию 300s) + запуск один раз при старте.
- **"Cleanup stale locks не реализован"** — не подтверждено: recovery-loop в [app/core/recovery.py](app/core/recovery.py) удаляет `PrepareLock` с `expires_at <= now` и абортит “зависшие” payment-транзакции по таймауту.
- **"Нет валидации signature в payment"** — не подтверждено: в [app/core/payments/service.py](app/core/payments/service.py) подпись `request.signature` проверяется через `verify_signature(...)`.

**Частично / формулировка должна быть точнее:**
- **"Expired locks не влияют на capacity"**: при построении графа роутер учитывает только `PrepareLock.expires_at > now`, то есть истёкшие locks *не* должны снижать capacity. Реальный риск/особенность здесь другая: роутинг работает по снимку, и если lock истечёт/появится во время роутинга, возможны ложные отказы (но безопасность обеспечивается TTL-check в commit).
- **"Graph cache без инвалидации"**: в коде есть cache-логика, но по дефолту `ROUTING_GRAPH_CACHE_TTL_SECONDS = 0` (кеш фактически выключен). Релевантно только если TTL будет включён.

---

## 2) Критические несоответствия (P0)

### P0-1: Алгоритм PID не соответствует протоколу

**Протокол (норма):** `PID = base58(sha256(public_key))` ([docs/ru/02-protocol-spec.md](docs/ru/02-protocol-spec.md))

**Реализация:** `PID = base64url(public_key_bytes) без padding` ([app/core/auth/crypto.py](app/core/auth/crypto.py))

**Почему это важно:**
- PID становится «производным идентификатором формата версии реализации», а не протокола.
- Любая федерация/интероперабельность в будущем будет ломаться без явного versioning.

**Минимальные варианты решения (без overengineering):**
1) **Привести код к протоколу** и сделать миграцию данных (participants.pid + все FK/связи/внешние ссылки), с переходным периодом (accept old pid as alias).
2) **Если оставляем текущий PID как MVP-упрощение** — это должно быть зафиксировано в протоколе как `PIDv0` (или в decisions/defaults) и везде единообразно (README/ru/en/spec/api).

---

### P0-2: Канонизация/подписи сообщений не соответствуют протоколу

**Протокол:** подписи должны делаться по canonical JSON (Appendix A), и модель подписи/`signatures[]` описана явно.

**Реализация:**
- Регистрация: строковый message `geo:participant:create:{display_name}:{type}:{public_key}` ([app/core/participants/service.py](app/core/participants/service.py))
- Платёж: строковый message `geo:payment:request:{sender_pid}:{json.dumps(payload, sort_keys=True)}` ([app/core/payments/service.py](app/core/payments/service.py))
- `Transaction.signatures` в БД есть, но практически не заполняется/не используется как протокольный механизм.

**Риски:**
- «Подпись» становится API-специфичной, а не протокольной (невозможно валидировать по протоколу).
- Нельзя гарантировать совместимость клиентских реализаций, которые будут следовать протоколу.

**Минимальные варианты решения:**
- Ввести один общий `canonical_json(payload)` (как в протоколе), и привести все подписи к нему (минимум: ParticipantCreate + PaymentCreate).
- Зафиксировать форматы подписываемых payload в OpenAPI и docs (чтобы клиенты могли воспроизводимо подписывать).

---

### P0-3: Конкурентные платежи могут превысить лимиты на промежуточных рёбрах

**Суть:** `PrepareLock` действительно учитывается при расчёте capacity (и в роутере, и в engine), но **нет строгой сериализации** конкурирующих `prepare()` на одном и том же сегменте. Из-за этого два параллельных `prepare()` могут одновременно пройти проверку capacity до того, как locks друг друга станут видимыми.

Что уже есть:
- `prepare()`/`prepare_routes()` создают долгоживущие locks и проверяют reserved_usage.
- API использует Redis lock, но ключ — только по инициатору (`dlock:payment:{current_participant.id}:{equivalent}`) ([app/api/v1/payments.py](app/api/v1/payments.py)).

Почему этого недостаточно:
- Если два разных инициатора делают платежи, оба могут использовать один и тот же промежуточный сегмент `X -> Y`.
- Оба `prepare()` прочитают одинаковое reserved_usage до вставки своих locks и могут оба пройти check.

**Минимальная доработка (без overengineering):**
- В `PaymentEngine.prepare*` добавить **пессимистическую сериализацию** на уровне Postgres (advisory lock) по ключу `(equivalent_id, from_id, to_id)` для каждого сегмента.
  - Это небольшой код, не требует новых таблиц.
  - Даст детерминированную защиту от oversubscription.

Альтернатива (хуже для MVP): глобальные Redis locks для всех сегментов — будет тяжелей поддерживать и сложно отлаживать.

---

### P0-4: Клиринг игнорирует `policy.auto_clearing`

**Протокол/decisions:** auto-clearing требует согласия (default true, но должна быть проверка) ([docs/en/09-decisions-and-defaults.md](docs/en/09-decisions-and-defaults.md), протокол раздел 7).

**Реализация:**
- `ClearingService` не проверяет `auto_clearing` в policy trustlines/участников цикла ([app/core/clearing/service.py](app/core/clearing/service.py)).

**Минимальная доработка:**
- При выборе цикла проверять, что для всех рёбер/участников цикла разрешён auto-clearing (по протоколу: на уровне trustline policy).
- Если нет согласия — не исполнять цикл.

---

## 3) Несоответствия протоколу по безопасности/подписям (P1)

### P1-5: Нет Ed25519-подписей на mutating-операциях TrustLines/Clearing

**Протокол (идея/норма):** операции изменения состояния должны быть подписаны участником (Ed25519), а не только авторизованы токеном.

**Реализация (MVP):**
- Participants: регистрация подписана (proof-of-possession).
- Payments: запрос платежа подписан.
- TrustLines/Clearing: подписи не требуются, контроль — через JWT.

**Минимальный вариант без overengineering:**
- Ввести подпись хотя бы на TrustLine create/update/close (один формализованный payload + canonicalization).
- Для clearing/auto — либо также подписывать (если протокол требует согласие на действие), либо явно зафиксировать как hub-операцию при наличии `auto_clearing=true` (но тогда check auto_clearing обязателен).

## 4) Несоответствия OpenAPI vs код (P1)

### P1-1: `ParticipantCreateRequest.type` обязательный в коде, но не обязательный в OpenAPI

- Код: `type` обязателен в `ParticipantBase` ([app/schemas/participant.py](app/schemas/participant.py))
- OpenAPI: `type` не в required и имеет default `person` ([api/openapi.yaml](api/openapi.yaml))

**Решение:** выбрать одно:
- либо сделать `type` опциональным в коде (default=`person`),
- либо обновить OpenAPI и docs, что `type` обязателен.

---

## 5) Частичные/важные расхождения с протоколом (P1)

### P1-2: 2PC state machine упрощена относительно протокола

- В протоколе/ремедиации фигурируют состояния `NEW → ROUTED → PREPARE_IN_PROGRESS → PREPARED → COMMITTED/ABORTED`.
- В коде `PaymentService` создаёт tx в `NEW`, а `PaymentEngine.prepare*` сразу ставит `PREPARED` ([app/core/payments/service.py](app/core/payments/service.py), [app/core/payments/engine.py](app/core/payments/engine.py)).

**Почему важно:** recovery/observability и идемпотентность проще доказывать со строгими переходами.

**Минимальная доработка:**
- Ввести явные переходы состояний и фиксировать их, не меняя UX (всё равно commit сразу).

---

### P1-3: TrustLine policies реализованы частично

Что есть:
- `can_be_intermediate` учитывается в роутинге ([app/core/payments/router.py](app/core/payments/router.py))

Что отсутствует в критическом пути:
- `blocked_participants`: нет фильтрации путей через запрещённые PID.
- `daily_limit`: нет ограничений оборота.

**Минимальные доработки:**
- Добавить в роутер параметр `forbidden_nodes` (на базе политики) и исключать такие узлы.
- `daily_limit` — можно отложить, но тогда нужно явное документирование как “not enforced in MVP”.

---

### P1-4: Клиринг — алгоритм и результаты отличаются от протокольной модели

- Сейчас `find_cycles()` — эвристический DFS и ограничение `len(cycles) <= 10`, без строгой дедупликации/канонизации циклов.
- `execute_clearing()` создаёт tx типа CLEARING, но payload минимален (cycle debt_id + amount), без протокольных полей.

**Минимальные доработки:**
- Документировать ограничения (сколько циклов ищем/в каком порядке).
- В payload добавить минимум: equivalent, список рёбер как `(debtor_pid, creditor_pid)`, чтобы можно было отлаживать и сверять.

---

## 6) Улучшения, не отражённые в спецификации (отдельный список)

Это вещи, которые **повышают качество/безопасность/эксплуатируемость**, но не являются прямыми требованиями протокола.

- **Логирование фоновых задач без “тихого проглатывания”**: сейчас в [app/main.py](app/main.py) wiring recovery/integrity обёрнут в `try/except: pass`, что может скрыть критические поломки.
- **Явная политика по хранению нулевых долгов**: сейчас Debt rows с `0` оставляются (комментарий в `_apply_flow`), а в протоколе сказано “нулевые записи удаляются”. Лучше выбрать единообразно: либо удалять, либо документировать.
- **Единый механизм canonicalization/signature payload версий**: даже если не полностью протокольно, полезно иметь один файл/модуль “как подписывать запросы” для клиентов.
- **Явные лимиты на поиск циклов/роутинг** вынести в config-reference (сейчас часть есть, часть hardcoded — напр. `len(cycles)>10`, `count>100`).
- **Integrity checks по смысловым инвариантам**: добавить проверку `debt(to→from, E) ≤ limit(from→to, E)` по активным trustlines, и (опционально) неотрицательности/валидности сумм reserved locks.
- **Zero-sum sanity check (дешёвый smoke-test)**: добавить вычисление по эквиваленту $\sum_{rows} amount$ как total_debt и total_credit (они должны совпадать), и/или $\sum net\_balance = 0$.

---

## 7) Список доработок (без overengineering)

### P0 (делать в первую очередь)
1) Принять решение по PID: привести к протоколу или зафиксировать `PIDv0` как исключение + план миграции.
2) Привести подпись запросов к canonical JSON (минимум: register + payment request).
3) Устранить race/oversubscription на сегментах prepare через Postgres advisory locks.
4) Добавить проверку `auto_clearing` перед выполнением clearing.
5) Добавить `POST /auth/refresh` (или явно исключить из MVP) и синхронизировать это между docs/openapi/кодом.

### P1
5) Синхронизировать OpenAPI vs код по `ParticipantCreateRequest.type` (default vs required).
6) Сделать state machine платежа ближе к протоколу (минимум: фиксировать `ROUTED`/`PREPARE_IN_PROGRESS`).
7) Реализовать `blocked_participants` в роутинге (или явно пометить как “not enforced”).
8) Обогатить payload CLEARING для трассировки/аудита.
9) Добавить подпись (Ed25519) для TrustLine create/update/close или явно задокументировать как MVP-исключение (но тогда это SPEC MISMATCH).
10) Добавить integrity-проверку инварианта `debt(to→from,E) ≤ limit(from→to,E)` и отчёт по нарушителям.
11) Реализовать протокольные integrity endpoints (`/integrity/status`, `/integrity/checksum/{equivalent}`, `/integrity/verify`, `/integrity/audit-log`) или зафиксировать, что в MVP доступен только checkpoint в БД.

### P2
12) Определить политику по Debt==0 (удалять или хранить) и синхронизировать с протоколом.
13) Убрать/настроить хардкоды в clearing loop (10 циклов, 100 операций) через config.
14) (Опционально) Стандартизировать идемпотентность для mutating endpoints (не обязательно middleware; достаточно единообразного паттерна + уникальных индексов там, где нужно).
15) Добавить в integrity checkpoint zero-sum проверку по эквиваленту: $\sum credits = \sum debts$ (и/или $\sum net\_balance = 0$) с отчётом в invariants_status.
16) Протокол recovery (сообщения `RECOVERY_QUERY/RECOVERY_RESPONSE`): либо реализовать для межхабового режима, либо явно пометить как out-of-scope для hub-only MVP (чтобы не считалось “пропуском”).

---

## 8) Предложения по улучшению документации

### 7.1. Срочно синхронизировать (чтобы не вводить в заблуждение)
- README vs протокол: описать PID однозначно, с указанием версии/решения (сейчас README совпадает с кодом, но конфликтует с протоколом).
- [docs/en/04-api-reference.md](docs/en/04-api-reference.md) устарел относительно OpenAPI/кода:
  - там есть `GET /auth/challenge?pid=...`, `POST /auth/register`, `POST /auth/refresh`, `/participants/me`, `/participants/search`, которых нет/другие.
  - Внести правки, чтобы он отражал `api/openapi.yaml`.

### 7.2. Детализировать (чтобы клиенты могли реализоваться правильно)
- Добавить раздел “Как подписывать запросы”:
  - какие payload поля включаются,
  - canonicalization,
  - примеры подписи для registration/login/payment.
- Раздел “PID versioning”:
  - текущая формула,
  - целевая формула протокола,
  - миграционный план.

- Раздел “Integrity invariants”:
  - какие инварианты проверяются сейчас,
  - какие добавляем (zero-sum, debt≤limit, lock TTL cleanup),
  - как интерпретировать нарушения.

### 7.3. Зафиксировать MVP-ограничения как явные контрактные гарантии
- Clearing: поиск циклов — пределы, порядок, условия пропуска (locks, auto_clearing).
- 2PC: какие состояния реально используются в MVP и какие гарантии даются (idempotency, TTL).
- Добавить диаграмму state machine (даже простую), чтобы закрыть неоднозначность вокруг «2PC в MVP».

---

## 9) Примечания

Этот аудит сознательно избегает “расширений MVP” (inter-hub, disputes, KYC). Рекомендации сфокусированы на том, чтобы текущая реализация была **строго совместима с протоколом v0.1** и не имела скрытых расхождений, которые потом будут очень дорогими.
