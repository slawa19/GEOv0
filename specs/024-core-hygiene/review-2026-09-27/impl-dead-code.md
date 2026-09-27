# impl-dead-code — мёртвый код, неиспользуемые символы, остатки удалённых механизмов (`app/` целиком + `scripts/*.py`, `migrations/` как потребители)

HEAD: 4119ace (дерево чистое, проверено `git status --short` до и после).

**Как читалось.** Зона — 131 файл `app/*.py`, 39 858 строк. Сплошное построчное чтение всех 40 тыс. строк НЕ выполнялось: метод — машинный (AST по всему `app/`, `tests/`, `scripts/`, `migrations/`, `seeds/`, `admin-fixtures/`, `fixtures/`; ruff; `git grep`), затем ручное чтение каждого кандидата с контекстом.
Прочитано целиком (малые модули-кандидаты): `app/core/ledger/__init__.py` (9), `app/core/simulator/__init__.py` (3), `runtime.py` (15), `real_runner.py` (36), `real_payment_action.py` (12), `rejection_codes.py` (69), `app/db/models/config.py` (13), `app/db/models/transaction.py` (33).
Прочитано фрагментами вокруг кандидатов: `app/config.py:120-459`, `app/api/v1/admin.py:110-340, 1036-1060`, `app/api/v1/payments.py:55-110`, `app/api/v1/auth.py:25-80`, `app/api/v1/simulator.py:490-640, 1680-1700`, `app/core/payments/service.py:40-260, 740-1130, 1290-1420, 1640-1720, 2205-2310`, `app/core/clearing/service.py:470-560, 780-800, 1350-1440, 1530-1600, 1870-1900, 1984-2092`, `app/core/ledger/book.py:1-60, 114-130, 672-700, 760-830`, `app/core/ledger/reconciliation.py:1-50`, `app/core/money_boundary.py:280-330`, `app/core/invariants.py:1-90`, `app/core/auth/service.py:40-130`, `app/core/simulator/real_runner_impl.py:50-70, 300-320, 440-470, 920-990`, `runtime_impl.py:295-320`, `runtime_utils.py:75-105`, `sse_broadcast.py:60-90, 270-300, 430-460`, `money_replay.py:80-95`, `app/db/journal_triggers.py:1-80`, `app/db/journal_tables.py:1-70, 285-359`, `app/db/types.py:15-135, 175-182`, `app/db/models/debt.py:20-75`, `app/db/models/__init__.py`, `migrations/versions/002_event_log.py`, `004_db_schema_enhancements.py:30-70`, `031_drop_prepare_locks.py`, `api/openapi.yaml:2845-2870, 4420-4440, 4585-4600`.
Не прочитано: основная масса `app/core/simulator/*` (021), `app/core/clearing/service.py` вне указанных окон (023), `app/api/v1/admin.py` вне окон, `app/core/admin/metrics.py`, `app/schemas/simulator.py`, `app/core/payments/router.py`, `tests/` (только grep).

Скрипты (scratchpad `review/dead/`): `refs_ast.py` (определения и ссылки через AST), `refs.py` (токенный вариант, отброшен — см. ниже), `trans.py` (транзитивная мёртвость), `cfg.py` (поля `Settings`), `cols.py` (колонки ORM), `idx.py` (таблицы/индексы миграций против ORM), `mods.py` (модули без импортёров), `shims.py` (модули-реэкспорты), `params.py`, `sameval.py` (параметры), `scriptimports.py` (импорты `app.*` из scripts/migrations/tests/fixtures).

## Summary

Зона в целом чистая от грубых остатков: после 017–019 **нет** ни одного импорта удалённых модулей (`engine.py`, `recovery.py`, `journal.py`, `sqlite_transaction_control.py`) ни в `app/`, ни в `scripts/`, `migrations/`, `tests/`, `admin-fixtures/` (`scriptimports.py`: 0 битых `from app… import`); ruff `F401/F811/F841` — 0; модулей без импортёра — 0; `reaper`, `JournalWriter`, `net_mutual`, `cap_debts` в `app/` — 0 совпадений. AST по 1 307 определениям верхнего уровня и методам дал 27 не-живых кандидатов; после ручной проверки динамических входов **реально мёртвых в продакшене — 19 символов** (15 функций/классов/методов + 4 журнальные константы), ещё 8 живут **только ради тестов**. Большая часть уже учтена: `T1519` (BACKLOG, «Отложено из 015») называет 5 из них, 021 покрывает шимы и тестовые обёртки симулятора, 023 — остатки SQLite в обнаружении клиринга.

Новое и не покрытое ничем — в основном **устаревшая проза и мёртвые ветки, оставленные 018/019 на живом денежном пути**: докстринг пакета `app/core/ledger/__init__.py` до сих пор описывает удалённый журнал-слушатель как ЖИВОЙ (P2 — это входная дверь денежного ядра); недостижимая ветка «payment is in progress» в идемпотентности платежа (`payments/service.py:880-897`, недостижима по `chk_transaction_payment_terminal`); параметр-«рубильник» `row_lock`, который все три вызова передают `True`, а `False` дал бы fail-open чтение стопа (`money_boundary.py:287`); константы `SQLSTATE_*`/`JOURNAL_SEQUENCE`/`DEBT_JOURNAL_TABLE_NAMES`, экспортированные как «единый источник», но не читаемые ни SQL, ни кодом; заголовок `Idempotency-Key`, протянутый через три кадра и выброшенный в `execute`. В схеме БД — таблица `event_log` (миграция 002), которой нет в ORM и которую никто не читает и не пишет. Ни одна находка не является P1: денежная семантика нигде не меняется.

Первым делом я бы: одним коммитом (docs-only) переписал `app/core/ledger/__init__.py` и три устаревших комментария в `payments/service.py`/`journal_tables.py`; вторым — удалил ветку `:880-897` и параметр `row_lock` (с прогоном `tests/integration/test_payment_idempotency_postgres.py` и тестов стопа/hold); остальное — вместе с `T1519` одним revertable-коммитом на класс.

## Findings

| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| DC-01 | P2 | docs-drift | `app/core/ledger/__init__.py:1-9` | Докстринг пакета денежного ядра описывает удалённый `journal` как единственный и ЖИВОЙ модуль, «вооружающий» `Engine`/`Session` | none | S |
| DC-02 | P3 | dead-code | `app/core/payments/service.py:880-897` | Ветка 409 «Payment with same tx_id is in progress» недостижима: тип проверен на `:787`, состояние ограничено `chk_transaction_payment_terminal` | none | S |
| DC-03 | P3 | dead-code | `app/core/money_boundary.py:287-325` | `refuse_inactive_equivalents(row_lock)` — все 3 вызова передают `True`; ветка `False` — fail-open чтение стопа без `FOR SHARE` | none | S |
| DC-04 | P3 | dead-code | `app/db/journal_triggers.py:44-66`, `app/db/journal_tables.py:351-359` | `SQLSTATE_*`, `JOURNAL_SEQUENCE`, `DEBT_JOURNAL_TABLE_NAMES` в `__all__`, ни одного читателя; SQL держит литералы; комментарий ссылается на удалённый «write guard» | none | S |
| DC-05 | P3 | dead-code | `app/core/payments/service.py:1106`, `:2217`, `:2290`; `app/api/v1/payments.py:64,92` | `idempotency_key` протянут API → `pay` → `_pay_attempt` → `execute` и там не читается (ruff ARG002) | none | S |
| DC-06 | P3 | dead-code | `app/core/payments/service.py:980,992-996`; `app/api/v1/simulator.py:1695` | `create_payment_internal(commit=True)` — единственное допустимое значение, иное бросает; докстринг «never exposed via HTTP» при единственном прод-вызове из HTTP-обработчика | none (вызывающий — 021) | S |
| DC-07 | P3 | docs-drift | `app/core/payments/service.py:168,1303`; `app/core/simulator/money_replay.py:86-88`; `app/db/journal_tables.py:345-346`; `app/db/types.py:43` | Комментарии в настоящем/будущем времени о `PREPARED`, `PaymentEngine._is_retryable_db_error`, «flush hook», «stage B removes» | none | S |
| DC-08 | P3 | docs-drift | `app/core/ledger/reconciliation.py:12-15`; `app/db/types.py:81-134`; `app/db/models/debt.py:30-33,49-54`; `app/db/models/equivalent.py:27-34`; `app/db/reconciliation_tables.py:139` | Проза «на SQLite», «оба диалекта» в настоящем времени после 017 | none (клиринг — 023) | S |
| DC-09 | P3 | dead-code | `migrations/versions/002_event_log.py:24-50` | Таблица `event_log` + 6 индексов в БД, нет в ORM, ни читателя, ни писателя; `create_all` её не строит | none | S (Contract: yes) |
| DC-10 | P3 | dead-code | `app/db/models/transaction.py:11,32`; `migrations/versions/004_db_schema_enhancements.py:44-60` | `transactions.idempotency_key` пишется (`None` / `clearing:<tx_id>`), не читается; уникальность дублирует `tx_id`; частичный индекс на до-019 состояния | none | M (Contract: yes) |
| DC-11 | P3 | dead-code | `app/utils/exceptions.py:115`; `app/core/simulator/rejection_codes.py:29-35,52-55` | `TrustLineException` нигде не бросается → ветка `TRUSTLINE_*` маппинга мертва; ветка `BadRequest` возвращает одно и то же в обоих исходах | none | S |
| DC-12 | P3 | dead-code | `app/core/auth/service.py:108-117` | `AuthService.revoke_refresh_token` — ни вызова, ни маршрута logout в OpenAPI | none | S |
| DC-13 | P3 | dead-code | `app/core/invariants.py:22-85` | `check_zero_sum` + `_compute_imbalance` мертвы по решению T1402 (есть гард), докстринг всё ещё «smoke-test for inconsistency» | none (связано с BACKLOG `T1507`) | S |
| DC-14 | P3 | dead-code | `app/schemas/common.py:46,50`; `app/schemas/admin.py:118`; `app/config.py:454`; `app/core/simulator/runtime_utils.py:84`; `app/api/v1/simulator.py:620` | Неиспользуемые `SignedRequest`, `PaginationParams`, `AdminAuditLogResponse`, `get_settings`, `safe_decimal_env`, `_require_run_accepts_actions_or_error` | none (последние два — зона 021) | S |
| DC-15 | P3 | test-asset | `app/core/ledger/book.py:114-123`; `app/core/simulator/sse_broadcast.py:69-77,278-300,441-445`; `app/core/simulator/runtime_impl.py:312` | Живут только ради тестов: `DEBT_OPERATION_IDENTITY_CONSTRAINTS` (retry-предикат удалён с engine), `SseBroadcast.next_event_id`/`broadcast` и fallback «test doubles», `count_active_runs` | none (`count_active_runs` — зона 021) | S |
| DC-16 | P3 | test-asset | `tests/integration/test_p015_b4_entries_and_money_postgres.py:517-521,853-856` | Докстринги тестов описывают мёртвый `_apply_inject_event` как «real inject owner» и «PaymentEngine» как живой | BACKLOG:«Отложено из 015 при задании закрытия — 2026-09-14» (`T1519`, неполно) | S |
| DC-17 | P3 | docs-drift | `api/openapi.yaml:4594-4596` | Комментарий канона ссылается на `_ACTIVE_PAYMENT_TX_STATES (app/api/v1/admin.py:113-120)` — символа нет с 019 | none (судьба схемы — П4/`T1911`) | S (Contract: yes, только комментарий) |
| DC-18 | P3 | docs-drift | миграции `idx_*` против ORM `ix_*` (`scripts idx.py`) | Имена ИНДЕКСОВ расходятся между `alembic upgrade` и `create_all` так же, как имена FK | BACKLOG:«2026-09-11 — имена ограничений внешних ключей зависят от того, как создана база» (неполно: там только FK) | S |

Итоговые числа (подробно — «Таблица кандидатов» ниже): AST — 1 307 определений, из них не-живых 27 (a 9, b 8, c 10). После ручной проверки: **мёртвых в продакшене 19** (из них покрыты `T1519` — 4: `validate_idempotency_key`, `PaymentService.get_payment`, `PaymentDetail`, `_apply_inject_event`; в owner surface 021 без упоминания — 2: `safe_decimal_env`, `_require_run_accepts_actions_or_error`), **только-тесты 8**, остальное — живые через динамический вход (pydantic `model_post_init`, TypeDecorator `process_bind_param`, SQLAlchemy `@validates`, `SimulatorEvent` — контракт OpenAPI, 155 маршрутов/обработчиков с декораторами). ruff: 35 срабатываний (ARG001 16, ARG002 8, ERA001 9, PLW0603 2), из них содержательных 7, закомментированного кода — 1 строка. Остатки 017–019: 324 совпадения в `app/`+`scripts/`+`migrations/` классифицированы в разделе «Шаг 3».

