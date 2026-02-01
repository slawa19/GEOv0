# Спецификация: Behavior Model (Real Mode) — «полноценная модель поведения экономики»

**Версия:** 1.0  
**Дата:** 2026-02-01  
**Статус:** Draft

Цель: реализовать интерпретацию `behaviorProfiles` и `events` для **Real Mode** так, чтобы:
- модель поведения стала реалистичной (роли, суммы, частоты, адресаты);
- система оставалась стабильной (guardrails, детерминизм, отсутствие «взрывного» спама rejected);
- никакие протокольные инварианты не нарушались.

---

## 0) Статус реализации (на 2026-02-01)

### 0.1 Реализовано (код)
- `SIMULATOR_REAL_AMOUNT_CAP` (backward‑compatible default = `3.00`) и применение в выборе суммы.
- Интерпретация `behaviorProfiles.props` в planner (real mode):
  - `tx_rate`
  - `equivalent_weights`
  - `recipient_group_weights`
  - `amount_model[eq]` (используется `min/max/p50`; `p90` зарезервирован)
- Выбор receiver на основе достижимости (bounded BFS) + групповые веса.
- Prefilter суммы: `amount <= max_outgoing_limit(sender, eq)` (снижение шума rejected).

Точки реализации:
- Planner + amount: `app/core/simulator/real_runner.py` (`RealRunner._plan_real_payments`, `RealRunner._real_pick_amount`)

### 0.2 Реализовано (fixtures + runtime)
- Добавлен сценарий: `fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json`.
- Сценарий включён в дефолтный allowlist: `app/core/simulator/runtime_impl.py`.

### 0.3 Не реализовано (в этой версии; описано ниже как future)
Реализовано частично:
- `events[]: note/stress` — реализована минимальная интерпретация (см. раздел 6).
- `events[]: inject` — реализовано (warmup debts), но **выключено по умолчанию** и включается флагом `SIMULATOR_REAL_ENABLE_INJECT=1`.

---

## 1) Ключевая идея (простыми словами)

Сценарий уже описывает **сеть доверия** (кто кому разрешает быть должным и на какой лимит). Поведенческая модель отвечает на вопрос:

> «Кто и кому пытается платить, как часто и какими суммами — *и почему именно так*».

Важно: committed‑платёж не должен (и по протоколу не может) «превысить лимит доверия». Если по маршруту не хватает ёмкости — попытка корректно превращается в **rejection** (`NO_ROUTE`, `NO_CAPACITY`, `LIMIT_EXCEEDED` и т.п.).

Следовательно:
- «полноценная модель поведения» = реалистичный **спрос** (attempts) + реалистичная **структура кредита** (trustlines), а не «обход лимитов».

---

## 2) Термины и инварианты

### 2.1 TrustLine vs Payment
- TrustLine `from → to` = **creditor → debtor** (лимит риска).
- Payment обычно идёт **debtor → creditor** (через маршрутизацию).

### 2.2 Инварианты безопасности (никогда не ломаем)
1) `amount > 0`.
2) `amount` должен быть ограничен «разумным потолком» (см. 5.4).
3) Любая committed‑операция уважает лимиты и текущие долги (это обеспечивает платежный стек).
4) Planner **не должен** приводить к runaway‑нагрузке:
   - ограничение бюджета действий на тик сохраняется;
   - параллелизм ограничен `SIMULATOR_REAL_MAX_IN_FLIGHT` и пр.

### 2.3 Детерминизм (SB-NF-04)
Planner должен быть детерминированным по `(scenario, seed, tick_index)` и **prefix-stable**:
- изменение `intensity_percent` меняет только длину префикса списка действий на тик.

---

## 3) Входные данные

### 3.1 Scenario поля
Используем существующие поля schema:
- `participants[].groupId`, `participants[].behaviorProfileId`
- `behaviorProfiles[].props` и (опционально) `behaviorProfiles[].rules`
- `events[]`

Schema: `fixtures/simulator/scenario.schema.json`.

