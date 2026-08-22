# 015 — Внешнее исследование финансового ядра, 2026-08-21

**Провенанс.** Внешний исследователь — Codex по `docs/external-review-runbook.md` §3. Замороженный
credential-free клон на HEAD `de4dd08` (0 untracked, 0 файлов `.env`), `codex-cli 0.144.0-alpha.4`,
`--sandbox read-only`, флаг `-m` не передавался — фиксируется как непроверенный выбор модели.
Exit `0`, маркер `RESEARCH-DONE` получен, вывод полный. Задание — `research-brief.md`.

**Статус документа: ВХОД, а не спека.** Ниже — вывод внешней системы. Он перепроверяется
оркестратором по коду перед тем, как что-либо из него станет находкой программы. §1 `AGENTS.md`
различает вход в триаж и подтверждённую находку; этот файл — первое.

## Что оркестратор уже перепроверил по коду — 2026-08-21

Проверено на HEAD `35e0f67`, независимо от исследователя:

1. **ПОДТВЕРЖДЕНО и является самой острой находкой захода.** `app/core/payments/engine.py:1545-1554`:
   `tolerance = Decimal("0.00000001")`, сравнение `if abs(drift) > tolerance`. Величина `1e-8` — это
   **ровно один квант хранилища** `Numeric(20, 8)` (`app/db/models/debt.py:14`), а сравнение строгое.
   Следовательно расхождение ожидаемой и фактической дельты **в целый квант на операцию** проверку
   проходит и в БД сохраняется как вход следующей операции.

2. **ПОДТВЕРЖДЕНО дословно.** `tests/unit/test_clearing_additional_cases.py:813-851` — тест
   `test_execute_clearing_checkpoint_failure_is_explicitly_best_effort` роняет after-checkpoint
   `RuntimeError`-ом и утверждает `result == Decimal("10")`. То есть клиринг коммитится и
   рапортует успех при упавшей проверке целостности, а набор закрепляет это как намеренное.

3. **ПОДТВЕРЖДЕНО ранее, наблюдением на живой БД** (см. `research-brief.md`, раздел «Второе
   установленное»): валидация принимает `scale` до 18, хранилище `Numeric(20,8)`, Postgres молча
   округляет в обе стороны. Исследователь этот пункт назвал статически и честно записал, что режим
   округления без запуска не проверял; наблюдение оркестратора его закрывает.

**Связь пунктов 1 и 3, которую не назвал ни исследователь, ни бриф:** округление при сохранении
даёт расхождение **того же порядка**, что и допуск delta-проверки. Механизм накопления замкнут: вход
округляется на величину до кванта, а проверка, которая должна была это заметить, квант прощает.

**Не перепроверено оркестратором** и остаётся входом: инвентаризация тестов целиком, полнота журнала,
три независимых writer-а `Debt`, расхождения с протоколом из раздела 6, план тестов из раздела 8.

---

## 1. Вердикт

На HEAD `de4dd08` денежную корректность системы доказать нельзя: сильные проверки отдельных платежей, клиринга и PostgreSQL-конкурентности существуют, но полной фальсифицируемой сверки журнала с `debts` нет (`app/core/payments/engine.py:1203-1279`, `app/core/invariants.py:260-289`). Главный пробел — журнал не является полным, упорядоченным и обратимым описанием всех изменений долга: прямые записи создают seed, simulator injection и admin repairs (`scripts/seed_db.py:384-464`, `app/core/simulator/inject_executor.py:266-365,740-760`, `app/api/v1/integrity.py:297-467`). Публикуемый zero-sum структурно не способен заметить порчу суммы и уже зарегистрирован как `B-A3-001` с маршрутом в 014 (`app/core/invariants.py:62-85`, `specs/008-surface-code-review/tasks.md:92-98`). Дополнительно платеж и клиринг могут завершиться без полноценного checkpoint/audit evidence (`app/core/payments/engine.py:1140-1178,1281-1377`, `app/core/clearing/service.py:1427-1549`). Исследование статическое: код и тесты не запускались, поэтому фактический зелёный baseline не утверждается.

## 2. Инвентаризация тестов

