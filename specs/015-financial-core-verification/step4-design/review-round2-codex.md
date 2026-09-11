STEP4-DESIGN-R2: NO-GO  
T1525-START: YES

Проверено чистое дерево `7de0a7c`. Ниже `v2` — `../step4_design_v2.md`, `SA/` — `../sqlalchemy-2.0.25-src/`, `spec.md` — `specs/015-financial-core-verification/spec.md`. Пробы — **debug-only**, Python/SQLAlchemy 2.0.25, SQLite в памяти; PostgreSQL, app-level reproduction и canonical gates здесь не запускались.

1. **Закрытие обязательных замечаний первого раунда**

   | № | Статус | Основание и остаток |
   |---|---|---|
   | 1 | **PARTIAL** | Реальный commit target теперь выбран правильно. Остались неподхваченный two-phase commit и неполная проверка AUTOCOMMIT — ниже (`v2:25`, `:29`, `:35`). |
   | 2 | **CLOSED** | Completion требует равенства Core-chain и Session-boundary, регистрации того же handle/generation (`v2:30`). |
   | 3 | **PARTIAL** | Классы poisoning определены, но registry очищается **до фактического rollback**; возможен durable OPEN. Есть также удержание завершённых roots (`v2:23–25`, `:32`). |
   | 4 | **PARTIAL** | T1525 выделен правильно, но ещё не реализован; проверка точности пропускает неточно сохраняемую **дельту** между двумя точно сохраняемыми суммами (`v2:50`, `:58`). |
   | 5 | **PARTIAL** | Unmapped tables закрывают ORM-вставки служебных моделей; grant разрешает посторонний DML во время flush (`v2:60`, `:70`). |
   | 6 | **CLOSED** | Обязательные completion-поля, формы эффектов и ограничения счётчиков определены (`v2:61–64`); нужна буквальная реализация с NOT NULL. |
   | 7 | **CLOSED** | Head CAS перенесён в шаг 5; создание головы только в baseline transaction устраняет предложенный cold-head INSERT (`v2:65`). |
   | 8 | **PARTIAL** | C6 существенно исправлен, но его audit=true требует дополнительной trustline; C10 противоречит release-семантике; durable C7 остался SQLite-only (`v2:91–95`). |
   | 9 | **PARTIAL** | R1–R3/R5 задают полезные ограничения, но R4 технически не наблюдает заявленные события; AST whitelist недостаточно определён (`v2:78`, `:83`). |
   | 10 | **CLOSED** | Ремонты и measurement исключены; расширения периметра перечислены. Перед реализацией их следует перенести в binding contract (`v2:79`, `:115`). |

2. **Core-event mechanism**

   **Подтверждён обход при неуспешном rollback.** Последовательность: savepoint-bound OPEN → Debt flush → `rollback_savepoint` удаляет op → следующий listener бросает исключение до SQL rollback → SQLAlchemy деактивирует/отвязывает savepoint → root commit видит пустой registry и сохраняет Debt.

   Проба предложенных правил дала:

   ```text
   ROLLBACK_ERROR rollback savepoint listener failure
   AFTER_FAILED_ROLLBACK registry 0 nested None dbapi_in_tx True
   FAILED_ROLLBACK_DURABLE_WITHOUT_COMPLETED [(42,)]
   ```

   Событие вызывается **до** `do_rollback_to_savepoint`; деактивация происходит в `finally` (`SA/engine/base.py:1150`, `:2819`). Аналогично root `rollback` event предшествует DBAPI rollback (`:1105`). Поэтому `v2:25` нельзя трактовать как уведомление об успешном откате. Отказ DB rollback или cancellation должен сохранять запрет либо инвалидировать connection.

   **Ещё один обход — two-phase root.** `TwoPhaseTransaction` наследует `RootTransaction`, но вызывает `commit_twophase`, а не `commit`. PostgreSQL допускает непосредственный commit неподготовленной two-phase transaction через обычный DBAPI commit: `SA/engine/base.py:2863`, `:2900`, `:1200`; `SA/dialects/postgresql/base.py:3217`. Поддержку реализовывать необязательно: достаточно явно отвергать такой root до открытия операции.

   **asyncpg:** успешный `dialect.do_rollback(conn.connection)` вызывает адаптерный `rollback()`, который ожидает `asyncpg.Transaction.rollback()` и в `finally` устанавливает `_transaction=None`, `_started=False`. В штатном успешном случае состояние адаптера согласовано; последующий `do_commit` не выполняется из-за исключения listener (`SA/engine/default.py:691`; `SA/dialects/postgresql/asyncpg.py:860`; `SA/engine/base.py:1123`). При ошибке/cancellation сброс флагов сам по себе не доказывает закрытия серверной транзакции — нужна предусмотренная invalidation, включая `BaseException`.

   Отказ **release** через `ROLLBACK TO` намеренно оставляет root открытым и poisoned. Поэтому требование C10 «DBAPI tx not open» для всех отказов неверно: idle требуется после отказа root commit; после отказа release — отсутствие эффектов savepoint и запрет root commit до полного rollback (`v2:21`, `:95`).

   **WeakKeyDictionary:** ранней GC-потери при нормальном `Session.commit`, повторном `session.connection()`, pool return или invalidate не обнаружено. Активный Connection удерживает root; invalidate запрещает переподключение до завершения старой transaction (`SA/engine/base.py:2673`, `:666`, `:704`). `expire_on_commit=False` сохраняет ORM-объекты, не старую DB-транзакцию.

   Но `registry → state → op.root → key` — сильная обратная ссылка. Проба после successful commit и GC: `root_alive True entries 1`. Это **неограниченное удержание**, поскольку успешный commit не очищает registry (`v2:23–25`). Нужны weak references либо явный корректный lifecycle очистки.

   `_savepoint`/`_previous_nested` соответствуют описанию (`SA/engine/base.py:2781`). `_flushing` действительно охватывает listeners — отсюда обход ниже. `_execution_options` **не содержит всех способов задания isolation**: при `create_engine(..., isolation_level="AUTOCOMMIT")` проба дала `{}`, хотя AUTOCOMMIT включён. SQLAlchemy дополнительно проверяет dialect `_on_connect_isolation_level` (`SA/engine/base.py:1041`).

