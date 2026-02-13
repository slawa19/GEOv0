# Addendum: clarifications for Adaptive clearing policy (Real Mode)

Дата: 2026-02-13

Этот документ уточняет двусмысленные места в [`docs/ru/simulator/backend/adaptive-clearing-policy-spec.md`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:1) и вводит **нормативные** формулировки (MUST/SHOULD) для реализации и тестов.

## 0) Термины и обозначения

- `eq` — эквивалент (equivalent).
- `tick_index` — индекс тика симуляции.
- `WINDOW_TICKS` — длина rolling window для расчёта rate.
- `attempted_tick(eq)` — число попыток платежей за тик для `eq`.
- `rejected_no_capacity_tick(eq)` — число отказов за тик для `eq` с кодом `ROUTING_NO_CAPACITY`.
- `no_capacity_rate_window(eq)` — агрегированный за окно показатель:
  - `sum(rejected_no_capacity_tick) / max(1, sum(attempted_tick))`.
  - Формула закреплена в спека-документе: [`no_capacity_rate_window`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:75).
- `ZERO_VOLUME_EPS` — порог нулевого объёма, закреплён: [`ZERO_VOLUME_EPS = 1e-9`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:80).

## 1) Нормативная семантика budgets (None и clamp)

### 1.1 Decision budgets: None MUST трактоваться детерминированно

В `ClearingDecision` поля `time_budget_ms` и `max_depth` допускают `None` ([`ClearingDecision`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:49)).

**Уточнение (MUST):**
- Если `decision.time_budget_ms is None`, то для этого вызова используется `SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN`.
- Если `decision.max_depth is None`, то для этого вызова используется `SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN`.

**Мотивация:** минимизация стоимости клиринга по умолчанию соответствует целям спеки (ограничение стоимости, объяснимость) и warmup-ветке, где явно сказано «с минимальным бюджетом» ([`warmup fallback`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:171)).

### 1.2 Clamp MUST применяться всегда, до вызова clearing engine

Clamp-правило закреплено в спека-документе: [`clamp budgets`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:127).

**Уточнение (MUST):**
- `effective_time_budget_ms` вычисляется по clamp-правилу даже если исходный budget получен из `None` (см. 1.1).
- `effective_max_depth` вычисляется по clamp-правилу даже если исходный depth получен из `None`.

## 2) Приоритеты ограничителей: hysteresis, min interval (= cooldown), backoff

### 2.1 Hysteresis определяет состояние «клиринг активен»

Спека фиксирует hysteresis HIGH/LOW как фактор решения ([`MVP фактор 1`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:79)).

**Уточнение (MUST):**
- Policy хранит per-eq состояние `is_clearing_active`.
- Обновление `is_clearing_active` на тике происходит так:
  - если `no_capacity_rate_window >= HIGH` → `is_clearing_active = True`;
  - else если `no_capacity_rate_window < LOW` → `is_clearing_active = False`;
  - else → сохраняем предыдущее значение (`hold`).

### 2.2 Cooldown в MVP эквивалентен min_interval_ticks

Спека в тексте использует термин cooldown ([`goal: hysteresis/cooldown`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:20)), а в env knobs явно задан `MIN_INTERVAL_TICKS` ([`min_interval_ticks`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:139)).

**Уточнение (MUST):**
- В MVP отдельного параметра cooldown не вводится.
- `SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS` одновременно является:
  - минимальным интервалом между двумя clearing-вызовами для одного `eq`;
  - «cooldown»-ограничителем частоты.

### 2.3 Backoff MUST ограничивать частоту даже при активном hysteresis

Спека фиксирует backoff при «нулевом объёме» как второй фактор ([`MVP фактор 2`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:80)).

**Уточнение (MUST):**
- Если `is_clearing_active=True`, это означает «в принципе разрешено пытаться клирить», но фактический запуск клиринга дополнительно ограничивается `next_allowed_tick(eq)`, вычисляемым из min interval и backoff.

## 3) Формула backoff и правила reset

### 3.1 Backoff MUST быть экспоненциальным и детерминированным

Спека требует «экспоненциальный рост интервала» ([`backoff`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:80)) и задаёт верхнюю границу интервала ([`BACKOFF_MAX_INTERVAL_TICKS`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:140)).

**Уточнение (MUST):**
- Policy хранит per-eq счётчик `zero_volume_streak`.
- После каждого клиринга policy обновляет streak:
  - если `last_clearing_volume < ZERO_VOLUME_EPS` → `zero_volume_streak += 1`;
  - иначе → `zero_volume_streak = 0`.
- Интервал до следующего разрешённого клиринга для eq:
  - `interval_ticks = min(BACKOFF_MAX_INTERVAL_TICKS, MIN_INTERVAL_TICKS * (2 ** max(0, zero_volume_streak - 1)))`.
- `next_allowed_tick = last_clearing_tick + interval_ticks`.