## Детали

### DC-01 — докстринг пакета `app/core/ledger` описывает удалённый журнал как живой
Evidence (`app/core/ledger/__init__.py:1-5`):
```
"""The debt ledger's own machinery: the operation journal (programme 015, phase B step 4).

`journal` is the only module here today, and since step 4 slice C (2026-09-12) it is LIVE: importing
it arms the journal on the `Engine` and `Session` classes for the whole process, and
`app/db/models/__init__.py` imports it alongside the tables it protects.
```
Факт: `journal.py` в пакете нет (`git ls-files app/core/ledger` → `__init__.py`, `book.py`, `reconciliation.py`), `app/db/models/__init__.py:24-30` прямо говорит, что импорт слушателя заменён импортом `journal_triggers`, `app/db/journal_triggers.py:5` — «the listener journal … is gone».
Почему это важно: это первый текст, который читает человек или агент, открывший денежное ядро; он утверждает механизм защиты `debts`, которого нет, и не упоминает `book.py`/`reconciliation.py`. По §15 AGENTS.md «неверная посылка опаснее отсутствующей» — читатель будет искать «вооружение» или вернёт его.
Минимальное исправление: переписать докстринг на три строки: пакет = `book.py` (единственный писатель долгов, конверт операции) + `reconciliation.py` (критерии (а)/(б)); журнал пишет триггер БД (`app/db/journal_triggers.py`, миграция 029).
Что может сломаться: ничего (докстринг). Covered-by: none. Contract: no.

### DC-02 — недостижимая ветка «in progress» в идемпотентности платежа
Evidence (`app/core/payments/service.py:786-788` и `:880-897`):
```
        if existing_tx.type != "PAYMENT":
            raise ConflictException("tx_id already used")
...
        if existing_tx.state in {
            "NEW",
            "ROUTED",
            "PREPARE_IN_PROGRESS",
            "PREPARED",
            "PROPOSED",
            "WAITING",
        }:
```
и `app/db/models/transaction.py:31`: `CheckConstraint("type <> 'PAYMENT' OR state IN ('COMMITTED', 'ABORTED')", name='chk_transaction_payment_terminal')` (миграция 030).
Вывод: для строки типа `PAYMENT` из БД состояние ∈ {COMMITTED, ABORTED}; ветка недостижима. Тесты это уже фиксируют как исторический факт (`tests/integration/test_payment_idempotency_postgres.py:5`: «no longer meets a committed NEW row and a "payment is in progress" 409»; `tests/unit/test_p015_t1548_...py:203`: сообщение «invites a retry, for a row that is not…»).
Почему это важно: мёртвая ветка на живом пути идемпотентности, с метрикой `conflict_in_progress`, которую больше никто не может увидеть, и с сообщением, приглашающим клиента повторять. Не денежная ошибка.
Минимальное исправление: удалить `:880-897`. Вердикт: **safe delete** после проверки `git grep -n "conflict_in_progress" -- app tests admin-ui simulator-ui` (сейчас: только `service.py:893`) и прогона `verify_local.ps1 -BackendOnly -BackendSelector tests/integration/test_payment_idempotency_postgres.py tests/unit/test_p015_t1548_a_replay_without_a_stored_fingerprint_is_refused.py`.
Что может сломаться: юнит-тест, который строит `Transaction(state="PREPARED")` в памяти и зовёт `_resolve_existing_payment` — поиск `git grep -n "_resolve_existing_payment" -- tests` перед удалением. Covered-by: none (T1519 называет другую ветку — клиринговую). Contract: no.

### DC-03 — `row_lock` всегда `True`; `False` — fail-open рубильник
Evidence (`app/core/money_boundary.py:314-325`):
```
        `row_lock=False` has no caller left in the application.
        """
        ...
        if row_lock:
            stmt = stmt.with_for_update(read=True)
```
Вызовы: `app/core/clearing/service.py:142`, `app/core/payments/service.py:1919`, `app/core/simulator/real_runner_impl.py:649` — все `row_lock=True` (`sameval.py`: «always True over 3 calls»). Тот же докстринг (`:303-311`) объясняет, что именно `FOR SHARE` связывает денежного писателя со стопом; чтение без него возвращает значение до PATCH.
Почему это важно: мёртвый параметр, чьё единственное «другое» значение отключает защиту стопа/hold. Сейчас недостижим; это дверь, а не дыра.
Минимальное исправление: убрать параметр, всегда `with_for_update(read=True)`; три вызова теряют аргумент. **safe delete** (`git grep -n "row_lock" -- app tests scripts`).
Covered-by: none (money_boundary исключён из 021 и 023). Contract: no.

### DC-04 — «единый источник», которого никто не читает (018)
Evidence (`app/db/journal_triggers.py:44-66`): `__all__` экспортирует `SQLSTATE_GUARD`, `SQLSTATE_KEY_CHANGED`, `SQLSTATE_NO_OPEN_OPERATION`, `JOURNAL_SEQUENCE`; SQL ниже пишет литералы `ERRCODE = 'GE001'`, `nextval('debt_journal_entries_ordinal_seq')` (`:81,90,100,111`). `app/db/journal_tables.py:351-359`:
```
#: Every table the write guard protects on the journal's own side. Named once so the guard, the
#: migration and the tests cannot drift apart.
DEBT_JOURNAL_TABLE_NAMES = frozenset(
```
`git grep -n -w -e SQLSTATE_GUARD -e SQLSTATE_KEY_CHANGED -e SQLSTATE_NO_OPEN_OPERATION -e JOURNAL_SEQUENCE -e DEBT_JOURNAL_TABLE_NAMES -- app tests scripts migrations` — только определения и `__all__`. «Write guard» журнала — удалённый в 018 слушатель; нынешние охранники — триггеры.
Почему это важно: константы выглядят как точка настройки, а изменение их ничего не меняет; `GE001`/`GE002` при этом никто в приложении не классифицирует.
Минимальное исправление: удалить `DEBT_JOURNAL_TABLE_NAMES`; для SQLSTATE-констант — либо удалить из `__all__` и кода, либо оставить с комментарием «документирующие, SQL не читает их» (они описаны в модульном докстринге). Вердикт: `DEBT_JOURNAL_TABLE_NAMES` — **safe delete**; `SQLSTATE_*`/`JOURNAL_SEQUENCE` — **verify first** (нет ли планов 023 классифицировать `GE001`).
Covered-by: none. Contract: no.

