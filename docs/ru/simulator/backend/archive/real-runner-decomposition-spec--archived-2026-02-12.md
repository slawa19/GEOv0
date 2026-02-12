# Спецификация: декомпозиция `RealRunner` (real-mode)

> **Ревизия**: 2025-02-12 (вечер) — повторное ревью после завершения декомпозиции (1c0d173).
> Предыдущая: 2025-02-12 (утро) — код-ревью реализации (b7c6219).
> Предыдущая: 2025-02-10 — обновлена по результатам код-ревью `real_runner.py` (ee355db).

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

## Статус реализации (код-ревью 2025-02-12)

## Addendum 2: аудит реализации (2026-02-12, вечер)

> Повторное ревью реализации после завершения всей декомпозиции.
> Цель: сверить заявленное в спеке с фактическим кодом, обновить LOC-таблицу, зафиксировать новые находки.

### Фактическая таблица модулей (аудит LOC)

| Файл | Спека LOC | Факт LOC | Δ | Комментарий |
|---|---|---|---|---|
| `real_runner.py` | ~32 | **32** | ✅ | — |
| `real_runner_impl.py` | ~625 | **515** | −110 | Усох после удаления _get_xxx() и дублей |
| `inject_executor.py` | 976 | **912** | −64 | Чистка после CacheInvalidator extract |
| `real_payment_planner.py` | 726 | **726** | ✅ | — |
| `real_payments_executor.py` | 595 | **584** | −11 | — |
| `real_clearing_engine.py` | 572 | **570** | −2 | — |
| `trust_drift_engine.py` | 430 | **443** | +13 | Добавлены `touched_edges_by_eq` |
| `edge_patch_builder.py` | 270 | **270** | ✅ | — |
| `models.py` | 220 | **220** | ✅ | — |
| `sse_broadcast.py` | 308 | **588** | +280 | `SseEventEmitter` (7 emit-методов) добавлен в этот же файл |
| `runtime_utils.py` | 166 | **166** | ✅ | — |
| `real_tick_persistence.py` | 145 | **214** | +69 | Расширен: artifact throttling, DB metrics |
| `real_debt_snapshot_loader.py` | 83 | **83** | ✅ | — |
| `rejection_codes.py` | 69 | **69** | ✅ | — |
| `cache_invalidator.py` | ~110 | **110** | ✅ | — |
| `real_payment_action.py` | ~12 | **12** | ✅ | — |
| `real_tick_orchestrator.py` | — | **301** | Новый | Tick lifecycle: фазы, try/except, state transitions |
| `real_tick_metrics.py` | — | **125** | Новый | ops_sec, queue_depth, bottleneck tracking |
| `real_tick_clearing_coordinator.py` | — | **154** | Новый | Clearing phase: guard + delegate RealClearingEngine |
| `real_tick_trust_drift_coordinator.py` | — | **69** | Новый | Trust drift phase orchestration |
| `real_tick_payments_coordinator.py` | — | **142** | Новый | Payment phase: plan → execute → stall guards |
| `real_scenario_seeder.py` | — | **195** | Новый | DB seeding + _load_real_participants |
| `viz_patch_helper.py` | — | **296** | Вспомогательный | Quantile refresh + node/edge viz computation |
| `runtime_impl.py` ⚠️ | **Не в спеке** | **922** | ⚠️ | Самый крупный модуль! SimulatorRuntime orchestrator уровня выше RealRunner |

**Итого simulator backend**: ~6 904 LOC (22 модуля + runtime_impl).

### model_dump аудит (P13)

| Модуль | Call sites | Контекст |
|---|---|---|
| `sse_broadcast.py` (SseEventEmitter) | 6 | tx.updated, tx.failed, clearing.plan, clearing.done, 2× topology.changed |
| `runtime_impl.py` | 2 | `run_status` event (SimulatorRunStatusEvent) |

**Итого 8** call sites (было 10+). Доменные события — централизованы в `SseEventEmitter` ✅.
Оставшиеся 2 в `runtime_impl.py` — это `run_status`, который не является доменным SSE-событием
в том же смысле (это heartbeat/state sync), поэтому отдельный emit-метод не обязателен.

### Тесты

- `pytest tests/unit` → **233 passed** (спека заявляла 232; добавлен `test_fixtures_runner_clearing_done_amount`)
- Frontend `vitest run` → **190 passed / 36 files**

