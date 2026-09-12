# T1525 part 1 - baseline before any fix (measured 2026-09-11/12)

Tree: HEAD `1530878` (docs-only commit on top of `7de0a7c`; `git diff --stat 7de0a7c 1530878 -- app tests scripts pytest.ini` is empty),
plus two untracked files of this task:
`tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py` (6 red + 2 green controls) and
`tests/integration/test_p015_t1525_control_postgres.py` (postgres marker, deselected on the default tier).
Neighbour state left untouched: ` M specs/README.md`, `?? specs/016-duplicate-policy-owners/`.
Versions: SQLAlchemy 2.0.25, aiosqlite 0.20.0, sqlite 3.45.1, Python 3.11.9, asyncpg 0.29.0.
One measurement at a time; no python process of another session was running at the start of 3a/3b.

## 3a. Detector over the full default tier

Command (canonical selection of `scripts/verify_local.ps1` default branch, plus the plugin):

The detector is `scripts/t1525_savepoint_detector.py` (moved into the repository on 2026-09-12 - it
had lived in a scratch directory, so this measurement could not be re-derived from a clone). `scripts`
is importable from the repository root, so the plugin is loaded by module path with no PYTHONPATH:

```
T1525_DETECTOR_OUT=.local-run/test-runs/t1525-baseline/detector/detector.json \
GEO_TEST_ARTIFACT_ROOT=.local-run/test-runs/t1525-baseline/detector/artifacts \
TEST_DATABASE_URL=sqlite+aiosqlite:///./.local-run/test-runs/t1525-baseline/test.db \
.venv/Scripts/python.exe -m pytest -p scripts.t1525_savepoint_detector \
  --basetemp .local-run/test-runs/t1525-baseline/detector/pytest \
  -o cache_dir=.local-run/test-runs/t1525-baseline/detector/cache -q -m "not slow and not postgres"
```

(The command as ORIGINALLY RUN used `PYTHONPATH=<scratchpad>/t1525_detector` and
`-p t1525_savepoint_detector`, with the JSON written into the scratchpad. Only the plugin's location
changed; the plugin's logic, the selection and the environment are the same, which is why the numbers
below are reported unchanged rather than re-measured.)

(The test DB has to be `.local-run/test-runs/<task>/test.db` exactly - the conftest URL guard rejects a
deeper path; the file was deleted before 3a and again before 3b.)

Result: `7 failed, 1990 passed, 2 skipped, 147 deselected in 643.67s` (wall 651 s), exit 1.
The 7th failure was NOT the defect: `tests/unit/test_postgres_test_taxonomy.py::test_postgres_module_suffix_is_the_marker_owned_taxonomy`
- the PostgreSQL control was first named `test_p015_t1525_postgres_control.py`, which the taxonomy guard
rejects (postgres-marked modules must end `_postgres.py`). It was renamed to
`test_p015_t1525_control_postgres.py` after 3a and before 3b. The guard is a static AST check with no
database access, so the detector data below is unaffected.

Detector totals (non-vacuity: 35 529 SQLite statements and 195 SAVEPOINTs observed over 1 999 tests):

| counter | value |
|---|---|
| tests seen | 1999 |
| SQLite statements | 35529 |
| SAVEPOINT total | 195 (in 76 tests) |
| independent SAVEPOINT (in_transaction False) | 113 (in 63 tests) |
| durable RELEASE (in_transaction False after release) | 103 |
| released then root ROLLBACK | 9 (in 9 tests) |
| savepoints with unreadable state | 0 |

Innermost repo frame of the independent SAVEPOINTs (the SAVEPOINT is emitted lazily, at the first
statement inside the nested block, so the frame is that first statement; the owning `begin_nested()` is
one or two frames up):

| count | first statement | owning savepoint |
|---|---|---|
| 71 | `app/core/payments/engine.py:1642 _get_debt` | `_apply_flow` `begin_nested()` engine.py:1429 (called from commit `_uow` :1259) |
| 15 | `app/core/payments/service.py:529 _create_payment_impl` | caller's savepoint around `create_payment_internal_staged` (executor :384 in app; test `begin_nested` in tests) |
| 9 | `app/core/payments/service.py:552 _create_payment_impl` | same |
| 13 | `app/core/simulator/storage.py:497 _write` | `write_tick_metrics` savepoint storage.py:526 |
| 1 | `app/core/simulator/storage.py:671 write_tick_bottlenecks` | storage.py:668 |
| 2 | `app/core/payments/engine.py:585 _get_tx` | `_run_uow_with_retry(use_savepoint=True)` engine.py:511 (staged prepare/abort called with no outer savepoint - tests only) |
| 2 | the two new minimal reproducers | - |

### (ii) independent savepoint released, then root ROLLBACK on the same connection - 9 tests

