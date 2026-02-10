# Спецификация: декомпозиция `RealRunner` (real-mode)

> **Ревизия**: 2025-02-10 — обновлена по результатам код-ревью `real_runner.py` (ee355db).

## Контекст и цель

`RealRunner` в `app/core/simulator/real_runner.py` стал монолитом (~2 400 строк):
в одном файле и классе смешаны **9 зон ответственности**:

| # | Зона ответственности | LOC (≈) | Ключевые методы |
|---|---|---|---|
| 1 | Тиковый цикл (оркестрация фаз) | ~350 | `tick_real_mode`, `tick_real_mode_clearing` |
| 2 | Исполнение inject-событий (topology mutations) | ~400 | `_apply_due_scenario_events`, `_apply_inject_event`, `op_inject_debt`, `op_add_participant`, `op_create_trustline`, `op_freeze_participant` |
| 3 | Trust drift (growth/decay лимитов) | ~250 | `_init_trust_drift`, `_apply_trust_growth`, `_apply_trust_decay` |
| 4 | Payment planner (планирование платежей) | ~400 | `_plan_real_payments`, `_real_candidates_from_scenario`, `_real_pick_amount`, `_compute_stress_multipliers`, `_choose_receiver`, `_reachable_nodes` |
| 5 | Clearing orchestration | ~200 | `tick_real_mode_clearing` (clearing loop, ClearingService calls, patch computation) |
| 6 | SSE broadcast и сериализация | ~100 | `_broadcast_topology_changed`, `_broadcast_trust_drift_changed`, `_broadcast_topology_edge_patch` |
| 7 | Edge/node patch computation | ~200 | `_build_edge_patch_for_equivalent`, inline VizPatchHelper calls в tick/clearing |
| 8 | Cache invalidation | ~80 | `_invalidate_caches_after_inject` |
| 9 | Метрики, артефакты, DB seeding | ~200 | `flush_pending_storage`, `_seed_scenario_into_db`, `_load_real_participants`, `_load_debt_snapshot_by_pid` |

Вспомогательные функции уровня модуля:
- `_safe_decimal_env`, `_safe_optional_decimal_env` — парсинг env → Decimal
- `map_rejection_code` — маппинг ошибок платежей → стабильные коды для UI/analytics

Цель: разнести обязанности на модули без изменения внешнего поведения (API/события/семантика),
с понятными границами и тестируемыми контрактами.

Нефункциональные требования:
- поведение/контракты SSE не меняются, кроме явно согласованных улучшений;
- изменения детерминированы; не ухудшать стабильность и согласованность;
- минимальные диффы на шаг, лёгкий rollback;
- существующие тесты (15+ файлов) продолжают проходить без правок.

## Текущие контрактные инварианты (must keep)

1. **Directionality**: `from → to` = creditor → debtor (risk limit / TrustLine), не наоборот.
   Платежи идут в обратном направлении: debtor → creditor.
   Кандидаты в `_real_candidates_from_scenario` уже инвертированы (sender=to, receiver=from).

2. **Pydantic aliases**: `from_` → `from` при сериализации. Используется:
   - `SimulatorTxUpdatedEvent(from_=...).model_dump(mode="json", by_alias=True)`
   - `SimulatorTxFailedEvent(from_=...).model_dump(mode="json", by_alias=True)`
   - `SimulatorClearingPlanEvent`, `SimulatorClearingDoneEvent`, `SimulatorTopologyChangedEvent` — все с `by_alias=True`.

3. **`PaymentRouter._graph_cache`** должен инвалидироваться при:
   - trust drift (growth → per-equivalent, decay → per-touched-eq);
   - inject_debt (→ per affected equivalent);
   - freeze_participant (→ per incident equivalents);
   - inject create_trustline / add_participant (→ per affected equivalent).

4. **UI** допускает инкрементальные патчи (`edge_patch`/`node_patch`) и fallback на full snapshot `refreshSnapshot()`.
   Trust drift decay пока эмитит пустой `topology.changed` (frontend вызывает refreshSnapshot).
   Trust drift growth и inject_debt эмитят edge_patch с DB-авторитативными данными.

