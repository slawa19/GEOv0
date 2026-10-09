# Сценарии симулятора и движок запуска (RU)

Цель документа:
- **Часть 1 (для всех):** простыми словами — что такое сценарий и как его запустить.
- **Часть 2 (для технарей):** техническое устройство — сущности, верхнеуровневые алгоритмы, структура JSON, файлы движка, точки конфигурации.

См. также:
- Индекс симулятора: [README.md](README.md)
- Online-анализ проблем экономики сети (insights/уведомления): [network-economy-analyzer-spec.md](network-economy-analyzer-spec.md)
- Входной формат (schema): [../../../fixtures/simulator/scenario.schema.json](../../../fixtures/simulator/scenario.schema.json)
- Примеры сценариев: [../../../fixtures/simulator/](../../../fixtures/simulator/)

---

## Часть 1 — для пользователей (простыми словами)

### Что такое «сценарий»
Сценарий — это **описание мира**, а не скрипт с заранее прописанными транзакциями.

В сценарии описано:
- **кто участвует** (участники: люди/бизнесы/хабы);
- **какие лимиты доверия** между ними (trustlines = кредитные лимиты);
- **в каких эквивалентах** (UAH/HOUR/…);
- (опционально) группы/профили поведения — как «роли» в экономике.

Дальше сценарий запускает **один и тот же движок симулятора**, который в «тиках» (шаги времени) пытается генерировать платежи/клиринг на основе правил и ограничений.

### Есть два режима запуска
В API/UI есть `mode`:

1) **Fixtures mode**
- Это «демо-режим», который генерирует **визуальные события** (подсветки `tx.updated`, `clearing.done`) без реального прохождения по платежному стеку.
- Нужен, чтобы быстро проверить UI/визуализацию и топологию графа.

2) **Real mode**
- Это режим, где runner делает реальные вызовы в backend (payments/clearing), и события отражают реальные результаты.
- Для реалистичной проверки (конкурентность, локи, таймауты) предпочтительнее Postgres + Redis.

Подробный runbook: [backend/real-mode-runbook.md](backend/real-mode-runbook.md).

### Greenfield и Riverside — это разные сценарии, а не разные «скрипты»
Для каждого набора вроде **Greenfield Village** или **Riverside Town** обычно создаётся:
- отдельная папка с `scenario.json` в [../../../fixtures/simulator/](../../../fixtures/simulator/)
- но **движок один и тот же**

То есть «ещё один скрипт» писать не нужно, если вы не меняете сам алгоритм движка.

### Как добавить/запустить новый сценарий (практика)
Самый простой путь:
1) Скопировать один из примеров, например [../../../fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json](../../../fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json)
2) Поменять `scenario_id` на новый уникальный
3) Отредактировать участников и trustlines
4) Запустить backend + UI, затем выбрать сценарий в UI

Важно про список сценариев в UI:
- По умолчанию UI показывает демо-набор (включая realistic v2).
- Если вы добавили новый сценарий и не видите его в списке, выставьте env:
  - `SIMULATOR_SCENARIO_ALLOWLIST=all`
  - или `SIMULATOR_SCENARIO_ALLOWLIST=greenfield-village-100-realistic-v2,my-new-scenario`

Технически это фильтр в runtime (см. [../../../app/core/simulator/runtime_impl.py](../../../app/core/simulator/runtime_impl.py)).

### Чек‑лист: создать, проверить, запустить (1 страница)

**A) Создать сценарий**
1) Выберите основу:
  - минимальный: [../../../fixtures/simulator/minimal/scenario.json](../../../fixtures/simulator/minimal/scenario.json)
  - “похож на реальный”: [../../../fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json](../../../fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json)
  - realistic v2 (UAH-only): [../../../fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json](../../../fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json)

Примечание для realistic v2:
- чтобы увидеть реалистичные суммы (сотни UAH), запускайте real mode с `SIMULATOR_REAL_AMOUNT_CAP=500` (дефолт `3.00` сохраняет старое поведение).
2) Скопируйте в новую папку:
  - `fixtures/simulator/<your-scenario-id>/scenario.json`
3) Обязательные инварианты (частые ошибки):
  - `trustlines[].from → trustlines[].to` = creditor → debtor (направление нельзя «переворачивать»)
  - у каждого trustline должен быть `equivalent`
  - `trustlines[].equivalent` должен входить в `equivalents[]` (если используете `equivalents[]`)
  - для real mode: `participants[].id` должны существовать в БД (они будут сидироваться)

Практическое правило для ручных платежей (Interact Mode):
- чтобы отправить платёж `From=A` → `To=B`, в графе должен существовать активный trustline **`B → A`**
  (получатель кредитует отправителя; trustline всегда creditor → debtor).
  Пример: «Алиса покупает у Магазина» означает платёж `alice → shop`, поэтому нужен trustline `shop → alice`.

Правила «реалистичных сценариев» (realistic-v2) и чек-лист требований к входным данным:
- `docs/ru/simulator/realistic-scenarios.md`

**B) Проверить формат (schema)**

Формальная схема: [../../../fixtures/simulator/scenario.schema.json](../../../fixtures/simulator/scenario.schema.json)

