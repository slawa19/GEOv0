# T1706, первая половина — per-file инвентарь тестов: keep / transfer / delete и режим фикстуры A или B

- **Date:** 2026-09-21
- **Программа:** [017 — PostgreSQL как единственный движок](spec.md), задача `T1706`
- **Назначение:** условие входа в стадию 2. Спека требует публикации per-file инвентаря с назначенным режимом фикстуры **до** авторизации стадии 2: «счётчиков и трёх примеров для ревью недостаточно» (`spec.md`, Verification plan §2).
- **Status authority:** метка описательная. Инвентарь — измерение, а не решение: вердикт по каждому файлу становится обязательством только когда стадия 2 авторизована, и до тех пор перепроверяем.
- **Снято на:** `main`, HEAD `4310603`, дерево чистое. Вторая половина `T1706` — замер времени **Postgres**-тира и бюджет на подпись владельца — остаётся открытой: она идёт после стадии 2, потому что мерить нечего, пока тесты не поехали на Postgres. Половина «до» измерима сегодня и записана в разделе 8.

## Граница доказательства — прочесть до использования таблицы

Инвентарь построен **чтением кода**, ни один тест не запускался. Отсюда три ограничения, и они не смягчаются:

1. **Ни одно из 88 назначений режима B не проверено исполнением.** Каждое стоит на пяти якорях из раздела 1 и на чтении модуля. Назначение, которое не подтвердится при миграции, — ожидаемый исход, а не дефект инвентаря.
2. **Один класс не проверяется чтением вовсе:** достаточно ли велик набор данных у конкретного теста режима A, чтобы `SERIALIZABLE` дал `40001` против соседа. Это замер, и он принадлежит второй половине `T1706`.
3. **Файлы, по которым решение не принято, вынесены в раздел 4** отдельным списком, а не спрятаны в таблицу уверенным тоном.

**Что перепроверено оркестратором по коду лично** (§15: несущие факты субагента проверяются до интеграции):

- число файлов `test_*.py` — **299**, четырьмя независимыми командами, и столько же на `6c5e63f`; «около 304» из инвентаризации 2026-09-21 не воспроизводится и не принято;
- механизм `[CLR]` — `app/core/clearing/service.py:1530-1539`: на Postgres сессия с `bind` типа `AsyncConnection` отвергается безусловно (`GeoException`, `event=clearing.external_connection_bind_unsupported`), а `:1523` уводит не-Postgres в ранний возврат, из-за чего на SQLite это молчит;
- три из одиннадцати файлов, исполняющих клиринг на дефолтном тире, открыты и связка `db_session` + `ClearingService` подтверждена: `tests/unit/test_invariants.py:8,25`, `tests/unit/test_zero_debt_policy.py:7,19`, `tests/unit/test_clearing_additional_cases.py:8,47`.

Остальные 296 строк таблицы — работа агента, перепроверенная выборочно, а не сплошь.

**Тело ниже — отчёт агента дословно, на английском.** Оно сознательно не переведено: инвентарь — это evidence, и пересказ на другом языке подменил бы измерение его изложением. Русский здесь — рамка, которая говорит, чего измерение стоит; английский — само измерение.

## Найденное противоречие в спеке

`tests/unit/test_invariants.py` перечислен в разделе «Существующие селекторы, обязанные остаться зелёными» (`spec.md`, Verification plan §3). Он исполняет клиринг через `db_session`, то есть в режиме A на Postgres получает безусловный отказ `[CLR]`. Селектор не может остаться зелёным в режиме A. По §13 AGENTS.md это **дефект спеки, а не пробел реализации**: либо verification plan называет режим у каждого перечисленного селектора, либо строка о нём неверна. Передано держателю `spec.md` 2026-09-21.

---


## 0. Count reconciliation (§16 «я всё нашёл» проверяется числом)

| Measurement | Command | Value |
|---|---|---|
| Rows in my table | — | **299** |
| Test files in the tree | `git ls-files tests/ \| grep -c "test_.*\.py$"` | **299** |
| Same on disk | `find tests -name "test_*.py" \| wc -l` | 299 |
| Same at `6c5e63f` (the base the 017 spec was written on) | `git ls-tree -r --name-only 6c5e63f tests/ \| grep -c ...` | 299 |
| All tracked files under `tests/` | `git ls-files tests/ \| wc -l` | 313 |
| All `.py` under `tests/` | — | 312 |

Verified one-to-one with `comm`: no file in the tree is missing from the table, no row points at a file that does not exist, no duplicates.

**The «около 304» of the 2026-09-21 inventory does not reconcile with anything I can reproduce**: not 299 (`test_*.py`), not 312 (all `.py`), not 313 (tracked). The count was identical at `6c5e63f`, so it is not drift since the spec was written — the 304 is simply a different population, and the spec does not say which. I did not adopt it. If the closing review is going to use the spec's bucket counts (A 84 / B 14 / C 42 / D 59 / E ? / F 41 / G 37, summing to 277 + E = 304), **the discrepancy has to be resolved before those numbers are cited as a checklist** — my per-bucket counts (below) differ from them substantially, and at least part of the difference is that I put the *fixture mode* and the *bucket* on two independent axes, as instructed.

**13 non-test modules under `tests/` carry the fixtures and stands and must be converted in stage 2 as well** — they are not in the table because they are not tests, but they are the work: `tests/conftest.py`, `tests/scratch_db.py`, `tests/migrated_schema.py`, `tests/debt_setup.py`, `tests/p015_b4_support.py`, `tests/p015_b4a_stand.py` (`new_sqlite_stand`, used by 8 test modules), `tests/integration/t1523_restart_child.py`, `tests/integration/p012_pg_http.py`, `tests/contract/openapi_response_conformance.py`, plus four `__init__.py`.

## 1. The rule set I applied, with the anchors that decide

Mode is decided by mechanism, not by filename and not by the marker. Five decisive anchors, all read in this session:

| Code | Anchor | What it decides |
|---|---|---|
| `[FIXA]` | `tests/conftest.py:409-411` | Mode A = `engine.connect()` → `begin()` → `TestingSessionLocal(bind=connection)` → savepoint, rolled back. Session is **connection-bound**. |
| `[CLR]` | `app/core/clearing/service.py:1530-1539` | PostgreSQL clearing **refuses** an `AsyncConnection`-bound session outright (`GeoException`, `clearing.external_connection_bind_unsupported`). Any test that *executes* a cycle through `db_session`/`client` therefore cannot run in mode A. The tree already documents this: `tests/integration/test_p012_t1211_shared_edge_order_postgres.py:8-14` says in so many words that its outcome half has no PG twin for exactly this reason. |
| `[VIS]` | `tests/integration/test_simulator_real_snapshot_db_enrichment.py:59-69` | Another session's commit is invisible inside one snapshot; waiting for it means re-reading in a **new transaction**. Generalised: anything the application commits on its own engine-bound session (`TestingSessionLocal()`, the real-mode heartbeat, `app.db.session.AsyncSessionLocal`) is both **invisible to** and **not undone by** mode A. Today SQLite hides this because `conftest.py:375-395` hard-resets every table per test; on Postgres mode A there is no such reset, so those commits leak into the next test. |
| `[PROC]` | `tests/integration/test_p015_t1523_restart_after_commit_postgres.py:174-175` | «Seeded on its own connection and really committed: two other processes have to see it.» |
| `[LOCK]` | `app/core/payments/engine.py:182` | `if not self._is_postgres(): return` — advisory locks, and with them every contention schedule, exist only on Postgres. |

**Mode B is assigned when at least one of:** clearing execution on the shared session `[CLR]`; the test waits for or depends on another session's commit `[VIS]`; a second OS process `[PROC]`; a real concurrent schedule (2+ writers, advisory locks, `FOR UPDATE`, `40001`) `[LOCK]`; the module builds its own engine/sessionmaker with real root commits (today a scratch SQLite file, tomorrow a disposable database); or it needs `CREATE DATABASE`.
**Mode A** = uses `db_session`/`client` only, no cross-session visibility requirement. **`—`** = touches no database.

I did **not** use the `_postgres` suffix or `@pytest.mark.postgres` as evidence, and section 3 shows they disagree with the mechanism in 51 files.

## 2. The table

Legend for the reason column: the codes above; `own engine` = the module calls `create_async_engine`/`create_engine` itself; `2nd session` = it opens `TestingSessionLocal()`/`factory()` and commits; `[STC]` = `app/db/sqlite_transaction_control.py` (deleted by `T1703`); `[MAIN]` = the three startup probes `app/main.py:323-500` (deleted by `T1703`); `[INIT]` = `scripts/init_sqlite_db.py` (deleted by `T1704`).

### tests/ (1)

| path | bucket | verdict | mode | reason |
|---|---|---|---|---|
| tests/test_participants_me_and_auth_payloads.py | G | keep | A | HTTP auth/me payloads through the `client` fixture, `:14`; nothing crosses a session. |

### tests/contract/ (10)

| path | bucket | verdict | mode | reason |
|---|---|---|---|---|
| tests/contract/test_openapi_contract.py | C | keep | A | canon-vs-FastAPI diff plus two `client` envelope tests, `:92`; the clearing mention at `:1824` is a route string, not an execution. |
| tests/contract/test_p011_callable_surface_matches_canon.py | C | keep | — | route-table introspection only, `:131`. |
| tests/contract/test_p011_declared_status_reachability.py | C | keep | — | static canon read; no `client`/`db_session` in any signature. |
| tests/contract/test_p011_documentation_models_do_not_filter.py | C | keep | — | `TestClient` over a synthetic probe app, `:87` — no repository DB. |
| tests/contract/test_p011_nullable_needs_a_sibling_type.py | C | keep | — | YAML schema analysis. |
| tests/contract/test_p011_rate_limit_status_is_declared.py | C | keep | A | one real 429 through `client`, `:82`. |
| tests/contract/test_p011_reachable_statuses_are_declared.py | C | keep | A | real 403/422 through `client`, `:651`, `:671`. |
| tests/contract/test_p011_responses_conform_to_the_canon.py | C | keep | **B** | starts a real-mode run over HTTP `:1074` (background tick commits on its own session `[VIS]`) and re-runs itself in a child pytest `:1502` `[PROC]`. |
| tests/contract/test_p011_root_routes_bypass_the_policy_gate.py | C | keep | — | route-declaration read. |
| tests/contract/test_p011_success_responses_describe_their_content.py | C | keep | — | canon read. |