### Новые проблемы, обнаруженные при аудите 2026-02-12

#### P19: Frontend `fixtures.ts` теряет `cleared_amount`/`cleared_cycles` (BUG) — **FIXED**

Парсер `validateEvents()` в `simulator-ui/v2/src/fixtures.ts` при обработке
`clearing.done` **не передавал** поля `cleared_amount` и `cleared_cycles`.
Тип `ClearingDoneEvent` эти поля определяет, но парсер их просто не извлекал из JSON.

**Результат**: в fixtures-mode сумма клиринга не отображалась как floating label
(flyout `−742.50 UAH` над top-node клирингового цикла). В real-mode (SSE) проблемы
не было — `normalizeSimulatorEvent.ts` правильно обрабатывает `cleared_amount`.

**Исправлено**: добавлены `cleared_cycles: asOptionalNumber(evt.cleared_cycles)` и
`cleared_amount: asOptionalString(evt.cleared_amount)` в парсер `clearing.done`.

#### P20: Demo fixture `demo-clearing.json` не содержит `cleared_amount` — **FIXED**

Файл `simulator-ui/v2/public/simulator-fixtures/v1/UAH/events/demo-clearing.json`
содержал `clearing.done` event без полей `cleared_amount` и `cleared_cycles`.
Даже после фикса P19 парсер возвращал `undefined` → flyout не отображался.

**Исправлено**: добавлены `"cleared_cycles": 5, "cleared_amount": "742.50"`.

#### P21: `runtime_impl.py` (922 LOC) не отражён в таблице модулей спеки — **INFO**

`runtime_impl.py` — самый крупный файл в simulator backend. Содержит `SimulatorRuntime`
(lifecycle: start/stop/pause/resume, SSE subscribe/unsubscribe, event loop scheduling,
scenario loading, snapshot generation, action handlers).

Формально он **не входит** в scope декомпозиции RealRunner (это уровень выше), но
его размер (922 LOC) и наличие 2 `model_dump` call sites заслуживают упоминания.

### Обновлённая сводка статусов

| # | Проблема | Severity | Статус |
|---|---|---|---|
| P1-CRIT | `real_runner.py` монолит | ⛔ | ✅ RESOLVED (32 LOC) |
| P1 | Дублирование edge_patch | ⚠️ | ✅ RESOLVED |
| P2 | scenario dict мутируется in-place | ⚠️ | ⏳ tech debt |
| P3 | Inconsistent topology.changed | ⚠️ | ✅ RESOLVED |
| P4 | _should_warn_this_tick | ⚠️ | ✅ RESOLVED |
| P5 | Stress vs inject | ⚠️ | ✅ RESOLVED |
| P6 | Clearing в отдельный модуль | ⚠️ | ✅ RESOLVED |
| P7 | growth → TrustDriftResult | ⚠️ | ✅ RESOLVED |
| P8 | Lazy-init | ⚠️ | ✅ RESOLVED |
| P9 | InjectExecutor hybrid callbacks | ⚠️ | ⏳ OPEN |
| P10 | _RealPaymentAction location | ⚠️ | ✅ RESOLVED |
| P11 | _apply_due_scenario_events routing | ⚠️ | ⏳ OPEN |
| P12 | Инвариант #4 устарел | ⚠️ | ✅ RESOLVED |
| P13 | model_dump централизация | ⚠️ | ✅ RESOLVED (8 sites, 6 в emitter) |
| P14 | CacheInvalidator | ⚠️ | ✅ RESOLVED |
| P15 | Тесты через facade | ⚠️ | ✅ RESOLVED |
| P16 | runner: Any | ℹ️ | ✅ RESOLVED |
| P17 | decay wrapper теряет result | ℹ️ | ✅ RESOLVED |
| P18 | _get_xxx() boilerplate | ℹ️ | ✅ RESOLVED |
| **P19** | **fixtures.ts теряет cleared_amount** | **⚠️ BUG** | **✅ FIXED** |
| **P20** | **demo-clearing.json без cleared_amount** | **ℹ️ DATA** | **✅ FIXED** |
| **P21** | **runtime_impl.py 922 LOC не в спеке** | **ℹ️** | **Задокументировано** |

---

## Addendum 1: текущий статус в этой ветке (2026-02-12)

Это короткая отметка прогресса по этой спеки (без переписывания исторического код-ревью выше).