Примечание:
- при `zero_volume_streak == 0` интервал равен `MIN_INTERVAL_TICKS`, то есть backoff «не ухудшает» базовую частоту;
- при `zero_volume_streak == 1` интервал также равен `MIN_INTERVAL_TICKS` (первый «нулевой» клиринг не делает систему более консервативной, чем базовый cooldown).

## 4) Timeout MUST трактоваться как zero-volume для backoff

Спека требует hard-timeout per-eq ([`hard-timeout`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:156)) и говорит, что timeout считается zero-yield и ведёт к backoff ([`timeout -> backoff`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:158)), при этом yield не используется в решениях MVP ([`yield не используется`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:76)).

**Уточнение (MUST):**
- Если per-eq clearing завершился по timeout, то для целей MVP backoff это MUST трактоваться как:
  - `last_clearing_volume = 0.0` (то есть «нулевой объём»);
  - `last_clearing_cost_ms = hard_timeout_ms` (или фактическое elapsed; но значение MUST быть > 0).

## 5) Warmup fallback: точное поведение

Спека описывает warmup fallback ([`cold-start`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:169)).

**Уточнение (MUST):**
- Пока rolling window не заполнено (`len(window) < WINDOW_TICKS`), policy НЕ применяет hysteresis и backoff.
- В warmup:
  - если `warmup_fallback_cadence == 0` → `should_run=False`;
  - иначе `should_run = (tick_index % warmup_fallback_cadence == 0)`.
- Если `should_run=True` в warmup, budgets MUST быть минимальными (см. раздел 1.1).

**Уточнение (MUST, guardrail устойчивости):**
- Несмотря на то, что в warmup «не применяются hysteresis и backoff как критерии включения/выключения», policy MUST уважать `MIN_INTERVAL_TICKS` (cooldown) между двумя clearing-вызовами одного `eq`.
- Policy MAY уважать ранее накопленный backoff, если клиринг уже происходил и давал `last_clearing_volume < ZERO_VOLUME_EPS` или завершался ошибкой/timeout.

## 6) Missing data MUST деградировать безопасно

Спека требует устойчивость к missing данным ([`устойчивость к missing`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:57)), но не фиксирует конкретное поведение.

**Уточнение (MUST):**
- Missing/None значения сигналов трактуются как 0 (attempted, rejected_no_capacity, in_flight, queue_depth, last_clearing_*).
- Если `attempted_window_sum == 0`, то `no_capacity_rate_window` MUST быть 0.
- Rolling window MUST пополняться каждый тик (включая тики без платежей), чтобы warmup завершался детерминированно.

## 7) Guardrails и логирование причин

Guardrails находятся на координаторе, а не policy ([`guardrail scope`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:199)) и могут пропустить клиринг целиком на тик ([`skip all`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:431)). Спека также требует минимальный лог на tick decision ([`наблюдаемость`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:180)).

**Уточнение (MUST):**
- Если guardrail сработал до per-eq evaluate, координатор MUST записать лог-строку с `reason=CLEARING_SKIPPED_GUARDRAIL` и значениями `in_flight`/`queue_depth`.
- При guardrail skip-all policy.evaluate MAY не вызываться.

## 8) Canonical reason codes (для объяснимости и тестов)

Спека требует `reason: str` ([`ClearingDecision.reason`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:51)).

**Уточнение (SHOULD):** reason должен быть одним из фиксированного набора (строковые константы):
- `WARMUP_FALLBACK_RUN` / `WARMUP_FALLBACK_SKIP`
- `RATE_HIGH_ENTER` / `RATE_LOW_EXIT` / `RATE_HOLD`
- `SKIP_NOT_ACTIVE`
- `SKIP_MIN_INTERVAL`
- `SKIP_BACKOFF`
- `RUN_ACTIVE`
- `RUN_ACTIVE_AFTER_BACKOFF`

Для координатора (не policy):
- `CLEARING_SKIPPED_GUARDRAIL`
- `CLEARING_SKIPPED_TICK_BUDGET`
- `CLEARING_SKIPPED_MAX_EQ_PER_TICK`

## 9) Требования к тестам (корректность уточнений)

**MUST покрыть unit-тестами policy:**
- None budgets → использование MIN и корректный clamp.
- Порядок: hysteresis определяет активность, min_interval/backoff ограничивают частоту.
- Экспоненциальный backoff по формуле и reset streak при ненулевом объёме.
- Timeout → трактуется как zero-volume (backoff растёт).
- Warmup fallback семантика (cadence=0 и cadence>0).
- Missing data → безопасная деградация (no exceptions, rate=0, окно заполняется).

**MUST покрыть тестами координатора:**
- Guardrail skip-all → reason/log.
- Tick budget/max eq per tick → остальные eq пропускаются детерминированно.

---

## 10) Что НЕ меняется (явные non-goals)

- Не вводятся новые критерии решения по `clearing_yield_last` или `total_debt` (этап 2). См. [`yield не используется`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:76) и [`total_debt MVP: не используется`](docs/ru/simulator/backend/adaptive-clearing-policy-spec.md:69).
- Контракт SSE для клиринга: используется только `clearing.done` (с `cycle_edges` и `cleared_amount`), без `clearing.plan`.
