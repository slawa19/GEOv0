# 019 / T1901 — манифест по файлам и карта выживания по ассертам

- **Date:** 2026-09-25
- **Tree:** код = `main` `2aee461` (ветка `claude/019-spec-rework`, `2bb62e4`, меняет только `specs/`). Всё ниже — чтение кода; ни один тест не запускался, ни одно число прогона здесь не утверждается.
- **Status authority:** документ — вход стадий 2–5, а не их evidence. Каждая строка «TO WRITE» становится тестом названной стадии; «SURVIVES» и «REWRITE IN PLACE» — утверждения, которые внешнее ревью стадии (`T1910`, пункт 5) проверяет по коду. Правило спеки: **ни одного удаления теста без строки манифеста**.
- **Как собрано:** пять независимых read-only проходов по непересекающимся группам (G1 — ядро движка; G2 — состояния, recovery, идемпотентность и повтор денежной фазы, помощники; G3 — локи, резервы, interlock, клиринг, инжект; G4a — проверочные тесты 015 на Postgres; G4b — unit-тесты 015 и стоп `T1544`), затем сведение и перепроверка несущих фактов оркестратором: покрытие всех 66 файлов границы, замыкание импортов, счёт строк карт одним скриптом, выборочно открыты `tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py:51`, `test_clearing_payment_prepare_interlock_postgres.py:276-291`, `test_payment_prepare_error_taxonomy.py:198-208`, `test_clearing_commit_replay_postgres.py:468`, `test_p015_p1_money_replay_postgres.py:593`, `test_the_tier_refuses_a_database_that_is_not_postgres.py:28`, `app/api/v1/admin.py:246-257`, `:1159-1166`, `app/core/clearing/service.py:99-102`, `:1495`, `app/core/payments/engine.py:1569-1583`. Таблицы разделов 4–6 оставлены на английском, в котором их собрали проходы: это перечни `path:line`, а не проза.

## 1. Граница поиска и как её воспроизвести

Граница — объединение пяти множеств: широкий шаблон спеки, узкий шаблон «состояния/восстановление», **замыкание импортов** (тест, который сам шаблона не содержит, но импортирует модуль, достающий до удаляемого кода, после удаления перестаёт собираться), потребители interlock вне шаблонов и сеющие нетерминальный `PAYMENT` (ограда `030`).

```powershell
# 1) широкий шаблон спеки — 66 файлов / 33 108 строк (и на 2aee461, и на 2bb62e4)
git grep -l -E 'PaymentEngine|PrepareLock|prepare_locks|PaymentRecovery|ClearingCommittedAfterCancellation|payment_clearing_interlock' -- tests
# 2) узкий шаблон «состояния/восстановление» — 43 файла / 24 941 строка; 9 файлов вне широкого
git grep -l -E 'PREPARED|PrepareLock|prepare_locks|recovery|stuck' -- tests
# 3) замыкание импортов (скрипт ниже) — 61 модуль / 29 844 строки: 57 прямых, 4 транзитивных; 7 вне широкого
.\.venv\Scripts\python.exe t1901_closure.py .
# 4) ограда CHECK 030 бьёт и по подготовке: литерал нетерминального состояния — 30 файлов; вне 1)–3) один
git grep -l -E "state\s*[=:]\s*['\"](NEW|PREPARED|WAITING|ROUTED|PREPARE_IN_PROGRESS|PROPOSED)['\"]" -- tests
# счёт строк любого списка
git grep -l -E '<шаблон>' -- tests | ForEach-Object { (Get-Content $_).Count } | Measure-Object -Sum
```

**Узкий шаблон консультации не воспроизведён.** Консультация назвала «43 файла / 27 585 строк», сам шаблон не записала. Закреплён шаблон 2) — 43 / 24 941. Проверено около дюжины вариантов на `2aee461`: с `-i` — 46 / 29 233; `recover` вместо `recovery` — 50 / 30 787; плюс `TTL` — 49 / 27 896; плюс `abort` — 56 / 30 939; ни один не дал 43 / 27 585. Число консультации считать непроверяемым; манифест от него не зависит, потому что покрывает объединение всех пяти множеств.

Скрипт замыкания (сохранить как `t1901_closure.py` вне дерева; не коммитится — после стадии 5 его цели нет). Кроме `import`/`from … import` он ловит строковые цели `monkeypatch.setattr("app.core.payments.engine.X", …)`. На Windows вывод содержит `\r` — срезать перед `comm`.

```python
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
TARGETS = {"app.core.payments.engine", "app.core.recovery", "app.db.models.prepare_lock"}
PARENT_NAMES = {("app.core.payments", "engine"), ("app.core", "recovery"), ("app.db.models", "prepare_lock"),
                ("app.db.models", "PrepareLock")}
mods = {}
for p in sorted((root / "tests").rglob("*.py")):
    name = ".".join(p.relative_to(root).with_suffix("").parts).removesuffix(".__init__")
    mods[name] = p
def imports(p):
    out = set()
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module)
            for a in n.names:
                out.add(f"{n.module}.{a.name}")
                if (n.module, a.name) in PARENT_NAMES:
                    out.add("app.db.models.prepare_lock" if a.name == "PrepareLock" else f"{n.module}.{a.name}")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and any(n.value.startswith(t) for t in TARGETS):
            out.add(next(t for t in TARGETS if n.value.startswith(t)))
    return out
graph = {m: imports(p) for m, p in mods.items()}
direct = {m for m, im in graph.items() if im & TARGETS}
reach, changed = set(direct), True
while changed:
    changed = False
    for m, im in graph.items():
        if m not in reach and im & reach:
            reach.add(m); changed = True
for m in sorted(reach):
    p = mods[m]; n = sum(1 for _ in p.open(encoding="utf-8"))
    print(f"{n}\t{'direct' if m in direct else 'transitive'}\t{p.relative_to(root).as_posix()}")
```

Что каждое множество добавляет к широкому:

- **Замыкание (7):** `test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` (`import app.core.payments.engine as engine_module`, `:78`), `test_background_task_supervision.py` (`import app.core.recovery`, `:11`), `test_payment_staged_post_commit.py` (строковая цель `app.core.payments.engine.PAYMENT_EVENTS_TOTAL`, `:122`); транзитивно через помощники — `test_p015_f01512_…`, `test_p015_inject_retries_…`, `test_p018_mixed_inject_event_…` (через `test_p015_inject_holds_the_owner_lock_postgres.py`, у которого `PaymentEngine` импортирован на уровне модуля, `:51`) и `test_p015_step5a_reconciliation_postgres.py` (через `_pay` из `tests/unit/test_p015_step5a_reconciliation.py`). Двенадцать файлов широкого шаблона в замыкание не входят: они называют имена в докстрингах или строках, не импортируя их.
- **Узкий шаблон (9):** `tests/conftest.py`, `test_p1_reconcile_after_failed_rollback_postgres.py`, `test_simulator_sse_replay_410.py`, `test_admin_incidents_list.py`, `test_admin_liquidity_summary.py`, `test_admin_whoami_and_extras.py`, `test_background_task_supervision.py`, `test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py`, `test_simulator_adaptive_clearing_effectiveness_synthetic.py`.
- **Потребители interlock вне шаблонов (4, найдены проходом G3 поиском `_interlock`):** `test_p1_clearing_run_perimeter_postgres.py`, `test_p1_commit_then_refresh_postgres.py`, `test_clearing_additional_cases.py`, `test_zero_debt_policy.py`.
- **Ограда `030` (1):** `tests/integration/test_p018_b_book_transaction_contract_postgres.py:517-518` сеет `PAYMENT` в `NEW`. Остальные 29 файлов команды 4) уже в манифесте; сеющие `CLEARING` в `NEW` ограду не задевают (`09:120`).

Итого манифест покрывает **86 файлов / 39 719 строк**: 66 / 33 108 границы спеки и 20 / 6 611 расширения.

## 2. Числа

**По файлам.** Решение — судьба файла целиком; стадия — та, где исчезает реализация, от которой файл зависит (правило спеки «Стадии»). Файл, меняющийся в нескольких стадиях, считается в каждой.

| Решение | Граница спеки (66) | | Расширение (20) | | Всего | |
|---|---:|---:|---:|---:|---:|---:|
| | файлов | строк | файлов | строк | файлов | строк |
| **DROP** | 12 | 3 570 | 0 | 0 | 12 | 3 570 |
| **REWRITE** (с картой по ассертам) | 38 | 22 770 | 6 | 1 539 | 44 | 24 309 |
| **REWRITE (setup-only)** — ассерты не трогаются, меняется подготовка/импорт | 7 | 2 049 | 3 | 1 326 | 10 | 3 375 |
| **KEEP** | 9 | 4 719 | 11 | 3 746 | 20 | 8 465 |
| **Всего** | 66 | 33 108 | 20 | 6 611 | 86 | 39 719 |

| Стадия | Граница: файлов / строк | Расширение: файлов / строк | Всего |
|---|---:|---:|---:|
| 2 | 14 / 8 440 | 1 / 549 | 15 / 8 989 |
| 3 | 17 / 8 326 | 1 / 432 | 18 / 8 758 |
| 4 | 47 / 25 905 | 7 / 2 119 | 54 / 28 024 |
| 5 | 26 / 15 032 | 1 / 197 | 27 / 15 229 |

DROP по стадиям: **стадия 4 — 10 файлов / 3 376 строк** (`test_payments_2pc.py`, `test_payment_engine_uow_retry_postgres.py`, `test_payment_engine_retry_savepoint_nocommit.py`, `test_payment_engine_advisory_lock_key.py`, `test_payment_commit_advisory_locks_postgres.py`, `test_payment_pair_advisory_locks_postgres.py`, `test_payment_engine_audit_conflict_postgres.py`, `test_payment_abort_has_error_code.py`, `test_p017_uow_retry_after_a_real_40001_postgres.py`, `test_recovery_cleanup.py`); **стадия 5 — 2 / 194** (`test_prepare_locks_tx_id_fk_postgres.py`, `test_clearing_prepare_lock_conflict.py`). Удаляется 3 570 строк из 33 108 — основная масса программы, как и в 018, переписывание, а не удаление. Внутри REWRITE-файлов целиком удаляются ещё отдельные тесты (например, пять из десяти в `test_clearing_payment_prepare_interlock_postgres.py`, TTL-ветка `test_p015_step5c_hold_races_postgres.py::test_step5c_p_an_expired_payment_in_a_held_equivalent_is_aborted_as_expired`, движковый сценарий `test_p015_t1525_control_postgres.py`) — каждый с картой в разделе 5.

Стадия «2» в сводной таблице — жёсткая: импорт или константа, которые стадия 2 переносит в `money_boundary.py`. Пометка `2c` в разделе 4 — условная: цель патча — метод `PaymentEngine`, который стадия 2 может и не перестать вызывать; жёсткий срок для неё — стадия 4 (уже учтена).

**Карта по ассертам** (раздел 5; строки таблиц `file:line`, повторяющиеся ассерты одного теста сгруппированы проходами; счёт — скрипт по последней колонке, судьба — первое встреченное ключевое слово):

| Судьба | Строк | из них ⚑ |
|---|---:|---:|
| UNCHANGED — текст ассерта не меняется ни в одной стадии; правка подготовки теста, если есть, названа в разделе 4 (G2 пишет «SURVIVES: in place», G3 «KEEP», G4b «unchanged») | 116 | 79 |
| REWRITE IN PLACE (stage N) — ассерт остаётся, меняется подготовка/ожидание | 253 | 123 |
| SURVIVES — тот же эффект уже держит существующий узел, переживающий все стадии | 14 | 11 |
| TO WRITE (stage N) — новый тест или новое ожидание | 45 | 29 |
| DROP — проверяет только удаляемый механизм; удаляемый контракт назван | 121 | 6 |
| **Всего** | **549** | **248** |

Ещё около десятка TO WRITE сложены внутрь строк REWRITE/DROP как вторая половина ячейки (в основном G2: осушение `030` — отказ и успех, аудит повторного `ABORTED`, сохранение записанной ошибки admin abort, `409` на расхождение идентичности в API, «исчерпание не пишет `ABORTED`», базовая стоимость всего пути, новая точка инъекции таймаута для ячейки 2). Скрипт считает строку по первой судьбе.

⚑ — ассерт обязателен: его называет спека 019 (Verification plan §2/§3, в том числе `money_replay :530/:703/:751/:796`, `t1523 :252`, матрица `T1523`, итоговые долги lost-update, `T1522`, `T1544`, `T1546`, `T1548`, узкое место), он воспроизводит закрытие `T15xx`/`F-0xx` прежней программы или это контракт 018. **Из 248 ⚑: 213 выживают без нового теста** (79 без изменений, 123 по месту, 11 SURVIVES), **29 требуют теста** в названной стадии, **6 удаляются** — каждая с названным удаляемым контрактом и узлом-наследником:

1. `test_payment_commit_advisory_locks_postgres.py:566-587`, `:690-705`, `:716-739` (⚑ `T1529`, три строки) — гонка идентичности конверта **в фазе commit движка**: после стадии 3 первым сталкивается `transactions.tx_id`, эта гонка недостижима. Денежные, идентификационные и аудитные половины тех же тестов (`:636-643`, `:742-750`, `:753-760`) — TO WRITE стадии 3 (гонка идентичности `tx_id`, с историей журнала — раздел 3, стадия 3).
2. `test_payment_commit_advisory_locks_postgres.py:1274-1277` (⚑ Q2) — admin/recovery-abort **живого** платежа: после стадии 4 живых платежей нет; совместимость Q2 — SURVIVES в `test_admin_abort_tx.py`.
3. `test_clearing_payment_prepare_interlock_postgres.py:972-974` (⚑ клиринг под отменой) — путь отмены **в освобождении interlock** (место подъёма `clearing/service.py:1676` исчезает на стадии 5). Эффект держат `test_clearing_commit_replay_postgres.py[cancellation]` (`:700-702`, `:722-723`, `:773-779`; место подъёма `:2177`) и unit-потребитель `test_real_clearing_engine_partial_failure.py[committed_cancel]`.
4. `test_p015_inject_holds_the_owner_lock_postgres.py:339` (⚑ 015, фаза B, шаг 3) — «каждая запись долга инжекта под owner-локом»: удаляется, **только если** `T1908` не вернёт лок эквивалента; наследник — TO WRITE стадии 5 (расписания инжект/платёж и инжект/инжект на `SERIALIZABLE`, отказ неподходящей изоляции у инжекта).

Счёт воспроизводится скриптом по таблицам раздела 5 (`t1901_count.py` вне дерева: строки таблиц с заголовком `file:line`; судьба — самое раннее по позиции из `SURVIVES: in place`/`unchanged`/`KEEP` (= UNCHANGED), `DROP`, `TO WRITE`, `SURVIVES`, `REWRITE IN PLACE` в последней ячейке после снятия ⚑ и его источника, при равной позиции — в этом порядке; ⚑ — наличие знака в строке). «REWRITE IN PLACE — unchanged assert» у G4b считается REWRITE IN PLACE: ассерт тот же, подготовка теста меняется. Проходы вели разный словарь для «без изменений»; нормализация — первая строка таблицы выше. Группа G2 в сводке своего прохода называет 139 строк, скрипт насчитывает 126; расхождение — разбиение параметризованных строк, в таблицах их 126.

## 3. Что стадии обязаны сделать сверх спеки

Здесь всё, что противоречит плану стадий или отсутствует в нём. Пункты 1–9 — противоречия или пробелы, которые **требуют решения до кода** своей стадии; остальное — перечни потребителей. Номер находки прохода — в скобках (`G1 F1`, `G3 C2` …; тексты — раздел 6).

### Противоречия плану стадий

1. **Тесты, которые спека относит к стадии 5, ломаются на стадии 4** (`G3 C2`, `G3 C3`, `G1 F11`). Спека (§3 Verification plan) переносит «зависящие от owner-лока, interlock и резервов» на стадию 5, но эти файлы зависят и от движка или нетерминальных `PAYMENT`: lost-update и узкое место патчат `PaymentEngine._acquire_equivalent_owner_locks` (`test_concurrent_clearing_payment_lost_update_postgres.py:216`, `:241-245`; `test_concurrent_prepare_routes_bottleneck_postgres.py:79`, `:107-111`); второй тест узкого места зовёт `prepare_routes` и сеет `NEW` (`:327-399`) — DROP на стадии 4, эффект держит первый тест (`:194-197`, `:236-237`); `test_clearing_skip_releases_locks_postgres.py[locked]` сеет `PREPARED` + `PrepareLock` (`:119-148`); `test_clearing_payment_prepare_interlock_postgres.py` зовёт `PaymentEngine.prepare` (`:366`, `:513`), а его помощник `_seed_interlock_case` вставляет `PAYMENT` в `NEW` (`:276-291`) и импортируется двумя селекторами §3 (`test_p015_step5c_hold_races_postgres.py:58-62`, `test_p015_t1544_operator_stop_races_postgres.py:55-59`); `test_p015_t1543_frozen_line_is_not_limit_zero.py` сеет `PREPARED` и зовёт `engine.commit` (`:135-198`). Решение: смена цели патча и подготовки — на стадии 2 (если та перестаёт звать метод движка) или 4 крайний срок; настоящее переписывание механизма — на стадии 5. Из `_seed_interlock_case` строка `NEW` удаляется на стадии 4 (оба селектора `payment_tx_id` не читают).
2. **Pair-, tx- и session-owner-локи живут в `engine.py` между стадиями 4 и 5 без дома** (`G1 F1`). `_acquire_segment_advisory_lock_keys`, `_acquire_tx_advisory_lock` (`engine.py:237`), `acquire_session_equivalent_owner_lock`/`release_session_equivalent_owner_lock` (`:207`, `:226`; потребитель — `clearing/service.py:167`) удаляются вместе с `engine.py` на стадии 4, а спека снимает локи на стадии 5 «только после принудительной изоляции». Спека обязана выбрать: стадия 4 переносит их (в `money_boundary.py`), либо снятие pair/tx-локов раньше evidence `T1907`/`T1908` записывается как отдельное решение. От выбора зависит стадия 4 или 5 для `test_payment_engine_advisory_lock_key.py`, `test_payment_pair_advisory_locks_postgres.py`, части `test_payment_engine_advisory_locks_execute.py` и предпосылки ожидания `test_payment_inverse_multisegment_postgres.py:282-288`.
3. **Список §3 «обязаны остаться зелёными» содержит тесты, которые спека сама переписывает** (`G1 F5`, `G2 C8`, `G3 C1`, `G4a C6`): `test_payment_idempotency_postgres.py` («in progress» `:181`, `READ COMMITTED` `:119-125`, патч `service.engine.prepare` `:149`); `test_payment_inverse_multisegment_postgres.py` (`PREPARED` + `PrepareLock`); `test_p015_t1523_in_progress_and_insert_race_postgres.py` (ячейки 3 **и 5** — спека переформулирует только 3; предпосылка ячейки 5 — закоммиченный `NEW` победителя, `:466-475`, `:546`); `test_p015_p1_money_replay_postgres.py` (`_prepare_locks == 0`, `:593`, `:671`, `:743` — стадия 5); `test_p015_t1523_replay_after_a_hold_or_an_abort.py` (инъекция отказа через патч `PaymentEngine.prepare` и `PREPARE_TIMEOUT_SECONDS`, `:268-278`); `test_p015_step5c_hold_races_postgres.py` и `test_p015_step5b_criterion_b_postgres.py` (стадии 2–5); `test_p015_t1544_operator_stop_races_postgres.py`; `test_clearing_commit_replay_postgres.py` — **вердикт инвертируется** (п. 4). Читать §3 как «зелёный после переписывания по месту в стадии-владельце, ассерты по карте».
4. **Владелец повторов клиринга (стадия 5) инвертирует вердикт селектора §3** (`G3 C1`). `test_clearing_commit_replay_postgres.py::test_serializable_conflict_without_committed_occurrence_stays_failure_postgres` (`:293`) сегодня ждёт `E010` и неизменных долгов (`:468`, `:499-505`), потому что обработчик `clearing/service.py:2183-2194` лишь сверяет закоммиченное. С повтором на свежей сессии попытка перечитает 101 и очистит 30. Стадия 5 называет заменяющий контракт (исчерпание дедлайна → повторяемый отказ без строки клиринга) и пишет его тест; варианты с обрывом соединения (`:683-685`) проверяют привязку `AsyncConnection`, существующую только ради пиннутого соединения interlock.
5. **Таблица `FORK-4` не покрывает классы, которые тесты фиксируют сегодня** (`G1 F4`, `G4a C3`, `G4a C4`, `G4b F2`, `G4b F3`) — решает характеризация `T1902` до стадии 3:
   - внутренняя ошибка после `NEW` (`E010`; taxonomy `:482-505`, `:559-568`, `:1575-1584`; `IntegrityViolation` из `check_payment_delta` — `test_p015_t1525_control_postgres.py:295`; `23514` CHECK конверта — `test_p015_step5b_criterion_b_postgres.py:539`) — сегодня `ABORTED`;
   - отмена (`E007`, «Payment cancelled»; taxonomy `:1046-1051`, `:1108-1113`) — сегодня `ABORTED`; при одном коммите отмена **после** возврата `COMMIT` — это `COMMITTED` (taxonomy `[insert]` `:1079-1086`);
   - **стоп/hold как «окончательный отказ»** держится только при явной записи: сегодня отказ ловится в commit движка над долговечным `PREPARED` и записывается `ABORTED` (`test_p015_t1544_operator_stop_races_postgres.py:245`, `test_p015_t1544_operator_stop_refuses_money.py:300-303`, `test_p015_step5c_reaction_and_hold.py:681`, `test_p015_step5c_hold_races_postgres.py:230`). На стадии 3 повтор `pay()` после `40001` приходит в best-effort предпроверку до `NEW` (`service.py:720-732`) — строки нет; в порядке стадии 4 (шаг 2 до шага 4) чтение стопа тоже раньше вставки. Строка Q1 таблицы спеки («стоп/hold → `ABORTED`») без явного решения не выполняется;
   - staged-отказ стопа из предпроверки — исключение, структурный `PaymentResult(ABORTED)` или долговечная строка (`test_p015_t1544_operator_stop_through_the_tick_sqlite.py:311-336`, races `:480-486`; `test_p015_step5c_hold_through_the_tick_sqlite.py:177` ждёт исключение).
6. **Ячейка 8 `T1523` молча теряет предпосылку на стадии 3** (`G2 C7`). Дочерний процесс умирает внутри `PaymentEngine.commit` (`tests/integration/t1523_restart_child.py:59-70`); при `commit=False` внутри внешней транзакции `pay()` смерть не оставляет ничего долговечного, и предпосылка `test_p015_t1523_restart_after_commit_postgres.py:256-263` падает. Точка смерти переносится **после** внешнего `COMMIT` в той же стадии.
7. **Реальное-время отсечения `T1544` без owner-лока** (`G4b F1`, стадия 5). `T1544` обещает «после ответа `PATCH` деньги не коммитятся» — внешняя согласованность, а не сериализуемость. Клиринг читает стоп без блокировки строки (`clearing/service.py:100-101`, `row_lock=False`) и опирается на owner-лок `PATCH`. Без него `SERIALIZABLE` допускает порядок «клиринг прочитал активный → `PATCH` вернул 200 → клиринг закоммитил»; `test_p015_t1544_operator_stop_races_postgres.py:316` (порядок `["clearing","patch"]`) покраснеет. Стадия 5 обязана дать клирингу `FOR SHARE` (как у платежа и инжекта) или сохранить лок; та же проверка — для реакции и снятия hold сверки (`T1546`).
8. **Независимый захват объявленного потока для критерия (б) исчезает на стадии 4** (`G4a C8`, `G4b F4-F6`). C14 (`test_p015_b4_entries_and_money_postgres.py:1462-1467`, `:1513`), C5-P и C6-P (`test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py:603`, `:617`, `:702`, `:733`, `:825`, `:840`, `:863`) и помощник `_intent_flows` (`tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py:440-467`) берут объявление из `prepare_locks.effects` между prepare и commit. Без нового независимого захвата записанное намерение сравнивается само с собой — механизм программы против собственного дефекта, пункт 6 ревью `T1910`. Там же стадии 4 нужны: точка применения потока ниже вычисления намерения (замена `PaymentEngine._apply_flow`, `engine.py:1714`, — шов C6(i) в трёх модулях), дом для чтения предсостояния (`_read_payment_prestate`, `engine.py:451`), вход исполнения с явным маршрутом (сегодня `service.py:409` принимает только `constraints`; C6(i) требует A→B→C при существующей линии C→A).
9. **Алгоритм стадии 4 теряет три эффекта `PaymentEngine.commit`** (`G2 C1`, `G4b F6`): `check_trust_limits` и `check_debt_symmetry` (`engine.py:1569-1583`), строку `IntegrityAuditLog` на эквивалент (FIX-014, `:1601-1662`) и метрику `PAYMENT_EVENTS_TOTAL{commit,success}`. Их проверяют `test_invariants.py:203-207`, `:539-540` и ⚑ C6 (`audit == [True]`: wrong_writer `:867`, step5a `:989`, step5b `:320`, `:974`). Стадия 4 переносит их или записывает снятие датой. Уцелевшего узла «одна строка аудита на закоммиченный платёж» вне переписываемых файлов нет (`G1` итог, п. 9) — TO WRITE.

### Стадия 2 обязана также

- **Список переносимых примитивов неполон** (`G1 F2`, `G4a C1-C2`): кроме названных — `_DELTA_DRIFT_TOLERANCE` (`engine.py:89`), namespace и ключ owner-лока (`:94`, `:158`), `acquire_staged_equivalent_owner_locks` (`:184`), session-owner (`:207`, `:226`), `EQUIVALENT_INACTIVE_REASON`/`EQUIVALENT_INTEGRITY_HOLD_REASON` (`:378`, `:383` — из них собран `MONEY_STOP_REASONS`), `inactive_equivalent_conflict`/`integrity_hold_conflict` (`:390`, `:397`), бюджет лока (`:126`, `:175`). Тесты импортируют их под этими именами — нужен стабильный публичный адрес в `money_boundary.py`.
- **Owner surface пропускает потребителей в `app/`** (`G1 F3`, `G3 C11`, `G4b F8`): `app/core/clearing/service.py:26` (импорт), `:167`; `app/core/simulator/real_runner_impl.py:737`, `:759`; `app/core/payments/service.py:218`, `:722`, `:732`; комментарий `app/db/models/equivalent.py:19`; на стадии 4 — `app/api/v1/admin.py:1171`.
- **Помощники тестов с импортом движка на уровне модуля.** `tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py:51` (`_EQUIVALENT_OWNER_LOCK_NAMESPACE`, `PaymentEngine`; `:80` — ключ owner-лока) импортируют шесть модулей: `test_p015_b4_entries_and_money_postgres.py:84`, `:864`; `test_p015_f01512_…:38`; `test_p015_inject_retries_…:36`; `test_p018_mixed_inject_event_…:51`; `tests/p018_t1809_operation_cost_probe.py:68`. Импорт переключается на стадии 2 (крайний срок — 4, иначе при удалении `engine.py` перестают собираться все шесть); к стадии 5 стенд (`observed_factory`, `_seed`, `_run`, `_runner`, `_Artifacts`, `_stored`, `_observations`) переезжает в модуль поддержки, чтобы пережить удаление ассертов owner-лока. Прочие импорты движка из тестов — таблицы раздела 4 (G1: t1522 `:38`, `:166`; p012 rt1 `:78`, `:483`; p017 `:169-224`).
- **Форма запроса предпроверки инжекта** закреплена тестом: `test_p015_t1544_inject_refuses_a_deactivated_equivalent.py:122` ищет префикс `SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE`; перенос `refuse_inactive_equivalents` сохраняет форму или правит предпосылку в том же срезе.
- **Гонки порядка «owner до строки» против трёх путей admin** (`G4b`): `PATCH` покрыт (`test_p015_t1544_operator_stop_races_postgres.py:172`, `:651`, `:267`, `:334`, `:433`, `:735`), но ни одна гонка не ассертит отсутствие `40P01` и порядок ожидания по `pg_locks` — TO WRITE; `DELETE` эквивалента против платежа есть только в `test_p015_b4_entries_and_money_postgres.py` (C17) без этих ассертов, против клиринга нет — TO WRITE; снятие hold против денег — TO WRITE (`test_p015_step5c_hold_races_postgres.py:503` гоняет снятие только против голого держателя лока).

### Стадия 3 обязана также

- **Сессия `pay()` и фикстура mode-A** (`G2 C9`, `G3 C5`). HTTP-тесты mode A подставляют `db_session` через override `get_db` (`tests/conftest.py:596-597`). Если `pay()` открывает свою `SERIALIZABLE`-сессию или повторяет на свежей, он не видит засеянного в mode A, а его коммиты уходят мимо откатываемой внешней транзакции; ассерты-предпосылки `READ COMMITTED` в lost-update (`:184-191`), узком месте (`:122-128`) и skip (`:349-355`) начинают проверять сессию, которой платёж больше не пользуется, и остаются зелёными. Решить до `T1904`: первая попытка — на DI-сессии, либо HTTP-тесты платежа — mode B.
- **Классификаторы:** `test_p015_t1529_…:250-252` ждёт сырой `23505` при исчерпании (спека — `409/E008`); `test_apply_flow_retry_on_stale.py:102` ждёт повтора `StaleDataError` на месте (снимается `FORK-1`); отрицательные контроли узкого перевода — рядом с `test_p015_p1_money_conflict_predicate.py:155-207` (докстринг `:158` цитирует устаревший `engine.py:458`); taxonomy `:200-208` фиксирует `ABORTED` + `E008` на повторяемом `40001` (изменение поведения 1 — гипотеза до репродьюсера `T1902`).
- **Таймауты фаз:** `COMMIT_TIMEOUT_SECONDS` читают клиринг (`clearing/service.py:1577-1582`) и admin abort (`admin.py:1190`) — настройку не удалять; узкое место ставит обе настройки без `raising=False` (`:51-52`); ⚑ ячейка 2 `T1523` получает новую точку инъекции отказа (`:268-278`, `:303-305`).
- **Имена входов:** `PaymentService.create_payment_internal_staged` оборачивают `test_p015_t1525_control_postgres.py:355-363`, `test_p015_step5c_hold_through_the_tick_sqlite.py:172-183`, `test_p015_p1_money_replay_postgres.py:420`, `:445` (плюс импорт `service._payment_db_sqlstate` `:442`); `create_payment_internal` зовут напрямую races `:203`, `:416`, `:680`, tick `:311`, step5c `:583`, `:592`, `:618`, `:894`, `:923`, interact `:678`, `:779`, `:814`, `:850`, `:908`. Спека не говорит, переживают ли имена `execute()`; если нет — эти строки меняются на стадии 3.
- **Замер:** зонд `tests/p018_t1809_operation_cost_probe.py:189-192` меряет только `PaymentEngine.commit` уже подготовленного платежа; для «3 → 1 коммит» нужен базовый замер **всего** `POST /payments` на дереве до стадии 3 (`G2 C11`). Точка приземления `40001` стенда повтора денежной фазы (докстринг `test_p015_p1_money_replay_postgres.py:9-29`, `:619`) перемеряется на стадиях 3 и 4.
- **Гонка идентичности `tx_id`** пишется с историей журнала или с объяснением, почему столкновение `transactions.tx_id` от плана не зависит (`G1 F13`; `test_payment_commit_advisory_locks_postgres.py:647` замерил переход `40001 → 23505` на живом размере `debt_operations`). Стенды с синтетическим DBAPI-исключением (`test_payment_engine_uow_retry_postgres.py:141-152`, taxonomy `:148-157`) — не evidence повторов (§4 спеки).
- **Имена узлов в гарде:** `tests/unit/test_p017_required_gate_runs_on_postgres.py:69-74` называет узлы узкого места, lost-update и `test_payment_idempotency_postgres.py::test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres` — переименование ломает гард.

### Стадия 4 обязана также