Практика в репо:
- генератор seed‑сценариев всегда прогоняет проверку формы сценариев по schema

Команда (Windows):

```powershell
./.venv/Scripts/python.exe scripts/generate_simulator_seed_scenarios.py
```

**C) Запустить и посмотреть в UI**

Рекомендуемый быстрый запуск полного стека (Backend + Admin UI + Simulator UI):

```powershell
./scripts/run_full_stack.ps1 -Action start
```

Если нужно пересоздать базу лаунчера (PostgreSQL `geov0_dev_<DbSlug>`) и заново
исполнить рецепт демо‑сообщества:

```powershell
./scripts/run_full_stack.ps1 -Action start -ResetDb -SeedCommunity riverside-town-50
```

Если сценарий не виден в списке UI:
- выставьте `SIMULATOR_SCENARIO_ALLOWLIST=all` (или перечислите нужные id через запятую)

**D) Альтернатива: загрузить сценарий через API (без git‑изменений)**

Control plane поддерживает upload:
- `POST /api/v1/simulator/scenarios` (body: `{ "scenario": { ...scenario.json... } }`)

Загруженные сценарии сохраняются локально в `.local-run/simulator/scenarios/<scenario_id>/scenario.json` (путь по умолчанию; корень состояния задаётся `SIMULATOR_STATE_DIR`, см. `backend/run-storage.md`, раздел 4.2) и подхватываются runtime.

---

## Часть 2 — для технарей (сущности, алгоритмы, структура)

### 2.1 Сущности верхнего уровня

- **Scenario** — входной JSON, который описывает топологию и параметры (см. schema).
- **Participant** — узел графа: `person|business|hub`.
- **TrustLine** — направленный лимит `from → to`.
  - Семантика важная и фиксированная: `from` = creditor, `to` = debtor.
- **Equivalent** — “валюта/единица”, по которой строятся отдельные графы.
- **Run** — экземпляр прогона сценария (имеет `run_id`, `seed`, `tick_index`, `mode`, `intensity_percent`, состояние).
- **Events** — поток событий для UI (SSE): `run_status`, `tx.updated`, `tx.failed`, `clearing.*`, …
- **Snapshot / Metrics / Bottlenecks** — агрегаты, которые UI запрашивает отдельными endpoint’ами.

Модель событий/снапшотов: [backend/simulator-domain-model.md](backend/simulator-domain-model.md).
Online-анализ («что не так с экономикой сети» поверх метрик/бутылочных горлышек): [network-economy-analyzer-spec.md](network-economy-analyzer-spec.md).

Связи сущностей (упрощённо):

```mermaid
classDiagram
  class Scenario {
    +schema_version: string
    +scenario_id: string
    +equivalents: string[]
  }
  class Participant {
    +id: string
    +type: person|business|hub
    +groupId: string
    +behaviorProfileId: string
  }
  class TrustLine {
    +from: string  // creditor
    +to: string    // debtor
    +equivalent: string
    +limit: string
  }
  class Group {
    +id: string
    +label: string
  }
  class BehaviorProfile {
    +id: string
    +props: object
  }

  Scenario "1" o-- "*" Participant : participants
  Scenario "1" o-- "*" TrustLine : trustlines
  Scenario "1" o-- "*" Group : groups
  Scenario "1" o-- "*" BehaviorProfile : behaviorProfiles

  TrustLine --> Participant : from
  TrustLine --> Participant : to
  Participant --> Group : groupId
  Participant --> BehaviorProfile : behaviorProfileId
```

### 2.2 Где живёт движок (основные файлы)

Ключевые точки:
- Runtime “фасад”: [../../../app/core/simulator/runtime_impl.py](../../../app/core/simulator/runtime_impl.py)
- Жизненный цикл run: [../../../app/core/simulator/run_lifecycle.py](../../../app/core/simulator/run_lifecycle.py)
- Реестр сценариев + schema validation: [../../../app/core/simulator/scenario_registry.py](../../../app/core/simulator/scenario_registry.py)
- Fixtures mode генератор событий: [../../../app/core/simulator/fixtures_runner.py](../../../app/core/simulator/fixtures_runner.py)
- Real mode runner: [../../../app/core/simulator/real_runner_impl.py](../../../app/core/simulator/real_runner_impl.py), тик — [../../../app/core/simulator/tick.py](../../../app/core/simulator/tick.py)
- SSE broadcast + replay buffer: [../../../app/core/simulator/sse_broadcast.py](../../../app/core/simulator/sse_broadcast.py)
- Snapshot builder: [../../../app/core/simulator/snapshot_builder.py](../../../app/core/simulator/snapshot_builder.py)
- Artifacts (events.ndjson и др.): [../../../app/core/simulator/artifacts.py](../../../app/core/simulator/artifacts.py)

API слой (control plane): [../../../app/api/v1/simulator.py](../../../app/api/v1/simulator.py)

### 2.3 Поток данных: от scenario до UI