3. **Runtime write guard**

   **Per-flush grant слишком широк.** В `after_flush` другого listener оба вызова проходят предложенное условие:

   ```python
   session.execute(insert(Debt).values(...))
   session.connection().execute(Debt.__table__.update().values(...))
   ```

   Проба: `durable [(1, 77), (2, 99)]`, разрешённые statements — `Insert`, `AnnotatedInsert`, `Update`. Эти записи не входят в ранее собранные ORM-effects. `_flushing=True` и прежний `flush_context` сохраняются до `after_flush_postexec` (`SA/orm/session.py:4412`, `:4442`; `v2:70`).

   Есть и соседний вариант: поздний `before_flush` listener добавляет/меняет Debt **после** вычисления журналируемых эффектов; SQLAlchemy затем заново собирает dirty/new (`SA/orm/session.py:4339–4346`). Разрешение должно подтверждать конкретную проверенную запись, а не временное окно flush.

   `insertmanyvalues`, executemany, RETURNING и ORM `update(..., synchronize_session=...)` сами по себе обхода `before_execute` не создают: исходный DML проходит через него до выполнения/разбиения на batches (`SA/engine/base.py:1608`, `:2030`). Но внутри широкого grant они также разрешены.

   **Отказ до SQL не требует немедленного DB rollback**, если root надёжно poisoned: предыдущие успешные записи не должны стать durable после проглоченного исключения. Именно этот сценарий должен проверять C1. Это отличается от отказа внутри commit event.

