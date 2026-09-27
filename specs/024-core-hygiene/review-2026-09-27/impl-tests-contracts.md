# impl-tests-contracts — тестовый актив backend, OpenAPI, миграции, скрипты гейта

HEAD: 4119ace. Режим: только чтение, тесты не запускались. Исполнялось три вещи: `pytest --collect-only` (только сбор, без прогона; фиктивный URL `geov0_test_reviewcollect` на порту 1, к БД не подключался), `scripts/check_alembic_heads.py` и импорт `app.main` ради `app.openapi()`.

Прочитано целиком: `tests/conftest.py` (772), `pytest.ini` (83), `scripts/verify_local.ps1:120-250`, `scripts/check_alembic_heads.py`, `tests/p019_support.py`, `tests/p020_support.py:1-50`, `tests/unit/test_postgres_test_taxonomy.py` (246), `tests/unit/test_payment_timeouts.py` (208), `specs/017-postgres-only-engine/t1706-inventory.md` (разделы 0–2 и 6–9).
Инвентаризировано скриптами. AST прошёл по всем 355 `.py` под `tests/` (1705 тест-функций), сбор pytest дал 3207 элементов. Проверено: ассерты, тривиальные ассерты, skip/xfail/skipif, маркеры, фикстуры и их дубли, дубли тел и имён, SQLite/PG-пары, ссылки кода на удалённые механизмы (без докстрингов), `monkeypatch(..., raising=False)` с разрешением цели, импорты `app.*`. Скрипты лежат в `scratchpad/review/tc/`.
Выборочно прочитаны заголовки и ключевые участки примерно 35 модулей (они названы в находках), а также `tests/contract/test_openapi_contract.py:1-110, 440-750, 850-880, 1440-1560`, `migrations/versions/004, 007, 018, 030, 031`, `app/api/v1/trustlines.py:79-88`, `app/api/v1/admin.py:108-135`.
Не прочитано: тела примерно 300 тестовых модулей построчно (по заданию читал только по сигналам); `migrations/versions/*` целиком (только grep и разбор регулярными выражениями); `tests/contract/openapi_response_conformance.py`; `tests/migrated_schema.py`.

## Summary

Тестовый актив — 356 tracked-файлов и **114 102 строки** против **39 858** в `app/`, то есть в 2,86 раза больше. Pytest собирает 3207 элементов, дефолтный тир — 3186 (21 помечен `slow`).

**Вакуумных тестов в узком смысле почти нет.** 11 функций без ассертов, и все они проверяют через исключение: «не бросает» либо `_SuperSimFailure`. Ещё 15 функций содержат только тривиальные ассерты. `monkeypatch(..., raising=False)` бьёт мимо цели ровно в двух местах, и обе цели — атрибуты `app.state`, которые проставляет lifespan.

**Главная проблема актива — балласт трёх видов.**
1. **27 % элементов тира (864 из 3207) лежат в 41 файле, который вообще не импортирует `app`.** Это гарды инструментов, документации, CI и лаунчеров. Только два файла из них дают 5 962 строки и 430 элементов: самописный парсер PowerShell внутри Markdown для проверки документации и тесты PowerShell-лаунчера.
2. **Недоделанные остатки 017–020.** SQLite/PG-двойники теперь оба идут на PostgreSQL. Гарды охраняют формы, которых больше нет. Эксперименты 019/020 лежат в `tests/`, причём тесты неотгружаемых детекторов 020 входят в обязательный гейт. Ещё 26 недостижимых dialect-skip.
3. **17 % строк тестов — докстринги и комментарии** (13 468 + 6 337 строк). Часть из них утверждает несуществующее: «TIER. SQLite», «RED TODAY BECAUSE: таблицы нет», `PaymentEngine.commit`, `journal.py`.

**Контракты в порядке.** У Alembic один head (`031_drop_prepare_locks`). Пути и методы OpenAPI совпадают с кодом точно. 280 известных расхождений схем заморожены храповиками. Отсутствие удалённых в 018 эндпоинтов держит тест.

**Что делать первым:** один «срез выноса» по §14 — TC-02, TC-04, TC-05, TC-06 и TC-03, примерно 5–7 тысяч строк без потери покрытия. По TC-01 решает оркестратор или владелец: вынести тесты инструментов из backend-тира либо сузить их.

## Findings

| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| TC-01 | P2 | test-asset | tests/unit/test_backend_marker_policy.py:47-1590; tests/unit/test_run_full_stack_database_url_redaction.py | 41 файл без `import app`: 864 из 3207 элементов (27 %), 14 395 строк. Из них два файла — 5 962 строки и 430 элементов: самописный парсер PowerShell-в-Markdown для гарда документации и тесты лаунчера | none | M |
| TC-02 | P2 | duplication | tests/unit/test_p015_t1524_…:1-13; tests/unit/test_p015_t1533_…; tests/unit/test_p015_b4_wrong_writer_…:24 | После 017 SQLite/PG-двойники оба идут на PostgreSQL: 8 пар, 14 318 строк. У t1524/t1533 совпадают имена тестов, у wrong_writer тела c5/c6 похожи на 0.76–0.84 | none (017 закрыта, §7 инвентаря не исполнен) | M |
| TC-03 | P3 | dead-code | tests/conftest.py:54-72 против 26 `pytest.skip` в 19 файлах; conftest.py:159-161, :325-326, :423-428 | Ветки «не Postgres → skip» недостижимы: conftest отказывает ещё до сбора. Гард сам это признаёт (`test_postgres_test_taxonomy.py:131`) | none | S |
| TC-04 | P2 | test-asset | tests/unit/test_postgres_test_taxonomy.py; test_p015_b4_counterexample_marker_…; test_p019_no_intermediate_payment_state_is_written.py:18-21; test_p019_lock_primitives_…:24-28 | Гарды над удалёнными формами, которые уже перекрыты `--strict-markers`, CHECK миграции 030 и ImportError. Проверки job'а `required-backend` продублированы в трёх файлах | частично t1706 (вердикт delete для taxonomy не исполнен) | S |
| TC-05 | P3 | test-asset | tests/p019_t1904_owner_lock_contention_probe.py:1-12; tests/integration/test_p019_t1908_*:65/:91 | 2 569 строк измерительных инструментов для уже закрытых решений: slow-эксперименты T1908 (17 элементов) не входят ни в один гейт, плюс 4 несобираемых зонда и recorder | none | S |
| TC-06 | P2 | test-asset | tests/integration/test_p020_experimental_detectors_postgres.py; tests/unit/test_p020_rank_bound_diagnostic.py; scripts/p020_*.py, measure_p020_*.py | 75 элементов обязательного гейта тестируют детектор, которого нет в продукте (скрипты — 1 951 строка). 023 называет только import-guard | 023 (неполно) | S |
| TC-07 | P3 | docs-drift | test_p015_b4_wrong_writer_…:24; test_p015_t1523_replay_…:25; test_p015_t1523_the_commit_landed_…:37; 25 строк «RED TODAY» в 5 файлах; conftest.py:535-536; test_p019_no_durable_intermediate_state_postgres.py:3-5 | Докстринги описывают удалённое как текущее: SQLite-тир, отсутствующие таблицы журнала, `PaymentEngine.commit`, `engine.py _run_uow_with_retry` | none | S |
| TC-08 | P3 | dead-code | pytest.ini:70-78 | Фильтр `ignore:nested transaction already deassociated…` обоснован удалённым `journal.py::_release_refused_nested`. Излучателя больше нет, а новое предупреждение фильтр заглушит | none | S |
| TC-09 | P3 | test-asset | tests/unit/test_payment_timeouts.py:89-92, :181-195 | BACKLOG отнёс файл к корзине D («уходит с механизмом 019»). 019 закрыта, а файл жив: подменяет `_bind_payment`/`_apply_payment`, подделывает коммит через UPDATE, использует реальные `asyncio.sleep` | BACKLOG «Класс 2 из среза T1548» (не исполнено) | S |
| TC-10 | P3 | readability | test_clearing_payment_prepare_interlock_postgres.py; test_concurrent_prepare_routes_bottleneck_postgres.py; test_payment_engine_advisory_locks_execute.py; *_through_the_tick_sqlite.py ×2; test_admin_equivalent_input_validation.py:70; test_integrity_endpoints.py:163 | Имена файлов и тестов называют удалённые механизмы (prepare, interlock, engine, sqlite), хотя содержимое уже переписано | none | S |
| TC-11 | P3 | duplication | tests/conftest.py:659-716 и :719-772; фикстуры `factory` ×11, `migrated_url` ×9 | 11 способов получить сессию или движок. 38 сидинг-хелперов (1 558 строк) плюс 53 теста строят участников и линию руками. Две почти одинаковые auth-фикстуры | none | S (только auth) |
| TC-12 | P3 | architecture | api/openapi.yaml ↔ app; tests/contract/test_openapi_contract.py:33,:73,:110,:314,:427,:441; app/api/v1/trustlines.py:79-88 | Пути и методы совпадают, 280 семантических расхождений заморожены храповиками. По независимому сравнению 29 из 95 success-ответов расходятся полями или `required`, у 6 операций нет `response_model` | BACKLOG «Без владельца с 2026-08-24 — api/openapi.yaml…» (о владельце, не о дрейфе) | M |
| TC-13 | P3 | dead-code | api/openapi.yaml:4845, :4962, :4579-4581; app/schemas/admin.py:118; common.py:46,:50; payment.py:71 | Мёртвые схемы с обеих сторон: AdminAuditLogResponse, SignedRequest, PaginationParams, PaymentDetail. Комментарий в каноне ссылается на исчезнувший `_ACTIVE_PAYMENT_TX_STATES` | none (enum PREPARED — это решение Q2/П4) | S |
| TC-14 | P3 | architecture | app/db/models/transaction.py:32 против migrations/versions/007:32; 002_event_log.py:26; test_p018_b_schema_parity_postgres.py:47 | ORM и миграции расходятся: 29 индексов `ix_*` против `idx_*`, `uq_` против `ux_`. Только в миграциях есть таблица `event_log` (её никто не читает), GIN- и payload-индексы, `chk_equivalents_code_format`. Паритет проверяется лишь для 4 таблиц журнала | BACKLOG «2026-09-11 — имена ограничений внешних ключей…» (только FK) | M |
| TC-15 | P3 | dead-code | migrations/versions/{004,005,006,007,011,012,014,017,019,023,024,027,028,029,030,031}; 022:159; 026:166 | Dialect-ветки в 18 применённых миграциях недостижимы: alembic отказывает на SQLite. Миграции неизменяемы (§3), поэтому только документировать | none; Contract: yes | — |
| TC-16 | P3 | dead-code | scripts/{analyze_simulator_fixture_scenarios, check_latest_simulator_artifacts, run_clearing_demo10_100ticks, run_reference_simulator_run, smoke_simulator_api, generate_scenario_events}.py | 2 558 строк скриптов, на которые нет ни одной ссылки из ps1, package.json, CI, docker, tests и docs. В гейт входят только 3 скрипта из 21 | none | S |
| TC-17 | P3 | dead-code | tests/p019_support.py:29 | Маркер `target_xfail` из 019 не использует ни один тест. На main 7 xfailed (6 — R-020-1, 1 — T1549), а не 20: ещё 13 добавляет ветка 023-a3 | 023 / BACKLOG «Отложено из 015…» (для самих xfail) | S |

