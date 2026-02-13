# Алгоритм SimulationRunner (MVP): tick‑model + детерминизм

Дата: **2026-01-28**

Цель документа: убрать неоднозначности перед реализацией runner, чтобы backend и simulator-ui сходились по:
- смыслу `tick`/`sim_time_ms`
- интерпретации `intensity_percent`
- детерминизму (seed/порядок обхода)
- минимальному каталогу действий (что генерируем в MVP)

Source of truth:
- `fixtures/simulator/scenario.schema.json` (входной сценарий)
- `docs/ru/simulator/backend/simulator-domain-model.md` (типы событий/обязательные поля)
- `docs/ru/simulator/backend/ws-protocol.md` (SSE/REST команды)
- `api/openapi.yaml` (эндпоинты + `SimulatorEvent` union)
- `docs/ru/simulator/backend/acceptance-criteria.md` (SB-08, SB-NF-04 и др.)

Ограничение MVP:
- UI **не требует** события `tick` по умолчанию; `tick` допускается только как debug (см. `ws-protocol.md`).
- обязательное системное событие — `run_status`.

---

## 1) Модель времени

В run есть два времени:
- `ts` — wall-clock (ISO, время сервера), для всех событий.
- `sim_time_ms` — виртуальное время симуляции (мс от старта run), для `run_status` и внутренних вычислений.

### 1.1 Дискретизация (tick)
Runner работает дискретно.

Параметры:
- `tick_ms_base` — базовая длительность шага в виртуальном времени.
  - MVP фиксируем: `tick_ms_base = 1000` (1 секунда сим-времени на tick).
- `tick_index` — целый счётчик тиков, начинается с 0.

Правило обновления:
$$ sim\_time\_ms = tick\_index \cdot tick\_ms\_base $$

Важно:
- Реальная скорость выполнения тиков не обязана совпадать с `tick_ms_base`.
- `intensity_percent` влияет на **количество действий**, а не на виртуальную длительность тика.

---

## 2) Общая схема работы runner

### 2.1 Состояние run (минимум)
- `run_id`, `scenario_id`
- `state ∈ { idle, running, paused, stopping, stopped, error }`
- `seed` (целое)
- `tick_index`
- `intensity_percent` (0..100)
- `queue` (очередь запланированных действий)

### 2.2 Главный цикл (упрощённо)
1) `start`:
   - загрузить/валидировать сценарий
   - инициализировать RNG (см. раздел 3)
   - перейти в `running`
   - начать:
     - периодический `run_status` heartbeat (1–2 сек)
     - loop обработки тиков

2) `running`:
   - на каждом тике:
     - вычислить «бюджет действий» на тик из `intensity_percent`
     - выбрать действия (по поведению участников + события сценария)
     - выполнить действия (вызовы PaymentEngine / GEO API)
     - эмитить доменные события (`tx.updated`, `clearing.plan`, `clearing.done`) best-effort
     - обновить метрики/агрегаты (для `ops_sec` и т.п.)
     - увеличить `tick_index`

3) `pause`:
   - остановить обработку тиков
   - продолжать эмитить `run_status` (heartbeat)

4) `resume`:
   - возобновить обработку тиков

5) `stop`:
   - прекратить планирование новых действий
   - дождаться in-flight действий (или отменить по политике)
   - перейти в `stopped` и корректно завершить stream

---

## 3) Детерминизм и генерация случайности

Цель: выполнить SB-NF-04.

### 3.1 Seed
`seed` фиксируется при старте run:
- либо из `POST /runs` (если добавим поле в будущем),
- либо генерируется сервером и сохраняется в run state.

Рекомендация MVP (для воспроизводимости тестов):
- если `scenario_id` соответствует `fixtures/simulator/*`, разрешить режим фиксированного seed (например через query/debug или конфиг среды).

### 3.2 PRNG
Требования к PRNG:
- полностью детерминированный
- одинаковый результат на одной версии Python

Практичный MVP:
- использовать `random.Random(seed)` как базовый PRNG.

### 3.3 Порядок обхода
Чтобы не убить детерминизм «случайным порядком словаря»:
- `participants` в памяти упорядочены по `id` (лексикографически)
- `trustlines` упорядочены по `(equivalent, from, to)`

### 3.4 Стратегия RNG на тик
Чтобы изменения нагрузки (intensity) не «разносили» случайность на весь run:
- на каждый тик создаём производный RNG:
  - `tick_rng = Random(hash(seed, tick_index))`

Идея:
- выбор участников/действий на тике зависит от `(seed, tick_index)`.
- если пропущен тик (например pause), последовательность не должна "поползти" из-за количества вызовов `random()`.

MVP-формула derivation (без криптографии, но стабильная):
- `tick_seed = (seed * 1_000_003 + tick_index) & 0xFFFFFFFF`
- `tick_rng = Random(tick_seed)`