| Файл | Что заявляет | Способен ли упасть | Чем доказано | Вердикт |
|---|---|---:|---|---|
| `tests/unit/test_invariants.py:23-35` | Zero-sum для простого долга | Нет при порче `amount` | Проверяется только здоровый пример; реализация складывает каждую строку с обоими знаками (`app/core/invariants.py:62-85`) | Нефальсифицируемое покрытие `B-A3-001`, маршрут 014 |
| `tests/unit/test_invariants.py:39-179` | Trust limit и abort платежа при нарушении | Да | Создаётся долг выше лимита и ожидается `IntegrityViolationException` (`:39-61,73-179`) | Реальная component-защита; конкурентность не доказана |
| `tests/unit/test_invariants.py:183-250` | Нейтральность клиринга | Да | В негативном тесте меняется одно ребро, ожидается `CLEARING_NEUTRALITY_VIOLATION` (`:220-250`) | Фальсифицируемо; это не тавтология |
| `tests/unit/test_invariants.py:254-580` | Статусы checkpoint и audit успешных payment/clearing | Частично | Trust-limit/symmetry имеют негативные случаи; audit проверяется только на успешном пути (`:254-375,379-580`) | Доказывает happy path, не обязательность audit |
| `tests/unit/test_integrity_checkpoints.py:18-170` | Fail-closed недоступного checker и статусы инвариантов | Да, кроме healthy zero-sum | Есть injected failure, rollback и реальные trust/symmetry violations (`:18-58,96-170`) | Сильное component-покрытие; zero-sum внутри остаётся пустым |
| `tests/integration/test_integrity_endpoints.py:33-180` | Integrity API, checksum, сериализация, отказ checkpoint | Частично | Ошибка checkpoint не проглатывается (`:154-180`), но checksum проверяется лишь как строка длины 64 (`:57-80`) | Wire/fail-closed доказаны; содержательная денежная сверка — нет |
| `tests/integration/test_integrity_repairs_atomicity.py:61-350` | Атомарность repairs и admin audit | Да | Проверяются точные итоговые долги и отсутствие debt/audit после precommit failure (`:61-144,236-350`) | Атомарность доказана; audit summary недостаточен для обратного воспроизведения |
| `tests/unit/test_payment_delta_check.py:15-72` | Обнаружение расхождения planned/actual delta | Да | Expected `10`, actual `7`, ожидается drift `3` (`:15-72`) | Реальный falsifier helper-а; не доказывает, что commit всегда вызывает его |
| `tests/unit/test_payments_2pc.py:19-397` | Terminal idempotency, expired locks, fail-closed повреждённых flows | Да | Проверяются `ABORTED`/`COMMITTED` и сохранность lock при изменении после ожидания (`:19-113,208-397`) | Сильное SQLite component-покрытие, не реальная DB-конкурентность |
| `tests/integration/test_payments_idempotency.py:29-184` | Повтор `tx_id`, конфликт payload, обязательность `tx_id` | Да | Проверяются HTTP-результат и конфликт (`:29-184`) | Доказывает API-семантику, но не единственность денежного эффекта |
| `tests/integration/test_payment_idempotency_postgres.py:18-224` | Конкурентный duplicate payment | Да | Одна `Transaction`, долг `10.00000000`, один audit и одна публикация (`:215-224`) | Сильнейшее доказательство идемпотентности payment occurrence |
| `tests/integration/test_payment_commit_advisory_locks_postgres.py:188-862` | Сериализация prepare/commit/abort | Да | Проверяются блокировка, один денежный эффект и терминальное состояние (`:188-335,342-516,523-862`) | Реальное PostgreSQL evidence |
| `tests/integration/test_payment_engine_uow_retry_postgres.py:15-411` | Whole-UoW retry и concurrent insert | Да | После `40001` остаются правильные состояния, долг и число audit (`:15-182,196-411`) | Реальное PostgreSQL evidence |
| `tests/integration/test_payment_engine_audit_conflict_postgres.py:17-181` | Retry при DB-конфликте во время audit | Да | Инъецируется serialization failure, итог `COMMITTED` (`:17-181`) | Доказывает DBAPI-ветвь; не generic audit/checkpoint failure |
| `tests/integration/test_payment_inverse_multisegment_postgres.py:231` и `test_payment_pair_advisory_locks_postgres.py:22` | Обратные и multi-segment платежи | Да | Реальная конкуренция на общих pair locks | Сильное узкое evidence, не глобальная ledger-сверка |
| `tests/integration/test_payment_staged_multicall_postgres.py:207-490` | Сохранение locks, порядок владельцев, timeout restoration | Да | Проверяются `40001`, четыре `COMMITTED` и реальное ожидание lock (`:207-490`) | Сильная механика staged UoW |
| `tests/unit/test_payment_engine_advisory_locks_execute.py:65-540` и `test_payment_engine_advisory_lock_key.py:8-90` | Формирование и порядок advisory locks | Да для изменения вызовов | Проверяются SQL-вызовы/ключи через doubles | Механизм, не доказательство реального contention |
| `tests/unit/test_payment_engine_retry_savepoint_nocommit.py:41-190` | Savepoint/retry/error mapping | Да | Инъецируются SQLSTATE и lock timeout (`:41-190`) | Механика классификации; не денежный результат |
| `tests/unit/test_payment_staged_post_commit.py:69-270` | Staged rollback/commit/cancellation | Да | Проверяются отсутствие или однократность rows/effects (`:69-270`) | Полезная SQLite-атомарность |
| `tests/unit/test_payment_cleanup_cancellation.py:11-70`, `test_payment_timeouts.py:21-165` | Terminal cleanup после cancellation/timeout | Да | Инъецируются отмены и таймауты | Lifecycle evidence, не restart evidence |
| `tests/integration/test_payment_prepare_error_taxonomy.py:162-1570` | Error taxonomy и безопасный abort | Да | Много injected DB/client/cancellation failures | Ошибки и rollback; не самостоятельное доказательство сумм |
| `tests/integration/test_payment_prepare_capacity_policy.py:122-230` | Учёт persisted/local reservations | Да | Меняется набор reservations | Реальная проверка capacity policy, но без конкурентного PostgreSQL расписания |
| `tests/integration/test_payments_multipath.py:16-139` | Разбиение `10.00` по двум маршрутам | Только при грубом дефекте | Сумма переводится в `float` и округляется до двух знаков (`:134-139`) | Не доказывает Decimal-exactness или сохранность остатка |
| `tests/integration/test_payments_amount_validation.py:29-94` | Отказ некорректных amount | Да | Проверяются NaN, exponent, scale 30 и отрицательное (`:57-94`) | Доказывает rejection; не проверяет допустимые 9–18 знаков против `Numeric(20,8)` |
| `tests/integration/test_payments_constraints_avoid.py:18`, `test_payments_insufficient_capacity.py:16`, `test_payments_list_filters.py:31` | Routing policy и API-форма | Да | Изменение фильтра/ёмкости ломает ожидаемый HTTP-результат | Не заявляют и не доказывают итоговую денежную сверку |
| `tests/integration/test_clearing_commit_replay_postgres.py:56-815` | Конкурентный clearing occurrence и post-commit reconciliation | Да | Одна транзакция/audit для одного occurrence; новый набор debt IDs даёт второй occurrence (`:249-251,581-815`) | Сильная идемпотентность одного исполнения; это не replay ledger с нуля |
| `tests/integration/test_clearing_payment_prepare_interlock_postgres.py:258-1010`, `test_clearing_skip_releases_locks_postgres.py:23-300`, `test_concurrent_clearing_payment_lost_update_postgres.py:46` | Clearing/payment interlock и lost-update protection | Да | Реальные PostgreSQL locks, cancellation и concurrent payment | Сильное concurrency evidence |
| `tests/unit/test_clearing_additional_cases.py:103-851` | Policy, rollback, несколько циклов, failure handling | Да, но неоднородно | Есть негативные policy/DB случаи (`:103-809`) | Полезно; тест `:813-851` отдельно закрепляет продолжение без after-checkpoint |
| `tests/unit/test_zero_debt_policy.py:17-85` | Удаление нулевых долгов и enriched clearing payload | Да | Проверяются `COMMITTED`, отсутствие debts, `edges` и audit (`:17-85`) | Полезная трассируемость текущего формата |
| `tests/unit/test_clearing_sql_cycle_detection.py:15-220` | SQL triangles/quadrangles и фильтрация | Да | Меняется форма графа/вершины | Доказывает discovery, не правильность суммы исполнения |
| `tests/unit/test_clearing_plan_edges_extraction.py:27-60` | UI-plan extraction clearing edges | Формально да | Тестирует локально скопированную логику, а не product import | Устаревшее/ложное покрытие, уже отмечено в `specs/008-surface-code-review/tasks.md:815-825` |
| `tests/unit/test_clearing_prepare_lock_conflict.py:83` | Исключение prepare-locked edges | Частично | Fake session направляет выполнение в DFS | Не доказывает SQL-path фильтр; уже маршрутизировано (`specs/008-surface-code-review/tasks.md:823-825`) |
| `tests/integration/test_clearing_max_depth_controls_long_cycles.py:10` | Управление глубиной | Да | Цикл длины 5 блокируется/разрешается | Не проверяет границу v0.1 `6/7` |
| `tests/unit/test_recovery_cleanup.py:19-450` | Повторный abort и восстановление stale payments | Да | Проверяются повторные итерации, terminal outcomes и rollback failure (`:54-450`) | Хорошее SQLite component-evidence; реальный restart+PostgreSQL не доказан |
| `tests/unit/test_trustline_audit_fail_closed.py:24-295` | Trustline mutation не коммитится без checkpoint | Да | Checker/checkpoint failures инъецируются до commit (`:24-295`) | Сильный положительный образец fail-closed |
| `tests/unit/test_post_tick_audit.py:30-122` | Обнаружение simulator balance drift | Да | Внесён drift, ожидается `total_drift=30` (`:30-122`) | Фальсифицируемо, но только по participant net |
| `tests/integration/test_post_tick_audit_drift_runner_integration.py:120-305` | SSE и отрицательный IntegrityAuditLog при drift | Да | Инъецирован drift; ожидаются событие и `verification_passed=False` (`:268-305`) | Доказывает signal path, не полноту глобального аудита |