## Детали

### TC-01 — Четверть тира не тестирует приложение
Evidence (AST-скрипт `tc/noapp.py` и сбор pytest): 41 тестовый файл без единого `import app.*`, 14 395 строк; в них 864 из 3207 собранных элементов.
- `tests/unit/test_backend_marker_policy.py`: 2 940 строк, 214 элементов, 36 приватных функций парсера (`_strip_powershell_block_comments` :59, `_split_powershell_statements` :109, `_powershell_brace_context` :143, `_tokenize_powershell_arguments` :1203 и другие). Файл читает README, AGENTS.md и docs/ru|en|pl (`_ACTIVE_OPERATIONAL_DOCS` :14-25) и разбирает примеры команд в них. У файла 48 коммитов.
- `tests/unit/test_run_full_stack_database_url_redaction.py`: 3 022 строки, 216 элементов. Проверяет поведение `scripts/run_full_stack.ps1` и `run_local.ps1`; 47 тестов под `skipif(not _POWERSHELLS)`.
- Дальше идут `test_p017_t1712_community_descriptions.py` (531 строка), `test_p017_t1710_launcher_database_boundary.py` (53 элемента), `test_p017_t1713_community_recipes.py` (67), `test_p017_t1711_seed_recipe_refuses.py` (45).

**Почему это важно.** §19.5 фиксирует рост тестов с 29 до 107 тысяч строк как петлю; здесь видно, куда этот рост осел. Парсер документации противоречит §11 («без явной просьбы не добавляйте тесты на … формулировки документов») и §19: противник у этого механизма — неверный пример команды в markdown. Ядро эти тесты не защищают, но расходуют время тира, чтение и ревью.
**Минимальное исправление** (новый механизм не нужен):
- (а) вынести тесты лаунчера и документации в отдельный неблокирующий или ручной job, как `container-smoke`: одна строка в `quality.yml` и маркер;
- (б) свести гард документации к одной проверке «в активных документах нет голого `python -m pytest`» — это его исходная цель по §5.

Выбор между (а) и (б) — это развилка о ценности документов, решает оркестратор.
**Что может сломаться:** `test_p017_required_gate_runs_on_postgres.py` и `test_quality_workflow_schedule.py` проверяют форму `quality.yml`, поэтому их надо обновить в той же правке. Contract: no.

