# Стадия 2a — каталог дефолтного тира на PostgreSQL: что показало исполнение

- **Date:** 2026-09-23
- **Программа:** [017 — PostgreSQL как единственный движок](spec.md), задача `T1702`, слайс 2a
- **Назначение:** превратить предсказание инвентаря [`t1706-inventory.md`](t1706-inventory.md) («ни одно из назначений режима B не проверено исполнением») в измерение. Слайс 2b переводит тесты **по этому каталогу**; ассерты здесь не тронуты ни в одном тесте.
- **Status authority:** метка описательная. Каталог — измерение на одной машине в один день, а не решение; решения по каждому файлу принимает 2b.
- **Снято на:** ветка `claude/017-stage2a-catalogue` от `ea14726`, с инфраструктурой этого слайса (создание базы тира, режим B, таймаут на тест, регистратор). Windows 11, PostgreSQL 16 переносной, `127.0.0.1:5432`, база тира `geov0_test_p017s2a`.

## Граница доказательства — прочесть до использования таблицы

1. **Измерен дефолтный тир** (`not slow and not postgres`, 2705 тестов). 46 файлов, которым инвентарь назначил режим B и которые несут маркер `postgres`, здесь не мерялись: их меряет маркерный тир, и он зелёный (`302 passed, 1 xfailed`).
2. **Двадцать один файл дефолтного тира строит собственный SQLite-движок** (целиком или частично) (`tests/scratch_db.py`, `tests/p015_b4a_stand.py::new_sqlite_stand`, импорт стенда `t1544…_sqlite`). Их тесты на «Postgres-прогоне» идут **на SQLite**, и их зелёный — это зелёный SQLite, а не измерение на Postgres. Поименно — раздел 6.
3. **Класс «остаток» (RESIDUE) зависит от формы прогона.** Непрерывный прогон без перезапуска дал на одного пострадавшего больше (`test_trustline_signatures.py::test_trustline_create_rejects_invalid_signature`, `participants_pid_key (pid)=(bob)`), чем прогон с перезапуском после зависания. Число 52 — для прогона, описанного ниже; оно не константа.
4. **Режим B через переключатель слеп для файлов, которые импортируют `TestingSessionLocal` или `engine` из `tests.conftest`**: их вторая сессия остаётся на базе тира, а не на клоне. Для таких строк исход в колонке «switch B» помечен `(blind)` и **не** отвечает на вопрос «чинит ли режим B»; ответит только перевод второй сессии на `committed_database.sessionmaker` в 2b.
5. **Классы подтверждены чтением трейсбека**, а не угаданы: для каждой строки взята первая строка `E …` и, где причина в HTTP 500, — событие из захваченного лога. Где класс выведен по сигнатуре лога, а не по прочитанному стеку, это сказано в разделе 3.

## 1. Как это воспроизвести

Инструменты — в дереве: `tests/stage2_catalogue_recorder.py` (пишет каждое событие теста в JSONL сразу, после зависания продолжает с места), переключатель `GEO_TEST_FIXTURE_MODE=B` в `tests/conftest.py` (только для измерения; ни один гейт его не ставит).

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_p017s2a"
$env:GEO_TEST_ALLOW_DB_RESET = "1"; $env:GEO_TEST_USE_MIGRATED_SCHEMA = "1"
$env:GEO_CATALOGUE_JSONL = "<абсолютный путь>\catA.jsonl"
$env:PYTEST_ADDOPTS = "-p tests.stage2_catalogue_recorder --timeout=180"
# повторять, пока в JSONL нет строки "sessionfinish": каждый перезапуск отвечает на оставшееся
.\scripts\verify_local.ps1 -Python D:\Work\Projects\GEOv0\.venv\Scripts\python.exe -TaskSlug p017s2a -BackendOnly
```

Проходы, из которых собран каталог:

| проход | что | прогонов | стена | итог |
|---|---|---|---|---|
| A | весь дефолтный тир, режим A (как есть) | 2 (перезапуск после одного зависания) | 683 с + 134 с, из них 180 с — ожидание таймаута | см. раздел 2 |
| изоляция | каждый из 62 падающих файлов отдельным прогоном, свежая схема | 62 (+1 перезапуск) | около 10 мин | отделяет остаток от собственной причины |
| B-switch | весь дефолтный тир, `GEO_TEST_FIXTURE_MODE=B`, slug `p017s2ab` | 1 | 1368 с | `2642 passed, 59 failed, 4 skipped`, **ни одного зависания** |
| непрерывный A | весь тир одним процессом, зависание №1 исключено `--deselect` | 1 | 814 с | `2511 passed, 175 failed, 14 error, 4 skipped`; второго зависания нет |

## 2. Числа, которые обязаны сойтись

**Собрано 2705** (столько же, сколько на SQLite: `2702 passed + 3 skipped`, 307 deselected; расхождения в составе нет).

| исход на Postgres, режим A | тестов |
|---|---|
| PASSED | 2512 |
| FAILED | 174 |
| ERROR | 14 |
| TIMEOUT | 1 |
| SKIPPED | 4 |
| **сумма** | **2705** |

Четвёртый skip против трёх на SQLite — `test_simulator_write_tick_metrics_upsert.py::test_sqlite_money_metrics_are_lossy_and_say_so_once_per_run` («This pins SQLite-specific behaviour»): на Postgres это **отсутствующее измерение**, а не пройденное.

Регистратор насчитал 2706 ответов: у `test_p012_t1201_money_door_bounds.py::test_is_storable_money_refuses_what_the_column_would_change` один параметр получает id с адресом объекта (`<object object at 0x…>`), поэтому после перезапуска тот же тест собрался под другим nodeid и прошёл второй раз. Дубль вычтен; это находка о тесте (нестабильный nodeid ломает `--lf`, `--deselect` и любую сверку по имени), не о Postgres.

**Сверка с инвентарём по тестам** (файлы дефолтного тира, режим из инвентаря):

| режим по инвентарю | прошло в A | упало по собственной причине | упало от остатка |
|---|---|---|---|
| B (44 файла в тире) | **481** | 77 | 30 |
| A (93 файла) | 367 | **52** | 22 |
| — без БД (109 файлов) | 1418 | 8 | 0 |
| нет в инвентаре (9 файлов `test_p017_*`, появились после него) | 250 | 0 | 0 |

**481 тест в файлах режима B проходит в режиме A.** По файлам: 21 файл режима B проходит целиком, но 12 из них идут на собственном SQLite (раздел 6; `integration/test_p015_step5c_hold_through_the_tick_sqlite.py` — через стенд, импортированный из SQLite-модуля) — на Postgres в режиме A целиком проходят **9**: `contract/test_p011_responses_conform_to_the_canon.py`, `integration/test_p012_t1207_one_money_form_across_producers.py`, `integration/test_simulator_sse_real_smoke.py`, `integration/test_simulator_sse_tx_failed_timeout.py`, `unit/test_admin_config_patch_atomicity.py`, `unit/test_p015_b4_r4_fixture_migration_is_observably_equivalent.py`, `unit/test_p015_b4_write_guard.py`, `unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py`, `unit/test_websocket_payment_received_event.py`. **Проход в A необходим, но не достаточен**: раздел 4.3 показывает тест, который в A проходит по неверной причине.

**52 теста в файлах режима A падают по собственной причине** — это главное расхождение, раздел 4.1.

## 3. Классы причин

| класс | тестов | файлов | что это, по трейсбеку | B-switch |
|---|---|---|---|---|
| **FIXA** | 62 (48 F + 14 E) | 24 | Сама savepoint-фикстура режима A ломает платёжный путь приложения. Исключение `InvalidRequestError: Can't operate on closed transaction inside context manager` поднимается из **`tests/conftest.py` `_restart_savepoint`** (слушатель `after_transaction_end`, который перезапускает SAVEPOINT) внутри собственного `session.begin_nested()` приложения — `app/core/payments/engine.py:761` (`_run_uow_with_retry`) и `:1791` (`_apply_flow`). Через HTTP это 500 с логом `event=payment.commit_failed … error_type=InvalidRequestError` и `event=payment.commit_abort_failed … error_type=DebtOperationIncomplete`; один такой 500 (`test_payments_idempotency.py::test_payments_tx_id_returns_same_result`) прослежен до кадра `_restart_savepoint` отладочной обёрткой вокруг `PaymentEngine.commit`, остальные HTTP-500 отнесены к классу **по той же сигнатуре лога**, без собственного стека. Вариант того же: `This session is in 'prepared' state` (`test_scenarios.py::test_clearing` — платёж перед клирингом уже упал). Все 14 ERROR — это фикстуры `money_scenario` двух модулей `test_p011_*_on_the_wire.py`, упавшие на том же платеже. **На SQLite этот путь не исполнялся никогда**: там `db_session` — обычная сессия без savepoint и без слушателя (`tests/conftest.py`, ветка `_is_sqlite`). | 62 / 62 PASSED |
| **RESIDUE** | 52 | 17 | Остаток чужих коммитов: в полном прогоне падает (`UniqueViolationError` на `participants_pid_key`, `equivalents_code_key`; «лишние» строки в счётчиках), в изоляции проходит. На Postgres в режиме A нет пер-тестового сброса, который на SQLite делает `DELETE FROM` по всем таблицам, а коммиты через `TestingSessionLocal()` и через `AsyncSessionLocal` приложения (подменённый на `TestingSessionLocal`) — настоящие и переживают тест. **Загрязнители поимённо не установлены.** | 52 / 52 PASSED |
| **VIS** | 24 | 7 | Вторая сессия теста (`TestingSessionLocal()`, `take_baseline` на своей сессии) не видит незакоммиченного режима A: `NoResultFound`, `assert None == Decimal('10.00')`, `ForeignKeyViolationError … is not present in table "equivalents"`, «stand: no debts were seeded», «the debt behind a frozen line was destroyed». | 24 FAILED, **все blind** — не измерено |
| **CLR** | 20 | 9 | Клиринг отвергает сессию, привязанную к соединению: лог `event=clearing.external_connection_bind_unsupported`, `GeoException` с причиной «PostgreSQL clearing requires an engine-bound AsyncSession» (`app/core/clearing/service.py:1530-1539`); сюда же `test_execute_clearing_rollback_failure_keeps_original_error_sanitized` — его подменённый rollback срабатывает внутри `_rollback_before_interlock` ветки отказа (`service.py:1536`). | 16 PASSED, 4 FAILED (раздел 4.3) |
| **DIALECT-SQL** | 11 | 3 | SQL/драйвер, написанные под sqlite3: qmark-плейсхолдер `WHERE id = ?` → `syntax error at end of input`; строковая дата в параметре → asyncpg `expected a datetime.date or datetime.datetime instance, got 'str'`; API сырого sqlite3-соединения `'Connection' object has no attribute 'in_transaction'`. | 11 FAILED (blind) |
| **SQLITE-MECH** | 10 | 4 | Тест механизма SQLite: ассерт `dialect.name == "sqlite"`, `PRAGMA`, `SQLITE_BUSY_SNAPSHOT`, «without sqlite transaction control there is no verdict». | 10 FAILED |
| **SCHEMA-CHECK** | 3 | 2 | Тест сеет строку, которую мигрированная схема запрещает CHECK-ограничением, а `create_all` на SQLite — нет: `chk_equivalents_code_format`, `chk_debt_journal_entries_delta_arithmetic`. | 3 FAILED |
| **40001** | 3 | 3 | `SerializationError: could not serialize access due to read/write dependencies among transactions` / `due to concurrent update` между двумя соединениями одного теста. | 1 PASSED, 2 FAILED (оба blind) |
| **DIALECT-PREMISE** | 2 | 1 | Посылка стенда о том, что «эта база» может хранить: `stand: this database stored 100000000000.00000001 unchanged`, `…now stores 99999999999.99999999 exactly`. | 2 FAILED |
| **DIALECT-MESSAGE** | 1 | 1 | Ассерт на текст ошибки SQLite: `assert 'FOREIGN KEY' in …` — у Postgres «foreign key constraint». | FAILED |
| **HANG** | 1 | 1 | Ожидание строковой блокировки между двумя соединениями одного теста, раздел 5. | FAILED (blind) |
| **сумма** | **189** | 62 файла | = 174 + 14 + 1 | |