- **Сплошная проверка ограды `030` по дереву** (`G2 C2`) — команда 4) раздела 1, 30 файлов; помимо файлов движка: `tests/debt_setup.py:156-165` (`writer_operation(kind="PAYMENT")` сеет `NEW`; вызывающие `test_debt_symmetry.py:69`, `test_apply_flow_retry_on_stale.py:93`, `test_p018_b0a_money_the_column_cannot_hold.py:126`, `test_p018_b_step4_counterexamples_postgres.py:626`), `test_p018_a_serialization_failure_leaves_no_envelope.py:70-71`, `test_p018_b_book_transaction_contract_postgres.py:517-518`, `test_admin_incidents_list.py:33`, `:44`, `:106`, `test_admin_liquidity_summary.py:70-82`, `test_admin_whoami_and_extras.py:103-115`, `test_p015_t1548_…:208`, `test_payment_prepare_capacity_policy.py:27` (`ROUTED`). **Ложное зелёное:** `test_the_test_engine_enforces_foreign_keys.py:35-46` ждёт `IntegrityError` висячего FK, но сеет `PAYMENT NEW` — после `030` тот же класс исключения бросит CHECK; засеять `COMMITTED` и ассертить `23503`.
- **CHECK модели:** `app/db/models/transaction.py:25` (`chk_transaction_state`) отражает CHECK `030`, иначе `create_all` и мигрированная схема расходятся.
- **Третий читатель «застрявших»:** снимок графа `include=incidents` (`app/api/v1/admin.py:246-257`, фильтр `_ACTIVE_PAYMENT_TX_STATES` `:116-123`) — в спеке только `:850`, `:1104`, `:1153`, `metrics.py:648`; потребитель — `test_admin_whoami_and_extras.py:155`.
- **Admin abort (Q2) — дыры совместимости** (`G2 C10`): эндпоинт не фильтрует по типу (`admin.py:1159-1166`) — нетерминальный `CLEARING` (легален после `030`) сегодня прерывается через `PaymentEngine.abort`, ответ стадии 4 не определён; метрика `PAYMENT_EVENTS_TOTAL{abort,already_aborted}` (`test_admin_abort_tx.py:116-117`); ветка `ABORTED` сегодня заполняет `error` только если его нет (`engine.py:2007-2028`) — совместимость не перезаписывает записанный отказ, иначе меняется повтор ⚑ ячейки 2 (TO WRITE).
- **Recovery утекает за `recovery.py`** (`G2 C5`): ключи runtime-конфига `RECOVERY_ENABLED`, `RECOVERY_INTERVAL_SECONDS`, `PAYMENT_TX_STUCK_TIMEOUT_SECONDS` (`admin.py:366-368`), настройки `app/config.py:142-147` (и `PREPARE_LOCK_TTL_SECONDS`), `.env.example:51`, `main._record_recovery_iteration` (`app/main.py:255-263`) и эмиссия `:160-162`. **`RECOVERY_EVENTS_TOTAL` не удалять** — его использует hold сверки (`app/core/ledger/reconciliation.py:1029`, `:1109-1111`). Докстринги и документы вне owner surface: `docs/ru/config-reference.md:55-56`, `docs/ru/03-architecture.md:1515-1516`, `docs/ru/simulator/backend/observability.md:9`, `:32`, `docs/ru/simulator/backend/payment-integration.md:177`, `docs/ru/09-decisions-and-defaults.md:85`, `:250`, `:272`, `:294`.
- **Наблюдаемость фаз движка** (`G1 F9`): метки `PAYMENT_EVENTS_TOTAL{event=prepare|commit|abort}` эмитит только `engine.py` (и `admin.py:1223`); события логов `payment.prepare_failed`, `payment.commit_failed`, `payment.uow_retry op=commit` ассертят taxonomy `:520`, `:835` и audit_conflict `:217`. Набор меток и имена событий после стадии 4 — решение (AGENTS §12), не молчаливое исчезновение.
- **Селекторы и примеры, указывающие на DROP-файлы** (`G1 F7`): гард `tests/unit/test_the_tier_refuses_a_database_that_is_not_postgres.py:28` собирает `tests/integration/test_payment_engine_uow_retry_postgres.py`; `AGENTS.md:178`, `:211`, `:252`, `docs/ru/06-contributing.md:444`, `docs/ru/testing/quick-start-and-debugging.md:20` приводят `test_payments_2pc.py`/`test_payment_engine_uow_retry_postgres.py` как cheap gate. Перенацелить в том же срезе.
- **Общие помощники, которые должны пережить переписывание своих модулей** (`G4a C7`, `G4b F10`): `_prepare_payment` (`tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py:409`; пользователи — step5a/b/c unit, `test_p015_step5b_criterion_b_postgres.py:51`, `:207`, `:246`, `:380`, зонд t1809 `:57-60`), `_pay` (step5a unit, пользователь — `test_p015_step5a_reconciliation_postgres.py:253` с явным путём `["a","b","c"]`), `_seed_payment` (`test_p015_b4_entries_and_money_postgres.py:1335`); из файлов стадии 5 — `_seed_interlock_case`, `_use_serializable`, `_no_advisory_lock_is_held` (interlock), `_advisory_waiter_exists` (t1544 races), `_prepare_locks` (money_replay), стенд инжекта. Библиотека `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` импортируется пятью модулями (step5a/5b/5c unit, step5a PG, step5b PG).
- **Предпосылка C17** (`G4a C10`): свежий `pay()` в уже деактивированный эквивалент отказывается предпроверкой до owner-лока и не встаёт в очередь (`test_p015_b4_entries_and_money_postgres.py:1997`) — деактивировать после предпроверки (барьер) или признать, что связывающая гонка `T1544` живёт только в races.
- **Шов TTL:** якоря размещения (`test_p015_step5b_criterion_b.py:1035-1045`, `test_p015_step5c_reaction_and_hold.py:671-680`) кодируют сегодняшний порядок пути commit и чтение TTL — переопределить против порядка стадии 4.

### Стадия 5 обязана также

- **Против холостого прохода** (`G3 C4`, `G4b F12`, `G3 C10`, `G3 C6`, `G4a C5`): `_no_advisory_lock_is_held` станет истинным по построению (hold_races `:398`, races `:395`, interlock) — удалить или заменить; `_advisory_waiter_exists` (races `:127-147`, `locktype='advisory'`) — предпосылка каждой гонки `T1544` — нужна проба не-advisory ожидания (`transactionid`/`tuple` или `pg_stat_activity.wait_event_type='Lock'`); положительный контроль `test_simulator_clearing_no_deadlock.py:253-278` держится только на owner-локе родителя; `test_p1_clearing_run_perimeter_postgres.py:114` после `FORK-2` позеленеет из-за отказа изоляции (`GeoException`, `:129`), а не охраны периметра — сессия `SERIALIZABLE` (`:45`); измеритель `READ COMMITTED` `test_p015_step5b_criterion_b_postgres.py:327-360` — единственный положительный контроль стенда (б) — после `FORK-2` не запускается и заменяется.
- **Ещё тесты на `READ COMMITTED`, которых спека не называет** (`G3 C6`): `test_clearing_skip_releases_locks_postgres.py:349-355` и неявно `test_p1_clearing_run_perimeter_postgres.py:45` (клиринг наследует уровень, `clearing/service.py:1560`, `:1590`). Уточнение якорей спеки: lost-update пиннит RC на `:184-191`, `:263` — ассерт ожидания advisory, не пин изоляции.
- **Отказ «клиринг требует engine-bound сессию»** (`clearing/service.py:1495-1504`) — если `T1909` его снимает, положительный контроль `tests/integration/test_p017_t1702_mode_b_fixture_postgres.py:49`, `:316-319` пропадает, а тесты mode A начинают исполнять клиринг на тире (комментарии `test_clearing_additional_cases.py:770`, `:806`; `test_p012_t1211_shared_edge_order_postgres.py:9-15`).
- **Поведение узкого места:** сохранённый `ABORTED` проигравшего (`:232-235`) после стадии 5 становится «строки нет» (повтор на свежем снимке, отказ маршрутизации до вставки) — назвать в `T1908`, не «чинить» записью `ABORTED`. Имена узлов lost-update и узкого места держит гард `test_p017_required_gate_runs_on_postgres.py:69-72` (`:418-422`) и документы `docs/en/10-testing-framework.md:99`, `docs/ru/runbook-dev-wsl2-docker-no-desktop.md:315`.
- **`E002` details несут `reserved`** (`test_payment_prepare_capacity_policy.py:190`, `:217`) — стадия 5 меняет значение или ключ; сверить со схемой `E002` в `api/openapi.yaml` до заявления «wire не меняется».
- **Гард F-010-2** `test_p1_reconcile_after_failed_rollback_postgres.py` (чтение `_reconcile_committed_execution`, `clearing/service.py:249`) остаётся зелёным или перенацеливается на новый разрешитель исхода `T1907`.
- **Побочный эффект interlock ассертится:** `test_p015_step5c_hold_races_postgres.py:397` (`not clearing_session.in_transaction()`); цель патча клиринга `ClearingService._execute_clearing_with_amount` (wrong_writer PG `:522`, `:539`, `hits == 3` `:974`; `test_clearing_additional_cases.py:775-783`) сдвигается, если `T1907` перестраивает исполнение.
- **`downgrade` `031`/`030`** попутно проходит `test_p015_step5c_hold_races_postgres.py` T9 (`:614-688`, head → `027`) — сломанный `downgrade` всплывёт там.

### Вне 019 — для `T1911` (П4), без изменений в программе

- **Admin UI и фикстуры** продолжат показывать инциденты, которых реальный backend после стадии 4 не производит: `admin-ui/src/pages/IncidentsPage.vue:102`, `:146`, `:156`, `:179`; `admin-ui/src/api/realApi.ts:313`, `:917`, `:922`; `DashboardPage.vue:156-158`, `:635`; `LiquidityPage.vue:147`, `:183`; `admin-ui/src/advice/operatorAdvice.ts:52`, `:248-259`; `adminContracts.ts:79`; mock-тесты `mockApi.adminMutations.test.ts:112-417`, `adminMutationIntegrity.contract.test.ts:118-121`, `:182-184`, `:254`. Фикстуры: `admin-fixtures/v1/datasets/incidents.json` (2 × `PREPARE_IN_PROGRESS` и 1 × `COMMIT_IN_PROGRESS` — последнего значения нет даже в `chk_transaction_state`), `admin-fixtures/v1/api-snapshots/admin.incidents.page1.per20.json`, `incidents.json` обоих паков, генератор `admin-fixtures/tools/generate_admin_fixtures.py:382-390`, `:487-488`.
- **Журнал аудита фикстур:** по 30 записей `admin.transactions.abort` с причиной «stuck tx unblock» в `admin-fixtures/v1/datasets/audit-log.json` и `datasets/audit-log.json` обоих паков (`greenfield-village-100-v2`, `riverside-town-50-v2`), по 3 в снимках `admin.audit-log.page{1,2}.per20.json`; источник — `admin-fixtures/tools/generate_admin_fixtures.py:275`, `admin-fixtures/tools/adminlib.py:190`; синхронизированная копия — `admin-ui/public/admin-fixtures/`. Правка — только генератором и `sync:fixtures` (AGENTS §10), и только если П4 снимет эндпоинт.

## 4. Манифест по файлам

Сводная таблица — все 86 файлов: судьба, **жёсткие** стадии, группа прохода с развёрнутой строкой («что проверяет» и причина — подразделы 4.1–4.5).

| path | lines | scope | fate | stages | group | note |
|---|---:|---|---|---|---|---|
| `tests/unit/test_payments_2pc.py` | 434 | spec | DROP | 4 | 4.1 |  |
| `tests/integration/test_payment_engine_uow_retry_postgres.py` | 435 | spec | DROP | 4 | 4.1 |  |
| `tests/unit/test_payment_engine_retry_savepoint_nocommit.py` | 164 | spec | DROP | 4 | 4.1 |  |
| `tests/unit/test_payment_engine_advisory_lock_key.py` | 90 | spec | DROP | 4 | 4.1 | contract formally ends 5 (§3 п.1) |
| `tests/unit/test_payment_engine_advisory_locks_execute.py` | 552 | spec | REWRITE | 2, 4, 5 | 4.1 |  |
| `tests/integration/test_payment_commit_advisory_locks_postgres.py` | 1277 | spec | DROP | 4 | 4.1 |  |
| `tests/integration/test_payment_pair_advisory_locks_postgres.py` | 57 | spec | DROP | 4 | 4.1 | contract formally ends 5 (§3 п.1) |
| `tests/integration/test_payment_engine_audit_conflict_postgres.py` | 227 | spec | DROP | 4 | 4.1 |  |
| `tests/integration/test_payment_abort_has_error_code.py` | 49 | spec | DROP | 4 | 4.1 |  |
| `tests/integration/test_payment_prepare_capacity_policy.py` | 217 | spec | REWRITE | 4, 5 | 4.1 |  |
| `tests/integration/test_payment_prepare_error_taxonomy.py` | 1585 | spec | REWRITE | 3, 4 | 4.1 |  |
| `tests/integration/test_payment_staged_multicall_postgres.py` | 462 | spec | REWRITE | 2, 4, 5 | 4.1 |  |
| `tests/integration/test_prepare_locks_tx_id_fk_postgres.py` | 73 | spec | DROP | 5 | 4.1 |  |
| `tests/integration/test_p017_uow_retry_after_a_real_40001_postgres.py` | 138 | spec | DROP | 4 | 4.1 |  |
| `tests/unit/test_p017_default_tier_can_see_the_lock.py` | 87 | spec | REWRITE (setup-only) | 2 | 4.1 | DROP at 5 if T1908 removes the owner lock |
| `tests/unit/test_apply_flow_retry_on_stale.py` | 102 | spec | REWRITE | 3, 4 | 4.1 |  |
| `tests/unit/test_debt_optimistic_lock.py` | 121 | spec | KEEP | — | 4.1 |  |
| `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py` | 252 | spec | REWRITE | 3, 4 | 4.1 |  |
| `tests/unit/test_p015_t1522_payment_delta_drift_must_be_exact.py` | 172 | spec | REWRITE (setup-only) | 2 | 4.1 |  |
| `tests/unit/test_payment_delta_check.py` | 75 | spec | REWRITE (setup-only) | 2 | 4.1 |  |
| `tests/integration/test_payment_idempotency_postgres.py` | 267 | spec | REWRITE | 3, 4, 5 | 4.1 |  |
| `tests/integration/test_payment_inverse_multisegment_postgres.py` | 371 | spec | REWRITE | 4, 5 | 4.1 |  |
| `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` | 549 | ext | REWRITE (setup-only) | 2 | 4.1 | ext: closure |
| `tests/unit/test_payment_staged_post_commit.py` | 267 | ext | REWRITE | 4 | 4.1 | ext: closure |
| `tests/unit/test_recovery_cleanup.py` | 505 | spec | DROP | 4 | 4.2 |  |
| `tests/unit/test_admin_abort_tx.py` | 386 | spec | REWRITE | 4 | 4.2 |  |
| `tests/integration/t1523_restart_child.py` | 104 | spec | REWRITE (setup-only) | 3, 4 | 4.2 |  |
| `tests/integration/test_p015_t1523_in_progress_and_insert_race_postgres.py` | 587 | spec | REWRITE | 3, 4, 5 | 4.2 |  |
| `tests/integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py` | 335 | spec | REWRITE | 2, 3, 4 | 4.2 |  |
| `tests/integration/test_p015_t1523_restart_after_commit_postgres.py` | 301 | spec | REWRITE | 3, 5 | 4.2 |  |
| `tests/integration/p012_pg_http.py` | 67 | spec | KEEP | — | 4.2 |  |
| `tests/integration/test_p015_p1_money_replay_postgres.py` | 838 | spec | REWRITE | 5 | 4.2 | 3 if the staged entry is renamed |
| `tests/unit/test_p015_p1_money_conflict_predicate.py` | 224 | spec | KEEP | — | 4.2 | stage-3 negative controls land beside it |
| `tests/integration/test_p018_a_serialization_failure_leaves_no_envelope.py` | 130 | spec | REWRITE | 4 | 4.2 |  |
| `tests/unit/test_interact_actions_backend_p1.py` | 1826 | spec | KEEP | — | 4.2 | 3 setup-only if create_payment_internal is renamed |
| `tests/unit/test_invariants.py` | 659 | spec | REWRITE | 4 | 4.2 |  |
| `tests/unit/test_debt_symmetry.py` | 86 | spec | REWRITE | 4 | 4.2 |  |
| `tests/unit/test_the_test_engine_enforces_foreign_keys.py` | 68 | spec | REWRITE | 4, 5 | 4.2 |  |
| `tests/integration/test_p012_t1207_one_money_form_across_producers.py` | 1062 | spec | KEEP | — | 4.2 |  |
| `tests/debt_setup.py` | 396 | spec | REWRITE (setup-only) | 4 | 4.2 |  |
| `tests/p018_t1809_operation_cost_probe.py` | 407 | spec | REWRITE | 3, 4 | 4.2 | out of tier |
| `tests/unit/test_admin_incidents_list.py` | 124 | ext | REWRITE | 4 | 4.2 | ext: narrow |
| `tests/unit/test_admin_liquidity_summary.py` | 190 | ext | REWRITE | 4 | 4.2 | ext: narrow |
| `tests/unit/test_admin_whoami_and_extras.py` | 157 | ext | REWRITE | 4 | 4.2 | ext: narrow |
| `tests/unit/test_background_task_supervision.py` | 369 | ext | REWRITE | 4 | 4.2 | ext: narrow + closure |
| `tests/unit/test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py` | 432 | ext | REWRITE | 3, 4 | 4.2 | ext: narrow |
| `tests/integration/test_p1_reconcile_after_failed_rollback_postgres.py` | 167 | ext | KEEP | — | 4.2 | ext: narrow; re-point at 5 if T1907 replaces the resolver |
| `tests/integration/test_simulator_sse_replay_410.py` | 48 | ext | KEEP | — | 4.2 | ext: narrow |
| `tests/unit/test_simulator_adaptive_clearing_effectiveness_synthetic.py` | 431 | ext | KEEP | — | 4.2 | ext: narrow |
| `tests/conftest.py` | 731 | ext | KEEP | — | 4.2 | ext: narrow (comment only) |
| `tests/integration/test_clearing_commit_replay_postgres.py` | 810 | spec | REWRITE | 4, 5 | 4.3 | 2c |
| `tests/integration/test_clearing_payment_prepare_interlock_postgres.py` | 1064 | spec | REWRITE | 4, 5 | 4.3 | 2c |
| `tests/integration/test_clearing_skip_releases_locks_postgres.py` | 442 | spec | REWRITE | 4, 5 | 4.3 |  |
| `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py` | 403 | spec | REWRITE | 3, 4, 5 | 4.3 | 2c |
| `tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py` | 476 | spec | REWRITE | 3, 4, 5 | 4.3 | 2c |
| `tests/integration/test_simulator_clearing_no_deadlock.py` | 304 | spec | REWRITE | 4, 5 | 4.3 | 2c |
| `tests/unit/test_clearing_prepare_lock_conflict.py` | 121 | spec | DROP | 5 | 4.3 |  |
| `tests/unit/test_real_clearing_engine_partial_failure.py` | 323 | spec | KEEP | — | 4.3 |  |
| `tests/unit/test_routing_reserved_and_policy.py` | 284 | spec | REWRITE | 5 | 4.3 |  |
| `tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py` | 402 | spec | REWRITE | 4, 5 | 4.3 | 2c; helper library for 6 modules |
| `tests/unit/test_p015_t1544_inject_refuses_a_deactivated_equivalent.py` | 149 | spec | KEEP | — | 4.3 | stage 2 must keep the select shape |
| `tests/unit/test_p015_t1543_frozen_line_is_not_limit_zero.py` | 336 | spec | REWRITE | 4 | 4.3 |  |
| `tests/integration/test_p1_clearing_run_perimeter_postgres.py` | 197 | ext | REWRITE (setup-only) | 5 | 4.3 | ext: interlock path |
| `tests/integration/test_p1_commit_then_refresh_postgres.py` | 241 | ext | KEEP | — | 4.3 | ext: docstring |
| `tests/unit/test_clearing_additional_cases.py` | 948 | ext | KEEP | — | 4.3 | ext; patch target moves at 5 if T1909 renames the executor |
| `tests/unit/test_zero_debt_policy.py` | 109 | ext | KEEP | — | 4.3 | ext: comment |
| `tests/integration/test_p015_b4_entries_and_money_postgres.py` | 2685 | spec | REWRITE | 2, 4, 5 | 4.4 |  |
| `tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py` | 1134 | spec | REWRITE | 4, 5 | 4.4 |  |
| `tests/integration/test_p015_step5b_criterion_b_postgres.py` | 563 | spec | REWRITE | 3, 4, 5 | 4.4 |  |
| `tests/integration/test_p015_step5c_hold_races_postgres.py` | 688 | spec | REWRITE | 2, 3, 4, 5 | 4.4 |  |
| `tests/integration/test_p015_step5c_hold_through_the_tick_sqlite.py` | 197 | spec | REWRITE (setup-only) | 2 | 4.4 | 3 if the staged entry is renamed |
| `tests/integration/test_p015_t1525_control_postgres.py` | 644 | spec | REWRITE | 2, 3, 4, 5 | 4.4 |  |
| `tests/integration/test_p015_t1544_operator_stop_races_postgres.py` | 830 | spec | REWRITE | 2, 3, 4, 5 | 4.5 |  |
| `tests/integration/test_p015_t1544_operator_stop_refuses_money.py` | 446 | spec | REWRITE | 2, 3, 4, 5 | 4.5 |  |
| `tests/integration/test_p015_t1544_operator_stop_through_the_tick_sqlite.py` | 336 | spec | REWRITE | 2, 3 | 4.5 |  |
| `tests/unit/test_p015_b4_entries_and_money.py` | 752 | spec | KEEP | — | 4.5 |  |
| `tests/unit/test_p015_b4_fixture_blocks_contain_only_fixture_setup.py` | 195 | spec | KEEP | — | 4.5 |  |
| `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` | 1340 | spec | REWRITE | 4 | 4.5 | helper library for 5 modules |
| `tests/unit/test_p015_step5a_reconciliation.py` | 1018 | spec | REWRITE (setup-only) | 4 | 4.5 |  |
| `tests/unit/test_p015_step5b_criterion_b.py` | 1047 | spec | REWRITE | 4 | 4.5 |  |
| `tests/unit/test_p015_step5c_reaction_and_hold.py` | 931 | spec | REWRITE | 2, 4 | 4.5 |  |
| `tests/integration/test_p015_f01512_inject_refuses_an_opposing_debt_postgres.py` | 255 | ext | KEEP | — | 4.6 | ext: closure (transitive via inject_holds) |
| `tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py` | 186 | ext | KEEP | — | 4.6 | ext: closure (transitive via inject_holds) |
| `tests/integration/test_p015_step5a_reconciliation_postgres.py` | 422 | ext | KEEP | — | 4.6 | ext: closure (transitive via step5a unit `_pay`, wrong_writer unit) |
| `tests/integration/test_p018_mixed_inject_event_is_one_operation_postgres.py` | 208 | ext | KEEP | — | 4.6 | ext: closure (transitive via inject_holds, f01512) |
| `tests/integration/test_p018_b_book_transaction_contract_postgres.py` | 580 | ext | REWRITE (setup-only) | 4 | 4.6 | ext: non-terminal PAYMENT seed |


### 4.1. G1 — Ядро движка

Scope: 22 files of the brief + 2 import-closure files. Every line number opened in the worktree on 2026-09-24.
Legend for `stage`: the stage in which the implementation the file depends on disappears (brief rule). "4/5" = the
file dies with `engine.py` in stage 4 but the contract it pins (advisory locks) formally ends in stage 5 — see finding F1.

| path | lines | what it tests | fate | stage | reason |
|---|---|---|---|---|---|
| `tests/unit/test_payments_2pc.py` | 434 | `PaymentEngine.commit/abort` on hand-seeded `PREPARED` + `PrepareLock`: expired lock aborts, commit/abort idempotent on `COMMITTED`, `updated_at` bump, malformed persisted flows fail closed (E010), flows changed during lock wait | DROP | 4 | every test seeds a durable `PREPARED` row + `prepare_locks` and calls the engine; neither exists after stage 4 (CHECK `030`). Observable effects mapped in (B) |
| `tests/integration/test_payment_engine_uow_retry_postgres.py` | 435 | engine commit whole-UoW retry: (1) FAKE `40001` injected on `session.commit`; (2) real concurrent commit at SERIALIZABLE — 40001 on envelope INSERT, verdict retryable, final debts/audit | DROP | 4 | `engine.commit` of seeded `PREPARED`; test 1 is DBAPI injection (spec §4 forbids as concurrency evidence). Effects → new `pay()` retry test (stage 3). **Also a selector of a guard** (F7) |
| `tests/unit/test_payment_engine_retry_savepoint_nocommit.py` | 164 | `_run_uow_with_retry` on a fake session: savepoint mode does not retry 40P01 and does not roll back caller; `23505` on `uq_debts_debtor_creditor_equivalent` retryable only for `op=commit`; 55P03 → `asyncio.TimeoutError` | DROP | 4 | engine retry wrapper + classifier; classifier contract moves to `service.py:108`/`money_replay.py:104` (stage 3) — ⚑ rows are TO WRITE there |
| `tests/unit/test_payment_engine_advisory_lock_key.py` | 90 | pair advisory key: deterministic, direction-symmetric, BIGINT range; sorted acquisition | DROP | 4/5 | pure pair-lock arithmetic (contract ends stage 5); imports `PaymentEngine` (deleted stage 4) — F1 |
| `tests/unit/test_payment_engine_advisory_locks_execute.py` | 552 | engine lock mechanics: `SET LOCAL lock_timeout` + pair locks, tx-lock key/namespace, **owner-lock dedupe/sort/namespace**, preflight from persisted locks, owner→tx→first-read order for prepare/prepare_routes/commit/abort, abort reacquire, persisted-flow parser | REWRITE | 2 (owner-lock + budget tests retarget to `money_boundary`), 4 (rest deleted), 5 (owner-lock tests go if T1908 removes the lock) | mixed: owner-lock rows are a stage-2 preserved invariant; everything else is engine/pair/tx-lock/persisted-lock internal |
| `tests/integration/test_payment_commit_advisory_locks_postgres.py` | 1277 | six races over durable `PREPARED`: reservation blocks concurrent commit (RC), duplicate commit of one tx_id (SERIALIZABLE, ⚑ T1529 ×2 incl. 2000-row journal history + plan premise), commit vs abort, duplicate prepare vs commit, prepare vs abort | DROP | 4 | every test drives `engine.prepare/commit/abort` on committed `NEW`/`PREPARED` rows and waits on engine advisory locks; the commit-phase envelope race is unreachable once `transactions.tx_id` uniqueness is met first (spec «Идентичность»). Money/identity/audit effects → stage-3 identity race test + Q2 admin abort |
| `tests/integration/test_payment_pair_advisory_locks_postgres.py` | 57 | reverse-direction segments contend on one advisory key (55P03) | DROP | 4/5 | pair-lock identity; F1 |
| `tests/integration/test_payment_engine_audit_conflict_postgres.py` | 227 | real 40001 inside the commit's audit block is retried (not 25P02), payment COMMITTED; non-DB audit failure stays best-effort | DROP | 4 | `engine.commit` of seeded `PREPARED`; patches `engine_module.compute_integrity_checkpoint_for_equivalent`. ⚑ T401 (004) effect → TO WRITE stage 3 on `pay()` |
| `tests/integration/test_payment_abort_has_error_code.py` | 49 | `engine.abort(error_code=E007)` → `GET /payments/{tx_id}` shows `ABORTED` + code E007 | DROP | 4 | seeds `NEW` and calls `engine.abort`; stored-error-code-on-GET survives elsewhere |
| `tests/integration/test_payment_prepare_capacity_policy.py` | 217 | engine capacity = limit − debt + reverse debt; persisted reservations counted only if same equivalent/direction/valid; single vs multipath refusal details identical; multipath counts its own routes on top | REWRITE | 4 (engine private `_get_segment_capacity_and_reserved_usage`, `prepare`/`prepare_routes`; seeds `ROUTED` PAYMENT rows refused by CHECK `030`), 5 (reservation accounting) | capacity formula and own-route accounting are protected (AGENTS §8 routing) — must survive through the direct path |
| `tests/integration/test_payment_prepare_error_taxonomy.py` | 1585 | API/staged error taxonomy: retryable 40001 → 409/E008 **stored ABORTED**; typed client/server/operational errors → status + stored ABORTED + GET/retry/list replay + sanitisation + logs; cleanup ordering (rollback → abort); cancellation → ABORTED E007; timeout terminator (rollback/read/abort failures); staged paths | REWRITE | 3 (retryable no longer ABORTED; phase timeouts/terminators; staged structural refusal), 4 (patch targets `PaymentEngine.prepare/prepare_routes/commit/abort`, `service.engine.*`) | the file is the wire-level owner of the FORK-4 table; nearly every assert stays, several invert |
| `tests/integration/test_payment_staged_multicall_postgres.py` | 462 | (1) two staged batches under retained owner lock: exactly one 40001, fresh-tx retry, final debts/audit/limits; (2) owner lock sorts multi-equivalent set, disjoint equivalent not serialised; (3) staged owner restores caller `lock_timeout` | REWRITE | 2 (tests 2–3 retarget `acquire_staged_equivalent_owner_locks`), 4 (test 1 uses `engine.commit(commit=False)` on seeded `PREPARED`), 5 (tests 2–3 fate per T1908; `PrepareLock` count) | test 1 → DROP (effects survive in money_replay `:530`); tests 2–3 are the stage-2 owner-lock invariant |
| `tests/integration/test_prepare_locks_tx_id_fk_postgres.py` | 73 | `prepare_locks.tx_id` FK to `transactions.tx_id` blocks orphans; index `ix_prepare_locks_participant_expires_at` exists | DROP | 5 | table dropped by `031`; the schema facts become the `031` downgrade assertion (FORK-3 «полную пустую схему») |
| `tests/integration/test_p017_uow_retry_after_a_real_40001_postgres.py` | 138 | `_run_uow_with_retry` after a REAL 40001: failed rollback stops the retry (original error, cause attached); control: successful rollback → one retry | DROP | 4 | wrapper deleted with `engine.py`; the property moves to `pay()`'s retry loop (stage 3) — TO WRITE |
| `tests/unit/test_p017_default_tier_can_see_the_lock.py` | 87 | owner advisory lock is granted in `pg_locks` on the default tier; empty set takes none | REWRITE (setup-only) | 2 (import `_EQUIVALENT_OWNER_LOCK_NAMESPACE` :169/:202, `_equivalent_owner_lock_key` :203, `_acquire_equivalent_owner_locks` :210/:224 from `money_boundary`); 5: DROP if T1908 removes the owner lock, else stays | asserts unchanged |
| `tests/unit/test_apply_flow_retry_on_stale.py` | 102 | `StaleDataError` inside `_apply_flow` (→ `Book` loop `book.py:319`) is retried in place and lands 80 | REWRITE | 3 (loop removed: expectation inverts), 4 (`PaymentEngine._apply_flow` forwarder gone → call `Book`) | ⚑ FORK-1 / `09:237` L1 |
| `tests/unit/test_debt_optimistic_lock.py` | 121 | two sessions at SERIALIZABLE: stale writer refused with 40001, committed amount and version survive | KEEP | — | `PaymentEngine` appears only in the docstring (:8); no engine/Book/lock import; premise (SERIALIZABLE refuses before version compare) unaffected by any stage |
| `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py` | 252 | T1529 truth table of `PaymentEngine._is_retryable_db_error` for envelope identity `23505`; anti-drift of the identity set vs schema; retry rolls back once; bounded budget | REWRITE | 3 (classifier owner becomes `service.py:108` + `money_replay.py:104`; `op=` dimension and exhaustion result change), 4 (import) | 018 manifest had it KEEP-AS-IS (018 only); for 019 the classifier moves |
| `tests/unit/test_p015_t1522_payment_delta_drift_must_be_exact.py` | 172 | ⚑ T1522: one-atom drift detected, exact match silent, 2-atom report, tolerance constant == 0 | REWRITE (setup-only) | 2 | imports `PaymentEngine` (:38; `.check_payment_delta` :93/:120/:142) and module constant `_DELTA_DRIFT_TOLERANCE` (:166) → `money_boundary`. Spec §3 selector |
| `tests/unit/test_payment_delta_check.py` | 75 | `check_payment_delta` drift report shape (invariant, source, equivalent, total_drift, drifts pids) | REWRITE (setup-only) | 2 | import :7, call :59 → `money_boundary` |
| `tests/integration/test_payment_idempotency_postgres.py` | 267 | concurrent duplicate `create_payment_internal` of one tx_id at READ COMMITTED: loser 409/E008 "in progress", winner COMMITTED, one row/debt/audit/publication | REWRITE | 3 ("in progress" disappears; NEW no longer committed before prepare), 4 (patch `winner_service.engine.prepare` :130/:149), 5 (RC sessions :119-125 refused; `PrepareLock` count :204-208/:221) | spec §3 selector AND named by guard `test_p017_required_gate_runs_on_postgres.py:73` (F8) |
| `tests/integration/test_payment_inverse_multisegment_postgres.py` | 371 | A→B→C vs C→B→A over seeded `PREPARED` + `PrepareLock`: waiter parks on pair locks, both commit, debts net to 1/1, audit 2, limits intact | REWRITE | 4 (seed `PREPARED`/`PrepareLock`, `engine.commit`), 5 (pair-lock wait premise :282-288, `PrepareLock` count) | spec §3 selector; final-state asserts must survive |
| `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` | 549 | F-012-1 over HTTP + counter-check staircase | REWRITE (setup-only) | 2 | only `import app.core.payments.engine as engine_module` (:78) and `monkeypatch.setattr(engine_module, "_DELTA_DRIFT_TOLERANCE", …)` (:483) → `money_boundary`; docstring anchors `engine.py:1545` (:20, :120) stale. Stage 4 must keep `check_payment_delta` on the direct path or `:471-480` (409 PAYMENT_DELTA_DRIFT) loses its subject |
| `tests/unit/test_payment_staged_post_commit.py` | 267 | staged payment: savepoint rollback leaves nothing; effects/publication/cache/metrics apply once after outer commit; replay adds nothing; committed result not read from expired objects; cancellation leaves nothing | REWRITE | 4 | patches `app.core.payments.engine.PAYMENT_EVENTS_TOTAL` (:122) and `service.engine.commit` (:209-216); metric labels `prepare`/`commit` (:170-176) are emitted by `engine.py` (F9) |

### 4.2. G2 — Состояния, recovery, идемпотентность, повтор денежной фазы, помощники

Read-only pass on worktree `.local-run/worktrees/p019t1901` (code = `2aee461`). Every line number below was opened in that tree. Wide grep (`PaymentEngine|PrepareLock|prepare_locks|PaymentRecovery|ClearingCommittedAfterCancellation|payment_clearing_interlock`) = 66 files; narrow (`PREPARED|PrepareLock|prepare_locks|recovery|stuck`) = 43 files — both reproduced on this tree.

