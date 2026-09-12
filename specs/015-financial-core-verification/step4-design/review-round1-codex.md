STEP4-DESIGN: NO-GO

Проверено дерево `0a9b7b6`, полный контракт фазы B и установленная SQLAlchemy `2.0.25`. Главные блокеры: guard определяет **текущую**, а не **коммитящуюся** транзакцию; завершение допускается внутри чужого дочернего savepoint; SQLite не гарантирует предполагаемую точность и rollback-семантику.

Ниже `spec.md` означает `specs/015-financial-core-verification/spec.md`, `SA/` — `.venv/Lib/site-packages/sqlalchemy/`. Выполнены только диагностические пробы в памяти, включая AsyncSession/aiosqlite; это **debug-only**, не canonical gates и не испытание ещё отсутствующей реализации журнала.

**1. Развилки F1–F17**

| Fork | Решение | Обоснование / альтернатива |
|---|---|---|
| F1 | **AGREE** | Вычисление в `before_flush`, Core INSERT в `after_flush` допустимы в одной flush-транзакции. Уточнить формулировку контракта: `SA/orm/session.py:4404`, `:4412`, `:4446`. |
| F2 | **AGREE** | `Numeric(20,8)` соответствует Debt; signed BIGINT действительно недостаточен для полного диапазона атомов. Но SQLite нельзя считать доказательством сохранённых денег: `app/db/models/debt.py:18`, `spec.md:774`. |
| F3 | **AGREE** | Before/after нужны для направленной непрерывности, которую нетто скрывает. Они должны обозначать сохранённые значения, не просто ORM-память: `spec.md:612`, `:636`. |
| F4 | **AGREE** | Эпохи относятся к шагу 5. До baseline допустим только явно непроверяемый статус: `spec.md:643`, `:769`. |
| F5 | **DISAGREE** | Runtime CAS головы переносит в шаг 4 порядок, прямо назначенный шагу 5, и добавляет общий конфликт всем операциям эквивалента. Перенести распределение seq вместе с baseline/цепочкой либо явно изменить порядок и решить snapshot/retry-проблему: `spec.md:769`, `engine.py:399`. |
| F6 | **AGREE** | Отдельный `TEST_FIXTURE` не является глобальным режимом: он оставляет именованный журналируемый эффект. Сам enum не обеспечивает ограниченность контекста: `spec.md:627`. |
| F7 | **AGREE** | Для component-тестов внутренних писателей допустим настоящий контекст с корректными identity/Transaction/intent. Такие тесты не доказывают установку контекста реальным владельцем: `tests/unit/test_apply_flow_retry_on_stale.py:78`. |
| F8 | **DISAGREE** | Инструментирование закрытых ремонтов преждевременно: при включении флага они по-прежнему пишут без owner-lock. Оставить их заблокированными; восстановление — отдельный согласованный slice: `app/api/v1/integrity.py:329`, `:338`, `spec.md:735`. |
| F9 | **DISAGREE** | Для минимального шага 4 зафиксировать блокировку measurement script. Его адаптация требует отдельного расширения периметра; `TRUNCATE … CASCADE` требует отдельной политики сброса новой истории: `scripts/measure_clearing_min_amount_plan.py:214`, `spec.md:751`. |
| F10 | **AGREE** | Политику восстановления inject можно отложить. Однако duplicate→`db error` не является успешным идемпотентным replay и не закрывает надёжность операции: `real_runner_impl.py:619`, `:636`. |
| F11 | **AGREE** | Нужна последовательная проверка всех трёх барьеров, затем именованные мутации для достижения исходного дефекта. Нельзя потерять отдельное доказательство payment-delta отказа: `tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py:445`, `:458`. |
| F12 | **AGREE** | RESTRICT для истории соответствует сохранению обязательств. Но утверждение «409 существует» проверено только для удаления эквивалента, не любого удаления участника: `spec.md:669`, `app/api/v1/admin.py:1459`. |
| F13 | **AGREE** | Для принятого inject-события нулевой Debt-effect не означает отсутствия операции. Определить отдельно rejected/skipped/disabled события и эквиваленты намерения: `spec.md:717`, `real_runner_impl.py:536`, `:548`. |
| F14 | **AGREE** | Отказ журнала должен отравлять денежный UoW, даже после rollback дочернего savepoint. Это не должно распространяться на штатно обрабатываемый `StaleDataError`: `spec.md:624`, `engine.py:1492`. |
| F15 | **AGREE** | Пропуски ordinal допустимы после отката; требовать непрерывность номеров нельзя. Число и digest должны считаться по сохранившимся entries: `spec.md:719`. |
| F16 | **AGREE** | Scope полезен, но проверять нужно и эквиваленты **намерения**, которым выделяется head/slot. Scope должен происходить из авторитетного lock-set: `engine.py:408`, `real_runner_impl.py:571`. |
| F17 | **AGREE** | Label + manifest digest + batch UUID соответствует контракту. UUID фиксируется на логический batch и сохраняется при retry; сам по себе он не обеспечивает идемпотентность повторного запуска seed: `spec.md:627`, `scripts/seed_db.py:416`. |

