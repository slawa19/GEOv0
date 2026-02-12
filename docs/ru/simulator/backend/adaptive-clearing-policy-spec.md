# SPEC: Adaptive clearing policy (Real Mode)

Дата: 2026-02-12

Документ-основание (каноника): `adaptive-clearing-policy.md`.

## 1) Цель

Добавить в Real Mode симулятора **адаптивную политику клиринга** (feedback-control), чтобы:

- клиринг запускался чаще при признаках деградации ликвидности (например рост `ROUTING_NO_CAPACITY`),
- клиринг редел/останавливался при нулевой пользе (clearing_volume ≈ 0),
- стоимость клиринга оставалась ограниченной (time budget, depth),
- поведение было объяснимым и устойчивым (hysteresis/cooldown).

## 2) Scope

Включено:
- новый компонент policy, не привязанный к конкретному сценарию;
- интеграция в `RealRunner` (замена “каждые N тиков” на выбор policy, без ломки дефолта);
- сбор lightweight сигналов на тике, достаточных для policy;
- минимальная наблюдаемость (reason / counters), чтобы отлаживать.

Не включено:
- изменение UX/UI;
- “ML/оптимизатор” или подбор параметров offline;
- обязательное расширение OpenAPI метрик (можно как отдельный этап).

## 3) Backward compatibility

- По умолчанию система работает как сейчас (фиксированный cadence).
- Адаптивный режим включается отдельным env knob.

## 4) Дизайн (компоненты)

### 4.1 Новый модуль

Файл: `app/core/simulator/adaptive_clearing_policy.py`

Сущности:
- `AdaptiveClearingPolicyConfig` (параметры window/thresholds/cooldown/budgets)
- `AdaptiveClearingState` (rolling-window counters per eq, last decision, backoff)
- `ClearingDecision` (per equivalent):
  - `should_run: bool`
  - `reason: str` (короткий человекочитаемый код)
  - `time_budget_ms: int | None`
  - `max_depth: int | None`

Требования:
- чистая логика, без DB/IO;
- устойчивость к missing данным;
- конфигurable, но с безопасными дефолтами.

Гранулярность (важно):
- policy принимает сигналы и принимает решение **per-equivalent**.
- интеграция в `RealRunner` должна вызывать policy в цикле по `equivalents`.

### 4.2 Сигналы (минимум)

Policy получает на каждый тик (per eq):
- `attempted_payments_tick` (сколько попыток платежей в тике)
- `rejected_no_capacity_tick` (сколько отказов с кодом `ROUTING_NO_CAPACITY` в тике)
- `total_debt` (уже считается в runner; используем тренд/дельту, не абсолют)
- `last_clearing_volume` (объём клиринга на последнем запуске клиринга для этого eq)
- `last_clearing_cost_ms` (стоимость последнего клиринга по времени для этого eq)
- `in_flight`, `queue_depth` (guardrail)

Определения (для объяснимости):
- `no_capacity_rate_window = sum(rejected_no_capacity_tick) / max(1, sum(attempted_payments_tick))` за окно `WINDOW_TICKS`.
- `clearing_yield_last = last_clearing_volume / max(1, last_clearing_cost_ms)`.

Примечание: `total_debt` и `clearing_volume` уже пишутся как time-series в БД — policy может работать без БД, но эти метрики полезны для отладки.

### 4.3 Источник `ROUTING_NO_CAPACITY`

Варианты (выбрать один, остальные отложить):

Вариант A (рекомендуемый, чистый контракт): расширить `RealPaymentsResult`.

- `RealPaymentsExecutor` уже вычисляет `rejection_code` в `_emit_if_ready()` **после** `map_rejection_code(...)`.
- Там же он должен инкрементировать per-eq счётчик по коду:
  - `rejection_codes_by_eq[eq][rejection_code] += 1`
- `RealPaymentsResult` расширяется полем:
  - `rejection_codes_by_eq: dict[str, dict[str, int]]`
- `RealRunner` получает `rejection_codes_by_eq` из результата и вычисляет `rejected_no_capacity_tick`.

Плюсы: без дополнительного IO, без парсинга `events.ndjson`, без скрытой мутации `run` из executor.

### 4.4 Интеграция в RealRunner

Изменить место, где сейчас решается “пора ли клирить”.

- Добавить `SIMULATOR_CLEARING_POLICY=static|adaptive`.
- В `static` оставить `SIMULATOR_CLEARING_EVERY_N_TICKS` как есть.
- В `adaptive`:
  - вызывать policy на каждом тике **для каждого эквивалента**;
  - если policy решает `should_run`, запускать `RealClearingEngine.tick_real_mode_clearing` с указанным бюджетом.

Важно:
- сохранить isolated-session клиринга (как сейчас);
- не менять контракты SSE событий (`clearing.plan` / `clearing.done`);
- при ошибках клиринга не “травить” payment tick session.

### 4.5 Управление бюджетом клиринга

Минимально:
- `time_budget_ms`: прокидывать в `RealClearingEngine`.
- `max_depth`: прокидывать в `RealClearingEngine`.

Требование: budgets должны быть ограничены верхними лимитами (guardrails).