PostgreSQL-тесты способны падать и дают настоящее concurrency evidence, но job `postgres` запускается только по schedule/workflow dispatch; зелёный обычный PR его не включает (`AGENTS.md:152-169`). SQLite-прохождение прямо не считается доказательством advisory locks, isolation и concurrent writers (`AGENTS.md:198-210`).

Тесты, выглядящие как покрытие, но не являющиеся им:

- Healthy zero-sum assertions в `tests/unit/test_invariants.py:23-35`, `tests/unit/test_integrity_checkpoints.py:62-92` и `tests/integration/test_integrity_endpoints.py:57-81`: любое изменение положительного `Debt.amount` сохраняет их зелёными.
- `test_integrity_checksum_returns_404_until_checkpoint_exists` проверяет только наличие 64-символьной строки, не алгоритм или связь с состоянием (`tests/integration/test_integrity_endpoints.py:57-80`).
- “Replay” в `test_clearing_commit_replay_postgres.py` означает повтор одного deterministic occurrence, а не восстановление `debts` из истории (`app/core/clearing/service.py:155-186`).
- `test_payment_engine_delta_check_raises_on_drift` вызывает helper напрямую; удаление его вызова из commit не обязано уронить тест (`tests/unit/test_payment_delta_check.py:15-72`, вызов — `app/core/payments/engine.py:1256-1260`).
- Multipath-тест скрывает sub-cent и binary-float расхождения через `float` и `round(..., 2)` (`tests/integration/test_payments_multipath.py:134-139`).
- `test_execute_clearing_checkpoint_failure_is_explicitly_best_effort` доказывает прямо противоположное полной верификации: clearing остаётся `COMMITTED` после потери after-checkpoint (`tests/unit/test_clearing_additional_cases.py:813-851`).
- Mocked advisory-lock tests и SQLite optimistic-lock tests доказывают вызовы/ORM-механику, но не реальное PostgreSQL расписание (`tests/unit/test_payment_engine_advisory_locks_execute.py:65-540`, `AGENTS.md:198-210`).
- `test_clearing_plan_edges_extraction.py` и fake-session `test_clearing_prepare_lock_conflict.py` не достигают соответствующих product paths (`specs/008-surface-code-review/tasks.md:815-825`).

