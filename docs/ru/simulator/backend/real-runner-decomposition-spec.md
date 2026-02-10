# Спецификация: декомпозиция `RealRunner` (real-mode)

## Контекст и цель

`RealRunner` в `app/core/simulator/real_runner.py` стал монолитом: в одном файле и классе смешаны
- тиковый цикл (оркестрация фаз),
- исполнение inject-событий (изменения topology и долгов),
- trust drift (growth/decay лимитов),
- clearing/planner (выбор и исполнение операций),
- расчёт/эмиссия UI-патчей (viz/patch helper),
- SSE-broadcast и форматирование событий,
- накопление метрик/артефактов,
- инвалидация кэшей (routing graph, viz helper, edges_by_equivalent).

Цель: разнести обязанности на модули без изменения внешнего поведения (API/события/семантика),
с понятными границами и тестируемыми контрактами.

Нефункциональные требования:
- поведение/контракты SSE не меняются, кроме явно согласованных улучшений;
- изменения детерминированы; не ухудшать стабильность и согласованность;
- минимальные диффы на шаг, лёгкий rollback.

## Текущие контрактные инварианты (must keep)

- Directionality: `from → to` = creditor → debtor (risk limit), не наоборот.
- Pydantic aliases: при сериализации моделей с `Field(alias=...)` использовать `model_dump(mode="json", by_alias=True)`.
- `PaymentRouter._graph_cache` должен инвалидироваться при изменении лимитов (trust drift) и долгов (inject_debt).
- UI допускает инкрементальные патчи (edge_patch/node_patch) и fallback на full snapshot refresh.

## Предлагаемая декомпозиция

### 1) `RealTickOrchestrator`
Ответственность:
- основной `tick_real_mode`: порядок фаз, таймауты, контуры try/except, метрики `ops_sec`, queue_depth, state transitions.

Входы/выходы:
- принимает `run_id`, получает `run` и `scenario`, вызывает под-компоненты;
- возвращаемых значений нет; побочные эффекты: DB, SSE, in-memory caches.

### 2) `InjectExecutor`
Ответственность:
- `_apply_due_scenario_events` и конкретные ops (`inject_debt`, `add_participant`, `create_trustline`, `freeze_participant`);
- накопление `affected_equivalents`, patch-edges для inject_debt;
- единая точка cache invalidation + topology broadcast.

Явные API:
- `apply_due_events(session, run, scenario, run_id, event_index, now_ms) -> InjectResult`
- `InjectResult` содержит:
  - `affected_equivalents: set[str]`
  - `new_participants/trustlines` (для topology.changed)
  - `frozen_nodes/edges`
  - `edge_patches_by_eq` (для debt/limit изменений)

### 3) `TrustDriftEngine`
Ответственность:
- `_init_trust_drift`, `_apply_trust_growth`, `_apply_trust_decay`.

Явные API:
- `apply_decay(session, run, scenario, debt_snapshot, tick_index) -> TrustDriftResult`
- `apply_growth(clearing_session, run, eq_code, touched_edges, tick_index, cleared_amounts) -> TrustDriftResult`

`TrustDriftResult`:
- `touched_equivalents: set[str]`
- `edge_patch_by_eq` (полный edge_patch per equivalent при изменении лимитов)

### 4) `SseEventEmitter`
Ответственность:
- сериализация и broadcast событий: `tx.updated`, `tx.failed`, `clearing.plan`, `clearing.done`, `topology.changed`, `run_status`;
- общий helper для event_id/ts;
- строгая политика alias serialization.

### 5) `CacheInvalidator`
Ответственность:
- инвалидация `PaymentRouter._graph_cache`, `run._real_viz_by_eq`, `run._edges_by_equivalent`;
- узкая (targeted) инвалидация по equivalents.

### 6) `EdgePatchBuilder` (backend-authoritative)
Ответственность:
- генерация `edge_patch`/`node_patch` на основании DB (used/available/trust_limit + viz keys);
- два режима:
  - `only_edges` (для inject_debt: ограниченный patch без перерасчёта ширин)
  - `full_equivalent` (для trust drift: пересчёт width quantiles).

## Порядок миграции (итеративно)

Шаг 0 — подготовка:
- добавить тонкие `dataclass`-результаты (`InjectResult`, `TrustDriftResult`), не меняя поведения.

Шаг 1 — вынести `EdgePatchBuilder`:
- перенести текущую DB-логику edge_patch в отдельный модуль `app/core/simulator/edge_patch_builder.py`.
- `RealRunner` вызывает builder.

Шаг 2 — вынести `InjectExecutor`:
- перенести inject ops + пост-коммитные действия.
- сохранить существующие unit-тесты inject (дополнить при необходимости).

Шаг 3 — вынести `TrustDriftEngine`:
- перенести growth/decay, сохранив существующие точки вызова.

Шаг 4 — вынести `SseEventEmitter`:
- централизовать `.model_dump(..., by_alias=True)`.

Шаг 5 — уплотнение `RealRunner`:
- `RealRunner` остаётся фасадом и держателем shared state/lock, но не содержит доменную логику.

## Acceptance Criteria

- Все текущие unit/integration тесты проходят.
- Inject/trust drift не требуют full snapshot refresh для обновления `trust_limit/used/available/viz_*` (там, где есть edge_patch).
- `freeze_participant` не инвалидирует «все equivalents» без необходимости.
- Поведение SSE событий не ломает backward compatibility: старый UI может игнорировать новые поля.

## Риски и как их снизить

- Изменение сериализации alias (`from_` → `from`) может затронуть клиентов.
  - Митигация: централизовать emitter, покрыть контрактными тестами SSE.
- Edge width quantiles зависят от распределения лимитов.
  - Митигация: для trust drift использовать full-equivalent patch.