```mermaid
flowchart LR
  ScenarioFile[scenario.json] -->|load fixtures| ScenarioRegistry
  Upload[POST /api/v1/simulator/scenarios] -->|validate schema| ScenarioRegistry

  ScenarioRegistry --> Runtime[SimulatorRuntime]
  Runtime -->|POST /runs| RunLifecycle
  RunLifecycle --> Run[(RunRecord)]

  RunLifecycle -->|mode=fixtures| FixturesRunner
  RunLifecycle -->|mode=real| RealRunner

  FixturesRunner -->|SSE events| SSE[SseBroadcast]
  RealRunner -->|SSE events| SSE

  SSE --> UI[Simulator UI]
  Runtime --> Snapshot[SnapshotBuilder]
  Runtime --> Metrics[Metrics/Bottlenecks]
  UI -->|GET snapshot/metrics| Runtime
```

### 2.4 Fixtures mode vs Real mode (что реально отличается)

**Fixtures mode** (см. [../../../app/core/simulator/fixtures_runner.py](../../../app/core/simulator/fixtures_runner.py)):
- Не делает реальные платежи.
- Эмитит «визуальные» события на основе графа trustlines.
- Каденс клиринга/tx.updated в основном тайм‑based (`_next_*_at_ms`), а не “каждые N тиков”.

**Real mode** (см. [../../../app/core/simulator/real_runner_impl.py](../../../app/core/simulator/real_runner_impl.py) и [../../../app/core/simulator/tick.py](../../../app/core/simulator/tick.py)):
- (Один раз на run) сидит сценарий в БД и потом выполняет тик‑цикл.
- На тике планирует платежи (budget от intensity) и вызывает PaymentService.
- Может запускать клиринг:
  - **static cadence:** каждые `N` тиков (`SIMULATOR_CLEARING_EVERY_N_TICKS`) — единственная политика (адаптивная удалена 2026-09-28, программа 021, стадия 3)

### 2.5 Tick‑модель и детерминизм

Верхнеуровневое описание алгоритма runner: [backend/runner-algorithm.md](backend/runner-algorithm.md).

Практика в коде (Real mode):
- `tick_seed = (seed * 1_000_003 + tick_index) & 0xFFFFFFFF`
- для действий внутри тика делается производный `action_seed`

Это снижает “дрейф” случайности при изменениях нагрузки.

### 2.6 Структура `scenario.json` (поля и смысл)

Формальная схема: [../../../fixtures/simulator/scenario.schema.json](../../../fixtures/simulator/scenario.schema.json)

Ключевые поля:
- `schema_version` — версия формата входного JSON.
- `scenario_id` — строковый идентификатор.
- `name`, `description` — метаданные.
- `seed` — опционально, для детерминизма (если движок/контроль‑plane начнёт его использовать как вход).
- `equivalents: string[]` — список эквивалентов.
  - Практика runtime: граф и события строятся по `trustlines[].equivalent`, а `equivalents[]` используется как список режимов/фильтров.
- `participants[]`:
  - `id` — PID, должен существовать в БД (в real mode) для сидирования.
  - `type` — `person|business|hub`.
  - `status` — влияет на сидирование/фильтрацию (зависит от seed‑логики).
  - `groupId` — логическая роль (anchors/producers/…).
  - `behaviorProfileId` — привязка к профилю поведения.
- `trustlines[]`:
  - `from`/`to` — строго creditor→debtor.
  - `equivalent` — эквивалент ребра.
  - `limit` — лимит (строкой, чтобы не терять точность).
  - `policy` — произвольный объект (в т.ч. статус/auto_clearing).
- `groups[]`, `behaviorProfiles[]`, `events[]`:
  - Допускаются схемой как расширение.
  - На текущем этапе часть из них может не интерпретироваться планировщиком (см. раздел 2.7).

Подробное пояснение schema + MVP допущений: [backend/scenario-schema.md](backend/scenario-schema.md).

### 2.7 Что сейчас НЕ используется движком (важно для ожиданий)

На текущем этапе (см. [../../../app/core/simulator/real_runner_impl.py](../../../app/core/simulator/real_runner_impl.py)):
- `events[]` присутствуют в schema, но на текущем этапе **ещё не интерпретируются** planner’ом (см. спецификацию).
- `behaviorProfiles[]` в **real mode** интерпретируются частично: используется подмножество `behaviorProfiles.props` (`tx_rate`, `equivalent_weights`, `recipient_group_weights`, `amount_model[eq]`).
- Подбор суммы в real mode ограничен сверху: `amount <= min(SIMULATOR_REAL_AMOUNT_CAP, trustline.limit, props.amount_model[eq].max)`.
  - `SIMULATOR_REAL_AMOUNT_CAP` по умолчанию `3.00` (backward-compatible). Для realistic-v2 рекомендуется запускать с `SIMULATOR_REAL_AMOUNT_CAP>=500`.

Это сознательные MVP‑ограничения: сценарий описывает сеть, но «экономическая модель поведения» пока минимальна.

Планируемая эволюция (спецификация, чтобы не ломать детерминизм/guardrails):
- Поведенческая модель real mode (интерпретация `behaviorProfiles`/`events`, реалистичные суммы, выбор получателей): [backend/behavior-model-spec.md](backend/behavior-model-spec.md).

### 2.8 Где лежат сценарии и как они попадают в runtime