### tests/integration/ (120)

| path | bucket | verdict | mode | reason |
|---|---|---|---|---|
| test_admin_endpoints.py | G | keep | A | admin token gate through `client` `:10`. |
| test_admin_equivalent_input_validation.py | C | **transfer** | A | `test_admin_equivalent_mutation_responses_attach_utc_to_sqlite_timestamps` `:70` asserts the UTC attachment applied to **SQLite naive** timestamps; on `timestamptz` the premise is gone, so the assert must be re-stated on the Postgres value. |
| test_admin_feature_flags_multipath.py | A | keep | A | flags gate a payment through `db_session` `:19`. |
| test_admin_freeze_participant.py | A | keep | A | `client` `:12`. |
| test_admin_mutation_audit_atomicity.py | A | keep | A | rollback-on-audit-failure inside one session `:19`. |
| test_admin_routing_max_paths.py | A | keep | A | `db_session` `:14`. |
| test_audit_drift_delta_check_sse_integration.py | F | **transfer** | **B** | builds its own SQLite file engine `:54` with `tests/scratch_db` pragmas `:25`, and carries `test_this_modules_engine_has_the_application_sqlite_pragmas` `:50` — that test dies with `[STC]`, the stand becomes a disposable database. |
| test_auth_refresh.py | G | keep | A | `client` `:15`. |
| test_auth_token_type_enforced.py | G | keep | A | `client` `:10`. |
| test_clearing_commit_replay_postgres.py | B | keep | **B** | `gather` `:198` over `execute_clearing_with_amount` `:199` from `TestingSessionLocal()` `:91` `[CLR]`. |
| test_clearing_max_depth_controls_long_cycles.py | A | keep | **B** | `POST /api/v1/clearing/auto` `:96-103` asserting `cleared_cycles >= 1`, through `client` `[CLR]`. **Corrected 2026-09-21** — was `A` on the reasoning «no `ClearingService` anywhere in the file», which is literally true and beside the point: the token appears 0 times because the service is reached through the route. |
| test_clearing_payment_prepare_interlock_postgres.py | B | keep | **B** | own engine `:164`, two writers `:398`, advisory `:28` `[LOCK]`. |
| test_clearing_skip_releases_locks_postgres.py | B | keep | **B** | row-lock release under contention `:387` + clearing execution `:193` `[CLR]`. |
| test_concurrent_clearing_payment_lost_update_postgres.py | B | keep | **B** | `gather` `:244` clearing vs payment `[LOCK]`. |
| test_concurrent_prepare_routes_bottleneck_postgres.py | B | keep | **B** | `gather` `:169`, advisory `:27`, SSI `[LOCK]`. |
| test_daily_limit_not_enforced.py | A | keep | A | informational limit over HTTP `:42`; no clearing execution in the file. |
| test_equivalent_writer_and_legacy_reads.py | A | keep | A | writer validation through `db_session` `:28`. |
| test_health_and_equivalents.py | G | keep | A | `client` `:16`. |
| test_http_rate_limit.py | G | keep | A | `client` `:43`. |
| test_integrity_endpoints.py | C | **transfer** | A | `test_integrity_status_and_verify_serialize_sqlite_checkpoint_as_utc` `:163` is the SQLite naive-timestamp case; restate on `timestamptz`. |
| test_integrity_repairs_atomicity.py | A | keep | **B** | opens a second engine-bound session `TestingSessionLocal()` `:43` to read what the repair committed `[VIS]`. |
| test_p011_admin_money_is_a_decimal_string_on_the_wire.py | C | keep | A | five admin reads through `client` `:8`. |
| test_p011_events_poll_is_always_empty.py | C | keep | A | `client` `:4`. |
| test_p011_json_artifacts_are_downloaded_and_validated.py | C | keep | A | artifact download through the real route `:31`. |
| test_p011_money_is_a_decimal_string_on_the_wire.py | C | keep | A | `client` `:14`. |
| test_p012_money_form_and_detector_reach_postgres.py | A | keep | **B** | clearing execution `:717` from `TestingSessionLocal()` `:663` `[CLR]`. |
| test_p012_rt1_signed_amount_versus_stored_amount_postgres.py | A | keep | **B** | writes and re-reads storage on a second committed session `:140` `[VIS]`. |
| test_p012_rt2_precision_1_amount_is_erased_on_the_wire_postgres.py | A | keep | **B** | real-mode producers `:261` on a second session `:125` `[VIS]`. |
| test_p012_t1201_magnitude_bound_is_load_bearing_postgres.py | A | keep | **B** | second session `:74` + own `pg_client` `[VIS]`. |
| test_p012_t1201_money_door_at_the_entrances.py | A | keep | A | two HTTP entrances `:4`. |
| test_p012_t1202_money_modules_read_precision_postgres.py | A | keep | A | `ClearingService(db_session)` `:249` — **detection only, no execute/auto_clear in the file**, so `[CLR]` is not reached. |
| test_p012_t1207_one_money_form_across_producers.py | F | keep | **B** | real-mode producers `:75` with `gather` `:275` over sessions the runner owns `[VIS]`. The `execute_clearing_with_amount` at `:556` is a **fake** service, not the real one. |
| test_p012_t1210_net_balance_agrees_with_its_atoms.py | A | keep | A | net vs atoms through `db_session` `:394`; no clearing execution, no second writer. |
| test_p012_t1211_negative_zero_cannot_come_back_from_the_ledger_postgres.py | A | keep | A | one raw expression read on `db_session` `:44`. |
| test_p012_t1211_shared_edge_order_postgres.py | A | keep | A | `find_cycles` only `:38`; its own docstring `:8-14` records that the execution half deliberately stays off this fixture `[CLR]`. |
| test_p012_t1212_declared_precision_exceeds_storage_scale_postgres.py | C | keep | A | three declarations of precision compared over `client` `:196`. |
| test_p014_t1402_zero_sum_is_not_published_as_a_check.py | C | keep | A | the wire says zero-sum is not a check `:70`. |
| test_p015_b4_entries_and_money_postgres.py | D | keep | **B** | own engine `:184`, second session `:331`, `gather` `:1397`, clearing `:1766`, `FOR UPDATE` `:1693` `[LOCK]`. |
| test_p015_b4_transaction_contract_postgres.py | D | keep | **B** | own engine `:105`; the contract is about root transactions. |
| test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py | D | keep | **B** | own engine `:94`, clearing `:1059`, `FOR UPDATE` `:1007` `[CLR]`. |
| test_p015_b4a_journal_postgres.py | D | keep | **B** | own AUTOCOMMIT engine `:6` — the stand is the subject. |
| test_p015_f0156_repairs_are_closed_by_default.py | A | keep | **B** | asserts the endpoint touched nothing by reading a **durable** second session `:110` `[VIS]`. |
| test_p015_inject_holds_the_owner_lock_postgres.py | B | keep | **B** | owner advisory lock observed under contention `:128`, `:260` `[LOCK]`. |
| test_p015_inject_retries_a_serialization_failure_postgres.py | B | keep | **B** | real `40001` `:5`; imports the stand of the file above `[LOCK]`. |
| test_p015_p1_money_replay_postgres.py | B | keep | **B** | own engine `:105`, real `40001` replay `:1` `[LOCK]`. |
| **test_p015_p1_money_replay_sqlite.py** | D | **delete** | — | the whole stand is «a REAL SQLite conflict» on a file-backed WAL database with `[STC]` (`:1-20`, engine `:89`, `tests/scratch_db` `:64`, `PRAGMA journal_mode` assert `:578`). The behaviour it proves already has a Postgres twin — `test_p015_p1_money_replay_postgres.py`, whose own docstring `:164` says the defect is common to both backends. Nothing survives the removal of the SQLite driver. |
| test_p015_step5a_reconciliation_postgres.py | D | keep | **B** | `CREATE DATABASE` `:203` + own engines `:100`. |
| test_p015_step5b_criterion_b_postgres.py | D | keep | **B** | `CREATE DATABASE` `:165`, `gather` `:276`, clearing `:411` `[CLR]`. |
| test_p015_step5c_hold_races_postgres.py | B | keep | **B** | `CREATE DATABASE` `:630`, subprocess `:596`, `gather` `:193`, clearing `:360` `[PROC]`. |
| test_p015_step5c_hold_through_the_tick_sqlite.py | A | **transfer** | **B** | the **rule** is T1544's and is tier-independent (`:1-10`), but the stand is the SQLite one it imports from `test_p015_t1544_operator_stop_through_the_tick_sqlite` `:34`; the assert moves onto a disposable Postgres database, the stand does not survive. |
| test_p015_t1523_in_progress_and_insert_race_postgres.py | B | keep | **B** | insert race `:288` on a second session `:133` `[LOCK]`. |
| test_p015_t1523_replay_after_a_hold_or_an_abort.py | A | keep | A | two replay cells over HTTP `:63`; no second writer. |
| test_p015_t1523_restart_after_commit_postgres.py | B | keep | **B** | **`[PROC]`** — child process `:122` reads a commit made at `:175`. |
| test_p015_t1524_equivalent_deletion_keeps_obligations_postgres.py | A | keep | **B** | the RESTRICT race needs a row committed by another session `:58` `[VIS]`. |
| test_p015_t1525_classification_reads_deliberate_wrapping_only_postgres.py | D | keep | **B** | own engine `:70`, second session `:178`, real `FOR UPDATE` `:369`. |
| test_p015_t1525_control_postgres.py | D | **transfer** | **B** | own engine `:49`. It is framed as «the control for the SQLite savepoint module»; once that module is deleted the framing has no referent, but the assertion it makes — PostgreSQL savepoint/rollback semantics are correct — is a live invariant and should be kept under its own name. |
| test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py | A | keep | **B** | own engine `:90`; a `NaN` that reaches the column and must be observed after commit `[VIS]`. |
| test_p015_t1528_the_statement_is_read_not_guessed_postgres.py | D | keep | **B** | own stand `:65`. |
| test_p015_t1530_delta_arithmetic_postgres.py | D | keep | **B** | `CREATE DATABASE` `:113`, two construction paths — **see section 6**. |
| test_p015_t1533_participant_deletion_keeps_obligations_postgres.py | A | keep | **B** | second session `:82` seeds past the write guard and the deletion is observed after commit `[VIS]`. |
| test_p015_t1534_the_migrated_schema_flag_is_true_postgres.py | E | keep | A | reads the live catalogue through `db_session` `:74`; it is the counter-check the 017 verification plan names for the bootstrap path. |
| test_p015_t1544_operator_stop_races_postgres.py | B | keep | **B** | PATCH vs money `gather` `:213`, clearing `:297`, second session `:132` `[LOCK]`. |
| test_p015_t1544_operator_stop_refuses_money.py | A | keep | **B** | drives the real `actions/clearing` route `:432` `[CLR]`; also starts a real run `:372` `[VIS]`. |
| test_p015_t1544_operator_stop_through_the_tick_sqlite.py | A | **transfer** | **B** | the rule (a refusal is a rejection, not an error of the run) is tier-independent; the stand is a scratch SQLite engine `:64` with `tests/scratch_db` `:52` and dies with `[STC]`. |
| test_p015_t1545_max_flow_to_self.py | A | keep | A | one HTTP request `:22`. |
| test_p015_t1549_test_engine_runs_at_the_application_isolation_postgres.py | E | keep | **B** | asserts the **engine's** isolation with its own engine `:41`/session `:52`; it is the guard over the fixture itself, so stage 2 must re-point it at whichever engine mode A and mode B build. |
| test_p015_t1551_clearing_reduces_debt_on_a_frozen_line_postgres.py | A | keep | **B** | `auto_clear` `:114` from a second session `:58` `[CLR]`. |
| test_p1_clearing_run_perimeter_postgres.py | A | keep | **B** | the interlock path specifically — own engine `:55`, execution `:160` `[CLR]`. |
| test_p1_commit_then_refresh_postgres.py | A | keep | **B** | own engine `:69`; a connection is killed after a durable commit `[VIS]`. |
| test_p1_failed_rollback_state_postgres.py | B | keep | **B** | own engine `:50`; what a session looks like after `rollback()` itself fails. |
| test_p1_reconcile_after_failed_rollback_postgres.py | B | keep | **B** | own engine `:76`; the reconciliation read must get a **fresh snapshot** `[VIS]`. |
| test_p1_tick_session_ownership_postgres.py | F | keep | A | a best-effort writer must not discard the tick's transaction — one session, `db_session` `:38`. |
| test_p1_trustline_reopen_postgres.py | B | keep | **B** | concurrent create `:270` on a second session `:210` `[LOCK]`. |
| test_participants_search.py | A | keep | A | `client` `:12`. |
| test_participants_type_default.py | A | keep | A | `client` `:10`. |
| test_participants_uniqueness.py | A | keep | A | `client` `:12`. |
| test_payment_abort_has_error_code.py | A | keep | A | `db_session` `:15`. |
| test_payment_commit_advisory_locks_postgres.py | B | keep | **B** | `gather` `:400`, advisory `:31` `[LOCK]`. |
| test_payment_engine_audit_conflict_postgres.py | B | keep | **B** | real serialization failure `:154` `[LOCK]`. |
| test_payment_engine_uow_retry_postgres.py | B | keep | **B** | real concurrent insert `:363` `[LOCK]`. |
| test_payment_idempotency_postgres.py | B | keep | **B** | request-level races `:164` `[LOCK]`. |
| test_payment_inverse_multisegment_postgres.py | B | keep | **B** | `gather` `:305`, advisory `:208` `[LOCK]`. |
| test_payment_pair_advisory_locks_postgres.py | B | keep | **B** | two reverse segments contend on one advisory resource `:22` `[LOCK]`. |
| test_payment_prepare_capacity_policy.py | A | keep | A | reservation policy counting on `db_session` `:32`. |
| test_payment_prepare_error_taxonomy.py | A | keep | **B** | `gather` `:1232` over in-flight prepares; two coroutines on one `AsyncConnection` is not a schedule mode A can run `[FIXA]`. |
| test_payment_staged_multicall_postgres.py | B | keep | **B** | retained locks across batches `:283`, advisory `:28` `[LOCK]`. |
| test_payments_amount_validation.py | A | keep | A | `db_session` `:17`. |
| test_payments_constraints_avoid.py | A | keep | A | `db_session` `:20`. |
| test_payments_idempotency.py | A | keep | A | same-`tx_id` replay in one session `:34`. |
| test_payments_insufficient_capacity.py | A | keep | A | `db_session` `:16`. |
| test_payments_list_filters.py | A | keep | A | `db_session` `:19`. |
| test_payments_multipath.py | A | keep | A | `db_session` `:16`. |
| test_post_tick_audit_drift_runner_integration.py | F | **transfer** | **B** | own scratch engine `:61`/`:30` and `test_this_modules_engine_has_the_application_sqlite_pragmas` `:108-109`; the pragma test dies with `[STC]`. |
| test_prepare_locks_tx_id_fk_postgres.py | A | keep | A | one FK refusal on `db_session` `:13`. |
| test_scenarios.py | A | keep | **B** | `test_clearing` `:562` → `POST /api/v1/clearing/auto` `:709` asserting `cleared_cycles >= 1` `:711`, through `client` `[CLR]`. **Corrected 2026-09-21** — was `A` on an anchor at `:186`, above the clearing test. |
| test_simulator_adaptive_clearing_effectiveness_ab.py | F | **transfer** | **B** | A/B benchmark on «an isolated SQLite DB» (`:1-6`), own engine `:72`, pragma test `:106-107`. |
| test_simulator_adaptive_clearing_integration.py | F | **transfer** | **B** | own engine `:63`, pragma test `:108-109`, real tick `:35`. |
| test_simulator_artifacts_events_ndjson.py | F | keep | A | artifact listing over `client` `:12`. |
| **test_simulator_clearing_no_deadlock.py** | D | **delete** | — | its subject is named in its own first line: «Regression test for Bug X: **SQLite deadlock** when clearing runs concurrently with an uncommitted parent session» (`:1-3`), own SQLite engine `:71`, pragma test `:109-110`. The mechanism (SQLite's single write lock) does not exist on Postgres; the Postgres form of the same question is already `test_clearing_payment_prepare_interlock_postgres.py`. |
| test_simulator_metrics_migration_018_postgres.py | E | keep | **B** | migration 018 executed for real on a disposable schema, own engine `:176`. |
| test_simulator_metrics_numeric_value_postgres.py | F | keep | **B** | real tick `:67` writing metrics on the runner's session `:152` `[VIS]`. |
| test_simulator_network_growth.py | F | keep | A | «thick unit test» driving `_apply_due_scenario_events` on `db_session` `:6`; no background run, no second writer. |
| test_simulator_real_snapshot_db_enrichment.py | F | **transfer** | **B** | **the canonical `[VIS]` case** `:59-69`; also raw SQL with `?` placeholders `:118-120` (section 5). |
| test_simulator_scenario_upload_validation.py | F | keep | A | upload validation over `client` `:91`. |
| test_simulator_sse_fixtures_clearing_animation_pair.py | C | keep | A | **fixtures** mode — the `clearing_done` event is synthesised, no cycle is executed `:26`. |
| test_simulator_sse_real_smoke.py | C | keep | **B** | starts a **real** run `:20`; the heartbeat seeds and commits on its own session `[VIS]`, and mode A can neither see nor undo that. |
| test_simulator_sse_replay_410.py | C | keep | A | replay recovery over `client` `:11`, in-memory bus. |
| test_simulator_sse_smoke.py | C | keep | A | fixtures-mode SSE `:12`. |
| test_simulator_sse_trust_drift_decay_topology_patch.py | C | keep | **B** | real run `:22` `[VIS]`. |
| test_simulator_sse_tx_failed_timeout.py | C | keep | **B** | real run `:61` `[VIS]`. |
| test_simulator_super_smoke.py | F | keep | **B** | real run `:928`, `RealClearingEngine` `:810` on its own sessionmaker, `gather` `:811` `[VIS]`. **Anchor corrected 2026-09-21** — `:538` was cited as «real clearing action»; it is `clearing-once`, an SSE emitter that takes no session (`app/api/v1/simulator.py:2861-2866`). The mode was right, the stated reason was not. |
| test_static_clearing_hard_timeout_no_leak.py | F | keep | — | cancellation of a fake clearing task `:24`, no DB fixture at all. |
| test_trustline_cache_invalidation.py | A | keep | A | cache eviction after commit/rollback on `db_session` `:37`. |
| test_trustline_negative_constraints.py | A | keep | A | `db_session` `:20`. |
| test_trustlines_get_by_id.py | A | keep | A | `db_session` `:14`. |
| test_trustlines_list_filters_pagination.py | A | keep | A | `db_session` `:82`. |
| test_validation_error_envelope.py | C | keep | A | `client` `:6`. |

### tests/unit/ (168)

| path | bucket | verdict | mode | reason |
|---|---|---|---|---|
| test_admin_abort_tx.py | A | keep | A | abort + audit through `db_session` `:21`. |
| test_admin_audit_log_list.py | G | keep | A | `:12`. |
| test_admin_clearing_cycles.py | A | keep | A | cycle **listing** endpoint `:17`; no execution. |
| test_admin_config_patch_atomicity.py | G | keep | **B** | `asyncio.create_task` over an in-flight `patch_admin_config` `:194` — two overlapping handlers on one session; mode A binds them to one `AsyncConnection` `[FIXA]`. |
| test_admin_graph_ego.py | G | keep | A | `:17`. |
| test_admin_graph_snapshot.py | G | keep | A | `:17`. |
| test_admin_incidents_list.py | G | keep | A | `:13`. |
| test_admin_liquidity_summary.py | G | keep | A | `:25`. |
| test_admin_participant_metrics.py | G | keep | A | `:20`. |
| test_admin_participants_list.py | G | keep | A | `:10`. |
| test_admin_participants_stats.py | G | keep | A | `:16`. |
| test_admin_token_comparison.py | G | keep | — | `compare_digest` over the dependency graph, no DB. |
| test_admin_trustlines_bottlenecks.py | G | keep | A | `:23`. |
| test_admin_trustlines_list.py | G | keep | A | `:17`. |
| test_admin_whoami_and_extras.py | G | keep | A | `:46`. |
| **test_alembic_postgres_only.py** | E | **transfer** | — | half of it survives («alembic refuses a non-Postgres URL») and merges into `T1704`'s new `DATABASE_URL` refusal; the other half is dead — the asserted message text points at `[INIT]` (`:14-16`), and `test_supported_sqlite_initializer_remains_available` imports that script `:30`. |
| test_apply_flow_retry_on_stale.py | A | keep | **B** | a second, committed session `:67` supplies the stale value `[VIS]`. |
| test_audit_drift_integrity_log.py | F | keep | A | round-trip on `db_session` `:10`. |
| test_audit_drift_sse_event.py | C | keep | — | model serialization. |
| test_backend_marker_policy.py | E | **transfer** | — | the canonical tier list it pins contains the `postgres` marker, which `T1702` removes from `pytest.ini`, `verify_local.ps1` and the files; the guard must be re-stated against the new tier set or it will be red by construction. |
| test_background_task_supervision.py | G | keep | A | `AsyncSessionLocal` is monkeypatched with a fake `:243`; the `client` probe `:174` is the only DB touch. |
| test_balance_service_summary.py | A | keep | A | `:16`. |
| test_canonical_json.py | A | keep | — | pure. |
| test_check_alembic_heads.py | E | keep | — | script guard, no DB. |
| test_clearing_additional_cases.py | A | keep | **B** | `ClearingService(db_session)` with real `execute_clearing`/`auto_clear` `:149`, `:466` `[CLR]`. |
| test_clearing_auto_clearing_policy.py | A | keep | — | consent predicate over synthetic edges `:80`. |
| test_clearing_plan_edges_extraction.py | F | keep | — | pure extraction. |
| test_clearing_prepare_lock_conflict.py | A | keep | — | synthetic rows `:118`. |
| test_clearing_scope_reaches_every_replay_path.py | E | keep | — | AST guard over replay paths. |
| test_clearing_sql_cycle_detection.py | A | keep | A | triangle/quadrangle SQL on `db_session` `:17`; detection only. |
| test_crypto_pid.py | A | keep | — | pure. |
| **test_debt_optimistic_lock.py** | B | **transfer** | **B** | its docstring `:1-12` says outright that the mechanism has **a name per backend** and that only the SQLite tier selects it today; it imports `sqlite_busy_error_name` from `[STC]`. The invariant (the committed value survives, the stale writer is refused) is kept; the SQLite branch and the import go, and the second session `:69` makes it mode B. |
| test_debt_symmetry.py | A | keep | A | `:18`. |
| test_deployment_config.py | E | **transfer** | — | pins compose/Dockerfile defaults; `T1704` changes `DATABASE_URL`'s default and the run scripts, so the pinned values move with it. |
| test_edge_patch_builder.py | F | keep | A | patch shapes on `db_session` `:20`. |
| test_edges_by_equivalent_status_filter.py | F | keep | — | pure filter. |
| test_equivalent_code_validation.py | A | keep | — | pure. |
| test_equivalent_metadata_validation.py | A | keep | — | pure. |
| test_event_bus_backpressure.py | F | keep | — | in-process bus `:34`. |
| test_fixtures_runner_clearing_done_amount.py | F | keep | — | fixtures runner payload. |
| test_flow_and_periodicity.py | F | keep | — | planner phases, no DB. |
| test_freeze_participant_in_memory_status_overwrite.py | F | keep | — | in-memory scenario state. |
| test_integrity_checkpoints.py | A | keep | A | checkpoint batch on `db_session` `:21`. |
| test_interact_actions_backend_p1.py | F | keep | **B** | drives `actions/clearing` `:1077` through `client` `:123` `[CLR]` (several cases monkeypatch the service, but not all). |
| test_invariants.py | A | keep | **B** | `execute_clearing_with_amount` on `db_session` `:618-619` `[CLR]`. |
| test_measure_swallowed_exceptions_instrument.py | E | keep | — | guard over programme 010's instrument. |
| test_metrics_unmatched_routes_path_label.py | G | keep | A | `client` `:19`. |
| test_net_balance_utils.py | A | keep | — | pure. |
| test_no_mandatory_readback_after_commit.py | E | keep | — | AST counter-check over eight sites. |
| test_p011_sse_event_declares_patches.py | C | keep | — | emitter model vs declaration. |
| **test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py** | D | **delete** | — | the module exists **to record that SQLite does not have the behaviour under test** (`:1-6`); own SQLite engine `:78`. With one dialect there is nothing left to compare — the behaviour itself is pinned by `test_p012_rt1_signed_amount_versus_stored_amount_postgres.py`. |
| test_p012_t1201_money_door_bounds.py | A | keep | — | `parse_money_amount` as a function. |
| test_p012_t1210_daily_limit_policy_door.py | A | keep | — | grammar vs capacity, pure. |
| test_p012_t1210_detector_union_default_tier.py | A | keep | **B** | `auto_clear` on `db_session` `:225` `[CLR]`; note the PG twin (`shared_edge_order`) deliberately carries only the order half, so this file is where the **outcome** pin lives and it must survive the move. |
| test_p012_t1211_engine_ladder.py | F | keep | — | fake `ClearingService` subclass `:71-74`, no DB. |
| test_p012_t1211_money_path_never_types_a_float.py | E | keep | — | AST guard on annotations. |
| test_p012_t1211_money_rendering_conformance.py | C | keep | — | shared JSON conformance table. |
| test_p013_t1302_graph_snapshot_include_completeness.py | C | keep | A | `client` `:7`. |
| test_p014_t1406_no_mutable_database_in_the_working_tree.py | E | **transfer** | — | the guard stays useful (user `.local-run/*.db` files are explicitly **not** deleted by 017), but it is written around `tests/scratch_db` `:34` and the SQLite file family; the allow-list and the message must be re-stated. |
| test_p015_b4_counterexample_marker_is_not_a_hiding_place.py | E | keep | — | guards the `b4_counterexample` marker's **absence**; unrelated to the `postgres` marker. |
| test_p015_b4_entries_and_money.py | D | keep | **B** | own engine `:829`, subprocess `:890`, second session `:249` — the «SQLite half» of B4 step 2, but what it asserts is the journal's recording rule, which is tier-independent. Moves onto a disposable database. |
| test_p015_b4_fixture_blocks_contain_only_fixture_setup.py | E | keep | — | AST guard over `debt_fixture_setup` blocks. |
| test_p015_b4_r4_fixture_migration_is_observably_equivalent.py | D | keep | **B** | `TestingSessionLocal as factory` `:522` with real commits. |
| test_p015_b4_transaction_contract.py | D | keep | **B** | own engine `:500`; the subject is root-transaction boundaries. |
| test_p015_b4_write_guard.py | D | keep | **B** | factory sessions `:179`; the guard fires on real flushes. |
| test_p015_b4_wrong_writer_is_recorded_faithfully.py | D | keep | **B** | clearing execution on factory sessions `:731` `[CLR]`. |
| test_p015_b4a_journal_mechanism.py | D | keep | **B** | private stand `:41`, own engine `:1091`. |
| test_p015_inject_transaction_ownership.py | D | keep | **B** | second session `:11`, owner lock `:12`, real serialization `:360` `[LOCK]`. |
| test_p015_p1_money_conflict_predicate.py | D | **transfer** | **B** | the predicate's counter-check is built from **real** errors of both backends (`:11`, stand `:43`); the SQLite rows of the truth table go, the Postgres rows and the anti-vacuum stay. |
| test_p015_p1_money_phase_replay.py | D | keep | — | fake session and fake money attempt `:1-6`. |
| test_p015_step5a_reconciliation.py | D | keep | **B** | factory sessions + `gather` `:229`. |
| test_p015_step5b_criterion_b.py | D | keep | **B** | clearing on factory sessions `:541` `[CLR]`. |
| **test_p015_step5b_sqlite_startup_refuses_a_pre_027_schema.py** | D | **delete** | — | the contract is «the backend refuses to **START** on a SQLite database created before migration 027» (`:1-4`); it drives `app.main` `:26` and builds the pre-027 file with `create_engine` `:36` + `[STC]`. Both the probe `[MAIN]` and the file path disappear. |
| test_p015_step5c_reaction_and_hold.py | A | keep | **B** | clearing execution `:777` and own engine `:972` `[CLR]`. |
| test_p015_t1514_simulator_must_not_requantise_stored_money.py | F | keep | A | `db_session` `:63`. |
| test_p015_t1522_payment_delta_drift_must_be_exact.py | A | keep | A | `db_session` `:49`. |
| test_p015_t1523_the_commit_landed_then_the_caller_failed.py | A | keep | A | cell 7 on one session `:61`. |
| test_p015_t1524_equivalent_deletion_keeps_obligations.py | A | **transfer** | A | it exists as «the **SQLite half**» (`:1-13`) whose stated value is that it tests the **model** (`create_all`) while the PG half tests the **migration**. If the tier stops building from `create_all` (017's template plan), that distinction collapses and the file is a duplicate — the surviving content is the model-vs-migration comparison, which belongs with the `t1530` decision in section 6. **See also section 3.** |
| **test_p015_t1525_a_busy_does_not_mask_and_does_not_promise.py** | D | **delete** | — | the subject is `sqlite_busy_error_name` and `SQLITE_BUSY` classification (`:1-10`), 76 `sqlite` mentions, own engine `:276`, stand `:46`. The function lives in `[STC]`. |
| **test_p015_t1525_every_sqlite_engine_has_transaction_control.py** | E | **delete** | — | a source guard that **every SQLite engine the repository builds** carries `install_sqlite_transaction_control` (`:1-6`). With no SQLite engines it is a guard over the empty set — precisely the vacuous guard §9 forbids. Its role is taken over by `test_p017_no_second_dialect.py`. (Note: this is the one `unit/` file carrying `@pytest.mark.postgres` today, `:19`.) |
| **test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py** | D | **delete** | — | «on SQLite a SAVEPOINT opened before the first write is its own transaction» (`:1-4`) — a statement about pysqlite's legacy mode. Its Postgres control is `test_p015_t1525_control_postgres.py`, kept above. |
| **test_p015_t1525_sqlite_stale_snapshot_is_retried.py** | D | **delete** | — | «a stale **SQLite** snapshot is a retryable conflict» (`:1-4`); the snapshot rule is the one `[STC]` installs. Also carries `?`-placeholder raw SQL `:231`, `:303` (section 5). The Postgres form of the same class is `test_payment_engine_uow_retry_postgres.py`. |
| **test_p015_t1525_sqlite_transaction_control_is_in_effect.py** | D | **delete** | — | asks the live connection whether `[STC]` is installed (`PRAGMA journal_mode` `:97`, `:123`); stand `:30`. |
| **test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py** | D | **delete** | — | «the **SQLite tier**: NaN is refused here today, and by a constraint about a DIFFERENT rule» (`:1-5`). That is a fact about SQLite's type affinity, not about the product; the real defect is held by the PG sibling `test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py`. Also `?`-placeholder SQL `:198` (section 5). |
| test_p015_t1528_the_guard_reads_what_the_statement_writes.py | D | keep | **B** | private stand `:68` (`new_sqlite_stand` → disposable database). |
| test_p015_t1529_the_envelope_identity_is_a_retryable_race.py | D | keep | — | pure truth table over the classifier `:7`. |
| test_p015_t1530_the_journal_reads_its_own_record_back.py | D | keep | **B** | stand `:61`; `?`-placeholder SQL `:435`, `:567` (section 5). |
| test_p015_t1531_the_verification_read_is_not_rewritable.py | D | keep | **B** | stand `:47`; `?`-placeholder SQL `:365` (section 5). |
| test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py | D | keep | **B** | stand `:51`. |
| test_p015_t1533_participant_deletion_keeps_obligations.py | A | **transfer** | A | same as `t1524` above — «the SQLite half» whose declared value is model-vs-migration (`:10-13`). Section 3. |
| test_p015_t1543_frozen_line_is_not_limit_zero.py | A | keep | A | `db_session` `:72`. |
| test_p015_t1544_inject_refuses_a_deactivated_equivalent.py | A | keep | A | **the ⚑ assert the spec promises to preserve** (`spec.md:61`): the statement recorder is attached to the engine `:72-74` and asserts `INSERT INTO DEBT_OPERATIONS == []` `:121`. I read it — it is dialect-agnostic and needs no rewrite beyond the uppercased statement prefixes; it stays in mode A. |
| test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py | A | keep | A | `db_session` `:47`. |
| test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py | A | keep | **B** | `auto_clear` `:194` and `execute_clearing_with_amount` `:232` on `db_session` `[CLR]`. |
| test_p1_clearing_run_perimeter.py | A | keep | **B** | `execute_clearing_with_amount` on `db_session` `:148`, `:281` `[CLR]`. |
| test_p1_payment_run_perimeter.py | A | keep | A | payment half — no clearing execution, one session `:71`. |
| test_p1_tick_storage_flush_marker.py | F | keep | — | fake session. |
| test_participant_race_stays_a_declared_conflict.py | A | keep | — | classifier over a synthetic error. |
| test_payment_cleanup_cancellation.py | A | keep | — | cancellation drain, no DB. |
| test_payment_db_error_classifier.py | D | keep | — | pure classifier truth table. |
| test_payment_delta_check.py | A | keep | A | `db_session` `:17`. |
| test_payment_engine_advisory_lock_key.py | D | keep | — | key derivation, pure. |
| test_payment_engine_advisory_locks_execute.py | D | keep | — | recorded SQL on a fake session `:65`. |
| test_payment_engine_retry_savepoint_nocommit.py | D | keep | — | fake failures. |
| test_payment_router_invalidate_cache.py | A | keep | — | pure. |
| test_payment_staged_post_commit.py | A | keep | A | `db_session` `:34`. |
| test_payment_timeouts.py | A | keep | A | `db_session` `:21`. |
| test_payments_2pc.py | A | keep | A | `db_session` `:19`. |
| test_post_tick_audit.py | F | keep | A | `db_session` `:32`. |
| **test_postgres_marker_fail_closed.py** | E | **delete** | — | the entire subject is the `postgres` **marker**'s fail-closed collection (`conftest.py:76-89`), driven by a child pytest `:13`. `T1702` removes the marker, so the guard guards nothing. Its replacement is the new mandatory-Postgres URL refusal. |
| **test_postgres_test_taxonomy.py** | E | **delete** | — | pins the `_postgres` suffix ↔ marker taxonomy and the CI job `postgres` selecting the marker tier. `T1701` abolishes that job and `T1702` the marker; the spec also decides **not** to rename the files, so the suffix rule becomes false as written. |
| test_pytest_selector_guard.py | E | keep | — | `scripts/validate_pytest_selectors.py`, unaffected. |
| test_quality_workflow_schedule.py | E | **transfer** | — | pins which jobs are schedule-only; `T1701` moves the backend gate to ubuntu with a Postgres service and abolishes the `postgres` job. `T1701` already names this file. |
| test_rate_limit_memory_bound.py | G | keep | — | in-memory limiter. |
| test_real_clearing_engine_partial_failure.py | F | keep | — | fake engine and sessions. |
| test_real_payments_ordered_journal.py | F | keep | — | fake executor. |
| test_real_runner_tick_nested_partial_failures.py | F | keep | — | `AsyncSessionLocal` monkeypatched `:160`. |
| test_real_tick_clearing_coordinator_adaptive.py | F | keep | — | spy coordinator. |
| test_real_tick_commit_cancellation.py | F | keep | — | fake session. |
| test_real_tick_orchestrator_pending_clearing.py | F | keep | — | fake. |
| test_real_tick_orchestrator_rollback_resolution.py | F | keep | — | `AsyncSessionLocal` monkeypatched `:297`. |
| test_real_tick_persistence_post_commit.py | F | keep | — | fake. |
| test_recovery_cleanup.py | A | keep | A | `db_session` `:64`. |
| test_request_id_middleware_validation.py | G | keep | — | middleware only. |
| test_routing_constraints_timeout_ms.py | A | keep | — | pure. |
| test_routing_reserved_and_policy.py | A | keep | — | pure graph. |
| test_run_full_stack_database_url_redaction.py | E | **transfer** | — | `test_database_display_value_handles_sqlite_without_native_arguments` is a SQLite-URL case (`sqlite` first at `:250`), and `T1704` rewrites the script's URL handling. |
| test_run_lifecycle_per_run_scenario_isolation.py | F | keep | — | deep-copy of scenario per run. |
| test_scenario_inject_topology.py | F | keep | A | inject ops on `db_session` `:9`. |
| test_settings_guardrails.py | E | **transfer** | — | reads the settings allow-list including the SQLite default (`sqlite` `:69`); `T1704` makes `DATABASE_URL` mandatory and Postgres-only. |
| test_simulator_actions_feature_flag.py | C | keep | A | the flag-off 403; the route string at `:51` is not an execution. |
| test_simulator_actions_serialization.py | C | keep | — | `from`/`to` alias on the wire. |
| test_simulator_actor_and_csrf.py | G | keep | — | deps only. |
| test_simulator_adaptive_clearing_effectiveness_synthetic.py | F | keep | — | synthetic signal sequences. |
| test_simulator_adaptive_clearing_policy.py | F | keep | — | policy state machine. |
| test_simulator_cookie_session.py | G | keep | — | cookie signing. |
| test_simulator_fixtures_clearing_plan_done_pair.py | F | keep | — | fixtures-mode pair. |
| test_simulator_metrics_bottlenecks_real_mode.py | F | keep | A | mostly a fake session `:121`; the `*_end_to_end` tests use the shared `db_session` `:5` and commit nothing of their own. |
| test_simulator_owner_isolation.py | F | keep | — | runtime registry. |
| test_simulator_real_amount_model.py | F | keep | — | amount cap. |
| test_simulator_real_clearing_throttle.py | F | keep | — | `AsyncSessionLocal` replaced by `_DummySessionCtx` `:97`. |
| test_simulator_real_events_stress.py | F | keep | — | multipliers. |
| test_simulator_real_flush_pending_storage.py | F | keep | — | dummy session `:79`. |
| test_simulator_real_planner_determinism.py | F | keep | — | planner. |
| test_simulator_rejection_codes.py | F | keep | — | mapping. |
| test_simulator_run_status_response_schema.py | C | keep | — | model. |
| test_simulator_scenario_allowlist_and_archives.py | F | keep | — | allow-list. |
| test_simulator_sse_replay.py | C | keep | — | in-memory replay. |
| test_simulator_sse_replay_atomic.py | C | keep | — | in-memory queue `:13`. |
| test_simulator_sse_trust_drift_decay_topology_patch.py | C | keep | — | payload shape. |
| test_simulator_storage_schema_contract.py | E | keep | — | column/index contract read off the model. |
| test_simulator_tx_failed_event_schema.py | C | keep | — | model. |
| test_simulator_tx_updated_amount_flyout_contract.py | C | keep | — | model. |
| test_simulator_write_tick_metrics_upsert.py | F | **transfer** | A | `test_sqlite_money_metrics_are_lossy_and_say_so_once_per_run` (`sqlite` `:90`) is a warning about SQLite's lossy Numeric; that warning path is removed with the dialect. The upsert assertions stay. |
| **test_sqlite_dev_schema_repair.py** | D | **delete** | — | imports `repair_stale_trustline_uniqueness` from `[INIT]` (`:30-34`) and rebuilds a pre-019 SQLite table with `create_engine` `:70` + `[STC]`. Both the script and the file format go. The invariant it protects (migration 019's partial index) is already held on Postgres by `test_p1_trustline_reopen_postgres.py`. |
| test_sqlite_test_engine_enforces_foreign_keys.py | E | **transfer** | A | the guard's content — *the test engine enforces what the application enforces* — survives and is worth keeping on Postgres (where FKs are always on, the useful form becomes «the tier's schema really carries the RESTRICTs»); its SQLite pragma mechanism (`:3`) does not. |
| test_sse_queue_full_policy.py | C | keep | — | in-memory. |
| test_sse_rate_limit.py | G | keep | — | in-memory. |
| test_static_diagnostics_policy.py | E | keep | — | Ruff/Black policy. |
| test_test_database_guard.py | E | **transfer** | — | the 017 verification plan names it as a counter-check that must stay green, and it must: but half its cases are SQLite URLs (`:12` onward, 27 mentions). The `geov0_test_*` rule survives, the SQLite-path cases are replaced by Postgres-URL cases. **Do not delete** — this is the guard that keeps `GEO_TEST_ALLOW_DB_RESET` honest. |
| test_tick_money_paths_carry_the_run_perimeter.py | E | keep | — | AST/spy over the tick's money call sites `:42`. |
| test_topology_changed_no_empty_payload.py | C | keep | — | payload shape. |
| test_trust_drift.py | F | keep | — | drift phases, no DB. |
| test_trust_drift_decay_does_not_break_trust_limits.py | F | keep | A | `db_session` `:19`. |
| test_trustline_audit_fail_closed.py | A | keep | A | `db_session` `:25`. |
| test_trustline_conflict_identity.py | A | **transfer** | — | pure classifier, but its counter-probe is built from a **literal SQLite INSERT with `?` placeholders** `:28-30`; asyncpg's message uses `$1…$n`, so a probe left as-is would test a message shape the product can no longer produce — a guard passing on nothing. Section 5. |
| test_trustline_signatures.py | A | keep | A | `db_session` `:18`. |
| test_trustline_timestamps.py | C | keep | — | wire timestamp model. |
| test_warmup_and_capacity.py | F | keep | — | ramp and capacity, no DB. |
| test_websocket_payment_received_event.py | G | **transfer** | **B** | runs the **real lifespan** (`TestClient(app)` `:37`) with `main_module.engine` re-pointed at the test engine by a fixture whose stated reason is the SQLite startup probes `[MAIN]` (`:19-22`); a uvicorn server runs in a thread `:107`. Mode B, and the fixture's justification must be rewritten when `[MAIN]` goes. |
| test_zero_debt_policy.py | A | keep | **B** | `execute_clearing` on `db_session` `:50` `[CLR]`. |

## 3. Summary in numbers

**Buckets** (my assignment; the spec's 2026-09-21 figures in brackets for comparison — they do not match and I did not force them to):

| bucket | files | spec 2026-09-21 |
|---|---|---|
| A protocol behaviour | **91** | 84 |
| B Postgres concurrency | **23** | 14 |
| C wire/contract | **40** | 42 |
| D mechanism tests | **40** | 59 |
| E guards | **26** | (304−277 = 27) |
| F simulator internals | **52** | 41 |
| G other | **27** | 37 |
| **total** | **299** | 304 |

The largest divergences are D (40 vs 59) and F (52 vs 41): I put the journal *mechanism* modules in D and the simulator *runtime* modules in F, and there are ~17 files (`test_real_tick_*`, `test_simulator_real_*`) that can honestly be read either way. That reclassification is cosmetic for 017 — none of them changes verdict or mode — but it will matter for 018, which sizes its deletion by «D-журнал, 17 файлов, 19,3 тыс. строк».

**Verdicts:** keep **260**, transfer **26**, delete **13**.

**Modes:** A **100**, B **90**, `—` **109**. *(Corrected 2026-09-21 from A 102 / B 88 — two files moved A → B, see «Correction» below.)*
Cross-tab: keep/A 94, keep/B 79, keep/— 87; transfer/A 6, transfer/B 11, transfer/— 9; delete/— 13.

**Marker cross-check — the key measurement.** `@pytest.mark.postgres` today: **53** files (`grep -rl "pytest.mark.postgres" tests/ --include=test_*.py | wc -l`). Files with `postgres` in the **name**: 55. My mode-B set: **90** (corrected).

**45 files I assign mode B carry no `postgres` marker today — i.e. they run on SQLite right now and cannot see what they were written for** (corrected 2026-09-21; the two added are `tests/integration/test_scenarios.py` and `tests/integration/test_clearing_max_depth_controls_long_cycles.py`)**:**

`tests/contract/test_p011_responses_conform_to_the_canon.py`, `tests/integration/test_audit_drift_delta_check_sse_integration.py`, `test_integrity_repairs_atomicity.py`, `test_p012_t1207_one_money_form_across_producers.py`, `test_p015_f0156_repairs_are_closed_by_default.py`, `test_p015_step5c_hold_through_the_tick_sqlite.py`, `test_p015_t1544_operator_stop_refuses_money.py`, `test_p015_t1544_operator_stop_through_the_tick_sqlite.py`, `test_payment_prepare_error_taxonomy.py`, `test_post_tick_audit_drift_runner_integration.py`, `test_simulator_adaptive_clearing_effectiveness_ab.py`, `test_simulator_adaptive_clearing_integration.py`, `test_simulator_real_snapshot_db_enrichment.py`, `test_simulator_sse_real_smoke.py`, `test_simulator_sse_trust_drift_decay_topology_patch.py`, `test_simulator_sse_tx_failed_timeout.py`, `test_simulator_super_smoke.py`, `tests/unit/test_admin_config_patch_atomicity.py`, `test_apply_flow_retry_on_stale.py`, `test_clearing_additional_cases.py`, `test_debt_optimistic_lock.py`, `test_interact_actions_backend_p1.py`, `test_invariants.py`, `test_p012_t1210_detector_union_default_tier.py`, `test_p015_b4_entries_and_money.py`, `test_p015_b4_r4_fixture_migration_is_observably_equivalent.py`, `test_p015_b4_transaction_contract.py`, `test_p015_b4_write_guard.py`, `test_p015_b4_wrong_writer_is_recorded_faithfully.py`, `test_p015_b4a_journal_mechanism.py`, `test_p015_inject_transaction_ownership.py`, `test_p015_p1_money_conflict_predicate.py`, `test_p015_step5a_reconciliation.py`, `test_p015_step5b_criterion_b.py`, `test_p015_step5c_reaction_and_hold.py`, `test_p015_t1528_the_guard_reads_what_the_statement_writes.py`, `test_p015_t1530_the_journal_reads_its_own_record_back.py`, `test_p015_t1531_the_verification_read_is_not_rewritable.py`, `test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py`, `test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py`, `test_p1_clearing_run_perimeter.py`, `test_websocket_payment_received_event.py`, `test_zero_debt_policy.py`.

Among these, **13 execute real clearing on the default tier today** (corrected 2026-09-21 from 11) (`test_clearing_additional_cases`, `test_invariants`, `test_zero_debt_policy`, `test_p012_t1210_detector_union_default_tier`, `test_p015_t1551_…`, `test_p1_clearing_run_perimeter`, `test_interact_actions_backend_p1`, `test_p015_step5b_criterion_b`, `test_p015_b4_wrong_writer_…`, `test_p015_step5c_reaction_and_hold`, `test_p015_t1544_operator_stop_refuses_money`, **`test_scenarios`**, **`test_clearing_max_depth_controls_long_cycles`**). On PostgreSQL every one of them hits `[CLR]` the moment it runs in mode A — **this is the single largest breakage stage 2 will meet, and it is not visible from the marker at all.**

**8 files carry the marker but do not need mode B** (mode A is sufficient — they read, they do not contend):
`test_p012_t1202_money_modules_read_precision_postgres.py`, `test_p012_t1211_negative_zero_cannot_come_back_from_the_ledger_postgres.py`, `test_p012_t1211_shared_edge_order_postgres.py`, `test_p012_t1212_declared_precision_exceeds_storage_scale_postgres.py`, `test_p015_t1534_the_migrated_schema_flag_is_true_postgres.py`, `test_p1_tick_session_ownership_postgres.py`, `test_prepare_locks_tx_id_fk_postgres.py`, `test_p015_t1525_every_sqlite_engine_has_transaction_control.py` (the last one is a delete). Moving these to mode A is free time back.

**Line volume of the delete set:** 13 files, **4 158** lines.

## 4. Files where I did not reach a decision I would defend

1. **`tests/unit/test_p015_t1524_equivalent_deletion_keeps_obligations.py` and `tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py`.** Both declare their reason for existing as *model (`create_all`) vs migration*, and `t1533:10-13` cites `T1540` that the two artefacts **have measurably diverged before**. 017's template plan builds the tier from `alembic upgrade head`, so the `create_all` half of that pair would no longer be exercised by any tier. I marked both `transfer`, but whether they become one merged catalogue comparison or simply die depends on the same decision as section 6. **Question that settles it: after stage 2, is `Base.metadata.create_all` still built and compared anywhere, or is the migrated schema the only artefact the suite ever sees?**
2. **`tests/integration/test_p012_t1207_one_money_form_across_producers.py`** — mode B, medium confidence. It runs real-mode producers (`:75`) and gathers (`:275`), but its `execute_clearing_with_amount` (`:556`) is a fake, so the `[CLR]` trigger does not apply; the B assignment rests on `[VIS]` alone. **Question: does any of its producers read a row that the runner's own session committed, or is the whole path in-process?** A five-minute read of `:250-330` decides it.
3. **`tests/unit/test_admin_config_patch_atomicity.py`** — mode B on the grounds that `asyncio.create_task` over `patch_admin_config` (`:194`) is two handlers overlapping on one session. If both tasks in fact await in strict sequence (the barrier at `:190-192` suggests they might), mode A holds and this is one less disposable database.
4. **`tests/integration/test_simulator_sse_real_smoke.py`, `test_simulator_sse_trust_drift_decay_topology_patch.py`, `test_simulator_sse_tx_failed_timeout.py`** — assigned B because a real run's seeder commits on its own session, which mode A can neither see nor roll back. Their asserts, though, are purely on the SSE bus. **Question: is the run's seeding committed before the first event these tests read — i.e. is the residue real?** If the answer is that the runner never commits at intensity 0, all three drop to A.
5. **`tests/unit/test_p015_t1525_control_postgres.py`** — I said `transfer`, not `keep`, because its identity is «the control for the SQLite module». If you would rather the assertion keep its current name and simply lose one paragraph of prose, `keep` is defensible. Nothing about the mode changes either way.
6. **The D/F boundary for the 17 `test_real_tick_*` / `test_simulator_real_*` modules.** I called them F; the 2026-09-21 inventory's D count implies some of them were D. Immaterial for 017 (all are `keep`/`—`), material for 018's deletion estimate.

I did **not** verify by execution any of the 88 mode-B assignments — no test was run. Every one rests on reading the module and the five anchors in section 1. The one class I could not check by reading at all is whether a given mode-A test's data set is large enough that SERIALIZABLE will produce `40001` between the test and a neighbour; that is a measurement, and it belongs to the tier-time measurement half of `T1706`.

## 5. Tests that need `CREATE DATABASE` / the `CREATEDB` privilege

Four modules already do this today, each with its own maintenance connection to the `postgres` database (asyncpg raw, because `CREATE DATABASE` cannot run in a transaction — `test_p015_t1530_delta_arithmetic_postgres.py:111-113`):

| file | create/drop | current behaviour when the privilege is missing |
|---|---|---|
| tests/integration/test_p015_step5a_reconciliation_postgres.py | `:202-203`, drop `:263` | `pytest.skip` `:206` |
| tests/integration/test_p015_step5b_criterion_b_postgres.py | `:164-165`, drop `:197` | `pytest.skip` `:168` |
| tests/integration/test_p015_step5c_hold_races_postgres.py | `:629-630`, drop `:700` | `pytest.skip` `:633` |
| tests/integration/test_p015_t1530_delta_arithmetic_postgres.py | `:260-261`, drop `:328` | `pytest.skip` `:264-269` |

**All four skip with a message that calls itself «an ABSENT measurement, not a passing one».** That is honest prose over a `skip` — but a `skip` still reports green. 017 makes `CREATEDB` a hard requirement of the tier (the template-and-clone provisioning needs it anyway), so **stage 2 should turn these four skips into hard failures**: once `CREATEDB` is a precondition of running at all, a skip here is exactly the false green §9's anti-vacuum rule is about. That is a one-line change per file and it should be named in `T1701`'s scope, not left to be noticed later.

Beyond these four, **every mode-B test acquires the same requirement** under the spec's design (`CREATE DATABASE … TEMPLATE` per task or per test) — 88 files by my count. That is the cost line `T1706`'s second half has to price.

## 6. Raw SQL with SQLite placeholders or SQLite-only syntax

**`?` (qmark) placeholders in raw SQL — 7 sites in 6 test modules.** asyncpg is `numeric_dollar`; a qmark read binds nothing on Postgres, and «binds nothing» returns no rows, which reads exactly like «the row is gone» (`test_p015_t1530_delta_arithmetic_postgres.py:21-24` says this in the tree already):

| site | what it is | note |
|---|---|---|
| `tests/integration/test_simulator_real_snapshot_db_enrichment.py:118-120` | `DELETE FROM debts WHERE equivalent_id = ? AND creditor_id = ? AND debtor_id = ?` through the driver, deliberately past the journal write guard | the example the spec already names; **must be respelled**, file is `transfer`/B |
| `tests/unit/test_p015_b4_entries_and_money.py:1086` | `… WHERE id = ?` | file is `keep`/B — respell |
| `tests/unit/test_p015_t1530_the_journal_reads_its_own_record_back.py:435, :567` | `… WHERE identity = ?` and `INSERT INTO e VALUES (?, ?, ?)` | file is `keep`/B — respell |
| `tests/unit/test_p015_t1531_the_verification_read_is_not_rewritable.py:365` | `… WHERE id IN (?)` | file is `keep`/B — respell |
| `tests/unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py:231, :303` | `DELETE FROM debts WHERE debtor_id = ?` | file is **delete** — no work |
| `tests/unit/test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py:198` | `INSERT INTO t1526_check_probe VALUES (?)` | file is **delete** — no work |

Plus one that is not executed but is just as load-bearing:

| `tests/unit/test_trustline_conflict_identity.py:28-30` | a literal SQLite `INSERT … VALUES (?, ?, ?, ?, ?, ?, ?)` used as the **counter-probe text** for the uniqueness classifier | the classifier is matched against driver messages; asyncpg's spelling differs, so left as-is the probe would pass while testing a message the product can no longer emit. `transfer`. |

And one documentation-only mention: `tests/unit/test_p015_b4_write_guard.py:1132` discusses `SET version=?` in prose — no change needed.

**SQLite-only SQL syntax — 12 executed sites in 9 modules** (`PRAGMA`, `sqlite_master`):

| site | file verdict |
|---|---|
| `tests/integration/test_audit_drift_delta_check_sse_integration.py:101-102` (`PRAGMA journal_mode`, `PRAGMA foreign_keys`) | transfer/B — the assert dies with the stand |
| `tests/integration/test_post_tick_audit_drift_runner_integration.py:108-109` | transfer/B — same |
| `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py:106-107` | transfer/B — same |
| `tests/integration/test_simulator_adaptive_clearing_integration.py:108-109` | transfer/B — same |
| `tests/integration/test_simulator_clearing_no_deadlock.py:109-110` | **delete** |
| `tests/integration/test_p015_p1_money_replay_sqlite.py:578-579` | **delete** |
| `tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py:97, :123` | **delete** |
| `tests/unit/test_p015_inject_transaction_ownership.py:469` (`PRAGMA busy_timeout`) | keep/B — **one line to remove**, the rest of the module is dialect-neutral |
| `tests/unit/test_p015_step5c_reaction_and_hold.py:995` (`PRAGMA table_info(equivalents)`) | keep/B — **must be rewritten** as an `information_schema` read; this is a live assertion inside a kept file |
| `tests/unit/test_alembic_postgres_only.py:74` (`sqlite_master`) | transfer |
| `tests/unit/test_p015_step5b_sqlite_startup_refuses_a_pre_027_schema.py:42` | **delete** |
| `tests/unit/test_p015_t1525_a_busy_does_not_mask_and_does_not_promise.py:136` | **delete** |
| `tests/unit/test_sqlite_dev_schema_repair.py:89, :98` | **delete** |

Also in the helper modules, which stage 2 owns: `tests/scratch_db.py:67-70` (the three connect pragmas, and `install_test_sqlite_pragmas` is explicitly on the stage-3 deletion list), `tests/p015_b4a_stand.py:267-268` (same pragmas inside `new_sqlite_stand`, the stand 8 modules share), and `tests/conftest.py:167-177`, `:302-311`, `:375-395`.

**The actual respell work is therefore small and bounded: 5 qmark sites in kept files, 1 literal probe, and 2 executed `PRAGMA` sites in kept files** — everything else dies with its module. The large item is not SQL syntax; it is the 88 mode-B fixtures and the 11 clearing-executing files in section 3.

## 7. Decision requested — `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py:233-244, :276, :294-312`

**What the test actually does** (read in full, `:1-31` and `:232-330`). It creates **two scratch databases** on the same server, builds one with `Base.metadata.create_all` (`:276-280`) and the other with `ALEMBIC_VERSION_BOOTSTRAP` + `alembic upgrade head` (`:283-293`), then asserts four things about `debt_journal_entries` (`:294-312`):

1. both paths produced **some** CHECK constraints at all — an explicit non-vacuity assert (`:299-301`);
2. `chk_debt_journal_entries_delta_arithmetic` exists in **both** (`:302-307`);
3. the two paths produced the **identical stored predicate text** (`:308-312`);
4. the **set of CHECK constraint names** agrees between the two (`:313-317`);

and then, on both databases, that an entry saying «10 → 11, delta 2» is refused with SQLSTATE `23514` (`:319-327`) — i.e. the catalogue does not merely contain the constraint, it bites. The module states its own mutation: remove `op.create_check_constraint` from migration `024_debt_journal_delta` and the migrated half goes red while the metadata half stays green (`:246-250`).

**So the contract under test is not «`create_all` works».** It is **«the model and the migrations are the same artefact»** — the `T1540` divergence, stated as an executable comparison rather than as prose. Its value does not come from SQLite and does not come from the default tier; it comes from having two independently built catalogues on one server.

**Recommendation: keep it, as a named diagnostic exception, and make the exception explicit in three places.** Reasons:

- **The premise for removing it is false.** «No `create_all` in tests» is a rule about how the *tier* builds its schema, to stop a stale or metadata-built database masquerading as a migrated one (`conftest.py:194-230` records exactly that incident: stamped `022` with the tree at `024`, 87 constraints against 90). This module builds `create_all` into a **throwaway database it created for the purpose and drops in `finally`** (`:326-330`) — it never touches the tier's schema and cannot contaminate it. The rule's harm does not arise here.
- **Removing it deletes the only executable check of an invariant with a measured history of breaking.** `T1540` is on record that the two artefacts have diverged; `t1533:10-13` leans on the same distinction. After 017 the tier will be built by the migrations only, which means the model path stops being covered **anywhere** — the divergence would become silent rather than absent. That is a strictly worse position than today.
- **017 makes it cheaper, not dearer.** The module already does its own `CREATE DATABASE`; once `CREATEDB` is a hard precondition of the tier, its `pytest.skip` (`:264-269`) becomes a hard failure and the measurement stops being optional. That is an improvement this programme delivers for free.
- **What to change, so the exception is a decision and not an oversight:** (a) the `pytest.skip` at `:264-269` becomes a failure (§4 of this report); (b) the guard `test_p017_no_second_dialect.py` — or whichever guard stage 2 writes for «no `create_all` in tests» — carries this file in an explicit, commented allow-list of **one**, with the reason and the date, so the next reader does not «clean it up»; (c) the 017 Changelog records the exception with `T1540` as its origin.

**The alternative and why I do not recommend it.** Dropping the contract means either accepting that model-vs-migration divergence is henceforth undetected, or replacing it with a startup/CI check that diffs `Base.metadata` against the migrated catalogue — which is a **new mechanism**, and by §19.1 that needs the six questions and a programme of its own. For a programme whose stated scope is «removes a dialect, changes no money rule», introducing a new schema-comparison mechanism is the wrong trade; keeping one 90-line test that already works is the cheap answer.

**One consequence you should decide at the same time**, because it hangs off the same question: if the `create_all` path survives only inside this one module, then `tests/unit/test_p015_t1524_…` and `tests/unit/test_p015_t1533_…` lose the justification they state for existing (they are «the model half»), and their content should be folded into the PostgreSQL halves rather than migrated as separate files. If instead you decide the model path must keep functional coverage, those two stay as they are and move to mode A unchanged. I have marked both `transfer` pending that call — it is the one item in this inventory where my verdict is contingent on yours.

---

## 8. Baseline дефолтного тира на SQLite — половина «до» для бюджета

Бюджет времени тира подписывает владелец, и он — условие входа в стадию 3. Подписывать «после» без «до» не по чему, а «до» измеримо сегодня и ни от чего не зависит. Замер снят 2026-09-21 на `main` `4310603`, дерево чистое.

**Команда — дословно, оба раза одна и та же:**

```powershell
.\scripts\verify_local.ps1 -TaskSlug p017_baseline_cold -BackendOnly
```

**Условия.** Windows 11, локальный SQLite-файл `./.local-run/test-runs/p017_baseline_cold/test.db` (страж БД подтвердил `backend=sqlite` в обоих прогонах). Marker-выражение — дефолтное `not slow and not postgres` (`scripts/verify_local.ps1:133-135`), то есть ровно то, что идёт на обычном PR. UI-шаги выключены `-BackendOnly`. Второй прогон запущен тем же `-TaskSlug`, поэтому переиспользует `cache` и `basetemp` под `.local-run/test-runs/p017_baseline_cold/` — это и есть «тёплый». WSL не участвует: база — локальный файл.

| Прогон | Собрано | Результат | pytest | Стена | exit |
|---|---|---|---|---|---|
| холодный | 2447 + 3 skipped, **283 deselected** | passed | **268.42 s** | 272.0 s | `0` |
| тёплый | то же | passed | **268.54 s** | 271.5 s | `0` |

**Два вывода, и оба нужны, чтобы числом не воспользовались неправильно.**

1. **Прогрева нет: 268.42 против 268.54 — разница в пределах шума.** Тир упирается в вычисление, а не в построение схемы. Отсюда следует, что вилка 82 с против 669 с, записанная в 015 (`T1523`) и названная в спеке 017 необъяснённой, прогревом действительно **не объясняется** — на SQLite прогрева не существует вовсе. Она принадлежит Postgres и состоянию WSL, и стадия 3 не начинается, пока она не объяснена, как спека и требует.

2. **283 deselected — baseline покрывает 2447 тестов из 2730.** Это тесты с маркерами `slow` и `postgres`. После стадии 2 маркер `postgres` исчезает и его тесты входят в обязательный тир. Поэтому **268 s и будущее число Postgres-тира несравнимы напрямую**: они меряют разные популяции. Честное сравнение требует либо замера Postgres-тира на той же популяции, либо замера SQLite-тира на расширенной — второе невозможно, потому что именно эти тесты на SQLite и не идут. Это ограничение надо назвать владельцу вместе с числом, иначе бюджет будет подписан против неверной базы.

**Чего этот замер не доказывает.** Одна машина, один запуск каждого вида, без повторов и без разброса. Время CI (`windows-latest`) здесь не измерено и от локального отличается. Числа годятся как порядок величины для «до», а не как порог.

---

## 9. Correction 2026-09-21 — два файла, и механизм ошибки шире, чем один промах

Первая версия этого инвентаря (`af82788`) назначила режим `A` двум файлам, которые исполняют настоящий клиринг на общей сессии. На Postgres в режиме A оба получают безусловный отказ `[CLR]`.

| файл | было | стало | где исполняется |
|---|---|---|---|
| `tests/integration/test_scenarios.py` | A | **B** | `test_clearing` `:562` → `POST /api/v1/clearing/auto` `:709`, ассерт `cleared_cycles >= 1` `:711` |
| `tests/integration/test_clearing_max_depth_controls_long_cycles.py` | A | **B** | `POST /api/v1/clearing/auto` с `max_depth=5` `:96-102`, ассерт `cleared_cycles >= 1` `:103` |

Первую нашла соседняя сессия при вычитке; вторую — адверсарный скан, запущенный **потому, что найденная ошибка была формой, а не опечаткой**. Обе перепроверены по коду до публикации правки: цепочка `client` → `tests/conftest.py:436-437` (`yield db_session`) → `app/api/v1/clearing.py:51` (`ClearingService(db)`) открыта и прочитана целиком.

**Механизм ошибки оказался шире заявленного.** Сперва он читался как «режим выведен по первому тесту с `db_session`, а клиринг лежал ниже по файлу» — так выглядел `test_scenarios.py`. Второй случай показал настоящую форму: в `test_clearing_max_depth_controls_long_cycles.py` токен `ClearingService` встречается **ноль раз**, и записанное обоснование — «no `ClearingService` construction anywhere in the file» — **буквально истинно**. Сервис достигается через HTTP-роут. То есть режим решался **поиском токена по тексту файла, а не по достижимому call-графу**, и верное обоснование прикрывало неверный вывод.

Это ровно §15 AGENTS.md: «проверяйте обоснование, а не только вывод». Здесь оно сработало в обратную сторону — обоснование было истинным, а вывод из него не следовал.

**Что даёт уверенность, что класс закрыт.** Скан строил популяцию независимо (точный grep по семи формам вызова → 58 файлов, широкий grep по слову `clearing` → 111, разница 53 разобрана поштучно, 7 потребовали открытия) и разбирал 65 кандидатов: 2 расхождения, 24 подтверждения, 39 исключений. Отдельно проверены и **названы безопасными** формы, которые легко принять за пропуск: клиринг реального тика (`app/core/simulator/real_clearing_engine.py:154` берёт `AsyncSessionLocal`, а фикстура подменяет его на `TestingSessionLocal`, привязанный к **engine**, `tests/conftest.py:181-182` — connection-bound сессия туда не попадает), `clearing-once` (SSE-эмиттер без сессии), обе детекции `GET /clearing/cycles` (отказ живёт за `execute_clearing_with_amount`), все 13 не-тестовых хелперов под `tests/` и все 25 перекрёстных импортов между тестовыми модулями (импортируются только сидеры и фикстуры).

**Уточнение самого предиката, которое стоит дороже обеих находок.** Отказа мало достичь вызовом: `app/core/clearing/service.py:1523` уходит в ранний возврат при `or not cycle`. Значит «файл ходит в `/clearing/auto`» **не доказывает** режим B — нужен непустой цикл. В обеих находках ассерт `cleared_cycles >= 1` есть; в `tests/unit/test_interact_actions_backend_p1.py:1077` его нет, и там различие оказалось решающим. Третий потребитель этой же формы, `test_p015_t1544_operator_stop_refuses_money.py:337,345`, был помечен `B` верно с самого начала — то есть форма давала и правильные ответы, что и делало её незаметной.

**Граница этой правки.** Скан проверял **только** предикат `[CLR]`. Режимы, выставленные по `[VIS]`, `[PROC]`, `[LOCK]` и «собственный engine», он не оспаривал и не подтверждал. Ни одно назначение, включая исправленные, не проверено исполнением — тесты по-прежнему не запускались.