Иерархия существующих env (чётко, чтобы не было конфликтов):
- существует `SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS` (global hard ceiling для клиринга в real mode).
- существует `SIMULATOR_CLEARING_MAX_DEPTH` (global hard ceiling для глубины).

Правило:
- `effective_time_budget_ms = clamp(policy_time_budget_ms, ADAPTIVE_TIME_BUDGET_MS_MIN, min(ADAPTIVE_TIME_BUDGET_MS_MAX, SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS))`
- `effective_max_depth = clamp(policy_max_depth, ADAPTIVE_MAX_DEPTH_MIN, min(ADAPTIVE_MAX_DEPTH_MAX, SIMULATOR_CLEARING_MAX_DEPTH))`

## 5) Конфиг (env knobs)

Новые:
- `SIMULATOR_CLEARING_POLICY` = `static|adaptive` (default: `static`)

Adaptive:
- `SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS` (default: 30)
- `SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH` (default: 0.60)
- `SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW` (default: 0.30)
- `SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS` (default: 5)
- `SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS` (default: 60)

Guardrails (adaptive):
- `SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD` (default: 0; 0 = disabled)
- `SIMULATOR_CLEARING_ADAPTIVE_QUEUE_DEPTH_THRESHOLD` (default: 0; 0 = disabled)

Budgets:
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN` / `MAX`
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN` / `MAX`

Рекомендуемые дефолты (ориентиры для реализации):
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN=3`
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MAX=6`
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN=50`
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MAX=250`

Стратегия усреднения сигналов: rolling window (объяснимость)
- используем rolling window длиной `WINDOW_TICKS` (без EWMA).
- отдельный `alpha` не нужен.

## 6) Наблюдаемость и отладка

Минимально требуемое:
- лог на tick decision: `reason`, `no_capacity_rate`, `cooldown_remaining`, `budget`.

Желательно (этап 2):
- сохранять `no_capacity_rate` и `clearing_cost_ms` в `simulator_run_metrics` (потребует расширения `MetricSeriesKey` и OpenAPI).

Примечание: `clearing_cost_ms` полезен даже без сохранения как метрика — он нужен policy для yield.

## 7) Тестирование

### 7.1 Unit tests (policy)

Новые тесты:
- `tests/unit/test_simulator_adaptive_clearing_policy.py`

Покрыть сценарии:
1) `no_capacity_rate` растёт выше `high` → policy включает clearing, уважая cooldown.
2) `no_capacity_rate` падает ниже `low` → policy выключает clearing (hysteresis).
3) `clearing_volume`≈0 несколько раз подряд → backoff растёт.
4) `in_flight`/`queue_depth` выше порога → policy откладывает клиринг (guardrail).

### 7.2 Integration tests (runner)

Новые/расширенные тесты (примерные направления):
- симулировать тик-цикл с подменой `RealClearingEngine` (spy) и проверить, что он вызывается при нужных сигналах;
- проверить, что `static` режим не меняет поведение.

### 7.3 Regression: SSE contracts

Проверить существующие интеграционные тесты SSE (`tx.updated`, `clearing.*`) — адаптивность не должна ломать payload.

### 7.4 Scenario smoke

Опционально (не как строгий unit):
- прогон greenfield realistic-v2 в двух режимах (static vs adaptive) и сравнение по одинаковому `sim_time_ms`:
  - committed_rate,
  - доля `ROUTING_NO_CAPACITY`,
  - clearing_volume,
  - средняя стоимость клиринга (если есть).

### 7.5 Оценка эффективности (автоматизируемые тесты)

Цель этого раздела — не просто проверить «policy не ломает систему», а **измеримо оценить**, даёт ли adaptive режим пользу относительно baseline `static`.

Важно: это должны быть **повторяемые** прогоны. Без этого любые выводы о committed_rate/отказах будут шумом.

#### 7.5.1 Правила воспроизводимости (обязательные)

1) Всегда сравнивать `static` vs `adaptive` на **чистой БД**.
   - В dev это означает DB reset (SQLite state сильно влияет на результаты).
   - В автотестах — использовать изолированный SQLite файл/engine (как в `test_simulator_clearing_no_deadlock.py`).

2) Фиксировать:
   - `scenario_id` (и версию фикстуры/сценария),
   - `seed` (у `RunRecord`) и/или набор seeds,
   - `intensity_percent` и длительность прогона,
   - одинаковый `SIMULATOR_CLEARING_MAX_DEPTH` и `SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS` (hard ceilings).

3) Сравнение проводить по **одинаковому горизонту**:
   - либо одинаковое число тиков,
   - либо одинаковый `sim_time_ms`.

4) Исключать warmup:
   - считать метрики только на интервале $[t_{warmup}, t_{end}]$.
   - практичное правило: первые `WARMUP_TICKS` (например 30) не учитываются при сравнении эффективности.

#### 7.5.2 Метрики эффективности (что именно сравниваем)

Минимальный набор (должен быть доступен без расширения OpenAPI):
- `committed_rate = committed / attempted` (за окно после warmup)
- `no_capacity_rate = rejected(ROUTING_NO_CAPACITY) / attempted` (за окно после warmup)
- `errors_total` и `internal_errors_total` (должны оставаться 0 в “нормальных” условиях)
- `clearing_count` и средний интервал между clearing (характеризует «цену» адаптивности)