- [x] Централизована SSE-сериализация доменных событий через `SseEventEmitter` (alias-safe `by_alias=True`).
- [x] Мигрированы emit’ы `tx.*` и `clearing.*` на `SseEventEmitter`.
- [x] Добавлен модуль [app/core/simulator/cache_invalidator.py](../../../../app/core/simulator/cache_invalidator.py) и делегация invalidation из `inject_executor`.
- [x] `flush_pending_storage` вынесен в [app/core/simulator/real_tick_persistence.py](../../../../app/core/simulator/real_tick_persistence.py).
- [x] DB seeding + загрузка participants вынесены в [app/core/simulator/real_scenario_seeder.py](../../../../app/core/simulator/real_scenario_seeder.py).
- [x] Дополнительно “утоньшён” `tick_real_mode`: вынесены фазы в отдельные helper’ы
   - [app/core/simulator/real_tick_metrics.py](../../../../app/core/simulator/real_tick_metrics.py)
   - [app/core/simulator/real_tick_clearing_coordinator.py](../../../../app/core/simulator/real_tick_clearing_coordinator.py)
   - [app/core/simulator/real_tick_trust_drift_coordinator.py](../../../../app/core/simulator/real_tick_trust_drift_coordinator.py)
   - [app/core/simulator/real_tick_payments_coordinator.py](../../../../app/core/simulator/real_tick_payments_coordinator.py)

- [x] Payments-phase orchestration вынесена в coordinator: debt snapshot → plan → execute → stall/errors guards.
- [x] Убраны lazy-getter’ы: sub-компоненты `RealRunner` инициализируются в `__init__` (P8).
- [x] Оркестрация вынесена в [app/core/simulator/real_tick_orchestrator.py](../../../../app/core/simulator/real_tick_orchestrator.py): `RealRunner.tick_real_mode`/`fail_run`/`flush_pending_storage` делегируют туда.
- [x] `real_runner.py` превращён в тонкий фасад (re-export + monkeypatch hook points); основная реализация перенесена в [app/core/simulator/real_runner_impl.py](../../../../app/core/simulator/real_runner_impl.py).

Текущее состояние: `real_runner.py` ≈ 32 LOC; `real_runner_impl.py` ≈ 625 LOC.

Проверено тестами (без правок тестов):

- `pytest tests/unit` → **232 passed**
- `pytest tests/integration` → **52 passed, 3 skipped**

### Проверка утверждения “тесты тоже тестируют дубликаты, а не вынесенный код”

На текущем состоянии кода это **в основном не так**:

- Большая часть unit/integration тестов действительно импортирует `RealRunner`, но вызывает методы, которые **делегируют** в вынесенные модули:
   - `_plan_real_payments` / `_compute_stress_multipliers` → `RealPaymentPlanner`
   - `_apply_inject_event` / `_apply_due_scenario_events` (inject ветка) → `InjectExecutor`
   - `_build_edge_patch_for_equivalent` → `EdgePatchBuilder`
   - `_invalidate_caches_after_inject` → `inject_executor.invalidate_caches_after_inject` (внутри делегация в cache_invalidator)

Частичный exception:

- [tests/unit/test_simulator_sse_trust_drift_decay_topology_patch.py](../../../../tests/unit/test_simulator_sse_trust_drift_decay_topology_patch.py) содержит standalone helper, который **явно копирует** логику `_broadcast_topology_edge_patch` (в комментарии прямо написано “Exact replica…”). Этот тест больше про контракт (no-empty payload), чем про покрытие конкретной реализации.


> Аудит проведён по коммиту b7c6219. Сверка спецификации с фактическим состоянием кода.

### Структура модулей (факт)

Таблица ниже отражает **актуальное состояние этой ветки** (2026-02-12).