5. **Детерминизм планирования**: `_plan_real_payments` для данного `(seed, tick_index, scenario)` — детерминистичен.
   Это critical для invariant SB-NF-04. Ломать нельзя.

6. **Session model**: tick_real_mode использует single AsyncSession + `begin_nested()` savepoints.
   Clearing использует изолированную сессию (отдельный `AsyncSessionLocal()`).

7. **Warm-up, flow/periodicity, capacity-aware amounts** (Phase 1.1–1.4, Phase 4.1–4.3) —
   встроены в `_plan_real_payments` и не должны быть нарушены декомпозицией.

8. **RunRecord shared state**: `run._real_viz_by_eq`, `run._edges_by_equivalent`,
   `run._real_participants`, `run._trust_drift_config`, `run._edge_clearing_history` —
   мутируются in-place под `self._lock`. Новые модули должны принимать `run` + `lock` явно.

## Аудит текущих зависимостей и shared state

### Mutable shared state через RunRecord

```
run._real_participants          ← seed, inject add_participant
run._real_equivalents           ← seed
run._real_viz_by_eq             ← tick (VizPatchHelper cache), inject (evict), clearing (create/refresh)
run._edges_by_equivalent        ← lifecycle (init), inject (add/freeze), clearing (не мутирует)
run._real_fired_scenario_event_indexes ← inject (mark fired)
run._edge_clearing_history      ← trust drift init/growth
run._trust_drift_config         ← trust drift init
run._real_total_debt_by_eq      ← tick (throttled refresh)
run._real_last_tick_storage_payload  ← tick (cache for flush)
run._real_clearing_task         ← tick (clearing guard)
run._real_consec_tick_failures  ← tick error handling
run._real_consec_all_rejected_ticks ← tick stall detection
```

### External mutable state

```
PaymentRouter._graph_cache      ← класс-уровень dict, evict по eq_code
scenario dict (in-memory)       ← мутируется inject/trust drift (trustlines[].limit, participants[].status)
```

## Предлагаемая декомпозиция (обновлённая)

### 1) `RealTickOrchestrator` → `app/core/simulator/real_tick_orchestrator.py`

Ответственность:
- основной `tick_real_mode`: порядок фаз, таймауты, контуры try/except, метрики `ops_sec`, queue_depth, state transitions;
- DB seeding (`_seed_scenario_into_db`, `_load_real_participants`);
- финальный commit, artifacts throttling, metrics/bottlenecks DB writes;
- `fail_run`, `flush_pending_storage`;
- `tick_real_mode_clearing` (clearing orchestration loop).

Принимает sub-компоненты через DI (inject executor, trust drift engine, planner, etc.).

Явный контракт:
- Принимает `run_id`, получает `run` и `scenario`, вызывает подкомпоненты.
- Возвращаемых значений нет; побочные эффекты: DB, SSE, in-memory caches.

### 2) `InjectExecutor` → `app/core/simulator/inject_executor.py`

Ответственность:
- `_apply_due_scenario_events` и конкретные ops:
  - `inject_debt` — создание/обновление Debt rows;
  - `add_participant` — создание Participant + initial trustlines;
  - `create_trustline` — создание TrustLine;
  - `freeze_participant` — suspend participant + freeze TLs.
- Возврат `InjectResult` (structured dataclass, не dict);
- **Не выполняет** cache invalidation и SSE broadcast напрямую.

Явные API:
```python
@dataclass
class InjectResult:
    affected_equivalents: set[str]
    new_participants: list[tuple[uuid.UUID, str]]  # (id, pid)
    new_participants_scenario: list[dict[str, Any]]
    new_trustlines_scenario: list[dict[str, Any]]
    frozen_participant_pids: list[str]
    frozen_edges: list[dict[str, str]]  # {from_pid, to_pid, equivalent_code}
    inject_debt_equivalents: set[str]
    inject_debt_edges_by_eq: dict[str, set[tuple[str, str]]]
    applied: int
    skipped: int
    total_applied: Decimal
```

```python
async def apply_due_events(
    session, *, run_id, run, scenario, now_ms
) -> None  # fires per-event, marks indexes
```

