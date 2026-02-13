# Adaptive clearing policy (Real Mode): динамическая периодичность клиринга

Дата: 2026-02-12

> **Статус: реализовано (2026-02-13).**
> Модуль: `app/core/simulator/adaptive_clearing_policy.py`.
> Интеграция: `RealTickClearingCoordinator` (adaptive branch).
> Спецификация (с деталями реализации): `docs/ru/simulator/backend/archive/adaptive-clearing-policy-spec--archived-2026-02-13.md`.
> Тесты: `tests/unit/test_simulator_adaptive_clearing_policy.py` (17), `tests/unit/test_simulator_adaptive_clearing_effectiveness_synthetic.py` (12), `tests/integration/test_simulator_adaptive_clearing_integration.py` (3), `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py` (1).

## 0) Зачем это нужно

Фиксированная периодичность клиринга (например «каждые N тиков») удобна для MVP и демо, но **не является универсальной**:

- разные комьюнити/сценарии имеют разную плотность графа, распределение лимитов, интенсивность и направленность потоков;
- клиринг имеет **стоимость** (DB/CPU/локи/latency) и может конкурировать с платежами;
- реальная сеть не стационарна: пики/провалы активности требуют разной «частоты разрядки» долгов.

Поэтому вместо “подбора N тестами” вводим **адаптивную политику клиринга** — контроллер, который:

1) наблюдает состояние сети (простые сигналы),
2) решает, когда запускать клиринг и с каким бюджетом,
3) стабилизирует ликвидность при ограничении стоимости.

## 1) Термины

- **Tick** — шаг RealRunner (см. `SIMULATOR_TICK_MS_BASE`).
- **Cadence** — “как часто” инициировать clearing_attempt.
- **Debt pressure** — индикатор, что сеть «забита долгами» и ёмкости маршрутов деградируют.
- **Clearing yield** — насколько клиринг «полезен» (сколько долга схлопнул) относительно своей стоимости.

## 2) Принцип: feedback-control (автопилот)

Адаптивная политика — это не оптимизация “в вакууме”, а **обратная связь**:

- если сеть упирается (много `ROUTING_NO_CAPACITY`, растёт `total_debt`) — клиринг запускается чаще/глубже;
- если клиринг не приносит эффекта (clearing_volume ≈ 0) — клиринг редеет, чтобы не тратить ресурсы;
- если сеть стабильна — клиринг работает только как “фон”/страховка.

Это универсально: политика не «знает» сценарий заранее, а подстраивается по наблюдаемым сигналам.

## 3) Сигналы (что измеряем)

### 3.1 Сигналы ликвидности

Минимальный набор для Real Mode:

1) `no_capacity_rate` — доля отказов платежей с кодом `ROUTING_NO_CAPACITY` за окно W тиков.
   - Это прямой индикатор того, что **маршрутизация не находит ёмкость**.
   - Источник: `tx.failed.error.code` (эмитится уже сейчас) или внутренние счётчики на тике.

2) `total_debt` (по эквиваленту) — уже пишется как метрика.
  - Используем **тренд** (наклон) по rolling window, а не абсолют (абсолют зависит от масштаба комьюнити).

### 3.2 Сигналы стоимости

1) `clearing_cost_ms` — время выполнения clearing_attempt (по эквиваленту).
   - Важно для защиты от деградации throughput.
   - Источник: измерение времени в `RealClearingEngine`.

2) “конкуренция” — если в тике высокий `in_flight`, клиринг можно отложить (guardrail).

### 3.3 Сигналы полезности

1) `clearing_volume` (по эквиваленту) — уже считается и пишется как метрика.
2) `clearing_yield` = `clearing_volume / max(1, clearing_cost_ms)` (или другой нормализатор).

## 4) Решения (что контролируем)

Политика возвращает решения **per-equivalent** (потому что в реальном графе разные эквиваленты могут иметь разную плотность долгов и разную “ёмкость”):

1) `should_run_clearing_now` (да/нет)
2) `budget`:
   - `time_budget_ms` (верхняя граница времени клиринга)
   - `max_depth` (глубина поиска циклов)
   - дополнительные лимиты (например max FX edges для визуализации)

Примечание: в текущем коде `RealClearingEngine` уже поддерживает `real_clearing_time_budget_ms`, а `max_depth` ограничен `SIMULATOR_CLEARING_MAX_DEPTH`.

## 5) Рекомендуемая политика (правила, без ML)

### 5.1 Layering: триггерный + периодический

Для runtime GEO (не только симулятора) базовая архитектурная идея такая:

- **триггерный клиринг** коротких циклов (3–4) “рядом” с успешной транзакцией — часто и дёшево;
- **периодический** (фон) клиринг более дорогих циклов — реже.

В симуляторе Real Mode сейчас проще: есть `clearing_attempt(eq)` на тике. Адаптивная политика делает этот запуск “то чаще, то реже” и меняет бюджет.

### 5.2 Hysteresis + cooldown (стабильность)

Чтобы избежать “дёргания” (on/off каждый тик):

- два порога для `no_capacity_rate`: `high` и `low`;
- минимальный интервал между клирингами `min_interval_ticks` (cooldown);
- rolling window (по умолчанию) вместо мгновенных значений.