### 3.2 Runtime knobs (env)
Уже есть:
- `SIMULATOR_ACTIONS_PER_TICK_MAX`
- `SIMULATOR_CLEARING_EVERY_N_TICKS`
- guardrails real mode (`SIMULATOR_REAL_MAX_IN_FLIGHT`, `SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK`, `SIMULATOR_REAL_MAX_ERRORS_TOTAL`, …)

Добавлено:
- `SIMULATOR_REAL_AMOUNT_CAP` — общий потолок суммы в real mode (backward‑compatible default = `3.00`).

Примечание: на дату документа переменная уже поддержана кодом (см. раздел 0).

---

## 4) Выход: «действия» на тик

Real Mode оперирует внутренними действиями:
- `payment_attempt(sender, receiver, eq, amount)`
- `clearing_attempt(eq)` (уже реализовано cadence)

Поведенческая модель влияет на то, **какие** `payment_attempt` генерируются.

---

## 5) Behavioral model v1 (MVP+) — что именно реализуем

### 5.1 Модель профиля (props)
`behaviorProfiles[].props` трактуем как простую вероятностно‑статистическую модель.

Минимальный контракт `props` (опциональный; всё имеет дефолты):

```json
{
  "tx_rate": 0.25,
  "equivalent_weights": {"UAH": 0.95, "HOUR": 0.05},
  "recipient_group_weights": {"retail": 0.6, "services": 0.2, "households": 0.2},
  "amount_model": {
    "UAH": {"p50": 150, "p90": 600, "min": 20, "max": 2000}
  }
}
```

#### Типы и диапазоны (строго, для устойчивости)
- `tx_rate`: number, $0..1$.
- `equivalent_weights`: map `eq -> number`, $\ge 0$.
- `recipient_group_weights`: map `groupId -> number`, $\ge 0$.
- `amount_model`: map `eq -> model`.
  - `model.min`: number, $>0$ (опционально).
  - `model.max`: number, $>0$ (опционально).
  - `model.p50`: number, $>0$ (опционально).
  - `model.p90`: number (пока не используется в выборе суммы; зарезервирован для будущих улучшений).

Дефолты (если props отсутствуют):
- `tx_rate = 1.0` (участник «активен», но реальная активность всё равно ограничена budget)
- `equivalent_weights`: равномерно по `scenario.equivalents`
- `recipient_group_weights`: равномерно по всем группам
- `amount_model`: «малые суммы» с потолком `SIMULATOR_REAL_AMOUNT_CAP`

### 5.2 Как planner выбирает попытки платежей (как реализовано)
Планирование строится не «из senders», а из **кандидатов**, полученных из trustlines:
- из каждого trustline формируется кандидат в направлении платежа **debtor → creditor** (см. 2.1);
- затем кандидаты детерминированно перемешиваются `tick_rng`.

Далее planner идёт по индексу `i = 0..` и пытается набрать `target_actions` успешных (валидных) действий:
- для каждого `i` создаётся `action_rng = Random(action_seed(i))`;
- кандидат в позиции `i` проходит через фильтры поведения (см. 5.3);
- если прошёл — выбирается receiver (см. 5.5) и amount (см. 5.4), после чего action добавляется.

Важно для SB-NF-04:
- при меньшем `intensity_percent` planner возвращает префикс списка действий при большем `intensity_percent` (prefix-stable), потому что алгоритм идёт по одному и тому же детерминированному порядку `i` и останавливается после достижения меньшего `target_actions`.

Ограничение стабильности/производительности:
- planner делает максимум `max_iters = target_actions * 50` попыток (защита от очень малых вероятностей). Если фильтры слишком “жёсткие”, список может быть короче `target_actions`, но остаётся детерминированным.

### 5.3 Как применяются `tx_rate` и веса
В текущей реализации веса не “выбирают” эквивалент/получателя напрямую, а **смещают вероятность принятия** кандидатов и выбор receiver:
- `tx_rate` задаёт базовую активность sender.
- `equivalent_weights` влияет на вероятность принятия кандидата в конкретном `eq` через нормализацию по максимуму.
- `recipient_group_weights` влияет на выбор receiver (см. 5.5).