### TC-02 — SQLite/PG-двойники живут вдвоём на одном движке
Evidence (`tc/twins.py`): 8 пар `X.py` / `X_postgres.py`. После 017 обе половины каждой пары исполняются на PostgreSQL, потому что `tests/conftest.py:54-72` иначе отказывает.
- `tests/unit/test_p015_t1524_equivalent_deletion_keeps_obligations.py:1` начинается с `"""T1524, the SQLite half: …"""`. Все три теста называются так же, как в `…_postgres.py`: `test_the_database_refuses_to_delete_an_equivalent_that_carries_debt`, `test_the_route_refuses_when_its_usage_count_misses_a_debt`, `test_an_unused_equivalent_still_deletes`.
- `tests/unit/test_p015_t1533_…` против `…_postgres.py`: совпадают 3 имени из 4/5. Unit-половина строит схему через `create_all` (фикстура `model_url`). Это та «model half», которую инвентарь t1706 в §7 предлагал влить в PG-половину.
- `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` (1 450 строк) против `…_postgres.py` (1 132): четыре пары тестов c5/c6 с текстовым сходством тел 0.76–0.84. Unit-файл сам пишет «TIER. SQLite, the default tier, which is also the application's default `DATABASE_URL` (`app/config.py:55`)» (:24).
- Остальные пары: `b4_entries_and_money` (752 / 2 792, общих имён нет), `step5a` (1 020 / 428), `step5b` (1 056 / 587), `t1551` (299 / 133), `p1_clearing_run_perimeter` (460 / 199). Вместе 16 файлов и 14 318 строк.

**Почему это важно:** одно свойство дважды проверяется на одном движке, и любая правка контракта идёт через два файла.
**Минимальное исправление:**
- t1524-unit и t1533-unit удалить после сверки ассертов. По §11 это safe delete как «дублируется более сильным тестом»: PG-половины работают в режиме B с настоящими коммитами. Для t1533 отдельно решить, нужна ли проверка того, что ORM объявляет RESTRICT при `create_all` (см. TC-14).
- wrong_writer и b4_entries — verify first: поштучно сопоставить c4/c5/c6/c13/c17/c18 и оставить сильнейшую форму.
- В остальных парах общих имён нет, половины дополняют друг друга. Их не трогать.

Covered-by: none — 017 закрыта, рекомендация §7 инвентаря не исполнена.

### TC-03 — Недостижимые dialect-skip
Evidence: `tests/conftest.py:66-72` поднимает `pytest.UsageError` ещё до сбора, если backend не `postgresql`. При этом в 19 файлах стоят 26 вызовов `pytest.skip`. Пример из `tests/integration/test_clearing_commit_replay_postgres.py:69-71`:
```
dialect = db_session.get_bind().dialect.name
if dialect not in {"postgresql", "postgres"}:
    pytest.skip("Postgres-only: SERIALIZABLE clearing reconciliation")
```
`tests/unit/test_postgres_test_taxonomy.py:131` признаёт это прямо: «the PostgreSQL-only dialect skips, which are now unreachable». Внутри самого conftest та же мёртвая ветка встречается трижды: `:159-161` (`return {}` для не-PG), `:325-326` (`init_db`) и `:423-428`.
**Почему это важно:** мелочь, но skip — именно та форма, которую §9 требует держать под контрпроверкой, а 26 таких мест читаются как «на другом движке это пропускается».
**Исправление:** удалить ветки (около 80 строк); предпосылку оставить в докстринге модуля.

### TC-04 — Гарды над формами, которых нет
Evidence:
- `tests/unit/test_postgres_test_taxonomy.py` — 246 строк, 4 теста. В инвентаре t1706 у него вердикт **delete**, но файл на месте.
  - `test_no_module_takes_itself_out_of_the_postgres_tier` ищет маркер `postgres`. Применённый незарегистрированный маркер и так роняет сбор через `--strict-markers` (`pytest.ini:43`).
  - `…whole_tier_without_file_allowlist` (:170-201) и `…production_migration_entrypoint` (:204-222) повторяют `test_quality_workflow_schedule.py:138-156` (сервис postgres, `check_alembic_heads.py`, `docker-entrypoint.sh true`) и `test_p017_required_gate_runs_on_postgres.py:300` (запрет `-BackendSelector`).
  - Итог: `quality.yml` парсят три файла тремя способами — `_indented_blocks` на регулярках в одном и `yaml.safe_load` в двух других.
- `tests/unit/test_p015_b4_counterexample_marker_is_not_a_hiding_place.py` — 153 строки. Маркер снят 2026-09-12. Ценность осталась у одной проверки: «pytest.ini не регистрирует маркер».
- `tests/unit/test_p019_no_intermediate_payment_state_is_written.py` — 204 строки. Собственный докстринг (:18-21) говорит, что это закрывает «the immediate CHECK of migration `030` … whatever wrote it». Значит, AST-гард формы перекрыт более сильным ограничением БД. Импорт `PaymentEngine`/`PrepareLock` после удаления модулей и так даёт ImportError.
- `tests/unit/test_p019_lock_primitives_live_in_money_boundary.py` — 516 строк. Правило 3 (:24-28, «no import of the deleted module») перекрыто ImportError, правило 2 — во многом тоже. Реальная ценность — правило 1: у примитивов один дом.

**Исправление:** удалить taxonomy (всё полезное в нём уже покрыто двумя другими файлами); marker-гард свести к одной проверке `pytest.ini`; p019-гарды сузить до правила 1 и строки о CHECK 030. Это примерно 600–900 строк. Единственным владельцем формы `required-backend` должен остаться `test_p017_required_gate_runs_on_postgres.py`.

