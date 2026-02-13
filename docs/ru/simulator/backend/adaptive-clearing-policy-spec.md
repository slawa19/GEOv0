# SPEC: Adaptive clearing policy (Real Mode)

Дата: 2026-02-12

Документ-основание (каноника): `adaptive-clearing-policy.md`.

> **Статус реализации (2026-02-13):**
> Все шаги из §9.10 выполнены. 262 unit + 56 integration тестов проходят (0 regressions).
> Smoke-тестирование на 3 сценариях завершено — policy работает по спеке.
> A/B benchmark (static vs adaptive) реализован и проходит.
> Документация обновлена (§9.11).

## 1) Цель ✅

Добавить в Real Mode симулятора **адаптивную политику клиринга** (feedback-control), чтобы:

- клиринг запускался чаще при признаках деградации ликвидности (например рост `ROUTING_NO_CAPACITY`),
- клиринг редел/останавливался при нулевой пользе (clearing_volume < `ZERO_VOLUME_EPS`),
- стоимость клиринга оставалась ограниченной (time budget, depth),
- поведение было объяснимым и устойчивым (hysteresis/cooldown).

## 2) Scope ✅

Включено:
- новый компонент policy, не привязанный к конкретному сценарию;
- интеграция в `RealRunner` (замена “каждые N тиков” на выбор policy, без ломки дефолта);
- сбор lightweight сигналов на тике, достаточных для policy;
- минимальная наблюдаемость (reason / counters), чтобы отлаживать.

Не включено:
- изменение UX/UI;
- “ML/оптимизатор” или подбор параметров offline;
- обязательное расширение OpenAPI метрик (можно как отдельный этап).

## 3) Backward compatibility ✅

- По умолчанию система работает как сейчас (фиксированный cadence).
- Адаптивный режим включается отдельным env knob.

## 4) Дизайн (компоненты) ✅

### 4.1 Новый модуль ✅

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

### 4.2 Сигналы (минимум) ✅

Policy получает на каждый тик (per eq):
- `attempted_payments_tick` (сколько попыток платежей в тике)
- `rejected_no_capacity_tick` (сколько отказов с кодом `ROUTING_NO_CAPACITY` в тике)
- `total_debt` (уже считается в runner; **MVP: передаётся в `TickSignals`, но не используется в решениях — зарезервирован для этапа 2**)
- `last_clearing_volume` (объём клиринга на последнем запуске клиринга для этого eq)
- `last_clearing_cost_ms` (стоимость последнего клиринга по времени для этого eq; **MVP: хранится в state, но не участвует в критерии решений — зарезервирован для этапа 2**)
- `in_flight`, `queue_depth` (guardrail)

Определения (для объяснимости):
- `no_capacity_rate_window = sum(rejected_no_capacity_tick) / max(1, sum(attempted_payments_tick))` за окно `WINDOW_TICKS`.
- `clearing_yield_last = last_clearing_volume / max(1, last_clearing_cost_ms)` — **MVP: определение зафиксировано, но yield не используется в решениях (только `volume ≈ 0` → backoff); подключение yield как критерия — этап 2.**

**MVP решения принимаются по двум факторам:**
1. `no_capacity_rate_window` (hysteresis HIGH/LOW) → активация/деактивация клиринга.
2. `clearing_volume ≈ 0` (ниже `ZERO_VOLUME_EPS = 1e-9`) → backoff с экспоненциальным ростом интервала.

Примечание: `total_debt` и `clearing_volume` уже пишутся как time-series в БД — policy может работать без БД, но эти метрики полезны для отладки.

### 4.3 Источник `ROUTING_NO_CAPACITY` ✅ (Вариант A)

Варианты (выбрать один, остальные отложить):

Вариант A (рекомендуемый, чистый контракт): расширить `RealPaymentsResult`.

- `RealPaymentsExecutor` уже вычисляет `rejection_code` в `_emit_if_ready()` **после** `map_rejection_code(...)`.
- Там же он должен инкрементировать per-eq счётчик по коду:
  - `rejection_codes_by_eq[eq][rejection_code] += 1`
- `RealPaymentsResult` расширяется полем:
  - `rejection_codes_by_eq: dict[str, dict[str, int]]`
- `RealRunner` получает `rejection_codes_by_eq` из результата и вычисляет `rejected_no_capacity_tick`.

Плюсы: без дополнительного IO, без парсинга `events.ndjson`, без скрытой мутации `run` из executor.