### 5.4 Выбор amount (реалистично, но безопасно)
Алгоритм выбора суммы:
- берём модель из `amount_model[eq]` если есть;
- иначе fallback: равномерно/лог‑равномерно на `[min_amount, cap]`.

Потолки и клампы (обязательно):
- `cap_env = SIMULATOR_REAL_AMOUNT_CAP` (default `3.00` — совместимость)
- `cap_profile = props.amount_model[eq].max` (если задан)
- `cap = min(cap_env, cap_profile)`
- сумма дополнительно квантуется до `0.01` и `> 0`.

Как реализовано:
- если `amount_model[eq]` есть, используется треугольное распределение `triangular(low=min, high=cap, mode=p50)`;
- если модели нет — fallback равномерно на `[0.10, cap]`.

Примечание: кламп по `trustline.limit` в planner полезен как «снижение шума», но это *не гарантия* проходимости (проходимость зависит от текущего used по маршруту). В v1 разрешено:
- `amount <= min(cap, p90-like)`
- и опционально `amount <= max_outgoing_limit(sender, eq)` (см. 5.6).

### 5.5 Выбор receiver (реалистично и маршрутно‑осмысленно)
Ключевое изменение относительно текущего planner: receiver не должен быть «только кредитор по одному ребру».

Алгоритм (как реализовано; детерминированный, без тяжёлой маршрутизации):
1) Строим adjacency для направления платежа (debtor → creditor) из trustlines для выбранного `eq`.
2) Для sender считаем reachable через bounded BFS:
  - `max_depth = 3`
  - `max_nodes = 200`
3) Если reachable пусто — fallback на прямых соседей sender в этом `eq`.
4) Выбор receiver:
  - сначала выбираем `target_group` по `recipient_group_weights` (roulette wheel), затем выбираем случайного reachable участника этой группы;
  - если группа недоступна — fallback на любой reachable (с попыткой отдать предпочтение “какой-то” группе, если группы вообще заданы).

Примечание: это эвристика “маршрутной осмысленности”. Полная оценка проходимости маршрута (с учётом текущих долгов) остаётся задачей PaymentEngine.

### 5.6 Prefilter по «приблизительной ёмкости» (уменьшить rejected‑шум)
Чтобы не генерировать заведомо невозможные суммы:
- для каждого sender+eq можно вычислить `max_outgoing_limit(sender, eq)` как максимум лимита по исходящим trustlines в графе платежей (т.е. где sender является debtor).
- clamp amount сверху этим значением.

v2 (опционально): предфильтр по DB‑состоянию (used/available) — требует батч‑чтения и кэша, аккуратно для SQLite.

---

## 6) Events v1 (минимальная интерпретация)

Цель events: дать «драматургию» сценария (ярмарка, кризис, выход участника, стресс‑нагрузка) без ручного скриптования транзакций.

### 6.1 Поддерживаемые типы (v1) — контракт

#### `note`
Цель: оставить диагностический маркер в stream/artifacts.

Рекомендуемая форма:
```json
{ "time": 60000, "type": "note", "description": "Market day started", "metadata": {"tag": "market_day"} }
```

Поведение (реализовано):
- при наступлении `sim_time_ms >= time` один раз пишется маркер-событие (best-effort) в `artifacts/events.ndjson`.
  - Примечание: в MVP маркеры могут быть только в artifacts (без обязательной трансляции в SSE), чтобы не ломать union типов событий в UI.

#### `stress`
Цель: “сцена” повышенной/пониженной активности.

Рекомендуемая форма (v1):
```json
{
  "time": 120000,
  "type": "stress",
  "description": "Peak hour",
  "effects": [
    {"op": "mult", "field": "tx_rate", "value": 1.8, "scope": "all"}
  ],
  "metadata": {"duration_ms": 60000}
}
```