```python
async def apply_inject_event(
    session, *, run_id, run, scenario, event_index, event_time_ms, event, pid_to_participant_id
) -> InjectResult
```

Ключевое: `note` и `stress` events также обрабатываются здесь (stress уже сейчас обрабатывается вне inject, но `note` — да).

### 3) `TrustDriftEngine` → `app/core/simulator/trust_drift_engine.py`

Ответственность:
- `init_trust_drift(run, scenario)` — инициализация config + edge history;
- `apply_growth(clearing_session, run, eq_code, touched_edges, tick_index, cleared_amounts)` — DB limit update + commit;
- `apply_decay(session, run, tick_index, debt_snapshot, scenario)` — DB limit update (без commit, caller коммитит).

Явные API:
```python
@dataclass
class TrustDriftResult:
    updated_count: int
    touched_equivalents: set[str]
```

```python
def init(run: RunRecord, scenario: dict) -> None
async def apply_growth(...) -> TrustDriftResult
async def apply_decay(...) -> TrustDriftResult
```

Инвариант: growth вызывает `PaymentRouter._graph_cache.pop(eq)` и `session.commit()` внутри.
Decay **не** коммитит — это делает orchestrator.

### 4) `RealPaymentPlanner` → `app/core/simulator/real_payment_planner.py`

**Новый модуль**, не упомянутый в исходной спецификации.

Ответственность:
- `_plan_real_payments` (детерминистичный planner);
- `_real_candidates_from_scenario`;
- `_real_pick_amount` (amount model: triangular / lognormal);
- `_compute_stress_multipliers`;
- Вложенные helpers: `_choose_receiver`, `_reachable_nodes`, `_pick_group`.

Это ~400 LOC чистой логики **без DB-обращений**, полностью unit-testable.
Уже покрыт тестами: `test_warmup_and_capacity.py`, `test_flow_and_periodicity.py`,
`test_simulator_real_amount_model.py`, `test_simulator_real_planner_determinism.py`,
`test_simulator_real_events_stress.py`.

Явный контракт:
```python
def plan_payments(
    run: RunRecord,
    scenario: dict,
    *,
    debt_snapshot: dict | None,
    actions_per_tick_max: int,
    amount_cap: Decimal | None,
) -> list[RealPaymentAction]
```

### 5) `SseEventEmitter` → расширение `app/core/simulator/sse_broadcast.py`

Ответственность:
- Высокоуровневые broadcast-методы: `emit_topology_changed`, `emit_trust_drift_changed`,
  `emit_topology_edge_patch`, `emit_tx_updated`, `emit_tx_failed`, `emit_clearing_plan`,
  `emit_clearing_done`;
- Строгая политика alias serialization (единая точка `.model_dump(mode="json", by_alias=True)`);
- Общий helper для `event_id` + `ts` + artifact enqueue.

**Важно**: сейчас `SseBroadcast` — транспортный уровень (queue + replay).
Предлагается добавить `SseEventEmitter` как отдельный класс, который использует
`SseBroadcast.broadcast()` внутри, но инкапсулирует доменные event-типы.

Это устраняет дублирование `model_dump(mode="json", by_alias=True)` в 8+ местах
и снижает риск ошибок alias-сериализации.

### 6) `CacheInvalidator` → `app/core/simulator/cache_invalidator.py`

Ответственность:
- `invalidate_after_inject(run, scenario, inject_result)` — текущая `_invalidate_caches_after_inject`;
- `invalidate_routing_cache(equivalents)` — `PaymentRouter._graph_cache.pop(eq)`;
- `invalidate_viz_cache(run, equivalents)` — `run._real_viz_by_eq.pop(eq)`.

Мотивация: cache invalidation разбросан по 5 местам (inject, trust drift growth,
trust drift decay, clearing implicit, tick commit). Централизация снижает риск
пропущенной инвалидации.

### 7) `EdgePatchBuilder` → `app/core/simulator/edge_patch_builder.py`

Ответственность:
- `build_edge_patch_for_equivalent(session, run, eq_code, only_edges, include_width_keys)` —
  текущий `_build_edge_patch_for_equivalent`;