## 3. Перемотка и сверка

Полная перемотка `debts` с нуля сейчас невозможна для общего состояния репозитория.

Что уже пригодно:

- PAYMENT хранит отправителя, получателя, эквивалент, исходную сумму и точные строковые amounts по маршрутам (`app/core/payments/service.py:641-668`).
- Современный CLEARING хранит эквивалент и список `debtor/creditor/amount` по каждому ребру (`app/core/clearing/service.py:1397-1465`; принятое решение — `docs/ru/09-decisions-and-defaults.md:44-46`).
- Для ограниченного набора данных, созданного после известного baseline исключительно этими двумя writers, их эффекты можно воспроизвести.

Что мешает полной перемотке:

1. `Transaction.payload` — произвольный versionless JSON; нет схемы версии, commit sequence, immutable-chain или ограничения на изменение payload после commit (`app/db/models/transaction.py:6-27`).
2. `IntegrityAuditLog.tx_id` nullable и не является FK/unique-связью с `Transaction`; audit содержит hashes, но не обязательные точные debt deltas (`app/db/models/audit_log.py:28-44`).
3. Seed вставляет debts независимо от импортируемого подмножества transactions (`scripts/seed_db.py:384-464`).
4. Simulator injection напрямую создаёт/увеличивает Debt, округляет вниз до `0.01` и коммитит без Transaction/IntegrityAuditLog (`app/core/simulator/inject_executor.py:266-365,740-760`).
5. Admin repairs меняют или удаляют конкретные debts, но audit сохраняет только агрегаты `scanned/updated/deleted`, а не исходные и итоговые ребра (`app/api/v1/integrity.py:297-378,389-457`).
6. Trustline history нужна для проверки исторической допустимости маршрута, но update audit не содержит old/new limit или policy и не имеет `tx_id` (`app/core/trustlines/service.py:169-248`). Отсутствие trustline Transactions является осознанным MVP-решением, а не само по себе дефектом (`docs/ru/02-protocol-spec.md:236-243`).
7. Checkpoint hash нельзя обратить в состояние; кроме того, его модель не фиксирует версию алгоритма (`app/db/models/integrity_checkpoint.py:6-15`).

Предлагаемый фальсифицируемый инвариант для канонической пары участников `A < B`:

```text
Ldb(E,A,B) =
    debt(A → B, E) - debt(B → A, E)

Ljournal(E,A,B) =
    baseline(E,A,B) + Σ exact_signed_debt_delta(event,E,A,B)

require:
    Ldb(E,A,B) == Ljournal(E,A,B)
    для каждой пары и эквивалента,
    без лишних DB-рёбер, пропущенных или дублированных событий.
```

Для payment hop `u→v` сумма добавляется к направлению `u owes v`; для clearing edge `debtor→creditor` она вычитается. В отличие от zero-sum, проверка падает при изменении одного amount, при равном увеличении всех рёбер цикла, при неправильном маршруте, пропуске/дублировании события, потерянном multipath residual или незажурналированном repair. Clearing дополнительно проверяется против восстановленного pre-state: зафиксированная сумма обязана равняться минимуму amounts рёбер перед операцией (`app/core/clearing/service.py:1309-1314`).

Минимальная измеренно необходимая добавка — не event-sourcing framework, а versioned append-only запись точных debt deltas в том же DB UoW для каждого writer. Её можно реализовать расширением существующего integrity audit или узкой таблицей; обязательные данные: monotonic commit sequence, schema/checksum version, operation/tx identity, equivalent, debtor, creditor, signed delta, before/after amount и genesis/baseline marker. Необходимость следует не из архитектурного вкуса, а из трёх уже существующих необратимых writers (`scripts/seed_db.py:384-464`, `app/core/simulator/inject_executor.py:266-365`, `app/api/v1/integrity.py:297-467`).

## 4. Узлы денежного пути

- **Payment API/service.** До routing ищет `tx_id`, сверяет тип, инициатора и fingerprint; terminal retry возвращает сохранённый результат, незавершённый — конфликт (`app/core/payments/service.py:199-241,523-536`). Глобальная уникальность `tx_id` и обработка race присутствуют (`app/db/models/transaction.py:9-10`, `app/core/payments/service.py:646-694`). Идемпотентность одного payment occurrence подтверждена на PostgreSQL (`tests/integration/test_payment_idempotency_postgres.py:18-224`).