### TC-05 — Инструменты измерения закрытых решений в `tests/`
Evidence:
- `tests/integration/test_p019_t1908_lock_removal_experiments_postgres.py:1-4` сам говорит «NOT A GATE OF THE CURRENT CODE - a measurement that decides a fork», на `:65` стоит `pytestmark = [pytest.mark.slow]`. То же у `test_p019_t1908_clearing_starvation_probe_postgres.py:91`.
- Эти 17 элементов не запускает ни один job: `-IncludeExpensive` в CI нет, а `simulator-super-smoke` красный (BACKLOG).
- Несобираемые зонды: `tests/p018_t1809_operation_cost_probe.py` (415 строк), `tests/p019_t1903_statement_sequence_probe.py` (500), `tests/p019_t1904_owner_lock_contention_probe.py` (356; импортёров нет, упомянут только в спеке 019), `tests/stage2_catalogue_recorder.py` (151), `tests/p019_locks_off.py` (103, нужен только T1908). Всего 2 569 строк.

**Почему это важно:** развилки, ради которых всё это писалось, решены (`KEEP-EQUIVALENT-LOCK`, T1909). Код дрейфует вместе с продуктом, но никто его не исполняет.
**Исправление:** verify first. Спека 019 на :500 называет эти файлы «историческими зондами», которые можно перезапустить. Честный исход по §19.4 — удалить и записать в Changelog 019 SHA последнего рабочего дерева, чтобы при нужде восстановить их через `git show <sha>:path`.

### TC-06 — Обязательный гейт тестирует неотгружаемый детектор 020
Evidence:
- `tests/integration/test_p020_experimental_detectors_postgres.py:1-3`: «The detectors live in `scripts/p020_experimental_detectors.py`, outside the production path». 26 элементов, маркера `slow` нет.
- `tests/unit/test_p020_rank_bound_diagnostic.py:1-16`: «DIAGNOSTIC … not acceptance». 49 элементов.
- Проверяемый код — `scripts/p020_experimental_detectors.py` (591 строка); рядом лежат `measure_p020_detector_cost.py` (900) и `measure_p020_dfs_acceptance.py` (460).
- `specs/023-clearing-as-flow/spec.md:125` в списке «обязаны остаться зелёными» называет только `test_p020_experimental_detectors_are_not_imported_by_production.py`. О двух тестах и трёх скриптах выше в спеке ничего нет.

**Почему это важно:** 75 элементов обязательного PR-гейта держат код, который 020 закрыла как «не заменён», а 023 заменяет планировщиком MTCS.
**Исправление:** дописать в 023 (в срез d или в закрытие) явную строку об удалении `test_p020_experimental_detectors_postgres.py`, `test_p020_rank_bound_diagnostic.py`, import-guard и трёх `scripts/*p020*`.

### TC-07 — Докстринги утверждают несуществующее
Evidence:
- `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py:24`: «TIER. SQLite, the default tier, which is also the application's default `DATABASE_URL` (`app/config.py:55`)».
- `tests/integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py:25` и `tests/unit/test_p015_t1523_the_commit_landed_then_the_caller_failed.py:37`: «TIER. SQLite».
- 25 строк «RED TODAY BECAUSE: `debt_journal_entries` does not exist» в пяти файлах: `test_p015_b4_entries_and_money{,_postgres}.py`, `test_p015_b4_wrong_writer…{,_postgres}.py`, `test_p015_t1526_nan_amount_reaches…_postgres.py`. Все эти тесты зелёные с 2026-09-12.
- `tests/conftest.py:535-536`: «the payment engine opens its own begin_nested() (engine.py _run_uow_with_retry, _apply_flow)». `engine.py` удалён в 019.
- `tests/integration/test_p019_no_durable_intermediate_state_postgres.py:3-5`: «`EngineCommitBarrier` holds one `POST /payments` at the entry of `PaymentEngine.commit`». При этом сам класс в `tests/integration/p019_stand.py:317-321` уже говорит «since stage 4 it is `PaymentService._apply_payment`».
- Масштаб: докстринги и комментарии занимают 19 805 из 114 457 строк, 17 %.

**Почему это важно:** по §1 и §13 документ, противоречащий коду, хуже отсутствующего. Следующий агент будет планировать, исходя из «TIER. SQLite».
**Исправление:** одна правка на семейство — заменить утверждение датированной исторической пометкой, без переписывания нарратива.

### TC-08 — Фильтр предупреждений без излучателя
Evidence, `pytest.ini:70-78`:
```
# ... (`app/core/ledger/journal.py::_release_refused_nested`, design v2 §1.2).
    ignore:nested transaction already deassociated from connection
```
`git grep -n "deassociat\|_release_refused_nested" -- app tests` ничего не находит; в `app/core/ledger/` остались только `book.py` и `reconciliation.py`.
**Почему это важно:** если предупреждение SQLAlchemy появится по новой причине (например, двойной откат savepoint в `Book`), фильтр его скроет. Это anti-vacuum из §9.
**Исправление:** удалить фильтр вместе с комментарием и прогнать тир. Если предупреждение появится, записать его настоящий источник.