| test | savepoint site | rollback caller |
|---|---|---|
| `tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py::test_a_root_rollback_undoes_a_savepoint_opened_before_any_write[orm]` | test :217 | test :218 (`s.rollback()`) |
| `...::test_a_root_rollback_undoes_a_savepoint_opened_before_any_write[core]` | test :236 | test :239 (`root.rollback()`) |
| `...::test_an_aborted_payment_commit_leaves_debts_unchanged` | engine.py:1433 `_apply_flow` | engine.py:1297 (`session.rollback()` in the invariant handler) |
| `...::test_an_aborted_service_payment_leaves_debts_unchanged` | engine.py:1433 `_apply_flow` | engine.py:1297 |
| `...::test_a_rolled_back_tick_leaves_no_payment_from_the_executor` | service.py:529 <- executor.py:386 | `resolve_rollback_under_cancellation` (no repo frame: runs in a task) |
| `...::test_a_real_tick_failing_after_payments_leaves_no_payment` | service.py:529 <- executor.py:386 (x2 durable) | orchestrator rollback (no repo frame: task) |
| `tests/unit/test_payment_staged_post_commit.py::test_staged_payment_effects_apply_once_after_outer_commit` | service.py:552 via test :185 | no repo frame (session close at teardown with the retry's transaction open) - the test asserts nothing about that rollback |
| `tests/unit/test_simulator_metrics_bottlenecks_real_mode.py::test_synthetic_bottlenecks_get_writes_nothing_end_to_end` | storage.py:671 via test :1153 | no repo frame (close at teardown) - the test asserts the row IS written; no undo expected |
| `tests/unit/test_simulator_write_tick_metrics_upsert.py::test_failed_delegated_commit_leaves_the_session_usable` | storage.py:497 <- :528 via test :330 | storage.py:551 `session.rollback()` after the patched failing commit - the metric rows released at :529 survive that rollback; the test asserts only that the session stays usable |

No existing (pre-T1525) test was found that asserts an undo which the defect silently voids; the three
pre-existing (ii) hits have the shape but no expectation resting on the rollback.

### (i) independent savepoints without a following rollback - 54 further tests

tests/integration/test_admin_feature_flags_multipath.py::test_feature_flag_multipath_enabled_gates_multi_route_payment (4)
tests/integration/test_admin_routing_max_paths.py::test_routing_max_paths_limits_multipath_payment (6)
tests/integration/test_daily_limit_not_enforced.py::test_daily_limit_is_informational_only (1)
tests/integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py - 8 tests (2 each): test_admin_audit_log_declares_no_money_and_leaks_none, test_admin_bottlenecks_money_is_decimal_text_and_threshold_is_a_number, test_admin_liquidity_summary_money_is_decimal_text, test_admin_participant_metrics_money_is_decimal_text, test_admin_ratio_fields_are_json_numbers_not_strings, test_admin_trustlines_list_money_is_decimal_text, test_one_threshold_parameter_comes_back_as_a_number_twice_and_a_string_once, test_trustline_updated_at_reaches_the_wire_on_every_admin_route_that_serves_one
tests/integration/test_p011_money_is_a_decimal_string_on_the_wire.py - 6 tests (1 each): test_payment_by_tx_id_money_is_decimal_text, test_payment_create_money_is_decimal_text, test_payment_list_money_is_decimal_text, test_trustline_by_id_money_is_decimal_text, test_trustline_create_money_is_decimal_text, test_trustline_list_money_is_decimal_text
tests/integration/test_p012_t1201_money_door_at_the_entrances.py::test_a_payment_amount_with_trailing_zeros_commits_and_is_not_renormalised (1)
tests/integration/test_payment_prepare_capacity_policy.py::test_multipath_prepare_keeps_local_reservations_in_addition_to_persisted_ones (1, not released)
tests/integration/test_payment_prepare_capacity_policy.py::test_single_and_multipath_prepare_apply_the_same_persisted_reservation_policy (1, not released)
tests/integration/test_payment_prepare_error_taxonomy.py::test_staged_prepare_cancellation_aborts_before_outer_rollback (1, not released)
tests/integration/test_payment_prepare_error_taxonomy.py::test_staged_timeout_abort_failure_has_symmetric_safe_log (1, not released)
tests/integration/test_payments_constraints_avoid.py::test_payment_routing_constraints_avoid_filters_intermediate_pid (3)
tests/integration/test_payments_idempotency.py::test_payments_tx_id_returns_same_result (1)
tests/integration/test_payments_idempotency.py::test_payments_tx_id_reuse_with_different_payload_conflicts (1)
tests/integration/test_payments_list_filters.py::test_list_payments_filters (3)
tests/integration/test_payments_multipath.py::test_payment_multipath_split_two_routes (4)
tests/integration/test_post_tick_audit_drift_runner_integration.py::test_post_tick_audit_drift_emits_sse_and_persists_integrity_log (3)
tests/integration/test_scenarios.py::test_clearing (3), ::test_direct_payment (1), ::test_multihop_payment (2), ::test_multipath_payment (4)
tests/integration/test_simulator_adaptive_clearing_integration.py::test_static_policy_unchanged_with_adaptive_code_present (5, 4 durable)
tests/integration/test_simulator_clearing_no_deadlock.py::test_clearing_does_not_deadlock_on_sqlite (3)
tests/integration/test_simulator_scenario_upload_validation.py::test_scenario_upload_rejects_noncanonical_equivalent_before_storage[default-source_payload1-baseEquivalent] (1)
tests/integration/test_simulator_sse_real_smoke.py::test_simulator_run_events_sse_real_mode_has_run_status_and_tx_updated (3, 2 durable)
tests/integration/test_simulator_sse_trust_drift_decay_topology_patch.py::test_simulator_sse_trust_drift_decay_emits_edge_patch_not_empty_topology_changed (2)
tests/integration/test_trustline_negative_constraints.py::test_trustline_close_rejects_non_zero_debt (1), ::test_trustline_update_rejects_limit_below_used (1)
tests/unit/test_apply_flow_retry_on_stale.py::test_apply_flow_retries_on_stale_data (1, not released)
tests/unit/test_invariants.py::test_payment_commit_writes_integrity_audit_log_on_success (1)
tests/unit/test_p1_payment_run_perimeter.py - 4 tests: test_a_direct_edge_inside_the_perimeter_still_pays (1), test_a_reused_key_for_a_different_request_stays_a_conflict (2), test_a_run_that_contains_the_hop_still_pays_through_it (2), test_an_idempotent_replay_does_not_hand_back_a_foreign_route (2)
tests/unit/test_payment_staged_post_commit.py::test_committed_payment_result_does_not_read_expired_participants (1), ::test_staged_payment_cancellation_rolls_back_without_effects (1, not released - ROLLBACK TO works), ::test_staged_payment_rollback_has_no_rows_or_effects (1, not released - ROLLBACK TO works)
tests/unit/test_payments_2pc.py::test_commit_updates_transaction_updated_at (1)
tests/unit/test_simulator_metrics_bottlenecks_real_mode.py::test_missing_measurement_stays_null_end_to_end (2), ::test_writer_and_reader_agree_on_the_key_set_end_to_end (1)
tests/unit/test_simulator_write_tick_metrics_upsert.py::test_no_money_measurement_means_no_precision_warning (1), ::test_sqlite_money_metrics_are_lossy_and_say_so_once_per_run (3), ::test_write_tick_metrics_bulk_upsert_updates_without_duplicates (2)

Full per-node JSON and the run log were scratch artifacts of the original run and are not preserved
in the repository. To regenerate the JSON on any tree, run the command above - it writes
`nodes_with_independent_savepoints`, `nodes_with_released_then_rollback` and the per-node breakdown
to `$T1525_DETECTOR_OUT`. On a tree BEFORE the fix (`git worktree add` at `1530878`) it reproduces
the counters in this section; on a tree after it, the zeros of part 2.

What the detector does not see: a rollback that bypasses SQLAlchemy's `Connection` (raw DBAPI, pool reset
after the SQLAlchemy transaction ended); non-SQLite backends; work of background tasks is attributed to
the test running at that moment. "Released then rollback" counts close-with-open-transaction as a
rollback (SQLAlchemy dispatches the same event). It flags the shape, not harm.