### 4.4 Интеграция в RealRunner ✅

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

### 4.5 Управление бюджетом клиринга ✅

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

## 5) Конфиг (env knobs) ✅

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

Hard-timeout (adaptive branch):
- Per-eq clearing вызов оборачивается в `asyncio.wait_for` с hard timeout = `max(2s, budget_ms * 4 / 1000)`, capped at 8s.
- При timeout clearing для данного eq считается zero-yield (→ backoff).

Дополнительно (cap на тик, чтобы избежать `N * timeout` при нескольких equivalents):
- Координатор может ограничивать общий clearing loop в рамках одного тика:
  - `SIMULATOR_CLEARING_ADAPTIVE_TICK_BUDGET_MS` (default: 0; 0 = disabled) — wall-clock бюджет на обработку всех eq в этом тике.
  - `SIMULATOR_CLEARING_ADAPTIVE_MAX_EQ_PER_TICK` (default: 0; 0 = disabled) — максимум eq, которые можно клирить за один тик.
  - При достижении любого лимита дальнейшие eq в этом тике пропускаются.

Валидация конфигурации:
- `__post_init__` проверяет: `0 <= low < high <= 1`, `window_ticks >= 1`, `min_interval_ticks >= 1`, `budget_min <= budget_max`. При нарушении — лог warning (не exception).

Cold-start (warmup fallback):
- Новый knob `warmup_fallback_cadence` (default: `clearing_every_n_ticks` от static, 0 = disabled).
- Пока `len(window) < window_ticks`, policy использует static cadence (`tick_index % warmup_fallback_cadence == 0`) с минимальным бюджетом.
- После заполнения окна — переключается на полную адаптивную логику.

Стратегия усреднения сигналов: rolling window (объяснимость)
- используем rolling window длиной `WINDOW_TICKS` (без EWMA).
- отдельный `alpha` не нужен.

## 6) Наблюдаемость и отладка ✅ (минимум)

Минимально требуемое:
- лог на tick decision: `reason`, `no_capacity_rate`, `cooldown_remaining`, `budget`.

Желательно (этап 2):
- сохранять `no_capacity_rate` и `clearing_cost_ms` в `simulator_run_metrics` (потребует расширения `MetricSeriesKey` и OpenAPI).

Примечание: `clearing_cost_ms` полезен даже без сохранения как метрика — он нужен policy для yield.

## 7) Тестирование ✅

### 7.1 Unit tests (policy) ✅

Новые тесты:
- `tests/unit/test_simulator_adaptive_clearing_policy.py`

Покрыть сценарии:
1) `no_capacity_rate` растёт выше `high` → policy включает clearing, уважая cooldown.
2) `no_capacity_rate` падает ниже `low` → policy выключает clearing (hysteresis).
3) `clearing_volume` < `ZERO_VOLUME_EPS` несколько раз подряд → backoff растёт.
4) `in_flight`/`queue_depth` выше порога → **координатор** откладывает клиринг (guardrail; тестируется на уровне coordinator, не policy).
5) Cold-start: warmup fallback выставляет periodic cadence пока окно не заполнено.

### 7.2 Integration tests (runner) ✅

Новые/расширенные тесты (примерные направления):
- симулировать тик-цикл с подменой `RealClearingEngine` (spy) и проверить, что он вызывается при нужных сигналах;
- проверить, что `static` режим не меняет поведение.

### 7.3 Regression: SSE contracts ✅

Проверить существующие интеграционные тесты SSE (`tx.updated`, `clearing.*`) — адаптивность не должна ломать payload.

### 7.4 Scenario smoke ✅

Опционально (не как строгий unit):
- прогон greenfield realistic-v2 в двух режимах (static vs adaptive) и сравнение по одинаковому `sim_time_ms`:
  - committed_rate,
  - доля `ROUTING_NO_CAPACITY`,
  - clearing_volume,
  - средняя стоимость клиринга (если есть).

### 7.5 Оценка эффективности (автоматизируемые тесты) ✅

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

## 8) Acceptance criteria ✅

1) ✅ `SIMULATOR_CLEARING_POLICY=static` — поведение полностью как сейчас.
2) ✅ `SIMULATOR_CLEARING_POLICY=adaptive`:
   - при искусственно повышенной доле `ROUTING_NO_CAPACITY` клиринг становится чаще (не нарушая cooldown);
   - при нулевом `clearing_volume` политика уходит в backoff (редеет);
   - клиринг не превышает заданные budgets и не ломает payment loop.