| path | lines | what it tests | fate | stage | reason |
|---|---|---|---|---|---|
| `tests/unit/test_recovery_cleanup.py` | 505 | `app.core.recovery`: expired-lock cleanup, stale-PREPARED abort, per-item progress, outcome counters, rollback escalation | DROP | 4 | every subject is `recovery.py` (deleted stage 4) acting on durable `PREPARED`+`prepare_locks`, which CHECK `030` forbids; only surviving need = "030 refuses a non-drained DB" (TO WRITE 4) |
| `tests/unit/test_admin_abort_tx.py` | 386 | admin abort: 403/404/409, live→ABORTED+audit+metric, ABORTED idempotent, advisory race, bounded owner wait, audit/commit failure rollback | REWRITE | 4 | spec §3/Q2 names it: compatibility contract. Seeds `PAYMENT` in `WAITING` (`:45`, `:142`, `:200`, `:296`, `:334`) — refused by CHECK `030`; patches `PaymentEngine._acquire_tx_advisory_lock` (`:159`) and `app.api.v1.admin.PaymentEngine.abort` (`:214`) |
| `tests/integration/t1523_restart_child.py` | 104 | helper (child process) for T1523 cell 8 | REWRITE (setup-only) | 3, 4 | die point = wrap of `PaymentEngine.commit` (`:53`, `:59-70`). Stage 3: `commit(commit=False)` is inside the outer tx, so dying there leaves NOTHING durable — die point must move to after `pay()`'s outer COMMIT; stage 4: `PaymentEngine` import gone. Entry `PaymentService.create_payment` (`:75`) — keep if the name survives |
| `tests/integration/test_p015_t1523_in_progress_and_insert_race_postgres.py` | 587 | T1523 cells 3 (in-progress 409 from PREPARED) and 5 (insert race, 23505 or 40001) | REWRITE | 3, 4, 5 | ⚑ matrix. Stage 3: no durable NEW/PREPARED → cell 3 per spec, cell 5 premise (`:472`, `:546` `NEW`) impossible; barriers on `winner_service.engine.commit/prepare` (`:238`, `:245`, `:402`, `:443`) move/disappear (4); `_classify_payment_db_error` recorder (`:380-390`) follows the stage-3 classifier; `PrepareLock` count (`:561-570`) → 5 |
| `tests/integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py` | 335 | T1523 cell 1 (replay of COMMITTED under integrity hold) and cell 2 (replay of stored ABORTED) over HTTP | REWRITE | 2, 3, 4 | ⚑ `:252`. `PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON` (`:44`, `:247`) → constant moves (2, at latest 4); cell 2 abort injection `PaymentEngine.prepare` patch + `PREPARE_TIMEOUT_SECONDS` (`:268-278`, `:303-305`) → phase timeout changes (3), engine gone (4). Assertions stay |
| `tests/integration/test_p015_t1523_restart_after_commit_postgres.py` | 301 | T1523 cell 8: process A commits then dies, process B gets stored result, no double effect | REWRITE | 3, 5 | ⚑ matrix. Premises hold only if child's die point moves (3); `PrepareLock` import/leftover assert (`:154`, `:293-301`) → 5 |
| `tests/integration/p012_pg_http.py` | 67 | helper: HTTP client on committed sessions for 012 reproducers | KEEP | — | `PaymentEngine` only in docstring (`:10`, `:19`); docstring premise ("shared `client` gives 500") is already stale since the conftest fix 2026-09-23 (`tests/conftest.py:532-539`) |
| `tests/integration/test_p015_p1_money_replay_postgres.py` | 838 | P1: real 40001 replays the tick's money phase (⚑ `:530/:703/:751/:796`) | REWRITE | 3 (conditional), 5 | `PrepareLock` (`:75`) + `_prepare_locks` helper (`:493-512`) asserted `== 0` at `:593`, `:671`, `:743` → table dropped stage 5; patches `PaymentService.create_payment_internal_staged` (`:420`, `:445`) and imports `service._payment_db_sqlstate` (`:442`) — setup change in stage 3 only if the staged entry is renamed to `execute` / the helper moves |
| `tests/unit/test_p015_p1_money_conflict_predicate.py` | 224 | `money_conflict_name` accepts 40001/40P01/typed conflict, refuses everything else (real driver errors) | KEEP | — | no engine use (docstring `:158` cites stale `engine.py:458`). Stage 3 changes classifier `money_replay.py:104` → the StaleDataError negative/positive controls the spec demands naturally land here as NEW tests (see C) |
| `tests/integration/test_p018_a_serialization_failure_leaves_no_envelope.py` | 130 | 018 T1801: real 40001 inside `Book.post` → no envelope/entries, original SQLSTATE visible to retry predicate, clean retry | REWRITE | 4 | 018 contract. `PaymentEngine(loser)._is_retryable_db_error` (`:32`, `:99`) → predicate's new owner (`pay()`); placeholder `Transaction(type="PAYMENT", state="NEW")` (`:70-71`) refused by CHECK `030` |
| `tests/unit/test_interact_actions_backend_p1.py` | 1826 | simulator interact actions (payment-real, clearing-real, trustline) | KEEP | — | `ClearingCommittedAfterCancellation` (`:13`, `:1312`) is PRESERVED; patches `PaymentService.create_payment_internal` (`:678`, `:779`, `:814`, `:850`, `:908`) — survives unless stage 3 renames it (then setup-only, 3) |
| `tests/unit/test_invariants.py` | 659 | invariant checker; payment commit aborts on trust-limit violation; payment commit writes IntegrityAuditLog | REWRITE | 4 | two tests drive `PaymentEngine.commit` over hand-seeded `PREPARED`+`PrepareLock` (`:106`, `:435`); other 7 tests untouched |
| `tests/unit/test_debt_symmetry.py` | 86 | symmetry checker; `_apply_flow` nets mutual debts | REWRITE | 4 | `PaymentEngine._apply_flow` (`:8`, `:62`, `:71`) gone; plus `writer_operation(kind="PAYMENT")` placeholder (debt_setup) |
| `tests/unit/test_the_test_engine_enforces_foreign_keys.py` | 68 | tier DB refuses dangling FK (transactions.initiator_id; bare `PrepareLock.tx_id`) | REWRITE | 4, 5 | test 1 seeds `PAYMENT` `NEW` (`:42`): after `030` the CHECK raises `IntegrityError` first → test goes green for the WRONG reason (anti-vacuum break); test 2 uses `PrepareLock` (`:23`, `:58-67`) → 5 |
| `tests/integration/test_p012_t1207_one_money_form_across_producers.py` | 1062 | one money form across producers (snapshot, patches, clearing) | KEEP | — | only `ClearingCommittedAfterCancellation` (`:70`, `:560`) — preserved |
| `tests/debt_setup.py` | 396 | helper: `debt_fixture_setup`, `writer_operation`, fixture-block AST guard | REWRITE (setup-only) | 4 | `writer_operation` inserts `Transaction(type="PAYMENT", state="NEW")` placeholder (`:156-165`) → refused by CHECK `030` for kind `PAYMENT` (callers: `test_debt_symmetry.py:69`, `test_apply_flow_retry_on_stale.py:93`, `test_p018_b0a_money_the_column_cannot_hold.py:126`, `test_p018_b_step4_counterexamples_postgres.py:626`); use `COMMITTED` for PAYMENT (CLEARING `NEW` stays legal). Docstrings `:138`, `:171`, `:278`, `:303` mention `PaymentEngine` (AST guard examples — harmless) |
| `tests/p018_t1809_operation_cost_probe.py` | 407 | out-of-tier probe: cost of one payment commit / clearing / inject event | REWRITE | 3, 4 | measures only `PaymentEngine(session).commit(tx_id)` of a pre-PREPARED tx (`:54`, `:189-192`, helper `_prepare_payment` imported `:57-60` runs real `PaymentEngine.prepare`); spec §19.2 п.5 names it the template for stage 3/5 cost → the payment operation must become the whole API payment (`pay()`), and a BEFORE baseline of the whole 3-commit path must be taken on the pre-stage-3 tree |
| `tests/unit/test_admin_incidents_list.py` | 124 | `GET /admin/incidents` lists only stuck PAYMENT over SLA; pagination | REWRITE | 4 | outside wide grep. Seeds `PAYMENT` in `PREPARE_IN_PROGRESS`/`WAITING` (`:33`, `:44`, `:106`) → refused by `030`; spec: list returns empty |
| `tests/unit/test_admin_liquidity_summary.py` | 190 | liquidity summary incl. `incidents_over_sla` | REWRITE | 4 | outside wide grep. Seeds `PAYMENT` `PREPARED` (`:70-82`) → refused; `:101` `incidents_over_sla == 1` → 0 |
| `tests/unit/test_admin_whoami_and_extras.py` | 157 | whoami, feature flags, graph snapshot `include=incidents,...` | REWRITE | 4 | outside wide grep. Seeds `PAYMENT` `PREPARED` (`:103-115`) → refused; `:155` `len(incidents) >= 1` — a THIRD stuck-list reader (`admin.py:248-256`) the spec does not list |
| `tests/unit/test_background_task_supervision.py` | 369 | background job supervisor, lifespan order, health degradation, recovery loop iterations | REWRITE | 4 | imports `app.core.recovery` (`:11`); 4 tests use `name="recovery"` only as a label; 2 tests (`:303`, `:348`) test `recovery_loop` / `_run_recovery_iteration` / `main._record_recovery_iteration` |
| `tests/unit/test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py` | 432 | T1548: replay of a row without fingerprint refused before in-progress/perimeter; both lookup entrances | REWRITE | 3 (verify), 4 | outside wide grep. ⚑ T1548. `:208` sweeps stored states incl. `NEW/ROUTED/PREPARE_IN_PROGRESS/PREPARED` → unseedable after `030`; insert-race entrance (`:271`) moves from `IntegrityError` handler to the stage-3 identity resolver |
| `tests/integration/test_p1_reconcile_after_failed_rollback_postgres.py` | 167 | F-010-2 REFUTED guard: clearing's `_reconcile_committed_execution` read is fresh after a failed rollback | KEEP | — | "recovery" = the clearing outcome resolver's read, not `app.core.recovery`. Stage 5 rebuilds clearing outcome resolution (T1907) — this guard must stay green or be re-pointed then (see C) |
| `tests/integration/test_simulator_sse_replay_410.py` | 48 | SSE replay 410 | KEEP | — | word "recovery" in docstring `:1` only |
| `tests/unit/test_simulator_adaptive_clearing_effectiveness_synthetic.py` | 431 | adaptive clearing policy | KEEP | — | "recovery" = policy recovery phase (`:102-122`) |
| `tests/conftest.py` (matching lines only) | 731 | fixtures | KEEP | — | `:602` comment "(recovery/integrity)" about not running lifespan — stale after 4, comment only |

### 4.3. G3 — Локи, резервы, interlock, клиринг, инжект

Read-only pass on worktree `.local-run/worktrees/p019t1901` (code = `main` 2aee461). Line numbers are from that tree.
"Stage 2 (conditional)" = only if T1903 stops routing the call through the `PaymentEngine` method/constant the test patches or imports; stage 4 is the hard deadline, because `engine.py` is deleted there.

| path | lines | what it tests | fate | stage | reason |
|---|---|---|---|---|---|
| `tests/integration/test_clearing_commit_replay_postgres.py` | 810 | ⚑ spec §3 selector. Same-cycle concurrent clearings resolve to one durable occurrence; a real 40001 without a committed occurrence stays E010; post-commit cancellation/ack-loss/connection-loss reconciles and a new cycle still clears | REWRITE | 2 (cond., patch target), 4 (patch target), 5 | patches `PaymentEngine.acquire_session_equivalent_owner_lock` (:163, :180-184); advisory-wait barrier (:216); **test 2 verdict inverts under the stage-5 clearing retry owner** (:468, :499-505); connection-loss variants assume the pinned `AsyncConnection` bind (:683-685) |
| `tests/integration/test_clearing_payment_prepare_interlock_postgres.py` | 1064 | interlock schedules clearing-first / payment-first, single-connection pool, external-bind refusal, cancellation at checkout / in work / in release, interlock timeout; T1537 helper control; **helper library** for two §3 selectors | REWRITE (file stays for its helpers; 5 of 10 tests DROP) | 2 (cond.), 4, 5 | `_seed_interlock_case` inserts a `PAYMENT` in state `NEW` (:276-291) → refused by CHECK `030` at stage 4 and it is imported by `test_p015_step5c_hold_races_postgres.py:58-62` and `test_p015_t1544_operator_stop_races_postgres.py:55-59` (§3 selectors); tests :307, :484 call `PaymentEngine.prepare` (stage 4); owner-lock holder/probe via `PaymentEngine.acquire_staged_equivalent_owner_locks` (:865, :990, :1031); interlock internals `_locked_pairs_for_equivalent`, `_release_interlock_session`, pinned connection (stage 5) |
| `tests/integration/test_clearing_skip_releases_locks_postgres.py` | 442 | every skip branch ends the service-owned tx; policy skip releases debt rows so a concurrent payment commits | REWRITE | 4, 5 | branch `locked` seeds a `PREPARED` `PAYMENT` + `PrepareLock` (:119-148) → CHECK `030` at **stage 4**, not 5; test 2 pins READ COMMITTED (:349-355) and reads `prepare_locks` (:393-399, :411) → stage 5 |
| `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py` | 403 | ⚑ payment + clearing on one trustline keep both effects (final debts, versions, audits, publication) | REWRITE | 2 (cond.), 3 (premise), 4 (patch target), 5 | patches `PaymentEngine._acquire_equivalent_owner_locks` (:216, :233-245) → dies with `engine.py` at stage 4 although spec §3 places the file in stage 5; READ COMMITTED :184-191; advisory wait :263-275; `PrepareLock` :71, :326-330, :349 |
| `tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py` | 476 | ⚑ two payments over one 10-capacity bottleneck: one commit, one E002 | REWRITE | 2 (cond.), 3 (setup), 4, 5 | test 1 patches `PaymentEngine._acquire_equivalent_owner_locks` (:79-111) → stage 4; test 2 calls `PaymentEngine.prepare_routes` and seeds `NEW` `PAYMENT` rows (:327-399) → stage 4 DROP; READ COMMITTED :122-128 and `rejected: ABORTED` (:232-235) → stage 5 |
| `tests/integration/test_simulator_clearing_no_deadlock.py` | 304 | tick commits its parent session before clearing (Bug X); positive control = parent holds the equivalent owner lock | REWRITE | 2 (cond.) / 4 (import), 5 (stand) | the positive control is `PaymentEngine(...)._acquire_equivalent_owner_locks` (:263, :274); at stage 5 the owner lock no longer exists, so the stand must hold something clearing still needs (e.g. `FOR UPDATE` on a triangle debt) or the test turns vacuous again (its docstring :16-19 measured exactly that) |
| `tests/unit/test_clearing_prepare_lock_conflict.py` | 121 | `find_cycles` excludes an edge with an active prepare lock (stubbed session) | DROP | 5 | single test; subject is the reservation exclusion `_locked_pairs_for_equivalent` (`clearing/service.py:996`, calls :1126, :1265) removed at stage 5 |
| `tests/unit/test_real_clearing_engine_partial_failure.py` | 323 | tick clearing engine finalizes partial progress; ⚑ `ClearingCommittedAfterCancellation` consumed as actual amount; exact Decimal volume | KEEP | — | uses a fake service; only imports `ClearingCommittedAfterCancellation` (kept, spec §2 / `real_clearing_engine.py:390`); helper module for `tests/unit/test_p012_t1211_engine_ladder.py:45` |
| `tests/unit/test_routing_reserved_and_policy.py` | 284 | router subtracts reserved capacity; intermediate/blocked policy; T1545 self-pay | REWRITE | 5 | only test 1 touches reservations (:107-108 stub, :142-153, :173); other 4 tests unaffected |
| `tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py` | 402 | ⚑ 015 phase B step 3 reproducer: every injected debt written under its equivalent's owner lock; payments snapshot read under it; lock is transaction-level. **Helper library** for 6 modules | REWRITE | 2 (cond.) / 4 (module import), 5 | module-level `from app.core.payments.engine import _EQUIVALENT_OWNER_LOCK_NAMESPACE, PaymentEngine` (:51) — a failure here breaks collection of all importers (list in C3); owner-lock assertions die at stage 5 (unless T1908 keeps the equivalent lock) |
| `tests/unit/test_p015_t1544_inject_refuses_a_deactivated_equivalent.py` | 149 | ⚑ T1544 inject refusal before envelope | KEEP | — | behaviour through `_apply_due_scenario_events`; `PaymentEngine` only in docstring (:5). Stage-2 constraint: the guard's read must keep its column order (premise :122 matches `SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE`) |
| `tests/unit/test_p015_t1543_frozen_line_is_not_limit_zero.py` | 336 | ⚑ T1543 frozen line vs stored limit: invariant, checkpoint, payment audit, partial repayment | REWRITE | 4 | two tests seed `PREPARED` + `PrepareLock` (:135-181) and call `PaymentEngine.commit` with an `_apply_flow` perturbation (:184-198, :227-229, :258-260) → stage 4 (engine + CHECK `030`), not 5. 5 other tests unaffected |
| `tests/integration/test_p1_clearing_run_perimeter_postgres.py` | 197 | F-010-3 perimeter guard on the PG "interlock path" (:114, :141); SQL producer bind (:167) | REWRITE (setup-only) | 5 | own engine without `isolation_level` (:45) = server default READ COMMITTED, and clearing inherits the caller's level (`clearing/service.py:1560`, :1590). After stage 5 refuses non-SERIALIZABLE writers: :141 fails, and **:114 stays green for the wrong reason** (isolation refusal is also a `GeoException`, :129). Setup: `create_async_engine(committed_database.url, isolation_level="SERIALIZABLE")`; docstring :1-15 ("interlock path") goes stale. All assertions (:127, :129-137, :155, :161-163, :185-197) stay |
| `tests/integration/test_p1_commit_then_refresh_postgres.py` | 241 | F-009-6 trustline create: connection loss after commit is success | KEEP | — | not a money writer, no lock/interlock use; mention of the interlock file only in docstring (:24) |
| `tests/unit/test_clearing_additional_cases.py` | 948 | clearing policy/scope/detection, failure/rollback/commit/checkpoint paths | KEEP (conditional) | — | `_rollback_before_interlock`/interlock only in comments (:766-771, :804-810, :865-869). Condition: :775-783 patches `ClearingService._execute_clearing_with_amount` and dirties `self.session`; if T1909 renames/merges that private executor, the patch target moves (stage 5). Comments go stale at stage 5 |
| `tests/unit/test_zero_debt_policy.py` | 109 | clearing deletes zero debts; payload/audit shape | KEEP | — | `_rollback_before_interlock` only in comment (:48-54); plain-value capture is harmless after stage 5 |

### 4.4. G4a — Проверочные тесты 015 на Postgres

Read-only pass on worktree `.local-run/worktrees/p019t1901` (code = main `2aee461`). Nothing run. Line numbers opened in the worktree.

| path | lines | what it tests | fate | stage | reason |
|---|---|---|---|---|---|
| `tests/integration/test_p015_b4_entries_and_money_postgres.py` | 2685 | 015 B4 on PG: C4-P full-size entry chain, C8 real 40001 (hand retry, payment-owner retry, inject-owner retry), C12-P money domain, C13-P concurrent identity, C14 payment/clearing envelope intent + order, C17-P equivalent deletion (route, owner-lock race, raw DELETE), C18-P, C19-P forged rows, NaN | REWRITE | 2 (import), 4, 5 | Three tests drive the payment through `_seed_payment` (durable `PaymentEngine.prepare`, :1335-1376) and a separate `PaymentEngine.commit` (C8-owner :708, C14-payment :1424, C17-race :1848); owner-lock key/namespace imported from engine (:1898-1907, :2074-2098); `PrepareLock`/`prepare_locks` reads (:76, :591-597, :771-775, :1462-1467, :1484-1488, watcher :1379-1420); advisory gate + `pg_locks` observer (:208-236, :1920-1923, :1959-1961). Unaffected, KEEP in place: C4-P :401, C12-P :1068/:1198, C13-P :1229, C14-clearing :1564, C17 case 1 :1762, C18-P :2137, C19-P :2391/:2456/:2644, C8-inject :835 (only its imported stand, see C7) |
| `tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py` | 1134 | 015 B4 C5/C6 at FULL_SIZE under SERIALIZABLE: criterion (a) holds, (b) refutes a wrong payment writer and a wrong clearing writer; controls | REWRITE | 4, 5 (import) | Payment tests (:553, :648, :794) use `_prepare_payment` (durable prepare, :404-438), `_intent_flows` read from `prepare_locks` between prepare and commit (:441-465) as the independent declared-flow capture, `_collapse_the_route` patching `PaymentEngine._apply_flow` (:488-510), `PaymentEngine.commit` (:606, :706, :828). Clearing tests :881, :1045 KEEP (conditional, C13). `PrepareLock` import :67 goes with the model at stage 5 |
| `tests/integration/test_p015_step5b_criterion_b_postgres.py` | 563 | 015 step 5b on PG: version CHECK both paths, pre-state read placement vs locks, pre-state window forced (SERIALIZABLE, READ COMMITTED meter, application writer), asyncpg controls, unwidened CHECK followed through the service | REWRITE | 3, 4, 5 | Engine drive + private seam `PaymentEngine._read_payment_prestate` (:211, :250-264, :383-395); advisory-lock placement (:216-221) and owner-lock wait (:408-409); READ COMMITTED meter (:327) cannot run once payment refuses non-SERIALIZABLE (stage 5, FORK-2); :479 asserts `ABORTED` stored for a 23514 internal error (stage 3 classification). KEEP in place: :146, :459, :467 (the last two depend only on the unit module — other group) |
| `tests/integration/test_p015_step5c_hold_races_postgres.py` | 688 | T1546 hold binding at SERIALIZABLE: payment commit vs hold, reaction vs payment, owner lock before snapshot, clearing vs hold both orders, hold below TTL branch, admin clear under owner lock, RESTRICT, both schema paths + downgrade | REWRITE | 2, 3, 4, 5 | Patches `PaymentEngine.refuse_inactive_equivalents` (:253-261) and takes owner lock via `PaymentEngine.acquire_staged_equivalent_owner_locks` (:314, :530) → stage 2; T1 observes durable `PREPARED` (:207) → stage 3; barrier on `PaymentEngine.commit` (:187-194), T6 inserts `PREPARED`+`PrepareLock` and calls `PaymentEngine.commit` (:460-496) → stage 4 (T6 DROP); `_advisory_waiter_exists` (:215, :277, :317, :371, :438, :538), `_no_advisory_lock_is_held` (:398), `_locked_pairs_for_equivalent` pause (:423-431), interlock helpers (:58-62), `_prepare_locks` (:67, :231) → stage 5. KEEP in place: T8 :556, T9 :614 |
| `tests/integration/test_p015_step5c_hold_through_the_tick_sqlite.py` | 197 | T1546 through `RealRunner.tick_real_mode`: held inject consumed, held tick clearing not charged, held staged payment rejected; run not charged | REWRITE (setup-only) | 2 (or 4 at latest) | Only `from app.core.payments.engine import PaymentEngine` (:26) and `HOLD = PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON` (:47). Every assertion stays in place. Conditional stage 3: wrapper on `PaymentService.create_payment_internal_staged` (:172-183) if that name does not survive `execute()` (C11) |
| `tests/integration/test_p015_t1525_control_postgres.py` | 644 | T1525: aborted payment (engine commit / service) leaves debts unchanged; rolled-back tick leaves no staged payment (executor / real tick) | REWRITE | 2, 3, 4, 5 | Patch target `PaymentEngine.check_payment_delta` with engine-private `self._get_debt` (:167-182) → stage 2; engine-commit scenario (:222-268, test :592) drives durable `NEW`→`PREPARED`→fresh-session commit → DROP test at stage 4 (survivor: service test); service scenario asserts `ABORTED` stored for an integrity violation (:295) → stage 3; `PrepareLock` count (:62, :273-277, :296) → stage 5. Tick tests :620/:634 setup-only conditional (staged wrapper :355-363) |

Group totals: 6 files / 5911 lines. No whole-file DROP; no file KEEP.

#### Setup-only lines (assertions untouched)

- `test_p015_step5c_hold_through_the_tick_sqlite.py`: :26, :47 (stage 2 if the reason constants move with `MONEY_STOP_REASONS`, else stage 4). Conditional stage 3: :172, :183.
- `test_p015_b4_entries_and_money_postgres.py::test_c17_p_a_raw_delete_of_an_equivalent_with_history_is_refused_by_the_foreign_key` (:2053): :2074 import (stage 2), :2094-2100 owner advisory lock taken only for realism (stage 5: remove). Assertions :2085-2089, :2119-2127 stay (⚑ design v2 §10.1 case 3).
- `test_p015_b4_entries_and_money_postgres.py::test_c8_the_inject_owners_own_retry_...` (:835): no line edit; imported stand (:84-86, :864-870) lives in `test_p015_inject_holds_the_owner_lock_postgres.py` (see C7).
- `test_p015_t1525_control_postgres.py` tick tests (:620, :634): :355, :363 only if `create_payment_internal_staged` is renamed (stage 3).

### 4.5. G4b — Unit-тесты 015 и стоп T1544

Worktree `.local-run/worktrees/p019t1901` (code = `2aee461`). Read-only pass, 2026-09-24. Nothing run.
Line numbers are real (opened). Stage rule per the common brief.

| path | lines | what it tests | fate | stage | reason |
|---|---|---|---|---|---|
| `tests/integration/test_p015_t1544_operator_stop_races_postgres.py` | 830 | ⚑ T1544 cutoff by real races: payment↔PATCH both orders (:172, :651), clearing↔PATCH both orders (:267, :334), tick↔PATCH (:433), replay exhaustion (:510), TTL-vs-stop precedence (:577), inject↔PATCH (:735) | REWRITE | 2 (import/patch target), 3 (premises), 4 (barrier target, :577 DROP), 5 (advisory premises, clearing barrier, prepare_locks) | imports `PaymentEngine` (:47) for `EQUIVALENT_INACTIVE_REASON` (:156, :482, :641) and as barrier target (`commit` :192-199, `refuse_inactive_equivalents` :668-676, direct `commit` :638); every race premise is `_advisory_waiter_exists` (:127-147, `locktype='advisory'`); PREPARED visibility premise :216; `PrepareLock` (:51, :602, :627) and `_prepare_locks` counts; clearing barrier on `_locked_pairs_for_equivalent` (:288) |
| `tests/integration/test_p015_t1544_operator_stop_refuses_money.py` | 446 | ⚑ T1544 refusal points via HTTP: prepare-time (no row), replay after stop (T1523 cell 1, journal unchanged), commit-time, clearing auto, `clearing-real` 409 | REWRITE | 2 (constant), 3 (:262 stand), 4 (patch target), 5 (`PrepareLock`) | `PaymentEngine.EQUIVALENT_INACTIVE_REASON` (:176, :443); :262 patches `PaymentEngine.commit` and **commits the payment's own session mid-payment** (:284-287) — impossible once the payment is one transaction; `PrepareLock` count :304-309 |
| `tests/integration/test_p015_t1544_operator_stop_through_the_tick_sqlite.py` | 336 | T1544 through `tick_real_mode`: refused inject consumed, refused tick clearing not charged, refused staged payment is REJECTED | REWRITE | 2 (constant), 3 (staged refusal shape) | constant at :328; recorder :311-322 expects the staged refusal to be **raised** — T1905 makes a definitive staged refusal a structural `PaymentResult(ABORTED)`; `Transaction` count 0 at :336 depends on whether T1905 makes this refusal durable |
| `tests/unit/test_p015_b4_entries_and_money.py` | 752 | 018-rewritten C4/C13(open)/C15/C17/C18 on `Book` + triggers | KEEP | — | no engine import; `PaymentEngine._apply_flow` only in C18 docstring (:656). C18 (:650) hand-writes the savepoint retry (:710-722); it does not call `book.py:319/:395`, so stage 3 removal of the `Book` loop does not break it (docstring premise "API-shaped" becomes stale — finding F9) |
| `tests/unit/test_p015_b4_fixture_blocks_contain_only_fixture_setup.py` | 195 | AST guard over every `debt_fixture_setup` block + C21 | KEEP | — | `PaymentEngine` appears only inside source strings fed to the AST guard (:134, :143), never imported or executed; the guard rejects any non-constructor call, so the samples stay valid after `engine.py` is deleted |
| `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` | 1340 | ⚑ C5 honest payment (a)+(b); C13 payment/clearing replay one envelope; ⚑ C6(i) wrong route + control; ⚑ C6(ii) under-clearing + control. **Helper library** for 5 modules | REWRITE | 4 | helpers `_prepare_payment` (:409-437: NEW row + real `PaymentEngine.prepare` → durable PREPARED), `_intent_flows` (:440-467: reads `PrepareLock.effects` as the independent declaration), `_collapse_the_route` (:751-770: patches `PaymentEngine._apply_flow`); tests call `PaymentEngine(session).commit` (:542, :641, :645, :843, :976). Clearing tests (:670, :1069, :1243) untouched |
| `tests/unit/test_p015_step5a_reconciliation.py` | 1018 | ⚑ criterion (a), baseline, result transitions, scheduled host, interleave, C6 with baseline | REWRITE (setup-only) | 4 | only the payment driver: import :64, `_pay` :189-193, interleave prepare :268 + commit :302-304, C6 drive :979-982; imports `_prepare_payment`, `_collapse_the_route` (:78-85). Every assertion stays in place |
| `tests/unit/test_p015_step5b_criterion_b.py` | 1047 | ⚑ criterion (b) per kind, payment intent v2 prestate, v1 structure, clearing, inject subset, version split, (b) fingerprint, prestate placement anchor | REWRITE | 4 | setup-only everywhere except the placement anchor :997 (TTL anchor + "exactly one statement between stop and envelope" change with the stage-4 order). Driver lines: import :49, `_prepare_payment`/`_collapse_the_route`/`PaymentEngine.commit` at :315-318, :524-527, :947-950, :1016-1020; `_pay` via step5a |
| `tests/unit/test_p015_step5c_reaction_and_hold.py` | 931 | ⚑ T1516 reaction + ⚑ T1546 hold: log/metric after commit, idempotence, refusal points (prepare, commit, clearing, `clearing-real`), admin clear lifecycle | REWRITE | 2 (constants, direct helper call), 4 (:634 stand) | `PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON` / `EQUIVALENT_INACTIVE_REASON` (:560, :627, :827); direct `PaymentEngine.refuse_inactive_equivalents` (:623); :634 needs a durable PREPARED payment (:658-659) and the TTL anchor (:672-673); `PaymentService.create_payment_internal` used at :583, :592, :618, :894, :923 (entry point must survive stage 3 or these lines move) |

### 4.6. Вне групп — сведено оркестратором

| path | lines | what it tests | fate | stage | reason |
|---|---|---|---|---|---|
| `tests/integration/test_p015_f01512_inject_refuses_an_opposing_debt_postgres.py` | 255 | F-015-12: inject refuses a debt opposite to an existing one | KEEP (transitive) | — | reaches the engine only through `_Artifacts`, `_run`, `_runner`, `_World` from `test_p015_inject_holds_the_owner_lock_postgres.py` (`:38-43`); no line changes if the stage-2/4 import switch of that module (section 3, stage 2) keeps the helper names |
| `tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py` | 186 | 015 B3: a real 40001 restarts the inject's unit of work | KEEP (transitive) | — | same helper module (`:36-44`); condition as above |
| `tests/integration/test_p015_step5a_reconciliation_postgres.py` | 422 | step 5a on PG: schema paths, asyncpg spellings, cutover race | KEEP (transitive) | — | imports `_edges`, `_seed_triangle` (wrong_writer unit, engine-free) and `_pay` etc. from `tests/unit/test_p015_step5a_reconciliation.py` (`:41-54`); `_pay(factory, triangle, ["a","b","c"], "5")` (`:253`) needs the explicit-route entry that stage 4 must provide (section 3, item 8). Spec §3 selector |
| `tests/integration/test_p018_mixed_inject_event_is_one_operation_postgres.py` | 208 | 018 T1802: a mixed inject event is one operation | KEEP (transitive) | — | helpers from `test_p015_f01512_…` and `test_p015_inject_holds_the_owner_lock_postgres.py` (`:46-56`); condition as above |
| `tests/integration/test_p018_b_book_transaction_contract_postgres.py` | 580 | 018 stage B: `Book` transaction contract on PG | REWRITE (setup-only) | 4 | seeds `Transaction(type="PAYMENT", state="NEW")` (`:517-518`) — refused by CHECK `030`; seed `COMMITTED` (the test needs only an existing `tx_id`). Not matched by any pattern of section 1 except the `030` sweep |

## 5. Карта по ассертам

Одна таблица на каждый REWRITE-файл с картой и каждый DROP-файл, по группам. Колонки: строка ассерта, тест, проверяемый эффект, судьба. ⚑ — с источником в строке.

### 5.1. G1 — Ядро движка

#### `tests/unit/test_payments_2pc.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :62-66 | test_commit_rejects_expired_locks | expired reservation at commit → raise, tx ABORTED | DROP: `prepare_locks.expires_at` TTL at commit (no durable reservation outlives a request after stage 4; in stage 3 it lives inside one transaction) |
| :93 | test_commit_is_idempotent_when_already_committed | second commit of a COMMITTED tx is a no-op success | SURVIVES: `tests/integration/test_payments_idempotency.py::test_payments_tx_id_returns_same_result` (:120; replay same result :179, effects unchanged :183) |
| :129-140 | test_abort_is_noop_when_already_committed | abort of COMMITTED leaves COMMITTED (+ leftover lock deleted) | ⚑ (Q2) SURVIVES: `tests/unit/test_admin_abort_tx.py::test_admin_abort_tx_rejects_committed_transaction_without_audit` (:244; 409 :268, state COMMITTED :270, no audit :279). Lock-row cleanup part: DROP: engine abort cleans `prepare_locks` |
| :221-235 | test_commit_updates_transaction_updated_at | `updated_at` moves on commit (feeds `committed_at` on the wire, `service.py:1309`, `:1388`) | TO WRITE (stage 4): a payment inserted directly `COMMITTED` reports `committed_at` ≥ `created_at`, non-null, on POST and GET (the Core-UPDATE bump itself is DROP: no state UPDATE exists) |
| :312-315, :316-319, :329-331 | test_commit_fails_closed_and_abort_recovers_invalid_persisted_flows [invalid, mixed] | malformed persisted flows → E010, abort recovers, no debt, lock gone | DROP: persisted `prepare_locks.effects` parser (no persisted flows after stage 4) |
| :421-424, :432-434 | test_commit_fails_closed_when_validated_flows_change_during_lock_wait | flows re-validated after lock wait → E010, stays PREPARED, no debt | DROP: re-validation of persisted effects under lock (same contract) |

#### `tests/integration/test_payment_engine_uow_retry_postgres.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :155, :158 | test_payment_engine_commit_retries_whole_uow_on_serialization_failure_postgres | after a (fake) 40001 at COMMIT the whole unit re-runs, not just `commit()` | ⚑ (P0.1; spec §4 «повтор из того же снимка») TO WRITE (stage 3): `pay()` re-runs the whole attempt on a fresh session after a REAL 40001 (count attempts; not DBAPI injection). Staged side SURVIVES: `test_p015_p1_money_replay_postgres.py::test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` (:530; replays :559, one commit :563, `["40001"]` :567) |
| :165, :172, :184-185 | same | COMMITTED, no locks, debt 7 exactly once | TO WRITE (stage 3): same new test asserts state + single debt effect; lock count DROP (stage 5, `prepare_locks`) |
| :296-297 | test_payment_engine_commit_retries_real_concurrent_debt_insert_postgres | engine retry budget attribute == 3 | DROP: engine `_retry_attempts` attribute (budget becomes `PAYMENT_TOTAL_TIMEOUT_SECONDS`, `service.py:779`) |
| :356-357 | same | waiter blocked on owner lock while holder holds it | DROP: engine owner-lock wait schedule; owner-before-row order is re-proved by T1903 races (spec «Один порядок локов») |
| :360-361 | same | both commits succeed | TO WRITE (stage 3): two concurrent API payments on one pair at SERIALIZABLE both COMMITTED |
| :370, :381-385, :392 | same | exactly one DB error considered: real 40001, verdict retryable, met at envelope INSERT | ⚑ (T1529 verdict-recording) TO WRITE (stage 3): record `service.py:108` classifier verdicts in the same race; assert SQLSTATE 40001 and verdict retryable (statement not pinned — first write is now `transactions` INSERT) |
| :398 | same | effects applied once per payment | TO WRITE (stage 3): covered by final debt of the same test |
| :430-434 | same | both COMMITTED, debt 6, locks 0, audit 2, limit unchanged | ⚑ TO WRITE (stage 3): same test (state, debt, audit, limit); `lock_count` DROP (stage 5, `prepare_locks`) |