### DC-05 — `Idempotency-Key` протянут через три кадра и выброшен
Evidence: `app/api/v1/payments.py:91-92` «Legacy header is accepted but ignored…» `idempotency_key=idempotency_key`; `pay(..., idempotency_key)` `:2217` → `_pay_attempt` `:2270` → `execute(..., idempotency_key)` `:1106`, где ruff: `ARG002 Unused method argument: idempotency_key`. OpenAPI `:2857-2866` объявляет заголовок `deprecated`, «accepted but ignored». В `create_payment_internal`/`_staged` (`:1000`, `:1042`) параметр живой — из него строится `tx_id`.
Минимальное исправление: не передавать заголовок из `create_payment` дальше и убрать параметр из `pay`/`_pay_attempt`/`execute`; заголовок в роуте оставить (контракт). **safe delete** в `execute`/`_pay_attempt`; `pay` — **verify first** (`git grep -n "pay(" -- tests | grep idempotency_key`).
Covered-by: none. Contract: no (заголовок остаётся).

### DC-06 — `commit=True` как единственное значение
Evidence (`app/core/payments/service.py:980,992-996`):
```
        commit: bool = True,
...
        if not commit:
            raise ValueError(
                "commit=False is staged work; use create_payment_internal_staged() "
```
Прод-вызов один — `app/api/v1/simulator.py:1689-1697` с `commit=True`; при этом докстринг `:986-989` «This must never be exposed via HTTP endpoints». Остаток 019 (разделение staged/commit).
Минимальное исправление: убрать `commit` и проверку; поправить докстринг («единственный вызывающий — действие симулятора `action_payment_real`»). Тесты передают `commit=True` в 9 местах (`tests/unit/test_p1_payment_run_perimeter.py`, `test_p015_t1548_…`) — правка тестов механическая. **verify first**.
Covered-by: none; когда 021 уберёт `action_*`, у `create_payment_internal` может не остаться прод-вызывающих (см. «Оценка направления»). Contract: no.

### DC-07 — устаревшие комментарии на денежном пути
- `app/core/payments/service.py:167-168`: «must not interrupt the session-owned terminalization sequence and leave a durable NEW/PREPARED row» — такой строки быть не может (миграция 030).
- `app/core/payments/service.py:1303`: «forbidding a new PREPARED state after the PATCH returns…».
- `app/core/simulator/money_replay.py:86-88`: «the same pair `PaymentEngine._is_retryable_db_error` and the inject loop already use. Kept as one frozenset so the three retry sites cannot drift apart silently.» — `PaymentEngine` удалён; фактически это ТРИ раздельные константы: `payments/service.py:54`, `money_replay.py:89`, `real_runner_impl.py:65` (+`55P03`), плюс инлайн `clearing/service.py:413`. Утверждение «one frozenset» ложно.
- `app/db/journal_tables.py:345-346`: «something the flush hook must already have refused» — отказ теперь в `Book` (`app/core/ledger/book.py:766-774`, `Refusal.OUT_OF_SCOPE`).
- `app/db/types.py:43`: «the debt journal's listener (`journal.py::_check_storable`), which stage B of 018 removes» — уже удалён.
Минимальное исправление: одна docs-правка. Covered-by: none (дублирование SQLSTATE-наборов само по себе — 016 F-016-3, см. «Оценка направления»). Contract: no.

### DC-08 — проза о SQLite в настоящем времени после 017
Evidence: `app/core/ledger/reconciliation.py:12-15` «on SQLite it is not, because SQLite binds `Numeric` through float. The row check is done here on both dialects because one code path is cheaper…»; `app/db/types.py:113-117` «ON SQLITE, clause 3 can neither refuse nor accept anything… kept in the DDL of both dialects anyway»; `app/db/models/debt.py:30-33` «It is the only guard that can refuse a `NaN` on SQLite… is what fires today»; `app/db/reconciliation_tables.py:139` «(partial unique index, both dialects)». Большинство остальных SQLite-упоминаний в `app/` помечены `HISTORY` и корректны (`clearing/service.py:411`, `journal_tables.py:288`, `reconciliation.py:940`).
Почему это важно: `reconciliation.py:14-15` — это обоснование, почему Python-проверка строки дублирует `chk_debt_journal_entries_delta_arithmetic`; причина (SQLite) исчезла, решение осталось. Посылка неверна — по §15 такое приглашает «вернуть как было». Само дублирование — оборона от снятого ограничения, держать можно, но причину нужно переписать.
Минимальное исправление: переписать 4 места в прошедшем времени / убрать. Клиринговые (`clearing/service.py:530` «Dialect-aware», `:552`, `:791`, `:1432`, `_bind_uuid` `:479-480` = тождественная функция, `_debt_id_key` `:547-560` — нормализация, нужная только SQLite) — Covered-by 023 (заменяет обнаружение `:479-1468`). Contract: no.

### DC-09 — таблица `event_log` без модели и без потребителей
Evidence: `migrations/versions/002_event_log.py:24-50` создаёт `event_log` и `idx_event_log_*`; последующие миграции её не удаляют (`idx.py`: «tables in migrations not in ORM: ['event_log', 'prepare_locks']», `prepare_locks` удалена 031 через `op.drop_table(_TABLE)`). `git grep -n -w event_log -- app scripts` — 0. Нормативное описание есть только в `docs/en/10-testing-framework.md:150-304` (RU-исходник — та же `docs/ru/10-testing-framework.md`, на неё ссылается миграция).
Почему это важно: схема, построенная `create_all` (режим A фикстур), и схема `alembic upgrade head` различаются на целую таблицу; фича «доменные события в БД» зарегистрирована, но не реализована (§17 «Зарегистрировано, но не описано»).
Минимальное исправление: не патчить; записать в BACKLOG как «таблица объявлена testing-framework, писателя нет», решение (реализовать/удалить новой миграцией) — владельцу. Вердикт **verify first**. Covered-by: none. Contract: yes (миграция).