- **Router/multipath.** Capacity учитывает текущий прямой долг, обратный долг, который может быть погашен, и reservations (`app/core/payments/router.py:288-340`). Разбиение выполняется через `Decimal`, `alloc=min(remaining,bottleneck)` и точное вычитание остатка; явного округления здесь нет (`app/core/payments/router.py:460-549`). Router сам не обязан быть идемпотентным относительно изменяющегося графа; защита обеспечивается lookup `tx_id` до повторного routing (`app/core/payments/service.py:523-538`).

- **Prepare/commit.** Commit повторно читает и блокирует durable состояние, применяет flows и перед commit проверяет trust limits, debt symmetry и participant delta (`app/core/payments/engine.py:1128-1279`). Уже `COMMITTED` обрабатывается как terminal success (`tests/unit/test_payments_2pc.py:58-112`). Но participant delta не различает две попарные структуры с одинаковыми сальдо, а audit/checkpoint для generic non-DB ошибки best-effort (`app/core/payments/engine.py:1281-1377`).

- **Abort/timeouts/recovery.** Terminal commit не отменяется повторным abort; stale/expired операции проходят через повторяемый `PaymentEngine.abort` (`tests/unit/test_payments_2pc.py:76-101`, `app/core/recovery.py:74-235`). Unit-тесты подтверждают повторную обработку после локального failure (`tests/unit/test_recovery_cleanup.py:190-320`), но actual process restart с PostgreSQL не проверен.

- **Clearing occurrence.** Идентификатор детерминирован из множества debt IDs; повтор возвращает amount существующей `COMMITTED` записи (`app/core/clearing/service.py:155-186,1247-1263`). После row locks берётся текущий минимум и одинаково вычитается из каждого ребра (`app/core/clearing/service.py:1265-1320,1468-1484`). PostgreSQL-тесты подтверждают один durable occurrence и post-commit reconciliation (`tests/integration/test_clearing_commit_replay_postgres.py:56-815`).

- **Clearing verification.** Нейтральность действительно способна упасть: она сравнивает net position каждого участника до и после (`app/core/invariants.py:260-289`, `tests/unit/test_invariants.py:220-250`). Она не доказывает правильность выбранных рёбер или максимальность суммы, если неправильное изменение сохраняет net positions. Потеря checkpoint разрешена и не препятствует commit (`app/core/clearing/service.py:1427-1549`, `tests/unit/test_clearing_additional_cases.py:813-851`).

- **Trustlines.** Create/update/close не принимают `tx_id`; create retry возвращает conflict, а повтор update может создать новый audit (`app/core/trustlines/service.py:31-133,140-248`). Это расходится с общей фразой «любая операция с одинаковым tx_id», но соответствует явной MVP-оговорке, что trustline operations не создают Transaction (`docs/ru/02-protocol-spec.md:236-243,1322-1343`). Денежный state не меняется, однако историческую допустимость routing по журналу проверить нельзя из-за отсутствия old/new limit и policy.

- **Admin repairs.** Сами debt+admin-audit коммитятся атомарно (`app/api/v1/integrity.py:42-69`, `tests/integration/test_integrity_repairs_atomicity.py:61-350`), но точный эффект необратим из audit summary (`app/api/v1/integrity.py:367-378,446-457`). Повтор меняет итоговые counters и не имеет operation idempotency key.

- **Simulator injection/import.** Это отдельные writers `Debt`, не входящие в payment/clearing журнал (`app/core/simulator/inject_executor.py:266-365,740-760`, `scripts/seed_db.py:384-464`). Пока они используют общую таблицу, утверждать глобальную replayability нельзя; альтернатива — либо журналировать их, либо контрактно изолировать такие данные от проверяемого ledger.

- **Integrity/checkpoints.** Trust-limit и debt-symmetry проверки фальсифицируемы (`app/core/invariants.py:87-235`), zero-sum — нет (`:22-85`). Checkpoint хеширует debts и trustlines и записывает результаты всех трёх проверок (`app/core/integrity.py:18-116`), но не сравнивает состояние с независимым источником.

## 5. Накопление ошибки

- Входной payment amount допускает scale до 18 и total precision до 50 (`app/utils/validation.py:91-184`), тогда как `Debt.amount` хранится как `Numeric(20,8)` (`app/db/models/debt.py:7-28`). Это точка сужения; конкретный PostgreSQL rounding/rejection результат без запуска не проверен.

- `Equivalent.precision` допускает 0–18 (`app/utils/validation.py:17-25`, `app/db/models/equivalent.py:14,29-31`), хотя протокол задаёт 0–8 (`docs/ru/02-protocol-spec.md:138-156`). Payment/router/clearing используют общий `Decimal`, но не квантуют по `Equivalent.precision` (`app/core/payments/service.py:493-500,604-668`, `app/core/payments/router.py:460-549`, `app/core/clearing/service.py:1309-1314,1468-1484`). Это уже маршрутизированный класс программы 012 (`specs/008-surface-code-review/tasks.md:452-478`).

