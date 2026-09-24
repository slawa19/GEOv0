# 018 / T1808 — манифест удаления по файлам и карта выживания по ассертам

- **Date:** 2026-09-24
- **Tree:** `main` `3a038d3` (стадия A слита). Всё ниже — чтение кода; ни один тест не запускался, ни одно число прогона здесь не утверждается.
- **Status authority:** документ — вход стадии B, а не её evidence. Каждая строка «TO WRITE» становится тестом стадии B; «SURVIVES» — утверждение, которое внешнее ревью стадии B (`T1810`, пункт 4) проверяет по коду.
- **Как собрано:** четыре независимых read-only прохода по непересекающимся группам файлов (механизм журнала; записи и деньги шага 4; сверка и удержание; уборка), затем сведение и выборочная перепроверка несущих фактов оркестратором (`_check_storable` — единственная проверка хранимости, `journal.py:479-519`; узлы SURVIVES для C5/C6 существуют; число вызывающих `purge_test_ledger`). Таблицы по ассертам оставлены на английском, в котором они собраны: это перечни `path:line`, а не проза.

## 1. Граница поиска и как её воспроизвести

Граница — **замыкание импортов**, а не grep одного имени. Спека (`spec.md:15`) задала grep прямых импортов; он даёт **18** файлов, а не 19: девятнадцатый, `tests/integration/test_integrity_repairs_atomicity.py`, удалён `T1806`. И этого grep'а мало: `tests/p015_b4_support.py` импортирует журнал лениво внутри `journal_api()`, и пять модулей, которые ходят к журналу только через него, grep не видит, а после удаления `journal.py` они **продолжат собираться** и падать уже в исполнении.

```powershell
# 1) прямые импортёры (18 на 3a038d3)
git grep -l -E "from app.core.ledger.journal import|import app.core.ledger.journal|from app.core.ledger import journal" -- tests
# 2) замыкание импортов: всё, что достаёт до journal.py через модули tests/ (скрипт ниже)
.\.venv\Scripts\python.exe t1808_closure.py . narrow   # 29 модулей: без захода через tests/debt_setup.py
.\.venv\Scripts\python.exe t1808_closure.py . all      # 104 модуля: включая 75, достающих до журнала только через debt_setup
# 3) вызывающие purge_test_ledger и сырой DML по debts и журнальным таблицам
git grep -l purge_test_ledger -- tests
git grep -n -i -E "(INSERT INTO|UPDATE|DELETE FROM|TRUNCATE)( TABLE)? +(debts|debt_operations|debt_journal_entries|debt_operation_equivalents)\b" -- tests
```

Скрипт замыкания (сохранить как `t1808_closure.py` вне дерева; в дерево он не коммитится, потому что после стадии B его цели нет):

```python
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1]); TARGET = "app.core.ledger.journal"; mods = {}
for p in sorted((root / "tests").rglob("*.py")):
    name = ".".join(p.relative_to(root).with_suffix("").parts).removesuffix(".__init__")
    mods[name] = p
def imports(p):
    out = set()
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import): out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module); out |= {f"{n.module}.{a.name}" for a in n.names}
    return out
graph = {m: imports(p) for m, p in mods.items()}
direct = {m for m, im in graph.items() if TARGET in im}
skip = set() if (sys.argv[2:] or ["narrow"])[0] == "all" else {"tests.debt_setup"}
reach, changed = set(direct), True
while changed:
    changed = False
    for m, im in graph.items():
        if m not in reach and m not in skip and im & (reach - skip):
            reach.add(m); changed = True
for m in sorted(reach):
    p = mods[m]; n = sum(1 for _ in p.open(encoding="utf-8"))
    print(f"{n}\t{'direct' if m in direct else 'transitive'}\t{p.relative_to(root).as_posix()}")
```

Ленивый импорт внутри функции `ast.walk` видит (он обходит тела функций), поэтому `p015_b4_support.py` попадает в `direct`, а его пять потребителей — в `transitive`. Вне замыкания карта добавляет три файла, которые журнал не импортируют, но пишут сырым SQL то, что стадия B откажет: `test_p015_t1533_…` (unit и PG) и `test_p017_t1711_seed_recipe_postgres.py`.

## 2. Числа

| Решение | Файлов | Строк | Что значит |
|---|---:|---:|---|
| **DELETE** | 5 | 2 723 | проверяет только механизм слушателя; каждый наблюдаемый эффект получил строку в карте |
| **REWRITE** | 28 | 23 548 | доменный эффект через слушатель, сырой DML или удаляемые колонки — переписать под триггер и `Book` |
| **KEEP-DISPOSAL** | 21 | 10 450 | ассерты не трогаются; меняется только уборка (клон режима B) |
| **KEEP-AS-IS** | 53 | 19 392 | достаёт до журнала только через `debt_fixture_setup`/`writer_operation`, которые переживают B |
| **Всего** | 107 | 56 113 | 104 модуля замыкания `all` + 3 вне него |

Удаляется 2 723 строки из 56 113 — не «19 файлов механизма». Основная масса стадии B — переписывание 28 файлов, а не удаление.

**Карта по ассертам** (разделы 5–7; строки DELETE и REWRITE файлов, повторяющиеся ассерты одного теста сгруппированы, пропусков нет):

| Судьба | Строк | из них ⚑ |
|---|---:|---:|
| REWRITE IN PLACE — ассерт остаётся, меняется подготовка/колонка/уборка | 212 | 122 |
| SURVIVES — тот же эффект уже держит существующий тест, переживающий B | 22 | 15 |
| TO WRITE (planned) — покрывается запланированными `T1801`/`T1803` | 44 | 34 |
| TO WRITE (new) — новый тест стадии B (NEW-A…NEW-L и три по месту) | 108 | 62 |
| DROP — проверяет внутреннее состояние слушателя, у триггера смысла не имеет | 111 | 59 |
| **Всего** | **497** | **292** |

⚑ — ассерт назван обязательным: тест нёс `b4_counterexample` до `898b4a2` (C1–C21, условия 1–3; маркер был на весь модуль у `test_p015_b4_write_guard.py`, `test_p015_b4_transaction_contract{,_postgres}.py` и на отдельных тестах в entries/wrong-writer; `test_p015_b4a_journal_mechanism.py` его **не** нёс — поправка к брифу), воспроизводит закрытие T15xx по 015, или назван спекой 018. Из 292 ⚑: **137 выживают без нового теста** (122 по месту + 15 SURVIVES), **96 требуют теста** (34 в запланированных `T1801`/`T1803`, 62 новых), **59 удаляются** как внутреннее состояние слушателя — у каждого названный удаляемый контракт. Из этих 59 часть — **инверсии**, а не удаления: см. раздел 3, пункт 2.

Счёт строк воспроизводится подсчётом строк таблиц разделов 5–7 по последней колонке; строки со знаком ⚑ или в секциях «every row ⚑».

## 3. Что стадия B обязана сделать сверх спеки

Здесь всё, что противоречит плану стадии B или неполно в нём. **Ничего из этого не делает единый зелёный срез B невозможным**, но пункты 1–4 требуют решения до кода, а не по ходу.