| Файл | LOC | Статус | Комментарий |
|---|---|---|---|
| `real_runner.py` | ~32 | ✅ Facade | Backward-compatible фасад: re-export + monkeypatch hook points |
| `real_runner_impl.py` | ~625 | ✅ Создан | Основная реализация `RealRunner` (делегирует в вынесенные модули) |
| `inject_executor.py` | 976 | ✅ Создан | Полная реализация inject ops + cache invalidation + SSE broadcast |
| `real_payment_planner.py` | 726 | ✅ Создан | plan_payments, candidates, pick_amount, stress multipliers |
| `real_payments_executor.py` | 595 | ✅ Создан (вне спеки) | Исполнение платежей + SSE tx.updated/tx.failed |
| `real_clearing_engine.py` | 572 | ✅ Создан (вне спеки) | Clearing loop с isolated session |
| `trust_drift_engine.py` | 430 | ✅ Создан | init/growth/decay + broadcast helper |
| `edge_patch_builder.py` | 270 | ✅ Создан | DB-authoritative + VizPatchHelper-based patches |
| `models.py` | 220 | ✅ Обновлён | InjectResult, TrustDriftResult, EdgeClearingHistory, TrustDriftConfig |
| `sse_broadcast.py` | 308 | ✅ Обновлён | Transport (`SseBroadcast`) + domain emitter (`SseEventEmitter`) |
| `runtime_utils.py` | 166 | ✅ Создан | safe_int_env, safe_decimal_env, safe_optional_decimal_env + lifecycle utils |
| `real_tick_persistence.py` | 145 | ✅ Создан (вне спеки) | Метрики/bottlenecks DB writes + artifacts |
| `real_debt_snapshot_loader.py` | 83 | ✅ Создан (вне спеки) | load_debt_snapshot_by_pid |
| `rejection_codes.py` | 69 | ✅ Создан | map_rejection_code (pure function) |
| `cache_invalidator.py` | ~110 | ✅ Создан | Centralized cache invalidation (routing + viz + scenario/run mutations) |
| `real_payment_action.py` | ~12 | ✅ Создан | `_RealPaymentAction` вынесен для стабильного import/re-export |

### Прогресс по шагам миграции

| Шаг | Описание | Статус | Детали |
|---|---|---|---|
| 0 | Dataclass-результаты и rejection codes | ✅ Завершён | `InjectResult`, `TrustDriftResult` в models.py; `rejection_codes.py`; `runtime_utils.py` |
| 1 | `RealPaymentPlanner` | ✅ Завершён | `RealRunner._plan_real_payments` делегирует в `RealPaymentPlanner.plan_payments` (покрыто unit-тестами) |
| 2 | `EdgePatchBuilder` | ✅ Завершён | `_build_edge_patch_for_equivalent` делегирует в `EdgePatchBuilder` (покрыто unit/integration) |
| 3 | `InjectExecutor` | ✅ Завершён | inject ops выполняются через `InjectExecutor` (покрыто unit/integration) |
| 4 | `CacheInvalidator` | ✅ Завершён | cache invalidation централизована в `cache_invalidator.py` |
| 5 | `TrustDriftEngine` | ✅ Завершён | trust drift init/growth/decay вынесены в `TrustDriftEngine` + broadcast через emitter |
| 6 | `SseEventEmitter` | ✅ Завершён | доменные события (`tx.*`, `clearing.*`, `topology.changed`) централизованы в emitter |
| 7 | Thin facade `RealRunner` | ✅ Завершён | `real_runner.py` ≤ 200 LOC; реализация перенесена в `real_runner_impl.py` |

### Дополнительные модули (не в исходной спецификации)

Реализованы 4 модуля, не предусмотренных спецификацией:

1. **`RealPaymentsExecutor`** (`real_payments_executor.py`, 595 LOC) — исполнение `planned` payments, SSE emission `tx.updated` / `tx.failed`, edge_patch/node_patch computation через `EdgePatchBuilder.build_edge_patch_for_pairs`. Хороший extract, снижает сложность tick_real_mode.

2. **`RealClearingEngine`** (`real_clearing_engine.py`, 572 LOC) — clearing loop с isolated session, find_cycles → execute → trust_growth → patches. Принимает `apply_trust_growth` и `build_edge_patch_for_equivalent` как callbacks. Вынос оправдан (P6 в спеке говорит "остаётся в orchestrator", но фактически clearing loop достаточно автономен).

3. **`RealTickPersistence`** (`real_tick_persistence.py`, 145 LOC) — throttled DB writes для metrics/bottlenecks + artifact sync. Чистый extract, минимальные зависимости.

4. **`RealDebtSnapshotLoader`** (`real_debt_snapshot_loader.py`, 83 LOC) — `load_debt_snapshot_by_pid`. Trivial extract, полностью оправдан.

### Карта тестов → модулей (факт)