- Вынос inline edge_patch computation из `tick_real_mode` (payment committed path)
  и `tick_real_mode_clearing` (clearing done path);
- Два режима:
  - `only_edges` (для inject_debt: ограниченный patch без перерасчёта ширин);
  - `full_equivalent` (для trust drift growth: пересчёт width quantiles по всем TL).

**Обнаружено при ревью**: inline edge_patch в `tick_real_mode` (строки ~1050–1200)
дублирует логику `_build_edge_patch_for_equivalent` но через `VizPatchHelper.edge_viz()`.
Оба пути должны быть унифицированы в одном builder.

### 8) `RejectionCodeMapper` → `app/core/simulator/rejection_codes.py`

Вынос `map_rejection_code()` (самодостаточная pure function, ~50 LOC) в отдельный
модуль. Уже unit-testable, но сейчас живёт в real_runner.py рядом с 2400 строками.

## Обнаруженные проблемы при код-ревью

### P1: Дублирование логики edge_patch computation

- `_build_edge_patch_for_equivalent` — standalone метод, читает TL + Debt из DB,
  использует `viz_rules.link_alpha_key` / `link_width_key`.
- Inline в `tick_real_mode` — читает TL + Debt из DB, использует `VizPatchHelper.edge_viz()`.
- Inline в `tick_real_mode_clearing` — аналогично через `VizPatchHelper.edge_viz()`.

Результат: 3 пути для edge_patch с разной логикой quantile computation.
`_build_edge_patch_for_equivalent` использует `viz_rules.quantile` (пересчитывает quantiles
из всех TL), а VizPatchHelper кэширует quantiles с `refresh_every_ticks`.

**Рекомендация**: unified builder (EdgePatchBuilder) с передачей `VizPatchHelper` instance.

### P2: scenario dict мутируется in-place

Trust drift growth и decay мутируют `scenario["trustlines"][i]["limit"]`.
Inject мутирует `scenario["participants"]`, `scenario["trustlines"]`.
Freeze мутирует `scenario["participants"][i]["status"]`, `scenario["trustlines"][i]["status"]`.

Это работает благодаря deep-copy при создании run (`RunRecord._scenario_raw`),
но делает debugging и rollback сложнее. Рассмотреть `ScenarioState` wrapper
(long-term, не блокирует миграцию).

### P3: Inconsistent handling of `topology.changed` payload

- Inject: эмитит `topology.changed` с `added_nodes`, `added_edges`, `frozen_nodes`, `frozen_edges`.
- Trust drift decay: эмитит **пустой** `topology.changed` (frontend делает full refresh).
- Trust drift growth: эмитит `topology.changed` с `edge_patch` через `_broadcast_topology_edge_patch`.

Фронтенд обрабатывает все три варианта, но контракт не формализован.
**Рекомендация**: при выносе в SseEventEmitter документировать три сценария
и добавить контрактные тесты.

### P4: `_should_warn_this_tick` state на RunRecord

Throttle-логика (`_real_warned_tick`, `_real_warned_keys`) — утилитарная,
не связана с бизнес-логикой. При выносе модулей передавать как callback
`should_warn(key: str) -> bool`.

### P5: Stress multipliers не участвуют в inject

`_compute_stress_multipliers` обрабатывает `type="stress"` events,
а `_apply_due_scenario_events` обрабатывает `type="inject"` и `type="note"`.
Это два разных flow для событий одного timeline. При выносе InjectExecutor
учитывать, что stress events остаются в planner (не в inject executor).

### P6: `tick_real_mode_clearing` — часть orchestrator, не отдельный модуль

В отличие от inject/trust-drift, clearing orchestration (find_cycles, execute, patches,
trust growth) тесно связана с tick lifecycle (time budget, clearing task guard,
isolated session). Не стоит выносить в отдельный модуль; остаётся в orchestrator.

## Карта тестов → модулей