Загрузка сценариев:
- fixtures: [../../../fixtures/simulator/*/scenario.json](../../../fixtures/simulator/)
- uploaded: `.local-run/simulator/scenarios/<scenario_id>/scenario.json` (по умолчанию)

Валидация:
- При upload через API выполняется JSON Schema validation (см. `validate_scenario_or_400`).

### 2.9 Как генерируются “seed” сценарии (Greenfield/Riverside)

Эта секция отвечает на вопросы:
- что такое **seed‑сценарий** и чем он отличается от «обычного» сценария;
- что такое **описание сообщества** и **фикстуры симулятора** в этом репозитории;
- какой **полный процесс** подготовки данных: от человекочитаемой задумки сообщества → до `scenario.json` симулятора.

(2026-10-07, 032 S4: пакет `admin-fixtures/`, его генераторы и копия в `admin-ui/public/` удалены вместе с mock-режимом Admin UI; раздел описывает текущий процесс. Прежние шаги «сгенерировать canonical admin fixtures» и «синхронизировать fixtures в Admin UI» больше не существуют.)

#### 2.9.1 Терминология (быстро и без двусмысленности)

**Seed‑документ (seed doc)**
- Человекочитаемая спецификация демо‑сообщества: роли/экономическая логика/ожидаемые trustline‑паттерны/клиринговые циклы.
- Живёт в `docs/ru/seeds/*`.
- Регламент: [../../../docs/ru/seeds/README.md](../../../docs/ru/seeds/README.md).

**Описание сообщества (community description)**
- Структура сообщества в машиночитаемом виде: `seeds/communities/<id>/community.json` (участники, группы, линии, эквиваленты) и рецепт операций `recipe.json`. Правится руками.
- Из него выводятся и seed‑сценарии симулятора, и данные в базе (рецепт исполняется через доменные сервисы: `scripts/seed_db.py --source recipe --community <id>`).
- Регламент: [../../../docs/ru/seeds/README.md](../../../docs/ru/seeds/README.md).

**Fixtures симулятора (фикстуры)**
- Это `fixtures/simulator/<scenario_id>/scenario.json` и демо-снимки Simulator UI (`simulator-ui/v2/public/simulator-fixtures/`; статические версионируемые файлы) — *детерминированный источник* для генераторов/демо.
- Важно: fixtures ≠ данные продакшена, это **контролируемые** (reproducible) датасеты.

**Seed‑сценарий симулятора (seed scenario)**
- Это `fixtures/simulator/<scenario_id>/scenario.json`, который:
  - валиден по schema `fixtures/simulator/scenario.schema.json`;
  - детерминированно построен из описания сообщества;
  - отражает ту же «модель сообщества», что и данные в базе после рецепта (те же участники/лимиты/эквиваленты).

Простое правило: 
- seed docs → «почему и как устроено сообщество»;
- описание сообщества → «структура: кто есть кто и кто кому доверяет»;
- seed scenarios → «каноничный `scenario.json` симулятора для тех же данных».

#### 2.9.2 Из чего конкретно строятся seed‑сценарии

Seed‑сценарии собираются **детерминированно** из описания сообщества:
- **Вход (source of truth):** `seeds/communities/<id>/community.json`
- **Правила mapping:** [backend/fixtures-mapping.md](backend/fixtures-mapping.md)
- **Генератор seed‑сценариев:** [../../../scripts/generate_simulator_seed_scenarios.py](../../../scripts/generate_simulator_seed_scenarios.py)
- **Контракт выхода (schema):** [../../../fixtures/simulator/scenario.schema.json](../../../fixtures/simulator/scenario.schema.json)

#### 2.9.3 Полный процесс подготовки сценариев (end‑to‑end)

Ниже — «канонический» pipeline для Greenfield/Riverside (и любых последующих seed‑наборов).

**Шаг 0. Спроектировать сообщество (seed doc) и перенести замысел в описание**
- Внести/обновить seed‑документ в `docs/ru/seeds/`.
- Проверить семантику направлений trustlines (creditor → debtor).
- Описать структуру в `seeds/communities/<id>/community.json` (и рецепт в `recipe.json`).
- Регламент и чек‑листы: [../../../docs/ru/seeds/README.md](../../../docs/ru/seeds/README.md).

**Шаг 1. Засеять базу рецептом (для Admin UI и real mode)**

```powershell
./.venv/Scripts/python.exe scripts/seed_db.py --source recipe --community riverside-town-50
```

Рецепт исполняет настоящие операции через доменные сервисы; Admin UI затем читает эту базу через backend. Подробнее: [../../../docs/ru/seeds/README.md](../../../docs/ru/seeds/README.md).

**Шаг 2. Сгенерировать seed‑сценарии симулятора из описания сообщества**

```powershell
./.venv/Scripts/python.exe scripts/generate_simulator_seed_scenarios.py
```

Результат:
- перезаписываются (детерминированно) сценарии:
  - `fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json`
  - `fixtures/simulator/riverside-town-50-realistic-v2/scenario.json`

В генераторе дополнительно есть встроенная проверка формы сценария по JSON schema.

**Шаг 3. Запуск симулятора и проверка поведения**

Дальше вы выбираете сценарий в Simulator UI и запускаете его в `fixtures` или `real` mode.
Быстрый старт полного стека:
```powershell
./scripts/run_full_stack.ps1 -Action start
```

Если вы не видите сценарий в UI — проверьте allowlist:
- `SIMULATOR_SCENARIO_ALLOWLIST=all` или список id.

#### 2.9.4 Диаграмма процесса (seed → описание → scenario)

```mermaid
flowchart TD
  A[Seed docs\n docs/ru/seeds/*] -->|design rules, by hand| CD[Community description\n seeds/communities/*/community.json]
  CD -->|recipe through domain services| DB[Seeded database\n scripts/seed_db.py --source recipe]
  CD -->|structure| E[Simulator seed-scenarios generator\n scripts/generate_simulator_seed_scenarios.py]
  E -->|write| F[Seed scenarios\n fixtures/simulator/*/scenario.json]
  F -->|schema validation| Fs[scenario.schema.json]

  F -->|run| G[Simulator runtime\n fixtures mode / real mode]
  G -->|events/metrics| H[Simulator UI]
```

#### 2.9.5 Почему это называется именно “seed” сценарии

Слово **seed** здесь про «посевной/эталонный набор», который:
- задаёт **каноничную** стартовую топологию сообщества;
- должен быть **воспроизводимым** (одинаковые входы → одинаковые выходы);
- служит опорой для сравнения изменений движка/визуализации/аналитики (чтобы UX и метрики сравнивались на одном и том же графе).

То есть это не “seed” в смысле RNG‑seed, а “seed” в смысле **эталонной базы** данных/графа.

#### 2.9.6 Где закреплены правила (что «регламентирует» процесс)

- Терминология и дизайн сообщества: [../../../docs/ru/seeds/README.md](../../../docs/ru/seeds/README.md)
- Описание сообщества и рецепт: `seeds/communities/<id>/community.json`, `recipe.json`
- Mapping описания → scenario (контракт): [backend/fixtures-mapping.md](backend/fixtures-mapping.md)
- Генератор seed‑сценариев: [../../../scripts/generate_simulator_seed_scenarios.py](../../../scripts/generate_simulator_seed_scenarios.py)
- Schema сценария: [../../../fixtures/simulator/scenario.schema.json](../../../fixtures/simulator/scenario.schema.json)

### 2.10 Конфигурация runtime (env vars)

Основные knobs (см. [../../../app/core/simulator/runtime_impl.py](../../../app/core/simulator/runtime_impl.py)):
- `SIMULATOR_TICK_MS_BASE` — виртуальная длительность тика.
- `SIMULATOR_ACTIONS_PER_TICK_MAX` — верхний лимит budget действий.
- `SIMULATOR_CLEARING_EVERY_N_TICKS` — cadence клиринга в real mode.
- `SIMULATOR_SCENARIO_ALLOWLIST` — фильтр списка сценариев для UI.

SSE replay buffer:
- `SIMULATOR_EVENT_BUFFER_SIZE`
- `SIMULATOR_EVENT_BUFFER_TTL_SEC`
- `SIMULATOR_SSE_SUB_QUEUE_MAX`

Replay correctness не переключается env-флагом: любой переданный
`Last-Event-ID`, который нельзя полностью и упорядоченно восстановить, всегда
получает `HTTP 410`.

Real mode guardrails:
- `SIMULATOR_REAL_MAX_IN_FLIGHT`
- `SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK`
- `SIMULATOR_REAL_MAX_ERRORS_TOTAL`
- `SIMULATOR_CLEARING_MAX_DEPTH`

### 2.11 Последовательность одного тика (Real mode)

```mermaid
sequenceDiagram
  participant RL as RunLifecycle
  participant RR as RealRunner
  participant DB as DB (Postgres)
  participant Pay as PaymentService
  participant SSE as SSE stream

  RL->>RR: tick_real_mode(run_id)
  RR->>DB: seed scenario (once per run)
  RR->>RR: plan actions (budget from intensity)
  loop payment actions
    RR->>Pay: create payment (sender, receiver, eq, amount)
    Pay-->>RR: committed/rejected/error
    RR->>SSE: tx.updated / tx.failed
  end
  alt clearing tick
    RR->>DB: plan+apply clearing
    RR->>SSE: clearing.done
  end
  RR->>SSE: run_status (heartbeat)
```

### 2.12 Ошибки: где логгируются и как их расшифровывать

В симуляторе есть **две принципиально разные категории “плохих” исходов**:
- **Ожидаемые отказы (rejections)**: платеж “не прошёл” по бизнес‑причинам (нет маршрута, не хватает лимитов и т.п.). Это нормальная часть динамики сети.
- **Ошибки выполнения (errors)**: таймауты, неожиданные исключения, падения тика, ошибки инфраструктуры. Эти события сигналят о проблемах движка/стека, а не сценария.

Ниже — где всё это видно и как читать.

#### 2.12.1 Где смотреть (UI, SSE, artifacts, backend logs, DB)

**1) В UI (Simulator UI)**
- Панель run показывает `state` и (если run упал) `last_error` из события `run_status`.
- События `tx.failed` всплывают как “красные/жёлтые” сигналы на графе: это может быть как rejection, так и error.

**2) В SSE (поток событий)**
- Истина по событиям — это то, что пришло в stream: `tx.failed`, `run_status`, `clearing.*`.
- Для `tx.failed` самое важное поле — `error`:
  - `error.code` — нормализованный код причины (для UI/аналитики)
  - `error.details` — “сырые” детали (класс исключения, GEO‑код, HTTP‑статус и т.п.)

См. протокол: [backend/ws-protocol.md](backend/ws-protocol.md).

**3) В artifacts (`events.ndjson` + status/summary)**
- Все SSE‑события пишутся в `events.ndjson` (best‑effort).
- Локальный путь (dev, по умолчанию): `.local-run/simulator/runs/<run_id>/artifacts/events.ndjson`.
- Там же лежат `status.json`, `last_tick.json`, а при finalize — `summary.json` и `bundle.zip`.

См. где хранится и как устроено: [backend/run-storage.md](backend/run-storage.md).

**4) В backend logs**
- Движок логгирует исключения и предупреждения через стандартный `logging`.
- Полезные “якоря” по строкам логов:
  - `simulator.real.tick_failed` — тик real mode упал (с `exc_info=True`).
  - `simulator.storage.*` — проблемы записи run/метрик/артефактов в БД.
  - `simulator.artifacts.*` — проблемы создания/записи `events.ndjson` и финализации bundle.
  - `simulator.fixture_scenario_load_failed` / `simulator.uploaded_scenario_load_failed` — сценарий не загрузился.

**5) В DB (если включено хранение run state)**
- У run есть счётчики и последнее состояние:
  - `errors_total` — количество *ошибок выполнения* (см. ниже про отличие от rejections)
  - `last_error` (JSON) — “последняя фатальная/важная ошибка” (для `run_status`)

См. схему хранения: [backend/run-storage.md](backend/run-storage.md).

#### 2.12.2 Как отличить rejection от error

Практическое правило для real mode:
- **Rejection** обычно приходит как `tx.failed`, но при этом это *не* “ошибка движка”:
  - в `error.details` часто есть `status_code` в диапазоне `4xx` (клиентская бизнес‑ошибка)
  - `errors_total` при этом не увеличивается
- **Error** (таймаут/исключение) обычно:
  - увеличивает `errors_total`
  - обновляет `run_status.last_error`
  - может приводить к `run_status.state="error"` при превышении guardrails

#### 2.12.3 Примеры типовых ошибок и что они означают

Ниже примеры “что это нам говорит” — чаще всего это либо сигнал о топологии сценария, либо сигнал о проблеме стека.

**A) `tx.failed.error.code=ROUTING_NO_ROUTE`**
- Что это: нет маршрута в графе trustlines от отправителя к получателю.
- Что говорит о сценарии:
  - граф разорван (нет пути),
  - перепутано направление trustline (должно быть creditor → debtor),
  - неверный `equivalent` (в этом эквиваленте ребёр меньше/нет).

**B) `tx.failed.error.code=ROUTING_NO_CAPACITY`**
- Что это: путь есть, но по доступной ёмкости он не проходит (лимиты/доступное < сумма).
- Что говорит:
  - лимиты слишком маленькие для текущих долгов,
  - clearing слишком редкий/неэффективный,
  - сценарий создаёт слишком “горячие” узлы (узкие места) — это может быть ожидаемо.

**C) `tx.failed.error.code=TRUSTLINE_LIMIT_EXCEEDED`**
- Что это: конкретный trustline не выдерживает попытку (перелив через лимит).
- Что говорит:
  - либо лимит/политика trustline слишком строгие,
  - либо планировщик подбирает суммы слишком большие для текущей топологии.

**D) `tx.failed.error.code=TRUSTLINE_NOT_ACTIVE`**
- Что это: trustline/политика помечены как неактивные.
- Что говорит:
  - сидирование/fixtures создали часть ребёр неактивными,
  - или вы ожидаете ребро активным, но в данных оно выключено.

**E) `tx.failed.error.code=PAYMENT_TIMEOUT`**
- Что это: таймаут на уровне платежного стека.
- Что говорит:
  - перегрузка/локи/транзакции,
  - проблемы инфраструктуры (Postgres/Redis),
  - слишком высокий `intensity_percent` относительно ресурсов.

**F) `tx.failed.error.code=INTERNAL_ERROR`**
- Что это: непредвиденная ошибка сервера (5xx/исключение вне ожидаемых бизнес‑отказов).
- Что говорит:
  - потенциальный баг, транзакционное состояние “aborted”, проблемы с сессией/локами,
  - нужно смотреть backend logs по времени события и `error.details`.

**G) `tx.failed.error.code=PAYMENT_REJECTED`**
- Что это: “обобщённый” отказ, когда детали не удалось стабильно промаппить в более конкретный код.
- Что говорит:
  - это почти всегда про данные в `error.details` (там обычно есть `exc`, `geo_code`, `status_code`).

**H) `run_status.state="error"` + `last_error.code=REAL_MODE_TOO_MANY_TIMEOUTS|REAL_MODE_TOO_MANY_ERRORS|REAL_MODE_TICK_FAILED_REPEATED|HEARTBEAT_FAILED`**
- Что это: сработали guardrails и run принудительно остановлен как аварийный.
- Что говорит:
  - `REAL_MODE_TOO_MANY_TIMEOUTS`: деградация стека/локи/ресурсная нехватка.
  - `REAL_MODE_TOO_MANY_ERRORS`: систематическая ошибка исполнения (например, постоянные `INTERNAL_ERROR`).
  - `REAL_MODE_TICK_FAILED_REPEATED`: тик падает исключением несколько раз подряд (часто баг или транзакционное “poisoning”).
  - `HEARTBEAT_FAILED` (программа 034, F-034-10, 2026-10-08): исключение в самой итерации heartbeat — вне
    обработки ошибок тика (публикация статуса, реестр ранов, сбой внутри обработчика упавшего тика). Действует
    в обоих режимах (`real` и `fixtures`). Повтора нет: ран сразу `error`, тиков больше не будет; вернуть его в
    работу — `resume`. `last_error.message` несёт **только тип исключения** (`The heartbeat failed: <Type>`);
    текст исключения наружу не уходит, он в логе: строка `simulator.heartbeat.failed run_id=<id>` с трейсбеком.
    До 034 такое исключение молча завершало задачу heartbeat, и ран оставался `running` без тиков.

**I) `last_error.code=REAL_MODE_TICK_FAILED`, но run ещё `running`**
- Что это: один тик упал исключением, но runner пытается продолжить (до порога “подряд”).
- Что говорит:
  - часто это “всплеск” из‑за конкуренции/локов или единичная ошибка сессии,
  - если повторяется — будет `REAL_MODE_TICK_FAILED_REPEATED` и run станет `error`.

Практический флоу расследования:
1) Найти последний `tx.failed` в `events.ndjson` и посмотреть `error.code` + `error.details`.
2) Если run перешёл в `error`, смотреть `run_status.last_error` (это итоговый “стоп‑код”).
3) Сопоставить `ts` события с backend logs (якоря `simulator.real.*`, `simulator.storage.*`).
4) Если причина — routing/лимиты: проверять направление trustlines, связность, лимиты и cadence clearing.

#### 2.12.4 Мини‑пример: как выглядит `tx.failed` в данных

Пример (сокращённо; реальные события содержат `event_id`, `ts` и др.):

```json
{
  "type": "tx.failed",
  "equivalent": "HOUR",
  "from": "p_alice",
  "to": "p_bob",
  "error": {
    "code": "ROUTING_NO_ROUTE",
    "message": "ROUTING_NO_ROUTE",
    "details": {
      "exc": "RoutingException",
      "geo_code": "E001",
      "status_code": 400,
      "message": "No route"
    }
  }
}
```

Как читать:
- `error.code` — быстро классифицирует причину (нормализованный слой UI/аналитики).
- `error.details.*` — помогает точно понять первопричину (исключение/код/статус), и именно туда надо смотреть при расследовании.

---

### 2.13 Сюжетный сценарий `community-story-10` (036, срез C)

[`fixtures/simulator/community-story-10/scenario.json`](../../../fixtures/simulator/community-story-10/scenario.json) — не «мир, который живёт сам», а **рассказ из 12 эпизодов** о том, как работает сеть взаимного кредита. 10 жителей плюс Дмитро, которого вводит `inject`; один эквивалент `UAH`; `tick_seconds` 2.5; последний эпизод на 58-м тике, то есть около 2.4 минут на собственном темпе (паузы не считаются). Формат полей эпизода (`caption`, `pause_after`, `focus`, `anchor`, `expected_cycle`, `settings.playback`) — [`backend/scenario-schema.md`](backend/scenario-schema.md).

| № | Время (тик) | Тип | Кто → кому, сумма | Пауза | Что показывает |
|---|---|---|---|---|---|
| 0 | 3 000 (3) | `note` | — | да | знакомство: десять жителей, видно только доверие |
| 1 | 8 000 (8) | `payment` | Олена → Пекарня, 40.00 | да | покупка напрямую: долг Олены Пекарне 40 |
| 2 | 13 000 (13) | `payment` | Тарас → Ферма, 60.00 | да | покупка через посредника: Тарас должен Магазину, Магазин — Ферме |
| 3 | 18 000 (18) | `payment` | Ферма → Млин, 50.00 | нет | накопление долгов |
| 4 | 23 000 (23) | `payment` | Пекарня → Млин, 30.00 | нет | накопление долгов: цепочки, не круг |
| 5 | 28 000 (28) | `inject` | `add_participant` Дмитро (Магазин ему доверяет, он доверяет Млину) | да | новый участник и доверие к нему (`topology.changed`) |
| 6 | 33 000 (33) | `payment` | Млин → Дмитро, 40.00 | нет | долг Млина Дмитру |
| 7 | 38 000 (38) | `payment` | Дмитро → Магазин, 40.00 | да | долги замкнулись: Магазин → Ферма → Млин → Дмитро → Магазин |
| 8 | 43 000 (43) | `clearing` | `expected_cycle` Ферма, Магазин, Дмитро, Млин | да | круг гасится на 40 «до/после»: остаётся Магазин → Ферма 20, Ферма → Млин 10 |
| 9 | 48 000 (48) | `inject` | `freeze_participant` Тарас | да | заморозка участника: его долг Магазину 60 остаётся на виду |
| 10 | 53 000 (53) | `payment` | Петро → Нина, 25.00 | да | отказ без доверия: `ROUTING_NO_ROUTE`, ничего не сдвинуто |
| 11 | 58 000 (58) | `note` | — | да | итог |

Итоговые долги (должник → кредитор): Олена → Пекарня 40, Тарас → Магазин 60, Магазин → Ферма 20, Ферма → Млин 10, Пекарня → Млин 30. Идентификаторы участников в сценарии — `cs_<имя>` (отдельный префикс, чтобы не пересекаться с другими сценариями в одной базе); `name` — для показа.

**Предусловия запуска.**
- Режим `real` на PostgreSQL. Эпизоды 5 и 9 — `inject`: нужен флаг процесса `SIMULATOR_REAL_ENABLE_INJECT=1` (по умолчанию 0). Без него оба эпизода пропускаются **вслух** (одна заметка в артефакте событий и запись `episode_progress` со статусом `refused`, причина `inject_disabled_by_process`), а круг из эпизодов 6–7 не замкнётся — история потеряет смысл. Сценарий сам `inject_enabled: true`; это согласие, но не разрешение: потолок — флаг процесса.
- `intensity_percent` — 0 (умолчание сценария `settings.playback.intensity_percent`). Фоновые платежи сдвинули бы долги истории. Умолчание достигается только если клиент **не шлёт** число; Simulator UI сейчас шлёт всегда, поэтому из него интенсивность нужно выставить в 0 самому (остаток B2).
- **Перезапуск истории — новый ран**, а не `restart`: `restart` начинает новую эпоху и исполняет эпизоды заново поверх прежних долгов.

**Объявленные ограничения и почему история построена именно так** (остатки B1, `specs/BACKLOG.md` 036-2):
- *Периодический клиринг не отключается сценарием.* Он идёт на каждом тике, кратном `SIMULATOR_CLEARING_EVERY_N_TICKS` (по умолчанию 25: тики 25, 50, 75), и может забрать цикл эпизода раньше скриптового клиринга. Поэтому в истории на тиках 25 и 50 циклов нет: круг замыкается на тике 38 и гасится на тике 43, между ними кратных 25 нет. Если поменять расписание эпизодов или период, правило надо перепроверить: его держит `tests/unit/test_p036_c_community_story_shape.py`.
- *Порядок внутри тика задаёт тип события, а не `time`:* сначала `inject`, затем `payment`, затем `clearing`. Эпизоды разнесены на 5 тиков, на одном тике ничего не конкурирует.
- *Скриптовый клиринг может закончиться `incomplete`* в штатный бюджет прохода (250 мс) и доделаться следующим тиком; эпизод тратится только на `done`, и пауза наступает после него. Исполняющий тест ждёт `done` и не зависит от числа тиков.
- *Эпизод 10 — отказ по замыслу.* Статус `refused` и `tx.failed` — ожидаемый исход, а не поломка; ран при этом не останавливается.
- `expected_cycle` записан в порядке рёбер «кредитор → должник» (как `cycle_edges` и `cycles[].edges` в `episode_progress`): Ферма → Магазин → Дмитро → Млин → Ферма.

**Как это проверяется.** `tests/integration/test_p036_c_community_story_postgres.py` прогоняет историю от начала до конца настоящими тиками на PostgreSQL (`runtime.create_run`, продовый heartbeat с виртуальным `sleep`, `runtime.resume` на каждой паузе) и сверяет `episode_progress` и долги в базе после каждой паузы; `tests/unit/test_p036_c_community_story_shape.py` — размер, правило периодического клиринга и allowlist; схему держит `tooling-tests/portable/test_p036_a_scenario_schema_and_the_live_fixtures.py`. **Не сделано в срезе C:** запись событий рана как офлайн-фикстура для плеера (`T3631`) и что-либо в `simulator-ui/**`.

## Ссылки на существующую документацию

- Формат `scenario.json` (описание): [backend/scenario-schema.md](backend/scenario-schema.md)
- Формат `scenario.json` (JSON Schema): [../../../fixtures/simulator/scenario.schema.json](../../../fixtures/simulator/scenario.schema.json)
- Алгоритм runner (верхний уровень): [backend/runner-algorithm.md](backend/runner-algorithm.md)
- Протокол SSE/REST: [backend/ws-protocol.md](backend/ws-protocol.md)
- Интеграция с payment/clearing: [backend/payment-integration.md](backend/payment-integration.md)
- Модель событий/снапшотов/метрик: [backend/simulator-domain-model.md](backend/simulator-domain-model.md)
- Хранение run state + artifacts export: [backend/run-storage.md](backend/run-storage.md)
- Как поднять окружение: [backend/real-mode-runbook.md](backend/real-mode-runbook.md)