| Тестовый файл | Фактический import | Целевой модуль | Обновлён? |
|---|---|---|---|
| `test_warmup_and_capacity.py` | `real_runner.RealRunner` | `RealPaymentPlanner` | ❌ |
| `test_flow_and_periodicity.py` | `real_runner.RealRunner` | `RealPaymentPlanner` | ❌ |
| `test_simulator_real_amount_model.py` | `real_runner.RealRunner` | `RealPaymentPlanner` | ❌ |
| `test_simulator_real_planner_determinism.py` | `real_runner.RealRunner` | `RealPaymentPlanner` | ❌ |
| `test_simulator_real_events_stress.py` | `real_runner.RealRunner` | `RealPaymentPlanner` | ❌ |
| `test_trust_drift.py` | `real_runner.RealRunner` | `TrustDriftEngine` | ❌ |
| `test_scenario_inject_topology.py` | `real_runner.RealRunner` | `InjectExecutor` | ❌ |
| `test_freeze_participant_in_memory_status_overwrite.py` | `real_runner.RealRunner` | `CacheInvalidator` / `InjectExecutor` | ❌ |
| `test_simulator_real_flush_pending_storage.py` | `real_runner.RealRunner` | `RealRunner` (orchestrator) | — |
| `test_simulator_real_clearing_throttle.py` | `real_runner.RealRunner` | `RealRunner` (orchestrator) | — |
| `test_real_runner_tick_nested_partial_failures.py` | `real_runner.RealRunner`, `_RealPaymentAction` | `RealRunner` (orchestrator) | — |
| `test_simulator_clearing_no_deadlock.py` | `real_runner.RealRunner` | `RealRunner` (orchestrator) | — |
| `test_simulator_network_growth.py` | `real_runner.RealRunner` | `InjectExecutor` | ❌ |
| `test_simulator_rejection_codes.py` | `runtime._map_rejection_code` | `rejection_codes` | ✅ (через re-export в `runtime.py`) |
| `test_edge_patch_builder.py` | `edge_patch_builder.EdgePatchBuilder` | `EdgePatchBuilder` | ✅ **Новый тест** |
| `test_topology_changed_no_empty_payload.py` | `trust_drift_engine.broadcast_trust_drift_changed` | `TrustDriftEngine` | ✅ **Новый тест** |
| `test_simulator_sse_trust_drift_decay_topology_patch.py` | `trust_drift_engine.TrustDriftEngine` | `TrustDriftEngine` | ✅ **Новый тест** |

## Обнаруженные проблемы при код-ревью

### Сводка статусов (обновлено 2025-02-12 вечер, 1c0d173)

| # | Проблема | Severity | Статус |
|---|---|---|---|
| P1-CRIT | `real_runner.py` 2907 LOC, дубликаты | ⛔ Critical | ✅ **RESOLVED** — 32 LOC facade + 558 LOC impl |
| P1 | Дублирование edge_patch computation | ⚠️ | ✅ **RESOLVED** — `EdgePatchBuilder` unified |
| P2 | scenario dict мутируется in-place | ⚠️ | ⏳ Принято как tech debt (low risk) |
| P3 | Inconsistent `topology.changed` payload | ⚠️ | ✅ **RESOLVED** — `SseEventEmitter` + контрактные тесты |
| P4 | `_should_warn_this_tick` на RunRecord | ⚠️ | ✅ **RESOLVED** — передаётся как callback |
| P5 | Stress multipliers не участвуют в inject | ⚠️ | ✅ **RESOLVED** — stress в planner, inject в executor |
| P6 | `tick_real_mode_clearing` в orchestrator | ⚠️ | ✅ **RESOLVED** — `RealClearingEngine` + `RealTickClearingCoordinator` |
| P7 | `apply_trust_growth` → `int` vs `TrustDriftResult` | ⚠️ | ✅ **RESOLVED** — growth теперь возвращает `TrustDriftResult` |
| P8 | Lazy-init `getattr`/`setattr` | ⚠️ | ✅ **RESOLVED** — eager init в `__init__` |
| P9 | InjectExecutor hybrid callbacks | ⚠️ | ⏳ **OPEN** (работает, но архитектурно грязно) |
| P10 | `_RealPaymentAction` не в `models.py` | ⚠️ | ✅ **RESOLVED** — `real_payment_action.py` + re-export |
| P11 | `_apply_due_scenario_events` inline routing | ⚠️ | ⏳ **OPEN** (routing в `real_runner_impl.py`) |
| P12 | Инвариант #4 устарел | ⚠️ | ✅ **RESOLVED** — обновлён |
| P13 | `model_dump` в 10+ местах | ⚠️ | ✅ **RESOLVED** — `SseEventEmitter` (осталось 2 в `runtime_impl.py`) |
| P14 | Нет `CacheInvalidator` | ⚠️ | ✅ **RESOLVED** — `cache_invalidator.py` |
| P15 | Тесты тестируют дубликаты | ⚠️ | ✅ **RESOLVED** — тесты через facade → delegation |
| P16 | `RealTickOrchestrator` → `runner: Any` (новая) | ℹ️ | ✅ **RESOLVED** — typed `Protocol` runner port |
| P17 | `_apply_trust_decay` wrapper теряет TrustDriftResult (новая) | ℹ️ | ✅ **RESOLVED** — wrapper возвращает `TrustDriftResult` |
| P18 | `_get_xxx()` accessor boilerplate (новая) | ℹ️ | ✅ **RESOLVED** — getters удалены, прямой доступ к атрибутам |