3) ✅ Unit tests policy покрывают основные переходы состояний.

4) ✅ Добавлена методика и автотесты/бенчмарки, позволяющие измерять эффективность (A/B static vs adaptive) на clean DB, с исключением warmup.

Дополнительно (risk note):
- При существенном учащении клиринга может усилиться `trust_growth` (TrustDriftEngine). Это не блокирует внедрение policy, но требует регрессии/наблюдения: лимиты не должны “разбухать” от одной лишь частоты клиринга.
## 9) Implementation addendum (решения по интеграции) ✅

Дата: 2026-02-13

Результат сверки спеки с реальным кодом. Фиксирует решения по integration points, не описанным в основной части.

### 9.1 Точка вставки — `RealTickClearingCoordinator`, а не `RealRunner`

Основная спека в §4.4 абстрактно описывает «изменить место, где решается "пора ли клирить"». В реальном коде это:

- `RealTickClearingCoordinator.maybe_run_clearing()` — файл `app/core/simulator/real_tick_clearing_coordinator.py`
- Именно здесь live-check `tick_index % N != 0`

Решение:
- **Не создавать** параллельный coordinator.
- Расширить `RealTickClearingCoordinator`:
  - конструктор принимает `clearing_policy: Literal["static", "adaptive"]` и опциональный `AdaptiveClearingPolicyConfig`.
  - при `policy == "static"` — текущая логика без изменений.
  - при `policy == "adaptive"` — `maybe_run_clearing()` делегирует решение в `AdaptiveClearingPolicy.evaluate()` per-eq (см. §9.3).
- `AdaptiveClearingState` хранится **на координаторе** (не на `RunRecord`), т.к. он уже является stateful per-run объектом.

### 9.2 Прокидывание `time_budget_ms` и `max_depth` per-call

Сейчас `RealClearingEngine.tick_real_mode_clearing()` использует `self._clearing_max_depth_limit` и `self._real_clearing_time_budget_ms`, зафиксированные в конструкторе. Per-eq адаптивные budgets требуют per-call override.

Решение:
- Добавить в сигнатуру `tick_real_mode_clearing()` optional kwargs:
  ```python
  async def tick_real_mode_clearing(
      self,
      ...,
      time_budget_ms_override: int | None = None,
      max_depth_override: int | None = None,
  ) -> dict[str, float]:
  ```
- Внутри метода: `effective_budget = time_budget_ms_override or self._real_clearing_time_budget_ms`.
- Конструкторные значения остаются **hard ceiling** (guardrails из §4.5 спеки).
- Это минимальный рефактор — не ломает существующие call sites (`None` → прежнее поведение).

### 9.3 Per-eq решения: разбиение clearing loop

Сейчас `maybe_run_clearing()` вызывает один `run_clearing()` на все equivalents разом. Policy из §4.1 требует per-eq решений (разные `should_run`, разные budgets).

Решение:
- В `adaptive` ветке координатора: **не** вызывать единый `run_clearing()`.
- Вместо этого — цикл по equivalents:
  ```python
  for eq in equivalents:
      decision = policy.evaluate(eq, signals[eq])
      if not decision.should_run:
          continue
      volume = await run_clearing_for_eq(
          eq,
          time_budget_ms_override=decision.time_budget_ms,
          max_depth_override=decision.max_depth,
      )
      clearing_volume_by_eq[eq] = volume
  ```
- Для этого нужна **per-eq версия** `tick_real_mode_clearing()`. Текущий метод уже содержит `for eq in equivalents:` loop внутри — его нужно разделить:
  - вынести inner loop body в `_clear_single_equivalent(eq, ..., time_budget_ms, max_depth)` (private)
  - оставить `tick_real_mode_clearing()` как обёртку (не ломает `static` mode)
- `run_clearing_for_eq` лямбда передаётся в coordinator из orchestrator (как сейчас передаётся `run_clearing`).

Следствие: в `static` mode по-прежнему один вызов `tick_real_mode_clearing()` с полным списком equivalents.

### 9.4 Источник и хранение `last_clearing_volume` / `last_clearing_cost_ms`

Спека перечисляет их как сигналы (§4.2), но не описывает, кто записывает.