4. **T1525**

   Репродукция **достоверна по коду**: до `_apply_flow` идут чтения; затем savepoint, Debt flush и release; invariant failure вызывает root rollback уже после release (`app/core/payments/engine.py:1247`, `:1259`, `:1297`, `:1429`, `:1462`). Для legacy SQLite это достаточный механизм описанного нарушения атомарности. **P1 оправдан для действующего SQLite-пути**, а не как установленный PostgreSQL-дефект.

   Рецепт правильный: `isolation_level=None` ставится на **адаптированном DBAPI connection**. Его setter переносит изменение underlying sqlite3 connection в очередь aiosqlite; напрямую трогать `_conn` не нужно (`SA/dialects/sqlite/aiosqlite.py:61`, `:227–242`). `begin → exec_driver_sql("BEGIN")` соответствует [официальному рецепту SQLAlchemy](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#enabling-non-legacy-sqlite-transactional-modes-with-the-sqlite3-or-aiosqlite-driver).

   WAL из `tests/conftest.py:247` необходимо вынести из transaction и проверять фактический режим; приложение уже устанавливает PRAGMA в connect (`app/db/session.py:46`). Explicit BEGIN добавляет настоящие read snapshots: конкурентное read→write повышение может дать `SQLITE_BUSY_SNAPSHOT`; timeout и повтор savepoint не заменяют новый root snapshot.

   **Deferred BEGIN — правильный исходный выбор.** Оснований немедленно переходить на IMMEDIATE нет. Нельзя заранее объявлять adaptive clearing/no_deadlock сломанными: оба clearing-пути сначала коммитят parent (`real_tick_clearing_coordinator.py:333`, `:477`), а тест проверяет фактические циклы (`tests/integration/test_simulator_clearing_no_deadlock.py:293`). Предложенные многосессионные замеры обязательны перед закрытием T1525.

5. **Денежная точность**

   Утверждение для scale-8 `|v| < 2^26` **корректно как достаточная область**: максимальная ошибка преобразования в binary64 меньше половины атома; result processor форматирует до восьми знаков (`SA/sql/sqltypes.py:568`, `SA/engine/_py_processors.py:80`).

   Dialect-specific refusal допустим: различие с PostgreSQL отражает реальное ограничение SQLite. Полноразмерные денежные acceptance-тесты должны оставаться PostgreSQL-тестами, а не получать ослабленные ожидания.

   **Но проверки только before/after недостаточно.** Проба:

   ```text
   before = 100000000000          → точно
   after  = 0.00000001            → точно
   delta  = -99999999999.99999999  → -100000000000.00000000
   ```

   Оба Debt-значения проходят `v2:58`, journal delta — портится. Проверять нужно **все сохраняемые денежные поля**, включая вычисленную delta, до Debt SQL; иначе журнал нарушает критерий (а).

6. **Схема и шаг 5**

   Нового препятствия разделению шагов не найдено. Baseline-only создание heads соответствует принятому решению; snapshot/retry и согласование pre-epoch operations остаются обязательствами шага 5 (`v2:65`, `:112`; `spec.md:643`, `:769`).

   `length()` и `CHAR(64)` переносимы для hex digest. На PostgreSQL CHAR имеет семантику пробельного дополнения; это не обход длины для штатного 64-символьного hex ([документация PostgreSQL](https://www.postgresql.org/docs/17/datatype-character.html)). В SQLite объявленная длина CHAR сама не ограничивает значение — CHECK действительно нужен.

   Partial index следует объявить **для обоих dialects**, включая `sqlite_where`; прецедент существует в `app/db/models/trustline.py:42`. Зафиксировать NOT NULL для обязательных полей и группировку completion-CHECK: SQL CHECK, вернувший NULL, не отвергает строку. Сам перенос cross-row verification в шаг 6 не переоткрываю.

7. **C1–C22 и §10**

   **C6 теперь способен достигнуть неверного committed результата.** A→B→C и A→C имеют одинаковые net deltas; payment trust-check проверяет только подготовленные пары (`app/core/payments/engine.py:1252–1287`, `:1559`; `app/core/invariants.py:125`).

   Однако `audit verification_passed=true` не гарантирован: checkpoint проверяет **весь** эквивалент (`app/core/integrity.py:94`, `:126`; `engine.py:1341–1365`). Нужна активная trustline **C→A с limit≥x**, при авторитетном intent A→B→C. Wrapper должен записать A→C **ровно один раз**, поскольку `_apply_flow` вызывается для каждого сегмента.

   Недоклиринг через одинаковый остаток в один атом сохраняет позиции и проходит neutrality (`app/core/clearing/service.py:1980`; `app/core/invariants.py:269`). Контроль без mutation и независимое чтение обязательны.

   Оставшиеся пробелы:

   - C2 не проверяет DML внутри действующего grant.
   - C7 нужен также на PostgreSQL: round-1 durable rollback/reopen obligation исчезнуть не должен.
   - C10 нужно разделить на root refusal и release refusal.
   - C12 добавить точные operands → неточная delta.
   - C13 разделить duplicate opening → IntegrityError и настоящий replay → ранний возврат без открытия envelope (`v2:98`; `engine.py:1044`).
   - Добавить ошибки **самого rollback**, AUTOCOMMIT engine option и two-phase root.

   **§10.2 допустим как незакрытый acceptance contract**, без placeholder/skip/xfail. Он не является выполненным PG gate и не позволяет объявить весь шаг 2 закрытым (`spec.md:737–738`, `:764`; `v2:108`, `:112`). Его статус в перечне «PG mandatory» нужно явно обозначить как pending step 5.

8. **Codemod R1–R6**

   R1–R3 и R5 полезны; подсчёт assertions — структурный контроль, не доказательство сохранения смысла.

   **R4 в описанном виде не реализуем:** `before_cursor_execute` не видит DBAPI commit/rollback, implicit BEGIN и исключения Python из app frames. Проба INSERT→rollback дала trace только из CREATE и INSERT (`SA/engine/default.py:688–695`; `v2:83`).

   Нужны отдельные transaction events, результат завершения транзакции и явные reach-маркеры негативных путей. Нормализация должна сохранять параметры, кратность/batches и принадлежность транзакции. Иначе изменение суммы или исчезновение rollback даст false green. Допустимые изменения flush из-за открытия/завершения envelope следует определить явно, не скрывать общей нормализацией.

   AST guard также должен проверять **выражения рекурсивно**: whitelist statement «attribute assignment on local» сам по себе не запрещает вызов приложения в RHS. R6 приемлем только со scoped reset и проверенной отдельной test DB (`v2:78`, `:83`).

9. **Развилки**

   | Fork | Решение |
   |---|---|
   | R2-1 | **DISAGREE:** version pin не исправляет сильную ссылку value→root и неполную AUTOCOMMIT-проверку. |
   | R2-2 | **AGREE:** rollback-then-raise корректен при гарантированной invalidation на неуспехе; release не обязан оставлять backend idle. |
   | R2-3 | **AGREE:** разделение root poison и discardable body failure соответствует staged payments. |
   | R2-4 | **AGREE:** unmapped Core tables устраняют лишний ORM-путь записи. |
   | R2-5 | **DISAGREE:** grant на весь flush допускает незарегистрированный DML. |
   | R2-6 | **DISAGREE:** правило неполно без проверки сохранности delta и остальных journal money fields. |
   | R2-7 | **AGREE:** deferred BEGIN и installer на каждый SQLite engine — разумный вариант с обязательными замерами. |
   | R2-8 | **AGREE:** нарушение атомарности действующего денежного пути оправдывает P1. |
   | R2-9 | **AGREE:** отказ ремонтов можно проверять сейчас, сохраняя успешный путь как незакрытое обязательство T1511. |
   | R2-10 | **AGREE:** принятый inject с нулевым эффектом остаётся операцией. |
   | R2-11 | **DISAGREE:** нужен рекурсивный whitelist выражений и отрицательный тест скрытого вызова приложения. |
   | R2-12 | **AGREE:** отсутствие baseline означает UNVERIFIABLE, а не успешную проверку. |
   | R2-13 | **AGREE:** ранее принятый отдельный scope participant CASCADE не переоткрываю. |
   | R2-14 | **DISAGREE:** предложенный tracer не наблюдает заявленную transaction/exception trace. |

10. **Последовательность**

    **T1525 можно начинать сейчас независимо:** это отдельное предусловие, уже записанное в `spec.md:781`. В его scope входят red reproducers, installer, PRAGMA placement, все SQLite engine constructions и измерение блокировок.

    Ждать B4 должны журналовый runtime refusal через `has_sqlite_transaction_control()`, grants, registry и precision policy. T1525 не следует расширять ими или считать закрытым только по низкоуровневому savepoint-тесту.

**Обязательные изменения до кода** — для B4; начало T1525 не блокируют:

1. Заменить очистку registry по *предварительным* rollback events протоколом, безопасным при ошибке/cancellation; контрпример failed rollback→root commit обязан не сохранять Debt.
2. Устранить сильное удержание завершённых roots; добавить GC/reuse-контроль. Полностью проверять AUTOCOMMIT и явно отвергать unsupported two-phase roots.
3. Сузить write grants до проверенных записей; запретить посторонний DML и поздние ORM-мутации из flush listeners, сохранив положительные batch/RETURNING-контроли.
4. Проверять точную сохраняемость **delta** наряду с before/after; приведённый контрпример должен отказывать до Debt SQL.
5. Исправить C6 fixture/wrapper, разделить C10, вернуть PostgreSQL C7 и добавить перечисленные lifecycle/grant-контрпримеры.
6. Переписать R4 на реально наблюдаемые события и результаты; мутации удаления rollback, переноса flush и изменения суммы должны делать gate красным.
7. Уточнить рекурсивный AST whitelist; вызов приложения внутри разрешённого выражения должен отвергаться.
8. Зафиксировать pending-статус §10.2 и согласованные изменения периметра в binding contract; **до реализации журнала завершить T1525 с app-level и многосессионным evidence**.