### DC-10 — `transactions.idempotency_key` пишется, но не читается
Evidence: `app/core/clearing/service.py:2075` `idempotency_key=f"clearing:{tx_id_str}"`; `app/core/payments/service.py:1478` `"idempotency_key": None`; чтений `.idempotency_key` как колонки в `app/` нет (`cols.py`: `Transaction.idempotency_key reads=[]`). `transaction.py:32` `UniqueConstraint('initiator_id', 'type', 'idempotency_key', …)` — для CLEARING дублирует уникальность `tx_id` (ключ = `clearing:` + тот же `tx_id`). Миграция 004 создала частичный индекс `ix_transactions_initiator_idempotency_active` по `state IN (_ACTIVE_STATES)` — набор до-019 состояний; в ORM его нет.
Минимальное исправление: документировать; удаление колонки/индексов — отдельной миграцией с решением. **verify first** (`git grep -n "idempotency_key" -- admin-ui/src simulator-ui/v2/src admin-fixtures`). Covered-by: none. Contract: yes.

### DC-11 — `TrustLineException` не бросается; мёртвые ветки маппинга
Evidence: `app/utils/exceptions.py:115-118` — класс; `git grep -n -E "raise TrustLineException|TrustLineException\(" -- app` → 0. `app/core/simulator/rejection_codes.py:29-35` сопоставляет `exc_name == "TrustLineException"` строкой (динамический вход по имени класса — поэтому AST дал категорию c); строка может прийти только от этого класса. `:52-55`:
```
    if exc_name == "BadRequestException":
        # E009 = validation error
        if geo_code == "E009":
            return "INVALID_INPUT"
        return "INVALID_INPUT"
```
Минимальное исправление: удалить класс и ветку `TRUSTLINE_*` либо оставить как явный «зарезервированный код для UI»; свернуть `:52-55`. **verify first**: `git grep -n "TRUSTLINE_LIMIT_EXCEEDED\|TRUSTLINE_NOT_ACTIVE\|TRUSTLINE_REJECTED" -- simulator-ui/v2/src admin-ui/src` (UI может держать словарь кодов). Тест `tests/unit/test_simulator_rejection_codes.py:10-12` проверяет только маппинг. Covered-by: none (файл в `app/core/simulator/`, 021 его не называет). Contract: no.

### DC-12 — `revoke_refresh_token` без вызова
Evidence: `app/core/auth/service.py:108-117`; `git grep -n "revoke_refresh_token" -- app tests scripts` → только определение; `grep -n "logout\|revoke" api/openapi.yaml app/api/v1/auth.py` → 0. Соседний `refresh_tokens` отзывает `jti` сам (ротация), поэтому `revoke_jti` живой.
Вердикт: **verify first** — это либо заготовка logout (продуктовый вопрос), либо мёртвый код. Covered-by: none. Contract: no.

### DC-13 — `check_zero_sum` остался после вывода из публикации
Evidence: `app/core/invariants.py:22-27` докстринг «this check serves as a smoke-test for inconsistency»; вызовов нет, это закреплено гардом `tests/integration/test_p014_t1402_zero_sum_is_not_published_as_a_check.py:140` (`test_no_production_path_calls_check_zero_sum`). `_compute_imbalance` (`:62`) вызывается только из него (`trans.py`).
Почему это важно: метод, признанный тавтологией (014 `F-014-1`), лежит в `InvariantChecker` рядом с живыми проверками и описан как проверка — приглашение вернуть вызов.
Минимальное исправление: удалить оба метода; гард переписать на «символа нет» либо оставить как есть (он и так ищет вызов). **safe delete** после `git grep -n "check_zero_sum\|_compute_imbalance" -- app tests admin-fixtures`. Covered-by: none (T1402 снял вызов, `T1507` — канон). Contract: no.

### DC-14 — прочие неиспользуемые символы
`app/schemas/common.py:46` `SignedRequest` (в OpenAPI есть компонент `SignedRequest`, `api/openapi.yaml:4962`, но Python-класс не участвует в генерации — **safe delete** класса, компонент канона не трогать), `:50` `PaginationParams` (**safe delete**), `app/schemas/admin.py:118` `AdminAuditLogResponse` (011 `spec.md:967`: «ни одной операцией не используемая»; **safe delete** класса), `app/config.py:454` `get_settings` (**safe delete**), `app/core/simulator/runtime_utils.py:84` `safe_decimal_env` (**safe delete**; зона 021), `app/api/v1/simulator.py:620-637` `_require_run_accepts_actions_or_error` (упомянут только в комментариях `:496`, `:2020`, `:2080`; **safe delete**, зона 021). Проверка для всех: `git grep -n -w <имя> -- app tests scripts migrations admin-fixtures seeds` + поиск строкового имени в `getattr`/monkeypatch (сделан — 0). Covered-by: none. Contract: no.