---

### ⛔ CRITICAL — RESOLVED

### P1-CRIT: ~~real_runner.py вырос с ~2400 до 2907 LOC~~ → **RESOLVED**

> **Решено в 1c0d173.**
> - `real_runner.py` = **32 LOC** (thin facade: monkeypatch hook points + re-exports).
> - `real_runner_impl.py` = **558 LOC** (thin delegation: все методы ≤5 строк, вызывают sub-components).
> - Все дубликаты удалены. Тесты (13 файлов) проходят через facade → delegation → real module.

### ⚠️ SIGNIFICANT — RESOLVED

### P1: ~~Дублирование логики edge_patch computation~~ → **RESOLVED**

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

**Статус P6 (2025-02-12)**: Решение пересмотрено — clearing loop вынесен в
`RealClearingEngine` (572 LOC) и это оправдано: модуль достаточно автономен,
принимает callbacks для trust_growth и edge_patch, использует собственные isolated sessions.

### Новые проблемы, обнаруженные при ревью 2025-02-12

### P7: `apply_trust_growth` возвращает `int`, а не `TrustDriftResult`

Спецификация (секция 3) определяет: `apply_growth(...) → TrustDriftResult`.
Изначально: `TrustDriftEngine.apply_trust_growth()` → `int` (count updated edges),
а `apply_trust_decay()` → `TrustDriftResult`.

Это несогласованность: orchestrator (`tick_real_mode`) после growth не имеет
`touched_equivalents` / `touched_edges_by_eq` для emit edge_patch.
Сейчас growth edge_patch строится в `RealClearingEngine` через callback,
но при будущих рефакторах это может сломаться.

**Статус**: ✅ **RESOLVED** — `apply_trust_growth` возвращает `TrustDriftResult`
(включая `updated_count`, `touched_equivalents`, `touched_edges_by_eq`).

### P8: Lazy-init паттерн через `getattr`/`setattr` вместо `__init__`

Sub-компоненты (`_get_real_payments_executor`, `_get_trust_drift_engine`,
`_get_inject_executor`, `_get_real_clearing_engine`, `_get_real_tick_persistence`,
`_get_real_debt_snapshot_loader`, `_get_real_payment_planner`) используют:

```python
def _get_xxx(self) -> Xxx:
    executor = getattr(self, "_xxx", None)
    if executor is not None:
        return executor
    executor = Xxx(...)
    setattr(self, "_xxx", executor)
    return executor
```

Проблемы:
- Обход type checking (IDE не видит типы атрибутов).
- Fragile: typo в строке атрибута не поймается до runtime.
- 7 одинаковых boilerplate-методов.

**Рекомендация**: Инициализировать sub-компоненты в `__init__` (lazy init не нужен —
`RealRunner` создаётся один раз на старте runtime).

### P9: `InjectExecutor.apply_inject_event` принимает callbacks вместо DI

Спецификация говорит: InjectExecutor «не выполняет cache invalidation и SSE broadcast
напрямую». Фактически InjectExecutor:
1. **Выполняет** cache invalidation и SSE broadcast напрямую (через свои методы
   `self.invalidate_caches_after_inject()` и `self.broadcast_topology_changed()`).