---

## 4) Интерпретация intensity (0–100)

### 4.1 Что такое intensity
`intensity_percent` управляет «сколько действий планировать».

MVP цель:
- при 0% — фактически пауза генерации действий (но `run_status` продолжает идти)
- при 100% — максимальный допустимый бюджет действий, ограниченный guardrail’ами

### 4.2 Бюджет действий на тик
Задаём 2 константы (на уровне конфигурации сервера):
- `actions_per_tick_min = 0`
- `actions_per_tick_max = 20`

Примечание (текущая реализация):
- `actions_per_tick_max` имеет дефолт `ACTIONS_PER_TICK_MAX = 20`, но может быть переопределён env-переменной `SIMULATOR_ACTIONS_PER_TICK_MAX` (см. runtime).
- `tick_ms_base` имеет дефолт `TICK_MS_BASE = 1000`, но может быть переопределён `SIMULATOR_TICK_MS_BASE`.

Линейная интерполяция:
$$ actions\_budget = \left\lfloor actions\_per\_tick\_max \cdot \frac{intensity\_percent}{100} \right\rfloor $$

Примечания:
- это **бюджет планирования**, а не гарантия, что все действия будут успешны.
- если `actions_budget=0`, runner может оставаться в `running` (чтобы UI не путал паузу и «нулевую активность»), но доменных событий не будет.

### 4.3 Guardrails
Чтобы не убить PaymentEngine:
- лимит параллельности запросов (real mode): `SIMULATOR_REAL_MAX_IN_FLIGHT` (по умолчанию 1)
- лимит общего QPS на payments: `max_payments_per_sec` (конфиг)
- если лимиты превышены — новые действия откладываются/дропаются, но `run_status` остаётся.

Дополнительные guardrails (текущая реализация, env):
- `SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK` (по умолчанию 5)
- `SIMULATOR_REAL_MAX_ERRORS_TOTAL` (по умолчанию 200)
- клиринг: `SIMULATOR_CLEARING_MAX_DEPTH` (по умолчанию 6)

Поле `queue_depth` в `run_status` отражает накопление (если очередь есть).

---

## 5) Действия (actions) в MVP

Runner оперирует внутренними «действиями», которые приводят к вызовам API и/или эмиссии событий.

### 5.1 Каталог действий (минимум)
MVP включает:
1) `payment_attempt`
   - выбрать отправителя `from` и получателя `to`
   - выбрать `equivalent`
   - выбрать сумму (распределение задаётся профилем)
   - вызвать PaymentEngine/GEO API
   - по результату:
     - best-effort эмитить `tx.updated`
     - обновить метрики успеха/ошибок

2) `clearing_attempt` (опционально для раннего MVP)
   - инициировать клиринг по политике (фиксированный cadence или адаптивный feedback-control)
   - эмитить `clearing.plan` → потом `clearing.done`

Примечание (текущая реализация):
- **Real mode:** базовая «частота клиринга» по умолчанию `CLEARING_EVERY_N_TICKS = 25`, но переопределяется через `SIMULATOR_CLEARING_EVERY_N_TICKS`.
- **Fixtures mode:** клиринг/подсветки носят демонстрационный характер и управляются таймингами внутри fixtures-runner (не “каждые N тиков”).

Политика клиринга (`SIMULATOR_CLEARING_POLICY`):
- `static` (default) — фиксированный cadence (`SIMULATOR_CLEARING_EVERY_N_TICKS`), один вызов `tick_real_mode_clearing()` по всем equivalents.
- `adaptive` — динамическая периодичность на основе feedback-control. Координатор (`RealTickClearingCoordinator`) вызывает `AdaptiveClearingPolicy.evaluate()` **per-equivalent** на каждом тике:
  - сигналы: rolling-window `no_capacity_rate`, `clearing_volume`, `clearing_cost_ms`, `in_flight`, `queue_depth`.
  - решения: `should_run`, `time_budget_ms`, `max_depth` (per-eq).
  - hysteresis (HIGH/LOW пороги), cooldown (`min_interval_ticks`), exponential backoff при нулевом yield.
  - budget scaling: давление `no_capacity_rate` линейно масштабирует depth и time_budget в пределах `[MIN, min(MAX, global_ceiling)]`.
  - per-eq clearing loop заменяет единый `run_clearing()`, с per-call overrides в `tick_real_mode_clearing()`.