#### `tests/unit/test_payment_engine_retry_savepoint_nocommit.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :68-77 | test_staged_serialization_failure_is_owned_by_outer_transaction | inside a savepoint a 40P01 propagates, no retry, caller not rolled back | ⚑ (spec «`execute()` пробрасывает») SURVIVES: `test_payment_prepare_error_taxonomy.py::test_staged_insert_serialization_failure_propagates_without_local_rollback` (:298; :327, :336-337) — itself REWRITE IN PLACE stage 3; plus TO WRITE (stage 3): `execute()` propagates 40001/40P01 from Book/delta without retry and without touching the caller transaction |
| :97-100 | test_unique_violation_retry_is_narrowed_to_commit_debt_business_key | `23505` on `uq_debts_debtor_creditor_equivalent` retryable only for the transaction owner | ⚑ (spec §4 predicate list) TO WRITE (stage 3): truth table of `service.py:108` (and `money_replay.py:104`) — debt business key retryable at the owner; `execute()` never retries |
| :118-119 | same | other constraint / other table `23505` fail-closed | ⚑ (spec §2 counter-check «`23505` на неименованном ограничении остаётся неповторяемым») TO WRITE (stage 3): same truth table |
| :133-142 | test_savepoint_uow_does_not_leak_local_lock_timeout | savepoint UoW issues only the advisory lock, no `SET LOCAL` leak | DROP: engine advisory-lock timeout plumbing (owner-lock timeout restoration is pinned by staged_multicall :418, which moves in stage 2) |
| :163-164 | test_lock_not_available_is_mapped_to_asyncio_timeout | 55P03 on advisory lock → `asyncio.TimeoutError` | DROP: advisory-lock timeout mapping (stage 5 contract; if stage 2 re-homes owner-lock timeout, add the mapping row to the stage-2 owner-lock test) |

#### `tests/unit/test_payment_engine_advisory_lock_key.py` (DROP, stage 4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :33-38 | test_segment_lock_key_is_deterministic_and_bigint_range | pair key deterministic, symmetric, distinct per pair/equivalent, BIGINT | DROP: pair advisory key arithmetic (namespace-less `pg_advisory_xact_lock(bigint)`), removed stage 5 |
| :90 | test_acquire_segment_advisory_locks_uses_sorted_key_order | pair keys acquired sorted | DROP: pair-lock acquisition order (stage 5) |

#### `tests/unit/test_payment_engine_advisory_locks_execute.py` (REWRITE, stages 2/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :86-91 | test_acquire_segment_advisory_locks_executes_pg_advisory_xact_lock_for_each_unique_segment | `SET LOCAL lock_timeout` before each pair lock | DROP: pair locks (stage 4/5, F1) |
| :101-105 | test_acquire_segment_advisory_lock_keys_deduplicates_and_sorts_globally | pair keys deduped + sorted | DROP: pair locks |
| :114-127 | test_tx_lock_key_is_stable_and_uses_domain_separate_from_segment_keys | tx-lock key stable, own namespace | DROP: tx advisory lock (stage 4; same-tx_id serialisation replaced by `transactions.tx_id` uniqueness + resolver, stage 3) |
| :139-152 | test_equivalent_owner_locks_are_deduplicated_sorted_and_domain_separated | owner keys stable, deduped, sorted, one namespace ≠ tx namespace | ⚑ (spec stage 2 «полный отсортированный набор owner-локов сохраняется») REWRITE IN PLACE (stage 2): call the `money_boundary` owner-lock function; tx-namespace half DROP at 4; whole row DROP at 5 only if T1908 removes the owner lock |
| :197-199 | test_tx_preflight_acquires_every_persisted_equivalent_before_tx_lock | owner set derived from persisted flows | DROP: persisted-lock preflight (stage 4) |
| :225-232 | test_abort_preflight_does_not_invent_owner_for_empty_or_malformed_locks | abort preflight on empty/malformed locks takes no owner | DROP: engine abort preflight (stage 4) |
| :255-256 | test_segment_lock_timeout_uses_one_decreasing_commit_budget | one decreasing lock budget across successive locks | REWRITE IN PLACE (stage 2): budget is shared by owner lock (`engine.py:175` calls `_set_local_advisory_lock_timeout`) — re-express on owner keys in `money_boundary`; DROP at 5 with the lock |
| :267 | test_advisory_lock_budget_matches_service_zero_default | zero settings → 5.0 s budget | REWRITE IN PLACE (stage 2): same, for the owner-lock budget in `money_boundary` |
| :310-324 | test_all_payment_transitions_acquire_owner_before_tx_and_first_tx_read [4] | owner lock precedes tx lock and the first row read on every transition | ⚑ (spec «Один порядок локов: owner до любой строки») TO WRITE (stage 2): the single `money_boundary` ordering function takes owner before any row read (unit) + T1903 real races vs PATCH/DELETE/hold; engine transition parametrisation DROP at 4 |
| :379-388 | test_abort_reacquires_preheld_tx_lock_only_after_outer_rollback_retry [2] | abort re-acquires locks only after an outer rollback retry | DROP: engine abort retry/lock re-acquisition (stage 4) |
| :439-444 | test_persisted_prepare_lock_parser_and_keys_share_validated_flows | parser and key derivation share validated flows | DROP: persisted-lock parser (stage 4) |
| :491-494 | test_persisted_prepare_lock_parser_fails_closed [9] | malformed persisted effects → E010 | DROP: persisted-lock parser (stage 4) |
| :545-552 | test_commit_acquires_keys_derived_from_loaded_prepare_locks | commit locks owner→tx→segment from loaded locks | DROP: engine commit lock derivation (stage 4) |

#### `tests/integration/test_payment_commit_advisory_locks_postgres.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :360-363, :373-377 | test_prepare_reservation_blocks_concurrent_commit_on_same_segment_postgres | commit waits on owner lock while a prepare holds the pair; same keys | DROP: owner/pair lock wait at READ COMMITTED (spec §4: RC is not evidence) |
| :371-372 | same | second payment over already-consumed capacity refused E002 | ⚑ (capacity refusal; spec-named bottleneck) SURVIVES: `test_concurrent_prepare_routes_bottleneck_postgres.py::test_concurrent_payments_shared_bottleneck_commit_once_postgres` (:20; E002 :197, debt :236) — spec rewrites it in stage 5 keeping final asserts |
| :419, :421-422 | same | holder COMMITTED, debt 8, no locks | SURVIVES: same bottleneck node (:232-236); lock count DROP (stage 5) |
| :420 | same | refused waiter left `NEW` | DROP: durable `NEW` of a refused prepare (stage 3: no durable intermediate state; the stage-4 row is `ABORTED` or none per T1902) |
| :536-540, :555-558 | test_concurrent_same_transaction_commit_applies_effects_once_postgres | waiter parks on advisory lock; one rollback, two preflights | DROP: engine tx/pair-lock wait and retry-wrapper internals |
| :550-554 | same | duplicate concurrent commit of one tx_id is idempotent | ⚑ (T1529) TO WRITE (stage 3): two concurrent `pay()` with the same signed tx_id at SERIALIZABLE — loser returns the winner's stored result (or 409 per resolver), no exception leaks (spec «Идентичность `tx_id`»; host candidate: `test_p015_t1523_in_progress_and_insert_race_postgres.py::test_the_insert_race_at_serializable_leaves_one_payment_postgres` :343 after its own stage-3 rewrite) |
| :566-587 | same | the collision is 40001/23505 on envelope INSERT, verdict retryable | ⚑ (T1529) DROP: engine commit-phase envelope race — unreachable after stage 3 (the first collision is `transactions.tx_id`); classifier truth table for envelope constraints moves to t1529 unit REWRITE |
| :636-643 | same | one COMMITTED row, no error, debt 8, no reverse debt, audit 1, limit unchanged | ⚑ (T1529) TO WRITE (stage 3): same new test asserts these (money, identity, audit); `remaining_locks` DROP (stage 5) |
| :690-705 | test_concurrent_duplicate_commit_is_idempotent_with_journal_history_postgres | premise: 2000 history rows, completion UPDATE is not a Seq Scan | ⚑ (T1529) DROP: SSI plan premise of the engine commit-phase envelope race (unreachable, see above). Stage-3 identity test should still run on a journal with history (TO WRITE note) |
| :716-739 | same | waiter parked, holder first, op commit, `23505` on envelope identity constraint | ⚑ (T1529) DROP: same removed race |
| :742-750 | same | verdict retryable, waiter idempotent, one rollback, two preflights | ⚑ (T1529) TO WRITE (stage 3): idempotent outcome in the stage-3 identity race; wrapper counters DROP |
| :753-760 | same | COMMITTED, debt 8, no reverse, no locks, audit 1, exactly ONE envelope `COMPLETED` | ⚑ (T1529, 018 journal contract) TO WRITE (stage 3): same stage-3 identity race asserts one envelope `["COMPLETED"]`, one debt, one audit; locks DROP (stage 5) |
| :1005-1007, :1016 | test_concurrent_commit_and_abort_share_segment_lock_protocol_postgres | abort waits on tx lock behind commit | DROP: tx-lock protocol between commit and abort (stage 4) |
| :1014-1015, :1050-1053 | same | a racing abort does not undo a commit: COMMITTED, debt 8, no error | ⚑ (Q2) SURVIVES: `tests/unit/test_admin_abort_tx.py::test_admin_abort_tx_rejects_committed_transaction_without_audit` (:244; :268, :270); locks DROP (stage 5) |
| :1131-1133, :1140-1144 | test_duplicate_prepare_cannot_resurrect_transaction_during_commit_postgres | duplicate prepare holds tx lock; commit waits | DROP: engine tx-lock / durable-PREPARED resurrection guard |
| :1173-1175 | same | after duplicate prepare + commit: COMMITTED, debt 8 once, no locks | TO WRITE (stage 3): covered by the stage-3 identity race (duplicate tx_id never applies twice); locks DROP (stage 5) |
| :1238-1239, :1246-1247 | test_new_prepare_cannot_resurrect_transaction_during_abort_postgres | abort waits behind an in-flight prepare | DROP: prepare/abort race over durable `NEW` (neither exists after stage 4) |
| :1274-1277 | same | ABORTED with reason, no debt, no locks | ⚑ (Q2) DROP: admin/recovery abort of a live payment (no live payment after stage 4); Q2 compatibility (unknown→404, ABORTED→idempotent `aborted`+audit) SURVIVES: `test_admin_abort_tx.py::test_admin_abort_tx_404` (:27, :30) and `::test_admin_abort_tx_repeats_aborted_transaction_idempotently` (:88, :114) |

#### `tests/integration/test_payment_pair_advisory_locks_postgres.py` (DROP, stage 4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :47-54 | test_reverse_segments_contend_on_one_advisory_resource_postgres | reverse direction of one pair waits on the same key → 55P03 | DROP: reciprocal-pair advisory identity (stage 5). The protected effect (opposite directions of one pair cannot both land) is re-proved by T1908 payment/inject schedules on SERIALIZABLE (spec §2 experiments) |

#### `tests/integration/test_payment_engine_audit_conflict_postgres.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :205 | test_audit_serialization_failure_retries_before_transaction_is_poisoned | a real 40001 in the audit block is not degraded into 25P02 | ⚑ (T401/004; AGENTS §9 «проглоченный 40001 отравляет транзакцию») TO WRITE (stage 3): real 40001 provoked inside the payment's audit write under `execute()` reaches `pay()` as 40001 (not 25P02) and `pay()` re-runs to COMMITTED |
| :208 | same | premise: competitor committed within 10 s (no lock hides the conflict) | TO WRITE (stage 3): same premise in the new test |
| :219 | same | retry logged with `pgcode=40001` | TO WRITE (stage 3): new test asserts `pay()` retry log/counter with SQLSTATE 40001 (log event name changes from `payment.uow_retry op=commit`) |
| :220, :226 | same | payment COMMITTED after the retry | TO WRITE (stage 3): same |
| :227 | same | non-DB audit failure after the retry stays best-effort (checkpoint called ≥4 times) | TO WRITE (stage 3): same test, third call raises `ValueError`, payment still COMMITTED |

#### `tests/integration/test_payment_abort_has_error_code.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :101-105 | test_payment_aborted_result_has_error_code | GET of an ABORTED payment returns its stored typed code (E007), never "ERROR" | SURVIVES: `test_payment_prepare_error_taxonomy.py::test_prepare_preserves_typed_client_error_in_http_and_transaction` (:374; GET :428, :430) — REWRITE IN PLACE in stage 3/4 but assertion kept; and `test_p015_t1523_replay_after_a_hold_or_an_abort.py:252` (E007 on replay :314) — that node patches `PaymentEngine.prepare` (:268, :276) and needs its own stage-4 rewrite (other group) |

#### `tests/integration/test_payment_prepare_capacity_policy.py` (REWRITE, stages 4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :149 | test_segment_capacity_policy_counts_only_matching_valid_reservations | capacity = limit 100 − debt 30 + reverse debt 10 = 80 | ⚑ (capacity formula, AGENTS §8) REWRITE IN PLACE (stage 4): read capacity through the direct path's capacity function (engine private `_get_segment_capacity_and_reserved_usage` is gone) |
| :150 | same | only same-equivalent, same-direction, valid reservations count (7 of 7/50/60/999) | DROP: reservation accounting (stage 5, `engine.py:850`, `router.py:212`); until then REWRITE IN PLACE (stage 4) |
| :187-190 | test_single_and_multipath_prepare_apply_the_same_persisted_reservation_policy | single and multipath refusals are identical, available/needed/reserved | ⚑ (routing refusal) REWRITE IN PLACE (stage 4): single- and multipath direct execution give identical E002 details; `reserved` value/key changes in stage 5 (F10) |
| :215-217 | test_multipath_prepare_keeps_local_reservations_in_addition_to_persisted_ones | two routes over one segment: own first route counts (reserved 47 = 7 + 40) | ⚑ (AGENTS §8 «routing не может расходовать больше capacity») REWRITE IN PLACE (stage 4); stage 5: reserved = 40 (own route only) — the own-route accounting must survive reservation removal |

Setup to change in stage 4: `_payment_transaction` seeds `PAYMENT` rows in state `ROUTED` (:27, used :71, :164-166, :201) — refused by CHECK `030`.

#### `tests/integration/test_payment_prepare_error_taxonomy.py` (REWRITE, stages 3/4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :194-199 | test_retryable_database_failure_uses_e008_at_service_boundary [prepare, commit] | 40001 → 409/E008 `{"retryable": true, "conflict_kind": "database_concurrency"}` | ⚑ (09:343-350; wire) REWRITE IN PLACE (stage 3): patch point becomes the in-transaction step; `commit` param merges (no separate commit phase); stage 4 patch target leaves `service.engine` |
| :200-208 | same | the retryable conflict is stored ABORTED with E008 | ⚑ (spec behavior change 1; repro `test_p019_retryable_conflict_is_not_stored_aborted_postgres.py`) REWRITE IN PLACE (stage 3): inverted — no row after the conflict; a replay of the same tx_id executes |
| :236, :248-251 | test_insert_serialization_failure_is_typed_before_any_tx_is_persisted [public, internal] | 40001 at the NEW insert commit → E008, no row | REWRITE IN PLACE (stage 3): the NEW commit disappears; inject at the single COMMIT (patch of `db_session.commit` still reaches it) — no row stays true |
| :286-294 | test_http_insert_serialization_failure_returns_declared_conflict | HTTP 409 with exact E008 body | ⚑ (wire) REWRITE IN PLACE (stage 3): same, one commit |
| :327, :336-337 | test_staged_insert_serialization_failure_propagates_without_local_rollback | staged 40001 propagates, no local rollback, caller still in transaction | ⚑ (spec «`execute()` пробрасывает») REWRITE IN PLACE (stage 3): entry becomes `execute()`; assertions unchanged |
| :409-411 | test_prepare_preserves_typed_client_error_in_http_and_transaction [E001, E002, E008] | typed refusal keeps status and error body; single vs multipath entry | ⚑ (Q1) REWRITE IN PLACE (stage 4): patch target `PaymentEngine.prepare/prepare_routes` (:95-96) → the direct path's capacity/route recheck |
| :413-417 | same | stored ABORTED with the same error | ⚑ (Q1, FORK-4 row 1) REWRITE IN PLACE (stage 3 classification, 4 patch target) |
| :428-432 | same | GET and retry return the stored error; prepare not called again | ⚑ (Q1; T1523 replay) REWRITE IN PLACE (stage 4) |
| :475-477 | test_operational_prepare_error_is_sanitized_everywhere [1, 2] | OperationalError → 500 with safe E010 body | REWRITE IN PLACE (stage 4 patch target) |
| :482-505 | same | stored ABORTED E010; GET/retry/list(`status=ABORTED`) replay safe error | REWRITE IN PLACE (stage 3/4) — **expectation to be fixed by T1902**: FORK-4 table has no row for an internal (E010) failure (F4) |
| :516-524 | same | sentinel never exposed; `event=payment.prepare_failed` log w/o exc_info | REWRITE IN PLACE (stage 4): log event name is phase-named — decide the stage-4 name (F9) |
| :559-568 | test_typed_server_prepare_error_is_sanitized | typed server error → safe 500, stored ABORTED safe E010, no sentinel | REWRITE IN PLACE (stage 4) — same F4 caveat |
| :614-616, :630-635 | test_public_prepare_failure_rolls_back_session_before_abort | safe 500; stored ABORTED E010 | REWRITE IN PLACE (stage 3): ABORTED written in the outer transaction after the operation savepoint is rolled back |
| :617-626 | same | order rollback→abort; `engine.abort(...commit=True)` call shape | order: REWRITE IN PLACE (stage 3: savepoint rollback precedes the ABORTED write); call shape: DROP: `engine.abort` kwargs (stage 4) |
| :668-677 | test_direct_prepare_reraises_same_typed_error_after_durable_abort | the same exception object is re-raised after a durable ABORTED E008 | ⚑ (Q1) REWRITE IN PLACE (stage 3/4 patch target) — API path only (staged returns a structural result, :1521 row) |
| :718-723 | test_prepare_rollback_failure_does_not_abort_poisoned_session | failed rollback → safe 500, NO abort write | ⚑ (spec «отдельная короткая транзакция только после подтверждённого отката») REWRITE IN PLACE (stage 3) |
| :766-772 | test_prepare_abort_failure_replaces_original_client_error_with_safe_500 | failed ABORTED write → safe 500, original text hidden | REWRITE IN PLACE (stage 3/4: `engine.abort` patch target → the ABORTED-write step) |
| :823-831 | test_operational_commit_error_is_sanitized_in_response_and_transaction | operational failure at commit phase → safe 500, stored ABORTED E010 | REWRITE IN PLACE (stage 4): patch `Book.post`/`check_payment_delta` inside the savepoint (failure before COMMIT ⇒ definitive); failure AT the outer COMMIT is the FORK-4 «unknown» row → TO WRITE (stage 3): nothing written, no ABORTED |
| :832-839 | same | `event=payment.commit_failed` log safe | REWRITE IN PLACE (stage 4; F9 event name) |
| :893-912 | test_typed_commit_error_is_reraised_only_after_durable_abort | same exception, stored ABORTED E008; order + abort kwargs | stored row + identity: REWRITE IN PLACE (stage 4); order/kwargs: DROP: `engine.abort` call shape |
| :988-1014 | test_commit_cleanup_failure_is_safe_and_ordered [2×2] | cleanup failure → safe 500 E010; rollback failure ⇒ no abort; abort kwargs | REWRITE IN PLACE (stage 4) for safe 500 and "no write after failed rollback"; kwargs DROP |
| :1046-1051 | test_prepare_cancellation_preserves_cancel_and_durably_aborts | cancel re-raised; stored ABORTED E007 "Payment cancelled" | REWRITE IN PLACE (stage 3) — **expectation set by T1902**: cancellation is not a FORK-4 row (F4) |
| :1108-1113 | test_cancellation_at_other_payment_phases_has_terminal_state [insert, commit] | cancel after NEW commit / at commit → ABORTED E007 | `insert`: REWRITE IN PLACE (stage 3) — with one commit, a cancel after COMMIT returns means the payment IS committed; expectation inverts to COMMITTED + money moved; `commit`: REWRITE IN PLACE (stage 4 patch target; expectation per T1902/F4) |
| :1163 | test_staged_prepare_cancellation_aborts_before_outer_rollback | ABORTED observed before the caller savepoint rolls back | DROP: `engine.abort` observation hook (stage 4) — the stage-3 staged path returns a structural result, not a write-then-raise |
| :1164-1167 | same | caller savepoint rollback leaves no row | REWRITE IN PLACE (stage 3): unchanged for a cancellation (no structural result is produced) |
| :1248-1249 | test_repeated_cancellation_during_recovery_read_still_aborts [cancellation, timeout] | ABORTED written exactly once despite a second cancel during the terminator's read | ⚑ (FORK-4 terminal timeout with established rollback) REWRITE IN PLACE (stage 3): `PREPARE_TIMEOUT_SECONDS` (:1230) → the single payment budget; terminators `service.py:1206`, `:1247` change |
| :1303-1308 | test_timeout_rollback_failure_is_safe_without_read_or_abort | failed rollback after timeout → safe 500, no read, no ABORTED | ⚑ (FORK-4 «отсутствующая строка при неразрешённом коммите …»; no ABORTED on unknown) REWRITE IN PLACE (stage 3) |
| :1369-1382 | test_timeout_recovery_read_failure_is_safe_without_abort | read failure after timeout → safe 500, no ABORTED, safe log `payment.timeout_recovery_read_failed` | ⚑ (FORK-4 unknown outcome) REWRITE IN PLACE (stage 3) |
| :1441-1446, :1455-1462 | test_timeout_abort_failure_is_safe_after_recovery_read | ABORTED write failure after timeout → safe 500; safe log `payment.timeout_abort_failed` | REWRITE IN PLACE (stage 3) |
| :1447-1454 | same | `engine.abort("Payment timeout", E007, commit=True)` | ⚑ (FORK-4 row 3: timeout with established rollback → ABORTED E007) REWRITE IN PLACE (stage 3: assert the ABORTED E007 row/attempt, not the engine kwargs) |
| :1511-1513 | test_staged_timeout_abort_failure_has_symmetric_safe_log | staged: safe log on abort failure | REWRITE IN PLACE (stage 3) |
| :1514-1517 | same | staged timeout leaves no row after caller savepoint | REWRITE IN PLACE (stage 3) — expectation per T1902 staged characterization (structural ABORTED vs none for timeout; F4) |
| :1569-1574 | test_staged_generic_prepare_failure_is_safe_without_session_commit_or_rollback | safe E010; staged path neither commits nor rolls back the caller | ⚑ (spec staged contract) REWRITE IN PLACE (stage 3): `PaymentResult(ABORTED, error=E010)` returned instead of raised; `session_calls == []` stays |
| :1575-1584 | same | ABORTED E010 row inside caller transaction, no sentinel | ⚑ (Q1 staged; repro `test_p019_staged_refusal_is_durable_postgres.py`) REWRITE IN PLACE (stage 3): row durable after the executor savepoint (`real_payments_executor.py:421`) and caller commit |

#### `tests/integration/test_payment_staged_multicall_postgres.py` (REWRITE, stages 2/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :239, :245 | test_staged_multicall_batches_do_not_exhaust_retry_on_retained_locks_postgres | `engine.commit(commit=False)` succeeds for both calls of a batch | DROP: engine staged commit of seeded `PREPARED` (stage 4) |
| :273-275 | same | exactly one batch fails, with a real 40001 | ⚑ (spec §2 money_replay :530) SURVIVES: `test_p015_p1_money_replay_postgres.py::test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` (:530; `sqlstates == ["40001"]` :567) |
| :279-280 | same | retry of the whole batch in a fresh outer transaction succeeds | ⚑ SURVIVES: same node (replays == 1 :559, commits == 1 :563) |
| :289, :300-311 | same | all COMMITTED; debts A→B 4, B→C 4 | ⚑ SURVIVES: same node (debts :581, COMMITTED :589) |
| :312-319 | same | no `prepare_locks` left | DROP: `prepare_locks` (stage 5) |
| :320-328 | same | one PAYMENT audit row per payment | TO WRITE (stage 4): staged/direct path writes one `IntegrityAuditLog` PAYMENT row per committed payment (no staged audit assert outside rewritten files) |
| :336 | same | trust limits untouched | TO WRITE (stage 4): same new test (cheap) |
| :391-392, :406 | test_staged_owner_sorts_multi_equivalent_sets_without_global_serialization_postgres | waiter on {A,B} blocks behind holder of {B,A} | ⚑ (stage-2 sorted owner set) REWRITE IN PLACE (stage 2): `money_boundary` owner function; stage 5 per T1908 |
| :395-400 | same | disjoint equivalent is not serialised | REWRITE IN PLACE (stage 2); stage 5 per T1908 |
| :437, :444-455 | test_staged_owner_restores_outer_transaction_lock_timeout_postgres | owner acquisition restores caller `lock_timeout`; later statements wait unbounded | REWRITE IN PLACE (stage 2); DROP at 5 if the owner lock goes |

#### `tests/integration/test_prepare_locks_tx_id_fk_postgres.py` (DROP, stage 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :154-156 | test_prepare_locks_tx_id_fk_exists_and_blocks_orphans_postgres | orphan reservation refused by FK | DROP: `prepare_locks` table (migration `031`) |
| :164-176 | same | FK `tx_id → transactions.tx_id` and index `ix_prepare_locks_participant_expires_at` exist | TO WRITE (stage 5): `031` downgrade restores the FULL empty reservation schema (table, this FK, this index, other constraints) — FORK-3 |

#### `tests/integration/test_p017_uow_retry_after_a_real_40001_postgres.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :87-88 | test_a_failed_rollback_after_a_real_40001_stops_the_retry | premise: real 40001, classified retryable | TO WRITE (stage 3): `pay()` classifier (`service.py:108`) on a REAL 40001 |
| :109-118 | same | failed rollback ⇒ no re-run; original error surfaces with the rollback failure as cause | ⚑ (017 S2a contract, T1525 origin) TO WRITE (stage 3): `pay()` retry loop — an attempt whose session could not be rolled back/closed is not re-run on it; original error preserved; no ABORTED written (FORK-4 unknown) |
| :137-138 | test_the_same_40001_is_retried_when_the_rollback_succeeds | control: one retry then success | ⚑ TO WRITE (stage 3): same test's control — retried once on a fresh session |

#### `tests/unit/test_apply_flow_retry_on_stale.py` (REWRITE, stages 3/4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :102 | test_apply_flow_retries_on_stale_data | a stale debt version is silently retried in place and the flow lands (90 → 80) | ⚑ (FORK-1; `09:237` L1 withdrawn) REWRITE IN PLACE (stage 3): expect `RetryablePaymentConflictException` from `Book`, debt stays 90, no in-place retry; stage 4: call `Book` (not `PaymentEngine._apply_flow`, :7/:87/:95). Plus TO WRITE (stage 3): negative controls — other ORM errors are not translated (spec §4) |

#### `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py` (REWRITE, stages 3/4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :91 | test_t1529_an_envelope_identity_collision_during_commit_is_retryable [2] | envelope identity `23505` is retryable at the transaction owner | ⚑ (T1529; spec §4 predicate) REWRITE IN PLACE (stage 3): assert on `service.py:108` and `money_replay.py:104` classifiers (both owners) |
| :116 | test_t1529_the_identity_set_is_the_envelope_identity_the_schema_declares | retry predicate's constraint names == `debt_operations` unique constraints | ⚑ (T1529 anti-drift) SURVIVES: this node unchanged (imports only `book` + `journal_tables`) |
| :179-181 [other_constraint, table_prefix] | test_t1529_everything_else_still_fails_closed | pk collision / look-alike table → not retryable | ⚑ (spec §2 counter-check) REWRITE IN PLACE (stage 3) on the owners' classifiers |
| :179-181 [caller_owned_unit_of_work] | same | inside a caller-owned transaction (savepoint) never retried | ⚑ REWRITE IN PLACE (stage 3): `execute()` propagates; only `pay()`/money-phase owner retries |
| :179-181 [wrong_operation, abort] | same | `op=prepare`/`op=abort` not retryable | DROP: engine phase dimension of the classifier (no phases after stage 3/4) |
| :212-214 | test_t1529_the_retry_rolls_back_once_and_re_runs_the_unit_of_work | retry re-runs on a rolled-back transaction | REWRITE IN PLACE (stage 3): `pay()` re-runs on a fresh session (assert new session per attempt) |
| :250-252 | test_t1529_a_bounded_budget_keeps_a_permanent_duplicate_from_looping | budget exhausts, ORIGINAL 23505 re-raised after 3 attempts | ⚑ REWRITE IN PLACE (stage 3): exhaustion → `409/E008 retryable` per spec (`service.py:779` budget), not the raw 23505 — expectation changes |

#### `tests/integration/test_payment_idempotency_postgres.py` (REWRITE, stages 3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :125 | test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres | premise: both sessions READ COMMITTED | REWRITE IN PLACE (stage 5): SERIALIZABLE enforced — RC sessions are refused before the first write (repro `test_p019_money_writers_refuse_non_serializable_postgres.py`) |
| :174 | same | winner parked after its NEW commit, before prepare | REWRITE IN PLACE (stage 3): barrier after the `Transaction` insert inside the one transaction (NEW is no longer committed); stage 4: hook not on `engine.prepare` |
| :178-181 | same | loser gets 409/E008 "in progress" | ⚑ (T1523 matrix cell 3) REWRITE IN PLACE (stage 3): loser waits on the `transactions.tx_id` index and returns the winner's stored COMMITTED result (or the resolver's 409) — "in progress" disappears (spec «Идентичность») |
| :185-186 | same | winner COMMITTED | REWRITE IN PLACE (stage 3) |
| :218-220, :222-223 | same | one row, COMMITTED, debt 10, one audit `verification_passed` | ⚑ REWRITE IN PLACE (stage 3) — unchanged asserts |
| :221 | same | no `prepare_locks` left | DROP: `prepare_locks` (stage 5) |
| :225-227 | same | exactly one `payment.received` publication | ⚑ (publication) REWRITE IN PLACE (stage 3) — unchanged |

#### `tests/integration/test_payment_inverse_multisegment_postgres.py` (REWRITE, stages 4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :282-288 | test_inverse_multisegment_commits_serialize_and_preserve_invariants_postgres [forward, reverse] | inverse route parks on the holder's pair locks | REWRITE IN PLACE (stage 4 only if pair locks survive stage 4 — F1); stage 5: DROP: pair-lock wait, replaced by SERIALIZABLE conflict evidence (40001 + owner retry count, T1908) |
| :295-297 | same | both commits succeed | REWRITE IN PLACE (stage 4): both via `pay()`/`execute()` from seeded state without `PREPARED`/`PrepareLock` (:96, :114, :121-166) |
| :316-319 | same | both COMMITTED | ⚑ REWRITE IN PLACE (stage 4) |
| :331-342 | same | debts net to A→B 1, B→C 1 | ⚑ (spec §3 selector; money) REWRITE IN PLACE (stage 4) — unchanged |
| :343-352 | same | no `prepare_locks` left | DROP: `prepare_locks` (stage 5) |
| :353-363 | same | two PAYMENT audit rows | ⚑ REWRITE IN PLACE (stage 4) |
| :371 | same | four trust limits unchanged | REWRITE IN PLACE (stage 4) |

#### `tests/unit/test_payment_staged_post_commit.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :88-89, :98-101 | test_staged_payment_rollback_has_no_rows_or_effects | savepoint rollback: no tx, no debt, no publication, cache untouched | ⚑ (publication) SURVIVES: this node unchanged (no engine dependency) |
| :154-160 | test_staged_payment_effects_apply_once_after_outer_commit | nothing published/invalidated/counted before outer commit | ⚑ REWRITE IN PLACE (stage 4): patch target `app.core.payments.engine.PAYMENT_EVENTS_TOTAL` (:122) goes |
| :163-164 | same | `apply_once` True then False | REWRITE IN PLACE (stage 4) — unchanged |
| :167-169 | same | one publication, cache invalidated once | ⚑ REWRITE IN PLACE (stage 4) — unchanged |
| :170-176 | same | metrics `create`/`prepare`/`commit` success | REWRITE IN PLACE (stage 4): `prepare`/`commit` labels are emitted in `engine.py` (:1039, :1221, :1705); decide the stage-4 label set (F9) |
| :181-182 | same | one tx, one debt | ⚑ REWRITE IN PLACE (stage 4) — unchanged |
| :192-195 | same | replay of the same tx_id: no effects, no second publication | ⚑ (identity replay, publication) REWRITE IN PLACE (stage 4) — unchanged |
| :226-229 | test_committed_payment_result_does_not_read_expired_participants | result pids/equivalent read without lazy load after expire | REWRITE IN PLACE (stage 4): hook `service.engine.commit` (:209-216) → expire after the direct `Book` step |
| :262-264 | test_staged_payment_cancellation_rolls_back_without_effects | cancellation under savepoint: no tx, no debt, no publication | ⚑ SURVIVES: this node unchanged |

REWRITE (setup-only) files — setup lines only (every assertion stays in place):

- `tests/unit/test_p017_default_tier_can_see_the_lock.py` — stage 2: `:169` `engine_module` import, `:170` `PaymentEngine`, `:202` `_EQUIVALENT_OWNER_LOCK_NAMESPACE`, `:203` `_equivalent_owner_lock_key`, `:210`, `:224` `_acquire_equivalent_owner_locks` → `money_boundary`. Stage 5: file DROP (contract: owner advisory lock) iff T1908 removes the lock; asserts `:208`, `:213`, `:225` are measurer checks (AGENTS §15), not money effects.
- `tests/unit/test_p015_t1522_payment_delta_drift_must_be_exact.py` — stage 2: `:38` import, `:93`, `:120`, `:142` `PaymentEngine(...).check_payment_delta`, `:166` `_DELTA_DRIFT_TOLERANCE` → `money_boundary`. ⚑ T1522 (spec §2/§3).
- `tests/unit/test_payment_delta_check.py` — stage 2: `:7` import, `:59` call → `money_boundary`.
- `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` — stage 2: `:78` `import app.core.payments.engine as engine_module`, `:483` patch target of `_DELTA_DRIFT_TOLERANCE` → `money_boundary`; docstring anchors `:20`, `:120` (`engine.py:1545`, stale since before 019).