- Payment delta допускает расхождение до и включая `0.00000001`, потому что падает только при `abs(drift) > tolerance` (`app/core/payments/engine.py:1545-1557`). Следовательно, sub-quantum drift может проходить каждую операцию отдельно и сохраняться в БД как вход следующей операции; повторение создаёт накопление между journal amounts и persisted debts.

- В multipath нет явного округления или выбрасывания остатка: `remaining` уменьшается точным `Decimal`, а при ненулевом остатке весь routing отклоняется (`app/core/payments/router.py:496-549`). Риск возникает позже на storage boundary; текущий тест его скрывает преобразованием в `float` и округлением до cents (`tests/integration/test_payments_multipath.py:134-139`).

- Клиринг работает с уже сохранёнными amounts: берёт точный минимум и вычитает его из всех рёбер, остаток остаётся на ребре или нулевая строка удаляется (`app/core/clearing/service.py:1309-1314,1468-1484`). Сам этот алгоритм нового остатка не теряет, но наследует ранее суженную точность.

- Simulator injection явно округляет вниз до `0.01`, включая повторное сложение (`app/core/simulator/inject_executor.py:278-280,352-354`). Для equivalent с precision не равным 2 это самостоятельный источник систематического смещения, также относящийся к 012.

- Post-tick audit использует такую же tolerance `1e-8` и сравнивает participant net, а не пары (`app/core/simulator/post_tick_audit.py:241-259`). Более того, отсутствие `run_id`, idempotency helper, planned tx IDs, expected delta или equivalent приводит к `ok=True` (`:86-108,132-196`), поэтому пропущенное измерение может выглядеть как успех.

## 6. Расхождения с протоколом

| Расхождение | Классификация | Основание |
|---|---|---|
| Zero-sum заявлен как critical integrity check, но алгебраически всегда зелёный | Дефект концепции одновременно в коде и документе; уже `B-A3-001` → 014 | `app/core/invariants.py:62-85`; `docs/ru/02-protocol-spec.md:1769-1796`; `specs/008-surface-code-review/tasks.md:92-98` |
| PAYMENT/CLEARING audit допускается потерять, а денежную операцию commit | Дефект реализации относительно §11 | Протокол обещает audit integrity operations (`docs/ru/02-protocol-spec.md:1757-1765,1948-1970`); код best-effort (`app/core/payments/engine.py:1140-1178,1281-1377`, `app/core/clearing/service.py:1427-1549`) |
| Checksum по документу — canonical debts; код включает trustlines | Документ устарел; текущий охват сильнее, но алгоритм надо версионировать | `docs/ru/02-protocol-spec.md:1893-1920`; `app/core/integrity.py:23-47`; `app/db/models/integrity_checkpoint.py:6-15` |
| `available_credit` в документе не учитывает погашение обратного долга | Документ устарел | Формула документа `limit-current` (`docs/ru/02-protocol-spec.md:456-467`); код корректно добавляет reverse debt (`app/core/payments/router.py:322-340`), что соответствует netting в `_apply_flow` (`app/core/payments/engine.py:1389-1477`) |
| Equivalent precision 0–8 против допуска 0–18 и игнорирования precision на денежном пути | Дефект реализации, уже владелец 012 | `docs/ru/02-protocol-spec.md:138-156`; `app/utils/validation.py:17-25,91-184`; маршрут `specs/008-surface-code-review/tasks.md:452-478` |
| Публичный clearing `max_depth` допускает до 10 против v0.1 cycles 3–6 | Контрактная неоднозначность; маршрут 011, не новая finding 015 | `app/api/v1/clearing.py:20,33`; `docs/ru/02-protocol-spec.md:45-50`; §7 допускает временную конфигурацию для perf (`:992-1003`), но публичный режим не обозначен как perf-only |
| Payment schema/config потенциально допускают значения выше 6/3 | Не подтверждённый дефект: defaults корректны, документ разрешает experimental режим | Runtime caps берутся из settings (`app/core/payments/service.py:542-579`); stable defaults 6/3 (`docs/ru/09-decisions-and-defaults.md:48-55`); experimental 1..N (`docs/ru/02-protocol-spec.md:539-565`) |
| Общее правило идемпотентности «любая операция с tx_id» не применимо к trustlines | Устаревшая слишком широкая формулировка, не дефект текущего MVP | `docs/ru/02-protocol-spec.md:1322-1343` против явной carve-out `:236-243`; код `app/core/trustlines/service.py:31-348` |
| Протокол обещает сохраняемую историю/audit, но журнал не покрывает все debt writers | Дефект реализации/контракта в объёме 015 | `docs/ru/02-protocol-spec.md:1718-1753`; writers `scripts/seed_db.py:384-464`, `app/core/simulator/inject_executor.py:266-365`, `app/api/v1/integrity.py:297-467` |

## 7. Проект спеки 015