Правила (реализовано, v1):
- stress действует на интервале `[time, time + duration_ms)`.
- `field` в v1: `tx_rate` (и опционально `intensity_budget`, если нужно), `value` — multiplier.
- `scope` в v1: `all` | `group:<groupId>` | `profile:<behaviorProfileId>`.
- итоговый `tx_rate_eff = clamp01(tx_rate * Π multipliers)`.

Текущее ограничение реализации:
- применяется только `field=tx_rate`, `op=mult`.

`inject` (warmup debts) — отдельный контракт v2 (см. 6.3).

### 6.2 Маппинг времени
MVP: интерпретируем `event.time` как:
- integer ms от старта run (`sim_time_ms`).

Строковые токены (`day_10`) — только после отдельной договорённости.

### 6.3 Warmup / Inject (v2) — контракт и guardrails

Цель: стартовать экономику “не с нуля” (есть накопленные долги), не ломая инварианты.

Рекомендуемый подход v2:
- `inject` добавляет начальные долги через контролируемую процедуру (желательно: через единый сервис/транзакцию), с жёсткой валидацией against trustlines.

Runtime gate (реализация):
- `inject` применяется только если `SIMULATOR_REAL_ENABLE_INJECT=1`.

Рекомендуемая форма:
```json
{
  "time": 0,
  "type": "inject",
  "description": "Warmup debts",
  "effects": [
    {
      "op": "inject_debt",
      "equivalent": "UAH",
      "debtor": "PID_X",
      "creditor": "PID_Y",
      "amount": "120.00"
    }
  ],
  "metadata": {"max_total_amount": "20000.00"}
}
```

Валидации (обязательные):
- `amount > 0` и `amount` квантуется до `0.01`.
- существует trustline `creditor -> debtor` по `equivalent` и `amount <= trustline.limit`.
- лимиты guardrails:
  - max injected edges за run (например 500)
  - max total injected amount per eq (например 20000.00)
  - max injected amount per edge (например 1000.00)

Наблюдаемость (обязательно):
- emit `note`/маркер в artifacts о количестве инъекций и суммарных объёмах.

---

## 7) План реализации (последовательно, чтобы «жило и не ломало»)

### Phase 0 — Документация и контракт (без кода)
- Зафиксировать contract `props` (минимум) и дефолты.
- Зафиксировать инварианты и детерминизм.

Статус: реализовано (см. раздел 0).

### Phase 1 — Amount cap + amount_model (low risk)
- Добавить env `SIMULATOR_REAL_AMOUNT_CAP`.
- `_real_pick_amount(...)` читает модель по `eq`.
- Default поведение сохраняется (cap=3).

Статус: реализовано (см. раздел 0).

### Phase 2 — Использовать behaviorProfiles (tx_rate, weights)
- Разобрать `behaviorProfiles` и привязку `participants[].behaviorProfileId`.
- В planner включить `tx_rate` как фильтр активности senders.
- Сохранить prefix‑stability.

Статус: реализовано (см. раздел 0).

### Phase 3 — Receiver selection через достижимость + recipient_group_weights
- Построить per‑eq adjacency из trustlines.
- Реализовать bounded walk/BFS и выбор receiver по групповым весам.
- Добавить мягкий fallback.

Статус: реализовано (см. раздел 0).

### Phase 4 — Events v1 (stress/note)
- Применять временный multiplier к `tx_rate` или к budget.
- Эмитить `note` в stream/artifacts.

Статус: реализовано (минимальная интерпретация; см. раздел 6).

### Phase 5 — Warmup / inject debts (v2)
- Спроектировать безопасный `inject` с валидацией инвариантов (`debt <= limit`).
- Добавить guardrails на объём инъекций.

Статус: реализовано за флагом `SIMULATOR_REAL_ENABLE_INJECT=1` (см. 6.3).

---

## 8) Проверки и тест‑план (минимум, чтобы не сломать регрессы)