## 3b. Canonical gate, no detector

`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_local.ps1 -TaskSlug t1525-baseline -BackendOnly`
(`-ExecutionPolicy Bypass` added: without it this machine refuses to load the script - "execution of
scripts is disabled on this system", gate exit 1 in 1 s, no test run. Process scope only.)

- Result: `6 failed, 1991 passed, 2 skipped, 147 deselected, 4 warnings in 623.92s (0:10:23)`; gate wall 632 s; gate exit 1.
- The 6 failures are exactly the 6 red T1525 reproducers; nothing else fails. The 1991 passed include this
  file's 2 green controls, so without the file the tier is 1989 passed, 0 failed.
- `database is locked`: 0; `cannot start a transaction within a transaction`: 0; `SQLITE_BUSY`: 0 - in the
  gate output (also 0 in the 3a log, 0 files under `artifacts/` which is empty, 0 under basetemp).
  LIMIT OF THIS COUNT: the gate runs `-q`, which prints captured logs only for failing tests, so a lock
  retry logged by a passing test is not visible here. "0" means "not in the gate's output", not "never
  happened".

## 3c. Five multi-session SQLite modules x 10

Each repetition: fresh `.local-run/test-runs/t1525-multisession/` (deleted), own TEST_DATABASE_URL,
canonical basetemp/cache layout. `test_simulator_adaptive_clearing_effectiveness_ab.py` is
`@pytest.mark.slow` (:303), so the default marker deselects it; it was run with the `-IncludeExpensive`
expression `not postgres`.

| module | marker | pass/fail per rep 1..10 | tests | pytest time range | "database is locked" |
|---|---|---|---|---|---|
| test_audit_drift_delta_check_sse_integration | default | P P P P P P P P P P | 1 | 1.22-1.70 s | 0 |
| test_post_tick_audit_drift_runner_integration | default | P P P P P P P P P P | 1 | 1.81-2.16 s | 0 |
| test_simulator_adaptive_clearing_effectiveness_ab | not postgres | P P P P P P P P P P | 1 | 46.96-49.86 s | 0 |
| test_simulator_adaptive_clearing_integration | default | P P P P P P P P P P | 6 | 8.69-10.33 s | 0 |
| test_simulator_clearing_no_deadlock | default | P P P P P P P P P P | 1 | 2.03-2.37 s | 0 |

50/50 repetitions passed, exit 0 each. Same `-q` visibility limit as 3b. The per-rep logs were scratch
artifacts and are not preserved; the loop is ten repetitions of each module under the canonical
runner with its own `-TaskSlug`.

These five modules build their own engines (`create_async_engine(..., connect_args={"timeout": 5|10})`)
with no journal-mode pragma, so at the time of this measurement they ran in SQLite's default
rollback-journal mode, not WAL (read from code, not measured).

**Superseded 2026-09-12.** All five now receive the application's connect-time pragmas through
`tests/scratch_db.install_test_sqlite_pragmas` (WAL, `foreign_keys=ON`, `busy_timeout`), and each
module carries a test asserting its own engine reports `journal_mode=wal` and `foreign_keys=1`. The
50/50 result above was therefore measured under the rollback journal and does NOT transfer to the
WAL behaviour these modules now run in - it is kept as the record of that run, not as evidence about
the current tree.

## 3d. PRAGMA journal_mode on the default test DB during a test

Probe plugin (a scratch autouse fixture after `db_session`, not preserved in the repository),
fresh DB file (absent at session start), 8 tests of `tests/unit/test_payment_delta_check.py` and
`tests/unit/test_payments_2pc.py`: in every test `journal_mode = wal` through the fixture session, through a
fresh `TestingSessionLocal()` session and through a raw `sqlite3` connection; the `-wal` file exists.
Replaying the conftest statement in its own shape (`engine.begin()` -> `PRAGMA busy_timeout` ->
`PRAGMA journal_mode = WAL`) without swallowing returns `wal` and raises nothing. The file was absent at
session start and nothing else on the test engine sets WAL, so the conftest reset (tests/conftest.py:247-253)
is what turns it on - it works because pysqlite emits no `BEGIN` before a PRAGMA, i.e. by the same
legacy mode that causes T1525. After 3b the gate's own `t1525-baseline/test.db` also reads `wal`.

---

# T1525 part 2 - AFTER the fix (measured 2026-09-12)

Tree: HEAD `1530878` (unchanged) plus the T1525 working-tree changes; nothing committed. Fix =
`app/db/sqlite_transaction_control.py` (`isolation_level = None` on connect + `BEGIN` on SQLAlchemy's
`begin`), installed on every SQLite engine; the conftest WAL / busy_timeout / foreign_keys pragmas
moved from the per-test reset into the connect listener. Same machine, same commands, one run at a
time.

## Side by side

| measurement | before | after |
|---|---|---|
| default tier (canonical gate, `-BackendOnly`) | 6 failed / 1991 passed / 2 skipped / 147 deselected, 623.9 s (wall 632 s) | **2 failed** / 2009 passed / 2 skipped / 147 deselected, 596.1 s (wall 604 s) |
| the 6 T1525 reproducers | 6 red | 6 green |
| the 2 positive controls | green | green |
| PostgreSQL control file | 4 passed | 4 passed |
| `database is locked` in gate output | 0 | **2** (both from one regressed test, below) |
| `cannot start a transaction within a transaction` | 0 | 0 |
| `SQLITE_BUSY` | 0 | 0 |
| detector: savepoints total / independent / durable releases / released-then-rollback | 195 / **113** / **103** / **9** | 201 / **0** / **0** / **0** |
| detector: tests with any savepoint / with independent ones | 76 / 63 | 76 / **0** |
| detector: SQLite statements seen (non-vacuity) | 35 529 over 1 999 tests | 43 394 over 2 013 tests |
| five multi-session modules, 10 reps each | 50/50 passed | 50/50 passed, and faster (audit-drift 1.2-1.7 s -> 0.20-0.24 s; A/B 47.0-49.9 s -> 37.9-38.8 s) |
| SQLite-heavy subset (75 modules, `-rA`, logs of passing tests visible) | 445 passed, 193 s | 2 failed / 443 passed, **89 s** |
| realistic simulator run, 2 x 180 s | 91/91 ticks, 244/254 payments committed, 22/25 clearings, 0 lock strings, 0 tick failures | 93/90 ticks, 251/276 payments, 19/26 clearings, 0 lock strings, 0 tick failures |
| canonical PostgreSQL gate (`tests/integration`, marker `postgres`) | 139 passed / 329 deselected (recorded baseline) | 143 passed / 329 deselected, exit 0, 120.9 s (the +4 are this task's control file) |

Commands, exactly as run:

* detector after: same command as 3a with `TEST_DATABASE_URL=sqlite+aiosqlite:///./.local-run/test-runs/t1525-after/test.db`
  and basetemp/cache under `.local-run/test-runs/t1525-after/detector/`;
* gate after: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_local.ps1 -TaskSlug t1525-after -BackendOnly`;
* non-quiet subset: `python -m pytest --basetemp .local-run/test-runs/t1525-subset-<side>/pytest -o cache_dir=... -rA -m "not slow and not postgres" <75 modules>`
  where the list is every `tests/{unit,integration}` module matching simulator / clearing / payment(s) /
  scenarios / debt-optimistic-lock, minus the postgres-marked ones and minus this task's own files.
  The "before" side ran in a detached `git worktree` at `1530878` (its own `.local-run`), so the two
  sides differ only by the fix;
* five modules x 10: the same loop as 3c;
* simulator: a scratch driver script (not preserved in the repository) exercising the real in-process runtime
  (`runtime.create_run(mode="real", intensity 100)`, heartbeat ticks, background clearing every 5
  ticks, `storage.upsert_run`, plus a UI-like reader polling metrics and bottlenecks twice a second)
  against a scratch database under `.local-run/test-runs/t1525-simrun-<label>/`. The "before" pair ran
  on the pre-fix tree before any source change;
* PostgreSQL gate: `verify_local.ps1 -TaskSlug t1525-after-pg -BackendOnly -BackendMarker postgres -BackendSelector tests/integration`
  with `TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_ci`,
  `GEO_TEST_USE_MIGRATED_SCHEMA=1`, `GEO_TEST_ALLOW_DB_RESET=1`.

## Detector after the fix

The detector JSON of that run (regenerate with the 3a command on a fixed tree):
`nodes_with_independent_savepoints: []` and
`nodes_with_released_then_rollback: []`. Every one of the 201 savepoints ran inside a database
transaction. The scan is not empty: 43 394 SQLite statements, and the same 76 tests that opened
savepoints before still open them.

## The two regressions (not papered over; both reproduce 3 times out of 3 in isolation)

### 1. `tests/unit/test_debt_optimistic_lock.py::test_debt_optimistic_locking_raises_on_stale_update`

Statement sequence, traced per connection:

```
c2: BEGIN ; SELECT debts ...            (session s1 takes a read snapshot)
c3: BEGIN ; SELECT debts ...            (session s2 takes its own read snapshot)
c2: UPDATE debts SET amount=?, version=? WHERE id=? AND version=? ; COMMIT
c3: UPDATE debts SET ...                -> sqlite3.OperationalError: database is locked
c3: ROLLBACK
```

Diagnosis: WAL plus a real read transaction equals SQLITE_BUSY_SNAPSHOT. A transaction that read at
snapshot N cannot be upgraded to a writer once N+1 is committed; SQLite fails it immediately and
`busy_timeout` cannot help, because waiting cannot make an old snapshot current. Before the fix s2's
SELECT ran in autocommit, so its UPDATE opened a fresh transaction, saw that `version` had moved and
raised `StaleDataError` - which is what the test asserts. The lost update is still prevented; the
error class changed.

It reaches the application: `PaymentEngine._apply_flow` retries `StaleDataError`
(`app/core/payments/engine.py:1492`) and `_is_retryable_db_error` returns False for any
non-PostgreSQL bind (`:417`), so on SQLite a concurrent debt write now surfaces as a payment error
instead of a retry. It did NOT occur in the realistic simulator run (0 occurrences in 2 x 180 s,
183 ticks, 527 payments, 45 clearings): the tick serialises its own database work and awaits the
background clearing.

Options, none applied, the choice is the owner's: accept the SQLite error class in the test; extend
the retry predicate to SQLITE_BUSY_SNAPSHOT on SQLite; or `BEGIN IMMEDIATE` (serialises every
transaction, reads included - deliberately not chosen here).

### 2. `tests/integration/test_simulator_real_snapshot_db_enrichment.py::test_real_mode_graph_snapshot_enriches_used_and_net_sign`

The test polls with `db_session` for a row the simulator's heartbeat writes on ANOTHER connection:

```
c1: BEGIN ; SELECT equivalents WHERE code='UAH'   (x5, no COMMIT between)
c4: BEGIN ; INSERT INTO equivalents ... ; COMMIT   (the heartbeat seeds)
c1: SELECT equivalents WHERE code='UAH'            (x3 more, still the old snapshot)
-> assert eq is not None   fails
```

Diagnosis: the poll loop runs inside one read transaction whose snapshot is fixed at its first
SELECT. SQLite has no READ COMMITTED; before the fix each poll was its own autocommit read and
therefore saw the other session's commit. What became impossible is the test's shape - polling for
another session's commit without ending your own transaction - not the product behaviour. Follow-on
effect: the test fails before stopping its run, so its heartbeat keeps ticking into the next test,
which is where the two new warnings in the gate output come from (`Event loop is closed` in an
aiosqlite thread, and `coroutine 'AsyncSession.close' was never awaited`).

Minimal honest repair, not applied: end the transaction between polls (`await db_session.rollback()`,
or a fresh session per poll).

## Mutations (bytes restored, sha256 identical before and after, `git status` unchanged)

| mutation | result |
|---|---|
| M0 (none) | 22 passed |
| M1 remove the `begin` listener | 10 failed: all 6 reproducers, BOTH positive controls and both runtime transaction checks. Without `BEGIN`, `isolation_level = None` is pure autocommit, so even a write before the savepoint is durable |
| M2 remove `isolation_level = None` | **the effect tests stayed GREEN** (22 passed). On CPython 3.11 the legacy driver begins on its own only in front of INSERT/UPDATE/DELETE/REPLACE and never while a transaction is open, so the explicit `BEGIN` alone carries the atomicity. A named SHAPE assertion was then added to the runtime test (the driver's `isolation_level is None`), and M2 now fails exactly that one test - recorded as shape, not effect |
| M3 drop the installer from `tests/conftest.py` only | 9 failed: the 6 reproducers, both runtime transaction checks, AND the source guard |
| M4 move the WAL pragma back inside `engine.begin()` | on a FRESH database: 2 failed (`test_the_default_test_database_is_in_wal`, `test_a_fresh_database_gets_wal_from_the_conftest_connect_listener`); on a REUSED database only the fresh-file test fails, because journal mode is persistent in the file. Probed directly: `PRAGMA journal_mode=WAL` inside a transaction raises `OperationalError: cannot change into wal mode from within a transaction`, and `PRAGMA foreign_keys=ON` inside a transaction is silently a no-op |

## The three existing released-then-rollback tests

All three still pass, and their meaning is now what their names claim:

* `test_staged_payment_effects_apply_once_after_outer_commit` - unchanged; the savepoint is now
  nested in the session transaction the test commits itself.
* `test_synthetic_bottlenecks_get_writes_nothing_end_to_end` - the counter-check row written with
  `commit=False` is transaction-local instead of durable; the readers share that session, so the
  assertions are unaffected.
* `test_failed_delegated_commit_leaves_the_session_usable` - the metric rows written before the
  failing delegated commit are now actually discarded by the rollback the writer performs. Before the
  fix they survived it.

---

# T1525 part 3 - the two regressions resolved (measured 2026-09-12)

Part 2's "after" numbers stand as what was measured then; these supersede them where they differ,
and the difference is named each time. Tree: HEAD `1530878`, nothing committed.

## What changed since part 2

* `app/db/sqlite_transaction_control.py` - added `sqlite_busy_error_name(exc)`: walks the exception
  chain and matches `sqlite3`'s `sqlite_errorcode` on the SQLITE_BUSY family (`code & 0xFF == 5`,
  so BUSY 5, BUSY_RECOVERY 261, BUSY_SNAPSHOT 517, BUSY_TIMEOUT 773). Code, not message: all of them
  read "database is locked". No message fallback by design.
* `app/core/payments/engine.py` - `_dialect_name()` / `_is_sqlite()`; `_is_retryable_db_error` returns
  True for the busy family on a SQLite bind (PostgreSQL classification untouched); `_run_uow_with_retry`
  propagates a busy error in savepoint mode, the SQLite twin of the existing 40001/40P01 branch; the
  retry log line now carries `sqlite_error=`.
* `app/core/payments/service.py` - `_classify_payment_db_error` maps a SQLite busy error to
  `RetryablePaymentConflictException`, so a staged payment makes the tick replay instead of recording
  a terminal internal error.
* `tests/unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py` (new, 3 tests).
* `tests/unit/test_debt_optimistic_lock.py` - rewritten: the invariant on both tiers, the mechanism
  named per backend.
* `tests/integration/test_simulator_real_snapshot_db_enrichment.py` - the poll ends its read
  transaction before each re-read; a `stopped_runs` fixture stops the run pass or fail.

## Final numbers

| measurement | before T1525 | part 2 | part 3 (final) |
|---|---|---|---|
| canonical SQLite gate | 6 failed / 1991 passed, 623.9 s | 2 failed / 2009 passed, 596.1 s | **0 failed / 2014 passed** / 2 skipped / 147 deselected, 640.4 s (wall 649 s), exit 0 |
| `database is locked` in gate output | 0 | 2 | **0** |
| `cannot start a transaction within a transaction` / `SQLITE_BUSY` | 0 / 0 | 0 / 0 | 0 / 0 |
| gate warnings | 4 | 6 | **4** (the two leaked-heartbeat warnings are gone) |
| detector: savepoints / independent / durable releases / released-then-rollback | 195 / 113 / 103 / 9 | 201 / 0 / 0 / 0 | 201 and 200 / **0** / **0** / **0** (two runs) |
| detector: statements, tests, tests with savepoints | 35 529 / 1 999 / 76 | 43 394 / 2 013 / 76 | 43 795 / 2 016 / 78 |
| canonical PostgreSQL gate | 139 passed / 329 deselected | 143 passed / 329 deselected | **143 passed / 329 deselected**, 124.2 s (wall 133 s), exit 0 |

## Mutations for the part 3 work (bytes restored, sha256 identical, `git status` unchanged)

| mutation | result |
|---|---|
| M5 revert the SQLite branch of `_is_retryable_db_error` | 3 failed in `test_p015_t1525_sqlite_stale_snapshot_is_retried.py`: the retry test, the budget test and the classifier counter-proof. `test_debt_optimistic_lock` stays green, correctly - it asserts the refusal, not the retry |
| M6 revert the poll fix (`await db_session.rollback()` -> `pass`) | `test_real_mode_graph_snapshot_enriches_used_and_net_sign` fails, exactly as before the repair |

## Retry decision sites, enumerated (AGENTS.md 16.1)

Changed, because the T1525 control created the failure they must classify:

1. `app/core/payments/engine.py:417` `_is_retryable_db_error` - the predicate `_run_uow_with_retry` consults.
2. `app/core/payments/engine.py:545-559` - the savepoint-mode branch inside `_run_uow_with_retry`.
3. `app/core/payments/service.py:85` `_classify_payment_db_error` - what the caller/tick sees.

Enumerated and deliberately NOT changed:

4. `app/core/payments/engine.py:1492` `_apply_flow`'s `except StaleDataError` - the ORM-level retry;
   still correct and still reached whenever the ORM is the one to notice.
5. `app/core/clearing/service.py:253` `_is_retryable_concurrency_error` (PostgreSQL 40001/40P01 only).
6. `app/core/simulator/real_runner_impl.py:61` `_is_transient_inject_db_error` (40001/40P01/55P03).
7. `app/core/simulator/storage.py:30` `_retry_on_locked` - already SQLite-specific, matches the
   message text, 3 attempts with backoff.

(5) and (6) mean a SQLite BUSY_SNAPSHOT in clearing or in an inject is still terminal for that
operation rather than retried. It did not occur in any measurement, and widening them is a behaviour
change beyond this task - flagged for the owner, not taken.

## The blind-poll audit of application code

No application code has the shape. Checked, with what each one actually does:

* `app/core/recovery.py:278` - new session per iteration (`async with session_factory()`).
* `app/main.py:246` `_integrity_loop` - waits on an event; the checkpoint run opens its own session.
* `app/core/clearing/service.py:280` - new session per attempt, by construction.
* `app/core/simulator/real_clearing_engine.py:164` - its own isolated session per clearing run.
* `app/api/v1/simulator.py:1891` auto-clear loop - re-reads after its OWN commits, not another
  transaction's.
* `app/core/simulator/real_runner_impl.py:557` inject loop - rolls back before its single restart, so
  the retry takes a fresh snapshot.
* `app/utils/distributed_lock.py:68` (Redis), `app/api/v1/simulator.py:2529` (SSE keep-alive),
  `app/core/payments/router.py:578`, `app/core/simulator/helpers.py:23`,
  `app/core/simulator/artifacts.py:270`, `app/api/v1/admin.py:1892` - no database session waiting on
  another transaction.

## Open item for the owner: one non-reproducing lock failure

In the FIRST of the part 3 full-tier detector runs, `test_real_mode_graph_snapshot_enriches_used_and_net_sign`
failed with `sqlite3.OperationalError: database is locked` at its own `db_session.commit()`
(`tests/integration/test_simulator_real_snapshot_db_enrichment.py:108`) - not the poll, which had
already succeeded. Sequence: the test session reads (poll, trust line, participants, existing debt),
the simulator heartbeat commits on another connection roughly once a second, and the test session
then writes - SQLITE_BUSY_SNAPSHOT, on a plain test session with no retry wrapper around it. This is
regression class 1 on a path that is not a payment unit of work.

Frequency, measured rather than guessed: 1 occurrence in 4 full-tier runs after the fix (the two
canonical gates and the second detector run are clean), and 0 in 5 isolated repetitions of that
module under the same detector plugin. The detector's per-statement tracing widens the read->write
window, which is the likeliest reason it appeared there and not in the gates.

Not repaired here, because every available repair is a decision: retry the write, end the read
transaction immediately before it, or stop the run before the write. Reported for the owner.

---

# T1525 part 4 - the enrichment flake: repair applied, requirement NOT met (measured 2026-09-12)

Decision taken from the owner: fix the test, not the application - end the read transaction
immediately before the write. Applied in
`tests/integration/test_simulator_real_snapshot_db_enrichment.py`: `await db_session.rollback()` sits
directly above the read that feeds the write, so the write shares that fresh snapshot. No retry, no
sleep, and the heartbeat is not stopped to make it pass (the `stopped_runs` fixture from part 3 stays,
as hygiene, and is not what the evidence rests on).

One implementation detail worth recording: a rollback expires every ORM instance in the session, and
in async SQLAlchemy a refresh triggered by plain attribute access raises `MissingGreenlet`. Everything
the test needs after the write (`eq_id`, `tl_limit`, `creditor_id/pid`, `debtor_id/pid`) is therefore
captured as a plain value before the rollback.

## Evidence

| requirement | result |
|---|---|
| enrichment module, 10 runs in isolation under the detector plugin | **10/10 passed, 0 `database is locked`** |
| full default tier under the plugin, 4 runs, 0 lock occurrences required | **NOT met: 3 of 4 clean, run 4 failed** |
| independent savepoints 0 in every tier run | **met: 0 in all four** |
| M7: revert the pre-write rollback, hunt the flake in up to 4 tier runs | **did not reproduce in 4 runs** |

Per tier run (detector plugin loaded, fresh database each):

| run | result | `database is locked` | independent savepoints | savepoints | statements | tests |
|---|---|---|---|---|---|---|
| 1 | 2014 passed | 0 | 0 | 201 | 43 811 | 2016 |
| 2 | 2014 passed | 0 | 0 | 204 | 43 862 | 2016 |
| 3 | 2014 passed | 0 | 0 | 201 | 43 812 | 2016 |
| 4 | **1 failed**, 2013 passed | **2** | 0 | 204 | 43 844 | 2016 |

## What run 4 says, and it is not what I hoped

Same test, one line later than before the repair:

```
tests/integration/test_simulator_real_snapshot_db_enrichment.py:125
    await db_session.commit()
  sqlite3.OperationalError: database is locked
```

Before the repair the write sat seconds after the poll's reads; now it sits milliseconds after the
`existing` SELECT that opens its snapshot. The window is much smaller but it is not closed: the
sequence is still `SELECT existing` (opens the snapshot) -> `DELETE`/`flush` -> `INSERT` -> `commit`,
and the simulator heartbeat commits on another connection roughly once a second. If a heartbeat
commit lands inside that window the write is on a stale snapshot and SQLite refuses it outright.

So the prescribed repair reduces exposure rather than removing it. The residual window is inherent to
"read something, then write it" while another writer commits continuously - only not depending on the
prior read, retrying, or stopping the writer removes it, and all three are the owner's call.

## M7 and what it can and cannot show

M7 (the pre-write rollback reverted, `pass  # M7`) ran the full tier 4 times: every run 2014 passed
with 0 `database is locked`, then bytes restored, `sha256 identical: True`. The mutation did **not**
reproduce within 4 runs, and that is the plain statement, not a red mutation.

It is also uninformative at this sample size, which matters more than the bare result: the FIXED code
failed 1 run in 4 and the MUTATED code failed 0 in 4. Four runs cannot separate those two rates. A
mutation battery is the wrong instrument for a race this rare; discriminating would need tens of runs
or a stand that forces the interleaving deterministically (hold the test between its read and its
write, commit from the heartbeat's session, then let the write proceed).

## Final gates

* SQLite, `verify_local.ps1 -TaskSlug t1525-p4-gate -BackendOnly`: **2014 passed, 2 skipped, 147
  deselected, 4 warnings**, 585.6 s (wall 593 s), exit 0. `database is locked` 0,
  `cannot start a transaction within a transaction` 0, `SQLITE_BUSY` 0.
* PostgreSQL, `-BackendMarker postgres -BackendSelector tests/integration`: **143 passed, 329
  deselected**, 120.9 s (wall 129 s), exit 0.
* `ruff check app migrations` (pinned 0.1.14): exit 0. The `for l in links` E741 in the enrichment
  test is pre-existing at HEAD (its line 85) and untouched; ruff's CI scope does not include `tests/`.

---

# T1525 part 5 - the shape removed, the inject loss mode closed (measured 2026-09-12)

## 1. The enrichment race: shape changed, not window shrunk

Part 4 moved the write as late as possible and still lost once in four tier runs, because the shape
survived: read -> write -> commit in one transaction. The setup mutation now runs in its OWN short
session whose FIRST statement is a write (`DELETE`, then the `INSERT`, then `commit`), so the
transaction takes the write lock up front and holds no read snapshot that a concurrent commit could
invalidate. If the heartbeat is mid-commit, this writer WAITS on the lock - which `busy_timeout` does
cure - instead of being refused for a stale snapshot, which no waiting can cure.

Two details that are part of the fix rather than decoration:

* the comment at the site names the property (writes open the transaction; no read precedes them in
  it) and says not to "simplify" the session back onto `db_session`, so the shape cannot be
  reintroduced by a later editor who sees a redundant-looking session;
* `await db_session.rollback()` now sits AFTER that write, not before it. The snapshot request that
  follows reads through `db_session` - the `client` fixture overrides `get_db` with it - so its
  transaction must not predate the write, or the API would serve a snapshot without the debt.

The captured scalars (`eq_id`, `tl_limit`, `creditor_id/pid`, `debtor_id/pid`) and the `stopped_runs`
fixture stay. No retry, no sleep, and the heartbeat is not stopped to make the test pass.

### Evidence: full default tier x4 under the detector plugin

| run | result | `database is locked` | independent savepoints | released-then-rollback | savepoints | statements | tests |
|---|---|---|---|---|---|---|---|
| 1 | 2016 passed, 0 failed | 0 | 0 | 0 | 201 | 44 007 | 2018 |
| 2 | 2016 passed, 0 failed | 0 | 0 | 0 | 201 | 44 010 | 2018 |
| 3 | 2016 passed, 0 failed | 0 | 0 | 0 | 205 | 44 070 | 2018 |
| 4 | 2016 passed, 0 failed | 0 | 0 | 0 | 204 | 44 058 | 2018 |

Required 0 lock occurrences across four runs: **met**, where the part 4 shape failed 1 in 4.
Independent savepoints 0 in every run: **met**.

## 2. The inject classifier: a real loss mode, closed

`_is_transient_inject_db_error` (`app/core/simulator/real_runner_impl.py`) matched PostgreSQL
SQLSTATEs only. On SQLite the inject unit of work reads its owner-lock set, stages, and then writes,
so a commit by another connection in between leaves it on a stale snapshot and the explicit flush is
refused with SQLITE_BUSY_SNAPSHOT. Classified as an ordinary database error it reopened exactly the
loss mode 55P03 was added for: the owner records "inject failed (db error)", marks the event FIRED,
and the inject is DROPPED.

The predicate now returns True for the SQLite busy family, reusing
`app.db.sqlite_transaction_control.sqlite_busy_error_name` - one rule shared with the payment engine,
matched on sqlite3's error code, no message matching and no second predicate. The comment above
`_INJECT_TRANSIENT_SQLSTATES` no longer claims "nothing else is retried" without naming this.

### Tests, with a REAL interleaving rather than a synthetic error

`tests/unit/test_p015_inject_transaction_ownership.py` gains `_CommitFromAnotherSession`: it wraps
the real `stage_inject_event` and, after staging returns (the unit of work has read and not yet
written), commits an unrelated row from a second session. The owner's own `await session.flush()`
then raises a genuine SQLITE_BUSY_SNAPSHOT - the driver raises it, nothing fabricates an error or its
code.

* `test_a_stale_snapshot_is_transient_and_the_inject_lands_exactly_once`: interleave on attempt 1 ->
  the unit of work restarts (`spy.calls == 2`), the debt is **15.12345678** exactly (10.00 onto
  5.12345678, read through a new session), the event is fired once, one "inject applied" note, no
  transaction left open. Non-vacuity: `spy.interleaved == 1`.
* `test_a_stale_snapshot_on_both_attempts_leaves_the_event_pending`: interleave on both attempts ->
  the error propagates, and it is verified BY CODE to be SQLITE_BUSY_SNAPSHOT; the event stays
  PENDING (fired set empty), no debt, no notes, no open transaction. Non-vacuity:
  `spy.interleaved == 2`.
* The neighbouring anti-vacuum test's docstring, which said "only 40001/40P01 restart the unit of
  work", now names the real transient set.

**Mutation M8**: revert the SQLite branch of the predicate (`if False:`) -> both new tests fail,
including "applied exactly once". Restored, `sha256 identical: True`.

## 3. Clearing: no code change, limitation recorded

A comment at `ClearingService._is_retryable_concurrency_error` (`app/core/clearing/service.py`) now
states that the predicate is PostgreSQL-only on purpose: a SQLite BUSY_SNAPSHOT ends that clearing
execution, which is accepted because clearing is best-effort and the simulator attempts it again on a
later tick (unlike the inject, whose owner would mark the event fired and drop it), and because it
appeared in no measurement of T1525 - two 180 s multi-session runs, four full default tiers, five
multi-session modules ten times each. It names the shared predicate for whoever needs it later.

## 4. Close-out

* SQLite gate, `verify_local.ps1 -TaskSlug t1525-p5-gate -BackendOnly`: **2016 passed, 2 skipped, 147
  deselected, 4 warnings**, 645.2 s (wall 654 s), exit 0. `database is locked` 0,
  `cannot start a transaction within a transaction` 0, `SQLITE_BUSY` 0.
* PostgreSQL gate: **143 passed, 329 deselected**, 122.0 s (wall 130 s), exit 0.
* `ruff check app migrations` (pinned 0.1.14): exit 0.
* Test count, counted rather than derived (`--collect-only` per file): the default tier held
  **1989 passed** before T1525 - the part 1 baseline line of 1991 already included this task own 2
  passing control tests - and holds **2016** now. The +27 is 25 from the four new default-tier files
  (8 reproducers and controls, 4 runtime checks, 10 source-guard items, 3 retry tests) plus 2 from
  the new inject tests (that file went 24 -> 26). The optimistic-lock test was rewritten, not added.
  The PostgreSQL tier went 139 -> 143: the 4 control tests.

## Как читать `independent_savepoints: 0` — число зависит от выборки

Записано 2026-09-12, после того как это всплыло при проверке детектора как плагина.

Заголовочное `113 -> 0` относится к **каноническому дефолтному тиру целиком**. На других выборках
ноль не обязателен, и это не регрессия: `tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py`
содержит тест `test_installing_the_control_late_does_not_repair_a_connection_that_already_read`,
который **намеренно** воспроизводит legacy-режим — соединение уже читало, контроль ставится позже, и
savepoint открывает транзакцию сам. Прогон, включающий этот тест, покажет
`independent_savepoints: 1`, и это ожидаемо.

Поэтому при перепроверке числа сверяйте **ту же выборку**, что в замере, и помните правило непустоты
из самого детектора: чистое дерево — это `independent_savepoints: 0` при **ненулевых**
`savepoints_total` и `sqlite_statements`; прогон, не наблюдавший ничего, читается одинаково с
прогоном, не нашедшим ничего плохого.
