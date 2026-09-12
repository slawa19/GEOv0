T1525: NO-GO

Проверен `1530878..4d11a0b`, дерево чистое. Канонические гейты и файловые многосессионные тесты не запускались: clone read-only. Выполнены диагностические пробы в памяти на Python 3.11.9 / SQLAlchemy 2.0.25 / aiosqlite 0.20.0 / SQLite 3.45.1; успешные пробы — exit `0`.

1. **Полнота установки: текущие SQLite SQLAlchemy-конструкторы покрыты, гарантия стража — нет.**

   Контроль установлен в следующих местах:

   | Конструктор | Установка |
   |---|---|
   | `app/db/session.py:32` | `:61` |
   | `tests/conftest.py:103` | `:153` |
   | `tests/integration/test_audit_drift_delta_check_sse_integration.py:54` | `:61` |
   | `tests/integration/test_post_tick_audit_drift_runner_integration.py:59` | `:66` |
   | `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py:70` | `:72` |
   | `tests/integration/test_simulator_adaptive_clearing_integration.py:61` | `:68` |
   | `tests/integration/test_simulator_clearing_no_deadlock.py:69` | `:76` |
   | `tests/unit/test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py:78` | `:80` |
   | `tests/unit/test_sqlite_dev_schema_repair.py:70` | `:73` |
   | `tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py:112` | `:116` |

   Прямые `sqlite3.connect` **без контроля**: `scripts/check_sqlite_db.py:20`, `scripts/check_real_snapshot_debts_sqlite.py:89`, `scripts/run_simulator_run_and_analyze.py:348`, `scripts/run_local.ps1:878`, `tests/unit/test_alembic_postgres_only.py:70`. Они читают данные/схему, денежных записей через savepoint здесь нет. `scripts/init_sqlite_db.py:9`, `scripts/seed_db.py:18`, `scripts/cleanup_simulator_runs.py:33` наследуют application engine. Alembic и измерительный скрипт отвергают SQLite: `migrations/env.py:26`, `scripts/measure_clearing_min_amount_plan.py:110`; остальные найденные отдельные движки тестов — PostgreSQL.

   **Страж ложно зелёный на обходах.** Диагностика его настоящей функции дала:

   - алиас `create_engine as build`: `(findings=[], constructions=0)`;
   - два движка, установщик только на одном: `([], 2)`;
   - установщик внутри `if False`: `([], 1)`;
   - `pytest.mark.postgres` и фактически SQLite URL через переменную: `([], 0)`.

   Причина — сравнение имён и наличие любого установщика в scope: `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py:66`, `:119`, `:133`. Обычный конструктор внутри fixture или helper в сканируемых каталогах обнаруживается, включая runtime URL; алиасы и непрямые вызовы — нет.

2. **Атомарность savepoint исправлена; все последствия новых снимков не закрыты.**

   Явный `BEGIN` предшествует SQL savepoint (`app/db/sqlite_transaction_control.py:74`). Это покрывает `_apply_flow`, staged payments, clearing и inject на перечисленных движках.

   Но clearing на SQLite обходит владельца PostgreSQL-retry (`app/core/clearing/service.py:1459`), а busy превращается в E010 после rollback (`:2111`, `:2121`, `:66`). Обоснование «повторится следующим тиком» неполно: у HTTP clearing следующего тика нет; в simulator ошибка увеличивает `errors_total` (`app/core/simulator/real_clearing_engine.py:735`), который может остановить run (`app/core/simulator/real_tick_payments_coordinator.py:146`). Это регрессия доступности, не доказанная утечка долга.