Дополнительно (желательно, но допускается как “этап 2” наблюдаемости):
- `clearing_cost_ms` (per eq) и производные:
  - `clearing_yield = clearing_volume / max(1, clearing_cost_ms)`
- тренд `total_debt` (например, средняя дельта/наклон после warmup)

Примечание: в рамках тестов эффективность можно считать **из артефактов run** (summary/events) и/или из in-memory сигналов runner’а.

#### 7.5.3 Протокол сравнения (A/B)

Определения:
- A = baseline: `SIMULATOR_CLEARING_POLICY=static`
- B = candidate: `SIMULATOR_CLEARING_POLICY=adaptive`

Протокол:
1) Подготовить одинаковую начальную топологию (одинаковая seed community / сценарий) и одинаковый seed (или набор seeds).
2) Выполнить прогон A и прогон B на одинаковом горизонте.
3) Посчитать метрики на интервале после warmup.
4) Сравнить:
   - `no_capacity_rate` (в идеале должен снижаться или хотя бы не ухудшаться),
   - `committed_rate` (не должен ухудшаться существенно),
   - `clearing_count` и `clearing_cost_ms` (не должны «взрываться», budgets обязаны соблюдаться).

Чтобы избежать “ложных побед” от одного seed:
- запускать не менее `N_SEEDS=5` прогонов на разных seeds и сравнивать медианы.
- допустимый критерий: “B не хуже A по медиане committed_rate и лучше/не хуже по медиане no_capacity_rate при сопоставимой стоимости клиринга”.

Важно: exact пороги (например «+2pp committed_rate») зависят от сценария. В спецификации фиксируем **методику** и обязательные инварианты, а численные target’ы задаём как параметры benchmark suite.

#### 7.5.4 Какие автотесты добавить

1) Deterministic effectiveness (policy-level, строго)
- Новый файл: `tests/unit/test_simulator_adaptive_clearing_effectiveness_synthetic.py`
- Идея: симулировать последовательность тиков synthetic-сигналами (attempted/no_capacity/inflight/queue/yield) и измерять:
  - время реакции (через сколько тиков policy включает clearing после spike),
  - отсутствие «дребезга» (hysteresis/cooldown действительно ограничивает переключения),
  - корректный рост backoff при `clearing_volume≈0` подряд,
  - соблюдение clamp budgets.

2) System-level A/B benchmark (integration, медленный)
- Новый файл: `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py`
- Маркировать как `@pytest.mark.slow` (или аналогом, принятым в проекте), чтобы его можно было гонять отдельно.
- Требования к тесту:
  - использует изолированную SQLite БД (отдельный файл/engine),
  - запускает `RealRunner` на фиксированном числе тиков,
  - собирает summary-метрики (attempted/committed/rejection_codes_by_eq/clearing_count/optional cost),
  - assert’ы только на “не флак” инвариантах:
    - budgets соблюдены,
    - `errors_total == 0`,
    - “adaptive не деградирует committed_rate сильнее, чем на EPS” (EPS задаётся консервативно),
    - “adaptive не увеличивает no_capacity_rate сильнее, чем на EPS”.

3) Artifact-driven comparison (скрипт/снэпшот, не gating)
- На базе `scripts/run_simulator_run_and_analyze.py` добавить режим сравнения A/B, который:
  - гарантирует DB reset,
  - запускает A и B,
  - пишет сравнительный `ab_report.json` (с метриками after warmup).
- Это удобно для ручной калибровки threshold’ов policy и регрессии поведения.

#### 7.5.5 Минимальные критерии “эффективность измерена”

Считаем задачу по тестированию эффективности выполненной, если:
1) Есть детерминированные unit-тесты (synthetic), которые измеряют ключевые свойства контроллера.
2) Есть как минимум один system-level A/B прогон, который:
   - повторяем (clean DB + фиксированный seed),
   - формирует отчёт по метрикам after warmup,
   - проверяет не-флак инварианты (budgets/errors).
3) Результаты A/B (report) можно воспроизвести локально одной командой.

## 8) Acceptance criteria

1) `SIMULATOR_CLEARING_POLICY=static` — поведение полностью как сейчас.
2) `SIMULATOR_CLEARING_POLICY=adaptive`:
   - при искусственно повышенной доле `ROUTING_NO_CAPACITY` клиринг становится чаще (не нарушая cooldown);
   - при нулевом `clearing_volume` политика уходит в backoff (редеет);
   - клиринг не превышает заданные budgets и не ломает payment loop.
3) Unit tests policy покрывают основные переходы состояний.

4) Добавлена методика и автотесты/бенчмарки, позволяющие измерять эффективность (A/B static vs adaptive) на clean DB, с исключением warmup.

Дополнительно (risk note):
- При существенном учащении клиринга может усилиться `trust_growth` (TrustDriftEngine). Это не блокирует внедрение policy, но требует регрессии/наблюдения: лимиты не должны “разбухать” от одной лишь частоты клиринга.