### TC-09 — `test_payment_timeouts.py` пережил свой механизм
Evidence:
- BACKLOG («Класс 2 из среза T1548») относит файл к корзине **D** 019: «тест уходит вместе с механизмом». 019 закрыта, а файл изменён 2026-09-25 и остался в дереве.
- `monkeypatch.setattr(service, "_bind_payment", _slow_prepare)` с реальным `asyncio.sleep(0.05)` (:89-92).
- `_commit_then_hang` пишет `update(Transaction)…values(state="COMMITTED")` мимо платежа и затем спит 0.2 s (:181-195). То есть тест подменяет тот самый путь, который проверяет.
- `PREPARE_TIMEOUT_SECONDS` и `COMMIT_TIMEOUT_SECONDS` всё ещё читаются в `app/core/payments/service.py:1365-1366`.

**Исправление:** verify first.
- Если фазовые тайм-ауты после 019 ещё существуют, переписать тест на барьер (как `p019_stand.EngineCommitBarrier`), без подделки коммита.
- Если нет — удалить тест вместе с настройками (это уже зона app).

Настоящий коммит в этом сценарии уже покрыт ячейкой 7 T1523.

### TC-10 — Имена называют удалённое
Evidence:
- Файлы: `test_clearing_payment_prepare_interlock_postgres.py` (докстринг :3 сам говорит «There is no `PaymentEngine` and no durable `PREPARED`»), `test_concurrent_prepare_routes_bottleneck_postgres.py`, `test_payment_engine_advisory_locks_execute.py`, `test_payment_prepare_{error_taxonomy,capacity_policy}.py`, `p019_interlock_support.py`.
- `test_p015_{step5c_hold,t1544_operator_stop}_through_the_tick_sqlite.py` — оба работают на mode-B PostgreSQL (:3-4 и :23).
- Тесты `…attach_utc_to_sqlite_timestamps` (:70) и `…serialize_sqlite_checkpoint_as_utc` (:163) на самом деле проверяют timestamptz.
- В именах тестов: «prepare» — 16, «interlock» — 7, «engine» — 8, «sqlite» — 6.

**Исправление:** переименовывать при следующем касании. Массовое переименование сейчас сломает якоря в спеках, поэтому сейчас только документировать.

### TC-11 — Множественность фикстур и сидинга
Evidence:
- Способов получить БД 11:
  - `db_session` (режим A);
  - `db_session` + `MODE_B` (39 файлов);
  - `committed_database` (46);
  - `committed_session` — им пользуется только его собственный тест, `test_p017_t1702_mode_b_fixture_postgres.py:222,301`;
  - `tier_sessions_on_a_clone` (29);
  - `module_clone` (11);
  - `p019_stand` (10);
  - `make_pg_client_fixture` (5);
  - `TestingSessionLocal` напрямую (35);
  - свой `create_async_engine` (49);
  - свой `async_sessionmaker` (37).
- Одноимённые фикстуры: `factory` ×11 (9 разных тел), `migrated_url` ×9 (тела различаются только литералом суффикса, `test_p018_b_journal_guards_postgres.py:52-55`), `serializable_factory` ×5, `engine` ×5, `stand` ×5.
- Сидинг: 38 хелперов (1 558 строк) плюс 53 теста строят Equivalent, Participant и TrustLine вручную.
- `auth_headers` (:659-716) и `auth_user` (:719-772) примерно на 50 строк повторяют регистрацию и логин.
- Неиспользуемых фикстур нет: 25 кандидатов, найденных AST, оказались тестами под `usefixtures`.

**Почему это важно:** не очень. §2 запрещает строить фреймворк ради полноты, а режимы A и B различаются по делу.
**Исправление:** только определить `auth_headers` через `auth_user["headers"]`. Остальное документировать, не трогать.

### TC-12 — OpenAPI ↔ код
Evidence:
- `tests/contract/test_openapi_contract.py:1452` сверяет `generated_versioned_paths == canonical_paths`, а для каждой операции — методы, идентичности параметров и наличие тела.
- Я независимо прогнал `app.openapi()` (`tc/oa.py`): путей, которые есть только с одной стороны, — 0. Вне `/api/v1` лежат `/health`, `/healthz`, `/health/db`, `/metrics`.
- Храповики держат 280 расхождений: PARAMETER 22, TRANSPORT_HEADER 66, REQUEST 13, SUCCESS 62, ERROR_RESPONSE 51, SECURITY 66.
- Моё сравнение 95 success-ответов: у 29 расходятся верхнеуровневые поля или `required`. Примеры:
  - `/participants/*`: сгенерированная схема требует `created_at, display_name, public_key, updated_at`, канон — нет;
  - 13 операций `/simulator/*`: канон требует `api_version`, сгенерированная схема — нет.