**2. Привязка к транзакции: имеются конкретные обходы**

**(a) Commit без завершённого envelope — да.**

*Прямой commit родителя:* открыть операцию на root → записать Debt/OPEN envelope → открыть descendant savepoint → вызвать сохранённый `root_transaction.commit()`. `before_commit` вызывается **до** закрытия потомков; `get_nested_transaction()` показывает потомка, поэтому предложенный guard разрешает commit как release под OPEN-предком. Затем родитель коммитится без повторной проверки root.

Диагностическая реализация именно предложенного условия дала:

```text
ROOT_DIRECT_COMMIT allowed [('guard', False), ('guard', False)]
ROOT_DIRECT_PERSISTED 10.00000000
```

Основание: `SA/orm/session.py:1217–1231`, `:1253–1263`. Обычный `Session.commit()` и прямой commit сохранённого родителя нельзя считать эквивалентными для этого guard.

*Завершение не на своей границе:* операция открыта на root → первый эффект flush → открыть savepoint → выйти из `debt_operation`, оставив savepoint открытым. Проверка «bound tx находится в chain» проходит; COMPLETED/head/slot записываются внутри потомка. Затем rollback потомка возвращает envelope к OPEN и убирает completion, но запись в `session.info` остаётся COMPLETED. Root commit проходит.

**Нужно требовать завершение именно на связанной границе без открытого потомка**, а также проверять действительность записи handle, не только присутствие transaction в chain (`spec.md:619`).

**(b) Envelope/entries после rollback — условно да, на SQLite.**

После настоящего rollback общей PostgreSQL-транзакции Core entries откатываются вместе с Debt. Но на текущем SQLite отсутствует настройка явного BEGIN; released savepoint может оказаться самостоятельной DB-транзакцией:

```text
ASYNC_SQLITE_AFTER_ROOT_ROLLBACK 30.00000000
```