### 5.2. G2 — Состояния, recovery, идемпотентность, повтор денежной фазы, помощники

#### ⚑ Проверка якорей спеки (Verification plan §2)

| spec anchor | line holds | test | correct? |
|---|---|---|---|
| `test_p015_p1_money_replay_postgres.py:530` | `async def` line (decorator `:529`) | `test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` | yes — real 40001 → phase replay → one commit |
| `...money_replay_postgres.py:703` | `async def` (decorator `:702`) | `test_the_staged_prefix_of_a_conflicted_attempt_is_rolled_back` | yes |
| `...money_replay_postgres.py:751` | `async def` (decorator `:750`) | `test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget` | yes |
| `...money_replay_postgres.py:796` | `async def` (decorator `:795`) | `test_a_tail_failure_after_the_money_commit_never_replays_money` | yes |
| `test_p015_t1523_replay_after_a_hold_or_an_abort.py:252` | `async def` (decorator `:251`) | `test_a_tx_id_whose_payment_aborted_replays_the_stored_aborted_result` (T1523 cell 2) | yes — but its abort is produced by patching `PaymentEngine.prepare` + `PREPARE_TIMEOUT_SECONDS` (`:268-278`); both disappear (stage 3/4), see (B) |

Not named by the spec but in the same module: `:516` `test_the_stand_is_serializable` and `:619` `test_a_genuine_40001_is_raised_by_the_staged_write_on_this_backend[same-row|write-skew]` — both are stand controls for the four anchors and must survive with them.

T1523 matrix cells in this group: 1 & 2 (`replay_after_a_hold_or_an_abort`), 3 & 5 (`in_progress_and_insert_race`), 8 (`restart_after_commit` + child). Cell 3 is reformulated by spec «Идентичность `tx_id`»; cell 5 is reformulated by the same paragraph de facto (its premise — a committed `NEW` row before the loser inserts — cannot exist after stage 3), which the spec does not say.

#### `tests/unit/test_recovery_cleanup.py` (DROP, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:57-60` | `test_recovery_stops_before_stale_phase_when_session_cannot_rollback` | batch stops, stale phase skipped, two log events when rollback unusable | DROP: recovery batch sequencing (recovery.py deleted, 4) |
| `:108-111` | `test_cleanup_expired_prepare_locks_aborts_related_tx_and_deletes_locks` | cleanup counters | DROP: recovery TTL internals |
| `:117` | same | expired `prepare_locks` rows deleted | DROP: prepare_locks row counts |
| `:121-122` | same | stuck `PREPARED` payment → `ABORTED` "Prepare lock expired" | DROP: durable PREPARED no longer exists; replaced by TO WRITE (4): migration `030` refuses upgrade while any PAYMENT is non-terminal or any `prepare_locks` row exists, and succeeds on a drained DB (spec «Перевод») |
| `:161-163`, `:166-167`, `:172` | `test_abort_stale_payment_transactions_aborts_old_active_tx` | stale PREPARED → ABORTED "Recovered stale payment transaction", locks gone, counters | DROP: stale-payment reaper; same TO WRITE (4) as above covers the drain precondition |
| `:251`, `:255-256`, `:260-265`, `:268`, `:270`, `:274` | `test_recovery_iteration_preserves_expired_lock_progress_when_one_abort_fails` | per-item progress, log counters, second iteration converges | DROP: recovery batch progress |
| `:340-341`, `:345-346`, `:358-362`, `:365`, `:367` | `test_recovery_iteration_preserves_stale_abort_progress_when_one_abort_fails` | same for stale phase; one rollback | DROP: recovery batch progress |
| `:401-403` | `test_stale_recovery_counts_only_new_abort_outcomes` | outcome counters | DROP: recovery counters |
| `:466-470`, `:474` | `test_expired_lock_cleanup_counts_observed_resolved_ids_for_terminal_outcomes` | counters, DELETE count, locks gone | DROP: recovery counters / prepare_locks |
| `:504-505` | `test_recovery_item_rollback_failure_escalates_the_batch` | rollback failure escalates | DROP: recovery error escalation |

#### `tests/unit/test_admin_abort_tx.py` (REWRITE, stage 4) — spec §3: rewritten under Q2, not deleted

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:23` | `test_admin_abort_tx_requires_admin_token` | 403 without token | SURVIVES: in place (unaffected) |
| `:30` | `test_admin_abort_tx_404` | unknown tx_id → 404 | SURVIVES: in place (Q2: unknown → 404) |
| `:62`, `:64` | `test_admin_abort_tx_aborts_and_audits` | live `WAITING` PAYMENT → 200 `aborted` | DROP: aborting live (non-terminal) payment work — contract removed by stage 4 (no active work; seed refused by `030`) |
| `:65-66` | same | metric `abort/success` +1 | DROP: same removed contract (no `success` outcome exists any more) |
| `:70-72` | same | row → ABORTED with operator reason | DROP: same |
| `:84` | same | audit row `admin.transactions.abort` | TO WRITE (4): audit row on the ABORTED-repeat compatibility path (Q2 "ABORTED — идемпотентный `aborted` с записью аудита"; today no test asserts the audit on that path) |
| `:114-115` | `test_admin_abort_tx_repeats_aborted_transaction_idempotently` | ABORTED → 200 `aborted` | ⚑ Q2 — SURVIVES: in place (must pass without `PaymentEngine`) |
| `:116-117` | same | metric `already_aborted` +1, `success` unchanged | REWRITE IN PLACE (4): keep if the metric label survives (spec silent — see C) |
| `:119-124` | same | stored ABORTED with `error=None` gets `{E010, reason}` | REWRITE IN PLACE (4) — pins today's `engine.abort` fill-in (`engine.py:2007-2028`: existing code/message win, only a missing error is filled). Add TO WRITE (4): an ABORTED row WITH a stored error (e.g. `E007 "Payment timeout"`) keeps it byte-identical, so the payer's stored-ABORTED replay (⚑ T1523 cell 2) is not rewritten by an operator |
| `:171`, `:173-175`, `:177` | `test_admin_abort_tx_uses_lock_protected_already_aborted_metric` | concurrent abort wins under tx advisory lock → loser reports `already_aborted` | DROP: tx advisory lock race of live-payment abort (`PaymentEngine._acquire_tx_advisory_lock`) — no live state to race over |
| `:226-231`, `:240` | `test_admin_abort_tx_bounds_staged_owner_wait_and_rolls_back` | engine abort wait bounded → 504/E007, row untouched, no audit | DROP: bounded wait on `PaymentEngine.abort` owner locks (engine deleted; compatibility path takes no money lock) |
| `:268`, `:270`, `:279` | `test_admin_abort_tx_rejects_committed_transaction_without_audit` | COMMITTED → 409, unchanged, no audit | ⚑ Q2 — SURVIVES: in place |
| `:307`, `:317-318`, `:327` | `test_admin_abort_tx_rolls_back_when_audit_flush_fails` | audit flush failure rolls back the abort, no audit | REWRITE IN PLACE (4): seed `ABORTED` with `error=None` instead of `WAITING`; assert error still `None`, no audit |
| `:366`, `:374-377`, `:386` (param `WAITING`) | `test_admin_abort_tx_rolls_back_when_outer_commit_fails` | outer commit failure rolls back, metrics untouched, no audit | DROP (param `WAITING`): live-payment abort removed |
| same lines (param `ABORTED`) | same | same for ABORTED | SURVIVES: in place (param stays) |

#### `tests/integration/test_p015_t1523_in_progress_and_insert_race_postgres.py` (REWRITE, stages 3, 4, 5) — ⚑ T1523 matrix

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:184` | helper `session_at_application_isolation` | stand runs at app isolation (T1549) | SURVIVES: in place |
| `:272` | `test_a_second_request_while_the_first_is_prepared_is_refused_in_progress_postgres` (cell 3) | premise: first payment durably `PREPARED` on a 3rd connection | ⚑ REWRITE IN PLACE (3) per spec «Идентичность»: premise becomes "winner holds its uncommitted row; observer sees NO row for tx_id; second request is waiting on the `transactions.tx_id` unique index (`pg_locks`)" — barrier moves from `engine.commit` (`:238-245`) to before `pay()`'s outer COMMIT |
| `:273-274` | same | no envelope, no debt while held | SURVIVES: in place (still true, now because nothing is committed) |
| `:296` | same | second request did not route (refusal from lookup) | REWRITE IN PLACE (3): discriminator inverts — the second request's lookup misses, it routes and meets the index; assert it reached the index wait instead |
| `:300-306` | same | 409/E008 "in progress", not retryable | ⚑ REWRITE IN PLACE (3): expected answer becomes the winner's stored result (`COMMITTED`, same tx_id) — spec: "одновременный дубль ждёт индекс и получает результат первого"; "in progress" contract removed |
| `:310` | same | refusal moved nothing | REWRITE IN PLACE (3): after both finish — one row, one envelope, one debt effect (no second effect) |
| `:316-317`, `:321-323` (helper `:102-107`) | same | winner COMMITTED; exactly one COMMITTED row, one COMPLETED envelope, one debt of 10.00 | ⚑ SURVIVES: in place |
| `:462` | `test_the_insert_race_at_serializable_leaves_one_payment_postgres` (cell 5) | premise: loser's lookup missed | SURVIVES: in place |
| `:466`, `:472-475` | same | premise: winner's `NEW` row committed & visible before loser inserts; no envelope/debt | REWRITE IN PLACE (3): no committed NEW exists after stage 3 — the race becomes "loser inserts while winner's uncommitted row holds the index" (merges with cell 3); spec does not say this for cell 5 (see C) |
| `:480-484` | same | loser → 409/E008 | ⚑ REWRITE IN PLACE (3): same request identity → winner's stored result; 409 only for identity mismatch (TO WRITE 3: other fingerprint / initiator / type → 409, spec §1 staged race counter-checks, here on the API path) |
| `:501-509` | same | insert failed with 23505 or 40001 (any unique) | REWRITE IN PLACE (3): 23505 must be on the exact `transactions` tx_id constraint (name fixed by T1902) — "any unique" widening is forbidden by spec §4 |
| `:514-522` | same | 23505 branch: re-read, "in progress", not retryable | REWRITE IN PLACE (3): 23505 → identity resolver → stored result |
| `:525-532` | same | 40001 branch: classified 40001, `retryable: true`, `database_concurrency` | REWRITE IN PLACE (3): 40001 → `pay()` retries on a fresh session → stored result; `409/E008 retryable` only on budget exhaustion (TO WRITE 3: exhaustion stores no ABORTED) |
| `:546` | same | after race: only winner's row, state `NEW` | DROP: durable `NEW` mid-payment (removed stage 3) — replaced by `:472` rewrite (no row visible before winner commits) |
| `:547` | same | no envelope after race | SURVIVES: in place |
| `:551-552`, `:557-558` | same | winner COMMITTED; one payment; winner's row id | ⚑ SURVIVES: in place |
| `:570` | same | no `prepare_locks` rows for tx_id | DROP (5): prepare_locks row count (table dropped by `031`) |

#### `tests/integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py` (REWRITE, stages 2, 3, 4) — ⚑ T1523 cells 1, 2

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:190`, `:195-198` | `test_a_committed_payment_still_replays_its_result_under_an_integrity_hold` (cell 1) | first payment COMMITTED; one debt 10.00, COMPLETED envelope | ⚑ SURVIVES: in place |
| `:229-232` | same | replay under hold → 200 stored COMMITTED, triple unchanged | ⚑ T1546 — SURVIVES: in place |
| `:244-246`, `:248` | same | new payment under hold → 409/E008, equivalents `[code]` | ⚑ T1546 — SURVIVES: in place |
| `:247` | same | `reason == PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON` | ⚑ REWRITE IN PLACE (2): import from `money_boundary` (constant moves with `MONEY_STOP_REASONS`, `engine.py:378-387`); at latest 4 |
| `:282` | `test_a_tx_id_whose_payment_aborted_replays_the_stored_aborted_result` (cell 2, ⚑ `:252`) | first request → 504 | ⚑ REWRITE IN PLACE (3, 4): abort produced today by `PaymentEngine.prepare` sleep + `PREPARE_TIMEOUT_SECONDS=0.01` (`:268-278`); stage 3 removes/changes the per-phase timeout (`service.py:776`), stage 4 the patch target — re-inject a slow step inside `execute()` under `PAYMENT_TOTAL_TIMEOUT_SECONDS`; must still be a terminal timeout with confirmed rollback (FORK-4 → ABORTED) |
| `:288-295` | same | one ABORTED row, fingerprint stored, "Payment timeout", no debt/envelope/entry | ⚑ SURVIVES: in place (Q1: definitive refusal stored ABORTED) |
| `:309-317` | same | replay → 200 stored ABORTED, E007 "Payment timeout", triple unchanged | ⚑ SURVIVES: in place (spec §2 anchor) |
| `:324-327`, `:335` | same | control: fresh tx_id commits, envelope, 10.00 moved | ⚑ SURVIVES: in place (fault lift must also lift the new injection) |

#### `tests/integration/test_p015_t1523_restart_after_commit_postgres.py` (REWRITE, stages 3, 5) — ⚑ T1523 cell 8

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:243-251` | `test_a_payment_committed_by_a_process_that_died_is_replayed_by_another_postgres` | A exited 17 at the patched point, printed COMMITTED-THEN-EXIT, no RESULT/ERROR | REWRITE IN PLACE (3): child's die point moves from `PaymentEngine.commit` to after `pay()`'s outer COMMIT (`t1523_restart_child.py:59-70`); without the move the premise below FAILS at stage 3 (nothing durable) |
| `:256-263` | same | commit durable on 3rd connection: one COMMITTED row, COMPLETED envelope, one debt 10.00 | ⚑ SURVIVES: in place |
| `:273-284` | same | B is another pid, exits 0, answers stored COMMITTED 10.00 | ⚑ SURVIVES: in place |
| `:288` | same | triple byte-identical after replay | ⚑ SURVIVES: in place |
| `:301` | same | no `prepare_locks` leftovers | DROP (5): prepare_locks rows (table dropped) |

#### `tests/integration/test_p015_p1_money_replay_postgres.py` (REWRITE, stages 3 conditional, 5) — ⚑ spec §2

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:526` | `test_the_stand_is_serializable` | stand is SERIALIZABLE (measuring-instrument control) | SURVIVES: in place |
| `:559-560`, `:563`, `:567` | `test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` (⚑ `:530`) | one replay on 40001; competitor committed once; typed conflict's `__cause__` SQLSTATE is 40001 | ⚑ SURVIVES: in place — stage 3 translation of StaleDataError/`execute()` propagation must keep the DBAPI `__cause__` chain (`_record_conflict_sqlstates` `:433-456`); patch target `create_payment_internal_staged` (`:445`) changes only if renamed |
| `:570`, `:573-577` | same | plan recomputed on fresh snapshot, non-vacuous | ⚑ SURVIVES: in place |
| `:581-586` | same | debts = opening + competitor + replanned | ⚑ SURVIVES: in place |
| `:589-592` | same | one COMMITTED tx, none from discarded attempt | ⚑ SURVIVES: in place |
| `:593` | same | `prepare_locks` = 0 | DROP (5): prepare_locks row count |
| `:596-600` | same | `tx.updated` 1, `tx.failed` 0, counters | ⚑ SURVIVES: in place |
| `:603-612` | same | not an error; replay/conflict/progress counters | ⚑ SURVIVES: in place |
| `:661-663` | `test_a_genuine_40001_is_raised_by_the_staged_write_on_this_backend[same-row,write-skew]` | conflict real, raised while staging (not at outer commit) | REWRITE IN PLACE (3, conditional): recorder patches `create_payment_internal_staged` (`:420-430`); the measured landing point must be re-measured at stages 3 and 4 (engine calls inside savepoint → direct Book) — the module docstring `:9-29` claims it for the current code only |
| `:666-670`, `:672-675` | same | one COMMITTED tx; published once; counters | SURVIVES: in place |
| `:671` | same | `prepare_locks` = 0 | DROP (5): prepare_locks row count |
| `:689` | same (write-skew) | competitor's own change survives replay | SURVIVES: in place |
| `:694` | same | plan changes iff same-row | SURVIVES: in place |
| `:725`, `:727` | `test_the_staged_prefix_of_a_conflicted_attempt_is_rolled_back` (⚑ `:703`) | replanned; non-vacuous prefix | ⚑ SURVIVES: in place |
| `:733-735`, `:738-742` | same | discarded prefix gone; one COMMITTED tx per replanned payment | ⚑ SURVIVES: in place |
| `:743` | same | `prepare_locks` = 0 | DROP (5): prepare_locks row count |
| `:744-745` | same | publications and counters = replanned count | ⚑ SURVIVES: in place |
| `:769`, `:775` | `test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget` (⚑ `:751`) | 3 competitor commits, one exhausted log | ⚑ SURVIVES: in place |
| `:779-783` | same | only competitor's increments; no transaction; no `tx.updated` | ⚑ SURVIVES: in place — with stage 3 staged definitive refusals becoming durable, confirm that conflict exhaustion still leaves NO row (it is retryable, not definitive) |
| `:785-790` | same | not an error; `REAL_MODE_MONEY_CONFLICT_UNRESOLVED`; counters | ⚑ SURVIVES: in place |
| `:823`, `:825` | `test_a_tail_failure_after_the_money_commit_never_replays_money` (⚑ `:796`) | money phase once, no replay | ⚑ SURVIVES: in place |
| `:828`, `:830` | same | debt = opening + amount; COMMITTED | ⚑ SURVIVES: in place |
| `:832-833`, `:835-836` | same | published once; tail error counted as tick error | ⚑ SURVIVES: in place |

#### `tests/integration/test_p018_a_serialization_failure_leaves_no_envelope.py` (REWRITE, stage 4) — 018 contract T1801

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:90` | `test_t1801_a_serialization_failure_inside_the_book_leaves_no_envelope_and_retries_clean` | loser's snapshot sees 10 | SURVIVES: in place (setup `:70-71` placeholder `state="NEW"` → `COMMITTED`, stage 4, CHECK `030`) |
| `:98` | same | original SQLSTATE 40001 reaches caller | ⚑ 018 — SURVIVES: in place (spec §2 "исходное исключение/SQLSTATE видно владельцу повторов") |
| `:99` | same | `PaymentEngine._is_retryable_db_error(..., op="commit")` accepts it | ⚑ REWRITE IN PLACE (4): assert the retry predicate of the new owner (`pay()`/`service.py:108` classifier after stage 3) accepts it; predicate must keep `engine.py:563-621` semantics (40001/40P01/named 23505) |
| `:100-101` | same | no `book_rollback_error`; context empty after book savepoint rollback | ⚑ 018 — SURVIVES: in place |
| `:107`, `:108-116` | same | no envelope, no entries of loser | ⚑ 018 — SURVIVES: in place |
| `:123-128` | same | retry on fresh snapshot: one COMPLETED envelope, one `U` 11→12 | ⚑ 018 — SURVIVES: in place |

#### `tests/unit/test_invariants.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:50-63` | `test_edge_model_attributes_a_debt_directionally` | — | SURVIVES: in place (unaffected) |
| `:94-98` | `test_trust_limit_violation_detected` | checker raises TRUST_LIMIT_VIOLATION | SURVIVES: in place |
| `:203` (raises), `:206-207` | `test_payment_commit_aborts_on_trust_limit_violation[commit/no-commit]` | payment exceeding the limit at commit → `IntegrityViolationException` E008 `TRUST_LIMIT_VIOLATION` | REWRITE IN PLACE (4): drive the direct `Book` path with a flow over the limit (seed via route/limit change between routing and write, or patched capacity) and assert E008 TRUST_LIMIT_VIOLATION, no COMMITTED row, debts unchanged — stage 4 must keep `check_trust_limits` (`engine.py:1569-1577`, see C) |
| `:208-212` | same | `engine.abort` called with `_tx_lock_already_held` / owner-locks flags | DROP: engine abort call shape (removed stage 4) |
| `:261`, `:300-304`, `:360-363`, `:428-431` | clearing neutrality / checkpoint tests | — | SURVIVES: in place (unaffected) |
| `:526` | `test_payment_commit_writes_integrity_audit_log_on_success` | commit of seeded PREPARED succeeds | REWRITE IN PLACE (4): real payment via `execute()`/`pay()` (seeded PREPARED+PrepareLock `:476-513` refused by `030`) |
| `:529` | same | perturbation went through `_apply_flow` once | DROP: engine method call shape (`_apply_flow`) — keep an equivalent Book-level perturbation proof if the expire-all perturbation is kept |
| `:539-540` | same | `IntegrityAuditLog(PAYMENT, tx_id)` written, passed, participants | REWRITE IN PLACE (4): same assert on the direct path — stage 4 must keep the FIX-014 audit row (`engine.py:1601-1662`, see C) |
| `:549` | same | no `PrepareLock` rows after commit | DROP (4): prepare_locks row count (nothing writes them after stage 4) |
| `:637-659` | `test_clearing_writes_integrity_audit_log_on_success` | — | SURVIVES: in place |

#### `tests/unit/test_debt_symmetry.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:39-40` | `test_debt_symmetry_violation_detected` | DEBT_SYMMETRY_VIOLATION | SURVIVES: in place |
| `:84-86` | `test_apply_flow_nets_mutual_debts` | payment flow (amount 0) over mutual 10/7 nets to one direction 3 | REWRITE IN PLACE (4): call `Book.post(session, operation_for("PAYMENT", …), [PaymentFlow(a, b, Decimal("0"), eq)])` instead of `PaymentEngine._apply_flow` (it already forwards to `Book`, `test_invariants.py:527-528` comment); placeholder via fixed `writer_operation` |

#### `tests/unit/test_the_test_engine_enforces_foreign_keys.py` (REWRITE, stages 4, 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:45` | `test_a_dangling_reference_is_refused` | dangling `initiator_id` refused | REWRITE IN PLACE (4): seed `state="COMMITTED"` (NEW is refused by CHECK `030` with the same `IntegrityError` class → vacuous pass) and assert SQLSTATE `23503` |
| `:66` | `test_a_child_written_before_its_parent_is_refused` | bare FK without ORM ordering refused (`PrepareLock.tx_id`) | REWRITE IN PLACE (5): retarget to the other bare FK `debt_operations.tx_id → transactions.tx_id` (`app/db/journal_tables.py:138-141`); else DROP: prepare_locks FK (table dropped by `031`) |