| Тестовый файл | Текущий import | Целевой модуль |
|---|---|---|
| `test_warmup_and_capacity.py` | `RealRunner._plan_real_payments` | `RealPaymentPlanner` |
| `test_flow_and_periodicity.py` | `RealRunner._plan_real_payments` | `RealPaymentPlanner` |
| `test_simulator_real_amount_model.py` | `RealRunner._real_pick_amount` | `RealPaymentPlanner` |
| `test_simulator_real_planner_determinism.py` | `RealRunner._plan_real_payments` | `RealPaymentPlanner` |
| `test_simulator_real_events_stress.py` | `RealRunner._compute_stress_multipliers` | `RealPaymentPlanner` |
| `test_trust_drift.py` | `RealRunner._init_trust_drift`, `_apply_trust_growth`, `_apply_trust_decay` | `TrustDriftEngine` |
| `test_scenario_inject_topology.py` | `RealRunner._apply_due_scenario_events` | `InjectExecutor` |
| `test_simulator_network_growth.py` | `RealRunner._apply_due_scenario_events` | `InjectExecutor` |
| `test_freeze_participant_in_memory_status_overwrite.py` | `RealRunner._invalidate_caches_after_inject` | `CacheInvalidator` |
| `test_simulator_real_flush_pending_storage.py` | `RealRunner.flush_pending_storage` | `RealTickOrchestrator` |
| `test_simulator_real_clearing_throttle.py` | `RealRunner.tick_real_mode_clearing` | `RealTickOrchestrator` |
| `test_real_runner_tick_nested_partial_failures.py` | `RealRunner.tick_real_mode` | `RealTickOrchestrator` |
| `test_clearing_plan_edges_extraction.py` | standalone logic | `RealTickOrchestrator` |
| `test_simulator_clearing_no_deadlock.py` | `RealRunner` full | `RealTickOrchestrator` |

## Порядок миграции (итеративно, обновлённый)

### Шаг 0 — Подготовка: dataclass-результаты и rejection codes
- Добавить `InjectResult`, `TrustDriftResult` в `models.py`.
- Вынести `map_rejection_code` → `rejection_codes.py`.
- Вынести `_safe_decimal_env`, `_safe_optional_decimal_env` → `runtime_utils.py`.
- **Не** менять поведение. `RealRunner` импортирует из новых мест.
- Тесты: все проходят без изменений.

### Шаг 1 — Вынести `RealPaymentPlanner`
- Перенести `_plan_real_payments`, `_real_candidates_from_scenario`, `_real_pick_amount`,
  `_compute_stress_multipliers` и вложенные helpers.
- `RealRunner._plan_real_payments` → `self._planner.plan_payments(...)`.
- Обновить 5 тестовых файлов (import path).
- Этот шаг — самый безопасный: planner не имеет DB/SSE побочных эффектов.

### Шаг 2 — Вынести `EdgePatchBuilder`
- Перенести `_build_edge_patch_for_equivalent` в `edge_patch_builder.py`.
- Унифицировать inline edge_patch из tick и clearing через builder.
- `RealRunner` вызывает `builder.build_edge_patch(...)`.

### Шаг 3 — Вынести `InjectExecutor`
- Перенести inject ops + `_apply_due_scenario_events`.
- Inject возвращает `InjectResult`, orchestrator делает cache invalidation и SSE.
- Обновить 2 тестовых файла.

### Шаг 4 — Вынести `CacheInvalidator`
- Перенести `_invalidate_caches_after_inject` и точки cache eviction.
- Обновить 1 тестовый файл.

### Шаг 5 — Вынести `TrustDriftEngine`
- Перенести init/growth/decay.
- Обновить 1 тестовый файл.

### Шаг 6 — Вынести `SseEventEmitter`
- Создать доменный emitter поверх `SseBroadcast.broadcast()`.
- Централизовать `.model_dump(mode="json", by_alias=True)` (8+ call sites).
- Контрактные тесты для SSE event shapes.

### Шаг 7 — Уплотнение `RealRunner` → `RealTickOrchestrator`
- `RealRunner` остаётся thin facade для backward compat (`runtime_impl.py` imports).
- `RealTickOrchestrator` содержит `tick_real_mode`, `tick_real_mode_clearing`,
  `fail_run`, `flush_pending_storage`, DB seeding.
- Или: переименовать `RealRunner` в `RealTickOrchestrator`, обновить imports.

## Acceptance Criteria

