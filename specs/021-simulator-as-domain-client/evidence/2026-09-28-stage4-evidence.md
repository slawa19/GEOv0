# 021, стадия 4 (`T2105`) — evidence, 2026-09-28

Ветка `claude/021-s4` от `2df5703` (main). Стадии 1–3 слиты (PR #68, #70, #72), 023(d) слит (PR #74). Этот файл — ведомость
удаления (§14) и сырые результаты; сводка — Changelog спеки, запись 2026-09-28 «`T2105`, стадия 4».

## 1. Инвентаризация (§14): символ → судьба → проверка

Селектор инвентаря: `git grep -n -E 'real_tick_|RealTick|_RealRunnerPort' -- app tests scripts docs` на `2df5703`.
`write_real_tick_artifact` (метод `ArtifactsManager`) и имена тестов `*_real_tick_*` совпадают с селектором по тексту, к
модулям отношения не имеют — **keep** без изменений (12 тестовых модулей с дублёром артефактов).

### Код приложения

| Символ (на `2df5703`) | Судьба | Проверка |
|---|---|---|
| `real_tick_orchestrator.py::_RealRunnerPort` (19 атрибутов, 10 методов) | **safe delete** | R-021-8; единственный потребитель — сам модуль |
| `RealTickOrchestrator.tick_real_mode` / `fail_run` / `flush_pending_storage` / `_await_pending_clearing` / `_open_money_phase` / `_record_money_conflict_tick` | перенос в `tick.py::RealTick` (`tick` / `fail_run` / `flush_pending_storage` / те же имена) | вызывающие — только `RealRunnerImpl` (3 делегата, переписаны); тесты — §2 |
| `RealTickOrchestrator.tick_real_mode_clearing` (делегат с `time_budget_ms_override`/`max_depth_override`) | **safe delete** — часть мёртвой цепочки, принадлежащая стадии 4 (Changelog стадии 3) | `git grep tick_real_mode_clearing -- app`: вызова через оркестратор нет |
| `real_tick_clearing_coordinator.py::RealTickClearingCoordinator` (`maybe_run_clearing`, `compute_static_clearing_hard_timeout_sec(safe_int_env=)`, `_execute_clearing_with_timeout`, три копии колбэков) | перенос: `RealTick.maybe_run_clearing`, `RealTick.clearing_hard_timeout_sec()`, `_execute_clearing_with_timeout`; колбэки — одна копия `_commit_and_resolve` | каденция — `test_simulator_static_clearing_cadence.py`; таймаут — `test_static_clearing_hard_timeout_no_leak.py`, 023(d) |
| `real_tick_payments_coordinator.py::RealTickPaymentsCoordinator.run_payments_phase` | перенос: `RealTick.run_payments_phase` (коллабораторы — с раннера в момент вызова) | `test_real_payments_ordered_journal.py`, `test_p015_t1525_control_postgres.py` |
| `RealTickPaymentsPhaseResult` | переименован `TickPaymentsPhase`, без `rejection_codes_by_eq` | `test_p015_p1_money_phase_replay.py`, `test_tick_money_phase_resolution.py` |
| `real_tick_trust_drift_coordinator.py::RealTickTrustDriftCoordinator.apply_trust_decay_and_broadcast` | перенос: `RealTick.apply_trust_decay_and_broadcast` (граница долговечности и откат владельцем — те же строки) | R-021-3, R-021-4 (+контроль), `test_tick_commit_cancellation.py` |
| `real_tick_metrics.py::RealTickMetrics.populate_per_eq_metric_values` | перенос: `RealTick.populate_per_eq_metric_values` | `test_simulator_metrics_bottlenecks_real_mode.py`, `test_simulator_metrics_numeric_value_postgres.py` |
| `real_tick_persistence.py::RealTickPersistence.persist_tick_tail` / `flush_pending_storage` | перенос: `RealTick.persist_tick_tail` / `flush_pending_storage` | `test_tick_persistence_post_commit.py`, `test_p1_tick_storage_flush_marker.py`, `test_p1_tick_session_ownership_postgres.py` |
| `RealRunnerImpl._real_tick_persistence/_metrics/_clearing_coordinator/_trust_drift_coordinator/_payments_coordinator/_orchestrator` | **safe delete** → один `RealRunnerImpl._tick` | `git grep -E '_real_tick_(orchestrator|…)' -- app tests` = 0 |
| `RealPaymentsResult.rejection_codes_by_eq` + счётчик `_rejection_code_inc` в исполнителе | **safe delete** (читателя нет с 3-й стадии; Changelog стадии 3 отдаёт стадии 4) | `git grep rejection_codes_by_eq -- app tests scripts` = 0; `rejection_code` наблюдения (SSE `tx.failed`) не тронут |
| `RealRunnerImpl.tick_real_mode_clearing` и `RealRunner.tick_real_mode_clearing` (`real_runner.py`) | **keep → `T2109`**: вызывающих в `app/` больше нет (тик зовёт драйвер сам), но `real_runner.py` переопределяет метод через `super()`, а файл принадлежит `T2109` | записано в докстринге метода |
| `time_budget_ms_override` / `max_depth_override` / `clearing_service_cls` в драйвере, `real_runner.py`, `RealRunnerImpl` | **keep → `T2109`** | Changelog стадии 3 |
| `money_replay.py` | **keep** (владелец повтора 019, F-021-10) | не изменён |
| `post_tick_audit.py` | **keep** — решение Р-4.1 ниже | вызов перенесён в `RealTick._audit_after_tick` без изменений |
| `scripts/measure_p021_trust_line_batches.py` (затухание через `RealTickTrustDriftCoordinator`) | перенос на `RealTick.apply_trust_decay_and_broadcast` | `py_compile`; замер не перезапускался (§5 ниже) |
| `scripts/p021_benchmark_config.json:53` — текст `"call": "RealTickTrustDriftCoordinator…"` | **keep**: конфигурация замера заморожена (Verification plan п. 4) | тот же код под новым именем |

### Ссылки-комментарии на удалённые модули вне owner surface — **keep**, класс 2 в `BACKLOG.md`

`app/api/v1/simulator.py:1964` (цикл Interact-клиринга — только через `T2109`), `app/core/simulator/metrics_bottlenecks.py:177`, `:272`
(исключён, 022), `app/core/simulator/storage.py:408`, `:523` (исключён), `app/core/simulator/run_perimeter.py:18` (вне owner surface),
`docs/ru/admin-ui/specs/UNFINISHED.md:101` (датированная цитата). Исправлены в owner surface: `trust_drift_engine.py:452`, `09:236`,
`simulator-domain-model.md:176`, `test-plan.md:73`.

## 2. Тесты: перенос и удаление

Удалено тестов: **0**. Все ассерты перенесены без изменения смысла; меняется только способ собрать стенд.

| Модуль (на `2df5703`) | Стало | Что изменилось в стенде |
|---|---|---|
| `tests/unit/test_real_tick_orchestrator_rollback_resolution.py` | `tests/unit/test_tick_money_phase_resolution.py` | дублёры координаторов → дублёры `RealTick.run_payments_phase`/`maybe_run_clearing`, взятые с раннера в момент вызова |
| `tests/unit/test_real_tick_commit_cancellation.py` | `tests/unit/test_tick_commit_cancellation.py` | три класса → три метода `RealTick`; колбэки — через объект результата фазы |
| `tests/unit/test_real_tick_orchestrator_pending_clearing.py` | `tests/unit/test_tick_pending_clearing.py` | `RealTick` над `tick_unit_runner` |
| `tests/unit/test_real_tick_persistence_post_commit.py` | `tests/unit/test_tick_persistence_post_commit.py` | оба ассерта (Verification plan п. 3) без изменений |
| `tests/unit/test_real_tick_clearing_coordinator_static.py` | `tests/unit/test_tick_static_clearing.py` | драйвер заменяется в точке `_run_clearing`, объём — через накопитель закоммиченного; `CLEARING_ENABLED` — через `settings` |
| `test_p1_tick_storage_flush_marker.py`, `test_real_payments_ordered_journal.py`, `test_simulator_metrics_bottlenecks_real_mode.py`, `test_p015_p1_money_phase_replay.py` | на месте | конструкторы классов → `unit_tick(...)` / `TickPaymentsPhase` |
| `test_static_clearing_hard_timeout_no_leak.py`, `test_simulator_metrics_numeric_value_postgres.py`, `test_simulator_clearing_no_deadlock.py`, `test_p023_d_tick_driver_through_runner_postgres.py` | на месте | точка вызова — `RealTick`; таймаут — `clearing_hard_timeout_sec()` |
| `test_p015_p1_money_replay_postgres.py`, `test_p015_t1525_control_postgres.py`, `test_p021_decay_failure_is_rolled_back_by_its_owner_postgres.py`, `test_post_tick_audit_drift_runner_integration.py` | на месте | подмена метода класса/экземпляра — на `RealTick` |
| `test_p021_adaptive_clearing_is_gone.py` | на месте | контрпроверка «скан читает носителей»: удалённый координатор → `tick.py` |

Новый вспомогательный код тестов: `tests/simulator_tick_stand.py::tick_unit_runner`, `unit_tick`.

## 3. Red-first (коммит `1d3f9fe`, код `2df5703`)

Канонический раннер с `PYTEST_ADDOPTS="--runxfail --tb=line -q"` (только чтобы напечатать сообщение; с маркером — `10 passed, 2 xfailed`):

```
tests.p019_support.TargetMismatch: the tick is not one module without a runner port:
tests.p019_support.TargetMismatch: the tick reports clearing volume 0 for PQT; committed and published: 2.00000000
```

R-021-8 на `2df5703` — 18 находок: шесть файлов `real_tick_*`, шесть импортов в `real_runner_impl.py:34-40`, пять в
`real_tick_orchestrator.py:33-37`, `class _RealRunnerPort` (`:41`); точка входа денежной фазы — `real_tick_orchestrator.py:333`.

## 4. Мутации (каждая откатана, `git status` чист после каждой)

| Мутация | Селектор | Итог |
|---|---|---|
| объём снова из успешного возврата (при таймауте — нули) | `test_p023_d_…`, `test_tick_static_clearing.py` | красный: `TargetMismatch: the tick reports clearing volume 0 for PQT; committed and published: 2.00000000` |
| затухание перенесено после хвоста | `test_p021_decay_failure_…` | красный контроль: `{('C','D'): (100.00000000, 'active'), ('C','G'): (100.00000000, 'active')}` — отказ хвоста теперь съедает затухание |
| откат владельцем затухания убран | `test_p021_decay_failure_…` | 2 красных (`test_a_decay_failing_after_its_first_line_leaves_nothing_behind`, `test_a_decay_whose_checkpoint_fails_leaves_nothing_behind`): детектор пакета отклоняет поздний коммит, тик падает `REAL_MODE_TICK_FAILED` |
| возвращён модуль `app/core/simulator/real_tick_readded.py` | R-021-8 | красный `TargetMismatch: the tick is not one module without a runner port` |