2. **Дополнительно** принимает `build_edge_patch_for_equivalent` и
   `broadcast_topology_edge_patch` как callable-параметры от RealRunner.

Это создаёт гибрид: часть side-effects внутри executor, часть — через callbacks.
Контракт «InjectResult → orchestrator делает side-effects» не выполнен.

**Рекомендация**: Либо полностью вынести side-effects в orchestrator (чистый return
InjectResult), либо явно документировать, что InjectExecutor — self-contained
(сам делает invalidation + SSE).

### P10: `_RealPaymentAction` определён в `real_runner.py`, а не в `models.py`

Dataclass `_RealPaymentAction` — shared между RealRunner и тестами
(`test_real_runner_tick_nested_partial_failures.py` импортирует `_RealPaymentAction`).
По паттерну спецификации shared dataclasses должны быть в `models.py`.

Дополнительно: `RealPaymentPlanner` использует `action_factory` callback для создания
actions, что добавляет unnecessary indirection. Если `_RealPaymentAction` в `models.py`,
planner может создавать их напрямую.

### P11: `_apply_due_scenario_events` делегирует только `inject`, остальное — inline

`_apply_due_scenario_events` в `real_runner.py`:
- `type="note"` → обработка inline (artifact enqueue, mark fired) — 15 LOC
- `type="inject"` → делегация в `InjectExecutor.apply_inject_event()`
- `type="stress"` → **игнорируется** (обрабатывается отдельно в planner)
- unknown types → mark fired inline

Спецификация предполагает, что весь `_apply_due_scenario_events` уходит в
InjectExecutor. Фактически: routing остаётся в RealRunner.

**Рекомендация**: Либо перенести весь `_apply_due_scenario_events` в InjectExecutor
(переименовать в `ScenarioEventDispatcher`), либо оставить routing в orchestrator
и документировать это.

### P12: Контрактный инвариант #4 (trust drift decay) — поведение изменилось

Инвариант #4 в спецификации: «Trust drift decay пока эмитит пустой `topology.changed`
(frontend вызывает refreshSnapshot)».

**Фактически**: после фикса (коммиты с новыми тестами) trust drift decay теперь:
1. Вызывает `_build_edge_patch_for_equivalent` для каждого touched equivalent.
2. Если edge_patch не пустой — эмитит `topology.changed` с `edge_patch` + `reason="trust_drift_decay"`.
3. Если edge_patch пустой — **не эмитит ничего** (skip).

Это значительное улучшение (нет jitter на UI), но инвариант #4 в тексте устарел.

### P13: `model_dump(mode="json", by_alias=True)` — 10+ call sites без централизации

Вызовы `.model_dump(mode="json", by_alias=True)` разбросаны по модулям:

| Модуль | Event types | Кол-во call sites |
|---|---|---|
| `real_runner.py` | `topology.changed` (inject, trust_drift, edge_patch) | 3 |
| `inject_executor.py` | `topology.changed` | 1 |
| `trust_drift_engine.py` | `topology.changed` | 1 |
| `real_clearing_engine.py` | `clearing.plan`, `clearing.done` | 2 |
| `real_payments_executor.py` | `tx.updated`, `tx.failed` (×2) | 3 |

**Итого**: ~10 call sites. Если Pydantic изменит поведение `by_alias` или добавится
новый event type — риск пропуска alias-сериализации.

### P14: Отсутствие `CacheInvalidator` как отдельного модуля

Cache invalidation (`PaymentRouter._graph_cache.pop(eq)`) происходит в 3 модулях:

1. `inject_executor.py` → `invalidate_caches_after_inject()` (centralized для inject)
2. `trust_drift_engine.py` → inline `PaymentRouter._graph_cache.pop(eq)` в `apply_trust_growth` и `apply_trust_decay`
3. `real_runner.py` → дубликат `_invalidate_caches_after_inject` (МЁРТВЫЙ КОД)

Также `run._real_viz_by_eq.pop(eq)` разбросан по нескольким местам.

**Рекомендация**: после удаления дубликатов из real_runner.py, вынести cache eviction
в утилитный helper (даже без отдельного класса — достаточно функции в `runtime_utils.py`
или `inject_executor.py`).

### P15: ~~Тесты тестируют дубликаты, а не вынесенные модули~~ → **RESOLVED**