Env knobs для adaptive (подробнее — `real-mode-runbook.md`):
- `SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS`, `NO_CAPACITY_HIGH`, `NO_CAPACITY_LOW`, `MIN_INTERVAL_TICKS`, `BACKOFF_MAX_INTERVAL_TICKS`
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN/MAX`, `TIME_BUDGET_MS_MIN/MAX`
- `SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD`, `QUEUE_DEPTH_THRESHOLD`

См. каноничное описание: `adaptive-clearing-policy.md`.
Спецификация реализации: `adaptive-clearing-policy-spec.md`.

Важно:
- строгий контракт payload’ов событий — в `api/openapi.yaml` и `simulator-domain-model.md`.

---

## 6) Профили поведения (behaviorProfiles) — MVP интерпретация

Текущий статус и план расширения (real mode: `behaviorProfiles` + `events`, realistic amounts, receiver selection):
- [behavior-model-spec.md](behavior-model-spec.md)

Сценарий может содержать детальные `behaviorProfiles.rules`, но MVP-реализация может быть проще.

Статус текущей реализации:
- planner в real mode интерпретирует `behaviorProfiles.props` (см. спецификацию):
   - `tx_rate`, `equivalent_weights`, `recipient_group_weights`, `amount_model[eq]`.
- выбор действий строится из `trustlines[]` с детерминизмом по `(seed, tick_index)` и prefix-stability по `intensity_percent`.
- суммы платежей ограничены сверху:
   - `amount <= min(trustline.limit, SIMULATOR_REAL_AMOUNT_CAP, props.amount_model[eq].max)`.

Runtime knob:
- `SIMULATOR_REAL_AMOUNT_CAP` по умолчанию `3.00` (backward-compatible). Для realistic-v2 рекомендуется запускать с `SIMULATOR_REAL_AMOUNT_CAP>=500`.

### 6.1 Пресеты по `behaviorProfileId`
На этапе seed-сценариев (`greenfield-village-100`, `riverside-town-50`) используются ID:
- `anchor_hub`, `producer`, `retail`, `service`, `household`, `agent`

MVP правило:
- runner трактует эти ID как пресеты вероятностей:
  - частота платежей
  - распределение суммы
  - предпочтение получателей (внутри группы vs межгрупповое)

Пример сценария для ручной проверки (fixtures):
- `fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json`

### 6.2 Выбор платежа на тик (пример)
Алгоритм планирования `payment_attempt`:
1) сформировать список кандидатов-участников в фиксированном порядке `participants_sorted`
2) для каждого участника вычислить вероятность «создать платеж» на этом тике:
   - `p = base_p(profile, intensity_percent)`
3) используя `tick_rng`, отобрать до `actions_budget` действий
4) для каждого действия выбрать получателя:
   - с вероятностью `cluster_bias(profile)` выбрать из той же `groupId`
   - иначе выбрать из остальных
5) выбрать сумму:
   - `amount = round(lognormal/normal/triangular)` (конкретика — пресет)
   - сумма должна быть сериализуема как строка/decimal без потери (MVP допускает упрощение)

Примечание:
- чтобы не требовать сложных правил из 7.2 ИИ-спеки, MVP может игнорировать `rules[]`, если они отсутствуют или неизвестны.

---

## 7) Вызовы PaymentEngine / GEO API (на уровне алгоритма)

Требования:
- runner вызывает реальные endpoints ядра (SB-09)
- таймауты и ретраи должны быть явно заданы
- idempotency (если поддержано платежным API) — желательно

MVP политика ошибок:
- business-ошибки (нет маршрута/лимитов) не переводят run в `error` автоматически
- технические ошибки (таймаут/5xx) могут:
  - увеличивать счётчик ошибок
  - при превышении порога переводить run в `error` с `last_error.code=PAYMENT_TIMEOUT|INTERNAL_ERROR`

События UI:
- в MVP error отражается через `run_status.state="error"` + `last_error` (см. `simulator-domain-model.md`).

---

## 8) Эмиссия событий и update метрик

### 8.1 Что обязательно
- `run_status` (heartbeat) по правилам `ws-protocol.md`.

### 8.2 Что best-effort
- `tx.updated` — короткоживущие подсветки.
- `clearing.plan`/`clearing.done` — если clearing включён.

### 8.3 Поля `ops_sec` и `queue_depth`
MVP определение:
- `ops_sec` — сглаженная оценка выполненных действий в секунду (например over last 5–10 секунд wall-clock).
- `queue_depth` — текущая длина очереди действий (если применяется), иначе 0.

---

## 9) Тестируемость (что проверить по этому документу)

Минимальные проверки, которые должны быть реализуемы после написания runner:
- SB-08: при фиксированном seed и простом сценарии на N тиков получаем ожидаемое количество попыток платежей
- SB-NF-04: два прогона с одинаковым seed дают одинаковую последовательность внутренних действий (и, в тестовом окружении, одинаковые «исходы» при стабах)

Важно:
- интеграционные тесты с реальным PaymentEngine могут нарушать детерминизм исходов из-за внешних факторов; детерминизм SB-NF-04 проверяется на уровне planner/decision layer.