### 5.3 Backoff, если клиринг бесполезен

Если `clearing_volume` стабильно ≈ 0 (несколько запусков подряд), политика увеличивает интервал (делает реже) и/или снижает бюджет.

Это защищает от ситуации: «мы постоянно пытаемся клирить, но циклов нет».

Рекомендуемая формула backoff (объяснимая и устойчивая):

- пусть `zero_volume_streak` — число подряд запусков клиринга с `clearing_volume≈0`.
- тогда `effective_interval_ticks = min(backoff_max_interval_ticks, min_interval_ticks * 2^zero_volume_streak)`.
- при первом же полезном клиринге (`clearing_volume > 0`) streak сбрасывается.

Примечание: `clearing_yield = clearing_volume / max(1, clearing_cost_ms)` полезен для логов и сравнения сценариев,
но в MVP backoff считаем именно по `clearing_volume≈0` (это проще, детерминированнее и не зависит от наличия `clearing_cost_ms`).

### 5.4 Escalation, если сеть “забита”

Если `no_capacity_rate` высокий и/или `total_debt` растёт:

- клиринг запускается чаще (но уважает cooldown);
- бюджет может расти ступенчато (например `max_depth` ↑, `time_budget_ms` ↑) до лимитов.

## 6) Встраивание в текущую кодовую базу (архитектурно)

### 6.1 Новый компонент

Добавляем отдельный модуль (чтобы не раздувать orchestrator):

- `app/core/simulator/adaptive_clearing_policy.py`
  - `AdaptiveClearingPolicy` — чистая логика принятия решений
  - `AdaptiveClearingState` — состояние контроллера на run (rolling-window counters, last_clearing_tick, backoff)

Контракт (идея):

- вход: `tick_index`, последние агрегаты по эквиваленту (успехи/отказы), `total_debt`, `clearing_volume`, `in_flight`, `queue_depth`.
- выход: `ClearingDecision` (run? budget?).

### 6.2 Точки интеграции

- `RealRunner` (orchestrator):
  - вместо прямого “каждые N тиков” вызывает policy и решает, запускать ли `RealClearingEngine.tick_real_mode_clearing`.

- `RealPaymentsExecutor`:
  - уже знает `tx.failed.error.code` (например `ROUTING_NO_CAPACITY`), поэтому может собирать per-tick счётчики по кодам для policy.
  - важно: это должно быть lightweight и не требовать дополнительного DB/IO.

- `RealClearingEngine`:
  - измеряет `clearing_cost_ms` и возвращает его (или логирует) для policy.

- `RealTickPersistence`/`storage.write_tick_metrics`:
  - по желанию: сохранять дополнительные метрики (`clearing_cost_ms`, `no_capacity_rate`) как time-series.
  - не обязательно для самого контроллера; полезно для отладки и UI.

## 7) Конфигурация (предлагаемые env knobs)

Цель: безопасный дефолт + возможность тюнинга.

- `SIMULATOR_CLEARING_POLICY`: `static|adaptive` (дефолт `static` для backward compatibility)
- `SIMULATOR_CLEARING_EVERY_N_TICKS`: используется в `static`

Adaptive:
- `SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS` (например 30)
- `SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH` (например 0.60)
- `SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW` (например 0.30)
- `SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS` (например 5)
- `SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS` (например 60)

Guardrails (adaptive):
- `SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD` (0 = выключено)
- `SIMULATOR_CLEARING_ADAPTIVE_QUEUE_DEPTH_THRESHOLD` (0 = выключено)

Бюджеты:
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN` / `MAX`
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN` / `MAX`

Рекомендуемые дефолты (ориентиры для реализации):
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN=3`
- `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MAX=6`
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN=50`
- `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MAX=250`

Взаимодействие с существующими лимитами (важно):
- `SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS` и `SIMULATOR_CLEARING_MAX_DEPTH` считаются **hard ceiling**.
- adaptive policy может только выбирать значения *внутри* этих потолков.

Cold-start (первые тики):
- пока окно наблюдений меньше `WINDOW_TICKS`, используем оценки по доступным данным и не делаем агрессивных решений;
- допускается “консервативный” fallback к `static` cadence на старте (чтобы не недоклирить сеть), но с уважением cooldown.

Примечание про TrustDrift:
- учащение клиринга может усилить `trust_growth` (если growth завязан на события клиринга). Это не запрещает adaptive policy, но требует регрессионной проверки: лимиты не должны «разбухать» от одной лишь частоты клиринга.

## 8) Почему это эффективно

1) **Универсальность:** решения зависят от наблюдаемой динамики, а не от “магического N” для одного сценария.
2) **Экономия ресурсов:** клиринг активен только когда приносит пользу; при нулевом yield — backoff.
  - Для MVP “нулевой yield” трактуем как `clearing_volume≈0`.
3) **Стабильность:** hysteresis + cooldown предотвращают «пилообразную» нагрузку.
4) **Отладка:** политика объяснима: можно вывести причины решения (`reason`), графики сигналов и yield.

## 9) Связанные документы

- Алгоритм runner и роль `clearing_attempt`: `runner-algorithm.md`
- Канонические метрики: `simulator-domain-model.md`
- Run storage: `run-storage.md`