### DC-15 — символы, живущие только ради тестов
- `app/core/ledger/book.py:114-123` `DEBT_OPERATION_IDENTITY_CONSTRAINTS`: комментарий «a retry predicate that widens itself…», но retry-предикат был в `engine.py` (удалён 019, см. докстринг `tests/unit/test_p015_t1529_…py:1-12`); сейчас константу читает только этот тест как входные данные. **keep (только тесты)**, либо перенести в тест.
- `app/core/simulator/sse_broadcast.py:69-77` `next_event_id` («Legacy allocation helper for tests»), `:278-300` `broadcast` («Compatibility path for already-ID'd test payloads»), `:441-445` fallback «for narrow test doubles that predate atomic publishing» — у продового `SseBroadcast` `publish_event` есть всегда, ветка в проде недостижима. **keep (только тесты)**; 4 тестовых файла зовут `.broadcast(`/`.next_event_id(`.
- `app/core/simulator/runtime_impl.py:312` `count_active_runs` — только `tests/unit/test_simulator_owner_isolation.py`.
Covered-by: none (SSE-внутренности не названы в 021; `count_active_runs` — зона 021). Contract: no.

### DC-16 — тесты описывают мёртвый код как живой
Evidence: `tests/integration/test_p015_b4_entries_and_money_postgres.py:517-521` «driven through `PaymentEngine` … the two owners' own loops - `_run_uow_with_retry` and `_apply_inject_event`'s `while True:`»; `:853-856` «against the real inject owner - `_apply_due_scenario_events` -> `_apply_inject_event`'s `while True:` loop». `app/core/simulator/real_runner_impl.py:459`: «No caller in the tree (T1519 lists it as dead code)». `_run_uow_with_retry` в `app/` отсутствует.
Почему это важно: закрытие `T1519` (удаление `_apply_inject_event`) оставит тесты, которые словами утверждают, что проверяют удалённый путь. Covered-by: BACKLOG «Отложено из 015 при задании закрытия — 2026-09-14», `T1519` — неполно: задача не называет эти докстринги.

### DC-17 — комментарий канона указывает на несуществующий символ
Evidence: `api/openapi.yaml:4594-4596` «The query filters to `_ACTIVE_PAYMENT_TX_STATES` (app/api/v1/admin.py:113-120), so this is six of the nine transaction states»; `git grep -n "_ACTIVE_PAYMENT_TX_STATES" -- app` → 0; на `admin.py:115-121` теперь заметка «STUCK PAYMENT READERS ARE A COMPATIBILITY SURFACE». Covered-by: none (судьба схемы — П4/`T1911`). Contract: yes (правка комментария в OpenAPI, без изменения схемы).

### DC-18 — имена индексов тоже зависят от способа постройки базы
Evidence: `idx.py` — 36 индексов с явными именами `idx_*` в миграциях 001–013 (`idx_debts_creditor`, `idx_participants_pid`, …) не названы в ORM, где те же столбцы объявлены `index=True` (`app/db/models/debt.py:23-29`, `participant.py:10-14`) и получат имена `ix_<table>_<col>`. Это тот же корень, что BACKLOG «2026-09-11 — имена ограничений внешних ключей зависят от того, как создана база», но там названы только FK. Covered-by: BACKLOG (неполно — добавить индексы в тот же пункт). Contract: yes.

## Таблица кандидатов (все, сгруппировано по пакетам)

Легенда AST-категорий: a — ссылок нет нигде; b — только `tests/`; c — только строки/комментарии; d — только реэкспорт. «Живой-хук» — AST видит 0 ссылок, но вход динамический.

**app/api**
| Символ | path:line | Кат. | Вердикт | Проверка |
|---|---|---|---|---|
| 155 маршрутов с декораторами `router.*` | `app/api/v1/*.py` | a/b/c | keep (динамический вход: `APIRouter`, `app/api/router.py`) | исключены из счёта |
| `_require_run_accepts_actions_or_error` | `api/v1/simulator.py:620` | c | safe delete (DC-14; 021) | `git grep -n -w _require_run_accepts_actions_or_error` |
| `_graph_fetch_incidents(db, limit)` + `ADMIN_GRAPH_INCLUDE_MAX_INCIDENTS` | `api/v1/admin.py:241`, `config.py:241` | ruff ARG001 | keep (совместимость до П4/`T1911`) | — |
| `create_payment(session=...)` | `api/v1/payments.py:61` | ruff ARG001 | keep (Depends; FastAPI кэширует `get_db`, лишней сессии нет) | — |
| `participants.py:26,43,84 current_participant` | — | ruff ARG001 | keep (Depends — это auth-гейт) | — |
| `events_poll_active_run(equivalent, after)`, `actor` ×5 | `api/v1/simulator.py:2600-2654` | ruff ARG001 | keep (wire-параметры / auth-Depends) | — |
| `main.py:427 request` | handler signature | ruff ARG001 | keep (сигнатура FastAPI) | — |

**app/core/payments**
| `PaymentService.get_payment` | `payments/service.py:2625` | a | safe delete | Covered-by T1519 |
| ветка «in progress» | `:880-897` | — | safe delete (DC-02) | см. DC-02 |
| `execute(idempotency_key)` | `:1106` | ruff ARG002 | safe delete (DC-05) | |
| `create_payment_internal(commit)` | `:980` | sameval | verify first (DC-06) | |
| `create_payment_internal(description, constraints)`, `_staged(description, constraints)` | `:971`, `:1022` | params.py | keep (без прод-передачи, но часть внутреннего API; P3 не заявлено) | |

**app/core/clearing** (023)
| `_bind_uuid` (тождество, SQLite-остаток) | `clearing/service.py:479` | live | Covered-by 023 | |
| `_debt_id_key` (нормализация ради SQLite) | `:547` | live | Covered-by 023 | |
| `# nodes.sort()` (единственный закомментированный код, ERA001) | `:1363` | ruff | Covered-by 023 | |
| `execute_clearing(allowed_participant_pids)` — «Backward-compatible API», прод-вызов 1 без параметра | `:1530` | params.py | Covered-by 023 | |
| `_policy_flag(default)` всегда `True` | `:990` | sameval | Covered-by 023 | |
| «interlock»-имена (`_rollback_before_interlock`, `interlocked_equivalent_id`) | `:214-1885` | grep | Covered-by 023 (имя от 019; смысл теперь — исключительный лок эквивалента) | |
| ветка `interlocked_equivalent_id is None and debts` | `:1884-1888` | — | Covered-by T1519 («недостижимая ветка clearing/service.py:1895-1897», сдвинулась) | |

**app/core/ledger, money_boundary, invariants**
| докстринг `ledger/__init__.py` | `:1-9` | — | переписать (DC-01) | |
| `DEBT_OPERATION_IDENTITY_CONSTRAINTS` | `book.py:121` | b | keep (только тесты) (DC-15) | |
| `refuse_inactive_equivalents(row_lock)` | `money_boundary.py:287` | sameval | safe delete параметра (DC-03) | |
| `check_zero_sum`, `_compute_imbalance` | `invariants.py:22,62` | c / транзитивно | safe delete (DC-13) | |

**app/core/auth, app/utils, app/config, app/schemas**
| `AuthService.revoke_refresh_token` | `auth/service.py:108` | a | verify first (DC-12) |
| `login(device_info)` | `auth/service.py:52` | ruff ARG002 | keep (P3, аудит делает роут `auth.py:65-78`) |
| `validate_idempotency_key` | `utils/validation.py:73` | a | safe delete — Covered-by T1519 |
| `TrustLineException` | `utils/exceptions.py:115` | c (строкой) | verify first (DC-11) |
| `parse_amount_decimal(require_positive, max_integer_digits)` — в проде всегда default | `utils/validation.py:228` | sameval | keep (используется тестами; P3 не заявлено) |
| `get_settings` | `config.py:454` | a | safe delete (DC-14) |
| `Settings.model_post_init` | `config.py:320` | a | keep (динамический вход: pydantic) |
| `RECOVERY_ENABLED`, `RECOVERY_INTERVAL_SECONDS`, `PAYMENT_TX_STUCK_TIMEOUT_SECONDS` | `config.py:173-175` | cfg.py: читает только `/admin/config` | keep (инертны до П4/`T1911`, комментарий `:168-172` точен) |
| `PREPARE_TIMEOUT_SECONDS` | `config.py:185` | live (`payments/service.py:1365,1709`) | keep; имя унаследовано (фаза связывания) — P3, не заявлено |
| `SignedRequest`, `PaginationParams` | `schemas/common.py:46,50` | a | safe delete (DC-14) |
| `PaymentDetail` | `schemas/payment.py:71` | a | safe delete — Covered-by T1519 |
| `AdminAuditLogResponse` | `schemas/admin.py:118` | c | safe delete (DC-14) |
| `SimulatorEvent` | `schemas/simulator.py:335` | b | keep (контракт OpenAPI, `tests/contract/test_openapi_contract.py`) |
| метрика `RECOVERY_EVENTS_TOTAL` (`geo_recovery_events_total`) несёт события целостности и hold сверки | `utils/metrics.py:38`, `main.py:160`, `reconciliation.py:1106` | live | keep (имя — ops-контракт; P3 наименование) |

**app/db**
| `SQLSTATE_*`, `JOURNAL_SEQUENCE` | `journal_triggers.py:59-66` | c (только `__all__`) | verify first (DC-04) |
| `DEBT_JOURNAL_TABLE_NAMES` | `journal_tables.py:353` | c | safe delete (DC-04) |
| `MoneyNumeric.process_bind_param(dialect)` | `types.py:179` | b / ruff ARG002 | keep (динамический вход: TypeDecorator) |
| `Config` модель / таблица `config` | `models/config.py` | только реэкспорт `models/__init__.py:21,42` | keep — решение 008 (`tasks.md:90`, `D-A1c-005`) |
| `Config.updated_by` | `models/config.py:13` | cols.py: 0 чтений/записей | keep (вместе с моделью) |
| `Transaction.idempotency_key` + `uq_transactions_initiator_type_idempotency` | `models/transaction.py:11,32` | cols.py | verify first (DC-10) |
| `Transaction.signatures` | `models/transaction.py:15` | пишется, не читается | keep (хранение подписи — протокольное свидетельство, не мёртвый код) |
| таблица `event_log` (миграция 002) | — | idx.py | verify first (DC-09) |
| `simulator_run_metrics_float_archive` (миграция 018) | — | архив по замыслу | keep |
| индексы `idx_*` миграций против `ix_*` ORM | — | idx.py | DC-18 |

**app/core/simulator** (зона 021; перечислено для полноты, не как находки)
| шимы `runtime.py`, `real_runner.py`; реэкспорт `_map_rejection_code` только для `tests/unit/test_simulator_rejection_codes.py:31` | — | shims.py | Covered-by 021 |
| `RealRunnerImpl._compute_stress_multipliers`, `_init_trust_drift`, `_apply_trust_growth`, `_apply_trust_decay` (обёртки только для тестов; прод передаёт `self._trust_drift_engine.apply_trust_growth`, `real_runner_impl.py:988`) | `real_runner_impl.py:307,925,928,946` | b | Covered-by 021 |
| `RealRunnerImpl._apply_inject_event` | `real_runner_impl.py:447` | c | Covered-by T1519 |
| `count_active_runs` | `runtime_impl.py:312` | b | keep (только тесты), зона 021 |
| `safe_decimal_env` | `runtime_utils.py:84` | a | safe delete (DC-14) |
| неиспользуемые `session` (`real_clearing_engine.py:95`, `real_runner.py:16`, `real_runner_impl.py:973`, `real_tick_orchestrator.py:710`), `run_id/planned_len/tick_t0` (`real_tick_clearing_coordinator.py:182-186,461`) | ruff ARG002 | Covered-by 021 |
| `MetricsBottlenecks(utc_now)` | `metrics_bottlenecks.py:62` | — | Covered-by BACKLOG «Мёртвый параметр `utc_now`» |
| ERA001 в `adaptive_clearing_policy.py:205`, `real_payment_planner.py:279,470`, `snapshot_builder.py:259`; `admin/metrics.py:175,666`; `reconciliation.py:424` | ruff | ложные срабатывания (формулы/заголовки в комментариях) |

## Шаг 3 — классификация остатков 017–019 (`git grep -n -i` по `app scripts migrations`)

| Термин | app+scripts+migr / tests | Классификация (app/scripts; миграции — история, не трогать) |
|---|---|---|
| `PREPARED` | 13 / 89 | `admin.py:116,1046` — точная заметка о совместимости; `payments/service.py:885` — мёртвая ветка (DC-02); `:168`, `:1303` — устаревшие комментарии (DC-07); `transaction.py:25` — `chk_transaction_state` (контракт схемы, keep) |
| `prepare_locks` | 70 / 54 | в `app/` 5 — все исторические ссылки «dropped by 031»; остальное — миграции |
| `PaymentEngine` | 6 / 86 | `book.py:9,330`, `reconciliation.py:500`, `payments/service.py:213` — происхождение/история, корректно; `money_replay.py:87` — устаревший настоящий смысл (DC-07). Тесты: докстринги (DC-16) и намеренные строки гарда `test_p019_lock_primitives_live_in_money_boundary.py:321-361` |
| `engine.py` | 5 / 40 | `book.py:119` — происхождение; `scripts/measure_p020_*` — это `real_clearing_engine.py`, живое |
| `reaper` | 0 / 0 | — |
| `interlock` | 28 / 91 | клиринг: живой код под унаследованным именем (023); `money_boundary.py:37` — ссылка на живой `_release_interlock_session` |
| `journal.py` | 8 / 8 | все помечены «deleted/gone», кроме `ledger/__init__.py` (DC-01) и `types.py:43` (DC-07) |
| `JournalWriter` | 0 / 0 | — |
| `net-mutual`, `cap-debts` | 0 / 6 | тесты: гард отсутствия маршрутов `tests/contract/test_openapi_contract.py:862-869` — живой, корректный |
| `_listener` | 0 / 40 | в `app/` — 0; тесты не классифицировались поштучно (вне зоны) |
| `recovery` | 30 / 39 | `config.py:168-175`, `main.py:270-272` — точные заметки «inert until П4»; `RECOVERY_EVENTS_TOTAL` — имя-наследие; `storage.py:556`, `main.py:288` — восстановление ранов симулятора, другое понятие, живое |
| `sqlite` | 90 / 327 | `app/`: большинство с пометкой HISTORY — корректно; настоящее время — DC-08; клиринг — 023; `scripts/measure_clearing_min_amount_plan.py:63,118` — отказ на SQLite-URL, живой |
| `dialect` | 74 / 170 | `health.py:132-148` — живой (отчёт движка); `book.py:691` — живое чтение SQLAlchemy; `types.py:179` — сигнатура TypeDecorator; `schemas/common.py:14` — поле ответа health; `session.py:7-9` — точная заметка «no dialect branch» |

## Команды и результаты (дословно, итоговые строки)

- `.venv/Scripts/python.exe refs_ast.py refs_ast.json` → `Counter({'live': 1125, 'live-deco': 155, 'c': 10, 'a': 9, 'b': 8})`, `1307 defs`.
  Ограничение первой версии (`refs.py`, токенайзер): на Python 3.11 f-строка — один токен STRING, поэтому имена внутри `f"{X}"` не считались ссылками (ложные «мёртвые»: `_SQL_CLEARABLE_TRUSTLINE_STATUSES`, `_sql_auto_clearing_ok`, `_LIMIT_SQL`, `_DATABASE_URL_HOW_TO`); одноимённые маршрут и метод (`get_payment`) маскировали друг друга. AST-версия исправляет оба; присваивание (`Store`) не считается ссылкой.
- `trans.py` (транзитивно): добавил только `InvariantChecker._compute_imbalance`; `config.py` `_require_*`/`_guardrail_*` — ложные (зовутся из pydantic-хука `model_post_init`).
- `.venv/Scripts/ruff.exe check app --select F401,F811,F841,ARG001,ARG002,ARG005,ERA001,PLW0603 --statistics --no-cache` (ruff 0.1.14) → `16 ARG001`, `9 ERA001`, `8 ARG002`, `2 PLW0603`; exit 1. F401/F811/F841/ARG005 — 0.
- `mods.py` → модулей `app/` без импортёров — 0.
- `scriptimports.py` → битых `from app.X import Y` в `scripts/ migrations/ admin-fixtures/ seeds/ tests/` — 0. Строковые monkeypatch-цели `setattr("app.…")` — 2, обе намеренные строки гарда.
- `cfg.py` → полей `Settings`, не читаемых в `app/`: `DEFAULT_JWT_SECRET`, `DEFAULT_ADMIN_TOKEN`, `LEGACY_ENVIRONMENT`, `_ENV_ALIASES`, `_SAFE_ENVS`, `_MIN_SECRET_LENGTH`, `_UNSAFE_SECRET_*` — все читаются валидаторами внутри `config.py` (живые); инертные — только три `RECOVERY_*`/`PAYMENT_TX_STUCK_*` (известно). Флагов, у которых обе ветки делают одно и то же, в `config.py` не найдено; такой шаблон есть в коде — `rejection_codes.py:52-55` (DC-11).
- `idx.py` → `tables in migrations not in ORM: ['event_log', 'prepare_locks']`; `tables in ORM not in migrations: []`.

## Что не проверено

- Построчное чтение всего `app/` (40 тыс. строк) — не делалось; метод машинный + чтение кандидатов. Мёртвые ветки ВНУТРИ живых функций (как DC-02) находятся только чтением; найденные — по grep «PREPARED/in progress/unreachable/compat/legacy», систематического поиска недостижимых веток нет (vulture не установлен, по брифу не ставить).
- Методы с распространёнными именами (`get`, `execute`, `run`, `apply` и т. п.) AST-подсчёт засчитывает живыми по любому одноимённому вызову — ложноотрицательные возможны.
- Поля Pydantic-схем и ORM, читаемые через `from_attributes`/`model_validate` — не проверялись поштучно (`cols.py` даёт только прямые атрибутные чтения).
- Схема БД сравнивалась статически (AST миграций против ORM), без поднятия базы (бриф запрещает БД); `op.execute`-DDL и `op.create_table(<переменная>)` учтены вручную только для 018 и 031.
- `tests/` — только grep по остаткам; тесты под несуществующий код не классифицировались поштучно (40 совпадений `_listener`, 86 `PaymentEngine`), кроме DC-16.
- `admin-ui/`, `simulator-ui/` — только как потребители строк (i18n `RECOVERY_*`), не ревьюились.

## Оценка направления текущего плана

- **016 устарела по якорям после 019.** `F-016-3` цитирует `app/core/payments/engine.py:399-405,441-449` и `payments/service.py:471` (`engine._get_pgcode()`), `F-016-5` — `app/core/recovery.py:23-30,163-181` и «шесть состояний» активных платежей: обоих файлов нет, набор активных состояний для `PAYMENT` пуст по `chk_transaction_payment_terminal`. `F-016-5` в нынешнем виде снимается почти целиком (осталась лишь совместимая поверхность П4). `F-016-3` жива в другой форме: наборы `{"40001","40P01"}` — в `payments/service.py:54`, `money_replay.py:89`, `real_runner_impl.py:65` (+`55P03`), инлайн `clearing/service.py:413`, а комментарий `money_replay.py:86-88` ложно утверждает, что это «one frozenset». Если 016 когда-нибудь вернут, её находки нужно переснять на текущем HEAD.
- **021 и `create_payment_internal`.** Единственный прод-вызывающий `PaymentService.create_payment_internal` — HTTP-обработчик `action_payment_real` (`api/v1/simulator.py:1689`), то есть блок `action_*`, который 021 переводит на публичные сервисы. Если 021 уберёт этот вызов, метод (вместе с `commit`, DC-06) станет мёртвым в проде и останется жить только в ~10 тестах. Стоит назвать это в Tasks 021, чтобы удаление/перевод тестов не потерялся.
- **021 не называет в owner surface** `_require_run_accepts_actions_or_error` (`simulator.py:620`, вне диапазона `:972-2322`), `rejection_codes.py` (DC-11), `runtime_utils.safe_decimal_env`, `runtime_impl.count_active_runs` и SSE-совместимость для тестовых двойников (DC-15). Мелочь, но при чистке по списку они выпадут.
- **`T1519` (BACKLOG) неполон:** из восьми символов, мёртвых в проде вне зон 021/023 (`revoke_refresh_token`, `SignedRequest`, `PaginationParams`, `AdminAuditLogResponse`, `get_settings`, `check_zero_sum`+`_compute_imbalance`, `TrustLineException`, `DEBT_JOURNAL_TABLE_NAMES`), он не называет ни одного; его «недостижимая ветка `clearing/service.py:1895-1897`» сдвинулась на `:1884-1888`. При заявке на `T1519` имеет смысл взять этот список и DC-02/DC-03 одним классом «мёртвый код после 018/019».
