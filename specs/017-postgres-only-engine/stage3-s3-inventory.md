# 017, stage 3, slice S3 — the SQLite test stands leave the tree (`T1705`)

- **Date:** 2026-09-24
- **Programme:** [017 — PostgreSQL as the only engine](spec.md), task `T1705`
- **Base:** `claude/017-stage3-s2sim` at `8a6c3d2` (S2a, PR #30). Branch `claude/017-stage3-s3del`.
- **Status authority:** descriptive. What this slice did is established by the commit, the collected-test
  diff and the gate lines recorded below, not by this file.
- **Scope:** `tests/` only. `app/`, `scripts/`, `migrations/` are not touched; the application's SQLite
  code is removed by S4–S7, and section 6 lists the tests that will go red with it.

## How the inventory was taken

`git grep -n -i` over `tests/` for `sqlite`, `aiosqlite`, `PRAGMA`, `create_all`, `scratch_db`,
`_sqlite.py`, `sqlite_transaction_control`, `busy`, `database is locked` (126 files before), then every
file with a hit was read at the hit. A second pass after the edits parsed every remaining hit with `ast`
and separated executable code (names, attributes, non-docstring strings, imports) from prose; section 5
is that result.

Deletion rule applied (AGENTS.md §11): a deleted test is **(a)** proving SQLite-only behaviour whose
contract disappears with SQLite, or **(b)** duplicated by a named stronger PostgreSQL test (node id
given), or **(c)** empty. "Old" was not used as a reason.

## 1. Counts

Collected with the runner's marker expression (`-m "not slow"`), `--collect-only`:

| | node ids |
|---|---|
| base `8a6c3d2` | **3026** (+4 `slow` deselected) |
| after this slice | **2949** (+4 `slow` deselected) |
| deleted | **78** |
| added (a transfer that needed a new PostgreSQL test) | **1** — `tests/unit/test_p015_p1_money_conflict_predicate.py::test_a_real_serialization_failure_is_a_money_conflict` |
| renamed, same test | **3** — the two tests of `test_sqlite_test_engine_enforces_foreign_keys.py` (file renamed to `test_the_test_engine_enforces_foreign_keys.py`) and the `through-the-helper` case of the T1406 source-guard counter-test (now `through-the-scratch-root`) |

`3026 − 78 + 1 = 2949`. The diff of node ids also shows one pair that is not a change:
`test_p012_t1201_money_door_bounds.py::test_is_storable_money_refuses_what_the_column_would_change[<object object at 0x…>]`
is parametrised by a fresh `object()` whose address differs on every collection.

Decisions per test (78 deleted): **(a) 48**, **(b) 30**, **(c) 0**. The 30 under (b) include the
`t1530` DDL test (its SQLite half is (a), its PostgreSQL half (b)) and the SQLite predicate test that was
**transferred** — replaced by the one new PostgreSQL test above. Per file, below.

## 2. Per-file decisions

### 2.1 Files deleted whole

| file | tests | decision | reason |
|---|---|---|---|
| `tests/integration/test_p015_p1_money_replay_sqlite.py` | 4 | delete — orchestrator decision | `test_a_real_busy_snapshot_replays_the_money_phase_and_commits_once` **(b, ⚑)**, see section 3; `test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget` (b) → `tests/integration/test_p015_p1_money_replay_postgres.py::test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget`; `test_the_competitors_own_change_survives_the_replay` (b) → same module `::test_a_genuine_40001_is_raised_by_the_staged_write_on_this_backend[write-skew]` (assert `:706-708`, carried in S2a); `test_the_stand_runs_in_wal_with_the_production_transaction_control` (a) — WAL and the T1525 control are the SQLite stand's preconditions |
| `tests/unit/test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py` | 1 | delete (a) | the module records that SQLite does not round `Numeric(20,8)` on write; the behaviour itself is pinned on PostgreSQL by `test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` |
| `tests/unit/test_p015_step5b_sqlite_startup_refuses_a_pre_027_schema.py` | 6 | delete (a) | drives the SQLite-only startup probes of `app/main.py` (`_sqlite_refuse_pre_027_debt_operations`) on SQLite files; probe and file format leave with S4–S7. `test_step5b_the_startup_probe_does_not_run_on_postgresql` is an absence check of that same probe and goes with it |
| `tests/unit/test_p015_t1525_a_busy_does_not_mask_and_does_not_promise.py` | 10 | delete | subject is `sqlite_busy_error_name` / `SQLITE_BUSY` classification on a SQLite stand. (a) for the seven SQLite-error classification cases; (b) for three: `test_a_failed_rollback_stops_the_retry_instead_of_re_running` → `tests/integration/test_p017_uow_retry_after_a_real_40001_postgres.py::test_a_failed_rollback_after_a_real_40001_stops_the_retry` (added in S2a for exactly this), `test_the_same_conflict_is_retried_when_the_rollback_succeeds` → same module `::test_the_same_40001_is_retried_when_the_rollback_succeeds`, `test_the_service_classifier_does_not_read_a_busy_out_of___context__` → `tests/integration/test_p015_t1525_classification_reads_deliberate_wrapping_only_postgres.py::test_a_terminal_error_inside_a_40001_handler_is_not_retryable` |
| `tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py` | 8 | delete after moving its scenarios | the scenario helpers and assertions it shared with its PostgreSQL control were **moved into** `tests/integration/test_p015_t1525_control_postgres.py` (the control imported them from here). (a) for the four minimal ORM/Core savepoint tests — "a savepoint opened before the first write is its own transaction" is pysqlite's legacy mode; (b) for four: `test_an_aborted_payment_commit_leaves_debts_unchanged` **(⚑)**, `test_an_aborted_service_payment_leaves_debts_unchanged`, `test_a_rolled_back_tick_leaves_no_payment_from_the_executor`, `test_a_real_tick_failing_after_payments_leaves_no_payment` → `test_p015_t1525_control_postgres.py::test_postgres_…` of the same name, same scenario, same assertion function, at SERIALIZABLE on a real pool |
| `tests/unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py` | 3 | delete | "a stale SQLite snapshot is a retryable conflict". (b) `test_a_payment_that_loses_the_snapshot_race_is_retried_and_commits` → `tests/integration/test_payment_engine_uow_retry_postgres.py::test_payment_engine_commit_retries_whole_uow_on_serialization_failure_postgres`; (b) `test_a_payment_that_keeps_losing_the_race_is_refused_after_a_finite_budget` → `tests/integration/test_p015_p1_money_replay_postgres.py::test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget` and the synthetic-40001 budget cases in `tests/unit/test_payments_2pc.py`; (a) `test_the_classifier_reads_the_error_code_and_refuses_everything_else` — SQLite error codes |
| `tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py` | 5 | delete (a) | asks a live SQLite connection whether `app/db/sqlite_transaction_control.py` is installed (`PRAGMA journal_mode`, `in_transaction`) |
| `tests/unit/test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py` | 2 | delete | "the SQLite tier: NaN is refused by a constraint about a different rule". (b) `test_a_the_refusal_of_a_nan_amount_must_name_the_money_rule` → `tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py::test_a_an_orm_write_of_nan_must_not_reach_the_money_column` (the message assert was carried there in S2a); (a) `test_b_a_check_constraint_on_this_tier_can_never_refuse_a_nan` — SQLite type affinity |
| `tests/unit/test_sqlite_dev_schema_repair.py` | 4 | delete (a) | imports `repair_stale_trustline_uniqueness` from `scripts/init_sqlite_db.py` and rebuilds a pre-019 SQLite table; the invariant it protects (migration 019's partial index) is held on PostgreSQL by `tests/integration/test_p1_trustline_reopen_postgres.py` |
| `tests/scratch_db.py` (helper) | — | delete | SQLite-file helpers (`scratch_db_url`, `install_test_sqlite_pragmas`, sidecar cleanup). Its last non-SQLite user, the T1406 guard, now defines `SCRATCH_ROOT` itself |

### 2.2 Tests deleted from files that stay

| file | deleted | decision | reason |
|---|---|---|---|
| `tests/integration/test_audit_drift_delta_check_sse_integration.py`, `test_post_tick_audit_drift_runner_integration.py`, `test_simulator_adaptive_clearing_effectiveness_ab.py`, `test_simulator_adaptive_clearing_integration.py`, `test_simulator_clearing_no_deadlock.py` | `test_this_modules_engine_has_the_application_sqlite_pragmas` ×5, with the SQLite engine fixture each kept only for it | delete (a) — orchestrator decision, "the five pragma tests" | they assert `PRAGMA journal_mode = wal` and `PRAGMA foreign_keys = 1` on a SQLite engine; S2a already moved every domain test of these modules to PostgreSQL |
| `tests/integration/test_equivalent_writer_and_legacy_reads.py` | `test_legacy_invalid_equivalent_rows_remain_visible_on_read_surfaces`, `test_admin_patch_rejects_invalid_legacy_code_before_mutation`, the `legacy_sqlite_session` stand and the module's `db_session` override | delete (a) — orchestrator decision | a legacy CODE row can exist only without `chk_equivalents_code_format`, i.e. on SQLite; on PostgreSQL the row is unreachable. The legacy PRECISION tests stay |
| `tests/unit/test_alembic_postgres_only.py` | `test_supported_sqlite_initializer_remains_available` | delete (a) | runs `scripts/init_sqlite_db.py` and opens the SQLite file it made; the script leaves with S4–S7 (`T1704`) |
| `tests/unit/test_p014_t1406_no_mutable_database_in_the_working_tree.py` | `test_the_scratch_helper_puts_the_file_where_the_guard_expects` | delete (a) | tests the deleted `tests/scratch_db.py` helper. The guard itself is **transferred**, see 2.3 |
| `tests/unit/test_p015_b4_entries_and_money.py` | `test_c12_a_value_this_dialect_cannot_hold_is_refused_before_any_debt_sql` ×5, `test_c12_control_a_value_this_dialect_holds_exactly_is_accepted` ×2, `test_condition4_a_delta_this_dialect_cannot_hold_is_refused_though_both_ends_fit`, `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check` ×6, `test_c19_a_shape_valid_lie_is_accepted_and_is_therefore_step_6_s_job`, the `sqlite_money_stand` and the helpers only they used | C12 `[one atom past the exact domain]` (a) — the MONEY_ROUND_TRIP refusal, orchestrator decision; C12 `[more than eight decimal places]`, `[not a number]`, `[infinite]`, `[at the magnitude ceiling]` (b) → `tests/integration/test_p015_b4_entries_and_money_postgres.py::test_c12_p_a_value_outside_the_money_domain_is_refused_before_any_debt_sql[…]` of the same label; C12 control ×2 (b) → `::test_c12_p_control_this_dialect_stores_the_whole_domain_exactly` (covers `1.000000000` and the whole domain up to `999999999999.99999999`); condition 4 (a) — the delta is unstorable only through SQLite's float binding; C19 ×6 (b) → `::test_c19_p_a_shape_invalid_forged_row_is_refused_by_the_named_rule[…]` same labels — for `an update whose endpoints are equal` PostgreSQL names `chk_debt_journal_entries_delta_arithmetic`, because on PostgreSQL that CHECK shadows the equal-endpoints clause of the shape CHECK; the forgery is refused either way; C19 lie (a) — the "shape-valid lie is accepted" boundary holds only on SQLite, and the moved boundary is `::test_c19_p_the_boundary_with_step_6_moved_on_this_tier_and_here_is_where_it_is_now` |
| `tests/unit/test_p015_b4a_journal_mechanism.py` | `test_the_round_trip_predicate_refuses_what_sqlite_would_change`, `test_an_operation_refuses_to_open_on_sqlite_without_transaction_control` | delete (a) | the first is the MONEY_ROUND_TRIP refusal (orchestrator decision; the PostgreSQL exact-storage test `test_a_value_sqlite_would_change_is_exact_money_on_postgresql` stays); the second is the journal's `NO_TRANSACTION_CONTROL` refusal on a SQLite engine |
| `tests/unit/test_p015_inject_transaction_ownership.py` | `test_a_stale_snapshot_is_transient_and_the_inject_lands_exactly_once`, `test_a_stale_snapshot_on_both_attempts_leaves_the_event_pending`, the `sqlite_stand` and `_CommitFromAnotherSession` | delete (b) | a real conflict restarting the whole inject unit of work, landing once → `tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py::test_a_real_serialization_failure_restarts_the_whole_inject_unit_of_work` (real 40001); a spent budget leaving the event pending → `tests/unit/test_p015_inject_transaction_ownership.py::test_a_second_transient_failure_propagates_and_leaves_the_event_pending[mode_b-staging]` / `[mode_b-commit]` (40001/40P01 from the driver seam, mode B) |
| `tests/unit/test_p015_p1_money_conflict_predicate.py` | `test_a_real_sqlite_busy_snapshot_is_a_money_conflict` | **transfer** | replaced by `::test_a_real_serialization_failure_is_a_money_conflict` — a genuine 40001 produced by asyncpg on a mode-B clone. The integrity-error counter-check keeps its name and now uses a real asyncpg foreign-key error (SQLSTATE `23503`). See section 4 |
| `tests/unit/test_p015_step5a_reconciliation.py` | `test_step5a_without_sqlite_transaction_control_there_is_no_verdict` | delete (a) | the refusal is about a SQLite engine's transaction control; on PostgreSQL the predicate is never asked. The arithmetic test of the same module is **kept and moved** (section 4) |
| `tests/unit/test_p015_step5c_reaction_and_hold.py` | `test_step5c_startup_adds_the_hold_column_to_an_existing_sqlite_file_once` and its three SQLite-file helpers | delete (a) | drives `app.main._sqlite_ensure_equivalents_integrity_hold_column` on SQLite files |
| `tests/unit/test_p015_t1528_the_guard_reads_what_the_statement_writes.py` | `test_t1528_the_sqlite_driver_answers_the_transaction_probe_with_a_bool` and its `sqlite_stand` | delete (b) | → `tests/integration/test_p015_t1528_the_statement_is_read_not_guessed_postgres.py::test_t1528_p_asyncpg_answers_the_transaction_probe_with_a_bool` |
| `tests/unit/test_p015_t1530_the_journal_reads_its_own_record_back.py` | `test_t1530_the_arithmetic_constraint_is_postgresql_only_and_the_reason_is_measured` | delete (a)+(b) | compiles the DDL for the `sqlite` dialect and re-measures SQLite's float arithmetic on `sqlite3.connect(":memory:")` — a SQLite stand. Its PostgreSQL half (the CHECK is in the PostgreSQL DDL) is held more strongly by `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py::test_t1530_p_the_constraint_exists_and_bites_on_both_construction_paths` (both catalogues, and the CHECK bites) |
| `tests/unit/test_simulator_write_tick_metrics_upsert.py` | `test_sqlite_money_metrics_are_lossy_and_say_so_once_per_run`, `test_no_money_measurement_means_no_precision_warning`, the autouse fixture over `simulator_storage._SQLITE_MONEY_WARNED_RUN_IDS` | delete (a) | the first skipped itself on any backend but SQLite (so on the PostgreSQL tier it was a permanent skip); the second is its anti-vacuum control and is vacuous on PostgreSQL, where the warning never fires |

### 2.3 Kept, transferred or cleaned (no test deleted)

| file | decision | what changed |
|---|---|---|
| `tests/conftest.py` | transfer | removed: the `install_sqlite_transaction_control` / `install_test_sqlite_pragmas` imports and calls, `_is_sqlite` and every branch on it (SQLite file creation, `connect_args={"timeout": 30}`, the `PRAGMA table_info` hold release before `drop_all`, the per-test SQLite truncation with its `database is locked` retry loop, the SQLite `engine.dispose()` in the simulator cleanup), `OperationalError`. PostgreSQL paths are unchanged: `mode_b and not _is_sqlite` became `mode_b`, which is the same value on the only backend the tier accepts. Kept on purpose: the `sqlite` early return in `_mode_b_engine` and the non-PostgreSQL refusals in `init_db` / `_committed_database_context` (absence refusals, and the shape the T1525 source guard reads) |
| `tests/p015_b4a_stand.py` | transfer | `new_sqlite_stand` and its connect-listener helper removed; the docstring says what the stand is now. `Stand.driver_sql` keeps its `qmark` branch (a spelling rule, dead on asyncpg) |
| `tests/integration/test_p015_t1525_control_postgres.py` | transfer | now holds the scenarios, helpers and assertions it used to import from the deleted SQLite module; its four tests are unchanged |
| `tests/unit/test_p015_b4_entries_and_money.py` | transfer | `C15` (`test_c15_a_process_that_imports_only_the_models_still_cannot_write_a_debt`) was on a SQLite file only as a stand, its subject is not SQLite: the subprocess now writes into a mode-B clone (`committed_database.url`), without `create_all` and without the SQLite transaction control |
| `tests/unit/test_p015_step5a_reconciliation.py` | transfer | `test_step5a_a_journal_row_contradicting_its_own_arithmetic_is_failed_and_dominates` moves from a SQLite file to a mode-B clone with `chk_debt_journal_entries_delta_arithmetic` dropped — the orchestrator's decision (spec Changelog 2026-09-24): the verifier's per-row check is an existing money-path rule that the CHECK makes unreachable on the tier |
| `tests/unit/test_debt_optimistic_lock.py` | transfer | the SQLite branch (`sqlite_busy_error_name(...) == "SQLITE_BUSY_SNAPSHOT"`) and its import removed; the PostgreSQL branch (SQLSTATE `40001`) is now unconditional |
| `tests/unit/test_p015_step5c_reaction_and_hold.py` | transfer | the dialect branch of the FK-refusal assert keeps only SQLSTATE `23503` |
| `tests/unit/test_p014_t1406_no_mutable_database_in_the_working_tree.py` | transfer | `SCRATCH_ROOT` defined locally; `test_the_scanned_set_is_not_empty_in_this_repository` plants its own sentinel `.db` under `.local-run/test-runs/t1406-self-check/` and requires the walk to find it (it used to lean on the session's SQLite database existing, which after this slice nothing creates); the source guard's sanctioned roots drop the helper names |
| `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py` | transfer | `_KNOWN_SQLITE_CONSTRUCTIONS` (non-vacuity list) shrinks to `{"app/db/session.py"}` — every test entry was deleted |
| `tests/unit/test_sqlite_test_engine_enforces_foreign_keys.py` → `tests/unit/test_the_test_engine_enforces_foreign_keys.py` | keep, renamed | both tests run on PostgreSQL through `db_session` and assert a live property (the tier's schema refuses a dangling reference, including the bare `PrepareLock.tx_id` FK); only the name and docstring were about SQLite |
| `tests/unit/test_p015_t1544_inject_refuses_a_deactivated_equivalent.py` | keep | the ⚑ assert is already PostgreSQL (mode A) — section 3; docstring only |
| `tests/integration/test_p015_t1544_operator_stop_through_the_tick_sqlite.py`, `test_p015_step5c_hold_through_the_tick_sqlite.py` | keep | already mode-B PostgreSQL since S2a; the `_sqlite` file names are historical (like the `_postgres` suffix) and are referenced by name from the spec, which this slice may not edit |
| prose-only edits | keep | `test_p012_rt1_…_postgres.py`, `test_p012_t1201_money_door_bounds.py`, `test_p015_p1_money_phase_replay.py`, `test_the_tier_refuses_a_database_that_is_not_postgres.py`, `test_p015_t1530_the_journal_reads_its_own_record_back.py`, `test_p015_inject_transaction_ownership.py`, `test_p015_t1544_operator_stop_refuses_money.py`, the five S2a simulator modules: references that presented a deleted file as live evidence now say it left with SQLite |

## 3. The ⚑ asserts — where each now lives on PostgreSQL

| ⚑ assert (SQLite home) | PostgreSQL test that asserts the same effect | assert line |
|---|---|---|
| `test_an_aborted_payment_commit_leaves_debts_unchanged` (`test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py`, deleted) | `tests/integration/test_p015_t1525_control_postgres.py::test_postgres_an_aborted_payment_commit_leaves_debts_unchanged` — same scenario (`_scenario_engine_commit_violates_after_flows`), same assertion function, own SERIALIZABLE engine on a real pool | `_assert_aborted_payment_left_no_debt`: `assert outcome.debts_after == outcome.debts_before` (`:314`), after the mechanism asserts (the violation came after `_apply_flow` wrote; ABORTED; no prepare lock) |
| `test_a_stale_writer_cannot_overwrite_the_committed_debt_amount` (`tests/unit/test_debt_optimistic_lock.py`) | the same node, which has run on PostgreSQL in mode B since 017 stage 2b; this slice only removed its SQLite branch | `assert sqlstate == "40001"` (`:109`), `assert Decimal(str(stored.amount)) == Decimal("70")` (`:119`), version stepped once (`:121`) |
| `test_a_real_busy_snapshot_replays_the_money_phase_and_commits_once` (`test_p015_p1_money_replay_sqlite.py`, deleted) | `tests/integration/test_p015_p1_money_replay_postgres.py::test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` — a real `40001` from a competitor's commit after the tick's snapshot | `assert sqlstates == ["40001"]` (`:586`), the replan, the debt `opening + competitor + replanned`, one COMMITTED transaction, `tx.updated == 1`, `_real_money_replay_exhausted_total == 0` (`:629`) |
| `t1544_inject_refuses…` — "refusal before the envelope INSERT" (015 `spec.md:1748`) | `tests/unit/test_p015_t1544_inject_refuses_a_deactivated_equivalent.py::test_an_inject_into_a_deactivated_equivalent_is_consumed_without_debt_or_envelope` — already on the PostgreSQL tier (mode A, `db_session`); the recorder is on the engine and reads the statements the driver was sent | `assert refused_attempt.sent("INSERT INTO DEBT_OPERATIONS") == []` (`:126`), control `assert applied_attempt.sent(...)` (`:146`) |

## 4. Decisions recorded here

**`create_all` (`t1530`).** Read from the code, `Base.metadata.create_all` still has non-SQLite callers
that need it, so the contract stays and only the SQLite stand usages were removed:
`tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`, `test_p015_step5a_reconciliation_postgres.py`,
`test_p015_step5b_criterion_b_postgres.py` and `test_p015_step5c_hold_races_postgres.py` build one scratch
PostgreSQL database with `create_all` and one with `alembic upgrade head` and compare the catalogues —
the only executable check that the model and the migrations are one artefact (`T1540`); and
`tests/conftest.py::_ensure_schema_initialized` uses `drop_all` + `create_all` on the debug path where
`GEO_TEST_USE_MIGRATED_SCHEMA` is unset (the canonical runner and CI set it). Removed with SQLite:
`create_all` in `new_sqlite_stand`, `sqlite_money_stand`, `legacy_sqlite_session`, the SQLite reconciliation
stand, the five pragma fixtures, the C15 subprocess and `test_p015_p1_money_replay_sqlite.py`. Whether
`t1530` becomes a named exception of a future "no `create_all` in tests" guard is `T1707`'s call.

**Predicate test transferred, not deleted.** `money_conflict_name` is a filter on the money replay, and
its counter-check (AGENTS.md §9) was proved on errors the real driver produced — a real
`SQLITE_BUSY_SNAPSHOT` and a real SQLite foreign-key error. With SQLite gone the accepting half would
have been proved only by constructed exceptions, so both halves were re-made on asyncpg: a genuine
`40001` from two SERIALIZABLE sessions updating one row, and a genuine `23503`, on a mode-B clone.

**T1406 guard transferred, not deleted.** Its filesystem half still matters (developers' `.local-run/*.db`
are not deleted by 017, and the application's default URL is still SQLite until S4–S7). Its non-vacuity
test depended on some SQLite file existing; after this slice nothing in the suite creates one, so the test
plants its own.

## 5. What is left of `sqlite` in `tests/`

`git grep -n -i sqlite -- tests` after the slice: 544 hits in 102 files (before: 126 files). An `ast`
pass over every hit separates executable code from docstrings and comments: **133 hits in code, in 33
files; every other hit is prose** (history, "until 017 …", measurements recorded in docstrings). The code
hits, by kind:

- **Guards and absence refusals — they assert that SQLite is refused or absent, and stay:**
  `tests/unit/test_the_tier_refuses_a_database_that_is_not_postgres.py`,
  `test_p017_required_gate_runs_on_postgres.py`, `test_p017_t1701_provisioning_refuses_rather_than_skips.py`,
  `test_p017_s1_demo_fixture_generator_needs_no_database.py` (`aiosqlite` in the blocked-import list),
  `tests/conftest.py:402` (`_mode_b_engine` refuses a SQLite URL), `test_p014_t1406_…` (the source guard's
  regex and its counter-test data), `test_p1_commit_then_refresh_postgres.py:59` (a skip that cannot fire on
  the PostgreSQL-only tier), `test_p015_t1549_…_postgres.py:69` (asserts conftest's isolation helper
  returns nothing for a non-PostgreSQL backend).
- **Tests that pin SQLite-related names or behaviour in `app/` or `scripts/` — section 6.**
- **Dialect-spelling helpers with a dead SQLite branch** (`uuid.hex if dialect == "sqlite" else str(uuid)`):
  `tests/debt_setup.py:226`, `test_p015_step5a_reconciliation.py:112`, `test_p015_b4_entries_and_money.py:738`,
  `test_p015_b4_transaction_contract.py:179`, `test_p015_t1533_participant_deletion_keeps_obligations.py:53`,
  `tests/integration/test_p015_t1533_…_postgres.py:65`; and `Stand.driver_sql`'s `qmark` branch. None
  imports or constructs anything SQLite; left as they are to keep this slice to stands and SQLite-only tests.
- **Strings and names that mention SQLite but run on PostgreSQL:** failure messages
  (`p015_b4_support.py:156`, `p015_b4a_stand.py:70`, `test_p012_t1212_…:304`, `test_p015_b4_entries_and_money_postgres.py`
  parametrisation texts, `test_p012_t1210_…:157`, `test_p012_t1211_…:140`), the test name
  `test_p015_b4a_journal_mechanism.py::test_a_value_sqlite_would_change_is_exact_money_on_postgresql`, and the
  `_sqlite.py` file names of the two S2a tick modules (imported by name at
  `test_p015_step5c_hold_through_the_tick_sqlite.py:33`).

No test constructs a SQLite engine, opens a SQLite file, imports `aiosqlite`/`sqlite3` or
`app.db.sqlite_transaction_control` any more (checked by the same `ast` pass: the only `sqlite` imports
left are none; `sqlite3`/`sqlalchemy.dialects.sqlite` went with the `t1530` test).

## 6. Tests that pin SQLite code in `app/` or `scripts/` — for S4–S7

Not deleted here (they do not police test stands); each goes red, or must change, when the named code goes.

| test | what it pins | expected at S4–S7 |
|---|---|---|
| `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py` (all) | `install_sqlite_transaction_control` paired with every SQLite construction; non-vacuity entry `app/db/session.py` | red when `app/db/sqlite_transaction_control.py` or the SQLite branch of `app/db/session.py` is deleted — delete the guard with the mechanism (its successor is `T1707`) |
| `tests/unit/test_background_task_supervision.py:178-180` | `monkeypatch.setattr(main_module, "_sqlite_ensure_debts_version_column" / "_sqlite_refuse_pre_027_debt_operations" / "_sqlite_ensure_equivalents_integrity_hold_column", …)` | `AttributeError` when the three startup probes leave `app/main.py` — drop the three lines |
| `tests/unit/test_alembic_postgres_only.py::test_sqlite_alembic_fails_before_executing_revisions` | the refusal message in `migrations/env.py` names `scripts/init_sqlite_db.py` | message changes when the script goes (`T1704`) |
| `tests/unit/test_test_database_guard.py` | `scripts/validate_test_database_url.py` accepts SQLite URLs (half its cases) | change with the script |
| `tests/unit/test_settings_guardrails.py:69-78` | `app/config.py` default `DATABASE_URL = sqlite+aiosqlite:///./.local-run/geov0.db` | change with the default |
| `tests/unit/test_run_full_stack_database_url_redaction.py:258-265` | `scripts/run_full_stack.ps1` redaction, fed a SQLite URL | change with the script if it stops accepting SQLite |
| `tests/unit/test_p017_t1710_launcher_database_boundary.py:97-98` | launcher refusal of `sqlite+aiosqlite:///./.local-run/*.db` | absence guard; likely stays |
| `tests/unit/test_p017_t1711_seed_recipe_refuses.py` | `scripts/seed_recipe.assert_target_is_disposable` with SQLite URLs | change if the predicate stops knowing SQLite |
| `tests/unit/test_trustline_conflict_identity.py` (`test_classifier_reads_the_driver_error_and_not_the_statement`, `test_text_fallback_requires_the_full_triple`) | the text fallback of `_is_live_trustline_uniqueness_violation`, fed SQLite driver messages | delete or respell if the fallback is removed as SQLite code |
| `tests/integration/test_admin_equivalent_input_validation.py::test_admin_equivalent_mutation_responses_attach_utc_to_sqlite_timestamps`, `tests/integration/test_integrity_endpoints.py::test_integrity_status_and_verify_serialize_sqlite_checkpoint_as_utc` | the UTC attachment for naive timestamps; run on PostgreSQL today | keep or rename if the naive-timestamp path is removed |
| `tests/integration/test_p015_t1549_…_postgres.py:69` | `conftest._test_engine_isolation_kwargs("sqlite") == {}` | test-side; stays unless the helper changes |

Docstrings in `app/` still cite deleted test files (`app/db/sqlite_transaction_control.py:13,102,160`,
`app/db/types.py:34`); `app/` is out of this slice's scope.

## 7. Anti-vacuum — mutations in `app/`, each reverted

Each mutation was applied to one file of `app/`, the named test run through the canonical runner
(`scripts/verify_local.ps1 -TaskSlug p017s3del -BackendOnly -BackendSelector <node>`), and the file restored
byte for byte; `git status --short app/` was empty afterwards. Every run: exit `1`, `1 failed`.

| # | protects | mutation | red test | the failure |
|---|---|---|---|---|
| M1 | ⚑ aborted payment | `app/core/payments/engine.py`, commit's invariant handler: `if commit: await self.session.rollback()` → `if False:` | `test_p015_t1525_control_postgres.py::test_postgres_an_aborted_payment_commit_leaves_debts_unchanged` | `DebtOperationIncomplete [operation_not_completed]` — the journal refuses to commit the open operation, a second defence, before the debt assert is reached |
| M1b | ⚑ aborted payment, the debt assert itself | M1 **and** `app/core/ledger/journal.py` `_blocking_problem`: `if not op.is_settled:` (OPERATION_NOT_COMPLETED) → `if False:` | same | `the payment is ABORTED but its debt is stored: before={} after={(…): Decimal('7.00000000')}` — the assert at `:314` |
| M2 | ⚑ stale writer | `app/config.py`: `DB_POSTGRES_ISOLATION_LEVEL = "SERIALIZABLE"` → `"READ COMMITTED"` | `test_debt_optimistic_lock.py::test_a_stale_writer_cannot_overwrite_the_committed_debt_amount[mode_b]` | `isinstance(StaleDataError(...), DBAPIError)` is False — the database no longer refuses first |
| M3 | ⚑ busy/40001 replay | `app/core/simulator/money_replay.py::money_conflict_name` returns `None` first | `test_p015_p1_money_replay_postgres.py::test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` | `assert len(replays) == 1` → `0 == 1` |
| M4 | transferred predicate, accepting half | `money_conflict_name`: `if sqlstate in _TRANSIENT_SQLSTATES:` → `if False:` | `test_p015_p1_money_conflict_predicate.py::test_a_real_serialization_failure_is_a_money_conflict` | `assert None == '40001'` on the real asyncpg `SerializationError` |
| M5 | transferred predicate, refusing half | `money_conflict_name`: every `DBAPIError` → `"ANY_DATABASE_ERROR"` | `::test_a_real_integrity_error_on_the_same_backend_is_not_a_money_conflict` | `assert 'ANY_DATABASE_ERROR' is None` on the real `ForeignKeyViolationError` |
| M6 | transferred `C15` | `app/core/ledger/journal.py`: module-level `arm_journal_globally()` commented out | `test_p015_b4_entries_and_money.py::test_c15_a_process_that_imports_only_the_models_still_cannot_write_a_debt` | the subprocess on the clone printed `STORED:21.00000000` |
| M7 | kept step5a:427 on the clone | `app/core/ledger/reconciliation.py::_journal_sums`: `if (after or 0) - (before or 0) != delta:` → `if False:` | `test_p015_step5a_reconciliation.py::test_step5a_a_journal_row_contradicting_its_own_arithmetic_is_failed_and_dominates` | `assert 'UNVERIFIABLE' == 'FAILED'` |
| M8 | `t1544_inject_refuses…` in PostgreSQL form | `app/core/simulator/real_runner_impl.py`: `refuse_inactive_equivalents(...)` moved from before `debt_operation(...)` to the first statement inside it | `test_p015_t1544_inject_refuses_a_deactivated_equivalent.py::test_an_inject_into_a_deactivated_equivalent_is_consumed_without_debt_or_envelope` | `the inject opened its operation envelope before refusing the stop` — `INSERT INTO DEBT_OPERATIONS …` was sent |

## 8. Gates

Commands from the worktree, interpreter `D:\Work\Projects\GEOv0\.venv\Scripts\python.exe`, local
PostgreSQL at `127.0.0.1:5432`, database derived by the runner.

- **Baseline, full tier on the base `8a6c3d2`** (a detached worktree of that commit, slug `p017s3base`,
  so the two runs could not share a database): `.\scriptserify_local.ps1 -TaskSlug p017s3base -BackendOnly`
  → `3021 passed, 4 skipped, 4 deselected, 1 xfailed, 5 warnings in 1226.44s (0:20:26)`, Alembic head
  `028_equivalent_integrity_hold`, exit `0`, wall 1231 s.
- **Cheap, the 27 changed test modules:** `.\scriptserify_local.ps1 -TaskSlug p017s3del -BackendOnly
  -BackendSelector <the 27 files>` → `445 passed, 1 deselected in 93.77s`, exit `0`.
- **Milestone, full tier on this slice's commit:** `.\scriptserify_local.ps1 -TaskSlug p017s3del -BackendOnly`.
  It has to run on the commit that carries this file, so its summary line, exit code and time are in the
  hand-off report of the slice, not here.

## 9. Not verified

- CI (`required-backend`, `required-ui`) — the branch is pushed without a PR, so no job ran.
- Timing on CI: only local times were measured, one run each, no repeats.
- `-IncludeExpensive`: the four `slow` tests were not run; none of them was edited
  (`test_adaptive_does_not_degrade_vs_static` lives in an edited module, but only the pragma test and the
  SQLite fixture beside it changed).
- The dialect-spelling helpers of section 5 were not removed and their SQLite branches not exercised.
- Whether the tests of section 6 go red exactly as predicted is a prediction about S4–S7, not a measurement.
- The two `_sqlite.py` file names of the S2a tick modules were not renamed.