```markdown
# 015 — Фальсифицируемая сверка финансового ядра

- **Date:** 2026-08-22
- **Status:** DRAFT — implementation not authorized; starts after 009–014
- **Status authority:** completion is established only by acceptance criteria
  and fresh evidence, not by this field.
- **Owner surface:** app/core/integrity.py, app/core/invariants.py,
  app/db/models/{transaction,audit_log,integrity_checkpoint}.py,
  payment/clearing debt-mutation call sites, integrity API, migrations,
  behavioral tests and current RU protocol documentation.
- **Excluded owners:** fixes already routed to 010, 012 and 014; archive,
  simulator v1, metrics float work closed by 007.
- **Depends on:** completion/re-baseline of 009–014.

## Problem

The system publishes a green financial-integrity result without comparing debts
to an independent complete history. The existing journal cannot reconstruct all
persisted debt changes, and payment/clearing may commit without complete audit
evidence.

## Owner surface

The program owns the narrow contract required to:
1. represent every persisted debt mutation as an exact, versioned, atomic delta;
2. declare a zero/genesis or explicit baseline watermark;
3. fold those deltas and compare every canonical participant pair with debts;
4. make absence of required evidence UNVERIFIABLE/degraded, never healthy.

It does not own payment concurrency, precision policy or the already diagnosed
zero-sum defect except as dependencies on 009/012/014.

## Findings

- F-015-1 — P2 — 2026-08-22:
  The transaction/audit history is incomplete and non-invertible.
  Evidence: app/db/models/transaction.py:6-27;
  app/db/models/audit_log.py:28-44;
  scripts/seed_db.py:384-464;
  app/core/simulator/inject_executor.py:266-365,740-760;
  app/api/v1/integrity.py:297-467.

- F-015-2 — P2 — 2026-08-22:
  PAYMENT and CLEARING may commit after generic checkpoint/audit failure,
  violating the integrity-audit contract.
  Evidence: app/core/payments/engine.py:1140-1178,1281-1377;
  app/core/clearing/service.py:1427-1549;
  tests/unit/test_clearing_additional_cases.py:813-851;
  docs/ru/02-protocol-spec.md:1757-1765,1948-1970.

- F-015-3 — P3 — 2026-08-22:
  Checkpoints and integrity audits do not identify a checksum/payload version
  or enforce a one-to-one ordered link to the owning operation.
  Evidence: app/db/models/integrity_checkpoint.py:6-15;
  app/db/models/audit_log.py:28-44;
  app/db/models/transaction.py:6-27.

References, not new findings:
- B-A3-001 / program 014 — zero-sum false green.
- Program 012 — scale/precision and rounding.
- Program 010 F-010-1/F-010-2 — rollback fail-open paths.
- Program 011 — public contract limits.

## Current / Intended / Optimal

### Current
Payments and current clearing rows contain enough fields to derive their debt
effects, but other writers do not. Integrity checks cover limits, symmetry and
participant deltas; zero-sum is structurally incapable of detecting amount
corruption. Audit/checkpoints are not uniformly mandatory.

### Intended
The protocol requires idempotent monetary operations, traceable clearing,
immutable history and post-operation/periodic integrity checks. Missing
verification evidence is not equivalent to a successful check.

### Optimal
Use a small versioned append-only exact-debt-delta contract in the same DB UoW.
Declare genesis or a baseline watermark. Rebuild canonical pair balances and
compare them exactly with debts; validate intermediate trust limits and clearing
amounts when the historical inputs are available. Return UNVERIFIABLE when the
history is incomplete.

## Non-goals

- Introducing a generalized event-sourcing framework, outbox or service layer.
- Replacing routing, 2PC, advisory-lock or clearing algorithms.
- Reopening 010/012/014 findings.
- Proving full-mode routing optimality.
- Updating archive, visual baselines or applied migrations.
- Treating simulator metric floats as financial state.

## Verification plan

1. Current-code reproducers:
   - balanced-cycle amount corruption must make the intended reconcile assertion
     fail while current zero-sum remains green;
   - generic after-checkpoint failure must make the intended fail-closed/degraded
     assertion fail because current clearing commits.
2. Invariants and counterchecks:
   - exact pair equality; unique operation/delta link; chain continuity;
     explicit baseline; real payment/clearing/repair cases must still verify.
3. Existing selectors that must remain green:
   - tests/integration/test_payment_idempotency_postgres.py
   - tests/integration/test_payment_commit_advisory_locks_postgres.py
   - tests/integration/test_payment_engine_uow_retry_postgres.py
   - tests/integration/test_clearing_commit_replay_postgres.py
   - tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py
   - tests/integration/test_integrity_repairs_atomicity.py
   - tests/unit/test_invariants.py
   - tests/unit/test_trustline_audit_fail_closed.py
4. Forbidden false proof:
   zero-sum alone, checksum length alone, response equality alone,
   rounded float totals, SQLite/mocks as PostgreSQL concurrency evidence,
   “N passed” without inspecting artifacts, and treating a missing measurement
   as zero findings.

## Tasks

| ID | Task | Status |
|---|---|---|
| T1501 | Fix replay scope: zero genesis or explicit baseline, including imported/demo data policy | [!] not authorized |
| T1502 | Specify versioned exact debt-delta and checksum-chain contract | [!] not authorized |
| T1503 | Add migration/model constraints for ordering, linkage and immutability | [!] not authorized |
| T1504 | Instrument PAYMENT, CLEARING, repairs and every admitted Debt writer in the owning UoW | [!] not authorized |
| T1505 | Implement pairwise replay/reconciliation with UNVERIFIABLE state | [!] not authorized |
| T1506 | Make required payment/clearing evidence fail-closed or explicitly degraded | [!] not authorized |
| T1507 | Align integrity API, OpenAPI and current RU protocol; remove false zero-sum claim | [!] not authorized |
| T1508 | Add mutation-based unit, contract and PostgreSQL acceptance tests | [!] not authorized |
| T1509 | Run independent external review and publish fresh evidence on exact HEAD | [!] not authorized |

Legend: [x] complete; [ ] in progress; [!] blocked/not authorized.

## Changelog

- 2026-08-22 — external static research draft prepared against supplied
  HEAD de4dd08; no repository files changed and no project checks executed.
```