Два класса из ожидаемых брифом **не встретились вовсе**: ожидание **advisory**-лока между соединениями (единственное зависание — строковая блокировка, раздел 5) и `PRAGMA`/`sqlite_master` в сохраняемых файлах как причина падения (обе `PRAGMA` из инвентаря в сохраняемых файлах — `inject_transaction_ownership.py:469` и `step5c_reaction_and_hold.py:995` — ни одного теста не уронили). Два класса **не были предсказаны никем**: FIXA и RESIDUE.

## 4. Расхождения с инвентарём — там, где чтение ошиблось

### 4.1 Режим A назначен, а фикстура режима A сама ломает тест (FIXA) — 24 файла

Инвентарь исходил из того, что `db_session` в режиме A — рабочая фикстура, и решал только «хватает ли её». Исполнение показало, что на Postgres она **не выдерживает платёжный путь приложения**: 62 теста в 24 файлах, из них 20 файлов инвентарь назначил режимом A. Под переключателем B все 62 проходят. Вывод для 2b: либо эти файлы идут в режим B, либо чинится слушатель `_restart_savepoint` (это правка фикстуры режима A, которую бриф этого слайса запретил трогать, — решение за 2b). Файлы: `integration/test_admin_feature_flags_multipath.py`, `test_admin_routing_max_paths.py`, `test_daily_limit_not_enforced.py`, `test_p011_admin_money_is_a_decimal_string_on_the_wire.py`, `test_p011_money_is_a_decimal_string_on_the_wire.py`, `test_p012_t1201_money_door_at_the_entrances.py`, `test_p015_t1523_replay_after_a_hold_or_an_abort.py`, `test_payment_prepare_capacity_policy.py`, `test_payments_constraints_avoid.py`, `test_payments_idempotency.py`, `test_payments_list_filters.py`, `test_payments_multipath.py`, `test_trustline_negative_constraints.py`, `unit/test_admin_abort_tx.py`, `test_p015_t1523_the_commit_landed_then_the_caller_failed.py`, `test_p015_t1543_frozen_line_is_not_limit_zero.py`, `test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py`, `test_p1_payment_run_perimeter.py`, `test_payment_staged_post_commit.py`, `test_payments_2pc.py` (режим A); и режима B — `test_p015_t1544_operator_stop_refuses_money.py`, `test_payment_prepare_error_taxonomy.py`, `test_scenarios.py`, `unit/test_invariants.py` (по одному тесту).

**Селекторы спеки, раздел 3 «обязаны остаться зелёными»:** там перечислены `test_payments_idempotency.py` и `test_payments_2pc.py`, которым инвентарь назначил режим A, — оба красные по FIXA. Это тот же дефект спеки, что был у `test_invariants.py`: назван режим, в котором селектор зелёным остаться не может.

### 4.2 Предсказание `[CLR]` для 13 файлов

| файл | инвентарь | исполнение |
|---|---|---|
| `unit/test_clearing_additional_cases.py`, `unit/test_invariants.py`, `unit/test_zero_debt_policy.py`, `unit/test_p012_t1210_detector_union_default_tier.py`, `unit/test_p015_t1551_…`, `unit/test_p1_clearing_run_perimeter.py`, `integration/test_p015_t1544_operator_stop_refuses_money.py`, `integration/test_clearing_max_depth_controls_long_cycles.py` | CLR | **подтверждено** (лог `external_connection_bind_unsupported`) |
| `unit/test_interact_actions_backend_p1.py` | CLR | подтверждено на одном тесте (`test_action_clearing_real_total_cleared_amount_is_actual_not_precalc`), и только в изоляции: в полном прогоне его и ещё 29 тестов файла закрывает остаток |
| `integration/test_scenarios.py` | CLR | **не наблюдалось**: `test_clearing` падает раньше, на платеже (FIXA); в B проходит |
| `unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` | CLR | **опровергнуто**: файл целиком проходит в A. Собственный якорь инвентаря — «clearing execution on factory sessions :731» — и есть опровержение: `TestingSessionLocal()` привязан к engine, отказ `[CLR]` на него не действует |
| `unit/test_p015_step5b_criterion_b.py` | CLR | **опровергнуто** по той же причине (factory sessions `:541`); файл падает, но по VIS (`take_baseline` на своей сессии не видит эквивалент режима A) |
| `unit/test_p015_step5c_reaction_and_hold.py` | CLR | **не наблюдалось**: три падения файла — VIS, DIALECT-MESSAGE, 40001 |

### 4.3 Проход в режиме A по неверной причине, и то, что режим B меняет в самих тестах

- **`unit/test_clearing_additional_cases.py::test_execute_clearing_commit_failure_rolls_back_without_visible_effects` в изоляции ПРОХОДИТ в режиме A — ложно.** Тест подменяет сбой коммита и ждёт `GeoException`; на Postgres в A `GeoException` бросает отказ `[CLR]` раньше подменённого коммита, и `pytest.raises` его принимает. В B, где отказа нет, тот же тест: `Failed: DID NOT RAISE <class 'app.utils.exceptions.GeoException'>`. То есть в A этот тест зелёный, ничего не проверив. Рядом `test_execute_clearing_nonpositive_defensive_skip_rolls_back` в B: `assert Decimal('10.00000000') is None`.
- **`unit/test_invariants.py::test_clearing_writes_integrity_audit_log_on_success`, `unit/test_zero_debt_policy.py::test_clearing_deletes_zero_debts`** в B: `MissingGreenlet` — тест обращается к ORM-атрибуту после настоящего коммита клиринга. Режим B чинит `[CLR]`, но открывает ошибку в коде теста.
- **`unit/test_payment_timeouts.py::test_payment_commit_timeout_returns_committed_when_tx_already_committed`** — единственный тест, который в A проходит, а в B падает: `abort must not be called for an already COMMITTED tx`.

### 4.4 Файлы, где инвентарь назначил «keep», а часть тестов — механизм SQLite или SQLite-диалект (не предсказано)