### 8.1 Unit tests (Python)
- Детерминизм planner: одинаковый `(scenario, seed, tick_index)` → одинаковый список действий.
- Prefix‑stability: при `intensity=30%` список = префикс списка при `intensity=80%`.
- Amount model: суммы в диапазоне, корректное квантование.

Примечание: базовый набор unit-тестов на детерминизм/prefix-stability и amount_model обязателен до UI‑проверок.

### 8.2 Integration sanity (реальный run)
- На 2–3 мин при `intensity=50–70%`:
  - суммы должны распределяться (не только 1–3)
  - `tx.failed` не доминирует одним кодом из-за плохого выбора receiver
  - `success_rate` и `avg_route_length` выглядят «разумно»

### 8.3 Observability
- В `events.ndjson` должны быть видны причины выбора (не в каждом событии, а через `note`/debug‑маркер при включенном флаге).

---

## 9) Обновления документации (обязательно вместе с кодом)

При реализации фаз обновлять:
- `docs/ru/simulator/scenarios-and-engine.md` (раздел 2.7: что используется/не используется; ссылка на эту спеку)
- `docs/ru/simulator/backend/runner-algorithm.md` (раздел 6: ссылка на behavior model spec + дефолты)
- `docs/ru/09-decisions-and-defaults.md` (добавить дефолт `SIMULATOR_REAL_AMOUNT_CAP` и статус фич)
- при необходимости: `docs/ru/simulator/backend/acceptance-criteria.md` (добавить критерии реалистичности по суммам/миксу групп)

---

## 10) Scenario: `greenfield-village-100-realistic-v2` (обязательные требования)

Важно: часть целей realistic v2 достигается **не кодом**, а входными данными `scenario.json`. Чтобы гарантировать «выполнимость» целей (циклы для клиринга, P2P, реалистичные суммы), спецификация фиксирует требования к отдельному сценарию.

### 10.1 Принципы совместимости
- Каноничный сценарий `greenfield-village-100` не меняем.
- Вводим отдельный `scenario_id`: `greenfield-village-100-realistic-v2`.
- Реализация behavior model должна работать и на старых сценариях (пустые `props` → дефолты).

### 10.2 Эквиваленты (упрощение)
Требование v2:
- сценарий v2 использует только `UAH` как основной эквивалент;
- допускается задать `baseEquivalent="UAH"` (см. schema), а также/или `equivalents=["UAH"]`.

Цель: убрать «шум» от EUR/HOUR и сделать метрики/клиринг читаемыми.

### 10.3 Trustlines: гарантированное формирование циклов
Требование v2:
- в графе trustlines должны естественно возникать циклы типа:
  - Household → Retail → Producer → Household

Ключевое ребро для замыкания цикла:
- `Household (creditor) → Producer (debtor)` в `UAH` (это позволяет платежам идти Producer → Household).

Практическая форма требования:
- добавить набор trustlines `from=household_pid` → `to=producer_pid` с лимитами порядка `300..500 UAH` (или близко к этому), хотя бы для подмножества участников, чтобы клиринг стабильно появлялся.

### 10.4 behaviorProfiles.props: заполнить (минимум)
Требование v2:
- профили `household`, `retail`, `producer` имеют непустые `props`:
  - `tx_rate`
  - `recipient_group_weights`
  - `amount_model.UAH` (min/max/p50)

Рекомендуемые стартовые значения — как в документе `plans/simulator-realistic-scenario-v2-2026-01-30.md`.

### 10.5 Definition of Done (на уровне realistic-v2)
При `intensity=50–70%`, длительность 2–3 минуты (real mode) и `SIMULATOR_REAL_AMOUNT_CAP>=500`:
- Средний amount (UAH): целевой диапазон 100–500.
- Clearing events/min: 2–5.
- Success rate: 60–80%.
- P2P (households ↔ households): 10–20% (приближённо; допускается уточнение метрики в UI/analytics).

Артефакты проверки:
- В `events.ndjson` видны разнообразные суммы (не только 1–3).
- Регулярно встречаются `clearing.plan` и `clearing.done`.
- В `tx.updated` заметен микс групп (не только retail→anchor).