## 8. План тестов

| Предлагаемый тест | Внесённое повреждение, от которого он обязан покраснеть |
|---|---|
| `test_reconcile_detects_single_debt_amount_corruption` | После корректного committed payment напрямую увеличить один `Debt.amount` на `0.00000001`; ledger остаётся прежним |
| `test_reconcile_detects_net_neutral_cycle_corruption` | Одинаково увеличить три ребра замкнутого цикла; global zero-sum и participant net останутся неизменными, pairwise replay обязан упасть |
| `test_reconcile_rejects_missing_or_duplicate_delta` | Удалить одну exact-delta запись, затем отдельным параметром продублировать её; оба случая должны дать `UNVERIFIABLE/critical` |
| `test_committed_payload_or_delta_is_tamper_evident` | После commit изменить route amount или debtor/creditor в journal payload; checksum/link verification должна упасть |
| `test_replay_requires_genesis_or_baseline` | Создать ненулевой Debt без genesis/baseline marker; проверка обязана вернуть `UNVERIFIABLE`, а не healthy |
| `test_payment_generic_audit_failure_cannot_produce_verified_commit` | Заставить after-checkpoint/audit builder бросить `RuntimeError`; запрещено получить одновременно `COMMITTED` и отсутствие/degraded evidence |
| `test_clearing_after_checkpoint_failure_is_not_silently_verified` | Повредить второй checkpoint как в `tests/unit/test_clearing_additional_cases.py:824-834`; текущий зелёный commit должен стать красным acceptance-reproducer |
| `test_admin_repair_records_exact_reversible_deltas` | Выполнить net/cap repair и удалить одну пару before/after из journal; обратный fold обязан перестать совпадать |
| `test_payment_route_fold_detects_wrong_intermediate_edge` | В `_apply_flow` направить один hop через другую пару так, чтобы sender/receiver net сохранился; participant delta пройдёт, pairwise verifier обязан упасть |
| `test_clearing_amount_matches_replayed_prestate_minimum` | Записать в CLEARING сумму меньше/больше минимума pre-state при равном вычитании по циклу; neutrality останется зелёной, semantic replay обязан упасть |
| `test_historical_trustline_gap_is_unverifiable` | Удалить old/new limit из события перед payment; проверка исторической capacity обязана вернуть `UNVERIFIABLE`, а не считать маршрут допустимым |
| `test_checksum_algorithm_version_mismatch_fails_closed` | Поменять checksum version либо интерпретировать debt-only hash как debts+trustlines; цепочка обязана отвергнуться |
| `test_concurrent_duplicate_payment_has_one_delta_set_postgres` | Убрать unique operation→delta constraint или повторно записать deltas при duplicate `tx_id`; PostgreSQL-тест обязан увидеть больше одного набора |
| `test_concurrent_same_clearing_occurrence_has_one_delta_set_postgres` | Записать deltas до разрешения deterministic occurrence race; два конкурента не должны оставить два набора |
| Cross-program acceptance 012: `test_subquantum_amount_cannot_accumulate_against_journal_postgres` | Повторить payment с 9–18 знаками после запятой, который на `Numeric(20,8)` меняется не более чем на текущую tolerance; либо вход отклоняется/квантуется до журнала, либо сверка обязана упасть |

Для PostgreSQL-тестов обязателен отдельный `-BackendMarker postgres`; иначе marker-фильтр не проверит нужную семантику (`AGENTS.md:124-145,198-210`).

## 9. Что я не смог проверить и почему

- Не установлено фактическое состояние тестового baseline: по прямому запрету не запускались pytest, canonical runner, PostgreSQL, UI или приложение.
- Не проверен реальный режим округления/ошибки PostgreSQL `Numeric(20,8)` для scale 9–18; статически доказана только несовместимая граница типов (`app/utils/validation.py:91-184`, `app/db/models/debt.py:14`).
- Не воспроизведены process crash, restart и ambiguous commit на живом Postgres; вывод об их покрытии основан на коде и существующих тестах.
- Не доказано, какие исторические production rows содержат enriched clearing `edges`: текущий writer их пишет, но `Transaction.payload` versionless и модель допускает произвольный JSON (`app/core/clearing/service.py:1448-1465`, `app/db/models/transaction.py:14`).
- Не проверены deployed migrations и внешние данные, которых нет в credential-free clone; проект спеки поэтому требует явного genesis/baseline решения, а не предполагает его.
- Инвентаризация охватывает релевантные `payment*`, `payments_*`, `clearing*`, integrity, recovery, trustline-audit и post-tick семейства статически; она не утверждает исполнение всех 182 тестовых файлов, упомянутых в brief (`specs/015-financial-core-verification/research-brief.md:44-55`).

RESEARCH-DONE