| файл (инвентарь) | тесты | класс |
|---|---|---|
| `unit/test_p015_inject_transaction_ownership.py` (keep/B, «one line to remove») | `test_a_stale_snapshot_is_transient_and_the_inject_lands_exactly_once`, `test_a_stale_snapshot_on_both_attempts_leaves_the_event_pending` | SQLITE-MECH: `assert _test_engine.dialect.name == "sqlite"` — два теста механизма SQLite внутри сохраняемого файла |
| `unit/test_p015_step5a_reconciliation.py` (keep/B) | `test_step5a_without_sqlite_transaction_control_there_is_no_verdict` | SQLITE-MECH |
| `unit/test_p015_step5a_reconciliation.py` (keep/B) | `test_step5a_a_journal_row_contradicting_its_own_arithmetic_is_failed_and_dominates` | SCHEMA-CHECK: подделанную строку запрещает `chk_debt_journal_entries_delta_arithmetic` мигрированной схемы |
| `unit/test_p015_b4_entries_and_money.py` (keep/B) | `c18` — qmark `:1086` | DIALECT-SQL, **предсказано** |
| то же | семь тестов `c19` | DIALECT-SQL, **не предсказано**: строковая дата `'2026-09-12T00:00:00+00:00'` в параметре raw SQL |
| то же | `c12[one atom past the exact domain]`, `test_condition4_…` | DIALECT-PREMISE, не предсказано |
| `unit/test_p015_b4_transaction_contract.py` (keep/B) | два теста `c10` | DIALECT-SQL: API сырого sqlite3-соединения, не предсказано |
| `unit/test_p015_step5c_reaction_and_hold.py` (keep/B) | `test_step5c_the_evidence_of_a_hold_cannot_be_deleted_while_held` | DIALECT-MESSAGE, не предсказано |
| `integration/test_equivalent_writer_and_legacy_reads.py` (keep/**A**) | оба падающих теста | SCHEMA-CHECK: тест сеет «наследные» коды эквивалента, которые мигрированная схема запрещает `chk_equivalents_code_format`. Это ровно расхождение `create_all` и миграций, записанное в `tests/conftest.py` (87 против 90 ограничений): тест живёт только на схеме без этого CHECK |
| `integration/test_simulator_real_snapshot_db_enrichment.py` (transfer/B, qmark `:118-120` предсказан) | 1 | в A сначала 40001; предсказанный qmark виден в B: `syntax error at or near "AND"` |

### 4.5 Ответы на открытые вопросы раздела 4 инвентаря

- **п. 2** `test_p012_t1207_one_money_form_across_producers.py` — проходит в A целиком.
- **п. 3** `test_admin_config_patch_atomicity.py` — проходит в A целиком.
- **п. 4** `test_simulator_sse_real_smoke.py`, `test_simulator_sse_tx_failed_timeout.py` — проходят в A; `test_simulator_sse_trust_drift_decay_topology_patch.py` — **нет** (40001 в A, проходит в B).

Проход в A для этих файлов — необходимое, а не достаточное условие режима A (см. 4.3).

## 5. Зависания

**Зависание №1 — поимённо и с механизмом, измеренным на сервере.** `tests/unit/test_p015_inject_transaction_ownership.py::test_the_owner_widens_the_lock_set_once_for_a_trustline_created_after_its_read`. Воспроизведено трижды: проход A, изоляция, проверка значения таймаута (в непрерывном прогоне исключено `--deselect`). Механизм — **не advisory-лок**, как предполагал бриф, а строковая блокировка: наблюдатель `pg_stat_activity`/`pg_locks`, каждый раз одно и то же —

```
wait: Lock/transactionid   waiting_for: transactionid:ShareLock
query: INSERT INTO trust_lines (...) VALUES (...)
blocker: state "idle in transaction", last query "RELEASE SAVEPOINT sa_savepoint_3"
```

Тест сеет участников в `db_session` (режим A, незакоммиченно) и из подменённого `_resolve_inject_owner_lock_ids` вставляет линию через **второе** соединение `TestingSessionLocal()` (`test_p015_inject_transaction_ownership.py:719-729`). Проверка внешнего ключа ждёт конца транзакции, вставившей участника, — это транзакция того же теста, которая ждёт возврата этой корутины. Postgres такую взаимную блокировку не видит: одна её сторона — клиентское ожидание. Главный поток в дампе стоит в пустом цикле событий — стек корутины pytest-timeout не показывает. В B-switch тест не зависает, а падает (`non-vacuity: the racing trustline was never created`, blind: вторая сессия на базе тира).

**Зависание №2 — не воспроизведено.** Ни в одном из четырёх проходов второго зависания нет: ни в проходе A с перезапуском, ни в изоляции, ни в B, ни в **непрерывном** прогоне одним процессом с исключённым №1 (814 с, `2511 passed, 175 failed, 14 error, 4 skipped`), который ближе всего к прогону владельца. Имени у него нет. Гипотеза, не проверенная: оно зависело от остатка, накопленного к ~53 % прогона владельца, а остаток зависит от формы прогона (раздел «Граница», п. 3).

## 6. Что каталог не измерил

- **21 файл строит собственный SQLite-движок** и на Postgres-прогоне остаётся SQLite: `integration/test_audit_drift_delta_check_sse_integration.py`, `test_p015_p1_money_replay_sqlite.py`, `test_p015_step5c_hold_through_the_tick_sqlite.py`, `test_p015_t1544_operator_stop_through_the_tick_sqlite.py`, `test_post_tick_audit_drift_runner_integration.py`, `test_simulator_adaptive_clearing_effectiveness_ab.py` (часть тестов — `slow`), `test_simulator_adaptive_clearing_integration.py`, `test_simulator_clearing_no_deadlock.py`, `unit/test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py`, `test_p015_b4_entries_and_money.py` (частично), `test_p015_b4a_journal_mechanism.py`, `test_p015_p1_money_conflict_predicate.py`, `test_p015_step5b_sqlite_startup_refuses_a_pre_027_schema.py`, `test_p015_step5c_reaction_and_hold.py` (частично, `:972`), `test_p015_t1525_a_busy_does_not_mask_and_does_not_promise.py`, `test_p015_t1525_sqlite_transaction_control_is_in_effect.py` (частично), `test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py` (частично), `test_p015_t1528_the_guard_reads_what_the_statement_writes.py`, `test_p015_t1530_the_journal_reads_its_own_record_back.py`, `test_p015_t1531_the_verification_read_is_not_rewritable.py`, `test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py`. Их зелёный здесь — **отсутствующее** измерение Postgres, а не пройденное. Предсказанные инвентарём qmark в `t1530:435,:567` и `t1531:365` поэтому не проявились: код идёт в SQLite.
- **Режим B для VIS-строк** (24 теста) и всех прочих `(blind)` — не измерен.
- **Загрязнители остатка** не названы.
- **Агрегат `contract/test_p011_responses_conform_to_the_canon.py`** после перезапуска видит тела ответов только своего процесса, то есть в проходе A — около половины тира; его зелёный в этом проходе слабее, чем в непрерывном (где он тоже зелёный).
- **Linux-поведение таймаута** (метод `signal`) не мерялось: все прогоны — Windows.
- **Время CI** не мерялось; числа стены выше — одна машина, по одному прогону.

## 7. Для бюджета тира (`T1706`, вторая половина) — только как порядок величины

Дефолтный тир на Postgres в режиме A — около 640 с (683 + 134 минус 180 с ожидания таймаута; непрерывный прогон — 814 с), против 296–316 с на SQLite в тот же день. Тот же тир со **всеми** `db_session` в режиме B — 1368 с. Популяции не совпадают с будущим тиром (маркер `postgres` ещё не снят), и 189 тестов красные, так что это не «после», а оценка.

## 8. Поимённый каталог — все 189 непрошедших тестов

Колонки: исход в проходе A; дословная первая строка `E …` (обрезана на 160 знаках); класс (раздел 3; `*` — в полном прогоне симптом был остатком, класс взят из изолированного прогона); исход изолированного прогона файла; исход под `GEO_TEST_FIXTURE_MODE=B` (`blind` — см. «Граница», п. 4); вердикт и режим инвентаря.

| # | test (file :: name) | outcome A | first line of the cause (verbatim, cut at 160) | class | isolated A | switch B | inventory (verdict / mode) |
|---|---|---|---|---|---|---|---|
| 1 | `integration/test_admin_feature_flags_multipath.py` :: `test_feature_flag_multipath_enabled_gates_multi_route_payment` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 2 | `integration/test_admin_routing_max_paths.py` :: `test_routing_max_paths_limits_multipath_payment` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 3 | `integration/test_clearing_max_depth_controls_long_cycles.py` :: `test_clearing_max_depth_blocks_and_allows_length_5_cycle` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{"cleared_cycles":0,"partial":false}}} | CLR | FAILED | PASSED | keep / B |
| 4 | `integration/test_daily_limit_not_enforced.py` :: `test_daily_limit_is_informational_only` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 5 | `integration/test_equivalent_writer_and_legacy_reads.py` :: `test_admin_patch_rejects_invalid_legacy_code_before_mutation` | FAILED | asyncpg.exceptions.CheckViolationError: new row for relation "equivalents" violates check constraint "chk_equivalents_code_format" | SCHEMA-CHECK | FAILED | FAILED | keep / A |
| 6 | `integration/test_equivalent_writer_and_legacy_reads.py` :: `test_legacy_invalid_equivalent_rows_remain_visible_on_read_surfaces` | FAILED | asyncpg.exceptions.CheckViolationError: new row for relation "equivalents" violates check constraint "chk_equivalents_code_format" | SCHEMA-CHECK | FAILED | FAILED | keep / A |
| 7 | `integration/test_integrity_repairs_atomicity.py` :: `test_an_uninstrumented_repair_is_refused_before_it_writes_anything[cap-debts-to-trust-limits]` | FAILED | AssertionError: stand: no debts were seeded, so the repair would touch nothing | VIS | FAILED | FAILED (blind) | keep / B |
| 8 | `integration/test_integrity_repairs_atomicity.py` :: `test_an_uninstrumented_repair_is_refused_before_it_writes_anything[net-mutual-debts]` | FAILED | AssertionError: stand: no debts were seeded, so the repair would touch nothing | VIS | FAILED | FAILED (blind) | keep / B |
| 9 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_admin_audit_log_declares_no_money_and_leaks_none` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 10 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_admin_bottlenecks_money_is_decimal_text_and_threshold_is_a_number` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 11 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_admin_liquidity_summary_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 12 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_admin_participant_metrics_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 13 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_admin_ratio_fields_are_json_numbers_not_strings` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 14 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_admin_trustlines_list_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 15 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_one_threshold_parameter_comes_back_as_a_number_twice_and_a_string_once` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 16 | `integration/test_p011_admin_money_is_a_decimal_string_on_the_wire.py` :: `test_trustline_updated_at_reaches_the_wire_on_every_admin_route_that_serves_one` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 17 | `integration/test_p011_money_is_a_decimal_string_on_the_wire.py` :: `test_payment_by_tx_id_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 18 | `integration/test_p011_money_is_a_decimal_string_on_the_wire.py` :: `test_payment_create_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 19 | `integration/test_p011_money_is_a_decimal_string_on_the_wire.py` :: `test_payment_list_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 20 | `integration/test_p011_money_is_a_decimal_string_on_the_wire.py` :: `test_trustline_by_id_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 21 | `integration/test_p011_money_is_a_decimal_string_on_the_wire.py` :: `test_trustline_create_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 22 | `integration/test_p011_money_is_a_decimal_string_on_the_wire.py` :: `test_trustline_list_money_is_decimal_text` | ERROR | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | ERROR | PASSED | keep / A |
| 23 | `integration/test_p012_t1201_money_door_at_the_entrances.py` :: `test_a_payment_amount_with_trailing_zeros_commits_and_is_not_renormalised` | FAILED | AssertionError: a payment of '0.100000000' - the value 0.1 - was not accepted: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 24 | `integration/test_p015_f0156_repairs_are_closed_by_default.py` :: `test_repair_endpoints_refuse_while_the_finding_is_open[/api/v1/integrity/repair/cap-debts-to-trust-limits]` | FAILED | AssertionError: the debt behind a frozen line was destroyed | VIS | FAILED | FAILED (blind) | keep / B |
| 25 | `integration/test_p015_f0156_repairs_are_closed_by_default.py` :: `test_repair_endpoints_refuse_while_the_finding_is_open[/api/v1/integrity/repair/net-mutual-debts]` | FAILED | AssertionError: the debt behind a frozen line was destroyed | VIS | FAILED | FAILED (blind) | keep / B |
| 26 | `integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py` :: `test_a_committed_payment_still_replays_its_result_under_an_integrity_hold` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 27 | `integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py` :: `test_a_tx_id_whose_payment_aborted_replays_the_stored_aborted_result` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 28 | `integration/test_p015_t1544_operator_stop_refuses_money.py` :: `test_a_payment_in_a_deactivated_equivalent_is_refused_before_any_transaction_exists` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / B |
| 29 | `integration/test_p015_t1544_operator_stop_refuses_money.py` :: `test_an_accepted_payment_still_replays_its_result_after_the_stop` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / B |
| 30 | `integration/test_p015_t1544_operator_stop_refuses_money.py` :: `test_clearing_in_a_deactivated_equivalent_is_refused_and_keeps_the_debts` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / B |
| 31 | `integration/test_p015_t1544_operator_stop_refuses_money.py` :: `test_clearing_real_reports_the_stop_as_its_declared_409` | FAILED | AssertionError: {"code":"CLEARING_FAILED","message":"Clearing failed","details":null} | CLR | FAILED | PASSED | keep / B |
| 32 | `integration/test_payment_prepare_capacity_policy.py` :: `test_multipath_prepare_keeps_local_reservations_in_addition_to_persisted_ones` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 33 | `integration/test_payment_prepare_capacity_policy.py` :: `test_single_and_multipath_prepare_apply_the_same_persisted_reservation_policy` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 34 | `integration/test_payment_prepare_error_taxonomy.py` :: `test_staged_generic_prepare_failure_is_safe_without_session_commit_or_rollback` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / B |
| 35 | `integration/test_payment_prepare_error_taxonomy.py` :: `test_staged_insert_serialization_failure_propagates_without_local_rollback` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / B |
| 36 | `integration/test_payment_prepare_error_taxonomy.py` :: `test_staged_prepare_cancellation_aborts_before_outer_rollback` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / B |
| 37 | `integration/test_payment_prepare_error_taxonomy.py` :: `test_staged_timeout_abort_failure_has_symmetric_safe_log` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / B |
| 38 | `integration/test_payments_constraints_avoid.py` :: `test_payment_routing_constraints_avoid_filters_intermediate_pid` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 39 | `integration/test_payments_idempotency.py` :: `test_payments_tx_id_returns_same_result` | FAILED | assert 500 == 200 | FIXA | FAILED | PASSED | keep / A |
| 40 | `integration/test_payments_idempotency.py` :: `test_payments_tx_id_reuse_with_different_payload_conflicts` | FAILED | assert 500 == 200 | FIXA | FAILED | PASSED | keep / A |
| 41 | `integration/test_payments_list_filters.py` :: `test_list_payments_filters` | FAILED | assert 500 == 200 | FIXA | FAILED | PASSED | keep / A |
| 42 | `integration/test_payments_multipath.py` :: `test_payment_multipath_split_two_routes` | FAILED | assert 500 == 200 | FIXA | FAILED | PASSED | keep / A |
| 43 | `integration/test_scenarios.py` :: `test_clearing` | FAILED | sqlalchemy.exc.InvalidRequestError: This session is in 'prepared' state; no further SQL can be emitted within this transaction. | FIXA | FAILED | PASSED | keep / B |
| 44 | `integration/test_scenarios.py` :: `test_direct_payment` | FAILED | assert 500 == 200 | FIXA | FAILED | PASSED | keep / B |
| 45 | `integration/test_scenarios.py` :: `test_multihop_payment` | FAILED | assert 500 == 200 | FIXA | FAILED | PASSED | keep / B |
| 46 | `integration/test_scenarios.py` :: `test_multipath_payment` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / B |
| 47 | `integration/test_simulator_real_snapshot_db_enrichment.py` :: `test_real_mode_graph_snapshot_enriches_used_and_net_sign` | FAILED | asyncpg.exceptions.SerializationError: could not serialize access due to read/write dependencies among transactions | 40001 | FAILED | FAILED (blind) | transfer / B |
| 48 | `integration/test_simulator_sse_trust_drift_decay_topology_patch.py` :: `test_simulator_sse_trust_drift_decay_emits_edge_patch_not_empty_topology_changed` | FAILED | asyncpg.exceptions.SerializationError: could not serialize access due to read/write dependencies among transactions | 40001 | FAILED | PASSED | keep / B |
| 49 | `integration/test_trustline_negative_constraints.py` :: `test_trustline_close_rejects_non_zero_debt` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 50 | `integration/test_trustline_negative_constraints.py` :: `test_trustline_update_rejects_limit_below_used` | FAILED | AssertionError: {"error":{"code":"E010","message":"Internal server error","details":{}}} | FIXA | FAILED | PASSED | keep / A |
| 51 | `integration/test_trustlines_get_by_id.py` :: `test_get_trustline_by_id` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 52 | `unit/test_admin_abort_tx.py` :: `test_admin_abort_tx_aborts_and_audits` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | FIXA * | FAILED | PASSED | keep / A |
| 53 | `unit/test_admin_abort_tx.py` :: `test_admin_abort_tx_repeats_aborted_transaction_idempotently` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 54 | `unit/test_admin_abort_tx.py` :: `test_admin_abort_tx_rolls_back_when_outer_commit_fails[ABORTED-TX_ABORTED_COMMIT_FAILURE]` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 55 | `unit/test_admin_abort_tx.py` :: `test_admin_abort_tx_rolls_back_when_outer_commit_fails[WAITING-TX_ABORT_COMMIT_FAILURE]` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 56 | `unit/test_admin_abort_tx.py` :: `test_admin_abort_tx_uses_lock_protected_already_aborted_metric` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 57 | `unit/test_admin_clearing_cycles.py` :: `test_admin_clearing_cycles_returns_equivalents_and_cycles` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 58 | `unit/test_admin_graph_ego.py` :: `test_admin_graph_ego_depth_1_and_2` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 59 | `unit/test_admin_graph_snapshot.py` :: `test_admin_graph_snapshot_equivalent_enables_net_viz` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 60 | `unit/test_admin_graph_snapshot.py` :: `test_admin_graph_snapshot_hydrates_trustlines_and_debts` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 61 | `unit/test_admin_incidents_list.py` :: `test_admin_incidents_lists_only_stuck_payments` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 62 | `unit/test_admin_incidents_list.py` :: `test_admin_incidents_pagination` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 63 | `unit/test_admin_liquidity_summary.py` :: `test_admin_liquidity_summary_smoke` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 64 | `unit/test_admin_participant_metrics.py` :: `test_admin_participant_metrics_activity_counts` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 65 | `unit/test_admin_participant_metrics.py` :: `test_admin_participant_metrics_balance_and_counterparties_and_capacity_and_rank` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 66 | `unit/test_admin_participants_list.py` :: `test_admin_participants_list_pagination_and_filters` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 67 | `unit/test_admin_participants_stats.py` :: `test_admin_participants_stats_counts` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 68 | `unit/test_admin_trustlines_bottlenecks.py` :: `test_admin_trustlines_bottlenecks_filters_and_sorts` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 69 | `unit/test_admin_trustlines_list.py` :: `test_admin_trustlines_list_filters_and_pagination` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 70 | `unit/test_admin_whoami_and_extras.py` :: `test_admin_equivalents_include_inactive` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / A |
| 71 | `unit/test_admin_whoami_and_extras.py` :: `test_admin_graph_snapshot_include_extras_smoke` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "participants_pid_key" | RESIDUE | PASSED | PASSED | keep / A |
| 72 | `unit/test_apply_flow_retry_on_stale.py` :: `test_apply_flow_retries_on_stale_data` | FAILED | sqlalchemy.exc.NoResultFound: No row was found when one was required | VIS | FAILED | FAILED (blind) | keep / B |
| 73 | `unit/test_clearing_additional_cases.py` :: `test_auto_clear_clears_multiple_independent_cycles` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 74 | `unit/test_clearing_additional_cases.py` :: `test_execute_clearing_checkpoint_failure_is_explicitly_best_effort` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 75 | `unit/test_clearing_additional_cases.py` :: `test_execute_clearing_commit_failure_rolls_back_without_visible_effects` | FAILED | assert 3 == 0 | CLR | PASSED | FAILED | keep / B |
| 76 | `unit/test_clearing_additional_cases.py` :: `test_execute_clearing_nonpositive_defensive_skip_rolls_back` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | FAILED | keep / B |
| 77 | `unit/test_clearing_additional_cases.py` :: `test_execute_clearing_policy_skip_remains_non_exceptional` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 78 | `unit/test_clearing_additional_cases.py` :: `test_execute_clearing_rollback_failure_keeps_original_error_sanitized` | FAILED | RuntimeError: rollback private detail | CLR | FAILED | PASSED | keep / B |
| 79 | `unit/test_debt_optimistic_lock.py` :: `test_a_stale_writer_cannot_overwrite_the_committed_debt_amount` | FAILED | sqlalchemy.exc.NoResultFound: No row was found when one was required | VIS | FAILED | FAILED (blind) | transfer / B |
| 80 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_emits_durable_partial_done_before_sanitized_failure[cancelled_execute]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 81 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_emits_durable_partial_done_before_sanitized_failure[cancelled_finalize]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 82 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_emits_durable_partial_done_before_sanitized_failure[committed_cancel]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 83 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_emits_durable_partial_done_before_sanitized_failure[geo]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 84 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_emits_durable_partial_done_before_sanitized_failure[unexpected]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 85 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_happy_zero_cycles` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 86 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_initial_failure_uses_flat_sanitized_error` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 87 | `unit/test_interact_actions_backend_p1.py` :: `test_action_clearing_real_total_cleared_amount_is_actual_not_precalc` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | CLR * | FAILED | PASSED | keep / B |
| 88 | `unit/test_interact_actions_backend_p1.py` :: `test_action_participants_list_is_run_scoped_snapshot_only` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 89 | `unit/test_interact_actions_backend_p1.py` :: `test_action_participants_list_returns_array` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 90 | `unit/test_interact_actions_backend_p1.py` :: `test_action_payment_real_amount_manual_validation_stays_invalid_amount` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 91 | `unit/test_interact_actions_backend_p1.py` :: `test_action_payment_real_emits_tx_updated_with_edge_patch` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 92 | `unit/test_interact_actions_backend_p1.py` :: `test_action_payment_real_happy_mocked` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 93 | `unit/test_interact_actions_backend_p1.py` :: `test_action_payment_real_insufficient_capacity_when_topology_path_exists` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 94 | `unit/test_interact_actions_backend_p1.py` :: `test_action_payment_real_no_route_mocked` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 95 | `unit/test_interact_actions_backend_p1.py` :: `test_action_payment_real_retryable_conflict_is_not_rejected` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 96 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustline_close_happy_and_has_debt` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 97 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustline_create_after_close_is_a_declared_outcome` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 98 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustline_create_happy_and_conflict` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 99 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustline_create_schema_validation_is_invalid_request` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 100 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustline_create_self_loop_is_invalid_request` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 101 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustline_update_happy_and_used_exceeds_new_limit` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 102 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustlines_list_is_run_scoped_and_filters_by_participant_pid` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 103 | `unit/test_interact_actions_backend_p1.py` :: `test_action_trustlines_list_returns_array` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 104 | `unit/test_interact_actions_backend_p1.py` :: `test_every_mutating_action_is_run_scoped_not_just_create[payment-real-payload2]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 105 | `unit/test_interact_actions_backend_p1.py` :: `test_every_mutating_action_is_run_scoped_not_just_create[trustline-close-payload1]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 106 | `unit/test_interact_actions_backend_p1.py` :: `test_every_mutating_action_is_run_scoped_not_just_create[trustline-update-payload0]` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 107 | `unit/test_interact_actions_backend_p1.py` :: `test_mutating_action_is_run_scoped_like_its_read_sibling` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 108 | `unit/test_interact_actions_backend_p1.py` :: `test_payment_targets_multihop_returns_backend_reachable_to_pid_list` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 109 | `unit/test_interact_actions_backend_p1.py` :: `test_payment_targets_no_route_returns_empty_items` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 110 | `unit/test_interact_actions_backend_p1.py` :: `test_trustline_create_used_read_error_returns_503_and_error_envelope` | FAILED | asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "equivalents_code_key" | RESIDUE | PASSED | PASSED | keep / B |
| 111 | `unit/test_invariants.py` :: `test_clearing_writes_integrity_audit_log_on_success` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | FAILED | keep / B |
| 112 | `unit/test_invariants.py` :: `test_payment_commit_writes_integrity_audit_log_on_success` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / B |
| 113 | `unit/test_p012_t1210_detector_union_default_tier.py` :: `test_auto_clear_orders_the_union_when_the_sql_path_is_down` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 114 | `unit/test_p012_t1210_detector_union_default_tier.py` :: `test_auto_clear_over_a_shared_edge_clears_the_large_cycle_and_leaves_the_small` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 115 | `unit/test_p012_t1210_detector_union_default_tier.py` :: `test_the_ladder_widens_when_short_cycles_exist_but_none_executes` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 116 | `unit/test_p013_t1302_graph_snapshot_include_completeness.py` :: `test_asked_and_empty_is_distinguishable_from_not_asked` | FAILED | AssertionError: precondition: both are empty | RESIDUE | PASSED | PASSED | keep / A |
| 117 | `unit/test_p015_b4_entries_and_money.py` :: `test_c12_a_value_this_dialect_cannot_hold_is_refused_before_any_debt_sql[one atom past the exact domain]` | FAILED | AssertionError: stand: this database stored 100000000000.00000001 unchanged (read back Decimal('100000000000.00000001')) and the money rules of design v2 §4 per | DIALECT-PREMISE | FAILED | FAILED (blind) | keep / B |
| 118 | `unit/test_p015_b4_entries_and_money.py` :: `test_c18_entries_come_from_the_attempt_that_succeeded_and_not_the_stale_one` | FAILED | asyncpg.exceptions.PostgresSyntaxError: syntax error at end of input | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 119 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check[a completed envelope with a negative effect count]` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 120 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check[a completed envelope with no digest]` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 121 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check[an entry whose delta is zero]` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 122 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check[an envelope written by a schema this build does not know]` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 123 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check[an insert that claims a previous amount]` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 124 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_invalid_forged_row_is_refused_by_the_named_check[an update whose endpoints are equal]` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 125 | `unit/test_p015_b4_entries_and_money.py` :: `test_c19_a_shape_valid_lie_is_accepted_and_is_therefore_step_6_s_job` | FAILED | TypeError: expected a datetime.date or datetime.datetime instance, got 'str' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 126 | `unit/test_p015_b4_entries_and_money.py` :: `test_condition4_a_delta_this_dialect_cannot_hold_is_refused_though_both_ends_fit` | FAILED | AssertionError: stand: this database now stores 99999999999.99999999 exactly (read back Decimal('99999999999.99999999')), so the delta is no longer the unstorab | DIALECT-PREMISE | FAILED | FAILED (blind) | keep / B |
| 127 | `unit/test_p015_b4_transaction_contract.py` :: `test_c10_a_refused_release_leaves_the_root_open_and_poisoned` | FAILED | AttributeError: 'Connection' object has no attribute 'in_transaction' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 128 | `unit/test_p015_b4_transaction_contract.py` :: `test_c10_a_refused_root_commit_leaves_no_open_database_transaction` | FAILED | AttributeError: 'Connection' object has no attribute 'in_transaction' | DIALECT-SQL | FAILED | FAILED (blind) | keep / B |
| 129 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_freeze_of_two_participants_in_two_outside_equivalents_completes` | FAILED | AssertionError: {} | VIS | FAILED | FAILED (blind) | keep / B |
| 130 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_publish_failure_after_commit_keeps_the_committed_inject[artifacts]` | FAILED | AssertionError: assert None == Decimal('10.00') | VIS | FAILED | FAILED (blind) | keep / B |
| 131 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_publish_failure_after_commit_keeps_the_committed_inject[edge_patch_builder]` | FAILED | AssertionError: assert None == Decimal('10.00') | VIS | FAILED | FAILED (blind) | keep / B |
| 132 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_rolled_back_add_participant_is_not_seen_by_the_retry_but_a_committed_one_is` | FAILED | AssertionError: [] | VIS | FAILED | FAILED (blind) | keep / B |
| 133 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_stale_snapshot_is_transient_and_the_inject_lands_exactly_once` | FAILED | AssertionError: this stand forces a SQLite stale snapshot; the test engine is postgresql | SQLITE-MECH | FAILED | FAILED (blind) | keep / B |
| 134 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_stale_snapshot_on_both_attempts_leaves_the_event_pending` | FAILED | AssertionError: postgresql | SQLITE-MECH | FAILED | FAILED (blind) | keep / B |
| 135 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_transient_failure_is_retried_and_applied_exactly_once[commit-error_kwargs2]` | FAILED | AssertionError: the injected 10.00 must land exactly once on 5.12345678 | VIS | FAILED | FAILED (blind) | keep / B |
| 136 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_transient_failure_is_retried_and_applied_exactly_once[staging-error_kwargs0]` | FAILED | AssertionError: the injected 10.00 must land exactly once on 5.12345678 | VIS | FAILED | FAILED (blind) | keep / B |
| 137 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_transient_failure_is_retried_and_applied_exactly_once[staging-error_kwargs1]` | FAILED | AssertionError: the injected 10.00 must land exactly once on 5.12345678 | VIS | FAILED | FAILED (blind) | keep / B |
| 138 | `unit/test_p015_inject_transaction_ownership.py` :: `test_a_transient_failure_is_retried_and_applied_exactly_once[staging-error_kwargs3]` | FAILED | AssertionError: the injected 10.00 must land exactly once on 5.12345678 | VIS | FAILED | FAILED (blind) | keep / B |
| 139 | `unit/test_p015_inject_transaction_ownership.py` :: `test_staging_a_freeze_outside_the_lock_set_raises_before_staging_it` | FAILED | sqlalchemy.exc.NoResultFound: No row was found when one was required | VIS | FAILED | FAILED (blind) | keep / B |
| 140 | `unit/test_p015_inject_transaction_ownership.py` :: `test_staging_leaves_the_transaction_to_its_caller_commit_control` | FAILED | AssertionError: assert None == Decimal('10.00') | VIS | FAILED | FAILED (blind) | keep / B |
| 141 | `unit/test_p015_inject_transaction_ownership.py` :: `test_the_owner_locks_a_freezes_incident_equivalents_before_staging` | FAILED | sqlalchemy.exc.NoResultFound: No row was found when one was required | VIS | FAILED | FAILED (blind) | keep / B |
| 142 | `unit/test_p015_inject_transaction_ownership.py` :: `test_the_owner_returns_with_no_transaction_open` | FAILED | AssertionError: assert None == Decimal('10.00') | VIS | FAILED | FAILED (blind) | keep / B |
| 143 | `unit/test_p015_inject_transaction_ownership.py` :: `test_the_owner_widens_the_lock_set_once_for_a_trustline_created_after_its_read` | TIMEOUT | (process ended by pytest-timeout after 180 s; no traceback) | HANG | TIMEOUT | FAILED (blind) | keep / B |
| 144 | `unit/test_p015_step5a_reconciliation.py` :: `test_step5a_a_journal_row_contradicting_its_own_arithmetic_is_failed_and_dominates` | FAILED | asyncpg.exceptions.CheckViolationError: new row for relation "debt_journal_entries" violates check constraint "chk_debt_journal_entries_delta_arithmetic" | SCHEMA-CHECK | FAILED | FAILED (blind) | keep / B |
| 145 | `unit/test_p015_step5a_reconciliation.py` :: `test_step5a_without_sqlite_transaction_control_there_is_no_verdict` | FAILED | AssertionError: {'FAILED': 0, 'PASSED': 1, 'UNVERIFIABLE': 0, 'error': 0, ...} | SQLITE-MECH | FAILED | FAILED (blind) | keep / B |
| 146 | `unit/test_p015_step5b_criterion_b.py` :: `test_step5b_an_honest_inject_is_checked_as_its_subset_and_passed` | FAILED | asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "debt_reconciliation_baselines" violates foreign key constraint "fk_debt_reconciliation_b | VIS | FAILED | FAILED (blind) | keep / B |
| 147 | `unit/test_p015_step5b_criterion_b.py` :: `test_step5b_an_inject_outside_its_subset_is_failed[atom_writer]` | FAILED | asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "debt_reconciliation_baselines" violates foreign key constraint "fk_debt_reconciliation_b | VIS | FAILED | FAILED (blind) | keep / B |
| 148 | `unit/test_p015_step5b_criterion_b.py` :: `test_step5b_an_inject_outside_its_subset_is_failed[decrease]` | FAILED | asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "debt_reconciliation_baselines" violates foreign key constraint "fk_debt_reconciliation_b | VIS | FAILED | FAILED (blind) | keep / B |
| 149 | `unit/test_p015_step5b_criterion_b.py` :: `test_step5b_an_inject_outside_its_subset_is_failed[intent_amount]` | FAILED | asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "debt_reconciliation_baselines" violates foreign key constraint "fk_debt_reconciliation_b | VIS | FAILED | FAILED (blind) | keep / B |
| 150 | `unit/test_p015_step5b_criterion_b.py` :: `test_step5b_an_inject_outside_its_subset_is_failed[split_edge]` | FAILED | asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "debt_reconciliation_baselines" violates foreign key constraint "fk_debt_reconciliation_b | VIS | FAILED | FAILED (blind) | keep / B |
| 151 | `unit/test_p015_step5c_reaction_and_hold.py` :: `test_step5c_clearing_real_reports_the_hold_as_its_declared_409` | FAILED | asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "debt_reconciliation_results" violates foreign key constraint "fk_debt_reconciliation_res | VIS | FAILED | FAILED (blind) | keep / B |
| 152 | `unit/test_p015_step5c_reaction_and_hold.py` :: `test_step5c_the_evidence_of_a_hold_cannot_be_deleted_while_held` | FAILED | AssertionError: IntegrityError('(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class \'asyncpg.exceptions.ForeignKeyViolatio..."equivalents" | DIALECT-MESSAGE | FAILED | FAILED (blind) | keep / B |
| 153 | `unit/test_p015_step5c_reaction_and_hold.py` :: `test_step5c_the_hold_is_cleared_only_explicitly_after_a_later_passed_and_audited` | FAILED | asyncpg.exceptions.SerializationError: could not serialize access due to concurrent update | 40001 | FAILED | FAILED (blind) | keep / B |
| 154 | `unit/test_p015_t1523_the_commit_landed_then_the_caller_failed.py` :: `test_a_commit_that_landed_and_then_raised_a_db_error_answers_committed_once` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 155 | `unit/test_p015_t1523_the_commit_landed_then_the_caller_failed.py` :: `test_a_commit_that_landed_and_then_timed_out_answers_committed_once` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 156 | `unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py` :: `test_a_payment_that_keeps_losing_the_race_is_refused_after_a_finite_budget` | FAILED | AssertionError: ['event=payment.uow_retry op=commit attempt=1/3 delay_s=0.060 pgcode=40001 sqlite_error=None', 'event=payment.uow_retry op=commit attempt=2/3 de | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 157 | `unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py` :: `test_a_payment_that_loses_the_snapshot_race_is_retried_and_commits` | FAILED | AssertionError: postgresql | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 158 | `unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py` :: `test_the_classifier_reads_the_error_code_and_refuses_everything_else` | FAILED | assert None == 'SQLITE_BUSY_SNAPSHOT' | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 159 | `unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py` :: `test_a_fresh_database_gets_wal_from_the_conftest_connect_listener` | FAILED | AssertionError: the test engine no longer sets its connection pragmas on connect | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 160 | `unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py` :: `test_a_read_after_begin_is_inside_a_database_transaction` | FAILED | AssertionError: this module checks the default SQLite tier; the test engine is postgresql | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 161 | `unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py` :: `test_one_connection_is_outside_then_inside_then_outside_a_transaction` | FAILED | AttributeError: 'Connection' object has no attribute 'isolation_level' | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 162 | `unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py` :: `test_the_default_test_database_is_in_wal` | FAILED | asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "PRAGMA" | SQLITE-MECH | FAILED | FAILED (blind) | delete / — |
| 163 | `unit/test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py` :: `test_b_a_check_constraint_on_this_tier_can_never_refuse_a_nan` | FAILED | asyncpg.exceptions.PostgresSyntaxError: syntax error at or near ")" | DIALECT-SQL | FAILED | FAILED (blind) | delete / — |
| 164 | `unit/test_p015_t1543_frozen_line_is_not_limit_zero.py` :: `test_a_partial_repayment_of_debt_on_a_frozen_line_commits` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 165 | `unit/test_p015_t1543_frozen_line_is_not_limit_zero.py` :: `test_a_payment_beside_a_frozen_line_is_recorded_as_verified` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 166 | `unit/test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py` :: `test_a_fingerprinted_replay_of_a_different_request_is_still_the_old_conflict` | FAILED | app.core.ledger.journal.DebtOperationIncomplete: [root_poisoned] refusing to release savepoint sa_savepoint_8: this transaction was poisoned: no_operation. The  | FIXA | FAILED | PASSED | keep / A |
| 167 | `unit/test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py` :: `test_a_fingerprinted_replay_of_the_same_request_is_still_an_idempotent_hit` | FAILED | app.core.ledger.journal.DebtOperationIncomplete: [root_poisoned] refusing to release savepoint sa_savepoint_8: this transaction was poisoned: no_operation. The  | FIXA | FAILED | PASSED | keep / A |
| 168 | `unit/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py` :: `test_a_cycle_through_lines_frozen_by_the_simulator_inject_is_cleared` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 169 | `unit/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py` :: `test_a_frozen_line_without_consent_is_still_not_cleared` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 170 | `unit/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py` :: `test_clearing_reduces_the_over_limit_debt_on_a_frozen_line[active]` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 171 | `unit/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py` :: `test_clearing_reduces_the_over_limit_debt_on_a_frozen_line[frozen]` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 172 | `unit/test_p1_clearing_run_perimeter.py` :: `test_a_committed_replay_is_not_returned_to_a_foreign_scope` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | PASSED | keep / B |
| 173 | `unit/test_p1_clearing_run_perimeter.py` :: `test_the_owning_run_still_clears_its_own_cycle` | FAILED | AssertionError: {"code":"CLEARING_FAILED","message":"Clearing failed","details":null} | CLR | FAILED | PASSED | keep / B |
| 174 | `unit/test_p1_payment_run_perimeter.py` :: `test_a_direct_edge_inside_the_perimeter_still_pays` | FAILED | AssertionError: {"code":"PAYMENT_REJECTED","message":"Internal server error","details":{}} | FIXA | FAILED | PASSED | keep / A |
| 175 | `unit/test_p1_payment_run_perimeter.py` :: `test_a_replay_whose_route_cannot_be_read_is_refused` | FAILED | app.core.ledger.journal.DebtOperationIncomplete: [root_poisoned] refusing to release savepoint sa_savepoint_8: this transaction was poisoned: no_operation. The  | FIXA | FAILED | PASSED | keep / A |
| 176 | `unit/test_p1_payment_run_perimeter.py` :: `test_a_reused_key_for_a_different_request_stays_a_conflict` | FAILED | app.core.ledger.journal.DebtOperationIncomplete: [root_poisoned] refusing to release savepoint sa_savepoint_8: this transaction was poisoned: no_operation. The  | FIXA | FAILED | PASSED | keep / A |
| 177 | `unit/test_p1_payment_run_perimeter.py` :: `test_a_run_that_contains_the_hop_still_pays_through_it` | FAILED | AssertionError: {"code":"PAYMENT_REJECTED","message":"Internal server error","details":{}} | FIXA | FAILED | PASSED | keep / A |
| 178 | `unit/test_p1_payment_run_perimeter.py` :: `test_an_idempotent_replay_does_not_hand_back_a_foreign_route` | FAILED | app.core.ledger.journal.DebtOperationIncomplete: [root_poisoned] refusing to release savepoint sa_savepoint_8: this transaction was poisoned: no_operation. The  | FIXA | FAILED | PASSED | keep / A |
| 179 | `unit/test_p1_payment_run_perimeter.py` :: `test_the_staged_path_without_a_perimeter_keeps_its_old_behaviour` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 180 | `unit/test_payment_staged_post_commit.py` :: `test_committed_payment_result_does_not_read_expired_participants` | FAILED | app.core.ledger.journal.DebtOperationIncomplete: [root_poisoned] refusing to release savepoint sa_savepoint_7: this transaction was poisoned: no_operation. The  | FIXA | FAILED | PASSED | keep / A |
| 181 | `unit/test_payment_staged_post_commit.py` :: `test_staged_payment_cancellation_rolls_back_without_effects` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 182 | `unit/test_payment_staged_post_commit.py` :: `test_staged_payment_effects_apply_once_after_outer_commit` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 183 | `unit/test_payment_staged_post_commit.py` :: `test_staged_payment_rollback_has_no_rows_or_effects` | FAILED | assert 2 == 0 | RESIDUE | PASSED | PASSED | keep / A |
| 184 | `unit/test_payments_2pc.py` :: `test_commit_updates_transaction_updated_at` | FAILED | sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager.  Please complete the context manager before emitting further com | FIXA | FAILED | PASSED | keep / A |
| 185 | `unit/test_scenario_inject_topology.py` :: `test_malformed_inject_effect_skipped` | FAILED | AssertionError: assert [UUID('17c490...41e0909ec9c')] == [] | RESIDUE | PASSED | PASSED | keep / A |
| 186 | `unit/test_trustline_audit_fail_closed.py` :: `test_create_checkpoint_failure_is_not_swallowed_or_committed[1]` | FAILED | assert 1 == 0 | RESIDUE | PASSED | PASSED | keep / A |
| 187 | `unit/test_trustline_audit_fail_closed.py` :: `test_create_checkpoint_failure_is_not_swallowed_or_committed[2]` | FAILED | assert 1 == 0 | RESIDUE | PASSED | PASSED | keep / A |
| 188 | `unit/test_trustline_audit_fail_closed.py` :: `test_create_fails_closed_when_actual_invariant_checker_is_unavailable` | FAILED | assert 1 == 0 | RESIDUE | PASSED | PASSED | keep / A |
| 189 | `unit/test_zero_debt_policy.py` :: `test_clearing_deletes_zero_debts` | FAILED | app.utils.exceptions.GeoException: Internal server error | CLR | FAILED | FAILED | keep / B |

## 9. Перезамер после `0d4c25a` — 2026-09-23

Разделы 1–8 выше — историческое измерение слайса 2a от `ea14726`; они не правлены. Этот раздел — то же измерение тем же способом после одной правки: `0d4c25a` удалила из фикстуры режима A рецепт SQLAlchemy 1.4 (явный `begin_nested()` и слушатель `after_transaction_end` `_restart_savepoint`), оставив `join_transaction_mode="create_savepoint"`. Ни один тест, ассерт и ни одна строка `app/` не менялись.

**Снято на:** ветка `claude/017-stage2b-remeasure` от `0d4c25a`, та же машина и тот же переносной PostgreSQL 16 на `127.0.0.1:5432`, база тира `geov0_test_p017s2m`, slug `p017s2m`.

### 9.1 Как снято

Команда раздела 1 с заменой slug и базы (`-TaskSlug p017s2m`, `geov0_test_p017s2m`), регистратор `tests/stage2_catalogue_recorder.py`, `--timeout=180`, режим A (`GEO_TEST_FIXTURE_MODE` не задан).

| проход | что | прогонов | стена | итог |
|---|---|---|---|---|
| A (записанный) | весь дефолтный тир, режим A | 2: первый процесс завершил pytest-timeout на том же зависании №1 раздела 5, второй продолжил до `sessionfinish` | 488 с + 180 с ожидания таймаута; 127 с | 9.2 |
| изоляция | каждый из 21 файла (все файлы RESIDUE и файлы шести непрошедших FIXA) отдельным прогоном, свежая схема | 21 | около 3 мин | 9.4, 9.6 |
| непрерывный A + зонд | весь тир одним процессом, зависание №1 исключено `--deselect`; измерительный плагин **вне дерева** (`-p leak_probe` из каталога сессии, в репозитории его нет) после teardown каждого теста считает строки 10 таблиц базы тира свежим соединением и пишет nodeid, после которого число изменилось | 1 | 974 с — **несравнимо** с 814 с 2a: зонд добавляет запросы на каждый тест | `2565 passed, 135 failed, 4 skipped, 310 deselected`, pytest exit 1 |
| пары | «загрязнитель + жертва» в одном процессе, порядок задан командной строкой; контроль — жертвы без загрязнителя | 11 | секунды каждый | 9.6 |
| зонд изоляции самой правки | `-BackendMarker postgres -BackendSelector tests/integration/test_p017_mode_a_isolation_survives_an_application_commit_postgres.py` | 1 | — | `2 passed`, exit 0 |

`310 deselected` против 307 у 2a: +2 — модуль-зонд изоляции из `0d4c25a` (маркер `postgres`), +1 — исключённое зависание.

### 9.2 Числа до / после

**Собрано 2705** (без изменений). Регистратор насчитал 2706 ответов: тот же дубль `test_p012_t1201_money_door_bounds.py::test_is_storable_money_refuses_what_the_column_would_change[<object object at 0x…>]` после перезапуска (оба раза PASSED), вычтен.

| исход на Postgres, режим A | 2a (`ea14726`) | после `0d4c25a` | Δ |
|---|---|---|---|
| PASSED | 2512 | **2568** | +56 |
| FAILED | 174 | **132** | −42 |
| ERROR | 14 | **0** | −14 |
| TIMEOUT | 1 | **1** | 0 (то же зависание №1) |
| SKIPPED | 4 | **4** | 0 (те же четыре) |
| **сумма** | **2705** | **2705** | |

Непрошедших 189 → **133**; файлов с непрошедшими 62 → **44**. **Ни один тест, прошедший в 2a, не упал**: все 133 непрошедших есть в поимённом каталоге раздела 8.

Непрерывный прогон дал на 3 непрошедших больше записанного: `2565 + 135 + 4 + 1 (исключённое зависание) = 2705`. Лишние три — остаток, зависящий от формы прогона («Граница», п. 3), разобраны в 9.6.

### 9.3 Классы до / после (записанный прогон)

| класс | 2a: тестов / файлов | после: тестов / файлов | что изменилось |
|---|---|---|---|
| FIXA | 62 / 24 | **0 / 0** | 56 прошли; 6 упали по другой причине (9.4) |
| RESIDUE | 52 / 17 | **55 / 18** | ни один не исчез; +3 бывших FIXA (9.4, 9.6) |
| VIS | 24 / 7 | 24 / 7 | |
| CLR | 20 / 9 | **22 / 10** | +2 бывших FIXA: за платежом открылся отказ клиринга |
| DIALECT-SQL | 11 / 3 | 11 / 3 | |
| SQLITE-MECH | 10 / 4 | 10 / 4 | |
| SCHEMA-CHECK | 3 / 2 | 3 / 2 | |
| 40001 | 3 / 3 | 3 / 3 | |
| DIALECT-PREMISE | 2 / 1 | 2 / 1 | |
| DIALECT-MESSAGE | 1 / 1 | 1 / 1 | |
| HANG | 1 / 1 | 1 / 1 | то же зависание №1 |
| **TXTIME** (новый) | — | **1 / 1** | 9.5 |
| **сумма** | **189 / 62** | **133 / 44** | = 132 FAILED + 1 TIMEOUT |

Неизменившиеся классы сверены построчно: у всех не-FIXA строк раздела 8 первая строка `E …` в перезамере та же (единственное отличие — `delay_s` в логе ретрая строки 156: время, а не причина).

### 9.4 Судьба 62 FIXA

**56 прошли**, включая все 14 бывших ERROR (фикстуры `money_scenario` двух модулей `test_p011_*_on_the_wire.py`) и оба селектора спеки «обязаны остаться зелёными»: `test_payments_idempotency.py` (2/2) и `test_payments_2pc.py::test_commit_updates_transaction_updated_at`. Все FIXA-строки 19 файлов из 24 прошли; в оставшихся пяти файлах — шесть тестов:

| # (разд. 8) | тест | причина — дословно из трейсбека | новый класс | изоляция |
|---|---|---|---|---|
| 30 | `integration/test_p015_t1544_operator_stop_refuses_money.py::test_clearing_in_a_deactivated_equivalent_is_refused_and_keeps_the_debts` | `assert 500 == 409`, `{"error":{"code":"E010",…"details":{"cleared_cycles":0,"partial":false}}}`; лог `event=clearing.external_connection_bind_unsupported`, `RuntimeError: PostgreSQL clearing requires an engine-bound AsyncSession` | CLR | FAILED |
| 43 | `integration/test_scenarios.py::test_clearing` | `assert 500 == 200`; тот же лог `clearing.external_connection_bind_unsupported` | CLR — **ровно предсказание инвентаря**, которое 2a записал «не наблюдалось» (4.2): платёж перед клирингом больше не падает, и отказ `[CLR]` стал виден | FAILED |
| 41 | `integration/test_payments_list_filters.py::test_list_payments_filters` | `assert {'76c6d1be-…'} == {'cb09ed8f-…'}`, `Extra items in the left set` на фильтре `from_date` (`:233`) | **TXTIME** (9.5) | FAILED |
| 52 | `unit/test_admin_abort_tx.py::test_admin_abort_tx_aborts_and_audits` | `UniqueViolationError … "participants_pid_key"` | RESIDUE (в 2a — `FIXA *`; теперь файл в изоляции `10 passed`) | PASSED |
| 181 | `unit/test_payment_staged_post_commit.py::test_staged_payment_cancellation_rolls_back_without_effects` | `assert 2 == 0` | RESIDUE | PASSED |
| 182 | `unit/test_payment_staged_post_commit.py::test_staged_payment_effects_apply_once_after_outer_commit` | `assert 3 == 1` | RESIDUE | PASSED |

### 9.5 Что удаление слушателя открыло

**Среди 2512 тестов, прошедших в 2a, новых падений нет** — сверено по всему тиру, а не по выборке.

Открылись причины, которые раньше **маскировал** ранний отказ FIXA (тест падал, не дойдя до них):

- **CLR ×2** (#30, #43) — 9.4.
- **RESIDUE ×2** (#181, #182) — тесты теперь доходят до глобального счёта долгов и видят чужие закоммиченные строки (9.6).
- **TXTIME ×1 — новый класс, не предсказанный ни инвентарём, ни 2a.** `test_list_payments_filters` различает платежи по `created_at`, а `transactions.created_at` задаётся `server_default=func.now()` (`app/db/models/transaction.py:18`). В PostgreSQL `now()` — время начала **транзакции**, а режим A держит весь тест в одной внешней транзакции. Измерено, а не выведено: прогон файла с `-l` показывает у `p1`, `p2`, `p3` одинаковые `created_at` и `committed_at` (`'2026-09-23T16:50:56.247435Z'`), поэтому `from_date = p2.created_at` пропускает и `p1`. Под переключателем B тест в 2a проходил (раздел 8); в этом перезамере B не мерялся.

Замечено попутно, **ни одного теста этим не уронено**: в захваченных логах семи упавших тестов стоит чужое `DataError: invalid input for query argument $11: 3500158664 (value out of int32 range)` на `INSERT INTO simulator_runs (… seed …)` — `seed` симулятора не помещается в `INTEGER` колонки на PostgreSQL. Это лог фоновой записи прогона симулятора, не причина падения тех тестов, в чьих логах он виден; был ли он в 2a, не проверялось.

### 9.6 RESIDUE: гипотеза «остаток оставлял FIXA» опровергнута, загрязнители названы

**Из 52 тестов RESIDUE не исчез ни один** — все 52 FAILED в записанном прогоне с той же первой строкой `E …`. Падения FIXA посреди транзакции остатка не производили. Класс вырос до **55** (+#52, #181, #182).

Загрязнители найдены зондом непрерывного прогона (9.1), и **каждая связь подтверждена парой** «загрязнитель + жертва» в одном процессе; контроль — те же жертвы без загрязнителя (изоляция, все проходят).

| загрязнитель — что закоммичено в базу тира и пережило тест (по зонду) | жертвы | пара |
|---|---|---|
| `integration/test_simulator_real_snapshot_db_enrichment.py::test_real_mode_graph_snapshot_enriches_used_and_net_sign` — 100 участников, эквивалент `UAH`, 432 линии | **38**, все `equivalents_code_key` (`DETAIL: Key (code)=(UAH) already exists`): `test_admin_clearing_cycles` 1, `test_admin_graph_ego` 1, `test_admin_graph_snapshot` 2, `test_admin_liquidity_summary` 1, `test_admin_trustlines_bottlenecks` 1, `test_admin_trustlines_list` 1, `test_admin_whoami_and_extras::test_admin_equivalents_include_inactive` 1, `test_interact_actions_backend_p1` 30 (и 31-й, `CLR *`, с тем же симптомом в полном прогоне) | все 38 (+31-й) |
| `integration/test_simulator_sse_trust_drift_decay_topology_patch.py::test_simulator_sse_trust_drift_decay_emits_edge_patch_not_empty_topology_changed` — участники `alice`, `bob`, линия, долг, транзакция | **10**: 9 `participants_pid_key` (`Key (pid)=(alice)` / `(bob)`) — `test_trustlines_get_by_id` 1, `test_admin_abort_tx` 1, `test_admin_incidents_list` 2, `test_admin_participant_metrics` 2, `test_admin_participants_list` 1, `test_admin_participants_stats` 1, `test_admin_whoami_and_extras::test_admin_graph_snapshot_include_extras_smoke` 1; и `test_p013_t1302_…::test_asked_and_empty_is_distinguishable_from_not_asked` («precondition: both are empty», лишняя линия от `bob`) | все 10 |
| `unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py::test_a_payment_that_keeps_losing_the_race_is_refused_after_a_finite_budget` (сам SQLITE-MECH, #156) — 4 участника, эквивалент, линия, долг, транзакция | **7** во втором процессе записанного прогона: `test_payment_staged_post_commit` 3 (глобальный счёт `Debt`), `test_scenario_inject_topology::test_malformed_inject_effect_skipped` 1 и `test_trustline_audit_fail_closed` 3 (глобальный счёт `TrustLine`) | все 7 |
| `unit/test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py::test_a_the_refusal_of_a_nan_amount_must_name_the_money_rule` (сам **проходит**) — 2 участника, эквивалент, долг | второй источник для `test_payment_staged_post_commit` 3: в записанном прогоне `2 == 0` = долг T1525 + долг T1526 | да |
| `integration/test_simulator_sse_real_smoke.py` — 3 долга, 2 транзакции | в непрерывной форме — глобальные счётчики долгов (ниже); в записанной не предшествует жертвам второго процесса | да (`staged_post_commit` 3, `assert 3 == 0`) |

38 + 10 + 7 = **55**. Из пяти загрязнителей три — прогоны симулятора в реальном режиме, два — модули T1525/T1526. Каким путём их запись проходит мимо внешней транзакции режима A, по коду не прослеживалось; измерен только эффект — строки видны свежему соединению после teardown.

**Остаток, зависящий от формы прогона.** В непрерывном прогоне к 55 добавляются ещё три — жертвы, которые в записанном прогоне попали во второй, «чистый» процесс: `unit/test_p015_t1523_the_commit_landed_then_the_caller_failed.py` 2 (`assert 5 == 1` — глобальный счёт долгов; это бывшие FIXA #154, #155) и `unit/test_trustline_signatures.py::test_trustline_create_rejects_invalid_signature` (`Key (pid)=(bob)`; уже названа в «Граница», п. 3). Пара с загрязнителем trust-drift воспроизвела все три; без него — `3 passed`. Непрерывный RESIDUE = **58**.

Запись, пережившая тест, но без найденных жертв: `test_p011_json_artifacts_…`, `test_simulator_artifacts_events_ndjson`, `test_simulator_sse_replay_410` (строки `simulator_runs`), модули step5a/5b/5c (`integrity_checkpoints`, одна строка `integrity_audit_log`).

**Попутно, не разобрано:** `test_simulator_sse_real_smoke.py`, зелёный в полном прогоне и в 2a, первым тестом процесса в паре **упал** с `SerializationError` (40001); тем же 40001 падали оба других симуляторных загрязнителя (они и в каталоге 2a — класс 40001, #47, #48). Зависимость от порядка не мерялась.

### 9.7 Разбивка оставшихся 133 — вход для разделения 2b

| класс | тестов | файлов | где чинится |
|---|---|---|---|
| RESIDUE | 55 | 18 | у **пяти загрязнителей** (9.6), а не у 18 жертв: жертвы в изоляции зелёные |
| VIS | 24 | 7 | без изменений, раздел 8 |
| CLR | 22 | 10 | 9 файлов раздела 3 + `integration/test_scenarios.py`; в `test_p015_t1544_operator_stop_refuses_money.py` теперь 2 CLR вместо CLR + FIXA |
| DIALECT-SQL | 11 | 3 | без изменений |
| SQLITE-MECH | 10 | 4 | без изменений |
| SCHEMA-CHECK | 3 | 2 | без изменений |
| 40001 | 3 | 3 | без изменений |
| DIALECT-PREMISE | 2 | 1 | без изменений |
| TXTIME | 1 | 1 | `integration/test_payments_list_filters.py` |
| HANG | 1 | 1 | без изменений, раздел 5 |
| DIALECT-MESSAGE | 1 | 1 | без изменений |
| **сумма** | **133** | **44** | |

Оба файла T1525/T1526 инвентарь уже назначил `delete`; два симуляторных загрязнителя сами в классе 40001. Что перевод или удаление этих файлов снимает и их жертв — **вывод из пар, а не измерение после удаления**.

### 9.8 Чего перезамер не измерил

- **Режим B** не перезамерялся: колонка «switch B» раздела 8 — от `ea14726`.
- **Непрерывная форма** снята с зондом: её стена (974 с) несравнима, числа исходов сравнимы.
- **Механизм записи загрязнителей в обход внешней транзакции** — не прослежен по коду, только эффект.
- **40001 у `test_simulator_sse_real_smoke.py` первым в процессе** — один прогон, не повторялся.
- **`simulator_runs.seed` вне int32** — наблюдение из логов, отдельным тестом не проверено.
- **Файлы на собственном SQLite** (раздел 6) — по-прежнему отсутствующее измерение Postgres.
- **Linux и CI** не мерялись.