- У 6 операций нет `response_model`, и форму задаёт только YAML: `DELETE /trustlines/{id}` (`app/api/v1/trustlines.py:79-88`, `return {"status": "success", "message": "Trustline closed"}`), `/simulator/session/ensure`, `/simulator/admin/runs`, `/simulator/admin/runs/stop-all`, `/healthz`, `/health/db`.
- Остатки 018: `/integrity/repair/net-mutual-debts` и `…/cap-debts-to-trust-limits` отсутствуют в обоих документах, это держит `test_openapi_contract.py:861-869`.

**Почему это важно:** «авторитет №1» и код расходятся в 280 записанных местах, и сокращать этот список некому.
**Исправление:** не патчить, передать в очередь владельца канона. Contract: yes.

### TC-13 — Мёртвые куски контракта
Evidence:
- Компоненты YAML без `$ref`: `AdminAuditLogResponse` (`api/openapi.yaml:4845`; сам канон на :7553 пишет «not referenced by any operation») и `SignedRequest` (:4962).
- Pydantic-классы, которые нигде не используются: `AdminAuditLogResponse` (`app/schemas/admin.py:118`), `SignedRequest` (`common.py:46`), `PaginationParams` (`common.py:50`), `PaymentDetail` (`payment.py:71`). Проверено `git grep -nw <Class> -- app tests api`.
- Комментарий канона `api/openapi.yaml:4579-4581` ссылается на `_ACTIVE_PAYMENT_TX_STATES (app/api/v1/admin.py:113-120)`, но такого имени в `app/` нет.
- Enum PREPARED в каноне (:4429-4439, :4599) — это решение Q2/П4, поэтому как находку я его не заявляю.

**Исправление:** удалить 4 класса и 2 компонента; комментарий в каноне переписать. Contract: yes.

### TC-14 — ORM и миграции строят разные схемы
Evidence (`tc/mig.py`):
- У `MetaData` нет `naming_convention`. Поэтому 29 индексов называются `ix_*` в ORM и `idx_*` в миграциях 001, 002 и 009.
- `app/db/models/transaction.py:32` объявляет `UniqueConstraint(name='uq_transactions_initiator_type_idempotency')`, а миграция `007:31-36` создаёт unique index `ux_transactions_initiator_type_idempotency`.
- Только в миграциях существуют:
  - таблица `event_log` (002:26): модели нет, в `app/` ни одного читателя;
  - `simulator_run_metrics_float_archive` (018);
  - GIN-индексы (006) и payload-btree (011);
  - `ix_transactions_initiator_idempotency{,_active}` (004);
  - `ix_simulator_runs_created_at` (013);
  - `chk_equivalents_code_format` (007:41-46).
- Паритет двух способов сборки проверяется только для `debts` и трёх таблиц журнала (`test_p018_b_schema_parity_postgres.py:47`) и для одного CHECK (t1530).

**Почему это важно:** гейт строит схему миграциями (`verify_local.ps1:152-153`), так что на него это не влияет. Но `create_all` используют прямой pytest (conftest :272-284) и «model half» из TC-02 — они работают на схеме, которой нет в проде. `event_log` — мёртвая таблица в проде.
**Исправление:** документировать. Убрать `event_log` можно только новой миграцией; это Contract: yes.
Covered-by: BACKLOG покрывает только FK.

### TC-15 — Dialect-ветки в применённых миграциях
Evidence:
- 18 миграций (перечислены в таблице) содержат `if bind.dialect.name != "postgresql": return`; в 022:159 и 026:166 стоит `sqlite_where=`.
- `004:12` объясняет: «conditional by dialect to keep SQLite tests working».
- Alembic на SQLite отказывает (`test_alembic_postgres_only.py:29`), так что эти ветки недостижимы.

Попутно проверено:
- Head один: `031_drop_prepare_locks`.
- Заглушек в `downgrade` нет: 029, 030 и 031 делают настоящий откат.
- Два upgrade меняют данные: `010:19` (`DELETE FROM debts WHERE amount = 0`) и `018:177` (удаление после архивирования, с проверкой round-trip на :168-174).

**Исправление:** не патчить, миграции неизменяемы по §3.

### TC-16 — Скрипты без вызывающих
Evidence:
- В гейте участвуют только три скрипта из 21: `validate_pytest_selectors.py`, `validate_test_database_url.py` и `check_alembic_heads.py`.
- Без единой ссылки из ps1, package.json, `.github`, docker, `app`, tests и docs:
  - `analyze_simulator_fixture_scenarios.py` — 193 строки, последний коммит 2026-02-22;
  - `check_latest_simulator_artifacts.py` — 199, 2026-01-30;
  - `run_clearing_demo10_100ticks.py` — 458, 2026-02-15;
  - `run_reference_simulator_run.py` — 613, 2026-03-02;
  - `smoke_simulator_api.py` — 114, 2026-02-18.