Решение:
- `AdaptiveClearingState` хранит per-eq:
  ```python
  @dataclass
  class PerEqClearingHistory:
      last_clearing_volume: float = 0.0
      last_clearing_cost_ms: float = 0.0
      last_clearing_tick: int = -1
  ```
- **Координатор** записывает после каждого clearing вызова:
  ```python
  cost_ms = (time.monotonic() - t0) * 1000.0
  state.update_clearing_result(eq, volume=volume, cost_ms=cost_ms, tick=tick_index)
  ```
- Данные не персистятся в БД — они transient (per-run in-memory). При restart run они обнуляются — policy начинает в «cold start» (первые `WINDOW_TICKS` тиков без данных → fallback к static cadence через `warmup_fallback_cadence`; при `warmup_fallback_cadence=0` policy не клирит пока не соберёт сигналы).

### 9.5 `in_flight` и `queue_depth` — глобальные, а не per-eq

В текущем коде:
- `run.queue_depth` — скаляр (общий)
- `len(run._real_in_flight)` — один общий counter

Per-eq in_flight breakdown нетривиален и не нужен для MVP guardrail (цель: «не запускать clearing, если система перегружена»).

Решение:
- Policy принимает **глобальные** `in_flight` и `queue_depth`.
- Guardrail проверяется **один раз перед eq-loop** (не per-eq):
  ```python
  if in_flight > threshold or queue_depth > threshold:
      return {eq: 0.0 for eq in equivalents}  # skip all clearing this tick
  ```
- Per-eq breakdown отложен (этап 2, если понадобится).

### 9.6 `attempted_payments_tick` per-eq — вычисляется как сумма

`RealPaymentsResult.per_eq[eq]` содержит `committed`, `rejected`, `errors`, `timeouts`, но не `attempted`.

Решение:
- Policy вычисляет `attempted = committed + rejected + errors + timeouts` из `per_eq[eq]`.
- Отдельное поле `attempted` в `RealPaymentsResult` **не добавляем** — избыточно.

### 9.7 Lifecycle `AdaptiveClearingState`

Решение:
- Создаётся в `RealTickClearingCoordinator.__init__()` при `policy == "adaptive"`. При `static` — `None`.
- Живёт as long as coordinator (= as long as runner = as long as run).
- При stop/restart run — coordinator пересоздаётся → state обнуляется.
- Per-eq entries создаются lazily при первом `evaluate(eq, ...)`.

### 9.8 Rejection codes → signals pipeline

Спека §4.3 выбирает Вариант A. Конкретная реализация:

1. **`RealPaymentsExecutor._emit_if_ready()`**: после `map_rejection_code(err_details)` на [строке 369](app/core/simulator/real_payments_executor.py#L369) — инкрементировать in-memory counter:
   ```python
   self._rejection_codes_by_eq[eq][rejection_code] += 1
   ```
   Где `_rejection_codes_by_eq: dict[str, dict[str, int]]` — `defaultdict(lambda: defaultdict(int))`, обнуляется в `execute()` начале каждого tick batch.

2. **`RealPaymentsResult`**: добавить поле `rejection_codes_by_eq: dict[str, dict[str, int]]`.

3. **Coordinator**: извлекает `rejected_no_capacity_tick` per-eq:
   ```python
   rejected_no_capacity = result.rejection_codes_by_eq.get(eq, {}).get("ROUTING_NO_CAPACITY", 0)
   ```

### 9.9 Передача signals из payments phase в clearing coordinator

Сейчас `tick_real_mode()` в orchestrator вызывает payments → clearing последовательно, но payments result явно не передаётся в clearing coordinator.

Решение:
- `maybe_run_clearing()` получает дополнительный kwarg:
  ```python
  payments_result: RealPaymentsResult | None = None,
  ```
- В `static` mode — игнорируется.
- В `adaptive` mode — coordinator извлекает signals и передаёт в `policy.evaluate()`.
- Orchestrator передаёт `payments_phase` result в `maybe_run_clearing()`.

### 9.10 Порядок реализации (рекомендуемый)

| Шаг | Что | Файлы | Статус |
|-----|-----|-------|--------|
| 1 | `AdaptiveClearingPolicy` + `Config` + `State` + `Decision` (чистая логика) | `app/core/simulator/adaptive_clearing_policy.py` | ✅ |
| 2 | Unit tests policy (§7.1) | `tests/unit/test_simulator_adaptive_clearing_policy.py` | ✅ (17 тестов) |
| 3 | `rejection_codes_by_eq` в `RealPaymentsExecutor` + `RealPaymentsResult` | `app/core/simulator/real_payments_executor.py` | ✅ |
| 4 | `_clear_single_equivalent()` extract в `RealClearingEngine` + per-call overrides | `app/core/simulator/real_clearing_engine.py` | ✅ |
| 5 | Интеграция в `RealTickClearingCoordinator` (adaptive branch) | `app/core/simulator/real_tick_clearing_coordinator.py` | ✅ |
| 6 | Env knobs в `RealRunnerImpl.__init__()` + передача в coordinator | `app/core/simulator/real_runner_impl.py` | ✅ |
| 7 | Orchestrator: прокидывание `payments_result` → coordinator | `app/core/simulator/real_tick_orchestrator.py` | ✅ |
| 8 | Integration tests + A/B benchmark | `tests/integration/test_simulator_adaptive_clearing_*.py` | ✅ |
| 9 | Regression: SSE contract tests (прогон существующих) | — | ✅ (52 теста) |
| 10 | Обновление документации (§9.11) | см. ниже | ✅ |

### 9.11 Обновление документации после реализации ✅

После завершения реализации необходимо обновить следующие документы:

#### Обязательные (blocking merge)

| Документ | Что обновить |
|----------|-------------|
| `docs/ru/simulator/backend/runner-algorithm.md` | §«clearing_attempt»: добавить описание `adaptive` policy ветки, env knob `SIMULATOR_CLEARING_POLICY`, per-eq loop; обновить ссылки на новые env knobs (§5 спеки) |
| `docs/ru/simulator/backend/real-mode-runbook.md` | Секция env vars: добавить все новые `SIMULATOR_CLEARING_ADAPTIVE_*` knobs с описанием и дефолтами; описать cold-start поведение (fallback к static cadence на warmup) |
| `docs/ru/simulator/backend/adaptive-clearing-policy.md` | Каноника. Обновить статус: «реализовано». Добавить ссылку на спеку (`adaptive-clearing-policy-spec.md`). При расхождениях между каноникой и фактической реализацией — привести в соответствие |
| `docs/ru/09-decisions-and-defaults.md` | §1.10 (Simulator defaults): добавить `SIMULATOR_CLEARING_POLICY=static` (default), перечислить новые env knobs с дефолтами |
| `docs/ru/simulator/backend/test-plan.md` | Добавить новые тестовые файлы: `test_simulator_adaptive_clearing_policy.py`, `test_simulator_adaptive_clearing_effectiveness_synthetic.py`, `test_simulator_adaptive_clearing_effectiveness_ab.py` |
| `.github/copilot-instructions.md` | Если добавляются новые env knobs, влияющие на dev workflow — упомянуть в Quick Start / Guardrails |

#### Желательные (после merge, этап 2)

| Документ | Что обновить |
|----------|-------------|
| `docs/ru/simulator/backend/observability.md` | Добавить описание новых log-строк policy (`reason`, `no_capacity_rate`, `cooldown_remaining`, `budget`); если расширяется `MetricSeriesKey` — задокументировать новые ключи |
| `docs/ru/simulator/backend/simulator-domain-model.md` | Если расширяется `MetricSeriesKey` (`no_capacity_rate`, `clearing_cost_ms`) — обновить перечень метрик в §Metric time-series |
| `docs/ru/simulator/backend/real-runner-decomposition-spec.md` | Описать расширение `RealTickClearingCoordinator` (adaptive state, per-eq loop), новый модуль `adaptive_clearing_policy.py` |
| `api/openapi.yaml` | Если новые `MetricSeriesKey` попадают в API — обновить enum `MetricSeriesKey` |
| `docs/ru/simulator/README.md` | Уже есть ссылка на `adaptive-clearing-policy.md`. Добавить ссылку на спеку, если нужно |

#### Правило верификации

Перед merge PR с реализацией:
1. Запустить `grep -r "CLEARING_EVERY_N_TICKS\|CLEARING_POLICY\|CLEARING_ADAPTIVE" docs/` — убедиться, что все упоминания clearing cadence согласованы с новой реальностью.
2. Убедиться, что `adaptive-clearing-policy.md` (каноника) не противоречит фактическому поведению.
3. Env knobs, перечисленные в `real-mode-runbook.md`, должны совпадать с теми, что реально читаются в коде.