> Дубликаты удалены. 13 тестовых файлов импортируют `real_runner.RealRunner`
> (facade → `RealRunnerImpl`), все методы делегируют в вынесенные модули.
> Тесты **фактически** тестируют вынесенный код через facade.

### Новые проблемы, обнаруженные при повторном ревью 2025-02-12 (вечер)

### P16: `RealTickOrchestrator` принимает `runner: Any` (god-object pattern)

Изначально `RealTickOrchestrator.__init__(self, runner: Any)` использовал `Any`,
а внутри опирался на большой «скрытый» интерфейс runner'а.

Проблемы:
- IDE не видит типов → автокомплит и рефакторинг не работают.
- Orchestrator знает о **всех** internal methods runner'а — tight coupling.
- Если переименовать метод в `RealRunnerImpl`, orchestrator сломается только в runtime.

**Статус**: ✅ **RESOLVED** — orchestrator принимает typed runner `Protocol` (port)
и использует прямой доступ к нужным sub-components.

### P17: `_apply_trust_decay` wrapper в `real_runner_impl.py` теряет `TrustDriftResult`

```python
async def _apply_trust_decay(self, run, session, tick_index, debt_snapshot, scenario) -> TrustDriftResult:
   return await self._trust_drift_engine.apply_trust_decay(...)
```

`TrustDriftEngine.apply_trust_decay()` возвращает `TrustDriftResult` с полями
`updated_count`, `touched_equivalents`, `touched_edges_by_eq`.
Ранее wrapper в `real_runner_impl.py` **отбрасывал** `touched_*` и возвращал только `int`.

Это ОК для текущего кода (orchestrator в `real_tick_trust_drift_coordinator.py`
вызывает `trust_drift_engine.apply_trust_decay()` напрямую, не через wrapper).
Но wrapper создаёт ложное ожидание backward-compatibility для тестов, которые
вызывают `RealRunner._apply_trust_decay()` → получают `int` вместо `TrustDriftResult`.

**Статус**: ✅ **RESOLVED** — wrapper возвращает `TrustDriftResult`.

### P18: `_get_xxx()` accessor methods — unnecessary boilerplate

После перехода на eager init в `__init__` (P8 resolved), 15 методов вида:

```python
def _get_real_payments_executor(self) -> RealPaymentsExecutor:
    return self._real_payments_executor
```

Это pure passthrough без логики.

**Статус**: ✅ **RESOLVED** — `_get_xxx()` методы удалены; используется прямой доступ
к атрибутам (`self._xxx` / `rr._xxx`).

## Обновлённый контрактный инвариант #4

> Обновлено 2025-02-12 по результатам ревью:

4. **UI** допускает инкрементальные патчи (`edge_patch`/`node_patch`) и fallback на full snapshot `refreshSnapshot()`.
   - **Trust drift decay**: эмитит `topology.changed` с `edge_patch` + `reason="trust_drift_decay"`
     для каждого touched equivalent. Если edge_patch пустой — **не эмитит ничего** (skip).
     Покрыто контрактными тестами: `test_topology_changed_no_empty_payload.py`,
     `test_simulator_sse_trust_drift_decay_topology_patch.py`.
   - **Trust drift growth**: эмитит `topology.changed` с `edge_patch` + `reason="trust_drift_growth"`.
   - **Inject debt**: эмитит `topology.changed` с `edge_patch` + `reason="inject_debt"`.
   - **Inject topology**: эмитит `topology.changed` с `added_nodes`/`added_edges`/`frozen_nodes`/`frozen_edges`.
   - **Пустой payload**: ЗАПРЕЩЁН. Frontend вызывает `refreshSnapshot()` при пустом payload,
     что вызывает visible jitter. Backend **обязан** пропускать emit, если payload пуст.

## Рекомендуемый следующий шаг (action plan, обновлён 2025-02-12 вечер)

> P1-CRIT и P8 **уже решены**. P7/P16/P17/P18 также закрыты; ниже — оставшиеся приоритеты.

**Приоритет 4 (long-term) — Scenario mutations (P2)**

`ScenarioState` wrapper для immutable scenario с tracked mutations.

**Приоритет 5 (optional) — InjectExecutor pure return (P9) + event routing (P11)**

Рефакторить InjectExecutor → pure `InjectResult` return.
Перенести `_apply_due_scenario_events` routing в `ScenarioEventDispatcher`.

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
