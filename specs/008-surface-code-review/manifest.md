# 008 — Манифест «файл → слайс»

- **Date:** 2026-08-12
- **Frozen HEAD волн 2–6:** `7cb5149eb03c902977ac0d740d73a933a54e372e`
- **Назначение:** исполняемое разбиение объявленного scope. Каждый tracked-файл scope назначен
  **ровно одному** слайсу правилами ниже (первое совпадение выигрывает — двойное назначение
  невозможно по построению; catch-all в конце каждой поверхности делает пропуск невозможным).
  Закрывает F-PA-2 аудита `plan-audit-2026-08-12.md`.

## Порядок правил (первое совпадение выигрывает)

| # | Правило | Слайс |
|---|---|---|
| 1 | `tests/**` | A4 |
| 2 | `simulator-ui/v2/src/**/*.test.ts`, `**/__snapshots__/**` | B4 |
| 3 | `admin-ui/src/**/*.test.ts`, `admin-ui/src/test/**` | C4 |
| 4 | `simulator-ui/v2/e2e/**`, `simulator-ui/v2/playwright.*` | B5 |
| 5 | `admin-ui/e2e/**`, `admin-ui/e2e-real/**`, `admin-ui/playwright.*` | C5 |
| 6 | `app/api/v1/{simulator,integrity,websocket}.py` | A1a |
| 7 | `app/api/**`, `app/schemas/**` | A1b |
| 8 | `app/core/simulator/real_*`, `sse_broadcast.py`, `runtime_impl.py`, `run_lifecycle.py` | A2a |
| 9 | `app/core/simulator/**` (остальное) | A2b |
| 10 | `app/core/**` (остальное) | A3 |
| 11 | `app/**` (остальное: `main.py`, `config.py`, `db/**`, `utils/**`, маркеры пакетов) | A1c |
| 12 | `simulator-ui/v2/src/api/**`, `composables/realEventPipeline.ts`, `composables/useSimulatorRealMode.ts` | B1 |
| 13 | `composables/useSimulatorApp.ts`, `useSceneState.ts`, `useLayoutCoordinator.ts`, `composables/windowManager/**`, `composables/interact/**` | B2 |
| 14 | `simulator-ui/v2/src/**` (остальное) | B3 |
| 15 | `admin-ui/src/{api,stores,types,constants}/**` | C1 |
| 16 | `admin-ui/src/pages/graph/**`, `composables/useGraphVisualization.ts`, `useGraphAnalytics.ts`, `advice/**` | C2 |
| 17 | `admin-ui/src/**` (остальное) | C3 |

Прецедент тестов: правила 1–5 стоят раньше функциональных, поэтому `.test.ts` принадлежит
тестовому слайсу, а не функциональному — так фактически действовала закрытая C1 (17 продуктовых
файлов, тесты отложены в C4).

## Контрольная проверка

Регенерация: `git ls-files app admin-ui/src simulator-ui/v2/src tests admin-ui/e2e admin-ui/e2e-real simulator-ui/v2/e2e` + 4 playwright-конфига, классификация правилами выше. Проверка обязана падать и при
пропуске (`UNASSIGNED`), и при расхождении числа строк манифеста с числом файлов scope.

Итог на frozen HEAD: **710 файлов, 0 UNASSIGNED, 0 двойных назначений.**

| Слайс | Файлов | | Слайс | Файлов |
|---|---|---|---|---|
| A1a | 3 | | B1 | 9 |
| A1b | 28 | | B2 | 11 |
| A1c | 29 | | B3 | 146 |
| A2a | 17 | | B4 | 100 |
| A2b | 24 | | B5 | 16 |
| A3 | 22 | | C1 | 17 |
| A4 | 185 | | C2 | 24 |
| | | | C3 | 39 |
| | | | C4 | 34 |
| | | | C5 | 6 |

## Исполнительское разбиение волны 4 (2026-08-14)

**Правила 14 и 17 не меняются.** Слайсы `B3` и `C3` остаются теми же множествами файлов и теми же
единицами учёта в `evidence-index.md`. Ниже — разбиение **внутри** слайса на исполнительские
под-слайсы: замер показал, что `B3` = 24 091 физическая строка (20 589 непустых) и `C3` = 10 831
(9 889), то есть волна 4 больше волн 2 и 3 вместе, а один агент на такой объём не держит глубину
волны 3. Ориентир разбиения — когезия по слою, не равный размер; верхняя граница выведена из
факта: крупнейший слайс, реально пройденный агентом на этой глубине, — `A3` (22 файла / 8 228
физических строк, волна 3).

Правила под-слайсов применяются **внутри** уже определённого слайса, первое совпадение выигрывает;
пути даны относительно `simulator-ui/v2/src/` и `admin-ui/src/`.

| Под-слайс | Правило | Файлов | Физ. строк | Непустых | Файлов > 400 |
|---|---|---|---|---|---|
| B3-1 | `composables/**` | 56 | 8 108 | 6 795 | 4 |
| B3-2 | `components/**`, `App.vue`, `App.css`, `styles.css`, `main.ts` | 24 | 6 631 | 5 719 | 4 |
| B3-3 | `render/**`, `layout/**` | 21 | 3 672 | 3 230 | 2 |
| B3-4 | остальное B3: `ui-kit/**`, `utils/**`, `types/**`, `config/**`, `demo/**`, `dev/**`, корень (`fixtures.ts`, `types.ts`, `vizMapping.ts`, `scenes.ts`, `design-system-demo.ts`, `env.d.ts`, `vite-env.d.ts`) | 45 | 5 680 | 4 845 | 3 |
| **B3 итого** | правило 14 манифеста | **146** | **24 091** | **20 589** | **13** |
| C3-1 | `pages/**`, `composables/useGraphData.ts` | 12 | 6 914 | 6 329 | 11 |
| C3-2 | остальное C3: `i18n/**`, `content/**`, `layout/**`, `ui/**`, `utils/**`, `router/**`, `composables/useLatestRequest.ts`, `composables/useRouteHydrationGuard.ts`, корень (`App.vue`, `main.ts`, `style.css`, `env.d.ts`) | 27 | 3 917 | 3 560 | 4 |
| **C3 итого** | правило 17 манифеста | **39** | **10 831** | **9 889** | **15** |

`useGraphData.ts` отнесён к C3-1, а не к C3-2, потому что он — прямая пара `GraphPage.vue`
(соседство с закрытым слайсом C2, у которого девять P2): их следствия читаются вместе.

**Контрольная проверка разбиения** (2026-08-14, дерево `f008890`, продуктовый scope байт-в-байт
равен frozen HEAD): объединение B3-1…B3-4 равно множеству B3 манифеста (146 = 146), объединение
C3-1…C3-2 равно множеству C3 (39 = 39), файлов с двумя назначениями — **0**, пересечения между
под-слайсами разных слайсов — нет.

## Исполнительское разбиение волны 5 (2026-08-14)