#### `tests/p018_t1809_operation_cost_probe.py` (REWRITE, stages 3, 4) — out of tier, measurement

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:198` | `_payment` (in `test_t1809_operation_cost`) | commit really wrote 7.00 | REWRITE IN PLACE (3): operation = whole `pay()` (routing + write + one COMMIT), not `PaymentEngine.commit` of a pre-PREPARED tx; baseline of the whole 3-commit API path TO WRITE before stage 3 lands |
| `:245` | `_clearing` | cleared 10.00, no edges left | SURVIVES: in place |
| `:327-331` | `_inject` | inject note stats | SURVIVES: in place |
| `:391-394` | `test_t1809_operation_cost` | anti-vacuum: statements, xacts, tuples written > 0 | SURVIVES: in place |

#### `tests/unit/test_admin_incidents_list.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:15` | `test_admin_incidents_requires_admin_token` | 403 | SURVIVES: in place |
| `:70`, `:74-77` | `test_admin_incidents_lists_only_stuck_payments` | 200, page/per_page, total 1, one item | REWRITE IN PLACE (4): seed only a CLEARING `WAITING` row + terminal PAYMENT rows; expect 200, `total == 0`, `items == []` (spec: stuck lists return empty) |
| `:80-86` | same | item shape: tx_id, state, initiator_pid, equivalent, sla, age, UTC created_at | DROP: stuck-PAYMENT item content — no PAYMENT can be non-terminal after `030`; the wire schema (`AdminIncidentsListResponse`) is untouched by 019 and its fate is П4/T1911 |
| `:117`, `:121-124` | `test_admin_incidents_pagination` | page 2 of 3 stuck | REWRITE IN PLACE (4): pagination echo (`page`, `per_page`) on an empty result; `total 3` DROP (same removed contract) |

#### `tests/unit/test_admin_liquidity_summary.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:21` | `..._requires_admin_token` | 403 | SURVIVES: in place |
| `:94`, `:98-100`, `:103-125` | `test_admin_liquidity_summary_smoke` | totals, bottlenecks, nets, edges | SURVIVES: in place (drop the PREPARED seed `:70-82`) |
| `:101` | same | `incidents_over_sla == 1` | REWRITE IN PLACE (4): `== 0` (metric reads nothing; `admin.py:850-869`) |
| `:185-190` | `..._keeps_high_precision_threshold` | — | SURVIVES: in place |

#### `tests/unit/test_admin_whoami_and_extras.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:17`, `:24-25`, `:41-42`, `:60-66`, `:84-90` | whoami / dev auth / equivalents / feature flags | — | SURVIVES: in place |
| `:147`, `:151-153`, `:156-157` | `test_admin_graph_snapshot_include_extras_smoke` | 200; incidents/audit/transactions are lists; audit & transactions non-empty | SURVIVES: in place (seed the transaction as COMMITTED) |
| `:155` | same | `len(incidents) >= 1` | REWRITE IN PLACE (4): `== []` — graph snapshot incidents (`admin.py:248-256`) must return empty too |

#### `tests/unit/test_background_task_supervision.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:43-45` | `test_task_factory_failure_marks_job_degraded` | generic supervisor marks failed | REWRITE IN PLACE (4): label `name="recovery"` → `"integrity"` (generic; no recovery job exists) |
| `:68-70` | `test_task_creation_failure_marks_job_degraded` | same | REWRITE IN PLACE (4): same relabel |
| `:90-92` | `test_unexpected_task_exception_is_observable` | same | REWRITE IN PLACE (4): same relabel |
| `:117` | `test_shutdown_cancellation_is_not_reported_as_failure` | same | REWRITE IN PLACE (4): same relabel |
| `:188-195`, `:220-222`, `:240-265`, `:291-298` | lifespan / clean stop / integrity / health | — | SURVIVES: in place |
| `:343-345` | `test_recovery_loop_reports_each_iteration_and_can_recover` | recovery loop reports each iteration via `main._record_recovery_iteration` | DROP: recovery loop (removed stage 4); generic iteration reporting survives in `test_integrity_failure_degrades_and_later_success_recovers` (`:227`, asserts `:249-265`) |
| `:362-369` | `test_recovery_iteration_cancellation_is_not_reported_as_failure` | cancellation not a failure | DROP: recovery iteration; generic cancellation-not-failure survives in `test_shutdown_cancellation_is_not_reported_as_failure` (`:96`, `:117`) |

#### `tests/unit/test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py` (REWRITE, stages 3 verify, 4) — ⚑ T1548

| file:line | test function | effect checked | fate |
|---|---|---|---|
| `:180-193` | `test_a_replay_of_a_row_without_a_usable_fingerprint_is_refused` | 409 UNVERIFIABLE, nothing moved | ⚑ SURVIVES: in place |
| `:213-217` | `test_the_refusal_does_not_depend_on_the_stored_state` | refusal identical for NEW/ROUTED/PREPARE_IN_PROGRESS/PREPARED/COMMITTED/ABORTED | ⚑ REWRITE IN PLACE (4): sweep only `COMMITTED`, `ABORTED` (non-terminal PAYMENT unseedable after `030`); the "decided before in-progress branch" rationale (`:200-205`) becomes moot at stage 3 |
| `:234`, `:244` | `test_the_refusal_is_decided_before_the_perimeter` | identity answer before routing perimeter | ⚑ SURVIVES: in place |
| `:258`, `:266-267` | `test_the_stored_result_is_still_readable_after_the_refusal` | GET returns stored COMMITTED | ⚑ SURVIVES: in place |
| `:277`, `:292`, `:312-333` | `test_the_insert_race_row_is_refused_by_the_same_policy` | second entrance (IntegrityError handler) gives same refusal, not retryable, loser leaves nothing | ⚑ REWRITE IN PLACE (3): entrance becomes the exact tx_id identity resolver (rollback, new transaction, reread); the in-`build_graph` `db_session.commit()` (`:300-304`) commits the payment's own outer transaction under stage 3 — verify the stand still reaches the resolver |
| `:361-363`, `:381-387`, `:419-432` | anti-vacuum: fingerprinted hit, different request conflict, other type/sender | ⚑ SURVIVES: in place |

### 5.3. G3 — Локи, резервы, interlock, клиринг, инжект

#### `tests/integration/test_clearing_commit_replay_postgres.py` (REWRITE, stages 2(cond.)/4 patch target, 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :163, :180-184 (setup) | `test_concurrent_same_cycle_serializable_resolves_one_durable_occurrence_postgres` | barrier on `PaymentEngine.acquire_session_equivalent_owner_lock` | REWRITE IN PLACE (stage 2 cond. / 4): patch the moved `money_boundary` function; stage 5: barrier replaced (no session owner lock) |
| :198-199 | same | worker sessions really SERIALIZABLE (control) | REWRITE IN PLACE (stage 5): kept as control |
| :216-220 | same | second clearing waits on the first's advisory owner lock (holder/waiter pids) | DROP: session-owner advisory lock wait of clearing (`engine.py:207`, `clearing/service.py:1624`) removed stage 5; replaced by mechanism assert on 40001 / retry-owner attempt counter (TO WRITE in place, T1908) |
| :227 ⚑ (spec §3 selector) | same | both workers report 30 — second one replays the durable occurrence | REWRITE IN PLACE (stage 5): same expectation, reached via clearing retry owner + occurrence reconciliation |
| :228 | same | exactly 2 owner-lock acquisitions | DROP: owner-lock acquisition count (stage 5); replaced by attempts counter |
| :229 | same | no session left in a transaction | REWRITE IN PLACE (stage 5): unchanged |
| :257-263 ⚑ | same | one COMMITTED clearing tx, one CLEARING audit, debts {d0: 70, d2: 10} | REWRITE IN PLACE (stage 5): unchanged |
| :407 | `test_serializable_conflict_without_committed_occurrence_stays_failure_postgres` | no committed occurrence on first reconcile (barrier premise) | REWRITE IN PLACE (stage 5): only if the retry owner still reconciles before work; otherwise barrier moves |
| :448 | same | owner session SERIALIZABLE (control) | REWRITE IN PLACE (stage 5): unchanged |
| :468 ⚑ (spec §3 selector) | same | real 40001 without occurrence → E010 | REWRITE IN PLACE (stage 5), **verdict inverts**: the stage-5 retry owner (spec "Изоляция…" item 3) re-reads 101.00 on a fresh session and clears 30 → success. The "no fake success" contract moves to TO WRITE (stage 5): retry exhausted by the clearing deadline → retryable failure, no clearing row, debts unchanged |
| :469-470 ⚑ | same | the conflict really was SQLSTATE 40001 | REWRITE IN PLACE (stage 5): unchanged (it is now the retry trigger) |
| :471 | same | owner session not in transaction | REWRITE IN PLACE (stage 5): unchanged |
| :499-505 | same | no clearing tx, no audit, debts 101/30/40 | REWRITE IN PLACE (stage 5): becomes one COMMITTED clearing + one audit, debts {d0: 71, d2: 10}; old expectation → TO WRITE deadline-exhaustion test above |
| :683-686 (setup) | `test_post_commit_boundary_reconciles_and_new_cycle_still_executes_postgres[connection_loss*]` | commit patch invalidates the session's `AsyncConnection` bind | REWRITE IN PLACE (stage 5): without the pinned interlock connection the session is engine-bound and `assert isinstance(bind, AsyncConnection)` fails; invalidate `await session.connection()` instead |
| :700-702, :708-710 ⚑ (spec §2; `real_clearing_engine.py:390`) | same [cancellation, connection_loss_reconcile_cancellation] | `ClearingCommittedAfterCancellation` carries the cleared amount | KEEP in place through stage 5 (raise site `clearing/service.py:2177` survives) |
| :722-724 ⚑ | same, all params | reversed-cycle replay returns 30 == boundary amount; no open tx | KEEP in place (stage-5 retry owner must keep occurrence replay) |
| :745 | same | anti-vacuum: new Debt-ID set clears 5 | KEEP |
| :773-779 ⚑ | same | 2 COMMITTED clearing txs, 2 audits, debts {d0: 65, d2: 5} | KEEP |

#### `tests/integration/test_clearing_payment_prepare_interlock_postgres.py` (REWRITE, stages 2(cond.)/4/5)

Helpers: `_use_serializable` :35 (keep), `_no_advisory_lock_is_held` :122 (becomes vacuous at stage 5 — see C4), `_seed_interlock_case` :195 (stage 4: drop the `NEW` `PAYMENT` row :276-291 and `payment_tx_id` :301, which only :307/:484 use), `_wait_for_*` :42/:61/:92.

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :188 | `test_no_advisory_lock_check_ignores_other_databases_postgres` (T1537) | premise: foreign-db advisory lock visible as foreign | DROP (stage 5) together with `_no_advisory_lock_is_held`: advisory locks removed; T1537 control of a removed helper |
| :189 (→ :146, :153) | same | helper ignores other databases; no interlock-invalidation log | DROP (stage 5): same contract |
| :347, :349 | `test_clearing_owner_blocks_new_reverse_prepare_after_empty_snapshot_postgres` | clearing's empty reservation snapshot under SERIALIZABLE | DROP (stage 5): reservation snapshot `_locked_pairs_for_equivalent` |
| :375-379 | same | reverse prepare waits on clearing's owner lock | DROP (stage 5): advisory wait order; the schedule → TO WRITE (stage 5, T1908): clearing vs reverse payment under SERIALIZABLE, 40001 + retry counters |
| :386 ⚑ (clearing accounting) | same | clearing clears 30 | REWRITE IN PLACE (stage 4): `PaymentEngine.prepare` replaced by a full reverse payment (B→A 5.00) through the surviving service path; stage 5 → merged into T1908 schedule |
| :387, :436, :437 | same | prepare returned True; payment `PREPARED`; one prepare lock | DROP: durable `PREPARED` + reservation row (stage 4, spec "Стадия 4"); after rewrite: payment `COMMITTED` |
| :438-448 | same | one COMMITTED clearing, amount 30, cycle set, exactly one CLEARING audit | REWRITE IN PLACE (stage 4): add the payment's PAYMENT audit to the expected set |
| :449-453 ⚑ | same | debts {d0: 70 v2, d2: 10 v2}; limits unchanged | REWRITE IN PLACE (stage 4): expected d0 becomes 65 (v3) after the committed reverse payment; limits unchanged |
| :520-521 | `test_uncommitted_reverse_prepare_blocks_clearing_until_visible_postgres` | uncommitted prepare holds the owner lock | DROP (stage 4): `PaymentEngine.prepare(commit=False)` removed |
| :529-533 | same | clearing waits on the prepared payment's owner lock | DROP (stage 5 contract; test cannot run past stage 4): payment owner lock vs clearing session lock; TO WRITE (stage 5, T1908) covers payment-first/clearing interleaving under SERIALIZABLE |
| :537-538 | same | clearing then skips the cycle (None) because a committed reservation is visible | DROP (stage 4): "clearing excludes a pair with a live durable reservation" — no durable reservation exists after stage 4 |
| :596-606 | same | payment `PREPARED` + 1 lock; no clearing/audit; debts unchanged v1 | DROP (stage 4): durable `PREPARED`/reservation contract |
| :666-667 | `test_clearing_interlock_completes_with_single_connection_pool_postgres` | clearing succeeds (30) on a pool of one connection; tx ended | REWRITE IN PLACE (stage 4: seed loses `NEW` row; stage 5: premise "no second connection" trivially true — keep as regression) |
| :696, :702-707 | `test_postgres_clearing_rejects_external_connection_bind_postgres` | connection-bound session refused, pool not leaked | DROP (stage 5) **if** T1909 drops the refusal (`clearing/service.py:1495-1504`, exists only for the pinned connection); otherwise KEEP. Decision belongs to T1909 — see C6 |
| :767-773 | `test_cancellation_after_interlock_checkout_returns_connection_postgres` | cancellation between interlock checkout and isolation setup returns the connection | DROP (stage 5): interlock checkout `clearing/service.py:1583-1612`; the pause hook (`execution_options(isolation_level=…)`) will never fire → would fail on `wait_for` timeout, not pass |
| :827 | `test_cancellation_during_interlocked_work_rolls_back_before_unlock_postgres` | caller session ended after cancel | REWRITE IN PLACE (stage 5): pause hook `_locked_pairs_for_equivalent` (:807-819) moves to a surviving point inside the money UoW |
| :855-861 ⚑ | same | cancelled clearing leaves debts v1, no clearing tx, no audit | REWRITE IN PLACE (stage 5): unchanged expectations |
| :862-869 | same | no advisory lock left; owner lock acquirable | DROP (stage 5): owner-lock release; stage 2(cond.)/4: probe via `money_boundary` |
| :912-914 | `test_cancellation_during_preflight_select_rolls_back_caller_postgres` | cancel while blocked on `debts` read ends the caller tx | KEEP (no seed, no engine); name says "pinned ownership" — rename optional |
| :972-974 ⚑ (spec §2 committed-after-cancellation) | `test_cancellation_during_interlock_release_preserves_durable_amount_postgres` | cancellation during interlock release → `ClearingCommittedAfterCancellation(30)` | DROP (stage 5): raise site `clearing/service.py:1676` (release of the interlock) disappears. SURVIVES: `tests/integration/test_clearing_commit_replay_postgres.py::test_post_commit_boundary_reconciles_and_new_cycle_still_executes_postgres[cancellation]` :700-702, :722-723, :773-779 (raise site `:2177`) + unit consumer `tests/unit/test_real_clearing_engine_partial_failure.py::test_partial_clearing_is_finalized_before_failure_propagates[committed_cancel]` :108-112, :194-229 |
| :985-986 | same | exactly one COMMITTED clearing | DROP (stage 5): SURVIVES commit_replay[cancellation] :773-774 |
| :987-994 | same | no advisory lock left; owner lock acquirable | DROP (stage 5): owner-lock release |
| :1031-1038 | `test_interlock_timeout_rolls_back_work_and_releases_owner_postgres` | clearing blocked by a held owner lock times out (`TimeoutException`), caller tx ended | DROP (stage 5): interlock timeout on the owner lock. TO WRITE (stage 5, T1907): clearing retry-owner deadline from the clearing budget (`09:243`) → timeout, no effect, caller never committed/rolled back for isolation |
| :1051 | same | no advisory lock left | DROP (stage 5) |
| :1059 | same | retry after release clears 30 | TO WRITE (stage 5): same as above (retry after deadline succeeds) |

#### `tests/integration/test_clearing_skip_releases_locks_postgres.py` (REWRITE, stages 4, 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :119-148 (setup), :171-173, :201 | `test_skip_ends_service_owned_transaction_postgres[locked]` | reservation skip reached and ends the tx | DROP (stage 4): seeds `PREPARED` `PAYMENT` + `PrepareLock` → refused by CHECK `030`; contract (skip on live reservation) removed. "skip ends the tx" SURVIVES in the same test's other params :198-199 |
| :198-199, :201 | same [empty, malformed, missing, policy] | every None result ends the service-owned tx; policy branch reached | KEEP |
| :349-355 | `test_policy_skip_releases_debt_rows_before_concurrent_payment_postgres` | both sessions READ COMMITTED | REWRITE IN PLACE (stage 5): SERIALIZABLE (writers refuse RC) |
| :360 | same | policy skip returns None | REWRITE IN PLACE (stage 5): unchanged |
| :404-410 | same | payment not blocked by retained row locks; COMMITTED | REWRITE IN PLACE (stage 5): unchanged |
| :411 | same | no prepare lock left | DROP (stage 5): `prepare_locks` table (vacuous from stage 4) |
| :413 | same | anti-vacuum: debt 105 | REWRITE IN PLACE (stage 5): unchanged |

#### `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py` (REWRITE, stages 2(cond.)/4 patch, 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :184-191 | `test_concurrent_payment_and_clearing_same_trustline_preserve_effects_postgres` | clearing/payment sessions READ COMMITTED | REWRITE IN PLACE (stage 5): SERIALIZABLE (spec forbids RC as SSI evidence). Stage 3 caveat: if `pay()` opens its own session the assert checks a session the payment no longer uses (C5) |
| :216, :233-245 (setup) | same | barrier on `PaymentEngine._acquire_equivalent_owner_locks` | REWRITE IN PLACE (stage 2 cond. / 4): patch `money_boundary`; stage 5: removed |
| :222 | same | clearing's reservation scan empty | DROP (stage 5): `_locked_pairs_for_equivalent` |
| :261-262 | same | payment reached its owner-lock call | DROP (stage 5): owner lock |
| :263-275 | same | payment waits on clearing's advisory owner lock | DROP (stage 5): advisory wait. TO WRITE (stage 5, T1908): SQLSTATE 40001 observed + retry counters of both owners |
| :276-277 | same | neither finished while clearing held | REWRITE IN PLACE (stage 5): keep as barrier control on the new barrier |
| :291-292 ⚑ | same | clearing 30; payment COMMITTED | REWRITE IN PLACE (stage 5): unchanged |
| :306-309 ⚑ | same | payment COMMITTED; exactly one COMMITTED clearing | REWRITE IN PLACE (stage 5): unchanged |
| :341-343 ⚑ | same | clearing payload amount 30 | REWRITE IN PLACE (stage 5): unchanged |
| :344-347 ⚑ (spec VP "итоговые долги сохраняются") | same | final debts {d0: 120 v3, d2: 10 v2} | REWRITE IN PLACE (stage 5): unchanged; versions still hold (only committed updates bump `version`) |
| :348 | same | trust limits unchanged | REWRITE IN PLACE (stage 5): unchanged |
| :349 | same | no prepare lock for the payment | DROP (stage 5): `prepare_locks` table (vacuous from stage 4) |
| :350-356 ⚑ | same | PAYMENT + CLEARING audits, both verified | REWRITE IN PLACE (stage 5): unchanged |
| :358-360 ⚑ | same | one `payment.received` publication for the tx | REWRITE IN PLACE (stage 5): unchanged (no publication for aborted attempts) |

#### `tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py` (REWRITE, stages 2(cond.)/3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :51-53 (setup) | `test_concurrent_payments_shared_bottleneck_commit_once_postgres` | widen `PREPARE/COMMIT/TOTAL` timeouts | REWRITE IN PLACE (stage 3): `monkeypatch.setattr` without `raising=False` → `AttributeError` if stage 3 removes `PREPARE_TIMEOUT_SECONDS`/`COMMIT_TIMEOUT_SECONDS` |
| :79-111 (setup), :175-177, :178-179, :181 | same | owner-lock holder/waiter barrier; waiter blocked; 2 acquisitions | stage 2 cond./4: REWRITE IN PLACE (patch `money_boundary`); stage 5: DROP — owner-lock serialization; TO WRITE (T1908) 40001/retry evidence |
| :122-128 | same | payment sessions READ COMMITTED | REWRITE IN PLACE (stage 5): SERIALIZABLE; stage-3 caveat as lost-update |
| :182-183 | same | neither payment done while holder holds | REWRITE IN PLACE (stage 5): control on the new barrier |
| :194-197 ⚑ (spec §3 "bottleneck") | same | exactly one success, one `RoutingException` E002 | REWRITE IN PLACE (stage 5): unchanged |
| :232-235 ⚑ | same | states {winner: COMMITTED, loser: ABORTED} | REWRITE IN PLACE (stage 5), **expectation changes**: without the owner lock the loser hits 40001, `pay()` retries on a fresh snapshot, routing refuses before any insert (class "no row", spec §"Окончательный отказ", `service.py:790-880`) → loser has **no** row. Stages 3-4 keep `ABORTED` (capacity refusal on recheck is definitive) |
| :236-237 ⚑ | same | shared debt 8 ≤ capacity 10 | REWRITE IN PLACE (stage 5): unchanged |
| :238 | same | no prepare locks | DROP (stage 5): `prepare_locks` table |
| :239-241 ⚑ | same | one verified PAYMENT audit (winner) | REWRITE IN PLACE (stage 5): unchanged |
| :243-245 ⚑ | same | one `payment.received` for the winner | REWRITE IN PLACE (stage 5): unchanged |
| :411-420, :425-427 | `test_concurrent_prepare_routes_shared_bottleneck_serializes_on_postgres` | two `prepare_routes` → exactly one reserves, other E002 | DROP (stage 4, not 5): `PaymentEngine.prepare_routes` + `NEW` seed rows (:383-398, CHECK `030`). SURVIVES: same file `::test_concurrent_payments_shared_bottleneck_commit_once_postgres` :194-197, :236-237 (stronger: end-to-end, commits) |

#### `tests/integration/test_simulator_clearing_no_deadlock.py` (REWRITE, stages 2(cond.)/4 import, 5 stand)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :263, :274 (setup) | `test_the_tick_commits_its_parent_session_before_clearing` | parent session holds the equivalent owner lock when clearing is reached | stage 2 cond./4: REWRITE IN PLACE (`money_boundary`); stage 5: REWRITE IN PLACE — hold a lock clearing still needs (e.g. `SELECT … FOR UPDATE` of one triangle debt) and re-measure the mutation (remove the early commit → red) |
| :282-288 | same | tick completes within 10 s (no deadlock) | REWRITE IN PLACE (stage 5): unchanged |
| :291 | same | non-vacuity: parent held the lock | REWRITE IN PLACE (stage 5): now asserts the new holder |
| :294-301 | same | `clearing.done` published, ≥ 1 cycle cleared | REWRITE IN PLACE (stage 5): unchanged |
| :304 | same | no pending clearing task | REWRITE IN PLACE (stage 5): unchanged |

#### `tests/unit/test_clearing_prepare_lock_conflict.py` (DROP, stage 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :121 | `test_find_cycles_excludes_edges_with_active_prepare_locks` | cycle through a reserved pair not returned | DROP: reservation exclusion in detection (`clearing/service.py:996`, :1126, :1265) removed with `prepare_locks` (migration `031`) |

#### `tests/unit/test_routing_reserved_and_policy.py` (REWRITE, stage 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :173 | `test_build_graph_subtracts_reserved_capacity_and_respects_policy` | capacity 100 − reserved 30 = 70 | DROP: reserved-capacity accounting (`router.py:212`, :266, :331) removed stage 5; capacity from limit/debt stays covered by `tests/integration/test_payments_insufficient_capacity.py` (§3 selector) |
| :181, :185 | same | edge B→C exists; `max_hops=1` forbids A→B→C | REWRITE IN PLACE (stage 5): drop the lock stub (:45-49, :107-108, :142-153); graph A→B becomes 100 |
| :204, :208-210, :232, :254-256, :281-284 | other 4 tests | intermediate/blocked policy, T1545 self-pay | KEEP (no reservation use) |

#### `tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py` (REWRITE, stages 2(cond.)/4 import, 5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :51, :79-80 (setup) | module | owner-lock namespace + key arithmetic from `PaymentEngine` | REWRITE IN PLACE (stage 2 cond. / 4): import from `money_boundary` — module import failure breaks 6 importers (C3) |
| :337 ⚑ | `test_the_due_events_phase_writes_each_injected_debt_under_its_owner_lock` | both injects stored with exact amounts | REWRITE IN PLACE (stage 5): unchanged (move to the surviving stand) |
| :338 | same | both events fired | REWRITE IN PLACE (stage 5): unchanged |
| :339 (→ :305-317) ⚑ (015 phase B step 3 reproducer) | same | every debt flush held its equivalent's owner lock (non-vacuity both equivalents) | DROP (stage 5) **unless T1908 keeps the equivalent lock**: owner lock around inject. Replaced by TO WRITE (stage 5, T1907/T1908): inject refuses non-SERIALIZABLE (`test_p019_money_writers_refuse_non_serializable_postgres.py`) + payment/inject and inject/inject schedules on opposite directions |
| :342 | same | owner lock is transaction-level (released) | DROP (stage 5): same |
| :388-391 ⚑ | `test_a_real_tick_keeps_the_owner_lock_boundary_from_inject_to_payments` | tick applies both injects exactly once | REWRITE IN PLACE (stage 5): unchanged |
| :392 | same | inject flushes under owner lock | DROP (stage 5): as :339 |
| :394-402 | same | payments-phase snapshot read under the owner lock of each equivalent | DROP (stage 5): owner lock around the payments snapshot (`real_tick_orchestrator.py:304`); replaced by the SERIALIZABLE-boundary check of the tick (TO WRITE stage 5, T1907) |

#### `tests/unit/test_p015_t1543_frozen_line_is_not_limit_zero.py` (REWRITE, stage 4)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :118 | `test_a_debt_within_the_limit_of_a_frozen_line_is_not_a_violation` | no violation | KEEP |
| :129-132 | `test_a_frozen_line_within_its_limit_leaves_the_checkpoint_healthy` | checkpoint healthy | KEEP |
| :228-229 (setup :225, :184-198) | `test_a_payment_beside_a_frozen_line_is_recorded_as_verified` | commit succeeds under an identity-map-expiring perturbation | REWRITE IN PLACE (stage 4): drive the payment through the surviving `PaymentService` direct path instead of a seeded `PREPARED` + `PaymentEngine.commit`; the `_apply_flow` perturbation target moves (`book.py:306`) |
| :239-240 ⚑ (T1543) | same | PAYMENT audit `verification_passed` True, no error details | REWRITE IN PLACE (stage 4): unchanged |
| :259-260 (setup :254-256) | `test_a_partial_repayment_of_debt_on_a_frozen_line_commits` | commit succeeds | REWRITE IN PLACE (stage 4): as above |
| :271 ⚑ (T1543) | same | debt on the frozen line 42 → 32 | REWRITE IN PLACE (stage 4): unchanged |
| :288-297 | `test_a_frozen_line_over_its_limit_is_still_a_violation_against_that_limit` | violation vs 100; checkpoint critical | KEEP |
| :315-316 | `test_a_debt_without_a_live_line_is_a_violation_against_zero` | violation vs 0 | KEEP |
| :330, :336 | `test_an_active_line_is_compared_with_its_stored_limit` | at-limit ok, over-limit violation | KEEP |

### 5.4. G4a — Проверочные тесты 015 на Postgres

#### `tests/integration/test_p015_b4_entries_and_money_postgres.py` (REWRITE, stages 2/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :632 (with :591-597) | `test_c8_a_real_40001_leaves_one_envelope_and_only_the_successful_attempts_entries` | a TEST_FIXTURE retry invented no `transactions` and no `prepare_locks` | ⚑ REWRITE IN PLACE (stage 5): drop the `PrepareLock` query/`locks` half (model deleted); `transactions == []` half stays (015 C8) |
| :783 | `test_c8_the_payment_owners_own_retry_...` | exactly one genuine 40001 at the payment's own debt write | ⚑ REWRITE IN PLACE (stage 4): 40001 raised at the direct-execution `Book` write; `_apply_flow` wrapper (:676, :759) moves to the new per-flow seam (C9). Source: 015 C8, design v2 §9 |
| :788 | same | the owner re-ran the WHOLE unit of work (2 attempts) | ⚑ REWRITE IN PLACE (stage 4): drive through `pay()` (API retry owner, fresh session); count attempts of `execute`. Also evidence for spec §4 "no retry from the same snapshot" |
| :792, :799 | same | stored = competitor + paid (no stale-snapshot lost update); tx `COMMITTED` | ⚑ REWRITE IN PLACE (stage 4), expectation unchanged |
| :800 | same | prepare locks gone after commit | DROP (stage 5): `prepare_locks` table (trivially true from stage 4) |
| :805, :811, :812, :816, :821, :828 | same | one COMPLETED PAYMENT envelope; one `U` entry, `amount_before` = competitor, delta = paid; one equivalents row | ⚑ REWRITE IN PLACE (stage 4), unchanged (018 journal contract, 015 C8) |
| :1491 | `test_c14_the_payment_envelope_is_written_before_its_prepare_locks_are_deleted` | journal tables present (non-vacuity) | REWRITE IN PLACE (stage 4) |
| :1494 | same | `prepare` wrote PrepareLock rows | DROP (stage 4): durable reservations between prepare and commit |
| :1495 | same | commit issued exactly one `DELETE FROM prepare_locks` | DROP (stage 4): "commit consumes prepare_locks" |
| :1499 | same | prepare locks gone | DROP (stage 5): `prepare_locks` table |
| :1500, :1501 | same | tx `COMMITTED`; debt 8.00 moved | ⚑ REWRITE IN PLACE (stage 4): drive via `execute`/`pay()` |
| :1506, :1510 | same | exactly one COMPLETED PAYMENT envelope | ⚑ REWRITE IN PLACE (stage 4) |
| :1513 | same | stored intent flows == independent pre-commit capture (today the `prepare_locks.effects` snapshot) | ⚑ TO WRITE (stage 4): a new independent capture of the declared flows (e.g. the router's result captured by a wrapper, or the declared path×amount) — without it the intent is compared with nothing (015 C14, design v2 §7). See C8 |
| :1519 | same | envelope already visible when `prepare_locks` are deleted | DROP (stage 4): ordering "envelope flushed before `delete(PrepareLock)`" (design v2 §7) — the crash window it guarded (locks gone, journal not begun) needs durable reservations; in one transaction the envelope-before-debt-write order is held by the 018 debts trigger (GE001) |
| :1992 | `test_c17_p_the_owner_lock_race_never_leaves_an_equivalent_gone_with_its_history` | journal tables present | REWRITE IN PLACE (stage 4) |
| :1997 | same | both owners queued on the owner advisory lock in the forced order | TO WRITE (stage 5): order forcing via row locks (payment `FOR SHARE` vs admin DELETE) observed in `pg_locks`; interim stage 4: the fresh `pay()` is refused by the best-effort pre-check (service.py:720-732) before any lock, so the stand must deactivate after the pre-check (C10) |
| :2005, :2010 | same | admin delete refused 409; equivalent survives | ⚑ REWRITE IN PLACE (stage 4), unchanged (design v2 §10.1) |
| :2022, :2026, :2027-2029, :2030 | same | payment refused 409/E008 `equivalent_inactive`, not retryable | ⚑ REWRITE IN PLACE (stage 4; constant import :2029 at stage 2) — T1544 |
| :2031, :2034 | same | refused payment left no envelope, no debt | ⚑ REWRITE IN PLACE (stage 4), unchanged — T1544 |

#### `tests/integration/test_p015_b4_wrong_writer_is_recorded_faithfully_postgres.py` (REWRITE, stage 4; 5 import)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :612 | `test_c5_p_the_journal_of_an_honest_payment_is_exact_at_full_money_size` | seeded full-size amounts stored exactly | REWRITE IN PLACE (stage 4), unchanged |
| :616 | same | tx `COMMITTED` | ⚑ REWRITE IN PLACE (stage 4): drive via `execute`/`pay()` instead of `_prepare_payment`+`PaymentEngine.commit` |
| :617 | same | declared flows (from `prepare_locks`) = one flow a→b | ⚑ TO WRITE (stage 4): independent declared-flow capture (C8) |
| :621, :627 | same | criterion (b): intent replay == stored state; exact atom arithmetic | ⚑ REWRITE IN PLACE (stage 4) — 015 C5, spec §2 criterion (b) |
| :634, :636 | same | criterion (a): journal totals per edge == observed change | ⚑ REWRITE IN PLACE (stage 4) — 018 journal |
| :713 | `test_c6_p_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today` | wrong writer ran over both declared segments | REWRITE IN PLACE (stage 4): `_collapse_the_route` needs a per-flow seam below intent computation (C9) |
| :716, :719, :722, :723 | same | empty before; single A→C committed; `COMMITTED`; audit `verification_passed == [True]` | ⚑ REWRITE IN PLACE (stage 4) — 015 C6(i) |
| :733, :737, :741 | same | declared flows (from `prepare_locks`) a→b, b→c; declared ≠ after | ⚑ TO WRITE (stage 4): independent capture (C8) |
| :748, :753, :756, :758, :765, :769 | same | one COMPLETED PAYMENT envelope; stored intent = declared; (b) replay refutes the wrong writer | ⚑ REWRITE IN PLACE (stage 4) — 015 C6(i) |
| :776, :786 | same | criterion (a) records the wrong writer faithfully | ⚑ REWRITE IN PLACE (stage 4) |
| :831, :834 | `test_c6_p_control_the_same_payment_without_the_wrapper_satisfies_criterion_b` | `COMMITTED`; two-hop state at full size | ⚑ REWRITE IN PLACE (stage 4) |
| :840 | same | snapshot-path half: (b) from prepare-lock flows == after | ⚑ TO WRITE (stage 4): same half on the new independent capture (anti-vacuum control of C6) |
| :848, :852, :855, :859, :868 | same | envelope present, PAYMENT, COMPLETED, decodes flows, stored-intent replay == after | ⚑ REWRITE IN PLACE (stage 4) |
| :863 | same | stored intent == prepare-lock flows | ⚑ TO WRITE (stage 4): == new independent capture (C8) |

Clearing tests `test_c6_p_a_clearing_cycle_that_leaves_one_atom_...` (:881) and `test_c6_p_control_the_same_cycle_...` (:1045): KEEP, every assertion in place; conditional stage 5 on the patch target `ClearingService._execute_clearing_with_amount` (:522, :539) — C13.

#### `tests/integration/test_p015_step5b_criterion_b_postgres.py` (REWRITE, stages 3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :214 | `test_step5b_p_the_prestate_read_follows_every_advisory_lock_and_the_for_share` | tx `COMMITTED` | REWRITE IN PLACE (stage 4): drive via `execute` (today `_prepare_payment` unit helper :207 + `PaymentEngine.commit` :211) |
| :216-219 (`locks`), :221 (`max(locks) <`) | same | every `pg_advisory_xact_lock` precedes the stop read | DROP (stage 5): advisory-lock ordering (namespace 0x474551) |
| :219 (stops/envelopes), :220 | same | exactly one stop read, it is `FOR SHARE`; one envelope | ⚑ REWRITE IN PLACE (stage 4) — T1544 `FOR SHARE` kept by spec §2 |
| :221 (`stops[0] < envelopes[0]`) | same | stop read precedes the envelope | ⚑ REWRITE IN PLACE (stage 4) |
| :230, :232 | same | between stop and envelope: the Book opening (2 stmts) and exactly ONE `debts` read (pre-state) | ⚑ REWRITE IN PLACE (stage 4): recount named statements — the operation savepoint and `INSERT INTO transactions` may now fall inside the window (spec "Контракт исполнения" step 4). 015 step 5b placement / spec §2 "намерение v2 из чтения до записи" |
| :315, :316, :319 | `test_step5b_p_at_serializable_a_writer_outside_the_owner_lock_cannot_make_the_record_disagree` | 40001 forced a retry; pre-state READ AGAIN (3 then 4); `COMMITTED` | ⚑ REWRITE IN PLACE (stage 4): retry owner becomes `pay()` on a fresh session; seam `_read_payment_prestate` (:250-260) must exist in the new path (C9) |
| :320, :321 | same | edges; no criterion (b) finding | ⚑ REWRITE IN PLACE (stage 4) — spec §2 criterion (b) |
| :352 | `test_step5b_p_stand_control_at_read_committed_...` | the meter really runs at READ COMMITTED | REWRITE IN PLACE (stage 4) — see next row for stage 5 |
| :354, :355, :356, :358 | same | at RC the race DOES make the record disagree (`b_prestate_mismatch`) — positive control of the stand | ⚑ TO WRITE (stage 5): the payment refuses non-SERIALIZABLE before first write (FORK-2), so the meter cannot run the payment at RC; a new positive control is needed (C5). Interim stage 4: drive rewrite |
| :408, :409 | `test_step5b_p_an_application_writer_waits_on_the_owner_lock_through_the_prestate_window` | clearing waited on the owner advisory lock, did not finish inside the window | ⚑ TO WRITE (stage 5): clearing does not commit inside the payment's pre-state window under SSI + clearing retry owner (T1907 evidence); the advisory-wait form is DROP (owner lock) |
| :416, :417, :418, :423, :424 | same | `COMMITTED`; cleared 7 on the state the payment left; edges; no (b) finding; both fully recomputed | ⚑ REWRITE IN PLACE (stage 4 drive; stage 5 unchanged expectations) |
| :532, :535 | `test_step5b_p_an_unwidened_version_check_refuses_the_payment_and_the_service_aborts_it` | 5xx, not a client rejection; caused by 23514 | ⚑ REWRITE IN PLACE (stage 3) |
| :539 | same | tx stored `ABORTED` | ⚑ REWRITE IN PLACE (stage 3): expectation per T1902 classification of a non-retryable internal DB error (FORK-4 table is silent, C4) — Q1 |
| :540, :548 | same | no debt moved; no envelope | ⚑ REWRITE IN PLACE (stage 3), unchanged |
| :541 | same | `_prepare_locks == 0` | DROP (stage 5): `prepare_locks` table |

#### `tests/integration/test_p015_step5c_hold_races_postgres.py` (REWRITE, stages 2/3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :207 | `test_step5c_p_a_payment_commit_waiting_behind_the_reaction_is_refused_by_the_hold` | premise: an observer sees `{tx: PREPARED}` | REWRITE IN PLACE (stage 3): PREPARED no longer visible; premise = barrier counter + observer sees no row |
| :215 | same | payment's commit waits on the reaction's owner lock | ⚑ TO WRITE (stage 5): payment's `FOR SHARE` waits on the hold UPDATE (row lock in `pg_locks`) — the binding the docstring names (T1546) |
| :216 | same | payment not done while the hold is uncommitted | REWRITE IN PLACE (stage 4): barrier moves from `PaymentEngine.commit` (:187-194) into `execute` |
| :220, :224 | same | reaction set the hold; payment refused `equivalent_integrity_hold`, non-retryable, right codes | ⚑ REWRITE IN PLACE (stage 4) — T1546 |
| :226 | same | refusal came through a `FOR SHARE` 40001 retried by `event=payment.uow_retry op=commit` | REWRITE IN PLACE (stage 3): retry owner is `pay()`; log event of the new owner |
| :229, :232 | same | debts unchanged (opening + atom); hold remains | ⚑ REWRITE IN PLACE (stage 4) — T1546 |
| :230 | same | tx stored `ABORTED` | ⚑ REWRITE IN PLACE (stage 3): after `pay()` retries on a fresh session the refusal comes from the pre-NEW pre-check (service.py:720-732) → no row today's reading; conflicts with Q1 — decide in T1902 (C3) |
| :231 | same | `_prepare_locks == 0` | DROP (stage 5): table |
| :277, :278 | `test_step5c_p_a_reaction_arriving_while_a_payment_holds_its_check_waits_and_holds_after` | reaction waited on the payment's owner lock | ⚑ TO WRITE (stage 5): reaction's hold UPDATE waits on the payment's `FOR SHARE` (row lock) |
| :279, :285, :286, :287, :288, :293 | same | no hold yet; payment `COMMITTED`; FAILED+HOLD_SET; order payment→reaction; debts +10; next payment refused by hold | ⚑ REWRITE IN PLACE (stage 2: patch target `refuse_inactive_equivalents` :253-261 → `money_boundary`; stage 4) — T1546 |
| :317, :318 | `test_step5c_p_the_owner_lock_comes_before_the_authoritative_snapshot` | reaction waited on the owner lock | ⚑ TO WRITE (stage 5) if the reaction's owner lock (reconciliation.py:972, :1146) is removed: reaction under SERIALIZABLE does not hold an equivalent whose ledger was repaired concurrently |
| :327, :333, :337, :338 | same | verdict FAILED first; repair landed during the wait; HOLD_NOT_CONFIRMED; no hold | ⚑ REWRITE IN PLACE (stage 2: holder :314 via `money_boundary`; stage 5 per row above) — T1546 |
| :371, :372 | `test_step5c_p_a_clearing_that_waited_behind_the_reaction_refuses_in_its_fresh_snapshot` | clearing waited on the reaction's owner lock | ⚑ TO WRITE (stage 5): clearing conflicts with the hold UPDATE and its retry owner re-reads and refuses |
| :376, :379, :391, :396 | same | HOLD_SET; clearing refused by hold; debts unchanged; no CLEARING tx | ⚑ REWRITE IN PLACE (stage 5): helpers `_seed_interlock_case`/`_use_serializable` (:58-62) relocate (C7) — T1546 |
| :397 | same | caller's clearing session left no transaction open | REWRITE IN PLACE (stage 5): today's rollback of the caller session belongs to the pinned-connection interlock (C12) |
| :398 | same | no advisory lock held | DROP (stage 5): advisory locks |
| :438, :439 | `test_step5c_p_a_reaction_waits_for_a_clearing_that_already_read_the_hold` | reaction waited on the clearing's owner lock | ⚑ TO WRITE (stage 5): reaction serialized after the clearing (row lock / 40001 + retry) |
| :445, :446, :447, :448 | same | cleared 30; FAILED+HOLD_SET; order clearing→reaction; hold set | ⚑ REWRITE IN PLACE (stage 5): pause point `_locked_pairs_for_equivalent` (:423-431) is removed (clearing/service.py:996) — new seam after the hold read |
| :490, :491, :494 | `test_step5c_p_an_expired_payment_in_a_held_equivalent_is_aborted_as_expired` | TTL branch precedes the hold check in `PaymentEngine.commit`; expired payment `ABORTED` | DROP (stage 4): TTL branch of the engine commit over a durable `PREPARED` + expired `PrepareLock`; the setup (:468-482) cannot even be inserted after CHECK `030` |
| :538, :539 | `test_step5c_p_the_admin_clear_waits_for_the_owner_lock` | admin clear waited on the owner lock | DROP (stage 5): owner lock of admin clear (admin.py:1444); ordering against payment/clearing is the stage-2 admin-path race (T1903) |
| :543, :544 | same | clear returns 200; hold cleared | ⚑ REWRITE IN PLACE (stage 2 holder :530; stage 5 holder removed) — T1546 admin clear |

#### `tests/integration/test_p015_t1525_control_postgres.py` (REWRITE, stages 2/3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :259 | `test_postgres_an_aborted_payment_commit_leaves_debts_unchanged` (via `_scenario_engine_commit_violates_after_flows`) | premise: engine prepared the payment (`PREPARED`) | DROP (stage 4): durable `PREPARED` + fresh-session `PaymentEngine.commit` |
| :290-301 (helper `_assert_aborted_payment_left_no_debt`, engine scenario) | same | violation after flows → `ABORTED`, debts unchanged | ⚑ SURVIVES: `tests/integration/test_p015_t1525_control_postgres.py::test_postgres_an_aborted_service_payment_leaves_debts_unchanged` (:606-616, same helper :288-301) — T1525 |
| :290 | `test_postgres_an_aborted_service_payment_leaves_debts_unchanged` | integrity violation raised to the caller | REWRITE IN PLACE (stage 3): as surfaced by `pay()` |
| :291 | same | violation raised after the flow wrote the debt (observed in the committing session) | ⚑ REWRITE IN PLACE (stage 2): patch target `PaymentEngine.check_payment_delta` (:182) moves to `money_boundary`; replacement cannot use engine-private `self._get_debt` (:168) |
| :295 | same | tx stored `ABORTED` | ⚑ REWRITE IN PLACE (stage 3): FORK-4 table does not classify an integrity violation (non-business, non-retryable) — decide in T1902 (C4); Q1 |
| :296 | same | `prepare_locks_left == 0` | DROP (stage 5): table |
| :298 | same | debts unchanged | ⚑ REWRITE IN PLACE (stage 3), unchanged — T1525 |
| (new) | same | no `COMMITTED` row, no envelope after the failed delta check inside the operation savepoint | ⚑ TO WRITE (stage 3): spec Verification §2 item 3 requires "конверта нет"; this test does not assert envelopes today |
| :553, :555-557, :558, :559, :561 | `test_postgres_a_rolled_back_tick_leaves_no_payment_from_the_executor`, `test_postgres_a_real_tick_failing_after_payments_leaves_no_payment` | staged payments COMMITTED inside the tick; tick failure recorded; resolution `rollback`; no `tx.updated`; no tx and no debt stored | ⚑ REWRITE IN PLACE (stage 3, setup only if `create_payment_internal_staged` :355-363 is renamed) — T1525 |

### 5.5. G4b — Unit-тесты 015 и стоп T1544

#### `tests/integration/test_p015_t1544_operator_stop_races_postgres.py` (REWRITE, stages 2/3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :150-158 | `_assert_stop_refusal` (shared by :237, :369, :716) | ⚑ T1544 refusal: `ConflictException` E008, reason `equivalent_inactive`, `equivalents==[code]`, not retryable | REWRITE IN PLACE (stage 2): constant from `money_boundary` |
| :192-199 | `test_a_payment_prepared_before_the_stop_and_waiting_behind_it_is_refused` (:172) | barrier on `PaymentEngine.commit` between prepare and commit (setup) | REWRITE IN PLACE (stage 3): in one transaction the payment holds the owner lock continuously from step 2, so a PATCH arriving after "prepared" waits for the payment (that is scenario :651). To keep "payment snapshot older than the PATCH, then waits behind it", the barrier moves to **after the payment's first read and before its owner-lock acquisition**; stage 4: target moves to the `money_boundary` owner-lock function |
| :216 | same | premise: `{tx_id: "PREPARED"}` visible from another session | REWRITE IN PLACE (stage 3): PREPARED is no longer visible (spec §1 reproducer); premise becomes "payment snapshot taken, owner lock not held" |
| :225, :228 | same | premise: payment waits on the PATCH's owner lock (advisory waiter) | REWRITE IN PLACE (stage 5): wait measured on the row lock / SSI instead of `locktype='advisory'` |
| :232 | same | ⚑ PATCH returns 200 | REWRITE IN PLACE — unchanged assert |
| :234-237 | same | ⚑ T1544 payment refused by the stop | REWRITE IN PLACE (stage 2 constant) |
| :238-243 | same | premise: refusal came through `FOR SHARE` 40001 (`event=payment.uow_retry op=commit`) | REWRITE IN PLACE (stage 3): retry owner becomes `pay()`; log marker changes, `pgcode=40001` premise stays |
| :244 | same | ⚑ debts unchanged | REWRITE IN PLACE — unchanged assert |
| :245 | same | ⚑ `{tx_id: "ABORTED"}` | REWRITE IN PLACE (stage 3): after the `pay()` retry on a fresh snapshot the refusal comes from the pre-insert check (`service.py:722`) → no row. Expected value set by T1902/T1905 (finding F2) |
| :246 | same | prepare_locks rows of the world == 0 | DROP (stage 5): table `prepare_locks` removed (reservation-release contract) |
| :247 | same | equivalent inactive after | REWRITE IN PLACE — unchanged |
| :288-296 | `test_a_deactivating_patch_waits_for_a_clearing_that_already_read_the_flag` (:267) | barrier on `ClearingService._locked_pairs_for_equivalent` (setup) | REWRITE IN PLACE (stage 5): method removed (`clearing/service.py:996`); barrier moves to after the clearing's stop read, before its debt writes |
| :304, :308 | same | premise: PATCH waits on the clearing's owner lock | REWRITE IN PLACE (stage 5): ⚑ holds only if clearing's stop read becomes `FOR SHARE` (finding F1) |
| :314-317 | same | ⚑ clearing committed 30, PATCH 200, **order `["clearing","patch"]`** (T1544 cutoff), inactive after | REWRITE IN PLACE — unchanged asserts; stage 5 must keep them green (F1) |
| :358, :361 | `test_a_clearing_that_waited_behind_the_patch_refuses_in_its_fresh_snapshot` (:334) | premise: clearing waits on the PATCH's owner lock | REWRITE IN PLACE (stage 5): clearing waits on the row lock / meets 40001 and its retry owner (T1907) re-reads |
| :365, :367-369 | same | ⚑ PATCH 200; clearing refused by the stop | REWRITE IN PLACE (stage 2 constant) |
| :388-393 | same | ⚑ three debts unchanged incl. `version==1`; 0 CLEARING transactions | REWRITE IN PLACE — unchanged |
| :394 | same | clearing session left no open transaction | REWRITE IN PLACE — unchanged |
| :395 | same | `_no_advisory_lock_is_held` (imported from interlock module) | DROP (stage 5): session/advisory owner-lock leak contract removed with the interlock |
| :462, :465 | `test_a_tick_that_waited_behind_the_patch_discards_its_attempt_and_the_replay_refuses` (:433) | premise: tick attempt waits on the PATCH's owner lock | REWRITE IN PLACE (stage 5): row-lock wait |
| :469, :472-479 | same | ⚑ PATCH 200; exactly one money-phase replay with `conflict=RETRYABLE_PAYMENT_CONFLICT`; a staged payment raced | REWRITE IN PLACE — unchanged (money replay preserved, spec §2 `money_replay:530`) |
| :480-483 | same | outcomes `[Retryable…, ConflictException:equivalent_inactive]` | REWRITE IN PLACE (stage 2 constant; stage 3): if T1905 returns a structural `ABORTED` result for the stop, the recorder must read the reason from `result.error` (F3) |
| :485 | same | ⚑ debts unchanged | unchanged |
| :486 | same | ⚑ `_transactions == {}` | REWRITE IN PLACE (stage 3): depends on whether a staged stop refusal becomes durable `ABORTED` (T1905, F3) |
| :487 | same | prepare_locks == 0 | DROP (stage 5): table removed |
| :490-494 | same | ⚑ nothing published (`tx.updated`==0), counters: committed 0, errors 0, replays 1, committed ticks 1 | REWRITE IN PLACE — unchanged |
| :528, :552-553 | `test_a_commit_guard_conflict_on_every_attempt_exhausts_the_budget_without_money` (:510) | replay limit; every attempt `RetryablePaymentConflictException` | REWRITE IN PLACE — unchanged (`execute()` propagates the conflict) |
| :554-559 | same | ⚑ exactly one `money_phase_replay_exhausted` | unchanged (spec §2 `money_replay:751`) |
| :560-561 | same | ⚑ debts unchanged, no transaction rows | unchanged |
| :562 | same | prepare_locks == 0 | DROP (stage 5): table removed |
| :563-568 | same | ⚑ nothing published, errors 0, `REAL_MODE_MONEY_CONFLICT_UNRESOLVED`, exhausted 1, no-progress 1, still active | unchanged |
| :590-634 | `test_an_expired_payment_in_a_deactivated_equivalent_is_aborted_as_expired` (:577) | hand-written PREPARED + expired `PrepareLock`; premises | DROP (stage 4): durable PREPARED forbidden by CHECK `030`, no TTL |
| :636-643 | same | `PaymentEngine.commit` refuses as "expired before commit", not as stop | DROP (stage 4): removed contract "TTL branch of `PaymentEngine.commit` precedes the T1544 guard" |
| :644-645 | same | ABORTED + debts unchanged after expiry | DROP (stage 4): expiry/recovery contract removed with `recovery.py` and TTL |
| :668-676 | `test_a_patch_arriving_while_a_payment_holds_the_stop_check_waits_for_that_payment` (:651) | barrier: patch `PaymentEngine.refuse_inactive_equivalents` (setup) | REWRITE IN PLACE (stage 2): patch target `money_boundary.refuse_inactive_equivalents` |
| :698, :701 | same | premise: PATCH waits for the payment (advisory) | REWRITE IN PLACE (stage 5): PATCH's `UPDATE equivalents` waits on the payment's `FOR SHARE` row lock (`pg_locks` transactionid/tuple) |
| :707-712 | same | ⚑ payment COMMITTED, PATCH 200, order `["payment","patch"]`, debts +10 | unchanged |
| :714-719 | same | ⚑ next payment refused by the stop, debts unchanged | unchanged (stage 2 constant) |
| :784, :787 | `test_an_inject_that_waited_behind_the_patch_is_refused_and_writes_nothing` (:735) | premise: inject waits on the PATCH's owner lock | REWRITE IN PLACE (stage 5): row-lock wait; inject isolation per T1907 |
| :791, :796-811 | same | ⚑ PATCH 200; exactly one `inject.refused_equivalent_inactive`; `inject.transient_retry` (40001) seen | unchanged |
| :812-821 | same | ⚑ debts unchanged, 0 envelopes for the identity, event consumed | unchanged |
| — | (missing) | ⚑ "owner before row" vs **`DELETE /admin/equivalents/{code}`** (`admin.py:1532`, owner `:1552`) raced by a real payment and a real clearing | TO WRITE (stage 2). Not in this group; `test_p015_b4_entries_and_money_postgres.py` c17_p (~:1762-1935) races DELETE vs payment — owning group must confirm it asserts wait order and no `40P01` |
| — | (missing) | ⚑ "owner before row" vs **integrity-hold clear** (`admin.py:1444` → `:1451`) raced by a real payment and a real clearing | TO WRITE (stage 2). Existing `test_p015_step5c_hold_races_postgres.py:503` races the clear only against a bare lock holder, not money |
| — | (missing) | ⚑ absence of `40P01` and wait order by `pg_locks` on the PATCH races (:172, :267, :334, :651) | TO WRITE (stage 2): the existing PATCH races assert the wait but never assert "no 40P01" |

**Coverage of the three admin paths (spec T1903):** PATCH — covered (payment both orders :172/:651, clearing both orders :267/:334, tick :433, inject :735). DELETE equivalent — not in this file (TO WRITE). Hold release — not in this file (TO WRITE; hold *set by reaction* races live in `step5c_hold_races_postgres`).

#### `tests/integration/test_p015_t1544_operator_stop_refuses_money.py` (REWRITE, stages 2/3/4/5)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :172-180 | `_assert_stop_refusal` (HTTP) | ⚑ 409 E008, reason, equivalents, not retryable | REWRITE IN PLACE (stage 2 constant) |
| :198-205 | `test_a_payment_in_a_deactivated_equivalent_is_refused_before_any_transaction_exists` (:184) | ⚑ refused, no debt, **no transaction row** | unchanged (pre-check stays before insert) |
| :214-216 | same | ⚑ reactivation restores money | unchanged |
| :236-238, :243-249 | `test_an_accepted_payment_still_replays_its_result_after_the_stop` (:220) | ⚑ T1523 cell 1: one COMPLETED envelope; replay 200/COMMITTED same tx_id; debts, transactions, envelopes, entries unchanged | unchanged (identity resolver of stage 3 must keep stored result before the stop check) |
| :258 | same | ⚑ stop in force (control) | unchanged |
| :277-290 | `test_a_payment_prepared_before_the_stop_is_refused_at_commit` (:262) | stand: patched `PaymentEngine.commit` deactivates and **commits the payment's session** between prepare and commit | REWRITE IN PLACE (stage 3): stop must commit from another session after the payment's pre-check and before its binding `FOR SHARE` read (barrier at the `money_boundary` stop read); stage 4: no `PaymentEngine` |
| :295-297 | same | premise: state at commit == PREPARED | REWRITE IN PLACE (stage 3): premise becomes "pre-check passed with `is_active=True`" |
| :298-299 | same | ⚑ refused by the stop, no debt | unchanged |
| :300-303 | same | ⚑ state ABORTED | REWRITE IN PLACE (stage 3): value per T1902 (F2 — after 40001 retry the pre-check refuses with no row) |
| :304-309 | same | `PrepareLock` count 0 | DROP (stage 5): table removed |
| :336, :341-351 | `test_clearing_in_a_deactivated_equivalent_is_refused_and_keeps_the_debts` (:314) | ⚑ auto-clearing refused, debts kept; clears after reactivation | unchanged |
| :440-446 | `test_clearing_real_reports_the_stop_as_its_declared_409` (:388) | ⚑ 409 CONFLICT with reason, not retryable, debts kept | unchanged (stage 2 constant; `simulator.py:1974` consumer) |

#### `tests/integration/test_p015_t1544_operator_stop_through_the_tick_sqlite.py` (REWRITE, stages 2/3)

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :211-233 | `test_a_refused_inject_is_consumed_and_does_not_fail_the_run` (:182) | ⚑ run not charged, refusal once, one note, consumed, no debt, 0 envelopes | unchanged |
| :267-286 | `test_a_refused_tick_clearing_does_not_spend_the_error_budget` (:237) | ⚑ run not charged, phase cleared, refusal every tick, debts kept | unchanged |
| :311-322 | `test_a_refused_staged_payment_is_rejected_and_the_tick_continues` (:290) | recorder of staged outcomes (expects a raise) | REWRITE IN PLACE (stage 3): accept structural `ABORTED` result with `error.reason` (T1905, F3) |
| :328-330 | same | every staged outcome is the stop refusal | REWRITE IN PLACE (stage 2 constant; stage 3 shape) |
| :331-334 | same | ⚑ `rejected_total == refusals`, committed 0, run not charged, no debt | unchanged |
| :335-336 | same | ⚑ `Transaction` count 0 | REWRITE IN PLACE (stage 3): per T1905 decision on durability of a staged pre-insert stop refusal (F3) |

#### `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py` (REWRITE, stage 4)

Helper map (callers in brackets):

| helper (line) | touches PaymentEngine / PREPARED? | stage-4 fate | importers |
|---|---|---|---|
| `ATOM` :69, `_Triangle` :77, `_seed_triangle` :96, `_edges` :133 | no | keep | step5a unit :78-85, step5b unit :65-74, step5c unit :68-73, step5a PG :41-44, step5b PG :49-54 (`ATOM` only step5b unit) |
| `_prepare_payment` :409-437 | **yes**: NEW row + `PaymentEngine.prepare` (durable PREPARED + `prepare_locks`) | replace with an explicit-route execution helper (F4) | step5a unit, step5b unit, step5c unit, **step5b PG** (outside group) |
| `_intent_flows` :440-467 | **yes**: reads `PrepareLock.effects` | replace by the test's own declared route (path × amount); not imported elsewhere | local only |
| `_collapse_the_route` :751-770 | **yes**: patches `PaymentEngine._apply_flow` (forwarder kept by 018 `T1802`, `engine.py:1714`) | re-target to a per-flow seam in the direct path, e.g. `app.core.ledger.book._apply_payment_flow` (`book.py:305`, called by global name `:582`) (F5) | step5a unit, step5b unit |
| `_audit` :470, `_tx_state` :483 | no (read-only) | keep; `_audit` needs the payment audit row to survive stage 4 (F6) | step5a unit, step5b unit, step5c unit (`_tx_state`), step5b PG (`_tx_state`) |
| `_under_clear_by_one_atom` :1033 | no (patches `ClearingService._execute_clearing_with_amount`) | keep; stage 5 clearing retry owner must keep this seam or retarget (hits==3 assumes one attempt) | step5b unit |

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :538-542 | `test_c5_the_journal_of_an_honest_payment_equals_the_change_and_the_intent` (:496) | setup: prepare, intent snapshot, engine commit | REWRITE IN PLACE (stage 4): explicit-route payment via service |
| :547-551 | same | ⚑ COMMITTED; before/after state of C5 | unchanged |
| :555 | same | declared flows `[(a,b,5)]` from `prepare_locks` | REWRITE IN PLACE (stage 4): flows from the test's declared route |
| :556-560 | same | ⚑ criterion (b) implied == after | unchanged |
| :563-570 | same | ⚑ criterion (a) journal totals == observed change | unchanged |
| :579-595 | same | ⚑ three entries strictly ordered; first entry B→A 7→2 | unchanged |
| :639-645 | `test_c13_a_replayed_payment_commit_leaves_exactly_one_envelope` (:612) | setup: engine commit, then engine commit replay (early return) | REWRITE IN PLACE (stage 4): replay = second service call with the same tx_id (stored result). Duplicate effect also in `refuses_money.py::test_an_accepted_payment_still_replays_its_result_after_the_stop` (:235-247) |
| :652 | same | `replayed is True` (engine return shape) | REWRITE IN PLACE (stage 4): replay returns COMMITTED with the same tx_id |
| :653-658 | same | ⚑ money unchanged by replay, COMMITTED | unchanged |
| :660-666 | same | ⚑ exactly one envelope after replay (015 C13) | unchanged |
| :717-743 | `test_c13_a_replayed_clearing_leaves_exactly_one_envelope` (:670) | ⚑ clearing replay: same amount, one CLEARING tx, one envelope | unchanged (no engine) |
| :838-843 | `test_c6_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today` (:774) | setup: prepare A→B→C with C→A line present, wrapper, commit | REWRITE IN PLACE (stage 4): explicit route is mandatory — with the C→A line the router would pick A→C directly (F4) |
| :857 | same | ⚑ wrong writer ran once per declared segment | unchanged (needs the F5 seam) |
| :860-871 | same | ⚑ empty before; committed `A→C 5`; COMMITTED; **audit `[True]`** | unchanged (needs F6) |
| :876-888 | same | declared (from `prepare_locks`) disagrees with committed state | REWRITE IN PLACE (stage 4): declared = the test's route |
| :891-918 | same | ⚑ one PAYMENT envelope COMPLETED; stored intent == declared; (b) refutes | unchanged |
| :921-935 | same | ⚑ criterion (a) faithful | unchanged |
| :972-976 | `test_c6_control_the_same_payment_without_the_wrapper_satisfies_criterion_b` (:939) | setup | REWRITE IN PLACE (stage 4) |
| :979-986 | same | ⚑ COMMITTED, two-hop state | unchanged |
| :989-993 | same | half one: (b) over the `prepare_locks` snapshot | REWRITE IN PLACE (stage 4): over the declared route |
| :997-1025 | same | ⚑ half two over stored intent; intent == declared | unchanged |
| :1172-1239 | `test_c6_a_clearing_cycle_that_leaves_one_atom_on_every_edge_is_still_verified` (:1069) | ⚑ C6(ii) | unchanged |
| :1303-1340 | `test_c6_control_the_same_cycle_without_the_listener_satisfies_criterion_b` (:1243) | ⚑ C6(ii) control | unchanged |

#### `tests/unit/test_p015_step5a_reconciliation.py` (REWRITE (setup-only), stage 4)

Setup lines that change; **every assertion stays in place**:
- `:64` `from app.core.payments.engine import PaymentEngine` — remove.
- `:78-85` imports `_prepare_payment`, `_collapse_the_route` — follow the helper rewrite (F4, F5).
- `:189-193` `_pay` = `_prepare_payment` + `PaymentEngine.commit` — one explicit-route payment call. Also imported by step5b unit (:75-86) and step5a PG (:50).
- `:268`, `:302-304` `interleave_a_payment_between_the_verifiers_reads` — prepare before the pause, commit during it → run the whole payment during the pause (the prepare-before-pause is not load-bearing; premises :318-327 unchanged). Imported by step5a PG (per 018 manifest `:330`).
- `:979-982` C6 drive in `test_step5a_c6_still_commits_verified_and_criterion_a_is_blind_to_it_until_the_book_moves` (:959) — explicit route + seam.

⚑ tests kept verbatim: :345 (one-atom FAILED), :391, :558 (interleave), :674, :768, :959 (C6 (a)-silent/(b)-FAILED).

#### `tests/unit/test_p015_step5b_criterion_b.py` (REWRITE, stage 4)

Setup-only lines (assertions stay): `:49` import; `:315-318` (:300 C6), `:524-527` (:511 v1), `:947-950` (:932 fingerprint) — `_prepare_payment` + `_collapse_the_route` + `PaymentEngine.commit` → explicit-route payment with the F5 seam; `_pay` via step5a. Only :997 changes assertions:

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :1016-1020 | `test_step5b_the_prestate_is_one_read_after_the_operator_stop_and_before_the_envelope` (:997) | setup: prepare, then `PaymentEngine.commit` under a statement recorder | REWRITE IN PLACE (stage 4): record one whole payment |
| :1022 | same | COMMITTED | unchanged |
| :1024-1030 (`stops`, `envelopes`) | same | ⚑ one stop read, one envelope INSERT | unchanged |
| :1026-1031 (`ttl`, `ttl < stops`) | same | TTL read of `prepare_locks` precedes the stop | DROP (stage 4): TTL branch removed with durable PREPARED (T1544 precedence contract) |
| :1035-1045 | same | ⚑ between stop and envelope exactly the Book opening (2 statements) + one batched `debts` read (intent v2 prestate from the read before the write, spec §2) | REWRITE IN PLACE (stage 4): in the spec's order (owner → stop → routing reads → operation savepoint → `INSERT transactions` → Book) routing reads and the transaction INSERT fall between the anchors; re-anchor (e.g. operation savepoint → envelope) and fix whether the prestate is the routing read (spec "Общие шаги" 3) or a separate batched read (F7) |

#### `tests/unit/test_p015_step5c_reaction_and_hold.py` (REWRITE, stages 2/4)

Setup-only lines: `:50` import; `:556-562` `_assert_hold_refusal` constant (stage 2); `:827` constant (stage 2); `:70` `_prepare_payment` import (stage 4). Assertions changing:

| file:line | test function | effect checked | fate |
|---|---|---|---|
| :616-620 | `test_step5c_inactive_and_held_is_refused_as_inactive` (:600) | ⚑ prepare-time: inactive wins over hold | unchanged |
| :621-625 | same | direct call `PaymentEngine.refuse_inactive_equivalents(row_lock=True)` | REWRITE IN PLACE (stage 2): call `money_boundary.refuse_inactive_equivalents` |
| :626-629 | same | ⚑ reason `equivalent_inactive` at both points | unchanged (stage 2 constant) |
| :655-659 | `test_step5c_a_payment_prepared_before_the_hold_is_refused_at_commit_before_the_envelope` (:634) | setup + premise: durable PREPARED before the hold | REWRITE IN PLACE (stage 4): no durable PREPARED; stand = hold committed after the best-effort pre-check (`service.py:732`) and before the binding read (barrier), or the pre-check bypassed |
| :660-662 | same | ⚑ real scheduled reaction set the hold | unchanged |
| :664-670 | same | ⚑ T1546 commit-time hold refusal | REWRITE IN PLACE (stage 4): drive via the payment, not `PaymentEngine.commit` |
| :671-673 (`ttl`) | same | TTL read precedes the stop | DROP (stage 4): TTL branch removed |
| :671-673 (`stops`) | same | ⚑ hold read in the same statement as the stop, once | unchanged |
| :674-680 | same | ⚑ no envelope / debts write before the refusal; no envelope at all | unchanged |
| :681 | same | ⚑ state ABORTED | REWRITE IN PLACE (stage 4): per T1902 (F2 — in the stage-4 order the binding stop read precedes the `Transaction` INSERT) |
| :682 | same | ⚑ debts unchanged | unchanged |

⚑ tests otherwise unchanged: :224, :281, :327, :356, :389, :417, :442, :481, :516, :567, :728, :767, :853.

## 6. Находки и счёт проходов

Сведены в раздел 3 (номера находок — ссылки оттуда). Счёт — как записал проход; сводный счёт раздела 2 пересчитан скриптом и главенствует.

### 6.1. G1 — находки

- **F1 — pair/tx advisory locks have no home between stage 4 and stage 5.** Owner surface puts pair locks, tx lock and the owner session lock in stage 5, but they live in `engine.py` (`_acquire_segment_advisory_lock_keys`, `_acquire_tx_advisory_lock` `:237`, `acquire_session_equivalent_owner_lock` `:207`, `release_session_equivalent_owner_lock` `:226`), which stage 4 deletes. Stage 4 must either re-home them (then G1 files `advisory_lock_key`, `pair_advisory_locks`, part of `advisory_locks_execute`, the pair-wait premise of `inverse_multisegment:282-288` move to stage 5) or remove them in stage 4 (then they are stage-4 DROPs and the stage-4 direct path runs without pair locks before T1907/T1908 evidence exists — contradicts «локи снимаются только после принудительной изоляции»). Spec must say which.
- **F2 — stage-2 primitive list is incomplete.** Besides `refuse_inactive_equivalents`, `MONEY_STOP_REASONS`, owner lock, `check_payment_delta`/`_snapshot_net_positions`, consumers import: `_DELTA_DRIFT_TOLERANCE` (`engine.py:89`; t1522 `:166`, p012 `:483`), `_EQUIVALENT_OWNER_LOCK_NAMESPACE` (`engine.py:94`; p017 `:202`), `_equivalent_owner_lock_key` (`:158`), `acquire_staged_equivalent_owner_locks` (`:184`), `acquire_session_equivalent_owner_lock`/`release_session_equivalent_owner_lock` (`:207`, `:226`), `EQUIVALENT_INACTIVE_REASON`/`EQUIVALENT_INTEGRITY_HOLD_REASON` (`:378`, `:383`), `inactive_equivalent_conflict`/`integrity_hold_conflict` (`:390`, `:397`), the lock budget `_set_local_advisory_lock_timeout`/`_advisory_lock_budget_s` used by the owner lock (`:175`, `:126`).
- **F3 — stage-2 owner surface misses app consumers:** `app/core/clearing/service.py:167` (`release_session_equivalent_owner_lock`) — only `:100`, `:1626` listed; `app/core/simulator/real_runner_impl.py:737`, `:759` (`MONEY_STOP_REASONS`, `EQUIVALENT_INACTIVE_REASON`) — only `:616`, `:637` listed; `app/core/payments/service.py:218` (`self.engine`), `:722`, `:732` (`PaymentEngine.inactive_equivalent_conflict`/`integrity_hold_conflict`) — only `:502-534` listed; `app/api/v1/admin.py:1171` (admin abort via `PaymentEngine`, stage 4). Tests: `test_p015_t1523_replay_after_a_hold_or_an_abort.py:247` reads `PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON`.
- **F4 — FORK-4 table lacks two classes the wire tests pin today:** internal failure (OperationalError/typed server error → stored `ABORTED` E010, taxonomy `:482-505`, `:559-568`, `:1575-1584`) and cancellation (stored `ABORTED` E007 "Payment cancelled", taxonomy `:1046-1051`, `:1108-1113`). T1902 must characterise both per API/staged; and with one commit, `cancel after COMMIT returned` (taxonomy `[insert]` `:1079-1086`) means COMMITTED, not ABORTED.
- **F5 — spec §3 says `test_payment_idempotency_postgres.py` and `test_payment_inverse_multisegment_postgres.py` stay green**, but the first asserts «in progress» (`:181`, removed by stage 3), runs READ COMMITTED (`:119-125`, refused in stage 5) and patches `service.engine.prepare` (`:149`); the second seeds `PREPARED`+`PrepareLock` and calls `engine.commit` (`:96`, `:121-166`, `:277`). Both need stage 3/4/5 rewrites; §3 should list them as "rewritten in place, final asserts kept".
- **F6 — §2 mandatory selector `test_p015_p1_money_replay_postgres.py` imports `PrepareLock` (`:75`) and asserts `_prepare_locks(...) == 0` (`:593`, `:671`, `:743`)** — needs a stage-5 edit (other group; flagged because G1 maps survivals onto `:530`).
- **F7 — guard selector.** `tests/unit/test_the_tier_refuses_a_database_that_is_not_postgres.py:28` uses `tests/integration/test_payment_engine_uow_retry_postgres.py` (DROP stage 4) as its collection selector — stage 4 must retarget it. AGENTS.md `:178`, `:211`, `:252`, `docs/ru/06-contributing.md:444`, `docs/ru/testing/quick-start-and-debugging.md:20` cite `test_payments_2pc.py` / `test_payment_engine_uow_retry_postgres.py` as canonical cheap-gate examples — update in stage 4.
- **F8 — guard names a node.** `tests/unit/test_p017_required_gate_runs_on_postgres.py:73` names `test_payment_idempotency_postgres.py::test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres`; a stage-3 rename breaks it (also `…bottleneck…` and `…lost_update…` nodes there for stage 5).
- **F9 — observability names tied to engine phases.** Metric labels `PAYMENT_EVENTS_TOTAL{event=prepare|commit|abort}` are emitted only in `engine.py` (`:904`…`:2046`; admin `admin.py:1223` for abort); log events `payment.prepare_failed`, `payment.commit_failed`, `payment.uow_retry op=commit` are asserted by taxonomy `:520`, `:835` and audit_conflict `:217`. Spec stage 4 names only `metrics.py:648`; the label set and event names after stage 4 need a decision (AGENTS §12 unique event names).
- **F10 — E002 details carry `reserved`** (`capacity_policy:190`, `:217`; `RoutingException.details`). Stage 5 changes its value (and maybe removes the key); check `api/openapi.yaml` E002 details schema before calling stage 5 "no wire change".
- **F11 — CHECK `030` also bites test seeds that are not in the engine family**: `capacity_policy` seeds `PAYMENT` in `ROUTED` (`:27`) to anchor `PrepareLock` rows; any stage-4/5 test that needs a reservation row for the clearing interlock can only anchor it on a `COMMITTED`/`ABORTED` payment (or another type) — and after stage 4 no code writes `prepare_locks`, so interlock tests between stages 4 and 5 test an always-empty table (premise change for the clearing group).
- **F12 — spec §4 bans DBAPI-error injection as evidence; G1 carries three such stands** (`uow_retry_postgres:141-152`, taxonomy `_serialization_failure()` `:148-157` used at `:162`, `:213`, `:255`, `:298`). The taxonomy ones are wire-mapping tests (acceptable as unit of the classifier boundary); the stage-3 TO WRITE retry tests must use real schedules.
- **F13 — journal-history premise.** The T1529 journal-history race (`commit_advisory_locks:647`) measured that a live-sized `debt_operations` flips 40001→23505; the stage-3 identity-race TO WRITE should run with journal history too, or state why the `transactions.tx_id` collision is plan-independent.

#### G1 — счёт прохода

Files (24; 8 023 lines):

| fate | files | lines |
|---|---|---|
| DROP | 10 | 2 944 |
| REWRITE (with assertion map) | 9 | 4 075 |
| REWRITE (setup-only) | 4 | 883 |
| KEEP | 1 | 121 |

Per stage (a REWRITE file counts in every stage it lists; DROP files at their deletion stage):

| stage | DROP files | REWRITE files touched |
|---|---|---|
| 2 | 0 | 6 (advisory_locks_execute, staged_multicall, p017_default_tier, t1522, delta_check, p012_rt1) |
| 3 | 0 | 4 (error_taxonomy, apply_flow_retry, t1529, idempotency) |
| 4 | 9 (incl. 2 "4/5": advisory_lock_key, pair_advisory) | 9 (advisory_locks_execute, capacity_policy, error_taxonomy, staged_multicall, apply_flow_retry, t1529, idempotency, inverse_multisegment, staged_post_commit) |
| 5 | 1 (prepare_locks_fk) | 6 (advisory_locks_execute, capacity_policy, staged_multicall, idempotency, inverse_multisegment, p017_default_tier) |

Assertion rows (section B, 17 tables): **143 rows** (counted by script over the tables; 62 carry ⚑)

| fate | rows | ⚑ rows |
|---|---|---|
| REWRITE IN PLACE | 67 | 35 |
| SURVIVES | 13 | 10 |
| TO WRITE | 26 | 13 |
| DROP | 37 | 4 |

(Rows with a split fate are counted by their first-named fate. The 4 ⚑ DROP rows are T1529 commit-phase-race premises (`commit_advisory_locks:566-587`, `:690-705`, `:716-739`) and the Q2 live-abort row (`:1274-1277`); their money/identity halves are mapped on sibling TO WRITE/SURVIVES rows.)

### 6.2. G2 — находки

1. **Stage 4 algorithm omits three effects of `PaymentEngine.commit`.** Spec «Стадия 4» lists `Transaction` COMMITTED + `Book.post` + `check_payment_delta`. The engine also runs `InvariantChecker.check_trust_limits` and `check_debt_symmetry` (`app/core/payments/engine.py:1569-1583`), writes the FIX-014 `IntegrityAuditLog` row per equivalent (`:1601-1662`), and increments `PAYMENT_EVENTS_TOTAL{commit,success}` (`:1702-1706`). `tests/unit/test_invariants.py:203-207` and `:539-540` assert the first two. Stage 4 must carry them or record their removal with a date.
2. **CHECK `030` breaks setup outside the manifest's view.** Tests that insert `PAYMENT` in a non-terminal state fail on the immediate CHECK. In G2 (and outside both greps): `tests/debt_setup.py:156-165` (`writer_operation(kind="PAYMENT")` → 4 callers, listed in (A)); `tests/integration/test_p018_a_serialization_failure_leaves_no_envelope.py:70-71`; `tests/unit/test_admin_incidents_list.py:33,:44,:106`; `test_admin_liquidity_summary.py:78`; `test_admin_whoami_and_extras.py:110`; `test_p015_t1548_…:208`. **Not in either grep at all:** `tests/integration/test_p018_b_book_transaction_contract_postgres.py:517-518` (`type="PAYMENT", state="NEW"`). Across the tree 31 files have a non-terminal state literal (`grep -rnE "state\s*[=:]\s*['\"](NEW|PREPARED|WAITING|ROUTED|PREPARE_IN_PROGRESS|PROPOSED)['\"]" tests`). Needs a tree-wide stage-4 sweep, not per-group mapping.
3. **Vacuous pass after `030`.** `tests/unit/test_the_test_engine_enforces_foreign_keys.py:35-46` expects `IntegrityError` for a dangling FK but seeds `PAYMENT NEW`; the CHECK raises the same class, so the FK test goes green without testing FKs. The fix is to seed COMMITTED and assert `23503`.
4. **A third stuck-list reader is not in the spec.** `app/api/v1/admin.py:248-256` (graph snapshot `include=incidents`) filters on `_ACTIVE_PAYMENT_TX_STATES` (`admin.py:116-123`). The spec lists `:850`, `:1104`, `:1153` and `metrics.py:648`. The consumer test is `tests/unit/test_admin_whoami_and_extras.py:155`.
5. **Recovery config and metrics leak past `recovery.py`.** The runtime-config keys `RECOVERY_ENABLED`, `RECOVERY_INTERVAL_SECONDS` and `PAYMENT_TX_STUCK_TIMEOUT_SECONDS` sit at `app/api/v1/admin.py:366-368`, with settings at `app/config.py:142-147` (`PREPARE_LOCK_TTL_SECONDS` too) and `.env.example:51`. The launch helper `main._record_recovery_iteration` (`app/main.py:255-263`) and the metric emit (`:160-162`) also remain. **`RECOVERY_EVENTS_TOTAL` must NOT be deleted with recovery:** the integrity hold reuses it (`app/core/ledger/reconciliation.py:1029`, `:1109-1111`). The docs are outside the spec's owner surface: `docs/ru/config-reference.md:55-56`, `docs/ru/03-architecture.md:1515-1516`, `docs/ru/simulator/backend/observability.md:9`, `:32`, `docs/ru/09-decisions-and-defaults.md:85` (states "для recovery"), `:250`, `:272`, `:294`, and `docs/ru/simulator/backend/payment-integration.md:177`.
6. **Other users of the phase timeouts.** `COMMIT_TIMEOUT_SECONDS` is also used by clearing (`app/core/clearing/service.py:1580`) and admin abort (`admin.py:1190`). The stage-3 change to "two phase timeouts" (`service.py:776-777`) must not remove the setting. The spec anchor ⚑ `:252` depends on `PREPARE_TIMEOUT_SECONDS`, so it needs a new injection point in stage 3.
7. **T1523 cell 8 silently loses its premise in stage 3.** The child dies inside `PaymentEngine.commit` (`tests/integration/t1523_restart_child.py:59-70`). Once that call is `commit=False` inside `pay()`'s outer transaction, the kill leaves nothing durable, so premise `:256-263` fails. The die point must move to after the outer COMMIT in the same stage. The spec does not mention it.
8. **T1523 cell 5 is also reformulated, and the spec only says so for cell 3.** Its premise that the winner's `NEW` is committed before the loser inserts (`:466-475`, `:546`) cannot exist after stage 3, so the cell merges with the new cell 3 ("the duplicate waits on the index"). The spec's §3 selector list keeps the module as "must stay green", which contradicts expectations `:300-306` and `:480-484` (409 "in progress") that the spec itself changes. The manifest should state that §3 "green" means green after the stage-3 rewrite.
9. **The `client` fixture and "`pay()` opens its own session".** Mode-A HTTP tests (for example ⚑ `test_p015_t1523_replay_after_a_hold_or_an_abort.py`) inject `db_session` through the `get_db` override (`tests/conftest.py:596-597`). If stage-3 `pay()` opens its own SERIALIZABLE session or retries on a fresh session, it cannot see mode-A seeded rows, and its commits land outside the rolled-back outer transaction. Stage 3 must either keep the DI session as the first attempt's session or move HTTP payment tests to mode B. Decide before T1904.
10. **Admin abort compatibility has gaps in the spec.**
    - (a) The endpoint does not filter by type (`admin.py:1159-1166`), so a non-terminal CLEARING row (legal after `030`, `09:120`) is today aborted through `PaymentEngine.abort`. Stage 4 must define the answer (404? 409? `aborted`?).
    - (b) The `PAYMENT_EVENTS_TOTAL{abort,already_aborted}` metric (`test_admin_abort_tx.py:116-117`) is not mentioned.
    - (c) Today's ABORTED path fills `error` only when it is missing (`engine.py:2007-2028`). Compatibility must not overwrite a stored refusal, or the payer's stored-ABORTED replay (Q1, ⚑ cell 2) changes. No test pins "existing error kept": TO WRITE in stage 4.
11. **The cost probe needs a before-baseline of the whole API path.** `tests/p018_t1809_operation_cost_probe.py:189-192` measures only `PaymentEngine.commit` of an already PREPARED transaction. That is useless as the "before" for stage 3's 3→1 commits. Run a whole-`POST /payments` variant on the pre-stage-3 tree first. The probe also imports `_prepare_payment` (real `PaymentEngine.prepare`), which disappears in stage 4.
12. **The stage-3 classifier change needs its negative controls placed.** The spec §2/§4 counter-checks (raw ORM errors are not retryable; only `StaleDataError` from `Book` is translated) naturally belong beside `tests/unit/test_p015_p1_money_conflict_predicate.py:155-207`. The docstring there (`:158`) cites the stale `engine.py:458`.
13. **The money-replay stand's measured conflict landing point is code-specific.** The docstring (`test_p015_p1_money_replay_postgres.py:9-29`) and `:619` pin "40001 raised inside `create_payment_internal_staged`, never at the outer commit" for today's engine flow. Re-measure it at stages 3 and 4. If the landing point moves to the outer commit, the complete-buffer branch becomes reachable here, and the docstring's "WHAT THAT COSTS" paragraph changes.
14. **Clearing resolver guard.** `tests/integration/test_p1_reconcile_after_failed_rollback_postgres.py` pins the REFUTED verdict of F-010-2 on `_reconcile_committed_execution` (`clearing/service.py:249`). T1907 rebuilds clearing outcome resolution, so the guard must stay green or be re-pointed at the new resolver in stage 5.
15. **ORM model CHECK.** `app/db/models/transaction.py:25` holds the `chk_transaction_state` model constraint. Migration `030`'s PAYMENT-terminal CHECK must be mirrored in the model, or `create_all` and the migrated schema diverge (a 018-style schema-parity test exists for journal CHECKs).
16. **admin-ui consumers and fixtures.** The spec freezes `/incidents` until П4:
    - `admin-ui/src/pages/IncidentsPage.vue:102` (`abortTx`), `:146`, `:156`, `:179`; `admin-ui/src/api/realApi.ts:313` (`incidents_over_sla`), `:917` (`/admin/incidents`), `:922` (`abortTx`).
    - `admin-ui/src/pages/DashboardPage.vue:156-158`, `:635`; `admin-ui/src/pages/LiquidityPage.vue:147`, `:183`; `admin-ui/src/advice/operatorAdvice.ts:52`, `:248-259`; `admin-ui/src/api/adminContracts.ts:79`.
    - Mock tests: `admin-ui/src/api/mockApi.adminMutations.test.ts:112-417`, `admin-ui/src/api/adminMutationIntegrity.contract.test.ts:118-121`, `:182-184`, `:254`.
    - Fixtures: `admin-fixtures/v1/datasets/incidents.json` (3 items: 2 `PREPARE_IN_PROGRESS`, 1 `COMMIT_IN_PROGRESS`, which is not even a valid `chk_transaction_state` value), `admin-fixtures/v1/api-snapshots/admin.incidents.page1.per20.json`, both packs' `datasets/incidents.json`, and the generator `admin-fixtures/tools/generate_admin_fixtures.py:382-390`, `:487-488`.
    - After stage 4 the mock mode shows incidents the real backend can never produce. Record this for T1911; no change in 019.
17. **Docs.** Recovery is mentioned outside the spec's docs owner surface. See item 5; beyond that, `docs/ru/02-protocol-spec.md:243` (single-phase note) needs no change.

#### G2 — счёт прохода

Files: 26 (17 in G2 + 9 extra). Lines total 10 630.

| fate | files | lines |
|---|---|---|
| KEEP | 8 | 4 556 |
| REWRITE (setup-only) | 2 | 500 |
| REWRITE (with assertion map) | 15 | 5 069 |
| DROP | 1 | 505 |

A file is counted once for each stage in which it changes:

| stage | files | which |
|---|---|---|
| 2 | 1 | t1523 replay (constant import) |
| 3 | 7 | restart_child, in_progress, t1523 replay, restart_after_commit, money_replay (conditional), cost probe, t1548 (verify) |
| 4 | 16 | recovery_cleanup, admin_abort, restart_child, in_progress, t1523 replay, p018_a, invariants, debt_symmetry, fk, debt_setup, cost probe, incidents, liquidity, whoami, supervision, t1548 |
| 5 | 4 | in_progress, restart_after_commit, money_replay, fk |

Assertion rows in (B): 125, counted by script over the (B) tables. Each row is classified by the first fate keyword in its fate cell. One row is written per grouped line set; parametrized splits count separately.

| fate | rows | ⚑ rows |
|---|---|---|
| SURVIVES (in place / unaffected) | 62 | 36 |
| REWRITE IN PLACE | 34 | 9 |
| TO WRITE (standalone) | 1 | 0 |
| DROP | 28 | 0 |

⚑ rows total: 45. Sources: money_replay :530/:703/:751/:796 anchors, T1523 matrix cells 1/2/3/5/8, T1546, T1548, 018 T1801, Q2. No ⚑ row is DROP. The TO WRITE count additionally includes items folded into REWRITE/DROP rows (listed below).

The TO WRITE items folded into rows:
- 030 drain refusal/success;
- the audit row on ABORTED repeat;
- a stored error kept on admin abort;
- identity mismatch (fingerprint, initiator, type) → 409 on the API path;
- exhaustion stores no ABORTED;
- a whole-path cost baseline;
- a new timeout injection for cell 2.

### 6.3. G3 — находки

1. **Spec §3 contradiction, stage 5 — `test_clearing_commit_replay_postgres.py` cannot stay green as is.** Spec §3 lists it as a must-stay-green selector. But stage-5 condition 3 (clearing retry owner re-reads cycle rows on a fresh session) inverts `test_serializable_conflict_without_committed_occurrence_stays_failure_postgres` (:293): today the handler `clearing/service.py:2183-2194` only reconciles and raises E010 (:468, :499-505). With the retry owner the fresh attempt re-reads 101.00 and clears 30. Spec must say that this test changes verdict in stage 5, and name the replacement for its "no success without the deterministic transaction" contract (deadline exhaustion → retryable failure, no clearing row). Also its connection-loss variants assert an `AsyncConnection` bind (:683-685) that exists only because of the pinned interlock connection.
2. **Spec stage plan contradiction, stage 4 — files the spec places in stage 5 depend on `PaymentEngine`/intermediate states:**
   - `test_concurrent_clearing_payment_lost_update_postgres.py` patches `PaymentEngine._acquire_equivalent_owner_locks` (:216, :241-245);
   - `test_concurrent_prepare_routes_bottleneck_postgres.py` patches the same method (:79, :107-111), and its second test calls `PaymentEngine.prepare_routes` and seeds `NEW` `PAYMENT` rows (:332-334, :383-398);
   - `test_clearing_skip_releases_locks_postgres.py[locked]` seeds `PREPARED` `PAYMENT` + `PrepareLock` (:119-148);
   - `test_clearing_payment_prepare_interlock_postgres.py` calls `PaymentEngine.prepare` (:366, :513) and its seed writes a `NEW` `PAYMENT` (:276-291);
   - `test_p015_t1543_…` seeds `PREPARED` and calls `PaymentEngine.commit` (:157, :228, :259).
   All of these break at stage 4 (deleted `engine.py`; CHECK `030` refuses a non-terminal `PAYMENT` insert). The owner lock itself survives until stage 5 in `money_boundary.py`, so the lock-barrier tests need a **patch-target change at stage 2 (if the engine stops calling its own method) or 4 at the latest**, and their real rewrite at 5.
3. **Shared helpers broken by stage 4, including §3 selectors.** `_seed_interlock_case` (writes `NEW` `PAYMENT`, :276-291), `_no_advisory_lock_is_held`, `_use_serializable` are imported by `tests/integration/test_p015_step5c_hold_races_postgres.py:58-62` and `tests/integration/test_p015_t1544_operator_stop_races_postgres.py:55-59`. Both are spec §3 selectors. Neither uses `seed["payment_tx_id"]` (grep), so stage 4 must drop the `NEW` row from the seed (or those selectors fail under CHECK `030`). `test_p015_inject_holds_the_owner_lock_postgres.py` imports `PaymentEngine` at module level (:51) and is imported by `test_p015_b4_entries_and_money_postgres.py:84`, :864; `test_p015_f01512_inject_refuses_an_opposing_debt_postgres.py:38`; `test_p015_inject_retries_a_serialization_failure_postgres.py:36`; `test_p018_mixed_inject_event_is_one_operation_postgres.py:51`; `tests/p018_t1809_operation_cost_probe.py:68`. The module import has to be switched at stage 2/4, and at stage 5 the stand (`observed_factory`, `_seed`, `_run`, `_runner`, `_Artifacts`, `_stored`, `_observations`) must outlive the owner-lock assertions. The simplest way is to move it to a support module.
4. **Anti-vacuum, stage 5:** once advisory locks are gone, `_no_advisory_lock_is_held` (:122-153) is true by construction. Its callers in the step5c and t1544 race selectors (`test_p015_step5c_hold_races_postgres.py:398`, `test_p015_t1544_operator_stop_races_postgres.py:395`) and in this file then assert nothing. Stage 5 must drop or replace those calls. Keeping them silently is a green that means nothing.
5. **Stage 3 premise shift:** the lost-update (:184-191), bottleneck (:122-128) and skip (:349-355) tests force READ COMMITTED on the session they hand to `PaymentService.create_payment_internal`. If stage-3 `pay()` opens its own SERIALIZABLE session, those isolation asserts check a session the payment no longer uses, and the test still passes. The bottleneck test sets `PREPARE_TIMEOUT_SECONDS`/`COMMIT_TIMEOUT_SECONDS` without `raising=False` (:51-52), so removing those settings breaks it. Clearing also reads `COMMIT_TIMEOUT_SECONDS` for its interlock connection budget (`clearing/service.py:1577-1582`), so it is a consumer of the stage-3 timeout change.
6. **Stage 5, READ COMMITTED tests the spec does not list.** The spec names only lost-update :184/:263 and bottleneck :123. Checked: lost-update pins RC at **:184-191**. `:263` is the advisory-wait assertion, not an isolation pin. Bottleneck RC is at :122-128 (correct), but its wait mechanism is the monkeypatched `PaymentEngine` owner lock (:79-111), not an observed advisory wait. Two more tests exist. First, `test_clearing_skip_releases_locks_postgres.py:349-355`, which pins RC explicitly. Second, `test_p1_clearing_run_perimeter_postgres.py:45`, which gets RC implicitly: its engine has no `isolation_level`, and clearing inherits the caller's level (`clearing/service.py:1560`, :1590). After FORK-2, `test_interlock_path_refuses_a_cycle_outside_the_perimeter` (:114) would pass because of the isolation refusal (`GeoException`, :129) and would no longer be evidence for the perimeter guard.
7. **Stage 5, engine-bound refusal consumers.** If T1909 drops `clearing/service.py:1495-1504` ("PostgreSQL clearing requires an engine-bound AsyncSession"), then `tests/integration/test_p017_t1702_mode_b_fixture_postgres.py:49`, :316-319 lose their positive control (outside G3). Mode-A tests that relied on the refusal would start executing clearing on the tier. Examples: comments in `tests/unit/test_clearing_additional_cases.py:770`, :806, and `tests/integration/test_p012_t1211_shared_edge_order_postgres.py:9-15`. Stage 5 must decide whether to keep or drop the refusal, and list these consumers.
8. **Stage 5 — gate-pinned node ids:** `tests/unit/test_p017_required_gate_runs_on_postgres.py:69-72` names `test_concurrent_payments_shared_bottleneck_commit_once_postgres` and `test_concurrent_payment_and_clearing_same_trustline_preserve_effects_postgres` by node id, and its check (:418-422) fails on a rename or `slow` marker. The T1908 rewrite must keep both names (or update this guard in the same slice). The docs also reference the bottleneck selector: `docs/en/10-testing-framework.md:99`, `docs/ru/runbook-dev-wsl2-docker-no-desktop.md:315`.
9. **Stage 5 — behaviour change in the bottleneck test.** The loser's stored `ABORTED` (:232-235) holds in stages 3-4. At stage 5 there is no owner lock, so the loser retries on a fresh snapshot and routing refuses it before insertion, which leaves no row. Spec (Q1 table, `service.py:790-880`) already implies this. Name it in T1908 so that nobody "fixes" it by writing `ABORTED`.
10. **Stage 5 — `test_simulator_clearing_no_deadlock.py` positive control.** Its non-vacuity (:253-278, measured 2026-09-24) holds only while the parent holds the owner lock. Without a replacement holder the early-commit mutation goes green again (docstring :16-19).
11. **Stage 2 owner surface is incomplete (app code seen while reading).** `app/core/clearing/service.py:167` (`PaymentEngine(...).release_session_equivalent_owner_lock` in `_release_interlock_session`) and the module import `:26` are not in the stage-2 list (only :100, :1626). The same applies to `app/core/simulator/real_runner_impl.py:737` (`MONEY_STOP_REASONS`), `:759` (`EQUIVALENT_INACTIVE_REASON`) and `app/core/payments/service.py:722`, `:732` (`inactive_equivalent_conflict`/`integrity_hold_conflict`, outside `:502-534`). Some of these may belong to another group.
12. **Stage 2 constraint (T1544):** `test_p015_t1544_inject_refuses_a_deactivated_equivalent.py:122` checks its premise by the exact statement prefix `SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE`. The move of `refuse_inactive_equivalents` to `money_boundary.py` must keep that select shape, or it must update the premise in the same slice.
13. **Stale claim (docs in tests):** `test_p015_inject_holds_the_owner_lock_postgres.py:23-24` says "the shared test engine runs READ COMMITTED". That has been false since T1549 (`tests/conftest.py:139-172` runs the tier at `DB_POSTGRES_ISOLATION_LEVEL` = SERIALIZABLE). The dependency is conditional: the inject-lock assertions are DROP only if T1908's starvation experiment does not bring back an equivalent lock (spec "Evidence до решения" item 5).

#### G3 — счёт прохода

Files and lines by fate (16 files, 6 609 lines):

| fate | files | lines |
|---|---|---|
| KEEP | 5 (`real_clearing_engine_partial_failure`, `t1544_inject`, `p1_commit_then_refresh`, `clearing_additional_cases` (conditional), `zero_debt_policy`) | 1 770 |
| REWRITE with assertion map | 9 | 4 521 |
| REWRITE (setup-only) | 1 (`p1_clearing_run_perimeter_postgres`) | 197 |
| DROP | 1 (`clearing_prepare_lock_conflict`) | 121 |

Files by stage touched (a file can be counted in several stages):

| stage | files |
|---|---|
| 2 (conditional patch/import target) | 6: commit_replay, interlock, lost_update, bottleneck, no_deadlock, inject_owner_lock |
| 3 (premise/setup) | 3: lost_update, bottleneck, skip (test 2) |
| 4 | 8: commit_replay, interlock, skip, lost_update, bottleneck, no_deadlock, inject_owner_lock, t1543 |
| 5 | 10: commit_replay, interlock, skip, lost_update, bottleneck, no_deadlock, prepare_lock_conflict, routing, inject_owner_lock, perimeter (setup) |

Assertion rows in section B: 101 total (counted mechanically from the tables; the two "stage 2/4 then 5" rows are classed by their stage-5 fate).

| fate | rows | of which ⚑ |
|---|---|---|
| REWRITE IN PLACE | 54 | 22 |
| KEEP (in-file, unaffected) | 12 | 3 |
| DROP (with SURVIVES pointer) | 4 | 1 |
| DROP (removed contract) | 30 | 1 (inject :339, conditional on T1908, successor TO WRITE named) |
| TO WRITE (as primary fate) | 1 | 0 |

(Several DROP rows name a TO-WRITE replacement in the same cell. These cover T1908 schedules, the clearing deadline and the inject isolation refusal.) Every ⚑ observable (final debts, states, audits, publications, the committed-after-cancellation amount, E002, T1543, T1544) is REWRITE IN PLACE or KEEP, or is DROP with a named surviving node. No ⚑ row is dropped without a successor. The one inverted ⚑ verdict is commit_replay :468 (finding C1).

### 6.4. G4a — находки

- C1. **Stage 2's move list does not name the refusal constants/factories.** `EQUIVALENT_INACTIVE_REASON`, `EQUIVALENT_INTEGRITY_HOLD_REASON`, `inactive_equivalent_conflict`, `integrity_hold_conflict` (`app/core/payments/engine.py:378-401`) are what `MONEY_STOP_REASONS` (:387) is built from. Tests read them: `test_p015_step5c_hold_through_the_tick_sqlite.py:47`, `test_p015_step5c_hold_races_postgres.py:85`, `test_p015_b4_entries_and_money_postgres.py:2029`. App consumers missing from the stage-2 owner surface: `app/core/simulator/real_runner_impl.py:737` (`MONEY_STOP_REASONS`) and `:759` (`EQUIVALENT_INACTIVE_REASON`). The spec lists only `:616`, `:637`. The service pre-check also raises them (`service.py:~720`, `:732`).
- C2. **Owner-lock key and namespace are imported by tests.** `_EQUIVALENT_OWNER_LOCK_NAMESPACE`, `PaymentEngine._equivalent_owner_lock_key` (`test_p015_b4_entries_and_money_postgres.py:1898-1907`, `:2074-2098`), and `PaymentEngine.acquire_staged_equivalent_owner_locks` (`test_p015_step5c_hold_races_postgres.py:314`, `:530`). Stage 2 moves the owner lock into `money_boundary.py`, so these need a stable public name there. Stage 5 removes them.
- C3. **The Q1 durability of a hold refusal found after a retry changes in stage 3.** Today (`hold_races :230`) the `FOR SHARE` 40001 is retried inside `PaymentEngine.commit` over a durable `PREPARED`. The hold is then refused at commit and stored `ABORTED`. In stage 3, `pay()` retries the whole attempt on a fresh session. On this reading, the retry meets the best-effort pre-NEW pre-check (`service.py:720-732`) and leaves no row. A later replay of the same `tx_id` would then run again instead of returning the stored refusal. The FORK-4 table does not cover this case. T1902 must characterize it and the spec must decide it.
- C4. **The FORK-4 table is silent on non-retryable internal errors after `NEW`.** Two tests assert `ABORTED` stored today for this class: an `IntegrityViolationException` from `check_payment_delta` (`t1525 :295`) and 23514 from the envelope CHECK (`step5b :539`). The spec's Verification §2 item 3 requires only "no `COMMITTED`, no envelope, debts unchanged". The classification (definitive → `ABORTED`, or no row) has to be fixed in T1902 before stage 3.
- C5. **The READ COMMITTED meter dies at stage 5.** `step5b :327-360` is the positive control that shows the stand can see a (b) mismatch. FORK-2 makes the payment refuse non-SERIALIZABLE before its first write. This violates the rule that a stand needs its own positive control (§15 "стенд должен увидеть исход"). Stage 5 needs a replacement: a direct writer bypass under RC (not the application payment), or an explicit record that the control is replaced by `test_p019_money_writers_refuse_non_serializable_postgres.py`.
- C6. **Spec §3 lists two of these files as "must stay green", but they depend on the mechanisms that stages 3–5 remove.** `test_p015_step5c_hold_races_postgres.py`: T1 reads durable `PREPARED` (`:207`, stage 3), T6 is DROP at stage 4 (`:460`), and six waits are advisory (stage 5). `test_p015_step5b_criterion_b_postgres.py`: engine drive (stage 4), advisory placement and owner-lock wait (stage 5). §3's own list of engine- and lock-dependent tests omits both. They are rewritten in place across stages 2–5, not green-as-is.
- C7. **Helpers shared across groups that go away.** `_prepare_payment` in `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py:409` makes a durable prepare. It feeds `step5b_p :207`, `:246`, `:380` and the unit step-5b controls run by `step5b_p :463`/`:468`, so the stage-4 rewrite of that unit module must keep a same-named replacement. Twin: `wrong_writer_postgres :404`. Also `_seed_payment` (`entries :1335`). Helpers imported from files likely rewritten or dropped at stage 5:
  - `test_clearing_payment_prepare_interlock_postgres.py`: `_seed_interlock_case`, `_use_serializable`, `_no_advisory_lock_is_held` (`hold_races :58-62`)
  - `test_p015_t1544_operator_stop_races_postgres.py::_advisory_waiter_exists` (`step5b :46`, `hold_races :74`)
  - `test_p015_p1_money_replay_postgres.py::_prepare_locks` (`hold_races :67`, `step5b :500`)
  - the stand from `test_p015_inject_holds_the_owner_lock_postgres.py` (`entries :84`, `:864-870`)
- C8. **The independent capture of declared flows disappears at stage 4.** C14 (`entries :1462-1467`, `:1513`), C5-P (`wrong_writer :603`, `:617`) and C6-P (`:702`, `:733`, `:825`, `:840`, `:863`) snapshot `prepare_locks.effects` between a durable prepare and the commit. With no durable intermediate state, criterion (b) needs a new independent capture: the router's result via a wrapper, or the declared path × amount. Without one, the stored intent is compared with itself. This is a mechanism the programme builds against its own defects, so it is in scope of review item (6).
- C9. **Stage 4 needs patchable seams.** The wrong-writer counterexamples and C8 wrap `PaymentEngine._apply_flow` (`wrong_writer :500-509`; `entries :676`, `:759`). The pre-state barrier wraps `PaymentEngine._read_payment_prestate` (`step5b :250`, `:383`). The direct-execution path must expose a per-flow application point *below* intent computation, and a pre-state read, or C6(i) and the pre-state window cannot be posed. The spec's stage 4 does not say where the pre-state read (015 Phase B 7b) lives.
- C10. **The C17 race loses its premise at stage 4.** A fresh `pay()` into an equivalent already deactivated is refused by the best-effort pre-check before the owner lock, so it never queues (`entries :1997`). The stand must deactivate after the pre-check (barrier), or the T1544 binding race lives only in `test_p015_t1544_operator_stop_races_postgres.py` (another group — check there).
- C11. **Staged-wrapper name.** `PaymentService.create_payment_internal_staged` is wrapped at `t1525 :355-363` and `hold_tick :172-183`. The spec introduces `execute(session, request)` but does not say whether the staged method name survives. If it does not, these are stage-3 setup edits. `hold_tick :177` also expects the pre-NEW hold refusal to still *raise* `ConflictException` on the staged path. That holds only if the stage-3 structural `PaymentResult(ABORTED)` is limited to post-NEW definitive refusals, as the spec says ("classes that leave no row stay without a row").
- C12. **The pinned-connection side effect is asserted.** `hold_races :397` asserts `not clearing_session.in_transaction()`, which comes from today's interlock rolling back the caller's session. Stage 5 decides whether clearing still does that.
- C13. **Clearing patch targets.** The under-clearing listener arms around `ClearingService._execute_clearing_with_amount` (`wrong_writer :522`, `:539`) and counts `hits == 3` (`:974`). If T1907's whole-clearing retry owner restructures or retries that call, the count or the seam changes (stage 5, conditional).
- C14. **Downgrade path.** `hold_races` T9 (`:614-688`) downgrades head → `027`. After stages 4 and 5 this traverses the `031`/`030` downgrades, which is incidental evidence that they work on a scratch DB. No edit needed, but a broken `031` downgrade would surface here.
- C15. **Stale docstrings.** Line anchors into `engine.py` in `entries` / `wrong_writer` and the tick "owner-lock call returns early" note (`t1525 :381-382`) go stale with stages 2, 4 and 5. They are not assertions.

#### G4a — счёт прохода

Files by fate (6 files / 5911 lines):
- REWRITE, with assertion map: 5 files / 5714 lines (`entries` 2685, `wrong_writer` 1134, `step5b` 563, `hold_races` 688, `t1525` 644)
- REWRITE (setup-only): 1 file / 197 lines (`hold_through_the_tick`)
- KEEP: 0
- DROP: 0

Files touched per stage (one file can appear in several stages):
- stage 2: `entries`, `hold_races`, `hold_tick`, `t1525`: 4 files / 4214 lines
- stage 3: `step5b`, `hold_races`, `t1525`: 3 files / 1895 lines, plus `hold_tick` (conditional)
- stage 4: `entries`, `wrong_writer`, `step5b`, `hold_races`, `t1525`: 5 files / 5714 lines (`hold_tick` falls here at the latest if its constant does not move at stage 2)
- stage 5: `entries`, `wrong_writer` (import), `step5b`, `hold_races`, `t1525`: 5 files / 5714 lines

Whole tests dropped: 2
- `hold_races::test_step5c_p_an_expired_payment_in_a_held_equivalent_is_aborted_as_expired` (stage 4)
- `t1525::test_postgres_an_aborted_payment_commit_leaves_debts_unchanged` (stage 4; survivor: the service twin)

Assertion rows: 78
- REWRITE IN PLACE: 50
- TO WRITE: 14
- DROP: 13
- SURVIVES: 1

⚑ rows: 53
- REWRITE IN PLACE: 39
- TO WRITE: 13
- SURVIVES: 1
- DROP: 0

Per file (rows / ⚑):
- `entries`: 19 / 11
- `wrong_writer`: 14 / 12
- `step5b`: 15 / 11
- `hold_races`: 21 / 13
- `t1525`: 9 / 6

Every DROP row names a removed contract: `prepare_locks` table/reservations, advisory-lock ordering and waits, engine TTL branch over durable `PREPARED`, "envelope before `delete(PrepareLock)`".

### 6.5. G4b — находки

- **F1 (stage 5, ⚑ T1544 real-time cutoff).** T1544 is an external-consistency guarantee ("after the PATCH returns, no money commits"), not serializability. Clearing reads the stop with `row_lock=False` (`clearing/service.py:100-101`) and relies on the PATCH holding the advisory owner lock through commit (`engine.py:432-433` docstring). Without the owner lock, SERIALIZABLE admits clearing-read-True → PATCH commits 200 → clearing commits (serial order clearing<PATCH, no cycle). `races:267` (order `["clearing","patch"]`, :316) goes red unless stage 5 makes the clearing stop read `FOR SHARE` (as payment and inject do) or keeps a lock. Same check needed for reconciliation reaction/clear (T1546) paths.
- **F2 (stage 3/4, ⚑ Q1 table).** Stop/hold refusals are asserted stored `ABORTED` (`races:245`, `refuses_money:300-303`, `step5c:681`). In stage 3 the 40001 retry by `pay()` lands on the best-effort pre-check (`service.py:722/:732`, before any row) → no row; in the stage-4 order (spec "Общие шаги" 2 → 4) the binding stop read precedes the `Transaction` INSERT → no row either. The spec's Q1 row "стоп/hold → ABORTED" only holds if the refusal is persisted explicitly. T1902 must decide; the three asserts move with it.
- **F3 (stage 3, T1905).** Whether a staged stop refusal raised by the pre-insert check becomes a structural `PaymentResult(ABORTED)` and/or durable is not stated (spec keeps pre-insert classes rowless for the API path only). Affects `tick_sqlite:311-336`, `races:480-486`.
- **F4 (stage 4).** No service entry takes an explicit route (`service.py:409` has only `constraints`/avoid). C6(i) needs A→B→C with the C→A line present (binding condition 5, `wrong_writer:830-834`); the router would pick A→C directly. Stage 4 must provide an explicit-route execution entry (internal/test) replacing `engine.prepare(tx_id, path, amount, eq)` for `_prepare_payment` users: wrong_writer unit, step5a unit, step5b unit, step5c unit, **step5b PG** (`test_p015_step5b_criterion_b_postgres.py:51`).
- **F5 (stage 4).** `_collapse_the_route` patches `PaymentEngine._apply_flow`, a forwarder kept on purpose for tests (`engine.py:1714-1726`, 018 `T1802`; `book.py:74`). Deleting `engine.py` removes the seam of ⚑ C6(i) in 3 modules (wrong_writer, step5a :979, step5b :316/:525/:948). Stage 4 must name the replacement seam (`book._apply_payment_flow`, different signature `(session, flow)`).
- **F6 (stage 4).** The payment integrity audit row (`engine.py:1606-1651`, FIX-014) and the `check_trust_limits`/`check_debt_symmetry` calls (`engine.py:1575-1581`) have no named home in the spec (only `check_payment_delta` moves). ⚑ C6 asserts `audit == [True]` (`wrong_writer:867`, `step5a:989`, `step5b:320`, `:974`).
- **F7 (stage 4).** Prestate placement anchor (`step5b:1035-1045`) and the hold anchor (`step5c:671-680`) encode today's commit-path order; stage 4 must re-specify them against the new order and decide whether the v2 prestate is the routing read or a separate batched read (spec "Общие шаги" 3 vs `engine.py:451` `_read_payment_prestate`, which has no named home either).
- **F8 (stage 2, owner surface gap).** Consumers of `PaymentEngine` constants/classmethods missing from the stage-2 owner surface: `app/core/simulator/real_runner_impl.py:737`, `:759` (`MONEY_STOP_REASONS`, `EQUIVALENT_INACTIVE_REASON`); `app/core/payments/service.py:722`, `:732` (`inactive_equivalent_conflict`, `integrity_hold_conflict`; spec lists `service.py:502-534` only); `app/db/models/equivalent.py:19` comment. `EQUIVALENT_INACTIVE_REASON`/`EQUIVALENT_INTEGRITY_HOLD_REASON` (`engine.py:378`, `:383`) must move with `MONEY_STOP_REASONS`. Stage 5: `clearing/service.py:167` (`release_session_equivalent_owner_lock`) and `admin.py:1171` (abort, stage 4) also use `PaymentEngine`.
- **F9 (stage 3, docs only).** `b4_entries_and_money.py:650` C18 docstring calls itself "API-shaped" after `book.py:319/:395`, the loop stage 3 removes; after stage 3 the hand-written same-snapshot savepoint retry is exactly the pattern spec §4 forbids in production. The observable (rolled-back savepoint leaves no entry; `amount_before` from `OLD`) stays true of the trigger; reword the premise, no assert change.
- **F10 (stage 5, helper dependency).** `races` imports `_prepare_locks` from `test_p015_p1_money_replay_postgres.py` and `_no_advisory_lock_is_held`, `_seed_interlock_case`, `_use_serializable` from `test_clearing_payment_prepare_interlock_postgres.py` (:55-74). Their owners' stage-5 rewrite must keep `_seed_interlock_case`/`_use_serializable` (used by :267, :334) or move them.
- **F11 (stage 3 entry points).** `PaymentService.create_payment_internal` / `_staged` are called directly (`races:203`, `:416`, `:680`; `tick:311`; `step5c:583`, `:592`, `:618`, `:894`, `:923`). If stage 3 replaces them by `execute`/`pay`, these setup lines move in stage 3.
- **F12 (spec anchor).** `_advisory_waiter_exists` (`races:127-147`) is the premise of every T1544 race; spec §2 wants "порядок ожидания по `pg_locks`" — stage 5 needs a non-advisory wait probe (row/tuple/transactionid lock or `pg_stat_activity.wait_event_type='Lock'`), else premises go vacuous.

#### G4b — счёт прохода

Files / lines by fate: KEEP 2 / 947 (b4_entries 752, fixture_blocks 195); REWRITE 7 / 5 948 (one of them setup-only: step5a 1 018); DROP 0 / 0. Total 9 / 6 895.

Files touched per stage (a file may count in several): stage 2 — 4 files / 2 543 lines (races, refuses_money, tick, step5c); stage 3 — 3 / 1 612 (races, refuses_money, tick); stage 4 — 6 / 5 612 (races, refuses_money, wrong_writer, step5a, step5b, step5c); stage 5 — 2 / 1 276 (races, refuses_money).

Assertion rows (section B tables; helper map and setup-only line lists excluded): races 44, refuses_money 12, tick 6, wrong_writer 23, step5b 5, step5c 11 = **101**.
- REWRITE IN PLACE / unchanged: 88 (38 of them with a named stage change, 50 unchanged asserts)
- DROP: 10 (races :246, :395, :487, :562, :590-634, :636-643, :644-645; refuses :304-309; step5b TTL anchor; step5c TTL anchor). Contracts removed: `prepare_locks` row counts (4), advisory-lock leak (1), TTL-before-stop precedence / expiry (5)
- SURVIVES: 0 as a fate (one duplicate noted: wrong_writer C13 payment replay also asserted by `refuses_money.py:235-247`)
- TO WRITE: 3 (stage 2: DELETE-equivalent race, hold-release race, no-`40P01` + wait-order asserts on the PATCH races)

⚑ rows: 61 — REWRITE IN PLACE/unchanged 58, TO WRITE 3, DROP 0 (no ⚑ effect is dropped; every DROP is `prepare_locks` counting, advisory leak, or the TTL-precedence/expiry contract stage 4 removes).