- `generate_scenario_events.py` (981) упоминается один раз, в specs.
- Эндпоинты, которые скрипты вызывают, в каноне есть, то есть скрипты не сломаны.

**Вердикт по §14:** verify first, удалять одним отдельным cleanup-коммитом.

### TC-17 — xfail: сколько и почему
Evidence:
- На main 7 xfailed:
  - `test_p1_trustline_reopen_postgres.py:169` — strict, T1549 (500 вместо 409), учтён в BACKLOG;
  - `target_xfail_020` на трёх тестах, каждый с двумя параметрами (:72, :152, :227), — их судьбу решает 023.
- Число 20 в `specs/README.md:296` относится к ветке `claude/023-a3`, которая добавляет `target_xfail_023`.
- Все xfail строгие и с `raises=TargetMismatch`, поэтому сломанный стенд за ожидаемый провал не засчитается.
- `target_xfail` из 019 (`tests/p019_support.py:29-36`) не использует никто. Фабрика существует в трёх копиях (019/020/023).
- skipif: 47 на наличие PowerShell и 2 на ОС. Навсегда выключенных нет.
- Четыре CREATEDB-skip из §4 инвентаря уже превращены в отказ (например, `test_p015_step5a_reconciliation_postgres.py:185`).

## Сверка с инвентарём t1706 (HEAD `4310603` → `4119ace`)
- Файлов `test_*.py` было 299, стало 331: добавлено 68, ушло 36.
- delete (13): исполнено 11. Осталось два: `test_simulator_clearing_no_deadlock.py` (осознанно перенесён, :21) и `test_postgres_test_taxonomy.py` (не исполнено, TC-04).
- transfer (26): 25 перенесены на месте с прежними именами, 1 переименован. Рекомендация §7 не исполнена (TC-02).
- keep (260): 24 удалены вместе с механизмами 018/019 — это ожидаемо.

## Кандидаты на сокращение без потери покрытия

| Группа | Файлы / элементы / строки | Основание §11 | Вердикт |
|---|---|---|---|
| TC-06 детекторы 020 | 3 теста и 3 скрипта / 78 / 669 + 1 951 | проверяет неотгружаемый код, который заменяет 023 | удалить в срезе (d) 023 |
| TC-05 зонды и эксперименты | 7 / 17 slow / 2 569 | решённая развилка, ни в одном гейте | удалить, записав SHA в 019 |
| TC-02 t1524/t1533 unit | 2 / 7 / 352 | дублируется более сильным PG-тестом | safe delete после сверки |
| TC-02 wrong_writer, b4_entries | 4 / ~24 / ~6 100 | частичное дублирование | verify first |
| TC-04 гарды форм | 4 / ~15 / ~600–900 | перекрыты `--strict-markers`, CHECK 030, ImportError | удалить или сузить |
| TC-03 dialect-skip | 19 / — / ~80 | недостижимо | удалить ветки |
| TC-01 инструменты | 2 (+4) / 430 (+213) / 5 962 (+~1 400) | ядро не проверяет | вынести из тира или сузить — решает оркестратор |
| TC-16 скрипты | 6 / — / 2 558 | никем не вызываются | verify first |

## Что не проверено
- Тесты не запускались, поэтому ни одно «дублируется» не подтверждено мутацией. Сходство тел считалось текстом (difflib), а не поведением.
- Вакуумность в широком смысле не проверялась: параметризация, где все параметры идут одним путём, и хелперы-ассерты, которые ничего не проверяют.
- ORM и миграции сравнивались регулярными выражениями, без сборки схемы. Типы, nullability и `server_default` не сравнивались.
- OpenAPI сравнивался только по верхнему уровню success-ответов.
- `tests/contract/openapi_response_conformance.py` (800 строк) и `tests/migrated_schema.py` (719) не читались.
- `simulator-super-smoke` (3 slow-элемента, единственный сквозной тест симулятора; job красный) не разбирался.

## Оценка направления плана
- **023** должна явно назвать удаление всего, что относится к экспериментальным детекторам 020 (TC-06). Иначе оно переживёт замену так же, как механизмы 017–019 пережили в тестах своё удаление (TC-02, TC-04, TC-09).
- **Правило тестового актива** срабатывало на удаление механизма, но не на сворачивание двойников и гардов после него. Вердикты t1706 исполнены так: delete — 11 из 13, transfer — 25 из 26, §7 — 0. Предлагаю в закрытие 021 и 023 добавить строку «двойники и гарды удалённого — список и вердикт».
- **021** правильно забирает адаптивный клиринг вместе с его тестами: 5 файлов, 2 639 строк.