**Правила 1–5 не меняются.** Слайсы `A4`, `B4`, `C4`, `B5`, `C5` остаются теми же множествами
файлов и теми же единицами учёта в `evidence-index.md`. Ниже — разбиение **внутри** слайсов `A4`
и `B4`, потому что замер показал: волна 5 — **82 085 физических строк (70 052 непустых) в 341
файле**, то есть крупнее любой предыдущей волны, включая волну 4 (34 920 физических).

Ориентир разбиения — когезия по роли, затем выравнивание по объёму. Верхняя граница выведена из
факта, а не из вкуса: крупнейший под-слайс, реально пройденный одним агентом на глубине волны 4, —
`B3-1` (56 файлов / 8 108 физических строк). Формат волны 5 — матрица групп, а не пофайловый
вердикт (F-PA-5), поэтому запись дешевле, но **чтение остаётся пофайловым**: приложение покрытия
обязано содержать строку на каждый файл.

`A4-1` выделен по роли, а не по объёму: он несёт уникальную функцию волны — единственный
`tests/conftest.py`, контрактный гард и восемь policy/architecture guards. Его выводы (таксономия
маркеров, изоляция, знаменатели гардов) являются входом для остальных под-слайсов `A4`.

| Под-слайс | Правило | Файлов | Физ. строк | Непустых | Файлов > 400 |
|---|---|---|---|---|---|
| A4-1 | `tests/conftest.py`, `tests/__init__.py`, `tests/test_participants_me_and_auth_payloads.py`, `tests/artifacts/**`, `tests/contract/**` + 8 policy guards из `tests/unit/` (`test_backend_marker_policy`, `test_postgres_marker_fail_closed`, `test_postgres_test_taxonomy`, `test_alembic_postgres_only`, `test_deployment_config`, `test_quality_workflow_schedule`, `test_static_diagnostics_policy`, `test_run_full_stack_database_url_redaction`) | 14 | 7 390 | 6 715 | 3 |
| A4-2 | `tests/integration/**`, часть 1 (по алфавиту, деление по накопленному объёму) | 34 | 10 053 | 8 879 | 8 |
| A4-3 | `tests/integration/**`, часть 2 | 30 | 7 330 | 6 288 | 6 |
| A4-4 | `tests/unit/**` без 8 guards, часть 1 | 41 | 7 588 | 6 350 | 4 |
| A4-5 | `tests/unit/**` без 8 guards, часть 2 | 30 | 7 580 | 6 370 | 6 |
| A4-6 | `tests/unit/**` без 8 guards, часть 3 | 36 | 7 385 | 5 948 | 8 |
| **A4 итого** | правило 1 манифеста | **185** | **47 326** | **40 550** | **35** |
| B4-1 | `simulator-ui/v2/src/**` тесты и снапшоты, часть 1 | 18 | 8 905 | 7 425 | 3 |
| B4-2 | часть 2 | 40 | 8 703 | 7 271 | 6 |
| B4-3 | часть 3 | 42 | 8 163 | 6 906 | 3 |
| **B4 итого** | правило 2 манифеста | **100** | **25 771** | **21 602** | **12** |
| C4 | правило 3 манифеста, не делится | 34 | 6 283 | 5 514 | 4 |
| B5 | правило 4 манифеста, не делится | 16 | 2 235 | 1 986 | 2 |
| C5 | правило 5 манифеста, не делится | 6 | 470 | 400 | 0 |
| **Волна 5 итого** | | **341** | **82 085** | **70 052** | **53** |

**Контрольная проверка разбиения** (2026-08-14, дерево `f008890`, продуктовый scope байт-в-байт
равен frozen HEAD; разбиение сгенерировано машинно из `git ls-files`, а не составлено вручную):
объединение A4-1…A4-6 равно множеству `A4` манифеста (185 = 185), объединение B4-1…B4-3 равно
множеству `B4` (100 = 100), файлов с двумя назначениями — **0**, пересечений между под-слайсами
разных слайсов — нет.

## Полный список