1. **Денежная дверь без наследника (⚑ C12).** `_check_storable` (`app/core/ledger/journal.py:479-519`, вызов `:1904-1907`) — единственный отказ писательского уровня для суммы с масштабом больше 8 и модулем от `1E12`. `Book` проверяет только `> 0` (`app/core/ledger/book.py:382-395`), `MoneyNumeric` — только конечность (`app/db/types.py:131-160`), а `NUMERIC(20,8)` **молча округляет**: триггер увидит `0.12345679` вместо `0.123456789`. После B `test_c12_p_a_value_outside_the_money_domain_is_refused_before_any_debt_sql` не может остаться зелёным. Решение: предикат хранимости переезжает в конструирование эффекта `Book` (NEW-I). **Решено консультацией 2026-09-24 (`FORK-1`) и выполнено срезом B0a:** один предикат `money_storability_violation` (`app/utils/validation.py`), `Book` — на входе и на вычисленной сумме (`BookMoneyError`), `MoneyNumeric` — при bind; см. спеку 018, «Решения стадии B». Воспроизведённой потери нет — пути приложения к масштабу 9 неизвестны, — но это снятие существующего отказа, и молча его снимать нельзя (§9 AGENTS).
2. **Часть ⚑-приёмок шага 4 меняет ожидаемый исход с «отказ» на «записано верно».** Не удаление, а инверсия; список должен увидеть владелец до кода: условие 1 и `T1532`/`T1528` (откат savepoint'а вызывающего не удался — завершённая операция становится долговечной вместе со своими записями); условие 3, `T1527` (вставка на другое ребро), формы подмены `T1528`/`T1530`/`T1531` — не отвергаются, а записываются из `OLD`/`NEW`, неверного писателя ловит критерий (б) (7c); C11 — корневая операция при провале соседней теперь коммитится (контракт `Book`, п. 4). Это прямое следствие узкого заявления спеки (`spec.md:73`), но в ней нет перечня затронутых ⚑. Перечень — раздел 5, строки с «inverted».
3. **Контекст `SET LOCAL` привязан к транзакции, а не к сессии (C9).** Слушатель отвергал запись второй сессии на том же соединении; триггер **запишет её в чужой конверт**. `app/core/clearing/service.py` использует такую форму намеренно. Карта: `test_p015_b4_transaction_contract.py:782-787` — DROP с этим обоснованием; `test_p015_b4_entries_and_money.py:737-740` (конкурент C18 пишет сырым UPDATE внутри чужого конверта) — перестроить, иначе запись конкурента станет второй записью операции. Спека должна назвать это изменение семантики.
4. **Помощник порчи получает ещё два применения, которых спека не называет** (`spec.md:109`, `:122` разрешают его только для форм `T1508`): (а) пробы CHECK-ограничений журнала — строчные триггеры охраны срабатывают **до** CHECK, поэтому C19 (`test_p015_b4_entries_and_money_postgres.py:2550-2659`, `test_p015_b4a_journal_mechanism.py:1270-1296`, `test_p015_b4a_journal_postgres.py:349-388`) и проба `_arithmetic_bites` (`test_p015_t1530_delta_arithmetic_postgres.py:169-197` — тот самый образец `T1803`) без помощника измеряют охрану, а не CHECK; (б) `T1533` (`tests/unit/test_p015_t1533_…:66-73`, PG `:90-99`) нужен долг **без** истории журнала — после B его создаёт только replica. Форма «противоречивая арифметика» требует **обоих** исключений сразу: клон без CHECK **и** помощник (`test_p015_step5a_reconciliation.py:445-451` — охрана откажет `UPDATE` записи и на клоне). Список исключений в §4 спеки расширяется поимённо.
5. **Привилегия помощника.** `session_replication_role` требует суперпользователя (PG 15+: либо `GRANT SET ON PARAMETER`). CI (`.github/workflows/quality.yml:32-37`, `POSTGRES_USER: geo`) и локальный портативный сервер (`docs/ru/backend/postgres-local-portable.md:75`, `:79`) дают суперпользователя; но провизионирование тира принимает роль только с `CREATEDB` (`tests/migrated_schema.py:360-373`), и на ней помощник откажет. Предусловие объявить и проверять при входе в помощник, а не ловить первым падением. Replica выключает и RI-триггеры внешних ключей, и отложенный constraint-триггер: порча не проверяется FK, на `RESTRICT` в уборке после неё опираться нельзя.
6. **Уборка: все 28 вызывающих — клон, ограниченный помощник уборки не нужен.** Раздел 8. По §19 спека не строит помощник, у которого нет потребителя. Три цены: `tests/tier_on_a_clone.py` перепривязывает только `TestingSessionLocal`, а не `tests.conftest.engine` и не собственные движки на `TEST_DATABASE_URL` (девять модулей) и не URL дочерних процессов (`test_p015_t1523_restart_after_commit_postgres.py:235`, `:265`); три autouse-проверки «всё засеянное удалено» (`test_p015_b4_entries_and_money_postgres.py:302`, `test_p015_b4_wrong_writer_…_postgres.py:223`, `test_p015_t1525_classification_…:150`) на клоне проходят вхолостую и удаляются вместе с уборкой (§9, anti-vacuum); около 93 тестовых функций впервые получают клон, плюс около 110 тестов механизма, которые сегодня пишут в общий тир, — при замеренных 0.35–0.82 s на клон (`tests/p015_b4a_stand.py:289-292`) это до полутора минут тира; входит в `T1809`. Уборка идёт не только через `purge_test_ledger`: `p015_b4_support.drop_world` (6 модулей), `_drop_triangle` (5), `p015_b4a_stand.Stand.purge` (8), собственный `DELETE FROM debts` в `test_p015_t1533_…_postgres.py:105-123`, середина теста в `test_simulator_real_snapshot_db_enrichment.py:133-137`, `DELETE FROM debt_operations` в `test_payment_commit_advisory_locks_postgres.py:307-311`.
7. **Контракт транзакции `Book`, п. 1 и 2 — две формулировки, которые откажут приложению.** (а) «`session.in_transaction()`, иначе отказ» отвергнет каждую свежую сессию, где `Book` — первый оператор (autobegin ещё не случился): проверка идёт **после** `await session.connection()`, как сегодня `journal.py:2754`. (б) «флаг `Book` в процессе» — только на сессию/соединение: процессный флаг отверг бы параллельные операции, которые тесты гоняют намеренно (гонки удержания, два коммита advisory, конкурент инжекта) — и параллельные запросы приложения.
8. **Тесты, чью посылку меняет сам механизм B** (переписать с перемером, а не подгонкой): порядок операторов между стоп-чтением и `INSERT` конверта — между ними встанет `SAVEPOINT` `Book` (`test_p015_step5b_criterion_b.py:1022-1024`, `…_postgres.py:213-214`); посылка `T1529` о `23505` против `40001` (`test_payment_commit_advisory_locks_postgres.py:700`) — триггер добавляет чтения `debt_operations` в транзакции, коммитящей первой; её наполнитель истории вставляет `COMPLETED` с `flush_count` (`:269-277`) — после B только `OPEN` → `UPDATE` в одной транзакции; `schema_version = 2` становится допустимым — пробы CHECK версий (`test_p015_step5b_criterion_b.py:885-913`, PG `:135`, `:171`) ждут `3`; даунгрейд `head → 027` в `test_p015_step5c_hold_races_postgres.py:667`, `:679` пройдёт через даунгрейд `029` — он обязан проходить на пустой базе; число записей на операцию (`4`, один `D`, один `U`) перемерить — слушатель писал запись на ребро на flush, триггер пишет на строку оператора.
9. **Библиотека помощников в удаляемом по смыслу файле.** `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` импортируют пять селекторов §3 (`_seed_triangle`, `_drop_triangle`, `_edges`, `_prepare_payment`, `_tx_state`, `_audit`, `_collapse_the_route`, `_under_clear_by_one_atom`, `ATOM`): файл помечен REWRITE, а не DELETE, и его помощники переезжают в модуль поддержки в том же срезе. Его четыре теста C6 дублируют PG-близнеца (SURVIVES).
10. **Двери отказа без теста.** Запланированный репродьюсер `T1801` проверяет только сырой `UPDATE` через `exec_driver_sql`. ⚑ C2 требует и Core, `bulk_*`, DML-CTE, `insert … from_select`, независимую транзакцию, `text()` — отсюда NEW-B. `COPY` (названный в `spec.md:73`) не проверяет ни один тест сегодня; NEW-B добавляет его или спека снимает его из заявления.
11. **Двухфазный корень (условие 2).** Отложенный constraint-триггер срабатывает на `PREPARE TRANSACTION`, но проверить это можно только при `max_prepared_transactions > 0`, а по умолчанию и на CI он `0`. Путей двухфазного коммита в `app/` нет (`git grep -n -i "prepare transaction\|begin_twophase" -- app` — только `journal.py:154`, `:2695`), поэтому по §19.2 п. 6 крайний случай не берётся: две ⚑-проверки условия 2 — DROP с этим основанием.
12. **Не измерено:** что возвращает `COMMIT` после проглоченного `GE001` (asyncpg/SQLAlchemy могут не бросить на `ROLLBACK`-ответ сервера). Тесты C1 «коммит отвергнут» переписываются в «ничего не стало долговечным» (NEW-L), а не в ожидание исключения.
13. **Устаревшие якоря и формулировки спеки.** `purge_test_ledger` — `tests/debt_setup.py:246-337` (удаления конвертов `:313-317`, долгов `:336`), а не `:233-325`/`:302`/`:323`; «19 файлов» — 18 после `T1806`; «31 вызывающий» — **28** тестовых модулей плюс помощник `p015_b4_support.py` (ещё два файла только упоминают имя и чистят сами). Докстринги, которые устареют вместе с `journal.py`: `app/core/ledger/reconciliation.py:77`, `app/core/ledger/book.py:19-22`, `app/db/journal_tables.py:12`, `:280`. Ограничение `chk_debt_journal_entries_ordinal` (`journal_tables.py:237`) переименовывается в `029` вместе с колонкой. Сверка читает `flush_ordinal` ровно в названных спекой местах и больше ничего из удаляемой схемы (`flush_count`, `schema_version`, состояние конверта не читаются) — план `reconciliation.py` подтверждён; критерий (б) ключуется по `(kind, intent_encoding_version)` (`reconciliation.py:155-160`, `:748-751`), так что `schema_version = 2` для всех новых конвертов с ним не конфликтует.

### Перенос из ревью стадии A (Codex, 2026-09-24, заморожено на `53df006`: `READY-TO-MERGE: YES`, `CLASS-1: 0`, два P3 класса 2)

14. **`scripts/measure_clearing_min_amount_plan.py:218-222`** сбрасывает базу `TRUNCATE {table} CASCADE` по шести таблицам, включая `debts`, до операции `Book` — осознанный сброс одноразовой базы мимо `Book`, невидимый и слушателю, и гарду. После B `BEFORE TRUNCATE` его отвергнет, и хуже того — отказ **проглатывается** `except Exception: pass`, а на PostgreSQL первая ошибка прерывает транзакцию, так что и остальные `TRUNCATE` молча не выполнятся, и скрипт пойдёт строить граф на несброшенной базе (где после baseline `SEED` откажет). Стадия B даёт ему явный ограниченный путь сброса (одноразовая база скрипта создаётся заново, как клон тира, либо сброс через именованный помощник с проверкой URL, которую скрипт уже делает, `:48`, `:102`) и убирает `except Exception: pass` — отказ сброса должен останавливать замер.
15. **Гард `tests/unit/test_p018_only_book_writes_debts.py`** не видит DML через `sqlalchemy.update(Debt)` и псевдонимы функций DML (`:127-128`: имя функции берётся только у голого `ast.Name`) и присваивания с кортежной целью `debt.amount, x = …` (`:131-135`: цель проверяется только как `ast.Attribute`). Сегодня так не пишет ни один писатель. Триггер B видит весь DML, поэтому это — объявленная слепая зона гарда: либо гард расширяется на эти формы с контрпроверкой, либо они добавляются в перечень слепых зон его сообщения (`spec.md:102`).

## 4. Манифест по файлам

Решение и одна строка «что проверяет» для каждого DELETE и REWRITE файла — по группам проходов. KEEP-DISPOSAL — таблица раздела 8. KEEP-AS-IS — список в конце раздела.

### 4.1. Механизм журнала (группа 1)

| path | lines (wc -l) | what it tests | fate | reason |
|---|---|---|---|---|
| `tests/unit/test_p015_b4a_journal_mechanism.py` | 1390 | slice-A mechanism on a private stand: envelope/entries, rollback, savepoint conditions 1-3, hook & write guard refusals, money predicates, open-time refusals, CHECK forgeries, SQLAlchemy pins | REWRITE | domain effects (envelope/entries, scope, GE001/GE002, rollback/reopen, intent eq) → NEW-A/B/D/E/F/K, P1801/P1803; C19 forgery test rewritten in place via corruption helper; rest DROP |
| `tests/unit/test_p015_t1528_the_guard_reads_what_the_statement_writes.py` | 867 | T1528: parameter-dict guard holes, `_reconcile` readback, begin-guard driver probe, per-connection listener, `_values` pin | REWRITE | expression-key → P1803-GE002; I/U/D-in-one-flush → NEW-A; metadata-only → P1801 (е); tamper shapes become NEW-C (faithful record); rest DROP |
| `tests/unit/test_p015_t1530_the_journal_reads_its_own_record_back.py` | 585 | T1530: entry-INSERT tampering refused by `_verify_entries`/completion membership; savepoint-retry control; predicate text parity | REWRITE | altered/added entry → P1803-guard/jINSERT (⚑ T1538 basis); retry control → NEW-G; predicate-string test moves verbatim; readback tests DROP |
| `tests/unit/test_p015_t1531_the_verification_read_is_not_rewritable.py` | 458 | T1531: `_reconcile` read via `exec_driver_sql`, provenance predicate, SQLAlchemy dispatch pins, cursor surface, `_raw_params`, exports | DELETE | every subject is the Python verification read, which does not exist with a trigger; the one money effect is subsumed by NEW-C |
| `tests/unit/test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py` | 432 | T1532: SQL-stream savepoint account (`LOST_SAVEPOINT_CLOSE`, unrecorded rollback), parser pin, blind edge | DELETE | savepoint accounting (spec §4 forbids carrying it); its control's effects are owned by P1803-deferred + NEW-G |
| `tests/unit/test_p015_b4_write_guard.py` | 1462 | ⚑ step-2 counterexamples C2, C3, cond. 3, T1527, C20, C21 | REWRITE | C2 doors → NEW-B/P1801-base; C3/T1527 edge moves → P1803-GE002; C20 → NEW-D/NEW-K; C21 AST test kept in place; grant-window tests DROP (trigger records faithfully, NEW-C) |
| `tests/unit/test_p015_b4_transaction_contract.py` | 1477 | ⚑ step-2 counterexamples C1, cond. 1-2, C7, C9, C10, C11 | REWRITE | C1 → NEW-B/NEW-L; C7 → NEW-F; C9/C10 → P1801 (б) / P1803-deferred; C11 → NEW-E; registry/poison/savepoint-event assertions DROP; several verdicts invert (C7) |
| `tests/integration/test_p015_b4a_journal_postgres.py` | 393 | PG half of slice A: full-width money, AUTOCOMMIT/two-phase refusals, DML-CTE, NaN DB facts, CHECK forgeries | REWRITE | full width → NEW-H; AUTOCOMMIT → NEW-D; CTE → NEW-B; NaN DB-only facts and C19 forgeries rewritten in place; two-phase DROP (C6) |
| `tests/integration/test_p015_b4_transaction_contract_postgres.py` | 1365 | ⚑ PG counterexamples: C2 CTE, T1527 (edge/UUID spelling, prevented root rollback), C10 backend state, C7, C9 two backends, C11 durable, cond. 2 AUTOCOMMIT/two-phase | REWRITE | same mapping as unit twin; backend `idle` joins P1803-deferred:abandoned-OPEN; two-phase DROP (C6) |
| `tests/integration/test_p015_t1528_the_statement_is_read_not_guessed_postgres.py` | 303 | T1528 PG: literal-edge refusal with native UUIDs, full-width readback, asyncpg probe | DELETE | literal edge dup of unit → P1803-GE002; full width → NEW-H; readback & driver probe DROP |
| `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py` | 411 | T1530 PG: arithmetic CHECK identical and biting on `create_all` and alembic schemas; readback placeholders; full-width control | REWRITE | schema-parity test is spec 018's own template for P1803-parity (`:112`, `:211`) — rewrite in place and extend; bite probe must go through corruption helper (C3); readback test DROP; full width → NEW-H |
| `tests/p015_b4a_stand.py` | 384 | private stand: own engine + `Session` subclass with journal armed, shared-tier world, `exec_driver_sql` purge | DELETE | journal-internal harness (arming, uninstall, purge through the guard's blind spot); all 8 users are in this group; rewrites use `committed_database` + `tests/debt_setup.py` |

Помощник `tests/p015_b4a_stand.py` по функциям:

| function (line) | journal-internal? | note |
|---|---|---|
| `exact_money` (:64), `identity` (:76), `EXACT_DOMAIN_LIMIT` (:57) | no | SQLite-era domain bound, no longer a correctness precondition (`:22-27`) |
| `Stand.debt` (:100), `Stand.debt_values` (:113) | no | plain row builders |
| `Stand.operation` (:126) | **yes** | wraps `journal.debt_operation`; replace by `writer_operation`/`Book.operation` |
| `Stand.stored_debts` (:140), `rows` (:164), `envelopes` (:176), `operation_equivalents` (:196), `counts`/`_absolute_counts` (:211/:223), `driver_sql` (:231) | no | fresh-session reads; `counts` baseline exists only because the stand shares the tier DB |
| `Stand.entries` (:181) | partly | reads/orders by `flush_ordinal` (`:184`, `:193`) → `ordinal` |
| `Stand.purge` (:246) | **yes** | deletes journal rows + debts via `exec_driver_sql` relying on the write guard's blind spot (`:249-255`); after B every statement is refused by the guard/`debts` triggers |
| `Stand.close` (:270) | **yes** | `journal.uninstall_journal` |
| `new_postgres_stand` (:277), `_arm`/`arm_stand` (:319/:382) | **yes** | `journal.install_journal` on a private `Session` subclass; shared-tier world chosen because a mode-B clone costs 0.35-0.82 s (`:289-292`) |

### 4.2. Записи и деньги шага 4 (группа 2)

| path | lines | what it tests | verdict | reason |
|---|---|---|---|---|
| `tests/integration/test_p015_b4_entries_and_money_postgres.py` | 2774 | Entry chain at full money size, a real 40001 (hand-written retry and both owners' loops), money domain, concurrent identity, C14 envelope intent/order, C17 journal foreign keys, C18 40001, C19 forged rows | REWRITE | Imports `journal`: `uninstall_write_guard` in `_round_trip` :441-467 and in the C8-inject competitor :970-1064. Also uses `journal_api` refusal types (:1271, :1426) and `flush_ordinal`/`flush_count` through the helpers. Every test has a domain effect and survives rewritten, but C12-P needs a carrier that does not exist yet (C1) and C19-P needs the corruption helper (C2) |
| `tests/unit/test_p015_b4_entries_and_money.py` | 785 | Unit twin of the file above: C4 chain shapes, C13 spent identity, C15 a models-only process, C17 foreign keys, C18 StaleDataError retry | REWRITE | Uses `journal_api`/`operation` from the support module. `flush_ordinal == [1..4]`. The C18 competitor writes inside the open operation's transaction (C3) |
| `tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py` | 1252 | C5/C6 at FULL_SIZE with every lock under SERIALIZABLE: criterion (a) holds, (b) refutes the wrong writer; intent equals the prepare-lock snapshot | REWRITE (mechanical) | No `journal` import and no listener-internal assertion. Breaks only on `SELECT e.flush_ordinal` (:478, :481) and `purge_test_ledger` (:199). All assertions stay in place |
| `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` | 1375 | C5, C13 replay ×2, C6 ×4 at 5.00; **helper library** for five surviving modules | REWRITE (mixed) | Helpers `_seed_triangle`/`_drop_triangle`/`_edges`/`_prepare_payment`/`_tx_state`/`_audit`/`_collapse_the_route`/`_under_clear_by_one_atom`/`ATOM` are imported by the step5a/5b/5c selectors that must stay green (C5). The C6 ×4 tests duplicate the PG sibling. C5 and C13 ×2 stay. Breaks on `flush_ordinal` (:236, :239), the per-flush grouping (:599-601) and `purge_test_ledger` (:142) |
| `tests/unit/test_p015_b4_r4_fixture_migration_is_observably_equivalent.py` | 1146 | R4: traces of the same scenario unwrapped vs wrapped and stood-down vs armed; the instrument's sensitivity to three mutations | DELETE | Imports `journal_statement_is_own` (:82) and arms/disarms through `uninstall_write_guard` (:539, :559). Its subject — what arming the listener changes relative to standing it down — has no second state after B: an unwrapped write is GE001. It holds one live fact, the refused write surfacing at block exit, which moves to a new test |
| `tests/p015_b4_support.py` (helper) | 335 | Step-2 scaffolding: journal handle, operation opener, world, fresh-session readers | REWRITE | `journal_api` :90-101, `operation` :104-117, `scenario_end_refusals` :120-139 and `refusal_of` :142-148 are journal-internal. `stored_operations` :302-309 selects `flush_count`; `stored_entries` :312-320 orders by `flush_ordinal`; `drop_world` :232-247 calls `purge_test_ledger` |

Помощник `tests/p015_b4_support.py` по функциям:

| symbol | journal-internal? | fate | callers |
|---|---|---|---|
| `journal_api` :90, `JournalApi` :71, `_NoRefusalExistsYet` :61 | yes: imports `app.core.ledger.journal`, `DebtJournalError`/`DebtOperationIncomplete` | replace with `BookError` + DB SQLSTATE (`GE001`/`GE002`/`23505`); remove the step-2 red scaffolding | entries PG, entries unit, `test_p015_b4_transaction_contract{,_postgres}.py`, `test_p015_b4_write_guard.py` |
| `operation` :105 | yes: `journal.debt_operation` | → `Book.operation(session, operation_for(kind, identity, intent, scope_equivalent_ids=…))` | entries PG/unit, transaction_contract ×2, write_guard |
| `scenario_end_refusals` :120 | yes: the listener's poisoned-root `PendingRollbackError` | drop, or fold into the caller | `test_p015_b4_write_guard.py` only |
| `refusal_of` :142 | indirect: catches `api.refusals` | re-point to `BookError`/`DBAPIError` | entries PG (through its own `_refusal_or_database_error`), transaction_contract ×2, write_guard |
| `exact_money`, `EXACT_DOMAIN_LIMIT` :55, :151 | no (SQLite domain, obsolete since 017) | keep as harmless, or drop | entries unit (`World.debt`), transaction_contract, write_guard |
| `World`, `seed_world` :163, :206 | no | keep | entries PG/unit, r4, transaction_contract ×2, write_guard |
| `drop_world` :232 | no, but calls `purge_test_ledger` :244 | per-test clone (C8) or the bounded replica purge | entries PG/unit, r4, transaction_contract ×2, write_guard |
| `stored_debts` :250, `stored_rows` :283 | no | keep | all group-2 files plus transaction_contract ×2, write_guard |
| `stored_operations` :302 | schema: selects `flush_count` | drop the column | entries PG/unit, transaction_contract ×2 |
| `stored_entries` :312 | schema: `flush_ordinal` | → `ordinal` | entries PG/unit, transaction_contract ×2, write_guard |
| `missing_journal_tables` :323, table-name constants :277-280 | no | keep; the message quotes "migration 021", correct it | every group-2 file except r4 |

### 4.3. Сверка, критерий (б), удержание, сырые писатели журнала (группа 3)

| path | lines | what it tests | fate | reason |
|---|---|---|---|---|
| tests/unit/test_p015_step5a_reconciliation.py | 1025 | criterion (a), baseline, result transitions, scheduled host, T1508 (a)-forms | REWRITE | imports `DebtJournalError, Reason, debt_operation` (:44); every corruption goes through `_around_the_application` raw DML on `debts`/`debt_journal_entries` (GE001/guard after B); `flush_ordinal` in raw INSERT :520-522. Unaffected: :360, :536, :594, :806 (+cleanup). Imports domain helpers from b4 unit (:66-74). Cleanup `_drop_triangle`→`purge_test_ledger` (mode A, commits on tier DB) |
| tests/integration/test_p015_step5a_reconciliation_postgres.py | 426 | 5a on asyncpg: both schema paths of 3 reconciliation tables, controls, post-baseline refusal, RC interleave, SEED/baseline cutover race | REWRITE | imports journal (:28); atom raw UPDATE :268-271; `debt_operation`/`DebtJournalError` :290-309, :365-427. Unaffected: :175, :313. Imports b4 unit (:37-41), step5a unit (:42-51, :330). Spec §3 selector |
| tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py | 471 | NaN refused on ORM / sum / raw SQL / trust limit | REWRITE | `journal.uninstall_write_guard`/`install_write_guard` :204-225, :276-295; raw `INSERT INTO debts` non-vacuity :327-339 → GE001. test_d unaffected. Spec §3 selector; ⚑ T1526 |
| tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py | 578 | F-012-1 end to end over HTTP; counter-check staircase of guards | REWRITE | staircase monkeypatches `journal._MONEY_QUANTUM` (:474), `_reconcile` (:496), `_verify_entries` (:516), `_verify_completed_entries` (:521); three E010 asserts are listener guards. All other tests unaffected (mode B clone :93) |
| tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py | 183 | real 40001 restarts the inject UoW; stored = concurrent + injected once | REWRITE | competitor Core `update(Debt)` under `journal.uninstall_write_guard` :117-133 → GE001 after B. Imports stand from `test_p015_inject_holds_the_owner_lock_postgres` (:34-43, own `before_flush` listener, not journal) |
| tests/unit/test_p015_step5b_criterion_b.py | 1027 | criterion (b) per kind, v1/v2 payment, clearing, inject subset, version CHECK, prestate placement | REWRITE | no journal import, but raw DML refused after B: `_rewrite_intent` UPDATE `debt_operations` :165-179; `_move_entry_and_debt` UPDATE entries+debts :182-197; `_add_entry_and_debt` INSERT debts+entries with `flush_ordinal` :200-221; cycle_inflation :679-693; version probe INSERT OPEN+commit+DELETE :887-906; `flush_ordinal` select :134; schema_version pinned to 1 :885, :889, :911-913; statement anchor :1022-1024 broken by Book savepoint. Unaffected: :293, :553, :578, :919 (+cleanup). Imports b4 unit (:57-67), step5a unit (:68-80) |
| tests/integration/test_p015_step5b_criterion_b_postgres.py | 548 | version CHECK on both paths, prestate window races, (b) controls on asyncpg | REWRITE | probe INSERT (rolled back via `_Undo`, OK) but expects `schema=2` → 23514 (:135, :171) — after 029 schema 2 is admitted; anchor :213-214 broken by Book savepoint; :446-457 run unit fns (fixed by unit rewrite). Unaffected: :284, :313, :350, :466. Imports unit step5b (:36), step5a_p `_sqlstates` (:42), b4 unit (:45-51), step5a unit (:52). Spec §3 selector |
| tests/unit/test_p015_step5c_reaction_and_hold.py | 973 | reaction T1516 + hold T1546: log/metric after commit, idempotence, refusal points, admin clear | REWRITE | `_set_debt` raw UPDATE debts :148-151 (used by `_faulty_triangle` :138-145 and directly) → GE001. Unaffected: :490, :578, :614, :806. `hold_directly` :107-135 is a domain helper (writes only results/equivalents) — keep. Cleanup `purge_test_ledger` :737, :742 + `_drop_triangle` |
| tests/integration/test_p015_step5c_hold_races_postgres.py | 681 | hold vs payment/clearing races at SERIALIZABLE, lock-before-snapshot, admin clear, RESTRICT, both paths + downgrade | REWRITE | `_baseline_and_one_atom` raw UPDATE :106-109; repair raw UPDATE :318-322. Unaffected: :453, :496, :549, :607 (but see C11). Mode B (:83). Imports `hold_directly` (:79, domain). Spec §3 selector; ⚑ T1546 |
| tests/integration/test_p015_step5c_hold_through_the_tick_sqlite.py | 197 | tick classifies hold refusal as rejection (inject, clearing, payment) | KEEP-AS-IS | no journal use; only `hold_directly` from step5c unit (:45, domain helper, survives in the rewritten module) and t1544 tick stand (:33-44, mode-B clone). `debt_operations` count == 0 (:99) stays true if Book savepoint rolls back the refused event |
| tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py | 252 | `_is_retryable_db_error` truth table on envelope 23505 | KEEP-AS-IS | issues no SQL; `ENVELOPE_INSERT` (:31-36) is a string literal fed to the classifier, not executed; imports `book.DEBT_OPERATION_IDENTITY_CONSTRAINTS` (:25-27). See C6 (literal must stay representative of Book's stage-B INSERT) |
| tests/integration/test_payment_commit_advisory_locks_postgres.py | 1348 | commit advisory locks; T1529 duplicate commit idempotent with journal history | REWRITE | only :700 affected: `_give_the_journal_a_history` INSERT with `state='COMPLETED'` + `flush_count` (:269-277) — refused by guard (INSERT only OPEN) and names a dropped column; `_forget_the_journal_history` DELETE (:309-312) refused. Other 5 tests unaffected except cleanup `_cleanup_seed` → `purge_test_ledger` :165 (mode A) |
| tests/integration/test_p017_t1711_seed_recipe_postgres.py | 620 | recipe seed runs; 7 acceptance checks each doctored red and restored | REWRITE | doctorings via `_driver_sql` (:76-89): `UPDATE debts` (:452-453, :611, :616) → GE001; `UPDATE debt_operations` intent version / kind (:469-470, :479-480) and `DELETE`/`INSERT debt_operation_equivalents` (:489-496) → guard refusal. Offsets/trust-line doctorings unaffected. Own clone per test (`cloned_database`) — no cleanup issue |

Граф импортов между модулями тестов (помощники, переживающие удаление или переписывание своего файла):

| importer:line | imported from | names | kind | fate of the helper |
|---|---|---|---|---|
| step5a_p :37-41; step5b unit :57-67; step5b_p :45-51; step5c unit :63-69; step5a unit :66-74 | tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py | `_seed_triangle`, `_drop_triangle`, `_edges`, `_prepare_payment`, `_tx_state`, `_audit`, `_collapse_the_route`, `_under_clear_by_one_atom`, `ATOM` | DOMAIN (stand, C6 wrong writers via monkeypatch / ORM `set` listener). `_drop_triangle` (:123-152) is cleanup via `purge_test_ledger` | Must survive: if the b4 module is deleted/rewritten by its group, move these to a support module (e.g. `tests/p015_triangle_stand.py`); `_drop_triangle` gets the chosen cleanup |
| step5a_p :42-51, :330; step5b unit :68-80; step5b_p :52; step5c unit :70-77 | tests/unit/test_p015_step5a_reconciliation.py | `_baseline`, `_fixture_debts`, `_verify`, `_pay`, `_results`, `_result_rows`, `_run_once`, `_scheduled_run`, `_literal`, `CHECKPOINT_CHECKS`, `_assert_interleave`, `interleave_a_payment_between_the_verifiers_reads`, `_around_the_application` | DOMAIN except `_around_the_application` (raw write around the guard — becomes HELPER call) | Module survives (REWRITE); `_around_the_application` replaced by HELPER at every corrupting call site in all four importers |
| step5b_p :36 | tests/unit/test_p015_step5b_criterion_b.py | module (`_b_findings`, `_kinds`, `_coverage`, test fns) | DOMAIN | survives (REWRITE) |
| step5b_p :42 | tests/integration/test_p015_step5a_reconciliation_postgres.py | `_sqlstates` | DOMAIN | survives |
| races :79; tick :45 | tests/unit/test_p015_step5c_reaction_and_hold.py | `hold_directly` | DOMAIN (writes results + equivalents only) | survives unchanged |
| inject_retries :34-43 | tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py | stand + `_observations` (own `before_flush` listener) | DOMAIN; `_cleanup` :186-193 purges | not in group; its cleanup needs a mechanism |
| races :64-72; step5b_p :481-488 | tests/integration/test_p015_p1_money_replay_postgres.py | `_seed`, `_cleanup`, `_debts`, … , `factory` | DOMAIN (mode B clone, :85, :103) | `_cleanup` purge droppable on the clone |
| races :58-63 | tests/integration/test_clearing_payment_prepare_interlock_postgres.py | `_seed_interlock_case`, `_cleanup_interlock_case`, … | DOMAIN; cleanup purges | not in group |

### 4.4. Вне трёх групп

| path | lines | what it tests | verdict | reason |
|---|---|---|---|---|
| `tests/debt_setup.py` (helper) | 527 | `debt_fixture_setup`/`writer_operation` (Book operations), `purge_test_ledger`, the fixture-block AST guard | REWRITE | `journal_is_active`/`journal_is_installed` (`:86-99`) and the no-op branches (`:132-135`, `:183-185`) go: after B every block opens a `Book` operation. `purge_test_ledger` (`:246-337`) goes: every caller moves to a clone (section 8). The AST guard (`:357-527`) stays unchanged |
| `tests/integration/test_simulator_real_snapshot_db_enrichment.py` | 199 | real-mode snapshot enrichment from the DB | REWRITE | mid-test `DELETE FROM debts` (`:133-137`) without an envelope → `GE001`; move the edge removal into the same `debt_fixture_setup` block (`:139-147`). Unverified: a baseline created by the real-mode run would refuse `TEST_FIXTURE` |
| `tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py` | 172 | T1533: deleting a participant with an obligation is refused | REWRITE | seeds a debt **without** journal history by raw driver `INSERT` (`:66-73`) → `GE001`; only the corruption helper can create such a row — needs the named exception (section 3, item 4) or a re-specified premise |
| `tests/integration/test_p015_t1533_participant_deletion_keeps_obligations_postgres.py` | 299 | T1533 on PostgreSQL with committed rows | REWRITE | same raw seed (`:90-99`); own teardown `DELETE FROM debts` (`:105-123`) → clone |

KEEP-AS-IS, не входят ни в одну таблицу выше: `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py` (строка `ENVELOPE_INSERT` только передаётся классификатору, в базу не уходит) и 53 модуля, достающих до журнала только через `debt_fixture_setup`/`writer_operation`:

`tests/integration/test_clearing_max_depth_controls_long_cycles.py`, `tests/integration/test_integrity_endpoints.py`, `tests/integration/test_p012_t1202_money_modules_read_precision_postgres.py`, `tests/integration/test_p012_t1207_one_money_form_across_producers.py`, `tests/integration/test_p012_t1210_net_balance_agrees_with_its_atoms.py`, `tests/integration/test_p012_t1211_shared_edge_order_postgres.py`, `tests/integration/test_p014_t1402_zero_sum_is_not_published_as_a_check.py`, `tests/integration/test_p015_f01512_inject_refuses_an_opposing_debt_postgres.py`, `tests/integration/test_p015_step5c_hold_through_the_tick_sqlite.py`, `tests/integration/test_p015_t1544_operator_stop_races_postgres.py`, `tests/integration/test_p015_t1544_operator_stop_refuses_money.py`, `tests/integration/test_p015_t1544_operator_stop_through_the_tick_sqlite.py`, `tests/integration/test_p017_t1702_mode_b_fixture_postgres.py`, `tests/integration/test_p018_book_keeps_each_kind_to_its_semantics.py`, `tests/integration/test_p018_mixed_inject_event_is_one_operation_postgres.py`, `tests/integration/test_payment_prepare_capacity_policy.py`, `tests/integration/test_post_tick_audit_drift_runner_integration.py`, `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py`, `tests/integration/test_simulator_adaptive_clearing_integration.py`, `tests/integration/test_simulator_clearing_no_deadlock.py`, `tests/integration/test_simulator_super_smoke.py`, `tests/unit/test_admin_clearing_cycles.py`, `tests/unit/test_admin_graph_ego.py`, `tests/unit/test_admin_graph_snapshot.py`, `tests/unit/test_admin_liquidity_summary.py`, `tests/unit/test_admin_participant_metrics.py`, `tests/unit/test_admin_trustlines_bottlenecks.py`, `tests/unit/test_admin_trustlines_list.py`, `tests/unit/test_apply_flow_retry_on_stale.py`, `tests/unit/test_balance_service_summary.py`, `tests/unit/test_clearing_additional_cases.py`, `tests/unit/test_clearing_sql_cycle_detection.py`, `tests/unit/test_debt_optimistic_lock.py`, `tests/unit/test_debt_symmetry.py`, `tests/unit/test_edge_patch_builder.py`, `tests/unit/test_integrity_checkpoints.py`, `tests/unit/test_interact_actions_backend_p1.py`, `tests/unit/test_invariants.py`, `tests/unit/test_p012_t1210_detector_union_default_tier.py`, `tests/unit/test_p015_b4_fixture_blocks_contain_only_fixture_setup.py`, `tests/unit/test_p015_inject_transaction_ownership.py`, `tests/unit/test_p015_t1514_simulator_must_not_requantise_stored_money.py`, `tests/unit/test_p015_t1522_payment_delta_drift_must_be_exact.py`, `tests/unit/test_p015_t1524_equivalent_deletion_keeps_obligations.py`, `tests/unit/test_p015_t1543_frozen_line_is_not_limit_zero.py`, `tests/unit/test_p015_t1544_inject_refuses_a_deactivated_equivalent.py`, `tests/unit/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py`, `tests/unit/test_p1_clearing_run_perimeter.py`, `tests/unit/test_payment_delta_check.py`, `tests/unit/test_post_tick_audit.py`, `tests/unit/test_scenario_inject_topology.py`, `tests/unit/test_trust_drift_decay_does_not_break_trust_limits.py`, `tests/unit/test_zero_debt_policy.py`.

## 5. Карта по ассертам — механизм журнала (группа 1)

Planned tests named by spec 018 (not written yet):

- **P1801-base** — `test_p018_a_write_without_context_is_refused_by_the_database.py`: raw `UPDATE debts` via `exec_driver_sql` without an envelope → `GE001`, row unchanged. Counterchecks **(а)** OPEN-context passes + entry `delta=0.00000001`; **(б)** reused pool connection / next transaction after `SET LOCAL` → refused; **(в)** COMPLETED or unknown id → `GE001` not FK; **(г)** `set_config` in rolled-back savepoint → refused; **(д)** `TRUNCATE debts` refused; **(е)** `UPDATE` of `version` only passes, no entry.
- **P1803-parity** — two-construction-path test (defs, `tgenabled`, deferrable, sequence) + one Book op and refusals on both schemas.
- **P1803-GE002** — key-change `UPDATE` refused `GE002`, row unchanged, no entry.
- **P1803-jINSERT** — direct `INSERT` into `debt_journal_entries` with a valid OPEN context refused (depth rule); insert via `UPDATE debts` passes.
- **P1803-guard** — guard triggers on the 3 journal tables (`UPDATE`/`DELETE`/`TRUNCATE` refused; `debt_operations` INSERT only OPEN, UPDATE only OPEN→COMPLETED with immutable columns).
- **P1803-deferred:{normal | abandoned-OPEN | sp-rollback | cancel}** — the four outcomes of the deferred completion check.

New tests this map asks for (names are proposals):

- **NEW-A** `test_p018_b_book_operation_writes_one_envelope_and_its_entries` — Book op with I then U (and one flush with I+U+D, and a multi-row INSERT): one COMPLETED envelope `schema_version=2`, entries `(effect, before, after, delta)` exact and ordered by increasing `ordinal`, one entry per row, `effect_count` = row count, 64-char digest, one completion row per equivalent with `in_intent/in_scope/effect_count`.
- **NEW-B** `test_p018_b_every_dml_door_is_refused_without_an_envelope` — parametrised: ORM I/U/D, Core insert/update/delete, `insert(Debt), [rows]`, `bulk_save_objects`, `bulk_insert_mappings`, `bulk_update_mappings`, Core on `session.connection()`, Core on an engine connection, independent transaction, `select(...).add_cte(insert…)`, `select(update-cte)`, `insert().from_select(… cte)`, `text()`: each → SQLSTATE `GE001`, `debts` unchanged; control: same effect inside a Book op lands, read-only CTE passes.
- **NEW-C** `test_p018_b_the_entry_is_the_row` — inside an OPEN envelope a writer the app did not plan (late `before_flush` listener changing amount/edge, relationship-set FK, `amount = Debt.amount + 1`, `before_execute` param rewrite incl. full width, `after_flush` Core DML) is stored AND journalled exactly from OLD/NEW; a client `before_execute` listener on `debt_journal_entries` INSERT never fires; criterion (a) PASSED.
- **NEW-D** `test_p018_b_book_refuses_before_any_write` — Book contract refusals: effect outside `scope_equivalent_ids`; nested op (incl. PAYMENT inside TEST_FIXTURE; non-empty `geo.operation_id`); `Debt` pending before entry; AUTOCOMMIT (dialect-level form, `_execution_options` empty); no envelope/entries/debt written; controls: in-scope / declared-inside write lands.
- **NEW-E** `test_p018_b_a_failure_inside_book_leaves_nothing` — business exception / `CancelledError` in the block, injected failure at the envelope INSERT or the COMPLETED UPDATE: original exception re-raised, Book savepoint rolled back → no envelope, no entries, no debt of that op (read on a new session); a sibling op in the same transaction commits with COMPLETED + its entries; a later commit on the same connection carries nothing of the failed op.
- **NEW-F** `test_p018_b_a_rolled_back_operation_leaves_nothing_and_the_identity_reopens` — outer rollback removes envelope/entries/debt; same identity reopens and completes on the same connection; entries only of the 2nd attempt.
- **NEW-G** `test_p018_b_an_inner_savepoint_rollback_inside_an_operation_is_not_counted` — StaleDataError-retry shape (`begin_nested` rolled back inside a Book op): op COMPLETED, rolled-back entries gone, `effect_count` = surviving rows, debts as committed.
- **NEW-H** `test_p018_b_full_width_money_is_journalled_exactly` — `999999999999.99999999`, `100000000000.00000001`, U to `…98.99999999` (delta `-1`): debts and entry `amount_after/delta` exact.
- **NEW-I** `test_p018_b_book_refuses_an_unstorable_amount` — scale-9 amount refused before write (Book), `1E12` refused (Book or `22003`), NaN/Inf refused (`MoneyNumeric`); nothing stored. See (C4).
- **NEW-K** `test_p018_b_an_intent_equivalent_with_no_effects_is_recorded` — completion row `(in_intent=True, in_scope, effect_count=0)` for an untouched intent equivalent; op with zero effects COMPLETED `effect_count=0` (spec 018 §2, Phase B 9).
- **NEW-L** `test_p018_b_a_swallowed_refusal_leaves_nothing_durable` — a `GE001` swallowed by the caller: nothing of the transaction durable, including an earlier completed Book op; after `rollback()` the same session runs a Book op that commits.

Surviving tests cited (files do NOT import `journal.py` and do not use `p015_b4_support`/`p015_b4a_stand`):

- **S1** `tests/integration/test_payments_idempotency.py::test_payments_tx_id_returns_same_result` — one COMPLETED PAYMENT envelope, `effect_count == entry rows > 0` (`:95-100`).
- **S2** `tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py::test_a_an_orm_write_of_nan_must_not_reach_the_money_column` — NaN never stored, refusal says "non-finite", not "NOT NULL". **Conditional:** that file imports `journal` inside the test (`:204-206`, `:225`) only to stand the guard down; the wrapper must be removed by its own group.
- **S3** `tests/integration/test_payment_commit_advisory_locks_postgres.py::test_concurrent_duplicate_commit_is_idempotent_with_journal_history_postgres` — a 2nd envelope INSERT of the same identity hits `23505` on `uq_debt_operations_kind_identity`/`uq_debt_operations_tx_id`. **Conditional:** its history filler inserts `flush_count` (`:273`), see (C2).
- **S4** `tests/integration/test_p018_book_keeps_each_kind_to_its_semantics.py::test_clearing_decreases_and_deletes_at_zero` — ORM effects inside an open Book op are applied (anti-vacuum of "granted write lands").
- **S6** `tests/unit/test_p015_step5b_criterion_b.py::test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind` — faithful wrong writer: (a) blind, (b) FAILED (spec 018 §2, 7c).

⚑ sources: **C-n/cond-n** = module-level `pytestmark = pytest.mark.b4_counterexample` at `898b4a2~1` (`test_p015_b4_write_guard.py:61`, `test_p015_b4_transaction_contract.py:69`, `test_p015_b4_transaction_contract_postgres.py:69` — every test in those three files). **Correction to the brief:** `test_p015_b4a_journal_mechanism.py` did NOT carry the marker (its docstring `:9-11` says so; `git show 898b4a2~1:… | grep b4_counterexample` finds only that sentence). **T15xx** = closure reproducer named in 015 `spec.md:854-858`, `:2790-2845`. **018** = named in spec 018.

#### `tests/unit/test_p015_b4a_journal_mechanism.py` (REWRITE)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :96 | test_an_operation_that_completes_writes_one_envelope_and_its_entries | debts hold 12 after I then U | TO WRITE: NEW-A — same two-step op through Book |
| :98, :100 | 〃 | exactly one COMPLETED envelope | TO WRITE: NEW-A |
| :101 | 〃 | `(flush_count, effect_count) == (2, 2)` | TO WRITE: NEW-A — `effect_count == 2` by rows (the `flush_count` half is DROP: flush_count arithmetic, column dropped by 029) |
| :102, :103 | 〃 | kind TEST_FIXTURE, `tx_id` NULL, digest 64 chars | TO WRITE: NEW-A |
| :105 | 〃 | entries `(1,I,None,10,10),(2,U,10,12,2)` | TO WRITE: NEW-A — same effect/before/after/delta, `ordinal` strictly increasing (not `1,2`) |
| :113 | 〃 | completion row `(eq, in_intent F, in_scope T, 2)` | TO WRITE: NEW-A |
| :143, :151, :152, :155 | test_a_rolled_back_operation_leaves_nothing_and_the_same_identity_reopens | rollback leaves no envelope; reopen COMPLETED; only 2nd attempt's debt and entry | TO WRITE: NEW-F |
| :229, :233 | test_condition1_a_failed_savepoint_rollback_keeps_the_refusal | premise: `rollback_savepoint` listener raised; savepoint row seen | DROP: listener-internal — savepoint accounting (spec §4 forbids carrying it) |
| :239, :243 | 〃 | savepoint's completed-op debt not durable; commit refused | DROP: listener-internal — savepoint accounting; with a trigger the completed op's debt and its entries are one atomic fact, journal cannot disagree (see C7) |
| :283, :286, :287, :288 | test_condition1_control_a_successful_savepoint_rollback_releases_the_root | caller savepoint around an op rolled back: outer commits 9, outer COMPLETED, inner envelope gone | TO WRITE: P1803-deferred:sp-rollback — plus outer op COMPLETED |
| :326, :329 | test_condition2_a_committed_root_is_not_retained_by_the_registry | root collectable after commit | DROP: listener-internal — RootTransaction registry |
| :391 | test_condition3_dml_from_another_listeners_after_flush_is_refused | neighbour `after_flush` listener ran | TO WRITE: NEW-C — premise of the `after_flush` Core-DML form |
| :396, :400 | 〃 | refused as UNVERIFIED_DEBT_WRITE | DROP: listener-internal — `_journal_write` per-write grant; inside an OPEN envelope the trigger records any DML |
| :401 | 〃 | unverified writes not durable | TO WRITE: NEW-C — inverted: writes durable AND journalled from OLD/NEW; wrong-edge detection is (b)'s job (S6) |
| :450 | test_condition3_a_late_before_flush_listener_mutating_a_debt_is_refused | late listener changed amount | TO WRITE: NEW-C (premise) |
| :452, :456 | 〃 | refused UNVERIFIED | DROP: listener-internal — before_flush grant |
| :457 | 〃 | tampered 31 not durable | TO WRITE: NEW-C — inverted: stored 31 == entry 31 |
| :503 | test_condition3_control_a_multi_row_granted_flush_still_passes | a `debts` statement observed | DROP: listener-internal — before_execute statement counting |
| :505, :513 | 〃 | two rows stored; deltas 32, 33 | TO WRITE: NEW-A — multi-row statement, one entry per row |
| :509 | 〃 | batched into < 2 statements | DROP: listener-internal — `_journal_write` grant granularity |
| :517 | 〃 | both entries `flush_ordinal == 1` | DROP: listener-internal — flush_ordinal arithmetic (sequence ordinals are distinct) |
| :575 | test_the_hook_refuses_a_debt_changed_with_no_operation[I/U/D] | seed op COMPLETED | TO WRITE: NEW-B (setup premise) |
| :579, :580 | 〃 | flush refused NO_OPERATION | TO WRITE: NEW-B — ORM I/U/D → SQLSTATE `GE001` |
| :581 | 〃 | commit after swallowed refusal fails | DROP: DebtJournalError reason code — docstring `:530-536` itself credits this to SQLAlchemy's PendingRollbackError |
| :582 | 〃 | table unchanged | TO WRITE: NEW-B |
| :601, :615 | test_a_refused_write_poisons_the_transaction_until_it_is_rolled_back | Core `update(Debt)` refused UNVERIFIED | TO WRITE: NEW-B — Core update → `GE001` |
| :603, :616 | 〃 | commit refused ROOT_POISONED | DROP: DebtJournalError reason code — PG aborts the transaction itself |
| :617, :618 | 〃 | after rollback the session commits a new op (41) | TO WRITE: NEW-L |
| :643, :644 | test_the_hook_refuses_an_effect_outside_the_declared_scope | out-of-scope effect refused OUT_OF_SCOPE | TO WRITE: NEW-D — Book refuses before any write |
| :656 | 〃 | control: same write in scope lands | TO WRITE: NEW-D |
| :678, :686, :687 | test_the_hook_refuses_moving_a_stored_debt_to_another_edge | key change refused KEY_FIELD_CHANGED | TO WRITE (planned T1803): P1803-GE002 |
| :689 | 〃 | row stays on its edge | TO WRITE (planned T1803): P1803-GE002 |
| :756, :757, :758, :759 | test_the_write_guard_refuses_every_route_that_skips_the_flush_plan[5 forms] | Core U/D/I, bulk_save_objects, bulk_insert_mappings refused, table unchanged | TO WRITE: NEW-B |
| :784 | test_the_write_guard_control_reads_and_other_tables_are_untouched | stand premise: no debts | DROP: listener-internal — `_journal_write` Python guard anti-vacuum |
| :788, :789 | 〃 | other-table update passes; journal counts 0 | DROP: listener-internal — `_journal_write` statement classifier; a trigger on `debts` cannot fire on `equivalents` |
| :848 | test_the_write_guard_refuses_core_writes_to_the_journal_tables | a real envelope exists | TO WRITE (planned T1803): P1803-guard (premise) |
| :850 | 〃 | Core INSERT/UPDATE/DELETE on journal tables refused | TO WRITE (planned T1803): P1803-guard — forged OPEN envelope passes the row guard and is refused at COMMIT (P1803-deferred:abandoned-OPEN); `UPDATE kind` and `DELETE entries` refused by guard |
| :855 | 〃 | journal counts unchanged | TO WRITE (planned T1803): P1803-guard |
| :872 | test_the_journal_tables_are_not_mapped_by_anything | no mapper on journal tables | DROP: listener-internal — `_journal_write` guard premise (an ORM write would meet the same row triggers) |
| :915 | test_the_write_guard_names_exec_driver_sql_as_its_one_blind_spot | raw driver UPDATE changes the row | TO WRITE (planned T1801): base — inverted: `GE001`, row unchanged |
| :919, :920 | 〃 | expression UPDATE refused, no change | TO WRITE: NEW-B |
| :1020 (via :967) | test_the_hook_refuses_an_unstorable_amount_by_the_predicate_it_violates[5] → `_assert_refused_before_any_debt_sql` | NaN/NaN-float/Inf/1E12/scale-9 refused | TO WRITE: NEW-I |
| :1021 | 〃 | refusal names the violated predicate | TO WRITE: NEW-I — non-finite (`MoneyNumeric`), magnitude, quantization named separately (C4) |
| :1025 | 〃 | refused before any `debts` statement built | DROP: listener-internal — before_flush ordering |
| :1029 | 〃 | nothing stored | TO WRITE: NEW-I |
| :989, :991 | test_a_value_sqlite_would_change_is_exact_money_on_postgresql | `100000000000.00000001` stored and journalled exactly | TO WRITE: NEW-H |
| :1054, :1062 | test_nan_is_refused_by_the_hook_and_not_by_the_not_null_that_fires_today | journal refuses NaN by finiteness | DROP: DebtJournalError reason code — finiteness belongs to `MoneyNumeric` (S2) |
| :1068 | 〃 | un-journalled NaN write fails | SURVIVES: S2 |
| :1075 | 〃 | failure is not a DebtJournalError | DROP: listener-internal — arm-uninstall |
| :1078, :1082 | 〃 | failure names "non-finite"/NOT NULL; nothing stored | SURVIVES: S2 |
| :1102, :1105, :1113 | test_an_operation_refuses_to_open_on_an_engine_with_no_write_guard | ENGINE_NOT_INSTRUMENTED; arming fixes it | DROP: listener-internal — arm-uninstall |
| :1127, :1130, :1133 | test_an_operation_refuses_to_open_inside_another_one | nested op refused; outer completes | TO WRITE: NEW-D |
| :1149 | test_a_business_failure_discards_its_own_operation_and_refuses_the_commit | business exception reaches caller | TO WRITE: NEW-E |
| :1154, :1156 | 〃 | commit refused OPERATION_NOT_COMPLETED | DROP: DebtJournalError reason code — Book rolls its savepoint back, nothing is left to refuse (contract item 4) |
| :1159, :1160 | 〃 | no debt, no envelope | TO WRITE: NEW-E |
| :1173, :1176 | test_a_debt_already_pending_when_the_operation_opens_is_refused | pending Debt → INCOMPLETE_DEBT | TO WRITE: NEW-D (contract item 2) |
| :1186, :1187 | 〃 | control: declared inside lands | TO WRITE: NEW-D |
| :1208 | test_a_second_open_of_the_same_identity_is_refused_by_the_database | duplicate identity → IntegrityError | SURVIVES: S3 |
| :1214, :1215 | 〃 | first envelope and debt intact | TO WRITE: NEW-F — reopen of a COMPLETED identity refused, first intact |
| :1295, :1296 | test_a_forged_entry_shape_is_refused_by_the_check_constraints | forged I-with-before / U-equal / zero-delta refused by the named CHECK; well-shaped raw row accepted | REWRITE IN PLACE: forge through the named corruption helper (`session_replication_role=replica`), column `ordinal`; else the guard trigger answers first (C3) |
| :1316, :1320, :1322 | test_the_registry_survives_nothing_a_rollback_should_have_cleared | same session, next transaction, write without op refused NO_OPERATION; first write durable | TO WRITE (planned T1801): (б) — next transaction on the same connection after `SET LOCAL` |
| :1341, :1345, :1348, :1351, :1355, :1356 | test_the_sqlalchemy_internals_this_module_pins_still_exist | SQLAlchemy 2.0.25 private attrs | DROP: listener-internal — RootTransaction registry / savepoint accounting pins |
| :1389, :1390 | test_an_operation_records_an_intent_equivalent_it_never_touched | touched eq `(T,T,1)`, untouched intent eq `(T,T,0)` | TO WRITE: NEW-K ⚑ 018 §2 (Phase B 9), contract item 6 |

#### `tests/unit/test_p015_t1528_the_guard_reads_what_the_statement_writes.py` (REWRITE) — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :230 | test_t1528_a_sql_expression_amount_cannot_ride_a_metadata_only_grant | late listener set `amount = Debt.amount + 1` ⚑ T1528 | TO WRITE: NEW-C (premise, expression form) |
| :236, :247, :248 | 〃 | refused UNVERIFIED ⚑ T1528 | DROP: listener-internal — DML parser / `_journal_write` param grant |
| :241, :244 | 〃 | stored 10, no entries ⚑ T1528 | TO WRITE: NEW-C — inverted: stored 11, entry `U 10→11 delta 1` (the T1528 hole was "moved with no entry"; the trigger closes it structurally) |
| :299 | test_t1528_a_sql_expression_key_column_cannot_ride_a_money_grant | late listener set `creditor_id = literal(other)` ⚑ T1528 | TO WRITE (planned T1803): P1803-GE002 — literal-edge form |
| :305, :310, :313, :314 | 〃 | refused; edge and amount unchanged; no entries ⚑ T1528 | TO WRITE (planned T1803): P1803-GE002 |
| :315, :316 | 〃 | refusal type/reason UNVERIFIED ⚑ T1528 | TO WRITE (planned T1803): P1803-GE002 — SQLSTATE `GE002` |
| :383, :387, :388 | test_t1528_an_unused_decimal_parameter_cannot_stand_in_for_the_amount | Connection-level listener rewrote params (12 + audit 11) ⚑ T1528 | TO WRITE: NEW-C (premise, param-rewrite form) |
| :391, :399, :400 | 〃 | refused UNVERIFIED ⚑ T1528 | DROP: listener-internal — `_journal_write` amount-by-name grant |
| :395, :398 | 〃 | stored 10, no entries ⚑ T1528 | TO WRITE: NEW-C — inverted: stored 12, entry 10→12 |
| :465, :469 | test_t1528_a_parameter_changed_after_verification_is_caught_by_the_readback | engine-instance listener rewrote INSERT 11→12 ⚑ T1528 | TO WRITE: NEW-C (premise) |
| :472, :478, :479 | 〃 | refused UNRECONCILED_DEBT_ROW ⚑ T1528 | DROP: listener-internal — `_reconcile` readback |
| :476, :477 | 〃 | row absent, no entries ⚑ T1528 | TO WRITE: NEW-C — inverted: row 12, entry I 12 |
| :517, :518, :519, :520, :521 | test_t1528_control_one_flush_with_an_insert_an_update_and_a_delete_commits | one flush with U/D/I commits; entries D, I, U | TO WRITE: NEW-A — I+U+D in one flush |
| :562, :566, :567 | test_t1528_control_a_metadata_only_update_is_still_allowed | version bump reaches DB, amount 46, no entry | TO WRITE (planned T1801): (е) |
| :655, :659, :661 | test_t1528_the_begin_guard_classifies_each_driver_answer_separately[9] | `_on_begin` per driver answer | DROP: listener-internal — driver probe |
| :750, :751 | test_t1528_a_connection_level_neighbour_cannot_hide_a_prevented_savepoint_rollback | premise: savepoint wrote 42; rollback prevented ⚑ T1528 | DROP: listener-internal — savepoint accounting |
| :757 | 〃 | `pending_savepoint_rollbacks` recorded ⚑ T1528 | DROP: listener-internal — RootTransaction registry |
| :764, :767 | 〃 | commit refused; 42 not durable ⚑ T1528 | DROP: listener-internal — savepoint accounting (completed op + its entries are atomic; see C7) |
| :798, :804, :810, :815 | test_t1528_the_statement_values_attribute_this_fix_reads_still_exists | `ValuesBase._values` pin, `_statement_values` | DROP: listener-internal — DML parser |
| :860, :863, :864 | test_t1528_a_connection_that_accepts_no_listener_is_skipped_and_not_exposed_by_it | OptionEngine refuses listeners; journal still installed | DROP: listener-internal — arm-uninstall (per-connection registration) |

#### `tests/unit/test_p015_t1530_the_journal_reads_its_own_record_back.py` (REWRITE) — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :237 | test_t1530_an_entry_whose_amount_was_rewritten_after_the_guard_is_refused | engine-instance listener rewrote the entry INSERT ⚑ T1530 | TO WRITE: NEW-C — inverted: a listener on `debt_journal_entries` INSERT never fires (entries are written inside PL/pgSQL) |
| :243, :247, :248 | 〃 | refused UNRECORDED_JOURNAL_ENTRY ⚑ T1530 | DROP: listener-internal — `_verify_entries` readback |
| :249, :250, :251 | 〃 | debt 10, no entries, no envelope ⚑ T1530 | TO WRITE: NEW-C — entry equals the row (`U 10→11 delta 1`) |
| :304 | test_t1530_an_entry_whose_delta_contradicts_its_own_ends_is_refused | delta rewritten on a clone without the arithmetic CHECK ⚑ T1530 | TO WRITE: NEW-C — inverted premise (no client entry INSERT to rewrite) |
| :305, :309, :310 | 〃 | refused UNRECORDED_JOURNAL_ENTRY ⚑ T1530 | DROP: listener-internal — `_verify_entries` delta comparison (trigger computes `delta` from OLD/NEW; the CHECK stays, P1803-parity) |
| :311, :312 | 〃 | no entries, debt 10 ⚑ T1530 | TO WRITE: NEW-C |
| :362 | test_t1530_an_entry_the_journal_never_computed_cannot_be_added | a row was added to the entry INSERT ⚑ T1530 | TO WRITE (planned T1803): P1803-jINSERT (premise: an extra entry row can only come from a direct INSERT) |
| :363, :364, :365 | 〃 | refused; no entries ⚑ T1530 | TO WRITE (planned T1803): P1803-jINSERT — direct INSERT with valid OPEN context refused, nothing stored ⚑ 018 (mandatory test) |
| :416 | test_t1530_an_entry_dropped_from_the_insert_is_refused | listener dropped one of two entry rows ⚑ T1530 | DROP: listener-internal — `_verify_entries`; `FOR EACH ROW` has no batch to drop from |
| :417, :420 | 〃 | refused UNRECORDED ⚑ T1530 | DROP: listener-internal — `_verify_entries` |
| :421, :422 | 〃 | both debts unchanged ⚑ T1530 | TO WRITE: NEW-C — inverted: both rows changed, two entries |
| :487 | test_t1530_an_entry_altered_after_its_flush_is_refused_before_the_digest | raw `UPDATE debt_journal_entries` altered 1 row ⚑ T1530 ⚑ T1538 | TO WRITE (planned T1803): P1803-guard — inverted: UPDATE refused, 0 rows altered |
| :491, :495, :496 | 〃 | refused UNRECORDED ⚑ T1530 ⚑ T1538 | TO WRITE (planned T1803): P1803-guard — `UPDATE` on entries refused by the guard trigger (the basis of the T1538 re-decision, spec 018 `:144`) |
| :497, :498 | 〃 | entries/debt unchanged ⚑ T1530 | TO WRITE (planned T1803): P1803-guard |
| :539 | test_t1530_entries_a_savepoint_rollback_removed_are_not_a_disagreement | one envelope | TO WRITE: NEW-G |
| :540 | 〃 | `flush_count == 2` | DROP: listener-internal — flush_ordinal-flush_count arithmetic |
| :543, :546, :547, :548, :549 | 〃 | COMPLETED; `effect_count 1`; entry delta 1; debts 11 / 20 | TO WRITE: NEW-G |
| :579, :583, :584, :585 | test_t1530_the_migration_and_the_metadata_spell_the_same_predicate | migration 024 and `journal_tables.py` spell the arithmetic predicate | REWRITE IN PLACE: move verbatim to a journal-free module (it reads files only; subsumed later by P1803-parity) |

#### `tests/unit/test_p015_t1531_the_verification_read_is_not_rewritable.py` (DELETE) — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :163 | test_t1531_a_before_execute_neighbour_cannot_see_the_verification_read | premise: UPDATE rewritten to 12 ⚑ T1531 | TO WRITE: NEW-C (premise, param-rewrite form — duplicate of t1528 :465) |
| :169 | 〃 | readback SELECT never reached `before_execute` ⚑ T1531 | DROP: listener-internal — `_reconcile` verification read (does not exist with a trigger) |
| :175, :178, :179 | 〃 | refused UNRECONCILED ⚑ T1531 | DROP: listener-internal — `_reconcile` |
| :180, :181 | 〃 | debt 10, no entries ⚑ T1531 | TO WRITE: NEW-C — inverted: debt 12, entry 10→12 (record = row) |
| :245, :246, :249, :252, :256, :257, :258 | test_t1531_a_writers_statement_cannot_claim_the_journals_provenance | `journal_statement_is_own` provenance | DROP: listener-internal — before_execute provenance of the verification read |
| :318, :319, :324, :328 | test_t1531_text_dispatches_before_execute_and_exec_driver_sql_does_not | SQLAlchemy dispatch facts | DROP: listener-internal — before_execute / before_cursor_execute pins |
| :404, :405, :410 | test_t1531_before_cursor_execute_is_still_a_surface_and_is_not_claimed_closed | the cursor rewrite succeeds (hole pinned open) | DROP: listener-internal — before_cursor_execute rewrite; no verification read to rewrite (spec 018 `:15` names this hole as a reason for B) |
| :436, :437, :439, :441 | test_t1531_an_unknown_paramstyle_refuses_instead_of_binding_nothing | `_raw_params` per paramstyle; UNREADABLE_VERIFICATION | DROP: listener-internal — verification read / DebtJournalError reason code |
| :454, :457, :458 | test_t1531_the_journal_no_longer_exports_a_mark_a_statement_can_carry | module export surface | DROP: listener-internal — module deleted |

#### `tests/unit/test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py` (DELETE) — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :161, :162, :165, :169, :174 | test_t1532_a_class_level_neighbour_that_prevents_a_rollback_cannot_hide_it | premises: 42 inside; rollback prevented; not recorded; nesting gone; SQL shows only `SAVEPOINT` ⚑ T1532 | DROP: listener-internal — savepoint accounting |
| :183, :186, :187, :188 | 〃 | commit refused LOST_SAVEPOINT_CLOSE; 42 not durable ⚑ T1532 | DROP: listener-internal — savepoint accounting (C7) |
| :246, :250, :251, :252 | test_t1532_a_savepoint_rollback_the_journal_never_recorded_is_a_refusal | unrecorded `ROLLBACK TO` poisons; nothing durable | DROP: listener-internal — savepoint accounting (PG rolls back envelope+entries+debt together) |
| :311 | test_t1532_ordinary_savepoint_work_is_not_refused | op in a released caller savepoint COMPLETED | TO WRITE (planned T1803): P1803-deferred:normal |
| :312 | 〃 | op with an inner rolled-back savepoint COMPLETED | TO WRITE: NEW-G |
| :318 | 〃 | op in a rolled-back caller savepoint leaves no envelope | TO WRITE (planned T1803): P1803-deferred:sp-rollback |
| :319 | 〃 | durable debts 3 and 5 only | TO WRITE: NEW-G |
| :357, :358, :362, :366, :367, :368 | test_t1532_the_savepoint_statements_are_the_ones_sqlalchemy_emits | `_savepoint_statement` parser | DROP: listener-internal — savepoint accounting parser |
| :425, :430 | test_t1532_what_the_sql_account_still_does_not_see | `after_cursor_execute` exception stops the UoW; account empty | DROP: listener-internal — savepoint accounting |

#### `tests/unit/test_p015_b4_write_guard.py` (REWRITE, every row ⚑)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :203 | test_c2_a_debt_write_that_skips_the_orm_flush_plan_is_refused[9 forms] | premise: form changed table or was refused ⚑ C2 | TO WRITE: NEW-B |
| :209, :214 | 〃 | refused; table unchanged ⚑ C2 | TO WRITE: NEW-B — each of the 9 forms → `GE001` |
| :248, :249 | test_c2_control_reads_and_writes_to_other_tables_are_not_touched | granted ORM flush inside op stored 25; read works ⚑ C2 | SURVIVES: S4 |
| :250 | 〃 | `merge` on `equivalents` works ⚑ C2 | DROP: listener-internal — `_journal_write` statement classifier anti-vacuum (a `debts` trigger cannot fire on `equivalents`) |
| :275 | test_c2_limit_exec_driver_sql_is_not_intercepted_and_is_declared_so | raw driver UPDATE reaches the table (declared limit) ⚑ C2 | TO WRITE (planned T1801): base — inverted: `GE001`, row unchanged |
| :291 | 〃 | same UPDATE via ORM refused ⚑ C2 | TO WRITE: NEW-B |
| :315 | test_c2_the_journal_tables_refuse_the_same_writes | the 3 journal tables exist ⚑ C2 | TO WRITE (planned T1803): P1803-guard (premise) |
| :318 | 〃 | `api.available` ⚑ C2 | DROP: listener-internal — `journal_api()` import shim (`p015_b4_support.py:90-101`) |
| :337 | 〃 | forged OPEN envelope not accepted ⚑ C2 | TO WRITE (planned T1803): P1803-guard — INSERT `state='OPEN'` passes the row guard; its COMMIT is refused (P1803-deferred:abandoned-OPEN); forged envelope not durable |
| :382, :385, :390 | test_c3_moving_a_stored_debt_to_another_edge_is_refused | swap debtor/creditor refused; row unchanged ⚑ C3 | TO WRITE (planned T1803): P1803-GE002 — ORM swap form |
| :475 | test_c3_a_pending_debt_keyed_only_through_relationships_is_refused | hook saw `(None,None,None)` ⚑ C3 | DROP: listener-internal — before_flush view of pending FKs |
| :481 | 〃 | keyless-at-hook debt refused ⚑ C3 | TO WRITE: NEW-C — inverted: relationship-keyed Debt stored and journalled on its resolved edge |
| :559 | test_condition3_dml_from_another_listeners_after_flush_is_refused | neighbour listener ran ⚑ cond3 | TO WRITE: NEW-C (premise) |
| :565 | 〃 | refused ⚑ cond3 | DROP: listener-internal — before_flush per-write grant |
| :570 | 〃 | nothing durable ⚑ cond3 | TO WRITE: NEW-C — inverted: durable and journalled (C7) |
| :623 | test_condition3_a_late_before_flush_listener_mutating_a_debt_is_refused | listener changed amount ⚑ cond3 | TO WRITE: NEW-C (premise) |
| :626 | 〃 | refused ⚑ cond3 | DROP: listener-internal — before_flush grant |
| :631 | 〃 | tampered amount not durable ⚑ cond3 | TO WRITE: NEW-C — inverted: stored 31 == recorded 31 |
| :677, :678 | test_condition3_control_a_multi_row_granted_flush_still_passes | 2-row flush not refused; 32 and 33 stored ⚑ cond3 | TO WRITE: NEW-A — multi-row statement |
| :682 | 〃 | `0 < statements < 2` ⚑ cond3 | DROP: listener-internal — grant granularity |
| :846 | test_t1527_an_insert_on_another_edge_than_the_hook_recorded_is_refused[2 routes] | hook read `creditor` ⚑ T1527 | DROP: listener-internal — before_flush view |
| :850 | 〃 | row carries `extra0` ⚑ T1527 | TO WRITE: NEW-C (premise: relationship / late-listener edge) |
| :856 | 〃 | refused ⚑ T1527 | DROP: listener-internal — `_journal_write` edge signature |
| :861, :862 | 〃 | nothing durable; no entry ⚑ T1527 | TO WRITE: NEW-C — inverted: stored on `extra0`, entry names `extra0` (C7) |
| :932, :938, :943, :946 | test_t1527_an_update_that_moves_a_stored_debt_through_a_relationship_is_refused | relationship UPDATE of edge refused; row 50 on old edge; no entry ⚑ T1527 | TO WRITE (planned T1803): P1803-GE002 — relationship form |
| :1025 | test_t1527_an_expired_key_attribute_cannot_hide_an_edge_move | ORM history had no `deleted` ⚑ T1527 | DROP: listener-internal — before_flush attribute history |
| :1032, :1037, :1040 | 〃 | refused; row 10 on old edge; no entry ⚑ T1527 | TO WRITE (planned T1803): P1803-GE002 — expired-attribute form (trigger compares OLD/NEW, not history) |
| :1107 | test_t1527_a_substituted_primary_key_cannot_borrow_another_columns_identity | PK substituted after hook ⚑ T1527 | DROP: listener-internal — before_flush |
| :1112, :1117 | 〃 | refused; nothing durable ⚑ T1527 | DROP: listener-internal — `_journal_write` identity matching; the substituted row puts a debt id in `debtor_id` and fails `debts`' own FK |
| :1174, :1182, :1188, :1189 | test_t1527_an_update_that_moves_no_money_is_allowed_and_journals_nothing | version-only UPDATE allowed, reaches DB, amount 46, no entry ⚑ T1527 | TO WRITE (planned T1801): (е) |
| :1247 | test_c20_an_effect_outside_the_declared_scope_is_refused_and_poisons_the_root | premise: two equivalents ⚑ C20 | TO WRITE: NEW-D |
| :1250 | 〃 | out-of-scope effect refused ⚑ C20 | TO WRITE: NEW-D |
| :1255 | 〃 | root commit refused ⚑ C20 | DROP: listener-internal — root poison (`_journal_write`); Book rolls back its own savepoint, the whole op is gone |
| :1259 | 〃 | nothing durable (in-scope sibling effect too) ⚑ C20 | TO WRITE: NEW-D — both effects of the refused op absent |
| :1310, :1316, :1323, :1327 | test_c20_an_intent_equivalent_that_was_never_touched_is_recorded_with_zero_effects | untouched intent eq row `(in_intent T, effect_count 0)` ⚑ C20 ⚑ 018 §2 | TO WRITE: NEW-K |
| :1403, :1411, :1415 | test_c21_the_fixture_block_guard_rejects_application_calls[3] | AST guard rejects app calls incl. in expressions ⚑ C21 | REWRITE IN PLACE: keep verbatim (depends only on `tests.debt_setup.fixture_block_violations`); move to `test_p015_b4_fixture_blocks_contain_only_fixture_setup.py` when this module is dropped |
| :1441 | test_c21_an_application_operation_inside_a_fixture_operation_is_refused | `api.available` ⚑ C21 | DROP: listener-internal — `journal_api()` import shim |
| :1457 | 〃 | PAYMENT op inside TEST_FIXTURE op refused ⚑ C21 | TO WRITE: NEW-D — nesting (contract item 2) |

#### `tests/unit/test_p015_b4_transaction_contract.py` (REWRITE, every row ⚑)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :267, :272 | test_c1_a_debt_written_with_no_operation_is_refused[I/U/D] | control: same effect inside an op lands ⚑ C1 | TO WRITE: NEW-B — control half |
| :279 | 〃 | ORM I/U/D without op refused ⚑ C1 | TO WRITE: NEW-B — `GE001` |
| :337 | test_c1_a_swallowed_refusal_does_not_let_the_commit_through | earlier covered write visible in-transaction ⚑ C1 | TO WRITE: NEW-L |
| :343 | 〃 | flush or commit refused ⚑ C1 | TO WRITE: NEW-L — `GE001` at the flush (commit outcome is PG's; see C8) |
| :348 | 〃 | nothing durable, earlier write included ⚑ C1 | TO WRITE: NEW-L |
| :431, :435 | test_condition1_a_failed_savepoint_rollback_keeps_the_refusal | premise: rollback listener raised; 42 inside ⚑ cond1 | DROP: listener-internal — savepoint accounting |
| :441 | 〃 | savepoint's debt not durable ⚑ cond1 | DROP: listener-internal — savepoint accounting (C7; Book-level analogue is P1803-deferred:cancel) |
| :491 | test_condition2_a_finished_root_is_not_retained_by_the_registry | table exists ⚑ cond2 | DROP: listener-internal — import-shim premise |
| :492, :498 | 〃 | op COMPLETED; root collectable ⚑ cond2 | DROP: listener-internal — RootTransaction registry |
| :572, :575, :578, :579, :583 | test_c7_a_rolled_back_operation_leaves_nothing_and_the_identity_reopens_clean | only 2nd attempt's debt; reopen OK; one COMPLETED; one entry ⚑ C7 | TO WRITE: NEW-F |
| :639, :643, :648, :649, :650, :654 | test_c7_a_savepoint_bound_operation_rolled_back_leaves_the_root_intact | root commit allowed; 10 durable; root COMPLETED + 1 entry; savepoint op no envelope ⚑ C7 | TO WRITE (planned T1803): P1803-deferred:sp-rollback |
| :702, :707 | test_c9_a_debt_written_after_the_operations_transaction_ended_is_refused | first op durable; next-tx write refused ⚑ C9 | TO WRITE (planned T1801): (б) |
| :776 | test_c9_an_operation_on_one_session_does_not_cover_a_write_on_another | shared transaction lived ⚑ C9 | DROP: listener-internal — RootTransaction registry `session_ref` |
| :782, :787 | 〃 | session B's write refused / not durable ⚑ C9 | DROP: listener-internal — RootTransaction registry `session_ref`; `SET LOCAL` context is per DB transaction, B's write is inside A's envelope and journalled under it (C7) |
| :879 | test_c9_an_operation_orphaned_by_session_close_makes_the_external_commit_refuse | op record exists, `api.available` ⚑ C9 | DROP: listener-internal — import shim |
| :882, :886 | 〃 | envelope OPEN and debt 6 inside the external transaction ⚑ C9 | TO WRITE (planned T1803): P1803-deferred:abandoned-OPEN (premise) |
| :892, :898 | 〃 | external commit refused; nothing durable ⚑ C9 | TO WRITE (planned T1803): P1803-deferred:abandoned-OPEN |
| :941, :947, :952 | test_c9_control_a_completed_operation_survives_the_same_session_close | COMPLETED before close; commit allowed; 6 durable ⚑ C9 | TO WRITE (planned T1803): P1803-deferred:normal |
| :1012, :1018, :1022, :1027 | test_c10_a_refused_root_commit_leaves_no_open_database_transaction | commit while op OPEN refused; driver not in transaction; nothing durable ⚑ C10 | TO WRITE (planned T1803): P1803-deferred:abandoned-OPEN — plus driver `is_in_transaction() is False` after the refusal |
| :1097 | test_c10_a_refused_release_leaves_the_root_open_and_poisoned | connection not closed ⚑ C10 | DROP: listener-internal — release-event refusal premise |
| :1102, :1106 | 〃 | RELEASE refused; root still open ⚑ C10 | DROP: listener-internal — savepoint accounting (PG does not refuse RELEASE; only COMMIT meets the OPEN envelope) |
| :1110, :1114 | 〃 | later commit refused; nothing durable ⚑ C10 | TO WRITE (planned T1803): P1803-deferred:abandoned-OPEN |
| :1209, :1214 | test_c11_a_failure_in_the_operations_own_io_poisons_the_root[3] | injection fired at envelope INSERT / COMPLETED UPDATE; exception reached caller ⚑ C11 | TO WRITE: NEW-E |
| :1219 | 〃 | root commit refused ⚑ C11 | DROP: listener-internal — root poison; Book's savepoint rollback leaves nothing to refuse (contract item 4) |
| :1223 | 〃 | nothing durable ⚑ C11 | TO WRITE: NEW-E |
| :1327, :1332, :1338 | test_c11_the_completion_poison_survives_a_savepoint_rollback_that_removes_the_record | completion UPDATE failed; exception reached; `ROLLBACK TO` executed ⚑ C11 | TO WRITE: NEW-E |
| :1344, :1353 | 〃 | commit refused as `root_poisoned` ⚑ C11 | DROP: DebtJournalError reason code / root poison |
| :1359, :1366 | 〃 | root's own op and debt NOT durable ⚑ C11 | DROP: listener-internal — root poison; **inverts** under the B contract: the root's completed op commits (C7) |
| :1363 | 〃 | failed op's envelope absent ⚑ C11 | TO WRITE: NEW-E |
| :1456, :1459, :1464, :1469, :1470, :1473 | test_c11_a_body_failure_in_one_block_leaves_its_sibling_committable[2] | failure reached caller; tick commit allowed; sibling 12 durable + COMPLETED; rejected op no envelope ⚑ C11 | TO WRITE: NEW-E |

#### `tests/integration/test_p015_b4a_journal_postgres.py` (REWRITE)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :113, :115 | test_an_operation_records_full_width_money_exactly | full-width debt and entries `I`/`U -1` exact | TO WRITE: NEW-H |
| :157, :177 | test_an_operation_refuses_to_open_on_an_autocommit_root | AUTOCOMMIT refused (AUTOCOMMIT_ROOT) | TO WRITE: NEW-D — Book contract item 1, dialect-level AUTOCOMMIT form |
| :169, :173 | 〃 | premise: `_execution_options` empty; dialect says AUTOCOMMIT | TO WRITE: NEW-D (premise) |
| :205, :209, :216 | test_an_operation_refuses_to_open_on_a_two_phase_root | two-phase root refused | DROP: listener-internal — RootTransaction registry (refusal existed because the Core `commit` event never fires; a deferred constraint trigger fires at `PREPARE`); see C6 |
| :263, :268 | test_the_write_guard_sees_dml_hidden_in_a_cte | writing CTEs refused; nothing stored | TO WRITE: NEW-B — CTE forms → `GE001` |
| :267 | 〃 | read-only CTE passes | TO WRITE: NEW-B (control) |
| :294, :299 | test_nan_is_refused_by_the_hook_and_would_be_refused_by_the_column_too | hook refuses NaN by finiteness | DROP: DebtJournalError reason code (finiteness: S2) |
| :314, :318, :322 | 〃 | PG: `'NaN' > 0` true; magnitude clause false for NaN; stored `chk_debt_journal_entries_delta` carries `abs` | REWRITE IN PLACE: keep (DB-only; no stand needed) |
| :384, :388 | test_the_journal_tables_refuse_a_forged_row_at_the_database | well-shaped raw row accepted; 4 forged shapes refused | REWRITE IN PLACE: forge through the corruption helper, column `ordinal`; assert the CHECK (not the guard) refused (C3) |

#### `tests/integration/test_p015_b4_transaction_contract_postgres.py` (REWRITE, every row ⚑)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :201, :206, :211 | test_c2_p_a_debt_write_hidden_inside_a_cte_is_refused[2] | CTE-hidden debt INSERT refused, nothing durable ⚑ C2 | TO WRITE: NEW-B |
| :256 | test_t1527_p_the_grant_names_the_edge_and_matches_this_tiers_uuid_spelling | `api.available` ⚑ T1527 | DROP: listener-internal — import shim |
| :297 | 〃 | row carries `extra0` ⚑ T1527 | TO WRITE: NEW-C (premise) |
| :303 | 〃 | diverging INSERT refused ⚑ T1527 | DROP: listener-internal — `_journal_write` edge signature |
| :308, :309 | 〃 | nothing durable, no entry ⚑ T1527 | TO WRITE: NEW-C — inverted: stored on and journalled for `extra0` |
| :351, :355 | 〃 | control write not refused, 62 stored ⚑ T1527 | SURVIVES: S4 |
| :356 | 〃 | control write journalled ⚑ T1527 | SURVIVES: S1 |
| :360 | 〃 | `_key_text` spelling of `debtor_id` ⚑ T1527 | DROP: listener-internal — `_journal_write` UUID normalisation |
| :430 | test_t1527_p_a_prevented_root_rollback_cannot_be_committed_by_the_next_root | `api.available` ⚑ T1527 | DROP: listener-internal — import shim |
| :477, :481, :485 | 〃 | premises: rollback prevented, root detached, driver still in tx ⚑ T1527 | DROP: listener-internal — driver probe (`_on_begin`) premise |
| :492 | 〃 | begin or commit of the 2nd root refused ⚑ T1527 | DROP: listener-internal — driver probe |
| :498, :499 | 〃 | abandoned op's debt and envelope not durable ⚑ T1527 | TO WRITE: NEW-E — failure inside Book leaves nothing even when the caller's root rollback is then prevented and a 2nd root commits |
| :560, :563, :566 | test_t1527_p_an_ordinary_rollback_leaves_the_connection_reusable | rollback then reuse same connection: 74 durable, rolled-back op absent, 2nd COMPLETED ⚑ T1527 | TO WRITE: NEW-F — same-connection reuse |
| :618, :624, :628, :632 | test_c10_p_a_refused_root_commit_leaves_the_backend_idle | commit with op OPEN refused; backend `idle`; nothing durable ⚑ C10 | TO WRITE (planned T1803): P1803-deferred:abandoned-OPEN — plus `pg_stat_activity.state == 'idle'` |
| :694 | test_c10_p_a_refused_release_leaves_the_backend_in_transaction | backend observed ⚑ C10 | DROP: listener-internal — release-refusal premise |
| :697, :701 | 〃 | RELEASE refused; `idle in transaction` ⚑ C10 | DROP: listener-internal — savepoint accounting (PG does not refuse RELEASE) |
| :705, :708 | 〃 | later commit refused; nothing durable ⚑ C10 | TO WRITE (planned T1803): P1803-deferred:abandoned-OPEN |
| :758, :761, :764, :765, :766 | test_c7_p_a_rolled_back_operation_leaves_nothing_and_the_identity_reopens_clean | reopen after rollback; one COMPLETED; one entry ⚑ C7 | TO WRITE: NEW-F |
| :815 | test_c9_p_an_operation_does_not_cover_an_independent_transactions_write | A's covered write durable ⚑ C9 | TO WRITE: NEW-B — independent-transaction form (control) |
| :821, :825 | 〃 | B's independent write refused, not durable ⚑ C9 | TO WRITE: NEW-B — `GE001` from a 2nd backend |
| :929, :934 | test_c11_p_a_failure_in_the_operations_own_io_leaves_nothing_durable[3] | injection fired; exception reached ⚑ C11 | TO WRITE: NEW-E |
| :939 | 〃 | commit refused ⚑ C11 | DROP: listener-internal — root poison |
| :942, :943, :944 | 〃 | no debt, envelope, entries (other backend) ⚑ C11 | TO WRITE: NEW-E |
| :945 | 〃 | backend `idle` after the refused commit ⚑ C11 | DROP: listener-internal — root poison (the commit is no longer refused; nothing is left open) |
| :1035, :1040, :1043 | test_c11_p_the_completion_poison_survives_a_real_rollback_to_savepoint | completion UPDATE failed; `ROLLBACK TO` ran ⚑ C11 | TO WRITE: NEW-E |
| :1050, :1055 | 〃 | commit refused `root_poisoned` ⚑ C11 | DROP: DebtJournalError reason code / root poison |
| :1060, :1064 | 〃 | root's own debt/envelope not durable ⚑ C11 | DROP: listener-internal — root poison; **inverts** (root's op commits; C7) |
| :1061 | 〃 | failed op's envelope absent ⚑ C11 | TO WRITE: NEW-E |
| :1140, :1143, :1148, :1151, :1152, :1155, :1160, :1163 | test_c11_p_a_body_failure_leaves_its_siblings_envelope_and_entries_durable[2] | sibling committed with envelope + 1 entry; rejected op no envelope/entries ⚑ C11 | TO WRITE: NEW-E |
| :1228, :1232, :1262 | test_condition2_p_an_autocommit_root_is_refused_before_the_operation_opens | premises: options blind; dialect AUTOCOMMIT; probe row survives rollback ⚑ cond2 | TO WRITE: NEW-D (premise) |
| :1270, :1275 | 〃 | op refused before open; no debt ⚑ cond2 | TO WRITE: NEW-D — Book refuses AUTOCOMMIT |
| :1348, :1354, :1361, :1362 | test_condition2_p_a_two_phase_root_is_refused_before_the_operation_opens | two-phase root refused; nothing durable; no prepared xact ⚑ cond2 | DROP: listener-internal — RootTransaction registry (see C6) |

#### `tests/integration/test_p015_t1528_the_statement_is_read_not_guessed_postgres.py` (DELETE) — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :150, :153, :157, :160, :161, :162, :163 | test_t1528_p_an_edge_moved_by_a_literal_is_refused_with_native_uuids | literal edge move refused; edge/amount unchanged; no entry ⚑ T1528 | TO WRITE (planned T1803): P1803-GE002 — literal-edge form (same as t1528 unit :299) |
| :222 | test_t1528_p_the_readback_catches_full_width_money_changed_after_verification | full-width param rewrite fired ⚑ T1528 | TO WRITE: NEW-C — full-width param-rewrite form |
| :226, :232, :233 | 〃 | refused UNRECONCILED ⚑ T1528 | DROP: listener-internal — `_reconcile` readback |
| :230, :231 | 〃 | row absent, no entries ⚑ T1528 | TO WRITE: NEW-C — inverted: row and entry both `…99999999` |
| :262, :265 | test_t1528_p_control_full_width_money_passes_the_readback | largest value stored and journalled | TO WRITE: NEW-H |
| :293, :297, :300, :303 | test_t1528_p_asyncpg_answers_the_transaction_probe_with_a_bool | `is_in_transaction()` probe answers | DROP: listener-internal — driver probe |

#### `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py` (REWRITE) — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :250, :251, :253, :256, :259, :263 | test_t1530_p_the_constraint_exists_and_bites_on_both_construction_paths | CHECK present, same definition and same CHECK set on `create_all` and alembic schemas ⚑ T1530 ⚑ 018 (T1803 template, spec `:38`) | REWRITE IN PLACE: extend to P1803-parity (triggers, functions, `tgenabled`, deferrable, sequence) keeping the CHECK comparison |
| :272 | 〃 | contradicting entry refused with `23514` on both ⚑ T1530 | REWRITE IN PLACE: the probe (`:132-197`) must go through the corruption helper — a direct INSERT now meets the guard trigger (and its OPEN envelope the deferred check) before the CHECK (C3) |
| :359 | test_t1530_p_a_rewritten_entry_is_refused_with_asyncpg_spellings | entry INSERT rewritten ⚑ T1530 | TO WRITE: NEW-C — inverted: the listener never fires |
| :360, :361 | 〃 | readback ran with `$1`/`$2` ⚑ T1530 | DROP: listener-internal — verification read (`_raw_params`) |
| :364, :368, :369 | 〃 | refused UNRECORDED/ROOT_POISONED ⚑ T1530 | DROP: listener-internal — `_verify_entries` |
| :373, :374 | 〃 | debt unchanged; no entries ⚑ T1530 | TO WRITE: NEW-C — inverted: debt and entry both `…991` |
| :407, :408, :409, :410, :411 | test_t1530_p_an_ordinary_full_width_movement_still_records | full width stored, one exact entry, COMPLETED | TO WRITE: NEW-H |

### 5.C. Находки прохода (к разделу 3)

**C1. The manifest's search boundary misses three of these files.** `test_p015_b4_write_guard.py`, `test_p015_b4_transaction_contract.py` and `test_p015_b4_transaction_contract_postgres.py` do not import `journal`. They reach it through `tests/p015_b4_support.py::journal_api()` (`:90-101`), which imports lazily inside the test body. Spec 018 `:15` bounds `T1808` by `git grep … "from app.core.ledger.journal import|…"`. That grep yields 19 files, and these three are not among them. After deletion they **still collect**. `journal_api()` returns `module=None`, `operation()` becomes a no-op (`:113-115`) and `refusals` catches only `_NoRefusalExistsYet`. So the tests run, and trigger `GE001`s surface as uncaught `DBAPIError`, not as absent tests. The same applies to `test_p015_b4_entries_and_money.py`, `test_p015_b4_wrong_writer_is_recorded_faithfully(.py/_postgres.py)` and `p015_b4_support.py` itself (other groups). There is also `test_p015_t1526_…_postgres.py:204`/`:276`, where the import is inside the function: it fails at runtime, not at collection. The manifest has to add `git grep -l "p015_b4_support\|journal_api"` to its boundary.

**C2. Test files that don't import journal but use the stage-B schema** (outside group 1; listed so the single-slice claim can be checked):
- `tests/integration/test_payment_commit_advisory_locks_postgres.py:273` inserts `flush_count` into `debt_operations`. Migration 029 drops that column. S3 depends on this file.
- `tests/unit/test_p015_step5b_criterion_b.py:215` writes a raw `INSERT INTO debt_journal_entries (… flush_ordinal …)`. The column is renamed, and the guard now refuses the INSERT.
- `tests/integration/test_p015_t1526_…_postgres.py::test_c` (`:315-`) runs a raw `INSERT INTO debts` with no envelope, including its non-vacuity control. It now gets `GE001`.

None of these is among the 19 importers.

**C3. CHECK-shape evidence can no longer be reached by a direct INSERT.** `BEFORE … FOR EACH ROW` guard triggers fire before CHECK constraints. Three tests forge `debt_journal_entries` rows directly to prove which CHECK refuses:
- C19 in `test_p015_b4a_journal_mechanism.py:1270-1292`
- `test_p015_b4a_journal_postgres.py:349-381`
- `_arithmetic_bites` in `test_p015_t1530_delta_arithmetic_postgres.py:169-197`, which is spec 018's own template for `T1803`

After B they would record the guard's refusal, not `23514` or the CHECK's name. Their well-shaped control row (`:1286`, `:370-381`) would be refused too, so they would stop measuring the CHECK at all. To keep measuring it, the forgery has to go through the named corruption helper (`session_replication_role = replica`). Spec §4 (`:122`) allows that helper only for the corruption forms of `T1508`. Using it for CHECK probes is a third use that the spec does not name, and it should be written down.

**C4. Scale-9 money is no longer refused by any writer layer.** `_check_storable` (`journal.py:479-515`, called at `:1904-1907`) is the only writer-layer refusal of a `Debt` amount or delta with more than 8 decimal places. `debts.amount` is `NUMERIC(20,8)`, which rounds silently. `MoneyNumeric` refuses only non-finite values (`app/db/types.py:131-150`), and `is_storable_money` gates only the inject and seeder doors. Magnitude still fails at the database (`22003`), and NaN/Inf are still refused by `MoneyNumeric` plus `chk_debt_amount_positive`. Quantization would stop being refused, silently, unless `Book` adopts the predicate. Contract items 1-6 of spec 018's `Book.post` do not mention it. No application path is known to produce a scale-9 amount, so this is a hypothesis about exposure, not a reproduced loss. It still removes a C12 refusal.

**C5. Mode A cannot observe the deferred check, and these tests currently commit to the shared tier database.** `db_session` mode A rolls back the outer transaction and never commits it. Every "commit of an OPEN envelope is refused" assertion therefore needs mode B:
- C9 orphan
- C10 root/release
- C7 control
- `T1527` prevented rollback
- P1803-deferred

There is a second problem. The ⚑ `write_guard` and `transaction_contract` tests request `db_session` but ignore it. They commit through `TestingSessionLocal` on the shared tier database and tear down with `drop_world → purge_test_ledger` (`p015_b4_support.py:232-247`). The group-1 stand does the same with `exec_driver_sql` DELETEs (`p015_b4a_stand.py:246-268`). All of these teardowns are refused after B.

The rewrites need `committed_database` clones. The stand measured a clone at 0.35-0.82 s (`p015_b4a_stand.py:289-292`), about 70 stand tests plus about 40 parametrised ⚑ cases, which belongs in the `T1809` time budget.

**C6. The two-phase counterexample (condition 2) cannot be re-expressed on a default server.** The deferred constraint trigger fires at `PREPARE TRANSACTION`, so the trigger design covers a two-phase root without any `Book` refusal. That is why the refusal assertions are DROP. Proving it would mean running `PREPARE`, which needs `max_prepared_transactions > 0`. PostgreSQL's default is 0, and changing it requires a server restart, including on the CI `postgres:16` service. Without that server configuration, the ⚑ condition-2 two-phase clause has no test after B. Spec 018 does not mention two-phase roots.

**C7. Some binding step-4 acceptances flip from "refused" to "journalled faithfully".** Under the trigger design these change their expected outcome rather than simply moving to new tests. They need owner-visible listing because they were step-4 acceptance, marked ⚑:
- condition 1 (`transaction_contract.py:441`) and `T1532`/`T1528` savepoint (`t1532:183-188`, `t1528:764-767`): the caller's savepoint rollback fails, and a COMPLETED op's debt becomes durable. The journal agrees with `debts`, so there is no journal lie, but the durability of work the writer asked to undo is no longer refused. The Book-level analogue is P1803-deferred:cancel.
- C9 shared connection (`:782-787`): a second session in the same database transaction writes inside the first session's envelope and is attributed to its operation. `app/core/clearing/service.py` uses that shape deliberately (design v2 §1.2).
- C11 completion poison (`:1359`, `:1366`; PG `:1060`, `:1064`): the root's own operation now commits (contract item 4). Before, the whole root was poisoned.
- condition 3, `T1527` insert, `T1528`/`T1530`/`T1531` tamper shapes: they are no longer refused. They are stored and journalled from OLD/NEW, and catching a wrong writer is left to criterion (b) (S6, spec 018 §2 7c).

Spec 018 `:73` states the narrow claim, but no list says which ⚑ counterexamples invert. This section is that list.

**C8. What `COMMIT` returns after a swallowed `GE001` is not measured.** After a statement error, PostgreSQL returns `ROLLBACK` for a `COMMIT` in an aborted transaction. Whether asyncpg or SQLAlchemy raises on that commit is an unmeasured hypothesis. The C1 assertions "commit refused" (`b4a:581`, `:603`/`:616`; `transaction_contract.py:343`) should become durability assertions in NEW-L: nothing durable, earlier Book op included. They should not become an asserted commit exception.

**C9. The planned T1801/T1803 tests do not cover every refusal door.** The planned reproducer covers only the raw `exec_driver_sql` UPDATE plus counterchecks (а)-(е). The ⚑ C2 set also includes Core, `bulk_*`, the engine connection, DML-CTE, `insert…from_select`, and an independent transaction. Those have no planned test, which is why NEW-B is needed. Spec 018 `:73` names ORM, Core, raw SQL, `COPY` and DML-CTE; no current test in this group covers `COPY` or `text()` DML.

## 6. Карта по ассертам — записи и деньги шага 4 (группа 2)

⚑ = the assertion is mandatory: it belongs to a `b4_counterexample` test at `898b4a2~1` (C-id given), or 018 names it (§2: criterion (б) / 7c = C6).

Counterexample tests at `898b4a2~1`. PG entries file: C4-P, C8 (hand-written only), C12-P ×2, C13-P, C14 ×2, C17-P ×3, C18-P, C19-P ×2 (renamed since then: `…_refused_by_the_database` → `…_refused_by_the_named_rule`, `…_shape_valid_lie…` → `…_boundary_with_step_6_moved…`). Unit entries file: C4 ×4, C13, C15, C17, C18. Unit wrong-writer file: C5, C13 ×2, C6 ×4. PG wrong-writer file: C5-P, C6-P ×4. The r4 file did not exist at `898b4a2~1`. The two C8 owner-retry tests were added after the marker was removed; design v2 §9 names them, but they carry no ⚑.

Stock phrase: "REWRITE IN PLACE: unchanged" means the assertion text stays. Its setup changes: debts are written inside `Book.operation(... TEST_FIXTURE ...)` instead of `operation()`, cleanup runs on a clone (C8), and `flush_ordinal`/`flush_count` are gone from the helper queries.

#### tests/integration/test_p015_b4_entries_and_money_postgres.py

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :129 | `_atoms` (helper) | a value is a whole number of atoms | REWRITE IN PLACE: unchanged |
| :349 | `every_seeded_row_is_gone_when_the_test_ends` (autouse) | no rows leaked into the shared DB | REWRITE IN PLACE: moot on a per-test clone — replace with clone disposal (C8) |
| :545 | `test_c4_p_the_life_of_one_edge_at_full_money_size_is_exact_integer_atoms` ⚑C4 | the entries query answers (non-vacuity) | REWRITE IN PLACE: unchanged (query `ordinal`) |
| :550, :554 | same ⚑C4 | FULL_SIZE stored exactly (stand, `_round_trip`) | REWRITE IN PLACE: round trip inside a TEST_FIXTURE Book op, no `uninstall_write_guard` (C10) |
| :560 | same ⚑C4 | the edge ends deleted | REWRITE IN PLACE: unchanged |
| :563 | same ⚑C4 | `flush_ordinal == [1,2,3]` | REWRITE IN PLACE: `ordinal` strictly increasing within the op. The exact values 1..n are dropped: flush_ordinal arithmetic (C4) |
| :564 | same ⚑C4 | effects I,U,D | REWRITE IN PLACE: unchanged (from `TG_OP`) |
| :567, :572, :577 | same ⚑C4 | before/after/delta in exact atoms at 999999999999.99999999 | REWRITE IN PLACE: unchanged (from `OLD`/`NEW`, `delta` NUMERIC) |
| :701 | `test_c8_a_real_40001_leaves_one_envelope_and_only_the_successful_attempts_entries` ⚑C8 | the entries query answers | TO WRITE (planned T1801): `test_p018_a_serialization_failure_leaves_no_envelope` |
| :706 | same ⚑C8 | a genuine SQLSTATE 40001 | TO WRITE (planned T1801): serialization_failure — "исходный SQLSTATE виден предикату повтора" |
| :712 | same ⚑C8 | the retry owns the edge (44) | TO WRITE (planned T1801): serialization_failure — the retry completes |
| :717, :718 | same ⚑C8 | exactly one COMPLETED envelope per identity after the retry | TO WRITE (planned T1801): serialization_failure — "повтор завершается одним конвертом" |
| :723 | same ⚑C8 | the loser left no entry (one U) | TO WRITE (planned T1801): serialization_failure — "нет … записей проигравшей попытки" |
| :727, :732 | same ⚑C8 | the retry's `amount_before` is the winner's value; delta 13 | TO WRITE (planned T1801): serialization_failure — add `amount_before = winner's value` and the delta (the spec's list lacks them) |
| :733 | same ⚑C8 | no transactions/prepare_locks invented | TO WRITE (planned T1801): serialization_failure — add (a TEST_FIXTURE op owns neither) |
| :743 | same ⚑C8 | exactly one `debt_operation_equivalents` row | TO WRITE (planned T1801): serialization_failure — add the membership row count |
| :882 | `test_c8_the_payment_owners_own_retry_leaves_one_envelope_and_the_winning_entries` | the envelope query answers | REWRITE IN PLACE: unchanged |
| :887 | same | exactly one real 40001 at `_apply_flow` | REWRITE IN PLACE: unchanged (the forwarding `_apply_flow` stays, T1802) |
| :892 | same | the whole UoW ran twice | REWRITE IN PLACE: unchanged |
| :896 | same | debt = competitor + paid (no lost update) | REWRITE IN PLACE: unchanged |
| :903, :904 | same | tx COMMITTED, locks gone | REWRITE IN PLACE: unchanged |
| :909, :915 | same | one PAYMENT COMPLETED envelope per tx_id | REWRITE IN PLACE: unchanged |
| :916, :920, :925 | same | one U; amount_before = competitor's value; delta = paid | REWRITE IN PLACE: unchanged |
| :932 | same | one `debt_operation_equivalents` row | REWRITE IN PLACE: unchanged |
| :1093 | `test_c8_the_inject_owners_own_retry_leaves_one_envelope_and_the_winning_entries` | the envelope query answers | REWRITE IN PLACE: the competitor's Core `update(Debt)` runs inside its own TEST_FIXTURE `Book.operation` instead of `uninstall_write_guard` (:1049-1064) |
| :1097, :1101, :1105 | same | a 40001 at the owner's flush; staged twice; stored = concurrent + injected | REWRITE IN PLACE: unchanged (the stand from `test_p015_inject_holds_the_owner_lock_postgres.py` must survive too) |
| :1111, :1117, :1118 | same | one INJECT COMPLETED envelope per `run_id:event`, `tx_id` NULL | REWRITE IN PLACE: unchanged |
| :1121, :1124, :1129 | same | one U; amount_before = concurrent; delta = injected | REWRITE IN PLACE: unchanged |
| :1222, :1231, :1236 | `test_c12_p_a_value_outside_the_money_domain_is_refused_before_any_debt_sql` ⚑C12 | the stand: the value really is unacceptable (measured round trip) | REWRITE IN PLACE: round trip inside a Book op (C10). **B0a (2026-09-24):** `_round_trip` also stands down `MoneyNumeric`'s bind check (scoped, restored in `finally`), else the measurement of PostgreSQL's rounding is the bind refusal; B1 keeps that bypass |
| :1265 | same ⚑C12 | refused BEFORE any statement reaches `debts` | TO WRITE: `test_book_refuses_an_unstorable_amount_before_any_debt_sql` — the refusal of NaN/Infinity/1E12/0.123456789 needs a carrier that precedes the flush (Book effect construction). `MoneyNumeric` refuses at bind, after `before_execute` has fired. No carrier exists — see C1. **Carrier since B0a (2026-09-24):** `BookMoneyError` from `Book` (input and calculated amount), held by `tests/integration/test_p018_b0a_money_the_column_cannot_hold.py`; B1 rewrites this assertion onto it |
| :1271 | same ⚑C12 | the refusal is the money core's own type, not the column's complaint | TO WRITE: same test — the refusal is `BookError` (or the named carrier), not `22003`/a CHECK. Blocked on C1 — unblocked by B0a: `BookMoneyError.reason` names the predicate |
| :1277 | same ⚑C12 | nothing durable | REWRITE IN PLACE: holds for NaN/Inf/1E12 via `MoneyNumeric`/CHECK/22003; for `0.123456789` only once C1 is resolved — resolved by B0a (`Book` and `MoneyNumeric` both refuse it) |
| :1319, :1320 | `test_c12_p_control_this_dialect_stores_the_whole_domain_exactly` ⚑C12 | the whole domain is stored exactly | REWRITE IN PLACE: round trip inside a Book op (C10) |
| :1406 | `test_c13_p_two_concurrent_openers_of_one_identity_and_the_database_refuses_one` ⚑C13 | the envelope query answers | REWRITE IN PLACE: unchanged |
| :1410, :1414 | same ⚑C13 | a real overlap at the barrier | REWRITE IN PLACE: unchanged |
| :1420 | same ⚑C13 | exactly one success and one refusal | REWRITE IN PLACE: two concurrent `Book.operation` under one identity |
| :1426 | same ⚑C13 | the loser is refused by the DB or the journal | REWRITE IN PLACE: `IntegrityError` naming `uq_debt_operations_kind_identity` (no `api.refusals`) |
| :1430, :1431 | same ⚑C13 | one envelope, one debt | REWRITE IN PLACE: unchanged |
| :1567 | `test_c14_the_payment_envelope_is_written_before_its_prepare_locks_are_deleted` ⚑C14 | `api is not None` (always true) | DROP: listener-internal — the `journal_api()` handle of `journal.py`; vacuous |
| :1603 | same ⚑C14 | the envelope query answers | REWRITE IN PLACE: unchanged |
| :1606, :1607, :1611, :1612, :1613 | same ⚑C14 | the real owner ran: locks written then deleted once, COMMITTED, debt 8 | REWRITE IN PLACE: unchanged |
| :1618, :1622 | same ⚑C14 | one COMPLETED PAYMENT envelope | REWRITE IN PLACE: unchanged |
| :1625 | same ⚑C14 | stored intent = prepare-lock snapshot | REWRITE IN PLACE: unchanged |
| :1631 | same ⚑C14 | the envelope is visible on the connection when `DELETE prepare_locks` runs | REWRITE IN PLACE: unchanged (Book INSERTs the envelope at open, `engine.py:1474-1489`) |
| :1706 | `test_c14_the_clearing_envelope_records_the_pre_amounts_it_actually_cleared` ⚑C14 | `api is not None` | DROP: listener-internal — `journal_api()` handle; vacuous |
| :1771, :1774, :1778 | same ⚑C14 | the real clearing ran, cleared 30, edges exact | REWRITE IN PLACE: unchanged |
| :1784, :1788, :1790 | same ⚑C14 | one COMPLETED CLEARING envelope; intent pre-amounts = amounts read independently before the clearing | REWRITE IN PLACE: unchanged |
| :1920, :1921, :1925 | `test_c17_p_deleting_an_equivalent_whose_only_debt_was_cleared_is_refused_with_409` ⚑C17 | history I+D exists, no live debt | REWRITE IN PLACE: history written through a Book TEST_FIXTURE op |
| :1950, :1956, :1962, :1963 | same ⚑C17 | route 409 `referenced_by_existing_rows`; the equivalent and both entries survive | REWRITE IN PLACE: unchanged (journal FK RESTRICT is kept) |
| :2030 | `test_c17_p_the_owner_lock_race_never_leaves_an_equivalent_gone_with_its_history` ⚑C17 | `api is not None` | DROP: listener-internal — `journal_api()` handle; vacuous |
| :2119, :2124 | same ⚑C17 | envelope query; both owners queued on the lock | REWRITE IN PLACE: unchanged |
| :2132, :2137 | same ⚑C17 | delete refused 409; the equivalent survives | REWRITE IN PLACE: unchanged |
| :2149, :2153, :2154, :2157 | same ⚑C17 | payment refused E008 `EQUIVALENT_INACTIVE_REASON`, not retryable | REWRITE IN PLACE: unchanged |
| :2158, :2161 | same ⚑C17 | no envelope, no debt | REWRITE IN PLACE: unchanged |
| :2216, :2217, :2219 | `test_c17_p_a_raw_delete_of_an_equivalent_with_history_is_refused_by_the_foreign_key` ⚑C17 | history exists, no debt | REWRITE IN PLACE: history via a Book op |
| :2250, :2256, :2257 | same ⚑C17 | raw `DELETE FROM equivalents` refused by the FK; history intact | REWRITE IN PLACE: unchanged |
| :2344, :2347, :2351 | `test_c18_p_entries_come_from_the_retry_and_carry_the_concurrent_value` ⚑C18 | a real 40001 once; the retry wins | REWRITE IN PLACE: loser/retry through `Book.operation`, the competitor its own op |
| :2356, :2360, :2364, :2367 | same ⚑C18 | one U from the retry; amount_before 31; delta 13; one envelope | REWRITE IN PLACE: unchanged |
| :2550 | `test_c19_p_a_shape_invalid_forged_row_is_refused_by_the_named_rule` ⚑C19 | the target table exists | REWRITE IN PLACE: unchanged |
| :2557, :2563 | same ⚑C19 (7 cases) | the forged row is refused, and by the NAMED CHECK / 22003 | REWRITE IN PLACE: forge through the named corruption helper (`session_replication_role=replica`), because the guard triggers answer before any CHECK. `schema_version` case → 3; `flush_count`/`flush_ordinal` columns dropped/renamed in `_envelope_row`/`_entry_row`; `chk_debt_operations_completion` loses its `flush_count` part. See C2. The direct-INSERT refusal itself is planned T1803 |
| :2616 | `test_c19_p_the_boundary_with_step_6_moved_on_this_tier_and_here_is_where_it_is_now` ⚑C19 | the table exists | REWRITE IN PLACE: unchanged |
| :2633, :2638 | same ⚑C19 | the arithmetic lie is refused by `chk_debt_journal_entries_delta_arithmetic` | REWRITE IN PLACE: through the corruption helper (C2) |
| :2659 | same ⚑C19 | a single-row-consistent cross-row lie is ACCEPTED | REWRITE IN PLACE: accepted only through the corruption helper. A direct INSERT is now refused by the guard and a committed OPEN envelope by the deferred trigger, so today's form fails (C2) |

#### tests/unit/test_p015_b4_entries_and_money.py

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :168, :171 | `test_c4_one_edge_through_insert_update_update_delete_is_four_linked_entries` ⚑C4 | the table answers; the edge is gone | REWRITE IN PLACE: unchanged |
| :173 | same ⚑C4 | `flush_ordinal == [1,2,3,4]` | REWRITE IN PLACE: `ordinal` strictly increasing. The exact 1..n are dropped: flush_ordinal arithmetic (C4) |
| :174, :177, :183, :189 | same ⚑C4 | I,U,U,D; before = previous after; deltas 10,-3,5,-12 | REWRITE IN PLACE: unchanged |
| :249, :252 | `test_c4_an_amount_change_and_a_delete_in_one_flush_are_one_effect` ⚑C4 | the table answers; the row is gone | REWRITE IN PLACE: unchanged |
| :254, :258, :259, :260, :261 | same ⚑C4 | one D entry, before = STORED 10 (not the in-memory 0), delta -10 | REWRITE IN PLACE: unchanged (the ORM emits only the DELETE; `OLD.amount` = 10) |
| :299, :302, :304 | `test_c4_an_edge_created_and_removed_again_still_counts_two_effects` ⚑C4 | the envelope query answers; net zero; one envelope | REWRITE IN PLACE: unchanged |
| :305 | same ⚑C4 | `effect_count == 2` on net zero | REWRITE IN PLACE: unchanged (count of rows, as the spec requires) |
| :310, :311 | same ⚑C4 | I, D with ±10 | REWRITE IN PLACE: unchanged |
| :365, :368, :369 | `test_c4_a_deleted_edge_that_comes_back_is_an_insert_and_not_an_update` ⚑C4 | the table answers; a new PK on the same edge; 4 left | REWRITE IN PLACE: unchanged |
| :371, :372, :376 | same ⚑C4 | D then I; the I has `amount_before` NULL | REWRITE IN PLACE: unchanged (`TG_OP=INSERT`, `OLD` null) |
| :422, :423 | `test_c13_a_second_operation_with_a_spent_identity_is_refused` ⚑C13 | the first op COMPLETED | REWRITE IN PLACE: through `Book.operation` |
| :434, :439 | same ⚑C13 (2 params) | reopening under a spent identity is refused; still one envelope | REWRITE IN PLACE: refusal = `IntegrityError` `uq_debt_operations_kind_identity` |
| :549, :554 | `test_c15_a_process_that_imports_only_the_models_still_cannot_write_a_debt` ⚑C15 | the subprocess ran and printed a verdict | REWRITE IN PLACE: unchanged |
| :560 | same ⚑C15 | a models-only process cannot write a debt | REWRITE IN PLACE: verdict tightened to SQLSTATE `GE001` from the DB (the subprocess prints `exc.orig.sqlstate`). Not a duplicate of planned T1801, which is raw `exec_driver_sql`; this one is an ORM write from a process that never imported the app |
| :619, :620, :621 | `test_c17_a_row_the_journal_history_names_cannot_be_deleted` ⚑C17 (equivalent, participant) | history I+D, no debt | REWRITE IN PLACE: history via a Book op |
| :654, :659, :660 | same ⚑C17 | raw DELETE refused by the journal FK; the row and history survive | REWRITE IN PLACE: unchanged |
| :755 | `test_c18_entries_come_from_the_attempt_that_succeeded_and_not_the_stale_one` ⚑C18 | `pytest.fail`: the retry never succeeded | REWRITE IN PLACE: unchanged |
| :762, :766 | same ⚑C18 | exactly one StaleDataError; the retry wins 44 | REWRITE IN PLACE: the competitor bump must leave mine's op context — own TEST_FIXTURE op on the same connection BEFORE mine's op opens (C3) |
| :769, :773 | same ⚑C18 | the loser left no entry: one U | REWRITE IN PLACE: holds only after the C3 restructure. As written, the raw competitor UPDATE at :737-740 runs under mine's `SET LOCAL` and becomes a second entry |
| :777, :781 | same ⚑C18 | amount_before = concurrent 31; delta 13 | REWRITE IN PLACE: unchanged |

#### tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :258 | `every_seeded_row_is_gone_when_the_test_ends` | no leaked rows | REWRITE IN PLACE: moot on a clone (C8) |
| :441 | `_clearing_tx_id` (helper) | one CLEARING tx | REWRITE IN PLACE: unchanged |
| :716, :720, :721 | `test_c5_p_the_journal_of_an_honest_payment_is_exact_at_full_money_size` ⚑C5 | seeded amounts exact; COMMITTED; flow as declared | REWRITE IN PLACE: unchanged |
| :725, :731 | same ⚑C5 | (b) intent implies the state; forward +1 atom | REWRITE IN PLACE: unchanged |
| :738, :740 | same ⚑C5 | (a) Σdelta per edge = change, in atoms | REWRITE IN PLACE: `_entries_for_tx` selects `ordinal` |
| :820, :823, :826, :829, :830 | `test_c6_p_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today` ⚑C6/7c (018 §2) | the wrong writer ran; A→C FULL_SIZE; COMMITTED; audit True | REWRITE IN PLACE: unchanged |
| :840, :844, :848 | same ⚑C6/7c | declared route ≠ committed state | REWRITE IN PLACE: unchanged |
| :855, :860, :863 | same ⚑C6/7c | one COMPLETED PAYMENT envelope | REWRITE IN PLACE: unchanged |
| :865, :872, :876 | same ⚑C6/7c | stored intent = lock snapshot; (b) replay refutes | REWRITE IN PLACE: unchanged |
| :883, :893 | same ⚑C6/7c | (a) holds: the journal records the wrong writer faithfully | REWRITE IN PLACE: unchanged |
| :941, :944, :950 | `test_c6_p_control_the_same_payment_without_the_wrapper_satisfies_criterion_b` | an honest two-hop; (b) passes on the snapshot | REWRITE IN PLACE: unchanged |
| :958, :962, :965, :969, :973, :978 | same | envelope; intent decodes and equals the snapshot; (b) passes from the stored intent | REWRITE IN PLACE: unchanged |
| :1087, :1090, :1091, :1094, :1097, :1098 | `test_c6_p_a_clearing_cycle_that_leaves_one_atom_on_every_edge_is_still_verified` ⚑C6/7c | skim fired ×3; cleared FULL_SIZE; one atom left per edge; COMMITTED; audit True | REWRITE IN PLACE: unchanged |
| :1105 | same ⚑C6/7c | the rule closes the cycle | REWRITE IN PLACE: unchanged |
| :1114, :1119, :1122, :1124, :1131, :1134 | same ⚑C6/7c | one CLEARING envelope; pre-amounts = before; (b) refutes | REWRITE IN PLACE: unchanged |
| :1141, :1151 | same ⚑C6/7c | (a) holds | REWRITE IN PLACE: unchanged |
| :1217, :1218, :1223 | `test_c6_p_control_the_same_cycle_without_the_listener_satisfies_criterion_b` | an honest cycle closes; (b) on the read pre-state | REWRITE IN PLACE: unchanged |
| :1231, :1235, :1238, :1242, :1247 | same | envelope; pre-amounts = before; (b) from the stored intent | REWRITE IN PLACE: unchanged |

#### tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :410 | `_clearing_tx_id` (helper) | one CLEARING tx | REWRITE IN PLACE: unchanged |
| :570, :571, :572 | `test_c5_the_journal_of_an_honest_payment_equals_the_change_and_the_intent` ⚑C5 | honest mutual-pair payment committed, 10/7 → 8 | REWRITE IN PLACE: unchanged (the test stays for the intermediate value) |
| :578, :579 | same ⚑C5 | (b) intent implies the state | SURVIVES: `tests/unit/test_p015_step5b_criterion_b.py::test_step5b_an_honest_payment_records_both_directions_and_is_recomputed_in_full` (mutual pair, reconciliation PASSED with full recomputation) |
| :587, :590 | same ⚑C5 | (a) Σdelta = change | SURVIVES: same step5b test (PASSED ⇒ no criterion-(a) finding) |
| :601 (part) | same ⚑C5 | `len(entries) == 3` | REWRITE IN PLACE: unchanged |
| :601 (part) | same ⚑C5 | `sorted(by_flush.values()) == [1, 2]`: three effects grouped into two flushes | DROP: listener-internal — flush_ordinal/flush_count arithmetic. The trigger's `ordinal` is per row, so a flush has no identity left (C4) |
| :607 | same ⚑C5 | the first effect records B→A passing through 2 | REWRITE IN PLACE: the min-`ordinal` entry is B→A U 7→2 (statement order is preserved) |
| :670, :671, :672, :676 | `test_c13_a_replayed_payment_commit_leaves_exactly_one_envelope` ⚑C13 | the replay takes the early return; money and state unchanged | REWRITE IN PLACE: unchanged |
| :679, :680 | same ⚑C13 | still one envelope after the replay | REWRITE IN PLACE: unchanged. T1523 restart (`test_p015_t1523_restart_after_commit_postgres.py:285`) replays through the service, not `PaymentEngine.commit` twice, so it is not a substitute |
| :738, :739, :743, :758 | `test_c13_a_replayed_clearing_leaves_exactly_one_envelope` ⚑C13 | the replay was recognised; the cycle closed; one CLEARING tx | REWRITE IN PLACE: unchanged |
| :761, :762 | same ⚑C13 | one envelope | REWRITE IN PLACE: unchanged |
| :881 | `test_c6_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today` ⚑C6/7c | both segments applied once | SURVIVES: `tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py::test_c6_p_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today` (:820) |
| :884, :887, :890, :891 | same ⚑C6/7c | empty before; A→C; COMMITTED; audit True | SURVIVES: `tests/unit/test_p015_step5b_criterion_b.py::test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind` (edges {A→C: 5}, audit [True]) |
| :901, :905, :909 | same ⚑C6/7c | declared route ≠ committed | SURVIVES: PG sibling `…::test_c6_p_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today` (:840-:848) |
| :916, :921, :924, :928, :935 | same ⚑C6/7c | one COMPLETED PAYMENT envelope; stored intent = lock snapshot | SURVIVES: PG sibling, same test (:855-:872) |
| :939 | same ⚑C6/7c | (b) refutes the wrong route | SURVIVES: `tests/unit/test_p015_step5b_criterion_b.py::test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind` (status FAILED, b_delta_mismatch per edge) |
| :946, :955 | same ⚑C6/7c | (a) holds for the faithful wrong writer | SURVIVES: same step5b test (`_a_findings(outcome) == []`) |
| :1006, :1010, :1016 | `test_c6_control_the_same_payment_without_the_wrapper_satisfies_criterion_b` | an honest two-hop; (b) on the snapshot | SURVIVES: PG `…::test_c6_p_control_the_same_payment_without_the_wrapper_satisfies_criterion_b` (:941-:950) |
| :1025, :1030, :1033, :1037, :1041, :1046 | same | envelope; intent = snapshot; (b) from the stored intent | SURVIVES: PG control (:958-:978) |
| :1202, :1205, :1206, :1209, :1212, :1213 | `test_c6_a_clearing_cycle_that_leaves_one_atom_on_every_edge_is_still_verified` ⚑C6/7c | skim ×3; cleared 10; one atom left; COMMITTED; audit True | SURVIVES: PG `…::test_c6_p_a_clearing_cycle_that_leaves_one_atom_on_every_edge_is_still_verified` (:1087-:1098) |
| :1220 | same ⚑C6/7c | the rule closes the cycle | SURVIVES: PG, same test (:1105) |
| :1229, :1234, :1237, :1241, :1248, :1251 | same ⚑C6/7c | one CLEARING envelope; pre-amounts = before; (b) refutes | SURVIVES: PG, same test (:1114-:1134); the (b) verdict also `test_p015_step5b_criterion_b.py::test_step5b_the_c6_under_clearing_is_failed_by_b_while_a_stays_blind` |
| :1258, :1266 | same ⚑C6/7c | (a) holds | SURVIVES: `tests/unit/test_p015_step5b_criterion_b.py::test_step5b_the_c6_under_clearing_is_failed_by_b_while_a_stays_blind` (`_a_findings == []`) |
| :1336, :1337, :1344 | `test_c6_control_the_same_cycle_without_the_listener_satisfies_criterion_b` | an honest cycle closes; (b) on the read pre-state | SURVIVES: PG `…::test_c6_p_control_the_same_cycle_without_the_listener_satisfies_criterion_b` (:1217-:1223) |
| :1352, :1357, :1360, :1364, :1369 | same | envelope; pre-amounts = before; (b) from the stored intent | SURVIVES: PG control (:1231-:1247) |

#### tests/unit/test_p015_b4_r4_fixture_migration_is_observably_equivalent.py (DELETE)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :665 | `test_r4_two_identical_armed_runs_record_the_same_trace` (5 params) | the armed run issued journal statements | DROP: listener-internal — arm-uninstall. There is no un-armed state to compare against |
| :670, :674, :679, :683, :688 | same | the instrument's self-control: transaction events seen, IntegrityError recorded, money survived normalisation, label kept, two armed runs equal | DROP: listener-internal — arm-uninstall. The instrument exists only for the armed/stood-down comparison; its `journal-sql` provenance is `journal_statement_is_own` (the `_journal_write` Python guard's per-connection own-statement state) |
| :725, :728, :733 | `test_r4_the_wrapper_alone_changes_nothing_observable` (5 params) | wrapped = unwrapped with the journal stood down | DROP: listener-internal — arm-uninstall. With the trigger, the unwrapped body is GE001, so there is no neutral state |
| :781, :785, :790 | `test_r4_arming_the_journal_adds_its_own_statements_and_nothing_else` (2 params) | arming adds only journal-table statements | DROP: listener-internal — arm-uninstall |
| :843, :844, :846 | `test_r4_arming_the_journal_moves_the_debt_sql_earlier_when_no_flush_follows` | both statements issued; stood-down order | DROP: listener-internal — arm-uninstall |
| :850, :856 | same | armed: debt INSERT at block exit, before the read | DROP: listener-internal — arm-uninstall (a comparison with the stood-down run). The completion flush it depends on is carried by the TO WRITE row :886 |
| :879, :882 | `test_r4_arming_the_journal_moves_where_a_refused_write_raises` | stood-down: IntegrityError at the test's own flush | DROP: listener-internal — arm-uninstall |
| :880, :886 | same | armed: the unique violation surfaces at the `debt_fixture_setup` block exit | TO WRITE: `test_debt_fixture_setup_surfaces_a_refused_write_at_block_exit` — under stage-B `Book` (contract §3 flushes inside its savepoint) a duplicate-edge `Debt` added in the block raises IntegrityError at exit, and no OPEN envelope or entry is left |
| :1024, :1025, :1029, :1034, :1041 | `test_r4_the_trace_comparison_notices_each_named_mutation` (6 params) | the instrument sees rollback/flush/amount mutations | DROP: listener-internal — arm-uninstall (a self-test of an instrument whose subject is removed) |
| :1066, :1071, :1075 | `test_r4_changing_the_amount_changes_the_money_and_nothing_else` (2 params) | the amount mutation moves only money | DROP: listener-internal — arm-uninstall (instrument self-test) |
| :1124, :1129, :1136 | `test_r4_the_completion_flush_makes_the_tests_own_flush_a_no_op_for_shape_b` | both statements; stood-down order moved; armed trace unchanged | DROP: listener-internal — arm-uninstall |

### 6.C. Находки прохода (к разделу 3)

1. **The money door has no successor.** The only pre-SQL refusal of quantisation and magnitude for debt amounts is `_check_storable` in the listener (`app/core/ledger/journal.py:479-519`, called at `:1904-1905`). `MoneyNumeric` refuses non-finite values only (`app/db/types.py:145-153`), and at bind time, after `before_execute` has fired. `Book` checks only `> 0` (`app/core/ledger/book.py:382-395`). The trigger cannot take this over: `NEW.amount` has already been coerced to `NUMERIC(20,8)`, so `0.123456789` reaches it as `0.12345679`. After B, `0.123456789` would be silently rounded, and `1E12` refused only after the statement is sent (22003). ⚑C12 `test_c12_p_a_value_outside_the_money_domain_is_refused_before_any_debt_sql` (:1178; assertions :1265, :1271) cannot stay green, and 018 names no successor. A carrier has to be decided before B: the storability predicate in `Book` effect construction (satisfies "before any SQL"), and/or `is_storable_money` in `MoneyNumeric.process_bind_param` (also covers raw ORM, but only after `before_execute`).
2. **C19 forgeries are answered by the guard triggers before any CHECK.** Direct INSERT into `debt_journal_entries` is refused at depth 1, and INSERT into `debt_operations` with `state <> 'OPEN'` is refused. A legal forged OPEN envelope is refused at commit by the deferred trigger, so today's `cross_row_lie is None` (:2659) fails. The forgeries at :2382-2482, :2515-2666 must go through the spec's named corruption helper (`session_replication_role = replica`). That mode keeps CHECKs, but also switches off the FK triggers, so FK noise disappears; the case list should say so. Further consequences:
   - the `schema_version: 2` case (:2476-2481) becomes legal: use 3;
   - `_envelope_row`/`_entry_row` (:2683, :2695, :2709) carry `flush_count`/`flush_ordinal`;
   - `_unforge` (:2761-2774) DELETEs journal rows, which the guard refuses; the error is swallowed by `except DatabaseError`, so litter stays silently. A clone is needed.

   `session_replication_role` needs superuser (or the PG ≥ 15 `GRANT SET ON PARAMETER`): verify the `geo` role on the local portable server and on CI `postgres:16`.
3. **A transaction-scoped context changes attribution.** The listener bound an operation to its session (015 C9: another session's write is refused). `SET LOCAL geo.operation_id` binds it to the transaction: any write on the same connection while the envelope is OPEN — another `AsyncSession` on the same `Connection`, or `exec_driver_sql` — is journalled into that operation instead of refused. The unit C18 stand hits this (`test_p015_b4_entries_and_money.py:722-756`: the competitor's raw UPDATE inside mine's op becomes a second U, and :773 fails). The C9 assertions in the transaction-contract group need re-expressing to the same effect. The spec does not record this change in semantics.
4. **Per-flush structure is not deliverable.** The exact per-operation numbering (`[1,2,3]` at `…_postgres.py:563`, `[1..4]` at `…money.py:173`) and grouping by flush (`…faithfully.py:599-607`, "three effects in two flushes") come from `flush_ordinal`. The sequence `ordinal` is per row, global and gapped. Order within an operation survives, and the intermediate-value assertion survives as "the min-`ordinal` entry"; the grouping is dropped. This is consistent with the spec's "ordinal — порядок внутри операции, и только", but the manifest has to record the drop.
5. **The unit wrong-writer file cannot be deleted: it is a helper library for mandatory selectors.** It is imported by `tests/unit/test_p015_step5b_criterion_b.py:57`, `tests/unit/test_p015_step5a_reconciliation.py:66`, `tests/unit/test_p015_step5c_reaction_and_hold.py:63`, `tests/integration/test_p015_step5a_reconciliation_postgres.py:37` and `tests/integration/test_p015_step5b_criterion_b_postgres.py:45`: `_seed_triangle`, `_drop_triangle`, `_edges`, `_prepare_payment`, `_tx_state`, `_audit`, `_collapse_the_route`, `_under_clear_by_one_atom`, `ATOM`. Its `_drop_triangle` (:123-152) calls `purge_test_ledger`, so the cleanup of §3 selectors depends on this file's rewrite in the same slice. Options: move the helpers to a support module, or keep the file with C5 and C13 ×2 and drop the C6 ×4 duplicates.
6. **Coupled corruption path (another group's file, reached through these imports).** `tests/unit/test_p015_step5b_criterion_b.py:164-218` uses `_around_the_application` to UPDATE `debt_journal_entries`/`debt_operations`/`debts` and INSERT an entry with `flush_ordinal` 99 (:205-211). After B the guard triggers and GE001 refuse all of these, unless the helper becomes the named corruption helper and uses `ordinal`.
7. **Contract item 1 would refuse every fresh-session caller.** It says "`session.in_transaction()` … иначе отказ до первой записи". Every caller in this group opens `Book` or `debt_fixture_setup` on a fresh `AsyncSession` before any statement, for example `…_postgres.py:639-641`, `:853-855`, `:2305-2307`, `…faithfully_postgres.py:693-703`, `…money.py:225-231`. With autobegin, `in_transaction()` is False there. Today `debt_operation` calls `await session.connection()` first (`journal.py:2754`), so the check has to follow that call, or all of these are refused.
8. **Cleanup.** All five files commit real rows into the shared `TEST_DATABASE_URL` through their own engines: the SERIALIZABLE engines at `…_postgres.py:167-192` and `…faithfully_postgres.py:80-105`, and `TestingSessionLocal`/NullPool in the unit files. They rely on `purge_test_ledger`, `_unforge` and the autouse leak checks (`…_postgres.py:303-353`, `…faithfully_postgres.py:224-263`). Preferred mechanism (1), a `committed_database` clone per test with the file's own SERIALIZABLE engine on the clone URL, is feasible for all of them: they need real commits and several connections, and a clone gives both. The C8-inject test depends on `observed_factory` and its `_cleanup` from another group's `tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py:79-81` (purge at `:193`). That file must survive with a new cleanup.
9. **Mode A and the deferred trigger: no problem in this group.** These tests request `db_session` only to bootstrap the schema and write through their own engines with real commits, so the deferred check at commit is reached. No group-2 test depends on a mode-A rollback.
10. **The measurement bypasses have no stage-B equivalent.** `_round_trip` (:441-467) and the C8-inject competitor (:1049-1064) use `uninstall_write_guard` to write around the listener. Under the spec's §4 prohibition, their replacement must be a legal TEST_FIXTURE `Book` operation. Replica is not allowed in a behavioural test. These worlds have no baseline, so TEST_FIXTURE is allowed.
11. **R4 evidence becomes moot.** The r4 file is the only behavioural evidence that the slice-B `debt_fixture_setup` wrapping is neutral. After B an unwrapped write is GE001, so neutrality stops being a question. The one live fact — a refused write surfaces at block exit because the envelope flushes at completion — moves to the new test listed under r4 row :880/:886.

## 7. Карта по ассертам — сверка, критерий (б), удержание (группа 3)

- **HELPER** = the one named corruption helper of spec 018 §2 (`session_replication_role = replica` on a separate connection, commits the given statements). Not written yet.
- **Book-refusal** = the stage-B `Book` refusal of `SEED`/`TEST_FIXTURE` after baseline (spec :71, moved from `journal._complete`).
- **cleanup** = mechanism from spec "Уборка тестов в стадии B" (clone/rollback preferred; else bounded replica purge after all asserts).

#### tests/unit/test_p015_step5a_reconciliation.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :44 | (import) | `DebtJournalError, Reason, debt_operation` | DROP: listener-internal — `journal.py` API removed; replaced by `Book.operation` / Book-refusal in :830 |
| :115-121 | `_around_the_application` | raw driver write helper | REWRITE IN PLACE: callers that corrupt use HELPER; keep this name only for non-guarded tables (or remove) |
| :328, :331 | test_step5a_a_one_atom_change_around_the_application_is_failed | baseline counts; untouched state PASSED | REWRITE IN PLACE: unchanged (setup) |
| :333-337 | same | one-atom raw UPDATE lands (edge = 10.00000001) | REWRITE IN PLACE: atom via HELPER (bypass modelled as operator/restore) |
| :344-345 | same | checkpoint still `passed`, no alerts after the atom (the T1505 reproduction) | REWRITE IN PLACE: same, after HELPER atom |
| :348, :353, :354 | same | FAILED, `edge_residual` unexplained 0.00000001, edges_checked 1 ⚑ T1505 acceptance / T1508 one atom | REWRITE IN PLACE: atom via HELPER → FAILED (spec §2) |
| (new) | — | raw-driver one-atom UPDATE without context is refused, row unchanged ⚑ T1505/T1508 decision 018 | TO WRITE (planned T1801): main case of `test_p018_a_write_without_context_is_refused_by_the_database.py` (GE001, row unchanged) |
| :379-388 | test_step5a_an_application_payment_that_moves_debts_and_journal_is_passed | honest payment PASSED, edges_checked 3, entries grew, no offsets ⚑ T1505 negative control | REWRITE IN PLACE: unchanged body; only cleanup mechanism |
| :408-417 | test_step5a_without_a_baseline_the_result_is_unverifiable_never_passed | UNVERIFIABLE, missing baseline, no findings, entries_read 1 ⚑ T1508 missing baseline | REWRITE IN PLACE: atom via HELPER |
| :444-451 | test_step5a_a_journal_row_contradicting_its_own_arithmetic_is_failed_and_dominates | forged `amount_after` on an entry | REWRITE IN PLACE: HELPER on the clone-without-CHECK (guard trigger refuses UPDATE of entries even on the clone) — see C3 |
| :454-457 | same | FAILED + missing baseline, `entry_arithmetic` 11/10 ⚑ T1508 contradictory arithmetic | REWRITE IN PLACE: as above |
| :479-483 | test_step5a_a_missing_delta_on_an_edge_the_application_removed_is_failed | payment removes edge, PASSED, one `D` entry | REWRITE IN PLACE: unchanged (trigger writes `D` from OLD) |
| :484-492 | same | `D` entry deleted → FAILED residual -10 ⚑ T1508 missing entry | REWRITE IN PLACE: DELETE via HELPER |
| :511-515 | test_step5a_a_duplicated_delta_is_failed | setup: payment, PASSED, one `U` | REWRITE IN PLACE: unchanged |
| :517-530 | same | copied entry → FAILED residual -5 ⚑ T1508 duplicated entry | REWRITE IN PLACE: INSERT via HELPER; `flush_ordinal`→`ordinal`, `+100` → fresh unused ordinal (C12) |
| :283-292 (`_assert_interleave`) | test_step5a_a_payment_committed_between_the_verifiers_reads_is_still_passed | payment between verifier reads → PASSED, no error | REWRITE IN PLACE: unchanged; cleanup only |
| :606-619 | test_step5a_an_unchanged_verdict_keeps_one_row_and_advances_last_checked_at | one row, last_checked_at advances | REWRITE IN PLACE: unchanged; cleanup only |
| :640-655 | test_step5a_a_failed_then_passed_transition_inserts_and_keeps_the_failed_evidence | FAILED→PASSED inserts; FAILED evidence kept | REWRITE IN PLACE: both raw UPDATEs via HELPER |
| :689-714 | test_step5a_a_payment_on_an_already_failed_edge_keeps_the_fault_identity | same fingerprint across legit payment; new fault inserts | REWRITE IN PLACE: :689-691, :709-711 via HELPER |
| :732-750 | test_step5a_different_findings_under_the_same_status_insert | two FAILED fingerprints | REWRITE IN PLACE: three raw UPDATEs via HELPER |
| :779-800 | test_step5a_the_baseline_adopts_a_debt_the_journal_cannot_explain_and_later_change_is_checked | pre-baseline unjournalled debt adopted as offset 7; later payment PASSED ⚑ T1501 | REWRITE IN PLACE: raw `INSERT INTO debts` (:781-785) via HELPER (models pre-022 debt / restore) |
| :813, :823 | test_step5a_an_equivalent_has_exactly_one_baseline | second baseline refused, one header ⚑ T1501 | REWRITE IN PLACE: unchanged; cleanup only |
| :849-871 | test_step5a_a_seed_or_fixture_write_after_the_baseline_is_refused | writer: `debt_fixture_setup` / `debt_operation(kind="SEED")` | REWRITE IN PLACE: SEED via `Book.operation(... operation_for("SEED", ...))` |
| :873-878 | same | refused with `Reason.UNVERIFIABLE_WRITER_AFTER_BASELINE`; state, entries unchanged; PASSED ⚑ T1501 | REWRITE IN PLACE: `pytest.raises(<Book-refusal>)` and its reason field; state asserts unchanged |
| :880-883 | same | same write without baseline commits (anti-vacuum) | REWRITE IN PLACE: unchanged |
| :915-918 | test_step5a_the_scheduled_result_is_its_own_row_and_never_enters_a_checkpoint_or_audit | whole-unit corruption | REWRITE IN PLACE: via HELPER |
| :931-957 | same | FAILED stored as own row, criterion A, checkpoint untouched, no audit | REWRITE IN PLACE: unchanged after HELPER |
| :989-1002 | test_step5a_c6_still_commits_verified_and_criterion_a_is_blind_to_it_until_the_book_moves | C6 COMMITTED, audit [True], stored FAILED with only `b_*` ⚑ 018 VP §2 criterion (б)/7c "(а) passes, (б) fails" | REWRITE IN PLACE: unchanged (writer is honest-context ORM; trigger journals the wrong edge faithfully) |
| :1014-1023 | same | later atom adds `edge_residual`; audit untouched | REWRITE IN PLACE: :1014-1017 via HELPER |

#### tests/integration/test_p015_step5a_reconciliation_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :28 | (import) | journal API | DROP: listener-internal — removed module |
| :203-233 | test_step5a_p_both_construction_paths_build_the_same_tables_and_both_bite | 3 reconciliation tables identical on both paths; CHECK/unique bite | REWRITE IN PLACE: unaffected (tables untouched by 029) |
| :253, :258 | test_step5a_p_passed_failed_unverifiable_and_the_scheduled_row | honest payment PASSED, edges 3 | REWRITE IN PLACE: unchanged |
| :268-274 | same | atom → FAILED 0.00000001 on asyncpg ⚑ T1505/T1508 one atom; §3 selector | REWRITE IN PLACE: atom via HELPER |
| :276, :282-283 | same | unbaselined UNVERIFIABLE; one row each after two runs ⚑ T1508 missing baseline | REWRITE IN PLACE: unchanged after HELPER |
| :297-307 | test_step5a_p_a_fixture_write_after_the_baseline_is_refused | refusal `IN(...)` over native uuids; state unchanged ⚑ T1501 | REWRITE IN PLACE: `pytest.raises(<Book-refusal>)`; reason field |
| :341-343 | test_step5a_p_a_payment_committed_between_the_verifiers_reads_is_still_passed | RC counter-probe: level, PASSED | REWRITE IN PLACE: unchanged |
| :385-404 | test_step5a_p_a_baseline_committed_while_a_seed_is_open_cannot_commit_alongside_it | SEED opened via `debt_operation` | REWRITE IN PLACE: `Book.operation` SEED on the SERIALIZABLE session; baseline taken while it is open |
| :409-423 | same | SEED not committed, cause 40001, baseline stands ⚑ T1501 cutover race | REWRITE IN PLACE: unchanged asserts (docstring: `journal._complete` → Book completion) |

#### tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :204-206, :224-225 | test_a_an_orm_write_of_nan_must_not_reach_the_money_column | journal stand-down / re-arm | DROP: listener-internal — no journal finiteness predicate after B; nothing to stand down |
| :208-223 | same | refusal caught at `commit` | REWRITE IN PLACE: widen `try` to the whole `debt_fixture_setup` block — Book flushes at block exit, so the refusal raises there (C8) |
| :192, :228, :234, :244, :248 | same | non-vacuity; NaN not stored; refused; not NOT NULL; "non-finite" (MoneyNumeric) ⚑ T1526 | REWRITE IN PLACE: asserts unchanged |
| :276-295 | test_b_one_nan_debt_makes_the_sum_of_the_book_stop_being_a_number | stand-down + write | REWRITE IN PLACE: drop stand-down; widen `try` (C8) |
| :273, :304 | same | sum 5 before and after ⚑ T1526 | REWRITE IN PLACE: unchanged |
| :326-343 | test_c_the_database_itself_refuses_nan_when_python_is_bypassed | raw INSERT 7.00 stores (non-vacuity) | REWRITE IN PLACE: raw INSERT under an OPEN envelope context (inside `debt_fixture_setup`, same connection) — else GE001 |
| :347-384 | same | raw NaN INSERT refused 23514 by `chk_debt_amount_positive`, nothing stored ⚑ T1526 (migration reproducer) | REWRITE IN PLACE: run it under the same context so GE001 cannot be the refusal (CHECK precedes AFTER-row trigger anyway; asserts unchanged) |
| :422, :456, :460 | test_d_the_same_hole_in_the_other_money_column_is_closed_too | NaN limit refused 23514 ⚑ T1526 | REWRITE IN PLACE: unaffected (trust_lines); cleanup only |

#### tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| all asserts :297-356, :403-434, :575 | test_rt_012_1_a_signed…, _control…, counter_check (steps 1-2), _control_amount_survives… | door refuses / chain unchanged for scale 8 / door constant | REWRITE IN PLACE: unaffected |
| (B0a, 2026-09-24) | test_rt_012_1_counter_check_widening_the_door_reproduces_the_finding_end_to_end | door widened → 500 E010 refused by **Book's** storability check (spy on `book._refuse_unstorable` records `money_quantization`); then Book's check explicitly bypassed | KEEP (added by B0a, FORK-1): the replacement precision check reads the column's capacity, not `MONEY_MAX_SCALE`, so widening the door does not widen it; survives B1 unchanged, first barrier after the door |
| (B0a, 2026-09-24) | same | journal domain widened → 500 E010 refused at bind by **`MoneyNumeric`** (spy on `MoneyNumeric._refuse_unstorable` records `money_quantization`); then explicitly bypassed | KEEP (added by B0a): in B1, with the journal steps dropped, it directly follows the Book bypass |
| :461 | same | `from app.core.ledger import journal` | DROP: listener-internal — module removed |
| :463-467 | same | 500 E010 by journal quantization predicate (`_MONEY_QUANTUM`) | DROP: listener-internal — journal money-domain predicate removed; trigger checks no quantum |
| :474 | same | widen `_MONEY_QUANTUM` | DROP: listener-internal |
| :490-496 | same | 500 E010 by `_reconcile` debt readback (T1528) | DROP: listener-internal — readback removed; trigger builds the entry from stored NEW (spec :73) |
| :510-521 | same | 500 E010 by `_verify_entries`/`_verify_completed_entries` (T1530) | DROP: listener-internal — same reason |
| :524-533 | same | door widened → 409 `PAYMENT_DELTA_DRIFT`, drift 1E-9 ⚑ T1522 | REWRITE IN PLACE: unchanged; reached after the door is widened and the Book and `MoneyNumeric` checks are bypassed (B0a) |
| :539-555 | same | barrier widened → COMMITTED; ledger holds 0.12345679 ≠ signed | REWRITE IN PLACE: unchanged |
| (new, in place) | same | journal entry of that commit records the STORED value (0.12345679), not the signed one — trigger-side replacement of T1528/T1530 on this scenario | TO WRITE: `assert entry.amount_after == rows[0]` read from `debt_journal_entries` after :547 |

#### tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :117-133 | test_a_real_serialization_failure_restarts_the_whole_inject_unit_of_work | competitor commits the row via Core under journal stand-down | REWRITE IN PLACE: competitor keeps Core `update(Debt)` but inside `debt_fixture_setup` (OPEN envelope, trigger allows); no stand-down. Requires Book nesting check to be per session (C5) |
| :162 | same | exactly one real 40001, at owner's flush | REWRITE IN PLACE: unchanged; re-measure (competitor now also writes journal rows — hypothesis that 40001 still surfaces at the flush) |
| :166-176 | same | staged twice; stored = concurrent + injected once; fired index; note | REWRITE IN PLACE: unchanged |
| :179-181 | same | both inject debt flushes under owner lock (own listener) | REWRITE IN PLACE: unchanged (competitor Core update adds no ORM Debt flush) |

#### tests/unit/test_p015_step5b_criterion_b.py — as built in B1: 7.D

Helpers: `_rewrite_intent` :165-179, `_move_entry_and_debt` :182-197, `_add_entry_and_debt` :200-221 → REWRITE IN PLACE: all via HELPER; `flush_ordinal`→`ordinal` (:134, :215). `_downgrade_to_v1` :445-450 → HELPER, also `schema_version = 1` (C10).

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :261-287 | test_step5b_an_honest_payment_records_both_directions_and_is_recomputed_in_full | edges netted; intent v2; prestate 4 dirs; 4 entries; PASSED; coverage full PAYMENT | REWRITE IN PLACE: only `_operation_entries` column rename; entry count 4 to be re-measured under per-row trigger (hypothesis: equal) |
| :313-330 | test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind | (a) silent, (b) FAILED with 3 delta mismatches + prestate ⚑ 018 VP §2 criterion (б)/7c | REWRITE IN PLACE: unaffected (honest-context ORM writer); cleanup only |
| :356-385 | test_step5b_a_corrupted_payment_record_is_failed [recorded_delta, intent_flow, prestate] | coordinated corruption: (a) silent, (b) FAILED with expected kind ⚑ T1508 criterion-(b) forms | REWRITE IN PLACE: corruption via HELPER |
| :418-435 | test_step5b_net_neutral_cycle_inflation_on_a_payment_is_failed | net-neutral inflation; (a) silent; (b) 3 delta mismatches ⚑ T1508 net-neutral cycle inflation | REWRITE IN PLACE: HELPER + `ordinal` |
| :470-495 | test_step5b_a_v1_payment_is_structural_only_and_never_a_full_recomputation | v2 PASSED full; v1 structural_only, limited, fingerprint differs; coordinated delta corruption stays PASSED (stated limit) | REWRITE IN PLACE: downgrade and corruption via HELPER (set schema_version 1 too, C10) |
| :521-526 | test_step5b_a_v1_payment_with_an_edge_outside_its_flow_pairs_is_failed | FAILED `b_payment_v1_structure`, no baseline | REWRITE IN PLACE: downgrade via HELPER |
| :565-572 | test_step5b_an_honest_clearing_is_recomputed_in_full_and_passed | cleared 10; edges; PASSED; coverage | REWRITE IN PLACE: unaffected; cleanup only |
| :598-607 | test_step5b_the_c6_under_clearing_is_failed_by_b_while_a_stays_blind | skim hits 3; (a) silent; (b) 3 mismatches -10 vs -9.99999999 ⚑ T1508 (недоклиринг) | REWRITE IN PLACE: unaffected (ORM `set` listener, trigger records stored values) |
| :640-716 | test_step5b_a_corrupted_clearing_record_is_failed [5 params] | (a) silent; (b) FAILED per reason; cycle_inflation ⚑ T1508 | REWRITE IN PLACE: all corruptions incl. :679-693 via HELPER |
| :765-771 | test_step5b_an_honest_inject_is_checked_as_its_subset_and_passed | INJECT intent v1; one `U` +1.00; PASSED subset | REWRITE IN PLACE: column rename only (MODE_B) |
| :826-844 | test_step5b_an_inject_outside_its_subset_is_failed [atom_writer, intent_amount, split_edge, decrease] | each subset rule alone FAILED | REWRITE IN PLACE: atom_writer unaffected; other three via HELPER |
| :885 | test_step5b_the_version_split_and_the_check_on_the_metadata_path | versions `{(PAYMENT,1,1,2),(TEST_FIXTURE,1,1,1),(CLEARING,1,1,1)}` | REWRITE IN PLACE: schema_version 2 for all new envelopes (spec :62) |
| :887-913 | same | CHECK: intent 2 ok, 3 refused; schema/money 2 refused | REWRITE IN PLACE: probe in a rolled-back transaction (committing an OPEN envelope is refused by the deferred trigger; DELETE refused by guard); schema 2 admitted, schema 3 refused; money 2 still refused |
| :954-975 | test_step5b_a_b_finding_is_stored_in_the_same_row_and_fingerprint_and_never_in_a_checkpoint | one FAILED row, both criteria, checkpoint untouched, fingerprint stable | REWRITE IN PLACE: unaffected; cleanup only |
| :1011-1024 | test_step5b_the_prestate_is_one_read_after_the_operator_stop_and_before_the_envelope | exactly one statement (batched `debts` read) between stop and envelope INSERT | REWRITE IN PLACE: Book's SAVEPOINT now sits between (C4) — ignore `SAVEPOINT`/`RELEASE` statements in `between`, or anchor on the SAVEPOINT |

#### tests/integration/test_p015_step5b_criterion_b_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :169-173 | test_step5b_p_both_construction_paths_widen_only_the_intent_version_and_it_bites | 3 version CHECKs equal on both paths; intent `ARRAY[1, 2]`; bites intent2 ok, intent3/money2/schema2 23514 | REWRITE IN PLACE: `schema=2` → None, add `schema=3` → 23514; probe already rolled back (`_Undo`), guard allows OPEN INSERT |
| :207-214 | test_step5b_p_the_prestate_read_follows_every_advisory_lock_and_the_for_share | locks < stop FOR SHARE < envelope; exactly one `debts` read between | REWRITE IN PLACE: same SAVEPOINT exclusion as unit :1022 (C4) |
| :299-305 | test_step5b_p_at_serializable_a_writer_outside_the_owner_lock_cannot_make_the_record_disagree | 40001 retry, prestate re-read 3→4, edge 1, no (b) finding | REWRITE IN PLACE: unaffected (race writer uses `debt_fixture_setup`, payment's Book not yet open); cleanup only |
| :338-342 | test_step5b_p_stand_control_at_read_committed_the_same_race_does_make_the_record_disagree | RC: one read, `b_prestate_mismatch` | REWRITE IN PLACE: unaffected |
| :394-410 | test_step5b_p_an_application_writer_waits_on_the_owner_lock_through_the_prestate_window | clearing waits; cleared 7; both full recomputation, no finding | REWRITE IN PLACE: unaffected (needs per-session Book flag, C5) |
| :450 | test_step5b_p_the_criterion_b_controls_hold_on_asyncpg [15 params] | unit controls on asyncpg ⚑ T1508 (b)-forms; §3 selector | REWRITE IN PLACE: follows the unit rewrite |
| :455 | test_step5b_p_a_b_finding_is_stored_in_the_same_row_on_asyncpg | unit fn on asyncpg | REWRITE IN PLACE: follows unit |
| :517-533 | test_step5b_p_an_unwidened_version_check_refuses_the_payment_and_the_service_aborts_it | 23514 on envelope INSERT → 5xx, ABORTED, no debt moved, no lock, no envelope | REWRITE IN PLACE: unaffected (Book savepoint rollback leaves no envelope); restore CHECK text unchanged |

#### tests/unit/test_p015_step5c_reaction_and_hold.py — as built in B1: 7.D

`_set_debt` :148-151 → REWRITE IN PLACE: via HELPER (both the "fault" and the "repair" writes). `hold_directly` :107-135 keep (domain helper, imported by races :79 and tick :45).

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :244-256 | test_step5c_one_atom_around_the_application_holds_the_equivalent_through_the_scheduled_host | atom → hold on latest FAILED; residual 1 atom; one log, metric+1, announced after committed ⚑ T1516 / T1508 one atom → hold | REWRITE IN PLACE: atom via HELPER |
| :306-319 | test_step5c_a_hold_whose_commit_fails_persists_nothing_and_emits_no_log_and_no_metric | failed hold commit: no hold, evidence rolled back, no log/metric ⚑ T1516 | REWRITE IN PLACE: second atom via HELPER |
| :345-350 | test_step5c_one_equivalent_failing_to_hold_does_not_roll_back_anothers | per-equivalent transaction ⚑ T1516 | REWRITE IN PLACE: via `_faulty_triangle` HELPER |
| :383-386 | test_step5c_the_re_run_is_the_confirmation_a_fault_gone_by_the_reaction_is_not_held | repair before reaction → not held ⚑ T1516 | REWRITE IN PLACE: repair via HELPER |
| :410-416 | test_step5c_a_repeated_failed_is_idempotent | one hold never re-pointed; one log/metric ⚑ T1516 | REWRITE IN PLACE: via HELPER |
| :438-443 | test_step5c_no_hold_on_unverifiable | UNVERIFIABLE → no reaction, direct call no hold ⚑ T1546 | REWRITE IN PLACE: atom via HELPER |
| :470-484 | test_step5c_no_hold_on_a_verifier_error_either_before_or_inside_the_reaction | error ≠ verdict, no hold | REWRITE IN PLACE: via `_faulty_triangle` HELPER |
| :516-521 | test_step5c_no_hold_on_an_inherited_critical_checkpoint | critical checkpoint + PASSED → no hold ⚑ T1516 | REWRITE IN PLACE: unaffected; cleanup only |
| :544-558 | test_step5c_the_evidence_of_a_hold_cannot_be_deleted_while_held | DELETE evidence → 23503, hold remains ⚑ T1546 | REWRITE IN PLACE: `_faulty_triangle` via HELPER; the DELETE on results stays raw (table not guarded) |
| :599-607 | test_step5c_a_held_equivalent_refuses_a_new_payment_before_any_transaction_exists | prepare refusal E008, no tx, other equivalent commits ⚑ T1546 | REWRITE IN PLACE: unaffected (`hold_directly`); cleanup only |
| :641-644 | test_step5c_inactive_and_held_is_refused_as_inactive | inactive wins | REWRITE IN PLACE: unaffected; cleanup only |
| :676-701 | test_step5c_a_payment_prepared_before_the_hold_is_refused_at_commit_before_the_envelope | hold read in stop statement; nothing written before refusal; no envelope INSERT; ABORTED ⚑ T1546 | REWRITE IN PLACE: atom (:677) via HELPER; statement asserts unaffected (refusal precedes Book savepoint) |
| :779-794 | test_step5c_a_held_equivalent_refuses_clearing_and_another_equivalent_still_clears | held clearing refused, other clears ⚑ 018 VP §2 hold T1546 | REWRITE IN PLACE: atom (:777) via HELPER; cleanup `_drop_cycle` (:737, :742) |
| :863-870 | test_step5c_clearing_real_reports_the_hold_as_its_declared_409 | 409 declared reason, debts unchanged | REWRITE IN PLACE: unaffected (MODE_B) |
| :915-969 | test_step5c_the_hold_is_cleared_only_explicitly_after_a_later_passed_and_audited | full lifecycle, audit ⚑ T1546 | REWRITE IN PLACE: F1/F2/repair (:910, :921, :928) via HELPER |

#### tests/integration/test_p015_step5c_hold_races_postgres.py — as built in B1: 7.D

`_baseline_and_one_atom` :97-110 → REWRITE IN PLACE: atom via HELPER.

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :196-221 | test_step5c_p_a_payment_commit_waiting_behind_the_reaction_is_refused_by_the_hold | HOLD FIRST: commit waits, refused via 40001 FOR SHARE, debts unchanged, ABORTED ⚑ T1546 | REWRITE IN PLACE: atom via HELPER |
| :266-282 | test_step5c_p_a_reaction_arriving_while_a_payment_holds_its_check_waits_and_holds_after | PAYMENT FIRST ordering; next payment refused ⚑ T1546 | REWRITE IN PLACE: atom via HELPER |
| :306-329 | test_step5c_p_the_owner_lock_comes_before_the_authoritative_snapshot | repair lands while reaction waits → not held ⚑ T1516 | REWRITE IN PLACE: atom and repair (:318-322) via HELPER (helper takes no advisory lock — required) |
| :362-389 | test_step5c_p_a_clearing_that_waited_behind_the_reaction_refuses_in_its_fresh_snapshot | clearing refused after hold; debts unchanged ⚑ 018 VP §2 hold | REWRITE IN PLACE: atom via HELPER |
| :430-440 | test_step5c_p_a_reaction_waits_for_a_clearing_that_already_read_the_hold | CLEARING FIRST ordering | REWRITE IN PLACE: atom via HELPER |
| :483-487 | test_step5c_p_an_expired_payment_in_a_held_equivalent_is_aborted_as_expired | TTL precedence | REWRITE IN PLACE: unaffected |
| :531-537 | test_step5c_p_the_admin_clear_waits_for_the_owner_lock | clear waits on owner lock | REWRITE IN PLACE: unaffected |
| :567-568 | test_step5c_p_the_evidence_of_a_hold_cannot_be_deleted_while_held | 23503, hold remains ⚑ T1546 | REWRITE IN PLACE: unaffected |
| :638-681 | test_step5c_p_both_construction_paths_build_the_same_hold_column_and_the_downgrade_refuses_a_hold | hold column/FK equal; 028 downgrade refuses while held | REWRITE IN PLACE: unaffected text; now also passes through 029 downgrade (C11) |

#### tests/integration/test_payment_commit_advisory_locks_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :247-289 | `_give_the_journal_a_history` | 2000 COMPLETED envelopes + ANALYZE | REWRITE IN PLACE: INSERT with `state='OPEN'` then `UPDATE … SET state='COMPLETED', completed_at, effect_count, effect_digest` in the same transaction (legal under guard; deferred trigger sees final COMPLETED); drop `flush_count` from column list and values |
| :292-317 | `_forget_the_journal_history` | DELETE filler + VACUUM | REWRITE IN PLACE: run :700 on a disposable clone (mode B) and drop this helper; else bounded replica purge |
| :226-244 | `_completion_update_plan` | EXPLAIN of holder's completion UPDATE | REWRITE IN PLACE: mirror Book's stage-B completion statement (C6) |
| :743-813 | test_concurrent_duplicate_commit_is_idempotent_with_journal_history_postgres | premise (history, index plan, parked, 23505 at envelope INSERT, envelope constraint); idempotent result; one COMPLETED envelope ⚑ T1529 | REWRITE IN PLACE: asserts unchanged; re-measure 23505/40001 distribution (C6) |
| all other asserts | tests :321, :475, :1004, :1120, :1246 | advisory-lock protocol | REWRITE IN PLACE: unaffected; cleanup `_cleanup_seed` :165 only. :475 (duplicate commit via 40001 path) ⚑ T1529 |

#### tests/integration/test_p017_t1711_seed_recipe_postgres.py — as built in B1: 7.D

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :131-171, :192-196, :287-301, :332, :349-351 | control, unreachable state, stop-and-name, empty DB, absent DB | seed runs/refuses; counts | REWRITE IN PLACE: unaffected |
| :452-453 | `_doctorings` reconciliation_passed | debts +1/-1 → check red/green | REWRITE IN PLACE: via HELPER |
| :469-470 | `_doctorings` every_operation_examined | PAYMENT intent v1/v2 | REWRITE IN PLACE: via HELPER (guard: intent version immutable) |
| :479-480 | `_doctorings` clearing_executed | kind CLEARING→PAYMENT→back | REWRITE IN PLACE: via HELPER (guard: kind immutable) |
| :489-496 | `_doctorings` activity_in_every_equivalent | DELETE/INSERT `debt_operation_equivalents` | REWRITE IN PLACE: via HELPER (guard: no DELETE; INSERT only for OPEN op in context) |
| :457-466, :474-475, :484-485 | offsets, bottleneck, surviving cycle | untouched tables | REWRITE IN PLACE: unaffected (may stay on `_driver_sql`) |
| :516-533 | test_every_acceptance_check_reddens_on_the_state_it_exists_to_notice | each check red on doctoring, green on restore | REWRITE IN PLACE: unchanged asserts |
| :609-618 | test_the_launcher_readiness_survives_a_clearing_the_product_ran | readiness refuses unexplained money, recovers | REWRITE IN PLACE: :610-617 via HELPER |

(`test_p015_step5c_hold_through_the_tick_sqlite.py`, `test_p015_t1529_…`: KEEP-AS-IS, no table.)

### 7.C. Находки прохода (к разделу 3)

1. **Reconciliation reads `flush_ordinal` exactly at the spec's lines and nothing else of the removed schema.** `grep -n "flush_ordinal|flush_count|schema_version|intent_encoding_version" app/core/ledger/reconciliation.py` → `flush_ordinal` only :330, :350, :384, :527-528, :714; `flush_count` none; `schema_version` none; envelope `state` never read. `app/` + `scripts/` reference `flush_ordinal`/`flush_count` only in `reconciliation.py` (above), `journal.py`, `journal_tables.py` (:140, :168-185, :213, :237 `chk_debt_journal_entries_ordinal`, :292). Note `chk_debt_journal_entries_ordinal` (`flush_ordinal >= 1`, journal_tables.py:237) also needs renaming in 029.
2. **No other `app/`/`scripts/`/`migrations/` importer of `journal.py`** besides `app/db/models/__init__.py:32` and `app/core/ledger/book.py:54`. Stale doc references to journal internals that must be edited with the deletion: `app/core/ledger/reconciliation.py:77` (names `journal.py` `Reason.UNVERIFIABLE_WRITER_AFTER_BASELINE` as the post-baseline refusal), `app/core/ledger/book.py:19-22`, `app/db/journal_tables.py:12`, `:280`, `migrations/versions/024_…:24` (applied migration — docstring only, leave).
3. **Contradictory-arithmetic form needs BOTH exceptions at once.** Spec §2/§4 names "one-time clone without CHECK" and "HELPER" as separate exceptions, but `tests/unit/test_p015_step5a_reconciliation.py:445-451` forges via `UPDATE debt_journal_entries`, which the stage-B guard trigger refuses on the clone too. It must be HELPER (replica) on the CHECK-less clone. The complementary "normal schema refuses such a row" test must also go through replica, else the guard (not the CHECK) refuses it and the CHECK is unmeasured.
4. **Book's savepoint breaks two anchored statement-order tests.** Spec :68 puts the envelope INSERT inside Book's own savepoint; `SAVEPOINT` is emitted through `before_cursor_execute`, so it lands between the stop/prestate read and `INSERT INTO debt_operations`: `tests/unit/test_p015_step5b_criterion_b.py:1022-1024` and `tests/integration/test_p015_step5b_criterion_b_postgres.py:213-214` ("exactly one statement between") go red. `test_p015_step5c_reaction_and_hold.py:694-699` is unaffected (refusal precedes Book). (Hypothesis on exact statement text; measure.)
5. **"флаг Book в процессе" (spec :67) must be per session/connection, not per process.** Several tests run two Book operations concurrently in one process: the inject-retry competitor (after rewrite) runs while the inject's operation is open (`test_p015_inject_retries_a_serialization_failure_postgres.py:104-134`); step5b_p :350 (payment + clearing), all hold races, advisory-lock races (two commits). A process-wide flag would refuse them — and concurrent requests in the app.
6. **T1529 premise may move under the trigger.** `test_payment_commit_advisory_locks_postgres.py:700` depends on SSI reporting 23505 (15/18 measured, :210-221) and on the holder's completion UPDATE plan (:226-244, :755). Stage B adds reads of `debt_operations` in the holder's transaction: the `debts` trigger's `EXISTS (… id = ctx AND state = 'OPEN')` per row (spec :43), the deferred constraint trigger re-reading by `NEW.id` (:60), the `debt_operation_equivalents` guard. Extra SIRead locks — possibly on `ix_debt_operations_open`, the index the waiter's OPEN INSERT writes — can push every attempt to the 40001 rescue, and the test then FAILS its premise (by design). Also `_completion_update_plan` must mirror Book's actual stage-B completion statement; `_give_the_journal_a_history` (:269-277) inserts `COMPLETED` + `flush_count` (refused / dropped column); `_forget_the_journal_history` (:309-312) DELETE refused. The unit literal `ENVELOPE_INSERT` (`test_p015_t1529_…:31-36`) is not measured against Book's real INSERT; if Book changes the statement shape (e.g. `RETURNING`, raw text), the classifier's `is_envelope_insert` match must be re-proven by :700.
7. **HELPER preconditions.** `session_replication_role` is a superuser-only GUC (PGC_SUSET; PG15+ `GRANT SET ON PARAMETER`). No test sets it today (`grep session_replication_role tests app scripts` empty); the role's `rolsuper` is unverified — check on CI `postgres:16` and the local portable server. Replica also disables FK (RI) triggers and the deferred constraint trigger, so HELPER writes are not FK-checked (e.g. `_add_entry_and_debt` naming an `operation_id`): corruptions must only produce states the verifier reads, and teardown cannot rely on RESTRICT having held.
8. **t1526 test_a/test_b will ERROR, not fail, if only the stand-down is removed.** Today `uninstall_write_guard` makes `debt_fixture_setup` a no-op (`tests/debt_setup.py:130-134`), so the NaN flush happens at `session.commit()` inside the `try` (:219-223, :290-293). After B the block always opens `Book.operation`, which flushes at exit (spec :68) — the MoneyNumeric refusal is raised by the `async with` exit, outside the `try`. Also the stage-B Book `Effect` check `amount > 0` on a NaN Decimal raises `InvalidOperation` — relevant only if these tests are moved onto `posting.apply`.
9. **t1526 test_c:** non-vacuity raw `INSERT INTO debts` (:327-339) → GE001. The NaN INSERT still yields 23514 because CHECK precedes AFTER-row triggers, but without context the stand can no longer store the control row.
10. **v1 downgrade stand models an impossible envelope after B.** `_downgrade_to_v1` (`test_p015_step5b_criterion_b.py:445-450`) sets `intent_encoding_version = 1` on a new envelope that now carries `schema_version = 2`; historical v1 PAYMENTs are all `schema_version = 1`. Verdict unaffected (reconciliation keys on `(kind, intent_encoding_version)`, :155-160, :748-751, never `schema_version`), but HELPER should set `schema_version = 1` too.
11. **Criterion (b) vs schema_version=2 — no conflict in code, conflict in tests only.** `_READABLE_ENVELOPES` keys `("PAYMENT", 2)`/`("PAYMENT", 1)` on `intent_encoding_version` (`reconciliation.py:155-160`, `:748-751`); `schema_version` is not read. So 029's `schema_version = 2` for all new envelopes is safe provided Book keeps `intent_encoding_version` per kind (PAYMENT = `PAYMENT_INTENT_ENCODING_VERSION` = 2, others 1). Tests pinning `schema_version = 1`: step5b unit :885, :889, :911-913 (expects schema 2 refused), step5b_p :105, :135, :171 (`schema=2` → 23514 expected).
12. **Downgrade path through 029.** `test_p015_step5c_hold_races_postgres.py:667`, `:679` downgrade `head → 027` on a migrated scratch DB; after B this runs 029's downgrade first. Spec :157 has it refuse only when `schema_version = 2` envelopes exist; the test's DB has none, so 029's downgrade must succeed on an empty DB and restore `flush_count`/`flush_ordinal`, or this T1546 test goes red for a 029 reason.
13. **Ordinal collisions in forged duplicates.** `test_p015_step5a_reconciliation.py:520-522` uses `flush_ordinal + 100`; with a shared sequence the unique `(operation_id, ordinal, equivalent_id, debtor_id, creditor_id)` is only hit on the same op and edge — negligible, but use a fresh `nextval` or `max+1`.
14. **Cleanup exposure in this group.** Direct `purge_test_ledger` callers: t1526 :146, rt1 :198, advisory :165, step5c unit :737, :742; indirect through `_drop_triangle` (b4 :142 — step5a unit, step5a_p, step5b unit, step5b_p, step5c unit), p1_money_replay `_cleanup`, interlock `_cleanup_interlock_case`, inject_holds `_cleanup` :193. Already on clones (purge droppable): rt1 (:93), step5c races (:83), step5b inject tests (MODE_B), tick (t1544 `committed_database`), t1711 (`cloned_database`). The rest commit on the tier DB through `TestingSessionLocal` while taking `db_session` (mode A, but outer-rollback does not cover them) → spec option (1) means moving them to a clone.
15. **Entry-count assumptions to re-measure (hypothesis).** step5b unit :279 (`4` entries), step5a unit :483 (one `D`), :515 (one `U`), step5b :767 (one `U`): the listener wrote one entry per edge per flush; the trigger writes one per row statement. Equal if the ORM issues one statement per row per flush (expected), but not proven.

### 7.D. B1 part ii, group 2 — as built (2026-09-24, branch `claude/018-b1-g2`, commit `c9b4a7c`)

Scope: the reconciliation/hold family, the raw writers of group 3, and the `T1528`/`T1530`/`T1531`/`T1532` files of section 5. The tables of sections 5 and 7 above stay as the pre-B1 analysis; this is what was built. Node ids are `path::function`; `[..]` = every parametrisation.

**Per file.**

| file | outcome |
|---|---|
| `tests/unit/test_p015_step5a_reconciliation.py` | REWRITE in place: `_around_the_application` now calls the named corruption helper (`tests/ledger_corruption.py::corrupt`, URL of the factory's clone); new `_driver_statement` (triggers and FKs on) for tables the journal does not guard; forged duplicate uses `nextval('debt_journal_entries_ordinal_seq')`; SEED/TEST_FIXTURE after baseline → `BookError`, `Refusal.UNVERIFIABLE_WRITER_AFTER_BASELINE`; contradictory arithmetic = helper on the CHECK-less clone (both exceptions). 17 tests |
| `tests/integration/test_p015_step5a_reconciliation_postgres.py` | REWRITE in place: refusal via `BookError`; the cutover race opens `Book.operation(SEED)`; still `40001` (measured). 5 tests |
| `tests/unit/test_p015_step5b_criterion_b.py` | REWRITE in place: `ordinal`; helper corruptions; `_downgrade_to_v1` also sets `schema_version = 1` (7.C item 10); version probe in a rolled-back transaction, `schema_version` 1 and 2 admitted, 3 refused, money 2 refused; new envelopes `(kind, 2, 1, intent)`; the pre-state anchor names the book's opening (context read + `SAVEPOINT`) instead of counting it. Entry count `4` re-measured: unchanged. 23 tests |
| `tests/integration/test_p015_step5b_criterion_b_postgres.py` | REWRITE in place: `schema=2` → admitted, `schema=3` → `23514`, both paths; same anchor. 22 tests |
| `tests/unit/test_p015_step5c_reaction_and_hold.py` | REWRITE in place: faults via the helper (through `_around_the_application`); the evidence-DELETE test uses `_driver_statement`, because `replica` would switch its FK off. `hold_directly` unchanged (the tick module collects again once step 5a does). 15 tests |
| `tests/integration/test_p015_step5c_hold_races_postgres.py` | REWRITE in place: atom and repair via the helper (takes no advisory lock). 9 tests |
| `tests/integration/test_p015_step5c_hold_through_the_tick_sqlite.py` | KEEP-AS-IS, collects again. 3 tests |
| `tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py` | REWRITE in place: no stand-down; `try` spans the fixture block (book flushes at exit); `test_c` raw INSERTs run inside a `Book.operation(TEST_FIXTURE)` (context set; not `debt_fixture_setup`, same guard), NaN still `23514 chk_debt_amount_positive`. 4 tests |
| `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` | REWRITE in place: the three listener stairs (`_MONEY_QUANTUM`, `_reconcile`, `_verify_entries`/`_verify_completed_entries`) dropped; Book check → `MoneyNumeric` bind → 409 drift → COMMITTED; NEW assertion: the payment's entry `amount_after` equals the stored `0.12345679`. 7 tests |
| `tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py` | REWRITE in place: competitor Core `update(Debt)` inside its own `Book.operation(TEST_FIXTURE)` on its own session (not a `debt_fixture_setup` block, whose AST guard admits no Core statement); `40001` still at the owner's flush (asserted). 1 test |
| `tests/integration/test_payment_commit_advisory_locks_postgres.py` | REWRITE in place: history filler inserts `OPEN` (schema 2, no `flush_count`) and completes in the same transaction, `ANALYZE` after commit; plan probe mirrors the book's completion UPDATE. Re-measured: `23505` 18/18 attempts (3 runs × 6), premise unchanged. 6 tests |
| `tests/integration/test_p017_t1711_seed_recipe_postgres.py` | REWRITE in place: every doctoring and restore through the helper. 7 tests |
| `tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py` | REWRITE: module scratch database built by `create_all` (the MODEL half); debt without history seeded by the helper; deletion on an ordinary connection, `session_replication_role = origin` asserted. 4 tests |
| `tests/integration/test_p015_t1533_participant_deletion_keeps_obligations_postgres.py` | REWRITE: module clone of the migrated template (was the tier, whose `create_all` schema failed the constraint-name asserts on the debug path at `2fb1056`); helper seed; FK name asserts and "no history" kept; row cleanup gone. 5 tests |
| `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py` | REWRITE: CHECK comparison on both paths kept; the bite probe goes through `tests/ledger_corruption.py::probe` with an honest control, premise: a direct INSERT is answered by the guard (`23000`); predicate-text test moved in verbatim; the listener tests dropped (NEW-C/NEW-H below). 2 tests |
| `tests/unit/test_p015_t1528_the_guard_reads_what_the_statement_writes.py`, `tests/unit/test_p015_t1530_the_journal_reads_its_own_record_back.py` | DELETED (planned REWRITE realised as re-homing): every surviving row is a NEW-C/A/G/H or planned `T1801`/`T1803` test below; the rest were DROP |
| `tests/unit/test_p015_t1531_the_verification_read_is_not_rewritable.py`, `tests/unit/test_p015_t1532_a_savepoint_is_accounted_for_in_sql.py`, `tests/integration/test_p015_t1528_the_statement_is_read_not_guessed_postgres.py` | DELETED as planned |
| **new** `tests/integration/test_p018_b_the_record_is_the_stored_row_postgres.py` | NEW-C, NEW-A, NEW-G, NEW-H for these files' rows. 8 tests |

**⚑ rows of these files → node.** Counted over the section 5 and 7 tables of the files above: 190 rows, 90 ⚑ — REWRITE IN PLACE 112 (46 ⚑), TO WRITE 38 (27 ⚑), DROP 38 (17 ⚑), KEEP 2. Every REWRITE IN PLACE row stays on its own test (same name, table above). TO WRITE and re-homed rows:

| rows (section 5/7) | node |
|---|---|
| t1528 unit :230, :241, :244 (⚑ T1528, inverted) | `tests/integration/test_p018_b_the_record_is_the_stored_row_postgres.py::test_a_sql_expression_amount_from_a_late_listener_is_stored_and_journalled_as_stored` |
| t1528 unit :299, :305-:316; t1528 PG :150-:163 (⚑ T1528, P1803-GE002) | `…the_record_is_the_stored_row_postgres.py::test_a_literal_edge_from_a_late_listener_is_refused_as_a_key_change` + `tests/integration/test_p018_a_write_without_context_is_refused_by_the_database.py::test_t1803_moving_a_stored_debt_to_another_edge_is_refused_as_ge002` |
| t1528 unit :383-:398; t1531 :163, :180, :181 (⚑, inverted) | `…::test_parameters_rewritten_on_the_connection_are_what_is_stored_and_journalled` |
| t1528 unit :465-:477 (⚑, inverted) | `…::test_an_insert_rewritten_on_the_engine_instance_is_stored_and_journalled_as_stored` |
| t1528 unit :517-:521 | `…::test_one_flush_with_an_insert_an_update_and_a_delete_and_one_multi_row_statement` |
| t1528 unit :562-:567 (P1801 (е)) | `tests/integration/test_p018_a_write_without_context_is_refused_by_the_database.py::test_t1801_f_an_update_that_moves_no_money_passes_and_records_nothing` |
| t1530 unit :237, :249-:251, :304, :311, :312, :421, :422; t1530 PG :359, :373, :374 (⚑ T1530, inverted) | `…::test_the_entry_insert_is_never_a_client_statement_so_no_listener_can_rewrite_it` (+ full-width form below for PG :373) |
| t1530 unit :362-:365 (⚑ P1803-jINSERT) | `tests/integration/test_p018_b_journal_guards_postgres.py::test_t1803_a_direct_entry_insert_is_refused_even_with_a_valid_open_context` |
| t1530 unit :487-:498 (⚑ T1530/T1538, P1803-guard) | `tests/integration/test_p018_b_journal_guards_postgres.py::test_t1803_entries_and_membership_are_never_rewritten_and_no_journal_table_truncates` |
| t1530 unit :539, :543-:549; t1532 :312, :319 (NEW-G) | `…::test_an_inner_savepoint_rolled_back_inside_an_operation_leaves_only_what_survived` |
| t1532 :311, :318 (P1803-deferred normal / sp-rollback) | `tests/integration/test_p018_b_journal_guards_postgres.py::test_t1803_deferred_normal_and_caller_savepoint_rollback_both_commit` |
| t1528 PG :222, :230, :231 (⚑); t1528 PG :262, :265; t1530 PG :407-:411 (NEW-H) | `…::test_full_width_money_is_stored_and_journalled_exactly_even_when_rewritten` |
| t1530 unit :579-:585 (REWRITE IN PLACE, moved) | `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py::test_t1530_the_migration_and_the_metadata_spell_the_same_predicate` |
| t1530 PG :250-:272 (⚑ T1530/018) | `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py::test_t1530_p_the_constraint_exists_and_bites_on_both_construction_paths` |
| step5a unit "(new)" (⚑ T1505/T1508 decision 018) | `tests/integration/test_p018_a_write_without_context_is_refused_by_the_database.py::test_t1801_a_raw_driver_write_with_no_operation_is_refused_and_the_row_is_unchanged` |
| rt1 "(new, in place)" | `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py::test_rt_012_1_counter_check_widening_the_door_reproduces_the_finding_end_to_end` |
| T1533 (4.4, F5: "no journal history", "debt FK refuses deletion") | `tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py::{test_the_database_refuses_to_delete_a_debtor_who_still_owes, test_the_database_refuses_to_delete_a_creditor_who_is_still_owed, test_no_journal_history_names_these_participants, test_a_participant_who_owes_nothing_still_deletes}`; `tests/integration/test_p015_t1533_participant_deletion_keeps_obligations_postgres.py::{same four, with test_the_refusal_is_the_debts_own_constraint_and_not_the_journals_history, test_the_migrated_schema_declares_restrict_on_both_participant_columns}` |

**DROP rows (38, 17 ⚑)** — exactly the rows marked DROP in the section 5 and 7 tables of these files, each with the removed contract named there: `_journal_write` grant/parser, `_reconcile` and `_verify_entries` readbacks, `before_execute` provenance and `_raw_params`, savepoint accounting (`LOST_SAVEPOINT_CLOSE`, `pending_savepoint_rollbacks`), driver probe, per-connection registration, module exports, `flush_count` arithmetic, `journal.py` import rows, the RT1 journal stairs. The `FORK-2` flip of the prevented-savepoint-rollback rows (t1528 :750-:767, t1532 :161-:188): supported-path protection is `tests/integration/test_p018_b_book_transaction_contract_postgres.py::test_fork2_a_failed_rollback_after_completed_makes_the_transaction_unusable` and `::test_fork2_a_failure_after_completed_whose_rollback_succeeds_is_an_ordinary_refusal` (part i).

**Mutations (each reverted; `git status` of `app/`, `migrations/` clean after):**

| mutation | red |
|---|---|
| `reconciliation._current_debts` reads `debts.amount` at 7 decimals | 18: step5a unit ×2, step5a PG ×1, step5c unit ×10, hold races ×5 |
| `book._complete`: post-baseline SEED/TEST_FIXTURE refusal disabled | 4: step5a unit `[TEST_FIXTURE]`, `[SEED]`; step5a PG fixture refusal and cutover race |
| `debts` trigger U branch never writes (`… OR true THEN RETURN NULL`, `journal_triggers.py` and migration 029) | 6 of 8 in `test_p018_b_the_record_is_the_stored_row_postgres.py` (the two green: engine-instance INSERT, literal-edge refusal) |
| `debts` trigger I branch records `round(NEW.amount, 7)` (both copies) | RT1 counter-check: `entry records '0.12345680' while debts holds '0.12345679'` |
| model `debts.debtor_id` `ondelete='CASCADE'` | T1533 unit debtor test |
| `MoneyNumeric._refuse_unstorable` a no-op | T1526 `test_a` (message no longer names "non-finite") |
| criterion (b) findings not added to the outcome | 31 of 45 step5b unit + PG |

**Durations (local, debug path, `--durations=0`, same file set; module fixtures counted on their first test).** At `2fb1056` (listener) 443.7 s summed (4 T1533 PG failures there, on the tier's `create_all` schema); after, 461.2 s (+17.5 s): step5a unit +8.7, step5b unit +10.2 (each corruption opens its own helper engine and checks the privilege), T1533 unit +3.4 (a `create_all` scratch database), new module +4.4; deleted listener files −4.5, T1530 PG −3.2, step5b PG −5.9. `test_p017_t1711_seed_recipe_postgres.py` is 252.6 → 255.5 s of either total (unchanged by this slice; not in the `not slow` exclusion).

## 8. Механизм уборки для каждого вызывающего `purge_test_ledger`

- `purge_test_ledger` is `tests/debt_setup.py:246-337` (spec cites `:233-325`, `:302`, `:323`: **stale**). Journal/envelope DELETEs `:313-317`; debts DELETE `:336` (by equivalent only; `tx_ids` select envelopes only, never debts).
- Mode A `db_session`: `tests/conftest.py:517-530` (one connection, outer txn, rollback). Mode B: `committed_database`/`committed_session` `:448-464`, clone of migrated template `:393-445`, dropped at test end; `MODE_B` param `:557`; `sessionmaker_of` `:574`.
- **Existing precedent for CLONE of this exact family:** `tests/tier_on_a_clone.py` (autouse, rebinds `tests.conftest.TestingSessionLocal` to the clone's sessionmaker via monkeypatch). Rebinds **only** `TestingSessionLocal`, not `tests.conftest.engine`, not `TEST_DATABASE_URL`. Own-engine modules take `committed_database.url` (precedent `test_p015_p1_money_replay_postgres.py:103-126`).
- No direct caller writes through mode A: every one commits through `TestingSessionLocal()` sessions or its own engine on the tier DB (`db_session` is requested only for a dialect check). So ROLLBACK is applicable to none.
- `pg_locks` is cluster-wide; pid-filtered observers keep working across DBs. Observers filtering `current_database()` (interlock `_no_advisory_lock_is_held`) work once the observer is on the clone.

### B0b as built (2026-09-24, branch `claude/018-b0b-disposal`, commit `48aec47`)

The tables below this subsection are the pre-B0b analysis (anchors on `3a038d3`) and stay as written. This is what B0b actually did. Nothing in `app/` changed; the listener journal is still the only journal writer. No assertion changed, no skip/xfail was added; collected 3036 before and after (3029 passed, 2 skipped, 4 deselected, 1 xfailed in both full runs).

**Two spellings of one rebinding** (`tests/tier_on_a_clone.py`): importing `tier_sessions_on_a_clone` makes the `TestingSessionLocal` rebinding autouse for the module (as before); importing `tier_on_a_clone` makes it an opt-in fixture, `@pytest.mark.usefixtures("tier_on_a_clone")`, for modules where some tests commit nothing, or already use `MODE_B` (which clones under the same name, so a test must never get both). `tests.conftest.engine` is **not** rebound — a test that needs an engine over the clone asks for `committed_database` and uses `.engine`/`.url` by name — and `TEST_DATABASE_URL` is not rebound (scratch naming, `tier_on_a_clone.py:25`). ROLLBACK isolation (fork 5 MIXED): no converted test qualified — each commits through several sessions/connections or reads a commit back on another one; the tests that commit nothing were left where they were (mode A or no rows) and pay for no clone.

| module | tests (clones before → after) | mechanism | why (one line) |
|---|---|---|---|
| `test_clearing_commit_replay_postgres.py` | 6 (0 → 6) | CLONE, module autouse; SERIALIZABLE sessions over `committed_database.engine` (was `tests.conftest.engine`) | clearing needs an engine-bound session; several sessions commit and are observed |
| `test_clearing_payment_prepare_interlock_postgres.py` | 10 (0 → 7) | CLONE opt-in for the 7 seeding tests; their one-connection engines over `committed_database.url` | the T1537 check, the external-bind refusal and the preflight cancellation commit no rows and stay on the tier |
| `test_clearing_skip_releases_locks_postgres.py` | 6 (0 → 6) | CLONE, module | seeded rows committed and raced through two sessions |
| `test_concurrent_clearing_payment_lost_update_postgres.py` | 1 (0 → 1) | CLONE, module | clearing + payment + observer sessions |
| `test_concurrent_prepare_routes_bottleneck_postgres.py` | 2 (0 → 1) | CLONE opt-in for the payment test | the prepare-only test writes no debt or journal row and keeps its own row cleanup on the tier |
| `test_p012_money_form_and_detector_reach_postgres.py` | 25 (0 → 1) | CLONE opt-in for `test_the_persisted_clearing_payload_...` | clearing refuses a connection-bound session; the other tests are mode A (rollback) already |
| `test_p012_rt1_...`, `test_p012_rt2_...` | 7, 3 (unchanged) | existing CLONE; row cleanup deleted | purge was redundant on the clone |
| `test_p015_b4_entries_and_money_postgres.py` | 28 (0 → 28) | CLONE via the stand's engine (`serializable_engine(committed_database)`); pid-filtered observer engine stays on the tier | commit boundaries are the subject; autouse checker removed |
| `test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py` | 5 (0 → 5) | CLONE via `serializable_factory(committed_database)` | commits are the subject; autouse checker removed |
| `test_p015_inject_holds_the_owner_lock_postgres.py` | 2 (0 → 2) | CLONE via `observed_factory(committed_database)` (also used by b4 entries pg, inject retries) | real ticks commit; `_advisory_owner_locks_of` is pid-filtered and stays on the tier engine |
| `test_p015_p1_money_replay_postgres.py` | 7 (unchanged) | existing CLONE; `_cleanup` → `_forget_the_route_cache` (the process route cache is the only state that outlives a clone) | purge was redundant |
| `test_p015_t1523_in_progress_and_insert_race_postgres.py` | 2 (0 → 2) | CLONE, module | two sessions race one insert |
| `test_p015_t1523_restart_after_commit_postgres.py` | 1 (0 → 1) | CLONE, module; both child processes get `committed_database.url` | the test is two processes on one database |
| `test_p015_t1524_equivalent_deletion_keeps_obligations_postgres.py` | 3 (unchanged) | existing CLONE; cleanup deleted | purge was redundant |
| `test_p015_t1525_classification_..._postgres.py` | 3 (0 → 3) | CLONE via `serializable_factory(committed_database)` | a genuine 40001 needs two connections; autouse checker removed |
| `test_p015_t1525_control_postgres.py` | 4 (0 → 4) | CLONE via `serializable_factory(committed_database)`; `_forget_the_route_cache` | real tick/payment commits |
| `test_p015_t1526_nan_amount_..._postgres.py` | 4 (0 → 4) | CLONE via `factory(committed_database)` | "committed and read back on another session" is the point; the test-d trust-line DELETE went too |
| `test_p015_t1551_..._postgres.py` | 1 (0 → 1) | CLONE, module | clearing needs an engine-bound session |
| `test_p1_clearing_run_perimeter_postgres.py` | 3 (0 → 3) | CLONE via `engine_bound_sessions(committed_database)` | interlock path needs an engine-bound session |
| `test_payment_commit_advisory_locks_postgres.py` | 6 (0 → 6) | CLONE, module; `_forget_the_journal_history` (journal DELETE + `VACUUM` on the tier engine) removed | the shared-statistics hazard it undid cannot exist on a per-test clone |
| `test_payment_engine_audit_conflict_postgres.py`, `..._uow_retry_...`, `..._idempotency_...`, `..._inverse_multisegment_...` | 1, 2, 1, 2 (0 → all) | CLONE, module | competing sessions commit; uow-retry test 1's cleanup outside `finally` is gone with the rest |
| `test_payment_staged_multicall_postgres.py` | 3 (0 → 1) | CLONE opt-in for the seeding test | the two owner-lock tests take advisory locks on random ids and commit nothing |
| `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` | 7 (0 → 7) | CLONE, module; `_drop_triangle` deleted | payments/clearings commit through the factory |
| `tests/unit/test_p015_step5c_reaction_and_hold.py` | 15 (2 → 15) | CLONE opt-in on the 13 non-`MODE_B` tests; `_drop_cycle` deleted; statement recorder on `committed_database.engine` | reaction/hold commits; the 2 `MODE_B` tests keep their own clone |
| *importers of the deleted helpers* | | | |
| `tests/unit/test_p015_step5a_reconciliation.py` | 17 (1 → 17) | CLONE, module; `clone_without_the_arithmetic_check` alters the same clone | used `_drop_triangle` |
| `tests/unit/test_p015_step5b_criterion_b.py` | 23 (5 → 23) | CLONE opt-in on 12 non-`MODE_B` tests; recorder on `committed_database.engine` | used `_drop_triangle` |
| `test_p015_step5a_reconciliation_postgres.py`, `test_p015_step5b_criterion_b_postgres.py` | 5, 22 (0 → 4, 21) | `factory(tier_on_a_clone)`; the race engines over `committed_database.url` (explicit `url` argument to `_serializable_sessions`); construction-path tests unchanged | used `_drop_triangle`/p1 `_cleanup`; `_advisory_waiter_exists` reads `current_database()` through `TestingSessionLocal`, so it must be rebound too (measured: without it the premise assert failed) |
| `test_p015_step5c_hold_races_postgres.py`, `test_p015_t1544_operator_stop_races_postgres.py` | 9, 8 (unchanged) | existing CLONE; interlock/p1 cleanups deleted, `_forget_the_route_cache` kept | redundant on the clone |
| `test_p015_inject_retries_a_serialization_failure_postgres.py` | 1 (0 → 1) | CLONE through `observed_factory` | used inject `_cleanup` |

Clone counts measured by a fixture-setup counter over these 35 modules (245 tests): **45 → 213 clones, +168**.

**Removed:** the three autouse "every seeded row is gone" checkers (`test_p015_b4_entries_and_money_postgres.py`, `test_p015_b4_wrong_writer_..._postgres.py`, `test_p015_t1525_classification_..._postgres.py`) — fixtures, not tests, so the collected count is unchanged; the helpers `_drop_triangle` (unit wrong-writer), `_drop_cycle` (step5c), the `_cleanup`s of inject-holds, p1 money replay (now `_forget_the_route_cache`), t1524, t1525 ×2, t1526, perimeter, b4 entries pg (with `_SEEDED`), wrong-writer pg (with `_SEEDED`), `_cleanup_interlock_case`, `_cleanup_seed` ×3, `Stand.cleanup` (t1523), `_forget_the_journal_history`, and every inline cleanup block.

**Not converted, on purpose — B1 owns them:** `purge_test_ledger` stays for its one caller `p015_b4_support.drop_world`, used only by the listener-mechanism modules B1 deletes or rewrites (`test_p015_b4_write_guard.py`, `test_p015_b4_transaction_contract{,_postgres}.py`, `test_p015_b4_entries_and_money.py`, `test_p015_b4_r4_...`); `p015_b4a_stand.Stand.purge` (listener-mechanism files); `test_p015_t1533_..._postgres.py` raw `DELETE FROM debts` (its setup needs the corruption helper first, finding F5); the mid-test `DELETE FROM debts` in `test_simulator_real_snapshot_db_enrichment.py` (not disposal).

**Timing (local, Windows, PostgreSQL 16.9; the full tier's wall time moves ±90 s between identical runs here, so only same-conditions comparisons are attributable):**

| measurement | before (`df1e849`) | after (`48aec47`) | delta |
|---|---:|---:|---:|
| the 35 modules alone, back to back, same selectors | 166.8 s | 257.4 s | **+90.6 s** (setup +62.0, call +15.5, teardown +13.5) |
| full backend tier, pytest time | 1373.9 s; 1461.3 s (two runs) | 1672.2 s (see the final gate for a second sample) | noise-dominated: the untouched modules' summed durations differed by +103.5 s between the two identical base runs |
| raw `CREATE DATABASE … TEMPLATE` / `DROP DATABASE` of the 9.5 MB mode-B template, 10 runs | | | 0.240 s / 0.071 s median |

Per new clone locally ≈ 0.54 s (fixture overhead included). CI history: ≈ 0.13 s per clone (`specs/017-postgres-only-engine/spec.md:178`), so +168 clones projects to roughly +22 s on CI before cold-cache effects — **against a measured CI margin of 31–48 s** (`spec 017:167`). Not measured on CI. Levers not taken here (each changes shared test infrastructure and would itself need review): one maintenance connection per clone instead of two (`_mode_b_template` calls `database_exists` every time), and fewer round trips in `cloned_database`.

### Direct callers of `purge_test_ledger` (28 test modules + 1 helper)

Legend: TSL = `tests.conftest.TestingSessionLocal` on tier DB; "tier" = shared tier DB (leftovers leak). All purge sites are in `finally` or fixture teardown unless flagged.

| path | lines | DB obtained | commits via several conns? | purge site | stage-B | reason |
|---|---|---|---|---|---|---|
| tests/integration/test_clearing_commit_replay_postgres.py | 905 | TSL (:75,:339) + tier `engine` SERIALIZABLE bind (:180,:462) — tier | yes (setup/owner/observer/verify sessions) | :281, :549, :869 finally | CLONE | needs `engine` rebinding too (tier_on_a_clone doesn't) — use `committed_database.engine`. Mandatory selector |
| tests/integration/test_clearing_payment_prepare_interlock_postgres.py | 1102 | TSL; own single-conn engine from `TEST_DATABASE_URL` (:682-690); foreign engine on `postgres` DB (:162, no purge) | yes | `_cleanup_interlock_case` :313, called in finally :510,:660,:709,:819,:916,:1042,:1102 | CLONE | one-conn engine → clone URL; `_no_advisory_lock_is_held` (:116-140) reads current_database() — fine on clone. :713 test stays mode A |
| tests/integration/test_clearing_skip_releases_locks_postgres.py | 499 | TSL — tier | yes | :205, :462 finally | CLONE | tier_on_a_clone as-is |
| tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py | 433 | TSL — tier | yes (clearing/payment/observer :176-178) | :397 finally | CLONE | tier_on_a_clone as-is. Mandatory selector |
| tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py | 500 | TSL — tier | yes | :269 finally (test 2 :475-493 deletes no debts) | CLONE | as-is |
| tests/integration/test_p012_money_form_and_detector_reach_postgres.py | 790 | 10 tests mode A `db_session`; 1 test (:633) TSL — tier | yes, only :633 (`setup` :661, `worker` :711) | :767 finally | CLONE (that one test: `@MODE_B` + `sessionmaker_of`) | mode-A tests need nothing; clearing refuses connection-bound session so :633 can't be ROLLBACK |
| tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py | 578 | TSL already on clone (tier_on_a_clone :93) | yes | `scenario` fixture teardown :198 | CLONE (existing) | purge already redundant — delete it |
| tests/integration/test_p012_rt2_precision_1_amount_is_erased_on_the_wire_postgres.py | 498 | same (:82) | yes | `scenario_factory` teardown :198 | CLONE (existing) | delete purge |
| tests/integration/test_p015_b4_entries_and_money_postgres.py | 2774 | own SERIALIZABLE engine from `TEST_DATABASE_URL` (:167-193), observer engine pool 1 (:204-231) — tier | yes | `_cleanup` :275 (+`drop_world` :299), called in finally (13 sites :587…:2666) | CLONE — or n/a if deleted by manifest (b4 journal file) | engines → clone URL; autouse checker `every_seeded_row_is_gone_when_the_test_ends` :302-340 verifies purge on tier → delete with it (else vacuous) |
| tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py | 1252 | own SERIALIZABLE engine from `TEST_DATABASE_URL` (:81-105) — tier | yes | `_drop_triangle` :199, finally :745,:899,:984,:1156,:1252 | CLONE | engine → clone URL; delete autouse checker :223-262 |
| tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py | 425 | own SERIALIZABLE pool-2 engine from `TEST_DATABASE_URL` (:121-148); tier `engine` pg_locks observer (:305) | yes | `_cleanup` :193, finally :364,:425 | CLONE | engine → clone URL; observer filters by pid (cluster-wide) — ok |
| tests/integration/test_p015_p1_money_replay_postgres.py | 857 | own SERIALIZABLE engine on `committed_database.url` (:103-126) + tier_on_a_clone | yes | `_cleanup` :185, finally :633,:718,:766,:811,:857 | CLONE (existing) | delete purge |
| tests/integration/test_p015_t1523_in_progress_and_insert_race_postgres.py | 616 | TSL — tier | yes | stand `cleanup` :199, finally :360,:609 | CLONE | as-is |
| tests/integration/test_p015_t1523_restart_after_commit_postgres.py | 314 | TSL + **child processes** given `database_url=TEST_DATABASE_URL` (:235,:265) — tier | yes (incl. other processes) | :301 finally | CLONE | pass `committed_database.url` to the children; clone drop terminates stragglers |
| tests/integration/test_p015_t1524_equivalent_deletion_keeps_obligations_postgres.py | 178 | TSL on clone (tier_on_a_clone :45) | yes | `_cleanup` :92, finally :132,:162,:178 | CLONE (existing) | delete purge |
| tests/integration/test_p015_t1525_classification_reads_deliberate_wrapping_only_postgres.py | 383 | own SERIALIZABLE engine from `TEST_DATABASE_URL` (:62-79) — tier | yes | `_cleanup` :140, finally :334,:355,:383 | CLONE | engine → clone URL; delete autouse checker :150-190 |
| tests/integration/test_p015_t1525_control_postgres.py | 663 | own SERIALIZABLE engine (:585-606); patches `AsyncSessionLocal` (:473) — tier | yes | `_cleanup` :125, finally :621,:635,:649,:663 | CLONE | engine → clone URL |
| tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py | 471 | own pool-4 engine from `TEST_DATABASE_URL` (:77-93) — tier | yes | `_cleanup` :146, finally :253,:311,:386,:471 | CLONE | engine → clone URL; plus raw INSERT debts REWRITE (below) |
| tests/integration/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line_postgres.py | 157 | TSL, no `db_session` (:36-38) — tier | yes | :140 finally | CLONE | as-is |
| tests/integration/test_p1_clearing_run_perimeter_postgres.py | 233 | own engine from `os.environ["TEST_DATABASE_URL"]` (:44-57) — tier | yes | `_cleanup` :128, finally :167,:196,:233 | CLONE | engine → clone URL |
| tests/integration/test_payment_commit_advisory_locks_postgres.py | 1348 | TSL + tier `engine` AUTOCOMMIT for VACUUM (:305-318) — tier | yes | `_cleanup_seed` :165, finally ×11 (:432…:1348) | CLONE | also removes the shared-statistics hazard (:292-303): `_forget_the_journal_history` DELETE+VACUUM becomes unnecessary. Filler insert = REWRITE (below) |
| tests/integration/test_payment_engine_audit_conflict_postgres.py | 245 | TSL — tier | yes (competitor :123) | :231 finally | CLONE | as-is |
| tests/integration/test_payment_engine_uow_retry_postgres.py | 471 | TSL — tier | yes | **:189 NOT in finally** (test 1, success-path only, after asserts); :451 finally | CLONE | :189 would fail the bounded helper's AST guard; also leaks on failure today |
| tests/integration/test_payment_idempotency_postgres.py | 294 | TSL — tier | yes | :262 finally | CLONE | as-is |
| tests/integration/test_payment_inverse_multisegment_postgres.py | 399 | TSL — tier | yes | `_cleanup_seed` :186, finally :399 | CLONE | as-is |
| tests/integration/test_payment_staged_multicall_postgres.py | 488 | TSL — tier | yes | `_cleanup_seed` :189, finally :362 | CLONE | as-is |
| tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py | 1375 | `TSL as factory` (:541…) — tier; `db_session` requested but unused for writes | yes | `_drop_triangle` :142, finally ×7 (:612…:1375) | CLONE | `_drop_triangle` is imported by 5 other modules (below) — decide together |
| tests/unit/test_p015_step5c_reaction_and_hold.py | 973 | `TSL`/`TSL as factory` — tier; 2 tests `@MODE_B` (:804,:890) already on clone | yes | `_drop_triangle` finally ×14; `_drop_cycle` :737,:742 called finally :796-797 (MODE_B test) | CLONE | purge in the 2 MODE_B tests already redundant |
| tests/p015_b4_support.py (helper) | 335 | caller's factory | — | `drop_world` :244 | n/a if callers deleted; else CLONE at callers | callers below |

Sub-questions:
- **Purge before assertions / in setup: none.** No purge runs to clean leftovers of a previous run. Only anomaly: `test_payment_engine_uow_retry_postgres.py:189` (after assertions but outside `finally`).
- Tier-shared DB: every row except rt1, rt2, p1_money_replay, t1524 (already on clone) and step5c's 2 MODE_B tests.
- Properties CLONE would break: none found. Needed adaptations: (a) `tests.conftest.engine` users — commit_replay :75/:339, payment_commit_advisory :305 (inject_holds :305 is harmless); (b) own engines on `TEST_DATABASE_URL` → `committed_database.url` (b4_entries pg, wrong_writer pg, inject_holds, t1525×2, t1526, perimeter, interlock :682, step5b pg); (c) subprocess URL (t1523_restart); (d) delete the three autouse "row is gone" checkers. Isolation is preserved: clone engine uses the app level (`conftest.py:405-419`), and every stand sets its own explicitly anyway.

### Indirect callers (disposal through wrappers)

| wrapper | callers | DB | stage-B |
|---|---|---|---|
| `p015_b4_support.drop_world` (:232-247) | unit `test_p015_b4_entries_and_money.py` (7), unit `test_p015_b4_transaction_contract.py` (16), unit `test_p015_b4_r4_fixture_migration_is_observably_equivalent.py` (:563), unit `test_p015_b4_write_guard.py` (17), integ `test_p015_b4_transaction_contract_postgres.py` (13; own engine `TEST_DATABASE_URL` :91-124), integ b4_entries pg :299 | TSL / own engines — tier | n/a if deleted (write_guard is listener-mechanism; others per other groups); survivors → CLONE |
| unit wrong_writer `_drop_triangle` (:123) | unit `test_p015_step5a_reconciliation.py` (17), unit `test_p015_step5b_criterion_b.py` (12), unit step5c (15), integ `test_p015_step5a_reconciliation_postgres.py` (5; factory = TSL :69-72), integ `test_p015_step5b_criterion_b_postgres.py` (4; TSL :67-72 + own SERIALIZABLE/RC engines :76,:330) | tier | CLONE |
| `p015_b4a_stand.Stand.purge` (:246-265, own raw `DELETE FROM debts`/journal) | integ b4a_journal :82, t1528 pg :71, t1530 pg :96; unit b4a_journal_mechanism :56,:1115, t1528 :81, t1530 :70, t1531 :59, t1532 :63 | stand's own engine on tier | n/a if deleted (listener-mechanism files) |
| own raw teardown `DELETE FROM debts` | `test_p015_t1533_participant_deletion_keeps_obligations_postgres.py` `_cleanup` :105-123 (finally :179,:204,:244,:267); TSL — tier | tier | CLONE (but see t1533 finding) |

### Raw DML on debts/journal for purposes other than teardown

| path:line | what | refused in B? | verdict |
|---|---|---|---|
| tests/unit/test_p015_t1533_participant_deletion_keeps_obligations.py:66-73 | setup `INSERT INTO debts` via driver, no envelope (a debt with no journal history, on purpose) | yes GE001 | REWRITE — see finding F5 |
| tests/integration/test_p015_t1533_participant_deletion_keeps_obligations_postgres.py:90-99 (+ teardown :116-120) | same, committed on tier | yes | REWRITE (F5); teardown → CLONE |
| tests/unit/test_p015_t1544_inject_refuses_a_deactivated_equivalent.py:14,:126,:146 | only asserts statement text sent | no DML | KEEP-AS-IS |
| tests/unit/test_p015_p1_money_conflict_predicate.py:151,:168 | strings inside constructed `DBAPIError`; real DML only on scratch tables `probe`/`child` in a clone (:45-58,:109) | no | KEEP-AS-IS |
| tests/unit/test_payment_engine_retry_savepoint_nocommit.py:91,:103 | fake error `statement=` strings | no | KEEP-AS-IS |
| tests/integration/test_payment_engine_uow_retry_postgres.py:397,:401 | asserts statement text; writes go through engine | no | KEEP-AS-IS (teardown → CLONE) |
| tests/integration/test_simulator_real_snapshot_db_enrichment.py:133-137 | **mid-test** `DELETE FROM debts` of one edge (MODE_B clone), then re-add in `debt_fixture_setup` :139-147 | yes GE001 | REWRITE: remove/replace the edge inside the same `debt_fixture_setup` envelope (ORM delete or amount set). Unverified risk: TEST_FIXTURE refused after a baseline if the real-mode run created one for UAH |
| tests/integration/test_p015_t1524_equivalent_deletion_keeps_obligations_postgres.py:151,:171 | `"DELETE"` = HTTP method; seed via `debt_fixture_setup` :70 | no | KEEP-AS-IS (purge → delete) |
| tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py:326-338 | non-vacuity raw `INSERT INTO debts` 7.00 committed without envelope | yes | REWRITE: run the raw INSERT inside a `debt_fixture_setup` block on the same session (context set). NaN INSERT :346-362 still hits CHECK first (row CHECK precedes AFTER trigger) — but assert the SQLSTATE/constraint stays the CHECK's |
| tests/integration/test_payment_commit_advisory_locks_postgres.py:270-279, :307-311 | filler 2000 `debt_operations` rows `state='COMPLETED'` with `flush_count`; later `DELETE FROM debt_operations` | yes (INSERT only OPEN; DELETE refused; `flush_count` dropped by 029) | REWRITE filler (INSERT OPEN + UPDATE→COMPLETED in one txn, schema_version 2, no flush_count); DELETE+VACUUM obsolete under CLONE |
| Also found (other groups likely own): step5a pg :270, step5c_hold_races pg :108,:321; step5a unit :335…:1016; step5b unit :176-215,:682-689,:904; step5c unit :150 | corruption `UPDATE/INSERT debts`/journal via driver | yes | REWRITE → spec's named corruption helper (replica) |
| b4_entries pg :1043-1060; inject_retries_a_serialization_failure pg :110-130; b4_entries unit :738-741 | competitor Core `update(Debt)` / raw UPDATE with guard stood down | yes | REWRITE: competitor inside its own envelope (debt_fixture_setup/Book), or deleted with listener files |
| step5b pg :105-120 | raw `INSERT debt_operations ... 'OPEN'` in `engine.begin()` (CHECK probes) | good-value case: deferred trigger refuses commit of OPEN | REWRITE: probe then roll back instead of commit |
| p017_t1711 seed recipe pg :452-491, :611-616 | doctoring UPDATE debts/debt_operations, DELETE debt_operation_equivalents (recipe checks) | yes | REWRITE → corruption helper (or recipe must be re-specified) |

### Counts

- Files mentioning `purge_test_ledger`: 32 (incl. definition). Files that **call** it: 29 = 28 test modules + `tests/p015_b4_support.py` (helper, counted separately). Mention-only: t1533 pg, simulator snapshot (each has its own raw `DELETE FROM debts`). **Spec's "31 modules" (`spec.md:155`) is off** — it counts the two mention-only files and the helper.
- Of the 28 test modules: **CLONE 28** (of which 4 already on a clone — rt1, rt2, p1_money_replay, t1524 — purge just deleted; p012 money form: one test only), **ROLLBACK 0**, **BOUNDED-TEARDOWN 0**. b4_entries pg / wrong_writer pg may instead be DELETE by the manifest.
- Helper `p015_b4_support.drop_world`: n/a (follows its callers).
- Tests newly needing a clone if all go CLONE: ≈93 test functions (sum over modules minus the already-cloned ones and p012's 10 mode-A tests). Clone creation time per test **not measured** here.

### Can the test role SET `session_replication_role`?

- CI: `.github/workflows/quality.yml:32-37` service `postgres:16`, `POSTGRES_USER: geo` → the official image makes `POSTGRES_USER` the bootstrap **superuser** → yes.
- Local: `docs/ru/backend/postgres-local-portable.md:75` `initdb -U geo`, `:79` "`-U geo` делает суперпользователем" → yes.
- **But** the tier explicitly supports a non-superuser role: `tests/migrated_schema.py:360-373` accepts `rolsuper OR rolcreatedb`. `session_replication_role` is superuser-context (PG15+: superuser or `GRANT SET ON PARAMETER session_replication_role`); a CREATEDB-only role gets "permission denied to set parameter". The spec states no such precondition — applies to both the bounded helper and the corruption helper (`spec.md:109`).

### Findings on feasibility / completeness of the spec's disposal plan

- F1. The fallback (bounded replica helper + AST guard) has **no customer**: every caller can take CLONE. Per §19 it should not be built; if it is, its AST guard would already reject `test_payment_engine_uow_retry_postgres.py:189`.
- F2. `tests/tier_on_a_clone.py` is incomplete for this family: it rebinds only `TestingSessionLocal`. Tier `engine` users (commit_replay :180/:462 via :75/:339; payment_commit_advisory :305), own engines on `TEST_DATABASE_URL` (9 modules above) and subprocess URL (t1523_restart :235/:265) must be moved explicitly; the shim or each module needs it. A module missed here would **seed on the clone and act on the tier** (or vice versa) — fails loudly (FK/absent rows), not silently.
- F3. Three autouse "every seeded row is gone" checkers (b4_entries pg :302, wrong_writer pg :223, t1525 classification :150) assert the purge on the tier; after CLONE they read an empty tier and pass vacuously → must be deleted with the purge (§9 anti-vacuum).
- F4. The manifest scope in `spec.md:155` ("31 callers of purge_test_ledger") misses the disposal paths that are not that function: `drop_world` callers (6 modules), `_drop_triangle` importers (5), `p015_b4a_stand.purge` (8 users), t1533 pg own `DELETE FROM debts` (:116-120), snapshot mid-test DELETE (:133-137), payment_commit_advisory `DELETE FROM debt_operations` (:307-311). All are refused by B the same way.
- F5. t1533 (unit :66-73, pg :90-99, anti-vacuity test pg :208-245) needs a debt **without** journal history. After B only the replica corruption helper can produce one, and `spec.md:109`/`:122` restrict replica to the T1508 corruption forms. Either the exception list is extended by name, or t1533 is re-specified (with a Book-written debt the journal's participant RESTRICT (`C17`) refuses first and the test's premise is gone). Owner-level/spec gap.
- F6. Spec anchors for `purge_test_ledger` (`:233-325`, `:302`, `:323`) are stale on 3a038d3 → `:246-337`, `:313-317`, `:336`.
- F7. The replica privilege precondition (above) is undeclared; the tier's own provisioning accepts a role that would fail it.