1. Все текущие unit/integration тесты проходят (15+ файлов).
2. Inject/trust drift не требуют full snapshot refresh для `trust_limit/used/available/viz_*`
   (там, где есть edge_patch).
3. `freeze_participant` не инвалидирует «все equivalents» без необходимости
   (только incident equivalents из scenario trustlines).
4. Поведение SSE событий не ломает backward compatibility: старый UI может игнорировать новые поля.
5. Детерминизм планирования: `_plan_real_payments` с тем же seed даёт тот же результат.
6. `by_alias=True` — единая точка в `SseEventEmitter`.

## Метрики успеха (definition of done per step)

| Шаг | Критерий | Как проверить |
|---|---|---|
| 0 | Все тесты зелёные, diff ≤ 100 LOC | `pytest`, git diff |
| 1 | Planner тесты (5 файлов) проходят с новым import path | `pytest tests/unit/test_warmup*` etc. |
| 2 | edge_patch в tx.updated и clearing.done содержат одинаковые ключи | SSE contract test |
| 3 | inject integration tests проходят | `pytest tests/integration/test_simulator_network_growth.py` |
| 4 | Freeze invalidates only incident eqs | `test_freeze_participant_in_memory_status_overwrite` |
| 5 | Trust drift tests проходят с новым import | `pytest tests/unit/test_trust_drift.py` |
| 6 | Все SSE events проходят через emitter, no direct `model_dump` | grep audit |
| 7 | `real_runner.py` ≤ 200 LOC (facade) | `wc -l` |

## Риски и митигации

| Риск | Вероятность | Митигация |
|---|---|---|
| Изменение сериализации alias (`from_` → `from`) может затронуть клиентов | Средняя | Централизовать emitter (шаг 6), покрыть контрактными SSE тестами |
| Edge width quantiles зависят от распределения лимитов | Низкая | Для trust drift использовать full-equivalent patch через EdgePatchBuilder |
| Planner determinism broken by refactor | Высокая при неаккуратности | `test_simulator_real_planner_determinism.py` — запускать на каждом шаге |
| Shared mutable state race conditions | Средняя | Все мутации RunRecord under `self._lock`, передавать lock в подмодули |
| Clearing session isolation нарушена | Высокая | Clearing ВСЕГДА использует `AsyncSessionLocal()`, не parent session |
| Import path changes break monkeypatch in tests | Высокая | Backward-compat re-export в `real_runner.py`, update monkeypatch targets |
| Deep-copy scenario mutation conflicts | Низкая | Документировать; long-term: ScenarioState wrapper |

## Приложение: полная карта публичных методов RealRunner

```
# Lifecycle
tick_real_mode(run_id)                     → orchestrator
tick_real_mode_clearing(session, run_id, run, equivalents) → orchestrator
fail_run(run_id, code, message)            → orchestrator
flush_pending_storage(run_id)              → orchestrator

# Inject
_apply_due_scenario_events(session, ...)   → inject executor
_apply_inject_event(session, ...)          → inject executor
_invalidate_caches_after_inject(...)       → cache invalidator
_broadcast_topology_changed(...)           → sse emitter

# Trust drift
_init_trust_drift(run, scenario)           → trust drift engine
_apply_trust_growth(run, session, ...)     → trust drift engine
_apply_trust_decay(run, session, ...)      → trust drift engine
_broadcast_trust_drift_changed(...)        → sse emitter

# Planning
_plan_real_payments(run, scenario, ...)    → payment planner
_real_candidates_from_scenario(scenario)   → payment planner
_real_pick_amount(rng, limit, ...)         → payment planner
_compute_stress_multipliers(events, ...)   → payment planner

# Patches
_build_edge_patch_for_equivalent(...)      → edge patch builder
_broadcast_topology_edge_patch(...)        → sse emitter

# DB helpers
_seed_scenario_into_db(session, scenario)  → orchestrator
_load_real_participants(session, scenario)  → orchestrator
_load_debt_snapshot_by_pid(session, ...)   → orchestrator

# Utils
_should_warn_this_tick(run, key)           → orchestrator (callback)
_sim_idempotency_key(...)                  → orchestrator
_parse_event_time_ms(evt)                  → inject executor / shared util
```