```
A1a	app/api/v1/integrity.py
A1a	app/api/v1/simulator.py
A1a	app/api/v1/websocket.py
A1b	app/api/__init__.py
A1b	app/api/deps.py
A1b	app/api/router.py
A1b	app/api/v1/__init__.py
A1b	app/api/v1/admin.py
A1b	app/api/v1/auth.py
A1b	app/api/v1/balance.py
A1b	app/api/v1/clearing.py
A1b	app/api/v1/equivalents.py
A1b	app/api/v1/health.py
A1b	app/api/v1/participants.py
A1b	app/api/v1/payments.py
A1b	app/api/v1/trustlines.py
A1b	app/schemas/__init__.py
A1b	app/schemas/admin.py
A1b	app/schemas/auth.py
A1b	app/schemas/balance.py
A1b	app/schemas/clearing.py
A1b	app/schemas/common.py
A1b	app/schemas/equivalent.py
A1b	app/schemas/equivalents.py
A1b	app/schemas/graph.py
A1b	app/schemas/integrity.py
A1b	app/schemas/metrics.py
A1b	app/schemas/participant.py
A1b	app/schemas/payment.py
A1b	app/schemas/simulator.py
A1b	app/schemas/trustline.py
A1c	app/__init__.py
A1c	app/config.py
A1c	app/db/__init__.py
A1c	app/db/base.py
A1c	app/db/models/__init__.py
A1c	app/db/models/audit_log.py
A1c	app/db/models/auth_challenge.py
A1c	app/db/models/config.py
A1c	app/db/models/debt.py
A1c	app/db/models/equivalent.py
A1c	app/db/models/integrity_checkpoint.py
A1c	app/db/models/participant.py
A1c	app/db/models/prepare_lock.py
A1c	app/db/models/simulator_storage.py
A1c	app/db/models/transaction.py
A1c	app/db/models/trustline.py
A1c	app/db/session.py
A1c	app/main.py
A1c	app/utils/__init__.py
A1c	app/utils/background_jobs.py
A1c	app/utils/distributed_lock.py
A1c	app/utils/error_codes.py
A1c	app/utils/event_bus.py
A1c	app/utils/exceptions.py
A1c	app/utils/metrics.py
A1c	app/utils/observability.py
A1c	app/utils/request_id.py
A1c	app/utils/security.py
A1c	app/utils/validation.py
A2a	app/core/simulator/real_clearing_engine.py
A2a	app/core/simulator/real_debt_snapshot_loader.py
A2a	app/core/simulator/real_payment_action.py
A2a	app/core/simulator/real_payment_planner.py
A2a	app/core/simulator/real_payments_executor.py
A2a	app/core/simulator/real_runner.py
A2a	app/core/simulator/real_runner_impl.py
A2a	app/core/simulator/real_scenario_seeder.py
A2a	app/core/simulator/real_tick_clearing_coordinator.py
A2a	app/core/simulator/real_tick_metrics.py
A2a	app/core/simulator/real_tick_orchestrator.py
A2a	app/core/simulator/real_tick_payments_coordinator.py
A2a	app/core/simulator/real_tick_persistence.py
A2a	app/core/simulator/real_tick_trust_drift_coordinator.py
A2a	app/core/simulator/run_lifecycle.py
A2a	app/core/simulator/runtime_impl.py
A2a	app/core/simulator/sse_broadcast.py
A2b	app/core/simulator/__init__.py
A2b	app/core/simulator/adaptive_clearing_policy.py
A2b	app/core/simulator/artifacts.py
A2b	app/core/simulator/cache_invalidator.py
A2b	app/core/simulator/commit_resolution.py
A2b	app/core/simulator/edge_patch_builder.py
A2b	app/core/simulator/fixtures_runner.py
A2b	app/core/simulator/helpers.py
A2b	app/core/simulator/inject_executor.py
A2b	app/core/simulator/metrics_bottlenecks.py
A2b	app/core/simulator/models.py
A2b	app/core/simulator/net_balance_utils.py
A2b	app/core/simulator/post_tick_audit.py
A2b	app/core/simulator/rejection_codes.py
A2b	app/core/simulator/runtime.py
A2b	app/core/simulator/runtime_utils.py
A2b	app/core/simulator/scenario_equivalent.py
A2b	app/core/simulator/scenario_registry.py
A2b	app/core/simulator/session.py
A2b	app/core/simulator/snapshot_builder.py
A2b	app/core/simulator/storage.py
A2b	app/core/simulator/trust_drift_engine.py
A2b	app/core/simulator/viz_patch_helper.py
A2b	app/core/simulator/viz_rules.py
A3	app/core/__init__.py
A3	app/core/admin/__init__.py
A3	app/core/admin/metrics.py
A3	app/core/auth/__init__.py
A3	app/core/auth/canonical.py
A3	app/core/auth/crypto.py
A3	app/core/auth/service.py
A3	app/core/balance/__init__.py
A3	app/core/balance/service.py
A3	app/core/clearing/__init__.py
A3	app/core/clearing/service.py
A3	app/core/integrity.py
A3	app/core/invariants.py
A3	app/core/participants/__init__.py
A3	app/core/participants/service.py
A3	app/core/payments/__init__.py
A3	app/core/payments/engine.py
A3	app/core/payments/router.py
A3	app/core/payments/service.py
A3	app/core/recovery.py
A3	app/core/trustlines/__init__.py
A3	app/core/trustlines/service.py
A4	tests/__init__.py
A4	tests/artifacts/.gitkeep
A4	tests/conftest.py
A4	tests/contract/__init__.py
A4	tests/contract/test_openapi_contract.py
A4	tests/integration/__init__.py
A4	tests/integration/test_admin_endpoints.py
A4	tests/integration/test_admin_equivalent_input_validation.py
A4	tests/integration/test_admin_feature_flags_multipath.py
A4	tests/integration/test_admin_freeze_participant.py
A4	tests/integration/test_admin_mutation_audit_atomicity.py
A4	tests/integration/test_admin_routing_max_paths.py
A4	tests/integration/test_audit_drift_delta_check_sse_integration.py
A4	tests/integration/test_auth_refresh.py
A4	tests/integration/test_auth_token_type_enforced.py
A4	tests/integration/test_clearing_commit_replay_postgres.py
A4	tests/integration/test_clearing_max_depth_controls_long_cycles.py
A4	tests/integration/test_clearing_payment_prepare_interlock_postgres.py
A4	tests/integration/test_clearing_skip_releases_locks_postgres.py
A4	tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py
A4	tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py
A4	tests/integration/test_daily_limit_not_enforced.py
A4	tests/integration/test_equivalent_writer_and_legacy_reads.py
A4	tests/integration/test_health_and_equivalents.py
A4	tests/integration/test_http_rate_limit.py
A4	tests/integration/test_integrity_endpoints.py
A4	tests/integration/test_integrity_repairs_atomicity.py
A4	tests/integration/test_participants_search.py
A4	tests/integration/test_participants_type_default.py
A4	tests/integration/test_participants_uniqueness.py
A4	tests/integration/test_payment_abort_has_error_code.py
A4	tests/integration/test_payment_commit_advisory_locks_postgres.py
A4	tests/integration/test_payment_engine_audit_conflict_postgres.py
A4	tests/integration/test_payment_engine_uow_retry_postgres.py
A4	tests/integration/test_payment_idempotency_postgres.py
A4	tests/integration/test_payment_inverse_multisegment_postgres.py
A4	tests/integration/test_payment_pair_advisory_locks_postgres.py
A4	tests/integration/test_payment_prepare_capacity_policy.py
A4	tests/integration/test_payment_prepare_error_taxonomy.py
A4	tests/integration/test_payment_staged_multicall_postgres.py
A4	tests/integration/test_payments_amount_validation.py
A4	tests/integration/test_payments_constraints_avoid.py
A4	tests/integration/test_payments_idempotency.py
A4	tests/integration/test_payments_insufficient_capacity.py
A4	tests/integration/test_payments_list_filters.py
A4	tests/integration/test_payments_multipath.py
A4	tests/integration/test_post_tick_audit_drift_runner_integration.py
A4	tests/integration/test_prepare_locks_tx_id_fk_postgres.py
A4	tests/integration/test_scenarios.py
A4	tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py
A4	tests/integration/test_simulator_adaptive_clearing_integration.py
A4	tests/integration/test_simulator_artifacts_events_ndjson.py
A4	tests/integration/test_simulator_clearing_no_deadlock.py
A4	tests/integration/test_simulator_network_growth.py
A4	tests/integration/test_simulator_real_snapshot_db_enrichment.py
A4	tests/integration/test_simulator_scenario_upload_validation.py
A4	tests/integration/test_simulator_sse_fixtures_clearing_animation_pair.py
A4	tests/integration/test_simulator_sse_real_smoke.py
A4	tests/integration/test_simulator_sse_replay_410.py
A4	tests/integration/test_simulator_sse_smoke.py
A4	tests/integration/test_simulator_sse_trust_drift_decay_topology_patch.py
A4	tests/integration/test_simulator_sse_tx_failed_timeout.py
A4	tests/integration/test_simulator_super_smoke.py
A4	tests/integration/test_static_clearing_hard_timeout_no_leak.py
A4	tests/integration/test_trustline_cache_invalidation.py
A4	tests/integration/test_trustline_negative_constraints.py
A4	tests/integration/test_trustlines_get_by_id.py
A4	tests/integration/test_trustlines_list_filters_pagination.py
A4	tests/integration/test_validation_error_envelope.py
A4	tests/test_participants_me_and_auth_payloads.py
A4	tests/unit/__init__.py
A4	tests/unit/test_admin_abort_tx.py
A4	tests/unit/test_admin_audit_log_list.py
A4	tests/unit/test_admin_clearing_cycles.py
A4	tests/unit/test_admin_config_patch_atomicity.py
A4	tests/unit/test_admin_graph_ego.py
A4	tests/unit/test_admin_graph_snapshot.py
A4	tests/unit/test_admin_incidents_list.py
A4	tests/unit/test_admin_liquidity_summary.py
A4	tests/unit/test_admin_participant_metrics.py
A4	tests/unit/test_admin_participants_list.py
A4	tests/unit/test_admin_participants_stats.py
A4	tests/unit/test_admin_token_comparison.py
A4	tests/unit/test_admin_trustlines_bottlenecks.py
A4	tests/unit/test_admin_trustlines_list.py
A4	tests/unit/test_admin_whoami_and_extras.py
A4	tests/unit/test_alembic_postgres_only.py
A4	tests/unit/test_apply_flow_retry_on_stale.py
A4	tests/unit/test_audit_drift_integrity_log.py
A4	tests/unit/test_audit_drift_sse_event.py
A4	tests/unit/test_backend_marker_policy.py
A4	tests/unit/test_background_task_supervision.py
A4	tests/unit/test_balance_service_summary.py
A4	tests/unit/test_canonical_json.py
A4	tests/unit/test_check_alembic_heads.py
A4	tests/unit/test_clearing_additional_cases.py
A4	tests/unit/test_clearing_auto_clearing_policy.py
A4	tests/unit/test_clearing_plan_edges_extraction.py
A4	tests/unit/test_clearing_prepare_lock_conflict.py
A4	tests/unit/test_clearing_sql_cycle_detection.py
A4	tests/unit/test_crypto_pid.py
A4	tests/unit/test_debt_optimistic_lock.py
A4	tests/unit/test_debt_symmetry.py
A4	tests/unit/test_deployment_config.py
A4	tests/unit/test_edge_patch_builder.py
A4	tests/unit/test_edges_by_equivalent_status_filter.py
A4	tests/unit/test_equivalent_code_validation.py
A4	tests/unit/test_equivalent_metadata_validation.py
A4	tests/unit/test_event_bus_backpressure.py
A4	tests/unit/test_fixtures_runner_clearing_done_amount.py
A4	tests/unit/test_flow_and_periodicity.py
A4	tests/unit/test_freeze_participant_in_memory_status_overwrite.py
A4	tests/unit/test_integrity_checkpoints.py
A4	tests/unit/test_interact_actions_backend_p1.py
A4	tests/unit/test_invariants.py
A4	tests/unit/test_metrics_unmatched_routes_path_label.py
A4	tests/unit/test_net_balance_utils.py
A4	tests/unit/test_payment_cleanup_cancellation.py
A4	tests/unit/test_payment_db_error_classifier.py
A4	tests/unit/test_payment_delta_check.py
A4	tests/unit/test_payment_engine_advisory_lock_key.py
A4	tests/unit/test_payment_engine_advisory_locks_execute.py
A4	tests/unit/test_payment_engine_retry_savepoint_nocommit.py
A4	tests/unit/test_payment_router_invalidate_cache.py
A4	tests/unit/test_payment_staged_post_commit.py
A4	tests/unit/test_payment_timeouts.py
A4	tests/unit/test_payments_2pc.py
A4	tests/unit/test_post_tick_audit.py
A4	tests/unit/test_postgres_marker_fail_closed.py
A4	tests/unit/test_postgres_test_taxonomy.py
A4	tests/unit/test_pytest_selector_guard.py
A4	tests/unit/test_quality_workflow_schedule.py
A4	tests/unit/test_rate_limit_memory_bound.py
A4	tests/unit/test_real_clearing_engine_partial_failure.py
A4	tests/unit/test_real_payments_ordered_journal.py
A4	tests/unit/test_real_runner_tick_nested_partial_failures.py
A4	tests/unit/test_real_tick_clearing_coordinator_adaptive.py
A4	tests/unit/test_real_tick_commit_cancellation.py
A4	tests/unit/test_real_tick_orchestrator_pending_clearing.py
A4	tests/unit/test_real_tick_orchestrator_rollback_resolution.py
A4	tests/unit/test_real_tick_persistence_post_commit.py
A4	tests/unit/test_recovery_cleanup.py
A4	tests/unit/test_request_id_middleware_validation.py
A4	tests/unit/test_routing_constraints_timeout_ms.py
A4	tests/unit/test_routing_reserved_and_policy.py
A4	tests/unit/test_run_full_stack_database_url_redaction.py
A4	tests/unit/test_run_lifecycle_per_run_scenario_isolation.py
A4	tests/unit/test_scenario_inject_topology.py
A4	tests/unit/test_settings_guardrails.py
A4	tests/unit/test_simulator_actions_feature_flag.py
A4	tests/unit/test_simulator_actions_serialization.py
A4	tests/unit/test_simulator_actor_and_csrf.py
A4	tests/unit/test_simulator_adaptive_clearing_effectiveness_synthetic.py
A4	tests/unit/test_simulator_adaptive_clearing_policy.py
A4	tests/unit/test_simulator_cookie_session.py
A4	tests/unit/test_simulator_fixtures_clearing_plan_done_pair.py
A4	tests/unit/test_simulator_owner_isolation.py
A4	tests/unit/test_simulator_real_amount_model.py
A4	tests/unit/test_simulator_real_clearing_throttle.py
A4	tests/unit/test_simulator_real_events_stress.py
A4	tests/unit/test_simulator_real_flush_pending_storage.py
A4	tests/unit/test_simulator_real_planner_determinism.py
A4	tests/unit/test_simulator_rejection_codes.py
A4	tests/unit/test_simulator_run_status_response_schema.py
A4	tests/unit/test_simulator_scenario_allowlist_and_archives.py
A4	tests/unit/test_simulator_sse_replay.py
A4	tests/unit/test_simulator_sse_replay_atomic.py
A4	tests/unit/test_simulator_sse_trust_drift_decay_topology_patch.py
A4	tests/unit/test_simulator_storage_schema_contract.py
A4	tests/unit/test_simulator_tx_failed_event_schema.py
A4	tests/unit/test_simulator_tx_updated_amount_flyout_contract.py
A4	tests/unit/test_simulator_write_tick_metrics_upsert.py
A4	tests/unit/test_sse_queue_full_policy.py
A4	tests/unit/test_sse_rate_limit.py
A4	tests/unit/test_static_diagnostics_policy.py
A4	tests/unit/test_test_database_guard.py
A4	tests/unit/test_topology_changed_no_empty_payload.py
A4	tests/unit/test_trust_drift.py
A4	tests/unit/test_trust_drift_decay_does_not_break_trust_limits.py
A4	tests/unit/test_trustline_audit_fail_closed.py
A4	tests/unit/test_trustline_signatures.py
A4	tests/unit/test_trustline_timestamps.py
A4	tests/unit/test_warmup_and_capacity.py
A4	tests/unit/test_websocket_payment_received_event.py
A4	tests/unit/test_zero_debt_policy.py
B1	simulator-ui/v2/src/api/apiBase.ts
B1	simulator-ui/v2/src/api/http.ts
B1	simulator-ui/v2/src/api/normalizeSimulatorEvent.ts
B1	simulator-ui/v2/src/api/simulatorApi.ts
B1	simulator-ui/v2/src/api/simulatorContracts.ts
B1	simulator-ui/v2/src/api/simulatorTypes.ts
B1	simulator-ui/v2/src/api/sse.ts
B1	simulator-ui/v2/src/composables/realEventPipeline.ts
B1	simulator-ui/v2/src/composables/useSimulatorRealMode.ts
B2	simulator-ui/v2/src/composables/interact/useInteractDataCache.ts
B2	simulator-ui/v2/src/composables/interact/useInteractFSM.ts
B2	simulator-ui/v2/src/composables/interact/useInteractHistory.ts
B2	simulator-ui/v2/src/composables/useLayoutCoordinator.ts
B2	simulator-ui/v2/src/composables/useSceneState.ts
B2	simulator-ui/v2/src/composables/useSimulatorApp.ts
B2	simulator-ui/v2/src/composables/windowManager/geometry.ts
B2	simulator-ui/v2/src/composables/windowManager/interactWindowOfPhase.ts
B2	simulator-ui/v2/src/composables/windowManager/types.ts
B2	simulator-ui/v2/src/composables/windowManager/useWindowManager.ts
B2	simulator-ui/v2/src/composables/windowManager/windowContainerContext.ts
B3	simulator-ui/v2/src/App.css
B3	simulator-ui/v2/src/App.vue
B3	simulator-ui/v2/src/components/ActionBar.vue
B3	simulator-ui/v2/src/components/BottomBar.vue
B3	simulator-ui/v2/src/components/ClearingPanel.vue
B3	simulator-ui/v2/src/components/DevPerfOverlay.vue
B3	simulator-ui/v2/src/components/EdgeDetailPopup.vue
B3	simulator-ui/v2/src/components/EdgeTooltip.vue
B3	simulator-ui/v2/src/components/ErrorToast.vue
B3	simulator-ui/v2/src/components/GraphNavigator.vue
B3	simulator-ui/v2/src/components/InteractHistoryLog.vue
B3	simulator-ui/v2/src/components/LabelsOverlayLayers.vue
B3	simulator-ui/v2/src/components/ManualPaymentPanel.vue
B3	simulator-ui/v2/src/components/NodeCardOverlay.vue
B3	simulator-ui/v2/src/components/SimulatorAppRoot.vue
B3	simulator-ui/v2/src/components/SuccessToast.vue
B3	simulator-ui/v2/src/components/SystemBalanceBar.vue
B3	simulator-ui/v2/src/components/TopBar.vue
B3	simulator-ui/v2/src/components/TrustlineManagementPanel.vue
B3	simulator-ui/v2/src/components/WindowShell.vue
B3	simulator-ui/v2/src/components/common/HudBar.vue
B3	simulator-ui/v2/src/components/common/OverlaySelect.vue
B3	simulator-ui/v2/src/composables/demoActivityHold.ts
B3	simulator-ui/v2/src/composables/dropdownFocusCore.ts
B3	simulator-ui/v2/src/composables/realFx/useRealClearingFx.ts
B3	simulator-ui/v2/src/composables/realFx/useRealTxFx.ts
B3	simulator-ui/v2/src/composables/simulatorIsAnimating.ts
B3	simulator-ui/v2/src/composables/useActivePanelState.ts
B3	simulator-ui/v2/src/composables/useAdminRunsPanel.ts
B3	simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts
B3	simulator-ui/v2/src/composables/useAppComputeLayout.ts
B3	simulator-ui/v2/src/composables/useAppDragToPinAndPreview.ts
B3	simulator-ui/v2/src/composables/useAppFxOverlays.ts
B3	simulator-ui/v2/src/composables/useAppLayoutWiring.ts
B3	simulator-ui/v2/src/composables/useAppLifecycle.ts
B3	simulator-ui/v2/src/composables/useAppPhysicsAndPinning.ts
B3	simulator-ui/v2/src/composables/useAppPhysicsAndPinningWiring.ts
B3	simulator-ui/v2/src/composables/useAppPickingAndHover.ts
B3	simulator-ui/v2/src/composables/useAppRenderLoop.ts
B3	simulator-ui/v2/src/composables/useAppSceneState.ts
B3	simulator-ui/v2/src/composables/useAppUiDerivedState.ts
B3	simulator-ui/v2/src/composables/useAppViewAndNodeCard.ts
B3	simulator-ui/v2/src/composables/useAppViewWiring.ts
B3	simulator-ui/v2/src/composables/useCamera.ts
B3	simulator-ui/v2/src/composables/useCanvasInteractions.ts
B3	simulator-ui/v2/src/composables/useCookieSessionBootstrap.ts
B3	simulator-ui/v2/src/composables/useDestructiveConfirmation.ts
B3	simulator-ui/v2/src/composables/useDragPreview.ts
B3	simulator-ui/v2/src/composables/useDragToPinInteraction.ts
B3	simulator-ui/v2/src/composables/useEdgeHover.ts
B3	simulator-ui/v2/src/composables/useEdgeTooltip.ts
B3	simulator-ui/v2/src/composables/useFloatingLabelsViewFx.ts
B3	simulator-ui/v2/src/composables/useFxDebugControls.ts
B3	simulator-ui/v2/src/composables/useGeoSimDevHookSetup.ts
B3	simulator-ui/v2/src/composables/useHudDropdownFocus.ts
B3	simulator-ui/v2/src/composables/useInteractActions.ts
B3	simulator-ui/v2/src/composables/useInteractAutoBootstrapRun.ts
B3	simulator-ui/v2/src/composables/useInteractMode.ts
B3	simulator-ui/v2/src/composables/useLabelNodes.ts
B3	simulator-ui/v2/src/composables/useLayoutIndex.ts
B3	simulator-ui/v2/src/composables/useNodeCard.ts
B3	simulator-ui/v2/src/composables/useOverlayDropdownFocus.ts
B3	simulator-ui/v2/src/composables/useOverlayState.ts
B3	simulator-ui/v2/src/composables/useParticipantsList.ts
B3	simulator-ui/v2/src/composables/usePersistedSimulatorPrefs.ts
B3	simulator-ui/v2/src/composables/usePhysicsManager.ts
B3	simulator-ui/v2/src/composables/usePicking.ts
B3	simulator-ui/v2/src/composables/usePinning.ts
B3	simulator-ui/v2/src/composables/useQualityAutoguards.ts
B3	simulator-ui/v2/src/composables/useReducedMotionPreference.ts
B3	simulator-ui/v2/src/composables/useRenderLoop.ts
B3	simulator-ui/v2/src/composables/useSelectedNodeEdgeStats.ts
B3	simulator-ui/v2/src/composables/useSnapshotIndex.ts
B3	simulator-ui/v2/src/composables/useSystemBalance.ts
B3	simulator-ui/v2/src/composables/useTopBarContext.ts
B3	simulator-ui/v2/src/composables/useViewControls.ts
B3	simulator-ui/v2/src/composables/useWindowController.ts
B3	simulator-ui/v2/src/composables/useWmEdgeDetail.ts
B3	simulator-ui/v2/src/config/equivalents.ts
B3	simulator-ui/v2/src/config/fxConfig.ts
B3	simulator-ui/v2/src/demo/patches.ts
B3	simulator-ui/v2/src/demo/timerRegistry.ts
B3	simulator-ui/v2/src/design-system-demo.ts
B3	simulator-ui/v2/src/dev/DesignSystemDemoApp.vue
B3	simulator-ui/v2/src/dev/geoSimDevHook.ts
B3	simulator-ui/v2/src/env.d.ts
B3	simulator-ui/v2/src/fixtures.ts
B3	simulator-ui/v2/src/layout/forceLayout.ts
B3	simulator-ui/v2/src/layout/physicsD3.ts
B3	simulator-ui/v2/src/main.ts
B3	simulator-ui/v2/src/render/baseGraph.ts
B3	simulator-ui/v2/src/render/color.ts
B3	simulator-ui/v2/src/render/fxConfig.ts
B3	simulator-ui/v2/src/render/fxRenderer.ts
B3	simulator-ui/v2/src/render/fxRenderer/easing.ts
B3	simulator-ui/v2/src/render/fxRenderer/outlineCache.ts
B3	simulator-ui/v2/src/render/fxRenderer/renderFrame.ts
B3	simulator-ui/v2/src/render/fxRenderer/spawn.ts
B3	simulator-ui/v2/src/render/fxRenderer/state.ts
B3	simulator-ui/v2/src/render/fxRenderer/worldRect.ts
B3	simulator-ui/v2/src/render/glowSprites.ts
B3	simulator-ui/v2/src/render/gradientCache.ts
B3	simulator-ui/v2/src/render/linkGeometry.ts
B3	simulator-ui/v2/src/render/nodeFill.ts
B3	simulator-ui/v2/src/render/nodeGeometry.ts
B3	simulator-ui/v2/src/render/nodePainter.ts
B3	simulator-ui/v2/src/render/nodeSizing.ts
B3	simulator-ui/v2/src/render/readCssVar.ts
B3	simulator-ui/v2/src/render/roundedRect.ts
B3	simulator-ui/v2/src/scenes.ts
B3	simulator-ui/v2/src/styles.css
B3	simulator-ui/v2/src/types.ts
B3	simulator-ui/v2/src/types/layout.ts
B3	simulator-ui/v2/src/types/nodeShape.ts
B3	simulator-ui/v2/src/types/simulatorApp.ts
B3	simulator-ui/v2/src/types/uiPrefs.ts
B3	simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md
B3	simulator-ui/v2/src/ui-kit/designSystem.overlays.css
B3	simulator-ui/v2/src/ui-kit/designSystem.primitives.css
B3	simulator-ui/v2/src/ui-kit/designSystem.tokens.css
B3	simulator-ui/v2/src/ui-kit/overlayDiagnostics.ts
B3	simulator-ui/v2/src/ui-kit/overlayGeometry.ts
B3	simulator-ui/v2/src/ui-kit/overlaySurfaceCatalog.ts
B3	simulator-ui/v2/src/ui-kit/typography.md
B3	simulator-ui/v2/src/utils/clearingAmountAnchor.ts
B3	simulator-ui/v2/src/utils/counters.ts
B3	simulator-ui/v2/src/utils/edgeKey.ts
B3	simulator-ui/v2/src/utils/errorMessage.ts
B3	simulator-ui/v2/src/utils/hash.ts
B3	simulator-ui/v2/src/utils/isJwtLike.ts
B3	simulator-ui/v2/src/utils/isZeroDecimalString.ts
B3	simulator-ui/v2/src/utils/lruCache.ts
B3	simulator-ui/v2/src/utils/math.ts
B3	simulator-ui/v2/src/utils/numberFormat.ts
B3	simulator-ui/v2/src/utils/overlayPosition.ts
B3	simulator-ui/v2/src/utils/participants.ts
B3	simulator-ui/v2/src/utils/retryUntilTruthy.ts
B3	simulator-ui/v2/src/utils/runErrorClassification.ts
B3	simulator-ui/v2/src/utils/status.ts
B3	simulator-ui/v2/src/utils/stringHelpers.ts
B3	simulator-ui/v2/src/utils/throttledWarn.ts
B3	simulator-ui/v2/src/utils/txAmountLabel.ts
B3	simulator-ui/v2/src/utils/txDirection.ts
B3	simulator-ui/v2/src/utils/valueFormat.ts
B3	simulator-ui/v2/src/vite-env.d.ts
B3	simulator-ui/v2/src/vizMapping.ts
B4	simulator-ui/v2/src/api/apiBase.test.ts
B4	simulator-ui/v2/src/api/http.test.ts
B4	simulator-ui/v2/src/api/normalizeSimulatorEvent.test.ts
B4	simulator-ui/v2/src/api/simulatorApi.contract.test.ts
B4	simulator-ui/v2/src/api/sse.test.ts
B4	simulator-ui/v2/src/components/ActionBar.test.ts
B4	simulator-ui/v2/src/components/BottomBar.test.ts
B4	simulator-ui/v2/src/components/ClearingPanel.test.ts
B4	simulator-ui/v2/src/components/DevPerfOverlay.test.ts
B4	simulator-ui/v2/src/components/EdgeDetailPopup.test.ts
B4	simulator-ui/v2/src/components/EdgeTooltip.test.ts
B4	simulator-ui/v2/src/components/ErrorToast.test.ts
B4	simulator-ui/v2/src/components/GraphNavigator.test.ts
B4	simulator-ui/v2/src/components/InteractHistoryLog.test.ts
B4	simulator-ui/v2/src/components/InteractModeUi.test.ts
B4	simulator-ui/v2/src/components/ManualPaymentPanel.test.ts
B4	simulator-ui/v2/src/components/NodeCardOverlay.test.ts
B4	simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts
B4	simulator-ui/v2/src/components/SuccessToast.test.ts
B4	simulator-ui/v2/src/components/SystemBalanceBar.test.ts
B4	simulator-ui/v2/src/components/TopBar.focus.test.ts
B4	simulator-ui/v2/src/components/TopBar.wiring.test.ts
B4	simulator-ui/v2/src/components/TrustlineManagementPanel.test.ts
B4	simulator-ui/v2/src/components/WindowShell.test.ts
B4	simulator-ui/v2/src/components/common/HudBar.test.ts
B4	simulator-ui/v2/src/components/common/OverlaySelect.test.ts
B4	simulator-ui/v2/src/components/compactOverlayFormRails.test.ts
B4	simulator-ui/v2/src/composables/interact/useInteractDataCache.paymentTargets.test.ts
B4	simulator-ui/v2/src/composables/interact/useInteractDataCache.snapshotTrustlines.test.ts
B4	simulator-ui/v2/src/composables/interact/useInteractFSM.test.ts
B4	simulator-ui/v2/src/composables/layoutRawness.test.ts
B4	simulator-ui/v2/src/composables/realEventPipeline.test.ts
B4	simulator-ui/v2/src/composables/realFx/useRealClearingFx.test.ts
B4	simulator-ui/v2/src/composables/realFx/useRealTxFx.test.ts
B4	simulator-ui/v2/src/composables/simulatorIsAnimating.test.ts
B4	simulator-ui/v2/src/composables/useAppFxOverlays.test.ts
B4	simulator-ui/v2/src/composables/useCamera.test.ts
B4	simulator-ui/v2/src/composables/useCanvasInteractions.test.ts
B4	simulator-ui/v2/src/composables/useDestructiveConfirmation.test.ts
B4	simulator-ui/v2/src/composables/useDragToPinInteraction.test.ts
B4	simulator-ui/v2/src/composables/useEdgeHover.test.ts
B4	simulator-ui/v2/src/composables/useEdgeTooltip.test.ts
B4	simulator-ui/v2/src/composables/useFloatingLabelsViewFx.test.ts
B4	simulator-ui/v2/src/composables/useHudDropdownFocus.test.ts
B4	simulator-ui/v2/src/composables/useInteractActions.test.ts
B4	simulator-ui/v2/src/composables/useInteractMode.startPaymentFlow.test.ts
B4	simulator-ui/v2/src/composables/useInteractMode.test.ts
B4	simulator-ui/v2/src/composables/useLabelNodes.test.ts
B4	simulator-ui/v2/src/composables/useLayoutCoordinator.test.ts
B4	simulator-ui/v2/src/composables/useNodeCard.test.ts
B4	simulator-ui/v2/src/composables/useOverlayState.test.ts
B4	simulator-ui/v2/src/composables/useParticipantsList.test.ts
B4	simulator-ui/v2/src/composables/usePersistedSimulatorPrefs.test.ts
B4	simulator-ui/v2/src/composables/usePhysicsManager.test.ts
B4	simulator-ui/v2/src/composables/usePicking.test.ts
B4	simulator-ui/v2/src/composables/usePinning.test.ts
B4	simulator-ui/v2/src/composables/useReducedMotionPreference.test.ts
B4	simulator-ui/v2/src/composables/useRenderLoop.test.ts
B4	simulator-ui/v2/src/composables/useSceneState.test.ts
B4	simulator-ui/v2/src/composables/useSelectedNodeEdgeStats.test.ts
B4	simulator-ui/v2/src/composables/useSimulatorApp.auto-bootstrap-errors.test.ts
B4	simulator-ui/v2/src/composables/useSimulatorApp.windowManagementStep0.test.ts
B4	simulator-ui/v2/src/composables/useSimulatorRealMode.test.ts
B4	simulator-ui/v2/src/composables/useSimulatorStorage.test.ts
B4	simulator-ui/v2/src/composables/useSnapshotIndex.test.ts
B4	simulator-ui/v2/src/composables/useSystemBalance.test.ts
B4	simulator-ui/v2/src/composables/useViewControls.test.ts
B4	simulator-ui/v2/src/composables/useWmEdgeDetail.test.ts
B4	simulator-ui/v2/src/composables/windowManager/geometry.test.ts
B4	simulator-ui/v2/src/composables/windowManager/interactWindowOfPhase.test.ts
B4	simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts
B4	simulator-ui/v2/src/config/fxConfig.test.ts
B4	simulator-ui/v2/src/demo/patches.test.ts
B4	simulator-ui/v2/src/demo/timerRegistry.test.ts
B4	simulator-ui/v2/src/dev/geoSimDevHook.test.ts
B4	simulator-ui/v2/src/fixtures.test.ts
B4	simulator-ui/v2/src/layout/forceLayout.test.ts
B4	simulator-ui/v2/src/layout/physicsD3.microJitter.test.ts
B4	simulator-ui/v2/src/layout/physicsD3.viewportRetune.test.ts
B4	simulator-ui/v2/src/legacyReference/__snapshots__/legacyWindowsMarkupSnapshots.test.ts.snap
B4	simulator-ui/v2/src/legacyReference/legacyWindowsMarkupSnapshots.test.ts
B4	simulator-ui/v2/src/render/baseGraph.test.ts
B4	simulator-ui/v2/src/render/fxRenderer.cache.test.ts
B4	simulator-ui/v2/src/render/glowSprites.test.ts
B4	simulator-ui/v2/src/render/gradientCache.test.ts
B4	simulator-ui/v2/src/render/linkGeometry.test.ts
B4	simulator-ui/v2/src/render/nodeSizing.test.ts
B4	simulator-ui/v2/src/render/roundedRect.test.ts
B4	simulator-ui/v2/src/ui-kit/designSystem.overlays.test.ts
B4	simulator-ui/v2/src/ui-kit/overlayGeometry.test.ts
B4	simulator-ui/v2/src/ui-kit/overlaySurfaceCatalog.test.ts
B4	simulator-ui/v2/src/utils/clearingAmountAnchor.test.ts
B4	simulator-ui/v2/src/utils/isZeroDecimalString.test.ts
B4	simulator-ui/v2/src/utils/math.test.ts
B4	simulator-ui/v2/src/utils/numberFormat.test.ts
B4	simulator-ui/v2/src/utils/overlayPosition.test.ts
B4	simulator-ui/v2/src/utils/retryUntilTruthy.test.ts
B4	simulator-ui/v2/src/utils/runErrorClassification.test.ts
B4	simulator-ui/v2/src/utils/txAmountLabel.test.ts
B4	simulator-ui/v2/src/utils/txDirection.test.ts
B5	simulator-ui/v2/e2e/console-guard.spec.ts
B5	simulator-ui/v2/e2e/hud-qa-postfix.spec.ts
B5	simulator-ui/v2/e2e/manual-operations-interact.spec.ts
B5	simulator-ui/v2/e2e/phase5-functional-smoke.spec.ts
B5	simulator-ui/v2/e2e/playwright.phase5.config.ts
B5	simulator-ui/v2/e2e/scenario-switch-real-mode.spec.ts
B5	simulator-ui/v2/e2e/scenes.spec.ts
B5	simulator-ui/v2/e2e/scenes.spec.ts-snapshots/scene-A-win32.png
B5	simulator-ui/v2/e2e/scenes.spec.ts-snapshots/scene-B-win32.png
B5	simulator-ui/v2/e2e/scenes.spec.ts-snapshots/scene-C-win32.png
B5	simulator-ui/v2/e2e/scenes.spec.ts-snapshots/scene-D-tx-win32.png
B5	simulator-ui/v2/e2e/scenes.spec.ts-snapshots/scene-E-clearing-win32.png
B5	simulator-ui/v2/e2e/stale-runid-first-load.spec.ts
B5	simulator-ui/v2/e2e/tsconfig.json
B5	simulator-ui/v2/playwright.config.ts
B5	simulator-ui/v2/playwright.hud-qa.config.ts
C1	admin-ui/src/api/adminContracts.ts
C1	admin-ui/src/api/apiMode.ts
C1	admin-ui/src/api/envelope.ts
C1	admin-ui/src/api/errorFormat.ts
C1	admin-ui/src/api/errorToast.ts
C1	admin-ui/src/api/fixtures.ts
C1	admin-ui/src/api/index.ts
C1	admin-ui/src/api/mockApi.ts
C1	admin-ui/src/api/realApi.ts
C1	admin-ui/src/api/statusMapping.ts
C1	admin-ui/src/constants/graph.ts
C1	admin-ui/src/constants/timing.ts
C1	admin-ui/src/stores/auth.ts
C1	admin-ui/src/stores/config.ts
C1	admin-ui/src/stores/health.ts
C1	admin-ui/src/types/cytoscape-fcose.d.ts
C1	admin-ui/src/types/domain.ts
C2	admin-ui/src/advice/operatorAdvice.ts
C2	admin-ui/src/composables/useGraphAnalytics.ts
C2	admin-ui/src/composables/useGraphVisualization.ts
C2	admin-ui/src/pages/graph/GraphAnalyticsDrawer.vue
C2	admin-ui/src/pages/graph/GraphFiltersToolbar.vue
C2	admin-ui/src/pages/graph/GraphKeyboardNavigator.vue
C2	admin-ui/src/pages/graph/GraphLegend.vue
C2	admin-ui/src/pages/graph/GraphSearchBar.vue
C2	admin-ui/src/pages/graph/graphAnalyticsToggles.ts
C2	admin-ui/src/pages/graph/graphDevHooks.ts
C2	admin-ui/src/pages/graph/graphPageHelpers.ts
C2	admin-ui/src/pages/graph/graphTypes.ts
C2	admin-ui/src/pages/graph/graphUiOptions.ts
C2	admin-ui/src/pages/graph/tabs/BalanceTab.vue
C2	admin-ui/src/pages/graph/tabs/ConnectionsTab.vue
C2	admin-ui/src/pages/graph/tabs/CounterpartiesTab.vue
C2	admin-ui/src/pages/graph/tabs/CyclesTab.vue
C2	admin-ui/src/pages/graph/tabs/RiskTab.vue
C2	admin-ui/src/pages/graph/tabs/SummaryTab.vue
C2	admin-ui/src/pages/graph/useGraphConnections.ts
C2	admin-ui/src/pages/graph/useGraphFocusMode.ts
C2	admin-ui/src/pages/graph/useGraphPageOptions.ts
C2	admin-ui/src/pages/graph/useGraphPageStorage.ts
C2	admin-ui/src/pages/graph/useGraphPageWatchers.ts
C3	admin-ui/src/App.vue
C3	admin-ui/src/composables/useGraphData.ts
C3	admin-ui/src/composables/useLatestRequest.ts
C3	admin-ui/src/composables/useRouteHydrationGuard.ts
C3	admin-ui/src/content/tooltips.ts
C3	admin-ui/src/env.d.ts
C3	admin-ui/src/i18n/en.ts
C3	admin-ui/src/i18n/index.ts
C3	admin-ui/src/i18n/labels.ts
C3	admin-ui/src/i18n/ru.ts
C3	admin-ui/src/layout/AppShell.vue
C3	admin-ui/src/main.ts
C3	admin-ui/src/pages/AuditLogPage.vue
C3	admin-ui/src/pages/ConfigPage.vue
C3	admin-ui/src/pages/DashboardPage.vue
C3	admin-ui/src/pages/EquivalentsPage.vue
C3	admin-ui/src/pages/FeatureFlagsPage.vue
C3	admin-ui/src/pages/GraphPage.vue
C3	admin-ui/src/pages/IncidentsPage.vue
C3	admin-ui/src/pages/IntegrityPage.vue
C3	admin-ui/src/pages/LiquidityPage.vue
C3	admin-ui/src/pages/ParticipantsPage.vue
C3	admin-ui/src/pages/TrustlinesPage.vue
C3	admin-ui/src/router/index.ts
C3	admin-ui/src/router/query.ts
C3	admin-ui/src/style.css
C3	admin-ui/src/ui/CopyIconButton.vue
C3	admin-ui/src/ui/GraphAnalyticsTogglesCard.vue
C3	admin-ui/src/ui/LoadErrorAlert.vue
C3	admin-ui/src/ui/OperatorAdvicePanel.vue
C3	admin-ui/src/ui/TableCellEllipsis.vue
C3	admin-ui/src/ui/TooltipLabel.vue
C3	admin-ui/src/ui/participantStatus.ts
C3	admin-ui/src/utils/copyToClipboard.ts
C3	admin-ui/src/utils/cycleMapping.ts
C3	admin-ui/src/utils/datetime.ts
C3	admin-ui/src/utils/debounce.ts
C3	admin-ui/src/utils/decimal.ts
C3	admin-ui/src/utils/throttle.ts
C4	admin-ui/src/api/adminConfigFeatureFlags.contract.test.ts
C4	admin-ui/src/api/adminMutationIntegrity.contract.test.ts
C4	admin-ui/src/api/api.contract.test.ts
C4	admin-ui/src/api/apiMode.test.ts
C4	admin-ui/src/api/errorToast.test.ts
C4	admin-ui/src/api/mockApi.adminMutations.test.ts
C4	admin-ui/src/api/mockApi.listEndpoints.test.ts
C4	admin-ui/src/api/mockApi.loadJson.test.ts
C4	admin-ui/src/api/mockApi.participantMetrics.test.ts
C4	admin-ui/src/api/realApi.adminToken.test.ts
C4	admin-ui/src/api/realApi.buildQuery.test.ts
C4	admin-ui/src/api/realApi.freezeUnfreeze.test.ts
C4	admin-ui/src/api/realApi.listContracts.test.ts
C4	admin-ui/src/api/realApi.patchFeatureFlags.concurrency.test.ts
C4	admin-ui/src/api/realApi.requestJson.test.ts
C4	admin-ui/src/api/statusMapping.test.ts
C4	admin-ui/src/composables/useGraphAnalytics.test.ts
C4	admin-ui/src/composables/useGraphData.test.ts
C4	admin-ui/src/composables/useGraphVisualization.test.ts
C4	admin-ui/src/composables/useLatestRequest.test.ts
C4	admin-ui/src/i18n/i18n.hardcodedUiStrings.test.ts
C4	admin-ui/src/i18n/i18n.statsPunctuation.test.ts
C4	admin-ui/src/pages/adminAsyncOwnership.test.ts
C4	admin-ui/src/pages/graph/GraphAnalyticsDrawer.test.ts
C4	admin-ui/src/pages/graph/GraphKeyboardNavigator.test.ts
C4	admin-ui/src/pages/graph/graphPageHelpers.test.ts
C4	admin-ui/src/pages/graph/useGraphPageWatchers.test.ts
C4	admin-ui/src/pages/phase4OneShotOperator.test.ts
C4	admin-ui/src/test/setup.test.ts
C4	admin-ui/src/test/setup.ts
C4	admin-ui/src/utils/copyToClipboard.test.ts
C4	admin-ui/src/utils/cycleMapping.test.ts
C4	admin-ui/src/utils/decimal.test.ts
C4	admin-ui/src/utils/throttle.test.ts
C5	admin-ui/e2e-real/phase4-admin-real-contract.spec.ts
C5	admin-ui/e2e/graph.spec.ts
C5	admin-ui/e2e/participants.spec.ts
C5	admin-ui/e2e/phase4-operator.spec.ts
C5	admin-ui/playwright.config.ts
C5	admin-ui/playwright.phase4-real.config.ts
```