3. **Маска правильная; обход исключений допускает неправильный retry.**

   `code & 0xFF == 5` корректно выделяет BUSY, включая 261/517/773; не захватывает LOCKED=6, PROTOCOL=15, constraint или IOERR. Исключение LOCKED разумно: причина может находиться в собственной connection/shared cache. PROTOCOL тоже может означать конкурентный конфликт, но его исключение — консервативный отказ, не ложное разрешение. [SQLite: result codes](https://www.sqlite.org/rescode.html).

   **P2: terminal error маскируется историческим busy.** Реальная диагностическая последовательность через aiosqlite дала:

   ```text
   current: SQLITE_CONSTRAINT_UNIQUE, code=2067
   __context__: SQLITE_BUSY
   sqlite_busy_error_name(current): SQLITE_BUSY
   expected: None
   ```

   Функция продолжает обход после обнаружения собственного non-busy кода и учитывает `__context__`: `app/db/sqlite_transaction_control.py:128`, `:133`. Поэтому контрпроба изолированного FK-error не доказывает «constraint никогда не повторяется».

   Ошибка непосредственно `commit()` сохраняет код и через SQLAlchemy `.orig` — проверено, получен `SQLITE_BUSY=5`. Но BUSY **не означает автоматический rollback**: проба `INSERT … RETURNING` с незавершённым cursor дала `cannot commit transaction - SQL statements in progress`, `in_transaction=True`, собственные строки оставались видимы до отката. Это также BUSY, который простое ожидание не исправляет.

4. **P1: обещанного replay staged-платежей у владельца тика нет.**

   Engine-owned retry откатывает корень и повторяет `_uow`: `app/core/payments/engine.py:576`, `:581`, `:526`. Savepoint-mode правильно пробрасывает busy (`:555`); service преобразует его в typed conflict (`app/core/payments/service.py:100`), executor пробрасывает дальше (`app/core/simulator/real_payments_executor.py:428`).

   **Дальше — rollback и `REAL_MODE_TICK_FAILED`, без replay:** `app/core/simulator/real_tick_orchestrator.py:535`, `:554`, `:569`. Диагностика исходного метода со stub-фазой, бросающей typed conflict: **1 вызов фазы, 1 session, 1 rollback, `errors_total=1`**. Следующий heartbeat увеличивает `tick_index`, а не повторяет прежний тик (`app/core/simulator/runtime_impl.py:917`).

   HTTP также не перезапускает всю операцию: busy при первоначальном создании `Transaction` возвращается вызывающему после rollback (`app/core/payments/service.py:820`, `:846`, `:866`). Новый тест проверяет более поздний engine commit, а не это окно.

   Inject действительно откатывается и заново staging-ит; бюджет — один transient retry плюс одно расширение lock-set (`app/core/simulator/real_runner_impl.py:564`, `:618`, `:657`). Для непосредственно полученного BUSY и успешного rollback двойное применение не обнаружено. Новые тесты проверяют отказ `flush()`, **не commit-failure**; обобщать их на последнюю ветку нельзя. Бюджеты engine/inject конечны; внутреннего повторения SQLite busy в прежнем savepoint нет.

5. **Прагмы корректны у application/default test engine, но не у каждого scratch engine.**

   Здесь listeners регистрируются до первого checkout; оба движка используют `NullPool`: `app/db/session.py:34`, `:46`, `tests/conftest.py:106`, `:146`. Текущего пути с уже открытой connection перед установкой не найдено.

   У пяти отдельных integration engines выше установлен только transaction control: **WAL/FK listeners отсутствуют**. Они не доказывают полную эквивалентность application engine; особенно результаты concurrency-тестов в rollback journal нельзя переносить на WAL.

   Поздняя установка ретроактивно ничего не гарантирует. Проба «SELECT → install → SAVEPOINT/INSERT/RELEASE → root rollback» дала **`has_sqlite_transaction_control=True`, surviving rows `[(1,)]`**. Следовательно, `has_*` — проверка регистрации listeners, не состояния существующей транзакции (`app/db/sqlite_transaction_control.py:94`).

6. **Evidence полезное, но недостаточное для заявленного закрытия.**

   `113 → 0` — подходящая метрика наблюдавшихся savepoint, не доказательство retry/liveness. Детектор и исходные JSON находятся по недоступным `<scratchpad>`-ссылкам: `specs/015-financial-core-verification/t1525-measurements.md:16`, `:110`. Независимо перепроверить подсчёт из clone нельзя.

   Замена тестового read→write на write-first действительно устраняет конкретную stale-snapshot гонку (`tests/integration/test_simulator_real_snapshot_db_enrichment.py:109`). Четыре успешных прогона не доказывают отсутствие остальных гонок. M7 при `n=4` честно назван неинформативным (`t1525-measurements.md:454`).

   Runtime False→True→False проверяет настоящий эффект, но только выбранную connection. Отдельные rollback-тесты сильнее наличия listener (`tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py:677`). Retry-тест проверяет успешный commit после конфликта, но чтение постороннего долга **новой проверочной сессией** не является прямым наблюдением перечитывания внутри retry (`tests/unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py:174`).

   Обозначение `isolation_level=None` как **shape**, а не effect, честное (`tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py:79`). Строка оправдана документированным рецептом отключения управления драйвером; доказательством дополнительного эффекта на 3.11 она не становится. [SQLAlchemy: SQLite transaction control](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html).

7. **Путь «чтение → ожидание → запись» в приложении есть.**

   Auth читает участника общей session (`app/api/deps.py:140`), HTTP payment затем ждёт Redis-lock (`app/api/v1/payments.py:80`, `app/utils/distributed_lock.py:68`) и пишет через service. Конкурирующий commit делает снимок устаревшим ещё до первого `Transaction INSERT`.

   Другие непокрытые read→write операции: trustline create `app/core/trustlines/service.py:121` → `:235`, update `:308` → `:380`, close `:447` → `:496`. Whole-UoW retry у них нет. Новые busy-отказы здесь не устранены payment-классификатором.

   Бесконечное DB-polling одного снимка и бессрочное удержание снимка между итерациями recovery/integrity не подтверждены. Узкое утверждение об отсутствии **blind DB polling** следует отличать от более широкого и неверного утверждения об отсутствии ожиданий внутри читающей транзакции.

8. **Расширение периметра необходимо, но выполнено не полностью.**

   Изменения engine, service и inject-классификатора связаны непосредственно с новой SQLite-семантикой. Несвязанного scope creep здесь не обнаружено. Проблема — отсутствие обещанного владельца replay и неполная оценка отказов на остальных application paths.

**Обязательные изменения**

- **P1:** реализовать ограниченный restart денежной части тика после typed conflict; детерминированный тест должен форсировать настоящий BUSY, показать повтор из нового снимка, сохранение намерений и отсутствие двойных debt/SSE-effects.
- **P2:** исправить классификацию terminal DBAPI-error с busy в контексте. Добавить реальные chained constraint/IOERR-контрпробы и отдельную проверку BUSY при `commit()` с подтверждением физического rollback.
- **P2:** закрыть или явно согласовать новые отказы HTTP payment-before-insert, clearing и trustline mutations. Проверки — конкурентный commit между чтением и первой записью; для clearing дополнительно error-budget simulator.
- Усилить либо сузить обещание source guard; добавить приведённые обходы. Проверять фактические WAL/FK настройки отдельных integration engines.
- Предоставить воспроизводимый детектор и доступные очищенные артефакты измерения; переписать claims под реально проверенные пути.

**Ложные утверждения**

- «Typed staged conflict заставляет тик переиграть UoW» — владельца replay нет.
- «В приложении нет read→wait→write» — опровергается auth → Redis-lock → payment.
- «Clearing busy означает только перенос на следующий тик» — возможны HTTP 500 и остановка simulator по error budget.
- «BUSY означает, что ничего не записано/всё уже откатилось» — неверно без подтверждённого rollback.
- «Любая новая конструкция без контроля сделает source guard красным» — опровергнуто исполнимыми пробами.
- «Все три прагмы раньше находились внутри транзакции» (`specs/015-financial-core-verification/spec.md:837`) — FK в `tests/conftest.py` уже был connect-listener; application pragmas также уже стояли на connect.

Числа `113 → 0`, M7 и маркировку shape-проверки ложными не называю; их пределы доказательства существенно уже заключения «T1525 выполнено».