Последовательность: `Session.begin()` → `begin_nested()` до первого DML → INSERT → release → root rollback. Если envelope впервые вставляется внутри такого savepoint, сохранятся и он, и Debt. Текущие настройки: `app/db/session.py:31`, `:44`; `tests/conftest.py:102`, `:124`. Это [документированное ограничение SQLite transaction control](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#transactions-with-sqlite-and-the-sqlite3-driver).

**(c) Stale op после retry.**

После полноценного rollback привязка к объекту `SessionTransaction` и адресная очистка выглядят корректно. Descendant rollback внутри `_apply_flow` должен сохранять родительскую операцию (`engine.py:1429`, `:1492`). Но нужны проверки старого handle после rollback/reopen той же identity, ошибок **при открытии и завершении** контекста и `CancelledError`; обещания «exception in block» недостаточно.

**(d) Потеря root-poison.**

`expire_all()` меняет ORM-state, не `session.info`; greenlet также использует ту же sync-session (`SA/orm/session.py:3176`; `SA/ext/asyncio/session.py:1001`, `:1011`, `:1551`). Здесь обхода не обнаружено.

Но `Session.close()` **не всегда означает rollback DB-транзакции**. Для внешнего `Connection.begin()` default `conditional_savepoint` превращается в `rollback_only`; `should_commit=False`. Закрытие сессии вызывает root-end cleanup, оставляя внешнюю транзакцию живой:

```text
CLOSE_ROLLBACK_ONLY True {}
CLOSE_PERSISTED 20.00000000
```

То есть OPEN/poisoned UoW → `session.close()` → poison удалён → внешний `connection.commit()` сохраняет записи. Основание: `SA/orm/session.py:1162`, `:1178`, `:1361`, `:1377`.

**(e) Пропущенные commit.**

- Указанный пример clearing **не подтверждается**: `_commit_to_terminal()` вызывает `self.session.commit()`, не Connection commit (`app/core/clearing/service.py:302`). Global Session listener сработает.
- `Connection.commit()` и нормальный выход из `engine.begin()` Session-событие не вызывают.
- `join_transaction_mode="create_savepoint"` перехватывает Session commit, но он лишь выпускает savepoint. Это не доказательство durable commit или освобождения owner-lock (`tests/conftest.py:139`, `:268`; `spec.md:799`).

Нужен явный контракт допустимых владельцев DB-транзакции и защита реального commit, включая внешний Connection; одного `before_commit` в предложенной форме недостаточно.

**3. Hook и ORM history**

Для **нынешних трёх писателей на PostgreSQL при точно представимых суммах** алгоритм history обоснован:

- payment читает amount перед изменением; flush между применением и неттингом сбрасывает историю (`engine.py:1435`, `:1462`, `:1474`);
- clearing читает amount перед `-=` и delete (`clearing/service.py:1977`);
- inject загружает Debt и вычисляет сумму из `existing.amount` (`inject_executor.py:514`, `:551`).

Однако это история **ORM-значения**, а не безусловно сохранённого значения. На SQLite получено:

```text
AFTER_FLUSH_MEMORY 100000000000.00000001 DB 100000000000.00000000
NEXT_FLUSH_DELETED [Decimal('100000000000.00000001')]
```

Следовательно, `history.deleted[0]` уже ошибается относительно «предыдущей сохранённой». `version_id_col` этого не исправляет; он проверяет конкурентную версию, а не точность хранения (`app/db/models/debt.py:23`).

Core INSERT через `session.connection()` в `after_flush` допустим: `_warn_on_events` уже снят, ORM autoflush не запускается, исключение откатывает flush boundary (`SA/orm/session.py:4410–4448`). Но pending parents, добавленные **через relationships внутри контекста**, могут ещё иметь FK=None в `before_flush`; дизайн намеренно их отвергает — это надо отразить в API/helper, а не обещать общую поддержку pending parents.

`do_orm_execute` ловит обычный `update/delete(Debt)` **и Core Table DML через Session**; это подтверждено пробой и `SA/orm/session.py:2115`. Однако `statement.is_dml` недостаточно: `SELECT`, содержащий изменяющий CTE, имеет внешний `is_dml=False`. Legacy `bulk_*` также остаётся неперехваченным; простое перечисление исключений не выполняет обещание runtime-отказа bulk ORM (`spec.md:632`).

Дополнительная дыра дизайна: запрещены ORM **dirty/deleted** только двух моделей, но не `session.add(DebtJournalEntry(...))`, не вставка поддельного COMPLETED envelope и не ORM-изменения heads/slots. Нужен запрет new/dirty/deleted всех журналируемых служебных моделей вне единственного внутреннего Core-пути.

**4. Конкурентность и head CAS**

В штатных production-путях Debt изменяется под owner-lock:

- payment: owner-set до применения, освобождение при owner commit (`engine.py:408`, `:1259`, `:1402`);
- clearing: session-lock, rollback снимка захвата, свежий work transaction (`clearing/service.py:1585–1597`);
- inject: lock-set → staging → flush → commit (`real_runner_impl.py:565`, `:577`, `:641`).

Но это **не доказывает**, что каждый будущий head-write защищён: `intent_equivalent_ids` тоже создают slots; seed owner-lock не берёт; предлагаемые ремонты — тоже.

Общий head создаёт новое обязательное столкновение даже для непересекающихся рёбер. Последовательность: P2 читает PrepareLocks и получает снимок → ждёт owner-lock P1 → P1 обновляет head и коммитит → P2 получает lock, но сохраняет старый снимок → UPDATE head получает `40001`. Payment делает pre-read именно до lock (`engine.py:399`). Аналогичная проблема есть у transaction-level inject locks; только clearing явно обновляет снимок.

Это соответствует [семантике SERIALIZABLE PostgreSQL](https://www.postgresql.org/docs/18/sql-set-transaction.html). При непрерывной очереди каждый retry может снова устареть: payment ограничен тремя попытками (`app/config.py:121`), inject — одним повтором (`real_runner_impl.py:554`). «40001 storm обязательно будет» не измерено; **условия для пользовательского отказа существуют**, а C16 с двумя платежами их не исключает.

Дополнительно:

- При отсутствующей head строке наивный read→INSERT может получить `23505`, который текущий retry разрешает только для конкретного Debt constraint (`engine.py:427–440`). Нужен точный алгоритм cold-head создания.
- Атомарные CAS+slot+envelope и UNIQUE предотвращают committed duplicate/lost seq при правильном rollback. Независимый PostgreSQL sequence здесь действительно не нужен.
- Нового deadlock между тремя дисциплинированными владельцами не установлено. Для нескольких эквивалентов обязателен **полный owner-set до любых head writes**; позднее добирание lock недопустимо. Сортировка head UUID не заменяет сортировку owner-lock keys (`engine.py:150`).

**5. Правило для SQLite**

Допустимые `Numeric(20,8)` значения реально дали:

```text
100000000000.00000001 → 100000000000.00000000
100000000000.00000002 → 100000000000.00000000
999999999999.99999999 → 1000000000000.00000000
```

Отсюда:

- разные суммы могут получить одинаковый digest — ложное зелёное относительно намерения;
- history, delta и перечитанные before/after могут разойтись — ложное красное непрерывности;
- вычисление digest после readback лишь стабилизирует **уже потерявшее точность значение**.

Правило: точные денежные acceptance gates выполняются на PostgreSQL с независимыми integer-atom ожиданиями. SQLite проверяет механику на явно проверенных round-trip значениях; результаты вне этого поднабора не объявляются доказательством точности. Нельзя добавлять epsilon или округлять различия ради зелёного (`spec.md:671`, `:774`). SQLite rollback-control необходимо исправить отдельно от денежной точности.

**6. Миграция тестов**

`debt_fixture_setup(TEST_FIXTURE)` соответствует контракту **при фактическом закрытии до вызова приложения** (`spec.md:627`). Но «app code внутри запрещён благодаря nesting» неверно в общем случае: `_apply_flow` и `stage_inject_event` сами контекст не открывают и могут быть выполнены под TEST_FIXTURE. C10 с `engine.commit()` доказывает только этот конкретный вход.

Миграция всех затронутых setup необходима; **механическая замена add/add_all не доказана как минимально безопасная**. Helper добавляет flush, иногда раньше исходной точки исключения, и может менять:

- stale-state/version сценарии;
- группировку D/I и нескольких изменений в одном flush;
- FK-порядок, autoflush и момент ожидаемого constraint failure;
- исходный журнал/head, используемый последующими assertions.

Например, `test_apply_flow_retry_on_stale.py:64–85` обязан по-прежнему реально пройти `StaleDataError`; одного конечного `80` недостаточно. После codemod нужны сохранённые selectors/assertions/границы транзакций и проверка каждого изменённого негативного сценария. Cleanup raw SQL допустим только на изолированной тестовой DB; не расширять его до общего удаления чужой истории.

**7. Контрпримеры C1–C18**

В наборе **отсутствуют два прямо обязательных контрпримера шага 2**: конкурентный baseline и гонка удаления эквивалента (`spec.md:764`). Существующий T1524-тест можно переиспользовать, но C17 с готовой историей его не заменяет.

| Cases | Что изменить / ограничение доказательства |
|---|---|
| C1–C3 | Сохранить; добавить проглоченный отказ после уже выполненного flush, Core Table DML, legacy bulk и изменяющий CTE. |
| C4 | Хороший тест истории; добавить нулевой суммарный эффект с ненулевыми entries, delete→reinsert между flush и сохранённые значения на PG. |
| C5 | Равенство `journal = final − initial` проверяет только критерий (а). Дополнительно проверять независимый ожидаемый directed result. |
| C6 | **В текущей формулировке может быть пустым.** Подмена `a→b` на `a→c` меняет позиции и может быть остановлена существующим delta-barrier (`engine.py:1284`). Нужен неверный промежуточный маршрут с теми же конечными позициями; явно доказать committed wrong state, (а)=PASS, (б)=FAIL. |
| C7, C9, C11 | Добавить оба обхода из §2, completion внутри descendant с последующим rollback, ошибки `__aenter__/__aexit__`, cancellation, close/reuse и прямой parent commit. |
| C8 | Fake `40001` не доказывает snapshot-конфликт. Нужны реальные PG-конфликты на completion/head и проверка всех четырёх таблиц после retry/rollback. |
| C10 | Проверяет вложенный owner-entrypoint, но не общий запрет приложения под fixture-контекстом. |
| C12 | Проверять конечность и **точную представимость**, а не просто exponent/число знаков: `1.000000000` не требует потери точности. PG-control обязателен. |
| C13 | UNIQUE-error не равен доказательству идемпотентного исхода. Проверять конечные Debt/Transaction/envelope и повтор той же identity с другим intent. |
| C14 | Наличие envelope перед DELETE locks не доказывает завершение или durability. Нужна независимая проверка после commit; для clearing — снимок его собственного намерения. |
| C15 | Import-test — лишь wiring. В subprocess требуется реальная неинструментированная Debt-запись и отказ. |
| C16 | Два успешных платежа не проверяют rollback gaps, cold-head race, mixed writers и исчерпание retry. TEST_FIXTURE уже занимает seq: ожидать прирост от исходной головы, не обязательно `1,2`. |
| C17 | Нужны отдельно прямой FK-отказ, реальный API 409 и конкурентная гонка. Для участников остаётся CASCADE Debt; отсутствие journal rows не защищено историческими FK (`app/db/models/debt.py:11`). |
| C18 | Мутация объявления `version_id_col` — proxy. Нужен stale writer с реально изменённой конкурентом версией и наблюдаемым отказом/повтором. |

**Обязательный PostgreSQL:** baseline/delete races, реальные C8/C16, денежный C12, durable/replay части C7/C11/C13/C14/C17. Использовать SERIALIZABLE и независимые реальные транзакции; общий `db_session` скрывает commit/lock boundaries (`spec.md:799–804`). Добавить corruption-тесты для подделки служебных строк, неизвестных версий, неполного completion и несоответствия intent/effect scope.

**8. Периметр и разделение шагов**

До кода нужны явные решения по следующим расширениям:

- инструментирование ремонтов и measurement script — исключить из шага 4 либо отдельно авторизовать;
- изменение snapshot/lock/retry протокола payment/seed ради head CAS выходит за «только установка контекста» (`spec.md:746`, `:751`);
- размещение inject-контекста у владельца в `real_runner_impl.py` технически оправданно, но текущая строка разрешает там только владение UoW шага B3 (`spec.md:753`);
- исправление SQLite BEGIN и защита внешнего Connection затрагивают дополнительные session/transaction surfaces.

В шаг 5 перенести активное распределение seq, эпохи и запечатывание; в шаг 6 — полный verifier/эталон; в шаг 7 — operator command/containment. Обязательные контрпримеры должны быть зафиксированы заранее. Шаг 4 сам по себе не закрывает финансовую верификацию (`spec.md:767–774`).

**Обязательные изменения до кода**

1. Переписать transaction contract: проверять реальный commit target; закрыть прямой parent commit и внешние Connection-владельцы.
2. Требовать completion на исходной границе без открытых потомков; старый handle после rollback/reopen обязан отказывать.
3. Охватить poisoning весь lifecycle контекста, включая ошибки входа/выхода и cancellation; root-poison сохраняется до фактического завершения владельца.
4. Исправить SQLite BEGIN/savepoint semantics; отдельно закрепить пределы доказательства денежной точности.
5. Запретить неавторизованные ORM new/dirty/deleted всех служебных моделей; определить защиту legacy bulk и вложенного DML.
6. Задать строгие completion constraints: каждый обязательный атрибут COMPLETED — non-null, счётчики неотрицательны, формы эффектов и денежные значения валидны.
7. Перенести head CAS в шаг 5 либо представить отдельный PG-план cold-head, fresh-snapshot и mixed-writer retries, включая seed.
8. Исправить C6; добавить baseline/delete races и перечисленные transaction-обходы с непустыми положительными контролями.
9. Для codemod зафиксировать сохранение assertions, flush/commit boundaries и доказательство достижения исходных негативных путей.
10. Уточнить периметр; закрытые ремонты и measurement script оставить вне текущей реализации.